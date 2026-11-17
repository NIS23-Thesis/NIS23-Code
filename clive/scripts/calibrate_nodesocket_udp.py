#!/usr/bin/env python3
"""Distance-varying UDP ping/throughput probe THROUGH CLIVE NodeSocket.

Unlike native localhost ping or iperf3 (which bypass NodeSocket), these probes
traverse the emulated radio path. The results are OBSERVATIONS, not guaranteed
statistical matches to the configured PLR at small sample sizes. This is a
ping-/iperf-LIKE CLIVE integration calibration, not an external iperf3 run.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodesocket import NodeSocket, link_conditions
from orchestrator import DEFAULT_PORT, EmulationConfig, EmulationPlan, LinkConfig, MobilityConfig, NodeRole, NodeSpec


def plan_for(distance, seconds, seed, rate_bps, *, ipdv_enabled=False):
    positions = {'A': [30.0, 40.0], 'B': [30.0 + float(distance), 40.0]}
    a = NodeSpec('A', NodeRole.CLUSTER_HEAD, '127.0.0.185', DEFAULT_PORT)
    b = NodeSpec('B', NodeRole.CLUSTER_HEAD, '127.0.0.186', DEFAULT_PORT)
    mobility = MobilityConfig(tick_interval=0.5, area_width_m=300.0, area_height_m=300.0,
                              ch_mean_speed_mps=0.0, sn_mean_speed_mps=0.0,
                              speed_stddev=0.0, direction_stddev=0.0)
    conf = EmulationConfig(duration_s=float(seconds) + 6.0, seed=int(seed), startup_delay_s=0.3,
                           readiness_timeout_s=10.0, start_tolerance_s=0.5, io_poll_interval_s=0.001)
    link = LinkConfig(max_data_rate_bps=rate_bps, ipdv_enabled=ipdv_enabled)
    ticks = int(conf.duration_s / mobility.tick_interval) + 2
    trace = {i: positions for i in range(ticks)}
    plan = EmulationPlan([a, b], trace, link, mobility, conf, time.time() + 0.4)
    plan.validate()
    return plan, a, b


def exchange(sock_a, sock_b, dest_a, dest_b, payload, *, timeout=0.3):
    """Send echo ping A->B->A, without an external socket bypass."""
    t0 = time.monotonic()
    try:
        sock_a.sendto(payload, dest_b)
    except (BlockingIOError, RuntimeError):
        return None
    deadline = time.monotonic() + timeout
    echoed = False
    while time.monotonic() < deadline:
        incoming = sock_b.recvfrom()
        if incoming and incoming[0] == payload:
            try:
                sock_b.sendto(incoming[0], dest_a)
                echoed = True
            except (BlockingIOError, RuntimeError):
                break
        answer = sock_a.recvfrom()
        if echoed and answer and answer[0] == payload:
            return (time.monotonic() - t0) * 1000.0
        time.sleep(0.001)
    return None


def run_probe(distance, seconds, seed, rate_bps, ping_count, payload_bytes, *, ipdv_enabled=False):
    plan, node_a, node_b = plan_for(distance, seconds, seed, rate_bps, ipdv_enabled=ipdv_enabled)
    conditions = link_conditions(distance, plan.link_config)
    s_a = s_b = None
    try:
        s_a = NodeSocket(node_a, plan, seed)
        s_b = NodeSocket(node_b, plan, seed)
        while s_a.elapsed() < 0:
            time.sleep(0.001)
        rtts = []
        for i in range(ping_count):
            ping = b'P%03d' % i + b'X' * 60
            latency = exchange(s_a, s_b, node_a.address, node_b.address, ping)
            if latency is not None:
                rtts.append(latency)
        # Offer a modest amount MORE traffic than the modeled rate, not a raw
        # hundreds-of-megabits burst that only measures Python process overhead.
        offered_rate_bps = max(8000.0, conditions['bandwidth_bps'] * 1.15)
        packet_interval = payload_bytes * 8.0 / offered_rate_bps
        packet_interval = max(0.005, packet_interval)
        now = time.monotonic()
        deadline = now + seconds
        next_send = now
        attempted = 0
        received_ids = set()
        local_rejected = 0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                payload = b'D%07d' % attempted + b'X' * max(0, payload_bytes - 8)
                try:
                    s_a.sendto(payload, node_b.address)
                except BlockingIOError:
                    local_rejected += 1
                attempted += 1
                next_send += packet_interval
                if next_send < now - 2 * packet_interval:
                    next_send = now
            while True:
                got = s_b.recvfrom()
                if got is None:
                    break
                if got[0].startswith(b'D') and len(got[0]) >= 8:
                    received_ids.add(got[0][:8])
            time.sleep(min(0.001, max(0.0, next_send - time.monotonic())))
        received_in_window = len(received_ids)
        # Late deliveries are tracked separately and excluded from the
        # throughput numerator when the denominator is the test window.
        drain_deadline = time.monotonic() + 0.3
        while time.monotonic() < drain_deadline:
            got = s_b.recvfrom()
            if got is not None and got[0].startswith(b'D') and len(got[0]) >= 8:
                received_ids.add(got[0][:8])
            time.sleep(0.001)
        tx_metrics = s_a.metrics.snapshot()
        return {
            'distance_m': distance, 'seed': seed, 'configured_base_rate_bps': rate_bps,
            'modeled_reachable': conditions['reachable'],
            'modeled_plr': conditions['plr'],
            'modeled_link_rate_bps': conditions['bandwidth_bps'],
            'rssi_dbm': conditions['rssi_dbm'],
            'ipdv_ms': conditions['ipdv_ms'], 'ipdv_enabled': bool(ipdv_enabled),
            'ping_sent': ping_count, 'ping_echoes': len(rtts),
            'ping_loss_fraction': (ping_count - len(rtts)) / ping_count,
            'ping_median_rtt_ms': statistics.median(rtts) if rtts else None,
            'throughput_test_s': seconds, 'payload_bytes': payload_bytes,
            'udp_data_attempts': attempted, 'udp_data_local_queue_rejections': local_rejected,
            'udp_data_received_unique': len(received_ids),
            'udp_data_received_in_window': received_in_window,
            'udp_data_received_late': len(received_ids) - received_in_window,
            'udp_data_receive_goodput_bps': received_in_window * payload_bytes * 8 / seconds,
            'radio_delivery_attempts_all_probes': tx_metrics['delivery_attempts'],
            'radio_delivery_drops_all_probes': tx_metrics['delivery_drops'],
            'radio_kernel_handoffs_all_probes': tx_metrics['kernel_datagrams_sent'],
            'radio_drop_fraction_all_probes': (
                tx_metrics['delivery_drops'] / tx_metrics['delivery_attempts']
                if tx_metrics['delivery_attempts'] else None),
            'radio_attempts_unresolved_at_snapshot': max(0, tx_metrics['delivery_attempts']
                - tx_metrics['delivery_drops'] - tx_metrics['kernel_datagrams_sent']),
            'warning': 'Ping-like echo and iperf-like saturation are NodeSocket probes, NOT external native ping/iperf3. No large-sample PLR confidence guarantee.',
        }
    finally:
        for sock in (s_a, s_b):
            if sock:
                sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distances', nargs='+', type=float, default=[1.0, 20.0, 40.0, 75.0])
    parser.add_argument('--seconds', type=float, default=3.0, help='data throughput probe duration per distance (seconds)')
    parser.add_argument('--ping-count', type=int, default=6)
    parser.add_argument('--payload-bytes', type=int, default=256)
    parser.add_argument('--rate-bps', type=float, default=80000.0, help='low enough to avoid host CPU rate artifacts')
    parser.add_argument('--seed', type=int, default=810)
    parser.add_argument('--ipdv-enabled', action='store_true', help='include configured packet-delay variation in the measurements')
    parser.add_argument('--output', type=Path, default=ROOT / 'results' / 'calibration')
    args = parser.parse_args()
    if args.seconds <= 0 or args.ping_count < 1 or not 12 <= args.payload_bytes <= 1400 or args.rate_bps <= 0:
        parser.error('seconds/rate/ping count must be positive; payload bytes must be 12..1400')
    if any(d < 1 or d > 150 for d in args.distances):
        parser.error('distances must be within 1..150 m for this calibration plan')
    rows = []
    args.output.mkdir(parents=True, exist_ok=True)
    for dist in args.distances:
        row = run_probe(dist, args.seconds, args.seed, args.rate_bps, args.ping_count, args.payload_bytes, ipdv_enabled=args.ipdv_enabled)
        rows.append(row)
        print(f"{dist:6.1f} m: expected loss={row['modeled_plr']:.0%}, "
              f"UDP probe received={row['udp_data_received_unique']}/{row['udp_data_attempts']}, "
              f"received goodput={row['udp_data_receive_goodput_bps']:,.0f} bps", flush=True)
    report = {'schema': 'clive-nodesocket-udp-calibration-v1',
              'status': 'executed', 'probe_type': 'application-level UDP ping/iperf-style THROUGH CLIVE NodeSocket',
              'native_ping_or_iperf3_executed': False, 'results': rows,
              'limitations': [
                  'Native ping and iperf3 to localhost bypass the CLIVE emulated radio; these are custom NodeSocket-through probes.',
                  'Only modeled PLR and rate changes with distance; measured RTT includes process and OS scheduling.',
                  '80kbps is a controlled calibration rate, not the 1Gbps thesis maximum.',
                  'This does not validate physical wireless MAC contention, multi-hop routing, or real device batteries.',
                  'Finite probe counts may deviate substantially from expected stochastic packet loss.',
              ]}
    (args.output / 'calibration.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    cols = list(rows[0]) if rows else []
    with (args.output / 'calibration.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader(); writer.writerows(rows)
    print(f"Saved calibration evidence: {args.output}")


if __name__ == '__main__':
    main()
