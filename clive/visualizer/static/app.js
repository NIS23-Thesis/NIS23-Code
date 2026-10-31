"use strict";

const state = {
  run: null,
  meta: null,
  frame: null,
  mobilityTrace: null,
  currentTick: -1,
  metrics: null,
  timeline: [],
  timelineTotals: {sent: 0, received: 0, dropped: 0},
  hasPacketTraffic: false,
  currentTime: 0,
  playing: false,
  speed: 1,
  selectedNode: null,
  lastAnimationTs: performance.now(),
  playAnchorWallMs: null,
  playAnchorTimeS: 0,
  visibleEvents: [],
  packetEvents: [],
  relationships: {available: false, events: [], truncated: false},
  relationshipRevision: "",
  relationshipCache: {cursor: 0, time: -1, associations: new Map(), peers: new Map()},
  linkMode: "protocol",
  hoveredNode: null,
  frameRequestKey: null,
  eventRequestInFlight: false,
  lastEventRefreshWallMs: 0,
  view: {zoom: 1, panX: 0, panY: 0},
  pointer: {active: false, id: null, startX: 0, startY: 0, startPanX: 0, startPanY: 0, moved: false},
  periodicBusy: false,
  latestRunName: null,
  hitLinks: [],
  hoverLink: null,
  pinnedLink: null,
  pointerAt: null,
  lastHoverRefreshTs: 0,
  pinnedRenderKey: "",
  ui: {activeTab: "trafficPanel", sidebarCollapsed: false, focusMode: false},
};

const $ = (id) => document.getElementById(id);
const canvas = $("topologyCanvas");
const ctx = canvas.getContext("2d");
const trafficCanvas = $("trafficCanvas");
const tctx = trafficCanvas.getContext("2d");

