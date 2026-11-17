"use strict";

/**
 * Event Stream presentation logic only.
 *
 * CLIVE writes all diagnostic JSONL streams: packet/network records come from
 * NodeSocket; protocol-activity records are observations by CLIVE's FLAME
 * adapter. Their file stream alone does NOT identify which subsystem owns an
 * event (e.g. `flame_started` is adapter startup, not a wire packet).
 * The streams are joined only for display; the visualizer never drives FLAME.
 *
 * The API performs chronological sorting and filtering across the actual log files
 * before limiting/paginating; this renderer preserves the requested order.
 * All functions are pure and can be tested without a browser or running simulation.
 */
const CliveEventStream = (() => {
  const EVENT_HELP = Object.freeze({
    started: ["Node started", "CLIVE launched this node worker. This is a local lifecycle event, not a FLAME protocol packet."],
    flame_started: ["FLAME runtime initialized", "CLIVE’s FLAME adapter initialized the node’s protocol runtime. This is a local startup event, not a transmitted FLAME packet."],
    flame_hello_sent: ["HELLO submitted", "FLAME queued HELLO for sending. Look for a separate HELLO TX/RX record to confirm actual network transmission or reception."],
    flame_join_sent: ["JOIN submitted", "FLAME submitted JOIN toward this Cluster Head. The actual TX/RX is logged separately by CLIVE."],
    flame_joined: ["Associated after JOIN_ACCEPT", "The Sensor Node processed JOIN_ACCEPT and entered ASSOCIATED state. This is a state change, not a packet emission."],
    flame_ch_discovered: ["Cluster Head discovered", "A compatible HELLO was processed by the Sensor Node. Discovery does not yet mean association."],
    flame_connection_lost: ["Sensor association lost", "The Sensor Node observed its association becoming inactive or receiving a LEAVE."],
    flame_member_expired: ["CH membership timed out", "The Cluster Head's FLAME neighbor table marked an associated sensor INACTIVE."],
    flame_join_pending_expired: ["Pending JOIN expired", "The Cluster Head's neighbor table marked a provisional sensor JOIN INACTIVE; it was never confirmed as a member."],
    flame_leave_observed: ["LEAVE processed by Cluster Head", "The Cluster Head processed a LEAVE from this sensor."],
    flame_leave_sent: ["LEAVE queued by Sensor Node", "The Sensor Node submitted a LEAVE before shutdown."],
    flame_sensor_queued: ["SENSOR_DATA queued", "A SENSOR_DATA message entered FLAME's outgoing queue; radio delivery is not implied."],
    flame_sensor_received: ["SENSOR_DATA accepted", "FLAME accepted a complete SENSOR_DATA observation at the Cluster Head; NodeSocket RX alone is insufficient evidence."],
    flame_message_reassembled: ["FLAME message reassembled", "All expected fragments arrived and were reassembled into one FLAME message."],
    flame_trained: ["Local training executed", "The Cluster Head performed a local training update. This is FLAME internal work, not a wire packet."],
    flame_model_sent: ["MODEL_UPDATE transfer started", "The Cluster Head initiated a MODEL_UPDATE transfer. This does not mean it completed."],
    flame_model_received: ["MODEL_UPDATE buffered", "FLAME received the complete update into its aggregation buffer; actual aggregation is recorded separately."],
    flame_aggregated: ["Peer model aggregated", "The Cluster Head applied a peer aggregation update to its local model."],
    flame_transfer_complete: ["Reliable transfer ACK-complete", "The sender received the acknowledgments needed to complete the recorded reliable transfer. The packet type distinguishes SENSOR_DATA from MODEL_UPDATE."],
    flame_transfer_failed: ["Reliable transfer failed", "FLAME reported failure of this reliable transfer. Check its recorded packet type and failure reason."],
    flame_unassociated_sensor_rejected: ["Unassociated sensor rejected", "The Cluster Head refused a SENSOR_DATA message because the sender is not associated."],
    flame_invalid_model_peer: ["Incompatible model peer", "The incoming MODEL_UPDATE sender was not a valid compatible Cluster Head."],
    flame_sample_collected: ["FLAME sensing collected observation", "CLIVE recorded successful execution of FLAME SensingModule.collect_once(), supplied by the CLIVE MNIST data source. No wire packet is implied by this event alone."],
    flame_sample_buffered: ["Observation held offline", "The Sensor Node has no associated Cluster Head; its observation remains in the offline queue."],
    flame_offline_sample_dropped: ["Offline observation discarded", "The offline sample queue reached capacity, so its oldest observation was discarded."],
    flame_fl_error: ["FLAME integration exception", "CLIVE's adapter caught an exception while executing the FLAME workflow; Inspect shows the responsible stage/component where recorded. It is not a radio drop."],
    flame_error: ["Communication error", "The FLAME communication module reported an error."],
    flame_sensor_enqueue_blocked: ["Sensor queue full", "FLAME could not accept this observation into its outgoing queue."],
    flame_sample_source_exhausted: ["Sensor dataset exhausted", "This sensor has no unused samples left. Protocol housekeeping and departure continue."],
    flame_invalid_mnist_sample_rejected: ["MNIST observation rejected by CLIVE", "CLIVE's workload validator rejected invalid MNIST data before it entered FLAME's dataset manager."],
    flame_model_rejected_revision_limit: ["Model rejected at revision limit", "CLIVE refused another model at the protocol's configured 8-bit revision limit; this is an integration guard."],
    flame_model_revision_limit_reached: ["Model revision limit reached", "CLIVE recorded the 8-bit revision ceiling while servicing the FLAME workflow."],
    flame_model_retry_scheduled: ["Model reattempt scheduled", "CLIVE's adapter will try sending this model again after a delay; FLAME's ACK algorithm is unchanged."],
    flame_model_retry_superseded: ["Model retry cancelled — superseded", "CLIVE no longer has the same cached model payload, so no retransmission was promised."],
    flame_radio_backpressure_drop: ["Local send queue full", "CLIVE adapter reported a NodeSocket backpressure drop. This is a local delivery failure, not confirmation that the receiver rejected the packet."],
    flame_protocol_callback_slow: ["Slow protocol callback", "Synchronous FLAME processing took at least one configured ACK timeout; received ACKs may be delayed."],
    flame_model_retry_exhausted: ["Model reattempts exhausted", "The adapter has no remaining model reattempts after FLAME reported failed transfers."],
    flame_datagram_error: ["FLAME datagram error", "The FLAME communication module reported an invalid or failed datagram operation."],
    transmit: ["Datagram transmitted", "CLIVE emitted a UDP datagram into its emulated radio. Receiver delivery is a separate event."],
    receive: ["Datagram received", "A UDP datagram reached this node's NodeSocket. Full FLAME acceptance may happen later."],
    delivery_drop: ["Radio delivery failed", "CLIVE did not deliver the datagram to this intended receiver."],
    local_drop: ["Outgoing datagram dropped", "The local emulated socket dropped the send before successful delivery."],
    attempt: ["Send attempted", "A node tried to send a datagram; this is not a radio transmission yet."],
    admit: ["Datagram queued", "CLIVE accepted the datagram into its send queue; this is not a radio transmission yet."],
    scheduled_delivery: ["Delivery scheduled", "CLIVE placed a recipient delivery in its delay scheduler."],
    kernel_send: ["Kernel send completed", "The host OS accepted a UDP sendto operation; application delivery is not confirmed."],
    delivery_attempt: ["Recipient delivery considered", "One intended receiver was evaluated for reachability and radio loss."],
    socket_error: ["Socket error", "A CLIVE socket operation raised an exception."],
  });
  const CLIVE_LIFECYCLE = new Set(["started", "flame_started"]);
  // These flame_-prefixed events are CLIVE adapter/source/diagnostic operations,
  // not independent FLAME protocol state transitions. Keep the explicit list
  // synchronized with RunData._clive_adapter_event in visualizer.py.
  const CLIVE_ADAPTER = new Set([
    "flame_sample_collected", "flame_sample_buffered", "flame_offline_sample_dropped",
    "flame_sample_source_exhausted", "flame_model_retry_scheduled",
    "flame_model_retry_superseded", "flame_model_retry_exhausted",
    "flame_radio_backpressure_drop", "flame_protocol_callback_slow",
    "flame_model_revision_limit_reached", "flame_learning_evaluation_error",
    "flame_invalid_mnist_sample_rejected", "flame_model_rejected_revision_limit",
    "flame_fl_error",
  ]);
  // Deliberately allowlisted: `flame_` is the CLIVE adapter log prefix, not a
  // guarantee of ownership. Newly introduced entries default to CLIVE diagnostics
  // until reviewed, preventing protocol over-attribution after future updates.
  const FLAME_ACTION_EVENTS = new Set([
    "flame_hello_sent", "flame_join_sent", "flame_joined", "flame_ch_discovered",
    "flame_leave_observed", "flame_leave_sent", "flame_sensor_queued",
    "flame_sensor_received", "flame_model_sent", "flame_model_received",
    "flame_transfer_complete", "flame_transfer_failed",
    "flame_unassociated_sensor_rejected", "flame_invalid_model_peer",
    "flame_datagram_error", "flame_error", "flame_hello_queue_dropped",
    "flame_join_queue_dropped", "flame_leave_queue_dropped",
  ]);
  const FLAME_INTERNAL_EVENTS = new Set([
    "flame_connection_lost", "flame_member_expired", "flame_join_pending_expired",
    "flame_message_reassembled", "flame_trained", "flame_aggregated",
    "flame_sensor_enqueue_blocked", "flame_model_rejected_or_duplicate",
  ]);
  const FLAME_BEHAVIOR = new Set([
    ...FLAME_ACTION_EVENTS,
    ...FLAME_INTERNAL_EVENTS,
  ]);
  const FAILURE_PATTERN = /failed|error|rejected|dropped|retry_exhausted|queue_dropped|enqueue_blocked/;

  const HIGHLIGHT_EVENTS = new Set([
    "flame_hello_sent", "flame_ch_discovered", "flame_join_sent", "flame_joined",
    "flame_sensor_received", "flame_trained", "flame_model_sent", "flame_model_received",
    "flame_aggregated", "flame_transfer_complete", "flame_transfer_failed",
    "flame_connection_lost", "flame_member_expired", "flame_leave_observed", "flame_leave_sent",
  ]);
  const COLLAPSIBLE_EVENTS = new Set(["flame_hello_sent", "flame_ch_discovered", "flame_join_sent", "delivery_drop", "local_drop"]);

  const PACKET_NAMES = ["HELLO", "LEAVE", "SENSOR_DATA", "SENSOR_DATA_ACK",
    "MODEL_UPDATE", "MODEL_UPDATE_ACK", "JOIN", "JOIN_ACCEPT"];
  const RELATED_PACKET = Object.freeze({
    flame_hello_sent:0, flame_ch_discovered:0,
    flame_join_sent:6, flame_joined:7,
    flame_leave_sent:1, flame_leave_observed:1,
    flame_sensor_queued:2, flame_sensor_received:2,
    flame_model_sent:4, flame_model_received:4,
    flame_invalid_model_peer:4, flame_unassociated_sensor_rejected:2,
  });
  function isFlameCategory(group) {
    return ["flame-packet", "flame-action", "flame-internal"].includes(group);
  }
  function relatedPacket(event) {
    // If a packet type was recorded, trust that field rather than inferring a
    // different message family from an adapter callback's generic event name.
    // In particular, transfer_complete applies to both SENSOR_DATA (2) and
    // MODEL_UPDATE (4), and must never default to MODEL_UPDATE.
    if (event.packet_type != null) {
      return Number.isInteger(event.packet_type) && event.packet_type >= 0 &&
        event.packet_type < PACKET_NAMES.length ? PACKET_NAMES[event.packet_type] : null;
    }
    const id = RELATED_PACKET[event.event];
    return id === undefined ? null : PACKET_NAMES[id];
  }

  /** Origin and outcome are distinct: FLAME failures are not CLIVE radio drops. */
  function isFailure(event) {
    // A duplicate MODEL_UPDATE can be a successfully deduplicated replay,
    // rather than an outright validation failure.
    if (event.event === "flame_model_rejected_or_duplicate"
        && (event.details?.rejection_kind ?? event.rejection_kind) === "duplicate") return false;
    return ["delivery_drop", "local_drop", "socket_error", "flame_radio_backpressure_drop"].includes(event.event)
      || FAILURE_PATTERN.test(String(event.event || ""));
  }

  function category(event) {
    const name = String(event.event || "");
    if (event.stream === "packet") {
      // A decoded real TX/RX, not just FLAME queue or callback telemetry.
      if (["transmit", "receive"].includes(name) &&
          Number.isInteger(event.packet_type) && event.packet_type >= 0 &&
          event.packet_type < PACKET_NAMES.length) return "flame-packet";
      return "network";
    }
    if (CLIVE_LIFECYCLE.has(name) || CLIVE_ADAPTER.has(name)) return "clive";
    if (["protocol", "connection"].includes(event.stream) && FLAME_ACTION_EVENTS.has(name)) return "flame-action";
    if (["protocol", "connection"].includes(event.stream) && FLAME_INTERNAL_EVENTS.has(name)) return "flame-internal";
    return "clive"; // Unknown events are not assumed to be FLAME-owned.
  }

  function groupLabel(event) {
    return ({"flame-packet":"FLAME · PACKET", "flame-action":"FLAME · ACTION",
      "flame-internal":"FLAME · INTERNAL", clive:"CLIVE", network:"NETWORK"})[category(event)] || "CLIVE";
  }

  function explanation(event) {
    const documented = EVENT_HELP[event.event];
    if (documented) return documented;
    const description = String(event.event || "unknown")
      .replace(/^flame_/, "").replace(/_/g, " ");
    const title = description.charAt(0).toUpperCase() + description.slice(1);
    return [title, "Internal event. Open Inspect to see the original fields."];
  }

  /** A generic reliable-transfer callback must be titled from the ACTUAL packet type. */
  function displayTitle(event, packetName) {
    if (event.event === "flame_transfer_complete" || event.event === "flame_transfer_failed") {
      const family = relatedPacket(event);
      const subject = family ? `${family} transfer` : event.packet_type == null
        ? "Reliable transfer (type unrecorded)"
        : `Reliable transfer (unknown type ${event.packet_type})`;
      return `${subject} ${event.event === "flame_transfer_complete" ? "ACK-complete" : "failed"}`;
    }
    if (event.stream === "packet" && event.packet_type != null) {
      const type = relatedPacket(event) || `Unknown type ${event.packet_type}`;
      const stage = {transmit:"TX", receive:"RX", delivery_drop:"Radio drop", local_drop:"Local drop"}[event.event];
      if (stage) return `${type} · ${stage}`;
    }
    return explanation(event)[0];
  }

  /** Only NodeSocket TX/RX counts as an observed packet, not queued actions. */
  function packetLabel(event, packetName) {
    const related = relatedPacket(event);
    const wire = event.stream === "packet";
    const parts = [wire ? (related || "Unknown datagram") : related ? `${related} (related)` : null];
    if (event.sequence_number != null) {
      parts.push(`${wire ? "seq" : "ref seq"} #${event.sequence_number}`);
    }
    if (event.trigger_sequence_number != null) parts.push(`trigger seq #${event.trigger_sequence_number}`);
    if (event.message_id != null) parts.push(`message #${event.message_id}`);
    if (event.update_id != null) parts.push(`revision #${event.update_id}`);
    return parts.join(" · ") || "—";
  }

  function matches(event, query, packetName) {
    if (!query) return true;
    return [event.node, event.source, event.target, event.event, relatedPacket(event),
      event.sequence_number, event.trigger_sequence_number, event.message_id,
      event.update_id, event.reason, JSON.stringify(event.details || {})]
      .some(value => String(value ?? "").toLowerCase().includes(query));
  }

  function matchesFilter(event, filter, {node="", packet="all", outcome="all"}={}) {
    const group = category(event);
    const name = event.event;
    let passes = true;
    if (filter === "highlights") passes = ["flame-action","flame-internal"].includes(group)
      && (HIGHLIGHT_EVENTS.has(name) || isFailure(event));
    else if (filter === "flame") passes = isFlameCategory(group);
    else if (filter === "protocol") passes = ["flame-action", "flame-internal"].includes(group);
    else if (["flame-packet", "flame-action", "flame-internal", "clive"].includes(filter)) passes = group === filter;
    else if (filter === "network") passes = event.stream === "packet" &&
      ["transmit", "receive", "delivery_drop", "local_drop", "socket_error"].includes(name);
    else if (filter === "drop" || filter === "failures") passes = isFailure(event);
    else if (filter === "activity") passes = ["flame-action", "flame-internal"].includes(group)
      && !["flame_message_reassembled", "flame_sensor_queued"].includes(name);
    else if (filter === "key") passes = ["flame-action", "flame-internal"].includes(group)
      && !["flame_hello_sent", "flame_message_reassembled", "flame_sensor_queued"].includes(name);
    else if (filter === "packets") passes = event.stream === "packet" &&
      ["transmit", "local_drop"].includes(name);
    else if (["transmit", "receive"].includes(filter)) passes = event.stream === "packet" && name === filter;
    else if (filter === "packet") passes = event.stream === "packet";
    else if (filter === "internal") passes = ["clive", "network"].includes(group);
    else passes = filter === "all";
    if (!passes) return false;
    if (node && ![event.node, event.source, event.target].includes(node)) return false;
    if (packet && packet !== "all" && packet !== relatedPacket(event)) return false;
    if (outcome === "failure" && !isFailure(event)) return false;
    if (outcome === "nonfailure" && isFailure(event)) return false;
    return true;
  }

  /** Escape all event-derived text before interpolating it into the table. */
  function rowHtml(event, packetName, escapeHtml) {
    const group = category(event);
    const [, description] = explanation(event);
    const title = displayTitle(event, packetName);
    const from = event.source || (["protocol", "connection"].includes(event.stream) ? event.node : "—");
    const to = event.target || (event.is_broadcast ? "broadcast" : "—");
    const radio = [
      event.distance_m != null ? `${Number(event.distance_m).toFixed(1)} m` : null,
      event.rssi_dbm != null ? `${Number(event.rssi_dbm).toFixed(1)} dBm` : null,
    ].filter(Boolean).join(" · ");
    const fields = Object.entries(event.details || {}).map(([key, value]) =>
      `<dt>${escapeHtml(key.replace(/_/g, " "))}</dt>`
      + `<dd>${escapeHtml(typeof value === "object" ? JSON.stringify(value) : value)}</dd>`
    ).join("");
    const reason = event.reason
      ? `<span class="event-reason">Reason: ${escapeHtml(event.reason)}</span>` : "";
    const context = radio ? `<span class="event-context">${escapeHtml(radio)}</span>` : "";
    const repeated = event._repeat?.length > 1 ? event._repeat : null;
    const countMark = repeated ? `<span class="event-context"> × ${repeated.length} similar records · ${Math.min(...repeated.map(r=>r.time_s)).toFixed(3)}–${Math.max(...repeated.map(r=>r.time_s)).toFixed(3)}s</span>` : "";
    const instances = repeated ? `<details class="event-instances"><summary>${repeated.length} underlying records</summary><ol>${repeated.map(rec=>
      `<li>${rec.time_s.toFixed(6)}s · ${escapeHtml(rec.seq)} · ${escapeHtml(rec.event)}</li>`).join("")}</ol></details>` : "";

    return `<tr class="event-row event-${group}${isFailure(event) ? " is-failure" : ""}">
      <td class="mono event-time">${event.time_s >= 0 ? event.time_s.toFixed(6) + "s" : "—"}</td>
      <td><span class="event-phase ${group}">${escapeHtml(groupLabel(event))}</span></td>
      <td><strong>${escapeHtml(title)}</strong><span class="event-description">${escapeHtml(description)}</span>${countMark}${reason}</td>
      <td class="mono event-route">${escapeHtml(from)} <span class="muted">→</span> ${escapeHtml(to)}</td>
      <td class="mono event-packet">${escapeHtml(packetLabel(event, packetName))}</td>
      <td><details class="event-inspect"><summary aria-label="Inspect event ${escapeHtml(event.seq)}">Inspect</summary>
        <div class="event-inspect-body"><p>Record ID ${escapeHtml(event.seq)} · ${escapeHtml(event.event)} · ${escapeHtml(event.stream)} stream · displayed as ${escapeHtml(groupLabel(event))}${isFailure(event) ? " · failure outcome" : ""}</p>
        <p>${group === "flame-packet" ? "Decoded FLAME packet TX/RX observed by CLIVE NodeSocket. RX does not prove application acceptance." : group === "flame-action" ? "FLAME state or packet-processing action observed by the CLIVE adapter. This is not itself a wire TX/RX." : group === "flame-internal" ? "FLAME training, aggregation or internal state observed by the CLIVE adapter, not a wire packet." : group === "clive" ? "CLIVE runtime, data source or adapter event." : "CLIVE transport outcome or low-level socket diagnostic."}</p>
        ${context}${instances}<dl>${fields}</dl></div></details></td>
    </tr>`;
  }

  const EMPTY_MESSAGE = `<tr><td colspan="6" class="muted">No recorded events match this playback window and filter. Try another time range or open the full Event History page.</td></tr>`;

  /** Render in the server's global chronological order. No page-only sorting. */
  function render(events, {filter = "highlights", search = "", packetName, escapeHtml,
                           preFiltered = false, order = "desc", node = "", packet = "all",
                           outcome = "all"}) {
    const query = search.trim().toLowerCase();
    const matching = preFiltered ? events : events.filter(event =>
      matchesFilter(event, filter, {node, packet, outcome}) && matches(event, query, packetName));
    const ordered = preFiltered ? matching : [...matching].sort((a,b) =>
      (order === "asc" ? 1 : -1) * (Number(a.time_s) - Number(b.time_s)));
    const collapsed = [];
    for (const event of ordered) {
      const last = collapsed[collapsed.length-1];
      const gap = last ? (order === "asc" ? event.time_s - last._lastAt : last._lastAt - event.time_s) : Infinity;
      const same = last && COLLAPSIBLE_EVENTS.has(event.event)
        && last.event === event.event && last.source === event.source
        && last.target === event.target && last.node === event.node
        && last.packet_type === event.packet_type && gap >= 0 && gap <= 1.5;
      if (same) {
        last._repeat.push(event);
        last._lastAt = event.time_s;
      } else {
        collapsed.push({...event, _repeat:[event], _lastAt:event.time_s});
      }
    }
    const rows = collapsed.slice(0, 160);
    const failures = matching.filter(isFailure).length;
    const wire = matching.filter(event => category(event) === "flame-packet").length;
    const actions = matching.filter(event => category(event) === "flame-action").length;
    const internal = matching.filter(event => category(event) === "flame-internal").length;
    return {
      summary: `${rows.length} rows / ${matching.length} records · ${wire} packet · ${actions} action · ${internal} internal · ${failures} failures`,
      html: rows.map(event => rowHtml(event, packetName, escapeHtml)).join("") || EMPTY_MESSAGE,
    };
  }

  return Object.freeze({category, groupLabel, isFailure, explanation, displayTitle,
    relatedPacket, packetLabel, matchesFilter, render});
})();
