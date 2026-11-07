"""Integration-only thesis compliance regression tests; never alters flame/."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from generate_thesis_matrix import MOBILITY, DENSITY, FREQUENCY, AREA, generate, make_scenario
from mnist_topology import spatial_digit
from flame_protocol import _MNISTSource, _configuration
from types import SimpleNamespace


class ThesisAlignmentTests(unittest.TestCase):
    def test_matrix_is_full_factorial_and_300_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = generate(tmp, seed=23)
            cases = manifest['scenarios']
            self.assertEqual(len(cases), 4*3*3*3)
            self.assertEqual(len({(x['mobility'],x['density'],x['frequency'],x['area']) for x in cases}),108)
            import json
            for row in cases:
                sc = json.loads((Path(tmp) / row['scenario_file']).read_text())
                self.assertEqual(sc['runtime']['duration_s'],300)
                self.assertEqual(sc['flame']['trainer'],'mnist')
                self.assertEqual(sc['flame']['mnist_distribution'],'spatial_clusters')
                self.assertEqual(sc['flame']['sample_interval_s'], FREQUENCY[row['frequency']])
                self.assertEqual(sc['num_ch'],DENSITY[row['density']][0])
                self.assertEqual(sc['num_sn'],DENSITY[row['density']][1])
                self.assertEqual(sc['mobility']['area_width_m'], AREA[row['area']])
    def test_short_methodology_scenarios_rejected(self):
        with self.assertRaises(ValueError):
            make_scenario('high','low','low','small',duration_s=299)
    def test_spatial_class_is_independent_of_sensor_id(self):
        settings = { 'mnist_distribution':'spatial_clusters', 'sample_source':'synthetic', 'mnist_samples_per_sensor':2 }
        a=_MNISTSource('SN-1',810,settings,initial_position=(20,20),area_size=(200,200))
        b=_MNISTSource('SN-21',810,settings,initial_position=(22,21),area_size=(200,200))
        c=_MNISTSource('SN-31',810,settings,initial_position=(189,189),area_size=(200,200))
        self.assertEqual(a.digit,b.digit)
        self.assertNotEqual(a.digit,c.digit)
        self.assertEqual(a.collect()[-1],b.collect()[-1])
        self.assertEqual(spatial_digit((200,200),(200,200)),9)
        self.assertEqual(spatial_digit((0,0),(200,200)),0)
    def test_capacity_preset_is_schema_loadable_but_declares_proxy_workload(self):
        from control_center import validated_scenario
        import json
        path=ROOT/'configs/demos/capacity-220-nodes-transport-only-no-neural-training.json'
        scenario=json.loads(path.read_text())
        self.assertEqual(scenario['flame']['trainer'],'mean_pixel')
        self.assertEqual(scenario['num_ch']+scenario['num_sn'],220)
        validated_scenario(scenario)
    def test_gui_never_exposes_metadata_as_a_scenario(self):
        from control_center import presets
        names={x['id'] for x in presets()}
        self.assertFalse(any(x.endswith('/manifest.json') or x == 'all_options.json' for x in names))
        # The presentation launcher intentionally shows exactly five real MNIST demos.
        # Legacy diagnostics and the 108-case evaluation matrix remain CLI-accessible.
        from control_center import validated_scenario
        import json
        visible=[x for x in presets() if x['id'].startswith('demos/')]
        self.assertEqual(len(visible), 5)
        self.assertEqual({x['id'].split('/')[1].split('-')[1] for x in visible},
                         {'01','02','03','04','05'})
        for entry in visible:
            scenario=json.loads((ROOT / 'configs' / entry['id']).read_text())
            validated_scenario(scenario)
            self.assertEqual(scenario['flame']['trainer'],'mnist')
            self.assertEqual(scenario['flame']['sample_source'],'mnist')
            self.assertTrue(scenario['flame']['track_learning'])
        self.assertNotIn('demos/capacity-220-nodes-transport-only-no-neural-training.json',names)
        self.assertFalse(any(x.startswith('evaluations/') for x in names))
    def test_mnist_sample_pool_has_no_duplicate_records(self):
        from mnist_cache import class_samples
        import random
        records = class_samples(ROOT.parent / 'mnist' / 'mnist.npz', 7, random.Random(112), 100)
        self.assertEqual(100, len(set(records)))
    def test_accuracy_output_is_opt_in_and_mnist_only(self):
        p=SimpleNamespace(flame_settings={'evaluate_accuracy':True,'trainer':'mean_pixel'})
        with self.assertRaises(ValueError):
            _configuration(p)
        settings=_configuration(SimpleNamespace(flame_settings={}))
        self.assertFalse(settings['evaluate_accuracy'])
        self.assertEqual(settings['mnist_distribution'],'spatial_clusters')


if __name__=='__main__': unittest.main(verbosity=2)
