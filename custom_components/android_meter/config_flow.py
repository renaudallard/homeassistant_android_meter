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

"""Config and options flows.

Setting up only names the meter and hands out the webhook address the app
needs, as a QR code the phone's camera reads and shares to the app, and
as text. Everything else lives in the options, a menu of two screens: the
reading to teach, and the phone. The crop and the counter are set on the
phone, or with controls on the device page next to the Frame image.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import probatio
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    QrCodeSelector,
    QrCodeSelectorConfig,
    QrErrorCorrectionLevel,
)

from . import MeterConfigEntry
from .const import (
    CONF_INTERVAL,
    CONF_MAX_FLOW,
    CONF_QR_CODE,
    CONF_TORCH,
    CONF_WEBHOOK_ID,
    DEFAULT_NAME,
    DEFAULTS,
    DOMAIN,
    MAX_INTERVAL,
    MIN_INTERVAL,
)
from .teach import async_teach, teach_placeholders, teach_schema


def _webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """The address the phone posts to, on the local network."""
    try:
        return webhook.async_generate_url(
            hass, webhook_id, allow_external=False, prefer_external=False
        )
    except NoURLAvailableError:
        return webhook.async_generate_path(webhook_id)


def _pairing(url: str) -> dict[Any, Any]:
    """A QR code of the link the app opens to take url, when url is whole.

    Without an address for Home Assistant on the network the url is only
    a path, which the phone cannot use, and no code is shown.
    """
    if not url.startswith(("http://", "https://")):
        return {}
    link = f"androidmeter://setup?url={quote(url, safe='')}"
    return {
        probatio.Optional(CONF_QR_CODE): QrCodeSelector(
            QrCodeSelectorConfig(
                data=link, scale=6, error_correction_level=QrErrorCorrectionLevel.MEDIUM
            )
        )
    }


def _number(low: float, high: float, step: float = 1, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=low, max=high, step=step, mode=NumberSelectorMode.BOX)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


class AndroidMeterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Name a meter and give it a webhook."""

    VERSION = 1

    def __init__(self) -> None:
        self._name = DEFAULT_NAME
        self._webhook_id = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=probatio.Schema({probatio.Required("name", default=DEFAULT_NAME): str}),
            )
        self._name = user_input["name"]
        self._webhook_id = webhook.async_generate_id()
        return await self.async_step_pair()

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the phone where to post, as a QR code and as text."""
        if user_input is None:
            url = _webhook_url(self.hass, self._webhook_id)
            return self.async_show_form(
                step_id="pair",
                data_schema=probatio.Schema(_pairing(url)),
                description_placeholders={"webhook_url": url},
            )
        return self.async_create_entry(
            title=self._name,
            data={CONF_WEBHOOK_ID: self._webhook_id},
            options=dict(DEFAULTS),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: MeterConfigEntry) -> OptionsFlow:
        return AndroidMeterOptionsFlow()


class AndroidMeterOptionsFlow(OptionsFlow):
    """A menu: teach the reading, or the phone and pairing. The crop and
    the counter are set on the phone or on the device page."""

    def __init__(self) -> None:
        self._picture: tuple[bytes, datetime] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["teach", "phone"])

    def _current(self) -> dict[str, Any]:
        return {**DEFAULTS, **self.config_entry.options}

    async def async_step_teach(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The last picture, and the reading it shows."""
        entry: MeterConfigEntry = self.config_entry
        meter = entry.runtime_data
        errors: dict[str, str] = {}
        if user_input is not None:
            options = self._current()
            errors = await async_teach(meter, options, user_input, self._picture)
            if not errors:
                return self.async_create_entry(data=options)
        self._picture = meter.picture()
        return self.async_show_form(
            step_id="teach",
            data_schema=teach_schema(),
            errors=errors,
            description_placeholders=teach_placeholders(self.hass, entry.entry_id, meter),
        )

    async def async_step_phone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """How often the phone takes a picture, the torch, and pairing."""
        entry: MeterConfigEntry = self.config_entry
        current = self._current()
        if user_input is not None:
            options = {
                **current,
                CONF_INTERVAL: int(user_input[CONF_INTERVAL]),
                CONF_TORCH: bool(user_input[CONF_TORCH]),
                CONF_MAX_FLOW: float(user_input[CONF_MAX_FLOW]),
            }
            await entry.runtime_data.async_configure(options)
            return self.async_create_entry(data=options)
        url = _webhook_url(self.hass, entry.data[CONF_WEBHOOK_ID])
        schema = probatio.Schema(
            {
                **_pairing(url),
                probatio.Required(CONF_INTERVAL, default=current[CONF_INTERVAL]): _number(
                    MIN_INTERVAL, MAX_INTERVAL, unit="s"
                ),
                probatio.Required(CONF_TORCH, default=current[CONF_TORCH]): BooleanSelector(),
                probatio.Required(CONF_MAX_FLOW, default=current[CONF_MAX_FLOW]): _number(
                    0.1, 100, 0.1, "m³/h"
                ),
            }
        )
        return self.async_show_form(
            step_id="phone",
            data_schema=schema,
            description_placeholders={"webhook_url": url},
        )
