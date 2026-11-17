"use strict";

const state = {
  run: new URLSearchParams(window.location.search).get("run") || window.CLIVERunSelection?.read() || null,
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
  packetRequestInFlight: false,
  lastPacketMarkerKey: "",
  lastPacketMarkerAt: 0,
  trafficHoverBucket: null,
  trafficBucketS: 1,
  relationships: {available: false, events: [], truncated: false},
  relationshipRevision: "",
  stageTimeline: [],
  relationshipCache: CliveConnectionTracker.empty(),
  linkMode: "protocol",
  hoveredNode: null,
  frameRequestKey: null,
  eventRequestInFlight: false,
  eventRequestPending: false,
  eventResult: null,
  lastEventRefreshWallMs: 0,
  lastEventQuerySignature: "",
  lastEventQueryAt: 0,
  lastTopologyDrawAt: 0,
  lastTrafficDrawAt: 0,
  view: {zoom: 1, panX: 0, panY: 0},
  pointer: {active: false, id: null, startX: 0, startY: 0, startPanX: 0, startPanY: 0, moved: false},
  periodicBusy: false,
  latestRunName: null,
  guide: CliveGuidedPlayback.create(),
  guidedPlayback: false,
  ui: {activeTab: "trafficPanel", sidebarCollapsed: false, focusMode: false},
};

const $ = (id) => document.getElementById(id);
const canvas = $("topologyCanvas");
const ctx = canvas.getContext("2d");
const trafficCanvas = $("trafficCanvas");
const tctx = trafficCanvas.getContext("2d");
const trafficTooltip = $("trafficTooltip");

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
  const response = await fetch(visualizerApiPath(path), {cache: "no-store"});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || response.statusText);
  return value;
};
const visualizerApiPath = path => location.pathname.startsWith("/topology/") ? path.replace(/^\/api(?=\/)/, "/api/viz") : path;
const runQueryFor = (run) => run ? `run=${encodeURIComponent(run)}` : "";
const runQuery = () => runQueryFor(state.run);
const escapeHtml = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
const PACKET_NAMES = ["HELLO", "LEAVE", "SENSOR_DATA", "SENSOR_DATA_ACK", "MODEL_UPDATE", "MODEL_UPDATE_ACK", "JOIN", "JOIN_ACCEPT"];
const packetName = (n) => n == null ? "—" : (PACKET_NAMES[Number(n)] || `TYPE ${n}`);
// The visualizer displays both protocol identity and local emulated UDP address.
// Never infer an IP from node names: scenarios can use arbitrary overlay mapping.
function nodeSpec(name) { return state.meta?.nodes?.find(n => n.name === name) || null; }
function nodeIdentity(name, mode = $("nodeLabelMode")?.value || "name") {
  const spec = nodeSpec(name);
  const overlay = spec?.overlay_ip || spec?.ip || "IP unavailable";
  const underlay = spec?.underlay_ip || spec?.ip || "IP unavailable";
  return mode === "overlay" ? overlay : mode === "underlay" ? underlay
    : mode === "both" ? `${name} · ${overlay}` : mode === "none" ? "" : name;
}
const VIEW_DESCRIPTIONS = Object.freeze({
  protocol: "Recorded FLAME association and recent model activity; not predicted reachability.",
  radio: "Calculated one-hop radio possibility, based on CLIVE's link model. Dotted lines are not confirmed packet deliveries.",
  traffic: "Logged NodeSocket delivery / drop outcomes near the current playback time.",
  none: "Only recorded positions. All telemetry remains in Diagnostics.",
});
function syncLayerUI() {
  const mode = state.linkMode;
  document.querySelectorAll("[data-layer-for]").forEach(el => {
    el.hidden = !el.dataset.layerFor.split(/\s+/).includes(mode);
  });
  const legend = $("legendLinks");
  if (legend) legend.textContent = ({
    protocol: "Green = accepted membership · red dashed = out of range · purple = recorded CH model exchange",
    radio: "Blue/green/amber dashed = calculated reachability (strong/good/weak). Not an observed connection.",
    traffic: "Colored dots = recorded packet events · × = recorded drop",
    none: "Node positions only · CH squares, SN circles",
  })[mode];
  const notice = $("modeExplanation");
  if (notice) notice.textContent = VIEW_DESCRIPTIONS[mode];
}


function visibleWorkbenchTargets() {
  // Diagnostics remain navigable even if packet logging was disabled.
  // Showing an empty panel with an explanation is better than hiding it.
  return ["trafficPanel", "eventsPanel", "nodesPanel"];
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
  if (target === "eventsPanel") refreshVisibleEvents();
}

