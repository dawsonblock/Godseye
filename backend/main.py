"""
Godseye — Telemetry Hub (v4)
FastAPI backend: tar1090/readsb local ADS-B, OpenSky fallback,
CelesTrak satellite TLE + SGP4, anomaly detection, WebSocket fanout.
"""

import asyncio
import json
import math
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sgp4.api import Satrec, jday

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────

TAR1090_URL = os.getenv("TAR1090_URL", "").strip()
TAR1090_POLL_SECONDS = float(os.getenv("TAR1090_POLL_SECONDS", "2"))

OPENSKY_USER = os.getenv("OPENSKY_USER", "").strip()
OPENSKY_PASS = os.getenv("OPENSKY_PASS", "").strip()
OPENSKY_POLL_SECONDS = float(os.getenv("OPENSKY_POLL_SECONDS", "10"))

SAT_TLE_REFRESH_SECONDS = float(os.getenv("SAT_TLE_REFRESH_SECONDS", "21600"))
WS_PUSH_HZ = float(os.getenv("WS_PUSH_HZ", "1"))
SAT_STREAM_LIMIT = int(os.getenv("SAT_STREAM_LIMIT", "2000"))

AC_SOURCE = "tar1090" if TAR1090_URL else "opensky"

# ── App with lifespan ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start background tasks on startup, cancel on shutdown."""
    tasks = [
        asyncio.create_task(poll_aircraft_loop()),
        asyncio.create_task(refresh_tles_loop()),
        asyncio.create_task(push_loop()),
    ]
    print(
        f"[godseye] aircraft source: {AC_SOURCE}"
        + (f" ({TAR1090_URL})" if AC_SOURCE == "tar1090" else "")
    )
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Godseye", lifespan=lifespan)

# ── Utilities ────────────────────────────────────────────────────────────────


def now_unix() -> float:
    return time.time()


def deg2rad(d: float) -> float:
    return d * math.pi / 180.0


def rad2deg(r: float) -> float:
    return r * 180.0 / math.pi


_A = 6378137.0
_E2 = 6.69437999014e-3


def gmst_from_jd(jd: float) -> float:
    T = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * T * T
        - (T * T * T) / 38710000.0
    ) % 360.0
    return deg2rad(gmst_deg)


def eci_to_ecef(
    r_eci_km: Tuple[float, float, float], jd: float
) -> Tuple[float, float, float]:
    theta = gmst_from_jd(jd)
    c, s = math.cos(theta), math.sin(theta)
    x, y, z = r_eci_km
    return ((c * x + s * y) * 1000.0, (-s * x + c * y) * 1000.0, z * 1000.0)


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    lon = math.atan2(y, x)
    p = math.sqrt(x * x + y * y)
    lat = math.atan2(z, p * (1 - _E2))
    for _ in range(5):
        sin_lat = math.sin(lat)
        N = _A / math.sqrt(1 - _E2 * sin_lat * sin_lat)
        lat = math.atan2(z + _E2 * N * sin_lat, p)
    sin_lat = math.sin(lat)
    N = _A / math.sqrt(1 - _E2 * sin_lat * sin_lat)
    alt = p / math.cos(lat) - N
    return (rad2deg(lat), rad2deg(lon), alt)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    la1, la2 = deg2rad(lat1), deg2rad(lat2)
    dla = la2 - la1
    dlo = deg2rad(lon2 - lon1)
    a = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


# ── Aircraft State ──────────────────────────────────────────────────────────


@dataclass
class AircraftState:
    icao24: str
    callsign: str
    lat: float
    lon: float
    baro_alt_m: float
    geo_alt_m: float
    vel_mps: float
    heading_deg: float
    vertical_rate: float
    last_update: float
    origin: str
    on_ground: bool
    # tar1090-enriched fields
    registration: str
    ac_type: str
    squawk: str
    rssi: float
    category: str
    source: str  # "tar1090" or "opensky"


aircraft: Dict[str, AircraftState] = {}
prev_aircraft: Dict[str, AircraftState] = {}
_poll_anomalies_pending = False


# ── tar1090 / readsb Poller ─────────────────────────────────────────────────


