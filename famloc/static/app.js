/* Family Locator UI - vanilla JS + Leaflet.
   Model: people (device owners) are the primary objects. Each person's position at a time is the
   freshest fix from a carried device (iPhone preferred, then Watch, iPad). Devices sit underneath. */
"use strict";

let icRememberOpen = false, icAppleId = "";  // Setup tab: "Remember password…" form
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("famloc." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("famloc." + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
};
const H = 3600, D = 86400, GAP_BREAK = 6 * H, LANE_GAP = 3 * H;
const isPhone = () => innerWidth < 820;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Served at "/" directly or at "/family/" behind the radio-gateway proxy: resolve API paths against
// wherever this page lives.
const BASE = location.pathname.replace(/[^/]*$/, "");
/* Every request has a time limit (opts.timeout, ms). Without one, a request caught by the PC sleeping
   or the network dropping can hang for good, and the live refresh, which waits for the previous one,
   never runs again: the page sits on hours-old positions until a manual reload. */
async function api(path, opts = {}) {
  const { timeout = 30000, ...rest } = opts;
  const o = { headers: {}, ...rest };
  if (o.method && !["GET", "POST"].includes(o.method)) { o.headers["X-HTTP-Method-Override"] = o.method; o.method = "POST"; }
  path = BASE + path.replace(/^\//, "");
  if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), timeout);
  let r;
  try { r = await fetch(path, { ...o, signal: ctl.signal }); }
  catch (e) { throw e.name === "AbortError" ? new Error("The server didn't answer in time.") : e; }
  finally { clearTimeout(timer); }
  if (r.status === 401 && !path.includes("api/auth/")) { showAuth(); throw new Error("Signed out. Sign in again."); }
  // a user page whose link was replaced or deleted: reload, and the server shows "this link doesn't work"
  if (r.status === 404 && BASE.includes("/v/") && (r.headers.get("content-type") || "").includes("json")) {
    const d = await r.clone().json().catch(() => ({}));
    if (d.error === "dead link") { location.reload(); throw new Error("This link doesn't work any more."); }
  }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || (data && data.error) || r.statusText);
  return data;
}
/* Copy text. navigator.clipboard only exists on https pages (and localhost); on the plain-http home
   address it's missing, so fall back to the old select-and-copy, and failing that show the text in a
   box to copy by hand. Never claims success it didn't have. */
async function copyText(text, okMsg) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); toast(okMsg); return; }
  } catch { /* fall through */ }
  const ta = document.createElement("textarea");
  ta.value = text; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  if (ok) toast(okMsg); else prompt("Copy this:", text);
}
function toast(msg, ms = 3500) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

/* ---------------- formatting ---------------- */
const fmtDT = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const fmtDTY = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtT = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const fmtD = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
const fmtWD = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric" });
const now = () => Date.now() / 1000;
const dt = (ts) => {
  if (!ts) return "never";
  const d = new Date(ts * 1000), n = new Date();
  return (d.getFullYear() === n.getFullYear() ? fmtDT : fmtDTY).format(d);
};
const sameDay = (a, b) => new Date(a * 1000).toDateString() === new Date(b * 1000).toDateString();
const clock = (ts, ref = now()) => (sameDay(ts, ref) ? fmtT.format(new Date(ts * 1000)) : dt(ts));
function ago(ts) {
  if (!ts) return "never";
  const s = now() - ts;
  if (s < 90) return "just now";
  if (s < H) return `${Math.round(s / 60)} min ago`;
  if (s < 2 * D) return `${(s / H).toFixed(s < 10 * H ? 1 : 0)} h ago`;
  return `${Math.round(s / D)} days ago`;
}
const fmtMin = (m) => (m < 1 ? `${Math.round(m * 60)} s` : `${+(+m).toFixed(1)} min`);
function dur(min) {
  if (min < 60) return `${Math.round(min)} min`;
  if (min < 48 * 60) return `${(min / 60).toFixed(1)} h`;
  return `${(min / 1440).toFixed(1)} days`;
}
const toLocalInput = (ts) => { const d = new Date(ts * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
const fromLocalInput = (v) => new Date(v).getTime() / 1000;
const initials = (name) => String(name || "?").split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
const firstName = (name) => String(name || "").split(/\s+/)[0] || name;
const haversine = (a1, o1, a2, o2) => {
  const r = 6371008.8, p1 = a1 * Math.PI / 180, p2 = a2 * Math.PI / 180, dp = p2 - p1, dl = (o2 - o1) * Math.PI / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
};

/* ---------------- icons ---------------- */
const ICONS = {
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
  tablet: '<rect x="4" y="2.5" width="16" height="19" rx="2"/><path d="M11 18.5h2"/>',
  laptop: '<rect x="5" y="5" width="14" height="10" rx="1"/><path d="M3 19h18"/>',
  desktop: '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M9 20h6M12 16v4"/>',
  watch: '<rect x="7" y="7" width="10" height="10" rx="3"/><path d="M9 7l1-4h4l1 4M9 17l1 4h4l1-4"/>',
  buds: '<circle cx="8" cy="8" r="3"/><path d="M8 11v8"/><circle cx="16" cy="8" r="3"/><path d="M16 11v8"/>',
  tag: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  camera: '<rect x="3" y="7" width="18" height="13" rx="2"/><circle cx="12" cy="13.5" r="3.5"/><path d="M8 7l2-3h4l2 3"/>',
  car: '<path d="M4 15.5l1.6-5a2 2 0 0 1 1.9-1.4h9a2 2 0 0 1 1.9 1.4l1.6 5v3h-16z"/><circle cx="8" cy="15.5" r="1.3"/><circle cx="16" cy="15.5" r="1.3"/><path d="M5 18.5v1.5M19 18.5v1.5"/>',
  dot: '<circle cx="12" cy="12" r="4"/>',
};
function kindOf(d) {
  const c = `${d.device_class || ""} ${d.model || ""}`;
  if (d.source === "photos") return "camera";
  if (d.source === "tesla" || /^Car$/.test(d.device_class || "")) return "car";
  if (d.source === "airtag" || /AirTag/.test(c)) return "tag";
  if (/iPhone/.test(c)) return "phone";
  if (/iPad/.test(c)) return "tablet";
  if (/Watch/.test(c)) return "watch";
  if (/iMac|Macmini|Mac mini|MacPro|Mac Pro/.test(c)) return "desktop";
  if (/MacBook|Mac/.test(c)) return "laptop";
  if (/Accessory|AirPods|Beats/.test(c)) return "buds";
  return "dot";
}
const icon = (d) => `<svg class="dev-ico" viewBox="0 0 24 24" aria-hidden="true">${ICONS[kindOf(d)]}</svg>`;
function battIcon(b, charging) {
  const pct = Math.max(0, Math.min(1, b));
  return `<svg viewBox="0 0 20 10" aria-hidden="true"><rect x=".5" y=".5" width="16" height="9" rx="2" fill="none" stroke="currentColor"/>
    <rect x="17.5" y="3" width="2" height="4" rx="1" fill="currentColor"/><rect x="2" y="2" width="${(13 * pct).toFixed(1)}" height="6" rx="1" fill="currentColor" opacity="${charging ? 0.45 : 1}"/>${charging ? '<path d="M9.5 1.5L6 5.5h3l-1.5 3 4-4.2h-3l1.5-2.8z" fill="currentColor" stroke="var(--panel)" stroke-width=".6"/>' : ""}</svg>`;
}
function battHTML(d) {
  if (!d || d.battery == null) return "";
  const b = Math.round(d.battery * 100);
  return `<span class="batt ${b <= 20 ? "low" : ""}" title="${d.battery_status === "Charging" ? "Charging" : "Battery"}">${battIcon(d.battery, d.battery_status === "Charging")}${b}%</span>`;
}

/* ---------------- auth ---------------- */
let authMode = "login";
async function showAuth() {
  const st = await api("/api/auth/state");
  $("#app").classList.add("hidden"); $("#auth").classList.remove("hidden");
  authMode = st.password_set ? "login" : "setup";
  $("#authMsg").textContent = st.password_set ? "Sign in" : "First visit: choose a password for this page (8 or more characters).";
  $("#authPw2").classList.toggle("hidden", st.password_set);
  $("#authBtn").textContent = st.password_set ? "Sign in" : "Set password";
  $("#authPw").autocomplete = st.password_set ? "current-password" : "new-password";
  $("#authPw").focus();
}
$("#authForm").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#authErr").textContent = "";
  const pw = $("#authPw").value;
  try {
    if (authMode === "setup") {
      if (pw !== $("#authPw2").value) throw new Error("The two passwords don't match.");
      await api("/api/auth/setup", { method: "POST", body: { password: pw } });
    } else await api("/api/auth/login", { method: "POST", body: { password: pw } });
    $("#authPw").value = $("#authPw2").value = "";
    $("#auth").classList.add("hidden"); start();
  } catch (err) { $("#authErr").textContent = err.message; }
});
$("#logoutBtn").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.reload(); };

/* ---------------- map ---------------- */
const map = L.map("map", { zoomControl: false, maxZoom: 22, preferCanvas: true, worldCopyJump: true, zoomAnimation: !reduceMotion })
  .setView(store.get("view", [39.5, -98.35]), store.get("zoom", 4));
L.control.zoom({ position: "topright" }).addTo(map);
L.control.scale({ position: "bottomright", imperial: true }).addTo(map);
const canvasR = L.canvas({ padding: 0.3 });
map.createPane("dotsPane").style.zIndex = 420;   // fix dots above the tracks (overlayPane 400)
const dotsR = L.canvas({ padding: 0.3, pane: "dotsPane" });
new ResizeObserver(() => map.invalidateSize({ pan: false })).observe($("#map"));
const osmAttr = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const esriAttr = "Tiles &copy; Esri, Maxar, Earthstar Geographics, USDA, USGS, AeroGRID, IGN, GIS User Community";
const esri = (svc) => `https://server.arcgisonline.com/ArcGIS/rest/services/${svc}/MapServer/tile/{z}/{y}/{x}`;
const MAXZ = 22; // overzoom: past a layer's native level Leaflet scales up its last real tiles
const BASES = {
  "Streets (OSM)": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: MAXZ, maxNativeZoom: 19, attribution: osmAttr }),
  "Dark streets (OSM)": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: MAXZ, maxNativeZoom: 19, attribution: osmAttr, className: "tiles-dark" }),
  "Streets (Esri)": L.tileLayer(esri("World_Street_Map"), { maxZoom: MAXZ, maxNativeZoom: 19, attribution: esriAttr }),
  "Light grey (≤z16)": L.layerGroup([
    L.tileLayer(esri("Canvas/World_Light_Gray_Base"), { maxZoom: MAXZ, maxNativeZoom: 16, attribution: esriAttr }),
    L.tileLayer(esri("Canvas/World_Light_Gray_Reference"), { maxZoom: MAXZ, maxNativeZoom: 16 }),
  ]),
  "Dark grey (≤z16)": L.layerGroup([
    L.tileLayer(esri("Canvas/World_Dark_Gray_Base"), { maxZoom: MAXZ, maxNativeZoom: 16, attribution: esriAttr }),
    L.tileLayer(esri("Canvas/World_Dark_Gray_Reference"), { maxZoom: MAXZ, maxNativeZoom: 16 }),
  ]),
  "Satellite": L.tileLayer(esri("World_Imagery"), { maxZoom: MAXZ, maxNativeZoom: 19, attribution: esriAttr }),
  "Hybrid (satellite + labels)": L.layerGroup([
    L.tileLayer(esri("World_Imagery"), { maxZoom: MAXZ, maxNativeZoom: 19, attribution: esriAttr }),
    L.tileLayer(esri("Reference/World_Boundaries_and_Places"), { maxZoom: MAXZ, maxNativeZoom: 19 }),
    L.tileLayer(esri("Reference/World_Transportation"), { maxZoom: MAXZ, maxNativeZoom: 19, opacity: 0.8 }),
  ]),
  "Topographic (Esri)": L.tileLayer(esri("World_Topo_Map"), { maxZoom: MAXZ, maxNativeZoom: 19, attribution: esriAttr }),
  "Terrain (OpenTopoMap)": L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: MAXZ, maxNativeZoom: 17, attribution: osmAttr + ', SRTM | &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)' }),
  "Cycling (CyclOSM)": L.tileLayer("https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png", { maxZoom: MAXZ, maxNativeZoom: 20, attribution: osmAttr + ' | <a href="https://www.cyclosm.org">CyclOSM</a>' }),
  "Humanitarian": L.tileLayer("https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png", { maxZoom: MAXZ, maxNativeZoom: 19, attribution: osmAttr + " | Humanitarian OSM Team" }),
  "Public transport (ÖPNVKarte)": L.tileLayer("https://tileserver.memomaps.de/tilegen/{z}/{x}/{y}.png", { maxZoom: MAXZ, maxNativeZoom: 18, attribution: 'Map <a href="https://memomaps.de/">memomaps.de</a> CC-BY-SA, ' + osmAttr }),
};
// the page is dark only (rig house style), so the dark base map is the default
let baseName = store.get("base", "Dark streets (OSM)");
if (!BASES[baseName]) baseName = "Dark streets (OSM)";
BASES[baseName].addTo(map);

const L_people = L.layerGroup().addTo(map);
const L_tracks = L.layerGroup().addTo(map);
const L_acc = L.layerGroup().addTo(map);
const L_devices = L.layerGroup();
const L_points = L.layerGroup();
const L_heat = L.heatLayer([], { radius: 24, blur: 18, minOpacity: 0.45, maxZoom: 16, max: 0.6 });
const L_stays = L.layerGroup().addTo(map);
const L_places = L.layerGroup().addTo(map);
const L_photos = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 45 });
const L_temp = L.layerGroup().addTo(map);
const L_rail = L.tileLayer("https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png", { maxZoom: MAXZ, maxNativeZoom: 19, subdomains: "abc",
  attribution: 'Railways &copy; <a href="https://www.openrailwaymap.org/">OpenRailwayMap</a> (CC-BY-SA)' });
/* Speed colouring is a switch, not drawn content: its presence on the map turns it on. */
const L_speed = L.layerGroup();
const OVER = {
  "People and items": L_people, "Tracks": L_tracks, "Tracks coloured by speed": L_speed, "Accuracy circles": L_acc, "Every device separately": L_devices,
  "Every fix as a dot": L_points, "Heatmap": L_heat, "Stays (from Insights)": L_stays, "Places": L_places, "Photo locations": L_photos, "Railways (OpenRailwayMap)": L_rail,
};
// The heatmap draws onto a canvas sized to the map; with a 0-size map (page still hidden, panel
// animating) leaflet-heat's getImageData throws and, at load time, killed the whole app.
// Skip drawing until the map has a real size.
for (const fn of ["_redraw", "_reset"]) {
  const orig = L_heat[fn];
  L_heat[fn] = function (...a) {
    const sz = this._map && this._map.getSize();
    if (!sz || sz.x < 1 || sz.y < 1) return this;
    try { return orig.apply(this, a); } catch (e) { console.warn("heatmap redraw skipped:", e); return this; }
  };
}
// Saved overlays are restored once the map is visible (see restoreOverlays in start()).
const overOn = store.get("overlays2", null);
const overSeen = store.get("overlaysSeen", null);        // overlay names that existed when overlays2 was saved
const OVER_DEFAULT_ON = new Set(["Tracks coloured by speed"]);   // on for everyone until they turn it off
let overlaysRestored = false;
function restoreOverlays() {
  if (overlaysRestored) return;
  overlaysRestored = true;
  map.invalidateSize();
  if (overOn) for (const [n, l] of Object.entries(OVER)) {
    const isNew = !(overSeen || []).includes(n);
    if (overOn.includes(n) || (isNew && OVER_DEFAULT_ON.has(n))) l.addTo(map); else map.removeLayer(l);
  } else { L_photos.addTo(map); for (const n of OVER_DEFAULT_ON) OVER[n].addTo(map); }
}
const layerCtl = L.control.layers(BASES, OVER, { position: "topright", collapsed: true }).addTo(map);
/* Thunderforest Transport needs a (free) API key; offered only when one is set in data/config.toml. */
let tfAdded = false;
function addKeyedLayers(keys) {
  if (tfAdded || !keys || !keys.thunderforest) return;
  tfAdded = true;
  const name = "Transport (Thunderforest)";
  const lyr = L.tileLayer(`https://{s}.tile.thunderforest.com/transport/{z}/{x}/{y}{r}.png?apikey=${encodeURIComponent(keys.thunderforest)}`,
    { maxZoom: MAXZ, maxNativeZoom: 22, subdomains: "abc", attribution: 'Maps &copy; <a href="https://www.thunderforest.com/">Thunderforest</a>, ' + osmAttr });
  BASES[name] = lyr; layerCtl.addBaseLayer(lyr, name);
  if (store.get("base", "") === name) { Object.values(BASES).forEach((b) => map.hasLayer(b) && map.removeLayer(b)); lyr.addTo(map); }
}
let curBase = baseName;
map.on("baselayerchange", (e) => { store.set("base", e.name); curBase = e.name; schedRender(); });
map.on("overlayadd overlayremove", () => { if (!overlaysRestored) return; store.set("overlays2", Object.entries(OVER).filter(([, l]) => map.hasLayer(l)).map(([n]) => n)); store.set("overlaysSeen", Object.keys(OVER)); schedRender(); });
map.on("moveend", () => { const c = map.getCenter(); store.set("view", [c.lat, c.lng]); store.set("zoom", map.getZoom()); });
map.on("zoomend", () => { $("#map").classList.toggle("far", map.getZoom() < 14); schedRender(); });
const fly = (ll, z) => (reduceMotion ? map.setView(ll, z) : map.flyTo(ll, z, { duration: 0.6 }));