function syncWorkbenchVisibility() {
  // Events can be present in the compact connection log even with packet logging off.
  // Never hide a tab based on the scenario's logging switches.
  $("tab-traffic").hidden = false;
  $("tab-events").hidden = false;
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
  if (label) label.textContent = state.ui.sidebarCollapsed ? "Show inspector" : "Hide inspector";
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
      option.textContent = `${run.name}`;
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
    // Select the most recent run on first load. Once the user has chosen a run,
    // never unexpectedly replace it during a polling refresh.
    state.latestRunName = newest;
    if (!state.run || !data.runs.some(r => r.name === state.run)) {
      state.run = newest;
      switched = true;
    }

    select.value = state.run;
    window.CLIVERunSelection?.write(state.run);
    if (switched || previousRun !== state.run) {
      state.meta = null;
      state.frame = null;
      state.mobilityTrace = null;
      state.currentTick = -1;
      state.metrics = null;
      state.timeline = [];
      state.packetEvents = [];
      state.lastPacketMarkerKey = "";
      state.trafficHoverBucket = null;
      trafficTooltip.hidden = true;
      state.selectedNode = null;
      state.guide = CliveGuidedPlayback.create();
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
    const eventNode = $("eventNode");
    if (eventNode) {
      const keep = eventNode.value;
      const names = meta.nodes.map(n => n.name).filter(Boolean).sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
      eventNode.replaceChildren();
      eventNode.add(new Option("All nodes", ""));
      names.forEach(name => eventNode.add(new Option(name, name)));
      eventNode.value = names.includes(keep) ? keep : "";
    }
    setStatus(meta.manifest?.status || "waiting");
    $("topologySubtitle").textContent = `${meta.nodes.length} nodes · ${meta.mobility.area_width_m} × ${meta.mobility.area_height_m} m · ${meta.mobility.model.replaceAll("_", " ")} · ${meta.mobility.initial_topology} topology · position interval ${meta.tick_interval_s}s`;
    $("mNodes").textContent = fmtInt(meta.nodes.length);
    $("configRun").textContent = meta.run;
    $("learningPageLink").href = `learning.html?run=${encodeURIComponent(meta.run)}`;
    $("eventsPageLink").href = `events.html?run=${encodeURIComponent(meta.run)}`;
    $("timeSlider").max = meta.duration_s;
    $("timeSlider").step = "0.000001"; // 1 µs seek increments; recorded timing precision is bounded by source clock, not radio physics.
    renderConfig();
    applyLoggingVisibility();
    if (changed) {
      state.currentTime = 0;
      state.selectedNode = null;
      state.visibleEvents = [];
      state.eventResult = null;
      state.packetEvents = [];
      state.lastPacketMarkerKey = "";
      state.trafficHoverBucket = null;
      trafficTooltip.hidden = true;
      resetRelationships();
      state.frameRequestKey = null;
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
    ["Training workload", state.meta.flame?.trainer === "mean_pixel" ? "PROXY (not neural-network training)" : "PyTorch MNIST (if FLAME selected)"],
    ["MNIST data assignment", state.meta.flame?.mnist_distribution === "sensor_id" ? "Sensor ID → digit (legacy)" : "Initial geographic region → digit (spatial clusters)"],
    ["Sample repeat policy", state.meta.flame?.repeat_sensor_samples ? "Repeats enabled (transport stress)" : "Stop after sample pool exhaustion"],
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
  $("configList").innerHTML = entries.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("");
}

async function refreshTrace() {
  if (!state.meta || !state.run) return;
  const run = state.run;
  try {
    const response = await fetch(visualizerApiPath(`/api/mobility.bin?${runQueryFor(run)}`), {cache: "no-store"});
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
    const outcome = metrics.flame_outcome;
    $("mModels").textContent = outcome?.available ? fmtInt(outcome.model_transfers_completed) : "—";
    const notice = $("protocolHealth");
    if (notice) {
      if (outcome?.available) {
        const m = outcome;
        const mnistConfigured = (m.real_neural_network_trainer_configured ?? (m.trainer === "mnist"));
        const kind = !mnistConfigured ? "Proxy workload · "
          : m.real_neural_network_training ? "MNIST training observed · "
          : "MNIST trainer configured; no training observed · ";
        const accuracy = Number.isFinite(m.final_mnist_accuracy_mean)
          ? `Held-out MNIST accuracy: ${(100 * m.final_mnist_accuracy_mean).toFixed(2)}% (mean across evaluated CHs). `
          : "Held-out MNIST accuracy: not measured. ";
        notice.textContent = `${kind}Model delivery: ${m.model_transfers_completed}/${m.model_transfers_started} complete; ` +
          `${m.model_transfers_failed} failed, ${m.model_transfers_unfinished} unfinished, ` +
          `${m.model_transfers_superseded || 0} superseded (no cached retry). ` +
          accuracy;
      } else {
        notice.textContent = "FLAME model-delivery outcome unavailable.";
      }
    }
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
    state.trafficBucketS = Number(data.bucket_s) || 1;
    state.timelineTotals = data.totals || {sent: 0, received: 0, dropped: 0};
    state.hasPacketTraffic = Boolean(data.has_packet_traffic);
    $("trafficSummary").textContent = `TX ${fmtInt(state.timelineTotals.sent)} · RX ${fmtInt(state.timelineTotals.received)} · drop ${fmtInt(state.timelineTotals.dropped)}`;
    updateTrafficEmptyState();
  } catch (error) {
    console.error(error);
  }
}

// A time-window query scans indexed on-disk JSONL, not the capped live-event cache.
// Filtering is server-side *before* the 160-row presentation limit.
async function refreshVisibleEvents() {
  if (!state.run || !state.meta) return;
  if (state.eventRequestInFlight) {
    state.eventRequestPending = true;
    return;
  }
  const run = state.run;
  const selectedTime = state.currentTime;
  const windowChoice = "8";
  const windowS = windowChoice === "all" ? state.meta.duration_s : Number(windowChoice);
  const from = Math.max(0, selectedTime - windowS);
  const to = Math.min(state.meta.duration_s, selectedTime + 0.10);
  const filter = $("eventFilter").value;
  const search = $("eventSearch")?.value?.trim() || "";
  const order = $("eventSort").value;
  const node = $("eventNode").value;
  const packet = $("eventPacket").value;
  const outcome = $("eventOutcome").value;
  const query = new URLSearchParams({run, from_s: String(from), to_s: String(to),
    category: filter, search, order, node, packet, outcome, limit: "160"});
  const signature = query.toString();
  const nowMs = performance.now();
  // Completed recordings are immutable. Do not repeatedly scan the same
  // event window on every animation tick when playback is paused.
  if (signature === state.lastEventQuerySignature && state.eventResult &&
      (state.meta?.manifest?.status !== "running" ||
       nowMs - state.lastEventQueryAt < 1800)) return;
  // Mark the request in-flight only after the cached-result early return.
  // Otherwise a paused playback can permanently lock the Event Stream.
  state.eventRequestInFlight = true;
  try {
    const data = await api(`/api/events?${query}`);
    if (run !== state.run) return;
    // If playback moved substantially during the request, fetch the newer window.
    if (Math.abs(state.currentTime - selectedTime) > 1 ||
        $("eventFilter").value !== filter ||
        $("eventSort").value !== order ||
        $("eventNode").value !== node ||
        $("eventPacket").value !== packet ||
        $("eventOutcome").value !== outcome ||
        ($("eventSearch")?.value?.trim() || "") !== search) {
      state.eventRequestPending = true;
      return;
    }
    state.lastEventQuerySignature = signature;
    state.lastEventQueryAt = performance.now();
    state.eventResult = data;
    state.visibleEvents = data.events || [];
    // Do not derive animated traffic from the Event tab's selected filter.
    // Viewing PROTOCOL must never make real network traffic disappear.
    renderEventTable();
  } catch (error) {
    if (run === state.run) {
      $("eventSummary").textContent = `Could not load events: ${error.message}`;
      console.error("Event Stream request failed", error);
    }
  } finally {
    state.eventRequestInFlight = false;
    if (state.eventRequestPending) {
      state.eventRequestPending = false;
      refreshVisibleEvents();
    }
  }
}

// Only logged FLAME events can establish an observed association or model exchange.
// The playback cache is incremental; moving the slider backwards rebuilds it.
function resetRelationships() {
  state.relationships = {available: false, events: [], truncated: false};
  state.relationshipRevision = "";
  state.stageTimeline = [];
  state.relationshipCache = CliveConnectionTracker.empty();
  state.guide = CliveGuidedPlayback.create();
  $("nextAction").disabled = true;
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
      state.stageTimeline = CliveMapActivity.timeline(events);
      $("nextAction").disabled = state.stageTimeline.length === 0;
      state.relationshipRevision = rev;
      state.relationshipCache = CliveConnectionTracker.empty();
      state.guide = CliveGuidedPlayback.seek(state.stageTimeline, state.currentTime, performance.now());
      if (state.playing) rebasePlaybackClock();
      syncWorkbenchVisibility();
      updateSelectedNode();
    }
  } catch (error) { console.error(error); }
}

function replayRelationships(timeS) {
  let snapshot = state.relationshipCache;
  if (timeS < snapshot.time) {
    snapshot = CliveConnectionTracker.empty();
    state.relationshipCache = snapshot;
  }
  const events = state.relationships.events;
  while (snapshot.cursor < events.length && events[snapshot.cursor].time_s <= timeS) {
    CliveConnectionTracker.apply(snapshot, events[snapshot.cursor++]);
  }
  snapshot.time = timeS;
  return snapshot;
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
  const color = (!ab && !ba) ? "rgba(255,107,122,.78)" :
    type === "peer" ? themeCanvas("rgba(174,152,255,.87)", "rgba(104,72,186,.85)") :
    observed ? (confirmed ? "rgba(93,224,160,.83)" : "rgba(92,200,255,.76)") :
    type === "backbone" ? "rgba(155,140,255,.25)" :
    edgeColor(ab ? pair.ab.band : pair.ba.band, emphasize ? .86 : (window.CLIVETheme?.isLight() ? .76 : .52));
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = observed ? (emphasize ? 2.8 : 2.1) : emphasize ? 2.1 : 1.45;
  if (!observed) ctx.setLineDash([5, 5]);
  if (observed && !ab && !ba) ctx.setLineDash([3, 5]);
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  ctx.setLineDash([]);
  // Only predicted radio links get direction arrows here. Membership is state,
  // not a claim that a datagram was transmitted in either direction.
  if (!observed && $("showDirectionArrows")?.checked && ab) drawArrow(x1, y1, x2, y2, .63, color, emphasize ? 1.15 : .9);
  if (!observed && $("showDirectionArrows")?.checked && ba) drawArrow(x2, y2, x1, y1, .63, color, emphasize ? 1.15 : .9);
  ctx.restore();
  if ($("showLinkLabels")?.checked) drawLinkLabel(a, b, x1, y1, x2, y2, observed, confirmed);
  return true;
}

