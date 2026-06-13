#!/usr/bin/env python3
"""Compare crashcar C detail CSV against equivalent sidecar streaming math."""

from __future__ import print_function

import argparse
import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from single_detector_far import (  # noqa: E402
    FLAG_BACKGROUND,
    FLAG_FOREGROUND,
    RankBackground,
    SingleDetectorFeature,
    make_default_likelihood_model,
)

IFO_BY_ID = {0: "H1", 1: "L1", 2: "V1", 3: "K1"}


def safe_float(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def safe_int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def row_key(row):
    return (
        safe_int(row.get("bankid")),
        safe_int(row.get("tmplt_idx")),
        safe_int(row.get("end_time")),
        safe_int(row.get("end_time_ns")),
        safe_int(row.get("ifo_id")),
    )


def load_rows(patterns):
    rows = []
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            with open(path, newline="") as handle:
                reader = csv.DictReader(handle)
                for index, row in enumerate(reader):
                    row["_source_file"] = path
                    row["_source_index"] = index
                    rows.append(row)
    return rows


def summarize(values):
    values = sorted(abs(v) for v in values if v is not None and math.isfinite(v))
    if not values:
        return {"count": 0}
    def pct(q):
        idx = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
        return values[idx]
    return {
        "count": len(values),
        "max_abs": max(values),
        "median_abs": pct(0.50),
        "p95_abs": pct(0.95),
        "p99_abs": pct(0.99),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crashcar-csv", action="append", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--mismatch-csv")
    parser.add_argument("--llr-atol", type=float, default=1e-7)
    parser.add_argument("--far-rtol", type=float, default=1e-6)
    args = parser.parse_args()

    rows = load_rows(args.crashcar_csv)
    rows.sort(key=lambda r: (r.get("_source_file", ""), safe_int(r.get("_source_index"), 0)))

    model = make_default_likelihood_model()
    backgrounds = defaultdict(lambda: RankBackground(fit_min_points=20,
                                                     far_floor_count=1.0,
                                                     far_fit_boundary=1.0e-2))
    llr_diffs = []
    direct_far_diffs = []
    fitted_far_diffs = []
    mismatches = []
    foreground_checked = 0
    background_rows = 0

    for row in rows:
        ifo_id = safe_int(row.get("ifo_id"))
        ifo = IFO_BY_ID.get(ifo_id, str(ifo_id))
        rho = safe_float(row.get("snglsnr"))
        chisq = safe_float(row.get("chisq"))
        autocorr_power = safe_float(row.get("autocorr_power"))
        dof = safe_float(row.get("dof"))
        is_background = safe_int(row.get("is_background"), FLAG_FOREGROUND)
        bg = backgrounds[ifo]
        livetime = safe_float(row.get("bg_livetime"))
        if livetime is not None:
            bg.livetime = livetime
            bg._invalidate_fit_cache()
        feature = SingleDetectorFeature(
            ifo=ifo,
            rho=rho,
            chisq=chisq,
            tmplt_idx=row.get("tmplt_idx"),
            bankid=row.get("bankid"),
            autocorr_power=autocorr_power,
            dof=dof,
            end_time=row.get("end_time"),
            end_time_ns=row.get("end_time_ns"),
            is_background=is_background,
            source_row=row,
        )
        py_llr = model.rank(rho, chisq, autocorr_power, ifo=ifo, dof=dof)
        c_llr = safe_float(row.get("llr"))
        llr_diff = c_llr - py_llr if c_llr is not None else None
        llr_diffs.append(llr_diff)

        if is_background == FLAG_BACKGROUND:
            bg.add_rank(py_llr)
            if livetime is not None:
                direct_for_support = bg.direct_far(py_llr)
                bg.add_far_llr_point(py_llr, direct_for_support)
            background_rows += 1

        direct_far = bg.direct_far(py_llr)
        c_direct = safe_float(row.get("direct_far"))
        if c_direct is not None and math.isfinite(direct_far):
            direct_far_diffs.append(c_direct - direct_far)

        if is_background == FLAG_FOREGROUND:
            py_far, py_source = bg.far_with_source(py_llr, use_fit=True)
            c_far = safe_float(row.get("far_sngl"))
            if c_far is not None and math.isfinite(py_far):
                fitted_far_diffs.append(c_far - py_far)
                rel = abs(c_far - py_far) / py_far if py_far else float("inf")
            else:
                rel = float("inf")
            foreground_checked += 1
            if (llr_diff is None or abs(llr_diff) > args.llr_atol or
                    rel > args.far_rtol):
                mm = dict(row)
                mm.update({
                    "py_llr": py_llr,
                    "llr_abs_diff": llr_diff,
                    "py_far": py_far,
                    "py_far_source": py_source,
                    "far_abs_diff": None if c_far is None else c_far - py_far,
                    "far_rel_diff": rel,
                })
                mismatches.append(mm)

    summary = {
        "rows_total": len(rows),
        "background_rows": background_rows,
        "foreground_checked": foreground_checked,
        "llr_abs_diff": summarize(llr_diffs),
        "direct_far_abs_diff": summarize(direct_far_diffs),
        "fitted_far_abs_diff": summarize(fitted_far_diffs),
        "mismatch_count": len(mismatches),
        "mismatch_preview": mismatches[:10],
    }
    with open(args.output_json, "w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    if args.mismatch_csv:
        fieldnames = sorted(set().union(*(m.keys() for m in mismatches))) if mismatches else []
        with open(args.mismatch_csv, "w", newline="") as handle:
            if fieldnames:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(mismatches)
            else:
                handle.write("")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
