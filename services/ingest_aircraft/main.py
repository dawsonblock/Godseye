import asyncio
import os
import time
from datetime import datetime, timezone
import httpx
import json

from libs.common.db import get_db
from libs.common.stream import publish_event, init_nats, close_nats
from libs.common.models import TrackObject

TAR1090_URL = os.getenv("TAR1090_URL", "").strip()
TAR1090_POLL_SECONDS = float(os.getenv("TAR1090_POLL_SECONDS", "2"))

OPENSKY_USER = os.getenv("OPENSKY_USER", "").strip()
OPENSKY_PASS = os.getenv("OPENSKY_PASS", "").strip()
OPENSKY_POLL_SECONDS = float(os.getenv("OPENSKY_POLL_SECONDS", "10"))

AC_SOURCE = "tar1090" if TAR1090_URL else "opensky"


async def upsert_objects_db(objects: list[TrackObject]):
    if not objects:
        return

    async with get_db() as conn:
        # Batch upsert into objects_current
        query = """
            INSERT INTO objects_current (id, kind, source, ts, lat, lon, alt_m, heading_deg, speed_mps, meta, geom)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, ST_SetSRID(ST_MakePoint($6, $5), 4326))
            ON CONFLICT (id) DO UPDATE SET
                ts = EXCLUDED.ts,
                lat = EXCLUDED.lat,
                lon = EXCLUDED.lon,
                alt_m = EXCLUDED.alt_m,
                heading_deg = EXCLUDED.heading_deg,
                speed_mps = EXCLUDED.speed_mps,
                meta = EXCLUDED.meta,
                geom = EXCLUDED.geom;
        """

        args = [
            (
                obj.id,
                obj.kind,
                obj.source,
                obj.ts,
                obj.lat,
                obj.lon,
                obj.alt_m,
                obj.heading_deg,
                obj.speed_mps,
                json.dumps(obj.meta),
            )
            for obj in objects
        ]

        await conn.executemany(query, args)

        query_hist = """
            INSERT INTO objects_history (id, kind, source, ts, lat, lon, alt_m, heading_deg, speed_mps, meta, geom)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, ST_SetSRID(ST_MakePoint($6, $5), 4326))
        """
        await conn.executemany(query_hist, args)

        # Publish to NATS stream for delta updates
        # Just send simple dict payload for delta_batch
        msg = {
            "type": "delta",
            "kind": "aircraft",
            "source": AC_SOURCE,
            "data": [
                {
                    "id": o.id,
                    "lat": round(o.lat, 5),
                    "lon": round(o.lon, 5),
                    "alt_m": round(o.alt_m, 1) if o.alt_m is not None else 0,
                    "hdg": round(o.heading_deg, 1) if o.heading_deg is not None else 0,
                    "spd": round(o.speed_mps, 1) if o.speed_mps is not None else 0,
                    "meta": o.meta,
                }
                for o in objects
            ],
        }
        await publish_event("telemetry.aircraft.update", msg)


async def poll_tar1090_loop():
    print(f"[ingest_aircraft] Starting tar1090 ingestion from {TAR1090_URL}")
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                r = await client.get(TAR1090_URL)
                if r.status_code == 200:
                    data = r.json()
                    ac_list = data.get("aircraft", [])
                    ts = datetime.now(timezone.utc)

                    objects = []
                    for a in ac_list:
                        try:
                            icao24 = (a.get("hex") or "").strip().lower()
                            if not icao24 or icao24.startswith("~"):
                                continue

                            lat = a.get("lat")
                            lon = a.get("lon")
                            if lat is None or lon is None:
                                continue

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

                            gs_knots = a.get("gs") or 0
                            vel_mps = float(gs_knots) * 0.514444

                            track = float(a.get("track") or 0)

                            baro_rate = a.get("baro_rate") or a.get("geom_rate") or 0
                            vrate_mps = float(baro_rate) * 0.00508

                            meta = {
                                "callsign": (a.get("flight") or "").strip(),
                                "on_ground": on_ground,
                                "vertical_rate": vrate_mps,
                                "registration": (a.get("r") or "").strip(),
                                "ac_type": (a.get("t") or "").strip(),
                                "squawk": (a.get("squawk") or "").strip(),
                                "rssi": float(a.get("rssi") or 0),
                                "category": (a.get("category") or "").strip(),
                            }

                            # Clean empty metas
                            meta = {
                                k: v
                                for k, v in meta.items()
                                if v != "" and v is not None
                            }

                            track_obj = TrackObject(
                                id=icao24,
                                kind="aircraft",
                                source="tar1090",
                                ts=ts,
                                lat=float(lat),
                                lon=float(lon),
                                alt_m=geo_alt_m,
                                heading_deg=track,
                                speed_mps=vel_mps,
                                meta=meta,
                            )
                            objects.append(track_obj)
                        except Exception as e:
                            continue

                    await upsert_objects_db(objects)
            except Exception as e:
                print(f"[tar1090] poll error: {e}")

            await asyncio.sleep(TAR1090_POLL_SECONDS)


