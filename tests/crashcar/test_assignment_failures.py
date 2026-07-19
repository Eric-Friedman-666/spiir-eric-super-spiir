import pytest

from numeric_test_support import load_numeric


def test_sparse_strict_tail_has_no_substitute():
    numeric = load_numeric()
    result = numeric.evaluate_far([5.0], 100.0, 5.0001)
    assert result["calculated_far"] == 0.01
    assert result["assigned_far"] is None
    assert result["status"] == "failed_tail_fit"


def test_nonnegative_tail_slope_has_no_substitute():
    numeric = load_numeric()
    # At livetime=50 every empirical FAR is above the -2 anchor.  Anchoring
    # the sole selected tail point cannot produce a finite negative slope.
    result = numeric.evaluate_far([1.0, 2.0], 50.0, 3.0)
    assert result["assigned_far"] is None
    assert result["status"] == "failed_tail_fit"


@pytest.mark.parametrize(
    "ranks,livetime,query",
    [([], 100.0, 1.0), ([1.0], 0.0, 1.0),
     ([float("nan")], 100.0, 1.0), ([1.0], 100.0, float("nan"))],
)
def test_missing_support_livetime_or_rank_raises(ranks, livetime, query):
    numeric = load_numeric()
    with pytest.raises(ValueError):
        numeric.evaluate_far(ranks, livetime, query)
