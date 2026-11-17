"use strict";
const assert=require("node:assert/strict"), fs=require("node:fs"), path=require("node:path"), vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../visualizer/static/event_stream.js"),"utf8");
const events=vm.runInNewContext(source+"\nCliveEventStream;");
const records={
  started:["protocol","clive"],flame_started:["protocol","clive"],
  flame_sample_collected:["protocol","clive"],flame_model_retry_scheduled:["protocol","clive"],
  flame_fl_error:["protocol","clive"],flame_invalid_mnist_sample_rejected:["protocol","clive"],
  flame_joined:["protocol","flame-action"],flame_trained:["protocol","flame-internal"],
  flame_sensor_received:["connection","flame-action"],flame_transfer_failed:["protocol","flame-action"],
  transmit:["packet","network"],receive:["packet","network"],delivery_drop:["packet","network"],
  attempt:["packet","network"],
};
for(const [name,[stream,category]] of Object.entries(records)){
  const event={event:name,stream};
  assert.equal(events.category(event),category,name);
  const wantLabel=category==="flame-packet"?"FLAME · PACKET":category==="flame-action"?"FLAME · ACTION":category==="flame-internal"?"FLAME · INTERNAL":category==="clive"?"CLIVE":"NETWORK";
  assert.equal(events.groupLabel(event),wantLabel,name);
}
assert.ok(events.matchesFilter({event:"started",stream:"protocol"},"clive"));
assert.ok(!events.matchesFilter({event:"started",stream:"protocol"},"flame"));
assert.ok(!events.matchesFilter({event:"flame_started",stream:"protocol"},"flame"));
assert.ok(events.matchesFilter({event:"flame_joined",stream:"protocol"},"flame-action"));
assert.ok(!events.matchesFilter({event:"flame_joined",stream:"protocol"},"flame-packet"));
assert.ok(events.matchesFilter({event:"transmit",stream:"packet",packet_type:0},"flame-packet"));
assert.ok(events.matchesFilter({event:"flame_trained",stream:"protocol"},"flame-internal"));
assert.ok(events.matchesFilter({event:"flame_transfer_failed",stream:"protocol"},"drop"));
assert.ok(events.matchesFilter({event:"delivery_drop",stream:"packet"},"drop"));
assert.equal(events.isFailure({event:"flame_model_rejected_or_duplicate",stream:"protocol",details:{rejection_kind:"duplicate"}}),false);
assert.equal(events.isFailure({event:"flame_model_rejected_or_duplicate",stream:"protocol",details:{rejection_kind:"invalid"}}),true);
const e={event:"flame_transfer_failed",stream:"protocol",time_s:1,node:"CH-1",details:{},seq:"x1"};
const html=events.render([e],{filter:"all",packetName:()=>"—",escapeHtml:String}).html;
assert.match(html,/event-flame-action is-failure/);
assert.match(html,/FLAME · ACTION/);
assert.doesNotMatch(html,/event-drop/);
console.log("Event ownership and failure semantics JS PASS");
