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

"""The integration in Home Assistant: setup, webhook, options, entities."""

from __future__ import annotations

import random
from datetime import timedelta
from http import HTTPStatus
from typing import Any
from unittest.mock import patch
from urllib.parse import quote

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import async_fire_time_changed
from pytest_homeassistant.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.android_meter.const import DOMAIN
from tests import synth

LAYOUT = synth.Layout()
INTERNAL_URL = "http://192.168.1.10:8123"
CROP = {
    "x": LAYOUT.x,
    "y": LAYOUT.y,
    "width": LAYOUT.width,
    "height": LAYOUT.height,
    "gap": synth.GAP,
    "digits": LAYOUT.digits,
    "decimals": LAYOUT.decimals,
}


async def _entry(hass: HomeAssistant, internal_url: str | None = INTERNAL_URL) -> Any:
    assert await async_setup_component(hass, "http", {})
    if internal_url is not None:
        await hass.config.async_update(internal_url=internal_url)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Gas meter"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair"
    url = result["description_placeholders"]["webhook_url"]
    assert "/api/webhook/" in url
    if internal_url is None:
        assert url.startswith("/api/webhook/")
    qr = _qr(result)
    if internal_url is None:
        assert qr is None
    else:
        assert url.startswith(internal_url)
        assert qr == "androidmeter://setup?url=" + quote(url, safe="")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[0]


def _qr(result: Any) -> str | None:
    """The data of the pairing QR code a form shows, if any."""
    for key, selector in result["data_schema"].schema.items():
        if str(key) == "qr_code":
            return str(selector.config["data"])
    return None


async def _post(client: Any, entry: Any, data: bytes, kind: str = "image/jpeg") -> Any:
    return await client.post(
        f"/api/webhook/{entry.data['webhook_id']}", data=data, headers={"Content-Type": kind}
    )


async def _options(hass: HomeAssistant, entry: Any, **fields: Any) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], fields)
    await hass.async_block_till_done()
    return dict(result)


async def test_a_frame_is_read_once_taught(
    hass: HomeAssistant, hass_client_no_auth: Any, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    rng = random.Random(1)

    response = await _post(client, entry, synth.frame(89012345, LAYOUT, rng, light=0.9, shade=0))
    assert response.status == HTTPStatus.OK
    assert await response.json() == {"interval": 60, "torch": True, "status": "no_crop"}
    assert hass.states.get("sensor.gas_meter_status").state == "no_crop"
    assert hass.states.get("sensor.gas_meter_reading").state == "unknown"

    result = await _options(hass, entry, **CROP, reading="89012.345")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "reading" not in entry.options
    assert hass.states.get("sensor.gas_meter_reading").state == "89012.345"

    await _post(client, entry, synth.frame(1234567, LAYOUT, rng, light=0.9, shade=0))
    result = await _options(hass, entry, **CROP, reading="01234,567")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.states.get("sensor.gas_meter_learnt_digits").state == "10"
    assert hass.states.get("sensor.gas_meter_reading").state == "1234.567"

    freezer.tick(timedelta(seconds=60))
    response = await _post(client, entry, synth.frame(1234590, LAYOUT, rng, light=0.9, shade=0))
    assert (await response.json())["status"] == "ok"
    state = hass.states.get("sensor.gas_meter_reading")
    assert state.state == "1234.590"
    assert state.attributes["unit_of_measurement"] == "m³"
    assert state.attributes["device_class"] == "gas"
    assert state.attributes["state_class"] == "total_increasing"

    freezer.tick(timedelta(seconds=60))
    response = await _post(client, entry, synth.frame(1200000, LAYOUT, rng, light=0.9, shade=0))
    assert (await response.json())["status"] == "implausible"
    assert hass.states.get("sensor.gas_meter_reading").state == "1234.590"


async def test_the_webhook_refuses_what_is_not_a_frame(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    response = await _post(client, entry, b"hello", "text/plain")
    assert response.status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    response = await _post(client, entry, b"")
    assert response.status == HTTPStatus.BAD_REQUEST
    response = await _post(client, entry, b"not a jpeg")
    assert (await response.json())["status"] == "bad_frame"
    assert hass.states.get("sensor.gas_meter_status").state == "bad_frame"


async def test_options_errors(hass: HomeAssistant, hass_client_no_auth: Any) -> None:
    entry = await _entry(hass)
    client = await hass_client_no_auth()

    result = await _options(hass, entry, **CROP, reading="1234.567")
    assert result["errors"] == {"base": "no_frame"}
    result = await _options(hass, entry, reading="1234.567")
    assert result["errors"] == {"base": "no_crop"}

    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(2)))
    result = await _options(hass, entry, **{**CROP, "x": 600})
    assert result["errors"] == {"base": "crop_outside"}
    result = await _options(hass, entry, **{**CROP, "digits": 4, "decimals": 5})
    assert result["errors"] == {"base": "too_many_decimals"}
    result = await _options(hass, entry, **CROP, reading="123456.789")
    assert result["errors"] == {"reading": "bad_reading"}
    result = await _options(hass, entry, **CROP, reading="1234.5678")
    assert result["errors"] == {"reading": "bad_reading"}
    assert entry.options["width"] == 0


