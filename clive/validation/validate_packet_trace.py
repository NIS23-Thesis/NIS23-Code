"""CLIVE packet-trace regression. No changes to FLAME."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestrator import Orchestrator
from visualizer import RunData
from packet_trace import build_traces


def event(kind, at, node, packet=4, seq=11, pid=None, source=None, target=None,
          fingerprint='abcd1234', broadcast=False, reason=None):
    return {'event':kind, 'time_s':at, 'node':node, 'source':source or node,
            'target':target, 'stream':'packet', 'packet_type':packet,
            'sequence_number':seq, 'packet_id':pid,
            'payload_fingerprint':fingerprint, 'is_broadcast':broadcast,
            'archive_id':f'{node}:{kind}:{at}:{target}', 'reason':reason}


def main():
    tx=event('transmit',.1,'SN-1',packet=2,pid=7,target='CH-1')
    accepted=event('kernel_send',.11,'SN-1',packet=2,pid=7,target='CH-1')
    received=event('receive',.12,'CH-1',packet=2,pid=None,source='SN-1')
    result=build_traces([tx,accepted,received],0,1)
    p=result['packets'][0]
    assert p['status']=='received' and p['receiver_count']==1 and p['kernel_sends']==1, p
    assert p['fingerprint_confirmed'] is True
    wrong=event('receive',.12,'CH-1',packet=2,pid=None,source='SN-1',fingerprint='wrong')
    result=build_traces([tx,wrong],0,1)
    assert result['packets'][0]['status']=='unverified' and result['orphan_receives']==1
    # Sender-local IDs reset per node: identical integers must not cross-connect.
    unrelated=event('transmit',.1,'SN-2',packet=2,pid=7,target='CH-1')
    result=build_traces([tx,accepted,received,unrelated],0,1)
    assert len(result['packets'])==2 and result['packets'][0]['receiver_count']==1
    # Identical bytes sent twice cannot be assigned definitively to one attempt.
    retransmit=event('transmit',.115,'SN-1',packet=2,pid=8,target='CH-1')
    result=build_traces([tx,retransmit,received],0,1)
    assert result['ambiguous_receives']==1 and result['orphan_receives']==1
    assert sum(p['receiver_count'] for p in result['packets'] if p['status']=='ambiguous')==1
    assert all(p['receiver_count']==0 for p in result['packets'] if p['status']=='unverified')
    # Broadcast HELLO reaching two peers is ONE TX and TWO RX observations.
    hello=event('transmit',1,'CH-1',packet=0,seq=21,pid=12,target=None,broadcast=True)
    rx1=event('receive',1.01,'SN-1',packet=0,seq=21,source='CH-1')
    rx2=event('receive',1.02,'SN-2',packet=0,seq=21,source='CH-1')
    partial=event('delivery_drop',1.03,'CH-1',packet=0,seq=21,pid=12,
                  target='SN-3',reason='unreachable')
    result=build_traces([hello,rx1,rx2,partial],0,2)
    assert result['packets'][0]['status']=='partial',result
    assert result['packets'][0]['receiver_count']==2
    # CLIVE may drop at the recipient or even before emission.
    dropped=event('local_drop',2,'SN-1',packet=2,seq=22,pid=33,reason='queue_full')
    assert build_traces([dropped],0,3)['packets'][0]['status']=='not_transmitted'
    old=event('receive',.12,'CH-1',packet=2,pid=None,source='SN-1',fingerprint=None)
    legacy=build_traces([tx,old],0,1)['packets'][0]
    assert legacy['status']=='likely_received' and not legacy['fingerprint_confirmed']

    # End-to-end RunData: real plan address mapping and packet log reading.
    with tempfile.TemporaryDirectory(prefix='clive-trace-test-') as td:
        path=Path(td)/'run-packet-trace';path.mkdir()
        plan=Orchestrator().prepare({'num_ch':2,'num_sn':1,'duration':8.,'seed':810,
           'startup_delay_s':.1,'mobility':{'tick_interval':.5,
                   'area_width_m':100.,'area_height_m':100.}})
        plan.save(str(path/'plan.json'))
        addresses={n.name:n.address for n in plan.nodes}
        logs=path/'debug'/'packets';logs.mkdir(parents=True)
        def write(node,records):
            with (logs/f'{node}.jsonl').open('w') as f:
                for record in records:f.write(json.dumps(record)+'\n')
        write('SN-1',[{'event':'transmit','node_name':'SN-1','packet_id':8,
             'time_s':.1,'destination':list(addresses['CH-1']),
             'payload_fingerprint':'1234abcd',
             'common_header':{'packet_type':2,'sequence_number':55}}])
        write('CH-1',[{'event':'receive','node_name':'CH-1','time_s':.101,
            'source':list(addresses['SN-1']), 'payload_fingerprint':'1234abcd',
            'common_header':{'packet_type':2,'sequence_number':55}}])
        run=RunData(path)
        data=run.packet_trace(from_s=0,to_s=.2)
        assert data['total']==1 and data['packets'][0]['status']=='received',data
        assert data['packets'][0]['target']=='CH-1'
        assert run.packet_trace(from_s=0,to_s=.2,node='CH-1')['total']==1
        assert run.packet_trace(from_s=0,to_s=.2,packet_type='HELLO')['total']==0
        assert run.packet_trace(from_s=0,to_s=.2,sequence='55')['total']==1
        try:run.packet_trace(from_s=0,to_s=200)
        except ValueError:pass
        else:raise AssertionError('Wide scans must be rejected')
    print('CLIVE Packet Trace: all unit + disk/API regressions passed')

if __name__=='__main__':main()
