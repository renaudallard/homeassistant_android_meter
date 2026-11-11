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

import asyncio
import random
import threading
from datetime import timedelta
from decimal import Decimal
from http import HTTPStatus
from typing import Any
from unittest.mock import patch
from urllib.parse import quote

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import async_fire_time_changed
from pytest_homeassistant.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant.components.repairs import (
    process_repair_fix_flow,
    start_repair_fix_flow,
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


async def _open(hass: HomeAssistant, entry: Any, step: str) -> dict[str, Any]:
    """The options menu, then one of its forms."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["teach", "phone"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step
    return dict(result)


async def _step(
    hass: HomeAssistant, entry: Any, step: str, fields: dict[str, Any]
) -> dict[str, Any]:
    result = await _open(hass, entry, step)
    result = await hass.config_entries.options.async_configure(result["flow_id"], fields)
    await hass.async_block_till_done()
    return dict(result)


CONTROLS = {
    "digits": "number.gas_meter_drums",
    "decimals": "number.gas_meter_decimal_drums",
    "x": "number.gas_meter_crop_left",
    "y": "number.gas_meter_crop_top",
    "width": "number.gas_meter_crop_width",
    "height": "number.gas_meter_crop_height",
    "gap": "number.gas_meter_gap_between_windows",
}


async def _crop(hass: HomeAssistant, **fields: Any) -> None:
    """Set crop and counter options with the controls of the device page."""
    if "rotation" in fields:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.gas_meter_rotation", "option": str(fields["rotation"])},
            blocking=True,
        )
    for key, entity_id in CONTROLS.items():
        if key in fields:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": fields[key]},
                blocking=True,
            )
    await hass.async_block_till_done()


async def _options(hass: HomeAssistant, entry: Any, **fields: Any) -> dict[str, Any]:
    """Crop fields through the device controls, then the reading through
    Teach. An error comes back the way a form shows one."""
    crop = {key: value for key, value in fields.items() if key not in ("reading", "forget")}
    teach = {key: value for key, value in fields.items() if key in ("reading", "forget")}
    result: dict[str, Any] = {}
    if crop:
        try:
            await _crop(hass, **crop)
        except ServiceValidationError as err:
            return {"type": "error", "errors": {"base": err.translation_key}}
    if teach:
        result = await _step(hass, entry, "teach", teach)
    return result


async def test_a_frame_is_read_once_taught(
    hass: HomeAssistant, hass_client_no_auth: Any, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    rng = random.Random(1)

    response = await _post(client, entry, synth.frame(89012345, LAYOUT, rng, light=0.9, shade=0))
    assert response.status == HTTPStatus.OK
    reply = await response.json()
    assert reply["interval"] == 60
    assert reply["torch"] is True
    assert reply["status"] == "no_crop"
    assert reply["crop"]["width"] == 0
    assert reply["crop"]["locked"] is False
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
    assert hass.states.get("sensor.gas_meter_digits_to_learn").state == "none"
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


async def test_a_crop_of_the_last_drums(
    hass: HomeAssistant, hass_client_no_auth: Any, freezer: FrozenDateTimeFactory
) -> None:
    """The phone sees only the last six drums: the reading taught names
    them all, and the two left of the crop follow it."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    rng = random.Random(9)
    tail = {
        **CROP,
        "x": LAYOUT.x + 2 * (synth.WINDOW[0] + synth.GAP),
        "width": 6 * synth.WINDOW[0] + 5 * synth.GAP,
        "digits": 6,
    }
    await _post(client, entry, synth.frame(61890123, LAYOUT, rng, light=0.9, shade=0))
    result = await _options(hass, entry, **tail, reading="61890.123")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await _post(client, entry, synth.frame(61234567, LAYOUT, rng, light=0.9, shade=0))
    result = await _options(hass, entry, reading="61234.567")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.states.get("sensor.gas_meter_reading").state == "61234.567"

    freezer.tick(timedelta(seconds=60))
    response = await _post(client, entry, synth.frame(61234590, LAYOUT, rng, light=0.9, shade=0))
    assert (await response.json())["status"] == "ok"
    assert hass.states.get("sensor.gas_meter_reading").state == "61234.590"


async def test_new_decimal_drums_ask_for_the_reading(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The same drums read with one decimal less are ten times the volume:
    the reading is dropped and asked again, the digits are kept. The
    reading is shown with the new decimals. The reading dropped is stored
    at once, as the new decimals are."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    rng = random.Random(13)
    await _post(client, entry, synth.frame(89012345, LAYOUT, rng, light=0.9, shade=0))
    await _options(hass, entry, **CROP, reading="89012.345")
    await _post(client, entry, synth.frame(1234567, LAYOUT, rng, light=0.9, shade=0))
    await _options(hass, entry, reading="01234.567")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.gas_meter_lock_the_crop"}, blocking=True
    )
    precision = er.async_get(hass).async_get("sensor.gas_meter_reading").options["sensor"]
    assert precision["suggested_display_precision"] == 3
    await _crop(hass, decimals=2)
    await hass.async_block_till_done()
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["reading"] is None
    precision = er.async_get(hass).async_get("sensor.gas_meter_reading").options["sensor"]
    assert precision["suggested_display_precision"] == 2
    assert hass.states.get("sensor.gas_meter_reading").state == "unknown"
    assert hass.states.get("sensor.gas_meter_status").state == "untaught"
    assert hass.states.get("sensor.gas_meter_learnt_digits").state == "10"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"teach_{entry.entry_id}") is not None

    await _options(hass, entry, reading="012345.67")
    freezer.tick(timedelta(seconds=60))
    response = await _post(client, entry, synth.frame(1234575, LAYOUT, rng, light=0.9, shade=0))
    assert (await response.json())["status"] == "ok"
    assert hass.states.get("sensor.gas_meter_reading").state == "12345.75"


