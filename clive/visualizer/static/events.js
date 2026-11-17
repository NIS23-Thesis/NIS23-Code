"use strict";
// On-disk event history with explicit pagination and human-readable labels.
const $ = id => document.getElementById(id);
const qs = new URLSearchParams(location.search);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const packetTypes = ["HELLO", "LEAVE", "SENSOR_DATA", "SENSOR_DATA_ACK",
  "MODEL_UPDATE", "MODEL_UPDATE_ACK", "JOIN", "JOIN_ACCEPT"];
const packetName = id => id == null ? "—" : (packetTypes[Number(id)] || `TYPE ${id}`);
let selectedRun = qs.get("run") || window.CLIVERunSelection?.read() || "";
let offset = 0;
let requestVersion = 0;
let runNames = [];

function syncNavigation() {
  const suffix = selectedRun ? `?run=${encodeURIComponent(selectedRun)}` : "";
  for (const anchor of document.querySelectorAll(".clive-site-nav a[data-run-link]")) {
    const base = anchor.getAttribute("href").split("?")[0];
    anchor.href = `${base}${suffix}`;
  }
}

async function api(path) {
  const response = await fetch(window.location.pathname.startsWith("/topology/") ? path.replace(/^\/api(?=\/)/,"/api/viz") : path, {cache:"no-store"});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function applyDeepLink() {
  for (const [param,id] of [["from_s","from"],["to_s","to"],
    ["category","category"],["search","search"],["order","sortOrder"],
    ["node","nodeFilter"],["packet","packetFilter"],["outcome","outcomeFilter"]]) {
    const value = qs.get(param), field = $(id);
    if (value !== null && field) field.value = value;
  }
}

function eventRow(event) {
  const [,help] = CliveEventStream.explanation(event);
  const label = CliveEventStream.displayTitle(event, packetName);
  const group = CliveEventStream.category(event);
  const labelOrigin = CliveEventStream.groupLabel(event);
  const source = event.source || (event.stream === "protocol" || event.stream === "connection" ? event.node : "—");
  const target = event.target || (event.is_broadcast ? "broadcast" : "—");
  const details = event.reason ? ` · ${event.reason}` : "";
  return `<tr class="${CliveEventStream.isFailure(event) ? "event-is-failure" : ""}"><td class="mono" data-label="At">${Number(event.time_s).toFixed(6)}s</td>
    <td data-label="Group"><span class="archive-phase archive-${esc(group)}">${esc(labelOrigin)}</span></td>
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
    ++requestVersion;
    selectedRun = '';
    box.innerHTML = '<option value="">No runs yet</option>';
    $("previous").disabled = true;
    $("next").disabled = true;
    $("summary").textContent = 'No experiment results yet.';
    $("rows").innerHTML = '<tr><td colspan="6">Run an experiment first.</td></tr>';
    return false;
  }
  if (!runNames.includes(selectedRun)) selectedRun = runs[0].name;
  box.innerHTML = runs.map(run => `<option value="${esc(run.name)}">${esc(run.name)}</option>`).join("");
  box.value = selectedRun;
  window.CLIVERunSelection?.write(selectedRun);
  syncNavigation();
  return true;
}

async function refreshNodeFilter() {
  const select = $("nodeFilter");
  const requestedRun = selectedRun;
  const keep = select.value || qs.get("node") || "";
  select.innerHTML = '<option value="">All nodes</option>';
  if (!requestedRun) return;
  try {
    const meta = await api(`/api/meta?run=${encodeURIComponent(requestedRun)}`);
    if (selectedRun !== requestedRun) return;
    const nodes = (meta.nodes || []).map(n=>n.name).filter(Boolean).sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    select.innerHTML += nodes.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join("");
    select.value = nodes.includes(keep) ? keep : "";
  } catch (error) {
    console.warn("Node filter unavailable for the selected run", error);
  }
}

async function load() {
  const version = ++requestVersion;
  // Clear the old experiment immediately, before validation or network I/O.
  // A failed request must never display previous-run events under a new run ID.
  $("rows").innerHTML = '<tr><td colspan="6">Loading events for the selected run…</td></tr>';
  $("previous").disabled = true;
  $("next").disabled = true;
  if (!selectedRun) {
    $("summary").textContent = 'Choose a run to inspect recorded events.';
    return;
  }
  const from = $("from").value.trim();
  const to = $("to").value.trim();
  if (from !== "" && (!Number.isFinite(Number(from)) || Number(from) < 0) ||
      to !== "" && (!Number.isFinite(Number(to)) || Number(to) < 0) ||
      to !== "" && Number(to) < Number(from || 0)) {
    $("summary").textContent = "Check the time range: the end must be after the start.";
    $("rows").innerHTML = '<tr><td colspan="6">Correct the time range to load these events.</td></tr>';
    return;
  }
  const query = new URLSearchParams({run:selectedRun, offset:String(offset), limit:"100",
    category:$("category").value, search:$("search").value,
    order:$("sortOrder").value, node:$("nodeFilter").value,
    packet:$("packetFilter").value, outcome:$("outcomeFilter").value,
    from_s:from || "0"});
  if (to) query.set("to_s",to);
  $("summary").textContent = `Reading recorded events for ${selectedRun}…`;
  try {
    const data = await api("/api/events/archive?" + query.toString());
    if (version !== requestVersion) return;  // Ignore response from a previously selected run.
    $("rows").innerHTML = data.events.map(eventRow).join("") ||
      '<tr><td colspan="6">No recorded events match these filters. Try a wider time range or another event type.</td></tr>';
    const shown = data.events.length ? `Events ${offset + 1}–${offset + data.events.length}` : "No matching events";
    const direction = $("sortOrder").value === "asc" ? "Oldest first" : "Newest first";
    $("summary").textContent = `${shown} · ${direction}, globally sorted · ${data.has_more ? "More matching records" : "End of matching records"} · ${data.available ? "Original logs" : "No logs saved"}`;
    $("previous").disabled = offset === 0;
    $("next").disabled = !data.has_more;
  } catch (error) {
    if (version === requestVersion) {
      $("summary").textContent = `Unable to read ${selectedRun}: ${error.message}`;
      $("rows").innerHTML = '<tr><td colspan="6">Events unavailable for this run. Retry or choose another run.</td></tr>';
      $("previous").disabled = true;
      $("next").disabled = true;
    }
  }
}

$("runSelect").addEventListener("change", async event => {
  selectedRun=event.target.value;
  window.CLIVERunSelection?.write(selectedRun);
  history.replaceState(null,"",`?run=${encodeURIComponent(selectedRun)}`);
  offset=0;syncNavigation();
  await refreshNodeFilter();
  load();
});
$("archiveFilterForm").addEventListener("submit", event=>{event.preventDefault();offset=0;load();});
$("resetFilters").addEventListener("click",()=>{
  $("category").value="highlights";
  $("sortOrder").value="asc";
  $("from").value="0";
  $("to").value="";
  $("nodeFilter").value="";
  $("packetFilter").value="all";
  $("outcomeFilter").value="all";
  $("search").value="";
  offset=0;updateArchiveFilterHelp();load();
});
$("previous").addEventListener("click",()=>{offset=Math.max(0,offset-100);load()});
$("next").addEventListener("click",()=>{offset+=100;load()});
applyDeepLink();
refreshRunList().then(async available => {if(available){await refreshNodeFilter();load();}}).catch(error=>{
  ++requestVersion;
  $("rows").innerHTML = '<tr><td colspan="6">Run list unavailable. Refresh the page to retry.</td></tr>';
  $("previous").disabled = true;
  $("next").disabled = true;
  $("summary").textContent=`Could not load experiments: ${error.message}`;
});

// Filter descriptions emphasize which stage is being measured; they are not interchangeable.
const archiveFilterHelp={
  highlights:"Highlights — key FLAME decisions and training, not the routine startup/packet flood.",
  flame:"FLAME · All — recorded packet TX/RX plus observed actions and internal operations.",
  "flame-packet":"FLAME · Packet — only decoded FLAME datagrams actually transmitted/received by NodeSocket.",
  "flame-action":"FLAME · Action — observed HELLO discovery, membership changes, accepted data and transfer outcomes; not wire TX/RX.",
  "flame-internal":"FLAME · Internal — training, aggregation, reassembly and internal state transitions.",
  clive:"CLIVE — node lifecycle, dataset source, adapter maintenance and diagnostics.",
  network:"Network — NodeSocket TX, RX, drops and socket errors. TX and RX of FLAME packets may also appear in FLAME · Packet.",
  drop:"Failures / drops — FLAME failures and CLIVE network loss, with their actual origin preserved.",
  all:"All records — includes queue/scheduler and kernel diagnostics; no records are discarded."
};
function updateArchiveFilterHelp(){const note=$('eventFilterDescription');if(note)note.textContent=archiveFilterHelp[$('category').value]||'';}
$('category').addEventListener('change',updateArchiveFilterHelp);
updateArchiveFilterHelp();
