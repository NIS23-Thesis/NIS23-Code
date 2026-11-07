"use strict";

/**
 * Event Stream presentation logic only.
 *
 * CLIVE packet logs record network operations; FLAME logs record protocol actions.
 * The two streams are joined for display, never used to drive the experiment.
 *
 * The visualizer API supplies events in ascending (time_s, record ID) order.
 * render() reverses that order to show the newest events first in O(n) time.
 * All functions are pure and can be tested without a browser or running simulation.
 */
const CliveEventStream = (() => {
  const EVENT_HELP = Object.freeze({
    started: ["Node started", "CLIVE started this node's runtime."],
    flame_started: ["FLAME initialized", "Protocol modules and state are initialized."],
    flame_hello_sent: ["Cluster Head announced itself", "HELLO was submitted to the communication module; see packet traffic to verify radio delivery."],
    flame_join_sent: ["Sensor requested membership", "The Sensor Node sent a JOIN request to this Cluster Head."],
    flame_joined: ["Sensor joined Cluster Head", "JOIN_ACCEPT was processed and the Sensor Node entered ASSOCIATED state."],
    flame_ch_discovered: ["Cluster Head discovered", "The Sensor Node received a HELLO from this CH; this is not an association."],
    flame_connection_lost: ["Sensor association lost", "The Sensor Node observed its association becoming inactive or receiving a LEAVE."],
    flame_member_expired: ["CH membership timed out", "The Cluster Head's FLAME neighbor table marked an associated sensor INACTIVE."],
    flame_join_pending_expired: ["Pending JOIN expired", "The Cluster Head's neighbor table marked a provisional sensor JOIN INACTIVE; it was never confirmed as a member."],
    flame_leave_observed: ["CH received LEAVE", "The Cluster Head observed a sensor departure request."],
    flame_leave_sent: ["Sensor left cluster", "A LEAVE was submitted before shutdown."],
    flame_sensor_queued: ["Sensor observation queued", "Sensor data entered FLAME's send queue; it has not necessarily been delivered."],
    flame_sensor_received: ["Sensor observation accepted", "The Cluster Head accepted a complete SENSOR_DATA observation."],
    flame_message_reassembled: ["Message fully reassembled", "All expected fragments arrived and were assembled into one FLAME message."],
    flame_trained: ["Local model trained", "The Cluster Head performed a local training update. This action itself is not a wire packet."],
    flame_model_sent: ["Model transfer started", "The Cluster Head initiated a model update transfer. This does not mean it completed."],
    flame_model_received: ["Peer model received", "The receiving Cluster Head accepted a complete MODEL_UPDATE message."],
    flame_aggregated: ["Peer models aggregated", "The Cluster Head applied an aggregation update to its local model."],
    flame_transfer_complete: ["Transfer acknowledged complete", "The sender received the acknowledgments necessary for this transfer to finish."],
    flame_transfer_failed: ["Transfer failed", "FLAME gave up delivering this transfer; check the failure reason."],
    flame_unassociated_sensor_rejected: ["Unassociated sensor rejected", "The Cluster Head refused a SENSOR_DATA message because the sender is not associated."],
    flame_invalid_model_peer: ["Incompatible model peer", "The incoming MODEL_UPDATE sender was not a valid compatible Cluster Head."],
    flame_sample_collected: ["Observation submitted", "A sensor observation was generated/submitted to the sensing module."],
    flame_sample_buffered: ["Observation held offline", "The Sensor Node has no associated Cluster Head; its observation remains in the offline queue."],
    flame_offline_sample_dropped: ["Offline observation discarded", "The offline sample queue reached capacity, so its oldest observation was discarded."],
    flame_fl_error: ["Federated learning error", "FLAME raised an error while processing a model or sensor observation."],
    flame_error: ["Communication error", "The FLAME communication module reported an error."],
    flame_sensor_enqueue_blocked: ["Sensor queue full", "FLAME could not accept this observation into its outgoing queue."],
    flame_sample_source_exhausted: ["Sensor dataset exhausted", "This sensor has no unused samples left. Protocol housekeeping and departure continue."],
    flame_model_retry_scheduled: ["Model reattempt scheduled", "CLIVE's adapter will try sending this model again after a delay; FLAME's ACK algorithm is unchanged."],
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
  const TECHNICAL_EVENTS = new Set(["attempt", "admit", "scheduled_delivery", "kernel_send", "delivery_attempt"]);

  const FAILURE_PATTERN = /failed|error|rejected|dropped/;

  /** Four user-facing event groups; this does not change underlying FLAME packets. */
  function category(event) {
    if (["delivery_drop", "local_drop", "socket_error"].includes(event.event)
        || FAILURE_PATTERN.test(event.event)) return "drop";
    if (["protocol", "connection"].includes(event.stream)) return "protocol";
    if (event.event === "receive") return "receive";
    if (event.event === "transmit") return "transmit";
    return "internal"; // Queue/kernel steps are not actual radio transmissions.
  }

  function explanation(event) {
    const documented = EVENT_HELP[event.event];
    if (documented) return documented;
    const description = String(event.event || "unknown")
      .replace(/^flame_/, "").replace(/_/g, " ");
    const title = description.charAt(0).toUpperCase() + description.slice(1);
    return [title, "Internal event. Open Inspect to see the original fields."];
  }

  /** Packet seq belongs to a datagram. Trigger seq explains what caused an action. */
  function packetLabel(event, packetName) {
    const parts = [
      event.packet_type == null ? null : packetName(event.packet_type),
      event.sequence_number == null ? null : `seq #${event.sequence_number}`,
      event.trigger_sequence_number == null ? null : `trigger seq #${event.trigger_sequence_number}`,
      event.message_id == null ? null : `message #${event.message_id}`,
      event.update_id == null ? null : `revision #${event.update_id}`,
    ];
    return parts.filter(Boolean).join(" · ") || "— (not a packet)";
  }

  /** Case-insensitive search across protocol and network provenance. */
  function matches(event, query, packetName) {
    if (!query) return true;
    return [event.node, event.source, event.target, event.event,
      packetName(event.packet_type), event.sequence_number,
      event.trigger_sequence_number, event.message_id, event.update_id,
      event.reason, JSON.stringify(event.details || {})]
      .some(value => String(value ?? "").toLowerCase().includes(query));
  }

  function matchesFilter(event, filter) {
    if (filter === "activity") return ["protocol", "connection"].includes(event.stream)
      && event.event.startsWith("flame_")
      && !["flame_message_reassembled", "flame_sensor_queued", "flame_sample_collected", "flame_started"].includes(event.event);
    if (filter === "packets") return event.stream === "packet" &&
      ["transmit", "local_drop"].includes(event.event);
    if (filter === "network") return event.stream === "packet" &&
      ["transmit", "receive", "delivery_drop", "local_drop", "socket_error"].includes(event.event);
    if (filter === "key") return ["protocol", "connection"].includes(event.stream)
      && !["flame_hello_sent", "flame_message_reassembled",
        "flame_sample_collected", "flame_sensor_queued"].includes(event.event);
    if (filter === "protocol") return ["protocol", "connection"].includes(event.stream);
    return filter === "all" || category(event) === filter;
  }

  /** Escape all event-derived text before interpolating it into the table. */
  function rowHtml(event, packetName, escapeHtml) {
    const group = category(event);
    const [rawTitle, description] = explanation(event);
    const wireStage = {transmit:"Sent", receive:"Received", delivery_drop:"Lost to receiver", local_drop:"Dropped before send"};
    const type = event.packet_type == null ? null : packetName(event.packet_type);
    const title = event.stream === "packet" && wireStage[event.event] && type
      ? `${type} · ${wireStage[event.event]}` : rawTitle;
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

    return `<tr class="event-row event-${group}">
      <td class="mono event-time">${event.time_s >= 0 ? event.time_s.toFixed(3) + "s" : "—"}</td>
      <td><span class="event-phase ${group}">${event.stream === "packet" ? escapeHtml(({transmit:"TX",receive:"RX",delivery_drop:"LOSS",local_drop:"DROP",socket_error:"ERROR"})[event.event] || "CLIVE") : "FLAME"}</span></td>
      <td><strong>${escapeHtml(title)}</strong><span class="event-description">${escapeHtml(description)}</span>${reason}</td>
      <td class="mono event-route">${escapeHtml(from)} <span class="muted">→</span> ${escapeHtml(to)}</td>
      <td class="mono event-packet">${escapeHtml(packetLabel(event, packetName))}</td>
      <td><details class="event-inspect"><summary aria-label="Inspect event ${escapeHtml(event.seq)}">Inspect</summary>
        <div class="event-inspect-body"><p>Record ID ${event.seq} · ${escapeHtml(event.event)} · ${escapeHtml(event.stream)} stream</p>
        ${context}<dl>${fields}</dl></div></details></td>
    </tr>`;
  }

  const EMPTY_MESSAGE = `<tr><td colspan="6" class="muted">No recorded events match this playback window and filter. Try another time range or open the full Event History page.</td></tr>`;

  /**
   * Return HTML and a human-readable summary; callers decide where to display them.
   * Only the newest 160 rows are rendered, even if the server returned 1,000.
   */
  function render(events, {filter = "key", search = "", packetName, escapeHtml,
                           preFiltered = false}) {
    const query = search.trim().toLowerCase();
    const matching = preFiltered ? events : events.filter(event =>
      matchesFilter(event, filter) && matches(event, query, packetName));
    // /api/events guarantees this order, including records from multiple nodes.
    const newest = matching.slice().reverse();
    const rows = newest.slice(0, 160);
    const failures = matching.filter(event => category(event) === "drop").length;
    const protocol = matching.filter(event => ["protocol", "connection"].includes(event.stream)).length;

    return {
      summary: `${rows.length}/${matching.length} shown · ${protocol} FLAME events · ${failures} failures`,
      html: rows.map(event => rowHtml(event, packetName, escapeHtml)).join("") || EMPTY_MESSAGE,
    };
  }

  return Object.freeze({category, explanation, packetLabel, matchesFilter, render});
})();
