import os
import json
import nats
from nats.aio.client import Client as NATS

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")

_nc: NATS = None


async def init_nats():
    global _nc
    if _nc is None:
        _nc = await nats.connect(NATS_URL)
    return _nc


async def get_nc() -> NATS:
    nc = await init_nats()
    return nc


async def close_nats():
    global _nc
    if _nc and not _nc.is_closed:
        await _nc.close()
    _nc = None


async def publish_event(subject: str, payload: dict):
    nc = await get_nc()
    data = json.dumps(payload).encode()
    await nc.publish(subject, data)
