import math

import pytest

from numeric_oracle import calculated_far as oracle_far
from numeric_test_support import load_numeric


@pytest.mark.parametrize(
    "ranks,livetime,query",
    [([1.0, 2.0, 2.0, 3.0], 100.0, 2.0),
     ([1.0, 2.0, 2.0, 3.0], 100.0, 2.0000001),
     ([1.0, 2.0, 2.0, 3.0], 100.0, 99.0),
     ([-5.0], 7.0, -10.0),
     ([-5.0], 7.0, 100.0)],
)
def test_support_count_and_one_count_floor(ranks, livetime, query):
    numeric = load_numeric()
    actual = numeric.calculated_far(ranks, livetime, query)
    expected = oracle_far(ranks, livetime, query)
    assert math.isclose(actual, expected, rel_tol=0.0, abs_tol=0.0)
    assert actual > 0.0


@pytest.mark.parametrize(
    "ranks,livetime,query",
    [([], 10.0, 1.0), ([1.0], 0.0, 1.0), ([1.0], -1.0, 1.0),
     ([float("nan")], 10.0, 1.0), ([1.0], 10.0, float("inf"))],
)
def test_invalid_calculated_far_inputs_fail_closed(ranks, livetime, query):
    numeric = load_numeric()
    with pytest.raises(ValueError):
        numeric.calculated_far(ranks, livetime, query)
