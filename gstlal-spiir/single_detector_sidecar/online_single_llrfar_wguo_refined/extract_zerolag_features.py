#!/usr/bin/env python3
"""Extract SPIIR postcoh snapshot rows into a detector-local feature CSV."""

from __future__ import annotations

import argparse
import csv
import gzip
import glob
import json
import math
import os
import re
import datetime as _dt
from pathlib import Path


OUTPUT_COLUMNS = [
    "source_file",
    "source_row",
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

SNAPSHOT_RE = re.compile(
    r"(?:_zerolag_|sdpostcoh[^/_]*_|single_postcoh[^/_]*_)(\d+)_(\d+)\.xml(?:\.gz)?$"
)
GPS_UTC_OFFSET_SECONDS = 18
GPS_EPOCH_UNIX = 315964800


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--min-snr", type=float, default=4.0)
    parser.add_argument("--banks-per-group", type=int, default=6)
    parser.add_argument(
        "--max-snapshot-end-gps",
        type=float,
        default=None,
        help="only read postcoh snapshots whose filename start+duration is <= this GPS time",
    )
    parser.add_argument(
        "--min-snapshot-end-gps",
        type=float,
        default=None,
        help="only read postcoh snapshots whose filename start+duration is > this GPS time",
    )
    return parser.parse_args()


def open_text(filename: str):
    if filename.endswith(".gz"):
        return gzip.open(filename, "rt", newline="")
    return open(filename, "r", newline="")


def clean_column_name(line: str) -> str | None:
    if not line.startswith("<Column"):
        return None
    match = re.search(r'Name="postcoh:([^"]+)"', line)
    return match.group(1) if match else None


def bank_group_from_path(filename: str) -> str:
    parent = Path(filename).parent.name
    return parent if re.fullmatch(r"\d{3}", parent) else ""


def snapshot_bounds_from_filename(filename: str) -> tuple[float, float] | None:
    match = SNAPSHOT_RE.search(filename)
    if not match:
        return None
    start = float(int(match.group(1)))
    duration = float(int(match.group(2)))
    return start, start + duration


def snapshot_end_from_filename(filename: str) -> float | None:
    bounds = snapshot_bounds_from_filename(filename)
    if bounds is None:
        return None
    return bounds[1]


def gps_to_utc_label(gps: float | int | str | None) -> str | None:
    if gps is None:
        return None
    try:
        gps_float = float(gps)
    except (TypeError, ValueError):
        return None
    unix_time = gps_float + GPS_EPOCH_UNIX - GPS_UTC_OFFSET_SECONDS
    return _dt.datetime.utcfromtimestamp(unix_time).replace(
        microsecond=0).isoformat() + "Z"


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


def row_feature_counts(row: dict[str, str], min_snr: float) -> dict[str, int]:
    counts = {"H1": 0, "L1": 0}
    generic_ifo = row.get("ifo", "").strip()
    if generic_ifo in counts:
        rho = first_present(row, ("rho", "snglsnr"))
        chisq = first_present(row, ("chisq", "chi2"))
        if finite_positive(rho) and finite_positive(chisq):
            if float(rho) >= min_snr:
                counts[generic_ifo] += 1
        return counts

    ifos = row.get("ifos", "")
    for ifo in ("H1", "L1"):
        if ifos and ifo not in ifos:
            continue
        if finite_positive(row.get(f"snglsnr_{ifo}")) and finite_positive(row.get(f"chisq_{ifo}")):
            if float(row[f"snglsnr_{ifo}"]) >= min_snr:
                counts[ifo] += 1
    return counts


def row_ifo_gps(row: dict[str, str], ifo: str) -> float | None:
    generic_ifo = row.get("ifo", "").strip()
    if generic_ifo and generic_ifo != ifo:
        return None
    if generic_ifo == ifo:
        for key in ("end_time", f"end_time_{ifo}", f"end_time_sngl_{ifo}"):
            if finite_positive(row.get(key)):
                return float(row[key])
    for key in (
            f"end_time_sngl_{ifo}",
            f"end_time_{ifo}",
            "end_time"):
        if finite_positive(row.get(key)):
            return float(row[key])
    return None


def is_foreground_row(row: dict[str, str]) -> bool:
    value = str(row.get("is_background", "0")).strip().lower()
    return value in {"", "0", "false", "foreground"}


def selected_output_row(filename: str, source_row: int, row: dict[str, str]) -> dict[str, str]:
    output = {name: row.get(name, "") for name in OUTPUT_COLUMNS}
    output["source_file"] = filename
    output["source_row"] = str(source_row)
    output["bank_group"] = bank_group_from_path(filename)
    generic_ifo = row.get("ifo", "").strip()
    if generic_ifo in {"H1", "L1"}:
        rho = first_present(row, ("rho", "snglsnr"))
        chisq = first_present(row, ("chisq", "chi2"))
        output["ifos"] = row.get("ifos", generic_ifo) or generic_ifo
        output["ifo"] = generic_ifo
        output["rho"] = rho
        output["snglsnr"] = rho
        output["chisq"] = chisq
        output[f"snglsnr_{generic_ifo}"] = rho
        output[f"chisq_{generic_ifo}"] = chisq
        output[f"end_time_sngl_{generic_ifo}"] = row.get("end_time", "")
        output[f"end_time_ns_sngl_{generic_ifo}"] = row.get("end_time_ns", "")
    output["event_id"] = row.get("event_id", row.get("peak_index", ""))
    output["end_time_sngl_H1"] = row.get("end_time_H1", row.get("end_time_sngl_H1", ""))
    output["end_time_ns_sngl_H1"] = row.get("end_time_ns_H1", row.get("end_time_ns_sngl_H1", ""))
    output["end_time_sngl_L1"] = row.get("end_time_L1", row.get("end_time_sngl_L1", ""))
    output["end_time_ns_sngl_L1"] = row.get("end_time_ns_L1", row.get("end_time_ns_sngl_L1", ""))
    return output


def iter_single_trigger_csv_rows(filename: str):
    source_row = 0
    with open_text(filename) as input_file:
        reader = csv.DictReader(input_file)
        for row in reader:
            source_row += 1
            yield source_row, row


def iter_postcoh_rows(filename: str):
    columns: list[str] = []
    in_stream = False
    source_row = 0
    with open_text(filename) as input_file:
        for raw_line in input_file:
            line = raw_line.strip()
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


def bank_ranges(groups: set[str], banks_per_group: int) -> list[str]:
    ranges = []
    for group in sorted(groups):
        if not group:
            continue
        start = int(group) * banks_per_group
        end = start + banks_per_group - 1
        ranges.append(f"{start:04d}-{end:04d}")
    return ranges


def row_end_gps(row: dict[str, str]) -> float | None:
    values = []
    for ifo in ("H1", "L1"):
        value = row_ifo_gps(row, ifo)
        if value is not None:
            values.append(value)
    if finite_positive(row.get("end_time")):
        values.append(float(row["end_time"]))
    return max(values) if values else None


def is_single_trigger_boundary_row(row: dict[str, str]) -> bool:
    return str(row.get("source_kind", "")).strip() == "chunk_boundary"


def boundary_coverage_seconds(values: list[float]) -> float:
    """Return livetime covered by boundary-end timestamps.

    The single-trigger side branch writes one ``chunk_boundary`` row at the end
    of each processed data chunk.  A continuous 120-second window therefore has
    120 boundary endpoints but only 119 seconds between the first and last
    endpoint, so a plain max-min span undercounts the covered livetime by one
    chunk.
    """
    unique = sorted(set(values))
    if len(unique) < 2:
        return 0.0
    diffs = [
        unique[i + 1] - unique[i]
        for i in range(len(unique) - 1)
        if unique[i + 1] > unique[i]
    ]
    if not diffs:
        return 0.0
    diffs.sort()
    cadence = diffs[len(diffs) // 2]
    if not math.isfinite(cadence) or cadence <= 0.0:
        return max(0.0, unique[-1] - unique[0])
    return float(len(unique)) * cadence


def file_looks_like_single_csv(filename: str) -> bool:
    return filename.endswith(".csv")


def main() -> int:
    args = parse_args()
    filenames = []
    for pattern in args.glob:
        filenames.extend(glob.glob(pattern))
    filenames = sorted(set(filenames))
    input_files = len(filenames)
    selected_filenames = []
    filtered_before_window = 0
    filtered_after_window = 0
    for filename in filenames:
        snapshot_end = snapshot_end_from_filename(filename)
        if snapshot_end is None:
            if file_looks_like_single_csv(filename):
                selected_filenames.append(filename)
            continue
        if (args.min_snapshot_end_gps is not None
                and snapshot_end <= args.min_snapshot_end_gps):
            filtered_before_window += 1
            continue
        if (args.max_snapshot_end_gps is not None
                and snapshot_end > args.max_snapshot_end_gps):
            filtered_after_window += 1
            continue
        selected_filenames.append(filename)
    filenames = selected_filenames

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    skipped_corrupt_files = 0
    feature_counts = {"H1": 0, "L1": 0}
    groups = set()
    data_gps_values = []
    data_gps_by_ifo = {"H1": [], "L1": []}
    feature_gps_values = []
    feature_gps_by_ifo = {"H1": [], "L1": []}

    tmp_output = args.output + ".tmp"
    with open(tmp_output, "w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for filename in filenames:
            groups.add(bank_group_from_path(filename))
            is_single_csv = file_looks_like_single_csv(filename)
            row_iter = iter_single_trigger_csv_rows if is_single_csv else iter_postcoh_rows
            try:
                for source_row, row in row_iter(filename):
                    total_rows += 1
                    row_gps = row_end_gps(row)
                    if (is_single_csv and args.min_snapshot_end_gps is not None
                            and row_gps is not None
                            and row_gps <= args.min_snapshot_end_gps):
                        continue
                    if (is_single_csv and args.max_snapshot_end_gps is not None
                            and row_gps is not None
                            and row_gps > args.max_snapshot_end_gps):
                        continue
                    data_time_row = (
                        (not is_single_csv)
                        or is_single_trigger_boundary_row(row)
                    )
                    if data_time_row and row_gps is not None:
                        data_gps_values.append(row_gps)
                    for ifo in ("H1", "L1"):
                        ifo_gps = row_ifo_gps(row, ifo)
                        if data_time_row and ifo_gps is not None:
                            data_gps_by_ifo[ifo].append(ifo_gps)
                    if not is_foreground_row(row):
                        continue
                    counts = row_feature_counts(row, args.min_snr)
                    for ifo, count in counts.items():
                        feature_counts[ifo] += count
                        if count:
                            ifo_gps = row_ifo_gps(row, ifo)
                            if ifo_gps is not None:
                                feature_gps_by_ifo[ifo].append(ifo_gps)
                    if row_gps is not None:
                        feature_gps_values.append(row_gps)
                    writer.writerow(selected_output_row(filename, source_row, row))
            except (EOFError, OSError, gzip.BadGzipFile):
                skipped_corrupt_files += 1
                continue
    os.replace(tmp_output, args.output)

    selected_snapshot_bounds = [
        bounds for bounds in (snapshot_bounds_from_filename(filename)
                              for filename in filenames)
        if bounds is not None
    ]
    snapshot_gps_start = min((bounds[0] for bounds in selected_snapshot_bounds),
                             default=None)
    snapshot_gps_end = max((bounds[1] for bounds in selected_snapshot_bounds),
                           default=None)
    snapshot_duration = 0.0
    if snapshot_gps_start is not None and snapshot_gps_end is not None:
        snapshot_duration = max(0.0, snapshot_gps_end - snapshot_gps_start)

    feature_duration = 0.0
    if feature_gps_values:
        feature_duration = max(feature_gps_values) - min(feature_gps_values)
    latest_snapshot_end = max(
        (end for end in (snapshot_end_from_filename(filename)
                         for filename in filenames)
         if end is not None),
        default=None,
    )
    feature_gps_start = min(feature_gps_values) if feature_gps_values else None
    feature_gps_end = max(feature_gps_values) if feature_gps_values else None
    data_gps_start = min(data_gps_values) if data_gps_values else None
    data_gps_end = max(data_gps_values) if data_gps_values else None
    data_duration = 0.0
    if data_gps_values:
        if any(file_looks_like_single_csv(filename) for filename in filenames):
            data_duration = boundary_coverage_seconds(data_gps_values)
        else:
            data_duration = max(data_gps_values) - min(data_gps_values)
    data_gps_start_by_ifo = {
        ifo: min(values) if values else None
        for ifo, values in data_gps_by_ifo.items()
    }
    data_gps_end_by_ifo = {
        ifo: max(values) if values else None
        for ifo, values in data_gps_by_ifo.items()
    }
    data_duration_by_ifo = {
        ifo: (
            boundary_coverage_seconds(data_gps_by_ifo[ifo])
            if any(file_looks_like_single_csv(filename) for filename in filenames)
            else (
                data_gps_end_by_ifo[ifo] - data_gps_start_by_ifo[ifo]
                if data_gps_start_by_ifo[ifo] is not None
                and data_gps_end_by_ifo[ifo] is not None
                else 0.0
            )
        )
        for ifo in ("H1", "L1")
    }
    feature_gps_start_by_ifo = {
        ifo: min(values) if values else None
        for ifo, values in feature_gps_by_ifo.items()
    }
    feature_gps_end_by_ifo = {
        ifo: max(values) if values else None
        for ifo, values in feature_gps_by_ifo.items()
    }
    feature_duration_by_ifo = {
        ifo: (feature_gps_end_by_ifo[ifo] - feature_gps_start_by_ifo[ifo])
        if feature_gps_start_by_ifo[ifo] is not None
        and feature_gps_end_by_ifo[ifo] is not None
        else 0.0
        for ifo in ("H1", "L1")
    }
    # A live gzip snapshot can be visible before the writer has closed it.  In
    # that case the filename advertises the full snapshot span, but the parser
    # may only have seen a prefix of the rows.  Use the detector-row time range
    # until all selected gzip files are complete so the background livetime is
    # never credited for data that has not actually been extracted yet.
    if any(file_looks_like_single_csv(filename) for filename in filenames) and data_gps_values:
        gps_start = data_gps_start
        gps_end = data_gps_end
        duration = data_duration
        duration_source = "single_trigger_csv_boundaries"
    elif skipped_corrupt_files and feature_gps_values:
        gps_start = feature_gps_start
        gps_end = feature_gps_end
        duration = feature_duration
        duration_source = "feature_rows_due_to_open_gzip"
    else:
        gps_start = snapshot_gps_start if snapshot_gps_start is not None else feature_gps_start
        gps_end = snapshot_gps_end if snapshot_gps_end is not None else feature_gps_end
        duration = snapshot_duration if selected_snapshot_bounds else feature_duration
        duration_source = "snapshot_filename" if selected_snapshot_bounds else "feature_rows"
    summary = {
        "input_snapshot_kind": "single_trigger_csv"
        if any(file_looks_like_single_csv(filename) for filename in filenames)
        and not selected_snapshot_bounds else "postcoh",
        "files": len(filenames),
        "input_files": input_files,
        "filtered_files": input_files - len(filenames),
        "filtered_before_window_files": filtered_before_window,
        "filtered_after_window_files": filtered_after_window,
        "skipped_corrupt_files": skipped_corrupt_files,
        "min_snapshot_end_gps": args.min_snapshot_end_gps,
        "min_snapshot_end_utc": gps_to_utc_label(args.min_snapshot_end_gps),
        "max_snapshot_end_gps": args.max_snapshot_end_gps,
        "max_snapshot_end_utc": gps_to_utc_label(args.max_snapshot_end_gps),
        "latest_snapshot_end_gps": latest_snapshot_end,
        "latest_snapshot_end_utc": gps_to_utc_label(latest_snapshot_end),
        "selected_snapshot_start_gps": snapshot_gps_start,
        "selected_snapshot_start_utc": gps_to_utc_label(snapshot_gps_start),
        "selected_snapshot_end_gps": snapshot_gps_end,
        "selected_snapshot_end_utc": gps_to_utc_label(snapshot_gps_end),
        "selected_snapshot_duration_seconds": snapshot_duration,
        "postcoh_rows": total_rows,
        "data_gps_start": data_gps_start,
        "data_gps_start_utc": gps_to_utc_label(data_gps_start),
        "data_gps_end": data_gps_end,
        "data_gps_end_utc": gps_to_utc_label(data_gps_end),
        "data_duration_seconds": data_duration,
        "data_gps_start_H1": data_gps_start_by_ifo["H1"],
        "data_gps_start_utc_H1": gps_to_utc_label(data_gps_start_by_ifo["H1"]),
        "data_gps_end_H1": data_gps_end_by_ifo["H1"],
        "data_gps_end_utc_H1": gps_to_utc_label(data_gps_end_by_ifo["H1"]),
        "data_duration_seconds_H1": data_duration_by_ifo["H1"],
        "data_gps_start_L1": data_gps_start_by_ifo["L1"],
        "data_gps_start_utc_L1": gps_to_utc_label(data_gps_start_by_ifo["L1"]),
        "data_gps_end_L1": data_gps_end_by_ifo["L1"],
        "data_gps_end_utc_L1": gps_to_utc_label(data_gps_end_by_ifo["L1"]),
        "data_duration_seconds_L1": data_duration_by_ifo["L1"],
        "feature_rows_H1": feature_counts["H1"],
        "feature_rows_L1": feature_counts["L1"],
        "feature_rows_total": feature_counts["H1"] + feature_counts["L1"],
        "feature_gps_start": feature_gps_start,
        "feature_gps_start_utc": gps_to_utc_label(feature_gps_start),
        "feature_gps_end": feature_gps_end,
        "feature_gps_end_utc": gps_to_utc_label(feature_gps_end),
        "feature_duration_seconds": feature_duration,
        "feature_gps_start_H1": feature_gps_start_by_ifo["H1"],
        "feature_gps_start_utc_H1": gps_to_utc_label(feature_gps_start_by_ifo["H1"]),
        "feature_gps_end_H1": feature_gps_end_by_ifo["H1"],
        "feature_gps_end_utc_H1": gps_to_utc_label(feature_gps_end_by_ifo["H1"]),
        "feature_duration_seconds_H1": feature_duration_by_ifo["H1"],
        "feature_gps_start_L1": feature_gps_start_by_ifo["L1"],
        "feature_gps_start_utc_L1": gps_to_utc_label(feature_gps_start_by_ifo["L1"]),
        "feature_gps_end_L1": feature_gps_end_by_ifo["L1"],
        "feature_gps_end_utc_L1": gps_to_utc_label(feature_gps_end_by_ifo["L1"]),
        "feature_duration_seconds_L1": feature_duration_by_ifo["L1"],
        "gps_start": gps_start,
        "gps_start_utc": gps_to_utc_label(gps_start),
        "gps_end": gps_end,
        "gps_end_utc": gps_to_utc_label(gps_end),
        "duration_seconds": duration,
        "duration_hours": duration / 3600.0 if duration else 0.0,
        "duration_source": duration_source,
        "bank_groups": sorted(g for g in groups if g),
        "bank_ranges": bank_ranges(groups, args.banks_per_group),
        "min_snr": args.min_snr,
        "output": args.output,
    }
    tmp_summary = args.summary + ".tmp"
    with open(tmp_summary, "w") as output_file:
        json.dump(summary, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    os.replace(tmp_summary, args.summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
