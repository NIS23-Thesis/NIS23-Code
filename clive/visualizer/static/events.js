"use strict";
// On-disk event history with explicit pagination and human-readable labels.
const $ = id => document.getElementById(id);
const qs = new URLSearchParams(location.search);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const packetTypes = ["HELLO", "LEAVE", "SENSOR_DATA", "SENSOR_DATA_ACK",
  "MODEL_UPDATE", "MODEL_UPDATE_ACK", "JOIN", "JOIN_ACCEPT"];
const packetName = id => packetTypes[Number(id)] || (id == null ? "—" : `TYPE ${id}`);
let selectedRun = qs.get("run") || "";
let offset = 0;
let requestVersion = 0;
let runNames = [];

function syncNavigation() {
  const suffix = selectedRun ? `?run=${encodeURIComponent(selectedRun)}` : "";
  for (const anchor of document.querySelectorAll(".bar nav a")) {
    const base = anchor.getAttribute("href").split("?")[0];
    anchor.href = `${base}${suffix}`;
  }
}

async function api(path) {
  const response = await fetch(path, {cache:"no-store"});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function applyDeepLink() {
  for (const [param,id] of [["from_s","from"],["to_s","to"],
    ["category","category"],["search","search"]]) {
    const value = qs.get(param), field = $(id);
    if (value !== null && field) field.value = value;
  }
}

function eventRow(event) {
  const [rawLabel,help] = CliveEventStream.explanation(event);
  const stage = {transmit:"Sent", receive:"Received", delivery_drop:"Lost to receiver", local_drop:"Dropped before send"};
  const wireName = event.packet_type == null ? null : packetName(event.packet_type);
  const label = event.stream === "packet" && stage[event.event] && wireName
    ? `${wireName} · ${stage[event.event]}` : rawLabel;
  const group = CliveEventStream.category(event);
  const source = event.source || (event.stream === "protocol" || event.stream === "connection" ? event.node : "—");
  const target = event.target || (event.is_broadcast ? "broadcast" : "—");
  const details = event.reason ? ` · ${event.reason}` : "";
  return `<tr><td class="mono" data-label="At">${Number(event.time_s).toFixed(3)}s</td>
    <td data-label="Type"><span class="archive-phase archive-${esc(group)}">${esc(group.toUpperCase())}</span></td>
    <td data-label="Action"><strong>${esc(label)}</strong><span class="event-explained">${esc(help)}</span>
      ${details ? `<span class="event-reason">${esc(details)}</span>` : ""}</td>
    <td data-label="Node">${esc(event.node || "—")}</td><td data-label="Peer">${esc(source)} → ${esc(target)}</td>
    <td class="mono" data-label="Packet">${esc(CliveEventStream.packetLabel(event,packetName))}</td></tr>`;
}

async function refreshRunList() {
  const data = await api("/api/runs");
  const runs = data.runs || [];
  runNames = runs.map(run => run.name);
  const box = $("runSelect");
  if (!runs.length) {
    box.innerHTML = '<option value="">No runs yet</option>';
    $("summary").textContent = 'No experiment results yet.';
    $("rows").innerHTML = '<tr><td colspan="6">Run an experiment first.</td></tr>';
    return false;
  }
  if (!runNames.includes(selectedRun)) selectedRun = runs[0].name;
  box.innerHTML = runs.map(run => `<option value="${esc(run.name)}">${esc(run.name)} · ${esc(run.status)}</option>`).join("");
  box.value = selectedRun;
  syncNavigation();
  return true;
}

async function load() {
  if (!selectedRun) return;
  const version = ++requestVersion;
  const from = $("from").value.trim();
  const to = $("to").value.trim();
  if (from !== "" && (!Number.isFinite(Number(from)) || Number(from) < 0) ||
      to !== "" && (!Number.isFinite(Number(to)) || Number(to) < 0) ||
      to !== "" && Number(to) < Number(from || 0)) {
    $("summary").textContent = "Check the time range: the end must be after the start.";
    return;
  }
  const query = new URLSearchParams({run:selectedRun, offset:String(offset), limit:"100",
    category:$("category").value, search:$("search").value, from_s:from || "0"});
  if (to) query.set("to_s",to);
  $("summary").textContent = "Reading recorded events…";
  $("previous").disabled = true;
  $("next").disabled = true;
  try {
    const data = await api("/api/events/archive?" + query.toString());
    if (version !== requestVersion) return;  // Ignore response from a previously selected run.
    $("rows").innerHTML = data.events.map(eventRow).join("") ||
      '<tr><td colspan="6">No recorded events match these filters. Try a wider time range or another event type.</td></tr>';
    const shown = data.events.length ? `Events ${offset + 1}–${offset + data.events.length}` : "No matching events";
    $("summary").textContent = `${shown} · ${data.has_more ? "More recorded events available" : "End of matching recorded history"} · ${data.available ? "On-disk logs" : "No logs saved"}`;
    $("previous").disabled = offset === 0;
    $("next").disabled = !data.has_more;
  } catch (error) {
    if (version === requestVersion) $("summary").textContent = `Unable to read this run: ${error.message}`;
  }
}

$("runSelect").addEventListener("change", event => {selectedRun=event.target.value;offset=0;syncNavigation();load()});
$("apply").addEventListener("click",()=>{offset=0;load()});
$("previous").addEventListener("click",()=>{offset=Math.max(0,offset-100);load()});
$("next").addEventListener("click",()=>{offset+=100;load()});
applyDeepLink();
refreshRunList().then(available => {if(available)load()}).catch(error=>{
  $("summary").textContent=`Could not load experiments: ${error.message}`;
});
