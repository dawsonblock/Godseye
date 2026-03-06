import { Viewer, Cartesian3, Color, Math as CesiumMath, BillboardCollection, ScreenSpaceEventHandler, ScreenSpaceEventType, defined, ClockRange, PolylineGlowMaterialProperty } from 'cesium';

let historyPolyline = null;

// Base Setup
const viewer = new Viewer('cesiumContainer', {
    animation: false, baseLayerPicker: false, fullscreenButton: false, vrButton: false,
    geocoder: false, homeButton: false, infoBox: false, sceneModePicker: false,
    selectionIndicator: false, timeline: false, navigationHelpButton: false,
    navigationInstructionsInitiallyVisible: false, scene3DOnly: true,
    requestRenderMode: false // Continuous render for tracking
});
viewer.scene.globe.enableLighting = false; // Starts off
viewer.scene.debugShowFramesPerSecond = false;
// Set a high target to let requestAnimationFrame uncap
viewer.targetFrameRate = 60;

// Remove the default Cesium logo/credits cleanly
viewer.cesiumWidget.creditContainer.style.display = 'none';

const API_HTTP = `http://${window.location.hostname}:8000/api`;
const API_WS = `ws://${window.location.hostname}:8000/ws`;

let layers = {
    "aircraft": true,
    "satellite": true
};

// Icon Data URIs
const svgPlane = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white" width="48px" height="48px"><path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/></svg>`;
const svgMil = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white" width="48px" height="48px"><path d="M12 2L8 8v6l-6 4v2l6-1.5V20l-2 2v1l3-1 3 1v-1l-2-2v-1.5l6 1.5v-2l-6-4V8l-4-6z"/></svg>`;
const svgSat = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white" width="48px" height="48px"><path d="M19.34 4.66C18.42 3.74 17.16 3.2 15.82 3.2s-2.6.54-3.52 1.46l-4.72 4.72c-.08-.34-.23-.66-.46-.89L5.7 7.07c-.43-.43-1.01-.67-1.63-.67S2.87 6.64 2.44 7.07L1 8.51l2.43 2.43-1.06 1.06c-.39.39-.39 1.02 0 1.41l1.41 1.41c.39.39 1.02.39 1.41 0l1.06-1.06L8.68 16.2l-1.42 1.41c-.43.43-.67 1.01-.67 1.63s.24 1.2.67 1.63L8.68 22.3l1.41-1.41c.23-.23.4-.5.5-.78l3.69 3.69c.92.92 2.18 1.46 3.52 1.46s2.6-.54 3.52-1.46l4.28-4.28c.92-.92 1.46-2.18 1.46-3.52s-.54-2.6-1.46-3.52L19.34 4.66zM15 11c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm2 2c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm-4 4c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1z"/></svg>`;

const planeUri = 'data:image/svg+xml;base64,' + btoa(svgPlane);
const milUri = 'data:image/svg+xml;base64,' + btoa(svgMil);
const satUri = 'data:image/svg+xml;base64,' + btoa(svgSat);

// Graphics Collections
const acPoints = new BillboardCollection();
const satPoints = new BillboardCollection();
viewer.scene.primitives.add(acPoints);
viewer.scene.primitives.add(satPoints);

// State Dictionaries mapping ID -> Primitive
const acState = new Map();
const satState = new Map();

// UI Elements
const elAc = document.getElementById('stat-ac');
const elSat = document.getElementById('stat-sat');
const elFps = document.getElementById('stat-fps');

// Inspector Elements
const inspector = document.getElementById('inspector');
const insIcon = document.getElementById('ins-icon');
const insTitle = document.getElementById('ins-title');
const insBadge = document.getElementById('ins-badge');
const insId = document.getElementById('ins-id');
const insAlt = document.getElementById('ins-alt');
const insSpd = document.getElementById('ins-spd');
const insLatLon = document.getElementById('ins-latlon');

let selectedEntityId = null;

