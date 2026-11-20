"""Grad no! – Abfahrten von ÖBB und Wiener Linien für Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import GradNoClient, GradNoError
from .const import (
    ATTR_DATE,
    ATTR_EVA,
    ATTR_STATION,
    ATTR_TRAIN_NUMBER,
    CARD_URL,
    DOMAIN,
    SERVICE_GET_FORMATION,
    VERSION,
)
from .coordinator import GradNoConfigEntry, GradNoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

FORMATION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TRAIN_NUMBER): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_STATION, default=""): cv.string,
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional(ATTR_EVA, default=""): cv.string,
    }
)


def get_client(hass: HomeAssistant) -> GradNoClient:
    """Ein gemeinsamer Client für alle Haltestellen (teilt die Caches für Suche und Wagenreihung)."""
    data = hass.data.setdefault(DOMAIN, {})
    if "client" not in data:
        data["client"] = GradNoClient(async_get_clientsession(hass))
    return data["client"]


async def _async_register_card(hass: HomeAssistant) -> None:
    """Die Lovelace-Karte mitliefern: Datei bereitstellen und im Frontend laden."""
    if hass.data.setdefault(DOMAIN, {}).get("card"):
        return
    if hass.http is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    path = Path(__file__).parent / "frontend" / "grad-no-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), True)])
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    hass.data[DOMAIN]["card"] = True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Dienst und Karte einmalig registrieren."""

    async def get_formation(call: ServiceCall) -> ServiceResponse:
        day = call.data.get(ATTR_DATE)
        try:
            formation = await get_client(hass).fetch_formation(
                call.data[ATTR_TRAIN_NUMBER],
                station_name=call.data[ATTR_STATION],
                day=day.isoformat() if day else None,
                eva=call.data[ATTR_EVA],
            )
        except GradNoError as err:
            raise HomeAssistantError(str(err)) from err
        return formation.as_dict()

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_FORMATION,
        get_formation,
        schema=FORMATION_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    await _async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GradNoConfigEntry) -> bool:
    coordinator = GradNoCoordinator(hass, entry, get_client(hass))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GradNoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
