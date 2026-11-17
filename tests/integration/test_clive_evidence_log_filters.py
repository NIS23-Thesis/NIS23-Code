"""Regression: wire packet evidence, adapter outcomes, chronology and filters.

All changes remain in CLIVE; the team-owned FLAME package is not modified.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "clive"))
from visualizer import RunData  # noqa: E402


def _record(event, seconds, *, packet=None, node="CH-1", source=None):
    data = {"event": event, "node_name": node, "time_s": seconds}
    if packet is not None:
        data["common_header"] = {"packet_type": packet, "sequence_number": 42}
    if source is not None:
        data["source"] = source
    return data


@pytest.fixture
def sample_run(tmp_path):
    base = tmp_path / "debug"
    (base / "events").mkdir(parents=True)
    (base / "packets").mkdir(parents=True)
    # Each node file is chronological; global order requires a k-way merge.
    cases = {
        "packets/CH-1.radio.jsonl": [
            _record("transmit", 0.100000, packet=0),
            _record("transmit", 0.220000, packet=7),
            _record("transmit", 0.400000, packet=4),
            _record("delivery_drop", 0.600000, packet=4),
        ],
        "packets/SN-1.node.jsonl": [
            _record("receive", 0.101000, packet=0, node="SN-1"),
            _record("transmit", 0.150000, packet=6, node="SN-1"),
            _record("receive", 0.220001, packet=7, node="SN-1"),
        ],
        "events/SN-1.jsonl": [
            {"type": "flame_ch_discovered", "time_s": 0.110000, "node_name": "SN-1"},
            {"type": "flame_join_sent", "time_s": 0.140000, "node_name": "SN-1"},
            {"type": "flame_joined", "time_s": 0.230000, "node_name": "SN-1"},
            {"type": "flame_started", "time_s": 0.600000, "node_name": "SN-1"},
        ],
        "events/CH-1.jsonl": [
            {"type": "started", "time_s": 0.020000, "node_name": "CH-1"},
            {"type": "flame_hello_sent", "time_s": 0.090000, "node_name": "CH-1"},
            {"type": "flame_trained", "time_s": 0.500000, "node_name": "CH-1"},
            {"type": "flame_transfer_failed", "time_s": 0.600000, "node_name": "CH-1"},
        ],
    }
    for relative, data in cases.items():
        (base / relative).write_text("".join(json.dumps(x) + "\n" for x in data))
    return RunData(tmp_path)


def test_wire_packet_vs_actions(sample_run):
    records = sample_run.event_archive(category="flame-packet", order="asc")["events"]
    assert [e["event"] for e in records] == [
        "transmit", "receive", "transmit", "transmit", "receive", "transmit"]
    assert all(e["stream"] == "packet" for e in records)
    assert all(e["packet_type"] in range(8) for e in records)
    actions = sample_run.event_archive(category="flame-action")["events"]
    assert {e["event"] for e in actions} == {
        "flame_hello_sent", "flame_ch_discovered", "flame_join_sent",
        "flame_joined", "flame_transfer_failed"}
    assert sample_run.event_archive(category="flame-internal")["events"][0]["event"] == "flame_trained"
    assert {e["event"] for e in sample_run.event_archive(category="clive")["events"]} == {
        "flame_started", "started"}
    # Network TX/RX inspection intentionally overlaps decoded FLAME Packet evidence.
    assert len(sample_run.event_archive(category="network")["events"]) == 7


def test_archive_sort_pages_are_globally_consistent(sample_run):
    asc = sample_run.event_archive(category="all", limit=100, order="asc")["events"]
    desc = sample_run.event_archive(category="all", limit=100, order="desc")["events"]
    # All 15 records, including equal timestamps across streams, are reversible.
    assert len(asc) == 15
    assert [(e["time_s"], e["event"]) for e in desc] == [
        (e["time_s"], e["event"]) for e in reversed(asc)]
    for order, expected in (("asc", asc), ("desc", desc)):
        page1 = sample_run.event_archive(category="all", order=order, limit=4)
        page2 = sample_run.event_archive(category="all", order=order, limit=4, offset=4)
        page3 = sample_run.event_archive(category="all", order=order, limit=10, offset=8)
        assert page1["has_more"] and page2["has_more"] and not page3["has_more"]
        actual = page1["events"] + page2["events"] + page3["events"]
        assert [e["archive_id"] for e in actual] == [e["archive_id"] for e in expected]
        assert [e["event"] for e in actual] == [e["event"] for e in expected]


def test_filters_apply_before_pagination_and_consistently_in_preview(sample_run):
    for order in ("asc", "desc"):
        criteria = dict(category="flame", node="SN-1", packet="HELLO", order=order)
        archive = sample_run.event_archive(limit=100, **criteria)["events"]
        preview = sample_run.events(from_s=0, to_s=1, limit=100, **criteria)["events"]
        assert [(e["event"], e["time_s"]) for e in archive] == [
            (e["event"], e["time_s"]) for e in preview]
        assert [e["event"] for e in preview] == (
            ["receive", "flame_ch_discovered"] if order == "asc"
            else ["flame_ch_discovered", "receive"])
    # Searching for packet families includes related actions only when selected.
    assert {e["event"] for e in sample_run.event_archive(packet="HELLO")["events"]} == {
        "transmit", "receive", "flame_ch_discovered", "flame_hello_sent"}
    failures = sample_run.event_archive(outcome="failure")["events"]
    assert {e["event"] for e in failures} == {"delivery_drop", "flame_transfer_failed"}
    assert len(sample_run.event_archive(category="all", search="flame_trained")["events"]) == 1
    # A 2-record limit must choose the globally newest (not the first file).
    two = sample_run.events(from_s=0, to_s=1, order="desc", category="all", limit=2)["events"]
    assert len(two) == 2 and all(e["time_s"] == 0.6 for e in two)


def test_filter_validation(sample_run):
    with pytest.raises(ValueError):
        sample_run.event_archive(order="banana")
    with pytest.raises(ValueError):
        sample_run.event_archive(packet="NOT_A_PACKET")
    with pytest.raises(ValueError):
        sample_run.events(from_s=0, to_s=1, order="unordered")
