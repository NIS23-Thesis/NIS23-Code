"""CLIVE integration-only regression checks. Does not patch or import-edit flame/."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emulator import Emulator
from flame_protocol import FlameProtocol, _MNISTSource, _configuration
from flame.communication.message import PacketType
from visualizer import RunData


class AdapterAndVisualizerTests(unittest.TestCase):
    def test_events_preserve_protocol_packet_numbers(self):
        run = object.__new__(RunData)
        run._address_to_name = {("127.0.0.3", 27062): "SN-1"}
        run._next_seq = 7
        e = run._normalize_event({
            "type": "flame_sensor_received", "time_s": 3.0,
            "sequence_number": 42, "packet_type": 2,
            "source": ["127.0.0.3", 27062], "message_id": 9,
        }, "CH-1.jsonl", "protocol")
        self.assertEqual(e["seq"], 7)  # Event ID, not wire sequence
        self.assertEqual(e["sequence_number"], 42)
        self.assertEqual(e["message_id"], 9)
        self.assertEqual((e["source"], e["target"]), ("SN-1", "CH-1"))

    def test_packet_common_header_and_protocol_trigger_differ(self):
        run = object.__new__(RunData)
        run._address_to_name = {}
        run._next_seq = 1
        datagram = run._normalize_event({"event": "receive", "time_s": 1,
            "common_header": {"packet_type": 3, "sequence_number": 65535}}, "CH-1.jsonl", "packet")
        train = run._normalize_event({"type": "flame_trained", "time_s": 2,
            "trigger_sequence_number": 42, "update_id": 13}, "CH-1.jsonl", "protocol")
        self.assertEqual(datagram["packet_type"], 3)
        self.assertEqual(datagram["sequence_number"], 65535)
        self.assertIsNone(train["sequence_number"])
        self.assertEqual(train["trigger_sequence_number"], 42)

    def test_mnist_samples_no_repeat_without_explicit_opt_in(self):
        source = _MNISTSource("SN-1", 810, {"sample_source":"synthetic", "mnist_samples_per_sensor":2})
        self.assertTrue(source.has_next())
        source.collect(); source.collect()
        self.assertFalse(source.has_next())
        self.assertTrue(source.has_next(repeat=True))

    def test_settings_validate_adapter_retry(self):
        plan = SimpleNamespace(flame_settings={"model_retry_limit": -1})
        with self.assertRaises(ValueError):
            _configuration(plan)
        valid = _configuration(SimpleNamespace(flame_settings={"model_retry_limit": 3}))
        self.assertFalse(valid["repeat_sensor_samples"])

    def test_transfer_failures_retry_bounded_without_fiddling_flame(self):
        p = object.__new__(FlameProtocol)
        p.is_ch = True
        p.stats = Counter()
        p._sent_updates = {(('127.0.0.2', 27062), 7)}
        p._model_retry_after = {}
        p._model_retry_counts = Counter()
        p._model_transfer_status = {(('127.0.0.2', 27062), 7): "pending"}
        p.s = {"model_retry_limit": 2, "model_retry_backoff_s": 2.0}
        p.node = SimpleNamespace(events=[], elapsed=lambda: 8.0)
        ev = {"type":"transfer_failed", "packet_type":PacketType.MODEL_UPDATE,
              "destination":('127.0.0.2',27062), "message_id":7,"sequence_number":10,"reason":"retry_exhausted"}
        p._on_comm_event(ev)
        self.assertNotIn((('127.0.0.2', 27062),7),p._sent_updates)
        self.assertEqual(p._model_retry_after[(('127.0.0.2',27062),7)],10.0)
        p._on_comm_event(ev)
        p._on_comm_event(ev)
        self.assertEqual(p._model_transfer_status[(('127.0.0.2',27062),7)],'retry_exhausted')
        self.assertEqual(p.stats['model_retry_scheduled'], 2)

    def test_run_metrics_do_not_conflate_worker_exit_with_training(self):
        with tempfile.TemporaryDirectory() as td:
            flame = Path(td) / 'flame'; flame.mkdir()
            (flame/'CH-1.json').write_text(json.dumps({
                'role':'CH', 'workload':{'trainer':'mean_pixel', 'real_neural_network_training':False},
                'counters':{'trained':1}, 'model_transfer_summary':{
                    'started_versions':3, 'completed_versions':1, 'failed_versions':1,
                    'unfinished_versions':1}}))
            em = object.__new__(Emulator); em.run_dir = td
            result = em._flame_outcome()
            self.assertFalse(result['real_neural_network_training'])
            self.assertFalse(result['model_delivery_success'])
            self.assertEqual(result['model_transfers_unfinished'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
