"""Datenabfrage für Grad no! (ÖBB Scotty, Wiener Linien, ÖBB Live).

Portiert aus der Firmware (src/hafas.cpp). Die Auswertung besteht aus reinen
Funktionen ohne Home-Assistant-Abhängigkeit, damit sie mit Beispieldaten
getestet werden kann. Die Netzwerkzugriffe laufen über eine aiohttp-Session.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp

TZ = ZoneInfo("Europe/Vienna")

# ÖBB Scotty (HAFAS) – inoffizielle, aber seit Jahren stabile Endpunkte der Fahrplanauskunft.
# Scotty enthält ganz Österreich (inkl. Wiener Linien, Graz Linien, Postbus ...).
BOARD_URL = "https://fahrplan.oebb.at/bin/stboard.exe/dn"
SEARCH_URL = "https://fahrplan.oebb.at/bin/ajax-getstop.exe/dn"
# Wiener Linien Open Data (CC BY 4.0, Datenquelle: Stadt Wien – data.wien.gv.at), ohne API-Key
WL_MONITOR_URL = "https://www.wienerlinien.at/ogd_realtime/monitor"
WL_STOPS_URL = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-haltestellen.csv"
# ÖBB Live (Wagenreihung) – inoffiziell
LIVE_INFO_URL = "https://live.oebb.at/backend/info"
LIVE_STATIONS_URL = "https://live.oebb.at/assets/assets/stations.json"

USER_AGENT = "Mozilla/5.0 (HomeAssistant; GradNo)"
TIMEOUT = aiohttp.ClientTimeout(total=20)

PROVIDER_SCOTTY = "scotty"
PROVIDER_WL = "wiener_linien"

FILTER_ALL = "all"
FILTER_TRAIN = "train"
FILTER_BUS = "bus"

# productsFilter: eine Stelle pro HAFAS-Produktklasse
#   0 RJ/RJX/ICE  1 IC/EC  2 NJ/EN  3 D  4 REX/R  5 S-Bahn  6 Bus  7 Schiff
#   8 U-Bahn  9 Straßenbahn  10 –  11 Anrufsammeltaxi  12 Sonstige Züge
PRODUCTS_FILTER = {
    FILTER_ALL: "1111111111111",
    FILTER_TRAIN: "1111110000001",
    FILTER_BUS: "0000001011010",
}

KIND_TRAIN = "train"
KIND_SBAHN = "sbahn"
KIND_BUS = "bus"
KIND_TRAM = "tram"
KIND_METRO = "metro"

# Fernverkehr: hier ist die Nummer im Namen eine echte Zugnummer (bei REX 6, S 1, R 81 ... eine Liniennummer)
LONG_DISTANCE = {"RJ", "RJX", "IC", "EC", "EN", "NJ", "D", "ICE", "ECE", "TGV", "IR", "EST"}

MAX_DEPARTURES = 20


class GradNoError(Exception):
    """Fehler bei der Abfrage (Verbindung, HTTP, Antwort nicht lesbar)."""


# ---------------------------------------------------------------- Datenmodell


@dataclass
class StopConfig:
    """Eine Haltestelle mit Filtern, wie in der Weboberfläche der Anzeige."""

    provider: str
    stop_id: str
    name: str = ""
    products: str = FILTER_ALL  # nur Scotty
    direction: str = ""  # Zielfilter, Komma-getrennt ("Villach, Spittal")
    lines: str = ""  # Linienfilter, Komma-getrennt ("S1, REX 7, 5110")
    walk_min: int = 0  # Gehzeit: Abfahrten, die früher abfahren, ausblenden
    ahead_min: int = 60  # Vorschau: nur Abfahrten in den nächsten x Minuten


@dataclass
class Departure:
    time: str  # geplante Abfahrt HH:MM
    rt_time: str | None  # Prognose HH:MM
    line: str
    kind: str
    destination: str
    destination_changed: bool
    platform: str
    delay: int | None  # Minuten, None = keine Echtzeitdaten
    cancelled: bool
    planned: datetime | None
    departure: datetime | None  # tatsächliche (bzw. erwartete) Abfahrt
    date: str  # YYYY-MM-DD (für die Wagenreihung)
    train_number: int | None  # nur Fernverkehr

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["planned"] = self.planned.isoformat() if self.planned else None
        d["departure"] = self.departure.isoformat() if self.departure else None
        return d


@dataclass
class Board:
    station: str
    departures: list[Departure] = field(default_factory=list)


@dataclass
class Wagon:
    kind: str  # coach, cab, engine
    number: int | None
    first: int
    second: int
    business: int
    bikes: int
    wheelchairs: int
    load: int  # 0 = unbekannt, 1..3
    dining: bool
    quiet: bool
    family: bool
    part: int  # Index in Formation.parts


@dataclass
class Formation:
    title: str
    station: str
    platform: str
    parts: list[str]
    wagons: list[Wagon]
    load_info: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- Text-Helfer

_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")


def decode_body(raw: bytes) -> str:
    """Scotty liefert Latin-1, alle anderen UTF-8."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def clean_text(value: Any) -> str:
    """HTML-Auszeichnungen und -Entities entfernen, Leerzeichen normalisieren."""
    if value is None or value is False:
        return ""
    s = str(value)
    if "<" in s:
        s = _TAG_RE.sub("", s)
    if "&" in s:
        s = html.unescape(s)
    return _SPACE_RE.sub(" ", s.replace("\xa0", " ")).strip()


