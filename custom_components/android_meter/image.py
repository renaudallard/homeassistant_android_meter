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

"""The last frame, with the windows outlined, for setting the crop."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import MeterConfigEntry, recognizer
from .entity import MeterEntity
from .meter import Meter


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([FrameImage(hass, entry.runtime_data, entry)])


class FrameImage(MeterEntity, ImageEntity):
    """The last frame received, turned upright and drawn over."""

    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, meter: Meter, entry: MeterConfigEntry) -> None:
        MeterEntity.__init__(self, meter, entry, "frame")
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = meter.frame_at
        self._shown = self._key()
        self._drawn: tuple[object, bytes] | None = None

    def _key(self) -> object:
        """What the drawing depends on: the frame, the crop and the read."""
        meter = self.meter
        return (meter.frame_at, meter.settings.geometry, meter.last)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.meter.async_add_listener(self._updated))

    @callback
    def _updated(self) -> None:
        """Mark the image updated whenever its drawing changes, a new crop
        included, so that the device page shows it again."""
        key = self._key()
        if key != self._shown:
            self._shown = key
            self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        meter = self.meter
        if meter.frame is None:
            return None
        key = self._key()
        if self._drawn is not None and self._drawn[0] == key:
            return self._drawn[1]
        try:
            image = await self.hass.async_add_executor_job(
                recognizer.render, meter.frame, meter.settings.geometry, meter.last
            )
        except recognizer.FrameError:
            return None
        self._drawn = (key, image)
        return image
