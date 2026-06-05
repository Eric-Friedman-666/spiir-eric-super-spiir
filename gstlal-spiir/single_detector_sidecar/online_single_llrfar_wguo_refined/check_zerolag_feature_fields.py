#!/usr/bin/env python3
"""Check whether postcoh zerolag XML files expose detector-local features."""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import math
import re
from pathlib import Path


IFO_LIST = ("H1", "L1")
OUTPUT_COLUMNS = [
    "source_file",
    "source_row",
    "ifo",
    "end_time",
    "end_time_ns",
    "bankid",
    "tmplt_idx",
    "snglsnr",
    "chisq",
    "cohsnr",
    "cmbchisq",
    "far",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read postcoh zerolag XML snapshots and report whether H1/L1 "
            "snglsnr and chisq are available for detector-local extraction."
        )
    )
    parser.add_argument("--glob", action="append", required=True)
    parser.add_argument("--output-csv")
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--max-output-rows", type=int, default=1000)
    return parser.parse_args()


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
    saw_postcoh_table = False
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
                    saw_postcoh_table = True
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
            yield source_row, dict(zip(columns, values)), columns
    if not saw_postcoh_table:
        yield 0, {"__missing_postcoh_table__": "1"}, columns


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
        "end_time": end_time,
        "end_time_ns": end_time_ns,
        "bankid": row.get("bankid", ""),
        "tmplt_idx": row.get("tmplt_idx", ""),
        "snglsnr": snglsnr,
        "chisq": chisq,
        "cohsnr": row.get("cohsnr", ""),
        "cmbchisq": row.get("cmbchisq", ""),
        "far": row.get("far", ""),
    }


def main() -> int:
    args = parse_args()
    filenames: list[str] = []
    for pattern in args.glob:
        filenames.extend(sorted(glob.glob(pattern)))
    filenames = sorted(set(filenames))
    if args.max_files is not None:
        filenames = filenames[:args.max_files]

    summary = {
        "files": len(filenames),
        "postcoh_rows": 0,
        "missing_postcoh_table_files": 0,
        "files_with_detector_fields": {ifo: 0 for ifo in IFO_LIST},
        "detector_rows": {ifo: 0 for ifo in IFO_LIST},
        "coherent_rows_with_cohsnr_cmbchisq": 0,
        "seen_columns": [],
        "example_files": filenames[:5],
    }
    seen_columns: set[str] = set()
    output_rows: list[dict[str, str]] = []

    for filename in filenames:
        file_has_detector = {ifo: False for ifo in IFO_LIST}
        for source_row, row, columns in iter_postcoh_rows(filename):
            seen_columns.update(columns)
            if row.get("__missing_postcoh_table__"):
                summary["missing_postcoh_table_files"] += 1
                continue
            summary["postcoh_rows"] += 1
            if finite_positive(row.get("cohsnr")) and finite_positive(row.get("cmbchisq")):
                summary["coherent_rows_with_cohsnr_cmbchisq"] += 1
            for ifo in IFO_LIST:
                feature = detector_feature_row(filename, source_row, row, ifo)
                if feature is None:
                    continue
                file_has_detector[ifo] = True
                summary["detector_rows"][ifo] += 1
                if len(output_rows) < args.max_output_rows:
                    output_rows.append(feature)
        for ifo, present in file_has_detector.items():
            if present:
                summary["files_with_detector_fields"][ifo] += 1

    summary["seen_columns"] = sorted(seen_columns)

    if args.output_csv:
        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(output_rows)
        summary["output_csv"] = str(output_path)
        summary["output_rows"] = len(output_rows)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
