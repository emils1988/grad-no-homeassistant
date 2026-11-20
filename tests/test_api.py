"""Tests der Datenauswertung mit Beispieldaten (gleiche Fälle wie in der Firmware)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "grad_no_api", ROOT.parent / "custom_components" / "grad_no" / "api.py"
)
api = importlib.util.module_from_spec(_spec)
sys.modules["grad_no_api"] = api
_spec.loader.exec_module(api)

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=api.TZ)  # Testdaten: 12.11.2026, ab 06:31


def fixture(name: str) -> bytes:
    return (ROOT / "fixtures" / name).read_bytes()


def stop(stop_id: str, provider: str = api.PROVIDER_SCOTTY, **kw) -> api.StopConfig:
    kw.setdefault("ahead_min", 1440)
    return api.StopConfig(provider=provider, stop_id=stop_id, **kw)


def scotty(st: api.StopConfig, name: str = "scotty_board.txt", now: datetime = NOW) -> api.Board:
    return api.parse_scotty_board(api.decode_body(fixture(name)), st, now)


def wl(st: api.StopConfig, now: datetime = NOW) -> api.Board:
    return api.parse_wl_monitor(json.loads(fixture("wl_monitor.json")), st, now)


# ---------------------------------------------------------------- Scotty


def test_scotty_board() -> None:
    b = scotty(stop("1290401"))
    d = b.departures
    assert b.station == "Wien Hbf (U)"
    assert len(d) == 8  # 7 + Bus 11 (doppelt geliefert, nur einmal angezeigt)
    assert (d[7].line, d[7].platform, d[7].time) == ("Bus 11", "B2", "06:58")
    assert d[0].kind == api.KIND_TRAM and d[0].delay == 9 and d[0].rt_time == "06:40"
    assert d[0].departure == datetime(2026, 11, 12, 6, 40, tzinfo=api.TZ)
    assert d[1].train_number == 463
    assert d[2].train_number == 66 and d[2].delay is None
    assert d[3].train_number == 19950 and d[3].cancelled
    assert d[3].destination == "München Hbf"  # Latin-1 -> UTF-8
    assert d[4].train_number is None  # REX 6 = Linie, keine Zugnummer
    assert d[5].kind == api.KIND_BUS and d[6].kind == api.KIND_SBAHN
    assert d[2].date == "2026-11-12"
    assert d[0].as_dict()["departure"].startswith("2026-11-12T06:40")


def test_scotty_changed_destination() -> None:
    d = scotty(stop("001232120"), "scotty_tullnerfeld.txt").departures
    assert len(d) == 5
    assert d[2].destination == "Wien Hbf" and d[2].destination_changed  # <b> entfernt, Zusatz gekürzt
    assert not d[0].destination_changed and not d[4].destination_changed
    assert d[3].destination == "St.Pölten Hbf"  # &#246; -> ö
    assert d[0].delay == 72


def test_scotty_ahead_and_walk() -> None:
    # 06:00 + 30 min Vorschau -> nichts (erste Abfahrt 06:31, verspätet 06:40)
    assert scotty(stop("1290401", ahead_min=30)).departures == []
    assert len(scotty(stop("1290401", ahead_min=45)).departures) == 3  # 06:40, 06:44 (EC), 06:45
    # Gehzeit 45 min: alles vor 06:45 fällt weg, ausgefallene Züge bleiben sichtbar
    lines = [d.line for d in scotty(stop("1290401", walk_min=45)).departures]
    assert "Tram O" not in lines and "EC 463" not in lines and "RJX19950" in lines
    # schon abgefahren
    late = datetime(2026, 11, 12, 6, 56, tzinfo=api.TZ)
    assert [d.line for d in scotty(stop("1290401"), now=late).departures] == ["S 1", "Bus 11"]


def test_scotty_filters() -> None:
    assert len(scotty(stop("1290401", lines="S1, REX 6, 13A")).departures) == 3
    d = scotty(stop("1290401", lines="o", direction="bruno")).departures
    assert len(d) == 1 and d[0].line == "Tram O"
    assert scotty(stop("1290401", lines="o", direction="leopoldau")).departures == []
    assert len(scotty(stop("1290401", products=api.FILTER_BUS)).departures) == 3
    assert len(scotty(stop("1290401", products=api.FILTER_TRAIN)).departures) == 5


def test_scotty_url() -> None:
    assert "maxJourneys=30" in api.scotty_board_url(stop("1290401"))
    assert "maxJourneys=60" in api.scotty_board_url(stop("1290401", lines="S1"))
    assert "productsFilter=0000001011010" in api.scotty_board_url(stop("1", products=api.FILTER_BUS))


def test_scotty_garbage() -> None:
    with pytest.raises(api.GradNoError):
        api.parse_scotty_board("<html>Wartung</html>", stop("1"), NOW)


# ---------------------------------------------------------------- Wiener Linien


def test_wl_monitor() -> None:
    b = wl(stop("60201040", api.PROVIDER_WL))
    d = b.departures
    assert b.station == "Praterstern S U"
    assert len(d) == 5
    assert d[0].line == "5" and d[0].kind == api.KIND_TRAM and d[0].delay == 0  # früher dran -> 0
    assert d[0].time == "07:01" and d[0].rt_time == "06:59"
    assert d[1].line == "U1" and d[1].delay == 1 and d[1].kind == api.KIND_METRO
    assert d[2].line == "82A" and d[2].kind == api.KIND_BUS and d[2].platform == "3"
    assert d[3].line == "U1" and d[3].delay is None
    assert all(d[i - 1].departure <= d[i].departure for i in range(1, len(d)))
    assert len(wl(stop("60201040", api.PROVIDER_WL, lines="U1")).departures) == 2


def test_wl_error_message() -> None:
    with pytest.raises(api.GradNoError, match="Haltestelle"):
        api.parse_wl_monitor({"message": {"value": "Haltestelle unbekannt"}}, stop("1"), NOW)


# ---------------------------------------------------------------- Suche


def test_wl_search() -> None:
    csv = api.decode_body(fixture("wl_stops.csv"))
    assert len(api.search_wl_stops(csv, "prater")) == 2
    assert len(api.search_wl_stops(csv, "Börse")) == 1
    assert len(api.search_wl_stops(csv, "boerse")) == 1
    assert len(api.search_wl_stops(csv, "Wien Hauptbahnhof")) == 2
    assert len(api.search_wl_stops(csv, "Hbf Süd")) == 1
    assert api.search_wl_stops(csv, "Wien") == []


def test_scotty_search() -> None:
    text = (
        'SLs.sls={"suggestions":[{"value":"Wien Hbf (U)","id":"A=1@O=Wien Hbf (U)@X=1@Y=2@L=1290401@",'
        '"extId":"1290401","type":"1"},{"value":"Wien, Hauptstra&#223;e 1","id":"A=2@O=x@L=99@","type":"2"},'
        '{"value":"Villach Hbf","id":"A=1@O=Villach Hbf@L=8100147@","type":1}]};SLs.showSuggestion();'
    )
    assert api.parse_scotty_search(text) == [
        {"id": "1290401", "name": "Wien Hbf (U)"},
        {"id": "8100147", "name": "Villach Hbf"},
    ]
    assert api.parse_scotty_search("") == []


# ---------------------------------------------------------------- Wagenreihung


def test_find_eva() -> None:
    stations = json.loads(fixture("live_stations.json"))
    assert api.find_eva(stations, "Wien Hbf (U)")[0] == "8103000"
    assert len(api.find_eva(stations, "Wien Hbf (U)")) == 3
    assert api.find_eva(stations, "Villach Hbf") == ["8100147"]
    assert api.find_eva(stations, "Spittal/Drau-Millstätter See") == ["8100092"]
    assert api.find_eva(stations, "Spittal/Drau") == ["8100092"]  # Präfix
    assert api.find_eva(stations, "Amlach") == []


def test_formation() -> None:
    f = api.parse_formation(json.loads(fixture("live_info_66.json")))
    assert f is not None
    assert len(f.wagons) == 16 and len(f.parts) == 2 and f.load_info
    assert f.wagons[0].kind == "engine" and f.wagons[7].kind == "cab" and f.wagons[7].business == 16
    assert f.wagons[5].dining
    assert f.wagons[1].bikes == 1 and f.wagons[12].load == 3 and f.wagons[12].part == 1
    assert f.parts[1] == "Muenchen Hbf"
    assert f.title.startswith("RJX 66") and f.platform == "8"
    assert api.parse_formation({"train": {"wagons": []}}) is None


# ---------------------------------------------------------------- Helfer


def test_helpers() -> None:
    assert api.train_number_of("RJX 66") == 66
    assert api.train_number_of("RJX19950") == 19950
    assert api.train_number_of("S 1") is None
    assert api.line_matches("Bus 5110", "5110")
    assert api.line_matches("S 1", "s1")
    assert not api.line_matches("S 10", "S1")
    assert api.kind_from_line("U4") == api.KIND_METRO
    assert api.clean_text("<b>Wien&nbsp; Hbf</b>") == "Wien Hbf"
    assert api.short_dest("Wien Hbf (Bahnsteige 3-12)") == "Wien Hbf"
