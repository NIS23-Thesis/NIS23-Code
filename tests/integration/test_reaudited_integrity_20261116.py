"""Scientific data, trace, and radio emission regression checks."""
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'clive'))
from mnist_cache import prepare_mnist_cache, class_samples
from orchestrator import Orchestrator, EmulationPlan
from mobility_store import HEADER
from nodesocket import _RadioEngine


def _small_mnist(path):
    np=pytest.importorskip('numpy')
    images=np.arange(20*784,dtype=np.uint32).reshape((20,28,28)).astype('uint8')
    labels=np.array(list(range(10))*2,dtype=np.uint8)
    np.savez_compressed(path,x_train=images,y_train=labels)


def test_same_size_cache_tampering_is_detected_and_rebuilt(tmp_path):
    src=tmp_path/'tiny.npz';_small_mnist(src)
    cache=prepare_mnist_cache(src, tmp_path/'cache')
    shard=cache/'digit-4.bin';original=shard.read_bytes()
    tampered=bytearray(original);tampered[0]^=1;shard.write_bytes(tampered)
    assert shard.stat().st_size==len(original)
    # The source dataset is intact; a corrupted derived shard must not persist.
    prepare_mnist_cache(src,cache)
    assert shard.read_bytes()==original


def test_sensor_sampling_rejects_tampered_selected_shard(tmp_path):
    import random
    src=tmp_path/'tiny.npz';_small_mnist(src)
    cache=prepare_mnist_cache(src,tmp_path/'cache')
    shard=cache/'digit-2.bin';raw=bytearray(shard.read_bytes());raw[1]^=0xff;shard.write_bytes(raw)
    with pytest.raises(OSError,match='integrity mismatch'):
        class_samples(src,2,random.Random(3),1,cache_path=cache)


def test_tampered_mobility_trace_refused(tmp_path):
    plan=Orchestrator().prepare({'num_ch':1,'num_sn':1,
        'runtime':{'duration_s':1},'mobility':{'model':'static',
        'initial_topology':'manual','manual_positions':{'CH-1':[10,10],'SN-1':[12,10]}}})
    file=tmp_path/'plan.json';plan.save(str(file))
    trace=tmp_path/'mobility.bin';payload=bytearray(trace.read_bytes())
    struct.pack_into('<d',payload,HEADER.size,11.0);trace.write_bytes(payload)
    loaded=EmulationPlan.load(str(file))
    with pytest.raises(ValueError,match='digest mismatch'):
        with loaded.open_trace():
            pass


def test_unreachable_emission_respects_shared_radio_airtime():
    class Status:
        def send(self,v):pass
    plan=Orchestrator().prepare({'num_ch':1,'num_sn':1,
        'runtime':{'duration_s':3.0},
        'link':{'range_mode':'explicit','max_range_m':5.0,
                'max_data_rate_bps':8000.0,'ipdv_enabled':False},
        'mobility':{'model':'static','initial_topology':'manual',
                   'manual_positions':{'CH-1':[0,0],'SN-1':[100,100]},
                   'area_width_m':200,'area_height_m':200}})
    plan.epoch_start=time.time()-0.1
    engine=_RadioEngine(plan.nodes[0],plan,0,Status())
    try:
        for i in range(20):
            req={'id':i,'data':b'x'*1000,'bits':8000,
                 'destination':plan.nodes[1].address,'broadcast':False,
                 'target':'SN-1','header':None,'queued_at_mono':time.monotonic(),
                 'emitted':False,'outstanding':0}
            engine.accept(req);engine.process_due()
        # 20 packets cannot all emit instantly on an 8 kbps shared channel.
        assert engine.metrics.totals['packets_sent']<20
        assert engine.metrics.totals['delivery_drops']==engine.metrics.totals['packets_sent']
    finally:
        engine.close()
