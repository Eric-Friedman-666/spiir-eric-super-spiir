#!/usr/bin/env python3
"""Terminal table monitor for an online single-detector SPIIR run.

This script is read-only.  It watches the JSON/CSV surfaces already written by
the realtime monitor and worker-local single-detector sidecars, then redraws a
compact table in place so the user does not have to read an ever-growing tail.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


GPS_UTC_OFFSET_SECONDS = 18
GPS_EPOCH_UNIX = 315964800
ZEROLAG_XML_FEATURE_CACHE = {}
FAR_LEDGER_CACHE = {}
FRAME_CACHE_COVERAGE_CACHE = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display a dynamic status table for an Eric-super-spiir run.")
    parser.add_argument("--run-dir", default=".", help="Run directory to watch.")
    parser.add_argument("--job-id", default="", help="Optional Slurm job id.")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Refresh interval in seconds.")
    parser.add_argument("--once", action="store_true",
                        help="Render one table and exit.")
    parser.add_argument("--no-clear", action="store_true",
                        help="Do not clear the terminal before rendering.")
    parser.add_argument("--no-slurm", action="store_true",
                        help="Do not call squeue for job state.")
    parser.add_argument("--log-lines", type=int, default=0,
                        help="Number of recent monitor log lines to show.")
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def gps_to_utc_label(gps) -> str | None:
    if gps is None:
        return None
    try:
        unix_time = float(gps) + GPS_EPOCH_UNIX - GPS_UTC_OFFSET_SECONDS
    except (TypeError, ValueError, OverflowError):
        return None
    return dt.datetime.utcfromtimestamp(unix_time).replace(
        microsecond=0).isoformat() + "Z"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except Exception:
        return ""


def read_run_config(run_dir: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    for path in sorted((run_dir / "logs").glob("run_config_*.env")):
        for line in read_text(path).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            try:
                parsed = shlex.split(line, comments=False, posix=True)
            except ValueError:
                parsed = [line]
            if not parsed or "=" not in parsed[0]:
                continue
            key, value = parsed[0].split("=", 1)
            config[key.strip()] = value
    return config


def parse_gps(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def fmt_gps_utc(value) -> str:
    gps = parse_gps(value)
    if gps is None:
        return "-"
    return f"{gps_to_utc_label(gps)} ({gps})"


def parse_cache_range(cache_file: str | None) -> tuple[int | None, int | None]:
    if not cache_file:
        return None, None
    match = re.search(r"_(\d+)_(\d+)\.cache$", Path(cache_file).name)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def frame_cache_read_starts(cache_file: str | None,
                            requested_start: int | None = None) -> dict[str, int]:
    starts: dict[str, int] = {}
    if not cache_file:
        return starts
    path = Path(cache_file).expanduser()
    try:
        handle = path.open()
    except OSError:
        return starts
    with handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 4:
                continue
            ifo = fields[0].strip().upper()
            if ifo in {"H", "H1"}:
                key = "H1"
            elif ifo in {"L", "L1"}:
                key = "L1"
            else:
                continue
            gps = parse_gps(fields[2])
            duration = parse_gps(fields[3])
            if gps is None:
                continue
            read_start = gps
            if requested_start is not None and duration is not None:
                if gps <= requested_start < gps + duration:
                    read_start = requested_start
                elif gps < requested_start:
                    continue
            starts[key] = min(read_start, starts.get(key, read_start))
    return starts


def frame_cache_online_seconds(cache_file: str | None, window_start,
                               window_end) -> dict[str, float | None]:
    result = {"H1": None, "L1": None}
    start = parse_gps(window_start)
    end = parse_gps(window_end)
    if not cache_file or start is None or end is None or end <= start:
        return result
    path = Path(cache_file).expanduser()
    try:
        stat = path.stat()
    except OSError:
        return result
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size, start, end)
    cached = FRAME_CACHE_COVERAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    intervals = {"H1": [], "L1": []}
    try:
        with path.open() as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 4:
                    continue
                ifo = fields[0].strip().upper()
                if ifo in {"H", "H1"}:
                    key = "H1"
                elif ifo in {"L", "L1"}:
                    key = "L1"
                else:
                    continue
                gps = parse_gps(fields[2])
                duration = parse_gps(fields[3])
                if gps is None or duration is None:
                    continue
                overlap_start = max(start, gps)
                overlap_end = min(end, gps + duration)
                if overlap_end > overlap_start:
                    intervals[key].append((overlap_start, overlap_end))
    except OSError:
        return result

    for key, rows in intervals.items():
        if not rows:
            result[key] = 0.0
            continue
        rows.sort()
        total = 0.0
        current_start, current_end = rows[0]
        for row_start, row_end in rows[1:]:
            if row_start <= current_end:
                current_end = max(current_end, row_end)
            else:
                total += current_end - current_start
                current_start, current_end = row_start, row_end
        total += current_end - current_start
        result[key] = total

    FRAME_CACHE_COVERAGE_CACHE[cache_key] = result
    return result


def timing_summary(run_dir: Path) -> dict[str, str]:
    config = read_run_config(run_dir)
    cache_file = config.get("FRAME_CACHE_FILE")
    cache_start, cache_end = parse_cache_range(cache_file)
    first_starts = frame_cache_read_starts(cache_file, cache_start)
    hl_first = None
    if first_starts.get("H1") is not None and first_starts.get("L1") is not None:
        hl_first = max(first_starts["H1"], first_starts["L1"])
    data_start = parse_gps(config.get("DATA_START_TIME"))
    official_start = data_start if data_start is not None else hl_first
    return {
        "cache_start": fmt_gps_utc(cache_start),
        "cache_end": fmt_gps_utc(cache_end),
        "h_first": fmt_gps_utc(first_starts.get("H1")),
        "l_first": fmt_gps_utc(first_starts.get("L1")),
        "hl_start": fmt_gps_utc(official_start),
    }


def infer_job_id(run_dir: Path) -> str:
    for path in sorted((run_dir / "logs").glob("run_config_*.env"), reverse=True):
        match = re.search(r"run_config_(\d+)\.env$", path.name)
        if match:
            return match.group(1)
    return ""


def slurm_status(job_id: str) -> dict:
    if not job_id:
        return {}
    try:
        result = subprocess.run(
            ["squeue", "-h", "-j", job_id, "-o", "%i|%T|%M|%D|%R"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return {}
    line = result.stdout.strip().splitlines()
    if not line:
        return {"job_id": job_id, "state": "not-in-squeue"}
    parts = line[0].split("|", 4)
    while len(parts) < 5:
        parts.append("")
    return {
        "job_id": parts[0],
        "state": parts[1],
        "elapsed": parts[2],
        "nodes": parts[3],
        "node_list": parts[4],
    }


def slurm_workdir(job_id: str) -> Path | None:
    if not job_id:
        return None
    try:
        result = subprocess.run(
            ["scontrol", "show", "job", job_id],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return None
    match = re.search(r"\bWorkDir=(\S+)", result.stdout)
    if not match:
        return None
    path = Path(match.group(1)).expanduser()
    return path if path.exists() else None


def candidate_results_roots(run_dir: Path) -> list[Path]:
    roots = []
    for root in (
        run_dir.parent if run_dir.name.startswith("run_") else None,
        Path(os.environ.get("ERIC_SUPER_SPIIR_RESULTS", "")).expanduser()
        if os.environ.get("ERIC_SUPER_SPIIR_RESULTS") else None,
        Path.home() / "spiir-eric-super-spiir" / "gstlal-spiir" / "results",
        Path("/home/qliang/spiir-eric-super-spiir/gstlal-spiir/results"),
    ):
        if root and root not in roots and root.exists():
            roots.append(root)
    return roots


def find_run_dir_by_job_id(run_dir: Path, job_id: str) -> Path | None:
    if not job_id:
        return None
    for root in candidate_results_roots(run_dir):
        matches = sorted(root.glob(f"run_*/logs/run_config_{job_id}.env"))
        if matches:
            return matches[-1].parents[1]
    return None


def newest_run_dir(run_dir: Path) -> Path | None:
    for root in candidate_results_roots(run_dir):
        runs = sorted(path for path in root.glob("run_*") if looks_like_run_dir(path))
        if runs:
            return runs[-1]
    return None


def looks_like_run_dir(path: Path) -> bool:
    return (
        (path / "monitor").exists()
        or (path / "logs").exists()
        or any(path.glob("[0-9][0-9][0-9]/*_zerolag_*.xml.gz"))
    )


def resolve_run_dir(run_dir: Path, job_id: str) -> tuple[Path, str | None]:
    run_dir = run_dir.expanduser().resolve()
    if looks_like_run_dir(run_dir):
        return run_dir, None
    workdir = slurm_workdir(job_id)
    if workdir and looks_like_run_dir(workdir):
        return workdir.resolve(), str(run_dir)
    matched = find_run_dir_by_job_id(run_dir, job_id)
    if matched and looks_like_run_dir(matched):
        return matched.resolve(), str(run_dir)
    newest = newest_run_dir(run_dir)
    if newest:
        return newest.resolve(), str(run_dir)
    return run_dir, None


def fmt_bool(value) -> str:
    if value is True:
        return "YES"
    if value is False:
        return "NO"
    return "?"


def fmt_number(value) -> str:
    if value is None:
        return "-"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.3g}"


def fmt_seconds(value) -> str:
    if value is None:
        return "-"
    try:
        seconds = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return str(value)
    return str(dt.timedelta(seconds=max(0, seconds)))


def parse_utc_seconds(label: str | None) -> float | None:
    if not label:
        return None
    text = str(label).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def live_elapsed_hms(realtime: dict) -> str:
    """Advance elapsed seconds locally between realtime JSON updates."""
    base = realtime.get("elapsed_seconds")
    try:
        base_seconds = float(base)
    except (TypeError, ValueError):
        return realtime.get("elapsed_hms") or "-"
    updated = parse_utc_seconds(realtime.get("updated_utc"))
    if updated is not None:
        base_seconds += max(0.0, time.time() - updated)
    return fmt_seconds(base_seconds)


def fmt_range(start, end) -> str:
    if start and end:
        return f"{start} -> {end}"
    if end:
        return f"... -> {end}"
    return "-"


def hlt(h, l, total) -> str:
    return f"H:{fmt_number(h)} L:{fmt_number(l)} T:{fmt_number(total)}"


def truncate(text: str, width: int) -> str:
    text = str(text)
    if width <= 1:
        return text[:width]
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def make_table(headers: list[str], rows: list[list[str]],
               max_width: int | None = None) -> str:
    if not rows:
        rows = [["-" for _ in headers]]
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))
    if max_width:
        static = 3 * (len(widths) - 1)
        budget = max_width - static
        while sum(widths) > budget and max(widths) > 8:
            idx = max(range(len(widths)), key=lambda i: widths[i])
            widths[idx] -= 1
    lines = []
    header_line = " | ".join(truncate(h, widths[i]).ljust(widths[i])
                             for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * widths[i] for i in range(len(widths)))
    lines.extend([header_line, sep_line])
    for row in rows:
        lines.append(" | ".join(
            truncate(str(cell), widths[i]).ljust(widths[i])
            for i, cell in enumerate(row)
        ))
    return "\n".join(lines)


def csv_row_counts(path: Path, assignment_utc: str | None = None) -> dict:
    counts = {"H1": 0, "L1": 0, "total": 0}
    if not path.exists():
        return counts
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if assignment_utc is not None and row.get("assignment_utc") != assignment_utc:
                    continue
                ifo = (row.get("ifo") or row.get("ifos") or "").strip().upper()
                if ifo == "H1":
                    counts["H1"] += 1
                elif ifo == "L1":
                    counts["L1"] += 1
                counts["total"] += 1
    except Exception:
        return {"H1": None, "L1": None, "total": None}
    return counts


def csv_ledger_summary(path: Path) -> dict:
    empty = {"counts": {"H1": 0, "L1": 0, "total": 0}, "utc_range": "-"}
    if not path.exists():
        return empty
    try:
        stat = path.stat()
    except OSError:
        return empty
    cache_key = str(path)
    cache_sig = (stat.st_mtime_ns, stat.st_size)
    cached = FAR_LEDGER_CACHE.get(cache_key)
    if cached and cached.get("sig") == cache_sig:
        return cached["summary"]

    counts = {"H1": 0, "L1": 0, "total": 0}
    starts = []
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ifo = (row.get("ifo") or row.get("ifos") or "").strip().upper()
                if ifo == "H1":
                    counts["H1"] += 1
                elif ifo == "L1":
                    counts["L1"] += 1
                counts["total"] += 1
                try:
                    end_time = float(row.get("end_time"))
                except (TypeError, ValueError):
                    continue
                try:
                    end_time += float(row.get("end_time_ns") or 0.0) * 1e-9
                except (TypeError, ValueError):
                    pass
                starts.append(end_time)
    except Exception:
        return {"counts": {"H1": None, "L1": None, "total": None}, "utc_range": "-"}

    summary = {
        "counts": counts,
        "utc_range": fmt_range(
            gps_to_utc_label(min(starts)) if starts else None,
            gps_to_utc_label(max(starts)) if starts else None),
    }
    FAR_LEDGER_CACHE[cache_key] = {"sig": cache_sig, "summary": summary}
    return summary


def subtract_counts(total: dict, part: dict) -> dict:
    result = {}
    for key in ("H1", "L1", "total"):
        if total.get(key) is None or part.get(key) is None:
            result[key] = None
        else:
            result[key] = total.get(key, 0) - part.get(key, 0)
    return result


def finite_positive(value) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return numeric > 0.0


def zerolag_feature_csv_summary(path: Path, min_snr=4.0) -> dict:
    summary = {
        "postcoh_rows": None,
        "H1": None,
        "L1": None,
        "total": None,
        "utc_range": "-",
    }
    if not path.exists():
        return summary
    postcoh_rows = 0
    counts = {"H1": 0, "L1": 0}
    gps_values = []
    try:
        min_snr = float(min_snr)
    except (TypeError, ValueError):
        min_snr = 4.0
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                postcoh_rows += 1
                if finite_positive(row.get("end_time")):
                    gps_values.append(float(row["end_time"]))
                ifos = row.get("ifos", "")
                for ifo in ("H1", "L1"):
                    if ifos and ifo not in ifos:
                        continue
                    snr = row.get(f"snglsnr_{ifo}")
                    chisq = row.get(f"chisq_{ifo}")
                    if not finite_positive(snr) or not finite_positive(chisq):
                        continue
                    try:
                        if float(snr) >= min_snr:
                            counts[ifo] += 1
                    except ValueError:
                        continue
    except Exception:
        return summary
    summary.update({
        "postcoh_rows": postcoh_rows,
        "H1": counts["H1"],
        "L1": counts["L1"],
        "total": counts["H1"] + counts["L1"],
        "utc_range": fmt_range(
            gps_to_utc_label(min(gps_values)) if gps_values else None,
            gps_to_utc_label(max(gps_values)) if gps_values else None),
    })
    return summary


def zerolag_counts(run_dir: Path) -> tuple[int, dict[str, int], str, str]:
    counts: dict[str, int] = {}
    latest = ""
    latest_mtime = -1.0
    starts = []
    ends = []
    paths = list(run_dir.glob("[0-9][0-9][0-9]/sdpostcoh*.xml.gz"))
    if not paths:
        paths = list(run_dir.glob("[0-9][0-9][0-9]/*_zerolag_*.xml.gz"))
    for path in paths:
        group = path.parent.name
        counts[group] = counts.get(group, 0) + 1
        parts = snapshot_parts(path)
        if parts:
            start, _duration, end = parts
            starts.append(start)
            ends.append(end)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest = str(path.relative_to(run_dir))
    utc_range = fmt_range(
        gps_to_utc_label(min(starts)) if starts else None,
        gps_to_utc_label(max(ends)) if ends else None)
    return sum(counts.values()), counts, latest, utc_range


def xml_column_name(raw: str) -> str:
    name = raw.strip()
    if ":" in name:
        name = name.split(":", 1)[1]
    return name


def detector_feature_counts_from_row(row: dict[str, str],
                                     min_snr: float = 4.0) -> dict[str, int]:
    counts = {"H1": 0, "L1": 0}
    ifos = row.get("ifos", "")
    for ifo in ("H1", "L1"):
        if ifos and ifo not in ifos:
            continue
        snr = row.get(f"snglsnr_{ifo}")
        chisq = row.get(f"chisq_{ifo}")
        if not finite_positive(snr) or not finite_positive(chisq):
            continue
        try:
            if float(snr) >= min_snr:
                counts[ifo] += 1
        except ValueError:
            continue
    return counts


def count_zerolag_xml_features(path: Path, min_snr: float = 4.0) -> dict:
    try:
        stat = path.stat()
    except OSError:
        return {"H1": None, "L1": None, "total": None, "postcoh_rows": None}
    key = (str(path), stat.st_mtime_ns, stat.st_size, float(min_snr))
    cached = ZEROLAG_XML_FEATURE_CACHE.get(key)
    if cached is not None:
        return cached

    columns: list[str] = []
    counts = {"H1": 0, "L1": 0, "total": 0, "postcoh_rows": 0}
    column_re = re.compile(r'<Column\s+Name="([^"]+)"')
    opener = gzip.open if path.suffix == ".gz" else open
    in_stream = False
    try:
        with opener(path, "rt", errors="replace") as handle:
            for raw in handle:
                if not in_stream:
                    match = column_re.search(raw)
                    if match:
                        columns.append(xml_column_name(match.group(1)))
                    if "<Stream" in raw and 'Name="postcoh:table"' in raw:
                        in_stream = True
                    continue
                if "</Stream>" in raw:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    values = next(csv.reader([line]))
                except Exception:
                    continue
                row = dict(zip(columns, values[:len(columns)]))
                counts["postcoh_rows"] += 1
                row_counts = detector_feature_counts_from_row(row, min_snr)
                counts["H1"] += row_counts["H1"]
                counts["L1"] += row_counts["L1"]
    except Exception:
        return {"H1": None, "L1": None, "total": None, "postcoh_rows": None}
    counts["total"] = counts["H1"] + counts["L1"]
    ZEROLAG_XML_FEATURE_CACHE[key] = counts
    return counts


def snapshot_parts(path: Path) -> tuple[int, int, int] | None:
    match = re.search(
        r"(?:_zerolag_|sdpostcoh[^/_]*_|single_postcoh[^/_]*_)(\d+)_(\d+)\.xml(?:\.gz)?$",
        path.name)
    if not match:
        return None
    start = int(match.group(1))
    duration = int(match.group(2))
    return start, duration, start + duration


def parse_worker_nodes(run_dir: Path) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    worker_on_node = re.compile(r"worker\s+(\d+)/\d+\s+on\s+(\S+)")
    worker_bank = re.compile(
        r"worker\s+(\d+)\s+on\s+(\S+)\s+(?:starting|owns) bank group\s+(\d+)")
    for path in sorted((run_dir / "logs").glob("pipe_*.out")):
        for line in read_text(path).splitlines():
            match = worker_on_node.search(line)
            if match:
                worker = match.group(1)
                mapping.setdefault(worker, {})["node"] = match.group(2)
            match = worker_bank.search(line)
            if match:
                worker = match.group(1)
                mapping.setdefault(worker, {})["node"] = match.group(2)
                mapping.setdefault(worker, {})["start_group"] = match.group(3)
    return mapping


def worker_zerolag_summary(run_dir: Path, worker_id: str,
                           worker_group: str | None,
                           bank_groups: list[str] | None = None) -> dict:
    try:
        wid = int(worker_id)
        group_id = int(worker_group) if worker_group not in (None, "") else wid
    except ValueError:
        return {"files": None, "duration": None, "range": "-"}
    files = []
    starts = []
    ends = []
    detector_counts = {"H1": 0, "L1": 0, "total": 0, "postcoh_rows": 0}
    selected_groups = set(bank_groups or [])
    snapshot_paths = list(run_dir.glob("[0-9][0-9][0-9]/sdpostcoh*.xml.gz"))
    if not snapshot_paths:
        snapshot_paths = list(run_dir.glob("[0-9][0-9][0-9]/*_zerolag_*.xml.gz"))
    for path in snapshot_paths:
        if selected_groups and path.parent.name not in selected_groups:
            continue
        try:
            group = int(path.parent.name)
        except ValueError:
            continue
        if group != group_id:
            continue
        parts = snapshot_parts(path)
        if not parts:
            continue
        start, duration, end = parts
        files.append(path)
        starts.append(start)
        ends.append(end)
        counts = count_zerolag_xml_features(path)
        for key in ("H1", "L1", "total", "postcoh_rows"):
            if detector_counts.get(key) is None or counts.get(key) is None:
                detector_counts[key] = None
            else:
                detector_counts[key] += counts.get(key, 0)
    union_duration = (max(ends) - min(starts)) if starts and ends else None
    return {
        "files": len(files),
        "duration": union_duration,
        "range": f"{min(starts)}->{max(ends)}" if starts and ends else "-",
        "utc_range": fmt_range(
            gps_to_utc_label(min(starts)) if starts else None,
            gps_to_utc_label(max(ends)) if ends else None),
        "detector_rows": hlt(detector_counts.get("H1"),
                             detector_counts.get("L1"),
                             detector_counts.get("total")),
        "postcoh_rows": detector_counts.get("postcoh_rows"),
    }


def recent_lines(path: Path, count: int) -> list[str]:
    if count <= 0:
        return []
    text = read_text(path)
    if not text:
        return []
    return text.splitlines()[-count:]


def collect_workers(run_dir: Path, config: dict[str, str]) -> list[dict]:
    workers = []
    node_map = parse_worker_nodes(run_dir)
    frame_cache_file = config.get("FRAME_CACHE_FILE")
    for status_path in sorted((run_dir / "monitor").glob(
            "worker_*/latest_single_background_status.json")):
        match = re.search(r"worker_(\d+)", str(status_path))
        worker_id = match.group(1) if match else "?"
        status = read_json(status_path)
        worker_group = status.get("worker_group", worker_id)
        zsummary = worker_zerolag_summary(
            run_dir, worker_id, worker_group, status.get("bank_groups"))
        worker_dir = run_dir / "single_branch" / f"worker_{worker_id}"
        ledger = worker_dir / "single_final_far_all.csv"
        ledger_summary = csv_ledger_summary(ledger)
        far_counts = {
            "H1": status.get("formal_assigned_far_rows_H1"),
            "L1": status.get("formal_assigned_far_rows_L1"),
            "total": status.get("formal_assigned_far_rows_total"),
        }
        if any(value is None for value in far_counts.values()):
            far_counts = ledger_summary["counts"]
        assignment_ledger = read_json(
            run_dir / "monitor" / f"worker_{worker_id}"
            / "latest_single_assignment_ledger.json")
        new_far_counts = {
            "H1": None,
            "L1": None,
            "total": assignment_ledger.get("newly_assigned_rows",
                                           status.get("assignment_new_rows")),
        }
        kept_far_counts = subtract_counts(far_counts, new_far_counts)
        state_file = run_dir / "monitor" / f"worker_{worker_id}" / (
            ".last_single_background_update_snapshot_state")
        node_info = node_map.get(worker_id, {})
        bg_duration_seconds = status.get("accumulated_background_time_seconds",
                                         status.get("duration_seconds"))
        online_seconds = frame_cache_online_seconds(
            frame_cache_file, status.get("gps_start"), status.get("gps_end"))
        bg_update = "-"
        try:
            lag = float(zsummary.get("duration")) - float(bg_duration_seconds)
        except (TypeError, ValueError):
            pass
        else:
            bg_update = "current" if lag <= 0.5 else f"pending +{fmt_seconds(lag)}"
        workers.append({
            "id": worker_id,
            "node": node_info.get("node", "-"),
            "start_group": node_info.get("start_group", "-"),
            "ready": status.get("background_ready"),
            "groups": ",".join(status.get("bank_groups") or []),
            "ranges": ",".join(status.get("bank_ranges") or []),
            "injected": fmt_seconds(zsummary.get("duration")),
            "injected_range": zsummary.get("utc_range", "-"),
            "zerolag_coverage": fmt_seconds(zsummary.get("duration")),
            "zerolag_utc": zsummary.get("utc_range", "-"),
            "zerolag_detector_rows": zsummary.get("detector_rows", "H:- L:- T:-"),
            "bg_h1_trigger_span": fmt_seconds(status.get("feature_duration_seconds_H1")),
            "bg_l1_trigger_span": fmt_seconds(status.get("feature_duration_seconds_L1")),
            "bg_h1_online": fmt_seconds(online_seconds.get("H1")),
            "bg_l1_online": fmt_seconds(online_seconds.get("L1")),
            "zerolag_files": zsummary.get("files"),
            "zerolag_postcoh_rows": zsummary.get("postcoh_rows"),
            "bg_detector_rows": hlt(
                status.get("feature_rows_H1"),
                status.get("feature_rows_L1"),
                status.get("feature_rows_total")),
            "bg_trigger_range": fmt_range(
                status.get("feature_gps_start_utc"),
                status.get("feature_gps_end_utc")),
            "bg_rows_H1": status.get("feature_rows_H1"),
            "bg_rows_L1": status.get("feature_rows_L1"),
            "bg_rows_total": status.get("feature_rows_total"),
            "bg_start_utc": status.get("gps_start_utc"),
            "bg_end_utc": status.get("gps_end_utc"),
            "window": fmt_range(status.get("gps_start_utc"),
                                status.get("gps_end_utc")),
            "duration": fmt_seconds(bg_duration_seconds),
            "bg_update": bg_update,
            "files": status.get("files"),
            "input_files": status.get("input_files"),
            "filtered": status.get("filtered_before_window_files"),
            "bg_rows": hlt(status.get("feature_rows_H1"),
                           status.get("feature_rows_L1"),
                           status.get("feature_rows_total")),
            "far_rows": hlt(far_counts.get("H1"), far_counts.get("L1"),
                            far_counts.get("total")),
            "far_trigger_utc": ledger_summary.get("utc_range", "-"),
            "far_rows_H1": far_counts.get("H1"),
            "far_rows_L1": far_counts.get("L1"),
            "far_rows_total": far_counts.get("total"),
            "new_far_rows": hlt(new_far_counts.get("H1"),
                                new_far_counts.get("L1"),
                                new_far_counts.get("total")),
            "kept_far_rows": hlt(kept_far_counts.get("H1"),
                                 kept_far_counts.get("L1"),
                                 kept_far_counts.get("total")),
            "new_rows": status.get("assignment_new_rows"),
            "dup_rows": status.get("assignment_duplicate_candidate_rows"),
            "state": read_text(state_file).strip() or "-",
        })
    return workers


def collect(run_dir: Path, job_id: str, include_slurm: bool) -> dict:
    config = read_run_config(run_dir)
    realtime = read_json(run_dir / "monitor" / "realtime_status.json")
    pipeline_progress = read_json(run_dir / "monitor" / "pipeline_progress.json")
    aggregate = read_json(run_dir / "monitor" / "latest_single_background_status.json")
    summary = read_json(run_dir / "monitor" / "latest_single_summary.json")
    ztotal, zcounts, zlatest, zutc = zerolag_counts(run_dir)
    slurm = slurm_status(job_id) if include_slurm else {}
    merged_file = run_dir / "single_branch" / "single_final_far_all.csv"
    merged_summary = csv_ledger_summary(merged_file)
    merged_counts = {
        "H1": aggregate.get("formal_assigned_far_rows_H1"),
        "L1": aggregate.get("formal_assigned_far_rows_L1"),
        "total": aggregate.get("formal_assigned_far_rows_total"),
    }
    if any(value is None for value in merged_counts.values()):
        merged_counts = merged_summary["counts"]
    return {
        "realtime": realtime,
        "pipeline_progress": pipeline_progress,
        "aggregate": aggregate,
        "summary": summary,
        "workers": collect_workers(run_dir, config),
        "zerolag_total": ztotal,
        "zerolag_counts": zcounts,
        "zerolag_latest": zlatest,
        "zerolag_utc": zutc,
        "slurm": slurm,
        "job_id": job_id,
        "timing": timing_summary(run_dir),
        "merged_counts": merged_counts,
        "merged_far_utc": merged_summary.get("utc_range", "-"),
        "monitor_lines": recent_lines(
            run_dir / "monitor" / "realtime_monitor.log", 4),
        "source_cwd": None,
    }


def pair_table(rows: list[tuple[str, str]], width: int) -> str:
    paired = []
    for idx in range(0, len(rows), 2):
        left = rows[idx]
        right = rows[idx + 1] if idx + 1 < len(rows) else ("", "")
        paired.append([left[0], left[1], right[0], right[1]])
    return make_table(["Metric", "Value", "Metric", "Value"], paired,
                      max_width=width)


def sum_field(rows: list[dict], field: str):
    values = [row.get(field) for row in rows if row.get(field) is not None]
    if len(values) != len(rows):
        return None
    try:
        return sum(float(value) for value in values)
    except (TypeError, ValueError):
        return None


def worker_window_summary(workers: list[dict]) -> str:
    ranges = {
        (worker.get("bg_start_utc"), worker.get("bg_end_utc"))
        for worker in workers
        if worker.get("bg_start_utc") and worker.get("bg_end_utc")
    }
    if not ranges:
        return "-"
    if len(ranges) == 1:
        start, end = next(iter(ranges))
        return fmt_range(start, end)
    return "per-node windows differ"


def render(run_dir: Path, data: dict, log_lines: int) -> str:
    width = shutil.get_terminal_size((132, 40)).columns
    realtime = data["realtime"]
    pipeline_progress = data.get("pipeline_progress") or {}
    aggregate = data["aggregate"]
    slurm = data["slurm"]
    merged_counts = data["merged_counts"]
    workers = data["workers"]
    zcounts = data["zerolag_counts"]
    zgroups = " ".join(f"{group}:{count}" for group, count in sorted(zcounts.items()))
    if not zgroups:
        zgroups = "-"
    worker_bg_h = sum_field(workers, "bg_rows_H1")
    worker_bg_l = sum_field(workers, "bg_rows_L1")
    worker_bg_t = sum_field(workers, "bg_rows_total")
    worker_far_h = sum_field(workers, "far_rows_H1")
    worker_far_l = sum_field(workers, "far_rows_L1")
    worker_far_t = sum_field(workers, "far_rows_total")

    title = f"Eric-super-spiir monitor | {utc_now()}"
    lines = [title, str(run_dir.resolve())]
    if data.get("source_cwd"):
        lines.append(f"Auto-selected Slurm WorkDir; command cwd was {data['source_cwd']}")
    if data["job_id"] or slurm:
        lines.append(
            "Slurm: "
            f"job={slurm.get('job_id', data['job_id'] or '-')} "
            f"state={slurm.get('state', '-')} "
            f"elapsed={slurm.get('elapsed', '-')} "
            f"nodes={slurm.get('nodes', '-')} "
            f"where={slurm.get('node_list', '-')}")

    injected_live = (
        pipeline_progress.get("current_injected_duration_hms")
        or realtime.get("pipeline_injected_duration_hms")
        or "not exported")
    injected_live_utc = (
        pipeline_progress.get("current_injected_range_utc")
        or realtime.get("pipeline_injected_range_utc")
        or "not exported")

    timing_rows = [
        ("Cache start UTC", data["timing"].get("cache_start", "-")),
        ("Cache end UTC", data["timing"].get("cache_end", "-")),
        ("H read start UTC", data["timing"].get("h_first", "-")),
        ("L read start UTC", data["timing"].get("l_first", "-")),
        ("Official HL start UTC", data["timing"].get("hl_start", "-")),
        ("Injected live", f"{injected_live} ({injected_live_utc})"),
    ]
    lines.append("")
    lines.append("Run timing")
    lines.append(pair_table(timing_rows, width))

    for worker in workers:
        lines.append("")
        lines.append(f"Node {worker['node']} / worker {worker['id']}")
        node_rows = [
            ("Start bank group", worker["start_group"]),
            ("Bank range", worker["ranges"] or "-"),
            ("Zerolag files", fmt_number(worker["zerolag_files"])),
            ("ZeroLag triggers", worker["zerolag_detector_rows"]),
            ("Zerolag UTC", worker["zerolag_utc"]),
            ("BG trigger rows", worker["bg_detector_rows"]),
            ("Background ready", fmt_bool(worker["ready"])),
            ("H online", worker["bg_h1_online"]),
            ("L online", worker["bg_l1_online"]),
            ("Background time", worker["duration"]),
            ("Background window", worker["window"]),
            ("Total FAR rows", worker["far_rows"]),
            ("FAR trigger UTC", worker["far_trigger_utc"]),
        ]
        lines.append(pair_table(node_rows, width))

    final_rows = [
        ("Elapsed", live_elapsed_hms(realtime)),
        ("Slurm elapsed", slurm.get("elapsed", "-")),
        ("Zerolag", f"{fmt_number(data['zerolag_total'])} files"),
        ("Latest zerolag", data["zerolag_latest"] or realtime.get("raw_latest_zerolag_file", "-")),
        ("Zerolag UTC", data.get("zerolag_utc", "-")),
        ("Groups", zgroups),
        ("Background",
         f"{fmt_bool(aggregate.get('background_ready'))} / {fmt_seconds(aggregate.get('accumulated_background_time_seconds', aggregate.get('duration_seconds')))}"),
        ("Worker BG window", worker_window_summary(workers)),
        ("Worker BG rows", hlt(worker_bg_h, worker_bg_l, worker_bg_t)),
        ("Worker FAR sum", hlt(worker_far_h, worker_far_l, worker_far_t)),
        ("Merged FAR ledger", hlt(merged_counts.get("H1"),
                                  merged_counts.get("L1"),
                                  merged_counts.get("total"))),
        ("Merged FAR UTC", data.get("merged_far_utc", "-")),
        ("Merge lag",
         fmt_number(None if not workers
                    or worker_far_t is None
                    or merged_counts.get("total") is None
                    else worker_far_t - merged_counts.get("total"))),
        ("Worker count", fmt_number(len(workers))),
        ("Node list", slurm.get("node_list", "-")),
    ]
    lines.append("")
    lines.append("Final merged summary")
    lines.append(pair_table(final_rows, width))

    log_tail = data["monitor_lines"][-log_lines:] if log_lines else []
    if log_tail:
        lines.append("")
        lines.append("Recent monitor lines")
        lines.extend(truncate(line, width) for line in log_tail)

    lines.append("")
    lines.append("Press Ctrl-C to stop the table. Pipeline/job keeps running.")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    job_id = args.job_id or infer_job_id(run_dir)
    run_dir, source_cwd = resolve_run_dir(run_dir, job_id)
    if not job_id:
        job_id = infer_job_id(run_dir)
    first = True
    while True:
        data = collect(run_dir, job_id, include_slurm=not args.no_slurm)
        data["source_cwd"] = source_cwd
        output = render(run_dir, data, args.log_lines)
        if not args.no_clear and not args.once:
            if first:
                sys.stdout.write("\033[?25l")
            sys.stdout.write("\033[H\033[J")
        sys.stdout.write(output + "\n")
        sys.stdout.flush()
        if args.once:
            break
        first = False
        try:
            time.sleep(max(1.0, args.interval))
        except KeyboardInterrupt:
            break
    if not args.no_clear and not args.once:
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
