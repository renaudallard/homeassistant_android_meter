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

"""The Repairs notice that asks for a reading, and its fix.

The fix is the teaching screen of the options: the last picture, the
digits learnt, and the reading to type.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from . import MeterConfigEntry
from .teach import async_teach, teach_placeholders, teach_schema


class TeachFlow(RepairsFlow):
    """Teach the reading of one meter."""

    def __init__(self, entry: MeterConfigEntry) -> None:
        self._entry = entry
        self._picture: tuple[bytes, datetime] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        entry = self._entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        meter = entry.runtime_data
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await async_teach(meter, user_input, self._picture)
            if not errors:
                return self.async_create_entry(data={})
        self._picture = meter.picture()
        return self.async_show_form(
            step_id="init",
            data_schema=teach_schema(),
            errors=errors,
            description_placeholders=teach_placeholders(self.hass, entry.entry_id, meter),
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    entry_id = data.get("entry_id") if data else None
    entry = hass.config_entries.async_get_entry(entry_id) if isinstance(entry_id, str) else None
    if entry is None:
        raise ValueError("unknown meter")
    return TeachFlow(entry)
