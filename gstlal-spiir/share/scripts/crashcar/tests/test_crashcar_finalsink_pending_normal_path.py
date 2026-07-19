#!/usr/bin/env python3
"""A109 same-row FinalSink and unique final-owner contracts."""

import inspect

import numpy as np
import pytest

from gstlal_spiir.pipemodules import pipe_macro
from gstlal_spiir.pipemodules import postcoh_finalsink as finalsink
from gstlal_spiir.pipemodules.postcohtable import postcoh_table_def


class FakePostcohRow:
    """Wrapper-shaped A109 row with the normal A107 arrays plus two scalars."""

    _ARRAY_FAMILIES = (
        "snglsnr",
        "chisq",
        "end_time_sngl",
        "end_time_ns_sngl",
        "deff",
        "coaphase",
        "far_sngl",
        "far_1w_sngl",
        "far_1d_sngl",
        "far_2h_sngl",
        "livetime_1w_sngl",
        "livetime_1d_sngl",
        "livetime_2h_sngl",
        "nevent_1w_sngl",
        "nevent_1d_sngl",
        "nevent_2h_sngl",
    )

    def __init__(self, ifos):
        count = max(pipe_macro.get_ifo_id(ifo)
                    for ifo in pipe_macro.IFO_MAP) + 1
        integer_families = {
            "end_time_sngl",
            "end_time_ns_sngl",
            "livetime_1w_sngl",
            "livetime_1d_sngl",
            "livetime_2h_sngl",
            "nevent_1w_sngl",
            "nevent_1d_sngl",
            "nevent_2h_sngl",
        }
        for family in self._ARRAY_FAMILIES:
            dtype = np.int64 if family in integer_families else np.float64
            setattr(self, family, np.zeros(count, dtype=dtype))
        self.ifos = ifos
        self.event_id = 1701
        self.end_time = 1240000000
        self.end_time_ns = 123
        self.bankid = 7
        self.tmplt_idx = 11
        self.far = 0.0
        self.H1_LLR = 0.0
        self.L1_LLR = 0.0
        for horizon in ("1w", "1d", "2h"):
            setattr(self, "far_%s" % horizon, 0.0)
            setattr(self, "nevent_%s" % horizon, 0)
            setattr(self, "livetime_%s" % horizon, 0)
        for ifo in pipe_macro.IFO_MAP:
            if ifo not in ifos:
                continue
            ifo_id = pipe_macro.get_ifo_id(ifo)
            self.end_time_sngl[ifo_id] = self.end_time
            self.end_time_ns_sngl[ifo_id] = self.end_time_ns
            self.deff[ifo_id] = 100.0 + ifo_id
            self.coaphase[ifo_id] = 0.25 * (ifo_id + 1)
            self.chisq[ifo_id] = 1.5
        self._column_defaults = {
            name: ("" if kind == "lstring" else 0)
            for name, kind in postcoh_table_def.POSTCOH_A109_COLUMN_PAIRS
        }

    def __getattr__(self, name):
        defaults = object.__getattribute__(self, "_column_defaults")
        for ifo in pipe_macro.IFO_MAP:
            suffix = "_" + ifo
            if not name.endswith(suffix):
                continue
            family = name[:-len(suffix)]
            try:
                array = object.__getattribute__(self, family)
            except AttributeError:
                break
            return array[pipe_macro.get_ifo_id(ifo)]
        if name in defaults:
            return defaults[name]
        raise AttributeError(name)


def set_single(row, ifo, rho=4.0, llr=17.25, far=0.0):
    ifo_id = pipe_macro.get_ifo_id(ifo)
    row.snglsnr[ifo_id] = rho
    row.far_sngl[ifo_id] = far
    setattr(row, ifo + "_LLR", llr)


def test_schema_is_exact_a107_prefix_plus_two_real8_columns():
    assert len(postcoh_table_def.POSTCOH_A107_COLUMN_PAIRS) == 107
    assert len(postcoh_table_def.POSTCOH_A109_COLUMN_PAIRS) == 109
    assert postcoh_table_def.POSTCOH_A109_COLUMN_PAIRS[:107] == (
        postcoh_table_def.POSTCOH_A107_COLUMN_PAIRS)
    assert postcoh_table_def.POSTCOH_A109_COLUMN_PAIRS[107:] == (
        ("H1_LLR", "real_8"),
        ("L1_LLR", "real_8"),
    )


def test_serialization_keeps_the_same_authoritative_row():
    row = FakePostcohRow("H1")
    set_single(row, "H1", rho=4.0, llr=17.25, far=2.5e-6)
    projected = finalsink._postcoh_row_for_serialization(
        row, postcoh_table_def.POSTCOH_SCHEMA_MODE_CRASHCAR_A109)
    assert projected is row
    assert projected.H1_LLR == pytest.approx(17.25)
    assert projected.L1_LLR == 0.0
    assert projected.far_sngl_H1 == pytest.approx(2.5e-6)


