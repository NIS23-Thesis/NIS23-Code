#!/usr/bin/env python3
"""Acceptance tests for CLIVE's NodeSocket.

Place this file in the same directory as nodesocket.py and orchestrator.py, then run:

    python3 validate_nodesocket_acceptance.py

Optional statistical checks:

    python3 validate_nodesocket_acceptance.py --statistical

This is deliberately a black-box/behavioral validator.  The expected values come from
FLAME thesis Sections 3.2.1, 3.2.2, 4.1.2.4 and 4.1.3 rather than from NodeSocket's
implementation details.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass

try:
    from nodesocket import (
        BROADCAST_ADDRESS,
        NodeSocket,
        energy_rx,
        energy_tx,
        link_conditions,
        parse_common_header,
    )
    from orchestrator import (
        DEFAULT_PORT,
        EmulationConfig,
        EmulationPlan,
        LinkConfig,
        MobilityConfig,
        NodeRole,
        NodeSpec,
    )
except ImportError as exc:
    raise SystemExit(
        "Put this script beside nodesocket.py and orchestrator.py, then run it from that folder.\n"
        f"Import error: {exc}"
    )


PASS = "PASS"
FAIL = "FAIL"
WARN = "CHECK"


@dataclass
class Result:
    name: str
    status: str
    detail: str


RESULTS: list[Result] = []
_PAIR_ID = 0


def report(name: str, ok: bool, detail: str) -> None:
    RESULTS.append(Result(name, PASS if ok else FAIL, detail))


def warning(name: str, detail: str) -> None:
    RESULTS.append(Result(name, WARN, detail))


def almost(a: float, b: float, *, rel: float = 1e-6, abs_: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def packet(seq: int = 1, payload_bytes: int = 32, packet_type: int = 15) -> bytes:
    # Common FLAME header: version=1, packet type=low nibble, role=CH, padding=0, seq=16-bit.
    first = (1 << 4) | (packet_type & 0x0F)
    return bytes([first, 0x80]) + (seq & 0xFFFF).to_bytes(2, "big") + b"x" * max(0, payload_bytes - 4)


def _next_loopbacks() -> tuple[str, str]:
    global _PAIR_ID
    _PAIR_ID += 1
    # Keep comfortably inside 127/8 and avoid common 127.0.0.1.
    a = 20 + _PAIR_ID * 2
    return f"127.0.0.{a}", f"127.0.0.{a + 1}"


def static_plan(
    distance_m: float,
    *,
    duration_s: float = 5.0,
    max_rate_bps: float = 1e9,
    ipdv_enabled: bool = False,
    mode: str = "localhost",
    tick_interval: float = 0.5,
) -> tuple[EmulationPlan, NodeSpec, NodeSpec]:
    ip_a, ip_b = _next_loopbacks()

    if mode == "localhost":
        overlay_a, overlay_b = ip_a, ip_b
        underlay_a, underlay_b = ip_a, ip_b
    elif mode == "lan":
        # Still use loopback as the physical underlay so the test runs on one machine,
        # while overlay addresses are different and therefore require translation.
        overlay_a = f"10.99.0.{_PAIR_ID * 2 + 1}"
        overlay_b = f"10.99.0.{_PAIR_ID * 2 + 2}"
        underlay_a, underlay_b = ip_a, ip_b
    else:
        raise ValueError(mode)

    a = NodeSpec("A", NodeRole.CLUSTER_HEAD, overlay_a, DEFAULT_PORT, underlay_a, DEFAULT_PORT)
    b = NodeSpec("B", NodeRole.CLUSTER_HEAD, overlay_b, DEFAULT_PORT, underlay_b, DEFAULT_PORT)

    mobility = MobilityConfig(
        tick_interval=tick_interval,
        area_width_m=200.0,
        area_height_m=200.0,
        ch_mean_speed_mps=0.0,
        sn_mean_speed_mps=0.0,
        speed_stddev=0.0,
        direction_stddev=0.0,
    )
    config = EmulationConfig(
        duration_s=duration_s,
        mode=mode,
        seed=12345,
        startup_delay_s=1.0,
        io_poll_interval_s=0.001,
        receive_batch_size=256,
        receive_budget_s=0.01,
        max_queue_packets=4096,
        max_queue_bytes=8 * 1024 * 1024,
        log_tail_records=512,
        worker_grace_s=1.0,
        readiness_timeout_s=2.0,
        max_clock_offset_s=0.01,
    )
    link = LinkConfig(max_data_rate_bps=max_rate_bps, ipdv_enabled=ipdv_enabled)
    ticks = int(duration_s / tick_interval) + 1
    trace = {tick: {"A": [0.0, 0.0], "B": [float(distance_m), 0.0]} for tick in range(ticks)}
    plan = EmulationPlan([a, b], trace, link, mobility, config, time.time() + 0.05)
    plan.validate()
    return plan, a, b


def start_pair(plan: EmulationPlan, a: NodeSpec, b: NodeSpec) -> tuple[NodeSocket, NodeSocket]:
    sa = NodeSocket(a, plan, plan.config.seed)
    sb = NodeSocket(b, plan, plan.config.seed)
    while sa.elapsed() < 0:
        time.sleep(0.0005)
    return sa, sb


def wait_sender_done(sock: NodeSocket, expected_attempts: int, timeout: float = 4.0) -> dict:
    deadline = time.monotonic() + timeout
    last = sock.metrics.snapshot()
    while time.monotonic() < deadline:
        last = sock.metrics.snapshot()
        resolved = last["underlay_datagrams_sent"] + last["delivery_drops"]
        if last["delivery_attempts"] >= expected_attempts and resolved >= expected_attempts:
            return last
        sock.check_health()
        time.sleep(0.002)
    return last


def receive_one(sock: NodeSocket, timeout: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = sock.recvfrom()
        if result is not None:
            return result
        time.sleep(0.0005)
    return None


def model_table() -> None:
    cfg = LinkConfig()
    print("\n=== Thesis link-model sweep ===")
    print("distance   RSSI(dBm)   BW(% max)   PLR     IPDV(ms)   reachable")
    for d in (1, 10, 14, 15, 16, 31, 32, 33, 40, 68, 69, 70, 100):
        c = link_conditions(d, cfg)
        pct = 100 * c["bandwidth_bps"] / cfg.max_data_rate_bps
        ipdv = "N/A" if c["ipdv_ms"] is None else f"{c['ipdv_ms']:.0f}"
        print(f"{d:>7}   {c['rssi_dbm']:>9.2f}   {pct:>9.0f}%   {c['plr']*100:>4.0f}%   {ipdv:>8}   {c['reachable']}")


def test_link_mapping() -> None:
    cfg = LinkConfig()
    cases = [
        (1.0, 1.00, 0.00, 1.0, True),
        (20.0, 0.44, 0.02, 5.0, True),
        (40.0, 0.20, 0.10, 25.0, True),
        (100.0, 0.00, 1.00, None, False),
    ]
    ok = True
    details = []
    for d, frac, plr, ipdv, reachable in cases:
        c = link_conditions(d, cfg)
        got_frac = c["bandwidth_bps"] / cfg.max_data_rate_bps
        this = (
            almost(got_frac, frac)
            and almost(c["plr"], plr)
            and c["ipdv_ms"] == ipdv
            and c["reachable"] is reachable
        )
        ok &= this
        details.append(f"{d:g}m->{got_frac:.2f}/{c['plr']:.2f}/{c['ipdv_ms']}/{c['reachable']}")
    report("RSSI -> bandwidth/PLR/IPDV mapping", ok, "; ".join(details))


def test_rssi_formula() -> None:
    cfg = LinkConfig()
    ok = True
    details = []
    for d in (1.0, 10.0, 40.0, 100.0):
        expected = cfg.rssi0_dbm - 10 * cfg.pathloss_exponent * math.log10(max(d, cfg.d0_m) / cfg.d0_m)
        got = link_conditions(d, cfg)["rssi_dbm"]
        ok &= almost(got, expected)
        details.append(f"{d:g}m={got:.2f}dBm")
    report("RSSI path-loss equation", ok, ", ".join(details))


def test_header_and_energy() -> None:
    data = packet(seq=0x1234, payload_bytes=100)
    h = parse_common_header(data)
    report(
        "Common header parsing",
        h == {"protocol_version": 1, "packet_type": 15, "role": 1, "padding": 0, "sequence_number": 0x1234},
        str(h),
    )

    cfg = LinkConfig()
    bits = 800
    d = 10.0
    expected_tx = bits * (cfg.e_elec_j_per_bit + cfg.e_amp_j_per_bit_m2 * d ** cfg.pathloss_exponent)
    expected_rx = bits * cfg.e_elec_j_per_bit
    report(
        "Energy equations 3-13 / 3-14",
        almost(energy_tx(bits, d, cfg), expected_tx) and almost(energy_rx(bits, cfg), expected_rx),
        f"TX={energy_tx(bits,d,cfg):.9g}J RX={energy_rx(bits,cfg):.9g}J",
    )


def test_unicast_and_metrics() -> None:
    plan, a, b = static_plan(10.0, duration_s=2.0)
    sa, sb = start_pair(plan, a, b)
    try:
        data = packet(seq=7, payload_bytes=64)
        sa.sendto(data, (b.overlay_ip, b.overlay_port))
        got = receive_one(sb, 0.4)
        sender = wait_sender_done(sa, 1, 0.5)
        receiver = sb.metrics.snapshot()
        ok = got is not None and got[0] == data and got[1] == (a.overlay_ip, a.overlay_port)
        ok &= sender["packets_sent"] == 1 and receiver["packets_received"] == 1
        ok &= sender["bits_sent"] == len(data) * 8 and receiver["bits_received"] == len(data) * 8
        ok &= sender["by_packet_type"].get("15", {}).get("packets_sent") == 1
        ok &= receiver["by_packet_type"].get("15", {}).get("packets_received") == 1
        report("Near unicast + overlay source + metrics", ok,
               f"received={got is not None}, sent={sender['packets_sent']}, recv={receiver['packets_received']}")
    finally:
        sa.close(); sb.close()


def test_lan_translation() -> None:
    plan, a, b = static_plan(10.0, duration_s=2.0, mode="lan")
    sa, sb = start_pair(plan, a, b)
    try:
        data = packet(seq=8, payload_bytes=48)
        sa.sendto(data, (b.overlay_ip, b.overlay_port))
        got = receive_one(sb, 0.4)
        ok = got is not None and got[0] == data and got[1] == (a.overlay_ip, a.overlay_port)
        report("LAN overlay <-> underlay translation", ok,
               f"returned_source={None if got is None else got[1]}, expected={(a.overlay_ip,a.overlay_port)}")
    finally:
        sa.close(); sb.close()


def test_out_of_range() -> None:
    plan, a, b = static_plan(100.0, duration_s=2.0)
    sa, sb = start_pair(plan, a, b)
    try:
        data = packet(seq=9, payload_bytes=64)
        sa.sendto(data, (b.overlay_ip, b.overlay_port))
        sender = wait_sender_done(sa, 1, 0.5)
        got = receive_one(sb, 0.08)
        report(
            "Out-of-range packet is not delivered",
            got is None and sender["delivery_drops"] == 1 and sender["underlay_datagrams_sent"] == 0,
            f"received={got is not None}, delivery_drops={sender['delivery_drops']}",
        )

        tx_events = [e for e in sender["packet_log"] if e.get("event") == "transmit"]
        logged_bw = tx_events[-1].get("bandwidth_bps") if tx_events else None
        # Table 3-3 says the unreachable band has 0% bandwidth.
        report(
            "Out-of-range logged bandwidth is 0%",
            logged_bw == 0,
            f"logged={logged_bw}; thesis band expects 0 (delivery behavior may still be correct)",
        )
    finally:
        sa.close(); sb.close()


def test_transmission_delay_not_double_counted() -> None:
    # Scale the max rate down so L/R is large enough to measure reliably.
    rate = 80_000.0
    plan, a, b = static_plan(10.0, duration_s=2.0, max_rate_bps=rate, ipdv_enabled=False)
    sa, sb = start_pair(plan, a, b)
    try:
        data = packet(seq=10, payload_bytes=1000)
        expected = len(data) * 8 / rate + 10.0 / plan.link_config.propagation_speed_mps + plan.link_config.base_delay_s
        sa.sendto(data, (b.overlay_ip, b.overlay_port))
        got = receive_one(sb, 0.8)
        snap = wait_sender_done(sa, 1, 0.8)
        events = snap["packet_log"]
        attempt = next((e for e in events if e.get("event") == "attempt"), None)
        underlay = next((e for e in events if e.get("event") == "underlay_send"), None)
        if attempt and underlay:
            modeled = underlay["time_s"] - attempt["time_s"]
            # Allow scheduler overhead, but not a second full L/R interval.
            ok = got is not None and expected * 0.80 <= modeled <= expected * 1.50
            report("Transmission delay ~= L/R once", ok,
                   f"expected~{expected:.4f}s, observed model path~{modeled:.4f}s")
        else:
            report("Transmission delay ~= L/R once", False, "missing attempt/underlay_send events")
    finally:
        sa.close(); sb.close()


def test_bandwidth_shaping() -> None:
    # Use 1 Mbps instead of 1 Gbps so Python/host capacity cannot hide whether shaping works.
    max_rate = 1_000_000.0
    payload = 1000
    count = 30
    checks = []
    all_ok = True
    for distance, fraction in ((10.0, 1.00), (20.0, 0.44), (40.0, 0.20)):
        target = max_rate * fraction
        expected_duration = (count - 1) * payload * 8 / target
        plan, a, b = static_plan(distance, duration_s=max(3.0, expected_duration + 1.0),
                                 max_rate_bps=max_rate, ipdv_enabled=False)
        sa, sb = start_pair(plan, a, b)
        try:
            data = packet(payload_bytes=payload)
            for i in range(count):
                sa.sendto(data[:2] + (i & 0xFFFF).to_bytes(2, "big") + data[4:], (b.overlay_ip, b.overlay_port))
            snap = wait_sender_done(sa, count, timeout=max(2.0, expected_duration + 0.8))
            tx = [e for e in snap["packet_log"] if e.get("event") == "transmit"]
            if len(tx) >= count and tx[-1]["time_s"] > tx[0]["time_s"]:
                measured = (count - 1) * len(data) * 8 / (tx[-1]["time_s"] - tx[0]["time_s"])
                ok = 0.85 * target <= measured <= 1.15 * target
                checks.append(f"{distance:g}m target={target/1e3:.0f}kbps measured={measured/1e3:.0f}kbps")
                all_ok &= ok
            else:
                checks.append(f"{distance:g}m missing transmit events")
                all_ok = False
        finally:
            sa.close(); sb.close()
    report("Token-bucket bandwidth ratios (scaled host-safe test)", all_ok, "; ".join(checks))


def test_broadcast_policy() -> None:
    # Three-node topology: one near receiver and one out-of-range receiver.  A broadcast
    # must count as ONE sender transmission while receiver outcomes remain pairwise.
    global _PAIR_ID
    _PAIR_ID += 1
    base = 80 + _PAIR_ID * 3
    ips = [f"127.0.1.{base+i}" for i in range(3)]
    a = NodeSpec("A", NodeRole.CLUSTER_HEAD, ips[0], DEFAULT_PORT, ips[0], DEFAULT_PORT)
    b = NodeSpec("B", NodeRole.SENSOR_NODE, ips[1], DEFAULT_PORT, ips[1], DEFAULT_PORT)
    c = NodeSpec("C", NodeRole.SENSOR_NODE, ips[2], DEFAULT_PORT, ips[2], DEFAULT_PORT)
    mobility = MobilityConfig(
        tick_interval=0.5, area_width_m=200.0, area_height_m=200.0,
        ch_mean_speed_mps=0.0, sn_mean_speed_mps=0.0,
        speed_stddev=0.0, direction_stddev=0.0,
    )
    config = EmulationConfig(
        duration_s=2.0, mode="localhost", seed=12345, startup_delay_s=1.0,
        io_poll_interval_s=0.001, receive_batch_size=256, receive_budget_s=0.01,
        max_queue_packets=4096, max_queue_bytes=8 * 1024 * 1024,
        log_tail_records=512, worker_grace_s=1.0, readiness_timeout_s=2.0,
        max_clock_offset_s=0.01,
    )
    link = LinkConfig(max_data_rate_bps=1_000_000.0, ipdv_enabled=False)
    trace = {
        tick: {"A": [0.0, 0.0], "B": [10.0, 0.0], "C": [100.0, 0.0]}
        for tick in range(int(config.duration_s / mobility.tick_interval) + 1)
    }
    plan = EmulationPlan([a, b, c], trace, link, mobility, config, time.time() + 0.05)
    plan.validate()
    sa = NodeSocket(a, plan, plan.config.seed)
    sb = NodeSocket(b, plan, plan.config.seed)
    sc = NodeSocket(c, plan, plan.config.seed)
    while sa.elapsed() < 0:
        time.sleep(0.0005)
    try:
        data = packet(seq=11, payload_bytes=1000)
        sa.sendto(data, BROADCAST_ADDRESS)
        got_b = receive_one(sb, 0.4)
        got_c = receive_one(sc, 0.1)
        snap = wait_sender_done(sa, 2, 0.8)
        tx = [e for e in snap["packet_log"] if e.get("event") == "transmit"]
        attempts = [e for e in snap["packet_log"] if e.get("event") == "delivery_attempt"]

        ok = len(tx) == 1
        ok &= snap["packets_sent"] == 1
        ok &= snap["delivery_attempts"] == 2
        ok &= got_b is not None and got_b[0] == data
        ok &= got_c is None
        ok &= snap["delivery_drops"] == 1
        if tx:
            ok &= tx[0].get("bandwidth_bps") is None
            ok &= almost(tx[0].get("airtime_rate_bps"), link.max_data_rate_bps)
        by_target = {tuple(e["destination"]): e for e in attempts}
        near = by_target.get((b.overlay_ip, b.overlay_port))
        far = by_target.get((c.overlay_ip, c.overlay_port))
        ok &= near is not None and almost(near["bandwidth_bps"], link.max_data_rate_bps)
        ok &= far is not None and far["bandwidth_bps"] == 0
        report(
            "Broadcast = one TX with pairwise receiver outcomes",
            ok,
            f"tx={snap['packets_sent']}, delivery_attempts={snap['delivery_attempts']}, "
            f"near_received={got_b is not None}, far_received={got_c is not None}, "
            f"drops={snap['delivery_drops']}",
        )
    finally:
        sa.close(); sb.close(); sc.close()


def statistical_plr() -> None:
    print("\n=== Statistical PLR checks ===")
    count = 1000
    for distance, expected, tolerance in ((20.0, 0.02, 0.02), (40.0, 0.10, 0.04)):
        plan, a, b = static_plan(distance, duration_s=3.0, ipdv_enabled=False)
        sa, sb = start_pair(plan, a, b)
        try:
            data = packet(payload_bytes=32)
            for i in range(count):
                sa.sendto(data[:2] + (i & 0xFFFF).to_bytes(2, "big") + data[4:], (b.overlay_ip, b.overlay_port))
            snap = wait_sender_done(sa, count, timeout=2.0)
            attempts = snap["delivery_attempts"]
            drops = snap["delivery_drops"]
            observed = drops / attempts if attempts else 1.0
            ok = attempts == count and abs(observed - expected) <= tolerance
            report(f"Observed PLR near {expected*100:.0f}% at {distance:g}m", ok,
                   f"{drops}/{attempts} = {observed*100:.2f}% (tolerance +/-{tolerance*100:.0f}pp)")
        finally:
            sa.close(); sb.close()


def print_summary() -> int:
    print("\n=== Acceptance summary ===")
    width = max(len(r.name) for r in RESULTS)
    for r in RESULTS:
        print(f"{r.status:>5}  {r.name:<{width}}  {r.detail}")
    failed = sum(r.status == FAIL for r in RESULTS)
    checks = sum(r.status == WARN for r in RESULTS)
    print(f"\n{len(RESULTS)-failed-checks} passed, {failed} failed, {checks} design-check warning(s).")
    if failed:
        print("A FAIL means the observable NodeSocket behavior does not match the thesis-derived acceptance condition used by this validator.")
    if checks:
        print("A CHECK is behavior implemented by NodeSocket that the thesis does not clearly specify; decide it explicitly before treating it as correct.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--statistical", action="store_true", help="also run 1000-packet PLR checks")
    args = parser.parse_args()

    model_table()
    test_link_mapping()
    test_rssi_formula()
    test_header_and_energy()
    test_unicast_and_metrics()
    test_lan_translation()
    test_out_of_range()
    test_transmission_delay_not_double_counted()
    test_bandwidth_shaping()
    test_broadcast_policy()
    if args.statistical:
        statistical_plr()
    return print_summary()


if __name__ == "__main__":
    raise SystemExit(main())