/* ---------------- state ---------------- */
const S = {
  devices: [], byId: {}, people: [], personByName: {}, items: [], photoDevs: [], fences: [], data: {}, start: 0, end: 0, t: 0, preset: store.get("preset", "live"),
  follow: true, playing: false, firstFit: true, picking: false, showHidden: false, runs: [], lastRefresh: 0,
  expanded: new Set(store.get("expanded", [])), afterPlaceSave: null,
};
const visible = (d) => S.showHidden || !d.hidden;
/* Map visibility, per viewer (this browser): "p:<owner>" hides a person, a device id hides one device. */
S.mapOff = new Set(store.get("mapOff", []));
const devOn = (d) => visible(d) && !S.mapOff.has(d.id);
const personOn = (p) => !S.mapOff.has("p:" + p.name);
function setMapOff(keys, off) {
  for (const k of keys) off ? S.mapOff.add(k) : S.mapOff.delete(k);
  store.set("mapOff", [...S.mapOff]);
  computeRuns(); drawLanes(); render();
}
function showOnly(p) { setMapOff(S.people.filter((x) => x !== p).map((x) => "p:" + x.name), true); setMapOff(["p:" + p.name, ...p.devices.map((d) => d.id)], false); }
function showEveryone() { S.mapOff.clear(); setMapOff([], false); }
const EYE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 6.1A9.7 9.7 0 0 1 12 6c6.4 0 10 6 10 6a17 17 0 0 1-3.2 3.9M6.6 6.6C3.7 8.4 2 12 2 12s3.6 7 10 7a9.6 9.6 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';
function eyeBtn(on, label) {
  return `<button class="eye${on ? "" : " is-off"}" aria-pressed="${on}" aria-label="${on ? "Hide" : "Show"} ${esc(label)} on the map" title="${on ? "Hide on the map" : "Show on the map"}">${on ? EYE : EYE_OFF}</button>`;
}
const ownerName = (owner) => (S.personByName && S.personByName[owner] ? S.personByName[owner].display : owner);
const CARRY_RANK = { phone: 0, watch: 1, tablet: 2 };

function buildPeople(list) {
  const colors = Object.fromEntries(list.map((p) => [p.name, p.color]));
  S.people = list.map((p) => {
    const devs = S.devices.filter((d) => d.owner === p.name && d.source !== "photos");
    const carried = devs.filter((d) => kindOf(d) in CARRY_RANK);
    return { name: p.name, display: p.display || p.name, color: colors[p.name], tracking: p.tracking || {}, devices: devs, carried: carried.length ? carried : devs.filter((d) => d.last) };
  });
  S.personByName = Object.fromEntries(S.people.map((p) => [p.name, p]));
  S.items = S.devices.filter((d) => !d.owner && d.source !== "photos");
  S.photoDevs = S.devices.filter((d) => d.source === "photos");
}

