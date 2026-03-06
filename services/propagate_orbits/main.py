import asyncio
import os
import json
import time
import math
from datetime import datetime, timezone
from sgp4.api import Satrec, jday

from libs.common.db import get_db
from libs.common.stream import publish_event, init_nats, close_nats
from libs.common.models import TrackObject

PROPAGATE_INTERVAL_SECONDS = float(os.getenv("PROPAGATE_INTERVAL_SECONDS", "3.0"))
SAT_STREAM_LIMIT = int(os.getenv("SAT_STREAM_LIMIT", "2000"))

_A = 6378137.0
_E2 = 6.69437999014e-3


def deg2rad(d: float) -> float:
    return d * math.pi / 180.0


def rad2deg(r: float) -> float:
    return r * 180.0 / math.pi


def gmst_from_jd(jd: float) -> float:
    T = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * T * T
        - (T * T * T) / 38710000.0
    ) % 360.0
    return deg2rad(gmst_deg)


def eci_to_ecef(r_eci_km, jd: float):
    theta = gmst_from_jd(jd)
    c, s = math.cos(theta), math.sin(theta)
    x, y, z = r_eci_km
    return ((c * x + s * y) * 1000.0, (-s * x + c * y) * 1000.0, z * 1000.0)


def ecef_to_geodetic(x: float, y: float, z: float):
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


async def propagate_loop():
    print(
        f"[propagate_orbits] Starting SGP4 propagation loop (every {PROPAGATE_INTERVAL_SECONDS}s, limit {SAT_STREAM_LIMIT})"
    )
    while True:
        try:
            t_unix = time.time()
            ts = datetime.now(timezone.utc)
            tm = time.gmtime(t_unix)
            frac = t_unix - int(t_unix)
            jd, fr = jday(
                tm.tm_year,
                tm.tm_mon,
                tm.tm_mday,
                tm.tm_hour,
                tm.tm_min,
                tm.tm_sec + frac,
            )

            objects = []

            async with get_db() as conn:
                # Fetch recent TLEs
                rows = await conn.fetch(
                    "SELECT norad_id, name, tle1, tle2 FROM sat_catalog LIMIT $1",
                    SAT_STREAM_LIMIT,
                )

                for row in rows:
                    try:
                        sat = Satrec.twoline2rv(row["tle1"], row["tle2"])
                        e, r_eci_km, v_eci_km = sat.sgp4(jd, fr)
                        if e != 0:
                            continue

                        ecef = eci_to_ecef(r_eci_km, jd + fr)
                        lat, lon, alt_m = ecef_to_geodetic(*ecef)

                        v_x, v_y, v_z = v_eci_km
                        speed_mps = math.sqrt(v_x**2 + v_y**2 + v_z**2) * 1000.0

                        meta = {
                            "name": row["name"],
                            "ecef": [round(c, 1) for c in ecef],
                            "alt_km": round(
                                (
                                    math.sqrt(
                                        ecef[0] ** 2 + ecef[1] ** 2 + ecef[2] ** 2
                                    )
                                    - _A
                                )
                                / 1000.0,
                                1,
                            ),  # simple sphere altitude for UI parity
                        }

                        track_obj = TrackObject(
                            id=row["norad_id"],
                            kind="satellite",
                            source="celestrak",
                            ts=ts,
                            lat=lat,
                            lon=lon,
                            alt_m=alt_m,
                            speed_mps=speed_mps,
                            meta=meta,
                        )
                        objects.append(track_obj)
                    except Exception as ex:
                        continue

                if objects:
                    # Upsert current positions
                    query = """
                        INSERT INTO objects_current (id, kind, source, ts, lat, lon, alt_m, speed_mps, meta, geom)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, ST_SetSRID(ST_MakePoint($6, $5), 4326))
                        ON CONFLICT (id) DO UPDATE SET
                            ts = EXCLUDED.ts,
                            lat = EXCLUDED.lat,
                            lon = EXCLUDED.lon,
                            alt_m = EXCLUDED.alt_m,
                            speed_mps = EXCLUDED.speed_mps,
                            meta = EXCLUDED.meta,
                            geom = EXCLUDED.geom;
                    """
                    args = [
                        (
                            o.id,
                            o.kind,
                            o.source,
                            o.ts,
                            o.lat,
                            o.lon,
                            o.alt_m,
                            o.speed_mps,
                            json.dumps(o.meta),
                        )
                        for o in objects
                    ]
                    await conn.executemany(query, args)

                    # NATS publish
                    msg = {
                        "type": "delta",
                        "kind": "satellite",
                        "data": [
                            {
                                "id": o.id,
                                "nm": o.meta["name"],
                                "ecef": o.meta["ecef"],
                                "alt_km": o.meta["alt_km"],
                            }
                            for o in objects
                        ],
                    }
                    await publish_event("telemetry.satellite.update", msg)

        except Exception as e:
            print(f"[propagate_orbits] loop error: {e}")

        await asyncio.sleep(PROPAGATE_INTERVAL_SECONDS)


async def main():
    await init_nats()
    await propagate_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(close_nats())
