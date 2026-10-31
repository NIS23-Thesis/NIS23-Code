#!/usr/bin/env python3
"""Record cgroup peak memory for one CLIVE command; never modifies protocol logic."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--output',required=True)
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1]==['--'] else args.command
cgroup = Path('/sys/fs/cgroup')
reads=lambda name: int((cgroup/name).read_text()) if (cgroup/name).is_file() else 0
base=reads('memory.current')
start=time.monotonic()
peak=base
peak_pids=reads('pids.current')
samples=[]
last_sample=-1
proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
while proc.poll() is None:
    mem=reads('memory.current')
    tasks=reads('pids.current')
    peak=max(peak,mem)
    peak_pids=max(peak_pids,tasks)
    elapsed=time.monotonic()-start
    if int(elapsed)!=last_sample:
        last_sample=int(elapsed)
        samples.append({'time_s':round(elapsed,2),'memory_bytes':mem,'tasks':tasks})
    time.sleep(.15)
out,err=proc.communicate()
peak=max(peak,reads('memory.current'))
r={'command':command,'returncode':proc.returncode,'elapsed_s':round(time.monotonic()-start,3),
   'cgroup_baseline_bytes':base,'cgroup_peak_bytes':peak,
   'cgroup_extra_peak_bytes':peak-base,'peak_cgroup_tasks':peak_pids,
   'samples':samples,'stdout':out[-3000:],'stderr':err[-3500:]}
Path(args.output).write_text(json.dumps(r,indent=2))
print(json.dumps(r, indent=2))
sys.exit(proc.returncode)