/* binary search: last point with ts <= t */
function idxAt(arr, t) {
  let lo = 0, hi = arr.length - 1, r = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (arr[m][0] <= t) { r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}
function lastAt(id, t) { const a = S.data[id]; if (!a || !a.length) return null; const i = idxAt(a, t); return i >= 0 ? a[i] : null; }
const lastOf = (d) => (d.last ? [d.last.ts, d.last.lat, d.last.lon, d.last.acc, d.battery] : null);

/* Times each device clearly moved (consecutive own fixes > 250 m apart, beyond their accuracy),
   rebuilt when data loads. Used to tell which device is with the person when they disagree. */
let MOVES = {};
/* Outliers: a fix is left out (soft: only in this page, nothing is deleted) when it is BOTH rough
   (worse than SPIKE_ACC) AND an out-and-back jump: more than SPIKE_MIN_M from the last kept fix and
   from the next good fix, while those two agree with each other, all within 15 minutes. Measured on a
   week of real fixes: catches the 400-2,400 m cell-tower jumps but keeps the 30-65 m Wi-Fi/cell
   fixes that make up ~40% of a drive. The newest fix is never judged (nothing after it yet).
   Left-out fixes are kept in S.spikes for the "Every fix as a dot" layer.
   The Fixes slider moves "rough" for this too, but never below SPIKE_ACC (tighter than that, fixes
   are already off the line): at 200 m only jumps worse than 200 m go, at "All fixes" none do.
   S.raw keeps the loaded fixes so this can be redone when the slider moves. */
const SPIKE_ACC = 50, SPIKE_MIN_M = 300, SPIKE_WINDOW = 15 * 60;   // real jumps measured: all back within 4 min
const spikeAccNow = () => Math.max(SPIKE_ACC, confAcc());
function dropSpikes() {
  S.spikes = {}; S.data = { ...S.raw }; S.spikeAcc = spikeAccNow();
  if (S.spikeAcc === Infinity) return;
  const SPIKE_ACC = S.spikeAcc;
  for (const [id, a] of Object.entries(S.data)) {
    if (a.length < 3) continue;
    const kept = [], out = [];
    for (let i = 0; i < a.length; i++) {
      const p = a[i], prev = kept[kept.length - 1];
      if (prev && (p[3] || 0) > SPIKE_ACC) {
        let next = null;
        for (let j = i + 1; j < a.length && a[j][0] - prev[0] <= SPIKE_WINDOW; j++) if ((a[j][3] || 0) <= SPIKE_ACC) { next = a[j]; break; }
        if (next) {
          const d1 = haversine(prev[1], prev[2], p[1], p[2]), d2 = haversine(p[1], p[2], next[1], next[2]);
          if (Math.min(d1, d2) > SPIKE_MIN_M && haversine(prev[1], prev[2], next[1], next[2]) < 0.3 * Math.min(d1, d2)) { out.push(p); continue; }
        }
      }
      kept.push(p);
    }
    if (out.length) { S.data[id] = kept; S.spikes[id] = out; }
  }
}
function buildMoves() {
  MOVES = {};
  for (const [id, a] of Object.entries(S.data)) {
    const m = [];
    for (let i = 1; i < a.length; i++) {
      const p0 = a[i - 1], p1 = a[i];
      if (p1[0] - p0[0] <= 35 * 60 && haversine(p0[1], p0[2], p1[1], p1[2]) > Math.max(250, (p0[3] || 0) + (p1[3] || 0))) m.push(p1[0]);
    }
    MOVES[id] = m;
  }
}
function lastMoveAt(id, t) { const m = MOVES[id]; if (!m || !m.length) return -Infinity; let lo = 0, hi = m.length - 1, r = -Infinity; while (lo <= hi) { const k = (lo + hi) >> 1; if (m[k] <= t) { r = m[k]; lo = k + 1; } else hi = k - 1; } return r; }

/* Where is a person at time t? Candidates: each carried device's latest fix (within 30 min of the
   freshest). If they agree (within 300 m / their accuracy), the phone wins, then Watch, then iPad.
   If they're in clearly different places, the device that moved most recently is the one with the
   person, e.g. the Watch that left home, not the phone that stayed there. */
function personPos(p, t = S.t) {
  const cands = [];
  for (const d of p.carried) {
    if (!devOn(d)) continue;
    const pt = lastAt(d.id, t) || (S.follow && d.last && d.last.ts <= t + 60 ? lastOf(d) : null);
    if (pt) cands.push({ dev: d, p: pt, rank: CARRY_RANK[kindOf(d)] ?? 3 });
  }
  if (!cands.length) return null;
  const freshest = Math.max(...cands.map((c) => c.p[0]));
  const live = cands.filter((c) => c.p[0] >= freshest - 30 * 60);
  const byRank = (a, b) => a.rank - b.rank || b.p[0] - a.p[0];
  const ref = live.slice().sort(byRank)[0];
  const apart = live.some((c) => haversine(c.p[1], c.p[2], ref.p[1], ref.p[2]) > Math.max(300, (c.p[3] || 0) + (ref.p[3] || 0)));
  if (!apart) return ref;
  return live.sort((a, b) => lastMoveAt(b.dev.id, t) - lastMoveAt(a.dev.id, t) || byRank(a, b))[0];
}
/* The device that represents a person over the loaded range (lanes, Insights): their phone if it has
   any fixes, else Watch, else iPad. Device type wins over fix count, because an iPad left at home
   often has MORE fixes than the phone that went out with the person. */
function primaryOf(p) {
  let best = null;
  for (const d of p.carried) {
    if (!devOn(d)) continue;
    const n = (S.data[d.id] || []).length;
    if (!n) continue;
    const key = [-(CARRY_RANK[kindOf(d)] ?? 3), n];
    if (!best || key[0] > best.key[0] || (key[0] === best.key[0] && key[1] > best.key[1])) best = { d, key };
  }
  return best ? best.d : null;
}
function fenceAt(lat, lon) {
  let hit = null;
  for (const g of S.fences) if (haversine(lat, lon, g.lat, g.lon) <= g.radius && (!hit || g.radius < hit.radius)) hit = g;
  return hit;
}
/* Status line for a person at the cursor. */
/* Average speed while moving: path length over the device's last 3 fixes (more, up to 6, until
   they span 3 min, so 30-s follow fixes aren't all noise). Only from ONE device (mixing devices
   fakes jumps), only if the newest fix is recent, no gap > 35 min, and the net move clears
   250 m / the fixes' accuracy, so GPS jitter at rest never shows as a speed. Fixes worse than
   the Fixes slider (default ROUGH_ACC, 50 m: Apple's cell/Wi-Fi guesses) are skipped for speed and
   the track line; they still place people. */
const ROUGH_ACC = 50;   // same as the server's rough_accuracy_m default
const SPEED_MIN_FIXES = 3, SPEED_MAX_FIXES = 6, SPEED_MIN_SPAN = 3 * 60, SPEED_FRESH = 15 * 60;
function avgSpeed(pos, t = S.t) {
  if (!pos) return null;
  const a = S.data[pos.dev.id] || [];
  const i = idxAt(a, pos.p[0]);
  let pts = i >= 0 ? a.slice(Math.max(0, i - SPEED_MAX_FIXES + 1), i + 1) : [];
  if (!pts.length || pts[pts.length - 1][0] < pos.p[0]) pts.push(pos.p);   // live fix not loaded yet
  const maxAcc = confAcc();
  pts = pts.filter((q) => q[3] == null || q[3] <= maxAcc);         // a 400-1600 m cell guess zig-zags the path
  const last = pts[pts.length - 1];
  if (!last || t - last[0] > SPEED_FRESH) return null;   // all recent fixes rough: no speed
  let k = pts.length - 1;                                  // walk back: 3 fixes, more until 3 min
  while (k > 0 && pts[k][0] - pts[k - 1][0] <= 35 * 60
         && (pts.length - k < SPEED_MIN_FIXES || last[0] - pts[k][0] < SPEED_MIN_SPAN)) k--;
  pts = pts.slice(k);
  if (pts.length < 2) return null;
  const first = pts[0], span = last[0] - first[0];
  if (span < 60) return null;
  if (haversine(first[1], first[2], last[1], last[2]) <= Math.max(250, (first[3] || 0) + (last[3] || 0))) return null;
  let path = 0;
  for (let j = 1; j < pts.length; j++) path += haversine(pts[j - 1][1], pts[j - 1][2], pts[j][1], pts[j][2]);
  return { kmh: path / span * 3.6, fixes: pts.length, span };
}
const MPH = (kmh) => Math.round(kmh / 1.609344);
const fmtSpeed = (v) => `${MPH(v.kmh)} mph`;

function personStatus(p) {
  const pos = personPos(p);
  if (!pos) return { pos: null, text: p.carried.some((d) => visible(d) && S.mapOff.has(d.id)) ? "Devices hidden on the map" : "No location yet", stale: true };
  const [ts, lat, lon] = pos.p;
  const g = fenceAt(lat, lon);
  const run = (S.runs.find((r) => r.person === p.name) || { runs: [] }).runs.find((r) => r.t0 <= ts && ts <= r.t1 + 60);
  const age = S.t - ts;
  const stale = age > 2 * H;
  let text;
  if (g) {
    const since = run && run.state === "f" + g.id ? run.t0 : null;
    text = `At <span class="place-name">${esc(g.name)}</span>${since && since > S.start + 60 ? ` since ${clock(since, S.t)}` : ""}`;
  } else if (run && run.state === "move") text = "On the move";
  else text = "Not at a saved place";
  return { pos, text, stale, place: g, when: S.follow ? ago(ts) : `as of ${clock(ts, S.t)}` };
}

/* ---------------- time range ---------------- */
function presetRange(p, first) {
  const n = now();
  const span = { "6h": 6 * H, "24h": D, "live": D, "7d": 7 * D, "30d": 30 * D, "1y": 365 * D }[p];
  if (span) return [n - span, n];
  return [first || n - 30 * D, n];
}
function markPresets() { $$("#presets button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.p === S.preset))); }
async function setPreset(p) {
  S.preset = p; store.set("preset", p); markPresets();
  let first;
  if (p === "all") first = (await api("/api/range")).first;
  [S.start, S.end] = presetRange(p, first);
  S.t = S.end; S.follow = p === "live";
  await loadPoints();
}
$$("#presets button").forEach((b) => (b.onclick = () => { stopPlay(); setPreset(b.dataset.p); }));
function customRange(a, b) {
  stopPlay(); S.preset = "custom"; markPresets(); S.follow = false;
  S.start = a; S.end = b; S.t = b; loadPoints();
}
$("#tApply").onclick = () => {
  const a = fromLocalInput($("#tFrom").value), b = fromLocalInput($("#tTo").value);
  if (!(a < b)) return toast("Pick a start time before the end time.");
  customRange(a, b);
};
const shift = (dir) => { const span = S.end - S.start; customRange(S.start + dir * span, S.end + dir * span); };
$("#tPrev").onclick = () => shift(-1);
$("#tNext").onclick = () => shift(1);
/* Phone: the timeline folds down to one row (range + play) so the map gets the screen.
   Only the phone CSS reads .folded; desktop always shows everything. */
function setTbFold(f) {
  $("#timebar").classList.toggle("folded", f);
  const b = $("#tbFold"), lab = f ? "Show the timeline" : "Hide the timeline";
  b.setAttribute("aria-expanded", String(!f)); b.title = lab; b.setAttribute("aria-label", lab);
}
setTbFold(store.get("tbFolded", true));
$("#tbFold").onclick = () => {
  const f = !$("#timebar").classList.contains("folded"); setTbFold(f); store.set("tbFolded", f);
  requestAnimationFrame(() => { map.invalidateSize({ pan: false }); drawLanes(); });
};
$("#moreBtn").onclick = () => { const on = $("#timebar").classList.toggle("more"); $("#moreBtn").setAttribute("aria-expanded", String(on)); };

const scrub = $("#scrub");
scrub.addEventListener("input", () => {
  S.t = S.start + (scrub.value / 1000) * (S.end - S.start);
  S.follow = S.preset === "live" && scrub.value >= 999;
  schedRender();
});

/* ---------------- banner: live vs viewing the past ---------------- */
function updateBanner() {
  const b = $("#viewBanner");
  if (S.follow && !S.playing) {
    // stale = the page itself hasn't managed to refresh (asleep, offline, server down): say so
    const stale = S.lastRefresh && now() - S.lastRefresh > 120;
    b.className = "view-banner live" + (stale ? " stale" : "");
    b.innerHTML = stale
      ? `<span class="dot"></span><span>Last updated ${esc(ago(S.lastRefresh))}, reconnecting…</span>`
      : `<span class="dot"></span><span>Live, updated ${S.lastRefresh ? ago(S.lastRefresh) : "now"}</span>`;
  } else {
    b.className = "view-banner";
    b.innerHTML = `<span class="dot" style="background:var(--contour)"></span><span>Viewing ${esc(dt(S.t))}</span><button class="btn small primary" id="backNow">Back to now</button>`;
    $("#backNow").onclick = () => { stopPlay(); setPreset("live"); };
  }
}

/* ---------------- playback ---------------- */
let lastFrame = 0;
function stopPlay() {
  S.playing = false;
  $("#playIco").innerHTML = '<path d="M8 5l11 7-11 7z"/>';
  $("#playBtn").setAttribute("aria-label", "Play"); $("#playBtn").title = "Play";
}
$("#playBtn").onclick = () => {
  if (S.playing) { stopPlay(); render(); return; }
  if (S.t >= S.end - 1) S.t = S.start;
  S.playing = true; S.follow = false;
  $("#playIco").innerHTML = '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';
  $("#playBtn").setAttribute("aria-label", "Pause"); $("#playBtn").title = "Pause";
  lastFrame = performance.now(); requestAnimationFrame(tick);
};
function tick(ts) {
  if (!S.playing) return;
  const dtS = (ts - lastFrame) / 1000; lastFrame = ts;
  S.t = Math.min(S.end, S.t + dtS * speedSec());
  render();
  if (S.t >= S.end) { stopPlay(); render(); return; }
  requestAnimationFrame(tick);
}

/* ---------------- loading ---------------- */
async function loadDevices() {
  const [devs, people] = await Promise.all([api("/api/devices"), api("/api/people")]);
  S.devices = devs; S.byId = Object.fromEntries(devs.map((d) => [d.id, d]));
  buildPeople(people);
  fillDeviceSelect();
}
async function loadFences() { S.fences = await api("/api/geofences"); renderPlaces(); }
async function loadPoints() {
  $("#tFrom").value = toLocalInput(S.start); $("#tTo").value = toLocalInput(S.end);
  const res = await api(`/api/points?start=${S.start}&end=${S.end}`, { timeout: 120000 });   // 1y / All can be big
  S.raw = res.points; dropSpikes();
  buildMoves();
  if (res.truncated) toast(`This range has ${res.total.toLocaleString()} fixes, so the tracks are thinned evenly to about 200,000. Choose a shorter range for every fix.`, 6000);
  computeRuns(); drawLanes(); buildPhotos(); syncSliders(); render(true);
  if (S.firstFit) { S.firstFit = false; requestAnimationFrame(() => { map.invalidateSize(); fitAll(); }); }
  if (!$("#tab-insights").classList.contains("hidden")) loadInsights();
}
function fitAll() {
  const ll = [];
  for (const p of S.people.filter(personOn)) { const pos = personPos(p); if (pos) ll.push([pos.p[1], pos.p[2]]); }
  if (ll.length === 1) map.setView(ll[0], 15); else if (ll.length) map.fitBounds(ll, { padding: [70, 70], maxZoom: 15 });
}

/* ---------------- timeline lanes ---------------- */
function laneH() { return isPhone() ? 20 : 28; }
const LANE_TOP = 20;
/* A person's path as the marker sees it: every carried-device fix that would have been the marker
   position at its own moment (phone by default; Watch/iPad only when clearly fresher). This makes
   the lanes follow whichever device is actually with the person, e.g. a Watch when the phone stayed home. */
function markerSeq(p) {
  const all = [];
  for (const d of p.carried) {
    if (!devOn(d)) continue;
    for (const pt of S.data[d.id] || []) all.push([pt, d]);
  }
  all.sort((x, y) => x[0][0] - y[0][0]);
  const saved = S.follow; S.follow = false;   // judge each moment from stored fixes only
  try {
    return all.filter(([pt, d]) => { const pos = personPos(p, pt[0]); return pos && pos.dev.id === d.id; }).map(([pt]) => pt);
  } finally { S.follow = saved; }
}
function computeRuns() {
  S.runs = S.people.filter(personOn).map((p) => {
    const d = primaryOf(p);
    const a = markerSeq(p);
    const states = a.map((pt, i) => {
      const g = fenceAt(pt[1], pt[2]);
      if (g) return "f" + g.id;
      const nb = [a[i - 1], a[i + 1]].filter(Boolean);
      const moving = nb.some((q) => Math.abs(q[0] - pt[0]) < 45 * 60 && haversine(q[1], q[2], pt[1], pt[2]) > 250);
      return moving ? "move" : "else";
    });
    const runs = [];
    for (let i = 0; i < a.length; i++) {
      const t0 = a[i][0], next = a[i + 1] ? a[i + 1][0] : Math.min(S.end, now());
      const t1 = Math.min(next, t0 + LANE_GAP);
      const last = runs[runs.length - 1];
      if (last && last.state === states[i] && Math.abs(last.t1 - t0) < 1) last.t1 = t1;
      else runs.push({ state: states[i], t0, t1 });
    }
    return { person: p.name, display: p.display, color: p.color, dev: d, runs };
  });
}
function runColor(state, person) {
  if (state === "move") return person.color;
  if (state === "else") return null;
  const g = S.fences.find((f) => "f" + f.id === state);
  return g ? g.color : "#999";
}
function runLabel(state) {
  if (state === "move") return "On the move";
  if (state === "else") return "Not at a saved place";
  const g = S.fences.find((f) => "f" + f.id === state);
  return g ? `At <span class="place-name">${esc(g.name)}</span>` : "At a place";
}
function drawLanes() {
  const c = $("#laneCanvas"), track = $("#laneTrack"), lh = laneH(), n = S.runs.length;
  const w = track.clientWidth, h = LANE_TOP + Math.max(1, n) * lh + 2, dpr = devicePixelRatio || 1;
  c.style.height = h + "px"; c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.body), span = S.end - S.start || 1, X = (t) => ((t - S.start) / span) * w;
  // night shading 20:00-06:00 local, for ranges up to a month
  if (span <= 32 * D) {
    g.fillStyle = css.getPropertyValue("--night");
    const d0 = new Date(S.start * 1000); d0.setHours(0, 0, 0, 0);
    for (let day = d0.getTime() / 1000 - D; day < S.end; day += D) {
      const a = day + 20 * H, b = day + 30 * H;
      if (b > S.start && a < S.end) g.fillRect(X(Math.max(a, S.start)), LANE_TOP, X(Math.min(b, S.end)) - X(Math.max(a, S.start)), h - LANE_TOP);
    }
  }
  // ticks
  const steps = [H, 3 * H, 6 * H, 12 * H, D, 2 * D, 7 * D, 14 * D, 30 * D, 91 * D, 365 * D];
  const step = steps.find((st) => (st / span) * w >= 70) || 365 * D;
  const tz = new Date().getTimezoneOffset() * 60;
  g.font = `13px ${css.getPropertyValue("--ui")}`; g.textBaseline = "top";
  for (let t = Math.ceil((S.start - tz) / step) * step + tz; t < S.end; t += step) {
    const x = X(t); g.fillStyle = css.getPropertyValue("--rule"); g.fillRect(x, LANE_TOP - 2, 1, h - LANE_TOP + 2);
    g.fillStyle = css.getPropertyValue("--muted");
    const d = new Date(t * 1000);
    g.fillText(step < D ? (d.getHours() === 0 ? fmtWD.format(d) : fmtT.format(d)) : step < 7 * D ? fmtWD.format(d) : fmtD.format(d), x + 3, 0);
  }
  // lanes
  const elseCol = css.getPropertyValue("--muted");
  S.runs.forEach((R, i) => {
    const y = LANE_TOP + i * lh + 4, bh = lh - 9, person = S.personByName[R.person];
    g.fillStyle = css.getPropertyValue("--rule-2"); g.fillRect(0, y + bh / 2 - 0.5, w, 1);
    for (const r of R.runs) {
      const x0 = X(Math.max(r.t0, S.start)), x1 = Math.max(x0 + 1.5, X(Math.min(r.t1, S.end)));
      const col = runColor(r.state, person);
      g.globalAlpha = col ? 0.92 : 0.28; g.fillStyle = col || elseCol;
      g.fillRect(x0, y, x1 - x0, bh);
    }
    g.globalAlpha = 1;
  });
  // names + legend
  $("#laneNames").style.paddingTop = LANE_TOP + "px";
  $("#laneNames").innerHTML = S.runs.map((R) => `<div data-p="${esc(R.person)}" style="height:${lh}px" title="${esc(R.display)}"><i style="background:${S.personByName[R.person].color}"></i><span>${esc(firstName(R.display))}</span></div>`).join("") || `<div style="height:${lh}px" class="muted">No one yet</div>`;
  const used = new Set(S.runs.flatMap((R) => R.runs.map((r) => r.state)));
  const leg = S.fences.filter((f) => used.has("f" + f.id)).map((f) => `<span><i style="background:${f.color}"></i><span class="place-name">${esc(f.name)}</span></span>`);
  if (used.has("move")) leg.push('<span><i style="background:linear-gradient(90deg,#c8385a,#1f78b4,#e08e0b)"></i>Moving (person\'s colour)</span>');
  if (used.has("else")) leg.push(`<span><i style="background:${elseCol};opacity:.35"></i>Elsewhere</span>`);
  leg.push("<span>Blank: no data</span>");
  $("#laneLegend").innerHTML = leg.join("");
}
$("#laneNames").addEventListener("click", (e) => {
  const el = e.target.closest("[data-p]"); if (!el) return;
  const p = S.personByName[el.dataset.p], pos = p && personPos(p);
  if (pos) fly([pos.p[1], pos.p[2]], Math.max(map.getZoom(), 15)); else toast(`No location for ${ownerName(el.dataset.p)} at ${clock(S.t)}.`);
});
$("#laneTrack").addEventListener("mousemove", (e) => {
  const tr = $("#laneTrack").getBoundingClientRect(), x = e.clientX - tr.left, y = e.clientY - tr.top;
  const i = Math.floor((y - LANE_TOP) / laneH()), R = S.runs[i], tip = $("#laneTip");
  if (!R || y < LANE_TOP) { tip.classList.add("hidden"); return; }
  const t = S.start + (x / tr.width) * (S.end - S.start);
  const r = R.runs.find((q) => q.t0 <= t && t <= q.t1);
  tip.innerHTML = `<b>${esc(firstName(R.display))}</b> ${r ? `${runLabel(r.state)}, ${clock(r.t0, r.t1)} to ${clock(r.t1, r.t1)}` : `no fixes around ${clock(t)}`}`;
  tip.style.left = Math.max(60, Math.min(tr.width - 60, x)) + "px"; tip.classList.remove("hidden");
});
$("#laneTrack").addEventListener("mouseleave", () => $("#laneTip").classList.add("hidden"));
// Clicking (or releasing a drag) on a lane centres the map on that person at that moment, using the
// device the marker uses then (the one actually with them).
let laneGrab = null;
$("#laneTrack").addEventListener("pointerdown", (e) => {
  const tr = $("#laneTrack").getBoundingClientRect();
  laneGrab = Math.floor((e.clientY - tr.top - LANE_TOP) / laneH());
});
// pointerup, not "change": clicking another lane at the same moment doesn't change the value
$("#laneTrack").addEventListener("pointerup", () => setTimeout(centreOnLane, 0));
function centreOnLane() {
  const R = laneGrab != null ? S.runs[laneGrab] : null;
  laneGrab = null;
  if (!R) return;
  const p = S.personByName[R.person], pos = p && personPos(p, S.t);
  if (!pos) return toast(`No location for ${p ? p.display : R.person} at ${clock(S.t)}.`);
  const ll = [pos.p[1], pos.p[2]];
  if (map.getZoom() < 12) fly(ll, 15); else if (reduceMotion) map.panTo(ll, { animate: false }); else map.panTo(ll);
}
addEventListener("resize", () => drawLanes());

/* ---------------- track speed colours ----------------
   Rainbow, as chosen: purple walking (under 5 mph), blue to 20, yellow to 55, orange to 75,
   red to 90, magenta over (trains, planes). Shades tuned with the dataviz validator so NEIGHBOURING bands stay apart: on light
   tiles (#f2efe9) every check passes (CVD worst 8.9, normal vision >= 15.8); yellow/orange are
   low-contrast on light tiles and purple/red on dark ones, which the casing (dark halo on light
   maps, light halo on dark) plus the legend and hover labels make up for. Same colours on every map. */
const SPEED_BINS = [[5, "under 5"], [20, "5–20"], [55, "20–55"], [75, "55–75"], [90, "75–90"], [Infinity, "90+"]];   // mph
const SPEED_COLORS = ["#7b3fd0", "#1e88e5", "#d4b000", "#f06400", "#b71c1c", "#d81b9c"];
const SPEED_UNSURE = "#8a8a86", SPEED_SEG_GAP = 15 * 60;   // a longer hop is a straight line over a gap, not the path
const darkTiles = () => /dark/i.test(curBase);
function segSpeedBin(p0, p1) {
  const maxAcc = confAcc();   // the Fixes slider: worse than that is a cell-tower guess, no honest speed
  if ((p0[3] || 0) > maxAcc || (p1[3] || 0) > maxAcc) return -1;
  const dt = p1[0] - p0[0]; if (dt <= 0) return -2;                             // same moment: inherit
  if (dt > SPEED_SEG_GAP) return -1;                                             // e.g. 68 km in 2.6 h = "16 mph" incl. a stop
  const mph = haversine(p0[1], p0[2], p1[1], p1[2]) / dt * 2.2369363;
  return SPEED_BINS.findIndex(([max]) => mph < max);
}
let speedLegend = null;
function showSpeedLegend(on) {
  if (!on) { if (speedLegend) { speedLegend.remove(); speedLegend = null; } return; }
  const html = `<span class="sl-h">mph</span>${SPEED_BINS.map(([, lab], i) => `<span class="sl-i"${i ? "" : ' title="Walking"'}><i style="background:${SPEED_COLORS[i]}"></i>${lab}</span>`).join("")}<span class="sl-i sl-unsure" title="More than 15 min between fixes"><i class="unsure"></i>no speed</span>`;
  if (!speedLegend) {
    speedLegend = L.control({ position: "bottomleft" });
    speedLegend.onAdd = () => { const el = L.DomUtil.create("div", "speed-legend"); el.setAttribute("aria-label", "Track colour = speed"); return el; };
    speedLegend.addTo(map);
  }
  speedLegend.getContainer().innerHTML = html;
}

/* ---------------- render ---------------- */
let rafPending = false;
function schedRender() { if (!rafPending) { rafPending = true; requestAnimationFrame(() => { rafPending = false; render(); }); } }

/* Clicking a person or device (marker or list row) centres the map on them and KEEPS it centred:
   every render (live update or playback frame) pans to where they are now. Zoom is left alone.
   Dragging the map or the chip's x releases it; wheel / double-click / pinch zoom do NOT, and while
   locked they zoom around the map centre (= the target) instead of the pointer, so the target
   stays put. S.lock = {kind: "p", name} | {kind: "d", id}. */
function centreOn(e) { unlock(); map.panTo(e.target.getLatLng(), { animate: !reduceMotion, duration: 0.4 }); }
let lockLL = null;
function lockPos(L0 = S.lock) {
  if (!L0) return null;
  if (L0.kind === "p") { const p = S.personByName[L0.name], pos = p && personPos(p, S.t); return pos ? [pos.p[1], pos.p[2]] : null; }
  const d = S.byId[L0.id], pt = d && (lastAt(d.id, S.t) || (S.follow ? lastOf(d) : null));
  return pt ? [pt[1], pt[2]] : null;
}
function zoomAroundCentre(on) {
  const v = on ? "center" : true;              // Leaflet: "center" = zoom about the map centre
  map.options.scrollWheelZoom = v; map.options.doubleClickZoom = v; map.options.touchZoom = v;
}
function setLock(target, ll) {
  S.lock = target; lockLL = ll; zoomAroundCentre(!!target); showLockChip();
}
function lockOn(target) {
  setLock(target, lockPos(target));
  if (lockLL) map.panTo(lockLL, { animate: !reduceMotion, duration: 0.4 });
}
function unlock() { if (!S.lock) return; setLock(null, null); }
function keepLocked() {
  if (!S.lock) return;
  const ll = lockPos(); if (!ll) return;                     // no fix at this moment: stay put, keep the lock
  const same = lockLL && Math.abs(ll[0] - lockLL[0]) < 1e-7 && Math.abs(ll[1] - lockLL[1]) < 1e-7;
  const off = map.latLngToContainerPoint(ll).distanceTo(map.latLngToContainerPoint(map.getCenter()));
  if (same && off < 3) return;                               // there, and still in the middle
  lockLL = ll;
  map.panTo(ll, { animate: !reduceMotion && !S.playing, duration: 0.5 });
}
function showLockChip() {
  let el = $("#lockChip");
  if (!S.lock) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement("div"); el.id = "lockChip"; el.setAttribute("role", "status");
    $("#map").appendChild(el);
    L.DomEvent.disableClickPropagation(el);
  }
  const name = S.lock.kind === "p" ? (S.personByName[S.lock.name] || { display: S.lock.name }).display : (S.byId[S.lock.id] || { name: "device" }).name;
  el.innerHTML = `<span>Centred on <b>${esc(name)}</b></span><button aria-label="Stop keeping ${esc(name)} centred" title="Stop following on the map">×</button>`;
  $("button", el).onclick = unlock;
}
map.on("dragstart", unlock);
function render(full = false) {
  const span = S.end - S.start || 1;
  scrub.value = Math.round(((S.t - S.start) / span) * 1000);
  scrub.setAttribute("aria-valuetext", dt(S.t));
  updateBanner();
  const trail = trailSec(), from = trail ? Math.max(S.start, S.t - trail) : S.start, trackAcc = confAcc();
  L_tracks.clearLayers(); L_people.clearLayers(); L_acc.clearLayers(); L_points.clearLayers(); L_devices.clearLayers();
  const heat = [], wantDots = map.hasLayer(L_points), wantHeat = map.hasLayer(L_heat), wantDevs = map.hasLayer(L_devices);

  /* The line joins only good fixes. A rough one (worse than the Fixes slider, default ROUGH_ACC: Wi-Fi/cell-tower guesses) is
     shown as a small ring on its own instead of being joined in, so a scatter of rough fixes reads
     as points rather than spaghetti. Click either for the fix and its accuracy circle. */
  const drawTrack = (d, color, opts = {}) => {
    const a = S.data[d.id]; if (!a || !a.length) return;
    const i1 = idxAt(a, S.t); if (i1 < 0) return;
    const i0 = idxAt(a, from - 1e-6) + 1;
    const pick = (e) => { L.DomEvent.stop(e); selectNearestFix(d, a, i0, i1, e.latlng); };
    const good = [];
    for (let i = i0; i <= i1; i++) {
      const p = a[i];
      if (wantHeat) heat.push([p[1], p[2], 0.5]);
      if (wantDots) L.circleMarker([p[1], p[2]], { radius: 3, color, weight: 1, fillOpacity: 0.7, renderer: dotsR }).on("click", (e) => { L.DomEvent.stop(e); selectFix(d, p); }).addTo(L_points);
      if ((p[3] || 0) > trackAcc) {
        if (!wantDots) L.circleMarker([p[1], p[2]], { radius: 3, color, weight: 1.5, opacity: 0.8 * (opts.opacity || 1), fill: false, renderer: dotsR })
          .bindTooltip(`Rough fix, within ${Math.round(p[3])} m`).on("click", (e) => { L.DomEvent.stop(e); selectFix(d, p); }).addTo(L_tracks);
      } else good.push(p);
    }
    if (wantDots) for (const p of S.spikes?.[d.id] || []) if (p[0] >= from && p[0] <= S.t)
      L.circleMarker([p[1], p[2]], { radius: 5, color: "#b0b0aa", weight: 1.5, fill: false, dashArray: "2 2", renderer: dotsR }).on("click", (e) => { L.DomEvent.stop(e); selectFix(d, p, true); }).addTo(L_points);
    if (speedOn && !opts.dash) return drawSpeedRuns(good, pick);
    let seg = [];
    const flush = () => { if (seg.length > 1) L.polyline(seg, { color, weight: opts.weight || 3.5, opacity: opts.opacity || 0.85, dashArray: opts.dash, renderer: canvasR, lineJoin: "round" }).on("click", pick).addTo(L_tracks); seg = []; };
    for (let i = 0; i < good.length; i++) {
      if (i && good[i][0] - good[i - 1][0] > GAP_BREAK) flush();
      seg.push([good[i][1], good[i][2]]);
    }
    flush();
  };
  const speedOn = map.hasLayer(L_speed) && map.hasLayer(L_tracks);
  showSpeedLegend(speedOn);
  // speed mode: a surface-coloured casing, then runs of same-speed-band segments on top
  const drawSpeedRuns = (g, pick) => {
    const casing = darkTiles() ? "rgba(255,255,255,.55)" : "rgba(25,25,24,.55)";
    const runs = []; let cur = null, chunk = [];
    const casings = [];
    for (let i = 1; i < g.length; i++) {
      const p0 = g[i - 1], p1 = g[i];
      if (p1[0] - p0[0] > GAP_BREAK) { if (chunk.length > 1) casings.push(chunk); chunk = []; cur = null; continue; }
      if (!chunk.length) chunk.push([p0[1], p0[2]]);
      chunk.push([p1[1], p1[2]]);
      let b = segSpeedBin(p0, p1); if (b === -2) b = cur ? cur.b : 0;
      if (cur && cur.b === b) cur.pts.push([p1[1], p1[2]]);
      else { cur = { b, pts: [[p0[1], p0[2]], [p1[1], p1[2]]] }; runs.push(cur); }
    }
    if (chunk.length > 1) casings.push(chunk);
    for (const c of casings) L.polyline(c, { color: casing, weight: 6, opacity: 1, renderer: canvasR, lineJoin: "round", interactive: false }).addTo(L_tracks);
    for (const r of runs) {
      const unsure = r.b < 0;
      L.polyline(r.pts, { color: unsure ? SPEED_UNSURE : SPEED_COLORS[r.b], weight: 3.5, opacity: 1, dashArray: unsure ? "4 5" : null, renderer: canvasR, lineJoin: "round", lineCap: "round" })
        .bindTooltip(unsure ? "No speed: more than 15 min between fixes" : r.b === 0 ? "Walking (under 5 mph)" : `${SPEED_BINS[r.b][1]} mph`, { sticky: true }).on("click", pick).addTo(L_tracks);
    }
  };

  // people: one marker each, merged when together
  const placed = [];
  for (const p of S.people.filter(personOn)) {
    const st = personStatus(p);
    // Track the device the marker is placed by, so marker and line always join. Draw the main
    // device too (faintly) when it's a different one, e.g. an iPad that stayed at home.
    const prim = primaryOf(p), markDev = st.pos ? st.pos.dev : prim;
    if (markDev) drawTrack(markDev, p.color);
    if (prim && markDev && prim.id !== markDev.id) drawTrack(prim, p.color, { weight: 2, opacity: 0.35, dash: "2 6" });
    if (!st.pos) continue;
    const [ts, lat, lon, acc] = st.pos.p;
    if (acc && acc > 15) L.circle([lat, lon], { radius: acc, color: p.color, weight: 1, opacity: 0.5, fillOpacity: 0.07, interactive: false }).addTo(L_acc);
    placed.push({ p, st, lat, lon, ts });
  }
  const groups = [];
  for (const it of placed) {
    // merge people at the same saved place, or whose markers would overlap on screen at this zoom
    const px = map.latLngToContainerPoint([it.lat, it.lon]);
    const g = groups.find((G) => (it.st.place && G.place && it.st.place.id === G.place.id) || G.px.distanceTo(px) < 36);
    if (g) { g.members.push(it); if (!g.place && it.st.place) g.place = null; }
    else groups.push({ place: it.st.place, lat: it.lat, lon: it.lon, px, members: [it] });
  }
  for (const G of groups) {
    if (G.members.length === 1) {
      const { p, st, lat, lon } = G.members[0];
      const sp = avgSpeed(st.pos);
      const ic = L.divIcon({ className: "pmark" + (st.stale ? " stale" : ""), iconSize: [34, 34], iconAnchor: [17, 17],
        html: `<div class="av" style="background:${p.color}">${esc(initials(p.display))}</div><div class="lbl">${esc(firstName(p.display))}${sp ? `<span class="spd">${fmtSpeed(sp)}</span>` : ""}</div>` });
      L.marker([lat, lon], { icon: ic, zIndexOffset: 1000, title: p.display, keyboard: true }).bindPopup(() => personPopup(p), { autoPan: false }).on("click", () => lockOn({ kind: "p", name: p.name })).addTo(L_people);
    } else {
      if (G.place && G.members.some((m) => !m.st.place || m.st.place.id !== G.place.id)) G.place = null;
      const avs = G.members.map(({ p }) => `<div class="av" style="background:${p.color}" title="${esc(p.display)}">${esc(initials(p.display))}</div>`).join("");
      const ic = L.divIcon({ className: "", iconSize: null, html: `<div class="gmark">${avs}${G.place ? `<span class="place-name">${esc(G.place.name)}</span>` : ""}</div>` });
      const names = G.members.map(({ p }) => firstName(p.display)).join(", ");
      L.marker([G.lat, G.lon], { icon: ic, zIndexOffset: 1000, title: names }).bindPopup(() => groupPopup(G), { autoPan: false }).on("click", centreOn).addTo(L_people);
    }
  }
  // items (AirTags etc.)
  for (const d of S.items) {
    if (!devOn(d)) continue;
    const pt = lastAt(d.id, S.t) || (S.follow ? lastOf(d) : null); if (!pt) continue;
    drawTrack(d, "#7a7f86", { weight: 2, dash: "4 4" });
    L.marker([pt[1], pt[2]], { icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: `<div class="dmark" style="background:#5e6b78;width:22px;height:22px;display:grid;place-items:center"><svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:none;stroke:#fff;stroke-width:2">${ICONS.tag}</svg></div>` }), title: d.name })
      .bindPopup(() => fixPopup(d, pt, true), { autoPan: false }).on("click", () => lockOn({ kind: "d", id: d.id })).addTo(L_people);
  }
  // every device separately
  if (wantDevs) for (const p of S.people.filter(personOn)) for (const d of p.devices) {
    if (!devOn(d)) continue;
    const pt = lastAt(d.id, S.t) || (S.follow ? lastOf(d) : null); if (!pt) continue;
    L.marker([pt[1], pt[2]], { icon: L.divIcon({ className: "", iconSize: [14, 14], iconAnchor: [7, 7], html: `<div class="dmark" style="background:${p.color}"></div>` }), title: d.name })
      .bindTooltip(d.name, { direction: "top", offset: [0, -6] }).bindPopup(() => fixPopup(d, pt, true), { autoPan: false }).on("click", () => lockOn({ kind: "d", id: d.id })).addTo(L_devices);
  }
  if (wantHeat) L_heat.setLatLngs(heat);
  renderPeople();
  keepLocked();
}

