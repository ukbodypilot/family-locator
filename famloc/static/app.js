/* Family Locator UI - vanilla JS + Leaflet. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("famloc." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("famloc." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const H = 3600, D = 86400, GAP_BREAK = 6 * H;

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  if (r.status === 401 && !path.startsWith("/api/auth")) { showAuth(); throw new Error("auth required"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || (data && data.error) || r.statusText);
  return data;
}

function toast(msg, ms = 3000) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

/* ---------------- time formatting ---------------- */
const fmtDT = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtT = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const fmtD = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
const dt = (ts) => (ts ? fmtDT.format(new Date(ts * 1000)) : "—");
function ago(ts) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < H) return `${Math.round(s / 60)} min ago`;
  if (s < 2 * D) return `${(s / H).toFixed(s < 10 * H ? 1 : 0)} h ago`;
  return `${Math.round(s / D)} d ago`;
}
function dur(min) {
  if (min < 60) return `${Math.round(min)} min`;
  if (min < 48 * 60) return `${(min / 60).toFixed(1)} h`;
  return `${(min / 1440).toFixed(1)} d`;
}
const toLocalInput = (ts) => { const d = new Date(ts * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
const fromLocalInput = (v) => new Date(v).getTime() / 1000;
const now = () => Date.now() / 1000;

/* ---------------- auth ---------------- */
let authMode = "login";
async function showAuth() {
  const st = await api("/api/auth/state");
  $("#app").classList.add("hidden"); $("#auth").classList.remove("hidden");
  authMode = st.password_set ? "login" : "setup";
  $("#authMsg").textContent = st.password_set ? "Sign in" : "First run: choose a password for this web UI (8+ characters).";
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
      if (pw !== $("#authPw2").value) throw new Error("passwords differ");
      await api("/api/auth/setup", { method: "POST", body: { password: pw } });
    } else await api("/api/auth/login", { method: "POST", body: { password: pw } });
    $("#authPw").value = $("#authPw2").value = "";
    $("#auth").classList.add("hidden"); start();
  } catch (err) { $("#authErr").textContent = err.message; }
});
$("#logoutBtn").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.reload(); };

/* ---------------- map + base layers ---------------- */
const map = L.map("map", { zoomControl: false, preferCanvas: true, worldCopyJump: true }).setView(store.get("view", [39.5, -98.35]), store.get("zoom", 4));
L.control.zoom({ position: "topright" }).addTo(map);
L.control.scale({ position: "bottomright", imperial: true }).addTo(map);
const canvasR = L.canvas({ padding: 0.3 });

const osmAttr = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const esriAttr = "Tiles &copy; Esri &mdash; Esri, Maxar, Earthstar Geographics, USDA, USGS, AeroGRID, IGN, GIS User Community";
const esri = (svc) => `https://server.arcgisonline.com/ArcGIS/rest/services/${svc}/MapServer/tile/{z}/{y}/{x}`;
const BASES = {
  "Streets (OSM)": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: osmAttr }),
  "Streets (Esri)": L.tileLayer(esri("World_Street_Map"), { maxZoom: 19, attribution: esriAttr }),
  "Light grey": L.layerGroup([
    L.tileLayer(esri("Canvas/World_Light_Gray_Base"), { maxZoom: 16, maxNativeZoom: 16, attribution: esriAttr }),
    L.tileLayer(esri("Canvas/World_Light_Gray_Reference"), { maxZoom: 16, maxNativeZoom: 16 }),
  ]),
  "Dark grey": L.layerGroup([
    L.tileLayer(esri("Canvas/World_Dark_Gray_Base"), { maxZoom: 16, maxNativeZoom: 16, attribution: esriAttr }),
    L.tileLayer(esri("Canvas/World_Dark_Gray_Reference"), { maxZoom: 16, maxNativeZoom: 16 }),
  ]),
  "Satellite": L.tileLayer(esri("World_Imagery"), { maxZoom: 19, attribution: esriAttr }),
  "Hybrid (satellite + labels)": L.layerGroup([
    L.tileLayer(esri("World_Imagery"), { maxZoom: 19, attribution: esriAttr }),
    L.tileLayer(esri("Reference/World_Boundaries_and_Places"), { maxZoom: 19 }),
    L.tileLayer(esri("Reference/World_Transportation"), { maxZoom: 19, opacity: 0.8 }),
  ]),
  "Topographic (Esri)": L.tileLayer(esri("World_Topo_Map"), { maxZoom: 19, attribution: esriAttr }),
  "Terrain (OpenTopoMap)": L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, attribution: osmAttr + ', SRTM | &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)' }),
  "Cycling (CyclOSM)": L.tileLayer("https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png", { maxZoom: 20, attribution: osmAttr + ' | <a href="https://www.cyclosm.org">CyclOSM</a>' }),
  "Humanitarian": L.tileLayer("https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png", { maxZoom: 19, attribution: osmAttr + " | Humanitarian OSM Team" }),
};
const darkPref = matchMedia("(prefers-color-scheme: dark)").matches;
let baseName = store.get("base", darkPref ? "Dark grey" : "Streets (OSM)");
if (!BASES[baseName]) baseName = "Streets (OSM)";
BASES[baseName].addTo(map);

const L_tracks = L.layerGroup().addTo(map);
const L_points = L.layerGroup();
const L_acc = L.layerGroup().addTo(map);
const L_heat = L.heatLayer([], { radius: 18, blur: 16, minOpacity: 0.3, maxZoom: 17 });
const L_stays = L.layerGroup().addTo(map);
const L_places = L.layerGroup().addTo(map);
const L_photos = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 45 });
const L_now = L.layerGroup().addTo(map);
const L_search = L.layerGroup().addTo(map);
const OVER = {
  "Current positions": L_now, "Tracks": L_tracks, "Accuracy circles": L_acc, "All fixes (dots)": L_points,
  "Heatmap": L_heat, "Stays (Insights)": L_stays, "Places / geofences": L_places, "Photo locations": L_photos,
};
const overOn = store.get("overlays", null);
if (overOn) for (const [n, l] of Object.entries(OVER)) { if (overOn.includes(n)) l.addTo(map); else map.removeLayer(l); }
else L_photos.addTo(map);
L.control.layers(BASES, OVER, { position: "topright", collapsed: true }).addTo(map);
map.on("baselayerchange", (e) => store.set("base", e.name));
const saveOver = () => store.set("overlays", Object.entries(OVER).filter(([, l]) => map.hasLayer(l)).map(([n]) => n));
map.on("overlayadd overlayremove", () => { saveOver(); render(); });
map.on("moveend", () => { const c = map.getCenter(); store.set("view", [c.lat, c.lng]); store.set("zoom", map.getZoom()); });

