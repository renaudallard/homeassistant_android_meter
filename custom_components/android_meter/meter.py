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
import json
import logging
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

from . import autocrop, recognizer
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
    DEFAULT_NAME,
    DEFAULTS,
    DOMAIN,
    MAX_DECIMALS,
    MAX_DIGITS,
    MAX_FRAME_BYTES,
    MAX_MESSAGE_BYTES,
    ROTATIONS,
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

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 60

# What the crop and counter controls, and the phone's crop editor, change.
CROP_BOX = (CONF_X, CONF_Y, CONF_WIDTH, CONF_HEIGHT)
CROP_FIELDS = (CONF_ROTATION, *CROP_BOX, CONF_GAP, CONF_DIGITS, CONF_DECIMALS)
# A change of these forgets the digits learnt or what a reading means, so
# a locked crop refuses them.
LOCKED = CROP_FIELDS
_CROP_RANGES = {
    CONF_ROTATION: (0, 270),
    CONF_X: (0, 20000),
    CONF_Y: (0, 20000),
    CONF_WIDTH: (0, 20000),
    CONF_HEIGHT: (0, 20000),
    CONF_GAP: (0, 2000),
    CONF_DIGITS: (1, MAX_DIGITS),
    CONF_DECIMALS: (0, MAX_DECIMALS),
}

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
    """The reading typed to teach, or None when it is not one.

    It is the whole meter: a crop may leave drums out when the phone
    cannot see them all. Drums left of the crop are kept, and decimals
    right of it are dropped, as the crop cannot read them.
    """
    text = text.strip()
    if _READING.fullmatch(text) is None:
        return None
    value = Decimal(text.replace(",", "."))
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > settings.decimals:
        value = value.quantize(Decimal(1).scaleb(-settings.decimals), rounding=ROUND_FLOOR)
    return value


def frame_error(settings: Settings, size: tuple[int, int] | None) -> str | None:
    """Why the counter's settings do not hold, or the crop does not fit a
    frame of size, as sent, or None."""
    if settings.decimals > settings.digits:
        return "too_many_decimals"
    g = settings.geometry
    if g is None:
        return None
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


def _find_counter(data: bytes) -> dict[str, int] | None:
    """The crop of the counter found on a picture. Runs in an executor."""
    try:
        frame = recognizer.open_frame(data, 0, "RGB")
    except recognizer.FrameError:
        return None
    crop = autocrop.find(frame)
    # A row of more drums than a counter has is not one.
    return None if crop is None else _checked(crop)


async def _read(request: web.Request, limit: int) -> bytes | None:
    """The body of a request, or None when it is over limit. aiohttp
    unpacks a body sent compressed, which its length does not bound. One
    that cannot be unpacked, or cut short as the phone lost the network,
    reads as no body."""
    if (request.content_length or 0) > limit:
        return None
    try:
        data = await request.read()
    except web.HTTPRequestEntityTooLarge:
        return None
    except web.RequestPayloadError, ConnectionResetError:
        return b""
    return data if len(data) <= limit else None


def _find_message(body: bytes) -> bool:
    """Whether a message from the phone is {"find": true}."""
    try:
        message = json.loads(body)
    except ValueError:
        return False
    return bool(message == {"find": True})


def _crop_message(body: bytes) -> dict[str, int] | None:
    """The crop fields of a message from the phone, each a whole number in
    its range, or None when the message is anything else."""
    try:
        message = json.loads(body)
    except ValueError:
        return None
    crop = message.get("crop") if isinstance(message, dict) else None
    return _checked(crop) if isinstance(crop, dict) else None


def _checked(crop: dict[str, Any]) -> dict[str, int] | None:
    """Crop fields, if each is a whole number in its range, or None."""
    if not crop or not set(crop) <= set(CROP_FIELDS):
        return None
    for key, value in crop.items():
        low, high = _CROP_RANGES[key]
        if type(value) is not int or not low <= value <= high:
            return None
    if CONF_ROTATION in crop and crop[CONF_ROTATION] not in ROTATIONS:
        return None
    return dict(crop)


def _time(value: object) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


