"""CLIVE-only checks for first-received HELLO, event categories, and complete traffic totals.

Never patches FLAME implementation; only uses its public packet/neighbor APIs.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orchestrator import Orchestrator
from visualizer import RunData
from flame_protocol import FlameProtocol
from flame.communication.message import PacketType
from flame.neighbor.neighbor_module import NeighborModule, NodeRole


def check_hello_order():
    adapter = FlameProtocol.__new__(FlameProtocol)
    adapter._left = False
    adapter._joined_ch = None
    adapter._join_target = None
    adapter._next_join = 0
    adapter._join_nonce = 10
    adapter.is_ch = False
    adapter.model_id = 1
    adapter._discovered_ch = {}
    adapter.s = {"neighbor_timeout_s": 30, "join_retry_s": 1}
    adapter.neighbors = NeighborModule(model_id=1)
    adapter.node = SimpleNamespace(elapsed=lambda: 0.1)
    adapter._record = lambda *a, **k: None
    sent = []
    adapter.comms = SimpleNamespace(send_message=lambda packet, destination: sent.append(destination))
    original_service_join = adapter._service_join
    # Two acceptable HELLOs arrive; simulate processing independently of the timer.
    # CH .9 arrives before .1, so IP ordering would choose the wrong one.
    adapter._service_join = lambda *_: None
    first = ("10.0.0.9", 27062)
    second = ("10.0.0.1", 27062)
    for ip, port in (first, second):
        adapter.neighbors.process_hello(ip, port, NodeRole.CLUSTER_HEAD, model_id=1)
        adapter._on_message({"packet_type": PacketType.HELLO, "source_address": (ip, port)})
    assert list(adapter._discovered_ch) == [first, second]
    original_service_join(0.1)
    assert sent == [first], sent
    # Once the older CH is inactive, the next one in receipt order is eligible.
    adapter.neighbors.get_neighbor(*first).association_state = type(
        adapter.neighbors.get_neighbor(*first).association_state).INACTIVE
    adapter._next_join = 0
    original_service_join(2)
    assert sent[-1] == second, sent
    print("First HELLO association order PASS")


def check_event_stream_and_timeline():
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / 'run-hello-traffic'
        run.mkdir()
        plan = Orchestrator().prepare({
            'num_ch': 2, 'num_sn': 1, 'duration': 10.0, 'seed': 810,
            'startup_delay_s': 0.2,
            'mobility': {'tick_interval': .5, 'area_width_m': 100, 'area_height_m': 100},
        })
        plan.save(str(run/'plan.json'))
        packets = run/'debug'/'packets'
        protocols = run/'debug'/'events'
        packets.mkdir(parents=True)
        protocols.mkdir(parents=True)
        dest = next(x for x in plan.nodes if x.name == 'CH-1')
        sender = next(x for x in plan.nodes if x.name == 'SN-1')
        events=[]
        for i in range(300):
            t = i * .02
            events += [
                {'event':'attempt', 'node_name':'SN-1', 'time_s':t+.0001},
                {'event':'admit', 'node_name':'SN-1', 'time_s':t+.0002},
                {'event':'transmit', 'node_name':'SN-1', 'time_s':t+.0003,
                 'common_header':{'packet_type':2,'sequence_number':i}},
                {'event':'receive', 'node_name':'CH-1', 'time_s':t+.0005,
                 'source':list(sender.address),
                 'common_header':{'packet_type':2,'sequence_number':i}},
            ]
        events.append({'event':'delivery_drop','node_name':'SN-1','time_s':6.2,
                       'destination':list(dest.address)})
        (packets/'events.jsonl').write_text('\n'.join(map(json.dumps,events))+'\n')
        (protocols/'CH-1.jsonl').write_text(json.dumps({
            'type':'flame_trained', 'time_s':3.0, 'node_name':'CH-1'})+'\n')
        view=RunData(run,max_events=5)
        view.events()  # evicts nearly everything from the live cache
        expected={'transmit':300, 'receive':300, 'drop':1, 'protocol':1, 'key':1}
        for category, count in expected.items():
            rows=view.events(from_s=0,to_s=10,category=category,limit=2000)
            assert rows['matching_count']==count,(category,rows['matching_count'])
        assert view.events(from_s=0,to_s=10,category='traffic')['matching_count']==301
        values=view.timeline(1)
        assert values['totals']=={'sent':300,'received':300,'dropped':1},values['totals']
        assert values['rows'][2]['sent']==50 and values['rows'][2]['received']==50
        # Appended events must appear on the next graph query (not double-count old lines).
        with (packets/'events.jsonl').open('a') as f:
            f.write(json.dumps({'event':'transmit','node_name':'SN-1','time_s':7.2})+'\n')
        assert view.timeline(1)['totals']['sent']==301
        assert view.timeline(1)['totals']['sent']==301
        print("Event Stream categories and uncapped traffic timeline PASS")


if __name__ == '__main__':
    check_hello_order()
    check_event_stream_and_timeline()
