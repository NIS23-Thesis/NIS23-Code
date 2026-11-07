#!/usr/bin/env python3
"""Bounded end-to-end smoke of demo presets; explicit non-evaluation runs.

Run per-demo CLIVE workers with SHORTENED durations, retain per-case JSON status,
and never call these runs 300-second thesis evaluation. The 220-node transport
capacity preset is skipped by default on memory-limited machines; opt in.
"""
from __future__ import annotations
import argparse, json, os, signal, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT/'configs/demos'

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--results', type=Path, required=True)
    a.add_argument('--resume', action='store_true', help='skip previously completed presets, retain per-case evidence')
    a.add_argument('--include-220', action='store_true', help='danger: can exhaust RAM, do not equate to neural training')
    a.add_argument('--seconds', type=float, default=4)
    a.add_argument('--case-timeout', type=int, default=55)
    args=a.parse_args()
    args.results.mkdir(parents=True,exist_ok=True)
    previous = (args.results/'results.json')
    outcomes=json.loads(previous.read_text()) if args.resume and previous.is_file() else []
    completed={entry['preset'] for entry in outcomes if entry['status'] in {'PASS', 'SKIPPED_RESOURCE_RISK'}}
    for preset in sorted(DEMO.glob('*.json')):
        config=json.loads(preset.read_text())
        label=preset.stem
        if preset.name in completed:
            print('[ALREADY CHECKED]',preset.name,flush=True)
            continue
        if config['num_ch']+config['num_sn']>200 and not args.include_220:
            outcomes.append({'preset':preset.name,'status':'SKIPPED_RESOURCE_RISK','reason':'220 processes need more RAM than the 4 GiB test environment; configuration separately validated'})
            print('[SKIPPED RESOURCE]',preset.name,flush=True);continue
        real_training=config.get('flame',{}).get('trainer')=='mnist'
        # These are *smoke* runs. Use each preset's other settings unchanged.
        config['runtime']['duration_s']=min(config['runtime'].get('duration_s',args.seconds),8 if real_training else args.seconds)
        config['runtime']['startup_delay_s']=max(2.5, config['runtime'].get('startup_delay_s',2.0))
        config['runtime']['readiness_timeout_s']=max(40,config['runtime'].get('readiness_timeout_s',30))
        config['runtime']['start_tolerance_s']=max(3.0,config['runtime'].get('start_tolerance_s',.10))
        config['runtime']['worker_grace_s']=max(10,config['runtime'].get('worker_grace_s',5))
        config['runtime']['radio_execution']='thread'  # compact radio; avoids unnecessary OOM in demo matrix
        path=args.results/(label+'.json');path.write_text(json.dumps(config,indent=2)+'\n')
        cmd=[sys.executable,str(ROOT/'clive.py'),'--scenario',str(path),'--results',str(args.results/'runs')]
        t0=time.monotonic()
        log=args.results/(label+'.log')
        with log.open('w') as stream:
            p=subprocess.Popen(cmd,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:exitcode=p.wait(timeout=args.case_timeout);timeout=False
            except subprocess.TimeoutExpired:
                timeout=True;exitcode=124;os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
        duration=round(time.monotonic()-t0,2)
        status='PASS' if exitcode==0 else 'TIMEOUT' if timeout else 'FAILED'
        reason=None
        output=log.read_text(errors='replace')
        runpath=Path(output.strip().splitlines()[-1]) if output.strip() else None
        metric=None
        if runpath and runpath.is_dir() and (runpath/'metrics.json').exists():
            m=json.loads((runpath/'metrics.json').read_text());metric={k:m.get(k) for k in ('success','execution_success','flame_outcome')}
            if not m.get('success'):status='FAILED';reason='worker-process success false'
        outcomes.append({'preset':preset.name,'status':status,'exit_code':exitcode,'duration_wall_s':duration,'run_path':str(runpath) if metric else None,'metrics':metric,'reason':reason})
        print(f'[{status}] {preset.name} exit={exitcode} wall={duration:.1f}s',flush=True)
        (args.results/'results.json').write_text(json.dumps(outcomes,indent=2)+'\n')
    failures=[o for o in outcomes if o['status'] in {'FAILED','TIMEOUT'}]
    print('Completed:',len(outcomes),'passed',sum(o['status']=='PASS' for o in outcomes),'failed',len(failures),'skipped',sum(o['status'].startswith('SKIPPED') for o in outcomes),flush=True)
    if failures:sys.exit(1)

if __name__=='__main__': main()
