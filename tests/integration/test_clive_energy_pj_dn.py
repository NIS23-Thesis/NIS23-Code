"""CLIVE energy model: pJ coefficient and d**n exponent (FLAME untouched)."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "clive"))
from orchestrator import LinkConfig
from nodesocket import energy_tx, energy_rx, link_conditions


def test_100_pj_amplifier_and_cubic_exponent():
    cfg = LinkConfig(pathloss_exponent=3)
    assert cfg.e_amp_j_per_bit_mn == 100e-12
    bits, distance = 8192, 20.0
    expected = bits * (50e-9 + 100e-12 * distance ** 3)
    assert math.isclose(energy_tx(bits, distance, cfg), expected, rel_tol=1e-12)
    assert math.isclose(energy_rx(bits, cfg), bits * 50e-9, rel_tol=1e-12)


def test_exponent_still_varies_with_rssi_model():
    bits, distance = 8192, 20.0
    cfg2 = LinkConfig(pathloss_exponent=2)
    cfg3 = LinkConfig(pathloss_exponent=3)
    assert math.isclose(energy_tx(bits, distance, cfg2), bits*(50e-9 + 100e-12*distance**2))
    assert energy_tx(bits, distance, cfg3) > energy_tx(bits, distance, cfg2)
    assert link_conditions(distance, cfg2)["rssi_dbm"] != link_conditions(distance, cfg3)["rssi_dbm"]


def test_zero_distance_is_circuit_only():
    cfg=LinkConfig()
    assert math.isclose(energy_tx(1024, 0, cfg), 1024*50e-9)


def test_legacy_ambiguous_m2_key_fails_loudly():
    with pytest.raises(ValueError,match="Legacy e_amp_j_per_bit_m2"):
        LinkConfig.from_dict({"e_amp_j_per_bit_m2": 1e-7})


def test_energy_coefficient_round_trip():
    original = LinkConfig(pathloss_exponent=3)
    encoded = original.to_dict()
    assert "e_amp_j_per_bit_m2" not in encoded
    parsed = LinkConfig.from_dict(encoded)
    assert parsed.e_amp_j_per_bit_mn == 100e-12
    assert parsed.pathloss_exponent == 3
