import math

from numeric_oracle import BETAS
from numeric_test_support import load_numeric


def test_required_64_point_beta_grid():
    numeric = load_numeric()
    actual = numeric.beta_grid()
    assert len(actual) == 64
    assert actual == list(BETAS)
    assert math.isclose(actual[0], 0.003, rel_tol=0.0, abs_tol=1e-15)
    assert math.isclose(actual[-1], 0.192, rel_tol=0.0, abs_tol=1e-15)