/* ---------------- popups ---------------- */
function popupEl(html, handlers) {
  const el = document.createElement("div"); el.className = "pop"; el.innerHTML = html;
  el.addEventListener("click", (e) => { const a = e.target.dataset.a; if (a && handlers[a]) handlers[a](); });
  return el;
}
function personPopup(p) {
  const st = personStatus(p), [ts, lat, lon, acc] = st.pos.p, d = st.pos.dev;
  const canLocate = !S.viewer && d.source === "icloud", sp = avgSpeed(st.pos);
  return popupEl(`<h3>${esc(p.display)}</h3><p>${st.text}</p>
    <table><tr><td>Fix</td><td>${esc(dt(ts))} (${esc(ago(ts))})</td></tr>
    <tr><td>From</td><td>${esc(d.name)}</td></tr>
    ${sp ? `<tr><td>Speed</td><td>${fmtSpeed(sp)} <span class="muted">avg of ${sp.fixes} fixes over ${dur(sp.span / 60)}</span></td></tr>` : ""}
    <tr><td>Accuracy</td><td>${acc != null ? `within ${Math.round(acc)} m` : "unknown"}</td></tr>
    ${d.battery != null ? `<tr><td>Battery</td><td>${Math.round(d.battery * 100)}%</td></tr>` : ""}</table>
    <div class="acts">${canLocate ? '<a data-a="locate">Locate now</a>' : ""}<a data-a="ins">Insights</a><a data-a="here">What's here?</a>
    <a data-a="place">Make this a place</a><a href="https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}" target="_blank" rel="noopener">Directions</a></div>`, {
    locate: () => locateOne(d), ins: () => openInsights(d.id), here: () => whatsHere(lat, lon), place: () => newPlace(lat, lon),
  });
}
function groupPopup(G) {
  return popupEl(`<h3>${G.place ? `<span class="place-name">${esc(G.place.name)}</span>` : "Together"}</h3>
    <table>${G.members.map(({ p, ts }) => `<tr><td>${esc(p.display)}</td><td>${esc(ago(ts))}</td></tr>`).join("")}</table>
    <div class="acts">${G.place ? "" : '<a data-a="place">Make this a place</a>'}<a data-a="zoom">Zoom in</a></div>`, {
    place: () => newPlace(G.lat, G.lon), zoom: () => fly([G.lat, G.lon], 19),
  });
}
function fixPopup(d, p, current = false) {
  const [ts, lat, lon, acc, batt, kind, meta] = p;
  return popupEl(`<h3>${esc(d.name)}</h3>${d.owner ? `<p class="muted">${esc(ownerName(d.owner))}</p>` : ""}
    <table><tr><td>When</td><td>${esc(dt(ts))} (${esc(ago(ts))})</td></tr>
    <tr><td>Accuracy</td><td>${acc != null ? `within ${Math.round(acc)} m` : "unknown"}</td></tr>
    ${batt != null ? `<tr><td>Battery</td><td>${Math.round(batt * 100)}%</td></tr>` : ""}
    ${kind ? `<tr><td>Source</td><td>${esc(kind)}</td></tr>` : ""}
    ${meta && meta.file ? `<tr><td>Photo</td><td>${esc(String(meta.file).split("/").pop())}</td></tr>` : ""}
    <tr><td>Position</td><td>${lat.toFixed(5)}, ${lon.toFixed(5)}</td></tr></table>
    <div class="acts">${current && !S.viewer && d.source === "icloud" ? '<a data-a="locate">Locate now</a>' : ""}<a data-a="here">What's here?</a>
    <a data-a="place">Make this a place</a></div>`, {
    locate: () => locateOne(d), here: () => whatsHere(lat, lon), place: () => newPlace(lat, lon),
  });
}
/* Clicking a track or a fix dot selects one saved fix: its details pop up and its accuracy circle is
   drawn, so a fix that's far off the route but has a huge circle reads as an outlier, not a detour.
   L_sel is never cleared by render(), so the selection survives live refreshes; closing the popup
   clears it. */
map.createPane("selPane").style.zIndex = 450;   // above tracks (overlayPane 400), below markers (600)
const selR = L.svg({ pane: "selPane" });
const L_sel = L.layerGroup().addTo(map);
function selectFix(d, p, outlier = false) {
  const [, lat, lon, acc] = p, ll = [lat, lon];
  L_sel.clearLayers();
  if (acc) L.circle(ll, { radius: acc, color: "#e8a33d", weight: 2, dashArray: "5 4", fillColor: "#e8a33d", fillOpacity: 0.15, interactive: false, pane: "selPane", renderer: selR }).addTo(L_sel);
  L.circleMarker(ll, { radius: 5, color: "#e8a33d", weight: 2.5, fillColor: "#fff", fillOpacity: 1, interactive: false, pane: "selPane", renderer: selR }).addTo(L_sel);
  const el = fixPopup(d, p);
  if (outlier) el.insertAdjacentHTML("afterbegin", '<p class="muted small" style="margin:0 0 6px">Left out of the track and position: a rough fix far from the fixes either side of it.</p>');
  const pop = L.popup({ autoPan: false, offset: [0, -4] }).setLatLng(ll).setContent(el);
  pop.on("remove", () => { if (L_sel._pop === pop) { L_sel.clearLayers(); L_sel._pop = null; } });
  L_sel._pop = pop; pop.openOn(map);
}
function selectNearestFix(d, a, i0, i1, latlng) {
  // nearest saved fix on screen to where the track was clicked
  const c = map.latLngToContainerPoint(latlng);
  let best = -1, bd = Infinity;
  for (let i = i0; i <= i1; i++) { const q = map.latLngToContainerPoint([a[i][1], a[i][2]]), dd = q.distanceTo(c); if (dd < bd) { bd = dd; best = i; } }
  if (best >= 0) selectFix(d, a[best]);
}
function buildPhotos() {
  L_photos.clearLayers();
  for (const d of S.photoDevs) {
    if (!visible(d) || !S.data[d.id]) continue;
    L_photos.addLayers(S.data[d.id].map((p) => L.marker([p[1], p[2]], { icon: L.divIcon({ className: "photomark", iconSize: [10, 10] }) }).bindPopup(() => fixPopup(d, p))));
  }
}

