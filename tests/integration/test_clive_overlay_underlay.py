"""Overlay/underlay transport separation regression tests for CTTHES2 CLIVE."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "clive"))
from orchestrator import DEFAULT_PORT, EmulationPlan, NodeRole, NodeSpec, Orchestrator
from nodesocket import NodeSocket


def auto_scenario(**extra):
    return {
        "num_ch": 1, "num_sn": 1,
        "runtime": {"duration_s": 2.0, "seed": 118, "startup_delay_s": 0.5,
                    "radio_execution": "process"},
        "addressing": {"overlay_network": "10.88.0.0/24",
                       "underlay_network": "127.16.0.0/24",
                       "overlay_port": 31521, "underlay_port": 31522},
        "mobility": {"model": "static", "initial_topology": "manual",
                     "manual_positions": {"CH-1": [10, 10], "SN-1": [11, 10]}},
        "link": {"range_mode": "explicit", "max_range_m": 50.0,
                 "ipdv_enabled": False},
        **extra,
    }


def test_original_localhost_mapping_is_preserved():
    plan = Orchestrator().prepare({"num_ch": 1, "num_sn": 1, "runtime": {"duration_s": 1}})
    assert [(n.address, n.underlay_address) for n in plan.nodes] == [
        (("127.0.0.2", DEFAULT_PORT), ("127.0.0.2", DEFAULT_PORT)),
        (("127.0.0.3", DEFAULT_PORT), ("127.0.0.3", DEFAULT_PORT)),
    ]


def test_distinct_auto_overlay_and_underlay_ports_roundtrip(tmp_path):
    plan = Orchestrator().prepare(auto_scenario())
    assert plan.nodes[0].address == ("10.88.0.2", 31521)
    assert plan.nodes[0].underlay_address == ("127.16.0.2", 31522)
    target = tmp_path / 'plan.json'
    plan.save(str(target))
    saved = json.loads(target.read_text())
    assert saved["nodes"][0]["overlay_ip"] == "10.88.0.2"
    assert saved["nodes"][0]["overlay_port"] == 31521
    assert saved["nodes"][0]["underlay_ip"] == "127.16.0.2"
    restored = EmulationPlan.load(str(target))
    assert [n.address for n in restored.nodes] == [n.address for n in plan.nodes]
    assert [n.underlay_address for n in restored.nodes] == [n.underlay_address for n in plan.nodes]


def test_explicit_node_list_with_separate_endpoints():
    plan = Orchestrator().prepare({"runtime": {"duration_s": 1}, "nodes": [
        {"name": "CH-X", "role": "CH", "overlay": {"ip": "10.99.1.20", "port": 32001},
         "underlay": {"ip": "127.18.0.20", "port": 32002}},
        {"name": "SN-X", "role": "SN", "overlay": {"ip": "10.99.1.21", "port": 32001},
         "underlay": {"ip": "127.18.0.21", "port": 32002}},
    ]})
    assert plan.nodes[0].role == NodeRole.CLUSTER_HEAD
    assert plan.nodes[1].role == NodeRole.SENSOR_NODE
    assert plan.nodes[1].overlay_address == ("10.99.1.21", 32001)
    assert plan.nodes[1].underlay_address == ("127.18.0.21", 32002)


@pytest.mark.parametrize("changes, message", [
    ({"addressing": {"overlay_network": "10.88.0.0/30"}}, "too small"),
    ({"addressing": {"underlay_network": "192.168.1.0/24"}}, "127.0.0.0/8"),
    ({"addressing": {"overlay_port": 0}}, "valid UDP ports"),
    ({"nodes": [{"name": "CH-1", "role": "CH", "ip": "10.0.0.1", "underlay_ip": "192.168.0.2"}]}, "127/8"),
])
def test_invalid_addressing_rejected(changes, message):
    scenario = {"num_ch": 1, "num_sn": 1, "runtime": {"duration_s": 1}}
    scenario.update(changes)
    with pytest.raises(ValueError, match=message):
        Orchestrator().prepare(scenario)


def test_duplicate_underlay_ip_is_rejected_even_with_different_ports():
    with pytest.raises(ValueError, match="underlay IP must be unique"):
        Orchestrator().prepare({"nodes": [
            {"name": "CH-1", "role": "CH", "ip": "10.50.0.1", "port": 20100,
             "underlay_ip": "127.20.0.2", "underlay_port": 20101},
            {"name": "SN-1", "role": "SN", "ip": "10.50.0.2", "port": 20100,
             "underlay_ip": "127.20.0.2", "underlay_port": 20102},
        ], "runtime": {"duration_s": 1}})


def test_nodesocket_uses_physical_underlay_and_returns_logical_overlay():
    plan = Orchestrator().prepare(auto_scenario())
    left = NodeSocket(plan.nodes[0], plan, plan.config.seed)
    right = None
    try:
        right = NodeSocket(plan.nodes[1], plan, plan.config.seed)
        assert left._sock.getsockname() == plan.nodes[0].underlay_address
        assert right._sock.getsockname() == plan.nodes[1].underlay_address
        while left.elapsed() < 0:
            time.sleep(0.005)
        payload = b'clive-overlay-underlay-byte-for-byte'
        assert left.sendto(payload, plan.nodes[1].overlay_address) == len(payload)
        deadline = time.monotonic() + 0.8
        actual = None
        while time.monotonic() < deadline and actual is None:
            actual = right.recvfrom()
            time.sleep(0.002)
        assert actual == (payload, plan.nodes[0].overlay_address)
        # An underlay endpoint is not a valid protocol destination.
        with pytest.raises(ValueError, match="unknown localhost node address"):
            left.sendto(payload, plan.nodes[1].underlay_address)
    finally:
        left.close()
        if right is not None:
            right.close()


def test_overlay_broadcast_expands_only_overlay_port():
    plan = Orchestrator().prepare(auto_scenario())
    left = NodeSocket(plan.nodes[0], plan, plan.config.seed)
    right = None
    try:
        right = NodeSocket(plan.nodes[1], plan, plan.config.seed)
        while left.elapsed() < 0:
            time.sleep(0.005)
        left.sendto(b'BROADCAST-CHECK', ('255.255.255.255', 31521))
        deadline = time.monotonic() + 0.8
        actual = None
        while time.monotonic() < deadline and actual is None:
            actual = right.recvfrom()
            time.sleep(0.002)
        assert actual == (b'BROADCAST-CHECK', plan.nodes[0].overlay_address)
    finally:
        left.close()
        if right is not None:
            right.close()


def test_flame_hello_uses_configured_overlay_broadcast_port():
    """Regression: HELLO must work when overlay port is not DEFAULT_PORT."""
    from types import SimpleNamespace
    from flame_protocol import FlameProtocol
    messages = []
    protocol = FlameProtocol.__new__(FlameProtocol)
    protocol.node = SimpleNamespace(spec=NodeSpec(
        "CH-T", NodeRole.CLUSTER_HEAD, "10.101.0.2", 28062, "127.101.0.2", 28072
    ))
    protocol.model_id = 1
    protocol.comms = SimpleNamespace(send_message=lambda message, destination: messages.append((message, destination)))
    protocol._record = lambda *args, **kwargs: None
    protocol._send_hello()
    assert len(messages) == 1
    assert messages[0][1] == ("255.255.255.255", 28062)


def test_controller_accepts_separate_subnets_without_dropping_metadata():
    from control_center import validated_scenario
    scenario = auto_scenario()
    result = validated_scenario(scenario)
    assert result['addressing'] == scenario['addressing']


def test_controller_accepts_explicit_nodes_and_rejects_invalid_counts():
    from control_center import validated_scenario
    scenario = {
        'nodes': [
            {'name': 'CH-1', 'role': 'CH', 'overlay': {'ip': '10.29.0.2', 'port': 28062},
             'underlay': {'ip': '127.29.0.2', 'port': 28072}},
            {'name': 'SN-1', 'role': 'SN', 'overlay': {'ip': '10.29.0.3', 'port': 28062},
             'underlay': {'ip': '127.29.0.3', 'port': 28072}},
        ],
        'runtime': {'duration_s': 1},
    }
    assert validated_scenario(scenario)['nodes'] == scenario['nodes']
    with pytest.raises(ValueError, match='num_ch does not match nodes'):
        validated_scenario({**scenario, 'num_ch': 2})
