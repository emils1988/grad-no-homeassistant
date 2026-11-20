"""Regelmäßige Abfrage einer Haltestelle."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Board, GradNoClient, GradNoError, StopConfig
from .const import (
    CONF_AHEAD_MIN,
    CONF_DIRECTION,
    CONF_LINES,
    CONF_PRODUCTS,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_WALK_MIN,
    DEFAULT_AHEAD_MIN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type GradNoConfigEntry = ConfigEntry[GradNoCoordinator]


def stop_from_entry(entry: ConfigEntry) -> StopConfig:
    o = entry.options
    return StopConfig(
        provider=entry.data[CONF_PROVIDER],
        stop_id=entry.data[CONF_STOP_ID],
        name=entry.data.get(CONF_STOP_NAME, ""),
        products=o.get(CONF_PRODUCTS, "all"),
        direction=o.get(CONF_DIRECTION, ""),
        lines=o.get(CONF_LINES, ""),
        walk_min=int(o.get(CONF_WALK_MIN, 0)),
        ahead_min=int(o.get(CONF_AHEAD_MIN, DEFAULT_AHEAD_MIN)),
    )


class GradNoCoordinator(DataUpdateCoordinator[Board]):
    """Holt die Abfahrtstafel einer Haltestelle."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: GradNoClient) -> None:
        interval = max(MIN_SCAN_INTERVAL, int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.stop = stop_from_entry(entry)

    async def _async_update_data(self) -> Board:
        try:
            return await self.client.fetch_board(self.stop)
        except GradNoError as err:
            raise UpdateFailed(str(err)) from err
