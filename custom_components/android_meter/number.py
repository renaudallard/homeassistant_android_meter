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

"""The crop and the counter as controls on the device page.

The crop sliders run over the last picture, turned upright, so they can
only place the crop on it. Each change redraws the Frame image.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MeterConfigEntry
from .const import (
    CONF_DECIMALS,
    CONF_DIGITS,
    CONF_GAP,
    CONF_HEIGHT,
    CONF_ROTATION,
    CONF_WIDTH,
    CONF_X,
    CONF_Y,
    MAX_DECIMALS,
    MAX_DIGITS,
)
from .entity import MeterEntity
from .meter import Meter

# Before the first picture the sliders still need a range.
_NO_FRAME = 4000


@dataclass(frozen=True, kw_only=True)
class MeterNumberDescription(NumberEntityDescription):
    """An option shown as a number; across says which side of the picture
    bounds it, None for a count."""

    across: bool | None = None


DESCRIPTIONS = (
    MeterNumberDescription(key=CONF_X, translation_key="crop_left", across=True),
    MeterNumberDescription(key=CONF_Y, translation_key="crop_top", across=False),
    MeterNumberDescription(key=CONF_WIDTH, translation_key="crop_width", across=True),
    MeterNumberDescription(key=CONF_HEIGHT, translation_key="crop_height", across=False),
    MeterNumberDescription(key=CONF_GAP, translation_key="gap", across=True),
    MeterNumberDescription(
        key=CONF_DIGITS,
        translation_key="digits",
        native_min_value=1,
        native_max_value=MAX_DIGITS,
        mode=NumberMode.BOX,
    ),
    MeterNumberDescription(
        key=CONF_DECIMALS,
        translation_key="decimals",
        native_min_value=0,
        native_max_value=MAX_DECIMALS,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    meter = entry.runtime_data
    async_add_entities(MeterNumber(meter, entry, description) for description in DESCRIPTIONS)


class MeterNumber(MeterEntity, NumberEntity):
    """One crop or counter option."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1
    entity_description: MeterNumberDescription

    def __init__(
        self, meter: Meter, entry: MeterConfigEntry, description: MeterNumberDescription
    ) -> None:
        super().__init__(meter, entry, description.translation_key or description.key)
        self.entity_description = description
        if description.across is not None:
            self._attr_mode = NumberMode.SLIDER
            self._attr_native_unit_of_measurement = "px"

    def _side(self) -> int:
        """The width or height of the last picture, turned upright."""
        size = self.meter.frame_size
        if size is None:
            return _NO_FRAME
        turned = int(self.meter.options[CONF_ROTATION]) in (90, 270)
        width, height = (size[1], size[0]) if turned else size
        return width if self.entity_description.across else height

    @property
    def native_min_value(self) -> float:
        low = self.entity_description.native_min_value
        return 0.0 if low is None else float(low)

    @property
    def native_max_value(self) -> float:
        high = self.entity_description.native_max_value
        if self.entity_description.across is None and high is not None:
            return float(high)
        if self.entity_description.key == CONF_GAP:
            return float(self._side() // max(1, int(self.meter.options[CONF_DIGITS])))
        return float(self._side())

    @property
    def native_value(self) -> float:
        return float(self.meter.options[self.entity_description.key])

    async def async_set_native_value(self, value: float) -> None:
        await self._async_apply(**{self.entity_description.key: int(value)})
