#!/usr/bin/env python3
"""Compare crashcar detail rows to the sidecar final single-FAR ledger."""

import argparse
import csv
import glob
import json
import math
import os
from collections import Counter, defaultdict

IFO_ID = {"H1": 0, "L1": 1, "V1": 2, "K1": 3}


def fint(value):
    if value in (None, ""):
        return None
    return int(float(value))


def flt(value):
    try:
        return float(value)
    except Exception:
        return math.nan


def side_key(row):
    return (
        fint(row.get("bankid")),
        fint(row.get("tmplt_idx")),
        fint(row.get("end_time")),
        fint(row.get("end_time_ns")),
        IFO_ID.get(row.get("ifo")),
    )


def crash_key(row):
    return (
        fint(row.get("bankid")),
        fint(row.get("tmplt_idx")),
        fint(row.get("end_time")),
        fint(row.get("end_time_ns")),
        fint(row.get("ifo_id")),
    )


def summarize(values):
    clean = sorted(abs(v) for v in values if math.isfinite(v))
    if not clean:
        return {"count": 0}

    def pct(q):
        index = min(len(clean) - 1, max(0, int(round(q * (len(clean) - 1)))))
        return clean[index]

    return {
        "count": len(clean),
        "max_abs": max(clean),
        "median_abs": pct(0.50),
        "p95_abs": pct(0.95),
        "p99_abs": pct(0.99),
    }


def load_csv(path):
    with open(path, newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--mismatch-csv", required=True)
    parser.add_argument("--sidecar-csv")
    parser.add_argument("--llr-atol", type=float, default=2e-6)
    parser.add_argument("--far-atol", type=float, default=2e-6)
    args = parser.parse_args()

    side_path = args.sidecar_csv or os.path.join(
        args.run_dir, "single_branch", "single_final_far_all.csv")
    side_rows = load_csv(side_path)

    crash_rows = []
    for path in sorted(glob.glob(os.path.join(
            args.run_dir, "crashcar_singlefar_detail_worker*.csv"))):
        for row in load_csv(path):
            row["_crash_file"] = os.path.basename(path)
            crash_rows.append(row)

    side_by_key = defaultdict(list)
    for row in side_rows:
        side_by_key[side_key(row)].append(row)
    crash_by_key = defaultdict(list)
    for row in crash_rows:
        crash_by_key[crash_key(row)].append(row)

    diffs = defaultdict(list)
    missing = []
    duplicates = []
    mismatches = []
    matched = 0

    for side in side_rows:
        key = side_key(side)
        candidates = crash_by_key.get(key, [])
        if not candidates:
            missing.append(key)
            continue
        if len(candidates) > 1:
            duplicates.append((key, len(candidates)))

        def score(row):
            return (
                abs(flt(row.get("far_sngl")) - flt(side.get("assigned_far"))),
                abs(flt(row.get("llr")) - flt(side.get("llr"))),
            )

        crash = min(candidates, key=score)
        matched += 1
        comparisons = {
            "llr": (flt(side.get("llr")), flt(crash.get("llr"))),
            "direct_far": (
                flt(side.get("direct_far")), flt(crash.get("direct_far"))),
            "assigned_far": (
                flt(side.get("assigned_far")), flt(crash.get("far_sngl"))),
            "far_1w_sngl": (
                flt(side.get("assigned_far")), flt(crash.get("far_1w_sngl"))),
            "bg_start": (
                flt(side.get("assign_bg_start")), flt(crash.get("bg_start"))),
            "bg_end": (
                flt(side.get("assign_bg_end")), flt(crash.get("bg_end"))),
        }
        bad = False
        for name, (expected, observed) in comparisons.items():
            diff = observed - expected
            diffs[name].append(diff)
            limit = args.llr_atol if name == "llr" else args.far_atol
            if name.startswith("bg_"):
                limit = 1e-6
            if abs(diff) > limit:
                bad = True
        if bad:
            row = {
                "key": repr(key),
                "side_ifo": side.get("ifo", ""),
                "source_file": side.get("source_file", ""),
                "crash_file": crash.get("_crash_file", ""),
                "crash_code_version": crash.get("code_version", ""),
                "crash_window_count": crash.get("window_count", ""),
                "crash_total_window_count": crash.get("total_window_count", ""),
            }
            for name, (expected, observed) in comparisons.items():
                row[f"{name}_side"] = expected
                row[f"{name}_crash"] = observed
                row[f"{name}_diff"] = observed - expected
            mismatches.append(row)

    summary = {
        "run_dir": args.run_dir,
        "sidecar_rows": len(side_rows),
        "sidecar_unique_keys": len(side_by_key),
        "crashcar_rows": len(crash_rows),
        "crashcar_unique_keys": len(crash_by_key),
        "matched_sidecar_rows": matched,
        "missing_sidecar_rows": len(missing),
        "duplicate_crash_keys_for_sidecar": len(duplicates),
        "mismatch_count": len(mismatches),
        "side_ifo_counts": Counter(row.get("ifo") for row in side_rows),
        "crash_ifo_counts": Counter(str(fint(row.get("ifo_id")))
                                    for row in crash_rows),
        "diffs": {name: summarize(values) for name, values in diffs.items()},
        "missing_preview": [repr(key) for key in missing[:20]],
        "mismatch_preview": mismatches[:10],
    }

    os.makedirs(os.path.dirname(args.output_json), exist_ok=True)
    with open(args.output_json, "w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    fields = sorted(set().union(*(row.keys() for row in mismatches))) if mismatches else []
    with open(args.mismatch_csv, "w", newline="") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(mismatches)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
