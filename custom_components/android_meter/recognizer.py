# Copyright (c) 2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Reading a drum counter off a camera frame.

The frame is turned, cropped to the counter and cut into one cell per
drum. Each cell is shrunk to a small mask with the digit's ink set, on
whatever colours the drum has, and compared with the masks learnt so far
for each digit by the share of ink they do not have in common, the best of
a one pixel shift either way.

A counter only moves forward, and only so fast. The reading is therefore
not taken cell by cell but chosen among the values the meter can have
reached since the last one, as the value whose digits look most like the
cells. That choice is also what labels a digit never seen before, which is
how the masks are learnt from a single reading typed in by the user.

Nothing here imports Home Assistant, so a read runs whole in an executor
and the tests feed it plain images.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import ExifTags, Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

# A mask is a Python int of CELL_W * CELL_H bits, the top left pixel
# highest, row after row.
CELL_W = 16
CELL_H = 24
_BITS = CELL_W * CELL_H
_FULL = (1 << _BITS) - 1
_FIRST_COLUMN = sum(1 << (_BITS - 1 - y * CELL_W) for y in range(CELL_H))
_LAST_COLUMN = sum(1 << (_BITS - CELL_W - y * CELL_W) for y in range(CELL_H))

# A frame with more pixels than this is refused before it is decoded.
MAX_PIXELS = 40_000_000

# How to turn a picture upright for each EXIF orientation. The others, 1
# and anything unknown, leave it as it is.
_UPRIGHT = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}

# A distance is the share of ink two masks do not have in common, in
# percent. A cell at rest at most MATCH away from its nearest known digit is
# trusted to show it; while the drum can still show a digit never seen, the
# cell must come within STRICT instead, as it might be that digit. A digit
# never seen costs NOVEL in a trusted cell, and is learnt only from a cell
# further than MATCH from every known one. A mask closer than DUPLICATE to
# one already kept adds nothing.
MATCH = 15.0
STRICT = 8.0
NOVEL = 24.0
DUPLICATE = 4.0

# A cell is trusted only if its nearest digit is at least SEPARATION closer
# than the next one: a 6 with its loop closed by thick strokes can come
# nearer an 8 than any 6 seen yet, but not much nearer than that 6.
SEPARATION = 6.0

# Readings whose cost is within MARGIN of the best one are as likely as it.
MARGIN = 8.0

# A cell whose grey levels spread less than this shows no digit at all: no
# light, a finger, or a crop off the counter.
MIN_CONTRAST = 40

# A digit whose ink spans at most this share of the window across is thin.
NARROW = 0.45

PER_DIGIT = 16

# Past this many possible values, after a long silence, they are not tried
# one by one: only a frame where every cell is plainly one digit is read.
MAX_SPAN = 200_000

_TURN = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


class FrameError(Exception):
    """The frame cannot be read."""


@dataclass(frozen=True, slots=True)
class Geometry:
    """Where the counter sits in a frame turned clockwise by rotation.

    The crop runs from the left edge of the first window to the right edge
    of the last, and gap is the width of the housing between two windows.
    """

    rotation: int
    x: int
    y: int
    width: int
    height: int
    digits: int
    gap: int = 0

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    @property
    def window(self) -> float:
        """Width of one window: the crop less the gaps, shared out."""
        return (self.width - (self.digits - 1) * self.gap) / self.digits

    def cells(self) -> list[tuple[int, int]]:
        """Left and right edge of each window, in the crop."""
        pitch = self.window + self.gap
        return [(round(i * pitch), round(i * pitch + self.window)) for i in range(self.digits)]


@dataclass(frozen=True, slots=True)
class Cell:
    """One drum: its mask, or None when blank, and whether it is at rest.

    The mask of a drum at rest spans the digit's height, as the masks
    learnt do.
    """

    mask: int | None
    at_rest: bool


@dataclass(frozen=True, slots=True)
class Read:
    """What a frame said.

    value is the reading in units of the last drum, or None when the frame
    was refused, and ceiling the highest reading as likely as it: the meter
    stands somewhere from value to ceiling. refused says why a frame was:
    "unreadable" when no drum could be trusted, "implausible" when the
    trusted ones show a value the meter cannot have reached. nearest is the
    closest known digit of each cell, trusted whether the read relied on
    it.
    """

    value: int | None
    ceiling: int | None
    refused: str | None
    nearest: tuple[int | None, ...]
    trusted: tuple[bool, ...]
    at_rest: tuple[bool, ...]
    learnt: int