const clamp = (value, lo, hi) => Math.max(lo, Math.min(hi, value));
const fmtInt = (n) => Math.round(Number(n || 0)).toLocaleString();
const fmtBits = (n) => {
  n = Number(n || 0);
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} Gb`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} Mb`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} kb`;
  return `${Math.round(n)} b`;
};
const fmtEnergy = (n) => {
  n = Number(n || 0);
  if (n >= 1) return `${n.toFixed(3)} J`;
  if (n >= 1e-3) return `${(n * 1e3).toFixed(3)} mJ`;
  if (n >= 1e-6) return `${(n * 1e6).toFixed(3)} µJ`;
  return `${(n * 1e9).toFixed(2)} nJ`;
};
const api = async (path) => {
  const response = await fetch(path, {cache: "no-store"});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || response.statusText);
  return value;
};
const runQueryFor = (run) => run ? `run=${encodeURIComponent(run)}` : "";
const runQuery = () => runQueryFor(state.run);
const escapeHtml = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
const PACKET_NAMES = ["HELLO", "LEAVE", "SENSOR_DATA", "SENSOR_ACK", "MODEL_UPDATE", "MODEL_ACK", "JOIN", "JOIN_ACCEPT"];
const packetName = (n) => PACKET_NAMES[Number(n)] || (n == null ? "—" : `TYPE ${n}`);

function visibleWorkbenchTargets() {
  // A tab can be hidden because it is INACTIVE, which is different from being
  // unavailable because logging was disabled. Never derive availability from
  // the panel.hidden property.
  if (!state.meta) return ["trafficPanel", "eventsPanel", "nodesPanel"];
  const packet = Boolean(state.meta.logging?.packet_events);
  const protocol = Boolean(state.meta.logging?.protocol_events);
  return [...(packet ? ["trafficPanel"] : []),
    ...(packet || protocol ? ["eventsPanel"] : []), "nodesPanel"];
}

function setWorkbenchTab(target) {
  const targets = ["trafficPanel", "eventsPanel", "nodesPanel"];
  const fallback = visibleWorkbenchTargets()[0] || "nodesPanel";
  if (!targets.includes(target) || !visibleWorkbenchTargets().includes(target)) target = fallback;
  state.ui.activeTab = target;
  targets.forEach((name) => {
    const panel = $(name);
    const button = document.querySelector(`.tab-button[data-target="${name}"]`);
    const isActive = name === target;
    if (panel) panel.hidden = !isActive;
    if (button) {
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-selected", isActive ? "true" : "false");
    }
  });
  if (target === "trafficPanel") drawTrafficChart();
}

function syncWorkbenchVisibility() {
  const packetLogging = Boolean(state.meta?.logging?.packet_events);
  const protocolLogging = Boolean(state.meta?.logging?.protocol_events);
  const trafficButton = $("tab-traffic");
  const eventsButton = $("tab-events");
  if (trafficButton) trafficButton.hidden = !packetLogging;
  if (eventsButton) eventsButton.hidden = !(packetLogging || protocolLogging);
  setWorkbenchTab(state.ui.activeTab);
}

function setSidebarCollapsed(collapse) {
  state.ui.sidebarCollapsed = Boolean(collapse);
  const shell = $("appShell");
  const btn = $("sidebarToggle");
  shell?.classList.toggle("sidebar-collapsed", state.ui.sidebarCollapsed);
  btn?.classList.toggle("active", state.ui.sidebarCollapsed);
  btn?.setAttribute("aria-pressed", state.ui.sidebarCollapsed ? "true" : "false");
  const label = btn?.querySelector(".sidebar-button-text");
  if (label) label.textContent = state.ui.sidebarCollapsed ? "Show panel" : "Hide panel";
}

function setFocusMode(enabled) {
  state.ui.focusMode = Boolean(enabled);
  const shell = $("appShell");
  const btn = $("canvasFocusToggle");
  shell?.classList.toggle("focus-mode", state.ui.focusMode);
  btn?.classList.toggle("active", state.ui.focusMode);
  btn?.setAttribute("aria-pressed", state.ui.focusMode ? "true" : "false");
  const label = btn?.querySelector("span:last-child");
  if (label) label.textContent = state.ui.focusMode ? "Exit focus" : "Focus map";
  setTimeout(() => { drawTopology(); drawTrafficChart(); }, 30);
}

function resizeCanvas(canvasEl, context) {
  const rect = canvasEl.getBoundingClientRect();
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const w = Math.max(1, Math.floor(rect.width * dpr));
  const h = Math.max(1, Math.floor(rect.height * dpr));
  if (canvasEl.width !== w || canvasEl.height !== h) {
    canvasEl.width = w;
    canvasEl.height = h;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  return rect;
}

function statusClass(status) {
  return ["running", "complete", "failed", "ready", "starting"].includes(status) ? status : "waiting";
}
function setStatus(status) {
  const pill = $("statusPill");
  pill.className = `status ${statusClass(status)}`;
  pill.textContent = (status || "waiting").toUpperCase();
}

function resetView() {
  state.view.zoom = 1;
  state.view.panX = 0;
  state.view.panY = 0;
  $("zoomReadout").textContent = "100%";
}

function fitNodes() {
  if (!state.frame?.positions?.length || !state.meta) return;
  const rect = canvas.getBoundingClientRect();
  const layout = topologyLayout(rect);
  const points = state.frame.positions.map(p => [layout.baseOx + p.x * layout.baseScale,
    layout.baseOy + layout.drawH - p.y * layout.baseScale]);
  const xs=points.map(p=>p[0]), ys=points.map(p=>p[1]);
  const width=Math.max(...xs)-Math.min(...xs), height=Math.max(...ys)-Math.min(...ys);
  const zoom=clamp(Math.min((rect.width-190)/Math.max(width,40), (rect.height-170)/Math.max(height,40)), 1, 12);
  const centerX=(Math.max(...xs)+Math.min(...xs))/2;
  const centerY=(Math.max(...ys)+Math.min(...ys))/2;
  state.view.zoom=zoom;
  state.view.panX= -(centerX-rect.width/2)*zoom;
  state.view.panY= -(centerY-rect.height/2)*zoom;
  $("zoomReadout").textContent=`${Math.round(zoom*100)}%`;
  drawTopology();
}

function topologyLayout(rect = canvas.getBoundingClientRect()) {
  if (!state.meta) return null;
  const w = rect.width;
  const h = rect.height;
  const pad = 42;
  const fieldW = state.meta.mobility.area_width_m;
  const fieldH = state.meta.mobility.area_height_m;
  const baseScale = Math.min((w - 2 * pad) / fieldW, (h - 2 * pad) / fieldH);
  const drawW = fieldW * baseScale;
  const drawH = fieldH * baseScale;
  const baseOx = (w - drawW) / 2;
  const baseOy = (h - drawH) / 2;
  const cx = w / 2;
  const cy = h / 2;
  const transform = (baseX, baseY) => [
    cx + state.view.panX + (baseX - cx) * state.view.zoom,
    cy + state.view.panY + (baseY - cy) * state.view.zoom,
  ];
  const point = (p) => transform(baseOx + p.x * baseScale, baseOy + drawH - p.y * baseScale);
  const fieldPoint = (x, y) => transform(baseOx + x * baseScale, baseOy + drawH - y * baseScale);
  return {w, h, fieldW, fieldH, baseScale, drawW, drawH, baseOx, baseOy, cx, cy, transform, point, fieldPoint};
}

function setZoom(newZoom, anchorX = null, anchorY = null) {
  const rect = canvas.getBoundingClientRect();
  const oldZoom = state.view.zoom;
  newZoom = clamp(newZoom, 0.35, 12);
  if (Math.abs(newZoom - oldZoom) < 1e-6) return;
  const cx = rect.width / 2;
  const cy = rect.height / 2;
  const ax = anchorX == null ? cx : anchorX;
  const ay = anchorY == null ? cy : anchorY;
  const ratio = newZoom / oldZoom;
  state.view.panX = ax - cx - (ax - cx - state.view.panX) * ratio;
  state.view.panY = ay - cy - (ay - cy - state.view.panY) * ratio;
  state.view.zoom = newZoom;
  $("zoomReadout").textContent = `${Math.round(newZoom * 100)}%`;
}

async function refreshRuns() {
  try {
    const data = await api("/api/runs");
    const select = $("runSelect");
    const previousRun = state.run;
    select.innerHTML = "";
    for (const run of data.runs) {
      const option = document.createElement("option");
      option.value = run.name;
      option.textContent = `${run.name} · ${run.status}`;
      select.appendChild(option);
    }
    if (!data.runs.length) {
      const option = document.createElement("option");
      option.textContent = "No runs yet";
      option.value = "";
      select.appendChild(option);
      state.run = null;
      state.latestRunName = null;
      setStatus("waiting");
      return false;
    }

    const newest = data.runs[0].name;
    let switched = false;
    if (state.latestRunName === null) {
      state.latestRunName = newest;
      if (!state.run || !data.runs.some(r => r.name === state.run)) {
        state.run = newest;
        switched = true;
      }
    } else if (newest !== state.latestRunName) {
      // A new CLIVE run appeared. Follow it automatically once, while still allowing
      // the user to manually inspect older runs until another new run is created.
      state.latestRunName = newest;
      state.run = newest;
      switched = true;
    } else if (!state.run || !data.runs.some(r => r.name === state.run)) {
      state.run = newest;
      switched = true;
    }

    select.value = state.run;
    if (switched || previousRun !== state.run) {
      state.meta = null;
      state.frame = null;
      state.mobilityTrace = null;
      state.currentTick = -1;
      state.metrics = null;
      state.timeline = [];
      state.selectedNode = null;
      state.hoverLink = null; state.pinnedLink = null;
      state.frameRequestKey = null;
      state.playing = false;
      state.playAnchorWallMs = null;
      $("playButton").textContent = "▶ Play";
      resetView();
      $("canvasEmpty").style.display = "";
    }
    return switched;
  } catch (error) {
    console.error(error);
    return false;
  }
}

async function refreshMeta() {
  if (!state.run) return;
  const run = state.run;
  try {
    const meta = await api(`/api/meta?${runQueryFor(run)}`);
    if (run !== state.run || !meta.ready) return;
    const changed = !state.meta || state.meta.run !== meta.run || state.meta.trace_sha256 !== meta.trace_sha256;
    state.meta = meta;
    setStatus(meta.manifest?.status || "waiting");
    $("topologySubtitle").textContent = `${meta.nodes.length} nodes · ${meta.mobility.area_width_m} × ${meta.mobility.area_height_m} m · ${meta.mobility.model.replaceAll("_", " ")} · ${meta.mobility.initial_topology} topology · position interval ${meta.tick_interval_s}s`;
    $("mNodes").textContent = fmtInt(meta.nodes.length);
    $("configRun").textContent = meta.run;
    $("timeSlider").max = meta.duration_s;
    $("timeSlider").step = Math.min(0.05, meta.tick_interval_s);
    renderConfig();
    applyLoggingVisibility();
    if (changed) {
      state.currentTime = 0;
      state.selectedNode = null;
      state.visibleEvents = [];
      state.packetEvents = [];
      resetRelationships();
      state.frameRequestKey = null;
      resetRelationships();
      state.mobilityTrace = null;
      state.currentTick = -1;
      state.playing = false;
      state.playAnchorWallMs = null;
      $("playButton").textContent = "▶ Play";
      resetView();
      await refreshTrace();
      await Promise.all([refreshVisibleEvents(), refreshRelationships()]);
    } else if (!state.frame && state.mobilityTrace) {
      setFrameFromTrace(true);
    }
  } catch (error) {
    console.error(error);
  }
}


function applyLoggingVisibility() {
  syncWorkbenchVisibility();
}

function renderConfig() {
  if (!state.meta) return;
  const m = state.meta.mobility, l = state.meta.link, r = state.meta.runtime;
  const protocol = state.meta.protocol || "protocol_interface:NullProtocol";
  const modelLabel = m.model === "gauss_markov" ? "Gauss–Markov" : m.model === "random_waypoint" ? "Random Waypoint" : "Static";
  const entries = [
    ["Duration", `${r.duration_s}s`],
    ["Area", `${m.area_width_m} × ${m.area_height_m} m`],
    ["Mobility", modelLabel],
    ["Initial topology", m.initial_topology],
    ["Position update interval", `${m.tick_interval}s`],
    ["Protocol", protocol.split(":").pop()],
    ["Radio range mode", l.range_mode],
    ["Default TX range", l.range_mode === "explicit" ? (l.max_range_m != null ? `${Number(l.max_range_m).toFixed(1)} m` : "role/node override") : "RSSI-derived"],
    ["Pathloss exponent", l.pathloss_exponent],
    ["Max data rate", `${(l.max_data_rate_bps / 1e9).toFixed(2)} Gbps`],
    ["Reference RSSI", `${l.rssi0_dbm} dBm @ ${l.d0_m} m`],
  ];
  if (m.model === "gauss_markov") {
    entries.splice(5, 0, ["GM α", m.alpha], ["CH mean speed", `${m.ch_mean_speed_mps} m/s`], ["SN mean speed", `${m.sn_mean_speed_mps} m/s`]);
  }
  if (m.pause_time_range) {
    entries.splice(5, 0, ["Pause mode", m.pause_trigger], ["Pause duration", `${m.pause_time_range[0]}–${m.pause_time_range[1]} s`]);
    if (m.pause_trigger === "periodic" && m.move_time_range) {
      entries.splice(7, 0, ["Move interval", `${m.move_time_range[0]}–${m.move_time_range[1]} s`]);
    }
    if (m.pause_trigger === "random" && m.pause_probability_per_second != null) {
      entries.splice(7, 0, ["Pause chance / s", `${(100 * Number(m.pause_probability_per_second)).toFixed(1)}%`]);
    }
  }
  $("configList").innerHTML = entries.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

async function refreshTrace() {
  if (!state.meta || !state.run) return;
  const run = state.run;
  try {
    const response = await fetch(`/api/mobility.bin?${runQueryFor(run)}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`mobility trace request failed: ${response.status}`);
    const buffer = await response.arrayBuffer();
    if (run !== state.run) return;
    if (buffer.byteLength < 20) throw new Error("invalid mobility binary: truncated header");

    const headerBytes = new Uint8Array(buffer, 0, 8);
    const magic = String.fromCharCode(...headerBytes);
    const view = new DataView(buffer);
    const version = view.getUint32(8, true);
    const nodeCount = view.getUint32(12, true);
    const tickCount = view.getUint32(16, true);
    if (magic !== "CLVMOB01" || version !== 1) throw new Error("unsupported mobility binary format");
    if (nodeCount !== state.meta.nodes.length) throw new Error("mobility binary node count mismatch");
    const expectedBytes = 20 + tickCount * nodeCount * 2 * 8;
    if (buffer.byteLength !== expectedBytes) throw new Error("mobility binary size mismatch");

    state.mobilityTrace = {
      ready: true,
      tick_interval_s: state.meta.tick_interval_s,
      duration_s: state.meta.duration_s,
      node_names: state.meta.nodes.map(n => n.name),
      node_roles: state.meta.nodes.map(n => n.role),
      node_count: nodeCount,
      tick_count: tickCount,
      view,
      data_offset: 20,
    };
    state.currentTick = -1;
    setFrameFromTrace(true);
    $("canvasEmpty").style.display = "none";
    fitNodes();
  } catch (error) {
    state.mobilityTrace = null;
    state.frame = null;
    state.currentTick = -1;
    $("canvasEmpty").style.display = "";
    console.error(error);
  }
}