async def poll_tar1090_loop():
    """Poll a local tar1090/readsb aircraft.json endpoint."""
    global _poll_anomalies_pending

    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                r = await client.get(TAR1090_URL)
                if r.status_code == 200:
                    data = r.json()
                    t = data.get("now", now_unix())
                    ac_list = data.get("aircraft", [])

                    prev_aircraft.clear()
                    prev_aircraft.update(aircraft)

                    for a in ac_list:
                        try:
                            icao24 = (a.get("hex") or "").strip().lower()
                            if not icao24 or icao24.startswith("~"):
                                continue  # skip non-ICAO (TIS-B)

                            lat = a.get("lat")
                            lon = a.get("lon")
                            if lat is None or lon is None:
                                continue

                            callsign = (a.get("flight") or "").strip()

                            # tar1090 uses feet for altitudes
                            baro_alt_ft = a.get("alt_baro")
                            if baro_alt_ft == "ground":
                                baro_alt_m = 0.0
                                on_ground = True
                            elif baro_alt_ft is not None:
                                baro_alt_m = float(baro_alt_ft) * 0.3048
                                on_ground = False
                            else:
                                baro_alt_m = 0.0
                                on_ground = a.get("alt_baro") == "ground"

                            geo_alt_ft = a.get("alt_geom")
                            geo_alt_m = (
                                float(geo_alt_ft) * 0.3048
                                if geo_alt_ft is not None
                                else baro_alt_m
                            )

                            # tar1090 uses knots for speed
                            gs_knots = a.get("gs") or 0
                            vel_mps = float(gs_knots) * 0.514444

                            track = float(a.get("track") or 0)

                            # Vertical rate: tar1090 gives ft/min
                            baro_rate = a.get("baro_rate") or a.get("geom_rate") or 0
                            vrate_mps = float(baro_rate) * 0.00508  # ft/min → m/s

                            registration = (a.get("r") or "").strip()
                            ac_type = (a.get("t") or "").strip()
                            squawk = (a.get("squawk") or "").strip()
                            rssi = float(a.get("rssi") or 0)
                            category = (a.get("category") or "").strip()

                            aircraft[icao24] = AircraftState(
                                icao24=icao24,
                                callsign=callsign,
                                lat=float(lat),
                                lon=float(lon),
                                baro_alt_m=baro_alt_m,
                                geo_alt_m=geo_alt_m,
                                vel_mps=vel_mps,
                                heading_deg=track,
                                vertical_rate=vrate_mps,
                                last_update=t,
                                origin="",
                                on_ground=on_ground,
                                registration=registration,
                                ac_type=ac_type,
                                squawk=squawk,
                                rssi=rssi,
                                category=category,
                                source="tar1090",
                            )
                        except Exception:
                            continue

                    # Prune aircraft not seen recently (tar1090 timeout ~ 60s)
                    cutoff = t - 60.0
                    for k in [k for k, v in aircraft.items() if v.last_update < cutoff]:
                        aircraft.pop(k, None)

                    _poll_anomalies_pending = True

            except Exception as e:
                print(f"[tar1090] poll error: {e}")

            await asyncio.sleep(TAR1090_POLL_SECONDS)


# ── OpenSky Poller (fallback) ───────────────────────────────────────────────


async def poll_opensky_loop():
    """Poll OpenSky Network API when no tar1090 is configured."""
    global _poll_anomalies_pending
    url = "https://opensky-network.org/api/states/all"
    auth = (OPENSKY_USER, OPENSKY_PASS) if (OPENSKY_USER and OPENSKY_PASS) else None

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                kw = {"auth": auth} if auth else {}
                r = await client.get(url, **kw)
                if r.status_code == 200:
                    data = r.json()
                    states = data.get("states") or []
                    t = now_unix()

                    prev_aircraft.clear()
                    prev_aircraft.update(aircraft)

                    for s in states:
                        try:
                            icao24 = (s[0] or "").strip()
                            if not icao24:
                                continue
                            lat, lon = s[6], s[5]
                            if lat is None or lon is None:
                                continue

                            callsign = (s[1] or "").strip()
                            baro_alt = float(s[7]) if s[7] is not None else 0.0
                            geo_alt = float(s[13]) if s[13] is not None else baro_alt
                            vel = float(s[9]) if s[9] is not None else 0.0
                            heading = float(s[10]) if s[10] is not None else 0.0
                            vrate = float(s[11]) if s[11] is not None else 0.0
                            origin = (s[2] or "").strip()
                            on_ground = bool(s[8]) if s[8] is not None else False

                            aircraft[icao24] = AircraftState(
                                icao24=icao24,
                                callsign=callsign,
                                lat=float(lat),
                                lon=float(lon),
                                baro_alt_m=baro_alt,
                                geo_alt_m=geo_alt,
                                vel_mps=vel,
                                heading_deg=heading,
                                vertical_rate=vrate,
                                last_update=t,
                                origin=origin,
                                on_ground=on_ground,
                                registration="",
                                ac_type="",
                                squawk="",
                                rssi=0.0,
                                category="",
                                source="opensky",
                            )
                        except Exception:
                            continue

                    cutoff = t - max(30.0, 3.0 * OPENSKY_POLL_SECONDS)
                    for k in [k for k, v in aircraft.items() if v.last_update < cutoff]:
                        aircraft.pop(k, None)

                    _poll_anomalies_pending = True

            except Exception:
                pass

            await asyncio.sleep(OPENSKY_POLL_SECONDS)


