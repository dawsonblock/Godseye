import { Viewer, Cartesian3, Color, Math as CesiumMath, PointPrimitiveCollection, BillboardCollection, NearFarScalar, Cartesian2 } from 'cesium';

// Base Setup
const viewer = new Viewer('cesiumContainer', {
    animation: false, baseLayerPicker: false, fullscreenButton: false, vrButton: false,
    geocoder: false, homeButton: false, infoBox: false, sceneModePicker: false,
    selectionIndicator: false, timeline: false, navigationHelpButton: false,
    navigationInstructionsInitiallyVisible: false, scene3DOnly: true,
    requestRenderMode: false // We need continuous render for delta smooth movement in the future
});
viewer.scene.globe.enableLighting = true;
viewer.scene.debugShowFramesPerSecond = false; // Custom FPS counter

const API_HTTP = `http://${window.location.hostname}:8000/api`;
const API_WS = `ws://${window.location.hostname}:8000/ws`;

let layers = {
    "aircraft": true,
    "satellite": true
};

// Graphics Collections
const acPoints = new PointPrimitiveCollection();
const satPoints = new PointPrimitiveCollection();
viewer.scene.primitives.add(acPoints);
viewer.scene.primitives.add(satPoints);

// State Dictionaries mapping ID -> Primitive
const acState = new Map();
const satState = new Map();

// UI Elements
const elAc = document.getElementById('stat-ac');
const elSat = document.getElementById('stat-sat');
const elFps = document.getElementById('stat-fps');

// Setup FPS counter safely hooking into postRender
let frameCount = 0;
let lastFpsTime = Date.now();
viewer.scene.postRender.addEventListener(() => {
    frameCount++;
    const now = Date.now();
    if (now - lastFpsTime >= 1000) {
        elFps.textContent = frameCount;
        frameCount = 0;
        lastFpsTime = now;
    }
});

function upsertAircraft(id, lat, lon, alt_m, meta) {
    if (!layers.aircraft) return;
    const position = Cartesian3.fromDegrees(lon, lat, alt_m || 0);
    if (acState.has(id)) {
        const p = acState.get(id);
        p.position = position;
    } else {
        // For Phase 1 we use points for scale instead of complex billboards
        const p = acPoints.add({
            position: position,
            pixelSize: 4,
            color: Color.fromCssColorString('#00f0ff'),
            id: "ac_" + id
        });
        acState.set(id, p);
    }
}

function upsertSatellite(id, ecef, meta) {
    if (!layers.satellite) return;
    const position = new Cartesian3(ecef[0], ecef[1], ecef[2]);
    if (satState.has(id)) {
        const p = satState.get(id);
        p.position = position;
    } else {
        const p = satPoints.add({
            position: position,
            pixelSize: 3,
            color: Color.fromCssColorString('#ff0055'),
            id: "sat_" + id
        });
        satState.set(id, p);
    }
}

// REST Initial Fetch
async function seedViewport() {
    // We could pass bbox, but we just grab bulk for MVP
    try {
        if (layers.aircraft) {
            const r = await fetch(`${API_HTTP}/objects?kind=aircraft`);
            const j = await r.json();
            if (j.data) {
                j.data.forEach(d => upsertAircraft(d.id, d.lat, d.lon, d.alt_m, d.meta));
            }
        }
        if (layers.satellite) {
            const r2 = await fetch(`${API_HTTP}/objects?kind=satellite`);
            const j2 = await r2.json();
            if (j2.data) {
                j2.data.forEach(d => {
                    // Calculate ECEF from lat/lon/alt returned by REST
                    if (d.meta && d.meta.ecef) {
                        upsertSatellite(d.id, d.meta.ecef, d.meta);
                    } else {
                        const pos = Cartesian3.fromDegrees(d.lon, d.lat, d.alt_m);
                        const arr = [pos.x, pos.y, pos.z];
                        upsertSatellite(d.id, arr, d.meta);
                    }
                });
            }
        }
    } catch (e) {
        console.error("Seed error", e);
    }
}

// WebSocket Delta Updates
let ws;
function connectWS() {
    ws = new WebSocket(API_WS);
    ws.onopen = () => {
        console.log("WS Connected");
        sendSub();
    };
    ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === "delta") {
                if (msg.kind === "aircraft" && msg.data) {
                    msg.data.forEach(d => upsertAircraft(d.id, d.lat, d.lon, d.alt_m, d.meta));
                } else if (msg.kind === "satellite" && msg.data) {
                    msg.data.forEach(d => upsertSatellite(d.id, d.ecef, d.meta));
                }
            }

            elAc.textContent = acState.size;
            elSat.textContent = satState.size;
        } catch (err) { }
    };
    ws.onclose = () => {
        setTimeout(connectWS, 2000);
    };
}

function sendSub() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        const activeLayers = [];
        if (layers.aircraft) activeLayers.push("aircraft");
        if (layers.satellite) activeLayers.push("satellite");
        ws.send(JSON.stringify({
            type: "sub_viewport",
            layers: activeLayers
            // bbox omitted, receiving true global delta streaming
        }));
    }
}

// Toggles
document.getElementById('btn-aircraft').addEventListener('click', (e) => {
    layers.aircraft = !layers.aircraft;
    e.target.classList.toggle('active');
    if (!layers.aircraft) {
        acPoints.removeAll();
        acState.clear();
        elAc.textContent = "0";
    } else { seedViewport(); }
    sendSub();
});

document.getElementById('btn-satellite').addEventListener('click', (e) => {
    layers.satellite = !layers.satellite;
    e.target.classList.toggle('active');
    if (!layers.satellite) {
        satPoints.removeAll();
        satState.clear();
        elSat.textContent = "0";
    } else { seedViewport(); }
    sendSub();
});

// Init
seedViewport();
connectWS();
