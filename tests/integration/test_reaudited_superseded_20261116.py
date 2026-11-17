"""Do not report resends that have no available cached model payload."""
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'clive'))
from flame_protocol import FlameProtocol
from flame.communication.message import PacketType


def test_superseded_model_retry_is_terminal_not_scheduled():
    peer=('127.0.0.3',27062)
    p=object.__new__(FlameProtocol)
    p.is_ch=True;p.model_id=1
    p.s={'model_retry_limit':2,'model_retry_backoff_s':1.0}
    p.stats=Counter();events=[]
    p.node=SimpleNamespace(elapsed=lambda:5.0,events=SimpleNamespace(append=events.append),
                           connection_events=SimpleNamespace(append=lambda event:None))
    p._model_transfer_status={(peer,1):'pending'}
    p._model_retry_counts=Counter();p._model_retry_after={}
    p._cached_update={'update_id':2,'packet_type':PacketType.MODEL_UPDATE}
    p._sent_updates={(peer,2)}
    p._track_model_transfer({'type':'transfer_failed','packet_type':int(PacketType.MODEL_UPDATE),
                            'destination':peer,'message_id':1,'reason':'retry_exhausted'})
    assert p.stats['model_retry_scheduled']==0
    assert p.stats['model_retry_superseded']==1
    assert p._model_transfer_status[(peer,1)]=='superseded'
    assert (peer,1) not in p._model_retry_after
