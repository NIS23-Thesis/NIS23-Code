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
  frameRequestKey: null,
  eventRequestInFlight: false,
  lastEventRefreshWallMs: 0,
  view: {zoom: 1, panX: 0, panY: 0},
  pointer: {active: false, id: null, startX: 0, startY: 0, startPanX: 0, startPanY: 0, moved: false},
  periodicBusy: false,
  latestRunName: null,
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
      state.frameRequestKey = null;
      state.mobilityTrace = null;
      state.currentTick = -1;
      state.playing = false;
      state.playAnchorWallMs = null;
      $("playButton").textContent = "▶ Play";
      resetView();
      $("showLinks").checked = false;
      await refreshTrace();
      await refreshVisibleEvents();
    } else if (!state.frame && state.mobilityTrace) {
      setFrameFromTrace(true);
    }
  } catch (error) {
    console.error(error);
  }
}


function applyLoggingVisibility() {
  const logging = state.meta?.logging || {};
  const packetEvents = Boolean(logging.packet_events);
  const protocolEvents = Boolean(logging.protocol_events);
  const trafficPanel = $("trafficPanel");
  const eventsPanel = $("eventsPanel");
  if (trafficPanel) trafficPanel.hidden = !packetEvents;
  if (eventsPanel) eventsPanel.hidden = !(packetEvents || protocolEvents);
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
    state.packetEvents = state.visibleEvents.filter(e => ["scheduled_delivery", "delivery_drop"].includes(e.event) && e.source && e.target);
    renderEventTable();
  } catch (error) {
    console.error(error);
  } finally {
    state.eventRequestInFlight = false;
  }
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
  ctx.fillStyle = "#090d12";
  ctx.fillRect(0, 0, w, h);
  if (!state.frame || !state.meta) return;

  const layout = topologyLayout(rect);
  const {fieldW, fieldH, fieldPoint, point} = layout;
  ctx.strokeStyle = "rgba(143,161,181,.10)";
  ctx.lineWidth = 1;
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
  ctx.strokeStyle = "rgba(143,161,181,.34)";
  ctx.strokeRect(tlx, tly, brx - tlx, bry - tly);

  const positions = currentDisplayPositions();
  let reachableCount = 0, poorCount = 0, goodCount = 0, strongCount = 0;

  // Topology links are intentionally selection-scoped. This keeps the mobility view
  // readable and avoids O(N²) pair rendering for large scenarios.
  if (state.selectedNode && $("showLinks").checked) {
    const selected = positions.find(p => p.name === state.selectedNode);
    const selectedSpec = state.meta.nodes.find(n => n.name === state.selectedNode);
    if (selected && selectedSpec) {
      const [ax, ay] = point(selected);
      for (const other of positions) {
        if (other.name === selected.name) continue;
        const d = Math.hypot(selected.x - other.x, selected.y - other.y);
        const c = linkCondition(d, selectedSpec);
        if (!c.reachable) continue;
        reachableCount++;
        if (c.band === "poor") poorCount++; else if (c.band === "good") goodCount++; else strongCount++;
        ctx.strokeStyle = c.band === "poor" ? "rgba(255,209,102,.34)" : c.band === "good" ? "rgba(92,200,255,.30)" : "rgba(93,224,160,.28)";
        ctx.lineWidth = 1.5;
        const [bx, by] = point(other);
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
      }
    }
  }

  for (const p of positions) {
    const [x, y] = point(p);
    const selected = p.name === state.selectedNode;
    if (selected) {
      ctx.beginPath(); ctx.arc(x, y, 12, 0, Math.PI * 2); ctx.fillStyle = "rgba(255,255,255,.10)"; ctx.fill();
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1; ctx.stroke();
    }
    if (p.role === 1) {
      const size = selected ? 7 : 6;
      ctx.fillStyle = "#9b8cff"; ctx.strokeStyle = "#d9d0ff"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.rect(x - size, y - size, size * 2, size * 2); ctx.fill(); ctx.stroke();
    } else {
      ctx.fillStyle = "#5cc8ff"; ctx.strokeStyle = "#b9eaff"; ctx.lineWidth = .8;
      ctx.beginPath(); ctx.arc(x, y, selected ? 5.5 : 4.3, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
    if ($("showLabels").checked || selected) {
      ctx.fillStyle = "#dbe7f4"; ctx.font = "11px ui-sans-serif, system-ui"; ctx.fillText(p.name, x + 8, y - 7);
    }
  }

  $("canvasCorner").textContent = state.selectedNode
    ? `${state.selectedNode}: ${reachableCount} outbound reachable · ${strongCount} strong · ${goodCount} good · ${poorCount} poor`
    : "Click a node to show its outbound reachable links";
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
    const type = e.packet_type == null ? "—" : `T${e.packet_type}${e.sequence_number == null ? "" : ` / #${e.sequence_number}`}`;
    return `<tr><td class="mono">${e.time_s >= 0 ? e.time_s.toFixed(3) : "—"}</td><td><span class="badge ${klass}">${e.event}</span></td><td>${e.source || e.node || "—"}</td><td>${e.target || "—"}</td><td class="mono">${type}</td><td class="muted">${link}${e.reason ? ` · ${e.reason}` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">No events in this time window.</td></tr>`;
}

function renderNodeTable() {
  if (!state.metrics) return;
  const q = $("nodeSearch").value.trim().toLowerCase();
  const rows = state.metrics.nodes.filter(n => !q || n.node_name.toLowerCase().includes(q));
  $("nodeRows").innerHTML = rows.map(n => `<tr data-node="${n.node_name}"><td><strong>${n.node_name}</strong></td><td>${n.role === 1 ? "Cluster Head" : "Sensor Node"}</td><td>${fmtInt(n.packets_sent)}</td><td>${fmtInt(n.packets_received)}</td><td>${fmtInt(Number(n.delivery_drops || 0) + Number(n.packets_dropped || 0))}</td><td>${fmtBits(n.bits_sent)}</td><td>${fmtEnergy(n.energy_sent_j)}</td></tr>`).join("");
  document.querySelectorAll("#nodeRows tr[data-node]").forEach(row => row.addEventListener("click", () => {
    state.selectedNode = row.dataset.node;
    updateSelectedNode();
  }));
}

function updateSelectedNode() {
  const box = $("selectedNode");
  if (!state.selectedNode || !state.frame || !state.meta) {
    box.className = "selected-empty";
    box.textContent = "Click a node in the topology.";
    $("selectedRole").textContent = "None";
    return;
  }
  const displayPositions = currentDisplayPositions();
  const p = displayPositions.find(x => x.name === state.selectedNode);
  if (!p) return;
  const spec = state.meta.nodes.find(x => x.name === state.selectedNode);
  const metric = state.metrics?.nodes.find(x => x.node_name === state.selectedNode);
  let neighbors = 0, strong = 0, good = 0, poor = 0;
  for (const other of displayPositions) {
    if (other.name === p.name) continue;
    const c = linkCondition(Math.hypot(p.x - other.x, p.y - other.y), spec);
    if (c.reachable) {
      neighbors++;
      if (c.band === "strong") strong++; else if (c.band === "good") good++; else poor++;
    }
  }
  $("selectedRole").textContent = spec?.role_name || "—";
  box.className = "selected-body";
  box.innerHTML = `<div class="selected-name">${p.name}</div><div class="kv"><span>Address</span><strong class="mono">${spec?.ip}:${spec?.port}</strong><span>Position</span><strong>${p.x.toFixed(2)}, ${p.y.toFixed(2)} m</strong><span>Max TX range</span><strong>${spec?.max_tx_range_m ? Number(spec.max_tx_range_m).toFixed(1) + " m" : "—"}</strong><span>Outbound reachable</span><strong>${neighbors}</strong><span>Link bands</span><strong>${strong} / ${good} / ${poor}</strong><span>TX packets</span><strong>${metric ? fmtInt(metric.packets_sent) : "—"}</strong><span>RX packets</span><strong>${metric ? fmtInt(metric.packets_received) : "—"}</strong><span>Delivery drops</span><strong>${metric ? fmtInt(metric.delivery_drops) : "—"}</strong></div>`;
}

function canvasNodeAt(clientX, clientY) {
  if (!state.frame || !state.meta) return null;
  const rect = canvas.getBoundingClientRect();
  const mx = clientX - rect.left, my = clientY - rect.top;
  const layout = topologyLayout(rect);
  let best = null, bestD = 14;
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
      await Promise.all([refreshMetrics(), refreshTimeline()]);
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
$("eventFilter").addEventListener("change", renderEventTable);
$("nodeSearch").addEventListener("input", renderNodeTable);

$("zoomIn").addEventListener("click", () => setZoom(state.view.zoom * 1.25));
$("zoomOut").addEventListener("click", () => setZoom(state.view.zoom / 1.25));
$("zoomReset").addEventListener("click", resetView);
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
  if (!state.pointer.active || e.pointerId !== state.pointer.id) return;
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
    const node = canvasNodeAt(e.clientX, e.clientY);
    if (node) {
      state.selectedNode = node;
      $("showLinks").checked = true;
    } else {
      state.selectedNode = null;
      $("showLinks").checked = false;
    }
    updateSelectedNode();
  }
}
canvas.addEventListener("pointerup", finishPointer);
canvas.addEventListener("pointercancel", finishPointer);
window.addEventListener("resize", () => { drawTopology(); drawTrafficChart(); });

(async function start() {
  await refreshRuns();
  await refreshMeta();
  requestAnimationFrame(updatePlayback);
  periodic();
})();
