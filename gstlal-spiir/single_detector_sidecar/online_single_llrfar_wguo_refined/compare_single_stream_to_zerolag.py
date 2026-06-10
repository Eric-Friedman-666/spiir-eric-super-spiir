#!/usr/bin/env python3
"""Compare the near-real-time single-trigger stream against zerolag XML.

The stream is meant to be a low-latency mirror of rows that later appear in the
formal zerolag snapshots.  This checker standardizes both inputs through
extract_zerolag_features.py and then compares the physical trigger rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXTRACT_SCRIPT = SCRIPT_DIR / "extract_zerolag_features.py"
SNAPSHOT_RE = re.compile(
    r"(?:_zerolag_|sdpostcoh[^/_]*_|single_postcoh[^/_]*_)(\d+)_(\d+)\.xml(?:\.gz)?$"
)

COMPARE_COLUMNS = [
    "bank_group",
    "bankid",
    "event_id",
    "ifos",
    "ifo",
    "is_background",
    "end_time",
    "end_time_ns",
    "rho",
    "snglsnr",
    "chisq",
    "cohsnr",
    "cmbchisq",
    "far",
    "fap",
    "far_1d",
    "far_1w",
    "far_2h",
    "end_time_sngl_H1",
    "end_time_ns_sngl_H1",
    "end_time_sngl_L1",
    "end_time_ns_sngl_L1",
    "snglsnr_H1",
    "snglsnr_L1",
    "chisq_H1",
    "chisq_L1",
    "mass1",
    "mass2",
    "mchirp",
    "tmplt_idx",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=".")
    parser.add_argument("--worker", default=None,
                        help="worker/bank-group tag such as 000 or 001")
    parser.add_argument("--stream-csv", default=None)
    parser.add_argument("--zerolag-glob", action="append", default=None)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--mismatch-csv", required=True)
    parser.add_argument("--min-snr", type=float, default=4.0)
    parser.add_argument("--banks-per-group", type=int, default=6)
    return parser.parse_args()


def snapshot_end_from_name(path: str) -> float | None:
    match = SNAPSHOT_RE.search(path)
    if not match:
        return None
    return float(int(match.group(1)) + int(match.group(2)))


def expand_inputs(patterns: list[str]) -> list[str]:
    import glob

    paths: list[str] = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern))
    return sorted(set(paths))


def run_extract(globs: list[str], output_csv: Path, summary_json: Path,
                max_end_gps: float | None, args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(EXTRACT_SCRIPT),
    ]
    for pattern in globs:
        cmd.extend(["--glob", pattern])
    cmd.extend([
        "--output", str(output_csv),
        "--summary", str(summary_json),
        "--min-snr", str(args.min_snr),
        "--banks-per-group", str(args.banks_per_group),
    ])
    if max_end_gps is not None:
        cmd.extend(["--max-snapshot-end-gps", str(max_end_gps)])
    subprocess.check_call(cmd, cwd=str(Path(args.run_dir).resolve()))


def norm(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text == "":
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if math.isnan(number) or math.isinf(number):
        return text
    return "%.12g" % number


def row_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
        norm(row.get("event_id")),
        norm(row.get("ifos")),
        norm(row.get("end_time")),
        norm(row.get("end_time_ns")),
        norm(row.get("bankid")),
        norm(row.get("tmplt_idx")),
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open(newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def index_rows(rows: list[dict[str, str]]) -> tuple[dict[tuple[str, ...], dict[str, str]], dict[tuple[str, ...], int]]:
    indexed: dict[tuple[str, ...], dict[str, str]] = {}
    duplicates: dict[tuple[str, ...], int] = {}
    for row in rows:
        key = row_key(row)
        if key in indexed:
            duplicates[key] = duplicates.get(key, 1) + 1
            continue
        indexed[key] = row
    return indexed, duplicates


def write_mismatches(path: Path, mismatches: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["kind", "key", "column", "stream_value", "zerolag_value"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in mismatches:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    worker = args.worker
    if worker is not None:
        worker = f"{int(worker):03d}" if worker.isdigit() else worker

    if args.stream_csv:
        stream_csv = args.stream_csv
    elif worker:
        stream_csv = f"{worker}/{worker}_single_triggers.csv"
    else:
        stream_csv = "[0-9][0-9][0-9]/*_single_triggers.csv"

    if args.zerolag_glob:
        zerolag_globs = args.zerolag_glob
    elif worker:
        zerolag_globs = [f"{worker}/{worker}_zerolag_*.xml.gz"]
    else:
        zerolag_globs = ["[0-9][0-9][0-9]/*_zerolag_*.xml.gz"]

    zerolag_files = expand_inputs([str(run_dir / pattern) for pattern in zerolag_globs])
    latest_zerolag_end = max(
        (end for end in (snapshot_end_from_name(path) for path in zerolag_files)
         if end is not None),
        default=None,
    )

    with tempfile.TemporaryDirectory(prefix="single_stream_compare_") as tmp:
        tmpdir = Path(tmp)
        stream_features = tmpdir / "stream_features.csv"
        zerolag_features = tmpdir / "zerolag_features.csv"
        stream_summary = tmpdir / "stream_summary.json"
        zerolag_summary = tmpdir / "zerolag_summary.json"
        run_extract([stream_csv], stream_features, stream_summary,
                    latest_zerolag_end, args)
        run_extract(zerolag_globs, zerolag_features, zerolag_summary,
                    latest_zerolag_end, args)
        stream_rows = read_rows(stream_features)
        zerolag_rows = read_rows(zerolag_features)

    stream_index, stream_dupes = index_rows(stream_rows)
    zerolag_index, zerolag_dupes = index_rows(zerolag_rows)

    mismatches: list[dict[str, str]] = []
    for key in sorted(set(stream_index) - set(zerolag_index)):
        mismatches.append({
            "kind": "missing_in_zerolag",
            "key": "|".join(key),
        })
    for key in sorted(set(zerolag_index) - set(stream_index)):
        mismatches.append({
            "kind": "missing_in_stream",
            "key": "|".join(key),
        })
    for key in sorted(set(stream_index) & set(zerolag_index)):
        stream_row = stream_index[key]
        zerolag_row = zerolag_index[key]
        for column in COMPARE_COLUMNS:
            stream_value = norm(stream_row.get(column))
            zerolag_value = norm(zerolag_row.get(column))
            if stream_value != zerolag_value:
                mismatches.append({
                    "kind": "value_mismatch",
                    "key": "|".join(key),
                    "column": column,
                    "stream_value": stream_value,
                    "zerolag_value": zerolag_value,
                })

    for key, count in sorted(stream_dupes.items()):
        mismatches.append({
            "kind": "duplicate_in_stream",
            "key": "|".join(key),
            "stream_value": str(count),
        })
    for key, count in sorted(zerolag_dupes.items()):
        mismatches.append({
            "kind": "duplicate_in_zerolag",
            "key": "|".join(key),
            "zerolag_value": str(count),
        })

    summary = {
        "pass": not mismatches,
        "run_dir": str(run_dir),
        "worker": worker,
        "stream_csv": stream_csv,
        "zerolag_globs": zerolag_globs,
        "zerolag_files": len(zerolag_files),
        "latest_zerolag_end_gps": latest_zerolag_end,
        "stream_rows": len(stream_rows),
        "zerolag_rows": len(zerolag_rows),
        "matched_rows": len(set(stream_index) & set(zerolag_index)),
        "missing_in_stream": sum(1 for row in mismatches if row["kind"] == "missing_in_stream"),
        "missing_in_zerolag": sum(1 for row in mismatches if row["kind"] == "missing_in_zerolag"),
        "value_mismatches": sum(1 for row in mismatches if row["kind"] == "value_mismatch"),
        "duplicate_in_stream": sum(1 for row in mismatches if row["kind"] == "duplicate_in_stream"),
        "duplicate_in_zerolag": sum(1 for row in mismatches if row["kind"] == "duplicate_in_zerolag"),
        "compare_columns": COMPARE_COLUMNS,
    }

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    write_mismatches(Path(args.mismatch_csv), mismatches)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