def test_multi_route_preserves_every_normal_single_far_byte():
    row = FakePostcohRow("H1L1")
    set_single(row, "H1", llr=21.0, far=3.0e-4)
    set_single(row, "L1", llr=22.0, far=7.0e-4)
    row.far = 9.0e-5
    before = row.far_sngl.tobytes()
    projected = finalsink._postcoh_row_for_serialization(
        row, postcoh_table_def.POSTCOH_SCHEMA_MODE_CRASHCAR_A109)
    decision = finalsink._crashcar_final_far_decision(row)
    assert projected is row
    assert row.far_sngl.tobytes() == before
    assert decision["route"] == "MULTI"
    assert decision["owner_ifo"] == ""
    assert decision["value"] == pytest.approx(row.far)


@pytest.mark.parametrize(
    ("ifos", "expected_nevents"),
    (
        ("H1", 1),
        ("L1", 1),
        ("H1V1", 2),
        ("L1V1", 2),
        ("H1L1", 2),
        ("H1L1V1", 2),
        ("V1", 1),
    ),
)
def test_coincs_cardinality_rule_is_normal_route_shape(ifos, expected_nevents):
    active_ifos, route = finalsink._crashcar_active_ifos_and_route(ifos)
    assert finalsink._crashcar_coinc_nevents_for_route(
        route, active_ifos) == expected_nevents


@pytest.mark.parametrize(
    ("ifos", "owner", "far"),
    (("H1", "H1", 1.0e-5), ("L1V1", "L1", 2.0e-5)),
)
def test_single_route_reads_only_its_unique_owner(ifos, owner, far):
    row = FakePostcohRow(ifos)
    set_single(row, owner, far=far)
    row.far = 1.0e-12
    other = "L1" if owner == "H1" else "H1"
    row.far_sngl[pipe_macro.get_ifo_id(other)] = 9.0e-13
    decision = finalsink._crashcar_final_far_decision(row)
    assert decision["owner_ifo"] == owner
    assert decision["valid"] == 1
    assert decision["value"] == pytest.approx(far)


@pytest.mark.parametrize("far", (0.0, -1.0))
def test_nonpositive_owner_far_is_not_a_formal_output(far):
    row = FakePostcohRow("H1")
    set_single(row, "H1", far=far)
    decision = finalsink._crashcar_final_far_decision(row)
    assert decision["valid"] == 0
    assert decision["value"] == 0.0
    dispatch = finalsink._crashcar_cluster_zero_dispatch(
        row, decision, -4.0)
    assert dispatch == {
        "write": False,
        "owner_ifo": "H1",
        "route": "H1_SINGLE",
    }


def test_hl_normal_far_alone_controls_threshold_dispatch():
    row = FakePostcohRow("H1L1")
    set_single(row, "H1", llr=11.0, far=1.0e-12)
    set_single(row, "L1", llr=12.0, far=2.0e-12)
    row.far = 3.0e-5
    decision = finalsink._crashcar_final_far_decision(row)
    assert finalsink._crashcar_cluster_zero_dispatch(
        row, decision, -4.0)["write"] is True
    row.far = 3.0e-3
    decision = finalsink._crashcar_final_far_decision(row)
    assert finalsink._crashcar_cluster_zero_dispatch(
        row, decision, -4.0)["write"] is False


def test_rho_equal_four_is_eligible_and_v1_is_not_a_single_owner():
    assert finalsink._crashcar_single_snr_eligible(4.0)
    assert not finalsink._crashcar_single_snr_eligible(float("nan"))
    row = FakePostcohRow("V1")
    row.snglsnr[pipe_macro.get_ifo_id("V1")] = 100.0
    row.far = 4.0e-5
    decision = finalsink._crashcar_final_far_decision(row)
    assert decision["route"] == "V1_ONLY"
    assert decision["owner_ifo"] == ""


def test_inactive_or_nonfinite_a109_llr_fails_closed():
    row = FakePostcohRow("H1")
    row.L1_LLR = 1.0
    with pytest.raises(RuntimeError, match="INACTIVE_A109_LLR"):
        finalsink._crashcar_final_far_decision(row)

    row = FakePostcohRow("H1L1")
    row.H1_LLR = np.nan
    with pytest.raises(RuntimeError, match="INVALID_A109_LLR"):
        finalsink._crashcar_final_far_decision(row)


def test_finalsink_constructor_and_normal_writer_contract():
    parameter = inspect.signature(finalsink.FinalSink.__init__).parameters[
        "snr_series_logfar_threshold"]
    assert parameter.default == -4
    text = open(finalsink.__file__, encoding="utf-8").read()
    assert "assemble_ligolw_snr_series_arrays" in text
    assert "self.coincs_document.assemble_ligolw_xmldoc(" in text
    assert "raw_value = postcoh_inspiral.far" in text
    for left, right in (
        ("candidate_event_", "manifest"),
        ("single_trigger_", "stream_fname"),
        ("far_assigned_", "sngl_exact"),
        ("__write_", "pending_coinc_if_needed"),
    ):
        assert left + right not in text
