import math

import pytest

from conversion_lift_test import lift_intervals, two_proportion_z_test


def test_matches_hand_computed_values():
    # 30/1000 vs 20/1000: pooled p = 0.025, se = sqrt(0.025 * 0.975 * 0.002)
    p1, p2, z, p_value = two_proportion_z_test(30, 1000, 20, 1000)
    assert (p1, p2) == (0.03, 0.02)
    assert z == pytest.approx(0.01 / math.sqrt(0.025 * 0.975 * 0.002))
    assert z == pytest.approx(1.4322, abs=1e-4)
    assert p_value == pytest.approx(0.0760, abs=1e-4)


def test_untestable_inputs_return_none():
    assert two_proportion_z_test(0, 0, 5, 100) is None
    assert two_proportion_z_test(5, 100, 0, 0) is None
    assert two_proportion_z_test(0, 100, 0, 100) is None  # se == 0


def test_intervals_contain_point_estimates():
    diff, rel = lift_intervals(300, 100_000, 200, 100_000)
    assert diff[0] < 0.001 < diff[1]
    assert rel[0] < 0.5 < rel[1]


def test_relative_interval_undefined_with_zero_count():
    _, rel = lift_intervals(0, 1000, 10, 1000)
    assert rel is None
