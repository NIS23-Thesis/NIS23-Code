"""Regression tests for the review findings; stdlib unittest, no dependencies."""
import json
import math
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from emulator import Emulator
from nodebootstrapper import Node, Protocol, TimingOverrunError
from nodesocket import NodeSocket, TokenBucket, BROADCAST_ADDRESS, link_conditions
from orchestrator import (Orchestrator, LinkConfig, MobilityConfig, EmulationConfig,
                          EmulationPlan, build_nodes)


class CrashProtocol(Protocol):
    def step(self, tick):
        raise RuntimeError("intentional worker failure")


class HangProtocol(Protocol):
    def step(self, tick):
        time.sleep(10)


class SilentProtocol(Protocol):
    pass


def plan_for_test(nodes=2, duration=2, **scenario):
    plan = Orchestrator().prepare(dict(num_ch=nodes, num_sn=0, duration=duration, **scenario))
    for index, node in enumerate(plan.nodes):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
            reservation.bind((node.underlay_ip, 0))
            node.underlay_port = reservation.getsockname()[1]
        for positions in plan.trace.values():
            positions[node.name] = [float(index), 0.0]
    plan.epoch_start = time.time()-.001
    plan.validate()
    return plan


def eventually(predicate, timeout=1):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.001)
    raise AssertionError("condition did not become true")


class ValidationTests(unittest.TestCase):
    def test_nonfinite_configs_rejected(self):
        for cfg in (LinkConfig(rssi0_dbm=math.nan), LinkConfig(max_data_rate_bps=math.inf),
                    MobilityConfig(area_width_m=math.nan), EmulationConfig(duration_s=math.nan)):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                cfg.validate()

    def test_node_name_cannot_escape_directory(self):
        for name in ("../outside", "/tmp/outside", "a/b", "..", "CON"):
            p = plan_for_test(1)
            p.nodes[0].name = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                p.validate()

    def test_invalid_ports_and_ip_rejected(self):
        for port in (0, 65536, 1.2, True):
            p = plan_for_test(1)
            p.nodes[0].underlay_port = port
            with self.assertRaises(ValueError):
                p.validate()
        p.nodes[0].underlay_port = 27062
        p.nodes[0].underlay_ip = "127.0.0.256"
        with self.assertRaises(ValueError):
            p.validate()

    def test_coordinates_validated(self):
        for position in ([], [1], [1, math.nan], [-1, 2], [201, 1], [1, 2, 3]):
            p = plan_for_test(1)
            p.trace[0][p.nodes[0].name] = position
            with self.subTest(position=position), self.assertRaises(ValueError):
                p.validate()

    def test_address_builder_crosses_octet_boundary(self):
        nodes = build_nodes(300, 0)
        self.assertEqual(nodes[254].underlay_ip, "127.0.1.0")
        self.assertEqual(len({n.underlay_ip for n in nodes}), 300)

    def test_no_fractional_or_boolean_node_count(self):
        for count in (True, 1.5):
            with self.assertRaises(ValueError):
                build_nodes(count, 0)

    def test_trace_interchange_and_json_roundtrip(self):
        p = plan_for_test()
        with tempfile.TemporaryDirectory() as root:
            p.save(Path(root)/"plan.json")
            p.save_trace(Path(root)/"mobility.tsv")
            self.assertEqual(EmulationPlan.load(Path(root)/"plan.json").to_dict(), p.to_dict())
            rows = (Path(root)/"mobility.tsv").read_text().splitlines()
            self.assertEqual(len(rows), len(p.trace)+1)
            self.assertEqual(rows[0], "time_s\tCH-1_x\tCH-1_y\tCH-2_x\tCH-2_y")

    def test_stationary_trace_does_not_move(self):
        p = Orchestrator().prepare(dict(duration=1))
        self.assertTrue(all(position == p.trace[0] for position in p.trace.values()))

    def test_seeded_trace_reproducible(self):
        scenario = dict(duration=1, seed=123, mobility=dict(ch_mean_speed_mps=4))
        self.assertEqual(Orchestrator().prepare(scenario).trace, Orchestrator().prepare(scenario).trace)

    def test_periodic_pause_requires_explicit_movement_duration(self):
        with self.assertRaises(ValueError):
            MobilityConfig(pause_trigger="periodic", pause_time_range=(1, 5)).validate()


