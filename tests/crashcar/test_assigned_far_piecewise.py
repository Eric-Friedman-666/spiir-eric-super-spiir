import math

import pytest

from numeric_oracle import evaluate_far as oracle_evaluate
from numeric_test_support import load_numeric


SUPPORT = [float(value) for value in range(200)]
LIVETIME = 10000.0


@pytest.mark.parametrize(
    "query",
    [99.5, math.nextafter(100.0, -math.inf), 100.0,
     math.nextafter(100.0, math.inf), 100.5, 250.0],
)
def test_below_equal_above_tail_matches_independent_oracle(query):
    numeric = load_numeric()
    actual = numeric.evaluate_far(SUPPORT, LIVETIME, query)
    expected = oracle_evaluate(SUPPORT, LIVETIME, query)
    assert actual["r_tail"] == expected["r_tail"] == 100.0
    assert actual["used_tail_fit"] is expected["used_tail_fit"]
    assert math.isclose(actual["calculated_far"], expected["calculated_far"],
                        rel_tol=0.0, abs_tol=0.0)
    assert math.isclose(actual["assigned_far"], expected["assigned_far"],
                        rel_tol=2e-15, abs_tol=1e-300)


def test_equality_uses_matching_background_support_far():
    numeric = load_numeric()
    result = numeric.evaluate_far(SUPPORT, LIVETIME, 100.0)
    assert result["status"] == "assigned_direct"
    assert result["used_tail_fit"] is False
    assert result["assigned_far"] == result["calculated_far"] == 0.01


def test_nonanchor_equality_uses_matching_support_far_then_nextafter_fits():
    numeric = load_numeric()
    support = [float(value) for value in range(20)]
    livetime = 950.0
    equality = numeric.evaluate_far(support, livetime, 10.0)
    assert equality["r_tail"] == 10.0
    assert equality["status"] == "assigned_direct"
    assert equality["used_tail_fit"] is False
    assert equality["calculated_far"] == 10.0 / 950.0
    assert equality["assigned_far"] == 10.0 / 950.0
    assert equality["assigned_far"] != 0.01

    strict_tail = numeric.evaluate_far(
        support, livetime, math.nextafter(10.0, math.inf))
    assert strict_tail["r_tail"] == 10.0
    assert strict_tail["status"] == "assigned_tail_fit"
    assert strict_tail["used_tail_fit"] is True
    assert math.isclose(strict_tail["assigned_far"], 0.01,
                        rel_tol=2e-15, abs_tol=0.0)


def test_first_empirical_index_wins_equal_closest_tail_tie():
    numeric = load_numeric()
    livetime = 100.0 * math.sqrt(2.0)
    result = numeric.tail_model([0.0, 1.0, 2.0], livetime)
    assert result["r_tail"] == 1.0


def test_pretail_sparse_support_uses_its_only_background_point():
    numeric = load_numeric()
    result = numeric.evaluate_far([5.0], 100.0, 5.0)
    assert result["assigned_far"] == result["calculated_far"] == 0.01
    assert result["used_tail_fit"] is False


def test_pretail_midpoint_chooses_lower_llr_conservative_far():
    numeric = load_numeric()
    result = numeric.evaluate_far([0.0, 10.0, 20.0, 30.0], 100.0, 5.0)
    assert result["calculated_far"] == 0.03
    assert result["assigned_far"] == 0.04
    assert result["used_tail_fit"] is False