function setFrameFromTrace(force = false) {
  const trace = state.mobilityTrace;
  if (!trace || !state.meta || !trace.tick_count) return;
  const interval = Math.max(1e-9, Number(trace.tick_interval_s));
  const tick = clamp(Math.floor((state.currentTime + 1e-9) / interval), 0, trace.tick_count - 1);
  if (!force && tick === state.currentTick) return;

  const positions = new Array(trace.node_count);
  const frameOffset = trace.data_offset + tick * trace.node_count * 16;
  for (let i = 0; i < trace.node_count; i++) {
    const offset = frameOffset + i * 16;
    positions[i] = {
      name: trace.node_names[i],
      role: trace.node_roles[i],
      x: trace.view.getFloat64(offset, true),
      y: trace.view.getFloat64(offset + 8, true),
    };
  }

  state.currentTick = tick;
  state.frame = {
    ready: true,
    requested_time_s: state.currentTime,
    tick,
    tick_time_s: tick * interval,
    positions,
  };
  $("canvasEmpty").style.display = "none";
  updateSelectedNode();
}

function rebasePlaybackClock(nowMs = performance.now()) {
  state.playAnchorWallMs = nowMs;
  state.playAnchorTimeS = state.currentTime;
}

function syncPlaybackTime(nowMs = performance.now()) {
  if (!state.playing || state.playAnchorWallMs == null || !state.meta) return;
  const elapsedS = Math.max(0, (nowMs - state.playAnchorWallMs) / 1000);
  state.currentTime = Math.min(state.meta.duration_s, state.playAnchorTimeS + elapsedS * state.speed);
}


async function refreshMetrics() {
  if (!state.run) return;
  try {
    const run = state.run;
    const metrics = await api(`/api/metrics?${runQueryFor(run)}`);
    if (run !== state.run) return;
    state.metrics = metrics;
    const t = state.metrics.totals;
    $("mSent").textContent = fmtInt(t.packets_sent);
    $("mReceived").textContent = fmtInt(t.packets_received);
    $("mDrops").textContent = fmtInt(t.delivery_drops + t.packets_dropped);
    $("mDropRatio").textContent = `${(100 * Number(t.observed_delivery_drop_ratio || 0)).toFixed(2)}%`;
    $("mEnergy").textContent = fmtEnergy(t.energy_sent_j);
    const complete = Boolean(metrics.complete);
    const runComplete = state.meta?.manifest?.status === "complete";
    $("metricsMode").textContent = complete ? "final" : (runComplete ? "unavailable" : "pending");
    renderNodeTable();
    updateSelectedNode();
  } catch (error) {
    console.error(error);
  }
}

function updateTrafficEmptyState() {
  const empty = $("trafficEmpty");
  if (state.hasPacketTraffic) {
    empty.hidden = true;
    empty.textContent = "";
    return;
  }
  empty.hidden = false;
  const protocol = state.meta?.protocol || "protocol_interface:NullProtocol";
  if (protocol.endsWith(":NullProtocol") || protocol === "protocol_interface:NullProtocol") {
    empty.textContent = "No packet traffic was generated. This run used NullProtocol; CLIVE emulates transport conditions but does not invent application traffic.";
  } else {
    empty.textContent = `No packet transmissions were recorded for ${protocol.split(":").pop()}.`;
  }
}

async function refreshTimeline() {
  if (!state.run || !state.meta?.logging?.packet_events) {
    state.timeline = [];
    state.timelineTotals = {sent: 0, received: 0, dropped: 0};
    state.hasPacketTraffic = false;
    return;
  }
  try {
    const run = state.run;
    const data = await api(`/api/timeline?${runQueryFor(run)}&bucket_s=1`);
    if (run !== state.run) return;
    state.timeline = data.rows || [];
    state.timelineTotals = data.totals || {sent: 0, received: 0, dropped: 0};
    state.hasPacketTraffic = Boolean(data.has_packet_traffic);
    $("trafficSummary").textContent = `TX ${fmtInt(state.timelineTotals.sent)} · RX ${fmtInt(state.timelineTotals.received)} · drop ${fmtInt(state.timelineTotals.dropped)}`;
    updateTrafficEmptyState();
  } catch (error) {
    console.error(error);
  }
}

async function refreshVisibleEvents() {
  const logging = state.meta?.logging || {};
  if (!state.run || !state.meta || state.eventRequestInFlight || !(logging.packet_events || logging.protocol_events)) return;
  state.eventRequestInFlight = true;
  const windowS = 1.75;
  try {
    const run = state.run;
    const data = await api(`/api/events?${runQueryFor(run)}&from_s=${Math.max(0, state.currentTime - windowS)}&to_s=${Math.min(state.meta.duration_s, state.currentTime + 0.10)}&limit=1000`);
    if (run !== state.run) return;
    state.visibleEvents = data.events || [];
    // Keep packet rendering bounded even for 220-node traces. The event table still
    // receives the complete requested time-window response.
    state.packetEvents = state.visibleEvents.filter(e => ["receive", "delivery_drop"].includes(e.event) && e.source && e.target).slice(-180);
    renderEventTable();
  } catch (error) {
    console.error(error);
  } finally {
    state.eventRequestInFlight = false;
  }
}

// Only logged FLAME events can establish an observed association or model exchange.
// The playback cache is incremental; moving the slider backwards rebuilds it.
function resetRelationships() {
  state.relationships = {available: false, events: [], truncated: false};
  state.relationshipRevision = "";
  state.relationshipCache = {cursor: 0, time: -1, associations: new Map(), peers: new Map()};
}

async function refreshRelationships() {
  if (!state.run || !state.meta) return;
  const run = state.run;
  try {
    const data = await api(`/api/relationships?${runQueryFor(run)}`);
    if (run !== state.run) return;
    const events = Array.isArray(data.events) ? data.events : [];
    const last = events.length ? events[events.length - 1] : null;
    const rev = `${events.length}:${last?.seq ?? 0}:${last?.time_s ?? -1}`;
    if (rev !== state.relationshipRevision || Boolean(data.available) !== state.relationships.available) {
      state.relationships = {available: Boolean(data.available), events, truncated: Boolean(data.truncated)};
      state.relationshipRevision = rev;
      state.relationshipCache = {cursor: 0, time: -1, associations: new Map(), peers: new Map()};
      updateSelectedNode();
    }
  } catch (error) { console.error(error); }
}

function replayRelationships(timeS) {
  let c = state.relationshipCache;
  if (timeS < c.time) {
    c = {cursor: 0, time: -1, associations: new Map(), peers: new Map()};
    state.relationshipCache = c;
  }
  const events = state.relationships.events;
  while (c.cursor < events.length && events[c.cursor].time_s <= timeS) {
    const ev = events[c.cursor++];
    if (ev.action === "flame_joined") {
      c.associations.set(ev.source, {ch: ev.target, joinedAt: ev.time_s, confirmedAt: null, lastSeen: ev.time_s});
    } else if (ev.action === "flame_sensor_received") {
      const old = c.associations.get(ev.source);
      if (!old || old.ch !== ev.target) {
        c.associations.set(ev.source, {ch: ev.target, joinedAt: null, confirmedAt: ev.time_s, lastSeen: ev.time_s});
      } else {
        old.confirmedAt = old.confirmedAt ?? ev.time_s;
        old.lastSeen = ev.time_s;
      }
    } else if (ev.action === "flame_leave_sent") {
      if (c.associations.get(ev.source)?.ch === ev.target) c.associations.delete(ev.source);
    } else if (ev.action === "flame_model_sent" || ev.action === "flame_model_received") {
      const key = [ev.source, ev.target].sort().join("|");
      c.peers.set(key, ev.time_s);
    }
  }
  c.time = timeS;
  return c;
}

function roleCompatible(a, b) {
  return Boolean(a && b && (a.role === 1 || b.role === 1));
}

function radioPair(a, b, specifications) {
  const specA = specifications.get(a.name), specB = specifications.get(b.name);
  const distance = Math.hypot(a.x - b.x, a.y - b.y);
  return {distance, ab: linkCondition(distance, specA), ba: linkCondition(distance, specB)};
}

function edgeColor(band, alpha = 0.60) {
  if (band === "poor") return `rgba(255,209,102,${alpha})`;
  if (band === "good") return `rgba(92,200,255,${alpha})`;
  return `rgba(93,224,160,${alpha})`;
}

function drawArrow(x1, y1, x2, y2, fraction, color, scale = 1) {
  const x = x1 + (x2 - x1) * fraction, y = y1 + (y2 - y1) * fraction;
  const angle = Math.atan2(y2 - y1, x2 - x1), size = 4 * scale;
  ctx.strokeStyle = color; ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(x - size * Math.cos(angle - 0.55), y - size * Math.sin(angle - 0.55));
  ctx.lineTo(x, y);
  ctx.lineTo(x - size * Math.cos(angle + 0.55), y - size * Math.sin(angle + 0.55));
  ctx.stroke();
}