def _turn(image: Image.Image, rotation: int) -> Image.Image:
    turn = _TURN.get(rotation)
    return image if turn is None else image.transpose(turn)


def open_frame(data: bytes, rotation: int, mode: str = "L") -> Image.Image:
    """Decode a JPEG frame and turn it upright: as its EXIF orientation
    says, then by rotation."""
    try:
        with Image.open(io.BytesIO(data), formats=["JPEG"]) as image:
            if image.width * image.height > MAX_PIXELS:
                raise FrameError("frame too large")
            # Some phones tag the turn instead of turning the pixels. The
            # tag is only read: writing a damaged EXIF block back, as
            # ImageOps.exif_transpose does, can fail with any error.
            orientation = image.getexif().get(ExifTags.Base.Orientation)
            frame = image.convert(mode)
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as err:
        raise FrameError("not a readable JPEG") from err
    method = _UPRIGHT.get(orientation) if isinstance(orientation, int) else None
    if method is not None:
        frame = frame.transpose(method)
    return _turn(frame, rotation)


def _spread(histogram: list[int]) -> int:
    """Grey levels between the 2nd and 98th percentile."""
    cut = sum(histogram) // 50
    seen = 0
    low = 0
    for level in range(256):
        seen += histogram[level]
        if seen > cut:
            low = level
            break
    seen = 0
    high = 255
    for level in range(255, -1, -1):
        seen += histogram[level]
        if seen > cut:
            high = level
            break
    return high - low


def _threshold(histogram: list[int]) -> int:
    """Otsu's threshold: the level that best splits ink from ground."""
    total = sum(histogram)
    weighted = sum(level * count for level, count in enumerate(histogram))
    below = 0
    below_weighted = 0
    best = (-1.0, 127)
    for level in range(255):
        below += histogram[level]
        if below == 0:
            continue
        above = total - below
        if above == 0:
            break
        below_weighted += level * histogram[level]
        spread = below_weighted / below - (weighted - below_weighted) / above
        between = below * above * spread * spread
        if between > best[0]:
            best = (between, level)
    return best[1]


def _bits(patch: Image.Image) -> int:
    """The ink of a grey patch, split at Otsu's threshold, as a mask."""
    level = _threshold(patch.histogram())
    bits = patch.point(lambda p: 255 if p > level else 0, mode="1")
    return int.from_bytes(bits.tobytes(), "big")


def _rows(mask: int, width: int, height: int) -> tuple[int, int] | None:
    """First and last row of a mask holding ink."""
    full = (1 << width) - 1
    inked = [y for y in range(height) if (mask >> ((height - 1 - y) * width)) & full]
    return (inked[0], inked[-1]) if inked else None


