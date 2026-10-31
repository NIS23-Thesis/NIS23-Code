"""Generate a reproducible 20CH+200SN capacity test without changing FLAME logic.

Clusters are isolated by radio distance. This tests NodeSocket, discovery,
association, real MNIST samples, and SENSOR_DATA delivery; it does NOT test
multi-cluster model exchange or simultaneous PyTorch training.
"""
import json
from pathlib import Path
positions={}
for j in range(20):
    x=50.0+60.0*j
    positions[f'CH-{j+1}']=[x,80.0]
for i in range(200):
    group=i//10
    slot=i%10
    x=50.0+60.0*group
    positions[f'SN-{i+1}']=[x+((slot%5)-2)*1.5,80.0+(slot//5)*2.0]
scenario={
 'num_ch':20,'num_sn':200,
 'runtime':{'duration_s':6.0,'seed':810,'radio_execution':'thread','startup_delay_s':10.0,
            'readiness_timeout_s':240.0,'start_tolerance_s':10.0,'worker_grace_s':20.0,
            'io_poll_interval_s':0.02},
 'link':{'range_mode':'explicit','max_range_m':22.0,'max_data_rate_bps':54000000},
 'mobility':{'model':'static','initial_topology':'manual','manual_positions':positions,
             'area_width_m':1250.0,'area_height_m':160.0,'tick_interval':0.5},
 'logging':{'packet_events':False,'protocol_events':False},
 'flame':{'sample_source':'mnist','trainer':'mean_pixel',
          'expected_observations':10000,'training_threshold':1.0,
          'hello_interval_s':0.75,'sample_interval_s':2.0,'service_interval_s':0.05},
}
Path(__file__).with_name('20ch_200sn_capacity.json').write_text(json.dumps(scenario,indent=2))
