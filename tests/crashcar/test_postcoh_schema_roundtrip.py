from pathlib import Path
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "gstlal-spiir/include/postcohtable.h"
WRAPPER = ROOT / "gstlal-spiir/python/pipemodules/postcohtable/_postcohtable.c"
FINALSINK = ROOT / "gstlal-spiir/python/pipemodules/postcoh_finalsink.py"


def _text(path):
    return path.read_text(encoding="utf-8")


def _row(*, ifos="H1", h_llr=9.75, l_llr=0.0,
         h_far=0.0, l_far=0.0, multi_far=0.0):
    return SimpleNamespace(
        event_id=4242,
        ifos=ifos,
        H1_LLR=h_llr,
        L1_LLR=l_llr,
        far_sngl=[h_far, l_far, 0.0, 0.0],
        far=multi_far,
    )


def test_a109_fields_have_matching_c_python_ligolw_types():
    from gstlal_spiir.pipemodules.postcohtable import postcoh_table_def

    header = _text(HEADER)
    wrapper = _text(WRAPPER)
    assert "REAL8 H1_LLR;" in header
    assert "REAL8 L1_LLR;" in header
    assert '"H1_LLR", T_DOUBLE' in wrapper
    assert '"L1_LLR", T_DOUBLE' in wrapper

    a107_pairs = postcoh_table_def.POSTCOH_A107_COLUMN_PAIRS
    a109_pairs = postcoh_table_def.POSTCOH_A109_COLUMN_PAIRS
    assert len(a107_pairs) == 107
    assert len(a109_pairs) == 109
    assert a109_pairs[:107] == a107_pairs
    assert a109_pairs[107:] == (
        ("H1_LLR", "real_8"),
        ("L1_LLR", "real_8"),
    )
    assert postcoh_table_def.postcoh_columns_for_schema_mode(
        postcoh_table_def.POSTCOH_SCHEMA_MODE_CRASHCAR_A109
    ) == tuple(name for name, unused_kind in a109_pairs)

    obsolete = "single_state_" + "mask"
    assert obsolete not in header
    assert obsolete not in wrapper
    assert obsolete not in dict(a109_pairs)


def test_unique_owner_far_decisions_do_not_compare_single_and_multi():
    from gstlal_spiir.pipemodules import postcoh_finalsink

    h = _row(ifos="H1", h_far=1.25e-5, multi_far=9.0e-9)
    decision = postcoh_finalsink._crashcar_final_far_decision(h)
    assert decision["route"] == "H1_SINGLE"
    assert decision["owner_ifo"] == "H1"
    assert decision["valid"] == 1
    assert decision["value"] == pytest.approx(1.25e-5)

    l = _row(ifos="L1V1", h_llr=0.0, l_llr=4.5,
             l_far=2.5e-5, multi_far=8.0e-10)
    decision = postcoh_finalsink._crashcar_final_far_decision(l)
    assert decision["route"] == "L1_SINGLE"
    assert decision["owner_ifo"] == "L1"
    assert decision["value"] == pytest.approx(2.5e-5)

    multi = _row(ifos="H1L1", h_llr=8.0, l_llr=9.0,
                 h_far=1.0e-8, l_far=2.0e-8, multi_far=3.0e-4)
    before = tuple(multi.far_sngl)
    decision = postcoh_finalsink._crashcar_final_far_decision(multi)
    assert decision["route"] == "MULTI"
    assert decision["owner_ifo"] == ""
    assert decision["value"] == pytest.approx(3.0e-4)
    assert tuple(multi.far_sngl) == before


def test_nonpositive_route_far_is_explicitly_invalid_without_backfill():
    from gstlal_spiir.pipemodules import postcoh_finalsink

    row = _row(ifos="H1", h_far=0.0)
    decision = postcoh_finalsink._crashcar_final_far_decision(row)
    assert decision == {
        "route": "H1_SINGLE",
        "owner_ifo": "H1",
        "value": 0.0,
        "valid": 0,
        "active_ifos": ("H1",),
    }
    dispatch = postcoh_finalsink._crashcar_candidate_output_dispatch(
        row, decision, -4.0)
    assert dispatch["write"] is False


def test_a109_inactive_llr_and_nonfinite_values_fail_closed():
    from gstlal_spiir.pipemodules import postcoh_finalsink

    with pytest.raises(RuntimeError, match="INACTIVE_A109_LLR"):
        postcoh_finalsink._crashcar_final_far_decision(
            _row(ifos="H1", l_llr=1.0))
    with pytest.raises(RuntimeError, match="INVALID_A109_LLR"):
        postcoh_finalsink._crashcar_final_far_decision(
            _row(ifos="H1L1", h_llr=float("nan"), l_llr=1.0))
    with pytest.raises(RuntimeError, match="INVALID_FINAL_FAR"):
        postcoh_finalsink._crashcar_final_far_decision(
            _row(ifos="H1L1", h_llr=1.0, l_llr=1.0,
                 multi_far=float("inf")))


def test_wrapper_uses_two_readonly_scalars_and_no_owned_llr_array():
    wrapper = _text(WRAPPER)
    assert wrapper.count('offsetof(PostcohInspiralWrapper, postcohtable.H1_LLR)') == 1
    assert wrapper.count('offsetof(PostcohInspiralWrapper, postcohtable.L1_LLR)') == 1
    assert "NPY_DOUBLE, MAX_NIFO" not in wrapper
    obsolete_array = "llr_" + "sngl"
    obsolete_mask = "single_state_" + "mask"
    assert obsolete_array not in wrapper
    assert obsolete_mask not in wrapper


def test_finalsink_has_one_route_owner_and_no_forced_unassigned_writer():
    source = _text(FINALSINK)
    assert "never compare single with multi" in source
    assert 'if route == "H1_SINGLE":' in source
    assert 'elif route == "L1_SINGLE":' in source
    assert "raw_value = postcoh_inspiral.far" in source
    assert "value if valid else 0.0" in source
    obsolete_writer = "__write_" + "pending_coinc_if_needed"
    obsolete_best = "_crashcar_" + "best_single"
    assert obsolete_writer not in source
    assert obsolete_best not in source


@pytest.mark.skipif(
    os.environ.get("CRASHCAR_RUN_COMPILED_CONTRACTS") != "1",
    reason="requires fresh built OzSTAR SPIIR C/Python runtime",
)
def test_built_wrapper_postcoh_and_coinc_ligolw_roundtrip():
    subprocess.run(
        [sys.executable, str(ROOT / "tests/crashcar/run_schema_roundtrip.py")],
        check=True,
    )


@pytest.mark.skipif(
    os.environ.get("CRASHCAR_RUN_COMPILED_CONTRACTS") != "1",
    reason="requires fresh built OzSTAR SPIIR C runtime",
)
def test_compiled_ifo_preserve_and_a109_unique_owner_contracts():
    subprocess.run(
        [sys.executable, str(ROOT / "tests/crashcar/run_compiled_contracts.py")],
        check=True,
    )
