#!/usr/bin/env python3
"""Machine gate for independent verification-sidecar numeric layer."""
from __future__ import annotations

import ast
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import literal_pdf_gaussian_oracle as literal
import verification_sidecar_numeric as adapter
import single_detector_far as production

A_EFF_GOLDEN = (
    ("H1", 0, 0, "0x1.51c7cb7edc628p+3", 120),
    ("H1", 99, 999, "0x1.6bf674db83c48p+3", 120),
    ("H1", 100, 500, "0x1.6a6db02c5db25p+3", 600),
    ("H1", 383, 0, "0x1.9ecc79fd573f8p+3", 600),
    ("L1", 0, 999, "0x1.a2d3c3ea59736p+3", 120),
    ("L1", 100, 0, "0x1.ad7fbadcf23ebp+3", 600),
    ("L1", 383, 999, "0x1.5b6717cb457f5p+4", 600),
)


def expect_raises(function, *args):
    try:
        function(*args)
    except (ValueError, ArithmeticError, KeyError, RuntimeError):
        return
    raise AssertionError("expected failure: %s%r" % (function.__name__, args))


def main():
    failures = []
    observations = {}

    source = (ROOT / "verification_sidecar_numeric.py").read_text()
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden_imports = [
        name for name in imported
        if ("crashcar" in name or "single_detector_far" in name
            or "literal_pdf_gaussian_oracle" in name)
    ]
    if forbidden_imports:
        failures.append("forbidden adapter imports: %r" % forbidden_imports)
    adapter_beta_hex = [
        (adapter._STEP + adapter._STEP * float(index)).hex()
        for index in range(64)
    ]
    literal_beta_hex = [
        (literal.BETA_STEP + literal.BETA_STEP * float(index)).hex()
        for index in range(64)
    ]
    if adapter_beta_hex != literal_beta_hex:
        failures.append("all-64 beta binary64 values drifted from literal")

    shapes = adapter.ActualPickleShapeSource()
    checked_samples = 0
    for ifo, bank, template, expected_hex, expected_dof in A_EFF_GOLDEN:
        value, dof = shapes.a_eff_and_dof(
            ifo, bank, template)
        checked_samples += 1
        if value.hex() != expected_hex:
            failures.append(
                "A_eff mismatch %s/%d/%d" % (
                    ifo, bank, template))
        if dof != expected_dof:
            failures.append(
                "fixed dof mismatch %s/%d" % (
                    ifo, bank))

    below = math.nextafter(4.0, -math.inf)
    equal = 4.0
    above = math.nextafter(4.0, math.inf)
    if adapter.threshold_eligible(below):
        failures.append("nextafter below threshold included")
    if not adapter.threshold_eligible(equal):
        failures.append("exact 4.0 excluded")
    if not adapter.threshold_eligible(above):
        failures.append("nextafter above excluded")

    vectors = [
        (equal, 1.0, 10.0, 120),
        (above, 0.75, 10.0, 120),
        (8.5, 0.8, 13.25, 120),
        (12.0, 1.2, 20.0, 600),
        (20.0, 0.55, 35.0, 600),
    ]
    for ifo, bank, template in (
        ("H1", 0, 0), ("H1", 99, 999),
        ("H1", 100, 500), ("H1", 383, 0),
        ("L1", 0, 999), ("L1", 100, 0), ("L1", 383, 999),
    ):
        a_eff, dof = shapes.a_eff_and_dof(ifo, bank, template)
        vectors.append((7.25, 0.9, a_eff, dof))

    llr_hex = []
    for vector in vectors:
        expected = literal.literal_pdf_llr(*vector)
        actual = adapter.pdf_gaussian_llr(*vector)
        llr_hex.append(actual.hex())
        if actual.hex() != expected.hex():
            failures.append(
                "LLR bit mismatch vector=%r expected=%s actual=%s" % (
                    vector, expected.hex(), actual.hex()))
    expect_raises(adapter.pdf_gaussian_llr, below, 1.0, 10.0, 120)
    expect_raises(adapter.pdf_gaussian_llr, 4.0, 1.0, 10.0, 2)
    expect_raises(shapes.a_eff_and_dof, "H1", 384, 0)
    expect_raises(shapes.a_eff_and_dof, "V1", 0, 0)
    expect_raises(shapes.a_eff_and_dof, "H1", 0, 1000)
    for bad_bank in (1.5, "01", "\u0661", -1, 384, True):
        expect_raises(adapter.fixed_dof, bad_bank)
    for bad_template in (1.5, "01", "\u0661", -1, 1000, True):
        expect_raises(
            shapes.a_eff_and_dof, "H1", 0, bad_template)
    for bad_dof in (120.5, "0120", "\u0661\u0662\u0660", True):
        expect_raises(
            adapter.pdf_gaussian_llr, 4.0, 1.0, 10.0, bad_dof)
    for bad_ranks in (
        [0.0, float("nan")],
        [0.0, float("inf")],
        [1.0, 0.0],
    ):
        expect_raises(adapter.calculated_far, bad_ranks, 1.0, 10.0)

    ranks = [0.0, 1.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0]
    livetime = 1000.0
    r_tail, slope, points = adapter.build_anchored_tail(ranks, livetime)
    if not slope < 0.0:
        failures.append("tail slope not negative")
    direct_at_tail, branch_at_tail, count, floor = adapter.assigned_far(
        ranks, r_tail, livetime, r_tail, slope)
    calculated_at_tail, expected_count, expected_floor = (
        adapter.calculated_far(ranks, r_tail, livetime)
    )
    if branch_at_tail != "direct":
        failures.append("r==r_tail did not use direct Calculated FAR")
    if direct_at_tail.hex() != calculated_at_tail.hex():
        failures.append("r==r_tail direct value mismatch")
    if (count, floor) != (expected_count, expected_floor):
        failures.append("r==r_tail support metadata mismatch")
    tail_rank = math.nextafter(r_tail, math.inf)
    tail_value, tail_branch, _count, _floor = adapter.assigned_far(
        ranks, tail_rank, livetime, r_tail, slope)
    if tail_branch != "tail" or not (math.isfinite(tail_value)
                                     and tail_value > 0.0):
        failures.append("strict r>tail branch invalid")
    floor_far, floor_count, floor_used = adapter.calculated_far(
        ranks, 100.0, livetime)
    if floor_count != 0 or not floor_used:
        failures.append("Calculated FAR one-count floor metadata invalid")
    if floor_far.hex() != (1.0 / livetime).hex():
        failures.append("Calculated FAR one-count floor value invalid")

    direct_decision = adapter.assignment_decision(
        ranks, r_tail, livetime)
    if (direct_decision["status"] != adapter.ASSIGNMENT_ASSIGNED
            or direct_decision["branch"] != "direct"):
        failures.append("decision at r_tail not ASSIGNED/direct")
    if (direct_decision["calculated_far"].hex()
            != direct_decision["assigned_far"].hex()):
        failures.append("direct Assigned FAR differs from Calculated FAR")

    tail_decision = adapter.assignment_decision(
        ranks, tail_rank, livetime)
    if (tail_decision["status"] != adapter.ASSIGNMENT_ASSIGNED
            or tail_decision["branch"] != "tail"):
        failures.append("strict above-tail decision not ASSIGNED/tail")
    singleton_direct = adapter.assignment_decision(
        [1.0], 1.0, livetime)
    if (singleton_direct["status"] != adapter.ASSIGNMENT_ASSIGNED
            or singleton_direct["branch"] != "direct"
            or singleton_direct["tail_slope"] is not None
            or singleton_direct["assigned_far"]
            != singleton_direct["calculated_far"]):
        failures.append(
            "direct event incorrectly depends on tail-slope availability")
    direct_without_slope = adapter.assigned_far(
        [1.0], 1.0, livetime, 1.0, None)
    if (direct_without_slope[0].hex()
            != singleton_direct["calculated_far"].hex()):
        failures.append("standalone direct FAR required a tail slope")
    expect_raises(
        adapter.assigned_far,
        [1.0], math.nextafter(1.0, math.inf),
        livetime, 1.0, None)

    pending_missing = adapter.assignment_decision(ranks, 1.0, None)
    pending_empty = adapter.assignment_decision([], 1.0, livetime)
    pending_bad_tail = adapter.assignment_decision(
        [1.0], math.nextafter(1.0, math.inf), livetime)
    for label, decision in (
        ("missing livetime", pending_missing),
        ("empty support", pending_empty),
        ("above-tail invalid fit", pending_bad_tail),
    ):
        if decision["status"] != adapter.ASSIGNMENT_PENDING:
            failures.append("%s did not produce PENDING" % label)
        if decision["assigned_far"] is not None:
            failures.append("%s fabricated Assigned FAR" % label)
    if (pending_bad_tail["r_tail"] != 1.0
            or pending_bad_tail["calculated_far"] is None):
        failures.append(
            "above-tail PENDING lost empirical r_tail/Calculated FAR")
    model = production.SingleDetectorLikelihoodModel(
        signal_dof=120, noise_dof=120)
    h_a_eff, h_dof = shapes.a_eff_and_dof("H1", 0, 0)
    expected_h_llr = adapter.pdf_gaussian_llr(
        7.25, 0.9, h_a_eff, h_dof)
    model_h_llr = model.log_likelihood_ratio(
        7.25, 0.9, h_a_eff, ifo="H1", dof=h_dof)
    if model_h_llr.hex() != expected_h_llr.hex():
        failures.append("formal likelihood adapter drift")
    feature = production.SingleDetectorFeature(
        "H1", 7.25, 0.9, tmplt_idx=0, bankid=0,
        autocorr_power=h_a_eff, dof=h_dof)
    branch = production.SingleDetectorBranch(
        model, ifos=("H1", "L1"), min_snr=4.0)
    if branch.llr_feature(feature).hex() != expected_h_llr.hex():
        failures.append("formal branch LLR drift")
    l_a_eff, l_dof = shapes.a_eff_and_dof("L1", 0, 0)
    poisoned_h_feature = production.SingleDetectorFeature(
        "H1", 7.25, 0.9, tmplt_idx=0, bankid=0,
        autocorr_power=l_a_eff, dof=l_dof)
    poisoned_h_llr = branch.llr_feature(
        poisoned_h_feature, autocorr_power=l_a_eff)
    if (poisoned_h_llr.hex() != expected_h_llr.hex()
            or poisoned_h_feature.autocorr_power.hex() != h_a_eff.hex()
            or poisoned_h_feature.dof != h_dof):
        failures.append("broad/cross-IFO A_eff poisoned formal H1 lookup")
    l_feature = production.SingleDetectorFeature(
        "L1", 7.25, 0.9, tmplt_idx=0, bankid=0,
        autocorr_power=h_a_eff, dof=h_dof)
    expected_l_llr = adapter.pdf_gaussian_llr(
        7.25, 0.9, l_a_eff, l_dof)
    if branch.llr_feature(l_feature).hex() != expected_l_llr.hex():
        failures.append("exact L1 tuple lookup drift")
    expect_raises(
        branch.llr_feature,
        production.SingleDetectorFeature(
            "H1", 7.25, 0.9, tmplt_idx=None, bankid=0))
    expect_raises(
        branch.llr_feature,
        production.SingleDetectorFeature(
            "H1", 7.25, 0.9, tmplt_idx=0, bankid=384))
    if production.load_template_shape_map(
            None, adapter.expected_pickle_directory(),
            ("H1", "L1")) is not None:
        failures.append("formal loader exposed a permissive map")
    expect_raises(
        production.load_template_shape_map,
        "legacy.csv", None, ("H1", "L1"))
    expect_raises(
        production.load_template_shape_map,
        None, "/tmp/not-wguo", ("H1", "L1"))
    expect_raises(
        production.load_wguo_bank_stats_map,
        adapter.expected_pickle_directory(), ("H1", "L1"))
    expect_raises(
        production.lookup_autocorr_power,
        {"H1:0000:0": l_a_eff}, "H1", 0, 0)

    for bad_branch_args in (
        (model, ("H1",), 4.0, None, 20, 1.0, 1.0e-2),
        (model, ("L1", "H1"), 4.0, None, 20, 1.0, 1.0e-2),
        (model, ("H1", "L1"), math.nextafter(4.0, -math.inf),
         None, 20, 1.0, 1.0e-2),
        (model, ("H1", "L1"), 4.0, None, 20, 2.0, 1.0e-2),
        (model, ("H1", "L1"), 4.0, None, 20, 1.0, 1.0e-1),
    ):
        expect_raises(production.SingleDetectorBranch, *bad_branch_args)

    alternate_model = production.SingleDetectorLikelihoodModel(
        signal_dof=2, noise_dof=999,
        beta_grid=[0.1, 0.2], beta_weights=[0.9, 0.1],
        default_autocorr_power=999.0, snr_log_weight=-7.0,
        rank_offset=12345.0, noise_beta=88.0)
    alternate_llr = alternate_model.log_likelihood_ratio(
        7.25, 0.9, h_a_eff, ifo="H1", dof=h_dof)
    if alternate_llr.hex() != expected_h_llr.hex():
        failures.append("legacy likelihood knobs changed formal LLR")
    expect_raises(
        alternate_model.log_likelihood_ratio,
        7.25, 0.9, h_a_eff, "H1", "0120")
    expect_raises(
        model.log_likelihood_ratio,
        7.25, 0.9, h_a_eff, "V1", h_dof)
    expect_raises(
        branch.llr_feature,
        production.SingleDetectorFeature(
            "V1", 7.25, 0.9, tmplt_idx=0, bankid=0,
            autocorr_power=h_a_eff, dof=h_dof))

    formal_bg = production.RankBackground()
    formal_bg.extend_ranks(ranks)
    formal_bg.add_livetime(livetime)
    formal_direct, formal_direct_source = formal_bg.far_with_source(
        r_tail, use_fit=True)
    if (formal_direct.hex() != calculated_at_tail.hex()
            or formal_direct_source
            != production.FAR_SOURCE_ASSIGNED_DIRECT):
        failures.append("formal direct Assigned FAR path drift")
    formal_tail, formal_tail_source = formal_bg.far_with_source(
        tail_rank, use_fit=True)
    if (formal_tail.hex() != tail_value.hex()
            or formal_tail_source != production.FAR_SOURCE_ASSIGNED_TAIL):
        failures.append("formal tail Assigned FAR path drift")
    empty_bg = production.RankBackground()
    pending_far, pending_source = empty_bg.far_with_source(
        1.0, use_fit=True)
    if (pending_far is not None
            or pending_source != production.FAR_SOURCE_PENDING):
        failures.append("formal empty BG did not remain explicit PENDING")
    pending_result = branch.assign_feature(feature)
    pending_rows = production.results_to_plot_rows([pending_result])
    if (pending_result.far is not None
            or pending_result.far_source != production.FAR_SOURCE_PENDING
            or pending_result.status != adapter.ASSIGNMENT_PENDING
            or pending_result.valid
            or pending_result.neg_log10_far != ""
            or pending_rows[0]["assigned_far"] is not None
            or pending_rows[0]["assigned_far_source"]
            != production.FAR_SOURCE_PENDING
            or pending_rows[0]["status"] != adapter.ASSIGNMENT_PENDING
            or pending_rows[0]["valid"]
            or pending_rows[0]["a_eff"].hex() != h_a_eff.hex()
            or pending_rows[0]["dof"] != h_dof):
        failures.append("PENDING result serialization fabricated a FAR")
    branch.background["H1"].extend_ranks(ranks)
    branch.background["H1"].add_livetime(livetime)
    assigned_result = branch.assign_feature(feature)
    expected_assignment = adapter.assignment_decision(
        ranks, expected_h_llr, livetime)
    assigned_rows = production.results_to_plot_rows([assigned_result])
    if (assigned_result.status != adapter.ASSIGNMENT_ASSIGNED
            or not assigned_result.valid
            or assigned_result.far is None
            or assigned_result.far.hex()
            != expected_assignment["assigned_far"].hex()
            or assigned_rows[0]["status"] != adapter.ASSIGNMENT_ASSIGNED
            or not assigned_rows[0]["valid"]
            or assigned_rows[0]["a_eff"].hex() != h_a_eff.hex()
            or assigned_rows[0]["dof"] != h_dof):
        failures.append("ASSIGNED result status/exact fields drift")

    class DummyRow(object):
        pass

    row = DummyRow()
    row.far_sngl = [123.0, 123.0, 123.0, 123.0]
    original = list(row.far_sngl)
    for invalid_far in (None, 0.0, float("inf"), float("nan")):
        if production.write_single_far_to_row(row, "H1", invalid_far):
            failures.append("invalid FAR reported as written")
        if row.far_sngl != original:
            failures.append("invalid FAR mutated the row")
    if not production.write_single_far_to_row(
            row, "H1", calculated_at_tail):
        failures.append("valid Assigned FAR was not written")
    if row.far_sngl[production.detector_index("H1")].hex() != (
            calculated_at_tail.hex()):
        failures.append("valid Assigned FAR row value drift")

    observations = {
        "adapter_imports": sorted(imported),
        "forbidden_adapter_imports": forbidden_imports,
        "actual_pickle_samples_checked": checked_samples,
        "actual_pickle_sha256": dict(adapter._PICKLE_SHA),
        "beta_count": len(adapter_beta_hex),
        "beta_hex": adapter_beta_hex,
        "strict_id_negative_case_count": 15,
        "finite_rank_negative_case_count": 3,
        "direct_without_tail_slope_status": singleton_direct["status"],
        "above_tail_invalid_fit_status": pending_bad_tail["status"],
        "formal_pending_status_valid": [
            pending_result.status, pending_result.valid],
        "formal_assigned_status_valid": [
            assigned_result.status, assigned_result.valid],
        "threshold_hex": {
            "below": below.hex(),
            "equal": equal.hex(),
            "above": above.hex(),
        },
        "llr_vector_count": len(vectors),
        "llr_hex": llr_hex,
        "tail": {
            "r_tail_hex": r_tail.hex(),
            "slope_hex": slope.hex(),
            "support_point_count": len(points),
            "at_tail_branch": branch_at_tail,
            "at_tail_far_hex": direct_at_tail.hex(),
            "above_tail_branch": tail_branch,
            "above_tail_far_hex": tail_value.hex(),
        },
    }
    if failures:
        raise AssertionError("; ".join(failures))
    print("PASS", observations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