class RateTests(unittest.TestCase):
    def test_bucket_accumulates_bounded_idle_credit(self):
        b = TokenBucket(1000, now=0)
        self.assertAlmostEqual(b.wait_time(1000, 1000, now=0), 1)
        self.assertTrue(b.consume(1000, 1000, now=10))
        self.assertEqual(b.tokens, 0)
        self.assertAlmostEqual(b.wait_time(1000, 1000, now=10), 1)

    def test_bucket_limits_many_packets(self):
        b = TokenBucket(100, now=0)
        self.assertTrue(b.consume(100, 100, now=1))
        self.assertFalse(b.consume(100, 100, now=1))
        self.assertTrue(b.consume(100, 100, now=2))

    def test_rate_change_does_not_reprice_past_credit(self):
        b = TokenBucket(10000, now=0)
        b.wait_time(1000, 100, now=0)
        self.assertAlmostEqual(b.wait_time(1000, 1000, now=1), .9)

    def test_rssi_bands(self):
        cfg = LinkConfig()
        for distance, fraction, loss in ((1,1,0), (20,.44,.02), (40,.2,.1), (70,0,1), (100,0,1)):
            c = link_conditions(distance, cfg)
            self.assertEqual(c["bandwidth_bps"], cfg.max_data_rate_bps*fraction)
            self.assertEqual(c["plr"], loss)


