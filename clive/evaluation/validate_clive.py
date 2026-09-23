"""CLIVE NodeSocket validator.

Tests available:
  single      - existing two-CH distance sweep; optional real iperf3 throughput
  pairwise    - multiple fixed CHs; validate every pair's one-way PLR/reachability
  concurrent  - one CH sends to several CHs at the same time; inspect per-flow and aggregate rates
  broadcast   - one CH broadcasts once per packet; receivers independently experience their link conditions

Examples:
  python3 validate_clive.py --tests single --distances 1,20,40,100
  python3 validate_clive.py --tests single --iperf --distances 1,20,40,100
  python3 validate_clive.py --tests pairwise --positions 0,20,40,100
  python3 validate_clive.py --tests concurrent --positions 0,20,40,100 --offered-rate 2000000
  python3 validate_clive.py --tests broadcast --positions 0,20,40,100
  python3 validate_clive.py --tests all --positions 0,20,40,100 --distances 1,20,40,100

The multi-node tests intentionally use static CH positions.  They validate NodeSocket,
not the full FLAME protocol and not MANET routing (CLIVE is single-hop only).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
from pathlib import Path
import select
import shutil
import socket
import statistics
import struct
import tempfile
import threading
import time

from clive.evaluation.iperf_bridge import run_iperf
from nodesocket import BROADCAST_ADDRESS, NodeSocket
from orchestrator import Orchestrator, atomic_json


TEST_NAMES = ("single", "pairwise", "concurrent", "broadcast")


def table_reference(distance, maximum_rate):
    """Independent Table 3-3 oracle: RSSI0=-30 dBm, n=3, d0=1 m."""
    if not math.isfinite(distance) or distance < 0:
        raise ValueError("distance must be finite and nonnegative")
    rssi = -30 - 30 * math.log10(max(1, distance))
    if rssi <= -85:
        fraction, loss, variation = 0.0, 1.00, None
    elif rssi <= -75:
        fraction, loss, variation = 0.20, 0.10, 25.0
    elif rssi < -65:
        fraction, loss, variation = 0.44, 0.02, 5.0
    else:
        fraction, loss, variation = 1.00, 0.00, 1.0
    return dict(
        reachable=fraction > 0,
        rssi_dbm=rssi,
        bandwidth_bps=maximum_rate * fraction,
        plr=loss,
        ipdv_ms=variation,
    )


def format_bps(value):
    if value is None:
        return "N/A"
    value = float(value)
    if value >= 1e9:
        return f"{value / 1e9:.3f} Gbps"
    if value >= 1e6:
        return f"{value / 1e6:.3f} Mbps"
    if value >= 1e3:
        return f"{value / 1e3:.3f} kbps"
    return f"{value:.0f} bps"


def parse_csv_floats(text, *, name):
    try:
        values = [float(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be comma-separated numbers") from exc
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise argparse.ArgumentTypeError(f"{name} must contain finite nonnegative numbers")
    return values


def loss_tolerance(loss_probability, count):
    """Four-sigma binomial tolerance plus 1/n, same policy as the single-link test."""
    if count <= 0:
        return 0.0
    return 4 * math.sqrt(loss_probability * (1 - loss_probability) / count) + 1 / count


def packet(seq, payload_bytes=64):
    if payload_bytes < 8:
        raise ValueError("payload_bytes must be at least 8")
    return b"\x0f\x80" + struct.pack("!H", seq & 0xFFFF) + bytes(payload_bytes - 4)


def unused_port(ip):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as candidate:
        candidate.bind((ip, 0))
        return candidate.getsockname()[1]


def wait_until_started(endpoint):
    while endpoint.elapsed() < 0:
        time.sleep(0.001)


def drain(endpoints, timeout=0.0, max_each=256):
    """Drain currently readable NodeSockets and return received packet counts by node name."""
    counts = {endpoint.spec.name: 0 for endpoint in endpoints}
    if not endpoints:
        return counts
    ready = select.select(endpoints, [], [], timeout)[0]
    for endpoint in ready:
        for _ in range(max_each):
            item = endpoint.recvfrom()
            if item is None:
                break
            counts[endpoint.spec.name] += 1
    return counts


def verify_ipdv_logs(directory, expected):
    """Check generated signed Gaussian IPDV samples from the two-node test logs."""
    samples = []
    for filename in ("a.packets.jsonl", "b.packets.jsonl"):
        with open(Path(directory) / filename, encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event["event"] == "scheduled_delivery":
                    post_delay = event.get("post_transmission_delay_s")
                    if post_delay is None:
                        return dict(passed=False, reason="scheduled_delivery missing post_transmission_delay_s")
                    if post_delay < 0:
                        return dict(passed=False, reason="negative post-transmission delay")
                    samples.append(event["ipdv_s"])
    if not expected["reachable"]:
        return dict(
            passed=not samples,
            sample_count=len(samples),
            reason="unreachable link has no scheduled deliveries",
        )
    if len(samples) < 30:
        return dict(passed=False, sample_count=len(samples), reason="insufficient samples")
    sigma = expected["ipdv_ms"] / 3000
    mean, sd = statistics.mean(samples), statistics.stdev(samples)
    mean_limit = 4 * sigma / math.sqrt(len(samples))
    sd_limit = 4 * sigma / math.sqrt(2 * (len(samples) - 1))
    return dict(
        passed=abs(mean) <= mean_limit and abs(sd - sigma) <= sd_limit,
        sample_count=len(samples),
        mean_s=mean,
        stddev_s=sd,
        expected_stddev_s=sigma,
        scope="generated IPDV samples; host scheduling/underlay latency not certified",
    )


@contextmanager
def pair(distance, duration, directory, *, seed=810, max_rate=1e9, ipdv=True):
    plan = Orchestrator().prepare(
        dict(
            num_ch=2,
            num_sn=0,
            duration=duration,
            seed=seed,
            mobility=dict(area_width_m=max(200, distance + 1), area_height_m=200),
            link=dict(max_data_rate_bps=max_rate, ipdv_enabled=ipdv),
        )
    )
    for positions in plan.trace.values():
        positions["CH-1"] = [0.0, 0.0]
        positions["CH-2"] = [distance, 0.0]
    for node in plan.nodes:
        node.underlay_port = unused_port(node.underlay_ip)
    plan.epoch_start = time.time() + 0.05
    plan.validate()
    a = b = None
    try:
        a = NodeSocket(plan.nodes[0], plan, seed, log_path=Path(directory) / "a.packets.jsonl")
        b = NodeSocket(plan.nodes[1], plan, seed, log_path=Path(directory) / "b.packets.jsonl")
        wait_until_started(a)
        yield plan, a, b
    finally:
        for endpoint in (a, b):
            if endpoint is not None:
                endpoint.close()
                atomic_json(
                    Path(directory) / f"{endpoint.spec.name}.metrics.json",
                    endpoint.metrics.snapshot(),
                )


@contextmanager
def fixed_nodes(positions, duration, directory, *, seed=810, max_rate=1e9, ipdv=True):
    """Create N CH NodeSockets on the x-axis at the supplied positions."""
    positions = [float(x) for x in positions]
    if len(positions) < 2:
        raise ValueError("multi-node tests require at least two positions")

    plan = Orchestrator().prepare(
        dict(
            num_ch=len(positions),
            num_sn=0,
            duration=duration,
            seed=seed,
            mobility=dict(area_width_m=max(200, max(positions) + 1), area_height_m=200),
            link=dict(max_data_rate_bps=max_rate, ipdv_enabled=ipdv),
        )
    )

    for trace_positions in plan.trace.values():
        for index, x in enumerate(positions, start=1):
            trace_positions[f"CH-{index}"] = [x, 0.0]

    for node in plan.nodes:
        node.underlay_port = unused_port(node.underlay_ip)

    plan.epoch_start = time.time() + 0.05
    plan.validate()
    endpoints = []
    try:
        for index, node in enumerate(plan.nodes):
            endpoints.append(
                NodeSocket(
                    node,
                    plan,
                    seed,
                    log_path=Path(directory) / f"ch-{index + 1}.packets.jsonl",
                )
            )
        wait_until_started(endpoints[0])
        yield plan, endpoints
    finally:
        for endpoint in endpoints:
            try:
                endpoint.close()
            finally:
                atomic_json(
                    Path(directory) / f"{endpoint.spec.name}.metrics.json",
                    endpoint.metrics.snapshot(),
                )


def udp_echo(a, b, count=1000, interval=0.005, settle=0.25):
    address_a = (a.spec.overlay_ip, a.spec.overlay_port)
    address_b = (b.spec.overlay_ip, b.spec.overlay_port)
    next_send = time.monotonic()
    pending, rtts = {}, []
    start_a = a.metrics.snapshot()
    start_b = b.metrics.snapshot()
    sent = 0
    deadline = next_send + count * interval + settle
    while time.monotonic() < deadline:
        now = time.monotonic()
        if sent < count and now >= next_send:
            data = b"\x0f\x80\x00\x00" + struct.pack("!I", sent) + bytes(56)
            pending[sent] = now
            a.sendto(data, address_b)
            sent += 1
            next_send = now + interval
            deadline = max(deadline, next_send + settle)
        for endpoint in select.select([a, b], [], [], min(0.001, interval))[0]:
            for _ in range(64):
                item = endpoint.recvfrom()
                if item is None:
                    break
                data, _ = item
                if endpoint is b:
                    b.sendto(data, address_a)
                elif len(data) >= 8:
                    sequence, = struct.unpack("!I", data[4:8])
                    started = pending.pop(sequence, None)
                    if started is not None:
                        rtts.append(time.monotonic() - started)
        if sent == count and not pending:
            break
    end_a, end_b = a.metrics.snapshot(), b.metrics.snapshot()
    return dict(
        attempted=sent,
        echoes=len(rtts),
        round_trip_loss=1 - len(rtts) / sent if sent else None,
        mean_rtt_s=statistics.mean(rtts) if rtts else None,
        min_rtt_s=min(rtts) if rtts else None,
        forward_sent=end_a["packets_sent"] - start_a["packets_sent"],
        forward_received=end_b["packets_received"] - start_b["packets_received"],
        local_drops=(end_a["packets_dropped"] - start_a["packets_dropped"] +
                     end_b["packets_dropped"] - start_b["packets_dropped"]),
    )


def throughput_summary(result):
    expected = result["expected"]
    link_rate = expected["bandwidth_bps"]
    goodput = result["expected_udp_goodput_bps"]

    if result["throughput_status"] == "not_run":
        return (
            f"throughput NOT RUN | theoretical link={format_bps(link_rate)} | "
            f"expected UDP goodput={format_bps(goodput)}"
        )

    if not expected["reachable"]:
        bridge = result.get("iperf3", {}).get("clive_bridge", {})
        return (
            f"throughput {result['throughput_status'].upper()} | expected=0 bps (unreachable) | "
            f"bridge sender_bits={bridge.get('sender_bits', 'N/A')} | "
            f"receiver_bits={bridge.get('receiver_bits', 'N/A')}"
        )

    measured = result.get("measured_udp_goodput_bps")
    low = result.get("throughput_accept_min_bps")
    high = result.get("throughput_accept_max_bps")
    ratio = result.get("throughput_measured_to_expected")
    ratio_text = "N/A" if ratio is None else f"{ratio * 100:.1f}% of expected"
    return (
        f"throughput {result['throughput_status'].upper()} | "
        f"link={format_bps(link_rate)} | expected goodput={format_bps(goodput)} | "
        f"measured={format_bps(measured)} | accepted={format_bps(low)}..{format_bps(high)} | "
        f"{ratio_text}"
    )


def validate_distance(
    distance,
    directory,
    *,
    packets=1000,
    interval=0.005,
    iperf=False,
    seconds=3,
    max_rate=1e9,
    seed=810,
    iperf_payload=1200,
):
    Path(directory).mkdir(parents=True, exist_ok=False)
    with pair(
        distance,
        packets * interval + seconds + 20,
        directory,
        seed=seed,
        max_rate=max_rate,
    ) as (_, a, b):
        expected = table_reference(distance, max_rate)
        echo = udp_echo(a, b, packets, interval)
        target = 1 - (1 - expected["plr"]) ** 2
        tolerance = loss_tolerance(target, packets)
        ping_ok = (
            echo["attempted"] == packets
            and echo["local_drops"] == 0
            and abs(echo["round_trip_loss"] - target) <= tolerance
        )
        expected_goodput = expected["bandwidth_bps"] * (1 - expected["plr"])
        result = dict(
            distance_m=distance,
            expected=expected,
            udp_echo=echo,
            expected_round_trip_loss=target,
            loss_tolerance=tolerance,
            udp_echo_pass=ping_ok,
            throughput_status="not_run",
            expected_link_bandwidth_bps=expected["bandwidth_bps"],
            expected_udp_goodput_bps=expected_goodput,
            measured_udp_goodput_bps=None,
            throughput_accept_min_bps=0.80 * expected_goodput if expected["reachable"] else 0.0,
            throughput_accept_max_bps=1.20 * expected_goodput if expected["reachable"] else 0.0,
            throughput_measured_to_expected=None,
        )
        result["ipdv_model_check"] = verify_ipdv_logs(directory, expected)

        if iperf:
            measurement = run_iperf(
                a,
                b,
                seconds=seconds,
                bitrate_bps=max(1e6, expected["bandwidth_bps"] * 1.05),
                payload_bytes=iperf_payload,
            )
            result["iperf3"] = measurement
            if not expected["reachable"]:
                bridge = measurement.get("clive_bridge", {})
                result["throughput_status"] = (
                    "pass_unreachable"
                    if (
                        ping_ok
                        and bridge.get("sender_bits", 0) > 0
                        and bridge.get("receiver_bits") == 0
                        and not bridge.get("errors")
                    )
                    else "inconclusive"
                )
            elif measurement["status"] == "measured":
                end = measurement.get("end", {})
                received = end.get("sum_received", end.get("sum", {})).get("bits_per_second")
                result["measured_udp_goodput_bps"] = received
                if received is not None and expected_goodput > 0:
                    result["throughput_measured_to_expected"] = received / expected_goodput
                low = result["throughput_accept_min_bps"]
                high = result["throughput_accept_max_bps"]
                if received is None:
                    result["throughput_status"] = "measurement_missing"
                elif received < low:
                    result["throughput_status"] = "below_expected_or_host_limited"
                elif received > high:
                    result["throughput_status"] = "above_expected"
                else:
                    result["throughput_status"] = "pass"
            else:
                result["throughput_status"] = measurement["status"]

        result["model_checks_pass"] = ping_ok and result["ipdv_model_check"]["passed"]
        result["fully_validated"] = result["model_checks_pass"] and result["throughput_status"] in {
            "pass",
            "pass_unreachable",
        }
        atomic_json(Path(directory) / "result.json", result)
        return result


def one_way_probe(sender, receiver, *, count, interval, payload_bytes=64, settle=0.5):
    start_sender = sender.metrics.snapshot()
    start_receiver = receiver.metrics.snapshot()
    destination = (receiver.spec.overlay_ip, receiver.spec.overlay_port)
    next_send = time.monotonic()
    sent = 0
    deadline = next_send + count * interval + settle

    while time.monotonic() < deadline:
        now = time.monotonic()
        if sent < count and now >= next_send:
            sender.sendto(packet(sent, payload_bytes), destination)
            sent += 1
            next_send = now + interval
            deadline = max(deadline, next_send + settle)
        drain([receiver], min(0.001, interval))
        if sent == count and time.monotonic() >= next_send + settle:
            break

    drain([receiver], settle)
    end_sender = sender.metrics.snapshot()
    end_receiver = receiver.metrics.snapshot()
    received = end_receiver["packets_received"] - start_receiver["packets_received"]
    local_drops = end_sender["packets_dropped"] - start_sender["packets_dropped"]
    return dict(
        attempted=sent,
        received=received,
        measured_loss=1 - received / sent if sent else None,
        local_drops=local_drops,
        packets_sent=end_sender["packets_sent"] - start_sender["packets_sent"],
    )


def validate_pairwise(positions, directory, *, packets, interval, max_rate, seed, bidirectional=False):
    Path(directory).mkdir(parents=True, exist_ok=False)
    n = len(positions)
    directions = n * (n - 1) if bidirectional else n * (n - 1) // 2
    duration = max(10.0, directions * packets * interval + directions * 0.75 + 5)
    results = []

    with fixed_nodes(positions, duration, directory, seed=seed, max_rate=max_rate) as (_, endpoints):
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append((i, j))
                if bidirectional:
                    pairs.append((j, i))

        for i, j in pairs:
            sender, receiver = endpoints[i], endpoints[j]
            distance = abs(positions[i] - positions[j])
            expected = table_reference(distance, max_rate)
            measured = one_way_probe(sender, receiver, count=packets, interval=interval)
            tolerance = loss_tolerance(expected["plr"], packets)
            ok = (
                measured["local_drops"] == 0
                and measured["measured_loss"] is not None
                and abs(measured["measured_loss"] - expected["plr"]) <= tolerance
            )
            item = dict(
                source=sender.spec.name,
                destination=receiver.spec.name,
                distance_m=distance,
                expected=expected,
                measured=measured,
                loss_tolerance=tolerance,
                passed=ok,
            )
            results.append(item)
            print(
                f"{sender.spec.name}->{receiver.spec.name} {distance:g}m: "
                f"{'PASS' if ok else 'FAIL'} | RSSI={expected['rssi_dbm']:.2f} dBm | "
                f"link={format_bps(expected['bandwidth_bps'])} | "
                f"PLR expected={expected['plr'] * 100:.1f}% measured={measured['measured_loss'] * 100:.1f}%",
                flush=True,
            )

    summary = dict(test="pairwise", positions_m=positions, results=results, passed=all(x["passed"] for x in results))
    atomic_json(Path(directory) / "summary.json", summary)
    return summary


def validate_broadcast(positions, directory, *, packets, interval, max_rate, seed, payload_bytes=64):
    Path(directory).mkdir(parents=True, exist_ok=False)
    duration = max(10.0, packets * interval + 5)
    with fixed_nodes(positions, duration, directory, seed=seed, max_rate=max_rate) as (_, endpoints):
        sender = endpoints[0]
        receivers = endpoints[1:]
        start_sender = sender.metrics.snapshot()
        starts = {r.spec.name: r.metrics.snapshot() for r in receivers}
        next_send = time.monotonic()
        sent = 0
        deadline = next_send + packets * interval + 1.0

        while time.monotonic() < deadline:
            now = time.monotonic()
            if sent < packets and now >= next_send:
                sender.sendto(packet(sent, payload_bytes), BROADCAST_ADDRESS)
                sent += 1
                next_send = now + interval
                deadline = max(deadline, next_send + 1.0)
            drain(receivers, min(0.001, interval))
            if sent == packets and time.monotonic() >= next_send + 1.0:
                break

        drain(receivers, 0.5)
        end_sender = sender.metrics.snapshot()
        tx_count = end_sender["packets_sent"] - start_sender["packets_sent"]
        attempts = end_sender["delivery_attempts"] - start_sender["delivery_attempts"]
        local_drops = end_sender["packets_dropped"] - start_sender["packets_dropped"]

        receiver_results = []
        for index, receiver in enumerate(receivers, start=1):
            end = receiver.metrics.snapshot()
            received = end["packets_received"] - starts[receiver.spec.name]["packets_received"]
            distance = abs(positions[0] - positions[index])
            expected = table_reference(distance, max_rate)
            measured_loss = 1 - received / packets if packets else None
            tolerance = loss_tolerance(expected["plr"], packets)
            ok = measured_loss is not None and abs(measured_loss - expected["plr"]) <= tolerance
            receiver_results.append(
                dict(
                    receiver=receiver.spec.name,
                    distance_m=distance,
                    expected=expected,
                    received=received,
                    measured_loss=measured_loss,
                    loss_tolerance=tolerance,
                    passed=ok,
                )
            )
            print(
                f"broadcast CH-1->{receiver.spec.name} {distance:g}m: "
                f"{'PASS' if ok else 'FAIL'} | RSSI={expected['rssi_dbm']:.2f} dBm | "
                f"PLR expected={expected['plr'] * 100:.1f}% measured={measured_loss * 100:.1f}% | "
                f"received={received}/{packets}",
                flush=True,
            )

        accounting_ok = (
            local_drops == 0
            and tx_count == packets
            and attempts == packets * len(receivers)
        )
        print(
            f"broadcast accounting: {'PASS' if accounting_ok else 'FAIL'} | "
            f"logical TX={tx_count} expected={packets} | "
            f"delivery attempts={attempts} expected={packets * len(receivers)}",
            flush=True,
        )

        summary = dict(
            test="broadcast",
            positions_m=positions,
            logical_transmissions=tx_count,
            delivery_attempts=attempts,
            local_drops=local_drops,
            accounting_pass=accounting_ok,
            receivers=receiver_results,
            passed=accounting_ok and all(x["passed"] for x in receiver_results),
        )
        atomic_json(Path(directory) / "summary.json", summary)
        return summary


def concurrent_worker(sender, receiver, *, stop_at, offered_rate_bps, payload_bytes, state):
    """Paced producer for one concurrent unicast flow."""
    destination = (receiver.spec.overlay_ip, receiver.spec.overlay_port)
    bits = payload_bytes * 8
    interval = bits / offered_rate_bps
    next_send = time.monotonic()
    seq = 0
    attempted = 0
    blocked = 0

    while time.monotonic() < stop_at:
        now = time.monotonic()
        if now < next_send:
            time.sleep(min(next_send - now, 0.001))
            continue
        try:
            sender.sendto(packet(seq, payload_bytes), destination)
            attempted += 1
            seq += 1
        except BlockingIOError:
            blocked += 1
        next_send += interval
        if next_send < now - interval:
            next_send = now + interval

    state.update(attempted=attempted, blocked=blocked)


def validate_concurrent(
    positions,
    directory,
    *,
    seconds,
    offered_rate,
    payload_bytes,
    max_rate,
    seed,
):
    """One sender (CH-1) sends simultaneously to every other CH."""
    Path(directory).mkdir(parents=True, exist_ok=False)
    duration = max(10.0, seconds + 5)

    with fixed_nodes(positions, duration, directory, seed=seed, max_rate=max_rate) as (_, endpoints):
        sender = endpoints[0]
        receivers = endpoints[1:]
        sender_start = sender.metrics.snapshot()
        receiver_starts = {r.spec.name: r.metrics.snapshot() for r in receivers}
        states = {r.spec.name: {} for r in receivers}

        start = time.monotonic() + 0.05
        stop_at = start + seconds
        while time.monotonic() < start:
            time.sleep(0.001)

        threads = []
        for receiver in receivers:
            thread = threading.Thread(
                target=concurrent_worker,
                kwargs=dict(
                    sender=sender,
                    receiver=receiver,
                    stop_at=stop_at,
                    offered_rate_bps=offered_rate,
                    payload_bytes=payload_bytes,
                    state=states[receiver.spec.name],
                ),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        while any(thread.is_alive() for thread in threads):
            drain(receivers, 0.002, max_each=1024)
        for thread in threads:
            thread.join()

        settle_end = time.monotonic() + 1.0
        while time.monotonic() < settle_end:
            drain(receivers, 0.002, max_each=1024)

        sender_end = sender.metrics.snapshot()
        emitted_bits = sender_end["bits_sent"] - sender_start["bits_sent"]
        local_drops = sender_end["packets_dropped"] - sender_start["packets_dropped"]
        aggregate_tx_bps = emitted_bits / seconds

        flow_results = []
        for index, receiver in enumerate(receivers, start=1):
            end = receiver.metrics.snapshot()
            received_bits = end["bits_received"] - receiver_starts[receiver.spec.name]["bits_received"]
            measured_bps = received_bits / seconds
            distance = abs(positions[0] - positions[index])
            expected = table_reference(distance, max_rate)
            state = states[receiver.spec.name]
            flow_results.append(
                dict(
                    receiver=receiver.spec.name,
                    distance_m=distance,
                    expected=expected,
                    offered_rate_bps=offered_rate,
                    attempted_packets=state.get("attempted", 0),
                    blocked_sends=state.get("blocked", 0),
                    measured_receive_bps=measured_bps,
                )
            )
            print(
                f"CH-1->{receiver.spec.name} {distance:g}m: "
                f"offered={format_bps(offered_rate)} | "
                f"link={format_bps(expected['bandwidth_bps'])} | "
                f"received={format_bps(measured_bps)} | blocked={state.get('blocked', 0)}",
                flush=True,
            )

        # The shared sender token bucket is explicitly intended to prevent aggregate
        # radio emission above max_data_rate_bps.  A small allowance covers timing granularity.
        shared_ceiling_ok = aggregate_tx_bps <= max_rate * 1.05
        reachability_ok = all(
            (flow["measured_receive_bps"] == 0 if not flow["expected"]["reachable"] else flow["measured_receive_bps"] > 0)
            for flow in flow_results
        )
        print(
            f"aggregate CH-1 TX: {format_bps(aggregate_tx_bps)} | "
            f"shared ceiling={format_bps(max_rate)} | "
            f"{'PASS' if shared_ceiling_ok else 'FAIL'}",
            flush=True,
        )

        summary = dict(
            test="concurrent",
            positions_m=positions,
            seconds=seconds,
            offered_rate_per_flow_bps=offered_rate,
            aggregate_tx_bps=aggregate_tx_bps,
            max_sender_rate_bps=max_rate,
            shared_ceiling_pass=shared_ceiling_ok,
            reachability_pass=reachability_ok,
            local_drops=local_drops,
            flows=flow_results,
            passed=shared_ceiling_ok and reachability_ok,
            note=(
                "This validates simultaneous NodeSocket traffic and the sender-wide ceiling. "
                "It does not model 802.11 contention between independent transmitters."
            ),
        )
        atomic_json(Path(directory) / "summary.json", summary)
        return summary


def parse_tests(text):
    names = [x.strip().lower() for x in text.split(",") if x.strip()]
    if not names:
        raise argparse.ArgumentTypeError("--tests cannot be empty")
    if "all" in names:
        return list(TEST_NAMES)
    invalid = [x for x in names if x not in TEST_NAMES]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"unknown test(s): {', '.join(invalid)}; choose from {', '.join(TEST_NAMES)},all"
        )
    return names


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tests",
        type=parse_tests,
        default=["single"],
        help="comma-separated: single,pairwise,concurrent,broadcast or all (default: single)",
    )
    parser.add_argument(
        "--distances",
        default=",".join(str(i) for i in range(1, 101)),
        help="distances for the single-link sweep (default: 1..100)",
    )
    parser.add_argument(
        "--positions",
        default="0,20,40,100",
        help="x-axis CH positions in metres for multi-node tests (default: 0,20,40,100)",
    )
    parser.add_argument("--packets", type=int, default=1000, help="packets per single-link distance")
    parser.add_argument("--multi-packets", type=int, default=300, help="packets per pair/broadcast multi-node test")
    parser.add_argument("--interval", type=float, default=0.005, help="packet interval for echo/pairwise/broadcast tests")
    parser.add_argument("--iperf", action="store_true", help="run real iperf3 in the single-link test")
    parser.add_argument("--iperf-payload", type=int, default=1200, help="iperf3 UDP payload bytes (default: 1200; max 65507)")
    parser.add_argument("--seconds", type=float, default=3.0, help="iperf and concurrent-test duration")
    parser.add_argument("--max-rate", type=float, default=1e9, help="configured NodeSocket maximum rate; thesis default is 1 Gbps")
    parser.add_argument("--offered-rate", type=float, default=2e6, help="concurrent test offered rate PER FLOW in bps (default: 2 Mbps)")
    parser.add_argument("--payload-bytes", type=int, default=1200, help="concurrent-test UDP payload bytes (default: 1200)")
    parser.add_argument("--bidirectional", action="store_true", help="pairwise test both A->B and B->A; default tests each pair once")
    parser.add_argument("--seed", type=int, default=810)
    parser.add_argument("--output", default="validation-results")
    parser.add_argument("--list-tests", action="store_true", help="print test descriptions and exit")
    args = parser.parse_args()

    if args.list_tests:
        print("single     : 2 CHs at each requested distance; echo/PLR/IPDV; optional iperf")
        print("pairwise   : N fixed CHs; every pair checked for one-way PLR/reachability")
        print("concurrent : CH-1 sends to all other CHs simultaneously; checks sender-wide rate ceiling")
        print("broadcast  : CH-1 broadcasts; one logical TX, independent receiver outcomes")
        return

    if args.packets <= 0 or args.multi_packets <= 0:
        parser.error("packet counts must be positive")
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be positive")
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be positive")
    if not math.isfinite(args.max_rate) or args.max_rate <= 0:
        parser.error("--max-rate must be positive")
    if not math.isfinite(args.offered_rate) or args.offered_rate <= 0:
        parser.error("--offered-rate must be positive")
    if not 8 <= args.payload_bytes <= 65507:
        parser.error("--payload-bytes must be between 8 and 65507")
    if not 16 <= args.iperf_payload <= 65507:
        parser.error("--iperf-payload must be between 16 and 65507")
    if args.iperf and "single" not in args.tests:
        parser.error("--iperf applies to the single-link test; include --tests single")
    if args.iperf and not shutil.which("iperf3"):
        parser.error("iperf3 is not installed")

    distances = parse_csv_floats(args.distances, name="--distances")
    positions = parse_csv_floats(args.positions, name="--positions")
    if len(positions) < 2 and any(name != "single" for name in args.tests):
        parser.error("multi-node tests require at least two --positions")

    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="validation-", dir=root))
    suite = dict(tests=args.tests, results={}, passed=True)

    if "single" in args.tests:
        print("\n=== SINGLE-LINK TEST ===", flush=True)
        single_dir = run / "single"
        single_dir.mkdir()
        results = []
        for index, distance in enumerate(distances, start=1):
            result = validate_distance(
                distance,
                single_dir / f"distance-{index}",
                packets=args.packets,
                interval=args.interval,
                iperf=args.iperf,
                seconds=args.seconds,
                max_rate=args.max_rate,
                seed=args.seed,
                iperf_payload=args.iperf_payload,
            )
            results.append(result)
            echo_loss = result["udp_echo"]["round_trip_loss"]
            echo_loss_text = "N/A" if echo_loss is None else f"{echo_loss * 100:.2f}%"
            expected_loss_text = f"{result['expected_round_trip_loss'] * 100:.2f}%"
            print(
                f"{distance:g}m: UDP echo {'PASS' if result['udp_echo_pass'] else 'FAIL'} "
                f"(RT loss measured={echo_loss_text}, expected={expected_loss_text}); "
                f"{throughput_summary(result)}",
                flush=True,
            )
        single_pass = all(item["model_checks_pass"] for item in results)
        if args.iperf:
            single_pass = single_pass and all(item["fully_validated"] for item in results)
        suite["results"]["single"] = dict(passed=single_pass, results=results)
        suite["passed"] = suite["passed"] and single_pass

    if "pairwise" in args.tests:
        print("\n=== MULTI-NODE PAIRWISE TEST ===", flush=True)
        result = validate_pairwise(
            positions,
            run / "pairwise",
            packets=args.multi_packets,
            interval=args.interval,
            max_rate=args.max_rate,
            seed=args.seed,
            bidirectional=args.bidirectional,
        )
        suite["results"]["pairwise"] = result
        suite["passed"] = suite["passed"] and result["passed"]

    if "concurrent" in args.tests:
        print("\n=== CONCURRENT ONE-TO-MANY TEST ===", flush=True)
        result = validate_concurrent(
            positions,
            run / "concurrent",
            seconds=args.seconds,
            offered_rate=args.offered_rate,
            payload_bytes=args.payload_bytes,
            max_rate=args.max_rate,
            seed=args.seed,
        )
        suite["results"]["concurrent"] = result
        suite["passed"] = suite["passed"] and result["passed"]

    if "broadcast" in args.tests:
        print("\n=== BROADCAST TEST ===", flush=True)
        result = validate_broadcast(
            positions,
            run / "broadcast",
            packets=args.multi_packets,
            interval=args.interval,
            max_rate=args.max_rate,
            seed=args.seed,
        )
        suite["results"]["broadcast"] = result
        suite["passed"] = suite["passed"] and result["passed"]

    atomic_json(run / "suite-summary.json", suite)
    print(f"\nResults: {run}")
    print(f"OVERALL: {'PASS' if suite['passed'] else 'FAIL'}")
    if not suite["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
