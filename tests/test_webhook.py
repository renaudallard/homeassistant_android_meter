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
import random
import threading
import time
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from tests import synth
from tests.test_integration import LAYOUT, _entry, _post


def _url(entry: Any) -> str:
    return f"/api/webhook/{entry.data['webhook_id']}"


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