function drawLink(a, b, pair, point, options = {}) {
  const {observed = false, confirmed = true, type = "candidate", emphasize = false, allowReverse = true} = options;
  const ab = pair.ab.reachable, ba = allowReverse && pair.ba.reachable;
  // An observed relationship can remain visible (as disconnected) even after
  // the nodes move out of range; it is NOT claimed to be a live radio link.
  if (!observed && !ab && !ba) return false;
  const [x1, y1] = point(a), [x2, y2] = point(b);
  state.hitLinks.push({kind: "link", a: a.name, b: b.name, ax: x1, ay: y1, bx: x2, by: y2,
    pair, observed, confirmed, type, emphasized: emphasize, priority: observed ? 4 : emphasize ? 3 : 1,
    relation: options.relation || null});
  const color = (!ab && !ba) ? "rgba(255,107,122,.78)" :
    observed ? (confirmed ? "rgba(93,224,160,.83)" : "rgba(92,200,255,.76)") :
    type === "backbone" ? "rgba(155,140,255,.25)" :
    edgeColor(ab ? pair.ab.band : pair.ba.band, emphasize ? .70 : .37);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = observed ? (emphasize ? 2.8 : 2.1) : emphasize ? 1.8 : 1.0;
  if (!observed) ctx.setLineDash([4, 6]);
  if (observed && !ab && !ba) ctx.setLineDash([3, 5]);
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  ctx.setLineDash([]);
  if (state.hoverLink?.kind === "link" && [state.hoverLink.a, state.hoverLink.b].sort().join("|") ===
      [a.name, b.name].sort().join("|") && state.linkMode !== "none") {
    ctx.strokeStyle = "rgba(229,245,255,.85)"; ctx.lineWidth = 3.2;
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
  }
  if (ab) drawArrow(x1, y1, x2, y2, .63, color, emphasize ? 1.15 : .9);
  if (ba) drawArrow(x2, y2, x1, y1, .63, color, emphasize ? 1.15 : .9);
  ctx.restore();
  return true;
}

// Link hit-testing is done in screen pixels, never in emulated meters. It ignores
// endpoints so node clicks continue to win over connections. Recorded packet dots
// win over broad candidate lines when several edges overlap.
function hitLinkAt(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const px = clientX - rect.left, py = clientY - rect.top;
  let best = null, score = Infinity;
  for (const edge of state.hitLinks) {
    const dx = edge.bx - edge.ax, dy = edge.by - edge.ay;
    const lengthSq = dx*dx + dy*dy;
    if (lengthSq < 80) continue;
    const t = clamp(((px-edge.ax)*dx + (py-edge.ay)*dy) / lengthSq, 0, 1);
    // Do not intercept nodes near either end of the line.
    if (t < .08 || t > .92) continue;
    let x = edge.ax + t*dx, y = edge.ay + t*dy;
    if (edge.kind === "packet") {
      const age = state.currentTime - edge.event.time_s;
      const progress = edge.event.event === "delivery_drop" ? .52 : clamp(age / .75, 0, 1);
      x = edge.ax + progress*dx; y = edge.ay + progress*dy;
    }
    const d = Math.hypot(px-x, py-y);
    const tolerance = edge.kind === "packet" ? 12 : 9;
    if (d > tolerance) continue;
    const metric = d - edge.priority * 1.5;
    if (metric < score) { best = edge; score = metric; }
  }
  return best;
}

function packetMeaning(ev) {
  const type = packetName(ev.packet_type);
  const details = {
    "HELLO": "The Cluster Head announces its presence to nearby nodes; this does not prove a sensor has joined.",
    "JOIN": "A Sensor Node requests association with a Cluster Head.",
    "JOIN_ACCEPT": "The Cluster Head answers the JOIN request; application-level data acceptance is a separate step.",
    "LEAVE": "A node notifies its peer that it is leaving the association.",
    "SENSOR_DATA": "A sensor-data fragment was processed by CLIVE's transport. This does not by itself prove FLAME consumed the observation.",
    "SENSOR_ACK": "A sensor-data fragment acknowledgement was recorded at the transport layer.",
    "MODEL_UPDATE": "A Cluster Head model fragment travelled over CLIVE; a single fragment is not a completed model exchange.",
    "MODEL_ACK": "Acknowledgement traffic for a model-update fragment was recorded."
  };
  return details[type] || "A packet event was recorded by CLIVE. Its application-level outcome is not established here.";
}

function recentLinkActivity(a, b, untilTime, limit = 3) {
  if (!state.relationships.available) return [];
  const labels = {
    flame_joined: "JOIN accepted by sensor",
    flame_sensor_received: "CH accepted sensor observation",
    flame_model_sent: "CH sent model-update event",
    flame_model_received: "CH received model-update event",
    flame_leave_sent: "LEAVE sent",
  };
  const rows=[];
  for (const ev of state.relationships.events) {
    if (ev.time_s > untilTime) break;
    if (!((ev.source === a && ev.target === b) || (ev.source === b && ev.target === a))) continue;
    rows.push({time: ev.time_s, text: `${labels[ev.action] || ev.action}: ${ev.source} → ${ev.target}`});
    if (rows.length > limit) rows.shift();
  }
  return rows.reverse();
}

function linkDescription(edge) {
  if (!state.meta) return null;
  const specs = new Map(state.meta.nodes.map(n=>[n.name,n]));
  const specA = specs.get(edge.a), specB = specs.get(edge.b);
  const aRole = specA?.role === 1 ? "CH" : "SN";
  const bRole = specB?.role === 1 ? "CH" : "SN";
  const title = `${edge.a} → ${edge.b}`;
  const time = `${state.currentTime.toFixed(2)} s`;
  if (edge.kind === "packet") {
    const ev = edge.event;
    const type = packetName(ev.packet_type);
    const dropped = ev.event === "delivery_drop";
    const label = dropped ? "Delivery dropped" : "Packet received";
    const facts = [
      ["Recorded at", `${Number(ev.time_s).toFixed(3)} s`],
      ["Packet", type],
      ["Sequence", ev.sequence_number == null ? "Not logged" : String(ev.sequence_number)],
      ["Distance", ev.distance_m == null ? "Not logged" : `${Number(ev.distance_m).toFixed(1)} m`],
      ["RSSI", ev.rssi_dbm == null ? "Not logged" : `${Number(ev.rssi_dbm).toFixed(1)} dBm`],
    ];
    if (ev.reason) facts.push(["Reason", ev.reason]);
    return {title, tag: label, tone: dropped ? "danger" : "success", mode: "Recorded packet · transport event",
      meaning: dropped
        ? `CLIVE recorded a delivery drop for ${type}. This packet did not arrive at the target through this attempt; FLAME may retransmit it later.`
        : packetMeaning(ev),
      nuance: dropped ? "A drop does not mean the complete sensor sample or model transfer failed."
        : "A receive event confirms a NodeSocket delivery, not necessarily complete FLAME processing.",
      facts, activity: []};
  }
  const pair = edge.pair;
  const ab = Boolean(pair?.ab?.reachable), ba = Boolean(pair?.ba?.reachable);
  const reachable = ab || ba;
  const oneWay = ab !== ba;
  const direction = ab && ba ? "Both directions reachable" : ab ? `${edge.a} → ${edge.b} only` : ba ? `${edge.b} → ${edge.a} only` : "Neither direction reachable";
  const isSensor = aRole === "SN" || bRole === "SN";
  const sensor = aRole === "SN" ? edge.a : edge.b;
  const ch = aRole === "CH" ? edge.a : edge.b;
  const facts = [
    ["At", time],
    ["Separation", `${Number(pair.distance).toFixed(1)} m`],
    ["Radio", direction],
    ["Approx. RSSI", `${Number(pair.ab.rssi).toFixed(1)} dBm`],
    ["A → B loss estimate", ab ? `${(pair.ab.plr*100).toFixed(0)}%` : "Unreachable"],
    ["B → A loss estimate", ba ? `${(pair.ba.plr*100).toFixed(0)}%` : "Unreachable"],
  ];
  if (edge.type === "radio") {
    const possible = isSensor && aRole === "SN" && bRole === "SN";
    return {title, mode: "Physical radio view", tag: "Radio-reachable", tone: "info",
      meaning: possible
        ? "CLIVE currently considers this Sensor Node-to-Sensor Node transmission physically possible. FLAME does not use SN-to-SN messaging."
        : `CLIVE currently estimates that ${edge.a} can transmit to ${edge.b}. This is radio reachability, not a confirmed JOIN, model exchange, or active transfer.`,
      nuance: "These values are modeled from position and radio settings at the current mobility tick; actual packet delivery may still fail.", facts};
  }
  if (edge.observed && !reachable) {
    return {title, mode: "FLAME protocol view", tag: "Historical · out of range", tone: "danger",
      meaning: isSensor
        ? `A relationship between ${sensor} and ${ch} was recorded earlier, but neither direction is radio-reachable now. FLAME may have timed out the association.`
        : `A model-exchange event between ${edge.a} and ${edge.b} was recorded earlier; their current positions are outside mutual radio range.`,
      nuance: "Logged history does not prove current membership; timeout and LEAVE state require separate protocol evidence.", facts};
  }
  if (edge.observed && isSensor) {
    const rel = edge.relation;
    facts.push(["JOIN observed", rel?.joinedAt == null ? "Not observed" : `${rel.joinedAt.toFixed(2)} s`]);
    facts.push(["Sensor accepted", rel?.confirmedAt == null ? "Not observed" : `${rel.confirmedAt.toFixed(2)} s`]);
    facts.push(["Last sensor received", rel?.lastSeen == null ? "Not observed" : `${rel.lastSeen.toFixed(2)} s`]);
    return {title, mode: "FLAME protocol view", tag: rel?.confirmedAt != null ? "Sensor data accepted" : "JOIN observed", tone: rel?.confirmedAt != null ? "success" : "info",
      meaning: rel?.confirmedAt != null
        ? `The ${ch} logged accepting sensor observations from ${sensor}. The latest accepted observation was at ${rel.lastSeen.toFixed(2)} s.`
        : `${sensor} logged JOIN acceptance by ${ch}. CH-side acceptance of sensor observations has not yet been recorded.`,
      nuance: oneWay ? "Only one radio direction is currently reachable; an ACK exchange may not succeed." : "This is recorded activity, not proof the nodes are transmitting at this exact instant.", facts};
  }
  if (edge.observed) {
    if (edge.relation?.modelExchangeAt != null) facts.push(["Last exchange event", `${edge.relation.modelExchangeAt.toFixed(2)} s`]);
    return {title, mode: "FLAME protocol view", tag: "CH model exchange observed", tone: "success",
      meaning: `FLAME logged model-update activity between ${edge.a} and ${edge.b} during this run. They may exchange more model updates if radio-reachable.`,
      nuance: "An exchange event does not guarantee that the whole fragmented model was reassembled or aggregated.", facts};
  }
  const message = isSensor
    ? `${sensor} and ${ch} are role-compatible and within modeled radio range. This is a possible HELLO/JOIN or SENSOR_DATA path, not evidence of an actual association.`
    : `${edge.a} and ${edge.b} are Cluster Heads within modeled radio range and could exchange models. No model exchange has been observed for this pair by this playback time.`;
  return {title, mode: "FLAME protocol view", tag: "Possible link · not confirmed", tone: "muted",
    meaning: message,
    nuance: !state.relationships.available ? "Protocol-event logging is unavailable, so existing associations cannot be verified." :
      "Dashed candidates are computed from distance and radio settings; no FLAME relationship is implied.", facts};
}

