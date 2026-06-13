#!/usr/bin/env python3
"""Compare detector-local postcoh features from two zerolag XML sets."""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


IFO_LIST = ("H1", "L1")
KEY_COLUMNS = ("ifo", "end_time", "end_time_ns", "bankid", "tmplt_idx")
DEFAULT_COMPARE_COLUMNS = ("snglsnr", "chisq", "cohsnr", "cmbchisq", "far")
MISMATCH_COLUMNS = [
    "kind",
    *KEY_COLUMNS,
    "count_a",
    "count_b",
    *[f"a_{name}" for name in DEFAULT_COMPARE_COLUMNS],
    *[f"b_{name}" for name in DEFAULT_COMPARE_COLUMNS],
    "a_source_file",
    "a_source_row",
    "b_source_file",
    "b_source_row",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Canonicalize H1/L1 detector-local features from two postcoh "
            "zerolag XML file sets and compare trigger keys plus SNR/chisq."
        )
    )
    parser.add_argument("--a-glob", action="append", required=True)
    parser.add_argument("--b-glob", action="append", required=True)
    parser.add_argument("--a-label", default="A")
    parser.add_argument("--b-label", default="B")
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--mismatch-csv", required=True)
    parser.add_argument("--rtol", type=float, default=1e-9)
    parser.add_argument("--atol", type=float, default=1e-12)
    parser.add_argument(
        "--compare-columns",
        default=",".join(DEFAULT_COMPARE_COLUMNS),
        help=(
            "comma-separated feature columns to compare; default compares "
            "snglsnr,chisq,cohsnr,cmbchisq,far"
        ),
    )
    parser.add_argument("--max-mismatch-rows", type=int, default=10000)
    return parser.parse_args()


def expand_globs(patterns: list[str]) -> list[str]:
    filenames: list[str] = []
    for pattern in patterns:
        filenames.extend(sorted(glob.glob(pattern)))
    return sorted(set(filenames))


