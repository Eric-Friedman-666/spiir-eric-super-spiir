import math
from types import SimpleNamespace

import pytest

from numeric_test_support import load_single_far


def _model(module, weights=None):
    return module.SingleDetectorLikelihoodModel(
        120.0,
        120.0,
        beta_grid=module.crashcar_numeric.beta_grid(),
        beta_weights=weights,
        snr_log_weight=0.5,
        rank_offset=0.0,
    )


def _formal_mapping(module):
    return module.autocorr_power_rows_to_map([{
        "ifo_id": 0,
        "bankid": 0,
        "tmplt_idx": 7,
        "autocorr_power": 2.5,
        "dof": 120.0,
        "ifo": "H1",
        "source_class": "BNS",
    }])


def test_huge_equal_beta_weights_normalize_stably_to_exact_one_over_64():
    module = load_single_far()
    model = _model(module, [1.0e308] * 64)
    assert model.beta_weights == [1.0 / 64.0] * 64
    assert math.fsum(model.beta_weights) == 1.0


@pytest.mark.parametrize(
    "weights",
    [
        [float("inf")] * 64,
        [float("nan")] * 64,
        [0.0] * 64,
        [-1.0] * 64,
        [1.0] * 63 + [float("inf")],
        [1.0] * 63 + [2.0],
    ],
)
def test_invalid_or_unequal_beta_weights_fail_closed(weights):
    module = load_single_far()
    with pytest.raises(ValueError):
        _model(module, weights)


@pytest.mark.parametrize("field", ["rho", "chisq"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_postcoh_feature_is_not_emitted(field, value):
    module = load_single_far()
    values = {"rho": 5.0, "chisq": 1.0}
    values[field] = value
    row = SimpleNamespace(
        ifos="H1",
        snglsnr=[values["rho"], 0.0, 0.0, 0.0],
        chisq=[values["chisq"], 0.0, 0.0, 0.0],
        bankid=0,
        tmplt_idx=7,
    )
    with pytest.raises(ValueError, match="nonfinite detector feature"):
        module.features_from_postcoh_row(
            row,
            ifos=("H1",),
            min_snr=4.0,
            autocorr_power_by_template=_formal_mapping(module),
        )


@pytest.mark.parametrize(
    "rho,chisq",
    [
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (5.0, float("nan")),
        (5.0, float("inf")),
    ],
)
def test_nonfinite_llr_inputs_raise(rho, chisq):
    module = load_single_far()
    model = _model(module)
    with pytest.raises(ValueError):
        model.log_likelihood_ratio(
            rho, chisq, autocorr_power=2.5, ifo="H1", dof=120.0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_add_rank_rejects_without_mutation(value):
    module = load_single_far()
    background = module.RankBackground()
    background.add_rank(1.0)
    before = list(background._ranks)
    with pytest.raises(ValueError):
        background.add_rank(value)
    assert background._ranks == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_extend_ranks_rejects_atomically(value):
    module = load_single_far()
    background = module.RankBackground()
    background.add_rank(1.0)
    before = list(background._ranks)
    with pytest.raises(ValueError):
        background.extend_ranks([2.0, value, 3.0])
    assert background._ranks == before


@pytest.mark.parametrize(
    "value", [0.0, -1.0, float("nan"), float("inf")])
def test_livetime_rejects_without_mutation(value):
    module = load_single_far()
    background = module.RankBackground()
    background.add_livetime(5.0, gps=100.0)
    before_livetime = background.livetime
    before_segments = list(background.livetime_segments)
    with pytest.raises(ValueError):
        background.add_livetime(value, gps=101.0)
    assert background.livetime == before_livetime
    assert background.livetime_segments == before_segments


def test_livetime_cumulative_overflow_rejects_without_mutation():
    module = load_single_far()
    background = module.RankBackground()
    background.livetime = 1.0e308
    before_segments = list(background.livetime_segments)
    with pytest.raises(ValueError, match="cumulative"):
        background.add_livetime(1.0e308, gps=101.0)
    assert background.livetime == 1.0e308
    assert background.livetime_segments == before_segments
