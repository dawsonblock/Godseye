import asyncio
import json
import os
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from libs.common.db import get_db, init_db_pool, close_db_pool
from libs.common.stream import init_nats, close_nats, get_nc

app = FastAPI(title="Godseye API v5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.client_prefs: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)
        self.client_prefs[ws] = {"bbox": None, "layers": ["aircraft", "satellite"]}

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)
        if ws in self.client_prefs:
            del self.client_prefs[ws]

    async def broadcast(self, message: str, kind: str):
        if not self.active_connections:
            return

        msg_obj = None
        try:
            msg_obj = json.loads(message)
        except:
            pass

        dead_connections = []
        for ws in self.active_connections:
            prefs = self.client_prefs.get(ws, {})

            if kind not in prefs.get("layers", []):
                continue

            if prefs.get("bbox") and msg_obj and "data" in msg_obj:
                w, s, e, n = prefs["bbox"]
                filtered_data = []
                for item in msg_obj["data"]:
                    if "lat" in item and "lon" in item:
                        lat, lon = item["lat"], item["lon"]
                        if w <= lon <= e and s <= lat <= n:
                            filtered_data.append(item)
                    elif "ecef" in item:
                        filtered_data.append(item)
                    else:
                        filtered_data.append(item)

                if not filtered_data:
                    continue

                custom_msg = msg_obj.copy()
                custom_msg["data"] = filtered_data
                try:
                    await ws.send_text(json.dumps(custom_msg, separators=(",", ":")))
                except Exception:
                    dead_connections.append(ws)
            else:
                try:
                    await ws.send_text(message)
                except Exception:
                    dead_connections.append(ws)

        for ws in dead_connections:
            self.disconnect(ws)


manager = ConnectionManager()


async def nats_listener_loop():
    print("[API] Starting NATS core listener")
    nc = await get_nc()

    try:

        async def message_handler(msg, kind):
            data_str = msg.data.decode()
            await manager.broadcast(data_str, kind)

        async def handle_ac(msg):
            await message_handler(msg, "aircraft")

        async def handle_sat(msg):
            await message_handler(msg, "satellite")

        await nc.subscribe("telemetry.aircraft.update", cb=handle_ac)
        await nc.subscribe("telemetry.satellite.update", cb=handle_sat)

        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        print(f"[API] NATS listener error: {e}")


@app.on_event("startup")
async def startup_event():
    await init_db_pool()
    await init_nats()
    asyncio.create_task(nats_listener_loop())


@app.on_event("shutdown")
async def shutdown_event():
    await close_db_pool()
    await close_nats()


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "layer": "api-gateway"})


@app.get("/api/objects")
async def get_objects(
    kind: str = Query(..., description="aircraft, satellite, ship, debris"),
    bbox: Optional[str] = Query(None, description="w,s,e,n coordinates"),
):
    async with get_db() as conn:
        if bbox:
            try:
                w, s, e, n = [float(x) for x in bbox.split(",")]
                query = """
                    SELECT id, kind, source, lat, lon, alt_m, heading_deg, speed_mps, meta
                    FROM objects_current
                    WHERE kind = $1
                      AND ST_Intersects(geom, ST_MakeEnvelope($2, $3, $4, $5, 4326))
                    LIMIT 10000;
                """
                rows = await conn.fetch(query, kind, w, s, e, n)
            except ValueError:
                return JSONResponse(
                    {"error": "Invalid bbox format. Use w,s,e,n"}, status_code=400
                )
        else:
            query = """
                SELECT id, kind, source, lat, lon, alt_m, heading_deg, speed_mps, meta
                FROM objects_current
                WHERE kind = $1
                LIMIT 5000;
            """
            rows = await conn.fetch(query, kind)

        results = []
        for r in rows:
            obj = dict(r)
            if isinstance(obj["meta"], str):
                obj["meta"] = json.loads(obj["meta"])
            results.append(obj)

        return JSONResponse({"data": results})


@app.get("/api/history/{entity_id}")
async def get_history(
    entity_id: str, limit: int = Query(100, description="Max points to return")
):
    async with get_db() as conn:
        query = """
            SELECT lat, lon, alt_m
            FROM objects_history
            WHERE id = $1
            ORDER BY ts DESC
            LIMIT $2;
        """
        rows = await conn.fetch(query, entity_id, limit)
        results = [dict(r) for r in rows]
        return JSONResponse({"data": results})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps({"type": "hello", "server": "godseye-api-v5"}))

        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)

                if msg.get("type") == "sub_viewport":
                    if "bbox" in msg:
                        manager.client_prefs[ws]["bbox"] = msg["bbox"]
                    if "layers" in msg:
                        manager.client_prefs[ws]["layers"] = msg["layers"]

                elif msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        manager.disconnect(ws)
