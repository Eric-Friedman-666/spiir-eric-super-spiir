#!/usr/bin/env python3
"""Lightweight real-time monitor for the online single-detector sidecar.

The monitor is intentionally read-only with respect to the SPIIR products.  It
only watches files that are already created by the running job and writes a
small status JSON/TSV/log under monitor/.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob
import json
import os
import re
import time
from pathlib import Path


ZEROLAG_RE = re.compile(r"_zerolag_(\d+)_(\d+)\.xml(?:\.gz)?$")
GPS_UTC_OFFSET_SECONDS = 18
GPS_EPOCH_UNIX = 315964800


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=".")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--job-id", default=os.environ.get("SLURM_JOB_ID", ""))
    parser.add_argument("--stop-file", default="STOP_REALTIME_MONITOR.flag")
    parser.add_argument("--pipeline", default="pipeline.sh")
    parser.add_argument("--json-output", default="monitor/realtime_status.json")
    parser.add_argument("--tsv-output", default="monitor/realtime_status.tsv")
    parser.add_argument("--log-output", default="monitor/realtime_monitor.log")
    return parser.parse_args()


def utc_now():
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def gps_to_utc_label(gps):
    if gps is None:
        return None
    try:
        gps = float(gps)
    except (TypeError, ValueError):
        return None
    unix_time = gps + GPS_EPOCH_UNIX - GPS_UTC_OFFSET_SECONDS
    return _dt.datetime.utcfromtimestamp(unix_time).replace(
        microsecond=0).isoformat() + "Z"


def seconds_hms(seconds):
    if seconds is None:
        return None
    try:
        return str(_dt.timedelta(seconds=int(float(seconds))))
    except (TypeError, ValueError, OverflowError):
        return None


def gps_range_label(start_gps, end_gps):
    start_label = gps_to_utc_label(start_gps)
    end_label = gps_to_utc_label(end_gps)
    if start_label and end_label:
        return f"{start_label} -> {end_label}"
    return None


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def parse_pipeline_times(path):
    result = {"configured_gps_start": None, "configured_gps_end": None}
    env_start = os.environ.get("DATA_START_TIME")
    env_end = os.environ.get("DATA_END_TIME")
    env_duration = os.environ.get("MAX_DATA_DURATION_SECONDS")
    try:
        if env_start:
            result["configured_gps_start"] = int(env_start)
        if env_end:
            result["configured_gps_end"] = int(env_end)
        elif env_start and env_duration:
            result["configured_gps_end"] = int(env_start) + int(env_duration)
    except ValueError:
        result = {"configured_gps_start": None, "configured_gps_end": None}
    if result["configured_gps_start"] is not None and result["configured_gps_end"] is not None:
        return result
    try:
        text = Path(path).read_text()
    except Exception:
        return result
    for key, pattern in (
            ("configured_gps_start", r"macrostart=(\d+)"),
            ("configured_gps_end", r"macroend=(\d+)")):
        match = re.search(pattern, text)
        if match:
            result[key] = int(match.group(1))
    return result


def snapshot_parts(filename):
    match = ZEROLAG_RE.search(filename)
    if not match:
        return None
    start = int(match.group(1))
    duration = int(match.group(2))
    return start, duration, start + duration


def latest_snapshot(files):
    latest = None
    latest_start = None
    latest_duration = None
    latest_end = None
    for filename in files:
        parts = snapshot_parts(filename)
        if parts is None:
            continue
        start, duration, end = parts
        if latest_end is None or end > latest_end:
            latest = filename
            latest_start = start
            latest_duration = duration
            latest_end = end
    return latest, latest_start, latest_duration, latest_end


def zerolag_status(max_snapshot_end_gps=None):
    raw_files = sorted(glob.glob("[0-9][0-9][0-9]/*_zerolag_*.xml.gz"))
    if max_snapshot_end_gps is None:
        files = raw_files
    else:
        files = []
        for filename in raw_files:
            parts = snapshot_parts(filename)
            if parts is not None and parts[2] <= max_snapshot_end_gps:
                files.append(filename)
    latest, latest_start, latest_duration, latest_end = latest_snapshot(files)
    raw_latest, raw_start, raw_duration, raw_end = latest_snapshot(raw_files)
    return {
        "zerolag_file_count": len(files),
        "latest_zerolag_file": latest,
        "latest_snapshot_start": latest_start,
        "latest_snapshot_start_utc": gps_to_utc_label(latest_start),
        "latest_snapshot_duration": latest_duration,
        "latest_snapshot_end": latest_end,
        "latest_snapshot_end_utc": gps_to_utc_label(latest_end),
        "current_injected_gps": latest_end,
        "raw_zerolag_file_count": len(raw_files),
        "raw_latest_zerolag_file": raw_latest,
        "raw_latest_snapshot_start": raw_start,
        "raw_latest_snapshot_start_utc": gps_to_utc_label(raw_start),
        "raw_latest_snapshot_duration": raw_duration,
        "raw_latest_snapshot_end": raw_end,
        "raw_latest_snapshot_end_utc": gps_to_utc_label(raw_end),
        "raw_current_injected_gps": raw_end,
    }


def replay_gate_status(start_wall, configured_start):
    enabled = env_flag("ONLINE_REPLAY_SYNC")
    if not enabled:
        return {"enabled": False, "allowed_gps": None, "allowed_duration_seconds": None}
    start_gps = os.environ.get("ONLINE_REPLAY_START_GPS") or configured_start
    try:
        start_gps = float(start_gps)
    except (TypeError, ValueError):
        start_gps = float(configured_start or 0)
    start_wall_text = os.environ.get("ONLINE_REPLAY_START_WALL")
    try:
        gate_start_wall = float(start_wall_text) if start_wall_text else float(start_wall)
    except ValueError:
        gate_start_wall = float(start_wall)
    try:
        rate = float(os.environ.get("ONLINE_REPLAY_RATE", "1.0"))
    except ValueError:
        rate = 1.0
    try:
        lag = float(os.environ.get("ONLINE_REPLAY_ALLOWED_LAG_SECONDS", "0"))
    except ValueError:
        lag = 0.0
    elapsed = max(0.0, time.time() - gate_start_wall)
    allowed = start_gps + elapsed * rate + lag
    return {
        "enabled": True,
        "start_gps": start_gps,
        "start_wall": gate_start_wall,
        "rate": rate,
        "allowed_lag_seconds": lag,
        "allowed_gps": allowed,
        "allowed_utc": gps_to_utc_label(allowed),
        "allowed_duration_seconds": max(0.0, allowed - start_gps),
    }


def current_bank_group():
    log_files = sorted(glob.glob("logs/pipe_*.out"))
    pattern = re.compile(r"starting bank group (\d+)")
    current = None
    latest_line = ""
    for filename in log_files[-3:]:
        try:
            lines = Path(filename).read_text(errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            match = pattern.search(line)
            if match:
                current = match.group(1)
                latest_line = line
    return current, latest_line


def first_bank_start_unix():
    log_files = sorted(glob.glob("logs/pipe_*.out"))
    pattern = re.compile(
        r"starting bank group \d+ at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z")
    first = None
    for filename in log_files:
        try:
            lines = Path(filename).read_text(errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            match = pattern.search(line)
            if not match:
                continue
            try:
                stamp = _dt.datetime.strptime(
                    match.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                        tzinfo=_dt.timezone.utc).timestamp()
            except ValueError:
                continue
            if first is None or stamp < first:
                first = stamp
    return first


def count_assigned_far_rows(path):
    counts = {"H1": 0, "L1": 0, "total": 0}
    csv_path = Path(path)
    if not csv_path.exists():
        return counts
    try:
        with csv_path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ifo = (row.get("ifo") or row.get("ifos") or "").strip()
                if ifo in ("H1", "L1"):
                    counts[ifo] += 1
                counts["total"] += 1
    except Exception:
        return {"H1": None, "L1": None, "total": None}
    return counts


def build_status(start_wall, args):
    pipeline = parse_pipeline_times(args.pipeline)
    configured_start = pipeline.get("configured_gps_start")
    gate = replay_gate_status(start_wall, configured_start)
    zerolag = zerolag_status(gate.get("allowed_gps") if gate.get("enabled") else None)
    summary = read_json("monitor/latest_single_summary.json")
    bg_status = read_json("monitor/latest_single_background_status.json")
    plot_summary = read_json("monitor/latest_single_plot_summary.json")
    bank_group, bank_line = current_bank_group()
    assigned_file = bg_status.get("assigned_file") or "single_branch/single_final_far_all.csv"
    assigned_counts = {
        "H1": bg_status.get("formal_assigned_far_rows_H1"),
        "L1": bg_status.get("formal_assigned_far_rows_L1"),
        "total": bg_status.get("formal_assigned_far_rows_total"),
    }
    if any(value is None for value in assigned_counts.values()):
        assigned_counts = count_assigned_far_rows(assigned_file)
    configured_end = pipeline.get("configured_gps_end")
    current_candidates = [
        value for value in (
            zerolag.get("current_injected_gps"),
            summary.get("gps_end"))
        if value is not None
    ]
    current_gps = max(current_candidates) if current_candidates else None
    if gate.get("enabled"):
        visible_snapshot_gps = zerolag.get("current_injected_gps")
        if visible_snapshot_gps is not None:
            current_gps = float(visible_snapshot_gps)
        elif summary.get("gps_end") is not None:
            current_gps = float(summary["gps_end"])
        else:
            current_gps = configured_start
    injected = None
    if configured_start is not None and current_gps is not None:
        injected = max(0, current_gps - configured_start)
    elapsed_origin = first_bank_start_unix() or start_wall
    elapsed = time.time() - elapsed_origin
    background_start_gps = bg_status.get("gps_start")
    background_end_gps = bg_status.get("gps_end")
    background_duration = bg_status.get(
        "accumulated_background_time_seconds",
        bg_status.get("background_duration_seconds",
                      bg_status.get("duration_seconds")))
    return {
        "updated_utc": utc_now(),
        "job_id": args.job_id,
        "run_dir": str(Path(".").resolve()),
        "elapsed_seconds": elapsed,
        "elapsed_hms": str(_dt.timedelta(seconds=int(elapsed))),
        "configured_gps_start": configured_start,
        "configured_start_utc": gps_to_utc_label(configured_start),
        "configured_gps_end": configured_end,
        "configured_end_utc": gps_to_utc_label(configured_end),
        "current_injected_gps": current_gps,
        "current_injected_utc": gps_to_utc_label(current_gps),
        "current_injected_range_utc": gps_range_label(configured_start, current_gps),
        "current_injected_duration_seconds": injected,
        "current_injected_duration_hours": (
            injected / 3600.0 if injected is not None else None),
        "current_injected_duration_hms": seconds_hms(injected),
        "current_bank_group": bank_group,
        "current_bank_log_line": bank_line,
        "bank_groups_seen": summary.get("bank_groups"),
        "bank_ranges_seen": summary.get("bank_ranges"),
        "zerolag_file_count": zerolag.get("zerolag_file_count"),
        "raw_zerolag_file_count": zerolag.get("raw_zerolag_file_count"),
        "latest_zerolag_file": zerolag.get("latest_zerolag_file"),
        "raw_latest_zerolag_file": zerolag.get("raw_latest_zerolag_file"),
        "latest_snapshot_start": zerolag.get("latest_snapshot_start"),
        "latest_snapshot_start_utc": zerolag.get("latest_snapshot_start_utc"),
        "latest_snapshot_duration": zerolag.get("latest_snapshot_duration"),
        "latest_snapshot_end": zerolag.get("latest_snapshot_end"),
        "latest_snapshot_end_utc": zerolag.get("latest_snapshot_end_utc"),
        "raw_latest_snapshot_start": zerolag.get("raw_latest_snapshot_start"),
        "raw_latest_snapshot_start_utc": zerolag.get("raw_latest_snapshot_start_utc"),
        "raw_latest_snapshot_duration": zerolag.get("raw_latest_snapshot_duration"),
        "raw_latest_snapshot_end": zerolag.get("raw_latest_snapshot_end"),
        "raw_latest_snapshot_end_utc": zerolag.get("raw_latest_snapshot_end_utc"),
        "raw_current_injected_gps": zerolag.get("raw_current_injected_gps"),
        "raw_current_injected_utc": gps_to_utc_label(
            zerolag.get("raw_current_injected_gps")),
        "online_replay_sync": gate.get("enabled"),
        "online_replay_start_gps": gate.get("start_gps"),
        "online_replay_start_wall": gate.get("start_wall"),
        "online_replay_rate": gate.get("rate"),
        "online_replay_allowed_lag_seconds": gate.get("allowed_lag_seconds"),
        "online_replay_allowed_gps": gate.get("allowed_gps"),
        "online_replay_allowed_utc": gate.get("allowed_utc"),
        "online_replay_allowed_range_utc": gps_range_label(
            configured_start, gate.get("allowed_gps")),
        "online_replay_allowed_duration_seconds": gate.get("allowed_duration_seconds"),
        "postcoh_trigger_rows": summary.get("postcoh_rows"),
        "identified_trigger_rows_H1": summary.get("feature_rows_H1"),
        "identified_trigger_rows_L1": summary.get("feature_rows_L1"),
        "identified_trigger_rows_total": summary.get("feature_rows_total"),
        "feature_rows_H1": summary.get("feature_rows_H1"),
        "feature_rows_L1": summary.get("feature_rows_L1"),
        "feature_rows_total": summary.get("feature_rows_total"),
        "accumulated_background_trigger_rows_single": bg_status.get(
            "accumulated_background_trigger_rows_single",
            bg_status.get("feature_rows_total")),
        "accumulated_background_trigger_rows_H1": bg_status.get(
            "accumulated_background_trigger_rows_H1",
            bg_status.get("feature_rows_H1")),
        "accumulated_background_trigger_rows_L1": bg_status.get(
            "accumulated_background_trigger_rows_L1",
            bg_status.get("feature_rows_L1")),
        "accumulated_background_trigger_rows_total": bg_status.get(
            "accumulated_background_trigger_rows_total",
            bg_status.get("feature_rows_total")),
        "accumulated_background_time_seconds": bg_status.get(
            "accumulated_background_time_seconds",
            bg_status.get("background_duration_seconds",
                          bg_status.get("duration_seconds"))),
        "accumulated_background_time_hours": bg_status.get(
            "accumulated_background_time_hours",
            bg_status.get("duration_hours")),
        "accumulated_background_time_hms": seconds_hms(background_duration),
        "background_gps_start": background_start_gps,
        "background_start_utc": bg_status.get(
            "gps_start_utc", gps_to_utc_label(background_start_gps)),
        "background_gps_end": background_end_gps,
        "background_end_utc": bg_status.get(
            "gps_end_utc", gps_to_utc_label(background_end_gps)),
        "background_range_utc": gps_range_label(
            background_start_gps, background_end_gps),
        "background_latest_snapshot_end_gps": bg_status.get("latest_snapshot_end_gps"),
        "background_latest_snapshot_end_utc": bg_status.get(
            "latest_snapshot_end_utc",
            gps_to_utc_label(bg_status.get("latest_snapshot_end_gps"))),
        "formal_assigned_far_file": assigned_file if Path(assigned_file).exists() else None,
        "formal_assigned_far_rows_H1": assigned_counts.get("H1"),
        "formal_assigned_far_rows_L1": assigned_counts.get("L1"),
        "formal_assigned_far_rows_total": assigned_counts.get("total"),
        "assigned_single_far_rows": assigned_counts.get("total"),
        "support_points": bg_status.get("support_points"),
        "background_ready": bg_status.get("background_ready"),
        "background_file": bg_status.get("background_file"),
        "plot_file": bg_status.get("plot_file"),
        "plot_support_points": plot_summary.get("support_points"),
    }


def write_status(status, args, header_state):
    Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
    tmp_json = args.json_output + ".tmp"
    Path(tmp_json).write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    os.replace(tmp_json, args.json_output)

    columns = [
        "updated_utc", "job_id", "elapsed_hms", "configured_gps_start",
        "configured_start_utc", "current_injected_gps",
        "current_injected_utc", "current_injected_range_utc",
        "current_injected_duration_seconds", "current_injected_duration_hms",
        "current_bank_group", "zerolag_file_count",
        "identified_trigger_rows_H1", "identified_trigger_rows_L1",
        "identified_trigger_rows_total",
        "accumulated_background_trigger_rows_single",
        "accumulated_background_trigger_rows_H1",
        "accumulated_background_trigger_rows_L1",
        "accumulated_background_trigger_rows_total",
        "accumulated_background_time_seconds",
        "accumulated_background_time_hms",
        "background_gps_start", "background_start_utc",
        "background_gps_end", "background_end_utc", "background_range_utc",
        "formal_assigned_far_rows_H1", "formal_assigned_far_rows_L1",
        "formal_assigned_far_rows_total", "postcoh_trigger_rows",
        "support_points",
        "background_ready", "online_replay_sync",
        "online_replay_allowed_duration_seconds", "raw_zerolag_file_count",
        "latest_zerolag_file",
    ]
    tsv_path = Path(args.tsv_output)
    need_header = not tsv_path.exists() or not header_state.get("written")
    with tsv_path.open("a") as output:
        if need_header:
            output.write("\t".join(columns) + "\n")
            header_state["written"] = True
        output.write("\t".join(str(status.get(col, "")) for col in columns)
                     + "\n")
    display = dict(status)
    for key in (
            "current_injected_duration_seconds",
            "identified_trigger_rows_H1",
            "identified_trigger_rows_L1",
            "identified_trigger_rows_total",
            "accumulated_background_trigger_rows_H1",
            "accumulated_background_trigger_rows_L1",
            "accumulated_background_trigger_rows_total",
            "accumulated_background_time_seconds",
            "formal_assigned_far_rows_H1",
            "formal_assigned_far_rows_L1",
            "formal_assigned_far_rows_total"):
        if display.get(key) is None:
            display[key] = 0
    for key in ("current_injected_range_utc", "background_range_utc"):
        if display.get(key) is None:
            display[key] = "n/a"
    with Path(args.log_output).open("a") as output:
        output.write(
            "[{updated_utc}] elapsed={elapsed_hms} "
            "injected={current_injected_duration_seconds}s "
            "({current_injected_range_utc}) "
            "background=(H:{accumulated_background_trigger_rows_H1}, "
            "L:{accumulated_background_trigger_rows_L1}, "
            "total:{accumulated_background_trigger_rows_total}, "
            "time:{accumulated_background_time_seconds}s "
            "({background_range_utc})) "
            "FAR=(H:{formal_assigned_far_rows_H1}, "
            "L:{formal_assigned_far_rows_L1}, "
            "total:{formal_assigned_far_rows_total})\n".format(**display))


def main():
    args = parse_args()
    os.chdir(args.run_dir)
    start_wall = time.time()
    header_state = {}
    while True:
        status = build_status(start_wall, args)
        write_status(status, args, header_state)
        print(json.dumps(status, sort_keys=True), flush=True)
        if args.stop_file and Path(args.stop_file).exists():
            break
        time.sleep(max(1.0, float(args.interval)))


if __name__ == "__main__":
    main()