# Unified dispatcher
async def poll_aircraft_loop():
    if AC_SOURCE == "tar1090":
        await poll_tar1090_loop()
    else:
        await poll_opensky_loop()


# ── Satellites (TLE + SGP4) ─────────────────────────────────────────────────


@dataclass
class SatState:
    norad_id: str
    name: str
    sat: Satrec
    tle1: str
    tle2: str
    last_tle_refresh: float


sats: Dict[str, SatState] = {}

CELESTRAK_TLE_URL = (
    "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
)


async def refresh_tles_loop():
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                r = await client.get(CELESTRAK_TLE_URL)
                if r.status_code == 200:
                    lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
                    t = now_unix()
                    for i in range(0, len(lines) - 2, 3):
                        name = lines[i]
                        l1, l2 = lines[i + 1], lines[i + 2]
                        if not (l1.startswith("1 ") and l2.startswith("2 ")):
                            continue
                        norad_id = l1[2:7].strip()
                        try:
                            sat = Satrec.twoline2rv(l1, l2)
                            sats[norad_id] = SatState(
                                norad_id=norad_id,
                                name=name,
                                sat=sat,
                                tle1=l1,
                                tle2=l2,
                                last_tle_refresh=t,
                            )
                        except Exception:
                            continue
            except Exception:
                pass
            await asyncio.sleep(SAT_TLE_REFRESH_SECONDS)


def propagate_sat_ecef(
    s: SatState, t_unix: float
) -> Optional[Tuple[float, float, float]]:
    tm = time.gmtime(t_unix)
    frac = t_unix - int(t_unix)
    jd, fr = jday(
        tm.tm_year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec + frac
    )
    e, r_eci_km, _ = s.sat.sgp4(jd, fr)
    if e != 0:
        return None
    return eci_to_ecef(r_eci_km, jd + fr)


def sat_altitude_km(ecef_m: Tuple[float, float, float]) -> float:
    x, y, z = ecef_m
    r = math.sqrt(x * x + y * y + z * z)
    return (r - _A) / 1000.0


# ── Anomaly / Event Engine ──────────────────────────────────────────────────

MAX_SPEED_MPS = 370.0
MAX_VERTICAL_RATE = 80.0
MAX_HEADING_CHANGE = 60.0
MAX_POSITION_JUMP_M = 100_000.0

_event_cooldown: Dict[str, float] = {}
EVENT_COOLDOWN_SECS = 30.0

# Squawk anomaly codes
SQUAWK_ALERTS = {
    "7500": "hijack",
    "7600": "radio_fail",
    "7700": "emergency",
}


@dataclass
class Event:
    timestamp: float
    kind: str
    object_id: str
    detail: str


event_buffer: deque = deque(maxlen=500)


def _emit_event(kind: str, obj_id: str, detail: str):
    key = f"{kind}:{obj_id}"
    t = now_unix()
    last = _event_cooldown.get(key, 0)
    if t - last < EVENT_COOLDOWN_SECS:
        return
    _event_cooldown[key] = t
    event_buffer.append(Event(t, kind, obj_id, detail))


def detect_aircraft_anomalies():
    for icao24, curr in aircraft.items():
        prev = prev_aircraft.get(icao24)
        label = curr.callsign or curr.registration or icao24

        # Squawk alert codes (always check, no prev needed)
        if curr.squawk in SQUAWK_ALERTS:
            _emit_event(
                SQUAWK_ALERTS[curr.squawk], icao24, f"{label}: squawk {curr.squawk}"
            )

        if prev is None:
            continue

        # Speed overshoot
        if curr.vel_mps > MAX_SPEED_MPS and not curr.on_ground:
            _emit_event("speed_exceed", icao24, f"{label}: {curr.vel_mps:.0f} m/s")

        # Vertical rate spike
        if abs(curr.vertical_rate) > MAX_VERTICAL_RATE:
            _emit_event(
                "vertical_rate", icao24, f"{label}: vrate {curr.vertical_rate:.0f} m/s"
            )

        # Heading change
        dh = abs(curr.heading_deg - prev.heading_deg)
        if dh > 180:
            dh = 360 - dh
        if dh > MAX_HEADING_CHANGE:
            _emit_event("rapid_turn", icao24, f"{label}: Δhdg {dh:.0f}°")

        # Position jump
        dist = haversine_m(prev.lat, prev.lon, curr.lat, curr.lon)
        if dist > MAX_POSITION_JUMP_M:
            _emit_event("position_jump", icao24, f"{label}: jumped {dist/1000:.0f} km")

    # Clean old cooldowns
    t = now_unix()
    stale = [k for k, v in _event_cooldown.items() if t - v > EVENT_COOLDOWN_SECS * 2]
    for k in stale:
        del _event_cooldown[k]


