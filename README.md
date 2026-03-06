<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/CesiumJS-6CADDF?style=for-the-badge&logo=cesium&logoColor=white" />
  <img src="https://img.shields.io/badge/WebSocket-4B0082?style=for-the-badge&logo=websocket&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" />
</p>

<h1 align="center">👁 GODSEYE</h1>
<h3 align="center">Real-Time Global Surveillance Console</h3>

<p align="center">
  <em>Track every aircraft and satellite on Earth — live, local, zero cloud.</em>
</p>

---

## Overview

**Godseye** is a local, self-contained 3D Earth console that renders **live aircraft** (ADS-B via OpenSky Network) and **live satellites** (TLE via CelesTrak + SGP4 propagation) on an interactive CesiumJS globe. It runs entirely on your Mac with no cloud dependencies.

| Feature | Description |
|---|---|
| ✈ **8,000+ live aircraft** | Real-time positions from OpenSky, updated every 10s |
| 🛰 **14,000+ satellites** | CelesTrak TLE catalog propagated via SGP4 |
| ⭐ **Starlink tracking** | Dedicated filter + count for SpaceX constellation |
| 🎯 **Click-to-follow** | Camera locks onto any entity |
| 📡 **Orbit prediction** | 90-minute forward ground track (satellite.js) |
| ⟿ **Aircraft trails** | Breadcrumb polylines behind each flight |
| 🔍 **Search** | Filter by callsign or NORAD ID |
| 🔺 **Alert zones** | Geofence polygons (N. Atlantic, Mid-East, launch sites) |
| ⏱ **Timeline replay** | 5-minute rewind slider |
| ⚠ **Anomaly detection** | Overspeed, altitude spikes, rapid turns, position jumps |
| 📊 **FPS + data age** | Real-time performance and freshness indicators |

---

## Quick Start

```bash
# 1  Clone
git clone https://github.com/dawsonblock/Godseye.git
cd Godseye

# 2  Backend setup
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3  Launch
uvicorn main:app --host 127.0.0.1 --port 8000

# 4  Open console
open http://127.0.0.1:8000
```

That's it. Aircraft and satellite data begin streaming immediately.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Mac (local)                          │
│                                                         │
│  ┌──────────────────────────────────────────┐           │
│  │  FastAPI Backend (Python)                │           │
│  │  ├─ OpenSky poller ─── ADS-B ──────── ✈ │           │
│  │  ├─ CelesTrak TLE ─── SGP4 ────────── 🛰│           │
│  │  ├─ Anomaly detector ── events ──── ⚠  │           │
│  │  └─ WebSocket fanout ── 1 Hz ─────── 📡 │           │
│  └─────────────┬────────────────────────────┘           │
│                │ ws://                                   │
│  ┌─────────────▼────────────────────────────┐           │
│  │  CesiumJS Frontend (Browser)             │           │
│  │  ├─ 3D globe + atmospheric lighting      │           │
│  │  ├─ SVG aircraft billboards (heading)    │           │
│  │  ├─ SVG satellite billboards (orbit)     │           │
│  │  ├─ satellite.js orbit prediction        │           │
│  │  └─ Command console UI                  │           │
│  └──────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────┘
```

---

## Configuration

Create or edit `backend/.env`:

| Variable | Default | Description |
|---|---|---|
| `OPENSKY_USER` | — | OpenSky username (optional, improves rate limits) |
| `OPENSKY_PASS` | — | OpenSky password |
| `OPENSKY_POLL_SECONDS` | `10` | Aircraft poll interval |
| `SAT_TLE_REFRESH_SECONDS` | `21600` | TLE refresh interval (6h) |
| `WS_PUSH_HZ` | `1` | WebSocket push frequency |
| `SAT_STREAM_LIMIT` | `2000` | Max satellites per frame |

### Cesium Ion Token (Optional)

For high-resolution world terrain, add your token in `frontend/index.html`:

```js
Cesium.Ion.defaultAccessToken = "YOUR_TOKEN";
```

Get a free token at [cesium.com/ion](https://cesium.com/ion/signup).

---

## Console Features

### Aircraft Icons

SVG plane silhouettes rotated by heading, color-coded:

- 🔘 **Grey** — on ground
- 🟠 **Orange** — low (&lt; 2 km)
- 🟡 **Yellow** — mid (2–6 km)
- 🟢 **Green** — high (6–10 km)
- 🔵 **Blue** — cruise (&gt; 10 km)

### Satellite Icons

SVG with solar-panel detail, colored by orbit:

- 🔵 **Cyan** — LEO (&lt; 2,000 km)
- 🟣 **Violet** — MEO (2k–35k km)
- 🟡 **Amber** — GEO (≥ 35k km)
- 🩷 **Magenta** — Starlink

### Inspector Panel

Click any entity for detailed telemetry — altitude (ft), speed (kts), heading, vertical rate, origin country, and on-ground status for aircraft; NORAD ID, name, altitude, orbit class, and type for satellites.

---

## Data Sources

| Source | Data | Rate |
|---|---|---|
| [OpenSky Network](https://opensky-network.org) | Aircraft ADS-B state vectors | 10s poll |
| [CelesTrak](https://celestrak.org) | Satellite TLE orbital elements | 6h refresh |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+, FastAPI, Uvicorn, SGP4, httpx |
| Frontend | CesiumJS, satellite.js, vanilla JS |
| Transport | WebSocket (1 Hz push) |
| Rendering | SVG billboards, polyline trails, polygon zones |

---

## License

MIT

---

<p align="center">
  <strong>Godseye</strong> — see everything.
</p>
