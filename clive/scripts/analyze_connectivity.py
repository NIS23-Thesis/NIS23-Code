#!/usr/bin/env python3
"""Explain CH connectivity and FL completion for one CLIVE result directory.

Useful because process success cannot imply successful model exchange. This
reads CLIVE traces and results only; it never changes the FLAME implementation.
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from orchestrator import EmulationPlan
from nodesocket import link_conditions


def report(run_dir):
    run_dir=Path(run_dir)
    plan=EmulationPlan.load(str(run_dir/'plan.json'))
    heads=[node for node in plan.nodes if node.name.startswith('CH-')]
    with plan.open_trace() as trace:
        positions={n.name:trace.position(0,n.name) for n in heads}
    edges=[]
    neighbors={n.name:set() for n in heads}
    for index,src in enumerate(heads):
        for dst in heads[index+1:]:
            distance=math.dist(positions[src.name],positions[dst.name])
            a=link_conditions(distance,plan.link_config,plan.link_config.tx_range_m(src))
            b=link_conditions(distance,plan.link_config,plan.link_config.tx_range_m(dst))
            if a['reachable'] and b['reachable']:
                neighbors[src.name].add(dst.name)
                neighbors[dst.name].add(src.name)
                edges.append({'source':src.name,'destination':dst.name,'distance_m':round(distance,2),
                              'packet_loss_probability':max(a['plr'],b['plr'])})
    components=[]
    unseen=set(neighbors)
    while unseen:
        start=min(unseen)
        seen={start};pending=[start]
        while pending:
            peer=pending.pop()
            for near in neighbors[peer]-seen:
                seen.add(near);pending.append(near)
        components.append(sorted(seen));unseen-=seen
    data=json.loads((run_dir/'metrics.json').read_text())
    summary=data.get('flame_outcome') or {}
    return {'scenario':str(run_dir),'cluster_head_count':len(heads),
            'initial_undirected_ch_links':edges,'initial_ch_connected_components':components,
            'isolated_cluster_heads':[n for n,peers in neighbors.items() if not peers],
            'radio_range_model':plan.link_config.range_mode,
            'modeled_effective_rssi_cutoff_m':round(plan.link_config.rssi_reachability_range_m(),2),
            'worker_process_success':data.get('success'),
            'model_transfer_completion':{'started':summary.get('model_transfers_started'),
                'completed':summary.get('model_transfers_completed'),'failed':summary.get('model_transfers_failed')},
            'aggregations':summary.get('model_updates_aggregated'),
            'limitations':['Only initial one-hop CH connectivity is computed; it is not a routing or mobility proof.',
                           'Model-transfer failure can have multiple causes; connectivity alone does not isolate ACK defects.']}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('run_dir',type=Path)
    ap.add_argument('--output',type=Path)
    a=ap.parse_args();result=report(a.run_dir);encoded=json.dumps(result,indent=2)
    if a.output:a.output.write_text(encoded+'\n')
    print(encoded)

if __name__=='__main__':main()
