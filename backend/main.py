"""
Live Earth MVP — Telemetry Hub (v3 — fixed & optimized)
FastAPI backend: OpenSky aircraft polling, CelesTrak satellite TLE + SGP4,
anomaly detection, WebSocket fanout.
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

OPENSKY_USER = os.getenv("OPENSKY_USER", "").strip()
OPENSKY_PASS = os.getenv("OPENSKY_PASS", "").strip()
OPENSKY_POLL_SECONDS = float(os.getenv("OPENSKY_POLL_SECONDS", "10"))
SAT_TLE_REFRESH_SECONDS = float(os.getenv("SAT_TLE_REFRESH_SECONDS", "21600"))
WS_PUSH_HZ = float(os.getenv("WS_PUSH_HZ", "1"))
SAT_STREAM_LIMIT = int(os.getenv("SAT_STREAM_LIMIT", "2000"))

# ── App with lifespan ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start background tasks on startup, cancel on shutdown."""
    tasks = [
        asyncio.create_task(poll_opensky_loop()),
        asyncio.create_task(refresh_tles_loop()),
        asyncio.create_task(push_loop()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Live Earth MVP", lifespan=lifespan)

# ── Utilities ────────────────────────────────────────────────────────────────


def now_unix() -> float:
    return time.time()


def deg2rad(d: float) -> float:
    return d * math.pi / 180.0


def rad2deg(r: float) -> float:
    return r * 180.0 / math.pi


# WGS-84
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
    """ECEF (meters) → (lat_deg, lon_deg, alt_m)."""
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
    """Great-circle distance in meters."""
    la1, la2 = deg2rad(lat1), deg2rad(lat2)
    dla = la2 - la1
    dlo = deg2rad(lon2 - lon1)
    a = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


# ── Aircraft (OpenSky) ──────────────────────────────────────────────────────


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


aircraft: Dict[str, AircraftState] = {}
prev_aircraft: Dict[str, AircraftState] = {}  # previous poll for anomaly detection
_poll_anomalies_pending = False  # flag: run anomalies only once per poll


async def poll_opensky_loop():
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

                    # Snapshot previous state for anomaly detection
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
                            )
                        except Exception:
                            continue

                    # Prune stale
                    cutoff = t - max(30.0, 3.0 * OPENSKY_POLL_SECONDS)
                    for k in [k for k, v in aircraft.items() if v.last_update < cutoff]:
                        aircraft.pop(k, None)

                    # Flag anomaly detection to run once
                    _poll_anomalies_pending = True

            except Exception:
                pass

            await asyncio.sleep(OPENSKY_POLL_SECONDS)


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
    return (r - _A) / 1000.0  # rough altitude in km


# ── Anomaly / Event Engine ──────────────────────────────────────────────────

MAX_SPEED_MPS = 370.0  # ~Mach 1.1 at altitude
MAX_VERTICAL_RATE = 80.0  # m/s
MAX_HEADING_CHANGE = 60.0  # degrees per poll cycle
MAX_POSITION_JUMP_M = 100_000.0  # 100 km in one poll

# Per-object cooldown to prevent event flooding
_event_cooldown: Dict[str, float] = {}
EVENT_COOLDOWN_SECS = 30.0


@dataclass
class Event:
    timestamp: float
    kind: str
    object_id: str
    detail: str


event_buffer: deque = deque(maxlen=500)


def _emit_event(kind: str, obj_id: str, detail: str):
    """Emit event with per-object+kind cooldown to avoid flooding."""
    key = f"{kind}:{obj_id}"
    t = now_unix()
    last = _event_cooldown.get(key, 0)
    if t - last < EVENT_COOLDOWN_SECS:
        return  # suppress duplicate
    _event_cooldown[key] = t
    event_buffer.append(Event(t, kind, obj_id, detail))


def detect_aircraft_anomalies():
    for icao24, curr in aircraft.items():
        prev = prev_aircraft.get(icao24)
        if prev is None:
            continue

        label = curr.callsign or icao24

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
            _emit_event("rapid_turn", icao24, f"{label}: Δheading {dh:.0f}°")

        # Position jump (possible spoofing)
        dist = haversine_m(prev.lat, prev.lon, curr.lat, curr.lon)
        if dist > MAX_POSITION_JUMP_M:
            _emit_event(
                "position_jump", icao24, f"{label}: jumped {dist / 1000:.0f} km"
            )

    # Clean old cooldowns
    t = now_unix()
    stale_keys = [
        k for k, v in _event_cooldown.items() if t - v > EVENT_COOLDOWN_SECS * 2
    ]
    for k in stale_keys:
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
        payload = json.dumps(msg, separators=(",", ":"))  # compact JSON
        dead: List[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = Hub()

# Cache last-broadcast event list to send only NEW events
_last_broadcast_event_idx = 0


# ── Routes ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return JSONResponse(
        {
            "ok": True,
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
        await ws.send_text(json.dumps({"type": "hello", "server_time": now_unix()}))
        while True:
            # Keepalive: wait for client pings or detect disconnect
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=60)
                # respond to pings
                if data == "ping":
                    await ws.send_text("pong")
            except asyncio.TimeoutError:
                # No message in 60s — send keepalive check
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
    global _poll_anomalies_pending, _last_broadcast_event_idx
    period = 1.0 / max(0.2, WS_PUSH_HZ)

    while True:
        t = now_unix()

        # Run anomaly detection ONCE per poll, not every push
        if _poll_anomalies_pending:
            detect_aircraft_anomalies()
            _poll_anomalies_pending = False

        # Aircraft snapshot
        ac = []
        for v in aircraft.values():
            ac.append(
                {
                    "id": v.icao24,
                    "cs": v.callsign,
                    "lat": round(v.lat, 5),
                    "lon": round(v.lon, 5),
                    "alt": round(v.geo_alt_m),
                    "hdg": round(v.heading_deg, 1),
                    "spd": round(v.vel_mps, 1),
                    "vr": round(v.vertical_rate, 1),
                    "og": v.origin,
                    "gnd": v.on_ground,
                }
            )

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

        # Send only the LAST 50 events (enough for the sidebar)
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
                "ac": ac,
                "sat": sat_items,
                "ev": recent_events,
            }
        )

        await asyncio.sleep(period)


# ── Static Frontend (must be LAST so it doesn't shadow API routes) ──────────

FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
