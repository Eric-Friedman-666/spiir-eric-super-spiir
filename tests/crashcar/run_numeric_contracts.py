#!/usr/bin/env python3
"""Run compiled C numerics against the independent Python oracle."""

import argparse
import ctypes
import json
import math
from pathlib import Path

from numeric_oracle import BETAS, evaluate_far, gaussian_llr


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "gstlal-spiir/gst/cuda/.libs/libgstcuda.so.0.0.0"


class Evaluation(ctypes.Structure):
    _fields_ = [
        ("calculated_far", ctypes.c_double),
        ("assigned_far", ctypes.c_double),
        ("r_tail", ctypes.c_double),
        ("tail_slope", ctypes.c_double),
        ("tail_intercept", ctypes.c_double),
        ("used_tail_fit", ctypes.c_int),
    ]


def _close(actual, expected, rel=3e-15, absolute=3e-13):
    return math.isclose(actual, expected, rel_tol=rel, abs_tol=absolute)


def _error(actual, expected):
    absolute = abs(actual - expected)
    relative = absolute / max(abs(expected), 1e-300)
    return absolute, relative


def configure_library(path):
    library = ctypes.CDLL(str(path))
    library.crashcar_singlefar_beta_grid_size.restype = ctypes.c_uint
    library.crashcar_singlefar_beta_at.argtypes = [
        ctypes.c_uint, ctypes.POINTER(ctypes.c_double)]
    library.crashcar_singlefar_beta_at.restype = ctypes.c_int
    library.crashcar_singlefar_dof_for_bank.argtypes = [
        ctypes.c_int, ctypes.POINTER(ctypes.c_double)]
    library.crashcar_singlefar_dof_for_bank.restype = ctypes.c_int
    library.crashcar_singlefar_compute_llr.argtypes = [
        ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
        ctypes.POINTER(ctypes.c_double)]
    library.crashcar_singlefar_compute_llr.restype = ctypes.c_int
    library.crashcar_singlefar_evaluate_far.argtypes = [
        ctypes.POINTER(ctypes.c_double), ctypes.c_uint, ctypes.c_double,
        ctypes.c_double, ctypes.POINTER(Evaluation)]
    library.crashcar_singlefar_evaluate_far.restype = ctypes.c_int
    return library


def c_evaluate(library, ranks, livetime, rank):
    array_type = ctypes.c_double * len(ranks)
    array = array_type(*ranks)
    result = Evaluation()
    ok = bool(library.crashcar_singlefar_evaluate_far(
        array, len(ranks), livetime, rank, ctypes.byref(result)))
    return ok, result