async def test_decimals_right_of_the_crop_are_dropped(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """The crop leaves the last red drum out: the whole reading of the
    meter, typed as it shows, is taught without it."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    head = {**CROP, "width": 7 * synth.WINDOW[0] + 6 * synth.GAP, "digits": 7, "decimals": 2}
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(14), light=0.9, shade=0))
    result = await _options(hass, entry, **head, reading="01234.567")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.states.get("sensor.gas_meter_reading").state == "1234.56"
    # Typed while the decimals were others, as one waiting for the lock is.
    meter = entry.runtime_data
    await meter.async_configure(reading=Decimal("1234.567"), picture=meter.picture())
    assert meter.reading == Decimal("1234.56")


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

    result = await _options(hass, entry, reading="1234.567")
    assert result["errors"] == {"base": "no_crop"}
    # No crop yet, and still no more decimal drums than drums.
    result = await _options(hass, entry, digits=2)
    assert result["errors"] == {"base": "too_many_decimals"}
    result = await _options(hass, entry, **CROP, reading="1234.567")
    assert result["errors"] == {"base": "no_frame"}
    result = await _options(hass, entry, reading="")
    assert result["errors"] == {"reading": "no_reading"}

    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(2)))
    result = await _options(hass, entry, **{**CROP, "x": 600})
    assert result["errors"] == {"base": "crop_outside"}
    result = await _options(hass, entry, **{**CROP, "digits": 4, "decimals": 5})
    assert result["errors"] == {"base": "too_many_decimals"}
    assert entry.options["x"] == CROP["x"]
    result = await _options(hass, entry, reading="123.4a")
    assert result["errors"] == {"reading": "bad_reading"}
    # The drums were set to 4 above: the error says what this counter takes.
    assert result["description_placeholders"]["digits"] == "4"
    assert result["description_placeholders"]["example"] == "0.123"
    result = await _options(hass, entry, reading="1234.5678901")
    assert result["errors"] == {"reading": "bad_reading"}
    # Every drum a decimal: the example is still a reading the form takes.
    await _crop(hass, decimals=4)
    result = await _options(hass, entry, reading="x")
    assert result["description_placeholders"]["example"] == "0.0123"


async def test_the_phone_goes_offline(
    hass: HomeAssistant, hass_client_no_auth: Any, freezer: FrozenDateTimeFactory
) -> None:
    """Five minutes after the last frame, a change of crop in between
    neither putting it off nor hiding it."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(3)))
    freezer.tick(timedelta(seconds=200))
    await _crop(hass, **CROP)
    assert hass.states.get("sensor.gas_meter_status").state == "untaught"
    freezer.tick(timedelta(seconds=101))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.gas_meter_status").state == "offline"
    await _crop(hass, x=CROP["x"] + 1)
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

    assert hass.states.get("switch.gas_meter_lock_the_crop").state == "on"
    result = await _options(hass, entry, **{**CROP, "x": CROP["x"] + 1})
    assert result["errors"] == {"base": "crop_locked"}
    assert hass.states.get("sensor.gas_meter_learnt_digits").state != "0"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.gas_meter_lock_the_crop"}, blocking=True
    )
    await _options(hass, entry, **{**CROP, "x": CROP["x"] + 1})
    assert hass.states.get("sensor.gas_meter_learnt_digits").state == "0"
    # No frame since the restart: the phone is still waited for.
    assert hass.states.get("sensor.gas_meter_status").state == "waiting"

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_pairing_code(hass: HomeAssistant) -> None:
    entry = await _entry(hass)
    result = await _open(hass, entry, "phone")
    qr = _qr(result)
    assert qr is not None
    assert qr.startswith(
        "androidmeter://setup?url=http%3A%2F%2F192.168.1.10%3A8123%2Fapi%2Fwebhook%2F"
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"interval": 30, "torch": False, "max_flow": 10}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "qr_code" not in entry.options
    assert entry.options["interval"] == 30
    assert entry.options["torch"] is False
    assert entry.options["max_flow"] == 10.0


