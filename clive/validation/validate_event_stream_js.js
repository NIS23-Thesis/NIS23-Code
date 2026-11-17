"use strict";
// Pure rendering checks. Does not require CLIVE processes or a browser.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../visualizer/static/event_stream.js"), "utf8");
const events = vm.runInNewContext(source + "\nCliveEventStream;");
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char =>
  ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const packetName = type => ({0:"HELLO", 2:"SENSOR_DATA", 4:"MODEL_UPDATE"}[type] || "—");

const packet = {seq: 2, event:"receive", stream:"packet", node:"CH-1", source:"SN-1",
  target:"CH-1", packet_type:2, sequence_number:42, time_s:2.0, details:{"raw":"<img src=x>"}};
const training = {seq:3, event:"flame_trained", stream:"protocol", node:"CH-1",
  packet_type:null, sequence_number:null, trigger_sequence_number:42, time_s:3.0,
  details:{note:"<script>"}};
const drop = {seq:1, event:"delivery_drop", stream:"packet", node:"CH-1",
  time_s:1.0, reason:"out of range", details:{}};
const data = [drop, packet, training]; // ascending by time (API contract)
assert.equal(events.category(drop), "network");
assert.equal(events.category(packet), "flame-packet");
assert.equal(events.category(training), "flame-internal");
assert.equal(events.category({event:"attempt",stream:"packet"}), "network");
assert.equal(events.category({event:"transmit",stream:"packet"}), "network");
assert.equal(events.category({event:"transmit",stream:"packet",packet_type:0}), "flame-packet");
assert.equal(events.category({event:"flame_hello_sent",stream:"protocol"}), "flame-action");
assert.match(events.packetLabel(packet, packetName), /seq #42/);
assert.match(events.packetLabel(training, packetName), /trigger seq #42/);
const all = events.render(data, {filter:"all", packetName, escapeHtml});
assert.match(all.summary, /3 rows \/ 3 records/);
assert.ok(all.html.indexOf("Local training executed") < all.html.indexOf("SENSOR_DATA · RX"));
assert.ok(!all.html.includes("<script>"));
assert.ok(!all.html.includes("<img src=x>"));
const found = events.render(data, {filter:"all", search:"42", packetName, escapeHtml});
assert.match(found.summary, /2 rows \/ 2 records/);
const drops = events.render(data, {filter:"drop", packetName, escapeHtml});
assert.match(drops.summary, /1 rows \/ 1 records/);
console.log("Event Stream JS presentation validation PASS");