_TRANSLIT = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


def fold(s: str) -> str:
    """Für die Suche: Kleinbuchstaben, Umlaute ausgeschrieben, Akzente weg."""
    s = s.translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def short_dest(s: str) -> str:
    """'Wien Hbf (Bahnsteige 3-12)' -> 'Wien Hbf'."""
    return re.sub(r"\s*\(Bahnsteig[^)]*\)?", "", s).strip()


def kind_from_line(line: str) -> str:
    p = line.strip().lower()
    if p.startswith(("bus", "ast", "o-bus", "obus", "nachtbus", "citybus", "rufbus")):
        return KIND_BUS
    if p.startswith(("tram", "str")):
        return KIND_TRAM
    if len(p) >= 2 and p[0] == "u" and (p[1].isdigit() or p[1] == " "):
        return KIND_METRO
    if len(p) >= 2 and p[0] == "s" and (p[1].isdigit() or p[1] == " "):
        return KIND_SBAHN
    return KIND_TRAIN


def _is_bus_like(kind: str) -> bool:
    return kind in (KIND_BUS, KIND_TRAM, KIND_METRO)


def product_matches(kind: str, products: str) -> bool:
    if products == FILTER_TRAIN:
        return not _is_bus_like(kind)
    if products == FILTER_BUS:
        return _is_bus_like(kind)
    return True


def _tokens(filter_list: str) -> list[str]:
    return [t.strip().lower() for t in (filter_list or "").split(",") if t.strip()]


def direction_matches(dest: str, filter_list: str) -> bool:
    toks = _tokens(filter_list)
    if not toks:
        return True
    d = dest.lower()
    return any(t in d for t in toks)


def line_matches(line: str, filter_list: str) -> bool:
    """Linienfilter: 'S1', 's 1', 'REX 7', '5110', 'O', 'U1' ...

    Treffer, wenn die ganze Linie (ohne Leerzeichen) oder ihr letztes Wort passt.
    """
    toks = _tokens(filter_list)
    if not toks:
        return True
    lo = line.strip().lower()
    compact = lo.replace(" ", "")
    last = lo.rsplit(" ", 1)[-1]
    return any(t.replace(" ", "") in (compact, last) for t in toks)


def train_number_of(line: str) -> int | None:
    m = re.match(r"\s*([A-Za-z]+)\s*(\d+)", line)
    if not m or m.group(1).upper() not in LONG_DISTANCE:
        return None
    return int(m.group(2))