/* ---------------- people list ---------------- */
function devSub(d) {
  const p = lastAt(d.id, S.t) || lastOf(d);
  const bits = [p ? ago(p[0]) : "no location"];
  if (p && p[3]) bits.push(`within ${Math.round(p[3])} m`);
  if (d.battery != null) bits.push(`${Math.round(d.battery * 100)}%${d.battery_status === "Charging" ? " charging" : ""}`);
  return bits.join(", ");
}
function devRow(d) {
  const row = document.createElement("div");
  const on = !S.mapOff.has(d.id);
  row.className = "dev" + (d.hidden ? " off" : "") + (on ? "" : " mapoff");
  row.innerHTML = `${icon(d)}<span class="dev-name" title="${esc(d.model || "")}">${esc(d.name)}</span>
    ${eyeBtn(on, d.name)}<button class="dev-more" aria-label="Actions for ${esc(d.name)}" aria-haspopup="menu" aria-expanded="false">⋯</button>
    <span class="dev-sub">${esc(devSub(d))}</span>`;
  row.addEventListener("click", (e) => {
    if (e.target.closest(".dev-more")) return devMenu(d, e.target.closest(".dev-more"));
    if (e.target.closest(".eye")) return setMapOff([d.id], on);
    const p = lastAt(d.id, S.t) || lastOf(d);
    if (p) { fly([p[1], p[2]], Math.max(map.getZoom(), 16)); showMarkerFor(d, p); setLock({ kind: "d", id: d.id }, [p[1], p[2]]); }
    else toast(`Apple has no location for ${d.name}. It's offline or has location turned off.`);
  });
  return row;
}
function followBadge(p) {
  const t = p.tracking || {};
  if (!t.following || !S.follow) return "";
  const why = t.following === "manual" ? "Following" : "Moving, following";
  return `<span class="follow-badge" title="${t.following_until ? "Until " + esc(clock(t.following_until)) : ""}">${why}, every ${fmtMin(t.effective_minutes)}</span>`;
}
function renderPeople() {
  const el = $("#peopleList"); el.innerHTML = "";
  const nOn = S.people.filter(personOn).length, devOff = [...S.mapOff].some((k) => !k.startsWith("p:"));
  if (S.people.length && (nOn < S.people.length || devOff)) {
    const bar = document.createElement("div"); bar.className = "mapfilter";
    bar.innerHTML = `<span>${nOn < S.people.length ? `Showing ${nOn} of ${S.people.length} people on the map` : "Some devices are hidden on the map"}</span><button class="btn small">Show all</button>`;
    $("button", bar).onclick = showEveryone; el.appendChild(bar);
  }
  if (!S.devices.length && S.viewer) {
    el.innerHTML = '<p class="muted">Nobody has been shared on this page yet.</p>';
    return;
  }
  if (!S.devices.length) {
    el.innerHTML = '<p class="muted">No one to show yet. Sign in to iCloud on the <a href="#setup" onclick="openTab(\'setup\')">Setup</a> tab and your family will appear here.</p>';
    return;
  }
  for (const p of S.people) {
    const st = personStatus(p), dev = st.pos ? st.pos.dev : p.carried[0], sp = avgSpeed(st.pos);
    const pOn = personOn(p);
    const box = document.createElement("div"); box.className = "person" + (pOn ? "" : " mapoff");
    const devs = p.devices.filter(visible);
    const withFix = devs.filter((d) => d.last).sort((a, b) => b.last.ts - a.last.ts), noFix = devs.filter((d) => !d.last);
    const open = S.expanded.has(p.name);
    box.innerHTML = `<div class="person-main" tabindex="0" role="button" aria-label="Show ${esc(p.display)} on the map">
        <div class="avatar" style="background:${p.color}">${esc(initials(p.display))}</div>
        <div class="person-name">${esc(p.display)}${p.display !== p.name ? ` <span class="muted small" title="Name in Apple's family data">${esc(p.name)}</span>` : ""}</div>
        <div class="person-side"><span class="side-btns">${eyeBtn(pOn, p.display)}<button class="dev-more person-more" aria-label="Actions for ${esc(p.display)}" aria-haspopup="menu" aria-expanded="false">⋯</button></span>${st.pos ? `<span>${esc(st.when)}</span>` : ""}${battHTML(dev)}</div>
        <div class="person-status ${st.stale ? "stale" : ""}">${st.text}${sp ? ` <span class="speed" title="Average of the last ${sp.fixes} fixes over ${dur(sp.span / 60)}">· ${fmtSpeed(sp)}</span>` : ""}${followBadge(p)}</div>
      </div>
      <div class="devs"><button class="devs-toggle" aria-expanded="${open}">${open ? "Hide" : "Show"} ${devs.length} device${devs.length === 1 ? "" : "s"}</button><div class="dev-list"></div></div>`;
    const list = $(".dev-list", box);
    if (open) {
      withFix.forEach((d) => list.appendChild(devRow(d)));
      if (noFix.length) {
        const det = document.createElement("details"); det.className = "nofix";
        det.innerHTML = `<summary class="devs-toggle">${noFix.length} without a location</summary>`;
        noFix.forEach((d) => det.appendChild(devRow(d))); list.appendChild(det);
      }
    }
    const go = () => { if (st.pos) { fly([st.pos.p[1], st.pos.p[2]], Math.max(map.getZoom(), 15)); setLock({ kind: "p", name: p.name }, [st.pos.p[1], st.pos.p[2]]); } else toast(`No location for ${p.display} yet.`); if (isPhone()) $("#app").classList.add("noside"); };
    $(".person-main", box).onclick = (e) => { if (e.target.closest(".eye")) return setMapOff(["p:" + p.name], pOn); if (e.target.closest(".person-more")) return personMenu(p, e.target.closest(".person-more")); go(); };
    $(".person-main", box).onkeydown = (e) => { if (e.target.closest("button")) return; if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    if (!S.viewer) $(".avatar", box).title = "Change colour";
    if (!S.viewer) $(".avatar", box).onclick = (e) => { e.stopPropagation(); pickColor(p.color, async (c) => { await api(`/api/people/${encodeURIComponent(p.name)}`, { method: "PATCH", body: { color: c } }); await refreshAll(); }); };
    $(".devs-toggle", box).onclick = () => { open ? S.expanded.delete(p.name) : S.expanded.add(p.name); store.set("expanded", [...S.expanded]); renderPeople(); };
    el.appendChild(box);
  }
  const section = (title, devs) => {
    const vis = devs.filter(visible); if (!vis.length) return;
    el.insertAdjacentHTML("beforeend", `<div class="group-h">${esc(title)}</div>`);
    const box = document.createElement("div"); box.className = "devs"; box.style.marginLeft = "0";
    vis.forEach((d) => box.appendChild(devRow(d))); el.appendChild(box);
  };
  section("Items", S.items);
  section("Photo history", S.photoDevs);
}
$("#showHidden").onchange = (e) => { S.showHidden = e.target.checked; computeRuns(); drawLanes(); render(); };
function pickColor(cur, cb) { const i = document.createElement("input"); i.type = "color"; i.value = cur; i.onchange = () => cb(i.value); i.click(); }

/* device actions menu */
function openMenu(x, y, items, caption) {
  const m = $("#ctxMenu");
  m.innerHTML = (caption ? `<div class="cap">${esc(caption)}</div>` : "") + items.map((it, i) => `<button role="menuitem" data-i="${i}">${esc(it[0])}</button>`).join("");
  m.classList.remove("hidden");
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + "px"; m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
  m.onclick = (e) => { const b = e.target.closest("[data-i]"); if (!b) return; closeMenu(); items[+b.dataset.i][1](); };
  $("button", m)?.focus();
}
function closeMenu() {
  $("#ctxMenu").classList.add("hidden");
  $$(".dev-more[aria-expanded=true]").forEach((b) => b.setAttribute("aria-expanded", "false"));
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
document.addEventListener("click", (e) => { if (!e.target.closest("#ctxMenu") && !e.target.closest(".dev-more")) closeMenu(); });
function devMenu(d, btn) {
  const r = btn.getBoundingClientRect(); btn.setAttribute("aria-expanded", "true");
  const items = [];
  if (!S.viewer && d.source === "icloud" && (d.meta || {}).locationCapable !== false) items.push(["Locate now", () => locateOne(d)]);
  if (d.last) items.push([`Show last known location (${ago(d.last.ts)})`, () => showLastKnown(d)]);
  items.push(["Insights and battery history", () => openInsights(d.id)]);
  if (!S.viewer) items.push(["Rename", async () => { const n = prompt("Name for this device", d.name); if (n) { await api(`/api/devices/${encodeURIComponent(d.id)}`, { method: "PATCH", body: { name: n } }); await refreshAll(); } }]);
  if (!S.viewer) items.push([d.hidden ? "Show this device" : "Hide this device", async () => { await api(`/api/devices/${encodeURIComponent(d.id)}`, { method: "PATCH", body: { hidden: d.hidden ? 0 : 1 } }); await refreshAll(); }]);
  openMenu(r.left, r.bottom + 4, items, d.model || d.name);
}
function personMenu(p, btn) {
  const r = btn.getBoundingClientRect(); btn.setAttribute("aria-expanded", "true");
  const showItem = S.people.filter(personOn).length === 1 && personOn(p)
    ? ["Show everyone on the map", showEveryone] : [`Show only ${firstName(p.display)} on the map`, () => showOnly(p)];
  const insItem = ["Insights", () => { const d = primaryOf(p) || p.carried[0]; if (d) openInsights(d.id); }];
  if (S.viewer) return openMenu(r.left - 150, r.bottom + 4, [showItem, insItem], p.display);
  openMenu(r.left - 150, r.bottom + 4, [
    ["Rename", async () => {
      const n = prompt(`Name to show for ${p.display}. Leave blank to use Apple's name ("${p.name}").`, p.display === p.name ? "" : p.display);
      if (n === null) return;
      await api(`/api/people/${encodeURIComponent(p.name)}`, { method: "PATCH", body: { display: n } });
      await refreshAll(); toast(n.trim() ? `Renamed to ${n.trim()}.` : `Using Apple's name, ${p.name}.`);
    }],
    ["Change colour", () => pickColor(p.color, async (c) => { await api(`/api/people/${encodeURIComponent(p.name)}`, { method: "PATCH", body: { color: c } }); await refreshAll(); })],
    S.people.filter(personOn).length === 1 && personOn(p)
      ? ["Show everyone on the map", showEveryone]
      : [`Show only ${firstName(p.display)} on the map`, () => showOnly(p)],
    ["Insights", () => { const d = primaryOf(p) || p.carried[0]; if (d) openInsights(d.id); }],
    p.tracking && p.tracking.following
      ? ["Stop following", () => followPerson(p, 0)]
      : ["Follow for 30 minutes", () => followPerson(p, 30)],
    ["Tracking settings", () => { openTab("setup"); setTimeout(() => $("#peopleTracking").scrollIntoView({ block: "center" }), 120); }],
  ], p.display);
}
async function followPerson(p, minutes) {
  try {
    await api(`/api/people/${encodeURIComponent(p.name)}/follow`, { method: "POST", body: { minutes } });
    toast(minutes ? `Following ${p.display}: a fresh location every ${fmtMin(S.cfgMoving || 2)} for ${minutes} min.` : `Stopped following ${p.display}.`, 5000);
    await refreshAll();
  } catch (e) { toast(e.message, 6000); }
}
async function loadTrackingTable() {
  const rows = await api("/api/config");
  const def = rows.find((r) => r.key === "locate_minutes")?.value ?? 15;
  const followDef = !!rows.find((r) => r.key === "follow_moving")?.value;
  S.cfgMoving = rows.find((r) => r.key === "moving_locate_minutes")?.value ?? 2;
  const ivOpts = (cur) => [["", `Default (${def ? fmtMin(def) : "never"})`], ["1", "1 min"], ["2", "2 min"], ["5", "5 min"], ["10", "10 min"], ["15", "15 min"], ["30", "30 min"], ["60", "1 hour"], ["0", "Never"]]
    .map(([v, l]) => `<option value="${v}" ${String(cur ?? "") === v ? "selected" : ""}>${l}</option>`).join("");
  const fOpts = (cur) => [["", `Default (${followDef ? "on" : "off"})`], ["1", "On"], ["0", "Off"]]
    .map(([v, l]) => `<option value="${v}" ${(cur == null ? "" : cur ? "1" : "0") === v ? "selected" : ""}>${l}</option>`).join("");
  const el = $("#peopleTracking");
  el.innerHTML = `<span class="h">Person</span><span class="h">Fresh location</span><span class="h">Follow while moving</span>` +
    S.people.map((p) => {
      const t = p.tracking || {};
      const st = t.following ? `<div class="st on">${t.following === "manual" ? "Following" : "Moving: following"} every ${fmtMin(t.effective_minutes)}${t.following_until ? ` until about ${esc(clock(t.following_until))}` : ""}</div>`
        : t.next_locate ? `<div class="st">Next fresh location around ${esc(clock(t.next_locate))}</div>` : `<div class="st">No fresh locations requested</div>`;
      return `<span>${esc(p.display)}</span>
        <select data-p="${esc(p.name)}" data-k="locate_minutes" aria-label="Fresh location interval for ${esc(p.display)}">${ivOpts(t.locate_minutes)}</select>
        <select data-p="${esc(p.name)}" data-k="follow" aria-label="Follow ${esc(p.display)} while moving">${fOpts(t.follow)}</select>${st}`;
    }).join("");
  el.onchange = async (e) => {
    const sel = e.target.closest("select"); if (!sel) return;
    const v = sel.value, body = {};
    body[sel.dataset.k] = v === "" ? null : sel.dataset.k === "follow" ? v === "1" : Number(v);
    try { await api(`/api/people/${encodeURIComponent(sel.dataset.p)}`, { method: "PATCH", body }); await loadDevices(); loadTrackingTable(); toast("Saved."); }
    catch (err) { toast(err.message, 6000); }
  };
}
function showMarkerFor(d, p) {
  L_temp.clearLayers();
  L.circleMarker([p[1], p[2]], { radius: 9, color: "#1e2b3a", weight: 2, fillOpacity: 0, dashArray: "3 3" }).addTo(L_temp);
  setTimeout(() => L_temp.clearLayers(), 4000);
}
function showLastKnown(d) {
  const p = lastOf(d); L_temp.clearLayers();
  L.marker([p[1], p[2]], { icon: L.divIcon({ className: "lastknown", iconSize: [16, 16] }) })
    .bindPopup(fixPopup(d, p, true)).addTo(L_temp).openPopup();
  fly([p[1], p[2]], 17);
  toast(`${d.name}: last known location, ${ago(p[0])}.`, 5000);
}
async function locateOne(d) {
  toast(`Locating ${d.name}. This can take up to 40 seconds.`, 40000);
  try {
    const r = await api(`/api/icloud/locate/${encodeURIComponent(d.id)}`, { method: "POST", timeout: 90000 });
    await refreshAll();
    const n = S.byId[d.id];
    if (r.updated && n && n.last) { fly([n.last.lat, n.last.lon], Math.max(map.getZoom(), 16)); toast(`Located ${d.name}.`, 5000); }
    else toast(`${d.name} didn't send a new location. It may be asleep, offline or without signal.`, 6000);
  } catch (e) { toast(e.message, 6000); }
}

/* ---------------- tabs ---------------- */
function openTab(name) {
  $$("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".tab-body").forEach((s) => s.classList.toggle("hidden", s.dataset.body !== name));
  $("#app").classList.remove("noside");
  if (S.viewer && name === "setup") name = "people";
  if (name === "alerts") { loadEvents(); if (S.viewer) loadMe().catch(() => {}); else loadConfigForms(); }
  if (name === "setup") { loadStatus(); loadConfigForms(); loadTrackingTable(); loadViewers().then(() => $("#usersCard").classList.remove("hidden"), () => $("#usersCard").classList.add("hidden")); }
  if (name === "insights") loadInsights();
  if (name !== "insights") L_stays.clearLayers();
  history.replaceState(null, "", "#" + name);
  setTimeout(() => map.invalidateSize(), 50);
}
window.openTab = openTab;
$$("#tabs button").forEach((b) => (b.onclick = () => openTab(b.dataset.tab)));
$("#tabs").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const tabs = $$("#tabs button"), i = tabs.findIndex((b) => b.getAttribute("aria-selected") === "true");
  const n = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]; openTab(n.dataset.tab); n.focus();
});
$("#sideToggle").onclick = () => { $("#app").classList.toggle("noside"); setTimeout(() => { map.invalidateSize(); drawLanes(); }, 50); };
$("#sheetHandle").onclick = () => $("#app").classList.add("noside");

