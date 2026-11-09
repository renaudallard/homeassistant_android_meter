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

"""Hours of a meter in use, read a frame a minute.

The gas comes in heating runs and short draws, with the meter standing
still in between, so the low drums are mostly caught turning. Starting from
one reading taught, the reading must never get ahead of the meter, every
digit learnt must be the one the drum really showed, and the reading must
keep up once the meter stops.
"""

from __future__ import annotations

import math
import random

import pytest

from custom_components.android_meter import recognizer as r
from tests import synth

LAYOUT = synth.Layout()
GEOMETRY = r.Geometry(0, LAYOUT.x, LAYOUT.y, LAYOUT.width, LAYOUT.height, LAYOUT.digits, synth.GAP)
MAX_FLOW = 6.0  # m³/h
MINUTES = 480


def _shown(volume: float) -> int:
    """The reading a person would write down: each drum's nearest figure."""
    return int("".join(str(round(p) % 10) for p in synth.positions(volume, LAYOUT.digits)))


@pytest.mark.parametrize("seed", [1, 2])
def test_a_day_of_heating(seed: int) -> None:
    rng = random.Random(seed)
    volume = float(rng.randrange(10**6, 10**7))
    truth: dict[str, object] = {}

    def frame(at: float) -> list[r.Cell]:
        data = synth.frame(at, LAYOUT, rng, light=rng.uniform(0.75, 1.05), shade=0)
        cells = r.cut(r.open_frame(data, 0), GEOMETRY)
        truth["cells"] = cells
        truth["positions"] = synth.positions(at, LAYOUT.digits)
        return cells

    patches = r.Patches()
    wrong: list[tuple[int, set[int]]] = []
    learn = patches.learn

    def checked(digit: int, mask: int) -> None:
        cells = truth["cells"]
        positions = truth["positions"]
        assert isinstance(cells, list) and isinstance(positions, list)
        shown = {round(positions[i]) % 10 for i, cell in enumerate(cells) if cell.mask == mask}
        if digit not in shown:
            wrong.append((digit, shown))
        learn(digit, mask)

    low = ceiling = _shown(volume)
    assert r.learn_reading(frame(volume), patches, low) > 0
    patches.learn = checked  # type: ignore[method-assign]

    read_at = 0
    refused = 0
    flow = 0.0
    left = 0
    for minute in range(1, MINUTES + 1):
        if left <= 0:
            kind = rng.random()
            if kind < 0.55:
                flow, left = 0.0, rng.randint(10, 60)
            elif kind < 0.85:
                flow, left = rng.uniform(1.0, 2.5), rng.randint(5, 20)
            else:
                flow, left = 1.5, rng.randint(1, 3)
        left -= 1
        volume += flow / 60 * 1000
        run = math.ceil(MAX_FLOW / 60 * (minute - read_at) * 1000) + 1
        result = r.read(frame(volume), patches, low, ceiling + run)
        if result.value is None or result.ceiling is None:
            refused += 1
            continue
        assert result.value <= math.floor(volume) + 1, "the reading got ahead of the meter"
        low, ceiling, read_at = result.value, result.ceiling, minute

    assert not wrong
    assert refused < MINUTES // 10
    # A meter that stopped mid-carry leaves drums between two digits, and
    # those are never trusted: the reading then waits, at most a turn of
    # the drum above them behind, until the meter moves again.
    if flow == 0.0:
        assert math.floor(volume) - low <= 100