def open_text_auto(filename: str):
    with open(filename, "rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(filename, "rt", newline="")
    return open(filename, "r", newline="")


def clean_column_name(line: str) -> str | None:
    if not line.startswith("<Column"):
        return None
    match = re.search(r'Name="([^"]+)"', line)
    if not match:
        return None
    name = match.group(1)
    if ":" in name:
        name = name.split(":", 1)[1]
    return name


def iter_postcoh_rows(filename: str):
    columns: list[str] = []
    in_table = False
    in_stream = False
    source_row = 0
    with open_text_auto(filename) as input_file:
        for raw_line in input_file:
            line = raw_line.strip()
            if not in_table and line.startswith("<Table") and 'Name="postcoh:table"' in line:
                in_table = True
                columns = []
                continue
            if in_table and not in_stream and line.startswith("</Table"):
                break
            if not in_table:
                continue
            if not in_stream:
                column = clean_column_name(line)
                if column:
                    columns.append(column)
                    continue
                if line.startswith("<Stream") and 'Name="postcoh:table"' in line:
                    in_stream = True
                continue
            if line.startswith("</Stream"):
                break
            if not line:
                continue
            values = next(csv.reader([line]))
            if len(values) > len(columns) and values[-1] == "":
                values = values[:-1]
            if len(values) != len(columns):
                continue
            source_row += 1
            yield source_row, dict(zip(columns, values))


def finite_positive(value: str | None) -> bool:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(x) and x > 0.0


def first_present(row: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return ""


def canonical_float(value: str) -> str:
    if value in (None, ""):
        return ""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(x):
        return str(value)
    return format(x, ".17g")


def detector_feature_row(filename: str, source_row: int, row: dict[str, str], ifo: str) -> dict[str, str] | None:
    generic_ifo = row.get("ifo", "").strip()
    if generic_ifo and generic_ifo != ifo:
        return None

    if generic_ifo == ifo:
        snglsnr = first_present(row, ("snglsnr", "rho"))
        chisq = first_present(row, ("chisq", "chi2"))
        end_time = first_present(row, ("end_time", f"end_time_{ifo}", f"end_time_sngl_{ifo}"))
        end_time_ns = first_present(row, ("end_time_ns", f"end_time_ns_{ifo}", f"end_time_ns_sngl_{ifo}"))
    else:
        ifos = row.get("ifos", "")
        if ifos and ifo not in ifos:
            return None
        snglsnr = first_present(row, (f"snglsnr_{ifo}", "snglsnr", "rho"))
        chisq = first_present(row, (f"chisq_{ifo}", "chisq", "chi2"))
        end_time = first_present(row, (f"end_time_sngl_{ifo}", f"end_time_{ifo}", "end_time"))
        end_time_ns = first_present(row, (f"end_time_ns_sngl_{ifo}", f"end_time_ns_{ifo}", "end_time_ns"))

    if not finite_positive(snglsnr) or not finite_positive(chisq):
        return None

    return {
        "source_file": filename,
        "source_row": str(source_row),
        "ifo": ifo,
        "end_time": str(end_time),
        "end_time_ns": str(end_time_ns),
        "bankid": str(row.get("bankid", "")),
        "tmplt_idx": str(row.get("tmplt_idx", "")),
        "snglsnr": canonical_float(snglsnr),
        "chisq": canonical_float(chisq),
        "cohsnr": canonical_float(row.get("cohsnr", "")),
        "cmbchisq": canonical_float(row.get("cmbchisq", "")),
        "far": canonical_float(row.get("far", "")),
    }


def load_features(patterns: list[str]) -> tuple[list[str], dict[tuple[str, ...], list[dict[str, str]]]]:
    filenames = expand_globs(patterns)
    rows_by_key: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for filename in filenames:
        for source_row, row in iter_postcoh_rows(filename):
            for ifo in IFO_LIST:
                feature = detector_feature_row(filename, source_row, row, ifo)
                if feature is None:
                    continue
                key = tuple(feature[name] for name in KEY_COLUMNS)
                rows_by_key[key].append(feature)
    for key in list(rows_by_key):
        rows_by_key[key].sort(key=lambda item: tuple(item[name] for name in DEFAULT_COMPARE_COLUMNS))
    return filenames, rows_by_key


def values_match(a_value: str, b_value: str, rtol: float, atol: float) -> bool:
    if a_value == b_value:
        return True
    try:
        a = float(a_value)
        b = float(b_value)
    except (TypeError, ValueError):
        return False
    return math.isclose(a, b, rel_tol=rtol, abs_tol=atol)


def compare_rows(
    a_row: dict[str, str],
    b_row: dict[str, str],
    compare_columns: tuple[str, ...],
    rtol: float,
    atol: float,
) -> list[str]:
    mismatched = []
    for name in compare_columns:
        if not values_match(a_row.get(name, ""), b_row.get(name, ""), rtol, atol):
            mismatched.append(name)
    return mismatched


def mismatch_record(
    kind: str,
    key: tuple[str, ...],
    a_row: dict[str, str] | None,
    b_row: dict[str, str] | None,
    count_a: int,
    count_b: int,
) -> dict[str, str]:
    record = {"kind": kind, "count_a": str(count_a), "count_b": str(count_b)}
    record.update(dict(zip(KEY_COLUMNS, key)))
    for name in DEFAULT_COMPARE_COLUMNS:
        record[f"a_{name}"] = a_row.get(name, "") if a_row else ""
        record[f"b_{name}"] = b_row.get(name, "") if b_row else ""
    record["a_source_file"] = a_row.get("source_file", "") if a_row else ""
    record["a_source_row"] = a_row.get("source_row", "") if a_row else ""
    record["b_source_file"] = b_row.get("source_file", "") if b_row else ""
    record["b_source_row"] = b_row.get("source_row", "") if b_row else ""
    return record


def main() -> int:
    args = parse_args()
    compare_columns = tuple(
        column.strip()
        for column in args.compare_columns.split(",")
        if column.strip()
    )
    unknown_columns = sorted(set(compare_columns) - set(DEFAULT_COMPARE_COLUMNS))
    if unknown_columns:
        raise SystemExit(f"unknown compare columns: {','.join(unknown_columns)}")
    a_files, a_rows = load_features(args.a_glob)
    b_files, b_rows = load_features(args.b_glob)

    all_keys = sorted(set(a_rows) | set(b_rows))
    mismatch_rows = []
    missing_in_a = 0
    missing_in_b = 0
    count_mismatches = 0
    value_mismatches = Counter()
    matched_keys = 0
    matched_detector_rows = 0

    for key in all_keys:
        left = a_rows.get(key, [])
        right = b_rows.get(key, [])
        if not left:
            missing_in_a += len(right)
            if len(mismatch_rows) < args.max_mismatch_rows:
                mismatch_rows.append(mismatch_record("missing_in_a", key, None, right[0], 0, len(right)))
            continue
        if not right:
            missing_in_b += len(left)
            if len(mismatch_rows) < args.max_mismatch_rows:
                mismatch_rows.append(mismatch_record("missing_in_b", key, left[0], None, len(left), 0))
            continue
        if len(left) != len(right):
            count_mismatches += abs(len(left) - len(right))
            if len(mismatch_rows) < args.max_mismatch_rows:
                mismatch_rows.append(mismatch_record("duplicate_count_mismatch", key, left[0], right[0], len(left), len(right)))
        for a_row, b_row in zip(left, right):
            mismatched = compare_rows(a_row, b_row, compare_columns, args.rtol, args.atol)
            if mismatched:
                for name in mismatched:
                    value_mismatches[name] += 1
                if len(mismatch_rows) < args.max_mismatch_rows:
                    mismatch_rows.append(
                        mismatch_record(
                            "value_mismatch:" + ",".join(mismatched),
                            key,
                            a_row,
                            b_row,
                            len(left),
                            len(right),
                        )
                    )
            else:
                matched_detector_rows += 1
        matched_keys += 1

    Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.mismatch_csv).parent.mkdir(parents=True, exist_ok=True)

    with open(args.mismatch_csv, "w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=MISMATCH_COLUMNS)
        writer.writeheader()
        writer.writerows(mismatch_rows)

    summary = {
        "a_label": args.a_label,
        "b_label": args.b_label,
        "a_files": len(a_files),
        "b_files": len(b_files),
        "a_detector_rows": sum(len(rows) for rows in a_rows.values()),
        "b_detector_rows": sum(len(rows) for rows in b_rows.values()),
        "a_unique_keys": len(a_rows),
        "b_unique_keys": len(b_rows),
        "matched_keys": matched_keys,
        "matched_detector_rows": matched_detector_rows,
        "missing_in_a_rows": missing_in_a,
        "missing_in_b_rows": missing_in_b,
        "duplicate_count_mismatches": count_mismatches,
        "value_mismatches": dict(value_mismatches),
        "mismatch_rows_written": len(mismatch_rows),
        "summary_json": args.summary_json,
        "mismatch_csv": args.mismatch_csv,
        "compare_columns": list(compare_columns),
        "rtol": args.rtol,
        "atol": args.atol,
        "status": "PASS"
        if not missing_in_a
        and not missing_in_b
        and not count_mismatches
        and not value_mismatches
        else "FAIL",
    }
    with open(args.summary_json, "w") as output_file:
        json.dump(summary, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
