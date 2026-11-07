'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root=path.resolve(__dirname,'../visualizer/static');
const map=require(path.join(root,'map_activity.js'));
const events=[
 {action:'flame_hello_sent',time_s:1,source:'CH-1'},
 {action:'flame_ch_discovered',time_s:1.12,source:'CH-1',target:'SN-1'},
 {action:'flame_join_sent',time_s:2.1,source:'SN-1',target:'CH-1'},
 {action:'flame_joined',time_s:3.12,source:'SN-1',target:'CH-1'},
 {action:'flame_sensor_received',time_s:3.6,source:'SN-1',target:'CH-1'},
 {action:'flame_model_sent',time_s:5.2,source:'CH-1',target:'CH-2',packet_type:4},
 {action:'flame_leave_sent',time_s:10.1,source:'SN-1',target:'CH-1'}
];
assert.equal(map.focus(events,.99),null,'No future event may be shown');
assert.equal(map.focus(events,1.01).stage,'hello');
assert.match(map.focus(events,1.01).description,/NOT a connection/);
assert.equal(map.focus(events,1.12).stage,'discovered');
assert.match(map.focus(events,1.12).description,/not joined/);
assert.equal(map.focus(events,2.1).stage,'request');
assert.match(map.focus(events,2.1).description,/not yet been observed/);
assert.equal(map.focus(events,2.1).time_s,2.1,'No invented event delay');
assert.equal(map.focus(events,3.12).stage,'accepted');
assert.equal(map.focus(events,3.6).stage,'confirmed');
assert.equal(map.focus(events,5.2).stage,'model');
assert.equal(map.focus(events,10.1).stage,'exit');
assert.equal(map.focus(events,13),null,'No stale markers');
assert.equal(map.recent(events,2.11).length,1,'Only one marker is ever displayed');
const crowded=[
 {action:'flame_hello_sent',time_s:1.0,source:'CH-1'},
 {action:'flame_join_sent',time_s:1.006,source:'SN-1',target:'CH-1'},
 {action:'flame_joined',time_s:1.007,source:'SN-2',target:'CH-2'},
];
assert.equal(map.focus(crowded,1.01).stage,'accepted','Show latest actually recorded event, without fictitious spacing');
assert.equal(map.focus(crowded,1.01).time_s,1.007,'Priority preserves actual timestamp');
assert.equal(map.focus(crowded,1.01,'SN-1').stage,'request','Selected node focuses its own activity');
assert.equal(map.focus(crowded,1.01,'SN-4').stage,'accepted','Unknown selection cannot hide all activity');
assert.equal(map.focus([{action:'flame_transfer_failed',time_s:1,source:'CH-1',target:'CH-2',packet_type:2}],1.5),null,'Sensor transfer should not masquerade as failed model');
assert.equal(map.focus([],1),null);
assert.ok(fs.readFileSync(path.join(root,'app.js'),'utf8').includes('CliveMapActivity.focus('));
assert.ok(fs.readFileSync(path.join(root,'index.html'),'utf8').includes('id="mapActivityStatus"'));
console.log('PASS: one focused HELLO/JOIN/accept state, exact chronology, selected node, expiry');