async def test_no_pairing_code_without_an_address(hass: HomeAssistant) -> None:
    """With no address for Home Assistant at all, only the path is shown."""

    def nowhere(*_args: object, **_kwargs: object) -> str:
        raise NoURLAvailableError

    with patch("custom_components.android_meter.config_flow.webhook.async_generate_url", nowhere):
        await _entry(hass, internal_url=None)


async def test_the_teaching_screen_shows_the_picture(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    entry = await _entry(hass)
    await _post(await hass_client_no_auth(), entry, synth.frame(1234567, LAYOUT, random.Random(6)))
    await _options(hass, entry, **CROP)
    result = await _open(hass, entry, "teach")
    placeholders = result["description_placeholders"]
    assert placeholders["frame_image"].startswith(
        "![](/api/image_proxy/image.gas_meter_frame?token="
    )
    assert placeholders["example"] == "01234.567"
    assert placeholders["known"] == "-"
    assert placeholders["unknown"] == "0 1 2 3 4 5 6 7 8 9"


async def test_no_picture_shown_none_taught(hass: HomeAssistant, hass_client_no_auth: Any) -> None:
    """With the Frame image disabled the form shows no picture, so it has
    none to teach."""
    entry = await _entry(hass)
    await _post(await hass_client_no_auth(), entry, synth.frame(1234567, LAYOUT, random.Random(6)))
    await _options(hass, entry, **CROP)
    er.async_get(hass).async_update_entity(
        "image.gas_meter_frame", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    result = await _options(hass, entry, reading="01234.567")
    assert result["errors"] == {"base": "no_frame"}
    assert result["description_placeholders"]["frame_image"] == ""
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": "text.gas_meter_teach_the_reading", "value": "01234.567"},
            blocking=True,
        )
    assert err.value.translation_key == "frame_hidden"


async def test_the_picture_shown_is_taught(
    hass: HomeAssistant, hass_client_no_auth: Any, freezer: FrozenDateTimeFactory
) -> None:
    """A frame that comes in while the reading is typed is not the one
    taught: the drums it shows have moved on."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    rng = random.Random(11)
    await _post(client, entry, synth.frame(1234567, LAYOUT, rng, light=0.9, shade=0))
    shown_at = entry.runtime_data.frame_at
    await _options(hass, entry, **CROP)
    result = await _open(hass, entry, "teach")

    freezer.tick(timedelta(seconds=60))
    await _post(client, entry, synth.frame(1234571, LAYOUT, rng, light=0.9, shade=0))
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"reading": "01234.567"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.read_at == shown_at

    freezer.tick(timedelta(seconds=60))
    response = await _post(client, entry, synth.frame(1234571, LAYOUT, rng, light=0.9, shade=0))
    assert (await response.json())["status"] == "ok"
    assert hass.states.get("sensor.gas_meter_reading").state == "1234.571"


async def test_a_crop_change_waits_for_a_reading_being_taught(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """A crop sent while a reading is taught is refused by the lock that
    reading sets, rather than forgetting the digits just learnt."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(12), light=0.9, shade=0))
    await _options(hass, entry, **CROP)
    meter = entry.runtime_data
    release = threading.Event()
    teach = meter._teach

    def slow(*args: Any) -> int:
        release.wait(10)
        return teach(*args)

    with patch.object(meter, "_teach", slow):
        taught = hass.async_create_task(
            meter.async_configure(reading=Decimal("1234.567"), picture=meter.picture())
        )
        while not meter._lock.locked():
            await asyncio.sleep(0)
        change = hass.async_create_task(meter.async_change({"x": CROP["x"] + 1}))
        for _ in range(5):
            await asyncio.sleep(0)
        release.set()
        await taught
        assert await change == "crop_locked"
    assert entry.options["x"] == CROP["x"]
    assert any(meter.patches.counts())


async def test_a_reading_typed_during_a_crop_change_is_learnt_on_the_new_crop(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """A reading typed while a crop change waits for a frame being read
    is taught on the crop in force when its turn comes, not on the one
    there was when it was typed, which would be dropped with what it
    learnt."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(12), light=0.9, shade=0))
    await _options(hass, entry, **CROP)
    meter = entry.runtime_data
    release = threading.Event()
    read = meter._read

    def slow(*args: Any) -> Any:
        release.wait(10)
        return read(*args)

    with patch.object(meter, "_read", slow):
        frame = hass.async_create_task(meter.async_frame(meter.frame))
        while not meter._lock.locked():
            await asyncio.sleep(0)
        change = hass.async_create_task(meter.async_change({"x": CROP["x"] + 1}))
        taught = hass.async_create_task(
            hass.services.async_call(
                "text",
                "set_value",
                {"entity_id": "text.gas_meter_teach_the_reading", "value": "1234.567"},
                blocking=True,
            )
        )
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()
        await frame
        assert await change is None
        await taught
    await hass.async_block_till_done()
    assert entry.options["x"] == meter.options["x"] == CROP["x"] + 1
    assert meter.locked
    assert any(meter.patches.counts())


async def test_a_frame_read_during_an_unload_leaves_no_timer(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """The unload waits for a frame being read, rather than letting it
    arm the offline timer of a meter that is gone."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(12), light=0.9, shade=0))
    meter = entry.runtime_data
    release = threading.Event()
    read = meter._read

    def slow(*args: Any) -> Any:
        release.wait(10)
        return read(*args)

    with patch.object(meter, "_read", slow):
        frame = hass.async_create_task(meter.async_frame(meter.frame))
        while not meter._lock.locked():
            await asyncio.sleep(0)
        unload = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()
        await frame
        assert await unload
    assert meter._silence is None


async def test_an_unloaded_meter_takes_no_change(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """A crop or a Find still on its way when the meter is unloaded says
    so, rather than that it was applied."""
    entry = await _entry(hass)
    await _post(await hass_client_no_auth(), entry, synth.frame(1234567, LAYOUT, random.Random(10)))
    meter = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await meter.async_change({"x": CROP["x"]}) == "not_loaded"
    assert await meter.async_find_crop() == "not_loaded"
    assert entry.options["x"] == entry.options["width"] == 0


async def test_the_repairs_notice_outlives_a_reload(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """A notice ignored stays ignored when the meter is reloaded. Home
    Assistant hides it while the meter is disabled, and removes it with
    the meter."""
    entry = await _entry(hass)
    await _post(await hass_client_no_auth(), entry, synth.frame(1234567, LAYOUT, random.Random(7)))
    await _options(hass, entry, **CROP)
    issues = ir.async_get(hass)
    issue_id = f"teach_{entry.entry_id}"
    assert issues.async_get_issue(DOMAIN, issue_id) is not None
    issues.async_ignore(DOMAIN, issue_id, True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None

    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issues.async_is_hidden(issue)
    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert not issues.async_is_hidden(issue)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_a_reading_is_asked_in_repairs(
    hass: HomeAssistant, hass_client: Any, hass_client_no_auth: Any
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = await _entry(hass)
    issues = ir.async_get(hass)
    issue_id = f"teach_{entry.entry_id}"
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    await _post(await hass_client_no_auth(), entry, synth.frame(1234567, LAYOUT, random.Random(7)))
    await _options(hass, entry, **CROP)
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {"name": entry.title}
    hass.config_entries.async_update_entry(entry, title="Water meter")
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"name": "Water meter"}

    client = await hass_client()
    flow = await start_repair_fix_flow(client, DOMAIN, issue_id)
    assert flow["step_id"] == "teach"
    assert not flow["errors"]
    assert flow["description_placeholders"]["frame_image"].startswith("![](/api/image_proxy/")
    flow = await process_repair_fix_flow(client, flow["flow_id"], {"reading": "nonsense"})
    assert flow["errors"] == {"reading": "bad_reading"}
    # Forgetting alone leaves the reading to type.
    flow = await process_repair_fix_flow(client, flow["flow_id"], {"forget": True})
    assert flow["type"] == "form"
    assert issues.async_get_issue(DOMAIN, issue_id) is not None
    flow = await process_repair_fix_flow(client, flow["flow_id"], {"reading": "01234.567"})
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get("sensor.gas_meter_reading").state == "1234.567"


async def test_the_device_controls(hass: HomeAssistant, hass_client_no_auth: Any) -> None:
    entry = await _entry(hass)
    await _post(await hass_client_no_auth(), entry, synth.frame(1234567, LAYOUT, random.Random(8)))
    width = hass.states.get("number.gas_meter_crop_width")
    assert width.attributes["max"] == 640
    assert width.attributes["mode"] == "slider"
    assert hass.states.get("number.gas_meter_crop_height").attributes["max"] == 480
    assert hass.states.get("number.gas_meter_drums").attributes["mode"] == "box"

    shown = hass.states.get("image.gas_meter_frame").state
    await _crop(hass, **CROP)
    assert entry.options["width"] == CROP["width"]
    assert hass.states.get("number.gas_meter_crop_left").state == str(float(CROP["x"]))
    assert hass.states.get("image.gas_meter_frame").state != shown
    assert hass.states.get("number.gas_meter_gap_between_windows").attributes["max"] == 640 // 8

    await _crop(hass, rotation=90)
    assert hass.states.get("number.gas_meter_crop_width").attributes["max"] == 480
    assert entry.options["width"] == 0
    await _crop(hass, rotation=0)
    await _crop(hass, **CROP)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": "text.gas_meter_teach_the_reading", "value": ""},
            blocking=True,
        )
    assert err.value.translation_key == "bad_reading"
    assert err.value.translation_placeholders == {
        "digits": "8",
        "decimals": "3",
        "example": "01234.567",
    }
    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": "text.gas_meter_teach_the_reading", "value": "01234.567"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.gas_meter_reading").state == "1234.567"
    assert hass.states.get("switch.gas_meter_lock_the_crop").state == "on"
    assert hass.states.get("text.gas_meter_teach_the_reading").state == ""
    with pytest.raises(ServiceValidationError) as err:
        await _crop(hass, x=CROP["x"] + 5)
    assert err.value.translation_key == "crop_locked"

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.gas_meter_forget_the_digits_learnt"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.gas_meter_learnt_digits").state == "0"
    assert hass.states.get("sensor.gas_meter_digits_to_learn").state == "0 1 2 3 4 5 6 7 8 9"
    assert hass.states.get("sensor.gas_meter_reading").state == "1234.567"


async def test_a_crop_drawn_on_the_phone(hass: HomeAssistant, hass_client_no_auth: Any) -> None:
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(9), light=0.9, shade=0))
    url = f"/api/webhook/{entry.data['webhook_id']}"

    response = await client.post(url, json={"crop": {**CROP, "rotation": 0}})
    assert response.status == HTTPStatus.OK
    reply = await response.json()
    assert reply["error"] is None
    assert reply["crop"]["width"] == CROP["width"]
    assert entry.options["x"] == CROP["x"]
    assert hass.states.get("number.gas_meter_crop_left").state == str(float(CROP["x"]))

    response = await client.post(url, json={"crop": {**CROP, "x": 600}})
    assert (await response.json())["error"] == "crop_outside"
    assert entry.options["x"] == CROP["x"]

    for bad in (
        {"crop": {"x": "1"}},
        {"crop": {"x": -1}},
        {"crop": {"rotation": 45}},
        {"crop": {"colour": 1}},
        {"crop": {}},
        [],
        {"x": 1},
    ):
        response = await client.post(url, json=bad)
        assert response.status == HTTPStatus.BAD_REQUEST
    response = await client.post(url, data=b"{", headers={"Content-Type": "application/json"})
    assert response.status == HTTPStatus.BAD_REQUEST

    await _options(hass, entry, reading="01234.567")
    response = await client.post(url, json={"crop": {**CROP, "x": CROP["x"] + 1}})
    reply = await response.json()
    assert reply["error"] == "crop_locked"
    assert reply["crop"]["locked"] is True
    assert entry.options["x"] == CROP["x"]