/* ---------------- places ---------------- */
let placePreview = null;
function renderPlaces() {
  L_places.clearLayers();
  const el = $("#placeList");
  el.innerHTML = S.fences.length ? "" : '<p class="muted small">No places yet. Add Home first: it makes the timeline and alerts much more useful.</p>';
  for (const g of S.fences) {
    L.circle([g.lat, g.lon], { radius: g.radius, color: g.color, weight: 2, fillOpacity: 0.08, dashArray: "6 5" })
      .on("click", () => editPlace(g)).addTo(L_places)
;
    L.tooltip({ permanent: true, direction: "bottom", className: "place-label", offset: [0, 2] })
      .setLatLng([g.lat - g.radius / 111320, g.lon]).setContent(esc(g.name)).addTo(L_places);
    const here = [...new Set(g.inside.map((id) => (S.byId[id]?.owner ? ownerName(S.byId[id].owner) : S.byId[id]?.name)).filter(Boolean))];
    const who = g.people?.length ? `for ${g.people.map((n) => firstName(ownerName(n))).join(", ")}` : "for everyone";
    const alerts = g.on_enter && g.on_exit ? "arrivals and departures" : g.on_enter ? "arrivals" : g.on_exit ? "departures" : "no alerts";
    const row = document.createElement("div"); row.className = "place-row"; row.tabIndex = 0;
    row.innerHTML = `<span class="ring" style="border-color:${g.color}"></span><div><div class="place-name">${esc(g.name)}</div>
      <div class="sub">${Math.round(g.radius)} m, ${alerts} ${esc(who)}</div>${here.length ? `<div class="sub">Here now: ${esc(here.map(firstName).join(", "))}</div>` : ""}</div>`;
    row.onclick = () => { fly([g.lat, g.lon], 16); editPlace(g); };
    el.appendChild(row);
  }
}
function showPlaceForm(g) {
  $("#placeForm").classList.remove("hidden");
  $("#pfId").value = g.id || ""; $("#pfName").value = g.name || "";
  $("#pfLat").value = g.lat?.toFixed(6) ?? ""; $("#pfLon").value = g.lon?.toFixed(6) ?? "";
  $("#pfRadius").value = g.radius || 150; $("#pfColor").value = g.color || "#9a6b43";
  $("#pfEnter").checked = g.on_enter ?? true; $("#pfExit").checked = g.on_exit ?? true;
  $("#pfDelete").classList.toggle("hidden", !g.id);
  $("#pfPeople").innerHTML = S.people.map((p) => `<label class="toggle"><input type="checkbox" value="${esc(p.name)}" ${(g.people || []).includes(p.name) ? "checked" : ""}> ${esc(p.display)}</label>`).join("");
  $("#pfItems").innerHTML = S.items.map((d) => `<label class="toggle"><input type="checkbox" value="${esc(d.id)}" ${(g.devices || []).includes(d.id) ? "checked" : ""}> ${esc(d.name)}</label>`).join("");
  updatePreview(); $("#pfName").focus();
}
function editPlace(g) { openTab("places"); showPlaceForm(g); }
function newPlace(lat, lon, after) { S.afterPlaceSave = after || null; openTab("places"); showPlaceForm({ lat, lon }); stopPicking(); map.closePopup(); }
function updatePreview() {
  const lat = parseFloat($("#pfLat").value), lon = parseFloat($("#pfLon").value), r = Number($("#pfRadius").value);
  $("#pfRadiusVal").textContent = r >= 1000 ? `${(r / 1000).toFixed(2)} km` : `${r} m`;
  if (placePreview) { map.removeLayer(placePreview.c); map.removeLayer(placePreview.m); placePreview = null; }
  if (isNaN(lat) || isNaN(lon)) return;
  const c = L.circle([lat, lon], { radius: r, color: $("#pfColor").value, weight: 2, fillOpacity: 0.15 }).addTo(map);
  const m = L.marker([lat, lon], { draggable: true, title: "Drag to move the centre" }).addTo(map);
  m.on("drag", (e) => c.setLatLng(e.target.getLatLng()));
  m.on("dragend", (e) => { const ll = e.target.getLatLng(); $("#pfLat").value = ll.lat.toFixed(6); $("#pfLon").value = ll.lng.toFixed(6); });
  placePreview = { c, m };
}
["pfLat", "pfLon", "pfRadius", "pfColor"].forEach((id) => $("#" + id).addEventListener("input", updatePreview));
function closePlaceForm() { $("#placeForm").classList.add("hidden"); if (placePreview) { map.removeLayer(placePreview.c); map.removeLayer(placePreview.m); placePreview = null; } stopPicking(); }
$("#pfCancel").onclick = closePlaceForm;
$("#placeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { name: $("#pfName").value.trim(), lat: +$("#pfLat").value, lon: +$("#pfLon").value, radius: +$("#pfRadius").value,
    color: $("#pfColor").value, on_enter: $("#pfEnter").checked, on_exit: $("#pfExit").checked,
    people: $$("#pfPeople input:checked").map((i) => i.value), devices: $$("#pfItems input:checked").map((i) => i.value) };
  const id = $("#pfId").value;
  await api(id ? `/api/geofences/${id}` : "/api/geofences", { method: id ? "PUT" : "POST", body });
  closePlaceForm(); await loadFences(); computeRuns(); drawLanes(); render();
  toast(`Saved ${body.name}.`);
  if (S.afterPlaceSave) { const f = S.afterPlaceSave; S.afterPlaceSave = null; f(); }
});
$("#pfDelete").onclick = async () => {
  if (!confirm(`Delete ${$("#pfName").value}? Its alerts stop too.`)) return;
  await api(`/api/geofences/${$("#pfId").value}`, { method: "DELETE" }); closePlaceForm(); await loadFences(); computeRuns(); drawLanes(); render();
};
function stopPicking() { S.picking = false; $("#app").classList.remove("picking"); }
$("#addPlaceBtn").onclick = () => { closePlaceForm(); S.picking = true; $("#app").classList.add("picking"); toast("Click the map where the place is."); };
map.on("click", (e) => { if (S.picking) newPlace(e.latlng.lat, e.latlng.lng); closeMenu(); });

/* ---------------- map context menu ---------------- */
map.on("contextmenu", (e) => {
  const { lat, lng } = e.latlng, oe = e.originalEvent;
  openMenu(oe.clientX, oe.clientY, [
    ["What's here?", () => whatsHere(lat, lng)],
    ["Create place here", () => newPlace(lat, lng)],
    ["Copy coordinates", () => copyText(`${lat.toFixed(6)}, ${lng.toFixed(6)}`, "Copied coordinates.")],
    ["Centre the map here", () => map.panTo([lat, lng])],
  ], `${lat.toFixed(5)}, ${lng.toFixed(5)}`);
});
async function whatsHere(lat, lon) {
  try {
    const r = await api(`/api/reverse?lat=${lat}&lon=${lon}`);
    L.popup().setLatLng([lat, lon]).setContent(`<div class="pop"><b>${esc(r.name || "No address found")}</b><br><span class="muted">${lat.toFixed(5)}, ${lon.toFixed(5)}</span></div>`).openOn(map);
  } catch (e) { toast(e.message); }
}

/* ---------------- search ---------------- */
const sInput = $("#search"), sBox = $("#searchResults");
let sTimer = null, sItems = [], sSel = -1;
function localMatches(q) {
  const lq = q.toLowerCase(), out = [];
  for (const p of S.people) if (`${p.display} ${p.name}`.toLowerCase().includes(lq)) {
    const pos = personPos(p, now() + 60) || personPos(p);
    if (pos) out.push({ g: "People", name: p.display, lat: pos.p[1], lon: pos.p[2], z: 16, sub: ago(pos.p[0]) });
  }
  for (const d of S.devices) if (`${d.name} ${d.model || ""}`.toLowerCase().includes(lq) && d.last)
    out.push({ g: "Devices", name: `${d.name}${d.owner ? ", " + firstName(ownerName(d.owner)) : ""}`, lat: d.last.lat, lon: d.last.lon, z: 17, sub: ago(d.last.ts) });
  for (const g of S.fences) if (g.name.toLowerCase().includes(lq)) out.push({ g: "Places", name: g.name, lat: g.lat, lon: g.lon, z: 16, sub: `${Math.round(g.radius)} m` });
  const m = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (m && Math.abs(+m[1]) <= 90 && Math.abs(+m[2]) <= 180) out.unshift({ g: "Coordinates", name: `${(+m[1]).toFixed(5)}, ${(+m[2]).toFixed(5)}`, lat: +m[1], lon: +m[2], z: 17, pin: true });
  return out.slice(0, 20);
}
function showResults(items, loading = false) {
  sItems = items; sSel = -1;
  if (!items.length && !loading) { sBox.innerHTML = '<div class="sh">Nothing matches. Press Enter to search addresses.</div>'; sBox.classList.remove("hidden"); return; }
  let html = "", lastG = "";
  items.forEach((it, i) => { if (it.g !== lastG) { html += `<div class="sh">${esc(it.g)}</div>`; lastG = it.g; } html += `<div class="sr" role="option" data-i="${i}"><span>${esc(it.name)}</span>${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</div>`; });
  if (loading) html += '<div class="sh">Searching addresses…</div>';
  sBox.innerHTML = html; sBox.classList.remove("hidden");
}
async function geoSearch(q) {
  const c = map.getCenter(), local = localMatches(q);
  showResults(local, true);
  try {
    const res = await api(`/api/geocode?q=${encodeURIComponent(q)}&lat=${c.lat}&lon=${c.lng}`);
    if (sInput.value.trim() !== q) return;
    showResults(local.concat(res.map((r) => ({ g: "Addresses", name: r.name, lat: r.lat, lon: r.lon, bbox: r.bbox, pin: true, sub: r.type }))));
  } catch (e) { showResults(local); toast(e.message); }
}
function pick(it) {
  sBox.classList.add("hidden"); L_temp.clearLayers();
  if (it.bbox && it.bbox.length === 4) map.fitBounds([[it.bbox[0], it.bbox[2]], [it.bbox[1], it.bbox[3]]], { maxZoom: 17 });
  else fly([it.lat, it.lon], it.z || 16);
  if (it.pin) L.marker([it.lat, it.lon]).bindPopup(popupEl(`<b>${esc(it.name)}</b><div class="acts"><a data-a="place">Make this a place</a></div>`, { place: () => newPlace(it.lat, it.lon) })).addTo(L_temp).openPopup();
}
sInput.addEventListener("input", () => {
  const q = sInput.value.trim(); clearTimeout(sTimer);
  if (!q) { sBox.classList.add("hidden"); L_temp.clearLayers(); return; }
  showResults(localMatches(q));
  if (q.length >= 4) sTimer = setTimeout(() => geoSearch(q), 900);
});
sInput.addEventListener("keydown", (e) => {
  const rows = $$(".sr", sBox);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); sSel = Math.max(0, Math.min(rows.length - 1, sSel + (e.key === "ArrowDown" ? 1 : -1)));
    rows.forEach((r, i) => r.classList.toggle("sel", i === sSel));
  } else if (e.key === "Enter") {
    e.preventDefault(); clearTimeout(sTimer);
    if (sSel >= 0 && sItems[sSel]) pick(sItems[sSel]); else geoSearch(sInput.value.trim());
  } else if (e.key === "Escape") sBox.classList.add("hidden");
});
sBox.addEventListener("click", (e) => { const r = e.target.closest(".sr"); if (r) pick(sItems[+r.dataset.i]); });
document.addEventListener("click", (e) => { if (!e.target.closest(".search")) sBox.classList.add("hidden"); });

