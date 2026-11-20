"""Tests für Einrichtung, Sensor, Optionen und Dienst (mit Beispieldaten statt echter Server)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from homeassistant import config_entries
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.grad_no.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
SCOTTY_BOARD = re.compile(r"fahrplan\.oebb\.at/bin/stboard\.exe")
SCOTTY_SEARCH = re.compile(r"fahrplan\.oebb\.at/bin/ajax-getstop\.exe")
WL_MONITOR = re.compile(r"wienerlinien\.at/ogd_realtime/monitor")
WL_STOPS = re.compile(r"wienerlinien-ogd-haltestellen\.csv")
LIVE_STATIONS = re.compile(r"live\.oebb\.at/assets/assets/stations\.json")
LIVE_INFO = re.compile(r"live\.oebb\.at/backend/info")

SEARCH_RESPONSE = (
    'SLs.sls={"suggestions":[{"value":"Wien Hbf (U)","id":"A=1@O=Wien Hbf (U)@L=1290401@",'
    '"extId":"1290401","type":"1"},{"value":"Wien Westbahnhof","id":"A=1@O=x@L=1291501@","type":"1"}]};'
)


def fx(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def now(freezer):
    # Beispieldaten sind vom 12.11.2026, ab 06:31 Uhr
    freezer.move_to("2026-11-12T05:00:00+00:00")  # 06:00 Wien
    return freezer


def scotty_entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Wien Hbf",
        data={"provider": "scotty", "stop_id": "1290401", "stop_name": "Wien Hbf (U)"},
        options={"products": "all", "lines": "", "direction": "", "walk_min": 0, "ahead_min": 1440, **options},
    )


# ---------------------------------------------------------------- Config-Flow


async def test_flow_scotty(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(SCOTTY_SEARCH, content=SEARCH_RESPONSE.encode("latin-1"))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "scotty", "query": "Wien Hbf"}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "stop"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"stop": "1291501", "name": "", "products": "train", "lines": " S1, REX 7 ", "walk_min": 5, "ahead_min": 90},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wien Westbahnhof"
    assert result["data"] == {"provider": "scotty", "stop_id": "1291501", "stop_name": "Wien Westbahnhof"}
    assert result["options"]["lines"] == "S1, REX 7"
    assert result["options"]["walk_min"] == 5 and result["options"]["products"] == "train"


async def test_flow_wl_search_and_no_results(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(WL_STOPS, content=fx("wl_stops.csv"))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "wiener_linien", "query": "Kahlenberg"}
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"query": "no_results"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "wiener_linien", "query": "Börse"}
    )
    assert result["step_id"] == "stop"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"stop": "60200195", "name": "Börse"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["provider"] == "wiener_linien" and result["data"]["stop_id"] == "60200195"
    assert aioclient_mock.call_count == 1  # Haltestellenliste nur einmal geladen


async def test_flow_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(SCOTTY_SEARCH, status=503)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "scotty", "query": "Villach"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


# ---------------------------------------------------------------- Sensor


async def test_sensor_scotty(hass: HomeAssistant, aioclient_mock, now) -> None:
    aioclient_mock.get(SCOTTY_BOARD, content=fx("scotty_board.txt"))
    entry = scotty_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.wien_hbf_next_departure")
    assert state is not None
    assert state.state == "2026-11-12T05:40:00+00:00"  # Tram O, 06:31 + 9 min
    a = state.attributes
    assert a["station"] == "Wien Hbf (U)" and a["line"] == "Tram O" and a["delay"] == 9
    assert len(a["departures"]) == 8
    assert a["departures"][3]["cancelled"] and a["departures"][3]["destination"] == "München Hbf"
    assert a["departures"][2]["train_number"] == 66
    assert "Scotty" in a["attribution"]


async def test_options_reload(hass: HomeAssistant, aioclient_mock, now) -> None:
    aioclient_mock.get(SCOTTY_BOARD, content=fx("scotty_board.txt"))
    entry = scotty_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"products": "all", "lines": "S1, REX 6, 13A", "direction": "", "walk_min": 0, "ahead_min": 1440,
         "scan_interval": 45},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 45
    await hass.async_block_till_done()
    state = hass.states.get("sensor.wien_hbf_next_departure")
    assert [d["line"] for d in state.attributes["departures"]] == ["REX 6", "Bus 13A", "S 1"]


async def test_sensor_wl_and_unavailable(hass: HomeAssistant, aioclient_mock, now) -> None:
    aioclient_mock.get(WL_MONITOR, content=fx("wl_monitor.json"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Praterstern",
        data={"provider": "wiener_linien", "stop_id": "60201040", "stop_name": "Praterstern"},
        options={"ahead_min": 1440},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.praterstern_next_departure")
    assert state.state == "2026-11-12T05:59:10+00:00"
    assert state.attributes["line"] == "5" and "data.wien.gv.at" in state.attributes["attribution"]

    aioclient_mock.clear_requests()
    aioclient_mock.get(WL_MONITOR, status=500)
    now.tick(120)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.praterstern_next_departure").state == STATE_UNAVAILABLE


# ---------------------------------------------------------------- Dienst Wagenreihung


async def test_service_formation(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(LIVE_STATIONS, content=fx("live_stations.json"))
    aioclient_mock.get(LIVE_INFO, content=fx("live_info_66.json"))
    assert await async_setup_component(hass, DOMAIN, {})
    res = await hass.services.async_call(
        DOMAIN, "get_formation", {"train_number": 66, "station": "Wien Hbf (U)", "date": "2026-11-01"},
        blocking=True, return_response=True,
    )
    assert len(res["wagons"]) == 16 and res["parts"][1] == "Muenchen Hbf" and res["platform"] == "8"
    assert res["wagons"][5]["dining"] is True
    assert "station=8103000" in str(aioclient_mock.mock_calls[-1][1])

    with pytest.raises(HomeAssistantError, match="Bahnhof nicht gefunden"):
        await hass.services.async_call(
            DOMAIN, "get_formation", {"train_number": 66, "station": "Amlach"}, blocking=True, return_response=True
        )
