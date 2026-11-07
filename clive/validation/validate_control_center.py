#!/usr/bin/env python3
"""Regression suite for CLIVE controller and first-observation delivery order."""
from __future__ import annotations
import json
import os
import socket
import uuid
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

CLIVE = Path(__file__).resolve().parents[1]
ROOT = CLIVE.parent
sys.path.insert(0, str(CLIVE))
sys.path.insert(0, str(ROOT))

from control_center import DEFAULT_SCENARIO, validated_scenario, presets
from flame.communication.communication_module import CommunicationModule
from flame.communication.message import PacketType
from flame.communication.parser import serialize_message
from flame.neighbor.neighbor_module import NeighborModule, AssociationState

class Sink:
    def __init__(self): self.sent=[]
    def sendto(self, payload, address): self.sent.append((payload,address));return len(payload)
    def close(self): pass

class SensorPromotionTest(unittest.TestCase):
    def test_first_reassembled_sensor_is_associated_before_app_callback(self):
        n=NeighborModule(model_id=1)
        address=('127.0.0.3',27062)
        seen=[]
        sock=Sink()
        module=CommunicationModule(sock,role=1,auto_start=False,poll_socket=False,
             neighbor_module=n,route_handler=lambda pkt: seen.append((pkt, n.get_neighbor(*address).association_state)))
        try:
            for msg in [
                {'packet_type':PacketType.JOIN,'role':0,'sequence_number':1,'join_id':100,'model_id':1},
                {'packet_type':PacketType.SENSOR_DATA,'role':0,'sequence_number':2,'data_id':4,
                 'sensor_type':1,'block_number':0,'last_block_number':1,'payload':b'first-'},
                {'packet_type':PacketType.SENSOR_DATA,'role':0,'sequence_number':2,'data_id':4,
                 'sensor_type':1,'block_number':1,'last_block_number':1,'payload':b'observation'},
            ]:
                module.receive_datagram(serialize_message(msg),address)
                module._drain_once()
                if msg['packet_type']==PacketType.JOIN:
                    self.assertEqual(n.get_neighbor(*address).association_state,AssociationState.JOIN_PENDING)
                if msg.get('block_number')==0:
                    self.assertEqual(n.get_neighbor(*address).association_state,AssociationState.JOIN_PENDING)
            sensors=[s for s in seen if s[0]['packet_type']==PacketType.SENSOR_DATA]
            self.assertEqual(len(sensors),1)
            self.assertEqual(sensors[0][0]['payload'],b'first-observation')
            self.assertEqual(sensors[0][1], AssociationState.ASSOCIATED)
        finally:module.stop()

    def test_unknown_sensor_does_not_become_associated(self):
        n=NeighborModule(model_id=1); seen=[]
        module=CommunicationModule(Sink(),role=1,auto_start=False,poll_socket=False,
             neighbor_module=n,route_handler=lambda pkt:seen.append(pkt))
        try:
            source=('127.0.0.8',27062)
            msg={'packet_type':PacketType.SENSOR_DATA,'role':0,'sequence_number':2,'data_id':1,
                 'sensor_type':1,'block_number':0,'last_block_number':0,'payload':b'a'}
            module.receive_datagram(serialize_message(msg),source)
            module._drain_once()
            self.assertIsNone(n.get_neighbor(*source))
            # The existing transport callback still delivers bytes for an unknown
            # sensor; the CLIVE adapter separately rejects unassociated traffic.
            self.assertEqual(len(seen),1)
        finally:module.stop()

