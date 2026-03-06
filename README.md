<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/CesiumJS-6CADDF?style=for-the-badge&logo=cesium&logoColor=white" />
  <img src="https://img.shields.io/badge/tar1090-ADS--B-ff6600?style=for-the-badge" />
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

**Godseye** is a local, self-contained 3D Earth console that renders **live aircraft** and **live satellites** on an interactive CesiumJS globe. It runs entirely on your Mac with no cloud dependencies.

### Aircraft Data Sources

| Source | Type | Latency | Data Richness |
|---|---|---|---|
| **tar1090 / readsb** (preferred) | Local ADS-B receiver | ~2s | Registration, type, squawk, RSSI, IAS/TAS |
| **OpenSky Network** (fallback) | Cloud API | ~10s | Callsign, position, altitude, speed |

When `TAR1090_URL` is set, Godseye polls your local receiver directly — no rate limits, richer data, faster updates. Without it, falls back to OpenSky.

| Feature | Description |
|---|---|
| ✈ **Live aircraft** | ADS-B positions via tar1090 or OpenSky |
| 🛰 **14,000+ satellites** | CelesTrak TLE catalog propagated via SGP4 |
| ⭐ **Starlink tracking** | Dedicated filter + count for SpaceX constellation |
| 🎯 **Click-to-follow** | Camera locks onto any entity |
| 📡 **Orbit prediction** | 90-minute forward ground track (satellite.js) |
| ⟿ **Aircraft trails** | Breadcrumb polylines behind each flight |
| 🔍 **Search** | Filter by callsign, registration, or NORAD ID |
| 🔺 **Alert zones** | Geofence polygons (N. Atlantic, Mid-East, launch sites) |
| ⏱ **Timeline replay** | 5-minute rewind slider |
| ⚠ **Anomaly detection** | Overspeed, altitude spikes, rapid turns, position jumps, squawk alerts (7500/7600/7700) |
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

# 3  Configure (optional — edit backend/.env)
#    Set TAR1090_URL to your local receiver, e.g.:
#    TAR1090_URL=http://192.168.1.100/tar1090/data/aircraft.json

# 4  Launch
uvicorn main:app --host 127.0.0.1 --port 8000

# 5  Open console
open http://127.0.0.1:8000
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Mac (local)                          │
│                                                         │
│  ┌──────────────────────────────────────────┐           │
│  │  FastAPI Backend (Python)                │           │
│  │  ├─ tar1090/readsb ─── ADS-B ──────  ✈  │  ← preferred
│  │  ├─ OpenSky Network ── ADS-B ──────  ✈  │  ← fallback
│  │  ├─ CelesTrak TLE ──── SGP4 ───────  🛰 │           │
│  │  ├─ Anomaly detector ── events ───  ⚠  │           │
│  │  └─ WebSocket fanout ── 1 Hz ─────  📡  │           │
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

Edit `backend/.env`:

| Variable | Default | Description |
|---|---|---|
| `TAR1090_URL` | — | **URL to your tar1090/readsb `aircraft.json`** |
| `TAR1090_POLL_SECONDS` | `2` | Local ADS-B poll interval (can be 1–2s) |
| `OPENSKY_USER` | — | OpenSky username (fallback) |
| `OPENSKY_PASS` | — | OpenSky password |
| `OPENSKY_POLL_SECONDS` | `10` | OpenSky poll interval |
| `SAT_TLE_REFRESH_SECONDS` | `21600` | TLE refresh (6h) |
| `WS_PUSH_HZ` | `1` | WebSocket push frequency |
| `SAT_STREAM_LIMIT` | `2000` | Max satellites per frame |

### tar1090 Setup

If you have a local ADS-B receiver (RTL-SDR + dump1090/readsb), set `TAR1090_URL` to its `aircraft.json` endpoint:

```bash
# PiAware / FlightAware
TAR1090_URL=http://piaware.local/tar1090/data/aircraft.json

# dump1090 standalone
TAR1090_URL=http://192.168.1.100:8080/data/aircraft.json

# readsb
TAR1090_URL=http://localhost/tar1090/data/aircraft.json
```

### Enriched Data (tar1090)

When using tar1090, you get richer telemetry per aircraft:

| Field | Example |
|---|---|
| Registration | N12345 |
| Aircraft Type | B738 |
| Squawk | 1200 |
| RSSI (signal) | -12.3 dBFS |
| Category | A3 |
| Emergency squawks | 7500 (hijack), 7600 (radio), 7700 (emergency) |

### Cesium Ion Token (Optional)

For high-resolution terrain:

```js
Cesium.Ion.defaultAccessToken = "YOUR_TOKEN";
```

---

## Console Features

### Aircraft Icons

SVG plane silhouettes rotated by heading, color-coded by altitude:

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

Click any entity for detailed telemetry. Aircraft show altitude (ft), speed (kts), heading, V/rate (fpm), and when using tar1090: registration, aircraft type, squawk (highlighted for emergencies), RSSI signal strength, and category.

---

## Data Sources

| Source | Data | Rate |
|---|---|---|
| [tar1090/readsb](https://github.com/wiedehopf/tar1090) | Local ADS-B receiver | 2s poll |
| [OpenSky Network](https://opensky-network.org) | Aircraft ADS-B (cloud) | 10s poll |
| [CelesTrak](https://celestrak.org) | Satellite TLE elements | 6h refresh |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+, FastAPI, Uvicorn, SGP4, httpx |
| Frontend | CesiumJS, satellite.js, vanilla JS |
| Transport | WebSocket (1 Hz push) |
| ADS-B | tar1090 / readsb / OpenSky |

---

## License

MIT

---

<p align="center">
  <strong>Godseye</strong> — see everything.
</p>
