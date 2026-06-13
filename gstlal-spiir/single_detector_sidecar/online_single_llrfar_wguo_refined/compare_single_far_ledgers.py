#!/usr/bin/env python3
"""Compare two run-level single-detector FAR ledgers by detector-local key."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


KEY_FIELDS = ("ifo", "end_time", "end_time_ns", "bankid", "tmplt_idx")
FLOAT_FIELDS = (
    "rho",
    "chisq",
    "llr",
    "direct_far",
    "assigned_far",
    "far",
    "assign_bg_start",
    "assign_bg_end",
    "assign_bg_livetime_seconds",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True)
    parser.add_argument("--observed", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--mismatch-csv", required=True)
    parser.add_argument("--float-atol", type=float, default=2e-6)
    parser.add_argument("--llr-atol", type=float, default=2e-6)
    return parser.parse_args()


def fint(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def flt(value: str | None) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def load_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def key(row: dict[str, str]) -> tuple:
    return (
        (row.get("ifo") or row.get("ifos") or "").strip(),
        fint(row.get("end_time")),
        fint(row.get("end_time_ns")),
        fint(row.get("bankid")),
        fint(row.get("tmplt_idx")),
    )


def summarize(values: list[float]) -> dict[str, float | int]:
    clean = sorted(abs(value) for value in values if math.isfinite(value))
    if not clean:
        return {"count": 0}

    def pct(q: float) -> float:
        idx = int(round(q * (len(clean) - 1)))
        idx = max(0, min(len(clean) - 1, idx))
        return clean[idx]

    return {
        "count": len(clean),
        "max_abs": clean[-1],
        "median_abs": pct(0.50),
        "p95_abs": pct(0.95),
        "p99_abs": pct(0.99),
    }


def main() -> int:
    args = parse_args()
    expected_rows = load_rows(args.expected)
    observed_rows = load_rows(args.observed)

    expected_by_key: dict[tuple, list[dict[str, str]]] = defaultdict(list)
    observed_by_key: dict[tuple, list[dict[str, str]]] = defaultdict(list)
    for row in expected_rows:
        expected_by_key[key(row)].append(row)
    for row in observed_rows:
        observed_by_key[key(row)].append(row)

    missing = []
    extra = []
    duplicate_expected = []
    duplicate_observed = []
    mismatches = []
    diffs: dict[str, list[float]] = defaultdict(list)
    matched = 0

    for row_key, rows in expected_by_key.items():
        if len(rows) > 1:
            duplicate_expected.append((row_key, len(rows)))
        candidates = observed_by_key.get(row_key, [])
        if not candidates:
            missing.append(row_key)
            continue
        if len(candidates) > 1:
            duplicate_observed.append((row_key, len(candidates)))
        expected = rows[0]

        def score(candidate: dict[str, str]) -> tuple[float, float]:
            return (
                abs(flt(candidate.get("assigned_far")) - flt(expected.get("assigned_far"))),
                abs(flt(candidate.get("llr")) - flt(expected.get("llr"))),
            )

        observed = min(candidates, key=score)
        matched += 1
        bad_fields = []
        out = {"key": repr(row_key)}
        for field in FLOAT_FIELDS:
            ev = flt(expected.get(field))
            ov = flt(observed.get(field))
            if math.isnan(ev) and math.isnan(ov):
                continue
            diff = ov - ev
            diffs[field].append(diff)
            limit = args.llr_atol if field == "llr" else args.float_atol
            if abs(diff) > limit:
                bad_fields.append(field)
            out[f"{field}_expected"] = ev
            out[f"{field}_observed"] = ov
            out[f"{field}_diff"] = diff
        for field in ("far_source", "assigned_far_source", "direct_far_source", "assign_bg_id"):
            ev = expected.get(field, "")
            ov = observed.get(field, "")
            if ev != ov:
                bad_fields.append(field)
            out[f"{field}_expected"] = ev
            out[f"{field}_observed"] = ov
        if bad_fields:
            out["bad_fields"] = ",".join(sorted(set(bad_fields)))
            mismatches.append(out)

    for row_key, rows in observed_by_key.items():
        if row_key not in expected_by_key:
            extra.append(row_key)

    summary = {
        "expected": str(args.expected),
        "observed": str(args.observed),
        "expected_rows": len(expected_rows),
        "observed_rows": len(observed_rows),
        "expected_unique_keys": len(expected_by_key),
        "observed_unique_keys": len(observed_by_key),
        "matched_rows": matched,
        "missing_rows": len(missing),
        "extra_rows": len(extra),
        "duplicate_expected_keys": len(duplicate_expected),
        "duplicate_observed_keys": len(duplicate_observed),
        "mismatch_rows": len(mismatches),
        "expected_ifo_counts": Counter(row.get("ifo") for row in expected_rows),
        "observed_ifo_counts": Counter(row.get("ifo") for row in observed_rows),
        "diffs": {field: summarize(values) for field, values in diffs.items()},
        "missing_preview": [repr(item) for item in missing[:20]],
        "extra_preview": [repr(item) for item in extra[:20]],
        "mismatch_preview": mismatches[:20],
    }

    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.output_json).open("w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    fields = sorted(set().union(*(row.keys() for row in mismatches))) if mismatches else []
    with Path(args.mismatch_csv).open("w", newline="") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(mismatches)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