/* ---------------- state ---------------- */
const S = {
  devices: [], byId: {}, fences: [], data: {}, start: 0, end: 0, t: 0, preset: store.get("preset", "live"),
  playing: false, firstFit: true, picking: false, insights: null, showHidden: false,
};

const isPhotos = (d) => d && d.source === "photos";
const visibleDevs = () => S.devices.filter((d) => S.showHidden || !d.hidden);
const initials = (d) => (d.owner && d.owner !== "Me" ? d.owner : d.name).split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
const devIcon = { iPhone: "📱", iPad: "▭", Mac: "💻", Watch: "⌚", AirPods: "🎧", AirTag: "◉", Photos: "📷" };
const classIcon = (d) => { for (const k in devIcon) if ((d.device_class || "").includes(k) || (d.model || "").includes(k)) return devIcon[k]; return "•"; };

/* ---------------- time range ---------------- */
function presetRange(p, first) {
  const n = now();
  const span = { "1h": H, "6h": 6 * H, "24h": D, "live": D, "7d": 7 * D, "30d": 30 * D, "1y": 365 * D }[p];
  if (span) return [n - span, n];
  return [first || n - 30 * D, n];
}
async function setPreset(p) {
  S.preset = p; store.set("preset", p);
  $$("#presets button").forEach((b) => b.classList.toggle("active", b.dataset.p === p));
  let first;
  if (p === "all") first = (await api("/api/range")).first;
  [S.start, S.end] = presetRange(p, first);
  S.t = S.end;
  await loadPoints();
}
$$("#presets button").forEach((b) => (b.onclick = () => { stopPlay(); setPreset(b.dataset.p); }));
$("#tApply").onclick = () => {
  const a = fromLocalInput($("#tFrom").value), b = fromLocalInput($("#tTo").value);
  if (!(a < b)) return toast("From must be before To");
  stopPlay(); S.preset = "custom"; $$("#presets button").forEach((x) => x.classList.remove("active"));
  S.start = a; S.end = b; S.t = b; loadPoints();
};
function shift(dir) {
  const span = S.end - S.start; stopPlay();
  S.preset = "custom"; $$("#presets button").forEach((x) => x.classList.remove("active"));
  S.start += dir * span; S.end += dir * span; S.t = S.end; loadPoints();
}
$("#tPrev").onclick = () => shift(-1);
$("#moreBtn").onclick = () => $("#timebar").classList.toggle("more");
$("#tNext").onclick = () => shift(1);

const scrub = $("#scrub");
scrub.addEventListener("input", () => { S.t = S.start + (scrub.value / 1000) * (S.end - S.start); if (S.preset === "live" && scrub.value < 1000) markCustomCursor(); schedRender(); });
function markCustomCursor() { /* scrubbing in live keeps range but freezes cursor until refresh */ }

/* ---------------- playback ---------------- */
let lastFrame = 0;
function stopPlay() { S.playing = false; $("#playIco").innerHTML = '<path d="M7 5l12 7-12 7z"/>'; }
$("#playBtn").onclick = () => {
  if (S.playing) return stopPlay();
  if (S.t >= S.end - 1) S.t = S.start;
  S.playing = true; $("#playIco").innerHTML = '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';
  lastFrame = performance.now(); requestAnimationFrame(tick);
};
function tick(ts) {
  if (!S.playing) return;
  const dtS = (ts - lastFrame) / 1000; lastFrame = ts;
  S.t = Math.min(S.end, S.t + dtS * Number($("#speed").value));
  render();
  if (S.t >= S.end) return stopPlay();
  requestAnimationFrame(tick);
}

/* ---------------- loading ---------------- */
async function loadDevices() {
  S.devices = await api("/api/devices");
  S.byId = Object.fromEntries(S.devices.map((d) => [d.id, d]));
  renderDeviceList(); fillDeviceSelects();
}
async function loadFences() { S.fences = await api("/api/geofences"); renderPlaces(); }
async function loadPoints() {
  $("#tFrom").value = toLocalInput(S.start); $("#tTo").value = toLocalInput(S.end);
  const res = await api(`/api/points?start=${S.start}&end=${S.end}`);
  S.data = res.points;
  if (res.truncated) toast("Range has too many points; showing the first 200k. Narrow the range.");
  drawHisto(); buildPhotos(); render(true);
  if (S.firstFit) { S.firstFit = false; fitAll(); }
  if (!$('[data-body="insights"]').classList.contains("hidden")) loadInsights();
}
function fitAll() {
  const ll = [];
  for (const d of visibleDevs()) { if (isPhotos(d)) continue; const p = lastAt(d.id, S.t); if (p) ll.push([p[1], p[2]]); }
  if (!ll.length) for (const d of visibleDevs()) if (d.last) ll.push([d.last.lat, d.last.lon]);
  if (ll.length === 1) map.setView(ll[0], 15); else if (ll.length) map.fitBounds(ll, { padding: [60, 60], maxZoom: 15 });
}

