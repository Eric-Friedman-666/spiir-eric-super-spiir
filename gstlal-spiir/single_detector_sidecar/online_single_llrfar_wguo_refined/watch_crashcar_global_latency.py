#!/usr/bin/env python3
"""Watch crashcar global single-FAR and multi zerolag latency surfaces."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import subprocess
import time
from pathlib import Path


GPS_UTC_OFFSET_SECONDS = 18
GPS_EPOCH_UNIX = 315964800
ZEROLAG_RE = re.compile(r"(?:^|/)(\d{3})_zerolag_(\d+)_(\d+)\.xml(?:\.gz)?$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--job-id", default="")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def utc(ts: float | None = None) -> str:
    if ts is None:
        ts = time.time()
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def cmd_out(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            args, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def job_state(job_id: str) -> str:
    if not job_id:
        return "UNKNOWN"
    out = cmd_out(["squeue", "-h", "-j", job_id, "-o", "%T"])
    if out:
        return out.splitlines()[0].strip()
    out = cmd_out(["sacct", "-n", "-P", "-j", job_id, "--format=State"])
    states = [line.split("|")[0].strip() for line in out.splitlines() if line.strip()]
    return states[0] if states else "UNKNOWN"


def safe_float(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def gps_from_row(row: dict[str, str]) -> float | None:
    gps = safe_float(row.get("end_time"))
    if gps is None:
        return None
    ns = safe_float(row.get("end_time_ns")) or 0.0
    return gps + ns * 1.0e-9


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def read_config(run_dir: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    for path in sorted((run_dir / "logs").glob("run_config_*.env")):
        try:
            for line in path.read_text(errors="replace").splitlines():
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip().strip("'\"")
        except OSError:
            continue
    return config


def expected_wall(gps: float | None, status: dict, config: dict) -> float | None:
    if gps is None:
        return None
    start_wall = safe_float(status.get("online_replay_start_wall"))
    if start_wall is None:
        return None
    start_gps = safe_float(
        status.get("online_replay_start_gps")
        or config.get("ONLINE_REPLAY_START_GPS")
        or config.get("DATA_START_TIME"))
    if start_gps is None:
        return None
    rate = safe_float(status.get("online_replay_rate") or config.get("ONLINE_REPLAY_RATE")) or 1.0
    return start_wall + (gps - start_gps) / rate


def latency_seconds(mtime: float | None,
                    gps: float | None,
                    status: dict,
                    config: dict) -> float | None:
    wall = expected_wall(gps, status, config)
    if mtime is None or wall is None:
        return None
    return mtime - wall


def frontier_lag_seconds(status: dict, gps: float | None) -> float | None:
    if gps is None:
        return None
    frontier = safe_float(status.get("current_injected_gps"))
    if frontier is None:
        frontier = safe_float(status.get("online_replay_allowed_gps"))
    if frontier is None:
        return None
    return frontier - gps


def summarize_csv(path: Path) -> dict:
    out = {
        "exists": path.exists(),
        "mtime": None,
        "rows": 0,
        "latest_event_gps": None,
        "ifo_counts": {"H1": 0, "L1": 0},
    }
    if not path.exists():
        return out
    try:
        out["mtime"] = path.stat().st_mtime
    except OSError:
        return out
    try:
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                out["rows"] += 1
                ifo = (row.get("ifo") or row.get("ifos") or "").strip()
                if ifo in out["ifo_counts"]:
                    out["ifo_counts"][ifo] += 1
                gps = gps_from_row(row)
                if gps is not None and (
                        out["latest_event_gps"] is None
                        or gps > out["latest_event_gps"]):
                    out["latest_event_gps"] = gps
    except (OSError, csv.Error):
        pass
    return out


def summarize_detail(run_dir: Path) -> dict:
    files = sorted(run_dir.glob("crashcar_singlefar_detail_worker*.csv"))
    latest_mtime = None
    latest_event = None
    latest_assignment_unix = None
    rows = 0
    for path in files:
        try:
            latest_mtime = max(latest_mtime or 0.0, path.stat().st_mtime)
        except OSError:
            continue
        try:
            with path.open(newline="") as handle:
                for row in csv.DictReader(handle):
                    rows += 1
                    gps = gps_from_row(row)
                    if gps is not None and (latest_event is None or gps > latest_event):
                        latest_event = gps
                        latest_assignment_unix = safe_float(row.get("assignment_unix"))
        except (OSError, csv.Error):
            continue
    return {
        "detail_files": len(files),
        "detail_rows": rows,
        "detail_latest_mtime": latest_mtime,
        "detail_latest_event_gps": latest_event,
        "detail_latest_assignment_unix": latest_assignment_unix,
    }


def summarize_zerolag(run_dir: Path) -> dict:
    latest_end = None
    latest_mtime = None
    latest_file = None
    files = sorted(run_dir.glob("*/*_zerolag_*.xml.gz"))
    for path in files:
        match = ZEROLAG_RE.search(str(path))
        if not match:
            continue
        end = float(match.group(2)) + float(match.group(3))
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if latest_end is None or end > latest_end or (
                end == latest_end and mtime > (latest_mtime or 0.0)):
            latest_end = end
            latest_mtime = mtime
            latest_file = str(path.relative_to(run_dir))
    return {
        "zerolag_files": len(files),
        "zerolag_latest_file": latest_file,
        "zerolag_latest_end_gps": latest_end,
        "zerolag_latest_mtime": latest_mtime,
    }


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    monitor = run_dir / "monitor"
    monitor.mkdir(parents=True, exist_ok=True)
    tsv = monitor / "crashcar_global_latency_watch.tsv"
    latest_json = monitor / "crashcar_global_latency_latest.json"
    summary_json = monitor / "crashcar_global_latency_summary.json"
    fields = [
        "sample_unix", "sample_utc", "state", "current_injected_gps",
        "detail_files", "detail_rows", "detail_latest_event_gps",
        "detail_latest_assignment_unix",
        "detail_latest_latency_s", "detail_frontier_lag_s",
        "single_rows", "single_latest_event_gps", "single_latest_latency_s",
        "single_frontier_lag_s",
        "single_h1_rows", "single_l1_rows",
        "zerolag_files", "zerolag_latest_file", "zerolag_latest_end_gps",
        "zerolag_latest_latency_s", "zerolag_frontier_lag_s",
    ]
    if not tsv.exists():
        with tsv.open("w", newline="") as handle:
            csv.DictWriter(handle, fieldnames=fields, delimiter="\t").writeheader()

    stable_done = 0
    while True:
        config = read_config(run_dir)
        status = read_json(monitor / "realtime_status.json")
        state = job_state(args.job_id)
        detail = summarize_detail(run_dir)
        single = summarize_csv(run_dir / "single_branch" / "single_final_far_all.csv")
        zerolag = summarize_zerolag(run_dir)
        now = time.time()
        row = {
            "sample_unix": now,
            "sample_utc": utc(now),
            "state": state,
            "current_injected_gps": safe_float(status.get("current_injected_gps")),
            "detail_files": detail["detail_files"],
            "detail_rows": detail["detail_rows"],
            "detail_latest_event_gps": detail["detail_latest_event_gps"],
            "detail_latest_assignment_unix": detail["detail_latest_assignment_unix"],
            "detail_latest_latency_s": latency_seconds(
                detail["detail_latest_assignment_unix"]
                if detail["detail_latest_assignment_unix"] is not None
                else detail["detail_latest_mtime"],
                detail["detail_latest_event_gps"], status, config),
            "detail_frontier_lag_s": frontier_lag_seconds(
                status, detail["detail_latest_event_gps"]),
            "single_rows": single["rows"],
            "single_latest_event_gps": single["latest_event_gps"],
            "single_latest_latency_s": latency_seconds(
                single["mtime"], single["latest_event_gps"], status, config),
            "single_frontier_lag_s": frontier_lag_seconds(
                status, single["latest_event_gps"]),
            "single_h1_rows": single["ifo_counts"]["H1"],
            "single_l1_rows": single["ifo_counts"]["L1"],
            "zerolag_files": zerolag["zerolag_files"],
            "zerolag_latest_file": zerolag["zerolag_latest_file"],
            "zerolag_latest_end_gps": zerolag["zerolag_latest_end_gps"],
            "zerolag_latest_latency_s": latency_seconds(
                zerolag["zerolag_latest_mtime"], zerolag["zerolag_latest_end_gps"],
                status, config),
            "zerolag_frontier_lag_s": frontier_lag_seconds(
                status, zerolag["zerolag_latest_end_gps"]),
        }
        with tsv.open("a", newline="") as handle:
            csv.DictWriter(handle, fieldnames=fields, delimiter="\t").writerow(row)
        payload = {
            **row,
            "run_dir": str(run_dir),
            "job_id": args.job_id,
            "online_replay_sync": config.get("ONLINE_REPLAY_SYNC"),
            "online_replay_rate": config.get("ONLINE_REPLAY_RATE"),
            "online_replay_start_gps": (
                status.get("online_replay_start_gps")
                or config.get("ONLINE_REPLAY_START_GPS")),
            "online_replay_start_wall": status.get("online_replay_start_wall"),
        }
        latest_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        summary_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        if args.once:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if state not in ("PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "UNKNOWN"):
            stable_done += 1
        else:
            stable_done = 0
        if stable_done >= 4:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
