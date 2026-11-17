"""Semantic event regressions: do not attribute reliable sensor transfers to model traffic."""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'clive'))
from visualizer import RunData


def ev(kind,packet_type=None):
    return {'event':kind,'stream':'protocol','node':'SN-1','time_s':1.0,
            **({'packet_type':packet_type} if packet_type is not None else {})}


def test_transfer_family_must_be_recorded_not_assumed():
    for kind in ('flame_transfer_complete','flame_transfer_failed'):
        assert kind not in RunData._RELATED_PACKET
        for family,ptype in (('SENSOR_DATA',2),('MODEL_UPDATE',4)):
            row=ev(kind,ptype)
            assert RunData._matches_event_filters(row,packet=family)
            assert not RunData._matches_event_filters(row,
                        packet='SENSOR_DATA' if family=='MODEL_UPDATE' else 'MODEL_UPDATE')
            assert family.lower() in RunData._search_text(row).lower()
        for row in (ev(kind), ev(kind,99)):
            assert not RunData._matches_event_filters(row,packet='MODEL_UPDATE')
            assert not RunData._matches_event_filters(row,packet='SENSOR_DATA')


def test_sensing_provenance_and_backpressure():
    assert RunData._preview_group(ev('flame_sample_collected')) == 'clive'
    row=ev('flame_radio_backpressure_drop')
    assert RunData._preview_group(row) == 'clive'
    assert RunData._is_failure_event(row)
    assert RunData._matches_event_filters(row,outcome='failure')
    assert not RunData._matches_event_filters(row,outcome='nonfailure')
    assert not RunData._is_failure_event(ev('flame_joined'))
    assert not RunData._is_failure_event({**ev('flame_model_rejected_or_duplicate'),
        'details':{'rejection_kind':'duplicate'}})


def test_archive_and_window_filters_agree_for_sensor_and_model_transfer(tmp_path):
    events=tmp_path/'debug'/'events'
    events.mkdir(parents=True)
    for node,ptype in [('SN-1',2),('CH-1',4)]:
        (events/f'{node}.jsonl').write_text('\n'.join(json.dumps({
            'type':'flame_transfer_complete', 'time_s':i*0.01 + ptype,
            'packet_type':ptype,'node_name':node,'message_id':i,
        }) for i in range(2))+'\n')
    run=RunData(tmp_path)
    for family,node in (('SENSOR_DATA','SN-1'),('MODEL_UPDATE','CH-1')):
        arch=run.event_archive(category='flame-action',packet=family,order='asc')['events']
        window=run.events(from_s=0,to_s=20,category='flame-action',packet=family,order='asc')['events']
        assert len(arch)==len(window)==2
        assert [x['node'] for x in arch]==[node,node]
        assert [x['packet_type'] for x in window]==[2 if family=='SENSOR_DATA' else 4]*2
        assert [x['time_s'] for x in arch]==[x['time_s'] for x in window]