async def test_the_phone_goes_offline(
    hass: HomeAssistant, hass_client_no_auth: Any, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(3)))
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.gas_meter_status").state == "offline"


async def test_frame_image_and_diagnostics(
    hass: HomeAssistant, hass_client: Any, hass_client_no_auth: Any
) -> None:
    entry = await _entry(hass)
    await _post(await hass_client_no_auth(), entry, synth.frame(1234567, LAYOUT, random.Random(4)))
    await _options(hass, entry, **CROP, reading="1234.567")

    client = await hass_client()
    response = await client.get("/api/image_proxy/image.gas_meter_frame")
    assert response.status == HTTPStatus.OK
    assert response.content_type == "image/jpeg"
    assert (await response.read())[:2] == b"\xff\xd8"

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["data"]["webhook_id"] == "**REDACTED**"
    assert entry.data["webhook_id"] not in str(diagnostics)
    assert diagnostics["reading"] == "1234.567"
    assert diagnostics["frame_size"] == list(LAYOUT.frame)


async def test_the_reading_survives_a_restart_and_removal_cleans_up(
    hass: HomeAssistant, hass_client_no_auth: Any, hass_storage: dict[str, Any]
) -> None:
    entry = await _entry(hass)
    await _post(await hass_client_no_auth(), entry, synth.frame(1234567, LAYOUT, random.Random(5)))
    await _options(hass, entry, **CROP, reading="1234.567")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    key = f"{DOMAIN}.{entry.entry_id}"
    assert hass_storage[key]["data"]["reading"] == "1234.567"
    assert hass.states.get("sensor.gas_meter_reading").state == "1234.567"
    assert hass.states.get("sensor.gas_meter_learnt_digits").state != "0"

    await _options(hass, entry, **{**CROP, "x": CROP["x"] + 1})
    assert hass.states.get("sensor.gas_meter_learnt_digits").state == "0"
    assert hass.states.get("sensor.gas_meter_status").state == "untaught"

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_pairing_code(hass: HomeAssistant) -> None:
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    qr = _qr(result)
    assert qr is not None
    assert qr.startswith(
        "androidmeter://setup?url=http%3A%2F%2F192.168.1.10%3A8123%2Fapi%2Fwebhook%2F"
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "qr_code" not in entry.options


async def test_no_pairing_code_without_an_address(hass: HomeAssistant) -> None:
    """With no address for Home Assistant at all, only the path is shown."""

    def nowhere(*_args: object, **_kwargs: object) -> str:
        raise NoURLAvailableError

    with patch("custom_components.android_meter.config_flow.webhook.async_generate_url", nowhere):
        await _entry(hass, internal_url=None)
