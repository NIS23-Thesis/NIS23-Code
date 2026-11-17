"""Regression guards for per-CH consistent identity across Model Learning widgets."""
from pathlib import Path
import json
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]/'clive'/'visualizer'/'static'


def test_colors_use_ch_identity_not_filtered_series_position():
    src=(ROOT/'learning.js').read_text()
    assert 'function chColor(name)' in src
    assert 'const color=chColor(node.name)' in src  # per-CH distribution
    assert 'ctx.strokeStyle=chColor(n.name)' in src  # line chart
    assert 'const color=chColor(n.name)' in src  # tooltip
    assert '${chMark(n.name)}' in src  # quality table
    assert '${chMark(p.node)}' in src  # history
    assert 'palette[i%palette.length]' not in src
    assert 'data-focus-ch' in src  # chart-scoped legend interaction


def test_20_cluster_heads_have_static_distinct_colors():
    node=shutil.which('node')
    assert node, 'Node.js is required by the CLIVE UI validation scripts'
    src=(ROOT/'learning.js').read_text()
    beginning=src.index('const CH_PALETTE')
    ending=src.index('let selectedRun=')
    pure=src[beginning:ending]
    js=pure+'\nconsole.log(JSON.stringify(Array.from({length:20},(_,i)=>chColor(`CH-${i+1}`))))'
    result=subprocess.run([node,'-e',js],capture_output=True,text=True,check=True)
    colors=json.loads(result.stdout)
    assert len(colors)==20
    assert len(set(colors))==20
    assert all(color.startswith('#') and len(color)==7 for color in colors)


def test_ch_identities_are_static_outside_chart_and_chart_legends_are_scoped():
    js=(ROOT/'learning.js').read_text()
    css=(ROOT/'learning-dashboard.css').read_text()
    assert '.ch-legend-item' in css
    assert 'aria-pressed' in js  # chart legend selection state
    assert "$('legend').addEventListener('click'" in js
    assert "const select=$('chartCHSelect')" in js
    assert "$('nodeSelect').value=select.value" not in js
    assert 'class="ch-label"' in js
