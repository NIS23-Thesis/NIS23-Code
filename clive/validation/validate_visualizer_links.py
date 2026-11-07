"""Regression probes for observed vs potential FLAME topology links.

No packet/protocol behavior is modified. All relationships are derived only from
recorded FLAME events; the visualization never infers membership from distance.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orchestrator import Orchestrator
from visualizer import RunData


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_relationship_reconstruction():
    with tempfile.TemporaryDirectory(prefix="clive-link-test-") as tmp:
        run = Path(tmp) / "run-test"
        run.mkdir()
        plan = Orchestrator().prepare({
            "num_ch": 2, "num_sn": 2, "duration": 2.0,
            "logging": {"protocol_events": True, "packet_events": True},
            "runtime": {"duration_s": 2.0, "startup_delay_s": 0.3},
            "mobility": {"model": "static", "tick_interval": 0.5},
        })
        plan.save(str(run / "plan.json"))
        addr = {node.name: list(node.address) for node in plan.nodes}
        write_jsonl(run / "debug/events/SN-1.jsonl", [
            {"type": "flame_joined", "node_name": "SN-1", "destination": addr["CH-1"], "time_s": 0.31},
            {"type": "flame_leave_sent", "node_name": "SN-1", "destination": addr["CH-1"], "time_s": 1.2},
        ])
        write_jsonl(run / "debug/events/CH-1.jsonl", [
            {"type": "flame_sensor_received", "node_name": "CH-1", "source": addr["SN-1"], "time_s": 0.72},
            {"type": "flame_model_sent", "node_name": "CH-1", "destination": addr["CH-2"], "time_s": 0.8},
        ])
        write_jsonl(run / "debug/events/CH-2.jsonl", [
            {"type": "flame_model_received", "node_name": "CH-2", "source": addr["CH-1"], "time_s": 1.1},
        ])
        write_jsonl(run / "debug/packets/CH-1.jsonl", [
            {"event": "scheduled_delivery", "node_name": "CH-1", "target": addr["SN-1"],
             "destination": addr["SN-1"], "common_header": {"packet_type": 0, "sequence_number": 9},
             "time_s": 0.15},
            {"event": "transmit", "node_name": "CH-1", "destination": ["255.255.255.255", 27062],
             "is_broadcast": True, "common_header": {"packet_type": 0}, "time_s": 0.2},
        ])
        view = RunData(run)
        rel = view.relationships()
        assert rel['available'] and len(rel['events']) == 5, rel
        assert [e['time_s'] for e in rel['events']] == sorted(e['time_s'] for e in rel['events'])
        assert [(e['source'], e['target'], e['action']) for e in rel['events']] == [
            ('SN-1', 'CH-1', 'flame_joined'),
            ('SN-1', 'CH-1', 'flame_sensor_received'),
            ('CH-1', 'CH-2', 'flame_model_sent'),
            ('CH-1', 'CH-2', 'flame_model_received'),
            ('SN-1', 'CH-1', 'flame_leave_sent'),
        ]
        events = view.events()['events']
        assert any(e['is_broadcast'] and e['target'] is None for e in events)
        assert any(e['event'] == 'scheduled_delivery' and e['target'] == 'SN-1' for e in events)
        # Subsequent refreshes cannot manufacture duplicate relationships.
        assert len(view.relationships()['events']) == 5
        # Existing on-disk protocol logs remain inspectable even if a setting
        # is later disabled: artifact presence, not only config, determines availability.
        plan.logging_config.protocol_events = False
        view._plan = plan
        assert view.relationships()['available'] is True
        # If neither protocol logging nor historical files exist, status is unknown.
        for path in (run / "debug/events").glob("*.jsonl"):
            path.unlink()
        blank = RunData(run)
        blank._plan = plan
        assert blank.relationships()['available'] is False
        print('Visualizer relationship provenance, sorting, directionality, and replay: PASS')


if __name__ == '__main__':
    test_relationship_reconstruction()
