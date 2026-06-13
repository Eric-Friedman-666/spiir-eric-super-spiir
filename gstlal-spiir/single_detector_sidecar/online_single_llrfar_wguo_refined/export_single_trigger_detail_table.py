#!/usr/bin/env python3
"""Export detector-local trigger details for a completed or live run.

This is an audit/reporting tool.  It reads the detector-local feature CSVs and
the append-only FAR ledgers, then writes one row per H1/L1 trigger.  It does not
assign, recompute, or mutate FAR values.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob
import json
import math
import os
from pathlib import Path

import single_detector_far as sdf


GPS_UTC_OFFSET_SECONDS = 18
GPS_EPOCH_UNIX = 315964800
GPS_LOOKUP_SCALE = 1_000_000

STATUS_ASSIGNED = "assigned"
STATUS_BACKGROUND_SUPPORT = "background_support"
STATUS_UNASSIGNED = "unassigned"
ASSIGNED_FAR_NOT_APPLICABLE_SOURCE = "not_assigned_background_support_bootstrap"
DIAGNOSTIC_FAR_SOURCE = getattr(
    sdf, "FAR_SOURCE_DIRECT_EMPIRICAL", "direct_empirical_count")

OUTPUT_FIELDS = [
    "worker_id",
    "worker_group",
    "ifo",
    "trigger_gps",
    "trigger_utc",
    "end_time",
    "end_time_ns",
    "event_id",
    "bank_group",
    "bankid",
    "tmplt_idx",
    "source_file",
    "source_row",
    "feature_csv_row",
    "feature_file",
    "ledger_file",
    "assignment_status",
    "snr",
    "chisq",
    "llr",
    "rank",
    "far",
    "far_source",
    "neg_log10_far",
    "assigned_far",
    "assigned_far_source",
    "assigned_neg_log10_far",
    "calculated_far",
    "calculated_far_source",
    "calculated_neg_log10_far",
    "direct_far",
    "direct_far_source",
    "direct_neg_log10_far",
    "assign_bg_id",
    "assign_bg_file",
    "assign_bg_start",
    "assign_bg_end",
    "assign_bg_livetime_seconds",
    "assign_bg_update_utc",
    "assign_bg_update_unix",
    "assignment_utc",
    "assignment_unix",
    "trigger_key",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=".")
    parser.add_argument(
        "--feature-csv",
        action="append",
        help="feature CSV to include; defaults to worker assignment feature CSVs",
    )
    parser.add_argument(
        "--ledger-csv",
        action="append",
        help="assigned FAR ledger CSV to merge; defaults to worker ledgers",
    )
    parser.add_argument("--ifos", default="H1,L1")
    parser.add_argument("--min-snr", type=float, default=4.0)
    parser.add_argument(
        "--output",
        default="single_branch/single_trigger_detail_table.csv",
    )
    parser.add_argument(
        "--summary",
        default="monitor/latest_single_trigger_detail_table.json",
    )
    return parser.parse_args()


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


def gps_lookup_key(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value) * GPS_LOOKUP_SCALE))
    except (TypeError, ValueError):
        return None


def format_number(value) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number) or math.isinf(number):
        return str(value)
    return "%.12g" % number


def format_neg_log10_far(value) -> str:
    try:
        far = float(value)
    except (TypeError, ValueError):
        return ""
    try:
        return "%.12g" % sdf.neg_log10_far(far)
    except (TypeError, ValueError, OverflowError):
        return ""


def feature_key(feature: sdf.SingleDetectorFeature) -> tuple[str, ...]:
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


def row_key(row: dict[str, str]) -> tuple[str, ...]:
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


def gps_seconds(feature: sdf.SingleDetectorFeature) -> float | None:
    return sdf.feature_gps_seconds(feature)


def gps_to_utc_label(gps: float | int | str | None) -> str:
    if gps is None:
        return ""
    try:
        gps_float = float(gps)
    except (TypeError, ValueError):
        return ""
    unix_time = gps_float + GPS_EPOCH_UNIX - GPS_UTC_OFFSET_SECONDS
    return (
        _dt.datetime.fromtimestamp(unix_time, _dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open(newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in OUTPUT_FIELDS})
    os.replace(str(tmp_path), str(path))


def infer_worker_id(path: Path) -> str:
    for part in path.parts:
        if part.startswith("worker_"):
            return part.split("_", 1)[1]
    return ""


def discover_feature_csvs(run_dir: Path) -> list[Path]:
    patterns = [
        "single_branch/worker_*/single_trigger_features_assignment_all_visible.csv",
        "single_branch/worker_*/single_trigger_features.csv",
        "single_branch/single_trigger_features_assignment_all_visible.csv",
        "single_branch/single_trigger_features.csv",
    ]
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for filename in sorted(glob.glob(str(run_dir / pattern))):
            path = Path(filename)
            key = path.resolve()
            if key not in seen:
                paths.append(path)
                seen.add(key)
    return paths


def discover_ledger_csvs(run_dir: Path) -> list[Path]:
    patterns = [
        "single_branch/worker_*/single_final_far_all.csv",
        "single_branch/single_final_far_all.csv",
    ]
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for filename in sorted(glob.glob(str(run_dir / pattern))):
            path = Path(filename)
            key = path.resolve()
            if key not in seen:
                paths.append(path)
                seen.add(key)
    return paths


def discover_background_jsons(run_dir: Path) -> list[Path]:
    patterns = [
        "single_branch/worker_*/backgrounds/BG-*.json",
        "single_branch/backgrounds/BG-*.json",
    ]
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for filename in sorted(glob.glob(str(run_dir / pattern))):
            path = Path(filename)
            key = path.resolve()
            if key not in seen:
                paths.append(path)
                seen.add(key)
    return sorted(paths, key=background_sort_key)


def background_sort_key(path: Path) -> tuple[str, int, str]:
    worker_id = infer_worker_id(path)
    bg_id = path.stem
    try:
        bg_number = int(bg_id.split("-", 1)[1])
    except (IndexError, ValueError):
        bg_number = 10**9
    return (worker_id, bg_number, bg_id)


def choose_feature_csvs(run_dir: Path, requested: list[str] | None) -> list[Path]:
    if requested:
        return [Path(item) if Path(item).is_absolute() else run_dir / item
                for item in requested]
    discovered = discover_feature_csvs(run_dir)
    by_worker: dict[str, Path] = {}
    for path in discovered:
        worker = infer_worker_id(path)
        # Prefer the all-visible assignment feature CSV over the sliding
        # background feature CSV when both exist for a worker.
        current = by_worker.get(worker)
        if current is None or "assignment_all_visible" in path.name:
            by_worker[worker] = path
    return [by_worker[key] for key in sorted(by_worker, key=lambda item: (item == "", item))]


def choose_ledger_csvs(run_dir: Path, requested: list[str] | None) -> list[Path]:
    if requested:
        return [Path(item) if Path(item).is_absolute() else run_dir / item
                for item in requested]
    return discover_ledger_csvs(run_dir)


def load_ledger_rows(paths: list[Path]) -> dict[tuple[str, ...], dict[str, str]]:
    rows_by_key: dict[tuple[str, ...], dict[str, str]] = {}
    for path in paths:
        worker_id = infer_worker_id(path)
        for row in read_rows(path):
            key = row_key(row)
            output = dict(row)
            output["_ledger_file"] = str(path)
            output["_worker_id"] = worker_id
            rows_by_key.setdefault(key, output)
    return rows_by_key


def load_bg_metadata(ledger_rows: dict[tuple[str, ...], dict[str, str]]
                     ) -> dict[tuple[str, str], dict[str, str]]:
    metadata: dict[tuple[str, str], dict[str, str]] = {}
    fields = [
        "assign_bg_id",
        "assign_bg_file",
        "assign_bg_start",
        "assign_bg_end",
        "assign_bg_livetime_seconds",
        "assign_bg_update_utc",
        "assign_bg_update_unix",
    ]
    for row in ledger_rows.values():
        worker_id = row.get("_worker_id", "")
        bg_id = row.get("assign_bg_id", "")
        if not worker_id or not bg_id:
            continue
        key = (worker_id, bg_id)
        if key in metadata:
            continue
        metadata[key] = {field: row.get(field, "") for field in fields}
    return metadata


def load_background_support_rows(
    run_dir: Path,
    ledger_rows: dict[tuple[str, ...], dict[str, str]],
) -> dict[tuple[str, str, int], dict[str, str]]:
    """Load diagnostic LLR/FAR values for triggers used to build backgrounds.

    Background-support rows are not formal FAR assignments, so they are absent
    from the append-only ledger.  They still need their own LLR and empirical
    calculated FAR for audit tables because those points form the background
    rank distribution.
    """
    metadata = load_bg_metadata(ledger_rows)
    support_rows: dict[tuple[str, str, int], dict[str, str]] = {}
    for path in discover_background_jsons(run_dir):
        worker_id = infer_worker_id(path)
        bg_id = path.stem
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        backgrounds = payload.get("backgrounds", {})
        if not isinstance(backgrounds, dict):
            continue
        meta = metadata.get((worker_id, bg_id), {})
        for ifo, info in backgrounds.items():
            if not isinstance(info, dict):
                continue
            livetime = meta.get("assign_bg_livetime_seconds") or format_number(
                info.get("livetime", ""))
            points = info.get("far_llr_points", [])
            if not isinstance(points, list):
                continue
            for point in points:
                if not isinstance(point, dict):
                    continue
                gps_key = gps_lookup_key(point.get("gps"))
                if gps_key is None:
                    continue
                key = (worker_id, ifo, gps_key)
                row = {
                    "llr": format_number(point.get("llr", "")),
                    "rank": format_number(point.get("llr", "")),
                    "calculated_far": format_number(point.get("far", "")),
                    "calculated_far_source": DIAGNOSTIC_FAR_SOURCE,
                    "calculated_neg_log10_far": format_neg_log10_far(
                        point.get("far", "")),
                    "direct_far": format_number(point.get("far", "")),
                    "direct_far_source": DIAGNOSTIC_FAR_SOURCE,
                    "direct_neg_log10_far": format_neg_log10_far(
                        point.get("far", "")),
                    "assigned_far_source": ASSIGNED_FAR_NOT_APPLICABLE_SOURCE,
                    "assign_bg_id": bg_id,
                    "assign_bg_file": meta.get("assign_bg_file", str(path)),
                    "assign_bg_start": meta.get("assign_bg_start", ""),
                    "assign_bg_end": meta.get("assign_bg_end", ""),
                    "assign_bg_livetime_seconds": livetime,
                    "assign_bg_update_utc": (
                        meta.get("assign_bg_update_utc")
                        or payload.get("created_utc", "")
                    ),
                    "assign_bg_update_unix": meta.get("assign_bg_update_unix", ""),
                }
                # Rolling backgrounds overlap.  The earliest background that
                # contains a bootstrap/support trigger is the diagnostic source
                # for that trigger in this audit table.
                support_rows.setdefault(key, row)
    return support_rows


def load_features(paths: list[Path],
                  ifos: tuple[str, ...],
                  min_snr: float) -> list[tuple[Path, str, sdf.SingleDetectorFeature]]:
    features: list[tuple[Path, str, sdf.SingleDetectorFeature]] = []
    for path in paths:
        worker_id = infer_worker_id(path)
        file_features = sdf.scan_feature_csv_files(
            [str(path)], ifos, min_snr, autocorr_power_by_template=None)
        features.extend((path, worker_id, feature) for feature in file_features)
    return features


def detail_row(feature_path: Path,
               worker_id: str,
               feature: sdf.SingleDetectorFeature,
               ledger: dict[str, str] | None,
               support: dict[str, str] | None = None) -> dict[str, str]:
    source = feature.source_row if isinstance(feature.source_row, dict) else {}
    key = feature_key(feature)
    gps = gps_seconds(feature)
    row = {
        "worker_id": worker_id or (ledger or {}).get("_worker_id", ""),
        "worker_group": source.get("bank_group", ""),
        "ifo": feature.ifo,
        "trigger_gps": "" if gps is None else "%.9f" % gps,
        "trigger_utc": gps_to_utc_label(gps),
        "end_time": "" if feature.end_time is None else feature.end_time,
        "end_time_ns": "" if feature.end_time_ns is None else feature.end_time_ns,
        "event_id": source.get("event_id", ""),
        "bank_group": source.get("bank_group", ""),
        "bankid": "" if feature.bankid is None else feature.bankid,
        "tmplt_idx": "" if feature.tmplt_idx is None else feature.tmplt_idx,
        "source_file": source.get("source_file", ""),
        "source_row": source.get("source_row", ""),
        "feature_csv_row": source.get("_feature_csv_row_index", ""),
        "feature_file": str(feature_path),
        "ledger_file": (ledger or {}).get("_ledger_file", ""),
        "assignment_status": (
            STATUS_ASSIGNED if ledger else
            STATUS_BACKGROUND_SUPPORT if support else
            STATUS_UNASSIGNED
        ),
        "snr": "%.12g" % feature.rho,
        "chisq": "%.12g" % feature.chisq,
        "trigger_key": "|".join(key),
    }
    if ledger:
        copy_fields = [
            "llr",
            "rank",
            "far",
            "far_source",
            "neg_log10_far",
            "assigned_far",
            "assigned_far_source",
            "assigned_neg_log10_far",
            "calculated_far",
            "calculated_far_source",
            "calculated_neg_log10_far",
            "direct_far",
            "direct_far_source",
            "direct_neg_log10_far",
            "assign_bg_id",
            "assign_bg_file",
            "assign_bg_start",
            "assign_bg_end",
            "assign_bg_livetime_seconds",
            "assign_bg_update_utc",
            "assign_bg_update_unix",
            "assignment_utc",
            "assignment_unix",
        ]
        for field in copy_fields:
            row[field] = ledger.get(field, "")
    elif support:
        copy_fields = [
            "llr",
            "rank",
            "calculated_far",
            "calculated_far_source",
            "calculated_neg_log10_far",
            "direct_far",
            "direct_far_source",
            "direct_neg_log10_far",
            "assigned_far_source",
            "assign_bg_id",
            "assign_bg_file",
            "assign_bg_start",
            "assign_bg_end",
            "assign_bg_livetime_seconds",
            "assign_bg_update_utc",
            "assign_bg_update_unix",
        ]
        for field in copy_fields:
            row[field] = support.get(field, "")
    return row


def summarize(rows: list[dict[str, str]],
              feature_csvs: list[Path],
              ledger_csvs: list[Path],
              output_path: Path) -> dict[str, object]:
    counts = {"H1": 0, "L1": 0, "total": 0}
    assigned = {"H1": 0, "L1": 0, "total": 0}
    background_support = {"H1": 0, "L1": 0, "total": 0}
    unassigned = {"H1": 0, "L1": 0, "total": 0}
    bg_ids: set[str] = set()
    for row in rows:
        ifo = row.get("ifo", "")
        if ifo in ("H1", "L1"):
            counts[ifo] += 1
        counts["total"] += 1
        status = row.get("assignment_status")
        if status == STATUS_ASSIGNED:
            if ifo in ("H1", "L1"):
                assigned[ifo] += 1
            assigned["total"] += 1
        elif status == STATUS_BACKGROUND_SUPPORT:
            if ifo in ("H1", "L1"):
                background_support[ifo] += 1
            background_support["total"] += 1
        elif status == STATUS_UNASSIGNED:
            if ifo in ("H1", "L1"):
                unassigned[ifo] += 1
            unassigned["total"] += 1
        if row.get("assign_bg_id"):
            bg_ids.add(row["assign_bg_id"])
    return {
        "output_file": str(output_path),
        "feature_csvs": [str(path) for path in feature_csvs],
        "ledger_csvs": [str(path) for path in ledger_csvs],
        "rows_total": counts["total"],
        "rows_H1": counts["H1"],
        "rows_L1": counts["L1"],
        "assigned_rows_total": assigned["total"],
        "assigned_rows_H1": assigned["H1"],
        "assigned_rows_L1": assigned["L1"],
        "background_support_rows_total": background_support["total"],
        "background_support_rows_H1": background_support["H1"],
        "background_support_rows_L1": background_support["L1"],
        "unassigned_rows_total": unassigned["total"],
        "unassigned_rows_H1": unassigned["H1"],
        "unassigned_rows_L1": unassigned["L1"],
        "not_assigned_rows_total": counts["total"] - assigned["total"],
        "assign_bg_ids": sorted(bg_ids),
        "updated_utc": (
            _dt.datetime.now(_dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
    }


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    ifos = sdf.split_ifos(args.ifos)
    feature_csvs = choose_feature_csvs(run_dir, args.feature_csv)
    ledger_csvs = choose_ledger_csvs(run_dir, args.ledger_csv)
    if not feature_csvs:
        raise SystemExit("no feature CSVs found")

    ledger_rows = load_ledger_rows(ledger_csvs)
    support_rows = load_background_support_rows(run_dir, ledger_rows)
    feature_rows = load_features(feature_csvs, ifos, args.min_snr)

    output_rows: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for feature_path, worker_id, feature in feature_rows:
        key = feature_key(feature)
        if key in seen:
            continue
        seen.add(key)
        gps_key = gps_lookup_key(gps_seconds(feature))
        support = None
        if gps_key is not None:
            support = support_rows.get((worker_id, feature.ifo, gps_key))
        output_rows.append(detail_row(
            feature_path, worker_id, feature, ledger_rows.get(key), support))

    output_rows.sort(key=lambda row: (
        float(row["trigger_gps"]) if row.get("trigger_gps") else -float("inf"),
        row.get("ifo", ""),
        row.get("bank_group", ""),
        row.get("bankid", ""),
        row.get("tmplt_idx", ""),
    ))

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = run_dir / output_path
    summary_path = Path(args.summary)
    if not summary_path.is_absolute():
        summary_path = run_dir / summary_path

    write_rows(output_path, output_rows)
    summary = summarize(output_rows, feature_csvs, ledger_csvs, output_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
