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

"""Finding the counter in a picture."""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image

from custom_components.android_meter import autocrop
from custom_components.android_meter import recognizer as r
from tests import synth


def _frame(data: bytes) -> Image.Image:
    return r.open_frame(data, 0, "RGB")


def test_a_real_meter_is_found() -> None:
    """An Actaris G4, its serial number painted over with a printed 012345
    that must not pass for the counter. A glare hides the second drum and a
    seal wire the seventh, the eighth is out of the picture."""
    data = (Path(__file__).parent / "fixtures" / "actaris_frame.jpg").read_bytes()
    crop = autocrop.find(_frame(data))
    assert crop is not None
    assert crop["digits"] == 6
    assert crop["decimals"] == 1
    for key, value in {"x": 329, "y": 201, "width": 383, "height": 80, "gap": 36}.items():
        assert abs(crop[key] - value) <= 6, key
    geometry = r.Geometry(
        0, crop["x"], crop["y"], crop["width"], crop["height"], crop["digits"], crop["gap"]
    )
    assert all(cell.at_rest for cell in r.cut(r.open_frame(data, 0), geometry))


def test_the_synthetic_meter_is_found() -> None:
    layout = synth.Layout()
    data = synth.frame(1234567, layout, random.Random(3), light=0.9, shade=0, jitter=0)
    crop = autocrop.find(_frame(data))
    assert crop is not None
    assert crop["digits"] == layout.digits
    assert crop["decimals"] == layout.decimals
    pitch = synth.WINDOW[0] + synth.GAP
    first = crop["x"] + (crop["width"] - (crop["digits"] - 1) * crop["gap"]) / crop["digits"] / 2
    assert abs(first - (layout.x + synth.WINDOW[0] / 2)) <= 4
    assert (
        abs(
            (crop["width"] - (crop["digits"] - 1) * crop["gap"]) / crop["digits"]
            + crop["gap"]
            - pitch
        )
        <= 2
    )
    assert crop["y"] <= layout.y + synth.WINDOW[1] / 2 <= crop["y"] + crop["height"]


def test_nothing_is_found_where_there_is_no_counter() -> None:
    blank = io.BytesIO()
    Image.new("RGB", (640, 480), (120, 120, 120)).save(blank, "JPEG")
    assert autocrop.find(_frame(blank.getvalue())) is None
