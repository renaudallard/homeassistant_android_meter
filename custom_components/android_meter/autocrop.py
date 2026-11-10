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

"""Finding the drum counter in a picture.

The digits of a counter are shapes of one height, their middles on one
line, spaced evenly, each in a window of its own: printed text has digits
of one height too, but nearly touching. So the strokes standing out from
their surroundings are found, light on dark and dark on light, grouped into
shapes, and the longest evenly spaced row of digit-sized ones, spaced at
least NARROWEST times their width, is the counter. A drum missing from the
row, hidden by a glare or dirt, leaves a gap of whole steps and is counted
all the same; one hidden at either end is not, as nothing tells it is
there. Red drums at the right end are the decimals.

Like the recogniser, nothing here imports Home Assistant.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from itertools import pairwise

from PIL import Image, ImageChops, ImageFilter, ImageOps

# The picture is searched this wide; strokes are worn away by STROKE pixels.
SEARCH_WIDTH = 400
STROKE = 7
# A digit is at least 3% and at most 35% of the picture high, and its
# height between 1.1 and 7 times its width.
LOWEST, HIGHEST = 0.03, 0.35
SQUAT, SLENDER = 1.1, 7.0
# Drum digits are spaced at least this many times the widest of them.
NARROWEST = 1.5
FEWEST = 3
# The window around a digit, in digit heights and widths.
WINDOW_HEIGHT = 1.6
WINDOW_WIDTH = 1.3


@dataclass(frozen=True, slots=True)
class Shape:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1

    @property
    def width(self) -> int:
        return self.right - self.left + 1

    @property
    def middle(self) -> float:
        return (self.left + self.right) / 2

    @property
    def level(self) -> float:
        return (self.top + self.bottom) / 2


def _shapes(mask: list[bool], width: int, height: int) -> list[Shape]:
    """The connected groups of set pixels."""
    seen = bytearray(width * height)
    shapes = []
    for start in range(width * height):
        if seen[start] or not mask[start]:
            continue
        seen[start] = 1
        stack = [start]
        left, top, right, bottom = width, height, -1, -1
        while stack:
            at = stack.pop()
            y, x = divmod(at, width)
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for ny in (y - 1, y, y + 1):
                if not 0 <= ny < height:
                    continue
                for nx in (x - 1, x, x + 1):
                    near = ny * width + nx
                    if 0 <= nx < width and mask[near] and not seen[near]:
                        seen[near] = 1
                        stack.append(near)
        shapes.append(Shape(left, top, right, bottom))
    return shapes


def _digits(grey: Image.Image) -> list[Shape]:
    """Digit-sized shapes standing out light from their surroundings."""
    opened = grey.filter(ImageFilter.MinFilter(STROKE)).filter(ImageFilter.MaxFilter(STROKE))
    strokes = ImageOps.autocontrast(ImageChops.subtract(grey, opened), cutoff=1)
    mask = [level > 110 for level in strokes.tobytes()]
    width, height = grey.size
    return [
        shape
        for shape in _shapes(mask, width, height)
        if LOWEST * height <= shape.height <= HIGHEST * height
        and SQUAT <= shape.height / shape.width <= SLENDER
    ]


@dataclass(frozen=True, slots=True)
class _Row:
    shapes: list[Shape]
    pitch: float
    windows: int


def _row(shapes: list[Shape]) -> _Row | None:
    """The best evenly spaced row of like digits among shapes."""
    best: _Row | None = None
    for anchor in shapes:
        row = sorted(
            (
                shape
                for shape in shapes
                if abs(shape.level - anchor.level) < 0.25 * anchor.height
                and 0.75 <= shape.height / anchor.height <= 1.33
            ),
            key=lambda shape: shape.left,
        )
        if len(row) < FEWEST:
            continue
        steps = [b.middle - a.middle for a, b in pairwise(row)]
        if not steps or min(steps) <= 0:
            continue
        # The step most others agree with.
        pitch = max(steps, key=lambda step: sum(abs(o - step) <= 0.15 * step for o in steps))
        run: list[Shape] = [row[0]]
        longest: list[Shape] = [row[0]]
        for shape, step in zip(row[1:], steps, strict=True):
            whole = round(step / pitch)
            if 1 <= whole <= 3 and abs(step - whole * pitch) <= 0.15 * pitch:
                run.append(shape)
            else:
                run = [shape]
            if len(run) > len(longest):
                longest = list(run)
        if len(longest) < FEWEST:
            continue
        span = longest[-1].middle - longest[0].middle
        windows = round(span / pitch) + 1
        pitch = span / (windows - 1)
        if pitch < NARROWEST * max(shape.width for shape in longest):
            continue
        found = _Row(longest, pitch, windows)
        if best is None or (windows, len(longest)) > (best.windows, len(best.shapes)):
            best = found
    return best


def find(frame: Image.Image) -> dict[str, int] | None:
    """The crop and counter of an upright RGB picture, or None.

    In pixels of the picture, as the options take them.
    """
    scale = frame.width / SEARCH_WIDTH
    small = frame.resize(
        (SEARCH_WIDTH, max(1, round(frame.height / scale))), Image.Resampling.BILINEAR
    )
    grey = small.convert("L")
    rows = [_row(_digits(grey)), _row(_digits(ImageOps.invert(grey)))]
    found = [row for row in rows if row is not None]
    if not found:
        return None
    row = max(found, key=lambda r: (r.windows, len(r.shapes)))
    digit_height = statistics.median(shape.height for shape in row.shapes)
    widest = max(shape.width for shape in row.shapes)
    level = statistics.median(shape.level for shape in row.shapes)
    first = row.shapes[0].middle
    last = first + (row.windows - 1) * row.pitch
    # A counter against the picture's edge gets windows as wide as fit,
    # still centred on its digits, as long as they hold the widest one.
    window = min(
        0.8 * row.pitch,
        WINDOW_WIDTH * widest,
        2 * first,
        2 * (small.width - last),
    )
    tall = min(WINDOW_HEIGHT * digit_height, 2 * level, 2 * (small.height - level))
    if window < widest or tall < digit_height:
        return None
    left = first - window / 2
    top = level - tall / 2
    # A red drum shows its colour around the window, the drum itself being
    # dark: the whole step is weighed, window and gap.
    decimals = 0
    for i in reversed(range(row.windows)):
        start = row.shapes[0].middle + (i - 0.5) * row.pitch
        box = small.crop((round(start), round(top), round(start + row.pitch), round(top + tall)))
        red, green, blue = (statistics.median(band.tobytes()) for band in box.split())
        if not (red > green + 40 and red > blue + 40):
            break
        decimals += 1
    width = (row.windows - 1) * row.pitch + window
    crop = {
        "rotation": 0,
        "x": round(left * scale),
        "y": round(top * scale),
        "width": round(width * scale),
        "height": round(tall * scale),
        "gap": round((row.pitch - window) * scale),
        "digits": row.windows,
        "decimals": decimals,
    }
    if crop["x"] + crop["width"] > frame.width or crop["y"] + crop["height"] > frame.height:
        return None
    return crop