function describeLinkHtml(edge, pinned = false) {
  const data = linkDescription(edge);
  if (!data) return "";
  const rows = data.facts.map(([k,v]) => `<div class="link-fact"><span>${escapeHtml(k)}</span><strong>${escapeHtml(v)}</strong></div>`).join("");
  const activity = edge.kind === "packet" ? [] : recentLinkActivity(edge.a,edge.b,state.currentTime);
  const activityHtml = activity.length
    ? `<div class="link-activity"><strong>Recent recorded actions</strong>${activity.map(item=>`<div><time>${item.time.toFixed(2)}s</time><span>${escapeHtml(item.text)}</span></div>`).join("")}</div>`
    : "";
  return `<div class="link-card-top"><span class="link-card-eyebrow">${escapeHtml(data.mode)}</span>${pinned ? '<button id="closeLinkCard" type="button" aria-label="Close link details">×</button>' : ""}</div>
    <div class="link-card-title">${escapeHtml(data.title)}</div>
    <div class="link-card-status ${escapeHtml(data.tone)}">${escapeHtml(data.tag)}</div>
    <p class="link-card-meaning">${escapeHtml(data.meaning)}</p>
    <div class="link-card-facts">${rows}</div>
    ${activityHtml}
    <p class="link-card-note">${escapeHtml(data.nuance)}</p>`;
}

