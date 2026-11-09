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

"""The recogniser on synthetic frames."""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image

from custom_components.android_meter import recognizer as r
from tests import synth

LAYOUT = synth.Layout()
GEOMETRY = r.Geometry(0, LAYOUT.x, LAYOUT.y, LAYOUT.width, LAYOUT.height, LAYOUT.digits, synth.GAP)


def _cells(volume: float, rng: random.Random, light: float = 0.9) -> list[r.Cell]:
    """A frame under the steady light of a torch, unless told otherwise."""
    return r.cut(r.open_frame(synth.frame(volume, LAYOUT, rng, light=light, shade=0), 0), GEOMETRY)


def _taught(rng: random.Random, *values: int) -> r.Patches:
    patches = r.Patches()
    for value in values:
        assert r.learn_reading(_cells(value, rng), patches, value) > 0
    return patches


def test_mask_bits_run_from_the_top_left() -> None:
    image = Image.new("L", (r.CELL_W, r.CELL_H), 0)
    image.putpixel((0, 0), 255)
    image.putpixel((r.CELL_W - 1, r.CELL_H - 1), 255)
    bits = image.point(lambda p: 255 if p else 0, mode="1")
    mask = int.from_bytes(bits.tobytes(), "big")
    assert mask == (1 << (r.CELL_W * r.CELL_H - 1)) | 1


def test_shifts_never_wrap_a_row() -> None:
    left = 1 << (r.CELL_W * r.CELL_H - 1)
    right = 1 << (r.CELL_W * r.CELL_H - r.CELL_W)
    for moved in r._shifts(left | right):
        assert moved.bit_count() <= 2
    assert all(m.bit_count() <= 1 for m in r._shifts(left)[3:])


def test_patches_survive_a_dump_and_refuse_junk() -> None:
    patches = _taught(random.Random(1), 12345678)
    again = r.Patches.load(patches.dump())
    assert again.dump() == patches.dump()
    junk = r.Patches.load([["zz", 5, "", "f" * 200], {}, *[[] for _ in range(8)]])
    assert junk.counts() == [0] * 10
    assert r.Patches.load({"not": "a list"}).counts() == [0] * 10


def test_open_frame_refuses_what_is_not_a_jpeg() -> None:
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, "PNG")
    for data in (b"", b"\xff\xd8\xff garbage", png.getvalue()):
        with pytest.raises(r.FrameError):
            r.open_frame(data, 0)


def test_open_frame_refuses_a_huge_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r, "MAX_PIXELS", 100)
    jpeg = io.BytesIO()
    Image.new("RGB", (20, 20)).save(jpeg, "JPEG")
    with pytest.raises(r.FrameError):
        r.open_frame(jpeg.getvalue(), 0)


def test_rotation_turns_clockwise() -> None:
    jpeg = io.BytesIO()
    Image.new("RGB", (40, 20)).save(jpeg, "JPEG")
    assert r.open_frame(jpeg.getvalue(), 90).size == (20, 40)
    assert r.open_frame(jpeg.getvalue(), 180).size == (40, 20)


def test_windows_skip_the_gaps() -> None:
    assert GEOMETRY.cells()[:2] == [(0, 40), (50, 90)]
    assert GEOMETRY.cells()[-1] == (350, 390)


def test_crop_outside_the_frame_is_refused() -> None:
    frame = r.open_frame(synth.frame(0, LAYOUT, random.Random(1)), 0)
    wide = r.Geometry(0, 600, 0, 100, 60, 8, 0)
    with pytest.raises(r.FrameError):
        r.cut(frame, wide)


def test_a_known_reading_reads_back() -> None:
    rng = random.Random(2)
    patches = _taught(rng, 1234567, 89012345)
    value = 1234567
    result = r.read(_cells(value, rng), patches, value - 50, value + 50)
    assert result.refused is None
    assert result.value == value
    assert all(result.trusted)


def test_a_dark_frame_is_unreadable() -> None:
    rng = random.Random(3)
    patches = _taught(rng, 1234567, 89012345)
    counts = patches.counts()
    result = r.read(_cells(1234567, rng, light=0.02), patches, 1234500, 1234600)
    assert result.value is None
    assert result.refused == "unreadable"
    assert patches.counts() == counts


def test_a_meter_behind_its_reading_is_implausible() -> None:
    rng = random.Random(4)
    patches = _taught(rng, 1234567, 89012345)
    result = r.read(_cells(1230000, rng), patches, 1234500, 1234600)
    assert result.value is None
    assert result.refused == "implausible"


def test_rolling_drums_never_push_the_reading_ahead() -> None:
    rng = random.Random(5)
    patches = _taught(rng, 1234567, 89012345)
    for step in range(40):
        volume = 1234500 + step * 2.37
        result = r.read(_cells(volume, rng), patches, 1234500, 1234700)
        if result.value is not None:
            assert result.value <= int(volume) + 1


def test_nothing_is_learnt_from_an_unteachable_frame() -> None:
    patches = r.Patches()
    rng = random.Random(6)
    cells = _cells(1234567.5, rng)
    learnt = r.learn_reading(cells, patches, 1234567)
    assert learnt == sum(1 for cell in cells if cell.at_rest)
    assert not cells[-1].at_rest


def test_render_outlines_the_windows() -> None:
    rng = random.Random(7)
    data = synth.frame(1234567, LAYOUT, rng)
    patches = _taught(rng, 1234567)
    result = r.read(_cells(1234567, rng), patches, 1234567, 1234570)
    drawn = r.render(data, GEOMETRY, result)
    with Image.open(io.BytesIO(drawn)) as image:
        assert image.size == LAYOUT.frame
        assert image.getpixel((LAYOUT.x, LAYOUT.y))[1] > 150