import random
import math

# Global state for mock planes
_mock_planes = []


def _generate_mock_states():
    global _mock_planes
    if not _mock_planes:
        prefixes = ["DAL", "UAL", "AAL", "SWA", "JBU", "NKS", "RCH", "AF", "FLT"]
        # Initialize 500 mock planes over North America
        for i in range(500):
            lat = random.uniform(25.0, 50.0)
            lon = random.uniform(-125.0, -70.0)
            hdg = random.uniform(0, 360)
            alt = random.uniform(5000, 35000)
            vel = random.uniform(200, 300)
            icao = f"MOCK{i:02X}"
            callsign = f"{random.choice(prefixes)}{i:03d}"
            _mock_planes.append(
                {
                    "icao24": icao,
                    "callsign": callsign,
                    "lat": lat,
                    "lon": lon,
                    "hdg": hdg,
                    "alt": alt,
                    "vel": vel,
                }
            )

    # Update positions
    states = []
    for p in _mock_planes:
        # Move slightly
        dist = (p["vel"] * 0.514444) * OPENSKY_POLL_SECONDS / 111320.0  # rough degrees
        p["lat"] += math.cos(math.radians(p["hdg"])) * dist
        p["lon"] += (math.sin(math.radians(p["hdg"])) * dist) / max(
            0.1, math.cos(math.radians(p["lat"]))
        )

        # Turn slightly
        p["hdg"] = (p["hdg"] + random.uniform(-5, 5)) % 360

        # Wrap around roughly
        if p["lon"] < -130:
            p["lon"] = -70
        if p["lon"] > -60:
            p["lon"] = -125
        if p["lat"] < 20:
            p["lat"] = 50
        if p["lat"] > 55:
            p["lat"] = 25

        states.append(
            [
                p["icao24"],
                p["callsign"],
                "US",
                0,
                0,
                p["lon"],
                p["lat"],
                p["alt"],
                False,
                p["vel"],
                p["hdg"],
                0,
                None,
                p["alt"],
                None,
                False,
                0,
            ]
        )
    return states


async def poll_opensky_loop():
    print(f"[ingest_aircraft] Starting OpenSky ingestion (fallback)")
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
                elif r.status_code == 429:
                    print("[opensky] Rate limited (429). Generating mock aircraft...")
                    states = _generate_mock_states()
                else:
                    print(f"[opensky] HTTP {r.status_code}")
                    await asyncio.sleep(OPENSKY_POLL_SECONDS)
                    continue

                ts = datetime.now(timezone.utc)
                objects = []
                for s in states:
                    try:
                        icao24 = (s[0] or "").strip()
                        if not icao24:
                            continue
                        lat, lon = s[6], s[5]
                        if lat is None or lon is None:
                            continue

                        baro_alt = float(s[7]) if s[7] is not None else 0.0
                        geo_alt = float(s[13]) if s[13] is not None else baro_alt
                        vel = float(s[9]) if s[9] is not None else 0.0
                        heading = float(s[10]) if s[10] is not None else 0.0
                        vrate = float(s[11]) if s[11] is not None else 0.0

                        meta = {
                            "callsign": (s[1] or "").strip(),
                            "origin": (s[2] or "").strip(),
                            "on_ground": bool(s[8]) if s[8] is not None else False,
                            "vertical_rate": vrate,
                        }

                        meta = {
                            k: v for k, v in meta.items() if v != "" and v is not None
                        }

                        track_obj = TrackObject(
                            id=icao24,
                            kind="aircraft",
                            source="opensky",
                            ts=ts,
                            lat=float(lat),
                            lon=float(lon),
                            alt_m=geo_alt,
                            heading_deg=heading,
                            speed_mps=vel,
                            meta=meta,
                        )
                        objects.append(track_obj)
                    except Exception:
                        continue

                await upsert_objects_db(objects)
            except Exception as e:
                print(f"[opensky] poll error: {e}")

            await asyncio.sleep(OPENSKY_POLL_SECONDS)


async def main():
    await init_nats()
    if AC_SOURCE == "tar1090":
        await poll_tar1090_loop()
    else:
        await poll_opensky_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(close_nats())
