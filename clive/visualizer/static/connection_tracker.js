"use strict";

/**
 * Reconstruct observed FLAME association history from recorded CLIVE events.
 * This module NEVER infers membership from physical distance. Radio reachability
 * is displayed separately by the topology UI. No FLAME state is modified.
 */
const CliveConnectionTracker = (() => {
  const descriptions = Object.freeze({
    flame_ch_discovered: "Discovered Cluster Head (HELLO)",
    flame_join_sent: "Sent JOIN request",
    flame_joined: "JOIN accepted by Sensor Node",
    flame_sensor_received: "Cluster Head accepted sensor observation",
    flame_leave_sent: "Sensor sent LEAVE",
    flame_leave_observed: "Cluster Head observed LEAVE",
    flame_connection_lost: "Sensor association lost",
    flame_member_expired: "Cluster Head marked sensor inactive",
    flame_join_pending_expired: "JOIN attempt expired before association",
    flame_model_sent: "Started peer model transfer",
    flame_model_received: "Accepted complete peer model",
    flame_transfer_complete: "Transfer acknowledged complete",
    flame_transfer_failed: "Transfer failed",
    flame_model_retry_scheduled: "Model reattempt scheduled",
    flame_model_retry_exhausted: "Model reattempts exhausted",
  });
  const membershipEvents = new Set([
    "flame_ch_discovered", "flame_join_sent", "flame_joined", "flame_leave_sent",
    "flame_leave_observed", "flame_connection_lost", "flame_member_expired", "flame_join_pending_expired",
  ]);
  const exitEvents = new Set([
    "flame_leave_sent", "flame_leave_observed", "flame_connection_lost", "flame_member_expired",
    "flame_join_pending_expired",
  ]);
  function empty() {
    return {cursor: 0, time: -1, associations: new Map(), peers: new Map(),
      attempts: new Map(), lastExit: new Map(), history: new Map(), membershipHistory: new Map(), peerDetails: new Map()};
  }
  function appendHistory(state, node, ev) {
    if (!node) return;
    let rows = state.history.get(node);
    if (!rows) { rows = []; state.history.set(node, rows); }
    rows.push(ev);
    if (rows.length > 160) rows.shift();
  }
  function apply(state, ev) {
    if (!ev || !ev.source || !ev.target) return state;
    appendHistory(state, ev.source, ev);
    if (ev.target !== ev.source) appendHistory(state, ev.target, ev);
    if (membershipEvents.has(ev.action)) {
      let events = state.membershipHistory.get(ev.source);
      if (!events) { events = []; state.membershipHistory.set(ev.source, events); }
      events.push(ev);
      if (events.length > 160) events.shift();
      if (ev.target !== ev.source) {
        let peerEvents = state.membershipHistory.get(ev.target);
        if (!peerEvents) { peerEvents = []; state.membershipHistory.set(ev.target, peerEvents); }
        peerEvents.push(ev);
        if (peerEvents.length > 160) peerEvents.shift();
      }
    }
    const {source, target, action} = ev;
    if (action === "flame_join_sent") {
      const previous = state.attempts.get(source);
      state.attempts.set(source, {ch: target, at: ev.time_s,
        count: previous?.ch === target ? previous.count + 1 : 1});
      // A new JOIN indicates the sensor is no longer using the old association.
      state.associations.delete(source);
    } else if (action === "flame_joined") {
      state.associations.set(source, {ch: target, joinedAt: ev.time_s,
        confirmedAt: null, lastSeen: ev.time_s});
      state.attempts.delete(source);
    } else if (action === "flame_sensor_received") {
      const previous = state.associations.get(source);
      state.associations.set(source, {ch: target,
        joinedAt: previous?.ch === target ? previous.joinedAt : null,
        confirmedAt: previous?.ch === target ? previous.confirmedAt ?? ev.time_s : ev.time_s,
        lastSeen: ev.time_s});
      state.attempts.delete(source);
    } else if (exitEvents.has(action)) {
      if (state.associations.get(source)?.ch === target)
        state.associations.delete(source);
      if (state.attempts.get(source)?.ch === target)
        state.attempts.delete(source);
      state.lastExit.set(source, {ch: target, at: ev.time_s, reason: ev.reason || descriptions[action], action});
    }
    if (["flame_model_sent", "flame_model_received", "flame_transfer_complete", "flame_transfer_failed",
      "flame_model_retry_scheduled", "flame_model_retry_exhausted"].includes(action) &&
        (ev.packet_type == null || Number(ev.packet_type) === 4)) {
      const key = [source, target].sort().join("|");
      state.peers.set(key, ev.time_s); // Legacy topology expects last event time.
      state.peerDetails.set(key, {at: ev.time_s, action, source, target, reason: ev.reason});
    }
    return state;
  }
  function historyFor(state, node, max = 15) {
    // Keep JOIN/LEAVE/disconnection milestones visible even when hundreds of
    // sensor observations or model fragments occur later in the same run.
    const membership = state.membershipHistory.get(node) || [];
    const model = (state.history.get(node) || []).filter(ev =>
      ev.action === "flame_model_received" || ev.action === "flame_model_sent" ||
      (ev.action === "flame_transfer_failed" && Number(ev.packet_type) === 4) ||
      (ev.action === "flame_transfer_complete" && Number(ev.packet_type) === 4)
    ).slice(-5);
    return [...membership.slice(-max), ...model]
      .sort((a,b) => b.time_s - a.time_s || (b.seq || 0) - (a.seq || 0)).slice(0,max);
  }
  function label(ev) { return descriptions[ev.action] || String(ev.action || "Event").replaceAll("_", " "); }
  return {empty, apply, historyFor, label};
})();
if (typeof module !== "undefined" && module.exports) module.exports = CliveConnectionTracker;
