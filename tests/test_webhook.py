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

"""What the webhook takes from whoever holds its address, and what it
never writes to the log: the address holds the meter's secret."""

from __future__ import annotations

import asyncio
import gzip
import logging
import random
import threading
import time
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from tests import synth
from tests.test_integration import LAYOUT, _entry, _post


def _logged(caplog: pytest.LogCaptureFixture, entry: Any) -> bool:
    """Whether a warning or an error names the webhook id."""
    secret = entry.data["webhook_id"]
    return any(
        secret in record.getMessage() or secret in (record.exc_text or "")
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )


def _url(entry: Any) -> str:
    return f"/api/webhook/{entry.data['webhook_id']}"


async def test_a_body_without_a_length_is_refused(
    hass: HomeAssistant, hass_client_no_auth: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The phone always sends one. Without, aiohttp reads up to 16 MB and
    fails past that, an error Home Assistant logs with the webhook id."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"find": true}'

    for kind in ("image/jpeg", "application/json"):
        response = await client.post(_url(entry), data=chunks(), headers={"Content-Type": kind})
        assert response.status == HTTPStatus.LENGTH_REQUIRED
    assert not _logged(caplog, entry)


async def test_a_body_too_large_once_unpacked_is_refused(
    hass: HomeAssistant, hass_client_no_auth: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """aiohttp unpacks a gzip body: its length says nothing of the size."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    for data in (gzip.compress(bytes(17 * 1024 * 1024)), b"\x1f\x8b not gzip"):
        response = await client.post(
            _url(entry),
            data=data,
            headers={"Content-Type": "image/jpeg", "Content-Encoding": "gzip"},
        )
        assert response.status in (HTTPStatus.BAD_REQUEST, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    assert not _logged(caplog, entry)


async def test_finds_run_one_at_a_time(hass: HomeAssistant, hass_client_no_auth: Any) -> None:
    """Each decodes the last picture whole: side by side they would take
    the memory of as many pictures."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(3)))
    count = threading.Lock()
    running = 0
    most = 0

    def find(data: bytes) -> None:
        nonlocal running, most
        with count:
            running += 1
            most = max(most, running)
        time.sleep(0.05)
        with count:
            running -= 1

    with patch("custom_components.android_meter.meter._find_counter", find):
        replies = await asyncio.gather(
            *(client.post(_url(entry), json={"find": True}) for _ in range(4))
        )
    assert most == 1
    for reply in replies:
        assert (await reply.json())["error"] == "counter_not_found"


async def test_a_frame_while_another_waits_is_refused(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """The phone sends one at a time. Frames piling up behind a slow one
    would each hold up to 8 MB."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    data = synth.frame(1234567, LAYOUT, random.Random(4))
    meter = entry.runtime_data
    release = threading.Event()
    read = meter._read

    def slow(*args: Any) -> Any:
        release.wait(10)
        return read(*args)

    with patch.object(meter, "_read", slow):
        first = hass.async_create_task(_post(client, entry, data))
        while not meter._lock.locked():
            await asyncio.sleep(0)
        second = hass.async_create_task(_post(client, entry, data))
        for _ in range(50):
            if second.done():
                break
            await asyncio.sleep(0.01)
        done = second.done()
        release.set()
        assert done
        assert (await second).status == HTTPStatus.SERVICE_UNAVAILABLE
        assert (await first).status == HTTPStatus.OK
    assert (await _post(client, entry, data)).status == HTTPStatus.OK
