"use strict";
const assert = require("node:assert/strict");
const tracker = require("../visualizer/static/connection_tracker.js");
const e = (action, source, target, time_s, extra = {}) => ({action, source, target, time_s, ...extra});
const entries = [
  e("flame_join_sent", "SN-1","CH-1",1),
  e("flame_join_sent", "SN-1","CH-1",1.5),
  e("flame_joined", "SN-1","CH-1",2),
  e("flame_sensor_received", "SN-1","CH-1",2.5),
  e("flame_model_sent", "CH-1","CH-2",2.6),
  e("flame_transfer_failed", "CH-1","CH-2",3.0,{packet_type:4,reason:"retry_exhausted"}),
  e("flame_connection_lost", "SN-1","CH-1",4,{reason:"timeout"}),
  e("flame_join_sent", "SN-1","CH-2",5),
  e("flame_joined", "SN-1","CH-2",5.5),
  e("flame_member_expired", "SN-1","CH-2",7),
];
const through = time => entries.reduce((state,row)=> {if(row.time_s<=time)tracker.apply(state,row); return state}, tracker.empty());
assert.equal(through(1.25).attempts.get("SN-1").ch,"CH-1");
assert.equal(through(1.75).attempts.get("SN-1").count,2);
assert.equal(through(2.1).associations.get("SN-1").ch,"CH-1");
assert.equal(through(2.7).associations.get("SN-1").confirmedAt,2.5);
assert.equal(through(3.5).peerDetails.get("CH-1|CH-2").action,"flame_transfer_failed");
assert.equal(through(4.5).lastExit.get("SN-1").reason,"timeout");
assert.equal(through(4.5).associations.has("SN-1"),false);
assert.equal(through(5.25).attempts.get("SN-1").ch,"CH-2");
assert.equal(through(6).associations.get("SN-1").ch,"CH-2");
assert.equal(through(8).associations.has("SN-1"),false);
assert.equal(tracker.historyFor(through(8),"SN-1")[0].action,"flame_member_expired");
assert.equal(through(1).associations.size,0); // rewind reconstruction
console.log("CLIVE node-connection history JS PASS");
