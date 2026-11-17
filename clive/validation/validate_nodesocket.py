#!/usr/bin/env python3
"""Behavioral acceptance checks for the localhost-only CLIVE NodeSocket.

Expected behavior is derived from the thesis link model: Gauss-Markov topology replay,
Table 3-3 RSSI/link conditions, Table 4-3 loss/IPDV/rate behavior, and Equations 3-13/3-14.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nodesocket import BROADCAST_ADDRESS, NodeSocket, energy_rx, energy_tx, link_conditions, parse_common_header
from orchestrator import DEFAULT_PORT, EmulationConfig, EmulationPlan, LinkConfig, MobilityConfig, NodeRole, NodeSpec


RESULTS = []
_PAIR_ID = 0


def report(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))


def packet(seq=1, size=64):
    header = bytes([(1 << 4) | 15, 0x80]) + (seq & 0xFFFF).to_bytes(2, "big")
    return (header + b"x" * max(0, size - 4))[:size]


def next_ips(count=2):
    global _PAIR_ID
    _PAIR_ID += 1
    base = 20 + _PAIR_ID * 8
    return [f"127.0.0.{base + i}" for i in range(count)]


def static_plan(positions, *, duration=2.0, tick_interval=0.5, max_rate=1e9, ipdv=False):
    names = list(positions)
    ips = next_ips(len(names))
    nodes = [NodeSpec(name, NodeRole.CLUSTER_HEAD, ip, DEFAULT_PORT) for name, ip in zip(names, ips)]
    mobility = MobilityConfig(
        tick_interval=tick_interval,
        area_width_m=500.0,
        area_height_m=500.0,
        ch_mean_speed_mps=0.0,
        sn_mean_speed_mps=0.0,
        speed_stddev=0.0,
        direction_stddev=0.0,
    )
    config = EmulationConfig(
        duration_s=duration,
        seed=810,
        startup_delay_s=0.1,
        io_poll_interval_s=0.001,
        receive_batch_size=128,
        receive_budget_s=0.01,
        worker_grace_s=1.0,
        readiness_timeout_s=5.0,
        start_tolerance_s=0.25,
    )
    link = LinkConfig(max_data_rate_bps=max_rate, ipdv_enabled=ipdv)
    ticks = int(duration / tick_interval) + 1
    trace = {tick: {name: list(xy) for name, xy in positions.items()} for tick in range(ticks)}
    plan = EmulationPlan(nodes, trace, link, mobility, config, time.time() + 0.08)
    plan.validate()
    return plan, nodes


def wait_start(sock):
    while sock.elapsed() < 0:
        time.sleep(0.0005)


def recv_one(sock, timeout=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = sock.recvfrom()
        if got is not None:
            return got
        time.sleep(0.0005)
    return None


def wait_resolved(sock, expected=1, timeout=2.0):
    deadline = time.monotonic() + timeout
    snapshot = sock.metrics.snapshot()
    while time.monotonic() < deadline:
        snapshot = sock.metrics.snapshot()
        resolved = snapshot["kernel_datagrams_sent"] + snapshot["delivery_drops"]
        if snapshot["delivery_attempts"] >= expected and resolved >= expected:
            return snapshot
        sock.check_health()
        time.sleep(0.001)
    return snapshot


def test_math():
    cfg = LinkConfig(ipdv_enabled=False)
    expected = [
        (1.0, 1.00, 0.00, 1.0, True),
        (20.0, 0.44, 0.02, 5.0, True),
        (40.0, 0.20, 0.10, 25.0, True),
        (100.0, 0.00, 1.00, None, False),
    ]
    ok = True
    for distance, frac, plr, ipdv, reachable in expected:
        c = link_conditions(distance, cfg)
        ok &= math.isclose(c["bandwidth_bps"], cfg.max_data_rate_bps * frac, rel_tol=1e-9)
        ok &= math.isclose(c["plr"], plr, rel_tol=1e-9)
        ok &= c["ipdv_ms"] == ipdv and c["reachable"] is reachable
    report("Table 3-3 link mapping", ok)

    bits, distance = 800, 3.0
    tx = bits * (cfg.e_elec_j_per_bit + cfg.e_amp_j_per_bit_mn * distance ** cfg.pathloss_exponent)
    rx = bits * cfg.e_elec_j_per_bit
    report("Equations 3-13/3-14 energy (100 pJ, d**n)", math.isclose(energy_tx(bits, distance, cfg), tx) and math.isclose(energy_rx(bits, cfg), rx))

    raw = bytes([0x1F, 0x80, 0x12, 0x34])
    report("Common-header parsing", parse_common_header(raw) == {
        "protocol_version": 1,
        "packet_type": 15,
        "role": 1,
        "padding": 0,
        "sequence_number": 0x1234,
    })


def test_direct_localhost_delivery():
    plan, nodes = static_plan({"A": (0.0, 0.0), "B": (1.0, 0.0)})
    a, b = [NodeSocket(n, plan, plan.config.seed) for n in nodes]
    try:
        wait_start(a)
        data = packet()
        a.sendto(data, nodes[1].address)
        got = recv_one(b)
        snap = wait_resolved(a)
        ok = got is not None and got[0] == data and got[1] == nodes[0].address
        ok &= snap["kernel_datagrams_sent"] == 1 and snap["packets_sent"] == 1
        report("Direct localhost UDP send/receive", ok)
    finally:
        a.close(); b.close()


def test_unreachable_drop():
    plan, nodes = static_plan({"A": (0.0, 0.0), "B": (100.0, 0.0)})
    a, b = [NodeSocket(n, plan, plan.config.seed) for n in nodes]
    try:
        wait_start(a)
        a.sendto(packet(), nodes[1].address)
        snap = wait_resolved(a)
        got = recv_one(b, 0.15)
        report(
            "Out-of-range link drops before kernel send",
            got is None and snap["delivery_drops"] == 1 and snap["kernel_datagrams_sent"] == 0,
        )
    finally:
        a.close(); b.close()


def test_bandwidth_delay():
    rate = 80_000.0
    data = packet(size=1000)
    plan, nodes = static_plan({"A": (0.0, 0.0), "B": (1.0, 0.0)}, max_rate=rate)
    a, b = [NodeSocket(n, plan, plan.config.seed) for n in nodes]
    try:
        wait_start(a)
        started = time.monotonic()
        a.sendto(data, nodes[1].address)
        got = recv_one(b, 1.0)
        elapsed = time.monotonic() - started
        expected = len(data) * 8 / rate
        report("Token-bucket transmission delay ~= L/R", got is not None and elapsed >= expected * 0.85, f"observed={elapsed:.4f}s expected~{expected:.4f}s")
    finally:
        a.close(); b.close()


def test_broadcast_pairwise():
    plan, nodes = static_plan({"A": (0.0, 0.0), "B": (1.0, 0.0), "C": (100.0, 0.0)})
    a, b, c = [NodeSocket(n, plan, plan.config.seed) for n in nodes]
    try:
        wait_start(a)
        a.sendto(packet(), BROADCAST_ADDRESS)
        near = recv_one(b, 0.5)
        far = recv_one(c, 0.15)
        snap = wait_resolved(a, expected=2)
        report(
            "Broadcast evaluates receiver links independently",
            near is not None and far is None and snap["delivery_attempts"] == 2 and snap["delivery_drops"] >= 1,
        )
    finally:
        a.close(); b.close(); c.close()


def main():
    test_math()
    test_direct_localhost_delivery()
    test_unreachable_drop()
    test_bandwidth_delay()
    test_broadcast_pairwise()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        raise SystemExit("FAILED: " + ", ".join(failed))


if __name__ == "__main__":
    main()