/* binary search: last point with ts <= t */
function idxAt(arr, t) {
  let lo = 0, hi = arr.length - 1, r = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (arr[m][0] <= t) { r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}
function lastAt(id, t) { const a = S.data[id]; if (!a || !a.length) return null; const i = idxAt(a, t); return i >= 0 ? a[i] : null; }

/* ---------------- histogram ---------------- */
function drawHisto() {
  const c = $("#histo"), w = c.clientWidth, h = c.clientHeight, dpr = devicePixelRatio || 1;
  c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
  const bins = Math.max(20, Math.floor(w / 4)), counts = new Array(bins).fill(0), span = S.end - S.start || 1;
  const perDev = [];
  for (const d of visibleDevs()) {
    const a = S.data[d.id]; if (!a) continue;
    const cs = new Array(bins).fill(0);
    for (const p of a) { const b = Math.min(bins - 1, Math.floor(((p[0] - S.start) / span) * bins)); if (b >= 0) { cs[b]++; counts[b]++; } }
    perDev.push([d.color, cs]);
  }
  const max = Math.max(1, ...counts), bw = w / bins;
  // stacked per-device bars so you can see who was moving when
  const base = new Array(bins).fill(0);
  for (const [color, cs] of perDev) {
    g.fillStyle = color; g.globalAlpha = 0.75;
    for (let i = 0; i < bins; i++) if (cs[i]) {
      const bh = (Math.sqrt(cs[i] + base[i]) / Math.sqrt(max)) * (h - 4) - (Math.sqrt(base[i]) / Math.sqrt(max)) * (h - 4);
      const y = h - 2 - (Math.sqrt(base[i]) / Math.sqrt(max)) * (h - 4) - bh;
      g.fillRect(i * bw + 0.5, y, Math.max(1, bw - 1), bh); base[i] += cs[i];
    }
  }
  g.globalAlpha = 1;
  // day ticks
  g.fillStyle = getComputedStyle(document.body).getPropertyValue("--muted"); g.font = "10px system-ui";
  const steps = [H, 3 * H, 6 * H, 12 * H, D, 2 * D, 7 * D, 14 * D, 30 * D, 91 * D, 365 * D];
  const tickStep = steps.find((st) => (st / span) * w >= 64) || 365 * D;
  const tz = new Date().getTimezoneOffset() * 60; // align day ticks to local midnight
  const first = Math.ceil((S.start - tz) / tickStep) * tickStep + tz;
  for (let t = first; t < S.end; t += tickStep) {
    const x = ((t - S.start) / span) * w; g.globalAlpha = 0.35; g.fillRect(x, 0, 1, h); g.globalAlpha = 0.8;
    g.fillText(tickStep < D ? fmtT.format(new Date(t * 1000)) : fmtD.format(new Date(t * 1000)), x + 3, 10);
  }
  g.globalAlpha = 1;
}
addEventListener("resize", () => drawHisto());

/* ---------------- render ---------------- */
let rafPending = false;
function schedRender() { if (!rafPending) { rafPending = true; requestAnimationFrame(() => { rafPending = false; render(); }); } }

function render(full = false) {
  const span = S.end - S.start || 1;
  scrub.value = Math.round(((S.t - S.start) / span) * 1000);
  $("#tNow").textContent = dt(S.t);
  $("#tSpan").textContent = `${fmtD.format(new Date(S.start * 1000))} → ${fmtD.format(new Date(S.end * 1000))} · ${dur(span / 60)}`;
  const trail = Number($("#trail").value);
  const from = trail ? Math.max(S.start, S.t - trail) : S.start;
  L_tracks.clearLayers(); L_now.clearLayers(); L_acc.clearLayers(); L_points.clearLayers();
  const heat = [], wantDots = map.hasLayer(L_points), wantHeat = map.hasLayer(L_heat);
  for (const d of visibleDevs()) {
    const a = S.data[d.id]; if (!a || !a.length) continue;
    const i1 = idxAt(a, S.t); if (i1 < 0) continue;
    let i0 = idxAt(a, from - 1e-6) + 1;
    if (isPhotos(d)) { if (wantHeat) for (let i = i0; i <= i1; i++) heat.push([a[i][1], a[i][2], 0.5]); continue; }
    // track: split into segments at long gaps
    let seg = [];
    const flush = () => { if (seg.length > 1) L.polyline(seg, { color: d.color, weight: 3.5, opacity: 0.85, renderer: canvasR, lineJoin: "round" }).addTo(L_tracks); seg = []; };
    for (let i = i0; i <= i1; i++) {
      const p = a[i];
      if (seg.length && p[0] - a[i - 1][0] > GAP_BREAK) flush();
      seg.push([p[1], p[2]]);
      if (wantHeat) heat.push([p[1], p[2], 0.6]);
      if (wantDots) L.circleMarker([p[1], p[2]], { radius: 3, color: d.color, weight: 1, fillOpacity: 0.7, renderer: canvasR })
        .bindPopup(() => pointPopup(d, p)).addTo(L_points);
    }
    flush();
    // current marker
    const p = a[i1];
    const stale = S.t - p[0] > 2 * H;
    const icon = L.divIcon({ className: "devmark" + (stale ? " stale" : ""), iconSize: [30, 30], iconAnchor: [15, 15],
      html: `<div class="b" style="background:${d.color}">${esc(classIcon(d) === "•" ? initials(d) : classIcon(d))}</div><div class="lbl">${esc(d.name)}</div>` });
    L.marker([p[1], p[2]], { icon, zIndexOffset: 1000, title: d.name }).bindPopup(() => pointPopup(d, p, true)).addTo(L_now);
    if (p[3] && p[3] > 15) L.circle([p[1], p[2]], { radius: p[3], color: d.color, weight: 1, opacity: 0.5, fillOpacity: 0.08, interactive: false }).addTo(L_acc);
  }
  if (wantHeat) L_heat.setLatLngs(heat);
  if (full) renderDeviceList();
}

function pointPopup(d, p, current = false) {
  const [ts, lat, lon, acc, batt, kind, meta] = p;
  const rows = [
    ["When", `${dt(ts)} <span class="muted">(${ago(ts)})</span>`],
    ["Accuracy", acc != null ? `±${Math.round(acc)} m` : "—"],
    batt != null ? ["Battery", `${Math.round(batt * 100)}%`] : null,
    kind ? ["Source", esc(kind)] : null,
    meta && meta.file ? ["Photo", esc(String(meta.file).split("/").pop())] : null,
    ["Position", `<code>${lat.toFixed(5)}, ${lon.toFixed(5)}</code>`],
  ].filter(Boolean);
  const el = document.createElement("div"); el.className = "pop";
  el.innerHTML = `<h4>${esc(d.name)}${d.owner ? ` <span class="muted">· ${esc(d.owner)}</span>` : ""}</h4>
    <table>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>
    <div class="acts"><a data-a="here">What's here?</a><a data-a="place">Make a place</a><a data-a="ins">Insights</a>
    ${current && d.source === "icloud" ? '<a data-a="locate">Locate this device</a>' : ""}
    <a href="https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}" target="_blank" rel="noopener">Directions</a></div>`;
  el.addEventListener("click", async (e) => {
    const a = e.target.dataset.a; if (!a) return;
    if (a === "here") whatsHere(lat, lon);
    if (a === "place") newPlace(lat, lon);
    if (a === "ins") { $("#insDevice").value = d.id; openTab("insights"); }
    if (a === "locate") locateOne(d);
  });
  return el;
}

/* photos: clustered, built once per load */
function buildPhotos() {
  L_photos.clearLayers();
  for (const d of visibleDevs()) {
    if (!isPhotos(d) || !S.data[d.id]) continue;
    const ms = S.data[d.id].map((p) => L.marker([p[1], p[2]], { icon: L.divIcon({ className: "photomark", iconSize: [10, 10] }) }).bindPopup(() => pointPopup(d, p)));
    L_photos.addLayers(ms);
  }
}

/* ---------------- device list ---------------- */
function deviceRow(d) {
  const p = lastAt(d.id, S.t) || (d.last ? [d.last.ts, d.last.lat, d.last.lon, d.last.acc] : null);
  const b = d.battery != null ? Math.round(d.battery * 100) : null;
  const canLocate = d.source === "icloud" && (d.meta || {}).locationCapable !== false;
  const row = document.createElement("div");
  row.className = "dev" + (d.hidden ? " off" : "");
  row.innerHTML = `<span class="dot" style="background:${d.color}"></span>
    <span class="nm" title="${esc(d.model || "")}">${esc(classIcon(d))} ${esc(d.name)}</span>
    <span class="tools">${canLocate ? '<button data-a="locate" title="Locate this device now">⟳</button>' : ""}
      <button data-a="color" title="Colour">●</button><button data-a="rename" title="Rename">✎</button>
      <button data-a="hide" title="${d.hidden ? "Show" : "Hide"}">${d.hidden ? "◌" : "◉"}</button></span>
    <span class="sub"><span>${p ? ago(p[0]) : "no location"}</span>
      ${p && p[3] ? `<span>±${Math.round(p[3])} m</span>` : ""}
      ${b != null ? `<span class="batt ${b <= 20 ? "low" : ""}">${b}%${d.battery_status === "Charging" ? "⚡" : ""}</span>` : ""}
      ${d.n_points ? `<span>${d.n_points} fixes</span>` : ""}${d.model ? `<span>${esc(d.model)}</span>` : ""}</span>`;
  row.onclick = async (e) => {
    const a = e.target.dataset.a;
    if (a === "locate") return locateOne(d, e.target);
    if (a === "hide") { await api(`/api/devices/${encodeURIComponent(d.id)}`, { method: "PATCH", body: { hidden: d.hidden ? 0 : 1 } }); await loadDevices(); render(); drawHisto(); return; }
    if (a === "rename") { const n = prompt("Display name", d.name); if (n) { await api(`/api/devices/${encodeURIComponent(d.id)}`, { method: "PATCH", body: { name: n } }); await loadDevices(); render(); } return; }
    if (a === "color") { const inp = document.createElement("input"); inp.type = "color"; inp.value = d.color; inp.onchange = async () => { await api(`/api/devices/${encodeURIComponent(d.id)}`, { method: "PATCH", body: { color: inp.value } }); await loadDevices(); render(); drawHisto(); }; inp.click(); return; }
    if (p) { map.setView([p[1], p[2]], Math.max(map.getZoom(), 15)); }
    else toast(`${d.name}: Apple has no location for this device (offline or location off)`);
    if (innerWidth < 820) $("#app").classList.add("noside");
  };
  return row;
}
function renderDeviceList() {
  const el = $("#deviceList"); el.innerHTML = "";
  const groups = {};
  for (const d of S.devices) { if (d.hidden && !S.showHidden) continue; (groups[d.owner || (isPhotos(d) ? "History" : "Items")] ||= []).push(d); }
  if (!S.devices.length) el.innerHTML = '<p class="muted">No devices yet. Sign in to iCloud on the <a href="#settings" onclick="openTab(\'settings\')">Settings</a> tab.</p>';
  const openNoFix = store.get("openNoFix", []);
  for (const [owner, devs] of Object.entries(groups)) {
    el.insertAdjacentHTML("beforeend", `<div class="owner-h">${esc(owner)}</div>`);
    const withFix = devs.filter((d) => d.last).sort((a, b) => b.last.ts - a.last.ts);
    const noFix = devs.filter((d) => !d.last);
    for (const d of withFix) el.appendChild(deviceRow(d));
    if (noFix.length) {
      const det = document.createElement("details"); det.className = "nofix"; det.open = openNoFix.includes(owner);
      det.innerHTML = `<summary>${noFix.length} without a location (offline / location off)</summary>`;
      det.ontoggle = () => { const s = new Set(store.get("openNoFix", [])); det.open ? s.add(owner) : s.delete(owner); store.set("openNoFix", [...s]); };
      for (const d of noFix) det.appendChild(deviceRow(d));
      el.appendChild(det);
    }
  }
}
async function locateOne(d, btn) {
  if (btn) btn.classList.add("spin");
  toast(`Locating ${d.name}… (up to ~40 s)`, 40000);
  try {
    const r = await api(`/api/icloud/locate/${encodeURIComponent(d.id)}`, { method: "POST" });
    await refreshAll();
    toast(r.updated ? `${d.name}: new fix from ${ago(r.fix_ts)}` : `${d.name} didn't answer with a new fix (asleep, offline or no signal)`, 6000);
    const n = S.byId[d.id];
    if (r.updated && n && n.last) map.setView([n.last.lat, n.last.lon], Math.max(map.getZoom(), 15));
  } catch (e) { toast(e.message, 6000); }
  finally { if (btn) btn.classList.remove("spin"); }
}
$("#showHidden").onchange = (e) => { S.showHidden = e.target.checked; renderDeviceList(); render(); drawHisto(); };
function fillDeviceSelects() {
  const sel = $("#insDevice"), cur = sel.value;
  sel.innerHTML = S.devices.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}${d.owner ? " · " + esc(d.owner) : ""}</option>`).join("");
  if (cur && S.byId[cur]) sel.value = cur;
}

/* ---------------- tabs ---------------- */
function openTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-body").forEach((s) => s.classList.toggle("hidden", s.dataset.body !== name));
  $("#app").classList.remove("noside");
  if (name === "alerts") loadEvents();
  if (name === "settings") { loadStatus(); loadConfigForm(); }
  if (name === "insights") loadInsights();
  if (name !== "insights") L_stays.clearLayers();
  history.replaceState(null, "", "#" + name);
}
window.openTab = openTab;
$$("#tabs button").forEach((b) => (b.onclick = () => openTab(b.dataset.tab)));
$("#sideToggle").onclick = () => { $("#app").classList.toggle("noside"); setTimeout(() => { map.invalidateSize(); drawHisto(); }, 50); };

/* ---------------- places / geofences ---------------- */
let placePreview = null;
function renderPlaces() {
  L_places.clearLayers();
  const el = $("#placeList"); el.innerHTML = S.fences.length ? "" : '<p class="muted small">No places yet.</p>';
  for (const g of S.fences) {
    L.circle([g.lat, g.lon], { radius: g.radius, color: g.color, weight: 2, fillOpacity: 0.1, dashArray: "6 4" })
      .bindTooltip(g.name, { permanent: false, direction: "top" }).on("click", () => editPlace(g)).addTo(L_places);
    const inside = g.inside.map((id) => S.byId[id]?.name).filter(Boolean);
    const row = document.createElement("div"); row.className = "place";
    row.innerHTML = `<span class="ring" style="border-color:${g.color}"></span><div><b>${esc(g.name)}</b>
      <div class="sub">${Math.round(g.radius)} m · ${g.on_enter ? "arrive" : ""}${g.on_enter && g.on_exit ? " + " : ""}${g.on_exit ? "leave" : ""} alerts
      ${g.devices.length ? ` · ${g.devices.length} device(s)` : ""}${inside.length ? `<br>Here now: ${esc(inside.join(", "))}` : ""}</div></div>`;
    row.onclick = () => { map.setView([g.lat, g.lon], 16); editPlace(g); };
    el.appendChild(row);
  }
}
function showPlaceForm(g) {
  $("#placeForm").classList.remove("hidden");
  $("#pfId").value = g.id || ""; $("#pfName").value = g.name || ""; $("#pfLat").value = g.lat?.toFixed(6) ?? "";
  $("#pfLon").value = g.lon?.toFixed(6) ?? ""; $("#pfRadius").value = g.radius || 150; $("#pfColor").value = g.color || "#2563eb";
  $("#pfEnter").checked = g.on_enter ?? true; $("#pfExit").checked = g.on_exit ?? true;
  $("#pfDelete").classList.toggle("hidden", !g.id);
  $("#pfDevices").innerHTML = S.devices.filter((d) => !isPhotos(d)).map((d) =>
    `<label class="mini"><input type="checkbox" value="${esc(d.id)}" ${(g.devices || []).includes(d.id) ? "checked" : ""}> ${esc(d.name)}${d.owner ? " · " + esc(d.owner) : ""}</label>`).join("");
  updatePreview(); $("#pfName").focus();
}
function editPlace(g) { openTab("places"); showPlaceForm(g); }
function newPlace(lat, lon) { openTab("places"); showPlaceForm({ lat, lon }); stopPicking(); }
function updatePreview() {
  const lat = parseFloat($("#pfLat").value), lon = parseFloat($("#pfLon").value), r = Number($("#pfRadius").value);
  $("#pfRadiusVal").textContent = r >= 1000 ? `${(r / 1000).toFixed(2)} km` : `${r} m`;
  if (placePreview) { map.removeLayer(placePreview.c); map.removeLayer(placePreview.m); placePreview = null; }
  if (isNaN(lat) || isNaN(lon)) return;
  const c = L.circle([lat, lon], { radius: r, color: $("#pfColor").value, weight: 2, fillOpacity: 0.15 }).addTo(map);
  const m = L.marker([lat, lon], { draggable: true, title: "Drag to move" }).addTo(map);
  m.on("drag", (e) => { c.setLatLng(e.target.getLatLng()); });
  m.on("dragend", (e) => { const ll = e.target.getLatLng(); $("#pfLat").value = ll.lat.toFixed(6); $("#pfLon").value = ll.lng.toFixed(6); });
  placePreview = { c, m };
}
["pfLat", "pfLon", "pfRadius", "pfColor"].forEach((id) => $("#" + id).addEventListener("input", updatePreview));
function closePlaceForm() { $("#placeForm").classList.add("hidden"); if (placePreview) { map.removeLayer(placePreview.c); map.removeLayer(placePreview.m); placePreview = null; } stopPicking(); }
$("#pfCancel").onclick = closePlaceForm;
$("#placeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { name: $("#pfName").value, lat: +$("#pfLat").value, lon: +$("#pfLon").value, radius: +$("#pfRadius").value,
    color: $("#pfColor").value, on_enter: $("#pfEnter").checked, on_exit: $("#pfExit").checked,
    devices: $$("#pfDevices input:checked").map((i) => i.value) };
  const id = $("#pfId").value;
  await api(id ? `/api/geofences/${id}` : "/api/geofences", { method: id ? "PUT" : "POST", body });
  closePlaceForm(); await loadFences(); toast("Place saved");
});
$("#pfDelete").onclick = async () => {
  if (!confirm("Delete this place?")) return;
  await api(`/api/geofences/${$("#pfId").value}`, { method: "DELETE" }); closePlaceForm(); loadFences();
};
function stopPicking() { S.picking = false; $("#app").classList.remove("picking"); }
$("#addPlaceBtn").onclick = () => { closePlaceForm(); S.picking = true; $("#app").classList.add("picking"); toast("Click the map to place the centre"); };
map.on("click", (e) => { if (S.picking) newPlace(e.latlng.lat, e.latlng.lng); hideCtx(); });

/* ---------------- context menu ---------------- */
function hideCtx() { $("#ctxMenu").classList.add("hidden"); }
map.on("contextmenu", (e) => {
  const { lat, lng } = e.latlng, m = $("#ctxMenu");
  m.innerHTML = `<div class="cap">${lat.toFixed(5)}, ${lng.toFixed(5)}</div><div data-a="here">What's here?</div>
    <div data-a="place">Create place here</div><div data-a="copy">Copy coordinates</div><div data-a="center">Centre map here</div>`;
  const pt = e.containerPoint, wrap = $(".mapwrap").getBoundingClientRect();
  m.style.left = Math.min(pt.x, wrap.width - 200) + "px"; m.style.top = Math.min(pt.y, wrap.height - 170) + "px";
  m.classList.remove("hidden");
  m.onclick = (ev) => {
    const a = ev.target.dataset.a; if (!a) return; hideCtx();
    if (a === "here") whatsHere(lat, lng);
    if (a === "place") newPlace(lat, lng);
    if (a === "copy") navigator.clipboard?.writeText(`${lat.toFixed(6)}, ${lng.toFixed(6)}`).then(() => toast("Copied"), () => toast(`${lat.toFixed(6)}, ${lng.toFixed(6)}`));
    if (a === "center") map.panTo([lat, lng]);
  };
});
async function whatsHere(lat, lon) {
  try {
    const r = await api(`/api/reverse?lat=${lat}&lon=${lon}`);
    L.popup().setLatLng([lat, lon]).setContent(`<div class="pop"><b>${esc(r.name || "Unknown")}</b><br><code>${lat.toFixed(5)}, ${lon.toFixed(5)}</code></div>`).openOn(map);
  } catch (e) { toast(e.message); }
}

/* ---------------- search ---------------- */
const sInput = $("#search"), sBox = $("#searchResults");
let sTimer = null, sItems = [], sSel = -1;
function localMatches(q) {
  const lq = q.toLowerCase(), out = [];
  for (const d of S.devices) if (`${d.name} ${d.owner || ""} ${d.model || ""}`.toLowerCase().includes(lq)) {
    const p = d.last ? [d.last.ts, d.last.lat, d.last.lon] : lastAt(d.id, S.end);
    if (p) out.push({ g: "People & devices", name: `${d.name}${d.owner ? " · " + d.owner : ""}`, lat: p[1], lon: p[2], z: 16, sub: ago(p[0]) });
  }
  for (const g of S.fences) if (g.name.toLowerCase().includes(lq)) out.push({ g: "Places", name: g.name, lat: g.lat, lon: g.lon, z: 16, sub: `${Math.round(g.radius)} m` });
  const m = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (m && Math.abs(+m[1]) <= 90 && Math.abs(+m[2]) <= 180) out.unshift({ g: "Coordinates", name: `${(+m[1]).toFixed(5)}, ${(+m[2]).toFixed(5)}`, lat: +m[1], lon: +m[2], z: 16, pin: true });
  return out;
}
function showResults(items, loading = false) {
  sItems = items; sSel = -1;
  if (!items.length && !loading) { sBox.innerHTML = '<div class="sh">No matches. Press Enter to search addresses.</div>'; sBox.classList.remove("hidden"); return; }
  let html = "", lastG = "";
  items.forEach((it, i) => { if (it.g !== lastG) { html += `<div class="sh">${esc(it.g)}</div>`; lastG = it.g; } html += `<div class="sr" data-i="${i}"><span>${esc(it.name)}</span>${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</div>`; });
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
  sBox.classList.add("hidden"); L_search.clearLayers();
  if (it.bbox && it.bbox.length === 4) map.fitBounds([[it.bbox[0], it.bbox[2]], [it.bbox[1], it.bbox[3]]], { maxZoom: 17 });
  else map.setView([it.lat, it.lon], it.z || 16);
  if (it.pin) {
    const el = document.createElement("div"); el.className = "pop";
    el.innerHTML = `<b>${esc(it.name)}</b><div class="acts"><a data-a="place">Make a place</a></div>`;
    el.onclick = (e) => { if (e.target.dataset.a === "place") newPlace(it.lat, it.lon); };
    L.marker([it.lat, it.lon]).bindPopup(el).addTo(L_search).openPopup();
  }
}
sInput.addEventListener("input", () => {
  const q = sInput.value.trim(); clearTimeout(sTimer);
  if (!q) { sBox.classList.add("hidden"); L_search.clearLayers(); return; }
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
$("#insDevice").onchange = loadInsights;
async function loadInsights() {
  const id = $("#insDevice").value; const body = $("#insBody");
  L_stays.clearLayers();
  if (!id) { body.innerHTML = '<p class="muted">No devices yet.</p>'; return; }
  body.innerHTML = '<p class="muted">Analysing…</p>';
  const r = await api(`/api/analysis?device=${encodeURIComponent(id)}&start=${S.start}&end=${S.end}`);
  S.insights = r;
  const d = S.byId[id]; const color = d?.color || "#2563eb";
  const maxKm = Math.max(0.01, ...r.per_day_km.map((x) => x.km));
  const stayTime = r.stays.reduce((a, s) => a + s.minutes, 0);
  body.innerHTML = `
    <div class="stat-grid">
      <div class="stat"><b>${r.km}</b><span>km travelled</span></div>
      <div class="stat"><b>${r.trips.length}</b><span>trips</span></div>
      <div class="stat"><b>${r.stays.length}</b><span>stays</span></div>
      <div class="stat"><b>${r.points}</b><span>fixes</span></div>
      <div class="stat"><b>${dur(stayTime)}</b><span>time at stays</span></div>
      <div class="stat"><b>${r.farthest_from_start_km}</b><span>km max from start</span></div>
    </div>
    ${r.per_day_km.length > 1 ? `<h3>Distance per day</h3><div class="bars">${r.per_day_km.map((x) => `<i title="${x.day}: ${x.km} km" style="height:${(x.km / maxKm) * 100}%;background:${color}"></i>`).join("")}</div>
      <div class="row between small muted"><span>${r.per_day_km[0].day}</span><span>${r.per_day_km.at(-1).day}</span></div>` : ""}
    <h3>Top places</h3><div class="ins-list" id="insPlaces">${r.top_places.map((p, i) => `<div data-i="${i}"><span>${esc(p.name)}</span><span class="muted">${p.visits}× · ${dur(p.hours * 60)}</span></div>`).join("") || '<span class="muted">none</span>'}</div>
    <h3>Stays</h3><div class="ins-list" id="insStays">${r.stays.slice().reverse().map((s, i) => `<div data-i="${r.stays.length - 1 - i}"><span>${esc(s.place || `${s.lat.toFixed(4)}, ${s.lon.toFixed(4)}`)}</span><span class="muted">${fmtDT.format(new Date(s.start * 1000))} · ${dur(s.minutes)}</span></div>`).join("") || '<span class="muted">none</span>'}</div>
    <h3>Trips</h3><div class="ins-list" id="insTrips">${r.trips.slice().reverse().map((t, i) => `<div data-i="${r.trips.length - 1 - i}"><span>${t.km} km · ${dur(t.minutes)}${t.avg_kmh ? ` · ${t.avg_kmh} km/h` : ""}</span><span class="muted">${fmtDT.format(new Date(t.start * 1000))}</span></div>`).join("") || '<span class="muted">none</span>'}</div>`;
  for (const s of r.stays) L.circleMarker([s.lat, s.lon], { radius: Math.min(22, 5 + Math.sqrt(s.minutes)), color, weight: 2, fillOpacity: 0.25 })
    .bindTooltip(`${esc(s.place || "Stay")}<br>${dt(s.start)}<br>${dur(s.minutes)}`).addTo(L_stays);
  $("#insPlaces").onclick = (e) => { const x = e.target.closest("[data-i]"); if (x) { const p = r.top_places[+x.dataset.i]; map.setView([p.lat, p.lon], 16); } };
  $("#insStays").onclick = (e) => { const x = e.target.closest("[data-i]"); if (x) { const s = r.stays[+x.dataset.i]; map.setView([s.lat, s.lon], 16); stopPlay(); S.t = s.end; render(); } };
  $("#insTrips").onclick = (e) => {
    const x = e.target.closest("[data-i]"); if (!x) return; const t = r.trips[+x.dataset.i];
    const seg = (S.data[id] || []).filter((p) => p[0] >= t.start && p[0] <= t.end).map((p) => [p[1], p[2]]);
    map.fitBounds(seg.length > 1 ? seg : [t.from, t.to], { padding: [50, 50], maxZoom: 16 });
    stopPlay(); S.t = t.end; render();
  };
}

/* ---------------- events ---------------- */
$("#evKind").onchange = loadEvents;
async function loadEvents() {
  const ev = await api(`/api/events?limit=300&kind=${$("#evKind").value}`);
  $("#eventList").innerHTML = ev.map((e) => `<div class="ev"><span class="k ${esc(e.kind)}">${esc(e.kind)}</span><span>${esc(e.message).replace(/\n/g, "<br>")}</span>
    <span class="t">${dt(e.ts)}${e.emailed ? " · emailed" : e.email_error ? ` · email failed: ${esc(e.email_error)}` : ""}</span></div>`).join("") || '<p class="muted">No events yet.</p>';
}

/* ---------------- status / settings ---------------- */
const stateBadge = (s) => ({ ok: "ok", need_2fa: "warn", need_login: "bad", error: "bad" }[s] || "");
async function loadStatus() {
  const st = await api("/api/status");
  const ic = st.icloud, at = st.airtag;
  $("#chips").innerHTML = [["iCloud", ic.state], ["AirTags", at.state], ["Email", st.email.configured ? "ok" : "off"]]
    .map(([n, s]) => `<span class="chip ${stateBadge(s)}" onclick="openTab('settings')" title="${esc(s)}"><i></i>${n}</span>`).join("");
  $("#icState").textContent = ic.state; $("#icState").className = "badge " + stateBadge(ic.state);
  $("#icDetail").textContent = [ic.apple_id, ic.detail].filter(Boolean).join(" · ");
  $("#icLogin").classList.toggle("hidden", !["logged_out", "need_login"].includes(ic.state));
  $("#ic2fa").classList.toggle("hidden", ic.state !== "need_2fa");
  $("#icActions").classList.toggle("hidden", ic.state !== "ok");
  if (ic.apple_id && !$("#icId").value) $("#icId").value = ic.apple_id;
  $("#icStats").textContent = `last poll ${ago(ic.last_poll)} · last fresh locate ${ago(ic.last_locate)} · ${ic.devices} devices · ${ic.new_points} new fixes · ${ic.poll_errors} errors`;
  $("#atState").textContent = at.state; $("#atState").className = "badge " + stateBadge(at.state);
  $("#atDetail").textContent = at.detail;
  $("#atKeys").textContent = at.key_files.length ? at.key_files.join(", ") : "none";
  $("#atDir").textContent = at.tag_dir;
  $("#atLogin").classList.toggle("hidden", !["logged_out", "need_login"].includes(at.state));
  $("#at2fa").classList.toggle("hidden", at.state !== "need_2fa");
  $("#atActions").classList.toggle("hidden", at.state !== "ok");
  const ph = st.photos;
  $("#phStatus").textContent = ph.kind ? `${ph.running ? "Running" : "Last run"} (${ph.kind}): ${ph.scanned} scanned, ${ph.with_gps} with GPS, ${ph.new} new${ph.error ? " · error: " + ph.error : ""}` : "";
  $("#emState").textContent = st.email.configured ? "configured" : "not configured"; $("#emState").className = "badge " + (st.email.configured ? "ok" : "bad");
  $("#emDetail").textContent = st.email.configured ? `to ${st.email.recipient} · ${st.email.emails_sent} sent, ${st.email.emails_failed} failed` : "Set email_address / email_app_password in data/config.toml";
  const bk = st.backup;
  $("#storageLine").textContent = `${st.storage.points.toLocaleString()} fixes stored in ${st.storage.db} (on disk, survives reboot)` +
    (bk ? ` · last daily backup ${ago(bk.ts)}, ${(bk.bytes / 1024).toFixed(0)} KB, 14 kept in data/backups/` : " · first backup pending");
  const c = st.config;
  $("#cfgLine").textContent = `v${st.version} · poll ${c.poll_seconds}s · fresh locate every ${c.locate_minutes} min · AirTags every ${c.airtag_poll_minutes} min · low battery ${c.low_battery_pct}% · stale ${c.stale_hours} h · stays ≥${c.stay_min_minutes} min within ${c.stay_radius_m} m`;
  return st;
}
const busy = async (btn, fn) => { btn.disabled = true; try { await fn(); } catch (e) { toast(e.message, 5000); } finally { btn.disabled = false; loadStatus(); } };
$("#icLogin").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/icloud/login", { method: "POST", body: { apple_id: $("#icId").value, password: $("#icPw").value } }); $("#icPw").value = ""; toast(r.detail || r.state); }); });
$("#ic2fa").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/icloud/2fa", { method: "POST", body: { code: $("#icCode").value } }); $("#icCode").value = ""; toast(r.detail || r.state); if (r.state === "ok") setTimeout(refreshAll, 4000); }); });
$("#icSms").onclick = (e) => busy(e.target, async () => toast((await api("/api/icloud/sms", { method: "POST" })).detail));
$("#icPoll").onclick = (e) => busy(e.target, locateNow);
$("#icLogout").onclick = (e) => { if (confirm("Sign out of iCloud and delete the saved session?")) busy(e.target, () => api("/api/icloud/logout", { method: "POST" })); };
$("#atLogin").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/airtag/login", { method: "POST", body: { apple_id: $("#atId").value, password: $("#atPw").value } }); $("#atPw").value = ""; toast(r.detail || r.state); }); });
$("#at2fa").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/airtag/2fa", { method: "POST", body: { code: $("#atCode").value } }); toast(r.detail || r.state); }); });
$("#atPoll").onclick = (e) => busy(e.target, async () => { await api("/api/airtag/poll", { method: "POST" }); await refreshAll(); });
$("#atLogout").onclick = (e) => busy(e.target, () => api("/api/airtag/logout", { method: "POST" }));
$("#phIcloud").onclick = (e) => busy(e.target, async () => { await api("/api/photos/icloud", { method: "POST", body: {} }); toast("Import started"); pollPhotos(); });
$("#phFolder").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { const r = await api("/api/photos/folder", { method: "POST", body: { path: $("#phPath").value } }); if (r.error) throw new Error(r.error); toast("Import started"); pollPhotos(); }); });
function pollPhotos() { const iv = setInterval(async () => { const st = await loadStatus(); if (!st.photos.running) { clearInterval(iv); await loadDevices(); loadPoints(); } }, 3000); }
$("#emTest").onclick = (e) => busy(e.target, async () => toast("Sent to " + (await api("/api/email/test", { method: "POST" })).to));
$("#pwChange").addEventListener("submit", (e) => { e.preventDefault(); busy(e.submitter, async () => { await api("/api/auth/change", { method: "POST", body: { old: $("#pwOld").value, new: $("#pwNew").value } }); $("#pwOld").value = $("#pwNew").value = ""; toast("Password changed"); }); });
async function loadConfigForm() {
  const rows = await api("/api/config");
  $("#cfgForm").innerHTML = rows.map((r) => `<label class="cfg-row"><span>${esc(r.label)} <span class="u">(${esc(r.unit)})</span></span>
    <input type="number" name="${r.key}" min="${r.min}" max="${r.max}" step="any" value="${r.value}"></label>`).join("") +
    '<div class="row gap end"><span class="muted small" style="margin-right:auto">Fresh fixes wake family devices (battery). Applied immediately.</span><button class="btn small primary">Save</button></div>';
}
$("#cfgForm").addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.submitter, async () => {
    const body = Object.fromEntries([...new FormData(e.target)].map(([k, v]) => [k, Number(v)]));
    await api("/api/config", { method: "PUT", body }); await loadConfigForm(); toast("Settings saved");
  });
});
async function locateNow() { toast("Asking devices for a fresh fix…", 8000); await api("/api/icloud/poll?locate=true", { method: "POST" }); await refreshAll(); toast("Updated"); }

/* ---------------- refresh loop ---------------- */
async function refreshAll() {
  await loadDevices();
  if (S.preset === "live" && !S.playing) {
    const follow = S.t >= S.end - 1;
    [S.start, S.end] = presetRange("live"); if (follow) S.t = S.end;
  }
  await loadPoints();
}
$("#trail").onchange = () => render();

let started = false;
async function start() {
  $("#app").classList.remove("hidden");
  if (innerWidth < 820) $("#app").classList.add("noside");
  map.invalidateSize();
  if (started) return refreshAll(); started = true;
  await Promise.all([loadDevices(), loadFences(), loadStatus()]);
  await setPreset(S.preset);
  const tab = location.hash.slice(1); if (tab && $(`[data-tab="${tab}"]`)) openTab(tab);
  setInterval(() => { if (!document.hidden) refreshAll().catch(() => {}); }, 60000);
  setInterval(() => { if (!document.hidden) loadStatus().catch(() => {}); }, 20000);
}

(async () => {
  const st = await api("/api/auth/state");
  if (st.authed) start(); else showAuth();
})();
