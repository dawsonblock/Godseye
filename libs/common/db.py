import os
import asyncpg
from contextlib import asynccontextmanager

DB_USER = os.getenv("POSTGRES_USER", "godseye")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "godseye")
DB_NAME = os.getenv("POSTGRES_DB", "godseye")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")

DSN = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

_pool = None


async def init_db_pool():
    global _pool
    if _pool is None:
        for attempt in range(10):
            try:
                _pool = await asyncpg.create_pool(DSN, min_size=5, max_size=20)
                break
            except Exception as e:
                print(f"[DB] Waiting for Postgres ({e})...")
                import asyncio

                await asyncio.sleep(2)
        if _pool is None:
            raise Exception("Failed to connect to database after 10 retries")
    return _pool


async def close_db_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_db():
    pool = await init_db_pool()
    async with pool.acquire() as conn:
        yield conn
