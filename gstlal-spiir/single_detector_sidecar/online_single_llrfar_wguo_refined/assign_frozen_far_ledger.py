#!/usr/bin/env python3
"""Append-only single-detector FAR ledger assignment.

The rolling background file is allowed to change every updater cycle.  The final
trigger ledger is not: once a trigger row has an assigned FAR, this script keeps
that value and only appends previously unseen trigger rows.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import single_detector_far as sdf


SNAPSHOT_RE = re.compile(
    r"(?:_zerolag_|sdpostcoh[^/_]*_|single_postcoh[^/_]*_)(\d+)_(\d+)\.xml(?:\.gz)?$"
)
DEFAULT_BACKGROUND_ACCUMULATION_SECONDS = float(
    os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0)
DEFAULT_FORMAL_BACKGROUND_ACCUMULATION_SECONDS = float(
    os.environ.get("FORMAL_BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--candidate-output")
    parser.add_argument("--ifos", default="H1,L1")
    parser.add_argument("--min-snr", type=float, default=4.0)
    parser.add_argument("--data-start-gps", type=float, default=None)
    parser.add_argument("--background-window-seconds", type=float,
                        default=DEFAULT_BACKGROUND_ACCUMULATION_SECONDS)
    parser.add_argument("--background-required-seconds", type=float,
                        default=DEFAULT_BACKGROUND_ACCUMULATION_SECONDS)
    parser.add_argument(
        "--background-update-seconds",
        type=float,
        default=float(os.environ.get("BACKGROUND_UPDATE_TRIGGER_SECONDS", "3600") or 3600.0),
        help=("cadence for freezing a new background surface; trigger batches "
              "between two update boundaries are assigned with the earlier "
              "frozen background"))
    parser.add_argument("--initial-window-policy", choices=("first-full", "skip"),
                        default="skip")
    parser.add_argument(
        "--allow-short-background-debug",
        action="store_true",
        default=os.environ.get("ALLOW_SHORT_BACKGROUND_DEBUG", "0") == "1",
        help=("allow a background shorter than the formal three-hour window; "
              "for explicitly marked developer tests only"))
    parser.add_argument("--calibrate-noise-dof", action="store_true")
    parser.add_argument("--snr-bins", default="")
    parser.add_argument("--min-calibration-count", type=int, default=50)
    parser.add_argument("--noise-dof", type=float, default=2.0)
    parser.add_argument("--signal-dof", type=float, default=None)
    parser.add_argument("--beta-max", type=float, default=0.03)
    parser.add_argument("--beta-grid-size", type=int, default=31)
    parser.add_argument("--default-autocorr-power", type=float, default=1.0)
    parser.add_argument("--noise-beta", type=float, default=-1.0)
    parser.add_argument("--rank-offset", type=float, default=0.0)
    parser.add_argument("--autocorr-power-file")
    parser.add_argument("--bank-stats-dir")
    parser.add_argument("--snr-log-weight", type=float, default=0.5)
    parser.add_argument("--fit-min-points", type=int, default=20)
    parser.add_argument("--far-floor-count", type=float, default=1.0)
    parser.add_argument("--far-fit-boundary", type=float, default=1.0e-2)
    parser.add_argument(
        "--max-new-windows-per-run",
        type=int,
        default=0,
        help=("maximum number of previously unseen assignment windows to process "
              "in this invocation; 0 means no cap"))
    parser.add_argument(
        "--background-archive-dir",
        help=("directory where assignment background files are frozen as BG-000.json, "
              "BG-001.json, ... for audit and replay"))
    return parser.parse_args()


def snapshot_times_from_filename(filename: str) -> tuple[float, float] | None:
    match = SNAPSHOT_RE.search(str(filename))
    if not match:
        return None
    start = float(int(match.group(1)))
    end = start + float(int(match.group(2)))
    return start, end


def feature_time(feature: sdf.SingleDetectorFeature) -> float | None:
    return sdf.feature_gps_seconds(feature)


def assignment_window_end(feature: sdf.SingleDetectorFeature,
                          args: argparse.Namespace) -> float | None:
    source = feature.source_row if isinstance(feature.source_row, dict) else {}
    snapshot_times = snapshot_times_from_filename(source.get("source_file", ""))
    if snapshot_times is not None:
        # A zerolag snapshot is the online batch that exposed this trigger.  Use
        # the immediately preceding background surface, not rows from the same
        # snapshot that are only known at/after the candidate batch is written.
        end = snapshot_times[0]
    else:
        end = feature_time(feature)
    if end is None:
        return None
    if args.data_start_gps is not None:
        first_full_end = (
            float(args.data_start_gps) + float(args.background_required_seconds)
        )
        update = float(args.background_update_seconds or 0.0)
        if args.initial_window_policy == "first-full" and end < first_full_end:
            end = first_full_end
        elif update > 0.0 and end >= first_full_end:
            # Freeze backgrounds on the configured update cadence.  With a 3h
            # background and 1h update cadence, all trigger snapshots starting
            # in [3h, 4h) use the background ending at 3h.
            steps = math.floor((end - first_full_end + 1.0e-6) / update)
            end = first_full_end + steps * update
    return float(end)


def norm(value) -> str:
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


def trigger_key_from_row(row: dict[str, str]) -> tuple[str, ...]:
    physical = (
        row.get("ifo") or row.get("category") or "",
        norm(row.get("end_time")),
        norm(row.get("end_time_ns")),
        norm(row.get("bankid")),
        norm(row.get("tmplt_idx")),
        norm(row.get("rho")),
        norm(row.get("chisq")),
    )
    if any(physical[1:]):
        return ("physical",) + physical
    return (
        "source",
        row.get("source_file", ""),
        row.get("source_row", ""),
        row.get("ifo") or row.get("category") or "",
    )


def trigger_key_from_feature(feature: sdf.SingleDetectorFeature) -> tuple[str, ...]:
    return (
        "physical",
        feature.ifo,
        norm(feature.end_time),
        norm(feature.end_time_ns),
        norm(feature.bankid),
        norm(feature.tmplt_idx),
        norm(feature.rho),
        norm(feature.chisq),
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open(newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    output = {field: row.get(field, "") for field in sdf.PLOT_ROW_FIELDS}
    if not output.get("assigned_far"):
        output["assigned_far"] = output.get("far", "")
    if not output.get("assigned_far_source"):
        output["assigned_far_source"] = output.get("far_source", "")
    if not output.get("assigned_neg_log10_far"):
        output["assigned_neg_log10_far"] = output.get("neg_log10_far", "")
    if not output.get("calculated_far"):
        output["calculated_far"] = output.get("direct_far", "")
    if not output.get("calculated_far_source"):
        output["calculated_far_source"] = output.get("direct_far_source", "")
    if not output.get("calculated_neg_log10_far"):
        output["calculated_neg_log10_far"] = output.get("direct_neg_log10_far", "")
    if not output.get("direct_far"):
        output["direct_far"] = output.get("calculated_far", "")
    if not output.get("direct_far_source"):
        output["direct_far_source"] = output.get("calculated_far_source", "")
    if not output.get("direct_neg_log10_far"):
        output["direct_neg_log10_far"] = output.get("calculated_neg_log10_far", "")
    return output


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sdf.PLOT_ROW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in sdf.PLOT_ROW_FIELDS})
    os.replace(str(tmp_path), str(path))


def utc_now() -> tuple[str, float]:
    now = time.time()
    text = (
        _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return text, now


def make_branch(args: argparse.Namespace,
                ifos: tuple[str, ...],
                background_features: list[sdf.SingleDetectorFeature],
                livetime: float) -> sdf.SingleDetectorBranch:
    model = sdf.make_likelihood_model_from_args(args)
    branch = sdf.SingleDetectorBranch(
        model,
        ifos=ifos,
        min_snr=args.min_snr,
        fit_min_points=args.fit_min_points,
        far_floor_count=args.far_floor_count,
        far_fit_boundary=args.far_fit_boundary)
    if args.calibrate_noise_dof and background_features:
        branch.calibrate_noise_dof_from_features(
            background_features,
            snr_bins=sdf.parse_snr_bins(args.snr_bins),
            min_count=args.min_calibration_count)
    for ifo in ifos:
        if any(feature.ifo == ifo for feature in background_features):
            branch.add_livetime(livetime, [ifo])
    branch.rebuild_background_support(background_features)
    branch.use_fitted_far = True
    return branch


def feature_in_window(feature: sdf.SingleDetectorFeature,
                      start: float,
                      end: float) -> bool:
    gps = feature_time(feature)
    return gps is not None and float(gps) >= start and float(gps) < end


def background_id_for_end(end: float,
                          args: argparse.Namespace,
                          fallback_index: int) -> str:
    if args.data_start_gps is not None and args.background_update_seconds > 0.0:
        first_full_end = (
            float(args.data_start_gps) + float(args.background_required_seconds)
        )
        if end >= first_full_end:
            index = math.floor(
                (end - first_full_end + 1.0e-6)
                / float(args.background_update_seconds)
            )
            return "BG-%03d" % max(0, int(index))
    return "BG-%03d" % fallback_index


def archive_background(branch: sdf.SingleDetectorBranch,
                       archive_dir: str | None,
                       bg_id: str) -> str:
    if not archive_dir:
        return ""
    path = Path(archive_dir) / f"{bg_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    branch.write_background_file(str(path))
    return str(path)


def count_by_ifo(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {"H1": 0, "L1": 0, "total": 0}
    for row in rows:
        ifo = (row.get("ifo") or "").strip()
        if ifo in counts:
            counts[ifo] += 1
        counts["total"] += 1
    return counts


def main() -> int:
    args = parse_args()
    if (float(args.background_required_seconds)
            < DEFAULT_FORMAL_BACKGROUND_ACCUMULATION_SECONDS
            and not args.allow_short_background_debug):
        sys.stderr.write(
            "BACKGROUND_CONTRACT_ERROR: --background-required-seconds="
            f"{float(args.background_required_seconds):.0f}s is shorter than "
            f"the formal required window "
            f"{DEFAULT_FORMAL_BACKGROUND_ACCUMULATION_SECONDS:.0f}s. "
            "Use ALLOW_SHORT_BACKGROUND_DEBUG=1 or "
            "--allow-short-background-debug only for non-formal developer tests.\n"
        )
        return 2
    ifos = sdf.split_ifos(args.ifos)
    output_path = Path(args.output)
    summary_path = Path(args.summary)
    candidate_path = Path(args.candidate_output) if args.candidate_output else None

    autocorr_power_by_template = sdf.load_template_shape_map(
        args.autocorr_power_file, args.bank_stats_dir, ifos)
    features = sdf.scan_feature_csv_files(
        [args.feature_csv], ifos, args.min_snr, autocorr_power_by_template)
    foreground_features = [
        feature for feature in features
        if feature.is_background == sdf.FLAG_FOREGROUND
    ]
    ignored_nonforeground_features = len(features) - len(foreground_features)

    existing_rows = read_rows(output_path)
    output_rows: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for row in existing_rows:
        key = trigger_key_from_row(row)
        if key in seen:
            continue
        output_rows.append(normalize_row(row))
        seen.add(key)

    groups: dict[float, list[sdf.SingleDetectorFeature]] = {}
    skipped_no_time = 0
    duplicate_candidates = 0
    for feature in foreground_features:
        key = trigger_key_from_feature(feature)
        if key in seen:
            duplicate_candidates += 1
            continue
        end = assignment_window_end(feature, args)
        if end is None:
            skipped_no_time += 1
            continue
        groups.setdefault(end, []).append(feature)

    assignment_utc, assignment_unix = utc_now()
    new_rows: list[dict[str, str]] = []
    skipped_not_ready = 0
    deferred_window_rows = 0
    background_windows = 0
    background_records: list[dict[str, object]] = []

    sorted_group_ends = sorted(groups)
    for index, end in enumerate(sorted_group_ends):
        if args.max_new_windows_per_run > 0 and background_windows >= args.max_new_windows_per_run:
            deferred_window_rows += sum(
                len(groups[remaining_end]) for remaining_end in sorted_group_ends[index:])
            break
        start = end - float(args.background_window_seconds)
        if args.data_start_gps is not None:
            start = max(start, float(args.data_start_gps))
        livetime = end - start
        if livetime < float(args.background_required_seconds):
            skipped_not_ready += len(groups[end])
            continue
        background_features = [
            feature for feature in foreground_features
            if feature_in_window(feature, start, end)
        ]
        if len(background_features) <= 30:
            skipped_not_ready += len(groups[end])
            continue
        branch = make_branch(args, ifos, background_features, livetime)
        bg_id = background_id_for_end(end, args, background_windows)
        bg_file = archive_background(branch, args.background_archive_dir, bg_id)
        rows = sdf.results_to_plot_rows(
            branch.assign_feature(feature) for feature in groups[end])
        background_windows += 1
        background_records.append({
            "bg_id": bg_id,
            "background_file": bg_file or None,
            "background_start": start,
            "background_end": end,
            "background_livetime_seconds": livetime,
            "background_feature_rows": len(background_features),
            "assigned_rows": len(rows),
            "assignment_update_utc": assignment_utc,
            "assignment_update_unix": assignment_unix,
        })
        for row in rows:
            row["assign_bg_id"] = bg_id
            row["assign_bg_file"] = bg_file
            row["assign_bg_start"] = start
            row["assign_bg_end"] = end
            row["assign_bg_livetime_seconds"] = livetime
            row["assign_bg_update_utc"] = assignment_utc
            row["assign_bg_update_unix"] = assignment_unix
            row["assignment_utc"] = assignment_utc
            row["assignment_unix"] = assignment_unix
            new_rows.append(row)
            output_rows.append(row)
            seen.add(trigger_key_from_row(row))

    write_rows(output_path, output_rows)
    if candidate_path:
        write_rows(candidate_path, new_rows)

    counts = count_by_ifo(output_rows)
    if args.initial_window_policy == "first-full":
        policy = (
            "append-only trigger ledger: existing assigned FAR rows are preserved; "
            "new trigger rows are assigned with a background window ending at the "
            "start of the trigger zerolag snapshot, using the first full run window "
            "for cold-start triggers that lack a full preceding background window"
        )
    else:
        policy = (
            "append-only trigger ledger: existing assigned FAR rows are preserved; "
            "new trigger rows are assigned with a background window ending at the "
            "start of the trigger zerolag snapshot; triggers inside the initial "
            "background accumulation window are used only to build the first "
            "background and are not assigned final FAR; after the first full "
            "background, assignment backgrounds are frozen on "
            "BACKGROUND_UPDATE_TRIGGER_SECONDS boundaries, so a 3h accumulation "
            "and 1h update cadence means [0h,3h) builds BG-000, [3h,4h) is "
            "assigned with BG-000, [1h,4h) builds BG-001, [4h,5h) is assigned with BG-001"
        )

    summary = {
        "assigned_file": str(output_path),
        "candidate_file": str(candidate_path) if candidate_path else None,
        "feature_file": args.feature_csv,
        "existing_rows_before_merge": len(existing_rows),
        "input_feature_rows": len(features),
        "candidate_rows": len(foreground_features),
        "ignored_nonforeground_rows": ignored_nonforeground_features,
        "duplicate_candidate_rows": duplicate_candidates,
        "newly_assigned_rows": len(new_rows),
        "skipped_no_time_rows": skipped_no_time,
        "skipped_not_ready_rows": skipped_not_ready,
        "deferred_window_rows": deferred_window_rows,
        "background_windows_used": background_windows,
        "background_files": background_records,
        "max_new_windows_per_run": args.max_new_windows_per_run,
        "background_window_seconds": args.background_window_seconds,
        "background_required_seconds": args.background_required_seconds,
        "background_update_seconds": args.background_update_seconds,
        "initial_window_policy": args.initial_window_policy,
        "formal_assigned_far_rows_H1": counts["H1"],
        "formal_assigned_far_rows_L1": counts["L1"],
        "formal_assigned_far_rows_total": counts["total"],
        "assignment_utc": assignment_utc,
        "assignment_unix": assignment_unix,
        "policy": policy,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
