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

"""Diagnostics: what the meter last saw and how far it learnt."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import MeterConfigEntry
from .const import CONF_WEBHOOK_ID


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MeterConfigEntry
) -> dict[str, Any]:
    meter = entry.runtime_data
    last = meter.last
    return {
        "data": async_redact_data(dict(entry.data), {CONF_WEBHOOK_ID}),
        "options": dict(entry.options),
        "status": meter.status,
        "reading": None if meter.reading is None else str(meter.reading),
        "ceiling": None if meter.ceiling is None else str(meter.ceiling),
        "read_at": None if meter.read_at is None else meter.read_at.isoformat(),
        "frame_at": None if meter.frame_at is None else meter.frame_at.isoformat(),
        "frame_size": meter.frame_size,
        "masks_per_digit": meter.patches.counts(),
        "mask_drums": meter.patches.drums(),
        "last_read": None
        if last is None
        else {
            "refused": last.refused,
            "nearest": last.nearest,
            "trusted": last.trusted,
            "at_rest": last.at_rest,
            "learnt": last.learnt,
        },
    }
