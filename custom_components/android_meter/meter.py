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

"""One meter: its settings, its last frame, its reading and what it learnt.

The reading is kept as an interval. reading is what the sensor shows, the
lowest value the frames allow, and ceiling the highest one still as
likely: the meter stands in between. The next frame is read from reading
up to ceiling plus what the meter can have run since, at the largest flow
it is built for.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from http import HTTPStatus
from typing import Any

from aiohttp import web
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import recognizer
from .const import (
    CONF_DECIMALS,
    CONF_DIGITS,
    CONF_GAP,
    CONF_HEIGHT,
    CONF_INTERVAL,
    CONF_MAX_FLOW,
    CONF_ROTATION,
    CONF_TORCH,
    CONF_WIDTH,
    CONF_X,
    CONF_Y,
    DEFAULTS,
    DOMAIN,
    MAX_FRAME_BYTES,
    SILENCE_FLOOR,
    SILENCE_INTERVALS,
    STATUS_BAD_CROP,
    STATUS_BAD_FRAME,
    STATUS_NO_CROP,
    STATUS_OFFLINE,
    STATUS_OK,
    STATUS_UNTAUGHT,
    STATUS_WAITING,
    STORAGE_VERSION,
)

SAVE_DELAY = 60

_READING = re.compile(r"[0-9]{1,12}(?:[.,][0-9]{1,6})?")


class TeachError(Exception):
    """A reading could not be taught. The argument is the form error."""


@dataclass(frozen=True, slots=True)
class Settings:
    """The options of an entry, read once."""

    interval: int
    torch: bool
    geometry: recognizer.Geometry | None
    digits: int
    decimals: int
    max_flow: float

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> Settings:
        merged = {**DEFAULTS, **options}
        width = int(merged[CONF_WIDTH])
        height = int(merged[CONF_HEIGHT])
        digits = int(merged[CONF_DIGITS])
        geometry = None
        if width > 0 and height > 0:
            geometry = recognizer.Geometry(
                rotation=int(merged[CONF_ROTATION]),
                x=int(merged[CONF_X]),
                y=int(merged[CONF_Y]),
                width=width,
                height=height,
                digits=digits,
                gap=int(merged[CONF_GAP]),
            )
        return cls(
            interval=int(merged[CONF_INTERVAL]),
            torch=bool(merged[CONF_TORCH]),
            geometry=geometry,
            digits=digits,
            decimals=int(merged[CONF_DECIMALS]),
            max_flow=float(merged[CONF_MAX_FLOW]),
        )

    def shape(self) -> list[int] | None:
        """What decides how a cell looks; masks learnt under another are
        worthless."""
        g = self.geometry
        if g is None:
            return None
        return [g.rotation, g.x, g.y, g.width, g.height, g.digits, g.gap]


def parse_reading(text: str, settings: Settings) -> Decimal | None:
    """The reading typed in the options, or None when the counter cannot
    show it."""
    text = text.strip()
    if _READING.fullmatch(text) is None:
        return None
    value = Decimal(text.replace(",", "."))
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or -exponent > settings.decimals:
        return None
    if value >= Decimal(10) ** (settings.digits - settings.decimals):
        return None
    return value


def frame_error(settings: Settings, size: tuple[int, int] | None) -> str | None:
    """Why the crop does not fit a frame of size, as sent, or None."""
    g = settings.geometry
    if g is None:
        return None
    if settings.decimals > g.digits:
        return "too_many_decimals"
    if g.window < 4 or g.height < 8:
        return "windows_too_small"
    if size is None:
        return None
    width, height = size if g.rotation in (0, 180) else (size[1], size[0])
    if g.x + g.width > width or g.y + g.height > height:
        return "crop_outside"
    return None


def _decimal(value: object) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def teach_issue_id(entry_id: str) -> str:
    return f"teach_{entry_id}"


def _units(value: Decimal, decimals: int, rounding: str) -> int:
    """A volume in units of the last drum."""
    return int(value.scaleb(decimals).to_integral_value(rounding=rounding))


def _volume(units: int, decimals: int) -> Decimal:
    return Decimal(units).scaleb(-decimals)


def _time(value: object) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


class Meter:
    """The live state of one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: Mapping[str, Any]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self._lock = asyncio.Lock()
        self.settings = Settings.from_options(options)
        self.patches = recognizer.Patches()
        self.reading: Decimal | None = None
        self.ceiling: Decimal | None = None
        self.read_at: datetime | None = None
        self.status = STATUS_WAITING
        self.frame: bytes | None = None
        self.frame_at: datetime | None = None
        self.frame_size: tuple[int, int] | None = None
        self.last: recognizer.Read | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._silence: CALLBACK_TYPE | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict):
            self.reading = _decimal(data.get("reading"))
            self.ceiling = _decimal(data.get("ceiling"))
            self.read_at = _time(data.get("read_at"))
            if data.get("shape") == self.settings.shape():
                self.patches = recognizer.Patches.load(data.get("patches"))
        if self.reading is None:
            self.ceiling = None
            self.read_at = None
        else:
            if self.ceiling is None or self.ceiling < self.reading:
                self.ceiling = self.reading
            if self.read_at is None:
                self.read_at = dt_util.utcnow()
        self._arm_silence()
        self._sync_issue()

    async def async_unload(self) -> None:
        if self._silence is not None:
            self._silence()
            self._silence = None
        await self._store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {
            "reading": None if self.reading is None else str(self.reading),
            "ceiling": None if self.ceiling is None else str(self.ceiling),
            "read_at": None if self.read_at is None else self.read_at.isoformat(),
            "shape": self.settings.shape(),
            "patches": self.patches.dump(),
        }

    @callback
    def _save(self) -> None:
        data = self._data()
        self._store.async_delay_save(lambda: data, SAVE_DELAY)

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def _notify(self) -> None:
        self._sync_issue()
        for update in list(self._listeners):
            update()

    def untaught(self) -> bool:
        """Whether the crop is set but no reading has been learnt yet."""
        return self.settings.geometry is not None and (
            self.reading is None or not any(self.patches.counts())
        )

    @callback
    def _sync_issue(self) -> None:
        """Raise the Repairs notice asking for a reading, or clear it."""
        issue = teach_issue_id(self.entry_id)
        if self.untaught():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="teach",
                data={"entry_id": self.entry_id},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue)

    @callback
    def _arm_silence(self) -> None:
        if self._silence is not None:
            self._silence()
        delay = max(SILENCE_INTERVALS * self.settings.interval, SILENCE_FLOOR)
        self._silence = async_call_later(self.hass, delay, self._silent)

    @callback
    def _silent(self, _now: datetime) -> None:
        self._silence = None
        self.status = STATUS_OFFLINE
        self._notify()

    def _bounds(self, now: datetime) -> tuple[int, int] | None:
        if self.reading is None or self.ceiling is None or self.read_at is None:
            return None
        decimals = self.settings.decimals
        seconds = max(0.0, (now - self.read_at).total_seconds())
        run = math.ceil(self.settings.max_flow * seconds / 3600 * 10**decimals)
        low = _units(self.reading, decimals, ROUND_FLOOR)
        return low, _units(self.ceiling, decimals, ROUND_CEILING) + run + 1

    def known(self) -> list[int]:
        """The digits learnt so far."""
        return [digit for digit, count in enumerate(self.patches.counts()) if count]

    def _read(
        self,
        data: bytes,
        geometry: recognizer.Geometry | None,
        bounds: tuple[int, int] | None,
    ) -> tuple[tuple[int, int] | None, str, recognizer.Read | None]:
        """Read a frame. Runs in an executor, under the lock."""
        rotation = 0 if geometry is None else geometry.rotation
        try:
            frame = recognizer.open_frame(data, rotation)
        except recognizer.FrameError:
            return None, STATUS_BAD_FRAME, None
        size = frame.size if rotation in (0, 180) else (frame.height, frame.width)
        if geometry is None:
            return size, STATUS_NO_CROP, None
        try:
            cells = recognizer.cut(frame, geometry)
        except recognizer.FrameError:
            return size, STATUS_BAD_CROP, None
        if bounds is None or not any(self.patches.counts()):
            return size, STATUS_UNTAUGHT, None
        read = recognizer.read(cells, self.patches, *bounds)
        return size, STATUS_OK if read.refused is None else read.refused, read

    async def async_frame(self, data: bytes) -> None:
        """Take a frame from the phone."""
        async with self._lock:
            now = dt_util.utcnow()
            size, status, read = await self.hass.async_add_executor_job(
                self._read, data, self.settings.geometry, self._bounds(now)
            )
            if size is not None:
                self.frame = data
                self.frame_at = now
                self.frame_size = size
            self.status = status
            self.last = read
            if read is not None and read.value is not None and read.ceiling is not None:
                decimals = self.settings.decimals
                value = _volume(read.value, decimals)
                if self.reading is None or value > self.reading:
                    self.reading = value
                self.ceiling = max(_volume(read.ceiling, decimals), self.reading)
                self.read_at = now
                self._save()
            self._arm_silence()
        self._notify()

    def _teach(
        self, data: bytes, geometry: recognizer.Geometry, value: int, into: recognizer.Patches
    ) -> int:
        """Learn the last frame as value. Runs in an executor, under the lock."""
        try:
            frame = recognizer.open_frame(data, geometry.rotation)
            cells = recognizer.cut(frame, geometry)
        except recognizer.FrameError:
            return -1
        return recognizer.learn_reading(cells, into, value)

    async def async_configure(
        self,
        options: Mapping[str, Any],
        reading: Decimal | None = None,
        forget: bool = False,
    ) -> None:
        """Take new options, and maybe a reading of the last frame to learn.

        Changing the crop, or asking to forget, drops what was learnt.
        Nothing changes when the reading cannot be taught.
        """
        async with self._lock:
            settings = Settings.from_options(options)
            fresh = forget or settings.shape() != self.settings.shape()
            patches = recognizer.Patches() if fresh else self.patches
            if reading is not None:
                if settings.geometry is None:
                    raise TeachError("no_crop")
                if self.frame is None:
                    raise TeachError("no_frame")
                value = _units(reading, settings.decimals, ROUND_FLOOR)
                learnt = await self.hass.async_add_executor_job(
                    self._teach, self.frame, settings.geometry, value, patches
                )
                if learnt < 0:
                    raise TeachError("crop_outside")
                if learnt == 0:
                    raise TeachError("nothing_at_rest")
            self.settings = settings
            self.patches = patches
            if reading is not None:
                self.reading = reading
                self.ceiling = reading
                self.read_at = self.frame_at
                self.status = STATUS_OK
                self.last = None
            elif fresh:
                self.status = STATUS_UNTAUGHT if settings.geometry is not None else STATUS_NO_CROP
                self.last = None
            self._save()
            self._arm_silence()
        self._notify()

    async def async_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """A frame from the phone. The answer tells it when to send the next."""
        if request.content_type != "image/jpeg":
            return web.Response(status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        length = request.content_length
        if length is not None and length > MAX_FRAME_BYTES:
            return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        data = await request.read()
        if len(data) > MAX_FRAME_BYTES:
            return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if not data:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        await self.async_frame(data)
        return web.json_response(
            {
                "interval": self.settings.interval,
                "torch": self.settings.torch,
                "status": self.status,
            }
        )