function getAircraftBrand(callsign) {
    const cs = (callsign || '').toUpperCase();
    if (cs.startsWith('DAL')) return { color: Color.fromCssColorString('#FF3366'), isMilitary: false };
    if (cs.startsWith('UAL')) return { color: Color.fromCssColorString('#33CCFF'), isMilitary: false };
    if (cs.startsWith('AAL')) return { color: Color.fromCssColorString('#AAB7C4'), isMilitary: false };
    if (cs.startsWith('SWA')) return { color: Color.fromCssColorString('#FFCC00'), isMilitary: false };
    if (cs.startsWith('JBU')) return { color: Color.fromCssColorString('#0066FF'), isMilitary: false };
    if (cs.startsWith('NKS')) return { color: Color.fromCssColorString('#FFE600'), isMilitary: false };
    if (cs.startsWith('RCH') || cs.startsWith('AF') || cs.startsWith('USAF')) return { color: Color.fromCssColorString('#00FF66'), isMilitary: true };
    return { color: Color.WHITE, isMilitary: false };
}

function getSatelliteBrand(name) {
    const n = (name || '').toUpperCase();
    if (n.includes('STARLINK')) return { color: Color.fromCssColorString('#B0B0B0') };
    if (n.includes('ISS') || n.includes('ZARYA')) return { color: Color.fromCssColorString('#FF00FF') };
    if (n.includes('ONEWEB')) return { color: Color.fromCssColorString('#FF6600') };
    if (n.includes('NOAA') || n.includes('GOES')) return { color: Color.fromCssColorString('#00B0FF') };
    return { color: Color.fromCssColorString('#666666') };
}

// Setup FPS counter
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

const scratchCartesian = new Cartesian3();
const INTERP_MS = 2000;

viewer.scene.preRender.addEventListener(() => {
    const now = performance.now();
    for (const p of acState.values()) {
        if (p.lastPosition && p.targetPosition && p.lastUpdateTime) {
            let t = (now - p.lastUpdateTime) / INTERP_MS;
            if (t > 1.0) t = 1.0;
            Cartesian3.lerp(p.lastPosition, p.targetPosition, t, scratchCartesian);
            p.position = scratchCartesian;
        }
    }
    for (const p of satState.values()) {
        if (p.lastPosition && p.targetPosition && p.lastUpdateTime) {
            let t = (now - p.lastUpdateTime) / INTERP_MS;
            if (t > 1.0) t = 1.0;
            Cartesian3.lerp(p.lastPosition, p.targetPosition, t, scratchCartesian);
            p.position = scratchCartesian;
        }
    }
});

function upsertAircraft(id, lat, lon, alt_m, meta) {
    if (!layers.aircraft) return;
    const position = Cartesian3.fromDegrees(lon, lat, alt_m || 0);

    const brand = getAircraftBrand(meta.callsign);
    const useUri = brand.isMilitary ? milUri : planeUri;

    // Package data onto the point for the inspector
    const pointData = { kind: 'aircraft', id, lat, lon, alt_m, meta, brandColor: brand.color.toCssColorString() };

    if (acState.has(id)) {
        const p = acState.get(id);

        p.lastPosition = p.position ? p.position.clone() : position.clone();
        p.targetPosition = position;
        p.lastUpdateTime = performance.now();

        p.id = pointData; // Update metadata
        p.image = useUri; // Sync icon incase it changed
        if (meta.track) {
            p.rotation = CesiumMath.toRadians(meta.track - 90);
        }
        // If this is the selected entity, pulse it
        if (selectedEntityId === id) {
            p.color = Color.YELLOW;
            p.scale = brand.isMilitary ? 0.6 : 0.5;
            updateInspector(pointData);
        } else {
            p.color = brand.color;
            p.scale = brand.isMilitary ? 0.4 : 0.3;
        }
    } else {
        const rotation = meta.track ? CesiumMath.toRadians(meta.track - 90) : 0;
        const p = acPoints.add({
            position: position,
            image: useUri,
            scale: brand.isMilitary ? 0.4 : 0.3,
            rotation: rotation,
            color: brand.color,
            id: pointData
        });
        p.lastPosition = position.clone();
        p.targetPosition = position;
        p.lastUpdateTime = performance.now();
        acState.set(id, p);
    }
}

