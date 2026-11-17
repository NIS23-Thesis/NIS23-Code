"""Guard the Model Learning UI revision-to-digit-chart workflow.

The saved API already contains per-revision digit snapshots. These checks ensure
that visualizer revisions remain linked to those stored observations.
"""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / 'clive' / 'visualizer' / 'static'


def test_history_revisions_drive_digit_panel_instead_of_lifetime_counts_only():
    html = (STATIC / 'learning.html').read_text()
    js = (STATIC / 'learning.js').read_text()
    css = (STATIC / 'learning-dashboard.css').read_text()
    for element in ['distributionHeading', 'revisionChartControls', 'revisionDigitMode',
                    'clearRevision', 'history']:
        assert f'id="{element}"' in html
    assert 'function selectRevision(row)' in js
    assert 'function sameRevision(point,selection)' in js
    assert "$('history').addEventListener('click'" in js
    assert 'digit_counts_in_training' in js
    assert 'digit_counts_before_training' in js
    assert 'digit_counts_since_previous_training' in js
    assert 'revision-history-row' in js
    assert '.revision-peer-message' in css


def test_training_only_revision_snapshot_does_not_conflate_peer_aggregation():
    html = (STATIC / 'learning.html').read_text()
    js = (STATIC / 'learning.js').read_text()
    for mode in ('used', 'before', 'new'):
        assert f'value="{mode}"' in html
    assert "point?.cause==='peer_aggregation'" in js
    assert 'No new local training occurred in this revision' in js
    assert 'Digit snapshot not recorded for this revision' in js
    assert 'Return digit chart to full-run totals' in html