/* ---------------- insights ---------------- */
function fillDeviceSelect() {
  const sel = $("#insDevice"), cur = sel.value;
  const opt = (d) => `<option value="${esc(d.id)}">${esc(d.name)}${d.last ? "" : " (no location)"}</option>`;
  sel.innerHTML = S.people.map((p) => `<optgroup label="${esc(p.display)}">${p.devices.map(opt).join("")}</optgroup>`).join("") +
    (S.items.length ? `<optgroup label="Items">${S.items.map(opt).join("")}</optgroup>` : "") +
    (S.photoDevs.length ? `<optgroup label="Photo history">${S.photoDevs.map(opt).join("")}</optgroup>` : "");
  if (cur && S.byId[cur]) sel.value = cur;
  else { const p = S.people.find((x) => primaryOf(x) || x.carried.length); const d = p && (primaryOf(p) || p.carried[0]); if (d) sel.value = d.id; }
}
function openInsights(id) { $("#insDevice").value = id; openTab("insights"); }
$("#insDevice").onchange = loadInsights;
function svgBars(rows, color) {
  const w = 300, h = 80, n = rows.length, max = Math.max(0.01, ...rows.map((r) => r.km)), bw = w / n;
  return `<svg class="chart" viewBox="0 0 ${w} ${h + 14}" preserveAspectRatio="none" role="img" aria-label="Distance per day">
    ${rows.map((r, i) => `<rect x="${(i * bw + 1).toFixed(1)}" y="${(h - (r.km / max) * h).toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" height="${((r.km / max) * h).toFixed(1)}" fill="${color}"><title>${r.day}: ${r.km} km</title></rect>`).join("")}
    <line class="ax" x1="0" y1="${h}" x2="${w}" y2="${h}"/><text x="0" y="${h + 12}">${esc(rows[0].day)}</text><text x="${w}" y="${h + 12}" text-anchor="end">${esc(rows[n - 1].day)}</text></svg>`;
}
function svgBattery(pts) {
  const w = 300, h = 80, X = (t) => ((t - S.start) / (S.end - S.start || 1)) * w, Y = (b) => h - b * h;
  const line = pts.map((p) => `${X(p[0]).toFixed(1)},${Y(p[4]).toFixed(1)}`).join(" ");
  return `<svg class="chart" viewBox="0 0 ${w} ${h + 14}" preserveAspectRatio="none" role="img" aria-label="Battery level over time">
    <line class="ax" x1="0" y1="${Y(0.2)}" x2="${w}" y2="${Y(0.2)}" stroke-dasharray="3 3"/><line class="ax" x1="0" y1="${h}" x2="${w}" y2="${h}"/>
    <polyline points="${line}" fill="none" stroke="var(--wood)" stroke-width="1.8" vector-effect="non-scaling-stroke"/>
    <text x="0" y="${h + 12}">${esc(fmtD.format(new Date(S.start * 1000)))}</text><text x="${w}" y="${h + 12}" text-anchor="end">${esc(fmtD.format(new Date(S.end * 1000)))}</text>
    <text x="${w}" y="${Y(0.2) - 3}" text-anchor="end">20%</text></svg>`;
}
async function loadInsights() {
  const id = $("#insDevice").value, body = $("#insBody");
  L_stays.clearLayers();
  $("#insRange").textContent = `From ${dt(S.start)} to ${dt(S.end)}. Change the range on the timeline.`;
  if (!id) { body.innerHTML = '<p class="muted">Nothing to analyse yet.</p>'; return; }
  body.innerHTML = '<p class="muted">Working it out…</p>';
  const r = await api(`/api/analysis?device=${encodeURIComponent(id)}&start=${S.start}&end=${S.end}`);
  const d = S.byId[id], person = d && S.personByName[d.owner], color = person ? person.color : "#3f7fa3";
  const stayMin = r.stays.reduce((a, s) => a + s.minutes, 0);
  const batt = (S.data[id] || []).filter((p) => p[4] != null);
  const unnamed = (x) => !x.place && !/[a-z]/i.test(x.name || "");
  body.innerHTML = `
    <div class="stats">
      <div class="stat"><b>${r.km}</b><span>km travelled</span></div>
      <div class="stat"><b>${r.trips.length}</b><span>trips</span></div>
      <div class="stat"><b>${r.stays.length}</b><span>stays</span></div>
      <div class="stat"><b>${dur(stayMin)}</b><span>time at stays</span></div>
      <div class="stat"><b>${r.farthest_from_start_km}</b><span>km furthest out</span></div>
      <div class="stat"><b>${r.points}</b><span>fixes</span></div>
    </div>
    ${r.per_day_km.length > 1 ? `<div class="ins-h">Distance per day</div>${svgBars(r.per_day_km, color)}` : ""}
    ${batt.length > 1 ? `<div class="ins-h">Battery</div>${svgBattery(batt)}` : ""}
    <div class="ins-h">Places visited most</div><div class="ins-list" id="insPlaces">${r.top_places.map((p, i) => `<div class="ins-item" data-i="${i}">
      <span>${unnamed(p) ? `Unnamed spot <button class="name-it" data-name="${i}">Name this place</button>` : `<span class="place-name">${esc(p.name)}</span>`}</span>
      <span class="muted">${p.visits} visit${p.visits === 1 ? "" : "s"}, ${dur(p.hours * 60)}</span></div>`).join("") || '<p class="muted small">No stays in this range.</p>'}</div>
    <div class="ins-h">Stays</div><div class="ins-list" id="insStays">${r.stays.map((s, i) => [s, i]).reverse().map(([s, i]) => `<div class="ins-item" data-i="${i}">
      <span>${s.place ? `<span class="place-name">${esc(s.place)}</span>` : `Unnamed spot <button class="name-it" data-stay="${i}">Name this place</button>`}</span>
      <span class="muted">${dur(s.minutes)}</span><span class="when">${esc(dt(s.start))}</span></div>`).join("") || '<p class="muted small">No stays in this range.</p>'}</div>
    <div class="ins-h">Trips</div><div class="ins-list" id="insTrips">${r.trips.map((t, i) => [t, i]).reverse().map(([t, i]) => `<div class="ins-item" data-i="${i}">
      <span>${t.km} km in ${dur(t.minutes)}</span><span class="muted">${t.avg_kmh ? `${MPH(t.avg_kmh)} mph average` : ""}</span><span class="when">${esc(dt(t.start))}</span></div>`).join("") || '<p class="muted small">No trips in this range.</p>'}</div>`;
  for (const s of r.stays) L.circleMarker([s.lat, s.lon], { radius: Math.min(22, 5 + Math.sqrt(s.minutes)), color, weight: 2, fillOpacity: 0.22 })
    .bindTooltip(`${s.place ? esc(s.place) : "Stay"}<br>${esc(dt(s.start))}<br>${dur(s.minutes)}`).addTo(L_stays);
  const again = () => loadInsights();
  $("#insPlaces").onclick = (e) => {
    const nb = e.target.closest("[data-name]"); if (nb) { const p = r.top_places[+nb.dataset.name]; return newPlace(p.lat, p.lon, again); }
    const x = e.target.closest("[data-i]"); if (x) { const p = r.top_places[+x.dataset.i]; fly([p.lat, p.lon], 16); }
  };
  $("#insStays").onclick = (e) => {
    const nb = e.target.closest("[data-stay]"); if (nb) { const s = r.stays[+nb.dataset.stay]; return newPlace(s.lat, s.lon, again); }
    const x = e.target.closest("[data-i]"); if (x) { const s = r.stays[+x.dataset.i]; fly([s.lat, s.lon], 16); stopPlay(); S.t = s.end; S.follow = false; render(); }
  };
  $("#insTrips").onclick = (e) => {
    const x = e.target.closest("[data-i]"); if (!x) return; const t = r.trips[+x.dataset.i];
    const seg = (S.data[id] || []).filter((p) => p[0] >= t.start && p[0] <= t.end).map((p) => [p[1], p[2]]);
    map.fitBounds(seg.length > 1 ? seg : [t.from, t.to], { padding: [50, 50], maxZoom: 16 });
    stopPlay(); S.t = t.end; S.follow = false; render();
  };
}

/* ---------------- alerts tab ---------------- */
$("#evKind").onchange = loadEvents;
async function loadEvents() {
  const ev = await api(`/api/events?limit=300&kind=${$("#evKind").value}`);
  $("#eventList").innerHTML = ev.map((e) => {
    // Emails carry link lines; in the app show the sentence and one action instead.
    const text = String(e.message).split(/\n\s*\n/)[0];
    const focus = (String(e.message).match(/#focus=([^\s]+)/) || [])[1];
    const mail = e.emailed ? ", emailed" : e.email_error === "quiet hours" ? ", not emailed (quiet hours)" : e.email_error ? `, email failed: ${esc(e.email_error)}` : "";
    return `<div class="ev"><i class="${esc(e.kind)}"></i><span>${esc(text).replace(/\n/g, "<br>")}${focus ? ` <button class="name-it" data-focus="${esc(focus)}">Show on map</button>` : ""}</span>
      <span class="t">${esc(dt(e.ts))}${mail}</span></div>`;
  }).join("") || '<p class="muted">Nothing has happened yet. Arrivals, departures and warnings will be listed here.</p>';
  $("#eventList").onclick = (ev2) => { const b = ev2.target.closest("[data-focus]"); if (b) { location.hash = "focus=" + b.dataset.focus; } };
}
const ALERT_KEYS = ["quiet_from_hour", "quiet_to_hour", "digest_hour", "low_battery_pct", "stale_hours", "geofence_exit_margin_m", "max_alert_accuracy_m"];
const TRACK_KEYS = ["locate_minutes", "follow_moving", "moving_locate_minutes", "follow_rest_minutes", "follow_max_hours", "movement_threshold_m", "family_locates_per_hour", "family_pause_minutes", "family_throttle_after", "family_pause_cap_minutes", "locate_spacing_s", "unanswered_backoff_after", "session_renew_minutes", "session_renew_moving_minutes", "session_renew_quiet_minutes", "quiet_after_minutes", "poll_seconds", "airtag_poll_minutes", "stay_radius_m", "stay_min_minutes"];
async function loadConfigForms() {
  const rows = await api("/api/config");
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
  const field = (r) => (typeof r.value === "boolean" || (r.min === 0 && r.max === 1 && !r.unit))
    ? `<select name="${r.key}"><option value="1" ${r.value ? "selected" : ""}>On</option><option value="0" ${r.value ? "" : "selected"}>Off</option></select>`
    : `<input type="number" name="${r.key}" min="${r.min}" max="${r.max}" step="any" value="${Number(r.value)}">`;
  const html = (keys, note) => keys.map((k) => byKey[k]).filter(Boolean).map((r) => `<label class="cfg-row"><span>${esc(r.label.replace(/ \(1 = on, 0 = off\)/, ""))}${r.unit ? ` <span class="u">${esc(r.unit)}</span>` : ""}</span>
    ${field(r)}</label>`).join("") +
    `<div class="row gap end"><span class="muted small" style="margin-right:auto">${note}</span><button class="btn small primary">Save settings</button></div>`;
  $("#alertCfg").innerHTML = html(ALERT_KEYS, "Hours are 0 to 23, local time.");
  $("#trackCfg").innerHTML = html(TRACK_KEYS, "Reading is free for the phones. A fresh location is one quick fix on the phone.");
}
for (const id of ["alertCfg", "trackCfg"]) $("#" + id).addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.submitter, async () => {
    const body = Object.fromEntries([...new FormData(e.target)].map(([k, v]) => [k, Number(v)]));
    await api("/api/config", { method: "PUT", body }); await loadConfigForms(); if (id === "trackCfg") loadTrackingTable(); toast("Saved settings.");
  });
});
$("#digestPreview").onclick = (e) => busy(e.target, async () => { const r = await api("/api/digest"); const b = $("#digestBox"); b.textContent = `${r.subject}\n\n${r.body}`; b.classList.remove("hidden"); });
$("#digestSend").onclick = (e) => busy(e.target, async () => { await api("/api/digest/send", { method: "POST" }); toast("Emailed the summary."); });

/* ---------------- setup tab ---------------- */
const stateBadge = (s) => ({ ok: "ok", need_2fa: "warn", need_login: "bad", error: "bad" }[s] || "");
const stateWord = (s) => ({ ok: "connected", need_2fa: "waiting for code", need_login: "sign in again", logged_out: "not signed in", error: "error" }[s] || s);
async function loadStatus() {
  if (S.viewer) return {};
  const st = await api("/api/status");
  if (!S.appsDone) { S.appsDone = true; if (window.RigApps) RigApps.render(st.apps, "Family Locator"); }
  const ic = st.icloud, at = st.airtag;
  $("#chips").innerHTML = [["iCloud", ic.state], ["AirTags", at.state], ["Email", st.email.configured ? "ok" : "off"]]
    .map(([n, s]) => `<button class="chip ${stateBadge(s)}" onclick="openTab('setup')" title="${esc(n)}: ${esc(stateWord(s))}"><i></i>${n}</button>`).join("");
  $("#icState").textContent = stateWord(ic.state); $("#icState").className = "badge " + stateBadge(ic.state);
  $("#icDetail").textContent = [ic.apple_id, ic.detail, ic.password_saved ? "Password saved for automatic re-login" : ""].filter(Boolean).join(". ");
  $("#icForget").classList.toggle("hidden", !ic.password_saved);
  icAppleId = ic.apple_id || "";
  $("#icRememberBtn").classList.toggle("hidden", ic.password_saved || ic.state !== "ok" || icRememberOpen);
  if (icRememberOpen && ic.state === "ok") $("#icLogin").classList.remove("hidden");
  $("#icLogin").classList.toggle("hidden", !["logged_out", "need_login"].includes(ic.state));
  $("#ic2fa").classList.toggle("hidden", ic.state !== "need_2fa");
  $("#icActions").classList.toggle("hidden", ic.state !== "ok");
  if (ic.apple_id && !$("#icId").value) $("#icId").value = ic.apple_id;
  $("#icStats").textContent = `Last check ${ago(ic.last_poll)}, last fresh locate ${ago(ic.last_locate)}. ${ic.devices} devices, ${ic.new_points} new fixes and ${ic.poll_errors} errors since the app started.`;
  $("#atState").textContent = stateWord(at.state); $("#atState").className = "badge " + stateBadge(at.state);
  $("#atDetail").textContent = at.detail;
  $("#atKeys").textContent = at.key_files.length ? at.key_files.join(", ") : "none yet";
  $("#atDir").textContent = at.tag_dir;
  $("#atLogin").classList.toggle("hidden", !["logged_out", "need_login"].includes(at.state));
  $("#at2fa").classList.toggle("hidden", at.state !== "need_2fa");
  $("#atActions").classList.toggle("hidden", at.state !== "ok");
  const ph = st.photos;
  $("#phStatus").textContent = ph.kind ? `${ph.running ? "Importing" : "Last import"}: ${ph.scanned} photos checked, ${ph.with_gps} with a location, ${ph.new} added${ph.error ? `. Error: ${ph.error}` : ""}` : "";
  $("#emState").textContent = st.email.configured ? "set up" : "not set up"; $("#emState").className = "badge " + (st.email.configured ? "ok" : "bad");
  $("#emDetail").textContent = st.email.configured ? `Alerts go to ${st.email.recipient}. ${st.email.emails_sent} sent, ${st.email.emails_failed} failed since the app started.` : "Add email_address and email_app_password to data/config.toml, then restart the app.";
  const bk = st.backup;
  $("#storageLine").textContent = `${st.storage.points.toLocaleString()} fixes saved on disk in ${st.storage.db}. ` + (bk ? `Last daily backup ${ago(bk.ts)} (${(bk.bytes / 1024).toFixed(0)} KB); 14 are kept.` : "The first backup hasn't run yet.");
  $("#cfgLine").textContent = `Version ${st.version}.`;
  addKeyedLayers(st.tile_keys);
  const fr = st.family_requests;
  if (fr) $("#familyBudget").innerHTML = fr.paused_until
    ? `<b style="color:var(--amber)">Family requests paused until ${esc(clock(fr.paused_until))}.</b> ${esc(fr.reason)}; it tries one again then. Your own devices aren't affected.`
    : `Family requests this hour: ${fr.used_last_hour} of ${fr.budget}${fr.self ? ` (${esc(firstName(ownerName(fr.self)))}'s own devices don't count)` : ""}.`;
  const L1 = st.links || {};
  $("#pubLink").innerHTML = L1.public ? `<a href="${esc(L1.public)}" target="_blank" rel="noopener">${esc(L1.public)}</a> <button class="name-it" id="copyPub">Copy</button>` : "The gateway's tunnel isn't running, so there's no outside link right now.";
  $("#lanLink").innerHTML = L1.lan ? `At home: <a href="${esc(L1.lan)}">${esc(L1.lan)}</a>` : "";
  if (L1.public) $("#copyPub").onclick = () => copyText(L1.public, "Copied the link.");
  return st;
}
const busy = async (btn, fn) => { if (btn) btn.disabled = true; try { await fn(); } catch (e) { toast(e.message, 6000); } finally { if (btn) btn.disabled = false; loadStatus().catch(() => {}); } };
$("#icLogin").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/icloud/login", { method: "POST", timeout: 90000, body: { apple_id: $("#icId").value, password: $("#icPw").value, remember: $("#icRemember").checked } }); $("#icPw").value = ""; icRememberOpen = false; toast(r.detail || stateWord(r.state)); }); });
$("#ic2fa").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/icloud/2fa", { method: "POST", timeout: 90000, body: { code: $("#icCode").value } }); $("#icCode").value = ""; toast(r.state === "ok" ? "Connected to iCloud." : r.detail); if (r.state === "ok") setTimeout(refreshAll, 4000); }); });
$("#icSms").onclick = (e) => busy(e.target, async () => toast((await api("/api/icloud/sms", { method: "POST" })).detail));
$("#icPoll").onclick = (e) => busy(e.target, async () => { toast("Locating everyone. This takes about 20 seconds.", 25000); await api("/api/icloud/poll?locate=true", { method: "POST", timeout: 90000 }); await refreshAll(); toast("Located everyone who answered."); });

$("#icRememberBtn").onclick = () => { icRememberOpen = true; $("#icLogin").classList.remove("hidden"); $("#icRememberBtn").classList.add("hidden"); $("#icRemember").checked = true; if (!$("#icId").value) $("#icId").value = icAppleId; $("#icPw").focus(); };
$("#icForget").onclick = (e) => { if (confirm("Delete the saved Apple ID password? Tracking carries on, but the next time Apple ends the session you'll have to sign in again.")) busy(e.target, async () => { await api("/api/icloud/forget-password", { method: "POST" }); toast("Saved password deleted."); refreshAll(); }); };
$("#icLogout").onclick = (e) => { if (confirm("Sign out of iCloud? Tracking stops until you sign in again.")) busy(e.target, () => api("/api/icloud/logout", { method: "POST" })); };
$("#atLogin").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/airtag/login", { method: "POST", body: { apple_id: $("#atId").value, password: $("#atPw").value } }); $("#atPw").value = ""; toast(r.detail || stateWord(r.state)); }); });
$("#at2fa").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/airtag/2fa", { method: "POST", body: { code: $("#atCode").value } }); toast(r.detail || stateWord(r.state)); }); });
$("#atPoll").onclick = (e) => busy(e.target, async () => { await api("/api/airtag/poll", { method: "POST" }); await refreshAll(); toast("Fetched AirTags."); });
$("#atLogout").onclick = (e) => busy(e.target, () => api("/api/airtag/logout", { method: "POST" }));
$("#phIcloud").onclick = (e) => busy(e.target, async () => { await api("/api/photos/icloud", { method: "POST", body: {} }); toast("Importing from iCloud Photos."); pollPhotos(); });
$("#phFolder").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/photos/folder", { method: "POST", body: { path: $("#phPath").value } }); if (r.error) throw new Error(r.error); toast("Importing the folder."); pollPhotos(); }); });
function pollPhotos() { const iv = setInterval(async () => { const st = await loadStatus(); if (!st.photos.running) { clearInterval(iv); await refreshAll(); } }, 3000); }
$("#emTest").onclick = (e) => busy(e.target, async () => toast("Sent a test email to " + (await api("/api/email/test", { method: "POST" })).to));
$("#pwChange").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { await api("/api/auth/change", { method: "POST", body: { old: $("#pwOld").value, new: $("#pwNew").value } }); $("#pwOld").value = $("#pwNew").value = ""; toast("Changed the password."); }); });

