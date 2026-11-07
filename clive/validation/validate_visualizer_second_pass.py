"""Second-pass CLIVE event archive regressions; does not import or modify FLAME."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator import Orchestrator
from visualizer import RunData


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="clive-second-pass-") as folder:
        run_dir = Path(folder) / "run-second-pass"
        run_dir.mkdir()
        plan = Orchestrator().prepare({
            "num_ch": 2, "num_sn": 1, "duration": 10.0, "seed": 810,
            "startup_delay_s": 0.2,
            "mobility": {"tick_interval": 0.5, "area_width_m": 100.0,
                         "area_height_m": 100.0},
        })
        plan.save(str(run_dir / "plan.json"))
        (run_dir / "run-manifest.json").write_text(json.dumps({"status": "complete"}))
        packets_dir = run_dir / "debug" / "packets"
        protocol_dir = run_dir / "debug" / "events"
        packets_dir.mkdir(parents=True)
        protocol_dir.mkdir(parents=True)
        def record(typ: str, time_s: float, *, packet_type=None, sequence_number=None):
            data = {"event": typ, "node_name": "CH-1", "time_s": time_s}
            if packet_type is not None:
                data["common_header"] = {"packet_type": packet_type,
                                         "sequence_number": sequence_number}
            return json.dumps(data)
        (packets_dir / "CH-1.jsonl").write_text("\n".join([
            record("transmit", 1.0, packet_type=4, sequence_number=42),
            record("local_drop", 2.0, packet_type=4, sequence_number=43),
            record("transmit", 8.0, packet_type=2, sequence_number=44),
        ]) + "\n")
        (protocol_dir / "CH-1.jsonl").write_text("\n".join([
            record("flame_hello_sent", 0.5),
            record("flame_join_sent", 1.5),
            record("flame_sensor_received", 1.6),
            record("flame_unassociated_sensor_rejected", 1.7),
            record("flame_trained", 8.5),
        ]) + "\n")
        rd = RunData(run_dir)
        packet = rd.event_archive(category="packet", search="MODEL_UPDATE", limit=50)
        assert len(packet['events']) == 2, packet
        assert [ev['sequence_number'] for ev in packet['events']] == [42, 43]
        assert not rd.event_archive(category="packet", search="MODEL_UPDATE_ACK")['events']
        assert len(rd.event_archive(category="packet", search="SENSOR_DATA")['events']) == 1
        lost = rd.event_archive(category="drop", limit=50)
        assert {ev['event'] for ev in lost['events']} == {
            "local_drop", "flame_unassociated_sensor_rejected"}, lost
        key = rd.event_archive(category="key", limit=50)
        key_names = {ev['event'] for ev in key['events']}
        assert "flame_join_sent" in key_names
        assert "flame_sensor_received" in key_names
        assert "flame_hello_sent" not in key_names
        in_window = rd.event_archive(category="all", from_s=0, to_s=2.0, limit=50)
        assert len(in_window['events']) == 6, in_window
        assert max(ev['time_s'] for ev in in_window['events']) == 2.0
        preview = rd.events(from_s=0, to_s=2, limit=50, category="key")
        assert {ev['event'] for ev in preview['events']} == key_names - {"flame_trained"}, preview
        print("Second-pass CLIVE archive tests PASS: packet-name search, aligned key/drop filters, bounded history")


if __name__ == "__main__":
    main()
