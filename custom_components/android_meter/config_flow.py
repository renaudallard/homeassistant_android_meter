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
as text. Everything else lives in the options, set once a frame has come in
and can be looked at: the crop, the counter, and the reading to learn
from.
"""

from __future__ import annotations

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
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from . import MeterConfigEntry
from .const import (
    CONF_DECIMALS,
    CONF_DIGITS,
    CONF_FORGET,
    CONF_GAP,
    CONF_HEIGHT,
    CONF_INTERVAL,
    CONF_MAX_FLOW,
    CONF_QR_CODE,
    CONF_READING,
    CONF_ROTATION,
    CONF_TORCH,
    CONF_WEBHOOK_ID,
    CONF_WIDTH,
    CONF_X,
    CONF_Y,
    DEFAULT_NAME,
    DEFAULTS,
    DOMAIN,
    MAX_DECIMALS,
    MAX_DIGITS,
    MAX_INTERVAL,
    MIN_INTERVAL,
    ROTATIONS,
)
from .meter import Settings, TeachError, frame_error, parse_reading

_INTEGERS = (
    CONF_INTERVAL,
    CONF_ROTATION,
    CONF_X,
    CONF_Y,
    CONF_WIDTH,
    CONF_HEIGHT,
    CONF_GAP,
    CONF_DIGITS,
    CONF_DECIMALS,
)


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
    """Crop, counter, and the reading to learn from."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry: MeterConfigEntry = self.config_entry
        meter = entry.runtime_data
        errors: dict[str, str] = {}
        current: dict[str, Any] = {**DEFAULTS, **entry.options}
        if user_input is not None:
            options = {key: user_input[key] for key in DEFAULTS}
            for key in _INTEGERS:
                options[key] = int(options[key])
            options[CONF_MAX_FLOW] = float(options[CONF_MAX_FLOW])
            options[CONF_TORCH] = bool(options[CONF_TORCH])
            settings = Settings.from_options(options)
            error = frame_error(settings, meter.frame_size)
            reading = None
            text = str(user_input.get(CONF_READING) or "").strip()
            if error is not None:
                errors["base"] = error
            elif text:
                reading = parse_reading(text, settings)
                if reading is None:
                    errors[CONF_READING] = "bad_reading"
            if not errors:
                try:
                    await meter.async_configure(
                        options, reading, bool(user_input.get(CONF_FORGET, False))
                    )
                except TeachError as err:
                    errors["base"] = str(err)
                else:
                    return self.async_create_entry(data=options)
            current = {**current, **options}

        size = meter.frame_size
        known = meter.known()
        url = _webhook_url(self.hass, entry.data[CONF_WEBHOOK_ID])
        schema = probatio.Schema(
            {
                **_pairing(url),
                probatio.Required(CONF_INTERVAL, default=current[CONF_INTERVAL]): _number(
                    MIN_INTERVAL, MAX_INTERVAL, unit="s"
                ),
                probatio.Required(CONF_TORCH, default=current[CONF_TORCH]): BooleanSelector(),
                probatio.Required(
                    CONF_ROTATION, default=str(current[CONF_ROTATION])
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[str(r) for r in ROTATIONS],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_ROTATION,
                    )
                ),
                probatio.Required(CONF_X, default=current[CONF_X]): _number(0, 20000, unit="px"),
                probatio.Required(CONF_Y, default=current[CONF_Y]): _number(0, 20000, unit="px"),
                probatio.Required(CONF_WIDTH, default=current[CONF_WIDTH]): _number(
                    0, 20000, unit="px"
                ),
                probatio.Required(CONF_HEIGHT, default=current[CONF_HEIGHT]): _number(
                    0, 20000, unit="px"
                ),
                probatio.Required(CONF_GAP, default=current[CONF_GAP]): _number(0, 2000, unit="px"),
                probatio.Required(CONF_DIGITS, default=current[CONF_DIGITS]): _number(
                    1, MAX_DIGITS
                ),
                probatio.Required(CONF_DECIMALS, default=current[CONF_DECIMALS]): _number(
                    0, MAX_DECIMALS
                ),
                probatio.Required(CONF_MAX_FLOW, default=current[CONF_MAX_FLOW]): _number(
                    0.1, 100, 0.1, "m³/h"
                ),
                probatio.Optional(CONF_READING): TextSelector(),
                probatio.Optional(CONF_FORGET, default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "webhook_url": url,
                "frame": "-" if size is None else f"{size[0]} x {size[1]}",
                "known": " ".join(str(d) for d in known) or "-",
                "unknown": " ".join(str(d) for d in range(10) if d not in known) or "-",
            },
        )