async def test_finding_the_counter(hass: HomeAssistant, hass_client_no_auth: Any) -> None:
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    url = f"/api/webhook/{entry.data['webhook_id']}"

    response = await client.post(url, json={"find": True})
    assert (await response.json())["error"] == "no_frame"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.gas_meter_find_the_counter"}, blocking=True
        )
    assert err.value.translation_key == "no_frame"

    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(10), light=0.9, shade=0))
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.gas_meter_find_the_counter"}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options["digits"] == LAYOUT.digits
    assert entry.options["decimals"] == LAYOUT.decimals
    assert entry.options["width"] > 0

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.gas_meter_crop_left", "value": 10},
        blocking=True,
    )
    response = await client.post(url, json={"find": True})
    reply = await response.json()
    assert reply["error"] is None
    assert reply["crop"]["x"] == entry.options["x"] != 10

    await _options(hass, entry, reading="01234.567")
    before = dict(entry.options)
    response = await client.post(url, json={"find": True})
    reply = await response.json()
    assert reply["error"] in (None, "crop_locked")
    assert reply["crop"]["locked"] is True
    assert dict(entry.options) == before


async def test_a_row_longer_than_a_counter_is_not_one(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """The finder counts as many drums as it sees, more than the options
    allow."""
    entry = await _entry(hass)
    client = await hass_client_no_auth()
    await _post(client, entry, synth.frame(1234567, LAYOUT, random.Random(10), light=0.9, shade=0))
    found = {**CROP, "digits": 14, "decimals": 8}
    url = f"/api/webhook/{entry.data['webhook_id']}"
    with patch("custom_components.android_meter.meter.autocrop.find", return_value=found):
        response = await client.post(url, json={"find": True})
    assert (await response.json())["error"] == "counter_not_found"
    assert entry.options["digits"] != 14
