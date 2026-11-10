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

"""The screen that teaches the reading, shared by the options and Repairs.

It shows the last picture as the Frame image draws it, with the drums
outlined, which digits are learnt and which are still to learn, and asks
for the reading the picture shows. The flow keeps the frame it showed, and
the reading is taught from that one, not from a frame that came in while
it was being typed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import probatio
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import BooleanSelector, TextSelector

from .const import CONF_FORGET, CONF_READING, DOMAIN
from .meter import Meter, TeachError, parse_reading


def teach_schema() -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Optional(CONF_READING): TextSelector(),
            probatio.Optional(CONF_FORGET, default=False): BooleanSelector(),
        }
    )


def _example(digits: int, decimals: int) -> str:
    """How a reading of this counter is written, as 01234.567, or 0.567
    when every drum is a decimal."""
    figures = ("0123456789" * 2)[:digits]
    whole = figures[: digits - decimals] or "0"
    return f"{whole}.{figures[digits - decimals :]}" if decimals else whole


def frame_picture(hass: HomeAssistant, entry_id: str, meter: Meter) -> str:
    """The Frame image as markdown, or nothing before the first picture.

    The link is the image entity's own, with its short lived token. The
    crop is added to it so that a browser shows the picture again, with
    the new outlines, once the crop changed.
    """
    entity_id = er.async_get(hass).async_get_entity_id("image", DOMAIN, f"{entry_id}_frame")
    state = hass.states.get(entity_id) if entity_id is not None else None
    if meter.frame is None or state is None:
        return ""
    url = state.attributes.get("entity_picture")
    if not isinstance(url, str) or not url.startswith("/api/image_proxy/"):
        return ""
    crop = "-".join(str(n) for n in meter.settings.shape() or [])
    taken = "" if meter.frame_at is None else str(int(meter.frame_at.timestamp()))
    return f"![]({url}&crop={crop}&at={taken})"


def teach_placeholders(hass: HomeAssistant, entry_id: str, meter: Meter) -> dict[str, str]:
    """The picture, the digits learnt and an example of a reading."""
    known = meter.known()
    return {
        **counter_placeholders(meter),
        "frame_image": frame_picture(hass, entry_id, meter),
        "known": " ".join(str(d) for d in known) or "-",
        "unknown": " ".join(str(d) for d in range(10) if d not in known) or "-",
    }


def counter_placeholders(meter: Meter) -> dict[str, str]:
    """How many drums the counter has, and how a reading of it is written."""
    settings = meter.settings
    return {
        "digits": str(settings.digits),
        "decimals": str(settings.decimals),
        "example": _example(settings.digits, settings.decimals),
    }


async def async_teach(
    meter: Meter,
    options: dict[str, Any],
    user_input: dict[str, Any],
    picture: tuple[bytes, datetime] | None,
) -> dict[str, str]:
    """Teach the reading typed on picture, the frame the screen showed, or
    forget what was learnt. The errors, if any."""
    text = str(user_input.get(CONF_READING) or "").strip()
    forget = bool(user_input.get(CONF_FORGET, False))
    if not text and not forget:
        return {CONF_READING: "no_reading"}
    reading = None
    if text:
        reading = parse_reading(text, meter.settings)
        if reading is None:
            return {CONF_READING: "bad_reading"}
    try:
        await meter.async_configure(options, reading, forget, picture)
    except TeachError as err:
        return {"base": str(err)}
    return {}
