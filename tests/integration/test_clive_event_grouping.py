"""Event ownership, outcome, and API-filter regressions. No FLAME edits needed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "clive"))
from visualizer import RunData


def record(name, stream, time_s):
    return {"event": name, "stream": stream, "time_s": time_s}


def test_grouping_distinguishes_clive_adapter_and_flame_behaviors():
    expected = {
        "started": "clive",
        "flame_started": "clive",
        "flame_sample_collected": "clive",
        "flame_model_retry_scheduled": "clive",
        "flame_model_retry_exhausted": "clive",
        "flame_invalid_mnist_sample_rejected": "clive",
        "flame_model_rejected_revision_limit": "clive",
        "flame_fl_error": "clive",
        "flame_ch_discovered": "flame-action",
        "flame_join_sent": "flame-action",
        "flame_joined": "flame-action",
        "flame_sensor_received": "flame-action",
        "flame_trained": "flame-internal",
        "flame_model_received": "flame-action",
        "flame_aggregated": "flame-internal",
        "flame_transfer_failed": "flame-action",  # not a CLIVE radio drop
    }
    for name, category in expected.items():
        assert RunData._preview_group(record(name, "protocol", 1)) == category, name
    for name, category in {
        "transmit": "network", "receive": "network", "delivery_drop": "network",
        "local_drop": "network", "socket_error": "network", "attempt": "network",
    }.items():
        assert RunData._preview_group(record(name, "packet", 1)) == category


def test_failure_is_separate_from_source():
    for event_name, expected_category in [
        ("flame_transfer_failed", "flame-action"),
        ("flame_fl_error", "clive"),
        ("flame_unassociated_sensor_rejected", "flame-action"),
    ]:
        event = record(event_name, "protocol", 1)
        assert RunData._preview_group(event) == expected_category
        assert RunData._is_failure_event(event)
    assert not RunData._is_failure_event(record("flame_joined", "protocol", 1))
    assert not RunData._is_failure_event({**record("flame_model_rejected_or_duplicate", "protocol", 1),
                                          "details": {"rejection_kind": "duplicate"}})
    assert RunData._is_failure_event({**record("flame_model_rejected_or_duplicate", "protocol", 1),
                                      "details": {"rejection_kind": "invalid"}})
    assert not RunData._panel_activity(record("started", "protocol", 1))
    assert not RunData._panel_activity(record("flame_started", "protocol", 1))
    assert RunData._panel_activity(record("flame_trained", "protocol", 1))
    # Decoded HELLO TX/RX are actual FLAME packet evidence, not protocol actions.
    # This must agree between the preview and archive and with event_stream.js.
    hello_tx = {**record("transmit", "packet", 1), "packet_type": 0}
    hello_rx = {**record("receive", "packet", 2), "packet_type": 0}
    assert RunData._preview_group(hello_tx) == "flame-packet"
    assert RunData._preview_group(hello_rx) == "flame-packet"
    for packet_event in (hello_tx, hello_rx):
        assert not RunData._panel_activity(packet_event)
        assert not RunData._matches_event_filters(packet_event, "activity")
        assert RunData._matches_event_filters(packet_event, "flame-packet")


def test_preview_and_archive_filters_match(tmp_path):
    names = ["started", "flame_started", "flame_sample_collected", "flame_joined",
             "flame_trained", "flame_model_retry_scheduled", "flame_fl_error",
             "flame_unassociated_sensor_rejected"]
    folder = tmp_path / "debug" / "events"
    folder.mkdir(parents=True)
    with (folder / "CH-1.jsonl").open("w") as fp:
        for idx, name in enumerate(names):
            fp.write(json.dumps({"type": name, "time_s": round(idx * 0.1 + .1, 3)}) + "\n")
    packet_folder = tmp_path / "debug" / "packets"
    packet_folder.mkdir(parents=True)
    with (packet_folder / "CH-1.radio.jsonl").open("w") as fp:
        for idx, name in enumerate(["transmit", "receive", "delivery_drop"]):
            fp.write(json.dumps({"event": name, "time_s": round(idx * .1 + 1., 3)}) + "\n")
    run = RunData(tmp_path)
    expected = {"all": 11, "clive": 5, "activity": 3, "protocol": 3,
                "network": 3, "packets": 1, "drop": 3}
    for category, count in expected.items():
        window = run.events(from_s=0, to_s=3, limit=100, category=category)
        archive = run.event_archive(from_s=0, to_s=3, limit=100, category=category)
        assert window["matching_count"] == count, (category, "window", window)
        assert len(archive["events"]) == count, (category, "archive", archive)
        assert [e["event"] for e in window["events"]] == [e["event"] for e in archive["events"]]
    assert {e["event"] for e in run.event_archive(category="clive")["events"]} == {
        "started", "flame_started", "flame_sample_collected", "flame_model_retry_scheduled", "flame_fl_error"
    }


def test_live_after_seq_query_uses_same_origin_filters(tmp_path):
    run = RunData(tmp_path)
    run._refresh_events = lambda: None
    events = [
        {"seq": 1, **record("started", "protocol", .01)},
        {"seq": 2, **record("flame_joined", "protocol", .2)},
        {"seq": 3, **record("flame_fl_error", "protocol", .3)},
        {"seq": 4, **record("transmit", "packet", .4)},
    ]
    run._events.extend(events)
    assert [x["event"] for x in run.events(category="protocol")["events"]] == ["flame_joined"]
    assert [x["event"] for x in run.events(category="clive")["events"]] == ["started", "flame_fl_error"]
    assert [x["event"] for x in run.events(category="clive", after_seq=1)["events"]] == ["flame_fl_error"]
    assert [x["event"] for x in run.events(category="drop")["events"]] == ["flame_fl_error"]