class Meter:
    """The live state of one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: Mapping[str, Any]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self._lock = asyncio.Lock()
        self.options: dict[str, Any] = {**DEFAULTS, **options}
        self.settings = Settings.from_options(options)
        self.locked = False
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
        # When the phone last sent a frame, or when the meter started.
        self._heard = dt_util.utcnow()
        # Unloaded: frames and changes still on their way are dropped.
        self._closed = False
        # A frame is being received or read: the phone sends one at a time.
        self._receiving = False
        # Each Find decodes the whole picture: one at a time.
        self._finding = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict):
            self.reading = _decimal(data.get("reading"))
            self.ceiling = _decimal(data.get("ceiling"))
            self.read_at = _time(data.get("read_at"))
            self.locked = data.get("locked") is True
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
        self.async_sync_issue()

    async def async_unload(self) -> None:
        """Store the state once the frame or change under way is done, and
        take no more, which would arm the timer and the store again."""
        async with self._lock:
            self._closed = True
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
            "locked": self.locked,
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
        self.async_sync_issue()
        for update in list(self._listeners):
            update()

    def untaught(self) -> bool:
        """Whether the crop is set but no reading has been learnt yet."""
        return self.settings.geometry is not None and (
            self.reading is None or not any(self.patches.counts())
        )

    @callback
    def async_sync_issue(self) -> None:
        """Raise the Repairs notice asking for a reading, or clear it.

        It is the entry's: Home Assistant hides it while the meter is
        disabled and removes it with the meter. It stays over a reload,
        so that one ignored stays so.
        """
        issue = teach_issue_id(self.entry_id)
        if self.untaught():
            # Named, as there may be more than one meter.
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue,
                config_entry_id=self.entry_id,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="teach",
                translation_placeholders={"name": entry.title if entry else DEFAULT_NAME},
                data={"entry_id": self.entry_id},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue)

    @callback
    def _arm_silence(self) -> None:
        """Mark the phone offline once it has been silent too long, counted
        from the last frame it sent."""
        if self._silence is not None:
            self._silence()
            self._silence = None
        if self.status == STATUS_OFFLINE:
            return
        delay = max(SILENCE_INTERVALS * self.settings.interval, SILENCE_FLOOR)
        left = delay - (dt_util.utcnow() - self._heard).total_seconds()
        self._silence = async_call_later(self.hass, max(0.0, left), self._silent)

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

    def picture(self) -> tuple[bytes, datetime] | None:
        """The last frame and when it came, as a teaching screen shows it."""
        if self.frame is None or self.frame_at is None:
            return None
        return self.frame, self.frame_at

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
            if self._closed:
                return
            now = dt_util.utcnow()
            self._heard = now
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
        """Learn a frame as value. Runs in an executor, under the lock."""
        try:
            frame = recognizer.open_frame(data, geometry.rotation)
            cells = recognizer.cut(frame, geometry)
        except recognizer.FrameError:
            return -1
        return recognizer.learn_reading(cells, into, value)

    async def async_configure(
        self,
        changes: Mapping[str, Any] | None = None,
        reading: Decimal | None = None,
        forget: bool = False,
        picture: tuple[bytes, datetime] | None = None,
    ) -> None:
        """Take option changes, and maybe the reading picture shows, to learn.

        picture is the frame the reading was read from, as picture() gave
        it when the screen was drawn: frames that came in since show other
        drums. Changing the crop, or asking to forget, drops what was
        learnt. Changing the decimal drums drops the reading, which the
        drums no longer show on the same scale. Nothing changes when the
        reading cannot be taught. A reading taught locks the crop, so that
        a slip on the device page cannot undo it.

        The changes go over the options in force once the lock is held, so
        that a crop changed meanwhile is kept.
        """
        async with self._lock:
            if self._closed:
                return
            await self._configure({**self.options, **(changes or {})}, reading, forget, picture)
        self._notify()

    async def _configure(
        self,
        options: Mapping[str, Any],
        reading: Decimal | None,
        forget: bool,
        picture: tuple[bytes, datetime] | None,
    ) -> None:
        """What async_configure does, under the lock. The options applied
        are stored in the entry too, and the state at once with them: a
        reading kept from before new decimal drums would be read on the
        wrong scale."""
        settings = Settings.from_options(options)
        fresh = forget or settings.shape() != self.settings.shape()
        rescaled = settings.decimals != self.settings.decimals
        patches = recognizer.Patches() if fresh else self.patches
        taught: Decimal | None = None
        if reading is not None:
            if settings.geometry is None:
                raise TeachError("no_crop")
            if picture is None:
                raise TeachError("no_frame")
            value = _units(reading, settings.decimals, ROUND_FLOOR)
            learnt = await self.hass.async_add_executor_job(
                self._teach, picture[0], settings.geometry, value, patches
            )
            if learnt < 0:
                raise TeachError("crop_outside")
            if learnt == 0:
                raise TeachError("nothing_at_rest")
            # Typed against the decimals there were before the lock.
            taught = _volume(value, settings.decimals)
        self.options = {**DEFAULTS, **options}
        self.settings = settings
        self.patches = patches
        # Only a frame ends the wait for the phone.
        silent = self.status in (STATUS_OFFLINE, STATUS_WAITING)
        if taught is not None and picture is not None:
            self.reading = taught
            self.ceiling = taught
            self.read_at = picture[1]
            if not silent:
                self.status = STATUS_OK
            self.last = None
            self.locked = True
        elif fresh or rescaled:
            if rescaled:
                self.reading = None
                self.ceiling = None
                self.read_at = None
            if not silent:
                self.status = STATUS_UNTAUGHT if settings.geometry is not None else STATUS_NO_CROP
            self.last = None
        await self._store.async_save(self._data())
        self._arm_silence()
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is not None:
            self.hass.config_entries.async_update_entry(entry, options=self.options)

    async def async_change(self, changes: Mapping[str, Any]) -> str | None:
        """Apply option changes from the device page or the phone.

        Only real changes count. A crop locked by a reading taught is
        refused, and so is one that does not fit the last picture; turning
        the picture without giving a crop clears the crop, which is
        measured on the turned picture. What is applied is stored in the
        entry's options too. Returns the error, or None.

        The lock is held from the first look at the options on, so that a
        reading being taught, or another change, is waited for. One that
        comes as the meter is unloaded is refused.
        """
        async with self._lock:
            if self._closed:
                return "not_loaded"
            given = set(changes)
            changed = {
                key: value for key, value in changes.items() if self.options.get(key) != value
            }
            if not changed:
                return None
            if self.locked and any(key in LOCKED for key in changed):
                return "crop_locked"
            if CONF_ROTATION in changed and not given & set(CROP_BOX):
                changed.update(dict.fromkeys(CROP_BOX, 0))
            options = {**self.options, **changed}
            error = frame_error(Settings.from_options(options), self.frame_size)
            if error is not None:
                return error
            await self._configure(options, None, False, None)
        self._notify()
        return None

    async def async_find_crop(self) -> str | None:
        """Find the counter on the last picture and apply its crop.

        Returns the error, or None. Finds asked meanwhile wait: each
        decodes the whole picture, and side by side they would take the
        memory of as many.
        """
        async with self._finding:
            frame = self.frame
            if frame is None:
                return "no_frame"
            crop = await self.hass.async_add_executor_job(_find_counter, frame)
            if crop is None:
                return "counter_not_found"
            return await self.async_change(crop)

    def crop(self) -> dict[str, Any]:
        """The crop and counter in force, for the phone's crop editor."""
        view: dict[str, Any] = {key: int(self.options[key]) for key in CROP_FIELDS}
        view["locked"] = self.locked
        return view

    @callback
    def async_set_locked(self, locked: bool) -> None:
        """Lock the crop against changes, or let it be changed."""
        self.locked = locked
        self._save()
        self._notify()

    async def async_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """A frame from the phone, or a message from it.

        The answer tells the phone when to send the next frame, and the
        crop in force for its editor. An error is logged here: Home
        Assistant would log it with the webhook id, the meter's secret.
        """
        try:
            return await self._async_answer(request)
        except Exception:
            _LOGGER.exception("Error handling what the phone sent to meter %s", self.entry_id)
            return web.Response(status=HTTPStatus.INTERNAL_SERVER_ERROR)

    async def _async_answer(self, request: web.Request) -> web.Response:
        """What async_webhook answers. A body must say its length, as the
        phone's do: aiohttp would read one that does not up to 16 MB. The
        phone sends one frame at a time, and frames piling up behind a
        slow one would each hold up to 8 MB: one coming while another is
        received or read is refused."""
        if request.content_length is None:
            return web.Response(status=HTTPStatus.LENGTH_REQUIRED)
        if request.content_type == "application/json":
            return await self._async_message(request)
        if request.content_type != "image/jpeg":
            return web.Response(status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        if self._receiving:
            return web.Response(status=HTTPStatus.SERVICE_UNAVAILABLE)
        self._receiving = True
        try:
            data = await _read(request, MAX_FRAME_BYTES)
            if data is None:
                return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            if not data:
                return web.Response(status=HTTPStatus.BAD_REQUEST)
            await self.async_frame(data)
        finally:
            self._receiving = False
        return web.json_response(
            {
                "interval": self.settings.interval,
                "torch": self.settings.torch,
                "status": self.status,
                "crop": self.crop(),
            }
        )

    async def _async_message(self, request: web.Request) -> web.Response:
        """Apply {"crop": {...}} drawn on the phone, on the picture as sent,
        or {"find": true}, the counter found on it."""
        body = await _read(request, MAX_MESSAGE_BYTES)
        if body is None:
            return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if _find_message(body):
            error = await self.async_find_crop()
            return web.json_response({"error": error, "crop": self.crop()})
        changes = _crop_message(body)
        if changes is None:
            return web.json_response({"error": "bad_message"}, status=HTTPStatus.BAD_REQUEST)
        error = await self.async_change(changes)
        return web.json_response({"error": error, "crop": self.crop()})
