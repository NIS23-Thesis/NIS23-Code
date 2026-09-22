#!/usr/bin/env python3
"""Build a self-contained ``visualizer.html`` from the CLIVE emulation data.

Reads ``plan.json`` and everything under ``results/`` (per-node metrics and
protocol events) and injects the data into ``visualizer.template.html`` so the
resulting ``visualizer.html`` can be opened directly in a browser.

Usage:
    python3 build_visualizer.py
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "visualizer.template.html")
OUTPUT = os.path.join(HERE, "visualizer.html")
RESULTS = os.path.join(HERE, "results")

# In the template the data sits on this exact line:
#     const CLIVE_DATA = /*__CLIVE_DATA__*/null;
DATA_PLACEHOLDER = "/*__CLIVE_DATA__*/null"


def load_jsonl(path: str):
    events = []
    if not os.path.exists(path):
        return events
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def main() -> None:
    with open(os.path.join(HERE, "plan.json"), "r", encoding="utf-8") as f:
        plan = json.load(f)

    metrics: dict = {}
    events: dict = {}

    if os.path.isdir(RESULTS):
        for name in sorted(os.listdir(RESULTS)):
            if name.endswith(".metrics.json"):
                node = name[: -len(".metrics.json")]
                with open(os.path.join(RESULTS, name), "r", encoding="utf-8") as f:
                    metrics[node] = json.load(f)
            elif name.endswith(".events.jsonl"):
                node = name[: -len(".events.jsonl")]
                events[node] = load_jsonl(os.path.join(RESULTS, name))

    payload = {"plan": plan, "metrics": metrics, "events": events}
    data_js = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        template = f.read()

    if DATA_PLACEHOLDER not in template:
        raise SystemExit(
            f"Placeholder {DATA_PLACEHOLDER!r} not found in {TEMPLATE}"
        )

    html = template.replace(DATA_PLACEHOLDER, data_js)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote {OUTPUT} ({len(html):,} bytes, ~{len(html) // 1024} KB)")
    print(
        f"  nodes: {len(plan.get('nodes', []))}, "
        f"metrics: {len(metrics)}, event logs: {len(events)}"
    )


if __name__ == "__main__":
    main()