function showLinkHover(edge, clientX, clientY) {
  const tooltip = $("linkTooltip");
  if (!edge || !state.meta || !tooltip) { if (tooltip) tooltip.hidden = true; return; }
  tooltip.innerHTML = describeLinkHtml(edge);
  tooltip.hidden = false;
  const rect = canvas.getBoundingClientRect();
  const margin = 12;
  let x = clientX - rect.left + 16, y = clientY - rect.top + 16;
  const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
  x = clamp(x, margin, Math.max(margin, rect.width - tw - margin));
  y = clamp(y, margin, Math.max(margin, rect.height - th - margin));
  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${y}px`;
}

function setPinnedLink(edge) {
  state.pinnedLink = edge ? {kind: edge.kind, a: edge.a, b: edge.b, eventSeq: edge.event?.seq, eventTime: edge.event?.time_s} : null;
  state.pinnedRenderKey = "";
  updatePinnedLink();
}

function updatePinnedLink() {
  const card = $("linkDetailCard");
  if (!card) return;
  if (!state.pinnedLink) { card.hidden = true; return; }
  const pin = state.pinnedLink;
  const edge = state.hitLinks.find(link => link.kind === pin.kind && link.a === pin.a && link.b === pin.b &&
    (pin.kind !== "packet" || (link.event?.seq === pin.eventSeq && link.event?.time_s === pin.eventTime)));
  if (!edge) {
    // A packet marker can age out of the 1.75s window, and a candidate can
    // disappear with mobility. Do not keep presenting stale details as current.
    card.hidden = true;
    state.pinnedLink = null;
    state.pinnedRenderKey = "";
    return;
  }
  const renderKey = `${state.relationshipRevision}|${state.currentTick}|${Math.floor(state.currentTime*4)}|${edge.a}|${edge.b}|${edge.kind}|${edge.event?.seq}`;
  if (card.hidden === false && state.pinnedRenderKey === renderKey) return;
  state.pinnedRenderKey = renderKey;
  card.innerHTML = describeLinkHtml(edge, true);
  card.hidden = false;
  card.querySelector("#closeLinkCard")?.addEventListener("click", ()=>setPinnedLink(null));
}

function updateLinkExplanation(snapshot, selected, stats) {
  const mode = state.linkMode;
  const notice = $("linkNotice"), summary = $("linkSummary");
  if (mode === "protocol") {
    const confirmed = [...snapshot.associations.values()].filter(r => r.confirmedAt != null).length;
    summary.textContent = `${snapshot.associations.size} logged SN joins (${confirmed} CH-confirmed) · ${stats.backbone} reachable CH peer pairs · ${stats.candidates} selected-node candidates`;
    notice.textContent = state.relationships.available
      ? "Cyan: Sensor Node logged an accepted JOIN. Green: Cluster Head also accepted sensor data. Dashed: role-compatible radio candidate only. Red dashed: previously observed connection now outside radio range."
      : "Association history unavailable: enable Protocol events in Experiment Studio before starting a run. Dashed candidates are not proof of JOIN or membership.";
  } else if (mode === "radio") {
    summary.textContent = selected ? `${stats.radio} outbound radio-reachable nodes from ${selected.name}` : "Select a node to inspect outbound radio range";
    notice.textContent = "Radio reachability includes SN–SN links. These are PHYSICAL possibilities, not FLAME protocol communications. Arrows show sender-side reachability.";
  } else if (mode === "traffic") {
    summary.textContent = `${stats.traffic} received/dropped packet events shown in the playback window`;
    notice.textContent = state.meta?.logging?.packet_events
      ? "Moving dots: recorded NodeSocket receives. Red crosses: modeled delivery drops. Up to 180 recent events are drawn; movement is illustrative, not measured propagation time."
      : "Packet events were disabled for this run. Enable Packet events in the scenario logger before starting an experiment to see actual packet activity.";
  } else {
    summary.textContent = "Link drawing hidden";
    notice.textContent = "Node positions are replayed from CLIVE's recorded mobility trace.";
  }
  if (state.relationships.truncated && mode === "protocol") notice.textContent += " Relationship-event history was truncated.";
}

function linkCondition(distance, sourceSpec) {
  const cfg = state.meta.link;
  const d = Math.max(distance, cfg.d0_m);
  const rssi = cfg.rssi0_dbm - 10 * cfg.pathloss_exponent * Math.log10(d / cfg.d0_m);
  const range = Number(sourceSpec?.max_tx_range_m || 0);
  if (cfg.range_mode === "explicit") {
    if (!range || distance > range) return {reachable: false, rssi, band: "none", plr: 1, rate: 0};
    const ratio = distance / range;
    if (ratio <= 15 / 70) return {reachable: true, rssi, band: "strong", plr: 0, rate: cfg.max_data_rate_bps};
    if (ratio <= 32 / 70) return {reachable: true, rssi, band: "good", plr: .02, rate: .44 * cfg.max_data_rate_bps};
    return {reachable: true, rssi, band: "poor", plr: .10, rate: .20 * cfg.max_data_rate_bps};
  }
  if (rssi <= -85) return {reachable: false, rssi, band: "none", plr: 1, rate: 0};
  if (rssi <= -75) return {reachable: true, rssi, band: "poor", plr: .10, rate: .20 * cfg.max_data_rate_bps};
  if (rssi < -65) return {reachable: true, rssi, band: "good", plr: .02, rate: .44 * cfg.max_data_rate_bps};
  return {reachable: true, rssi, band: "strong", plr: 0, rate: cfg.max_data_rate_bps};
}

function currentDisplayPositions() {
  return state.frame?.positions || [];
}


function drawTopology() {
  const rect = resizeCanvas(canvas, ctx);
  const w = rect.width, h = rect.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#090d12"; ctx.fillRect(0, 0, w, h);
  if (!state.frame || !state.meta) return;
  const layout = topologyLayout(rect), {fieldW, fieldH, fieldPoint, point} = layout;
  ctx.strokeStyle = "rgba(143,161,181,.08)"; ctx.lineWidth = 1;
  const grid = Math.max(10, Math.pow(10, Math.floor(Math.log10(Math.max(fieldW, fieldH) / 5))));
  for (let x = 0; x <= fieldW + 1e-6; x += grid) {
    const [x0, y0] = fieldPoint(x, 0), [x1, y1] = fieldPoint(x, fieldH);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  }
  for (let y = 0; y <= fieldH + 1e-6; y += grid) {
    const [x0, y0] = fieldPoint(0, y), [x1, y1] = fieldPoint(fieldW, y);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  }
  const [tlx, tly] = fieldPoint(0, fieldH), [brx, bry] = fieldPoint(fieldW, 0);
  ctx.strokeStyle = "rgba(143,161,181,.35)"; ctx.strokeRect(tlx, tly, brx - tlx, bry - tly);

  const positions = currentDisplayPositions();
  const specs = new Map(state.meta.nodes.map(n => [n.name, n]));
  const pos = new Map(positions.map(p => [p.name, p]));
  const selected = pos.get(state.selectedNode);
  const relationships = replayRelationships(state.currentTime);
  const stats = {backbone: 0, candidates: 0, radio: 0, traffic: 0};
  state.hitLinks = [];

  if (state.linkMode === "protocol") {
    const seen = new Set();
    // Observed sensor associations always take precedence over potential links.
    for (const [sensorName, relation] of relationships.associations) {
      const sn = pos.get(sensorName), ch = pos.get(relation.ch);
      if (!sn || !ch || !roleCompatible(specs.get(sensorName), specs.get(relation.ch))) continue;
      const pair = radioPair(sn, ch, specs);
      drawLink(sn, ch, pair, point, {observed: true, confirmed: relation.confirmedAt != null,
        emphasize: Boolean(selected && (selected.name === sn.name || selected.name === ch.name)), relation});
      seen.add([sn.name, ch.name].sort().join("|"));
    }
    const chs = positions.filter(p => p.role === 1);
    if ($("showBackbone").checked) {
      for (let i = 0; i < chs.length; i++) {
        for (let j = i + 1; j < chs.length; j++) {
          const a = chs[i], b = chs[j], key = [a.name, b.name].sort().join("|");
          const pair = radioPair(a, b, specs);
          const exchanged = relationships.peers.has(key);
          if (drawLink(a, b, pair, point, {type: "backbone", observed: exchanged,
            emphasize: Boolean(selected && (selected.name === a.name || selected.name === b.name)),
            relation: exchanged ? {modelExchangeAt: relationships.peers.get(key)} : null}) &&
            (pair.ab.reachable || pair.ba.reachable)) stats.backbone++;
        }
      }
    }
    if (selected && $("showCandidates").checked) {
      for (const other of positions) {
        if (selected.name === other.name || !roleCompatible(specs.get(selected.name), specs.get(other.name))) continue;
        const key = [selected.name, other.name].sort().join("|");
        if (seen.has(key)) continue;
        if (selected.role === 1 && other.role === 1 && $("showBackbone").checked) continue;
        if (drawLink(selected, other, radioPair(selected, other, specs), point, {emphasize: true})) stats.candidates++;
      }
    }
  } else if (state.linkMode === "radio" && selected) {
    // Only sender->receiver physics is drawn here; there is no claim that
    // FLAME has joined or exchanged anything with that receiver.
    for (const other of positions) {
      if (selected.name === other.name) continue;
      const pair = radioPair(selected, other, specs);
      if (!pair.ab.reachable) continue;
      if (drawLink(selected, other, pair, point, {type: "radio", emphasize: true, allowReverse: false})) stats.radio++;
    }
  } else if (state.linkMode === "traffic" && state.meta.logging?.packet_events) {
    // The packet endpoints and receive/drop outcomes are from recorded NodeSocket
    // events. Marker motion is illustrative, not a claimed latency measurement.
    for (const event of state.packetEvents) {
      const a = pos.get(event.source), b = pos.get(event.target);
      if (!a || !b) continue;
      const [x1,y1] = point(a), [x2,y2] = point(b);
      const age = state.currentTime - event.time_s;
      if (age < -0.10 || age > 1.75) continue;
      stats.traffic++;
      const dropped = event.event === "delivery_drop";
      state.hitLinks.push({kind: "packet", a: a.name, b: b.name,
        ax: x1, ay: y1, bx: x2, by: y2, event, priority: 6});
      const color = dropped ? "#ff6b7a" : event.packet_type === 4 ? "#aa9bff" : event.packet_type === 2 ? "#5cc8ff" : "#5de0a0";
      ctx.strokeStyle = dropped ? "rgba(255,107,122,.20)" : "rgba(143,161,181,.13)";
      ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
      const progress = dropped ? .52 : clamp(age / 0.75, 0, 1);
      const x = x1 + (x2-x1)*progress, y = y1 + (y2-y1)*progress;
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 2;
      if (dropped) {
        ctx.beginPath(); ctx.moveTo(x-4,y-4); ctx.lineTo(x+4,y+4); ctx.moveTo(x+4,y-4); ctx.lineTo(x-4,y+4); ctx.stroke();
      } else {
        ctx.beginPath(); ctx.arc(x,y,3.2,0,Math.PI*2); ctx.fill();
      }
    }
  }

  for (const p of positions) {
    const [x, y] = point(p);
    const isSelected = p.name === state.selectedNode, isHovered = p.name === state.hoveredNode;
    if (isSelected || isHovered) {
      ctx.beginPath(); ctx.arc(x, y, isSelected ? 15 : 11, 0, Math.PI * 2);
      ctx.fillStyle = isSelected ? "rgba(255,255,255,.13)" : "rgba(92,200,255,.09)";
      ctx.fill(); ctx.strokeStyle = isSelected ? "#ffffff" : "#9cb7cb";
      ctx.lineWidth = 1; ctx.stroke();
    }
    if (p.role === 1) {
      const size = isSelected ? 8 : 6.3;
      ctx.fillStyle = "#9b8cff"; ctx.strokeStyle = "#d9d0ff"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.rect(x-size,y-size,size*2,size*2); ctx.fill(); ctx.stroke();
    } else {
      ctx.fillStyle = "#5cc8ff"; ctx.strokeStyle = "#b9eaff"; ctx.lineWidth = .8;
      ctx.beginPath(); ctx.arc(x,y,isSelected ? 5.8 : 4.0,0,Math.PI*2); ctx.fill(); ctx.stroke();
    }
    if ($("showLabels").checked || isSelected || isHovered) {
      ctx.fillStyle = "#dbe7f4"; ctx.font = "11px ui-sans-serif, system-ui";
      ctx.fillText(p.name, x+10,y-8);
    }
  }
  updateLinkExplanation(relationships, selected, stats);
  updatePinnedLink();
  $("canvasCorner").textContent = `${positions.filter(p => p.role === 1).length} CH · ${positions.filter(p => p.role !== 1).length} SN · ${state.frame.tick_time_s.toFixed(2)}s`;
  $("timeReadout").textContent = `${state.currentTime.toFixed(2)} s`;
  $("tickReadout").textContent = `tick ${state.frame.tick} @ ${state.frame.tick_time_s.toFixed(2)}s`;
  $("timeSlider").value = state.currentTime;
}

function drawTrafficChart() {
  const rect = resizeCanvas(trafficCanvas, tctx), w = rect.width, h = rect.height;
  tctx.clearRect(0, 0, w, h);
  tctx.fillStyle = "#0a0f15";
  tctx.fillRect(0, 0, w, h);
  const rows = state.timeline;
  if (!rows.length || !state.meta) return;

  const padL = 42, padR = 16, padT = 18, padB = 26;
  const maxY = Math.max(1, ...rows.flatMap(r => [r.sent, r.received, r.dropped]));
  const duration = Math.max(.001, state.meta.duration_s);
  const x = (timeS) => padL + (w - padL - padR) * clamp(timeS / duration, 0, 1);
  const y = (v) => h - padB - (h - padT - padB) * (v / maxY);

  tctx.strokeStyle = "rgba(143,161,181,.11)";
  tctx.fillStyle = "#7f91a6";
  tctx.font = "10px ui-sans-serif, system-ui";
  for (let i = 0; i <= 4; i++) {
    const v = maxY * i / 4, py = y(v);
    tctx.beginPath(); tctx.moveTo(padL, py); tctx.lineTo(w - padR, py); tctx.stroke();
    tctx.fillText(Math.round(v), 5, py + 3);
  }
  for (let i = 0; i <= 4; i++) {
    const t = duration * i / 4, px = x(t);
    tctx.fillText(`${t.toFixed(duration < 10 ? 1 : 0)}s`, px - 10, h - 7);
  }

  const series = [["sent", "#ffd166"], ["received", "#5de0a0"], ["dropped", "#ff6b7a"]];
  for (const [key, color] of series) {
    tctx.strokeStyle = color; tctx.lineWidth = 1.6; tctx.beginPath();
    rows.forEach((r, i) => {
      const px = x(r.time_s), py = y(r[key]);
      if (i === 0) tctx.moveTo(px, py); else tctx.lineTo(px, py);
    });
    tctx.stroke();
  }

  const cursorX = x(state.currentTime);
  tctx.strokeStyle = "rgba(255,255,255,.5)";
  tctx.beginPath(); tctx.moveTo(cursorX, padT); tctx.lineTo(cursorX, h - padB); tctx.stroke();

  const legend = [["TX", "#ffd166"], ["RX", "#5de0a0"], ["Drop", "#ff6b7a"]];
  let lx = w - 190;
  for (const [label, color] of legend) {
    tctx.fillStyle = color; tctx.fillRect(lx, 7, 8, 8);
    tctx.fillStyle = "#9fb0c2"; tctx.fillText(label, lx + 12, 15); lx += 58;
  }
}

function renderEventTable() {
  const filter = $("eventFilter").value;
  let events = [...state.visibleEvents].sort((a, b) => b.time_s - a.time_s);
  if (filter === "protocol") events = events.filter(e => e.stream === "protocol");
  else if (filter !== "all") events = events.filter(e => e.event === filter);
  events = events.slice(0, 160);
  $("eventRows").innerHTML = events.map(e => {
    const klass = e.event === "delivery_drop" || e.event === "local_drop" ? "drop" : e.event === "receive" ? "rx" : e.event === "transmit" || e.event === "scheduled_delivery" ? "tx" : "";
    const link = [e.distance_m != null ? `${Number(e.distance_m).toFixed(1)}m` : null, e.rssi_dbm != null ? `${Number(e.rssi_dbm).toFixed(1)}dBm` : null].filter(Boolean).join(" · ") || "—";
    const type = e.packet_type == null ? "—" : `${packetName(e.packet_type)}${e.sequence_number == null ? "" : ` / #${e.sequence_number}`}`;
    return `<tr><td class="mono">${e.time_s >= 0 ? e.time_s.toFixed(3) : "—"}</td><td><span class="badge ${klass}">${escapeHtml(e.event)}</span></td><td>${escapeHtml(e.source || e.node || "—")}</td><td>${escapeHtml(e.target || (e.is_broadcast ? "broadcast" : "—"))}</td><td class="mono">${escapeHtml(type)}</td><td class="muted">${escapeHtml(link)}${e.reason ? ` · ${escapeHtml(e.reason)}` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">No events in this time window.</td></tr>`;
}

function renderNodeTable() {
  if (!state.metrics) return;
  const q = $("nodeSearch").value.trim().toLowerCase();
  const rows = state.metrics.nodes.filter(n => !q || n.node_name.toLowerCase().includes(q));
  $("nodeRows").innerHTML = rows.map(n => `<tr data-node="${escapeHtml(n.node_name)}"><td><strong>${escapeHtml(n.node_name)}</strong></td><td>${n.role === 1 ? "Cluster Head" : "Sensor Node"}</td><td>${fmtInt(n.packets_sent)}</td><td>${fmtInt(n.packets_received)}</td><td>${fmtInt(Number(n.delivery_drops || 0) + Number(n.packets_dropped || 0))}</td><td>${fmtBits(n.bits_sent)}</td><td>${fmtEnergy(n.energy_sent_j)}</td></tr>`).join("");
  document.querySelectorAll("#nodeRows tr[data-node]").forEach(row => row.addEventListener("click", () => {
    state.selectedNode = row.dataset.node;
    updateSelectedNode();
  }));
}

function updateSelectedNode() {
  const box = $("selectedNode");
  if (!state.selectedNode || !state.frame || !state.meta) {
    box.className = "selected-empty";
    box.textContent = "Select a Cluster Head or Sensor Node on the map.";
    $("selectedRole").textContent = "None";
    return;
  }
  const positions = currentDisplayPositions();
  const p = positions.find(x => x.name === state.selectedNode);
  if (!p) return;
  const spec = state.meta.nodes.find(x => x.name === state.selectedNode);
  const metric = state.metrics?.nodes.find(x => x.node_name === state.selectedNode);
  const rel = replayRelationships(state.currentTime);
  const eligible = positions.filter(other => other.name !== p.name && roleCompatible(spec, state.meta.nodes.find(n => n.name === other.name)));
  const outbound = eligible.filter(other => linkCondition(Math.hypot(p.x-other.x,p.y-other.y),spec).reachable);
  const inbound = eligible.filter(other => {
    const otherSpec = state.meta.nodes.find(n => n.name === other.name);
    return linkCondition(Math.hypot(p.x-other.x,p.y-other.y),otherSpec).reachable;
  });
  const ownAssociation = rel.associations.get(p.name);
  const associatedSensors = p.role === 1 ? [...rel.associations].filter(([sn,r]) => r.ch === p.name) : [];
  const peers = p.role === 1 ? [...rel.peers].filter(([key]) => key.split("|").includes(p.name)) : [];
  const jobs = p.role === 1 ? "Receives sensor observations · trains locally · aggregates and sends model updates" : "Collects observations · JOINs one Cluster Head · transmits SENSOR_DATA";
  $("selectedRole").textContent = p.role === 1 ? "CLUSTER HEAD" : "SENSOR NODE";
  box.className = "selected-body";
  box.innerHTML = `<div class="selected-name">${escapeHtml(p.name)}</div>
    <p class="role-description">${jobs}</p>
    <div class="kv">
      <span>Address</span><strong class="mono">${escapeHtml(spec?.ip)}:${escapeHtml(spec?.port)}</strong>
      <span>Position</span><strong>${p.x.toFixed(1)}, ${p.y.toFixed(1)} m</strong>
      <span>TX range</span><strong>${spec?.max_tx_range_m ? Number(spec.max_tx_range_m).toFixed(1) + " m" : "RSSI-derived"}</strong>
      <span>Eligible outbound reachable</span><strong>${outbound.length}</strong>
      <span>Eligible inbound reachable</span><strong>${inbound.length}</strong>
      <span>${p.role === 1 ? "Logged sensor associations" : "Logged association"}</span>
      <strong>${!state.relationships.available ? "Unknown (logging off)" : p.role === 1 ? associatedSensors.length : (ownAssociation ? escapeHtml(ownAssociation.ch) : "Not observed")}</strong>
      ${p.role === 1 ? `<span>Observed CH exchange peers</span><strong>${peers.length}</strong>` : `<span>CH-side accepted data</span><strong>${ownAssociation ? (ownAssociation.confirmedAt == null ? "Not yet observed" : "Yes") : "Unknown"}</strong>`}
      <span>TX packets</span><strong>${metric ? fmtInt(metric.packets_sent) : "—"}</strong>
      <span>RX packets</span><strong>${metric ? fmtInt(metric.packets_received) : "—"}</strong>
      <span>Delivery drops</span><strong>${metric ? fmtInt(metric.delivery_drops) : "—"}</strong>
    </div>
    <div class="selected-note">Reachability is calculated from the current mobility tick. Logged relationships are historical observations; they do not guarantee a node is still associated or reachable.</div>`;
}

function canvasNodeAt(clientX, clientY, maxDistance = 14) {
  if (!state.frame || !state.meta) return null;
  const rect = canvas.getBoundingClientRect();
  const mx = clientX - rect.left, my = clientY - rect.top;
  const layout = topologyLayout(rect);
  let best = null, bestD = maxDistance;
  for (const p of currentDisplayPositions()) {
    const [x, y] = layout.point(p);
    const d = Math.hypot(mx - x, my - y);
    if (d < bestD) { bestD = d; best = p.name; }
  }
  return best;
}

function updatePlayback(ts) {
  state.lastAnimationTs = ts;
  if (state.meta && state.playing) {
    syncPlaybackTime(ts);
    if (state.currentTime >= state.meta.duration_s) {
      state.currentTime = state.meta.duration_s;
      state.playing = false;
      state.playAnchorWallMs = null;
      $("playButton").textContent = "▶ Play";
    }
  }

  // Exact discrete replay: select the stored trace point for this tick. No interpolation and
  // no /api/frame request is made during playback.
  setFrameFromTrace();

  if (ts - state.lastEventRefreshWallMs > 750) {
    state.lastEventRefreshWallMs = ts;
    refreshVisibleEvents();
  }
  drawTopology();
  drawTrafficChart();
  if (state.pointerAt && !state.pointer.active && ts - state.lastHoverRefreshTs > 250) {
    state.lastHoverRefreshTs = ts;
    const {x,y} = state.pointerAt;
    if (!canvasNodeAt(x,y,7)) {
      state.hoverLink = hitLinkAt(x,y);
      showLinkHover(state.hoverLink,x,y);
    }
  }
  requestAnimationFrame(updatePlayback);
}


async function periodic() {
  if (state.periodicBusy) {
    setTimeout(periodic, 1500);
    return;
  }
  state.periodicBusy = true;
  try {
    await refreshRuns();
    await refreshMeta();
    if (state.run && state.meta) {
      await Promise.all([refreshMetrics(), refreshTimeline(), refreshRelationships()]);
    }
  } finally {
    state.periodicBusy = false;
    setTimeout(periodic, 1500);
  }
}

$("runSelect").addEventListener("change", async e => {
  state.run = e.target.value || null;
  state.playing = false;
  state.playAnchorWallMs = null;
  $("playButton").textContent = "▶ Play";
  state.meta = null;
  state.frame = null;
  state.mobilityTrace = null;
  state.currentTick = -1;
  state.metrics = null;
  state.timeline = [];
  state.frameRequestKey = null;
  state.selectedNode = null;
  state.hoverLink = null; state.pinnedLink = null;
  resetRelationships();
  resetView();
  await refreshMeta();
  if (state.meta) await Promise.all([refreshMetrics(), refreshTimeline()]);
});
$("playButton").addEventListener("click", () => {
  if (!state.meta || !state.mobilityTrace) return;
  if (state.playing) {
    syncPlaybackTime();
    state.playing = false;
    state.playAnchorWallMs = null;
  } else {
    if (state.currentTime >= state.meta.duration_s) {
      state.currentTime = 0;
      setFrameFromTrace(true);
    }
    state.playing = true;
    rebasePlaybackClock();
  }
  $("playButton").textContent = state.playing ? "❚❚ Pause" : "▶ Play";
});
$("timeSlider").addEventListener("input", e => {
  state.currentTime = Number(e.target.value);
  state.currentTick = -1;
  setFrameFromTrace(true);
  if (state.playing) rebasePlaybackClock();
  refreshVisibleEvents();
});
$("speedSelect").addEventListener("change", e => {
  if (state.playing) syncPlaybackTime();
  state.speed = Number(e.target.value);
  if (state.playing) rebasePlaybackClock();
});
function selectLinkMode(mode) {
  if (!["protocol", "radio", "traffic", "none"].includes(mode)) return;
  state.linkMode = mode;
  $("linkMode").value = mode;
  document.querySelectorAll(".mode-button[data-mode]").forEach(btn => {
    const active = btn.dataset.mode === mode;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
  state.hoverLink = null;
  setPinnedLink(null);
  showLinkHover(null);
  updateSelectedNode();
  drawTopology();
}
$("linkMode").addEventListener("change", e => selectLinkMode(e.target.value));
document.querySelectorAll(".mode-button[data-mode]").forEach(btn => btn.addEventListener("click", () => selectLinkMode(btn.dataset.mode)));
window.addEventListener("click", e => {
  const layer = $("layerMenu");
  if (layer?.open && !layer.contains(e.target)) layer.open = false;
});
$("showBackbone").addEventListener("change", () => drawTopology());
$("showCandidates").addEventListener("change", () => drawTopology());
$("showLabels").addEventListener("change", () => drawTopology());
$("eventFilter").addEventListener("change", renderEventTable);
$("nodeSearch").addEventListener("input", renderNodeTable);
document.querySelectorAll(".tab-button[data-target]").forEach((button) => button.addEventListener("click", () => setWorkbenchTab(button.dataset.target)));
$("sidebarToggle").addEventListener("click", () => setSidebarCollapsed(!state.ui.sidebarCollapsed));
$("canvasFocusToggle").addEventListener("click", () => setFocusMode(!state.ui.focusMode));

$("zoomIn").addEventListener("click", () => setZoom(state.view.zoom * 1.25));
$("zoomOut").addEventListener("click", () => setZoom(state.view.zoom / 1.25));
$("zoomReset").addEventListener("click", resetView);
$("fitNodes").addEventListener("click", fitNodes);
canvas.addEventListener("wheel", e => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const factor = Math.exp(-e.deltaY * 0.0015);
  setZoom(state.view.zoom * factor, mx, my);
}, {passive: false});
canvas.addEventListener("dblclick", e => { e.preventDefault(); resetView(); });
canvas.addEventListener("pointerdown", e => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  state.pointer.active = true;
  state.pointer.id = e.pointerId;
  state.pointer.startX = e.clientX - rect.left;
  state.pointer.startY = e.clientY - rect.top;
  state.pointer.startPanX = state.view.panX;
  state.pointer.startPanY = state.view.panY;
  state.pointer.moved = false;
  canvas.setPointerCapture(e.pointerId);
  canvas.classList.add("panning");
});
canvas.addEventListener("pointermove", e => {
  if (!state.pointer.active) {
    state.pointerAt = {x:e.clientX, y:e.clientY};
    const link = hitLinkAt(e.clientX, e.clientY);
    // Actual node glyphs remain selectable, but links can be inspected even when
    // nodes are closely clustered in the field.
    const node = canvasNodeAt(e.clientX, e.clientY, link ? 7 : 14);
    state.hoveredNode = node;
    const tooltip = $("nodeTooltip");
    if (node) {
      state.hoverLink = null;
      showLinkHover(null);
      const spec = state.meta?.nodes.find(n => n.name === node);
      tooltip.innerHTML = `<strong>${escapeHtml(node)}</strong><span>${spec?.role === 1 ? "Cluster Head" : "Sensor Node"}</span><small>Click to inspect role, observed associations, and traffic.</small>`;
      tooltip.hidden = false;
      const rect = canvas.getBoundingClientRect();
      tooltip.style.left = `${clamp(e.clientX - rect.left + 16, 8, Math.max(8,rect.width-240))}px`;
      tooltip.style.top = `${clamp(e.clientY - rect.top + 16, 8, Math.max(8,rect.height-110))}px`;
    } else {
      tooltip.hidden = true;
      state.hoverLink = link;
      showLinkHover(state.hoverLink, e.clientX, e.clientY);
    }
    canvas.style.cursor = node || state.hoverLink ? "pointer" : "grab";
    return;
  }
  if (e.pointerId !== state.pointer.id) return;
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  const dx = x - state.pointer.startX, dy = y - state.pointer.startY;
  if (Math.hypot(dx, dy) > 3) state.pointer.moved = true;
  state.view.panX = state.pointer.startPanX + dx;
  state.view.panY = state.pointer.startPanY + dy;
});
function finishPointer(e) {
  if (!state.pointer.active || e.pointerId !== state.pointer.id) return;
  const moved = state.pointer.moved;
  state.pointer.active = false;
  canvas.classList.remove("panning");
  try { canvas.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
  if (!moved) {
    const edge = hitLinkAt(e.clientX, e.clientY);
    const node = canvasNodeAt(e.clientX, e.clientY, edge ? 7 : 14);
    if (node) {
      state.selectedNode = node;
      setPinnedLink(null);
    } else {
      if (edge) setPinnedLink(edge);
      else { state.selectedNode = null; setPinnedLink(null); }
    }
    updateSelectedNode();
  }
}
canvas.addEventListener("pointerleave", () => { state.pointerAt=null; state.hoveredNode = null; state.hoverLink = null; $("nodeTooltip").hidden = true; showLinkHover(null); });
canvas.addEventListener("pointerup", finishPointer);
canvas.addEventListener("pointercancel", finishPointer);
window.addEventListener("resize", () => { drawTopology(); drawTrafficChart(); });
window.addEventListener("keydown", e=> { if(e.key==="Escape") { setPinnedLink(null); $("layerMenu").open=false; } });

(async function start() {
  setSidebarCollapsed(false);
  setFocusMode(false);
  setWorkbenchTab("trafficPanel");
  selectLinkMode("protocol");
  await refreshRuns();
  await refreshMeta();
  requestAnimationFrame(updatePlayback);
  periodic();
})();
