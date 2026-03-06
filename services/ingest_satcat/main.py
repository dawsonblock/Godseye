import asyncio
import os
from datetime import datetime, timezone
import httpx

from libs.common.db import get_db

CELESTRAK_TLE_URL = (
    "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
)
SAT_TLE_REFRESH_SECONDS = float(os.getenv("SAT_TLE_REFRESH_SECONDS", "21600"))


async def refresh_tles_loop():
    print(
        f"[ingest_satcat] Starting CelesTrak TLE ingestion (every {SAT_TLE_REFRESH_SECONDS}s)"
    )
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                r = await client.get(CELESTRAK_TLE_URL)
                if r.status_code == 200:
                    lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
                    ts = datetime.now(timezone.utc)

                    records = []
                    for i in range(0, len(lines) - 2, 3):
                        name = lines[i]
                        l1, l2 = lines[i + 1], lines[i + 2]
                        if not (l1.startswith("1 ") and l2.startswith("2 ")):
                            continue

                        norad_id = l1[2:7].strip()
                        records.append((norad_id, name, l1, l2, ts))

                    print(
                        f"[ingest_satcat] Fetched {len(records)} active TLEs from Celestrak"
                    )

                    async with get_db() as conn:
                        query = """
                            INSERT INTO sat_catalog (norad_id, name, tle1, tle2, last_updated)
                            VALUES ($1, $2, $3, $4, $5)
                            ON CONFLICT (norad_id) DO UPDATE SET
                                name = EXCLUDED.name,
                                tle1 = EXCLUDED.tle1,
                                tle2 = EXCLUDED.tle2,
                                last_updated = EXCLUDED.last_updated;
                        """
                        await conn.executemany(query, records)

            except Exception as e:
                print(f"[ingest_satcat] CelesTrak fetch error: {e}")

            await asyncio.sleep(SAT_TLE_REFRESH_SECONDS)


async def main():
    await refresh_tles_loop()


if __name__ == "__main__":
    asyncio.run(main())
