"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../visualizer/static/event_stream.js'),'utf8');
const ev=vm.runInNewContext(source+'\nCliveEventStream;');
const id=n=>['HELLO','LEAVE','SENSOR_DATA','SENSOR_DATA_ACK','MODEL_UPDATE','MODEL_UPDATE_ACK','JOIN','JOIN_ACCEPT'][n] ?? `TYPE ${n}`;
const esc=x=>String(x);
function fixture(name, type) {
  return {stream:'protocol',event:name,time_s:1.123456,seq:'test-1',node:'CH-1',
    ...(type===undefined?{}:{packet_type:type}),details:{}};
}
for (const [type, family] of [[2,'SENSOR_DATA'],[4,'MODEL_UPDATE']]) {
  for (const [name,state] of [['flame_transfer_complete','ACK-complete'],['flame_transfer_failed','failed']]) {
    const record=fixture(name,type);
    assert.equal(ev.relatedPacket(record),family);
    assert.equal(ev.displayTitle(record,id),`${family} transfer ${state}`);
    assert.equal(ev.matchesFilter(record,'flame-action',{packet:family}),true);
    assert.equal(ev.matchesFilter(record,'flame-action',{packet:family==='SENSOR_DATA'?'MODEL_UPDATE':'SENSOR_DATA'}),false);
    const row=ev.render([record],{filter:'all',packetName:id,escapeHtml:esc});
    assert.match(row.html,new RegExp(`${family} transfer ${state}`));
    assert.match(row.html,new RegExp(`${family} \\(related\\)`));
  }
}
for (const name of ['flame_transfer_complete','flame_transfer_failed']) {
  for (const type of [undefined,99,-1]) {
    const record=fixture(name,type);
    assert.equal(ev.relatedPacket(record),null);
    assert.equal(ev.matchesFilter(record,'flame-action',{packet:'MODEL_UPDATE'}),false);
    assert.equal(ev.matchesFilter(record,'flame-action',{packet:'SENSOR_DATA'}),false);
    assert.match(ev.displayTitle(record,id),type===undefined ? /type unrecorded/ : /unknown type/);
  }
}
const sample=fixture('flame_sample_collected');
assert.equal(ev.category(sample),'clive');
assert.match(ev.explanation(sample)[1],/SensingModule\.collect_once/);
assert.equal(ev.isFailure(fixture('flame_radio_backpressure_drop')),true);
assert.equal(ev.matchesFilter(fixture('flame_radio_backpressure_drop'),'drop'),true);
assert.equal(ev.isFailure(fixture('flame_model_rejected_or_duplicate')),true);
assert.equal(ev.isFailure({...fixture('flame_model_rejected_or_duplicate'),details:{rejection_kind:'duplicate'}}),false);
const packet={stream:'packet',event:'receive',packet_type:2,time_s:1,seq:'p1',node:'CH-1'};
assert.equal(ev.category(packet),'flame-packet');
assert.equal(ev.displayTitle(packet,id),'SENSOR_DATA · RX');
const action=fixture('flame_hello_sent');
assert.equal(ev.category(action),'flame-action');
assert.equal(ev.relatedPacket(action),'HELLO');
assert.equal(ev.matchesFilter(action,'flame-packet'),false);
console.log('PASS: transfer packet family, classification, provenance, drops and packet evidence');
