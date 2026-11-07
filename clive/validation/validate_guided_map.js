'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../visualizer/static');
const map=require(path.join(root,'map_activity.js'));
const guide=require(path.join(root,'guided_playback.js'));
const events=[
 {time_s:0.010,action:'flame_hello_sent',source:'CH-1'},
 {time_s:0.020,action:'flame_ch_discovered',source:'CH-1',target:'SN-1'},
 {time_s:0.040,action:'flame_join_sent',source:'SN-1',target:'CH-1'},
 {time_s:0.044,action:'flame_joined',source:'SN-1',target:'CH-1'},
 {time_s:0.044,action:'flame_joined',source:'SN-2',target:'CH-2'},
 {time_s:0.055,action:'flame_sensor_received',source:'SN-1',target:'CH-1'}
];
const timeline=map.timeline(events);
assert.equal(timeline.length,6);
assert.equal(map.focus(events,.011).stage,'hello');
assert.equal(map.focus(events,.030).stage,'discovered');
assert.equal(map.focus(events,.041).stage,'request');
assert.equal(map.focus(events,.045).stage,'accepted');
assert.equal(map.focus(events,.039).time_s,.020,'Do not show future JOIN');
let st=guide.seek(timeline,0,1000);
st=guide.advance(st,1050,1,1,timeline); // target would be .05 but must stop at .01
assert.equal(st.time,.010);
assert.equal(st.focus.action,'flame_hello_sent');
assert.ok(st.holdUntil>=1800);
st=guide.advance(st,1300,1,1,timeline);
assert.equal(st.time,.010,'HELLO must remain visibly held');
st=guide.advance(st,1901,1,1,timeline); // hold expired; rebase
assert.equal(st.time,.010);
st=guide.advance(st,1920,1,1,timeline); // .019s elapsed > .010 to next
assert.equal(st.time,.020);
assert.equal(st.focus.action,'flame_ch_discovered');
st=guide.advance(st,2300,1,1,timeline);
assert.equal(st.time,.020,'JOIN must not overlap HELLO discovery hold');
let nxt=guide.next(st,timeline);
assert.equal(nxt.time,.040);
assert.equal(nxt.focus.action,'flame_join_sent');
nxt=guide.next(nxt,timeline);
assert.equal(nxt.time,.044);
assert.equal(nxt.focus.source,'SN-1');
nxt=guide.next(nxt,timeline);
assert.equal(nxt.time,.044,'Same-timestamp records are both inspectable without invented ordering');
assert.equal(nxt.focus.source,'SN-2');
const skipped=guide.seek(timeline,.040);
assert.equal(guide.next(skipped,timeline).time,.044);
assert.equal(map.fromEvent(events[1],.019),null,'No future event');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
assert.ok(html.includes('id="showLinkLabels"'));
assert.ok(html.includes('id="guidedPlayback"'));
assert.ok(html.includes('id="nextAction"'));
assert.ok(!html.includes('id="linkTooltip"')&&!html.includes('id="linkDetailCard"'));
assert.ok(!app.includes('showLinkHover(')&&!app.includes('setPinnedLink('));
console.log('PASS: guided stage playback holds, same-time steps, labels layer, legacy popover removal');