def keep_by_time(dep: Departure, stop: StopConfig, now: datetime) -> bool:
    when = dep.departure
    if when is None:
        return True
    if when < now - timedelta(seconds=30):
        return False  # schon weg
    if when > now + timedelta(minutes=stop.ahead_min or 60):
        return False  # weiter in der Zukunft als die Vorschau
    if not dep.cancelled and stop.walk_min and when < now + timedelta(minutes=stop.walk_min):
        return False  # nicht mehr erreichbar
    return True


def _hm(t: datetime) -> str:
    return t.astimezone(TZ).strftime("%H:%M")


# ---------------------------------------------------------------- Scotty: Abfahrtstafel


def _scotty_json(text: str) -> Any:
    a = text.find("{")
    b = text.rfind("}")
    if a < 0 or b <= a:
        raise GradNoError("Antwort enthält keine Daten")
    try:
        return json.loads(text[a : b + 1])
    except ValueError as err:
        raise GradNoError(f"Antwort nicht lesbar: {err}") from err


def _parse_scotty_dt(da: str, hm: str) -> datetime | None:
    try:
        d, m, y = (int(x) for x in da.split("."))
        hh, mm = (int(x) for x in hm.split(":"))
    except (ValueError, AttributeError):
        return None
    if y < 100:
        y += 2000
    try:
        return datetime(y, m, d, hh, mm, tzinfo=TZ)
    except ValueError:
        return None


