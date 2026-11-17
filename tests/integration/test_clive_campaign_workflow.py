"""Regression tests for CLIVE evaluation harness; never modifies flame/."""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'clive' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / 'clive'))

from generate_loss_stress import build_scenarios
from generate_thesis_matrix import MOBILITY, DENSITY, FREQUENCY, AREA
from run_evaluations import scenario_files, write_campaign_reports
from calibrate_nodesocket_udp import plan_for
from nodesocket import link_conditions


def test_full_matrix_contains_108_distinct_scenarios():
    files = scenario_files('thesis_matrix')
    assert len(files) == 4 * 3 * 3 * 3 == 108
    assert len({f.stem for f in files}) == 108
    assert all(json.loads(f.read_text())['runtime']['duration_s'] >= 300 for f in files)


def test_sustained_loss_profiles_genuine_mnist_and_expected_radio_conditions():
    with tempfile.TemporaryDirectory() as td:
        result = build_scenarios(Path(td))
        assert result['status'] == 'configured_not_executed'
        assert len(result['scenarios']) == 3
        for row in result['scenarios']:
            c = json.loads((Path(td) / row['scenario']).read_text())
            assert c['runtime']['duration_s'] == 300
            assert c['flame']['trainer'] == c['flame']['sample_source'] == 'mnist'
            assert c['flame']['track_learning'] is False
            assert c['logging']['protocol_events'] is True
            assert row['modeled_link_plr'] in {0, .02, .10}
            a = c['mobility']['manual_positions']['CH-1']
            b = c['mobility']['manual_positions']['CH-2']
            assert b[0] - a[0] == row['distance_m']


def test_sustained_loss_requires_full_duration():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError, match='300'):
            build_scenarios(Path(td), duration_s=20)


def test_calibration_uses_node_socket_link_model_not_external_native_ping():
    plan, a, b = plan_for(40.0, 1.0, 810, 80000)
    result = link_conditions(40, plan.link_config)
    assert result['plr'] == .10 and result['reachable']
    assert plan.nodes == [a, b]
    assert plan.link_config.max_data_rate_bps == 80000


def test_campaign_report_counts_failures_and_missing_cases_without_fabrication():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a = root / 'a'; a.mkdir()
        (a / 'metrics.json').write_text('{}')
        rows = [
            {'scenario': 'case-a.json', 'seed': 810, 'run_dir': str(a), 'status': 'completed',
             'execution_success': True, 'protocol_success': False},
            {'scenario': 'case-b.json', 'seed': 810, 'status': 'wall_clock_timeout'},
        ]
        (root / 'evaluation-index.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        report = write_campaign_reports(root, {('case-a.json', 810), ('case-b.json', 810), ('case-c.json', 810)})
        assert report['attempts_recorded'] == 2
        assert report['planned_scenario_seed_pairs'] == 3
        assert report['missing_scenario_seed_pairs'] == 1
        assert report['statuses_latest_attempt'] == {'completed': 1, 'wall_clock_timeout': 1}
        assert report['protocol_success_of_completed_executions'] == {'false': 1}
        assert (root / 'campaign-results.csv').exists()


def test_missing_or_bad_index_is_not_treated_as_completed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / 'evaluation-index.jsonl').write_text('garbled\n')
        report = write_campaign_reports(root, {('x.json', 811)})
        assert report['attempts_recorded'] == 1
        assert report['statuses_all_attempts']['corrupt_index_record'] == 1
        assert report['missing_scenario_seed_pairs'] == 1


def test_short_thesis_runs_require_explicit_diagnostic_opt_in():
    cmd = [sys.executable, str(SCRIPTS / 'run_evaluations.py'), 'loss_stress', '--duration', '8', '--limit', '1']
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    assert p.returncode != 0
    assert 'allow-short-thesis-smoke' in p.stderr


def test_runner_report_only_does_not_launch_any_scenario():
    with tempfile.TemporaryDirectory() as td:
        cmd = [sys.executable, str(SCRIPTS / 'run_evaluations.py'), 'loss_stress', '--report-only', '--results', td]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        assert p.returncode == 0, p.stderr
        report = json.loads((Path(td) / 'campaign-summary.json').read_text())
        assert report['attempts_recorded'] == 0
        assert report['planned_scenario_seed_pairs'] == 3
        assert report['missing_scenario_seed_pairs'] == 3


def test_recover_only_complete_artifacts_and_preserve_supervisor_uncertainty():
    from run_evaluations import recover_interrupted_artifacts
    stress = scenario_files('loss_stress')[0]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case = root / stress.stem / 'seed-810'
        run = case / 'run-1'; run.mkdir(parents=True)
        used = json.loads(stress.read_text(encoding='utf-8'))
        (case / 'scenario-used.json').write_text(json.dumps(used))
        (run / 'metrics.json').write_text(json.dumps({
            'execution_success': True, 'protocol_success': False,
            'flame_outcome': {'model_transfers_started': 3, 'model_transfers_completed': 1}}))
        (run / 'run-manifest.json').write_text(json.dumps({'status': 'complete', 'execution_success': True}))
        assert recover_interrupted_artifacts(root, [stress]) == 1
        assert recover_interrupted_artifacts(root, [stress]) == 0
        record = json.loads((root / 'evaluation-index.jsonl').read_text().strip())
        assert record['status'] == 'recovered_artifact'
        assert record['protocol_success'] is False
        assert 'runner exit was not observed' in record['warning']


def test_diagnostic_frequency_override_requires_explicit_optin():
    p = subprocess.run([sys.executable, str(SCRIPTS / 'run_evaluations.py'),
                        'loss_stress', '--duration', '12', '--limit', '1',
                        '--diagnostic-sample-interval-s', '1.5'],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode != 0
    assert 'diagnostic' in p.stderr or 'allow-short-thesis-smoke' in p.stderr
