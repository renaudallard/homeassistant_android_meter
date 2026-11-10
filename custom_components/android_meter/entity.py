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

"""What the entities of a meter share."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    CONF_DECIMALS,
    CONF_HEIGHT,
    CONF_ROTATION,
    CONF_WIDTH,
    CONF_X,
    CONF_Y,
    DOMAIN,
    GEOMETRY,
)
from .meter import Meter, Settings, frame_error


class MeterEntity(Entity):
    """An entity that follows its meter."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, meter: Meter, entry: ConfigEntry, key: str) -> None:
        self.meter = meter
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            model="Android camera",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.meter.async_add_listener(self.async_write_ha_state))

    async def _async_apply(self, **changes: Any) -> None:
        """Apply option changes from a control on the device page.

        Only real changes count. A crop locked by a reading taught is
        refused, and so is one that does not fit the last picture; turning
        the picture clears the crop. What is applied is stored in the
        entry's options too.
        """
        meter = self.meter
        changes = {key: value for key, value in changes.items() if meter.options.get(key) != value}
        if not changes:
            return
        if meter.locked and any(key in (*GEOMETRY, CONF_DECIMALS) for key in changes):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="crop_locked")
        if CONF_ROTATION in changes:
            # The crop is measured on the turned picture: a new turn starts it anew.
            changes.update({CONF_X: 0, CONF_Y: 0, CONF_WIDTH: 0, CONF_HEIGHT: 0})
        options = {**meter.options, **changes}
        error = frame_error(Settings.from_options(options), meter.frame_size)
        if error is not None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key=error)
        await meter.async_configure(options)
        self.hass.config_entries.async_update_entry(self._entry, options=options)
