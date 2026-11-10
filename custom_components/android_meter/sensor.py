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

"""The reading of the meter, and how its reads go."""

from __future__ import annotations

from decimal import Decimal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MeterConfigEntry
from .const import STATUSES
from .entity import MeterEntity
from .meter import Meter


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    meter = entry.runtime_data
    async_add_entities(
        [
            ReadingSensor(meter, entry, "reading"),
            StatusSensor(meter, entry, "status"),
            LearntSensor(meter, entry, "learnt_digits"),
            ToLearnSensor(meter, entry, "digits_to_learn"),
        ]
    )


class ReadingSensor(MeterEntity, SensorEntity):
    """The volume the counter shows, as the Energy dashboard wants it."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, meter: Meter, entry: MeterConfigEntry, key: str) -> None:
        super().__init__(meter, entry, key)
        self._decimals = meter.settings.decimals

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.meter.async_add_listener(self._updated))

    @callback
    def _updated(self) -> None:
        """Store the new precision when the decimal drums change. Home
        Assistant reads it when the entity is added and when its registry
        entry changes, not on a state write."""
        decimals = self.meter.settings.decimals
        if decimals != self._decimals and self.registry_entry is not None:
            self._decimals = decimals
            self.async_registry_entry_updated()
        self.async_write_ha_state()

    @property
    def suggested_display_precision(self) -> int:
        return self.meter.settings.decimals

    @property
    def native_value(self) -> Decimal | None:
        return self.meter.reading


class StatusSensor(MeterEntity, SensorEntity):
    """How the last frame went, or why none came."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, meter: Meter, entry: MeterConfigEntry, key: str) -> None:
        super().__init__(meter, entry, key)
        self._attr_options = list(STATUSES)

    @property
    def native_value(self) -> str:
        return self.meter.status


class LearntSensor(MeterEntity, SensorEntity):
    """How many of the ten digits have been learnt."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int:
        return len(self.meter.known())


class ToLearnSensor(MeterEntity, SensorEntity):
    """The digits still to learn, so that a drum showing one is known to be
    left out and teaching again when one shows learns it at once."""

    @property
    def native_value(self) -> str:
        known = self.meter.known()
        return " ".join(str(digit) for digit in range(10) if digit not in known) or "none"