class ServerValidationTest(unittest.TestCase):
    def test_schema_and_manual_nodes(self):
        c=json.loads((CLIVE/'configs/demos/flame-synthetic-transport-and-aggregation-smoke.json').read_text())
        self.assertEqual(validated_scenario(c)['num_sn'],4)
        c['mobility']['manual_positions']['SN-4']=[-1,0]
        with self.assertRaises(ValueError):validated_scenario(c)
        c['mobility']['manual_positions']['SN-4']=[44,46]
        c['mobility']['manual_positions'].pop('SN-1')
        with self.assertRaises(ValueError):validated_scenario(c)

    def test_invalid_top_level_and_limits(self):
        c=json.loads((CLIVE/'configs/demos/flame-synthetic-transport-and-aggregation-smoke.json').read_text());c['bad']=1
        with self.assertRaises(ValueError):validated_scenario(c)
        c.pop('bad');c['num_ch']=201
        with self.assertRaises(ValueError):validated_scenario(c)

    def test_full_control_http_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix='clive-control-test-') as work:
            def free_port():
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0))
                    return sock.getsockname()[1]
            port=free_port()
            viz_port=free_port()
            while viz_port==port:viz_port=free_port()
            preset_name='regression GUI '+uuid.uuid4().hex[:10]
            preset_slug=preset_name.lower().replace(' ','-')
            proc=subprocess.Popen([sys.executable,'-u',str(CLIVE/'control_center.py'),
                 '--port',str(port),'--visualizer-port',str(viz_port),'--results',str(Path(work)/'results'), '--quiet'],
                 cwd=CLIVE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
            base=f'http://127.0.0.1:{port}'
            def request(path,data=None):
                req=Request(base+path,data=None if data is None else json.dumps(data).encode(),
                  headers={} if data is None else {'Content-Type':'application/json'})
                try:
                    with urlopen(req,timeout=4) as resp:return resp.status,json.loads(resp.read())
                except HTTPError as exc:return exc.code,json.loads(exc.read())
            try:
                for _ in range(70):
                    try:status,b=request('/api/bootstrap');break
                    except Exception:time.sleep(.1)
                else: self.fail(f'Control center did not answer /api/bootstrap; process_status={proc.poll()}')
                self.assertEqual(status,200)
                self.assertTrue(b['presets']);self.assertIn('flame',b['defaults'])
                # The entire frontend must be served by the same backend.
                with urlopen(base+'/',timeout=3) as page:
                    self.assertIn(b'CLIVE',page.read())
                with urlopen(base+'/app.js',timeout=3) as page:
                    self.assertIn(b'function renderSettings',page.read())
                s=json.loads((CLIVE/'configs/demos/flame-synthetic-transport-and-aggregation-smoke.json').read_text())
                s['num_ch']=1;s['num_sn']=1
                s['runtime']['duration_s']=2.2
                s['runtime']['startup_delay_s']=.5
                s['mobility']['manual_positions']={'CH-1':[40,40],'SN-1':[41,41]}
                s['flame']['hello_interval_s']=.2
                s['flame']['sample_interval_s']=1.25
                s['flame']['expected_observations']=1
                s['flame']['training_threshold']=1.0
                code,result=request('/api/validate',{'scenario':s});self.assertEqual(code,200,result)
                code,result=request('/api/validate',{'scenario':{**s,'num_ch':999}})
                self.assertEqual(code,400)
                code,result=request('/api/save',{'scenario':s,'name':preset_name})
                self.assertEqual(code,200,result)
                preset_id=result['id']
                code,result=request('/api/scenario/'+preset_id)
                self.assertEqual(code,200);self.assertEqual(result['scenario'],s)
                code,r=request('/api/start',{'scenario':s,'name':'controller regression'})
                self.assertEqual(code,200,r);self.assertTrue(r['running'])
                code,r=request('/api/start',{'scenario':s,'name':'second run'})
                self.assertEqual(code,400,'Must disallow concurrent experiments')
                for _ in range(160):
                    time.sleep(.12)
                    code,st=request('/api/status')
                    if not st['running']:break
                else: self.fail('CLIVE did not complete within 20s')
                self.assertEqual(st['status'],'complete',st['log'])
                self.assertTrue(st['run'],st)
                code,details=request('/api/run/'+st['run'])
                self.assertEqual(code,200);self.assertTrue(details['metrics']['success'])
                self.assertEqual(details['flame']['CH-1']['counters'].get('sensor_received'),1,
                     'First and only sensor sample must reach CH after JOIN')
                self.assertEqual(details['flame']['CH-1']['counters'].get('unassociated_sensor_rejected',0),0)
                # A longer run must be stoppable and terminate all experiment children.
                s['runtime']['duration_s']=60.0
                code,r=request('/api/start',{'scenario':s,'name':'stoppable'})
                self.assertEqual(code,200)
                time.sleep(.8)
                code,r=request('/api/stop',{})
                self.assertEqual(code,200,r);self.assertFalse(r['running']);self.assertEqual(r['status'],'stopped')
            finally:
                saved=CLIVE/'configs/gui'/(preset_slug+'.json')
                if saved.exists():saved.unlink()
                if proc.poll() is None:
                    os.killpg(proc.pid,signal.SIGTERM)
                    try:proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                if proc.stderr:proc.stderr.close()

if __name__=='__main__':unittest.main(verbosity=2)