function upsertSatellite(id, ecef, meta) {
    if (!layers.satellite) return;
    const position = new Cartesian3(ecef[0], ecef[1], ecef[2]);
    const sName = meta.name || id;
    const brand = getSatelliteBrand(sName);

    // We don't have lat/lon immediately here unless we convert back, but meta might have it
    const pointData = { kind: 'satellite', id, ecef, meta, brandColor: brand.color.toCssColorString() };

    if (satState.has(id)) {
        const p = satState.get(id);

        p.lastPosition = p.position ? p.position.clone() : position.clone();
        p.targetPosition = position;
        p.lastUpdateTime = performance.now();

        p.id = pointData;
        if (selectedEntityId === id) {
            p.color = Color.YELLOW;
            p.scale = 0.4;
            // Don't auto-update inspector for sats as often to save CPU if converting coords, but we will here
            updateInspector(pointData);
        } else {
            p.color = brand.color;
            p.scale = 0.2;
        }
    } else {
        const p = satPoints.add({
            position: position,
            image: satUri,
            scale: 0.2,
            color: brand.color,
            id: pointData
        });
        p.lastPosition = position.clone();
        p.targetPosition = position;
        p.lastUpdateTime = performance.now();
        satState.set(id, p);
    }
}

// REST Initial Fetch
async function seedViewport() {
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
        }));
    }
}

// Extracted Inspector Logic
function updateInspector(data) {
    if (!data) return;
    inspector.style.display = 'flex';

    if (data.kind === 'aircraft') {
        insIcon.textContent = 'flight';
        insTitle.textContent = data.meta.flight || data.meta.r || "UNKNOWN AC";
        insBadge.className = "badge ac";
        insBadge.textContent = "AC";
        insId.textContent = data.meta.hex || data.id;

        insAlt.textContent = data.alt_m ? `${Math.round(data.alt_m)} m` : "GND";
        insSpd.textContent = data.meta.gs ? `${Math.round(data.meta.gs)} kt` : "---";
        insLatLon.textContent = `${data.lat.toFixed(4)}, ${data.lon.toFixed(4)}`;

    } else if (data.kind === 'satellite') {
        insIcon.textContent = 'satellite_alt';
        insTitle.textContent = data.meta.name || "UNKNOWN SAT";
        insBadge.className = "badge sat";
        insBadge.textContent = "SAT";
        insId.textContent = data.meta.norad_cat_id || data.id;

        // Approximate geodetic from ECEF without cartographic library overload
        insAlt.textContent = data.meta.alt_km ? `${Math.round(data.meta.alt_km)} km` : "ORBIT";
        insSpd.textContent = data.meta.velocity_km_s ? `${data.meta.velocity_km_s.toFixed(2)} km/s` : "---";
        insLatLon.textContent = (data.meta.lat !== undefined) ? `${data.meta.lat.toFixed(4)}, ${data.meta.lon.toFixed(4)}` : "Tracking...";
    }
}

// Interaction: Picking & Search
function selectEntity(entityId, primitive) {
    if (historyPolyline) {
        viewer.entities.remove(historyPolyline);
        historyPolyline = null;
    }

    // Reset previous selection visuals
    if (selectedEntityId) {
        if (acState.has(selectedEntityId)) {
            const p = acState.get(selectedEntityId);
            if (p.id && p.id.brandColor) {
                p.color = Color.fromCssColorString(p.id.brandColor);
                const isMil = getAircraftBrand(p.id.meta?.callsign).isMilitary;
                p.scale = isMil ? 0.4 : 0.3;
            }
        }
        if (satState.has(selectedEntityId)) {
            const p = satState.get(selectedEntityId);
            if (p.id && p.id.brandColor) {
                p.color = Color.fromCssColorString(p.id.brandColor);
                p.scale = 0.2;
            }
        }
    }

    if (entityId && primitive) {
        const data = primitive.id;
        selectedEntityId = entityId;

        // Highlight new selection
        primitive.color = Color.YELLOW;
        // Use aircraft brand logic to decide selected scale
        let selScale = 0.4;
        if (data.kind === "aircraft") {
            const isMil = getAircraftBrand(data.meta?.callsign).isMilitary;
            selScale = isMil ? 0.6 : 0.5;
        }
        primitive.scale = selScale;

        updateInspector(data);

        fetch(`${API_HTTP}/history/${data.id}?limit=150`)
            .then(res => res.json())
            .then(json => {
                if (json.data && json.data.length > 1 && selectedEntityId === data.id) {
                    const positions = [];
                    for (const pt of json.data) {
                        positions.push(Cartesian3.fromDegrees(pt.lon, pt.lat, pt.alt_m));
                    }
                    historyPolyline = viewer.entities.add({
                        polyline: {
                            positions: positions,
                            width: 5,
                            material: new PolylineGlowMaterialProperty({
                                glowPower: 0.2,
                                color: primitive.color
                            })
                        }
                    });
                }
            })
            .catch(err => console.error("History fetch error:", err));
    } else {
        selectedEntityId = null;
        inspector.style.display = 'none';
    }
}

const handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
handler.setInputAction(function (click) {
    const pickedObject = viewer.scene.pick(click.position);
    if (defined(pickedObject) && defined(pickedObject.id)) {
        selectEntity(pickedObject.id.id, pickedObject.primitive);
    } else {
        selectEntity(null, null);
    }
}, ScreenSpaceEventType.LEFT_CLICK);

const searchInput = document.getElementById('searchInput');
if (searchInput) {
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const q = searchInput.value.trim().toUpperCase();
            if (!q) return;

            let foundPrim = null;

            // Search Aircraft
            for (const p of acState.values()) {
                const meta = p.id.meta || {};
                if (p.id.id === q || (meta.callsign || '').toUpperCase().includes(q) || (meta.registration || '').toUpperCase().includes(q)) {
                    foundPrim = p;
                    break;
                }
            }

            // Search Satellites
            if (!foundPrim) {
                for (const p of satState.values()) {
                    const meta = p.id.meta || {};
                    if (p.id.id === q || (meta.norad_cat_id && meta.norad_cat_id.toString() === q) || (meta.name || '').toUpperCase().includes(q)) {
                        foundPrim = p;
                        break;
                    }
                }
            }

            if (foundPrim) {
                selectEntity(foundPrim.id.id, foundPrim);
                viewer.camera.flyTo({
                    destination: foundPrim.position,
                    duration: 1.5
                });
            } else {
                alert(`No active tracking object found matching: ${q}`);
            }
        }
    });
}

// UI Events
document.getElementById('btn-aircraft').addEventListener('click', (e) => {
    layers.aircraft = !layers.aircraft;
    e.currentTarget.classList.toggle('active');
    if (!layers.aircraft) {
        acPoints.removeAll();
        acState.clear();
        elAc.textContent = "0";
        if (selectedEntityId && selectedEntityId.startsWith("ac")) inspector.style.display = 'none';
    } else { seedViewport(); }
    sendSub();
});

document.getElementById('btn-satellite').addEventListener('click', (e) => {
    layers.satellite = !layers.satellite;
    e.currentTarget.classList.toggle('active');
    if (!layers.satellite) {
        satPoints.removeAll();
        satState.clear();
        elSat.textContent = "0";
        if (selectedEntityId && selectedEntityId !== null && !selectedEntityId.startsWith("ac")) inspector.style.display = 'none';
    } else { seedViewport(); }
    sendSub();
});

document.getElementById('btn-close-ins').addEventListener('click', () => {
    selectedEntityId = null;
    inspector.style.display = 'none';
});

document.getElementById('btn-home').addEventListener('click', () => {
    viewer.camera.flyHome(1.5); // 1.5 second flight
});

let isLightingOn = false;
document.getElementById('btn-lighting').addEventListener('click', (e) => {
    isLightingOn = !isLightingOn;
    viewer.scene.globe.enableLighting = isLightingOn;
    if (isLightingOn) {
        e.currentTarget.classList.add('active');
        e.currentTarget.style.color = '#00f0ff';
    } else {
        e.currentTarget.classList.remove('active');
        e.currentTarget.style.color = ''; // revert to default
    }
});

// Sync clock for realistic lighting if enabled
viewer.clock.multiplier = 1.0;

// Init
seedViewport();
connectWS();