def run(plugin):
    library = configure_library(plugin)
    report = {
        "schema_version": 1,
        "plugin": str(plugin.resolve()),
        "beta": [],
        "dof": [],
        "llr": [],
        "far": [],
        "invalid": [],
        "max_abs_error": 0.0,
        "max_rel_error": 0.0,
    }

    assert library.crashcar_singlefar_beta_grid_size() == 64
    for index, expected in enumerate(BETAS):
        actual = ctypes.c_double()
        assert library.crashcar_singlefar_beta_at(index, ctypes.byref(actual))
        assert _close(actual.value, expected, rel=0.0, absolute=1e-15)
        report["beta"].append(actual.value)
    invalid_beta = ctypes.c_double()
    assert not library.crashcar_singlefar_beta_at(
        64, ctypes.byref(invalid_beta))

    dof_cases = {
        0: 120.0, 99: 120.0, 100: 600.0, 383: 600.0,
        -1: None, 384: None, 415: None, 416: None,
    }
    for bankid, expected in dof_cases.items():
        actual = ctypes.c_double()
        ok = bool(library.crashcar_singlefar_dof_for_bank(
            bankid, ctypes.byref(actual)))
        assert ok is (expected is not None)
        if expected is not None:
            assert actual.value == expected
        report["dof"].append({
            "bankid": bankid, "ok": ok,
            "dof": actual.value if ok else None,
        })

    vectors = (
        (4.0001, 1.0, 3.2, 120.0),
        (8.5, 0.9, 10.555639, 120.0),
        (49.8, 1.4, 5.7, 120.0),
        (4.0001, 1.0, 3.2, 600.0),
        (8.5, 0.9, 10.555639, 600.0),
        (49.8, 1.4, 5.7, 600.0),
    )
    for rho, chisq, a_eff, dof in vectors:
        expected = gaussian_llr(rho, chisq, a_eff, dof)
        actual = ctypes.c_double()
        assert library.crashcar_singlefar_compute_llr(
            rho, chisq, a_eff, dof, ctypes.byref(actual))
        assert _close(actual.value, expected)
        absolute, relative = _error(actual.value, expected)
        report["max_abs_error"] = max(report["max_abs_error"], absolute)
        report["max_rel_error"] = max(report["max_rel_error"], relative)
        report["llr"].append({
            "rho": rho, "chisq": chisq, "a_eff": a_eff, "dof": dof,
            "c": actual.value, "oracle": expected,
            "abs_error": absolute, "rel_error": relative,
        })

    invalid_llr = (
        (8.0, 1.0, 0.0, 120.0),
        (8.0, 1.0, math.nan, 120.0),
        (8.0, 1.0, 1.0, 681.0),
        (math.inf, 1.0, 1.0, 120.0),
    )
    for values in invalid_llr:
        actual = ctypes.c_double()
        ok = bool(library.crashcar_singlefar_compute_llr(
            *values, ctypes.byref(actual)))
        assert not ok and math.isnan(actual.value)
        report["invalid"].append({
            "kind": "llr", "values": [repr(value) for value in values],
        })

    support = [float(value) for value in range(200)]
    queries = [
        99.5, math.nextafter(100.0, -math.inf), 100.0,
        math.nextafter(100.0, math.inf), 100.5, 250.0,
    ]
    for query in queries:
        expected = evaluate_far(support, 10000.0, query)
        ok, actual = c_evaluate(library, support, 10000.0, query)
        assert ok and expected["assigned_far"] is not None
        assert actual.r_tail == expected["r_tail"]
        assert bool(actual.used_tail_fit) is expected["used_tail_fit"]
        for field in ("calculated_far", "assigned_far", "tail_slope",
                      "tail_intercept"):
            expected_value = expected[field]
            actual_value = getattr(actual, field)
            if expected_value is None:
                assert math.isnan(actual_value)
                continue
            assert _close(actual_value, expected_value,
                          rel=4e-15, absolute=4e-13)
            absolute, relative = _error(actual_value, expected_value)
            report["max_abs_error"] = max(report["max_abs_error"], absolute)
            report["max_rel_error"] = max(report["max_rel_error"], relative)
        report["far"].append({
            "query": query,
            "calculated_far": actual.calculated_far,
            "assigned_far": actual.assigned_far,
            "r_tail": actual.r_tail,
            "tail_slope": actual.tail_slope,
            "tail_intercept": actual.tail_intercept,
            "used_tail_fit": bool(actual.used_tail_fit),
        })

    nonanchor_support = [float(value) for value in range(20)]
    equality_query = 10.0
    strict_tail_query = math.nextafter(equality_query, math.inf)
    ok, equality = c_evaluate(
        library, nonanchor_support, 950.0, equality_query)
    assert ok
    assert equality.r_tail == 10.0
    assert not bool(equality.used_tail_fit)
    assert equality.calculated_far == 10.0 / 950.0
    assert equality.assigned_far == 10.0 / 950.0
    assert equality.assigned_far != 0.01
    ok, strict_tail = c_evaluate(
        library, nonanchor_support, 950.0, strict_tail_query)
    assert ok
    assert strict_tail.r_tail == 10.0
    assert bool(strict_tail.used_tail_fit)
    assert math.isclose(strict_tail.assigned_far, 0.01,
                        rel_tol=2e-15, abs_tol=0.0)
    report["far"].extend([
        {
            "label": "nonanchor_equality_direct",
            "query": equality_query,
            "calculated_far": equality.calculated_far,
            "assigned_far": equality.assigned_far,
            "r_tail": equality.r_tail,
            "used_tail_fit": bool(equality.used_tail_fit),
        },
        {
            "label": "nonanchor_nextafter_strict_tail",
            "query": strict_tail_query,
            "calculated_far": strict_tail.calculated_far,
            "assigned_far": strict_tail.assigned_far,
            "r_tail": strict_tail.r_tail,
            "used_tail_fit": bool(strict_tail.used_tail_fit),
        },
    ])

    ok, sparse_direct = c_evaluate(library, [5.0], 100.0, 5.0)
    assert ok
    assert sparse_direct.assigned_far == sparse_direct.calculated_far == 0.01
    ok, sparse_tail = c_evaluate(library, [5.0], 100.0, 5.0001)
    assert not ok
    assert sparse_tail.calculated_far == 0.01
    assert math.isnan(sparse_tail.assigned_far)
    report["invalid"].append({
        "kind": "sparse_tail", "calculated_far": sparse_tail.calculated_far,
    })

    ok, invalid_livetime = c_evaluate(library, [1.0], 0.0, 1.0)
    assert not ok and math.isnan(invalid_livetime.assigned_far)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, default=PLUGIN)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.plugin.is_file():
        raise SystemExit("built plugin missing: %s" % args.plugin)
    report = run(args.plugin)
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
