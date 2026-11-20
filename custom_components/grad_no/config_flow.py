"""Einrichten einer Haltestelle: Anbieter wählen, suchen, Haltestelle und Filter festlegen."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import FILTER_ALL, FILTER_BUS, FILTER_TRAIN, PROVIDER_SCOTTY, PROVIDER_WL, GradNoError
from .const import (
    CONF_AHEAD_MIN,
    CONF_DIRECTION,
    CONF_LINES,
    CONF_PRODUCTS,
    CONF_PROVIDER,
    CONF_QUERY,
    CONF_SCAN_INTERVAL,
    CONF_STOP,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_WALK_MIN,
    DEFAULT_AHEAD_MIN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_AHEAD_MIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PROVIDER_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[PROVIDER_SCOTTY, PROVIDER_WL],
        translation_key=CONF_PROVIDER,
        mode=SelectSelectorMode.LIST,
    )
)
PRODUCTS_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[FILTER_ALL, FILTER_TRAIN, FILTER_BUS],
        translation_key=CONF_PRODUCTS,
        mode=SelectSelectorMode.DROPDOWN,
    )
)


def _minutes(lo: int, hi: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=lo, max=hi, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min")
    )


def _filter_fields(with_interval: bool) -> dict[Any, Any]:
    fields: dict[Any, Any] = {
        vol.Optional(CONF_PRODUCTS, default=FILTER_ALL): PRODUCTS_SELECTOR,
        vol.Optional(CONF_LINES, default=""): TextSelector(),
        vol.Optional(CONF_DIRECTION, default=""): TextSelector(),
        vol.Optional(CONF_WALK_MIN, default=0): _minutes(0, 120),
        vol.Optional(CONF_AHEAD_MIN, default=DEFAULT_AHEAD_MIN): _minutes(10, MAX_AHEAD_MIN),
    }
    if with_interval:
        fields[vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL)] = NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL, max=3600, step=10, mode=NumberSelectorMode.BOX, unit_of_measurement="s"
            )
        )
    return fields


def _clean_options(data: dict[str, Any]) -> dict[str, Any]:
    out = {
        CONF_PRODUCTS: data.get(CONF_PRODUCTS, FILTER_ALL),
        CONF_LINES: (data.get(CONF_LINES) or "").strip(),
        CONF_DIRECTION: (data.get(CONF_DIRECTION) or "").strip(),
        CONF_WALK_MIN: int(data.get(CONF_WALK_MIN) or 0),
        CONF_AHEAD_MIN: int(data.get(CONF_AHEAD_MIN) or DEFAULT_AHEAD_MIN),
    }
    if CONF_SCAN_INTERVAL in data:
        out[CONF_SCAN_INTERVAL] = max(MIN_SCAN_INTERVAL, int(data[CONF_SCAN_INTERVAL]))
    return out


class GradNoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Haltestelle hinzufügen."""

    VERSION = 1

    def __init__(self) -> None:
        self._provider = PROVIDER_SCOTTY
        self._results: list[dict[str, str]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            from . import get_client

            self._provider = user_input[CONF_PROVIDER]
            try:
                self._results = await get_client(self.hass).search(self._provider, user_input[CONF_QUERY])
            except GradNoError as err:
                _LOGGER.warning("Haltestellensuche fehlgeschlagen: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if self._results:
                    return await self.async_step_stop()
                errors[CONF_QUERY] = "no_results"

        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER, default=self._provider): PROVIDER_SELECTOR,
                vol.Required(CONF_QUERY): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_stop(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None and CONF_STOP in user_input:
            stop_id = user_input[CONF_STOP]
            stop_name = next((r["name"] for r in self._results if r["id"] == stop_id), stop_id)
            title = (user_input.get(CONF_NAME) or "").strip() or stop_name
            return self.async_create_entry(
                title=title,
                data={CONF_PROVIDER: self._provider, CONF_STOP_ID: stop_id, CONF_STOP_NAME: stop_name},
                options=_clean_options(user_input),
            )

        options = [SelectOptionDict(value=r["id"], label=r["name"]) for r in self._results]
        schema = vol.Schema(
            {
                vol.Required(CONF_STOP, default=self._results[0]["id"]): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_NAME): TextSelector(),
                **_filter_fields(with_interval=False),
            }
        )
        return self.async_show_form(step_id="stop", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        return GradNoOptionsFlow()


class GradNoOptionsFlow(OptionsFlow):
    """Filter und Abfrageintervall ändern."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_clean_options(user_input))
        schema = vol.Schema(_filter_fields(with_interval=True))
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, dict(self.config_entry.options)),
        )
