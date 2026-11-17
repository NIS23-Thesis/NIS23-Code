'use strict';
// Regression for the current presentation model: exact event-by-event playback.
// The old grouped-moment API was retired because it obscured packet/action order.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../visualizer/static');
const map=require(path.join(root,'map_activity.js'));
const guided=require(path.join(root,'guided_playback.js'));
const events=[
 {action:'flame_hello_sent',time_s:0.010001,source:'CH-1'},
 {action:'flame_ch_discovered',time_s:0.010400,source:'CH-1',target:'SN-1'},
 {action:'flame_join_sent',time_s:0.010850,source:'SN-1',target:'CH-1'},
 {action:'flame_joined',time_s:0.012001,source:'SN-1',target:'CH-1'},
 {action:'flame_trained',time_s:0.050001,source:'CH-2'}
];
const timeline=map.timeline(events);
assert.equal(timeline.length,5);
assert.deepEqual(timeline,events);
assert.equal(map.focus(timeline,.010000),null,'future HELLO must not be shown');
assert.equal(map.focus(timeline,.010200).stage,'hello');
assert.equal(map.focus(timeline,.010500).stage,'discovered');
assert.equal(map.focus(timeline,.011000).stage,'request');
assert.equal(map.focus(timeline,.012100).stage,'accepted');
let st=guided.create();
for(let i=0;i<events.length;i++) {
 st=guided.next(st,timeline);
 assert.equal(st.time,events[i].time_s);
 assert.equal(st.cursor,i);
 assert.deepEqual(st.focus,events[i]);
}
assert.equal(guided.next(st,timeline).cursor,4);
assert.equal(guided.seek(timeline,.010100).cursor,0);
assert.equal(guided.seek(timeline,.010700).cursor,1);
console.log('PASS: event-by-event playback preserves exact chronology without future leaks');
