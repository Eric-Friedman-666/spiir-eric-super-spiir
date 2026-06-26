#!/usr/bin/env python3
"""Convert crashcar C detail CSV rows into detector-local feature CSV rows.

The C crashcar element runs inside each worker process, so its detail CSVs are
worker-local.  This converter emits the same detector-local feature surface
consumed by ``assign_frozen_far_ledger.py`` while preserving the single-FAR
values already assigned by the in-graph C element.  That keeps crashcar's final
ledger tied to the GStreamer branch boundary instead of reassigning the same
rows from zerolag XML snapshots.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob
import json
import math
import os
import re
from pathlib import Path

from extract_zerolag_features import OUTPUT_COLUMNS


IFO_BY_ID = {"0": "H1", "1": "L1", "2": "V1", "3": "K1"}
GPS_UTC_OFFSET_SECONDS = 18
GPS_EPOCH_UNIX = 315964800


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--min-snr", type=float, default=4.0)
    parser.add_argument("--banks-per-group", type=int, default=6)
    parser.add_argument("--max-snapshot-end-gps", type=float, default=None)
    parser.add_argument("--min-snapshot-end-gps", type=float, default=None)
    parser.add_argument(
        "--force-is-background",
        choices=("0", "1"),
        default=None,
        help=("override is_background on selected rows; use 1 when replaying "
              "crashcar detail rows as a pure background-accumulation set"))
    return parser.parse_args()


def finite_number(value: str | None) -> bool:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(x)


def finite_positive(value: str | None) -> bool:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(x) and x > 0.0


def gps_to_utc_label(gps: float | int | str | None) -> str | None:
    if gps is None:
        return None
    try:
        gps_float = float(gps)
    except (TypeError, ValueError):
        return None
    unix_time = gps_float + GPS_EPOCH_UNIX - GPS_UTC_OFFSET_SECONDS
    return _dt.datetime.fromtimestamp(unix_time, _dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def intish(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def worker_from_filename(filename: str) -> str:
    match = re.search(r"worker(\d+)", Path(filename).name)
    if not match:
        return ""
    return f"{int(match.group(1)):03d}"


def bank_group(row: dict[str, str], banks_per_group: int, filename: str) -> str:
    bankid = intish(row.get("bankid"))
    if bankid is not None and banks_per_group > 0:
        return f"{bankid // banks_per_group:03d}"
    return worker_from_filename(filename)


def row_end_gps(row: dict[str, str]) -> float | None:
    if finite_number(row.get("feature_gps")):
        return float(row["feature_gps"])
    if not finite_number(row.get("end_time")):
        return None
    gps = float(row["end_time"])
    if finite_number(row.get("end_time_ns")):
        gps += float(row["end_time_ns"]) * 1.0e-9
    return gps


def ifo_from_row(row: dict[str, str]) -> str:
    value = str(row.get("ifo_id", "")).strip()
    if value in IFO_BY_ID:
        return IFO_BY_ID[value]
    try:
        return IFO_BY_ID[str(int(float(value)))]
    except (TypeError, ValueError, KeyError):
        return ""


def selected_output_row(filename: str,
                        source_row: int,
                        row: dict[str, str],
                        banks_per_group: int,
                        forced_is_background: str | None = None) -> dict[str, str]:
    output = {name: "" for name in OUTPUT_COLUMNS}
    ifo = ifo_from_row(row)
    output["source_file"] = filename
    output["source_row"] = str(source_row)
    output["bank_group"] = bank_group(row, banks_per_group, filename)
    output["bankid"] = row.get("bankid", "")
    output["event_id"] = row.get("event_id", "")
    output["ifos"] = ifo
    output["ifo"] = ifo
    output["is_background"] = forced_is_background or row.get("is_background", "0")
    output["end_time"] = row.get("end_time", "")
    output["end_time_ns"] = row.get("end_time_ns", "")
    output["rho"] = row.get("snglsnr", "")
    output["snglsnr"] = row.get("snglsnr", "")
    output["chisq"] = row.get("chisq", "")
    output["cohsnr"] = row.get("cohsnr", "")
    output["cmbchisq"] = row.get("cmbchisq", "")
    output["far"] = row.get("far_multi", "")
    output["fap"] = row.get("fap", "")
    output["far_1d"] = (
        row.get("far_1d_sngl", "") or row.get("far_sngl", "")
        or row.get("far_1d", ""))
    output["far_1w"] = (
        row.get("far_1w_sngl", "") or row.get("far_sngl", "")
        or row.get("far_1w", ""))
    output["far_2h"] = (
        row.get("far_2h_sngl", "") or row.get("far_sngl", "")
        or row.get("far_2h", ""))
    output["mass1"] = row.get("mass1", "")
    output["mass2"] = row.get("mass2", "")
    output["mchirp"] = row.get("mchirp", "")
    output["tmplt_idx"] = row.get("tmplt_idx", "")
    if ifo in ("H1", "L1"):
        output[f"end_time_sngl_{ifo}"] = row.get("end_time", "")
        output[f"end_time_ns_sngl_{ifo}"] = row.get("end_time_ns", "")
        output[f"snglsnr_{ifo}"] = row.get("snglsnr", "")
        output[f"chisq_{ifo}"] = row.get("chisq", "")
    return output


def main() -> int:
    args = parse_args()
    filenames: list[str] = []
    for pattern in args.glob:
        filenames.extend(glob.glob(pattern))
    filenames = sorted(set(filenames))

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    selected_rows = 0
    filtered_before_window = 0
    filtered_after_window = 0
    skipped_corrupt_files = 0
    feature_counts = {"H1": 0, "L1": 0}
    gps_values: list[float] = []
    gps_by_ifo = {"H1": [], "L1": []}
    groups = set()

    tmp_output = args.output + ".tmp"
    with open(tmp_output, "w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for filename in filenames:
            try:
                with open(filename, newline="") as input_file:
                    reader = csv.DictReader(input_file)
                    for source_row, row in enumerate(reader, start=1):
                        total_rows += 1
                        gps = row_end_gps(row)
                        if (args.min_snapshot_end_gps is not None
                                and gps is not None
                                and gps <= args.min_snapshot_end_gps):
                            filtered_before_window += 1
                            continue
                        if (args.max_snapshot_end_gps is not None
                                and gps is not None
                                and gps > args.max_snapshot_end_gps):
                            filtered_after_window += 1
                            continue
                        ifo = ifo_from_row(row)
                        if ifo not in ("H1", "L1"):
                            continue
                        if not finite_positive(row.get("snglsnr")):
                            continue
                        if float(row["snglsnr"]) < args.min_snr:
                            continue
                        if not finite_positive(row.get("chisq")):
                            continue
                        output = selected_output_row(
                            filename, source_row, row, args.banks_per_group,
                            args.force_is_background)
                        writer.writerow(output)
                        selected_rows += 1
                        feature_counts[ifo] += 1
                        groups.add(output["bank_group"])
                        if gps is not None:
                            gps_values.append(gps)
                            gps_by_ifo[ifo].append(gps)
            except (OSError, csv.Error):
                skipped_corrupt_files += 1
    os.replace(tmp_output, args.output)

    gps_start = min(gps_values) if gps_values else None
    gps_end = max(gps_values) if gps_values else None
    duration = max(0.0, gps_end - gps_start) if gps_start is not None and gps_end is not None else 0.0
    latest_snapshot_end = gps_end
    summary = {
        "input_snapshot_kind": "crashcar_detail_csv",
        "files": len(filenames),
        "input_files": len(filenames),
        "filtered_files": 0,
        "filtered_before_window_rows": filtered_before_window,
        "filtered_after_window_rows": filtered_after_window,
        "skipped_corrupt_files": skipped_corrupt_files,
        "min_snapshot_end_gps": args.min_snapshot_end_gps,
        "min_snapshot_end_utc": gps_to_utc_label(args.min_snapshot_end_gps),
        "max_snapshot_end_gps": args.max_snapshot_end_gps,
        "max_snapshot_end_utc": gps_to_utc_label(args.max_snapshot_end_gps),
        "latest_snapshot_end_gps": latest_snapshot_end,
        "latest_snapshot_end_utc": gps_to_utc_label(latest_snapshot_end),
        "postcoh_rows": total_rows,
        "feature_rows_total": selected_rows,
        "feature_rows_H1": feature_counts["H1"],
        "feature_rows_L1": feature_counts["L1"],
        "foreground_feature_rows_total": selected_rows,
        "data_gps_start": gps_start,
        "data_gps_start_utc": gps_to_utc_label(gps_start),
        "data_gps_end": gps_end,
        "data_gps_end_utc": gps_to_utc_label(gps_end),
        "data_duration_seconds": duration,
        "duration_seconds": duration,
        "duration_hours": duration / 3600.0 if duration else 0.0,
        "duration_source": "crashcar_detail_feature_rows",
        "bank_groups": sorted(group for group in groups if group),
        "bank_ranges": [
            f"{int(group) * args.banks_per_group:04d}-"
            f"{int(group) * args.banks_per_group + args.banks_per_group - 1:04d}"
            for group in sorted(group for group in groups if group)
        ],
        "output": args.output,
        "updated_utc": _dt.datetime.now(_dt.timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    for ifo in ("H1", "L1"):
        values = gps_by_ifo[ifo]
        start = min(values) if values else None
        end = max(values) if values else None
        summary[f"data_gps_start_{ifo}"] = start
        summary[f"data_gps_start_utc_{ifo}"] = gps_to_utc_label(start)
        summary[f"data_gps_end_{ifo}"] = end
        summary[f"data_gps_end_utc_{ifo}"] = gps_to_utc_label(end)
        summary[f"feature_gps_start_{ifo}"] = start
        summary[f"feature_gps_end_{ifo}"] = end
        summary[f"feature_duration_seconds_{ifo}"] = (
            max(0.0, end - start) if start is not None and end is not None else 0.0)

    with open(args.summary, "w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