/* ---------------- user pages admin (main page only) ---------------- */
let viewersCache = [];
async function loadViewers() {
  viewersCache = await api("/api/viewers");
  const who = (v) => v.people.length ? v.people.map((n) => firstName(ownerName(n))).join(", ") : "nobody yet";
  $("#vList").innerHTML = viewersCache.map((v) => `<div class="place-row vrow" data-id="${v.id}">
      <div><div class="place-name">${esc(v.name)}${v.auto_owner ? ' <span class="muted small">(made automatically)</span>' : ""}</div>
        <div class="sub">Sees ${esc(who(v))}. ${v.email ? `Alerts to ${esc(v.email)}` : "No email"}${v.digest ? `, daily email at ${v.digest_hour}:00` : ""}.
          ${v.places} place${v.places === 1 ? "" : "s"}. ${v.last_seen ? `Last opened ${esc(ago(v.last_seen))}` : "Not opened yet"}.</div>
        <div class="row gap" style="margin-top:6px;flex-wrap:wrap">
          <button class="btn small" data-a="copy">Copy link</button>
          ${v.email ? '<button class="btn small" data-a="send">Email the link</button>' : ""}
          <button class="btn small" data-a="open">Open</button>
          <button class="btn small" data-a="edit">Edit</button>
          <button class="btn small" data-a="new">New link</button>
        </div></div></div>`).join("") || '<p class="muted small">No user pages yet.</p>';
}
function showViewerForm(v) {
  $("#vForm").classList.remove("hidden");
  $("#vId").value = v.id || ""; $("#vName").value = v.name || ""; $("#vEmail").value = v.email || "";
  $("#vDigest").checked = !!v.digest; $("#vHour").value = v.digest_hour ?? 8;
  $("#vPeople").innerHTML = S.people.map((p) => `<label class="toggle"><input type="checkbox" value="${esc(p.name)}" ${(v.people || []).includes(p.name) ? "checked" : ""}> ${esc(p.display)}</label>`).join("");
  $("#vDelete").classList.toggle("hidden", !v.id);
  $("#vName").focus();
}
$("#vAdd").onclick = () => showViewerForm({});
$("#vCancel").onclick = () => $("#vForm").classList.add("hidden");
$("#vForm").addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.submitter, async () => {
    const id = $("#vId").value, body = { name: $("#vName").value, email: $("#vEmail").value, digest: $("#vDigest").checked,
      digest_hour: Number($("#vHour").value), people: $$("#vPeople input:checked").map((i) => i.value) };
    const v = await api(id ? `/api/viewers/${id}` : "/api/viewers", { method: id ? "PUT" : "POST", body });
    $("#vForm").classList.add("hidden"); await loadViewers();
    toast(id ? "Saved." : `Made ${v.name}'s page. Copy or email the link to them.`, 5000);
  });
});
$("#vDelete").onclick = (e) => {
  const id = $("#vId").value, v = viewersCache.find((x) => String(x.id) === id);
  if (v && confirm(`Delete ${v.name}'s page? The link stops working, and its places and alerts are deleted.${v.auto_owner ? " It won't be made again automatically." : ""}`))
    busy(e.target, async () => { await api(`/api/viewers/${id}`, { method: "DELETE" }); $("#vForm").classList.add("hidden"); await loadViewers(); toast("Deleted."); });
};
$("#vList").onclick = (e) => {
  const b = e.target.closest("[data-a]"), row = e.target.closest(".vrow"); if (!b || !row) return;
  const v = viewersCache.find((x) => String(x.id) === row.dataset.id); if (!v) return;
  const link = v.link || v.lan_link;
  const act = b.dataset.a;
  if (act === "edit") return showViewerForm(v);
  if (act === "open") return window.open(link, "_blank", "noopener");
  if (act === "copy") return copyText(link, v.link ? "Copied the link." : "Copied the home-Wi-Fi link (the outside link is down right now).");
  if (act === "send") return busy(b, async () => { const r = await api(`/api/viewers/${v.id}/send-link`, { method: "POST" }); toast(`Emailed the link to ${r.to}.`); });
  if (act === "new" && confirm(`Make a new link for ${v.name}? The current link stops working straight away.`))
    return busy(b, async () => { await api(`/api/viewers/${v.id}/new-link`, { method: "POST" }); await loadViewers(); toast("New link made. Send it to them."); });
};

/* ---------------- refresh loop ---------------- */
async function refreshAll() {
  await Promise.all([loadDevices(), loadFences()]);
  if (S.preset === "live" && S.follow && !S.playing) { [S.start, S.end] = presetRange("live"); S.t = S.end; }
  await loadPoints();
  S.lastRefresh = now();   // only once it all worked, so the banner can tell a failing refresh
}
/* playback speed and trail: continuous sliders on a log scale (0-1000).
   Speed 1 s/s (real time) .. 1 h/s; trail 15 min .. 7 days, with the far right end (>= 970) = the whole
   loaded range. Dragging the trail into that end also switches the range to All, so "max" means all time. */
const SPEED_MIN = 1, SPEED_MAX = 3600, TRAIL_MIN = 900, TRAIL_MAX = 7 * 86400, TRAIL_WHOLE = 970;
const logPos = (v, lo, hi) => lo * Math.pow(hi / lo, v);
function niceSpan(sec) {
  const r = (x) => (x < 10 ? Math.round(x * 10) / 10 : Math.round(x));
  if (Math.round(sec) < 60) return `${Math.round(sec)} s`;
  if (Math.round(sec / 60) < 60) return `${Math.round(sec / 60)} min`;
  if (r(sec / 3600) < 24) { const h = r(sec / 3600); return `${h} ${h === 1 ? "hour" : "hours"}`; }
  const d = r(sec / 86400); return `${d} ${d === 1 ? "day" : "days"}`;
}
function speedSec() { return logPos(Number($("#speed").value) / 1000, SPEED_MIN, SPEED_MAX); }
function trailSec() { const v = Number($("#trail").value); return v >= TRAIL_WHOLE ? 0 : logPos(v / TRAIL_WHOLE, TRAIL_MIN, TRAIL_MAX); }
/* Fixes (confidence) slider: the accuracy a fix needs to join the track line, 5 m .. 2 km on a log
   scale, far right (>= CONF_ANY) = every fix whatever its accuracy. Default = ROUGH_ACC (50 m).
   Also used for the speeds on the cards/markers and the jump filter (dropSpikes). Page-only: the
   server's alerts don't change. */
const CONF_MIN = 5, CONF_MAX = 2000, CONF_ANY = 970;
const confPosOf = (m) => Math.round(Math.log(m / CONF_MIN) / Math.log(CONF_MAX / CONF_MIN) * CONF_ANY);
function confAcc() { const v = Number($("#conf").value); return v >= CONF_ANY ? Infinity : logPos(v / CONF_ANY, CONF_MIN, CONF_MAX); }
const niceM = (m) => m >= 1000 ? `${Math.round(m / 100) / 10} km` : `${m < 20 ? Math.round(m) : Math.round(m / 5) * 5} m`;
function syncSliders() {
  const sp = niceSpan(speedSec()).replace(/ hours?$/, " h")+ "/s";
  const tr = trailSec() ? niceSpan(trailSec()) : S.preset === "all" ? "All time" : S.end > S.start ? `Whole range (${niceSpan(S.end - S.start)})` : "Whole range";
  const ca = confAcc(), cf = ca === Infinity ? "All fixes" : `Within ${niceM(ca)}`;
  for (const [id, label] of [["speed", sp], ["trail", tr], ["conf", cf]]) {
    const el = $("#" + id);
    $("#" + id + "Out").textContent = label; el.setAttribute("aria-valuetext", label);
    el.style.setProperty("--fill", Number(el.value) / 10 + "%");
  }
}
$("#speed").value = store.get("speedPos2", 781); $("#trail").value = store.get("trailPos", 1000);
$("#conf").value = store.get("confPos", confPosOf(ROUGH_ACC)); syncSliders();
$("#conf").oninput = () => { store.set("confPos", Number($("#conf").value)); syncSliders(); schedRender(); };
$("#conf").onchange = () => {   // on release: redo the jump filter and what's built on it, if it moved
  if (!S.raw || spikeAccNow() === S.spikeAcc) return;
  dropSpikes(); buildMoves(); computeRuns(); drawLanes(); render();
};
$("#conf").ondblclick = () => { $("#conf").value = confPosOf(ROUGH_ACC); $("#conf").oninput(); $("#conf").onchange(); };   // back to the default
$("#speed").oninput = () => { store.set("speedPos2", Number($("#speed").value)); syncSliders(); };
let trailWas = Number($("#trail").value);
$("#trail").oninput = () => { store.set("trailPos", Number($("#trail").value)); syncSliders(); schedRender(); };
$("#trail").onchange = () => {   // on release, not while dragging: loading All can take a moment
  const v = Number($("#trail").value), into = v >= TRAIL_WHOLE && trailWas < TRAIL_WHOLE;
  trailWas = v;
  if (into && S.preset !== "all") { stopPlay(); setPreset("all"); }
};

function handleHash(initial = false) {
  const h = decodeURIComponent(location.hash.slice(1));
  if (!h) return;
  if (h.startsWith("focus=")) {
    const key = h.slice(6);
    const p = S.personByName[key] || S.people.find((x) => x.display === key);
    const d = !p && S.byId[key];
    openTab("people");
    if (isPhone()) $("#app").classList.add("noside");
    if (p) { const pos = personPos(p); if (pos) setTimeout(() => fly([pos.p[1], pos.p[2]], 16), 150); else toast(`No location for ${p.display} yet.`); }
    else if (d && d.last) setTimeout(() => fly([d.last.lat, d.last.lon], 16), 150);
  } else if (h === "lanes") { $("#timebar").scrollIntoView(); }
  else if ($(`[data-tab="${h}"]`)) {
    openTab(h);
    // The address remembers the last tab (#people, #setup...). On a phone, reloading must select it
    // without sliding the sheet up over the map; a hash change while using the page still opens it.
    if (initial && isPhone()) { $("#app").classList.add("noside"); setTimeout(() => map.invalidateSize(), 50); }
  }
}

let started = false;
async function start() {
  $("#app").classList.remove("hidden");
  if (isPhone()) $("#app").classList.add("noside");
  map.invalidateSize();
  restoreOverlays();
  if (started) return refreshAll();
  started = true;
  await Promise.all([loadDevices(), loadFences(), loadStatus().catch(() => {})]);
  S.lastRefresh = now();
  await setPreset(S.preset === "custom" ? "live" : S.preset);
  handleHash(true);
  addEventListener("hashchange", () => handleHash());
  // Live data: refresh every 30 s, and straight away when the page comes back into view or the network
  // returns (phones suspend a backgrounded tab entirely, so it has old data until then).
  // `refreshing` stops overlapping refreshes; it's a start time, so a refresh that somehow never
  // settles can't block the ones after it for more than 2 minutes (api() times out at 30 s anyway).
  let refreshing = 0;
  /* Diagnostics: whenever the data was more than 3 minutes old by the time a refresh succeeded, or 3
     refreshes in a row failed, the page reports what happened in between to the server log
     (journal "[ClientError] ... refresh:"). A failed refresh is otherwise silent by design. */
  const R = { busy: 0, fails: 0, lastErr: "", reasons: {} };
  const PAGE_VER = (document.querySelector('script[src*="app.js"]')?.src.match(/v=(\d+)/) || [])[1] || "?";
  const report = (what) => {
    const msg = `refresh: ${what} | page v${PAGE_VER} | preset ${S.preset}, follow ${S.follow}, playing ${S.playing}, visible ${!document.hidden}, `
      + `skipped busy ${R.busy}, failed ${R.fails}${R.lastErr ? ` (last: ${R.lastErr})` : ""}, triggers ${JSON.stringify(R.reasons)}`;
    try { navigator.sendBeacon(BASE + "api/client-error", new Blob([JSON.stringify({ msg, url: location.href, ua: navigator.userAgent })], { type: "application/json" })); } catch { /* best effort */ }
  };
  // Refreshes run whether or not the window is focused or even visible: on two monitors the browser
  // can be on show without being the active window, and browsers can't always tell. (Background
  // tabs get their timers slowed by the browser anyway, to about once a minute.)
  const liveRefresh = async (why = "tick") => {
    R.reasons[why] = (R.reasons[why] || 0) + 1;
    if (refreshing && now() - refreshing < 120) { R.busy++; return; }
    const mine = refreshing = now(), prev = S.lastRefresh;
    try {
      await refreshAll();
      const gap = now() - prev;
      if (prev && gap > 180) report(`data was ${Math.round(gap / 60)} min old, refreshed by "${why}"`);
      Object.assign(R, { busy: 0, fails: 0, lastErr: "", reasons: {} });
    } catch (e) {
      R.fails++; R.lastErr = String(e && e.message || e).slice(0, 200);
      if (R.fails === 3) report("3 refreshes in a row failed");
    } finally { if (refreshing === mine) refreshing = 0; }
    updateBanner();
  };
  // Judge by the wall clock, not by counting ticks: timers are throttled or paused while the PC sleeps
  // or the tab is in the background, so after a wake the first tick refreshes straight away.
  setInterval(() => { if (now() - S.lastRefresh >= 30) liveRefresh("tick"); else updateBanner(); }, 10000);
  addEventListener("online", () => liveRefresh("online"));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { liveRefresh("visible"); loadStatus().catch(() => {}); } });
  addEventListener("focus", () => liveRefresh("focus"));
  addEventListener("pageshow", (e) => { if (e.persisted) liveRefresh("pageshow"); });   // back/forward cache on iOS
  setInterval(() => loadStatus().catch(() => {}), 20000);
}

/* ---------------- user pages (v2) ----------------
   The same page, opened through a user page link (/v/<token>/): the server only answers for the people
   chosen for it, and refuses tracking, settings and accounts. Here we just take away the controls that
   would only ever get "not available" back, and give the Alerts tab the user's own box. */
function setupViewer(v) {
  S.viewer = v;
  $("#app").classList.add("viewer");
  $(".brand-text").textContent = `Family Locator · ${v.name}`;
  document.title = `Family Locator · ${v.name}`;
  for (const sel of ['[data-tab="setup"]', "#tab-setup", "#logoutBtn", "#chips", "#rulesPanel"]) { const el = $(sel); if (el) el.remove(); }
  const sh = $("#showHidden"); if (sh && sh.closest("label")) sh.closest("label").remove();
  $$('#evKind option').forEach((o) => { if (!["", "enter", "exit"].includes(o.value)) o.remove(); });
  $("#viewerPanel").classList.remove("hidden");
}
async function loadMe() {
  const me = await api("/api/me");
  $("#vpEmail").textContent = me.email ? `Arrivals and departures at your places are emailed to ${me.email}. Add places on the Places tab.`
    : "No email address is set for this page, so alerts are only listed here. Ask the person who sent you the link to add one.";
  $("#vpDigest").value = me.digest ? "1" : "0";
  $("#vpDigest").disabled = !me.email;
}
$("#vpDigest").onchange = (e) => busy(e.target, async () => { await api("/api/me", { method: "PUT", body: { digest: e.target.value === "1" } }); toast(e.target.value === "1" ? "Daily email on." : "Daily email off."); });
$("#vpPreview").onclick = (e) => busy(e.target, async () => { const r = await api("/api/me/digest"); $("#vpBox").textContent = `${r.subject}\n\n${r.body}`; $("#vpBox").classList.remove("hidden"); });

(async () => {
  const st = await api("/api/auth/state");
  if (st.viewer) setupViewer(st.viewer);
  if (st.authed) start(); else showAuth();
})();