class SocketTests(unittest.TestCase):
    def setUp(self):
        self.sockets = []
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        for s in self.sockets:
            s.close()
        self.tmp.cleanup()

    def endpoint(self, plan, index):
        s = NodeSocket(plan.nodes[index], plan, log_path=Path(self.tmp.name)/f"{len(self.sockets)}.jsonl")
        self.sockets.append(s)
        return s

    def test_broadcast_is_one_logical_transmission(self):
        p = plan_for_test(3)
        a, b, c = [self.endpoint(p, i) for i in range(3)]
        a.sendto(b"1234", BROADCAST_ADDRESS)
        self.assertEqual(eventually(b.recvfrom)[0], b"1234")
        self.assertEqual(eventually(c.recvfrom)[0], b"1234")
        m = a.metrics.snapshot()
        self.assertEqual(m["packets_sent"], 1)
        self.assertEqual(m["bits_sent"], 32)
        self.assertEqual(m["delivery_attempts"], 2)

    def test_broadcast_respects_destination_port(self):
        p = plan_for_test(3)
        p.nodes[2].overlay_port += 1
        a, b, c = [self.endpoint(p, i) for i in range(3)]
        a.sendto(b"abcd", BROADCAST_ADDRESS)
        eventually(b.recvfrom)
        time.sleep(.02)
        self.assertIsNone(c.recvfrom())
        self.assertEqual(a.metrics.snapshot()["delivery_attempts"], 1)

    def test_self_send(self):
        p = plan_for_test(1)
        a = self.endpoint(p, 0)
        a.sendto(b"loop", (a.spec.overlay_ip, a.spec.overlay_port))
        self.assertEqual(eventually(a.recvfrom)[0], b"loop")
        expected_tx_energy = len(b"loop") * 8 * p.link_config.e_elec_j_per_bit
        self.assertAlmostEqual(
            a.metrics.snapshot()["energy_sent_j"],
            expected_tx_energy,
        )

    def test_unknown_sender_is_rejected(self):
        p = plan_for_test(1)
        a = self.endpoint(p, 0)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as outsider:
            outsider.sendto(b"injected", (a.spec.underlay_ip, a.spec.underlay_port))
            a.wait_readable(.1)
            self.assertIsNone(a.recvfrom())
        self.assertEqual(a.metrics.snapshot()["packets_received"], 0)
        self.assertEqual(a.metrics.snapshot()["unknown_sources_rejected"], 1)

    def test_close_cancels_without_counting_tx(self):
        p = plan_for_test(link=dict(max_data_rate_bps=8))
        a = self.endpoint(p, 0)
        a.sendto(b"abcd", (p.nodes[1].overlay_ip, p.nodes[1].overlay_port))
        a.close()
        m = a.metrics.snapshot()
        self.assertEqual(m["packets_attempted"], 1)
        self.assertEqual(m["packets_sent"], 0)
        self.assertEqual(m["energy_sent_j"], 0)
        self.assertEqual(m["packets_dropped"], 1)

    def test_queue_has_backpressure(self):
        p = plan_for_test(link=dict(max_data_rate_bps=8), runtime=dict(max_queue_packets=1))
        a = self.endpoint(p, 0)
        destination = (p.nodes[1].overlay_ip, p.nodes[1].overlay_port)
        a.sendto(b"a", destination)
        with self.assertRaises(BlockingIOError):
            a.sendto(b"b", destination)

    def test_log_is_streamed_and_tail_bounded(self):
        p = plan_for_test(runtime=dict(log_tail_records=2))
        a, b = self.endpoint(p, 0), self.endpoint(p, 1)
        a.sendto(b"a", (b.spec.overlay_ip, b.spec.overlay_port))
        eventually(b.recvfrom)
        m = a.metrics.snapshot()
        self.assertEqual(len(m["packet_log"]), 2)
        self.assertGreater(m["records_written"], 2)
        self.assertEqual(len(Path(m["packet_log_path"]).read_text().splitlines()), m["records_written"])

    def test_historical_tick_cannot_be_forced(self):
        p = plan_for_test()
        a = self.endpoint(p, 0)
        with self.assertRaises(ValueError):
            a.set_tick(a.current_tick()+5)

    def test_expired_send_rejected(self):
        p = plan_for_test(duration=.1)
        a = self.endpoint(p, 0)
        with patch.object(a, "elapsed", return_value=1):
            with self.assertRaises(RuntimeError):
                a.sendto(b"a", (p.nodes[1].overlay_ip, p.nodes[1].overlay_port))
        self.assertEqual(a.metrics.snapshot()["packets_sent"], 0)

    def test_oversized_udp_is_rejected_synchronously(self):
        p = plan_for_test()
        a = self.endpoint(p, 0)
        with self.assertRaises(OSError):
            a.sendto(bytes(65508), BROADCAST_ADDRESS)

    def test_receive_callback_uses_live_clock(self):
        p = plan_for_test()
        a = self.endpoint(p, 0)
        with patch.object(a, "elapsed", return_value=.55):
            self.assertEqual(a.current_tick(), 5)
            
    def test_negative_ipdv_is_preserved_when_delay_positive(self):
        p = plan_for_test(link=dict(base_delay_s=.05))
        a, b = self.endpoint(p, 0), self.endpoint(p, 1)
        with patch.object(a._rng, "gauss", return_value=-1.0):
            a.sendto(b"abcd", (b.spec.overlay_ip, b.spec.overlay_port))
            eventually(b.recvfrom)
        events = [
            e for e in a.metrics.snapshot()["packet_log"]
            if e["event"] == "scheduled_delivery"
        ]
        self.assertTrue(events)
        self.assertLess(events[0]["post_transmission_delay_s"], .05)
        self.assertAlmostEqual(events[0]["ipdv_s"], -.001)

    def test_live_emission_rate_respects_token_budget(self):
        p = plan_for_test(link=dict(max_data_rate_bps=80000, ipdv_enabled=False))
        a, b = self.endpoint(p, 0), self.endpoint(p, 1)
        for _ in range(6):
            a.sendto(bytes(1000), (b.spec.overlay_ip, b.spec.overlay_port))
        received = []
        def drained():
            packet = b.recvfrom()
            if packet:
                received.append(packet)
            return len(received) == 6
        eventually(drained, timeout=1.5)
        emissions = [e["time_s"] for e in a.metrics.snapshot()["packet_log"] if e["event"] == "transmit"]
        self.assertEqual(len(emissions), 6)
        self.assertGreaterEqual(emissions[-1]-emissions[0], .48)