# ── WebSocket Fanout ────────────────────────────────────────────────────────


class Hub:
    def __init__(self):
        self.clients: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        try:
            self.clients.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, msg: dict):
        if not self.clients:
            return
        payload = json.dumps(msg, separators=(",", ":"))
        dead: List[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = Hub()


# ── Routes ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return JSONResponse(
        {
            "ok": True,
            "aircraft_source": AC_SOURCE,
            "aircraft_count": len(aircraft),
            "sat_count": len(sats),
            "event_count": len(event_buffer),
        }
    )


@app.get("/sat/{norad_id}/tle")
def get_tle(norad_id: str):
    st = sats.get(norad_id)
    if not st:
        return JSONResponse({"ok": False, "error": "unknown norad_id"}, status_code=404)
    return JSONResponse(
        {
            "ok": True,
            "norad_id": st.norad_id,
            "name": st.name,
            "tle1": st.tle1,
            "tle2": st.tle2,
            "last_refresh_unix": st.last_tle_refresh,
        }
    )


@app.get("/events")
def get_events():
    return JSONResponse(
        [
            {
                "time": e.timestamp,
                "kind": e.kind,
                "object_id": e.object_id,
                "detail": e.detail,
            }
            for e in event_buffer
        ]
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await hub.connect(ws)
    try:
        await ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "server_time": now_unix(),
                    "aircraft_source": AC_SOURCE,
                }
            )
        )
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=60)
                if data == "ping":
                    await ws.send_text("pong")
            except asyncio.TimeoutError:
                try:
                    await ws.send_text('{"type":"ping"}')
                except Exception:
                    break
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)


# ── Push Loop ───────────────────────────────────────────────────────────────


async def push_loop():
    global _poll_anomalies_pending
    period = 1.0 / max(0.2, WS_PUSH_HZ)

    while True:
        t = now_unix()

        if _poll_anomalies_pending:
            detect_aircraft_anomalies()
            _poll_anomalies_pending = False

        # Aircraft snapshot — include enriched fields
        ac = []
        for v in aircraft.values():
            d = {
                "id": v.icao24,
                "cs": v.callsign,
                "lat": round(v.lat, 5),
                "lon": round(v.lon, 5),
                "alt": round(v.geo_alt_m),
                "hdg": round(v.heading_deg, 1),
                "spd": round(v.vel_mps, 1),
                "vr": round(v.vertical_rate, 1),
                "gnd": v.on_ground,
            }
            # Include enriched fields only if present (keeps payload small)
            if v.registration:
                d["reg"] = v.registration
            if v.ac_type:
                d["typ"] = v.ac_type
            if v.squawk:
                d["sqk"] = v.squawk
            if v.rssi:
                d["rss"] = round(v.rssi, 1)
            if v.origin:
                d["og"] = v.origin
            if v.category:
                d["cat"] = v.category
            ac.append(d)

        # Satellite snapshot
        sat_items = []
        for k in list(sats.keys())[:SAT_STREAM_LIMIT]:
            st = sats[k]
            pos = propagate_sat_ecef(st, t)
            if pos is None:
                continue
            x, y, z = pos
            alt_km = sat_altitude_km(pos)
            sat_items.append(
                {
                    "id": st.norad_id,
                    "nm": st.name,
                    "ecef": [round(x, 1), round(y, 1), round(z, 1)],
                    "alt_km": round(alt_km, 1),
                }
            )

        # Events
        recent_events = []
        for e in list(event_buffer)[-50:]:
            recent_events.append(
                {
                    "t": round(e.timestamp, 2),
                    "k": e.kind,
                    "oid": e.object_id,
                    "d": e.detail,
                }
            )

        await hub.broadcast(
            {
                "type": "snapshot",
                "t": t,
                "src": AC_SOURCE,
                "ac": ac,
                "sat": sat_items,
                "ev": recent_events,
            }
        )

        await asyncio.sleep(period)


# ── Static Frontend ─────────────────────────────────────────────────────────

FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
