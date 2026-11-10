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

"""Synthetic frames of a drum gas meter.

Each drum is a strip of the figures 0 to 9 and 0 again, seen through a
window. The lowest drum turns with the volume; each higher one turns only
while the drum below goes from 9 to 0, as an odometer does, so a frame
taken mid-carry shows several drums between two figures. Integer drums are
white on black, decimal ones white on red. Light, noise and the position
of the counter in the frame vary per frame.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFont

PITCH = 70
WINDOW = (40, 62)
GAP = 10
GLYPH = 40


@dataclass(frozen=True)
class Layout:
    digits: int = 8
    decimals: int = 3
    x: int = 120
    y: int = 200
    frame: tuple[int, int] = (640, 480)

    @property
    def width(self) -> int:
        return self.digits * WINDOW[0] + (self.digits - 1) * GAP

    @property
    def height(self) -> int:
        return WINDOW[1]

    def options(self) -> dict[str, int]:
        """The crop a user would set: the windows, gaps included."""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "digits": self.digits,
            "gap": GAP,
        }


_STRIPS: dict[tuple[int, int, int], Image.Image] = {}
_NOISE: dict[tuple[int, int], list[Image.Image]] = {}


def _noise(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """Sensor noise, sigma 7 around 128, picked by rng among a few frames
    drawn once from a fixed seed: Image.effect_noise has a random source of
    its own, which made the frames differ from one run to the next."""
    frames = _NOISE.get(size)
    if frames is None:
        fixed = random.Random(0)
        count = size[0] * size[1]
        frames = []
        for _ in range(4):
            levels = bytes(min(255, max(0, round(fixed.gauss(128, 7)))) for _ in range(count))
            frames.append(Image.frombytes("L", size, levels).convert("RGB"))
        _NOISE[size] = frames
    frame = frames[rng.randrange(len(frames))]
    return frame.transpose(Image.Transpose.ROTATE_180) if rng.random() < 0.5 else frame


def _strip(background: tuple[int, int, int]) -> Image.Image:
    strip = _STRIPS.get(background)
    if strip is None:
        font = ImageFont.load_default(GLYPH)
        strip = Image.new("RGB", (WINDOW[0], PITCH * 12), background)
        draw = ImageDraw.Draw(strip)
        for j in range(12):
            figure = str((j - 1) % 10)
            centre = PITCH * j + PITCH // 2
            draw.text(
                (WINDOW[0] // 2, centre), figure, fill=(235, 235, 235), font=font, anchor="mm"
            )
        _STRIPS[background] = strip
    return strip


def positions(volume: float, digits: int) -> list[float]:
    """Where each drum stands, highest first, for a volume in units of the
    lowest drum."""
    turns = [volume]
    for _ in range(digits - 1):
        below = turns[-1]
        turns.append(math.floor(below / 10) + max(0.0, below % 10 - 9))
    return [turn % 10 for turn in reversed(turns)]


def frame(
    volume: float,
    layout: Layout,
    rng: random.Random,
    *,
    light: float | None = None,
    jitter: int = 1,
    shade: int | None = None,
) -> bytes:
    """A JPEG frame of the counter at volume.

    light scales the brightness, and shade turns the gradient across the
    frame; both are drawn at random when not given. A phone fixed to the
    meter with its torch on keeps both steady.
    """
    width, height = layout.frame
    image = Image.new("RGB", (width, height), (110, 110, 105))
    dx = rng.randint(-jitter, jitter)
    dy = rng.randint(-jitter, jitter)
    for i, position in enumerate(positions(volume, layout.digits)):
        red = i >= layout.digits - layout.decimals
        strip = _strip((170, 25, 25) if red else (25, 25, 25))
        top = round(PITCH + position * PITCH + PITCH / 2 - WINDOW[1] / 2)
        window = strip.crop((0, top, WINDOW[0], top + WINDOW[1]))
        image.paste(window, (layout.x + dx + i * (WINDOW[0] + GAP), layout.y + dy))
    level = rng.uniform(0.55, 1.15) if light is None else light
    turn = rng.choice((0, 90)) if shade is None else shade
    ramp = Image.linear_gradient("L").resize((width, height)).rotate(turn)
    ramp = ramp.point(lambda p: round(level * (200 + p * 55 / 255)))
    image = ImageChops.multiply(image, Image.merge("RGB", (ramp, ramp, ramp)))
    noise = _noise((width, height), rng)
    image = ImageChops.add(image, noise, 1.0, -128)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()