class RuntimeTests(unittest.TestCase):
    def test_expired_run_preserves_prior_outputs(self):
        p = plan_for_test(1)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"plan.json"
            p.save(path)
            old = Path(root)/"CH-1.metrics.json"
            old.write_text("previous result")
            emulator = Emulator(path, root)
            with self.assertRaises(RuntimeError):
                emulator.run()
            self.assertEqual(old.read_text(), "previous result")
            self.assertIsNone(emulator.run_dir)

    def test_receive_flood_has_packet_budget(self):
        p = plan_for_test(1, runtime=dict(receive_batch_size=3, receive_budget_s=1))
        class FakeSocket:
            def elapsed(self): return .01
            def recvfrom(self): return b"data", ("x", 1)
        node = Node(p.nodes[0], p, FakeSocket(), SilentProtocol)
        self.assertEqual(node._receive_available(), 3)
        node.events.close()

    def test_no_protocol_steps_after_deadline(self):
        p = plan_for_test(1)
        class FakeSocket:
            calls = 0
            def elapsed(self): return 3
            def check_health(self): pass
            def close(self): pass
            class metrics:
                @staticmethod
                def snapshot(): return {}
        class CountProtocol(Protocol):
            def step(self, tick): FakeSocket.calls += 1
        node = Node(p.nodes[0], p, FakeSocket(), CountProtocol)
        with patch.object(node, "_wait_for_shared_epoch"), self.assertRaises(TimingOverrunError):
            node.run()
        self.assertEqual(FakeSocket.calls, 0)

    def test_missed_tick_fails_instead_of_replaying(self):
        p = plan_for_test(1)
        class FakeSocket:
            def elapsed(self): return .25
            def check_health(self): pass
            def close(self): pass
        node = Node(p.nodes[0], p, FakeSocket(), SilentProtocol)
        with patch.object(node, "_wait_for_shared_epoch"), self.assertRaises(TimingOverrunError):
            node.run()

    def test_child_exception_preserves_metrics_and_error(self):
        self._failed_worker("test_clive:CrashProtocol", .3, .5)

    def test_hung_worker_is_terminated(self):
        start = time.monotonic()
        self._failed_worker("test_clive:HangProtocol", .2, .2)
        self.assertLess(time.monotonic()-start, 6)

    def _failed_worker(self, protocol, duration, grace):
        p = plan_for_test(1, duration=duration, runtime=dict(worker_grace_s=grace))
        p.epoch_start = time.time()+1
        with tempfile.TemporaryDirectory() as root:
            p.save(Path(root)/"plan.json")
            emu = Emulator(Path(root)/"plan.json", Path(root)/"results", protocol_class_path=protocol)
            with self.assertRaises((RuntimeError, TimeoutError)):
                emu.run()
            manifest = json.loads((Path(emu.run_dir)/"run-manifest.json").read_text())
            self.assertFalse(manifest["success"])
            self.assertEqual(manifest["status"], "failed")
            self.assertTrue((Path(emu.run_dir)/"CH-1.packets.jsonl").exists())
            if "Crash" in protocol:
                self.assertTrue((Path(emu.run_dir)/"CH-1.error.json").exists())
                self.assertFalse(json.loads((Path(emu.run_dir)/"CH-1.metrics.json").read_text())["success"])


if __name__ == "__main__":
    unittest.main()