def parse_scotty_board(text: str, stop: StopConfig, now: datetime) -> Board:
    data = _scotty_json(text)
    board = Board(station=clean_text(data.get("stationName")) or stop.name)
    seen: set[str] = set()
    for j in data.get("journey") or []:
        # Sammelhaltestellen (z.B. "Spittal/Drau" = ganze Stadt) liefern dieselbe Fahrt mehrfach,
        # einmal pro Haltepunkt. Nur das erste (früheste) Vorkommen jeder Fahrt behalten.
        jid = str(j.get("id") or "")
        if jid:
            if jid in seen:
                continue
            seen.add(jid)
        line = clean_text(j.get("pr"))
        st_raw = str(j.get("st") or "")
        # Scotty hebt ein geändertes Ziel fett hervor (z.B. Zug endet vorzeitig)
        changed = "<b>" in st_raw.lower()
        dest = short_dest(clean_text(st_raw)) or clean_text(j.get("lastStop"))
        kind = kind_from_line(line)
        if not product_matches(kind, stop.products):
            continue
        if not direction_matches(dest, stop.direction) or not line_matches(line, stop.lines):
            continue

        ti = str(j.get("ti") or "")
        da = str(j.get("da") or "")
        planned = _parse_scotty_dt(da, ti)
        delay: int | None = None
        rt_time: str | None = None
        cancelled = False
        rt = j.get("rt")
        if isinstance(rt, dict):
            delay = 0
            status = clean_text(rt.get("status")).lower()
            if "ausfall" in status or "entf" in status or "cancel" in status:
                cancelled = True
            dlt = str(rt.get("dlt") or "")
            if len(dlt) == 5:
                rt_time = dlt
            try:
                delay = int(rt.get("dlm") or 0)
            except (TypeError, ValueError):
                delay = 0

        when = planned
        if planned and rt_time:
            real = _parse_scotty_dt(da, rt_time)
            if real and real < planned - timedelta(hours=12):
                real += timedelta(days=1)  # Verspätung über Mitternacht
            if real:
                when = real
                if (delay or 0) <= 0 and real > planned:
                    delay = int((real - planned).total_seconds() // 60)
        elif planned and delay and delay > 0:
            when = planned + timedelta(minutes=delay)

        dep = Departure(
            time=ti,
            rt_time=rt_time,
            line=line,
            kind=kind,
            destination=dest,
            destination_changed=changed,
            platform=clean_text(j.get("tr")),
            delay=delay,
            cancelled=cancelled,
            planned=planned,
            departure=when,
            date=planned.strftime("%Y-%m-%d") if planned else "",
            train_number=train_number_of(line),
        )
        if not keep_by_time(dep, stop, now):
            continue
        board.departures.append(dep)
        if len(board.departures) >= MAX_DEPARTURES:
            break
    return board


def scotty_board_url(stop: StopConfig) -> str:
    filtered = bool(stop.direction or stop.lines or stop.walk_min)
    return (
        f"{BOARD_URL}?L=vs_scotty.vs_liveticker&evaId={quote(stop.stop_id)}&boardType=dep"
        f"&productsFilter={PRODUCTS_FILTER.get(stop.products, PRODUCTS_FILTER[FILTER_ALL])}"
        "&dirInput=&tickerID=dep&start=yes&eqstops=false&outputMode=tickerDataOnly"
        f"&maxJourneys={60 if filtered else 30}"
    )


# ---------------------------------------------------------------- Wiener Linien: Abfahrtsmonitor


def _wl_kind(vtype: str | None, name: str) -> str:
    if vtype == "ptMetro":
        return KIND_METRO
    if vtype in ("ptTram", "ptTramWLB"):
        return KIND_TRAM
    if vtype and vtype.startswith("ptBus"):
        return KIND_BUS
    if vtype == "ptTrainS":
        return KIND_SBAHN
    return kind_from_line(name)


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        try:
            return datetime.fromisoformat(str(s))
        except ValueError:
            return None


def parse_wl_monitor(data: Any, stop: StopConfig, now: datetime) -> Board:
    try:
        monitors = data["data"]["monitors"] or []
    except (KeyError, TypeError) as err:
        msg = (data or {}).get("message", {}).get("value") if isinstance(data, dict) else None
        raise GradNoError(f"Antwort ohne Abfahrten{': ' + msg if msg else ''}") from err
    title = None
    if monitors:
        title = (((monitors[0].get("locationStop") or {}).get("properties") or {}).get("title"))
    board = Board(station=clean_text(title) or stop.name)
    deps: list[Departure] = []
    for m in monitors:
        for ln in m.get("lines") or []:
            name = clean_text(ln.get("name"))
            dest = clean_text(ln.get("towards"))
            if not direction_matches(dest, stop.direction) or not line_matches(name, stop.lines):
                continue
            plat = clean_text(ln.get("platform"))
            for dep in ((ln.get("departures") or {}).get("departure")) or []:
                dt = dep.get("departureTime") or {}
                planned = _parse_iso(dt.get("timePlanned"))
                real = _parse_iso(dt.get("timeReal"))
                if not planned and not real:
                    continue
                kind = _wl_kind((dep.get("vehicle") or {}).get("type"), name)
                if not product_matches(kind, stop.products):
                    continue
                base = planned or real
                delay = None
                if real:
                    delay = max(0, int((real - planned).total_seconds() // 60)) if planned else 0
                d = Departure(
                    time=_hm(base),
                    rt_time=_hm(real) if real else None,
                    line=name,
                    kind=kind,
                    destination=dest,
                    destination_changed=False,
                    platform=plat,
                    delay=delay,
                    cancelled=False,
                    planned=planned.astimezone(TZ) if planned else None,
                    departure=(real or planned).astimezone(TZ),
                    date=base.astimezone(TZ).strftime("%Y-%m-%d"),
                    train_number=None,
                )
                if keep_by_time(d, stop, now):
                    deps.append(d)
    deps.sort(key=lambda d: d.departure or now)
    board.departures = deps[:MAX_DEPARTURES]
    return board


# ---------------------------------------------------------------- Haltestellensuche


def parse_scotty_search(text: str) -> list[dict[str, str]]:
    try:
        data = _scotty_json(text)
    except GradNoError:
        return []
    out: list[dict[str, str]] = []
    for s in data.get("suggestions") or []:
        if str(s.get("type")) != "1":
            continue  # nur Haltestellen (keine Adressen/POIs)
        sid = str(s.get("extId") or "")
        if not sid:
            m = re.search(r"@L=([^@]+)", str(s.get("id") or ""))
            sid = m.group(1) if m else ""
        if not sid:
            continue
        out.append({"id": sid, "name": clean_text(s.get("value"))})
        if len(out) >= 15:
            break
    return out


def search_wl_stops(csv_text: str, query: str) -> list[dict[str, str]]:
    """Wiener Linien: Haltestellenliste (CSV) nach allen Suchwörtern durchsuchen.

    Umlaute werden vereinheitlicht (ü = ue), 'Wien' wird ignoriert (alle Haltestellen
    sind in Wien), 'Hbf' = 'Hauptbahnhof'.
    """
    words = []
    for w in re.split(r"[\s,\-]+", fold(query)):
        if not w or w == "wien":
            continue
        if w == "hbf":
            w = "hauptbahnhof"
        elif w in ("bf", "bhf"):
            w = "bahnhof"
        words.append(w)
    if not words:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in csv_text.splitlines():
        parts = line.split(";")
        if len(parts) < 2 or not parts[0][:1].isdigit():
            continue  # Kopfzeile
        diva, name = parts[0].strip(), parts[1].strip()
        if diva in seen:
            continue
        hay = fold(name).replace("-", " ")
        if all(w in hay for w in words):
            seen.add(diva)
            out.append({"id": diva, "name": name})
            if len(out) >= 15:
                break
    return out


# ---------------------------------------------------------------- Wagenreihung (live.oebb.at)


def _norm_name(s: str) -> str:
    s = fold(s)
    s = re.sub(r"\([^)]*\)?", "", s)
    s = s.replace(" bahnhof", "").replace(" bhf", "")
    return _SPACE_RE.sub(" ", s).strip()


def find_eva(stations: list[dict[str, Any]], station_name: str) -> list[str]:
    """EVA-Nummer(n) für live.oebb.at zu einem Bahnhofsnamen (max. 3, ohne Klammerzusatz zuerst)."""
    want = _norm_name(station_name)
    if len(want) < 2:
        return []
    exact: list[str] = []
    prefix_eva, prefix_len = "", 9999
    for st in stations:
        eva, raw = str(st.get("eva") or ""), str(st.get("name") or "")
        if not eva or not raw:
            continue
        nn = _norm_name(raw)
        if nn == want:
            if "(" not in raw:
                exact.insert(0, eva)
            else:
                exact.append(eva)
        elif (nn.startswith(want) or want.startswith(nn)) and len(nn) >= 4 and len(raw) < prefix_len:
            prefix_eva, prefix_len = eva, len(raw)
    if exact:
        return exact[:3]
    return [prefix_eva] if prefix_eva else []


def parse_formation(data: Any) -> Formation | None:
    tti = data.get("timeTableInfo") or {}
    train = data.get("train") or {}
    parts: list[str] = []
    wagons: list[Wagon] = []
    for w in train.get("wagons") or []:
        kind = w.get("kind") or 0
        dest = clean_text(w.get("destinationName"))
        if dest and dest not in parts:
            parts.append(dest)
        feats = int(w.get("features") or 0)
        nr = int(w.get("ranking") or 0)
        wagons.append(
            Wagon(
                kind="engine" if kind == 256 else ("cab" if kind == 1 else "coach"),
                number=nr or None,
                first=int(w.get("capacityFirstClass") or 0),
                second=int(w.get("capacitySecondClass") or 0),
                business=int(w.get("capacityBusinessClass") or 0),
                bikes=int(w.get("capacityBicycle") or 0),
                wheelchairs=int(w.get("capacityWheelChair") or 0),
                load=int(w.get("numPassengerIcons") or 0),
                dining=bool(feats & 64),
                quiet=bool(feats & 128),
                family=bool(feats & 32),
                part=parts.index(dest) if dest in parts else 0,
            )
        )
    if not wagons:
        return None
    return Formation(
        title=clean_text(tti.get("renderedTrainName")),
        station=clean_text(tti.get("stationName")),
        platform=clean_text(tti.get("platform")),
        parts=parts,
        wagons=wagons,
        load_info=bool(train.get("displayLoadInfo")),
    )


# ---------------------------------------------------------------- Netzwerk


class GradNoClient:
    """Asynchroner Client; eine Instanz pro Home Assistant (Caches werden geteilt)."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._wl_stops: str | None = None
        self._live_stations: list[dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    async def _get(self, url: str, params: dict[str, str] | None = None, retries: int = 1) -> tuple[int, bytes]:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with self._session.get(
                    url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
                ) as resp:
                    return resp.status, await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                last = err
                if attempt < retries:
                    await asyncio.sleep(1)
        raise GradNoError(f"Verbindung fehlgeschlagen: {last}") from last

    async def fetch_board(self, stop: StopConfig, now: datetime | None = None) -> Board:
        now = now or datetime.now(TZ)
        if stop.provider == PROVIDER_WL:
            status, body = await self._get(WL_MONITOR_URL, {"diva": stop.stop_id})
            if status != 200:
                raise GradNoError(f"HTTP-Fehler {status}")
            try:
                data = json.loads(decode_body(body))
            except ValueError as err:
                raise GradNoError("Antwort nicht lesbar") from err
            return parse_wl_monitor(data, stop, now)
        status, body = await self._get(scotty_board_url(stop))
        if status != 200:
            raise GradNoError(f"HTTP-Fehler {status}")
        return parse_scotty_board(decode_body(body), stop, now)

    async def search(self, provider: str, query: str) -> list[dict[str, str]]:
        query = query.strip()
        if not query:
            return []
        if provider == PROVIDER_WL:
            async with self._lock:
                if self._wl_stops is None:
                    status, body = await self._get(WL_STOPS_URL)
                    if status != 200:
                        raise GradNoError(f"HTTP-Fehler {status}")
                    self._wl_stops = decode_body(body)
            return search_wl_stops(self._wl_stops, query)
        # Erst Latin-1 (klassische HAFAS-Kodierung), bei Umlauten ohne Treffer nochmal als UTF-8
        encodings = ["latin-1", "utf-8"] if any(ord(c) > 127 for c in query) else ["latin-1"]
        for enc in encodings:
            q = quote(query.encode(enc, errors="replace"))
            url = f"{SEARCH_URL}?REQ0JourneyStopsS0A=1&REQ0JourneyStopsB=15&S={q}?&js=true&"
            status, body = await self._get(url)
            if status != 200:
                raise GradNoError(f"HTTP-Fehler {status}")
            res = parse_scotty_search(decode_body(body))
            if res:
                return res
        return []

    async def find_eva(self, station_name: str) -> list[str]:
        async with self._lock:
            if self._live_stations is None:
                status, body = await self._get(LIVE_STATIONS_URL)
                if status != 200:
                    raise GradNoError(f"HTTP-Fehler {status}")
                try:
                    self._live_stations = json.loads(decode_body(body))
                except ValueError as err:
                    raise GradNoError("Bahnhofsliste nicht lesbar") from err
        return find_eva(self._live_stations or [], station_name)

    async def fetch_formation(
        self, train_number: int, station_name: str = "", day: str | None = None, eva: str = ""
    ) -> Formation:
        evas = [e.strip() for e in eva.split(",") if e.strip()] if eva else await self.find_eva(station_name)
        if not evas:
            raise GradNoError("Bahnhof nicht gefunden")
        day = day or date.today().isoformat()
        last = 0
        for e in evas:
            status, body = await self._get(
                LIVE_INFO_URL,
                {"trainNr": str(train_number), "station": e, "date": day, "cref": "live_web"},
            )
            last = status
            if status != 200:
                continue
            try:
                f = parse_formation(json.loads(decode_body(body)))
            except ValueError:
                continue
            if f:
                return f
        if last in (200, 404):
            raise GradNoError("Keine Wagenreihung verfügbar")
        raise GradNoError(f"HTTP-Fehler {last}")
