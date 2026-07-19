import math

import pytest

from numeric_oracle import gaussian_llr as oracle_llr
from numeric_test_support import load_numeric


VECTORS = (
    (4.0001, 1.0, 3.2, 120.0),
    (8.5, 0.9, 10.555639, 120.0),
    (49.8, 1.4, 5.7, 120.0),
    (4.0001, 1.0, 3.2, 600.0),
    (8.5, 0.9, 10.555639, 600.0),
    (49.8, 1.4, 5.7, 600.0),
)


@pytest.mark.parametrize("rho,chisq,a_eff,dof", VECTORS)
def test_independent_python_golden(rho, chisq, a_eff, dof):
    numeric = load_numeric()
    actual = numeric.gaussian_llr(rho, chisq, a_eff, dof)
    expected = oracle_llr(rho, chisq, a_eff, dof)
    assert math.isclose(actual, expected, rel_tol=2e-15, abs_tol=2e-13)


@pytest.mark.parametrize(
    "rho,chisq,a_eff,dof",
    [(8.0, 1.0, None, 120.0), (8.0, 1.0, 0.0, 120.0),
     (8.0, 1.0, float("nan"), 120.0), (8.0, 1.0, 1.0, 681.0),
     (float("inf"), 1.0, 1.0, 120.0)],
)
def test_invalid_llr_inputs_fail_closed(rho, chisq, a_eff, dof):
    numeric = load_numeric()
    with pytest.raises(ValueError):
        numeric.gaussian_llr(rho, chisq, a_eff, dof)


def test_bank_mapping_controls_template_llr():
    numeric = load_numeric()
    assert math.isclose(
        numeric.gaussian_llr_for_template(8.0, 1.1, 6.0, 99, 120.0),
        numeric.gaussian_llr(8.0, 1.1, 6.0, 120.0),
        rel_tol=0.0, abs_tol=0.0)
    with pytest.raises(ValueError, match="conflicts"):
        numeric.gaussian_llr_for_template(8.0, 1.1, 6.0, 100, 120.0)
