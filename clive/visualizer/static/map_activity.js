"use strict";
/**
 * Map focus is ONE recorded event, not an overlay of every recent action.
 * There is no synthetic latency, no fabricated event ordering, and no inferred JOIN.
 * The full Event History remains the authoritative record of concurrent activity.
 */
const CliveMapActivity = (() => {
  const EVENTS = Object.freeze({
    flame_hello_sent:          {stage: "hello", label: "HELLO broadcast", color: "#61c5ff", priority: 1},
    flame_ch_discovered:       {stage: "discovered", label: "HELLO received", color: "#61c5ff", priority: 2},
    flame_join_sent:           {stage: "request", label: "JOIN request", color: "#ffcc75", priority: 5},
    flame_joined:              {stage: "accepted", label: "JOIN accepted", color: "#70e1a9", priority: 7},
    flame_sensor_received:     {stage: "confirmed", label: "Sensor data accepted", color: "#70e1a9", priority: 4},
    flame_model_sent:          {stage: "model", label: "Model transfer started", color: "#b5a2ff", priority: 3},
    flame_model_received:      {stage: "model", label: "Model received", color: "#b5a2ff", priority: 4},
    flame_transfer_complete:   {stage: "model", label: "Transfer completed", color: "#b5a2ff", priority: 4},
    flame_transfer_failed:     {stage: "exit", label: "Transfer failed", color: "#ff8997", priority: 8},
    flame_trained:             {stage: "learning", label: "Local model trained", color: "#b5a2ff", priority: 3},
    flame_aggregated:          {stage: "learning", label: "Models aggregated", color: "#b5a2ff", priority: 3},
    flame_leave_sent:          {stage: "exit", label: "LEAVE sent", color: "#ff8997", priority: 8},
    flame_leave_observed:      {stage: "exit", label: "LEAVE received", color: "#ff8997", priority: 8},
    flame_connection_lost:     {stage: "exit", label: "Connection lost", color: "#ff8997", priority: 8},
    flame_member_expired:      {stage: "exit", label: "Membership timed out", color: "#ff8997", priority: 8},
    flame_join_pending_expired:{stage: "exit", label: "JOIN timed out", color: "#ff8997", priority: 8}
  });
  const AGE_LIMIT = 1.10; // Simulation seconds. No overlays beyond this lookback.
  function upperBound(events, timeS) {
    let lo = 0, hi = events.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (Number(events[mid].time_s) <= timeS) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }
  function narrate(ev) {
    const who = ev.source || "A node", peer = ev.target || "another node";
    switch(ev.stage) {
      case "hello": return `${who} announced that it is available. This is NOT a connection or JOIN.`;
      case "discovered": return `${peer} heard a HELLO from ${who}. It has discovered a candidate but has not joined.`;
      case "request": return `${who} sent a JOIN request to ${peer}. Acceptance has not yet been observed.`;
      case "accepted": return `${who} received JOIN_ACCEPT for ${peer}. CH-side data confirmation is separate.`;
      case "confirmed": return `${peer} accepted sensor data from ${who}. This confirms an observed data exchange.`;
      case "exit": return ev.action === "flame_transfer_failed" ? `${who} reported a failed model transfer with ${peer}.` :
        `${who} recorded a departure, loss or timeout involving ${peer}. See Event History for the reason.`;
      case "model": return `${who} recorded model-transfer activity${ev.target ? ` involving ${peer}` : ""}. See Event History for the packet/transfer status.`;
      case "learning": return ev.action === "flame_aggregated" && ev.target
        ? `${ev.target} aggregated peer model updates after a message from ${who}. The arrow shows the triggering peer, not necessarily every contributor.`
        : `${who} updated its local model. This is computation, not a radio packet.`;
      default: return "";
    }
  }
  function isVisible(row) {
    if (!EVENTS[row?.action] || !row.source || !Number.isFinite(Number(row.time_s))) return false;
    if ((row.action === 'flame_transfer_complete' || row.action === 'flame_transfer_failed') &&
        row.packet_type != null && Number(row.packet_type) !== 4) return false;
    return true;
  }
  /** Sorted recorded actions; same-timestamp actions remain distinct. No fake timestamps. */
  function timeline(events) {
    return (events || []).filter(isVisible).slice().sort((a,b) => Number(a.time_s)-Number(b.time_s));
  }
  function fromEvent(row, timeS) {
    if (!isVisible(row) || timeS < Number(row.time_s)) return null;
    const data={...EVENTS[row.action], ...row, time_s: Number(row.time_s), age: Math.max(0,timeS-Number(row.time_s))};
    return {...data, otherCount:0, description:narrate(data)};
  }
  /** Most recently recorded event, not the highest priority within an arbitrary 45 ms interval. */
  function focus(events, timeS, selectedNode = null) {
    if (!Array.isArray(events) || !events.length || !Number.isFinite(timeS)) return null;
    const end = upperBound(events,timeS);
    let latest = null;
    for (let i=end-1; i>=0 && end-i<=4000; i--) {
      const row=events[i], t=Number(row.time_s);
      if (timeS-t > AGE_LIMIT) break;
      if (!isVisible(row)) continue;
      if (selectedNode && row.source !== selectedNode && row.target !== selectedNode) {
        if (!latest) latest=row; // fallback if no selected-node activity exists
        continue;
      }
      return fromEvent(row,timeS);
    }
    return latest ? fromEvent(latest,timeS) : null;
  }
  // Legacy helper retained for existing CLIVE tests, but the MAP does not render
  // all recent actions anymore. It always calls focus().
  function recent(events, timeS, limit = 1) {
    const item=focus(events,timeS);
    return item && limit > 0 ? [item] : [];
  }
  return Object.freeze({focus, fromEvent, timeline, recent, AGE_LIMIT});
})();
if (typeof module !== "undefined" && module.exports) module.exports = CliveMapActivity;
