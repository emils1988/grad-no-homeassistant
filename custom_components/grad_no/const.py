"""Konstanten für Grad no!."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "grad_no"
VERSION: Final = "0.1.0"

CONF_PROVIDER: Final = "provider"
CONF_STOP_ID: Final = "stop_id"
CONF_STOP_NAME: Final = "stop_name"
CONF_QUERY: Final = "query"
CONF_STOP: Final = "stop"
CONF_PRODUCTS: Final = "products"
CONF_LINES: Final = "lines"
CONF_DIRECTION: Final = "direction"
CONF_WALK_MIN: Final = "walk_min"
CONF_AHEAD_MIN: Final = "ahead_min"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_AHEAD_MIN: Final = 60
MAX_AHEAD_MIN: Final = 1440
DEFAULT_SCAN_INTERVAL: Final = 60
# kürzestes erlaubtes Abfrageintervall in Sekunden (schont die Server)
MIN_SCAN_INTERVAL: Final = 30

ATTRIBUTION_SCOTTY: Final = "Daten: ÖBB Scotty"
ATTRIBUTION_WL: Final = "Datenquelle: Stadt Wien – data.wien.gv.at (CC BY 4.0)"

SERVICE_GET_FORMATION: Final = "get_formation"
ATTR_TRAIN_NUMBER: Final = "train_number"
ATTR_STATION: Final = "station"
ATTR_DATE: Final = "date"
ATTR_EVA: Final = "eva"

CARD_URL: Final = "/grad_no/grad-no-card.js"
