"""CLIVE-only tests for simplified packet/activity/network feed and temporary map signals."""
import json
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orchestrator import Orchestrator
from visualizer import RunData

def main():
    with tempfile.TemporaryDirectory() as work:
        p=Path(work)
        plan=Orchestrator().prepare({'num_ch':2,'num_sn':1,'duration':12.0,'seed':11,
            'startup_delay_s': .3, 'mobility':{'tick_interval':.5,'area_width_m':100,'area_height_m':100}})
        plan.save(str(p/'plan.json'))
        for kind in ['packets','events']:(p/'debug'/kind).mkdir(parents=True)
        names={n.name:n.address for n in plan.nodes}
        pack=[
            {'event':'attempt','node_name':'CH-1','time_s':1.0},
            {'event':'transmit','node_name':'CH-1','time_s':1.1,'is_broadcast':True,
             'common_header':{'packet_type':0,'sequence_number':7}},
            {'event':'receive','node_name':'SN-1','time_s':1.3,'source':list(names['CH-1']),
             'common_header':{'packet_type':0,'sequence_number':7}},
            {'event':'delivery_drop','node_name':'CH-2','time_s':3.0,
             'destination':list(names['SN-1']), 'common_header':{'packet_type':4,'sequence_number':9}},
        ]
        proto=[
            {'event':'flame_hello_sent','node_name':'CH-1','time_s':1.0},
            {'event':'flame_ch_discovered','node_name':'SN-1','time_s':1.31,'source':list(names['CH-1'])},
            {'event':'flame_join_sent','node_name':'SN-1','time_s':1.5,'target':list(names['CH-1'])},
            {'event':'flame_message_reassembled','node_name':'CH-1','time_s':2.0},
            {'event':'flame_trained','node_name':'CH-1','time_s':2.4},
            {'event':'flame_aggregated','node_name':'CH-1','time_s':2.6},
            {'event':'flame_leave_sent','node_name':'SN-1','time_s':4.0,'target':list(names['CH-1'])},
        ]
        (p/'debug/packets/packets.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in pack))
        (p/'debug/events/events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in proto))
        r=RunData(p)
        a=r.events(from_s=0,to_s=5,category='activity',limit=100)
        assert {'flame_hello_sent','flame_ch_discovered','flame_join_sent','flame_trained','flame_aggregated','flame_leave_sent'} == {x['event'] for x in a['events']},a
        packets=r.events(from_s=0,to_s=5,category='packets',limit=100)
        assert [x['event'] for x in packets['events']] == ['transmit'],packets
        assert r.events(from_s=0,to_s=5,category='packets',search='HELLO')['matching_count']==1
        assert {x['event'] for x in r.event_archive(category='activity',limit=100)['events']} == {
            x['event'] for x in a['events']}
        assert [x['event'] for x in r.event_archive(category='packets',limit=100)['events']] == [
            x['event'] for x in packets['events']]
        network=r.events(from_s=0,to_s=5,category='network',limit=100)
        assert [x['event'] for x in network['events']] == ['transmit','receive','delivery_drop'],network
        assert r.events(from_s=0,to_s=1.2,category='activity')['matching_count']==1
        rel=r.relationships()
        assert any(x['action']=='flame_hello_sent' and x['source']=='CH-1' for x in rel['events']),rel
        assert any(x['action']=='flame_leave_sent' for x in rel['events']),rel
        assert any(x['action']=='flame_aggregated' and x['source']=='CH-1' for x in rel['events']),rel
        print('PASS: three feed views, packet-name search, HELLO+disconnection map provenance')
if __name__=='__main__':main()
