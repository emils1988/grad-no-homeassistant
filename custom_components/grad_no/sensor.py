"""Sensor „Nächste Abfahrt“ mit der ganzen Abfahrtstafel in den Attributen."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PROVIDER_WL, Departure
from .const import ATTRIBUTION_SCOTTY, ATTRIBUTION_WL, CONF_PROVIDER, DOMAIN
from .coordinator import GradNoConfigEntry, GradNoCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: GradNoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([NextDepartureSensor(entry.runtime_data, entry)])


class NextDepartureSensor(CoordinatorEntity[GradNoCoordinator], SensorEntity):
    """Zeitpunkt der nächsten Abfahrt (ausgefallene werden übersprungen)."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_departure"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # die Tafel ändert sich jede Minute -> nicht in der Datenbank speichern
    _unrecorded_attributes = frozenset({"departures", "line", "destination", "platform", "delay"})

    def __init__(self, coordinator: GradNoCoordinator, entry: GradNoConfigEntry) -> None:
        super().__init__(coordinator)
        self._provider = entry.data[CONF_PROVIDER]
        self._attr_unique_id = f"{entry.entry_id}_next_departure"
        self._attr_attribution = ATTRIBUTION_WL if self._provider == PROVIDER_WL else ATTRIBUTION_SCOTTY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Wiener Linien" if self._provider == PROVIDER_WL else "ÖBB Scotty",
            model="Haltestelle",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _next(self) -> Departure | None:
        return next((d for d in self.coordinator.data.departures if not d.cancelled), None)

    @property
    def native_value(self) -> datetime | None:
        d = self._next()
        return d.departure if d else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        board = self.coordinator.data
        d = self._next()
        return {
            "station": board.station,
            "provider": self._provider,
            "line": d.line if d else None,
            "destination": d.destination if d else None,
            "platform": d.platform if d else None,
            "delay": d.delay if d else None,
            "departures": [x.as_dict() for x in board.departures],
        }