/** Optional map layer, never a second competing popover. */
function drawLinkLabel(a, b, x1, y1, x2, y2, observed, confirmed) {
  const text = `${nodeIdentity(a.name) || a.name} ↔ ${nodeIdentity(b.name) || b.name}`;
  const x = (x1 + x2) / 2, y = (y1 + y2) / 2;
  ctx.save();
  ctx.font = '11px ui-sans-serif, system-ui';
  const width = ctx.measureText(text).width + 17;
  const height = 20;
  ctx.fillStyle = themeCanvas('rgba(10, 24, 36, .94)', 'rgba(255,255,255,.96)');
  ctx.strokeStyle = observed ? (confirmed ? '#5de0a0' : '#5cc8ff') : '#697d91';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(x-width/2, y-height/2, width, height, 5);
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = themeCanvas('#e3eff8', '#1c344b');
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, x, y+.5);
  ctx.restore();
}

function updateLinkExplanation(snapshot, selected, stats) {
  const mode = state.linkMode;
  const notice = $("linkNotice"), summary = $("linkSummary");
  if (mode === "protocol") {
    const confirmed = [...snapshot.associations.values()].filter(r => r.confirmedAt != null).length;
    summary.textContent = `${snapshot.associations.size} recorded SN→CH memberships · ${confirmed} confirmed by sensor receipt · ${stats.backbone} recent CH model exchanges`;
    notice.textContent = state.relationships.available ? "Solid membership links come from FLAME events; they may outlast radio reachability until timeout." :
      "No FLAME relationship history for this run. Enable protocol event logging for new experiments.";
    if (state.relationships.truncated) notice.textContent += " Relationship history truncated.";
  } else if (mode === "radio") {
    const all = $("showCandidates")?.checked && (state.frame?.positions?.length ?? 0) <= 24;
    summary.textContent = all ? `${stats.radio} calculated reachable pairs (not packet deliveries)` : selected ?
      `${stats.radio} outbound possible paths from ${selected.name} (not observed)` : "Select a node to inspect its possible outgoing radio paths";
    notice.textContent = (state.frame?.positions?.length ?? 0) > 24 && $("showCandidates")?.checked ?
      "Dense topology: all-pairs display is disabled to reduce clutter. Click a node to focus its radio reachability." :
      "Dashed blue / green / amber lines represent modeled radio quality, not JOIN, reception, or a persistent connection.";
  } else if (mode === "traffic") {
    summary.textContent = `${stats.traffic} recorded packet outcomes near playback time`;
    notice.textContent = state.meta?.logging?.packet_events ? "Packet markers are recorded transport activity, not FLAME acceptance." :
      "Packet-event logging was disabled for this run; enable it in Setup before a new run.";
  } else {
    summary.textContent = "Only recorded node positions are drawn";
    notice.textContent = "Use Display settings to choose node names or IP labels.";
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


/** Show one stage of the recorded protocol timeline at a time.
 * HELLO is NEVER a link; only an actual JOIN request gets a temporary arrow.
 * The persistent connections are drawn independently by replayRelationships().
 */
function drawRecentProtocolSignals(positions, point) {
  const guidedEvent = state.guidedPlayback && state.guide.focus &&
    state.currentTime >= state.guide.focus.time_s &&
    state.currentTime - state.guide.focus.time_s <= CliveMapActivity.AGE_LIMIT
    ? CliveMapActivity.fromEvent(state.guide.focus, state.currentTime) : null;
  const event = guidedEvent || (state.relationships.available
    ? CliveMapActivity.focus(state.relationships.events, state.currentTime, state.selectedNode)
    : null);
  const status = $("mapActivityStatus");
  if (!event) {
    const message = state.relationships.available
      ? ""
      : "No recorded protocol activity is available for this run.";
    if (status.dataset.key !== `idle:${message}`) {
      status.dataset.stage = "idle";
      status.dataset.key = `idle:${message}`;
      status.innerHTML = `<span class="map-action-tag">NO ACTION AT THIS TIME</span><span class="map-action-description">${message}</span>`;
    }
    return;
  }
  const label = escapeHtml(event.label);
  const eventKey = `${event.action}|${event.time_s}|${event.source}|${event.target}|${event.otherCount}`;
  if (status.dataset.key !== eventKey) {
    status.dataset.key = eventKey;
    status.dataset.stage = event.stage;
    status.innerHTML = `<span class="map-action-tag">${label}</span><span class="map-action-time">Recorded at ${event.time_s.toFixed(6)} s</span>`+
      `<span class="map-action-description">${escapeHtml(event.description)}</span>`+
      (event.otherCount ? `<span class="map-action-more">+${event.otherCount} other recent actions in Event History</span>` : "");
  }

  const byName = new Map(positions.map(n => [n.name,n]));
  const source = byName.get(event.source), target = byName.get(event.target);
  if (!source) return;
  const [sx,sy] = point(source);
  ctx.save();
  ctx.lineWidth = 2.6;
  ctx.strokeStyle = event.color;
  ctx.fillStyle = event.color;
  // Moderate pulse to distinguish ONE stage, without covering other nodes.
  const pulse = 0.7 + .3*Math.sin(Math.max(0, event.age) * 5);
  ctx.globalAlpha = .80 + .20*pulse;
  if (event.stage === "hello") {
    // A CH broadcast can reach many nodes. Drawing individual edges implies
    // they all received it. A halo communicates announcement only.
    ctx.beginPath(); ctx.arc(sx,sy,18+5*pulse,0,2*Math.PI); ctx.stroke();
    ctx.beginPath();ctx.arc(sx,sy,11,0,2*Math.PI);ctx.stroke();
  } else if (event.stage === "request" && target) {
    // Explicitly directed sensor->CH JOIN: amber dashed arrow, NOT a link.
    const [tx,ty] = point(target);
    ctx.setLineDash([8,6]);
    ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(tx,ty);ctx.stroke();
    ctx.setLineDash([]);
    drawArrow(sx,sy,tx,ty,.72,event.color,1.7);
    ctx.beginPath();ctx.arc(sx,sy,12,0,2*Math.PI);ctx.stroke();
  } else if (event.stage === "discovered") {
    // HELLO received: mark the receiving SN only, never draw a JOIN arrow.
    const receiver = target || source, node=byName.get(receiver);
    if (node) {const [x,y]=point(node);ctx.beginPath();ctx.arc(x,y,13,0,2*Math.PI);ctx.stroke();}
  } else if (event.stage === "accepted" || event.stage === "confirmed") {
    ctx.beginPath();ctx.arc(sx,sy,15,0,2*Math.PI);ctx.stroke();
    const x=sx+13,y=sy-13;
    ctx.fillStyle="#153627";ctx.fillRect(x-8,y-9,18,18);
    ctx.strokeStyle="#70e1a9";ctx.beginPath();ctx.moveTo(x-4,y);ctx.lineTo(x-1,y+4);ctx.lineTo(x+6,y-5);ctx.stroke();
  } else if (event.stage === "exit") {
    ctx.beginPath();ctx.arc(sx,sy,15,0,2*Math.PI);ctx.stroke();
    ctx.beginPath();ctx.moveTo(sx+12,sy-18);ctx.lineTo(sx+22,sy-8);
    ctx.moveTo(sx+22,sy-18);ctx.lineTo(sx+12,sy-8);ctx.stroke();
  } else {
    // A model transfer or aggregation is an observed CH-to-CH action, not a
    // permanent association. Only draw a directed line with a recorded peer.
    if ($("showModelFlow")?.checked && target &&
        (event.stage === "model" || event.action === "flame_aggregated") &&
        source.role === 1 && target.role === 1 && source.name !== target.name) {
      const [tx,ty] = point(target);
      ctx.lineWidth = 2.4;
      ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(tx,ty);ctx.stroke();
      if ($("showModelFlow")?.checked) drawArrow(sx,sy,tx,ty,.68,event.color,1.6);
    }
    ctx.beginPath();ctx.arc(sx,sy,15,0,2*Math.PI);ctx.stroke();
  }
  ctx.restore();
}

const themeCanvas = (dark, light) => window.CLIVETheme?.canvas(dark, light) ?? dark;
function drawTopology() {
  const rect = resizeCanvas(canvas, ctx);
  const w = rect.width, h = rect.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = themeCanvas("#090d12", "#f8fbff"); ctx.fillRect(0, 0, w, h);
  if (!state.frame || !state.meta) return;
  const layout = topologyLayout(rect), {fieldW, fieldH, fieldPoint, point} = layout;
  ctx.strokeStyle = themeCanvas("rgba(143,161,181,.08)", "rgba(79,102,129,.11)"); ctx.lineWidth = 1;
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
  ctx.strokeStyle = themeCanvas("rgba(143,161,181,.35)", "rgba(79,102,129,.35)"); ctx.strokeRect(tlx, tly, brx - tlx, bry - tly);

  const positions = currentDisplayPositions();
  const specs = new Map(state.meta.nodes.map(n => [n.name, n]));
  const pos = new Map(positions.map(p => [p.name, p]));
  const selected = pos.get(state.selectedNode);
  const relationships = replayRelationships(state.currentTime);
  const stats = {backbone: 0, candidates: 0, radio: 0, traffic: 0};

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
          const last = relationships.peers.get(key);
          if (last == null || state.currentTime - last > 3.0) continue;
          const pair = radioPair(a, b, specs);
          // A model exchange is an event (purple), never a permanent backbone.
          const detail = relationships.peerDetails.get(key);
          drawLink(a, b, pair, point, {type: "peer", observed: true,
            confirmed: ["flame_model_received", "flame_transfer_complete", "flame_aggregated"].includes(detail?.action),
            emphasize: Boolean(selected && (selected.name === a.name || selected.name === b.name))});
          stats.backbone++;
          if (detail && $("showModelFlow")?.checked) {
            const src = pos.get(detail.source), dst = pos.get(detail.target);
            if (src && dst) { const [sx,sy] = point(src), [tx,ty] = point(dst); drawArrow(sx,sy,tx,ty,.70,themeCanvas("#ae98ff","#6848ba"),1.35); }
          }
        }
      }
    }
  } else if (state.linkMode === "radio") {
    // Full small-topology view or, for dense runs, the selected node's outgoing
    // candidates. Neither makes any claim about an actual packet receipt.
    const all = $("showCandidates")?.checked && positions.length <= 24;
    if (all) {
      for (let i = 0; i < positions.length; i++) {
        for (let j = i + 1; j < positions.length; j++) {
          const a = positions[i], b = positions[j], pair = radioPair(a,b,specs);
          if (drawLink(a,b,pair,point,{type:"radio",emphasize: Boolean(selected && (a.name===selected.name || b.name===selected.name))})) stats.radio++;
        }
      }
    } else if (selected) {
      for (const other of positions) {
        if (selected.name === other.name) continue;
        const pair = radioPair(selected,other,specs);
        if (drawLink(selected,other,pair,point,{type:"radio",emphasize:true,allowReverse:false})) stats.radio++;
      }
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

  // Map spotlight: one recorded action, no stacked HELLO/JOIN overlays.
  const spotlightOn = $("showProtocolHighlights").checked;
  $("mapActivityStatus").hidden = !spotlightOn || state.linkMode !== "protocol";
  if (!spotlightOn) { /* Protocol records remain in Event History; only the map overlay is hidden. */ }
  else if (state.linkMode === "protocol") drawRecentProtocolSignals(positions, point);
  else if (spotlightOn) { const banner = $("mapActivityStatus"); if (banner) { banner.dataset.stage = "idle"; banner.innerHTML = `<span class="map-action-tag">${state.linkMode === "radio" ? "RADIO VIEW" : state.linkMode === "traffic" ? "TRAFFIC VIEW" : "NODES ONLY"}</span><span class="map-action-description"></span>`; } }

  for (const p of positions) {
    const [x, y] = point(p);
    const isSelected = p.name === state.selectedNode, isHovered = p.name === state.hoveredNode;
    if (isSelected || isHovered) {
      ctx.beginPath(); ctx.arc(x, y, isSelected ? 15 : 11, 0, Math.PI * 2);
      ctx.fillStyle = isSelected ? themeCanvas("rgba(255,255,255,.13)", "rgba(0,116,198,.10)") : themeCanvas("rgba(92,200,255,.09)", "rgba(0,116,198,.08)");
      ctx.fill(); ctx.strokeStyle = isSelected ? themeCanvas("#ffffff", "#0674c9") : themeCanvas("#9cb7cb", "#7c9bb3");
      ctx.lineWidth = 1; ctx.stroke();
    }
    if (p.role === 1) {
      const size = isSelected ? 8 : 6.3;
      ctx.fillStyle = themeCanvas("#9b8cff", "#7855d1"); ctx.strokeStyle = themeCanvas("#d9d0ff", "#4c2a9e"); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.rect(x-size,y-size,size*2,size*2); ctx.fill(); ctx.stroke();
    } else {
      ctx.fillStyle = themeCanvas("#5cc8ff", "#0076b7"); ctx.strokeStyle = themeCanvas("#b9eaff", "#035178"); ctx.lineWidth = .8;
      ctx.beginPath(); ctx.arc(x,y,isSelected ? 5.8 : 4.0,0,Math.PI*2); ctx.fill(); ctx.stroke();
    }
    const identity = nodeIdentity(p.name);
    if (identity && ($("showLabels").checked || isSelected || isHovered)) {
      ctx.font = "11px ui-sans-serif, system-ui";
      ctx.fillStyle = themeCanvas("#dbe7f4", "#243b53");
      // Keep long IP labels readable near right and left edges.
      const labelW = ctx.measureText(identity).width;
      const textX = x + 14 + labelW > w-8 ? x - 10 - labelW : x + 10;
      ctx.fillText(identity, Math.max(8,textX), y-8);
    }
  }
  updateLinkExplanation(relationships, selected, stats);
  $("canvasCorner").textContent = `${positions.filter(p => p.role === 1).length} CH · ${positions.filter(p => p.role !== 1).length} SN · ${state.frame.tick_time_s.toFixed(2)}s`;
  // The playback clock is also the seek field: update it only when the user
  // is not editing, so recorded-time redraws never overwrite typed input.
  const clockInput = $("timeReadout");
  if (document.activeElement !== clockInput) clockInput.value = state.currentTime.toFixed(6);
  $("tickReadout").textContent = `position tick ${state.frame.tick} @ ${state.frame.tick_time_s.toFixed(3)}s`;
  $("timeSlider").value = state.currentTime;
  const playbackRatio = state.meta?.duration_s > 0
    ? clamp(state.currentTime / state.meta.duration_s, 0, 1) : 0;
  $("timeSlider").style.setProperty("--playback-progress", `${(playbackRatio * 100).toFixed(3)}%`);
}

function drawTrafficChart() {
  const rect = resizeCanvas(trafficCanvas, tctx), w = rect.width, h = rect.height;
  tctx.clearRect(0, 0, w, h);
  tctx.fillStyle = themeCanvas("#0a0f15", "#ffffff");
  tctx.fillRect(0, 0, w, h);
  const rows = state.timeline;
  if (!rows.length || !state.meta) return;

  const padL = 42, padR = 16, padT = 18, padB = 26;
  const maxY = Math.max(1, ...rows.flatMap(r => [r.sent, r.received, r.dropped]));
  const duration = Math.max(.001, state.meta.duration_s);
  const x = (timeS) => padL + (w - padL - padR) * clamp(timeS / duration, 0, 1);
  const y = (v) => h - padB - (h - padT - padB) * (v / maxY);

  tctx.strokeStyle = themeCanvas("rgba(143,161,181,.11)", "rgba(74,95,116,.17)");
  tctx.fillStyle = themeCanvas("#7f91a6", "#586c82");
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
  tctx.strokeStyle = themeCanvas("rgba(255,255,255,.5)", "rgba(35,55,75,.38)");
  tctx.beginPath(); tctx.moveTo(cursorX, padT); tctx.lineTo(cursorX, h - padB); tctx.stroke();
  if (state.trafficHoverBucket != null) {
    const hover = rows[state.trafficHoverBucket];
    if (hover) {
      const hx = x(hover.time_s);
      tctx.strokeStyle = "rgba(92,200,255,.80)";
      tctx.setLineDash([3, 4]);
      tctx.beginPath(); tctx.moveTo(hx, padT); tctx.lineTo(hx, h - padB); tctx.stroke();
      tctx.setLineDash([]);
      for (const [key, color] of [["sent", "#ffd166"], ["received", "#5de0a0"], ["dropped", "#ff6b7a"]]) {
        tctx.beginPath(); tctx.arc(x(hover.time_s), y(hover[key]), 3.5, 0, Math.PI * 2);
        tctx.fillStyle = color; tctx.fill();
      }
    }
  }

  const legend = [["TX", "#ffd166"], ["RX", "#5de0a0"], ["Drop", "#ff6b7a"]];
  let lx = w - 190;
  for (const [label, color] of legend) {
    tctx.fillStyle = color; tctx.fillRect(lx, 7, 8, 8);
    tctx.fillStyle = themeCanvas("#9fb0c2", "#4b6179"); tctx.fillText(label, lx + 12, 15); lx += 58;
  }
}

// Link animations use real RECEIVE / delivery-drop records independently of
// the Events tab filter. A PROTOCOL filter must not hide radio traffic.
async function refreshPacketMarkers() {
  if (state.linkMode !== "traffic" || !state.run || !state.meta?.logging?.packet_events) {
    if (!state.meta?.logging?.packet_events) state.packetEvents = [];
    return;
  }
  if (state.packetRequestInFlight) return;
  const run = state.run;
  const timeS = state.currentTime;
  const query = new URLSearchParams({
    run, from_s: String(Math.max(0, timeS - 1.2)),
    to_s: String(Math.min(state.meta.duration_s, timeS + .08)),
    category: "traffic", limit: "180"
  });
  const key = query.toString();
  if (key === state.lastPacketMarkerKey &&
      performance.now() - state.lastPacketMarkerAt < 1000) return;
  state.packetRequestInFlight = true;
  try {
    const data = await api(`/api/events?${query}`);
    if (run !== state.run || Math.abs(timeS - state.currentTime) > 1.5) return;
    state.packetEvents = data.events || [];
    state.lastPacketMarkerKey = key;
    state.lastPacketMarkerAt = performance.now();
  } catch (error) {
    console.error("Could not load traffic markers", error);
  } finally {
    state.packetRequestInFlight = false;
  }
}

function hideTrafficTooltip() {
  state.trafficHoverBucket = null;
  trafficTooltip.hidden = true;
  if (state.ui.activeTab === "trafficPanel") drawTrafficChart();
}

function updateTrafficTooltip(clientX, clientY) {
  if (!state.timeline.length || !state.meta) {
    hideTrafficTooltip();
    return;
  }
  const rect = trafficCanvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  const left = 42, right = 16, top = 18, bottom = 26;
  if (x < left || x > rect.width - right || y < top || y > rect.height - bottom) {
    hideTrafficTooltip();
    return;
  }
  const duration = Math.max(.001, Number(state.meta.duration_s));
  const hoveredTime = (x - left) / Math.max(1, rect.width - left - right) * duration;
  const index = Math.floor(hoveredTime / state.trafficBucketS);
  const bucket = state.timeline[index];
  if (!bucket) { hideTrafficTooltip(); return; }
  state.trafficHoverBucket = index;
  const endS = Math.min(duration, bucket.time_s + state.trafficBucketS);
  trafficTooltip.innerHTML = `<strong>${bucket.time_s.toFixed(1)}–${endS.toFixed(1)} seconds</strong>
    <span class="traffic-tooltip-row tx">Sent (TX)<b>${fmtInt(bucket.sent)}</b></span>
    <span class="traffic-tooltip-row rx">Received (RX)<b>${fmtInt(bucket.received)}</b></span>
    <span class="traffic-tooltip-row drop">Drops<b>${fmtInt(bucket.dropped)}</b></span>`;
  trafficTooltip.hidden = false;
  const container = trafficCanvas.parentElement.getBoundingClientRect();
  const tw = trafficTooltip.offsetWidth, th = trafficTooltip.offsetHeight;
  trafficTooltip.style.left = `${clamp(clientX - container.left + 14, 6, Math.max(6, container.width - tw - 6))}px`;
  trafficTooltip.style.top = `${clamp(clientY - container.top - th - 12, 6, Math.max(6, container.height - th - 6))}px`;
  drawTrafficChart();
}

// Event-specific presentation lives in event_stream.js; this view only wires UI state.
function renderEventTable() {
  const result = state.eventResult || {};
  const view = CliveEventStream.render(state.visibleEvents, {
    filter: $("eventFilter").value,
    search: $("eventSearch")?.value || "",
    packetName,
    escapeHtml,
    preFiltered: true,
    order: $("eventSort").value,
    node: $("eventNode").value,
    packet: $("eventPacket").value,
    outcome: $("eventOutcome").value,
  });
  const matching = Number(result.matching_count ?? state.visibleEvents.length);
  const hidden = Number(result.hidden_count ?? 0);
  $("eventSummary").textContent = `${view.summary} · ${fmtInt(matching)} matching` +
    (hidden ? ` · ${fmtInt(hidden)} more in full history` : "");
  $("eventRows").innerHTML = view.html;
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

function nodeLinkStatus(node, other, specs) {
  const a = specs.get(node.name), b = specs.get(other?.name);
  if (!other || !a || !b) return {label: "Peer not in topology", tone: "muted"};
  const distance = Math.hypot(node.x - other.x, node.y - other.y);
  const tx = linkCondition(distance, a), rx = linkCondition(distance, b);
  return {label: tx.reachable && rx.reachable ? "Two-way radio reachable" :
    tx.reachable ? "Outgoing only (return path unavailable)" :
    rx.reachable ? "Incoming only (outgoing unavailable)" : "Out of radio range",
    tone: tx.reachable && rx.reachable ? "success" : "danger",
    distance, tx, rx};
}

// Use the exact precomputed mobility trace to find the last two-way radio change.
// This is a PHY observation, NOT an inferred FLAME JOIN or LEAVE.
function lastRadioTransition(aName, bName) {
  const trace = state.mobilityTrace;
  if (!trace?.view || !state.meta) return null;
  const ia = trace.node_names.indexOf(aName), ib = trace.node_names.indexOf(bName);
  if (ia < 0 || ib < 0) return null;
  const specs = new Map(state.meta.nodes.map(node => [node.name, node]));
  const aSpec = specs.get(aName), bSpec = specs.get(bName);
  const reachable = (tick) => {
    const offset = trace.data_offset + tick * trace.node_count * 16;
    const ax = trace.view.getFloat64(offset + ia * 16, true), ay = trace.view.getFloat64(offset + ia * 16 + 8, true);
    const bx = trace.view.getFloat64(offset + ib * 16, true), by = trace.view.getFloat64(offset + ib * 16 + 8, true);
    const distance = Math.hypot(ax-bx, ay-by);
    return linkCondition(distance, aSpec).reachable && linkCondition(distance, bSpec).reachable;
  };
  const tick = Math.min(state.currentTick, trace.tick_count - 1);
  if (tick < 0) return null;
  const current = reachable(tick);
  for (let earlier = tick - 1; earlier >= 0; earlier--) {
    if (reachable(earlier) !== current) {
      return {currentlyReachable: current, changedAt: (earlier + 1) * trace.tick_interval_s};
    }
  }
  return {currentlyReachable: current, changedAt: null};
}

function radioChangeText(a, b) {
  const transition = lastRadioTransition(a,b);
  if (!transition || transition.changedAt == null) return "";
  return transition.currentlyReachable
    ? ` · two-way radio restored at ${transition.changedAt.toFixed(2)}s`
    : ` · two-way radio lost at ${transition.changedAt.toFixed(2)}s`;
}

function connectionRow(label, peer, kind, detail) {
  return `<div class="connection-row"><span class="connection-dot ${kind}"></span><div>
    <strong>${escapeHtml(label)} <span class="mono">${escapeHtml(peer || "—")}</span></strong>
    <small>${escapeHtml(detail)}</small></div></div>`;
}

function nodeHistoryHtml(snapshot, node) {
  if (!state.relationships.available) return `<div class="selected-note">Connection tracking was not recorded for this run. New CLIVE runs write compact connection logs even when verbose packet logging is disabled.</div>`;
  const events = CliveConnectionTracker.historyFor(snapshot, node, 14);
  if (!events.length) return `<p class="selected-note">No JOIN, accepted sensor data, model-transfer or departure events recorded for this node yet.</p>`;
  return `<ol class="connection-history">${events.map(ev => {
    const peer = ev.source === node ? ev.target : ev.source;
    const packet = ev.sequence_number == null ? "" : ` · packet seq #${ev.sequence_number}`;
    const revision = ev.update_id == null ? "" : ` · model revision #${ev.update_id}`;
    const reason = ev.reason ? ` · ${ev.reason}` : "";
    return `<li><time>${Number(ev.time_s).toFixed(2)}s</time><div><strong>${escapeHtml(CliveConnectionTracker.label(ev))}</strong>
      <small>${escapeHtml(peer)}${escapeHtml(packet)}${escapeHtml(revision)}${escapeHtml(reason)}</small></div></li>`;
  }).join("")}</ol>`;
}

function updateSelectedNode() {
  const box = $("selectedNode");
  if (!state.selectedNode || !state.frame || !state.meta) {
    box.className = "selected-empty";
    box.textContent = "Select a Cluster Head or Sensor Node to see connection attempts, observed membership, radio reachability and disconnect history.";
    $("selectedRole").textContent = "None";
    return;
  }
  const positions = currentDisplayPositions();
  const p = positions.find(x => x.name === state.selectedNode);
  if (!p) return;
  const specs = new Map(state.meta.nodes.map(n => [n.name,n]));
  const positionsByName = new Map(positions.map(n => [n.name,n]));
  const spec = specs.get(p.name);
  const metric = state.metrics?.nodes.find(x => x.node_name === p.name);
  const rel = replayRelationships(state.currentTime);
  const enabled = state.relationships.available;
  const compatible = positions.filter(other => other.name !== p.name && roleCompatible(spec, specs.get(other.name)));
  const nearby = compatible.map(other => ({other, radio: nodeLinkStatus(p, other, specs)}))
    .filter(row => row.radio.tx?.reachable || row.radio.rx?.reachable)
    .sort((a,b)=>a.radio.distance-b.radio.distance);
  const isCh = p.role === 1;
  const assoc = isCh ? null : rel.associations.get(p.name);
  const pending = isCh ? null : rel.attempts.get(p.name);
  const lastExit = isCh ? null : rel.lastExit.get(p.name);
  const members = isCh ? [...rel.associations].filter(([,x])=>x.ch === p.name) : [];
  const peerLinks = isCh ? [...rel.peers].filter(([key])=>key.split("|").includes(p.name)) : [];
  let status = "Unknown — no recorded connection history", tone = "muted";
  const connections = [];
  if (!isCh) {
    if (assoc) {
      const radio = nodeLinkStatus(p, positionsByName.get(assoc.ch), specs);
      const observedAt = assoc.confirmedAt == null ? "JOIN accepted; CH data acceptance not recorded" : `CH accepted data at ${assoc.lastSeen.toFixed(2)}s`;
      status = radio.tx?.reachable && radio.rx?.reachable ? "JOIN accepted · radio reachable" : "JOIN accepted earlier · radio currently unavailable";
      tone = radio.tone;
      connections.push(connectionRow("Associated with", assoc.ch, tone, `${radio.label}${radioChangeText(p.name, assoc.ch)} · ${observedAt}`));
    } else if (pending) {
      status = "Trying to join"; tone = "warning";
      const radio = nodeLinkStatus(p, positionsByName.get(pending.ch), specs);
      connections.push(connectionRow("JOIN requested to", pending.ch, "warning",
        `${pending.count} request(s), last at ${pending.at.toFixed(2)}s · ${radio.label}${radioChangeText(p.name, pending.ch)} · acceptance not recorded`));
    } else if (lastExit) {
      status = lastExit.action === "flame_join_pending_expired" ? "JOIN attempt expired" : "Disconnected (recorded)"; tone = "danger";
      connections.push(connectionRow(lastExit.action === "flame_join_pending_expired" ? "Failed JOIN to" : "Previously connected to", lastExit.ch, "danger",
        `Ended at ${lastExit.at.toFixed(2)}s · ${lastExit.reason}`));
    } else if (enabled) {
      status = "No JOIN recorded";
    }
  } else {
    status = enabled ? `${members.length} observed sensor member(s)` : "Membership unknown (logging off)";
    tone = members.length ? "success" : "muted";
    for (const [sn, relation] of members) {
      const radio = nodeLinkStatus(p, positionsByName.get(sn), specs);
      connections.push(connectionRow("Sensor member", sn, radio.tone,
        `${radio.label} · ${relation.confirmedAt == null ? "JOIN observed (not CH-confirmed)" : `last data accepted ${relation.lastSeen.toFixed(2)}s`}`));
    }
    for (const [key, lastAt] of peerLinks) {
      const peer = key.split("|").find(name => name !== p.name);
      const radio = nodeLinkStatus(p, positionsByName.get(peer), specs);
      const detail = rel.peerDetails.get(key);
      connections.push(connectionRow("CH model peer activity", peer, radio.tone,
        `${radio.label} · ${CliveConnectionTracker.label({action:detail?.action})} at ${lastAt.toFixed(2)}s (not a persistent CH association)`));
    }
  }
  const nearSummary = nearby.slice(0, 10).map(({other,radio}) =>
    connectionRow("Radio candidate", other.name, "muted", `${radio.distance.toFixed(1)} m · ${radio.label} (not proof of association)`));
  $("selectedRole").textContent = isCh ? "CLUSTER HEAD" : "SENSOR NODE";
  box.className = "selected-body";
  box.innerHTML = `<div class="selected-name">${escapeHtml(p.name)}</div>
    <div class="connection-status ${tone}">${escapeHtml(status)}</div>
    
    <div class="kv">
      <span>Playback time</span><strong>${state.currentTime.toFixed(2)}s</strong>
      <span>Position</span><strong>${p.x.toFixed(1)}, ${p.y.toFixed(1)} m</strong>
      <span>Overlay (FLAME) IP</span><strong class="mono">${escapeHtml(spec?.overlay_ip || spec?.ip || "—")}:${escapeHtml(spec?.overlay_port || spec?.port || "—")}</strong>
      <span>Underlay (UDP) IP</span><strong class="mono">${escapeHtml(spec?.underlay_ip || spec?.ip || "—")}:${escapeHtml(spec?.underlay_port || spec?.port || "—")}</strong>
      <span>Two-way/one-way radio peers</span><strong>${nearby.length}</strong>
      <span>TX / RX (run totals)</span><strong>${metric ? fmtInt(metric.packets_sent)+" / "+fmtInt(metric.packets_received) : "—"}</strong>
    </div>
    <section class="connection-section"><h4>Observed protocol connections / attempts</h4>
      ${connections.length ? connections.join("") : `<p class="connection-empty">${enabled ? "No active or attempted protocol association observed at this playback time." : "No protocol history available for this older run."}</p>`}
    </section>
    <details class="connection-section"><summary>Nearby radio candidates (${nearby.length})</summary>
      
      ${nearSummary.length ? nearSummary.join("") : `<p class="connection-empty">No nearby compatible radio peers.</p>`}
      ${nearby.length > 10 ? `<small>${nearby.length - 10} more candidate(s) not shown.</small>` : ""}
    </details>
    <details class="connection-section"><summary>Full connection history</summary>
      ${nodeHistoryHtml(rel, p.name)}
      
    </details>`;
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
    if (state.guidedPlayback && state.relationships.available) {
      state.guide = CliveGuidedPlayback.advance(state.guide, ts, state.speed,
        state.meta.duration_s, state.stageTimeline);
      state.currentTime = state.guide.time;
    } else syncPlaybackTime(ts);
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
    // The event view and animated radio markers have different data needs.
    if (state.ui.activeTab === "eventsPanel") refreshVisibleEvents();
    if (state.linkMode === "traffic") refreshPacketMarkers();
  }
  // Canvas drawing is expensive at high node counts. Playback needs smooth
  // motion, but a paused recording does not need 60 full redraws per second.
  const topologyInterval = state.playing ? 33 : 125;
  const trafficInterval = state.playing ? 100 : 500;
  if (ts - state.lastTopologyDrawAt >= topologyInterval) {
    state.lastTopologyDrawAt = ts;
    drawTopology();
  }
  if (ts - state.lastTrafficDrawAt >= trafficInterval) {
    state.lastTrafficDrawAt = ts;
    if (state.ui.activeTab === "trafficPanel") drawTrafficChart();
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
    const active = state.meta?.manifest?.status === "running";
    setTimeout(periodic, active ? 1500 : 6000);
  }
}

$("runSelect").addEventListener("change", async e => {
  state.run = e.target.value || null;
  window.CLIVERunSelection?.write(state.run);
  const query = new URL(window.location.href);
  if (state.run) query.searchParams.set("run", state.run); else query.searchParams.delete("run");
  history.replaceState(null, "", query);
  state.playing = false;
  state.playAnchorWallMs = null;
  $("playButton").textContent = "▶ Play";
  state.meta = null;
  state.frame = null;
  state.mobilityTrace = null;
  state.currentTick = -1;
  state.metrics = null;
  state.timeline = [];
  state.packetEvents = [];
  state.lastPacketMarkerKey = "";
  state.trafficHoverBucket = null;
  trafficTooltip.hidden = true;
  state.frameRequestKey = null;
  state.selectedNode = null;
  state.guide = CliveGuidedPlayback.create();
  resetRelationships();
  resetView();
  await refreshMeta();
  if (state.meta) await Promise.all([refreshMetrics(), refreshTimeline()]);
});
$("playButton").addEventListener("click", () => {
  if (!state.meta || !state.mobilityTrace) return;
  if (state.playing) {
    if (!state.guidedPlayback) syncPlaybackTime();
    state.playing = false;
    state.playAnchorWallMs = null;
  } else {
    if (state.currentTime >= state.meta.duration_s) {
      state.currentTime = 0;
      setFrameFromTrace(true);
    }
    state.playing = true;
    if (state.guidedPlayback) {
      const now = performance.now();
      // Resume the same recorded action if paused mid-stage. A fresh seek would
      // skip additional actions sharing its exact timestamp.
      state.guide = state.guide.focus && Math.abs(state.guide.time - state.currentTime) < 1e-7
        ? CliveGuidedPlayback.resume(state.guide, now)
        : CliveGuidedPlayback.seek(state.stageTimeline, state.currentTime, now);
    }
    rebasePlaybackClock();
  }
  $("playButton").textContent = state.playing ? "❚❚ Pause" : "▶ Play";
});
$("guidedPlayback").addEventListener("change", e => {
  state.guidedPlayback = Boolean(e.target.checked);
  state.guide = CliveGuidedPlayback.seek(state.stageTimeline, state.currentTime, performance.now());
  if (state.playing) rebasePlaybackClock();
});
$("nextAction").addEventListener("click", () => {
  state.playing = false;
  state.playAnchorWallMs = null;
  $("playButton").textContent = "▶ Play";
  state.guide = CliveGuidedPlayback.next(state.guide, state.stageTimeline);
  state.currentTime = state.guide.time;
  $("nextAction").disabled = state.guide.cursor >= state.stageTimeline.length - 1;
  setFrameFromTrace(true);
  drawTopology();
  refreshVisibleEvents();
});
function seekToEnteredTime() {
  if (!state.meta) return;
  const input = $("timeReadout"), value = Number(input.value);
  if (!input.value.trim() || !Number.isFinite(value)) { input.setCustomValidity("Enter a valid simulation time"); input.reportValidity(); return; }
  input.setCustomValidity("");
  state.currentTime = clamp(value, 0, state.meta.duration_s);
  input.value = state.currentTime.toFixed(6);
  state.guide = CliveGuidedPlayback.seek(state.stageTimeline, state.currentTime, performance.now());
  state.currentTick = -1;
  setFrameFromTrace(true);
  if (state.playing) rebasePlaybackClock();
  drawTopology();
  refreshVisibleEvents();
  if (state.linkMode === "traffic") refreshPacketMarkers();
}
$("seekForm").addEventListener("submit", e => {
  e.preventDefault();
  seekToEnteredTime();
  // Return the clock to live updating when playback continues.
  if ($("timeReadout").validity.valid) $("timeReadout").blur();
});
$("timeReadout").addEventListener("change", seekToEnteredTime);
$("timeReadout").addEventListener("input", () => $("timeReadout").setCustomValidity(""));
$("timeSlider").addEventListener("input", e => {
  state.currentTime = Number(e.target.value);
  state.guide = CliveGuidedPlayback.seek(state.stageTimeline, state.currentTime, performance.now());
  $("nextAction").disabled = state.guide.cursor >= state.stageTimeline.length - 1;
  state.currentTick = -1;
  setFrameFromTrace(true);
  if (state.playing) rebasePlaybackClock();
  refreshVisibleEvents();
});
$("speedSelect").addEventListener("change", e => {
  if (state.playing && !state.guidedPlayback) syncPlaybackTime();
  state.speed = Number(e.target.value);
  if (state.playing) {
    rebasePlaybackClock();
    if (state.guidedPlayback) state.guide = CliveGuidedPlayback.resume(state.guide, performance.now());
  }
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
  syncLayerUI();
  updateSelectedNode();
  drawTopology();
  if (mode === "traffic") refreshPacketMarkers();
}
$("linkMode").addEventListener("change", e => selectLinkMode(e.target.value));
document.querySelectorAll(".mode-button[data-mode]").forEach(btn => btn.addEventListener("click", () => selectLinkMode(btn.dataset.mode)));
window.addEventListener("click", e => {
  const layer = $("layerMenu");
  if (layer?.open && !layer.contains(e.target)) layer.open = false;
});
syncLayerUI();
$("showBackbone").addEventListener("change", () => drawTopology());
$("nodeLabelMode").addEventListener("change", () => {
  // Selecting an IP format is a request to actually see it on the map.
  $("showLabels").checked = $("nodeLabelMode").value !== "none";
  $("showLabels").disabled = $("nodeLabelMode").value === "none";
  try { localStorage.setItem("clive-map-node-labels-v1", $("nodeLabelMode").value); } catch (_) {}
  drawTopology();
});
try {
  const savedLabels = localStorage.getItem("clive-map-node-labels-v1");
  if (["name","overlay","underlay","both","none"].includes(savedLabels)) {
    $("nodeLabelMode").value = savedLabels;
    $("showLabels").checked = savedLabels !== "none";
    $("showLabels").disabled = savedLabels === "none";
  }
} catch (_) {}
$("showCandidates").addEventListener("change", () => drawTopology());
$("showLabels").addEventListener("change", () => drawTopology());
$("showLinkLabels").addEventListener("change", () => drawTopology());
$("showDirectionArrows").addEventListener("change", () => drawTopology());
$("showModelFlow").addEventListener("change", () => drawTopology());
$("showProtocolHighlights").addEventListener("change", () => drawTopology());
function requestEventFilterUpdate() {
  // Clear rows from the old category immediately. Large JSONL queries may take
  // a moment; displaying old TRANSMIT rows under RECEIVE looks like a broken tab.
  $("eventRows").innerHTML = '<tr><td colspan="6" class="muted">Loading matching recorded events…</td></tr>';
  $("eventSummary").textContent = "Reading recorded history…";
  state.lastEventQuerySignature = "";
  refreshVisibleEvents();
}
for (const id of ["eventFilter", "eventSort", "eventNode", "eventPacket", "eventOutcome"]) {
  $(id).addEventListener("change", requestEventFilterUpdate);
}
let eventSearchDebounce = null;
$("eventSearch").addEventListener("input", () => {
  clearTimeout(eventSearchDebounce);
  eventSearchDebounce = setTimeout(requestEventFilterUpdate, 250);
});

trafficCanvas.addEventListener("pointermove", e => updateTrafficTooltip(e.clientX, e.clientY));
trafficCanvas.addEventListener("pointerleave", hideTrafficTooltip);
trafficCanvas.addEventListener("pointercancel", hideTrafficTooltip);
$("nodeSearch").addEventListener("input", renderNodeTable);
document.querySelectorAll(".tab-button[data-target]").forEach((button) => button.addEventListener("click", () => setWorkbenchTab(button.dataset.target)));
$("sidebarToggle").addEventListener("click", () => setSidebarCollapsed(!state.ui.sidebarCollapsed));
$("canvasFocusToggle").addEventListener("click", () => setFocusMode(!state.ui.focusMode));
// Keep the secondary options menu unobtrusive after a selection or outside click.
document.addEventListener("pointerdown", (event) => {
  const menu = $("playbackOptions");
  if (menu?.open && !menu.contains(event.target)) menu.open = false;
});
document.addEventListener("keydown", (event) => {
  const menu = $("playbackOptions");
  if (event.key === "Escape" && menu?.open) {
    menu.open = false;
    menu.querySelector("summary")?.focus();
  }
});

$("toggleDiagnostics").addEventListener("click", ()=>{
 const shown=document.body.classList.toggle("show-diagnostics");
 $("toggleDiagnostics").setAttribute("aria-pressed",shown?"true":"false");
 $("toggleDiagnostics").textContent=shown?"Show fewer metrics":"Show more metrics";
 drawTrafficChart();
});

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
    const node = canvasNodeAt(e.clientX, e.clientY, 14);
    state.hoveredNode = node;
    const tooltip = $("nodeTooltip");
    if (node) {
      const spec = state.meta?.nodes.find(n => n.name === node);
      tooltip.innerHTML = `<strong>${escapeHtml(node)}</strong><span>${spec?.role === 1 ? "Cluster Head" : "Sensor Node"}</span><small>Overlay: ${escapeHtml(spec?.overlay_ip || spec?.ip || "—")}<br>Underlay: ${escapeHtml(spec?.underlay_ip || spec?.ip || "—")}<br>Click to inspect this node.</small>`;
      tooltip.hidden = false;
      const rect = canvas.getBoundingClientRect();
      tooltip.style.left = `${clamp(e.clientX - rect.left + 16, 8, Math.max(8,rect.width-240))}px`;
      tooltip.style.top = `${clamp(e.clientY - rect.top + 16, 8, Math.max(8,rect.height-110))}px`;
    } else tooltip.hidden = true;
    canvas.style.cursor = node ? "pointer" : "grab";
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
    const node = canvasNodeAt(e.clientX, e.clientY, 14);
    state.selectedNode = node || null;
    updateSelectedNode();
  }
}
canvas.addEventListener("pointerleave", () => { state.hoveredNode = null; $("nodeTooltip").hidden = true; });
canvas.addEventListener("pointerup", finishPointer);
canvas.addEventListener("pointercancel", finishPointer);
window.addEventListener("resize", () => { drawTopology(); drawTrafficChart(); });
window.addEventListener("clive-theme-change", () => { drawTopology(); drawTrafficChart(); });
window.addEventListener("keydown", e=> { if(e.key==="Escape") $("layerMenu").open=false; });

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