def _stroke(mask: int, width: int, height: int) -> int:
    """The digit's own ink: the largest stroke, and any stroke beside it.

    Marks wholly above or below it, such as the bar some drums print under
    each digit, or a speck, are left out, and so is a stroke reaching in
    from the side of the window, as an edge of the frame or a seal mark
    printed between two drums does.
    """
    size = width * height
    bits = bin(mask)[2:].zfill(size)
    seen = [c == "0" for c in bits]
    strokes: list[tuple[int, int, int, bool, list[int]]] = []
    for start in range(size):
        if seen[start]:
            continue
        seen[start] = True
        stack = [start]
        found = [start]
        while stack:
            at = stack.pop()
            y, x = divmod(at, width)
            for ny in (y - 1, y, y + 1):
                if not 0 <= ny < height:
                    continue
                for nx in (x - 1, x, x + 1):
                    near = ny * width + nx
                    if 0 <= nx < width and not seen[near]:
                        seen[near] = True
                        stack.append(near)
                        found.append(near)
        rows = [at // width for at in found]
        side = any(at % width in (0, width - 1) for at in found)
        strokes.append((len(found), min(rows), max(rows), side, found))
    if not strokes:
        return 0
    main = max(strokes, key=lambda stroke: stroke[0])
    _, top, bottom, _, _ = main
    kept = 0
    for stroke in strokes:
        _, first, last, side, found = stroke
        if stroke is main or (last >= top and first <= bottom and not side):
            for at in found:
                kept |= 1 << (size - 1 - at)
    return kept


def _flatten(work: Image.Image) -> Image.Image:
    """The window less its own background, so that a reflection's glow
    across it does not split a digit at the threshold. The background is
    the window with its strokes worn away."""
    background = work.filter(ImageFilter.MinFilter(7)).filter(ImageFilter.GaussianBlur(3))
    return ImageOps.autocontrast(ImageChops.subtract(work, background), cutoff=1)


def _cell(image: Image.Image) -> Cell:
    """A window as a cell.

    A window whose grey levels hardly spread once shrunk to a cell, which
    averages the sensor's noise away, is blank. Otherwise the grey levels
    are stretched and turned so the ink is bright, the window's background
    is taken away, and the rest is split by Otsu's threshold.

    A drum is at rest when the digit's own ink stays clear of the window's
    top and bottom edges: one between two digits shows strokes the window
    cuts. A digit at rest is cut down to the rows it spans, a thin one
    centred across, and stretched to the height of a cell, so that it fills the mask
    whatever its size in the frame, and wherever the last drum, which stops
    where the gas did, left it in its window.
    """
    small = image.resize((CELL_W, CELL_H), Image.Resampling.BILINEAR)
    if _spread(small.histogram()) < MIN_CONTRAST:
        return Cell(None, False)
    width, height = CELL_W * 2, CELL_H * 2
    work = image.resize((width, height), Image.Resampling.BILINEAR)
    work = ImageOps.autocontrast(work, cutoff=2)
    if ImageStat.Stat(work).median[0] > 127:
        work = ImageOps.invert(work)
    work = _flatten(work).filter(ImageFilter.GaussianBlur(0.7))
    shown = _bits(work.resize((CELL_W, CELL_H), Image.Resampling.BILINEAR))
    digit = _stroke(_bits(work), width, height)
    rows = _rows(digit, width, height)
    if rows is None or rows[0] == 0 or rows[1] == height - 1:
        return Cell(shown, False)
    top, bottom = rows
    # The grey window with all but the digit's strokes, and a pixel around
    # them, blanked: shrinking grey keeps what a shrunk mask would lose.
    keep = Image.frombytes("1", (width, height), digit.to_bytes(width * height // 8, "big"))
    keep = keep.convert("L").filter(ImageFilter.MaxFilter(3))
    ink = ImageChops.multiply(work, keep)
    # A thin digit, a 1, is centred across on its ink: windows are cut by
    # estimate, and a 1 two pixels off matches nothing. A wide one keeps its
    # place, which tells a 7 from a 1.
    columns = [0] * width
    for at in range(width * height):
        if digit >> (width * height - 1 - at) & 1:
            columns[at % width] += 1
    inked = [x for x, count in enumerate(columns) if count]
    shift = 0
    if inked[-1] - inked[0] + 1 <= NARROW * width:
        centre = sum(x * count for x, count in enumerate(columns)) / sum(columns)
        shift = round((width - 1) / 2 - centre)
    centred = Image.new("L", (width, height), 0)
    centred.paste(ink, (shift, 0))
    centred = centred.crop((0, max(0, top - 1), width, min(height, bottom + 2)))
    return Cell(_bits(centred.resize((CELL_W, CELL_H), Image.Resampling.BILINEAR)), True)


def cut(frame: Image.Image, geometry: Geometry) -> list[Cell]:
    """Cut an upright frame into one cell per drum."""
    if geometry.x + geometry.width > frame.width or geometry.y + geometry.height > frame.height:
        raise FrameError("crop outside the frame")
    if geometry.window < 4 or geometry.height < 8:
        raise FrameError("windows too small")
    counter = frame.crop(geometry.box)
    return [
        _cell(counter.crop((left, 0, right, geometry.height))) for left, right in geometry.cells()
    ]


def _shifts(mask: int) -> list[int]:
    """The mask moved by up to one pixel either way, its edges left bare."""
    across = (mask, (mask >> 1) & ~_FIRST_COLUMN, (mask << 1) & ~_LAST_COLUMN & _FULL)
    shifted: list[int] = []
    for moved in across:
        shifted.extend((moved, moved >> CELL_W, (moved << CELL_W) & _FULL))
    return shifted


def _distance(a: int, b: int) -> float:
    either = (a | b).bit_count()
    if either == 0:
        return 0.0
    return 100.0 * (1.0 - (a & b).bit_count() / either)


class Patches:
    """The masks learnt for each digit, shared by every drum.

    Shared, because the drums print the same figures and the last one
    shows each of them within hours, where a high drum may take years.
    """

    def __init__(self) -> None:
        self._digits: list[list[int]] = [[] for _ in range(10)]

    def known(self, digit: int) -> bool:
        return bool(self._digits[digit])

    def counts(self) -> list[int]:
        return [len(kept) for kept in self._digits]

    def distance(self, shifts: Sequence[int], digit: int) -> float | None:
        """Distance from a cell, given by its shifts, to the nearest mask of
        digit, None when it has none."""
        kept = self._digits[digit]
        if not kept:
            return None
        return min(_distance(moved, known) for known in kept for moved in shifts)

    def learn(self, digit: int, mask: int) -> None:
        kept = self._digits[digit]
        kept.append(mask)
        if len(kept) > PER_DIGIT:
            del kept[0]

    def dump(self) -> list[list[str]]:
        return [[format(mask, "x") for mask in kept] for kept in self._digits]

    @classmethod
    def load(cls, data: object) -> Patches:
        """Rebuild from dump(), keeping only what is well formed."""
        patches = cls()
        if not isinstance(data, list) or len(data) != 10:
            return patches
        for digit, kept in enumerate(data):
            if not isinstance(kept, list):
                continue
            for text in kept[-PER_DIGIT:]:
                if not isinstance(text, str) or not 0 < len(text) <= _BITS // 4:
                    continue
                try:
                    mask = int(text, 16)
                except ValueError:
                    continue
                if mask > 0:
                    patches._digits[digit].append(mask)
        return patches


def _figures(value: int, digits: int) -> list[int]:
    """What the last digits drums show for value. Drums further left, out
    of the crop, follow from the reading."""
    return [ord(c) - 48 for c in str(value % 10**digits).zfill(digits)]


def _reachable(low: int, high: int, digits: int) -> list[set[int]]:
    """The figures each drum shows somewhere from low to high."""
    reach = []
    for i in range(digits):
        weight = 10 ** (digits - 1 - i)
        first = low // weight
        last = min(high // weight, first + 9)
        reach.append({k % 10 for k in range(first, last + 1)})
    return reach


def learn_reading(cells: Sequence[Cell], patches: Patches, value: int) -> int:
    """Learn every cell at rest as the digit value puts there."""
    learnt = 0
    for cell, digit in zip(cells, _figures(value, len(cells)), strict=True):
        if cell.mask is not None and cell.at_rest:
            patches.learn(digit, cell.mask)
            learnt += 1
    return learnt


def read(cells: Sequence[Cell], patches: Patches, low: int, high: int) -> Read:
    """Read cells as a value from low to high, learning what they show.

    low is the last reading and high the ceiling of the last read plus what
    the meter can have run since: the meter has to stand in between. The
    cells are the last drums of the meter, so a value is compared on its
    last figures and the drums left of the crop follow from the reading.

    Only trusted cells weigh in the choice: a drum between two digits, a
    blank cell or one unlike any known digit says nothing, and every value
    fits it as well. Of the values as likely as the best, the lowest is
    taken, so that a doubt delays the reading but never moves it further
    than the meter went.

    A cell at rest is learnt only when every value as likely as the best
    puts the same digit there: a digit never seen when the cell matches no
    known one, a known one while no digit the drum can show is still
    unknown, as the cell might otherwise be that one. A drum that cannot
    turn from low to high is learnt whenever its nearest digit is the one
    it must show, however far, which is how the masks soon cover the light
    and the play of the mount after a single reading was taught.
    """
    n = len(cells)
    nearest: list[int | None] = [None] * n
    trusted: list[bool] = [False] * n
    at_rest = tuple(cell.at_rest for cell in cells)

    def refuse(why: str) -> Read:
        return Read(None, None, why, tuple(nearest), tuple(trusted), at_rest, 0)

    if high < low:
        return refuse("implausible")
    reach = _reachable(low, high, n)
    known = [patches.known(digit) for digit in range(10)]
    table: list[list[float]] = []
    dists: list[list[float | None]] = []
    for i, cell in enumerate(cells):
        row: list[float | None] = [None] * 10
        if cell.mask is not None:
            moved = _shifts(cell.mask)
            row = [patches.distance(moved, digit) for digit in range(10)]
        dists.append(row)
        ranked = sorted((dist, digit) for digit, dist in enumerate(row) if dist is not None)
        if ranked:
            best, nearest[i] = ranked[0]
            limit = MATCH if all(known[digit] for digit in reach[i]) else STRICT
            apart = len(ranked) == 1 or ranked[1][0] - best >= SEPARATION
            trusted[i] = cell.at_rest and best <= limit and apart
        if trusted[i]:
            table.append([NOVEL if dist is None else dist for dist in row])
        else:
            table.append([0.0] * 10)

    if not any(trusted):
        return refuse("unreadable")

    # A drum that cannot turn between low and high shows one digit. When it
    # plainly shows another, the reading is wrong or the frame is not this
    # counter.
    fixed = [next(iter(digits)) if len(digits) == 1 else None for digits in reach]
    for i, cell in enumerate(cells):
        digit = fixed[i]
        if digit is None or not cell.at_rest or not known[digit]:
            continue
        own = dists[i][digit]
        other = min(
            (dist for d, dist in enumerate(dists[i]) if d != digit and dist is not None),
            default=None,
        )
        if own is not None and other is not None and own > NOVEL and other < own - 2 * SEPARATION:
            return refuse("implausible")

    if high - low >= MAX_SPAN:
        if not all(trusted):
            return refuse("unreadable")
        # The lowest value from low on that ends as the drums show.
        shown = int("".join(str(digit) for digit in nearest))
        value = low - low % 10**n + shown
        if value < low:
            value += 10**n
        if value > high:
            return refuse("implausible")
        return Read(value, value, None, tuple(nearest), tuple(trusted), at_rest, 0)

    costs = [
        sum(row[digit] for row, digit in zip(table, _figures(v, n), strict=True))
        for v in range(low, high + 1)
    ]
    least = min(costs)
    near = [low + i for i, cost in enumerate(costs) if cost <= least + MARGIN]
    value = near[0]
    figures = _figures(value, n)

    for i in range(n):
        if trusted[i] and table[i][figures[i]] > MATCH:
            return refuse("implausible")

    settled = [True] * n
    for v in near[1:]:
        for i, digit in enumerate(_figures(v, n)):
            if digit != figures[i]:
                settled[i] = False
    learnt = 0
    for i, cell in enumerate(cells):
        if cell.mask is None or not cell.at_rest or not settled[i]:
            continue
        digit = figures[i]
        dist = dists[i][digit]
        if dist is None:
            closest = min((d for d in dists[i] if d is not None), default=None)
            fresh = closest is None or closest > MATCH
        elif fixed[i] is not None:
            fresh = dist > DUPLICATE and nearest[i] == digit
        else:
            fresh = trusted[i] and DUPLICATE < dist <= MATCH and all(known[d] for d in reach[i])
        if fresh:
            patches.learn(digit, cell.mask)
            learnt += 1
    return Read(value, near[-1], None, tuple(nearest), tuple(trusted), at_rest, learnt)


def render(data: bytes, geometry: Geometry | None, last: Read | None) -> bytes:
    """The frame upright, with each window outlined, for checking the crop.

    A window is green when the read trusted it, orange when it did not,
    and yellow when the last frame was not read: until a reading is taught
    and the next frame comes, or after one the crop does not fit or that
    could not be opened. The nearest known digit is written under it, or
    ~ when the drum was between two digits.
    """
    frame = open_frame(data, 0 if geometry is None else geometry.rotation, "RGB")
    if geometry is not None:
        draw = ImageDraw.Draw(frame)
        font = ImageFont.load_default(max(12, geometry.height // 2))
        width = max(1, geometry.height // 40)
        for i, (start, end) in enumerate(geometry.cells()):
            left = geometry.x + start
            right = geometry.x + end - 1
            colour = "yellow"
            label = ""
            if last is not None and i < len(last.trusted):
                colour = "lime" if last.trusted[i] else "orange"
                digit = last.nearest[i]
                if not last.at_rest[i]:
                    label = "~"
                elif digit is not None:
                    label = str(digit)
            draw.rectangle(
                (left, geometry.y, right, geometry.y + geometry.height - 1),
                outline=colour,
                width=width,
            )
            if label:
                draw.text(
                    (left + width, geometry.y + geometry.height + width),
                    label,
                    fill=colour,
                    font=font,
                )
    out = io.BytesIO()
    frame.save(out, "JPEG", quality=80)
    return out.getvalue()
