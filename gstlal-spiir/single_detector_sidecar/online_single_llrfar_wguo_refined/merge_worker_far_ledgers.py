#!/usr/bin/env python3
"""Merge worker-local single-detector FAR ledgers into the run-level product.

Each worker owns its own background and append-only FAR ledger under
single_branch/worker_<id>/.  This script does not recompute FAR.  It only
publishes the run-level CSV by appending rows that are already frozen in a
worker-local ledger.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import math
import os
import time
from pathlib import Path

import single_detector_far as sdf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=".")
    parser.add_argument("--worker-count", type=int, default=1)
    parser.add_argument("--output", default="single_branch/single_final_far_all.csv")
    parser.add_argument("--candidate-output",
                        default="single_branch/single_final_far_latest_candidates.csv")
    parser.add_argument("--summary", default="monitor/latest_single_background_status.json")
    parser.add_argument("--combined-summary", default="monitor/latest_single_summary.json")
    parser.add_argument("--plot-summary", default="monitor/latest_single_plot_summary.json")
    parser.add_argument("--lock-stale-seconds", type=float, default=1800.0)
    return parser.parse_args()


def norm(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if math.isnan(number) or math.isinf(number):
        return text
    return "%.12g" % number


def trigger_key(row: dict[str, str]) -> tuple[str, ...]:
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


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open(newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sdf.PLOT_ROW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in sdf.PLOT_ROW_FIELDS})
    os.replace(str(tmp_path), str(path))


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def count_rows(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {"H1": 0, "L1": 0, "total": 0}
    for row in rows:
        ifo = (row.get("ifo") or row.get("ifos") or "").strip()
        if ifo in ("H1", "L1"):
            counts[ifo] += 1
        counts["total"] += 1
    return counts


def acquire_lock(lock_dir: Path, stale_seconds: float) -> bool:
    try:
        lock_dir.mkdir()
        (lock_dir / "pid").write_text(str(os.getpid()))
        (lock_dir / "host").write_text(os.uname().nodename)
        return True
    except FileExistsError:
        pass
    try:
        age = time.time() - lock_dir.stat().st_mtime
    except OSError:
        age = stale_seconds + 1.0
    if age >= stale_seconds:
        for child in lock_dir.glob("*"):
            try:
                child.unlink()
            except OSError:
                pass
        try:
            lock_dir.rmdir()
        except OSError:
            return False
        return acquire_lock(lock_dir, stale_seconds)
    return False


def utc_now() -> str:
    return (
        _dt.datetime.utcnow()
        .replace(microsecond=0)
        .isoformat()
        + "Z"
    )


def worker_paths(worker_count: int) -> list[tuple[int, Path, Path, Path, Path]]:
    paths = []
    for worker_id in range(max(1, worker_count)):
        branch_dir = Path("single_branch") / f"worker_{worker_id}"
        monitor_dir = Path("monitor") / f"worker_{worker_id}"
        paths.append((
            worker_id,
            branch_dir / "single_final_far_all.csv",
            branch_dir / "single_final_far_latest_candidates.csv",
            monitor_dir / "latest_single_background_status.json",
            monitor_dir / "latest_single_summary.json",
        ))
    return paths


def min_number(values):
    clean = [float(value) for value in values if value is not None]
    return min(clean) if clean else None


def max_number(values):
    clean = [float(value) for value in values if value is not None]
    return max(clean) if clean else None


def sum_number(values):
    total = 0
    seen = False
    for value in values:
        if value is None:
            continue
        total += float(value)
        seen = True
    return total if seen else None


def main() -> int:
    args = parse_args()
    os.chdir(args.run_dir)
    lock_dir = Path("single_branch/.merge_worker_far.lockdir")
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    if not acquire_lock(lock_dir, args.lock_stale_seconds):
        print("merge_worker_far_ledgers: another merge is already running; skipping")
        return 0
    try:
        output_path = Path(args.output)
        existing_rows = [normalize_row(row) for row in read_rows(output_path)]
        output_rows = []
        seen = set()
        for row in existing_rows:
            key = trigger_key(row)
            if key in seen:
                continue
            output_rows.append(row)
            seen.add(key)

        latest_candidates: list[dict[str, str]] = []
        worker_details = []
        statuses = []
        summaries = []
        added_rows = 0
        duplicate_worker_rows = 0

        for worker_id, ledger_path, candidate_path, status_path, summary_path in worker_paths(
                args.worker_count):
            rows = [normalize_row(row) for row in read_rows(ledger_path)]
            candidate_rows = [normalize_row(row) for row in read_rows(candidate_path)]
            latest_candidates.extend(candidate_rows)
            status = read_json(status_path)
            summary = read_json(summary_path)
            if status:
                statuses.append(status)
            if summary:
                summaries.append(summary)

            new_from_worker = 0
            for row in rows:
                key = trigger_key(row)
                if key in seen:
                    duplicate_worker_rows += 1
                    continue
                output_rows.append(row)
                seen.add(key)
                added_rows += 1
                new_from_worker += 1

            worker_details.append({
                "worker_id": worker_id,
                "ledger_file": str(ledger_path),
                "candidate_file": str(candidate_path),
                "status_file": str(status_path),
                "summary_file": str(summary_path),
                "ledger_rows": len(rows),
                "new_rows_merged": new_from_worker,
                "background_ready": status.get("background_ready"),
                "bank_groups": summary.get("bank_groups") or status.get("bank_groups"),
                "duration_seconds": status.get(
                    "accumulated_background_time_seconds",
                    status.get("duration_seconds")),
                "gps_start_utc": status.get("gps_start_utc"),
                "gps_end_utc": status.get("gps_end_utc"),
            })

        write_rows(output_path, output_rows)
        if args.candidate_output:
            write_rows(Path(args.candidate_output), latest_candidates)

        counts = count_rows(output_rows)
        ready_values = [bool(status.get("background_ready")) for status in statuses]
        all_ready = bool(ready_values) and all(ready_values)
        status_feature_totals = [
            status.get("accumulated_background_trigger_rows_total",
                       status.get("feature_rows_total")) for status in statuses
        ]
        status_feature_h1 = [
            status.get("accumulated_background_trigger_rows_H1",
                       status.get("feature_rows_H1")) for status in statuses
        ]
        status_feature_l1 = [
            status.get("accumulated_background_trigger_rows_L1",
                       status.get("feature_rows_L1")) for status in statuses
        ]
        durations = [
            status.get("accumulated_background_time_seconds",
                       status.get("duration_seconds")) for status in statuses
        ]
        gps_starts = [status.get("gps_start") for status in statuses]
        gps_ends = [status.get("gps_end") for status in statuses]

        combined_summary = {
            "worker_local_sidecar": True,
            "worker_count": max(1, args.worker_count),
            "workers": worker_details,
            "bank_groups": sorted({
                group
                for summary in summaries
                for group in (summary.get("bank_groups") or [])
            }),
            "bank_ranges": sorted({
                item
                for summary in summaries
                for item in (summary.get("bank_ranges") or [])
            }),
            "postcoh_rows": int(sum_number(s.get("postcoh_rows") for s in summaries) or 0),
            "feature_rows_H1": int(sum_number(s.get("feature_rows_H1") for s in summaries) or 0),
            "feature_rows_L1": int(sum_number(s.get("feature_rows_L1") for s in summaries) or 0),
            "feature_rows_total": int(sum_number(s.get("feature_rows_total") for s in summaries) or 0),
            "gps_start": min_number(s.get("gps_start") for s in summaries),
            "gps_start_utc": next((s.get("gps_start_utc") for s in summaries if s.get("gps_start_utc")), None),
            "gps_end": max_number(s.get("gps_end") for s in summaries),
            "gps_end_utc": next((s.get("gps_end_utc") for s in reversed(summaries) if s.get("gps_end_utc")), None),
            "duration_seconds": min_number(durations),
            "duration_hours": (min_number(durations) or 0.0) / 3600.0 if durations else 0.0,
            "output": args.output,
        }
        Path(args.combined_summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.combined_summary).write_text(
            json.dumps(combined_summary, indent=2, sort_keys=True) + "\n")

        merged_status = dict(combined_summary)
        merged_status.update({
            "background_ready": all_ready,
            "background_file": "per-worker single_branch/worker_<id>/single_far_llr_background.json",
            "assigned_file": args.output,
            "candidate_file": args.candidate_output,
            "support_file": "per-worker single_branch/worker_<id>/single_llr_far_support.csv",
            "plot_file": "per-worker single_branch/worker_<id>/single_llr_far_background.png",
            "worker_status_files": [
                f"monitor/worker_{worker_id}/latest_single_background_status.json"
                for worker_id in range(max(1, args.worker_count))
            ],
            "worker_assignment_model": (
                "each worker accumulates its own background from exactly one "
                "bank group, assigns FAR only for that group's triggers, then this script "
                "merges the frozen worker ledgers into the run-level CSV"
            ),
            "formal_assigned_far_rows_H1": counts["H1"],
            "formal_assigned_far_rows_L1": counts["L1"],
            "formal_assigned_far_rows_total": counts["total"],
            "assigned_points": counts["total"],
            "merge_existing_rows_before": len(existing_rows),
            "merge_new_rows": added_rows,
            "merge_duplicate_worker_rows": duplicate_worker_rows,
            "accumulated_background_trigger_rows_H1": int(sum_number(status_feature_h1) or 0),
            "accumulated_background_trigger_rows_L1": int(sum_number(status_feature_l1) or 0),
            "accumulated_background_trigger_rows_total": int(sum_number(status_feature_totals) or 0),
            "accumulated_background_time_seconds": min_number(durations),
            "accumulated_background_time_hours": (
                (min_number(durations) or 0.0) / 3600.0 if durations else 0.0),
            "gps_start": min_number(gps_starts),
            "gps_end": max_number(gps_ends),
            "background_accumulation_seconds_required": float(
                os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0),
            "updated_utc": utc_now(),
            "updated_unix": time.time(),
        })
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(
            json.dumps(merged_status, indent=2, sort_keys=True) + "\n")

        plot_summary = {
            "worker_local_sidecar": True,
            "support_points": int(sum_number(
                status.get("support_points") for status in statuses) or 0),
            "assigned_points": counts["total"],
            "plot_file": merged_status["plot_file"],
            "updated_utc": merged_status["updated_utc"],
        }
        Path(args.plot_summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.plot_summary).write_text(
            json.dumps(plot_summary, indent=2, sort_keys=True) + "\n")

        print(json.dumps(merged_status, sort_keys=True))
        return 0
    finally:
        for child in lock_dir.glob("*"):
            try:
                child.unlink()
            except OSError:
                pass
        try:
            lock_dir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
