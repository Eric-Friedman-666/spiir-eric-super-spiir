#!/usr/bin/env python3
"""Reusable crashcar two-2x2 plotting tool.

The plotting contract follows Eric-bless-crashcar.pdf Section 4.1:

* Figure 1 panels (b)-(d) read every run-local FinalSink zerolag XML in the selected glob and aggregate the full history of triggers that have assigned FAR.
* Figure 1 panel (a) reads the selected worker's authoritative
  single_background.json. Crashcar C detail rows are available only as an
  explicit non-authoritative diagnostic source.
* Figure 2 discovers normal CoincsDoc candidate XML directly below run/ and
  joins each standard COMPLEX8 event_id through SnglInspiral/CoincMap. Missing,
  duplicate, or inexact identities fail closed; no second event stream or
  replacement curve is synthesized.
"""

from __future__ import annotations

from array import array
import argparse
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shlex
import stat
import textwrap
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap


FAR_BIN_LABELS = ["< -5", "[-5,-4)", "[-4,-3)", "[-3,-2)", "[-2,-1)", "[-1,0)", ">= 0"]
FAR_BIN_COLORS = ["#08306b", "#2166ac", "#4393c3", "#92c5de", "#fee08b", "#f46d43", "#a50026"]
IFO_COLORS = {"H1": "#1f77b4", "L1": "#ff7f0e", "V1": "#2ca02c", "K1": "#9467bd"}
CHISQ_VIEW = (0.5, 1.5)
LLR_XMIN = -10.0
LLR_XMAX = 200.0
SNR_XMIN = 4.0
FAR_POINT_SIZE = 16.0
TAIL_POINT_SIZE = 26.0
FIT_CURVE_MAX_POINTS = 700
TAIL_FIT_COLOR = "#2ca02c"
DEFAULT_SEGMENT_GLOB = "run/[0-9][0-9][0-9]/H1L1V1_SEGMENTS_*.xml.gz"
DEFAULT_SINGLE_FAR_BASES = ("far_sngl",)
DEFAULT_COHERENT_FAR_BASES = ("far_1w", "far_1d", "far_2h", "far")
ZEROLAG_NAME_RE = re.compile(r"_zerolag_(\d+)_(\d+)\.xml(?:\.gz)?$")
COINCS_NAME_RE = re.compile(r"^(?P<ifos>[A-Za-z0-9]+)_(?P<end_time>\d+)_(?P<bankid>\d+)_(?P<tmplt_idx>\d+)\.xml(?:\.gz)?$")
FINAL_ROUTE_BY_IFOS = {
    "H1": ("H1_SINGLE", "H1"),
    "H1V1": ("H1_SINGLE", "H1"),
    "L1": ("L1_SINGLE", "L1"),
    "L1V1": ("L1_SINGLE", "L1"),
    "H1L1": ("MULTI", ""),
    "H1L1V1": ("MULTI", ""),
    "V1": ("V1_ONLY", ""),
}


def _load_crashcar_plot_support():
    """Load the one crashcar-owned plot API at its invariant relative path."""
    script_path = Path(__file__).resolve()
    support_path = (
        script_path.parent.parent
        / "share"
        / "scripts"
        / "crashcar"
        / "crashcar_plot_support.py"
    )
    numeric_path = support_path.with_name("crashcar_numeric.py")
    if not support_path.is_file() or not numeric_path.is_file():
        raise ImportError(
            "crashcar plot support unavailable; expected exact files "
            f"{support_path} and {numeric_path}"
        )
    module_name = "_crashcar_plot_support_" + hashlib.sha256(
        str(support_path).encode("utf-8")
    ).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, support_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create import spec for crashcar plot support: {support_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ImportError(f"failed to load crashcar plot support {support_path}: {exc}") from exc
    required_symbols = (
        "FLAG_FOREGROUND",
        "feature_gps_seconds",
        "features_from_feature_csv_row",
        "rank_feature",
        "crashcar_numeric",
    )
    missing = [symbol for symbol in required_symbols if not hasattr(module, symbol)]
    if missing:
        raise ImportError(
            f"crashcar plot support {support_path} is missing required symbols: {missing}"
        )
    loaded_support = Path(module.__file__).resolve()
    loaded_numeric = Path(module._CRASHCAR_NUMERIC_PATH).resolve()
    if loaded_support != support_path or loaded_numeric != numeric_path:
        raise ImportError(
            "crashcar plot support provenance mismatch: "
            f"support={loaded_support}, numeric={loaded_numeric}"
        )
    return module, support_path, numeric_path


(
    _CRASHCAR_PLOT_SUPPORT,
    _CRASHCAR_PLOT_SUPPORT_PATH,
    _CRASHCAR_NUMERIC_PATH,
) = _load_crashcar_plot_support()
FLAG_FOREGROUND = _CRASHCAR_PLOT_SUPPORT.FLAG_FOREGROUND
feature_gps_seconds = _CRASHCAR_PLOT_SUPPORT.feature_gps_seconds
features_from_feature_csv_row = _CRASHCAR_PLOT_SUPPORT.features_from_feature_csv_row
rank_feature = _CRASHCAR_PLOT_SUPPORT.rank_feature
CRASHCAR_NUMERIC = _CRASHCAR_PLOT_SUPPORT.crashcar_numeric
TAIL_BOUNDARY_LOG10_FAR = math.log10(CRASHCAR_NUMERIC.TAIL_FAR)
if not math.isclose(TAIL_BOUNDARY_LOG10_FAR, -2.0, rel_tol=0.0, abs_tol=1.0e-12):
    raise ImportError(
        "crashcar numeric contract requires TAIL_FAR=1e-2 "
        f"but loaded {CRASHCAR_NUMERIC.TAIL_FAR!r} from {_CRASHCAR_NUMERIC_PATH}"
    )

def as_float(value, default=float("nan")) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except Exception:
        return default


def finite_positive(value) -> float | None:
    number = as_float(value)
    if number > 0.0 and math.isfinite(number):
        return number
    return None

def equal_within_one_ulp(actual: float, expected: float) -> bool:
    """Return whether two finite binary64 values differ by at most one ULP."""
    if not math.isfinite(actual) or not math.isfinite(expected):
        return False
    if actual == expected:
        return True
    lower = math.nextafter(expected, -math.inf)
    upper = math.nextafter(expected, math.inf)
    return lower <= actual <= upper


def canonical_nonnegative_decimal_int(value, label: str) -> int:
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None:
        raise ValueError(f"{label} must be a canonical nonnegative decimal integer")
    return int(value)


def log10_positive(value) -> float | None:
    number = finite_positive(value)
    if number is None:
        return None
    return math.log10(number)


def first_positive_field(row: dict, keys: Iterable[str]) -> tuple[float | None, str | None]:
    for key in keys:
        number = finite_positive(row.get(key))
        if number is not None:
            return number, key
    return None, None


def row_contains_ifo(row: dict, ifo: str) -> bool:
    return ifo in str(row.get("ifos", ""))


def parse_csv_list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_ifo_id_map(value: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in value.split(","):
        if not item.strip():
            continue
        left, sep, right = item.partition(":")
        if not sep:
            raise ValueError(f"invalid --ifo-id-map item: {item!r}")
        mapping[left.strip()] = right.strip()
    return mapping


def parse_postcoh_rows(
    path: Path,
    selected_columns: set[str] | None = None,
) -> Iterable[dict[str, str]]:
    # CoincsDoc appends Postcoh after normal process/Sngl/Coinc tables, so the
    # column registry must be scoped to the Postcoh table itself.
    yield from parse_ligolw_table_rows(
        path,
        "postcoh:table",
        selected_columns=selected_columns,
    )


def parse_ligolw_table_rows(
    path: Path,
    table_name: str,
    selected_columns: set[str] | None = None,
) -> Iterable[dict[str, str]]:
    columns: list[str] = []
    selected_indices: list[tuple[int, str]] | None = None
    in_table = False
    in_stream = False
    with open_text_maybe_gzip(path) as handle:
        for line in handle:
            if not in_table:
                if f'<Table Name="{table_name}"' in line:
                    in_table = True
                continue
            match = re.search(r'<Column Name="([^"]+)"', line)
            if match:
                columns.append(match.group(1))
                continue
            if f'<Stream Name="{table_name}"' in line:
                in_stream = True
                if selected_columns is not None:
                    selected_indices = [
                        (index, name)
                        for index, name in enumerate(columns)
                        if name in selected_columns
                    ]
                continue
            if in_stream:
                if "</Stream>" in line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    values = next(csv.reader(io.StringIO(stripped)))
                except Exception:
                    continue
                if len(values) == len(columns) + 1 and values[-1] == "":
                    values = values[:-1]
                if len(values) == len(columns):
                    if selected_indices is None:
                        yield dict(zip(columns, values))
                    else:
                        yield {
                            name: values[index]
                            for index, name in selected_indices
                        }
                continue
            if "</Table>" in line:
                break


def parse_segment_intervals(path: Path) -> list[tuple[str, float, float]]:
    segment_def_ifos: dict[str, str] = {}
    for row in parse_ligolw_table_rows(path, "segment_definer:table"):
        segment_def_ifos[str(row.get("segment_def_id", ""))] = str(row.get("ifos", "")).strip()

    intervals: list[tuple[str, float, float]] = []
    for row in parse_ligolw_table_rows(path, "segment:table"):
        ifo = segment_def_ifos.get(str(row.get("segment_definer:segment_def_id", "")), "")
        if ifo not in ("H1", "L1"):
            continue
        start = as_float(row.get("start_time")) + 1e-9 * as_float(row.get("start_time_ns"), 0.0)
        end = as_float(row.get("end_time")) + 1e-9 * as_float(row.get("end_time_ns"), 0.0)
        if math.isfinite(start) and math.isfinite(end) and end > start:
            intervals.append((ifo, start, end))
    return intervals


def union_duration(intervals: list[tuple[float, float]]) -> float:
    clean = sorted((float(start), float(end)) for start, end in intervals if end > start)
    if not clean:
        return 0.0
    total = 0.0
    cur_start, cur_end = clean[0]
    for start, end in clean[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
            continue
        total += cur_end - cur_start
        cur_start, cur_end = start, end
    total += cur_end - cur_start
    return total


def infer_background_window(zerolag: dict, accumulation_seconds: float) -> dict | None:
    starts: list[tuple[int, int]] = []
    for filename in zerolag.get("files", []):
        match = ZEROLAG_NAME_RE.search(Path(filename).name)
        if match:
            starts.append((int(match.group(1)), int(match.group(2))))
    if not starts or accumulation_seconds <= 0:
        return None
    min_start = min(start for start, _duration in starts)
    max_start = max(start for start, _duration in starts)
    max_duration = max(duration for start, duration in starts if start == max_start)
    if max_start - min_start >= accumulation_seconds:
        end = float(max_start)
        start = end - float(accumulation_seconds)
        mode = "latest_completed_accumulation"
    else:
        start = float(min_start)
        end = float(max_start + max_duration)
        mode = "partial_available_accumulation"
    return {
        "start": start,
        "end": end,
        "duration": max(0.0, end - start),
        "mode": mode,
        "accumulation_seconds": float(accumulation_seconds),
        "zerolag_start_min": int(min_start),
        "zerolag_start_max": int(max_start),
    }


def load_online_summary(run_root: Path, segment_glob: str, background_window: dict | None) -> dict:
    if not background_window or background_window.get("duration", 0.0) <= 0:
        return {
            "available": False,
            "reason": "background window unavailable",
            "segment_glob": segment_glob,
            "by_ifo": {},
        }
    start = float(background_window["start"])
    end = float(background_window["end"])
    duration = float(background_window["duration"])
    files = sorted(run_root.glob(segment_glob))
    intervals_by_ifo: dict[str, list[tuple[float, float]]] = {"H1": [], "L1": []}
    parsed_files = 0
    for path in files:
        intervals = parse_segment_intervals(path)
        if intervals:
            parsed_files += 1
        for ifo, seg_start, seg_end in intervals:
            clipped_start = max(start, seg_start)
            clipped_end = min(end, seg_end)
            if clipped_end > clipped_start:
                intervals_by_ifo[ifo].append((clipped_start, clipped_end))

    by_ifo = {}
    for ifo in ("H1", "L1"):
        online_seconds = union_duration(intervals_by_ifo[ifo])
        by_ifo[ifo] = {
            "online_seconds": online_seconds,
            "fraction": online_seconds / duration if duration > 0 else float("nan"),
            "raw_interval_count": len(intervals_by_ifo[ifo]),
        }
    return {
        "available": bool(files),
        "segment_glob": segment_glob,
        "segment_file_count": len(files),
        "parsed_segment_file_count": parsed_files,
        "background_window": background_window,
        "by_ifo": by_ifo,
    }


def selected_panel_a_windows(panel_a: dict) -> dict[str, dict]:
    windows: dict[str, dict] = {}
    for ifo in ("H1", "L1"):
        counts: Counter[tuple[int, int]] = Counter()
        for point in panel_a.get("points", []):
            if point.get("ifo") != ifo:
                continue
            bg_start = point.get("bg_start")
            bg_end = point.get("bg_end")
            if bg_start is None or bg_end is None:
                continue
            counts[(int(bg_start), int(bg_end))] += 1
        if not counts:
            continue
        (bg_start, bg_end), row_count = counts.most_common(1)[0]
        windows[ifo] = {
            "start": float(bg_start),
            "end": float(bg_end),
            "duration": float(max(0, bg_end - bg_start)),
            "row_count": int(row_count),
            "distinct_window_count": int(len(counts)),
            "multiple_windows": len(counts) > 1,
        }
    return windows


def load_panel_a_online_summary(run_root: Path, segment_glob: str, panel_a: dict) -> dict:
    windows = selected_panel_a_windows(panel_a)
    if not windows:
        return {
            "available": False,
            "reason": "panel-a BG windows unavailable",
            "segment_glob": segment_glob,
            "by_ifo": {},
        }

    files = sorted(run_root.glob(segment_glob))
    intervals_by_ifo: dict[str, list[tuple[float, float]]] = {"H1": [], "L1": []}
    parsed_files = 0
    for path in files:
        intervals = parse_segment_intervals(path)
        if intervals:
            parsed_files += 1
        for ifo, seg_start, seg_end in intervals:
            if ifo in intervals_by_ifo:
                intervals_by_ifo[ifo].append((seg_start, seg_end))

    by_ifo = {}
    for ifo, window in windows.items():
        start = float(window["start"])
        end = float(window["end"])
        duration = float(window["duration"])
        clipped_intervals = []
        for seg_start, seg_end in intervals_by_ifo.get(ifo, []):
            clipped_start = max(start, seg_start)
            clipped_end = min(end, seg_end)
            if clipped_end > clipped_start:
                clipped_intervals.append((clipped_start, clipped_end))
        online_seconds = union_duration(clipped_intervals)
        by_ifo[ifo] = {
            "online_seconds": online_seconds,
            "fraction": online_seconds / duration if duration > 0 else float("nan"),
            "raw_interval_count": len(clipped_intervals),
            "background_window": window,
        }

    return {
        "available": bool(files),
        "segment_glob": segment_glob,
        "segment_file_count": len(files),
        "parsed_segment_file_count": parsed_files,
        "by_ifo": by_ifo,
    }


def zerolag_required_columns(
    single_far_bases: tuple[str, ...],
    coherent_far_bases: tuple[str, ...],
) -> set[str]:
    columns = {
        "ifos",
        "event_id",
        "bankid",
        "tmplt_idx",
        "end_time",
        "end_time_ns",
        "end_time_sngl_H1",
        "end_time_ns_sngl_H1",
        "end_time_sngl_L1",
        "end_time_ns_sngl_L1",
        "snglsnr_H1",
        "snglsnr_L1",
        "chisq_H1",
        "chisq_L1",
        "cohsnr",
        "cmbchisq",
        "H1_LLR",
        "L1_LLR",
    }
    # Unique FinalSink ownership is fixed even when a caller overrides the
    # plotting FAR priorities.
    columns.update(("far", "far_sngl_H1", "far_sngl_L1"))
    for base in single_far_bases:
        columns.update((f"{base}_H1", f"{base}_L1"))
    columns.update(coherent_far_bases)
    return columns


def load_zerolag(
    run_root: Path,
    zerolag_glob: str,
    *,
    selected_columns: set[str] | None = None,
) -> dict:
    files = sorted(run_root.glob(zerolag_glob))
    rows: list[dict] = []
    rows_by_ifos: Counter[str] = Counter()
    columns_seen: set[str] = set()
    for path in files:
        worker = path.parent.name
        source = str(path.relative_to(run_root))
        for row in parse_postcoh_rows(path, selected_columns=selected_columns):
            columns_seen.update(row)
            row["_worker"] = worker
            row["_source"] = source
            rows_by_ifos[row.get("ifos", "")] += 1
            rows.append(row)
    return {
        "glob": zerolag_glob,
        "files": [str(path) for path in files],
        "file_count": len(files),
        "rows": rows,
        "rows_by_ifos": dict(rows_by_ifos),
        "columns_seen": sorted(columns_seen),
    }


def normalize_worker_id(value: str) -> str:
    text = str(value).strip()
    return text.zfill(3) if text.isdigit() else text


def load_panel_a_detail(
    run_root: Path,
    detail_glob: str,
    panel_a_worker: str,
    ifo_id_map: dict[str, str],
    max_points: int,
    bg_policy: str,
) -> dict:
    worker_id = normalize_worker_id(panel_a_worker)
    worker_pattern = re.compile(rf"worker{re.escape(worker_id)}(?:\.csv)?$")
    candidates = sorted(path for path in run_root.glob(detail_glob) if worker_pattern.search(path.name))
    if not candidates:
        return {
            "exists": False,
            "source_kind": "detail_calculated_far_diagnostic",
            "files": [],
            "points": [],
            "counts": {},
            "reason": f"no file matching worker{worker_id}",
        }

    points: list[dict] = []
    counts_all: Counter[str] = Counter()
    counts_ready: Counter[str] = Counter()
    counts_by_window: Counter[tuple[str, int, int, int, int]] = Counter()
    rows_seen = 0
    for path in candidates:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows_seen += 1
                ifo_id = str(row.get("ifo_id", ""))
                ifo = row.get("ifo") or ifo_id_map.get(ifo_id, f"IFO{ifo_id}")
                counts_all[ifo] += 1
                calculated_valid = (
                    str(row.get("far_calculated_valid", "")).strip() == "1")
                direct_far = finite_positive(row.get("far_calculated_exact")) if calculated_valid else None
                window_count = finite_positive(row.get("window_count"))
                total_window_count = finite_positive(row.get("total_window_count"))
                bg_start = finite_positive(row.get("bg_start"))
                bg_end = finite_positive(row.get("bg_end"))
                llr = as_float(row.get("llr"))
                if (
                    ifo not in ("H1", "L1")
                    or direct_far is None
                    or window_count is None
                    or total_window_count is None
                    or bg_start is None
                    or bg_end is None
                    or not math.isfinite(llr)
                ):
                    continue
                counts_ready[ifo] += 1
                window_key = (ifo, int(bg_start), int(bg_end), int(window_count), int(total_window_count))
                counts_by_window[window_key] += 1
                points.append(
                    {
                        "ifo": ifo,
                        "llr": llr,
                        "log_far": math.log10(direct_far),
                        "direct_far": direct_far,
                        "window_count": int(window_count),
                        "total_window_count": int(total_window_count),
                        "bg_start": int(bg_start),
                        "bg_end": int(bg_end),
                        "event_id": row.get("event_id", ""),
                        "snglsnr": as_float(row.get("snglsnr")),
                        "chisq": as_float(row.get("chisq")),
                    }
                )

    downsampled = False
    original_points = len(points)
    latest_total_by_ifo: dict[str, int] = {}
    latest_bg_end_by_ifo: dict[str, int] = {}
    if bg_policy == "latest":
        for point in points:
            latest_total_by_ifo[point["ifo"]] = max(latest_total_by_ifo.get(point["ifo"], -1), point["total_window_count"])
            latest_bg_end_by_ifo[point["ifo"]] = max(latest_bg_end_by_ifo.get(point["ifo"], -1), point["bg_end"])
        points = [point for point in points if point["bg_end"] == latest_bg_end_by_ifo.get(point["ifo"])]

    if max_points > 0 and len(points) > max_points:
        step = max(1, math.ceil(len(points) / max_points))
        points = points[::step]
        downsampled = True

    return {
        "exists": True,
        "source_kind": "detail_calculated_far_diagnostic",
        "worker": worker_id,
        "files": [str(path) for path in candidates],
        "rows_seen": rows_seen,
        "points": points,
        "counts_all": dict(counts_all),
        "counts_ready": dict(counts_ready),
        "counts_ready_selected": dict(Counter(point["ifo"] for point in points)),
        "bg_policy": bg_policy,
        "latest_total_window_count_by_ifo": latest_total_by_ifo,
        "latest_bg_end_by_ifo": latest_bg_end_by_ifo,
        "ready_windows": [
            {
                "ifo": ifo,
                "bg_start": bg_start,
                "bg_end": bg_end,
                "window_count": window,
                "total_window_count": total,
                "rows": rows,
            }
            for (ifo, bg_start, bg_end, window, total), rows in sorted(
                counts_by_window.items(), key=lambda item: (item[0][0], item[0][2], item[0][4], item[0][3])
            )
        ],
        "points_original": original_points,
        "points_plotted": len(points),
        "downsampled": downsampled,
    }


def _schema4_exact_int(value, label: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an exact integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} is below its minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds its maximum")
    return value


def _schema4_ns(value, label: str, *, duration: bool = False) -> int:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a seconds/nanoseconds object")
    require_exact_keys(value, ("seconds", "nanoseconds"), label)
    seconds = _schema4_exact_int(value["seconds"], f"{label}.seconds")
    nanoseconds = _schema4_exact_int(
        value["nanoseconds"], f"{label}.nanoseconds",
        minimum=0, maximum=999_999_999,
    )
    total = seconds * 1_000_000_000 + nanoseconds
    if total < -(1 << 63) or total > (1 << 63) - 1:
        raise ValueError(f"{label} exceeds signed int64 nanoseconds")
    if duration and total < 0:
        raise ValueError(f"{label} must be nonnegative")
    return total


def _schema4_binary64(value, label: str) -> float:
    if type(value) is not str or re.fullmatch(
        r"-?0x[01]\.[0-9a-f]{13}p[+-](?:0|[1-9][0-9]*)", value
    ) is None:
        raise ValueError(f"{label} must be a canonical binary64 hex string")
    try:
        number = float.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not a binary64 value") from exc
    if not math.isfinite(number) or (number == 0.0 and value.startswith("-")):
        raise ValueError(f"{label} must be finite and cannot be negative zero")
    if number.hex() != value:
        raise ValueError(f"{label} is not the unique canonical binary64 spelling")
    return number


def read_strict_schema4_background(
    path: Path,
    *,
    expected_sha256: str | None = None,
) -> tuple[dict, str]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ValueError(f"authoritative background JSON is missing: {path}") from exc
    except OSError as exc:
        raise ValueError(
            f"cannot open authoritative background JSON with O_NOFOLLOW: {path}: {exc}"
        ) from exc

    close_error = None
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o444
            or before.st_size <= 0
            or before.st_size > 256 * 1024 * 1024
        ):
            raise ValueError(
                "authoritative schema4 background must be a bounded "
                "mode-0444 regular file"
            )
        remaining = before.st_size
        chunks = []
        while remaining:
            block = os.read(fd, min(1024 * 1024, remaining))
            if not block:
                raise ValueError("authoritative schema4 background read was incomplete")
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(fd)
    finally:
        try:
            os.close(fd)
        except OSError as exc:
            close_error = exc
    if close_error is not None:
        raise ValueError(
            f"authoritative schema4 background close failed: {close_error}"
        ) from close_error
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mode != after.st_mode
    ):
        raise ValueError("authoritative schema4 background changed during single-fd read")

    raw = b"".join(chunks)
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None:
        if (
            re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or digest != expected_sha256
        ):
            raise ValueError("authoritative schema4 background SHA256 mismatch")
    if (
        not raw.endswith(b"\n")
        or b"\n" in raw[:-1]
        or b"\r" in raw
        or b"\x00" in raw
    ):
        raise ValueError(
            "authoritative schema4 background must be one UTF-8 JSON line "
            "with exactly one terminal LF"
        )
    try:
        text = raw[:-1].decode("utf-8")
        doc = json.loads(text, object_pairs_hook=strict_json_object)
    except Exception as exc:
        raise ValueError(
            f"malformed authoritative schema4 background JSON: {path}: {exc}"
        ) from exc
    if not isinstance(doc, dict):
        raise ValueError("authoritative schema4 background root is not an object")
    return doc, digest


def validate_schema4_background_document(
    doc: dict,
    *,
    expected_worker: str | int,
    expected_worker_count: int,
    expected_worker_bank_ids: Iterable[int],
    expected_window_seconds: float,
) -> dict:
    provenance_keys = (
        "run_namespace_sha256",
        "source_manifest_sha256",
        "runtime_manifest_sha256",
        "config_sha256",
        "segment_xml_sha256",
        "segment_canonical_sha256",
        "template_shape_map_sha256",
    )
    require_exact_keys(
        doc,
        (
            "schema_version", "background_kind", *provenance_keys,
            "worker_id", "worker_count", "worker_bank_ids",
            "accepted_version", "epoch_gps", "window_start_gps",
            "window_end_gps", "window_duration", "update_period",
            "far_floor_count", "tail_log10_far", "backgrounds",
        ),
        "schema4 background",
    )
    schema_version = _schema4_exact_int(doc["schema_version"], "schema_version")
    if schema_version != 4 or doc["background_kind"] != "no_injection":
        raise ValueError("authoritative background schema/kind mismatch")
    for key in provenance_keys:
        if type(doc[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", doc[key]) is None:
            raise ValueError(f"authoritative background provenance is invalid: {key}")

    worker_id = _schema4_exact_int(doc["worker_id"], "worker_id", minimum=0)
    worker_count = _schema4_exact_int(doc["worker_count"], "worker_count", minimum=1)
    expected_worker_text = normalize_worker_id(str(expected_worker))
    if not expected_worker_text.isdigit() or worker_id != int(expected_worker_text):
        raise ValueError("authoritative background worker mismatch")
    if worker_id >= worker_count:
        raise ValueError("authoritative background worker is outside worker_count")
    bank_ids = doc["worker_bank_ids"]
    if (
        not isinstance(bank_ids, list)
        or not bank_ids
        or any(type(bank) is not int or bank < 0 for bank in bank_ids)
        or bank_ids != sorted(set(bank_ids))
    ):
        raise ValueError("authoritative background worker_bank_ids are noncanonical")
    expected_bank_ids = list(expected_worker_bank_ids)
    if (
        type(expected_worker_count) is not int
        or expected_worker_count < 1
        or worker_count != expected_worker_count
        or bank_ids != expected_bank_ids
    ):
        raise ValueError("authoritative background worker geometry/roster mismatch")
    accepted_version = _schema4_exact_int(
        doc["accepted_version"], "accepted_version", minimum=1, maximum=(1 << 63) - 1
    )

    epoch_ns = _schema4_ns(doc["epoch_gps"], "epoch_gps")
    window_start_ns = _schema4_ns(doc["window_start_gps"], "window_start_gps")
    window_end_ns = _schema4_ns(doc["window_end_gps"], "window_end_gps")
    window_duration_ns = _schema4_ns(
        doc["window_duration"], "window_duration", duration=True
    )
    update_period_ns = _schema4_ns(
        doc["update_period"], "update_period", duration=True
    )
    if (
        window_duration_ns <= 0
        or update_period_ns <= 0
        or window_start_ns >= window_end_ns
        or window_end_ns - window_start_ns != window_duration_ns
        or epoch_ns != window_end_ns
    ):
        raise ValueError("authoritative background window/epoch/update is invalid")
    expected_window = float(expected_window_seconds)
    if (
        not math.isfinite(expected_window)
        or expected_window <= 0.0
        or expected_window != window_duration_ns / 1_000_000_000.0
    ):
        raise ValueError("authoritative background window differs from plot configuration")
    far_floor_count = _schema4_exact_int(
        doc["far_floor_count"], "far_floor_count"
    )
    raw_tail_log10_far = doc["tail_log10_far"]
    if (
        type(raw_tail_log10_far) not in (int, float)
        or not math.isfinite(float(raw_tail_log10_far))
        or float(raw_tail_log10_far) >= 0.0
    ):
        raise ValueError("authoritative background tail boundary must be finite and negative")
    tail_log10_far = float(raw_tail_log10_far)
    if far_floor_count != 1:
        raise ValueError("authoritative background FAR floor mismatch")

    backgrounds = doc["backgrounds"]
    if not isinstance(backgrounds, dict):
        raise ValueError("authoritative backgrounds must be an H1/L1 object")
    require_exact_keys(backgrounds, ("H1", "L1"), "schema4 backgrounds")

    parsed_backgrounds: dict[str, dict] = {}
    total_support = 0
    for ifo in ("H1", "L1"):
        payload = backgrounds[ifo]
        if not isinstance(payload, dict):
            raise ValueError(f"{ifo} authoritative background is not an object")
        require_exact_keys(
            payload,
            ("livetime", "support_count", "tail_fit", "far_llr_points"),
            f"{ifo} authoritative background",
        )
        livetime_ns = _schema4_ns(
            payload["livetime"], f"{ifo}.livetime", duration=True
        )
        support_count = _schema4_exact_int(
            payload["support_count"], f"{ifo}.support_count",
            minimum=1, maximum=1_000_000,
        )
        points_raw = payload["far_llr_points"]
        if not isinstance(points_raw, list) or len(points_raw) != support_count:
            raise ValueError(f"{ifo} support_count/far_llr_points length mismatch")
        if (
            livetime_ns <= 0
            or livetime_ns > window_duration_ns
            or livetime_ns >= (1 << 53)
            or livetime_ns * 5 <= window_duration_ns
        ):
            raise ValueError(f"{ifo} detector occupancy/livetime is invalid")

        tail_fit = payload["tail_fit"]
        if not isinstance(tail_fit, dict):
            raise ValueError(f"{ifo}.tail_fit is not an object")
        require_exact_keys(
            tail_fit,
            ("method", "r_tail", "slope", "fit_unique_rank_count"),
            f"{ifo}.tail_fit",
        )
        if tail_fit["method"] != "anchored_ols_all_unique_ranks_ge_r_tail":
            raise ValueError(f"{ifo} tail method mismatch")
        stored_r_tail = _schema4_binary64(tail_fit["r_tail"], f"{ifo}.r_tail")
        stored_slope = _schema4_binary64(tail_fit["slope"], f"{ifo}.slope")
        fit_count = _schema4_exact_int(
            tail_fit["fit_unique_rank_count"], f"{ifo}.fit_unique_rank_count",
            minimum=2, maximum=support_count,
        )
        if stored_slope >= 0.0:
            raise ValueError(f"{ifo} tail slope must be negative")

        parsed_points = []
        for index, point in enumerate(points_raw):
            if not isinstance(point, dict):
                raise ValueError(f"{ifo} point {index} is not an object")
            require_exact_keys(point, ("gps", "llr", "far"), f"{ifo} point {index}")
            gps_ns = _schema4_ns(point["gps"], f"{ifo} point {index}.gps")
            llr = _schema4_binary64(point["llr"], f"{ifo} point {index}.llr")
            far = _schema4_binary64(point["far"], f"{ifo} point {index}.far")
            if not window_start_ns <= gps_ns < window_end_ns:
                raise ValueError(f"{ifo} point {index} GPS is outside the authority window")
            if far <= 0.0:
                raise ValueError(f"{ifo} point {index} FAR must be positive")
            parsed_points.append({
                "gps_ns": gps_ns,
                "gps": gps_ns / 1_000_000_000.0,
                "llr": llr,
                "far": far,
                "llr_hex": point["llr"],
                "far_hex": point["far"],
            })

        if parsed_points != sorted(
            parsed_points, key=lambda point: (point["llr"], point["gps_ns"])
        ):
            raise ValueError(f"{ifo} far_llr_points are not canonically sorted")

        livetime_seconds = livetime_ns / 1_000_000_000.0
        ranks = [point["llr"] for point in parsed_points]
        for begin in range(len(parsed_points)):
            if begin > 0 and ranks[begin] == ranks[begin - 1]:
                continue
            end = begin + 1
            while end < len(ranks) and ranks[end] == ranks[begin]:
                end += 1
            count_ge = len(ranks) - begin
            expected_far = count_ge / livetime_seconds
            if not math.isfinite(expected_far) or expected_far <= 0.0:
                raise ValueError(f"{ifo} Calculated FAR invariant is invalid")
            for index in range(begin, end):
                if not equal_within_one_ulp(
                    parsed_points[index]["far"], expected_far
                ):
                    raise ValueError(
                        f"{ifo} stored Calculated FAR exceeds one-ULP tolerance"
                    )
                parsed_points[index]["count_ge"] = count_ge

        total_support += support_count
        parsed_backgrounds[ifo] = {
            "livetime_ns": livetime_ns,
            "livetime_seconds": livetime_seconds,
            "support_count": support_count,
            "tail_fit": {
                "method": tail_fit["method"],
                "r_tail": stored_r_tail,
                "slope": stored_slope,
                "fit_unique_rank_count": fit_count,
            },
            "points": parsed_points,
        }

    if total_support > 2_000_000:
        raise ValueError("authoritative total support bound exceeded")
    return {
        "worker_id": worker_id,
        "worker_count": worker_count,
        "worker_bank_ids": list(bank_ids),
        "accepted_version": accepted_version,
        "epoch_gps": dict(doc["epoch_gps"]),
        "epoch_gps_ns": epoch_ns,
        "window_start_gps": dict(doc["window_start_gps"]),
        "window_start_gps_ns": window_start_ns,
        "window_end_gps": dict(doc["window_end_gps"]),
        "window_end_gps_ns": window_end_ns,
        "window_duration": dict(doc["window_duration"]),
        "window_duration_ns": window_duration_ns,
        "update_period": dict(doc["update_period"]),
        "update_period_ns": update_period_ns,
        "far_floor_count": 1,
        "tail_log10_far": tail_log10_far,
        "provenance": {key: doc[key] for key in provenance_keys},
        "backgrounds": parsed_backgrounds,
    }


def load_panel_a_background_json(
    path: Path,
    background_accumulation_seconds: float,
    max_points: int,
    panel_a_worker: str,
    *,
    start_bank: int,
    banks_per_worker: int,
    worker_count: int,
    expected_sha256: str | None = None,
) -> dict:
    worker_text = normalize_worker_id(panel_a_worker)
    if (
        not worker_text.isdigit()
        or type(start_bank) is not int
        or type(banks_per_worker) is not int
        or type(worker_count) is not int
        or start_bank < 0
        or banks_per_worker < 1
        or worker_count < 1
        or int(worker_text) >= worker_count
    ):
        raise ValueError("Panel-A worker geometry is invalid")
    worker_index = int(worker_text)
    expected_roster = list(range(
        start_bank + worker_index * banks_per_worker,
        start_bank + (worker_index + 1) * banks_per_worker,
    ))
    doc, source_sha256 = read_strict_schema4_background(
        path, expected_sha256=expected_sha256
    )
    authority = validate_schema4_background_document(
        doc,
        expected_worker=worker_index,
        expected_worker_count=worker_count,
        expected_worker_bank_ids=expected_roster,
        expected_window_seconds=background_accumulation_seconds,
    )
    total_window_count = sum(
        payload["support_count"] for payload in authority["backgrounds"].values()
    )
    points: list[dict] = []
    counts_ready: dict[str, int] = {}
    min_far_by_ifo: dict[str, float] = {}
    floor_far_by_ifo: dict[str, float] = {}
    latest_bg_end_by_ifo: dict[str, int] = {}
    online_by_ifo: dict[str, dict] = {}
    ready_windows = []
    bg_start = authority["window_start_gps_ns"] / 1_000_000_000.0
    bg_end = authority["window_end_gps_ns"] / 1_000_000_000.0
    window_seconds = authority["window_duration_ns"] / 1_000_000_000.0

    for ifo in ("H1", "L1"):
        payload = authority["backgrounds"][ifo]
        livetime = payload["livetime_seconds"]
        counts_ready[ifo] = payload["support_count"]
        floor_far_by_ifo[ifo] = 1.0 / livetime
        latest_bg_end_by_ifo[ifo] = math.floor(bg_end)
        online_by_ifo[ifo] = {
            "online_seconds": livetime,
            "fraction": livetime / window_seconds,
            "raw_interval_count": 0,
            "background_window": {
                "start": bg_start,
                "end": bg_end,
                "duration": window_seconds,
                "row_count": payload["support_count"],
                "source": "schema4_background_livetime",
            },
        }
        ready_windows.append({
            "ifo": ifo,
            "bg_start": math.floor(bg_start),
            "bg_end": math.floor(bg_end),
            "window_count": payload["support_count"],
            "total_window_count": total_window_count,
            "rows": payload["support_count"],
            "source": "schema4_background",
        })
        for point in payload["points"]:
            direct_far = point["far"]
            min_far_by_ifo[ifo] = min(
                min_far_by_ifo.get(ifo, direct_far), direct_far
            )
            points.append({
                "ifo": ifo,
                "llr": point["llr"],
                "log_far": math.log10(direct_far),
                "direct_far": direct_far,
                "direct_far_count_ge": point["count_ge"],
                "bg_livetime": livetime,
                "window_count": payload["support_count"],
                "total_window_count": total_window_count,
                "bg_start": math.floor(bg_start),
                "bg_end": math.floor(bg_end),
                "event_id": "",
                "snglsnr": float("nan"),
                "chisq": float("nan"),
                "bankid": "",
                "tmplt_idx": "",
                "worker": normalize_worker_id(panel_a_worker),
                "gps": point["gps"],
                "stored_llr_hex": point["llr_hex"],
                "stored_far_hex": point["far_hex"],
            })

    original_points = len(points)
    downsampled = False
    if max_points > 0 and len(points) > max_points:
        step = max(1, math.ceil(len(points) / max_points))
        points = points[::step]
        downsampled = True

    return {
        "exists": True,
        "authoritative": True,
        "metadata_role": "authoritative_schema4_background",
        "source_kind": "background_json",
        "worker": normalize_worker_id(panel_a_worker),
        "files": [str(path)],
        "source": str(path),
        "source_sha256": source_sha256,
        "points": points,
        "counts_all": counts_ready,
        "counts_ready": counts_ready,
        "counts_ready_selected": dict(Counter(point["ifo"] for point in points)),
        "bg_policy": "schema4_background",
        "latest_total_window_count_by_ifo": {
            ifo: total_window_count for ifo in counts_ready
        },
        "latest_bg_end_by_ifo": latest_bg_end_by_ifo,
        "ready_windows": ready_windows,
        "points_original": original_points,
        "points_plotted": len(points),
        "downsampled": downsampled,
        "min_direct_far_by_ifo": min_far_by_ifo,
        "floor_far_by_ifo": floor_far_by_ifo,
        "tail_fit_by_ifo": {
            ifo: dict(authority["backgrounds"][ifo]["tail_fit"])
            for ifo in ("H1", "L1")
        },
        "schema4_authority": {
            key: authority[key]
            for key in (
                "worker_id", "worker_count", "worker_bank_ids",
                "accepted_version", "epoch_gps", "epoch_gps_ns",
                "window_start_gps", "window_start_gps_ns",
                "window_end_gps", "window_end_gps_ns",
                "window_duration", "window_duration_ns",
                "update_period", "update_period_ns",
                "far_floor_count", "tail_log10_far", "provenance",
            )
        },
        "online_summary": {
            "available": True,
            "segment_glob": "",
            "segment_file_count": 0,
            "parsed_segment_file_count": 0,
            "by_ifo": online_by_ifo,
            "source": "schema4_background_livetime",
        },
    }


def far_bin(log_far: float) -> int:
    if log_far < -5.0:
        return 0
    if log_far >= 0.0:
        return len(FAR_BIN_LABELS) - 1
    return int(math.floor(log_far + 5.0)) + 1


def new_compact_far_store() -> dict:
    return {
        "storage": "compact_arrays_v1",
        "snr": array("d"),
        "chisq": array("d"),
        "far_bin": array("B"),
        "points_raw_total": 0,
        "points_total": 0,
        "points_in_fixed_view": 0,
        "by_worker": Counter(),
        "by_bankid": Counter(),
        "by_ifo": Counter(),
        "view_by_worker": Counter(),
        "view_by_bankid": Counter(),
        "view_by_ifo": Counter(),
    }


def add_compact_far_point(
    store: dict,
    *,
    snr: float,
    chisq: float,
    far: float,
    worker: str,
    bankid: str,
    ifo: str,
) -> None:
    store["points_raw_total"] += 1
    log_far = math.log10(far)
    if snr < SNR_XMIN or not math.isfinite(log_far):
        return
    store["points_total"] += 1
    store["snr"].append(float(snr))
    store["chisq"].append(float(chisq))
    store["far_bin"].append(far_bin(log_far))
    worker_key = str(worker)
    bank_key = str(bankid)
    ifo_key = str(ifo)
    store["by_worker"][worker_key] += 1
    store["by_bankid"][bank_key] += 1
    store["by_ifo"][ifo_key] += 1
    if CHISQ_VIEW[0] <= chisq <= CHISQ_VIEW[1]:
        store["points_in_fixed_view"] += 1
        store["view_by_worker"][worker_key] += 1
        store["view_by_bankid"][bank_key] += 1
        store["view_by_ifo"][ifo_key] += 1


def compact_far_arrays(stores: Iterable[dict], view_mode: str):
    x_parts = []
    y_parts = []
    color_parts = []
    for store in stores:
        snr = np.frombuffer(store["snr"], dtype=np.float64)
        chisq = np.frombuffer(store["chisq"], dtype=np.float64)
        colors = np.frombuffer(store["far_bin"], dtype=np.uint8)
        if view_mode == "all":
            mask = slice(None)
        else:
            mask = (chisq >= CHISQ_VIEW[0]) & (chisq <= CHISQ_VIEW[1])
        x_parts.append(snr[mask])
        y_parts.append(chisq[mask])
        color_parts.append(colors[mask])
    nonempty = [index for index, part in enumerate(x_parts) if part.size]
    if not nonempty:
        return (
            np.asarray([], dtype=float),
            np.asarray([], dtype=float),
            np.asarray([], dtype=np.uint8),
        )
    if len(nonempty) == 1:
        index = nonempty[0]
        return x_parts[index], y_parts[index], color_parts[index]
    return (
        np.concatenate([x_parts[index] for index in nonempty]),
        np.concatenate([y_parts[index] for index in nonempty]),
        np.concatenate([color_parts[index] for index in nonempty]),
    )


def compact_point_summary(stores: Iterable[dict], view_mode: str) -> dict:
    stores = tuple(stores)
    by_worker: Counter[str] = Counter()
    by_bankid: Counter[str] = Counter()
    by_ifo: Counter[str] = Counter()
    view_by_worker: Counter[str] = Counter()
    view_by_bankid: Counter[str] = Counter()
    view_by_ifo: Counter[str] = Counter()
    for store in stores:
        by_worker.update(store["by_worker"])
        by_bankid.update(store["by_bankid"])
        by_ifo.update(store["by_ifo"])
        if view_mode == "all":
            view_by_worker.update(store["by_worker"])
            view_by_bankid.update(store["by_bankid"])
            view_by_ifo.update(store["by_ifo"])
        else:
            view_by_worker.update(store["view_by_worker"])
            view_by_bankid.update(store["view_by_bankid"])
            view_by_ifo.update(store["view_by_ifo"])
    return {
        "points_total": sum(store["points_total"] for store in stores),
        "points_total_snr_min": SNR_XMIN,
        "points_raw_total": sum(store["points_raw_total"] for store in stores),
        "points_in_view": sum(
            store["points_total"] if view_mode == "all"
            else store["points_in_fixed_view"]
            for store in stores
        ),
        "by_worker": dict(by_worker),
        "by_bankid": dict(by_bankid),
        "view_by_worker": dict(view_by_worker),
        "view_by_bankid": dict(view_by_bankid),
        "by_ifo": dict(by_ifo),
        "view_by_ifo": dict(view_by_ifo),
    }


def log_edges(values: Iterable[float], n: int, fallback=(SNR_XMIN, 20.0), min_value: float = SNR_XMIN) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if arr.size == 0:
        lo, hi = fallback
    else:
        lo, hi = np.quantile(np.log10(arr), [0.01, 0.99])
        if lo == hi:
            lo -= 0.1
            hi += 0.1
        pad = 0.08 * max(hi - lo, 0.1)
        lo, hi = 10 ** (lo - pad), 10 ** (hi + pad)
    lo = max(float(lo), float(min_value))
    hi = max(float(hi), lo * 1.05)
    return np.logspace(math.log10(lo), math.log10(hi), n + 1)


def min_bin_grid(xs, ys, values, xedges, yedges):
    grid = np.full((len(xedges) - 1, len(yedges) - 1), np.nan)
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    values = np.asarray(values, dtype=float)
    if xs.size == 0:
        return grid
    xi = np.searchsorted(xedges, xs, side="right") - 1
    yi = np.searchsorted(yedges, ys, side="right") - 1
    mask = (xi >= 0) & (xi < grid.shape[0]) & (yi >= 0) & (yi < grid.shape[1]) & np.isfinite(values)
    if not np.any(mask):
        return grid
    flat = grid.ravel()
    idx = xi[mask] * grid.shape[1] + yi[mask]
    flat[np.unique(idx)] = np.inf
    np.minimum.at(flat, idx, values[mask])
    grid = flat.reshape(grid.shape)
    grid[~np.isfinite(grid)] = np.nan
    return grid


def build_single_points(zerolag_rows: list[dict], single_far_bases: tuple[str, ...]) -> list[dict]:
    points: list[dict] = []
    for row in zerolag_rows:
        for ifo in ("H1", "L1"):
            snr = finite_positive(row.get(f"snglsnr_{ifo}"))
            chisq = finite_positive(row.get(f"chisq_{ifo}"))
            far, far_source = first_positive_field(row, [f"{base}_{ifo}" for base in single_far_bases])
            if not row_contains_ifo(row, ifo) or snr is None or chisq is None or far is None:
                continue
            points.append(
                {
                    "kind": "single",
                    "ifo": ifo,
                    "snr": snr,
                    "chisq": chisq,
                    "far": far,
                    "log_far": math.log10(far),
                    "far_source": far_source,
                    "event_id": row.get("event_id"),
                    "bankid": row.get("bankid", ""),
                    "worker": row.get("_worker", ""),
                    "source": row.get("_source"),
                    "ifos": row.get("ifos"),
                }
            )
    return points


def build_multi_points(
    zerolag_rows: list[dict],
    coherent_far_bases: tuple[str, ...],
) -> list[dict]:
    points: list[dict] = []
    for row in zerolag_rows:
        ifos = str(row.get("ifos", ""))
        detectors = {token for token in ("H1", "L1", "V1", "K1") if token in ifos}
        if len(detectors) < 2:
            continue
        snr = finite_positive(row.get("cohsnr"))
        chisq = finite_positive(row.get("cmbchisq"))
        far, far_source = first_positive_field(row, coherent_far_bases)
        if snr is None or chisq is None or far is None:
            continue
        points.append(
            {
                "kind": "multi",
                "ifo": "+".join(sorted(detectors)),
                "snr": snr,
                "chisq": chisq,
                "far": far,
                "log_far": math.log10(far),
                "far_source": far_source,
                "event_id": row.get("event_id"),
                "bankid": row.get("bankid", ""),
                "worker": row.get("_worker", ""),
                "source": row.get("_source"),
                "ifos": row.get("ifos"),
            }
        )
    return points


def add_discrete_colorbar(fig, axes, mesh) -> None:
    cbar = fig.colorbar(mesh, ax=axes.ravel().tolist(), fraction=0.024, pad=0.018, ticks=np.arange(len(FAR_BIN_LABELS)))
    cbar.ax.set_yticklabels(FAR_BIN_LABELS)
    cbar.set_label("log10(FAR) bin")


def eligible_far_points(points: list[dict]) -> list[dict]:
    return [
        point
        for point in points
        if point["snr"] >= SNR_XMIN
        and math.isfinite(point["log_far"])
    ]


def final_view_points(points: list[dict]) -> list[dict]:
    return [
        point
        for point in eligible_far_points(points)
        if CHISQ_VIEW[0] <= point["chisq"] <= CHISQ_VIEW[1]
    ]


def point_summary(points: list[dict], view: list[dict]) -> dict:
    eligible = eligible_far_points(points)
    return {
        "points_total": len(eligible),
        "points_total_snr_min": SNR_XMIN,
        "points_raw_total": len(points),
        "points_in_view": len(view),
        "by_worker": dict(Counter(str(point.get("worker", "")) for point in eligible)),
        "by_bankid": dict(Counter(str(point.get("bankid", "")) for point in eligible)),
        "view_by_worker": dict(Counter(str(point.get("worker", "")) for point in view)),
        "view_by_bankid": dict(Counter(str(point.get("bankid", "")) for point in view)),
        "by_ifo": dict(Counter(str(point.get("ifo", "")) for point in eligible)),
        "view_by_ifo": dict(Counter(str(point.get("ifo", "")) for point in view)),
    }


def thin_curve(xs: np.ndarray, ys: np.ndarray, max_points: int = FIT_CURVE_MAX_POINTS) -> tuple[np.ndarray, np.ndarray]:
    if xs.size <= max_points:
        return xs, ys
    indices = np.unique(np.linspace(0, xs.size - 1, max_points).astype(int))
    return xs[indices], ys[indices]


def panel_a_segmented_fit(
    points: list[dict],
    tail_boundary: float = TAIL_BOUNDARY_LOG10_FAR,
    stored_tail_fit: dict | None = None,
) -> dict | None:
    """Render the same direct/tail law used by the crashcar FAR assignment."""
    tail_boundary = float(tail_boundary)
    if not math.isfinite(tail_boundary) or tail_boundary >= 0.0:
        raise ValueError("tail boundary must be finite and negative")
    ranks = [
        as_float(point.get("llr"))
        for point in points
        if math.isfinite(as_float(point.get("llr")))
    ]
    if len(ranks) < 2:
        return None

    livetimes = [
        as_float(point.get("bg_livetime"))
        for point in points
        if finite_positive(point.get("bg_livetime")) is not None
    ]
    result = {
        "support_point_count": 0,
        "support_plot_point_count": 0,
        "tail_point_count": 0,
        "tail_source": (
            "authoritative_schema4_background.tail_fit"
            if stored_tail_fit is not None
            else "shared_crashcar_numeric.tail_model"
        ),
        "tail_boundary_log10_far": tail_boundary,
        "r_tail": None,
        "assignment_boundary": "r<=r_tail direct calculated FAR; r>r_tail fitted FAR",
        "tail_status": "unavailable_missing_livetime",
        "livetime": None,
        "support_x": np.asarray([], dtype=float),
        "support_y": np.asarray([], dtype=float),
        "tail_line_x": np.asarray([], dtype=float),
        "tail_line_y": np.asarray([], dtype=float),
        "tail_x": np.asarray([], dtype=float),
        "tail_y": np.asarray([], dtype=float),
        "tail_x_min": None,
        "tail_x_max": None,
        "tail_slope": None,
        "tail_intercept": None,
    }
    if not livetimes:
        return result
    livetime = livetimes[0]
    if len(livetimes) != len(points) or any(
        not math.isclose(value, livetime, rel_tol=1.0e-12, abs_tol=1.0e-12)
        for value in livetimes[1:]
    ):
        result["tail_status"] = "unavailable_inconsistent_livetime"
        return result

    # Draw the authoritative saved model verbatim.  This external plotter
    # neither recomputes/corrects that finite negative slope nor emits a
    # replacement background artifact.
    if stored_tail_fit is not None:
        r_tail = as_float(stored_tail_fit.get("r_tail"))
        slope = as_float(stored_tail_fit.get("slope"))
        if (
            not math.isfinite(r_tail)
            or not math.isfinite(slope)
            or slope >= 0.0
        ):
            raise ValueError(
                "authoritative stored tail requires finite r_tail and negative slope"
            )
        support_by_rank: dict[float, float] = {}
        for point in points:
            rank = as_float(point.get("llr"))
            log_far = as_float(point.get("log_far"))
            if math.isfinite(rank) and math.isfinite(log_far):
                support_by_rank.setdefault(rank, log_far)
        support_x = np.asarray(sorted(support_by_rank), dtype=float)
        support_y = np.asarray(
            [support_by_rank[rank] for rank in support_x], dtype=float
        )
        support_x_plot, support_y_plot = thin_curve(support_x, support_y)
        tail_mask = support_x >= r_tail
        tail_x = support_x[tail_mask]
        tail_y = support_y[tail_mask]
        line_x_max = max(
            float(np.nanmax(tail_x)) if tail_x.size else r_tail,
            r_tail + 1.0e-6,
        )
        line_x = np.linspace(r_tail, line_x_max, 160)
        line_y = tail_boundary + slope * (line_x - r_tail)
        result.update(
            {
                "support_point_count": int(support_x.size),
                "support_plot_point_count": int(support_x_plot.size),
                "tail_point_count": int(tail_x.size),
                "livetime": float(livetime),
                "r_tail": r_tail,
                "support_x": support_x_plot,
                "support_y": support_y_plot,
                "tail_x": tail_x,
                "tail_y": tail_y,
                "tail_x_min": (
                    float(np.nanmin(tail_x)) if tail_x.size else None
                ),
                "tail_x_max": (
                    float(np.nanmax(tail_x)) if tail_x.size else None
                ),
                "tail_line_x": line_x,
                "tail_line_y": line_y,
                "tail_slope": slope,
                "tail_intercept": tail_boundary - slope * r_tail,
                "tail_status": "valid_authoritative_stored_negative_slope",
            }
        )
        return result

    if not math.isclose(
        tail_boundary, TAIL_BOUNDARY_LOG10_FAR, rel_tol=0.0, abs_tol=1.0e-12
    ):
        raise ValueError(
            "diagnostic tail recomputation requires the current numeric boundary"
        )
    model = CRASHCAR_NUMERIC.tail_model(ranks, livetime)
    support_x = np.asarray(model["empirical_ranks"], dtype=float)
    support_y = np.asarray(model["empirical_log10_fars"], dtype=float)
    support_x_plot, support_y_plot = thin_curve(support_x, support_y)
    tail_index = int(model["tail_index"])
    r_tail = float(model["r_tail"])
    tail_x = support_x[tail_index:]
    tail_y = support_y[tail_index:]
    slope = model["tail_slope"]
    intercept = model["tail_intercept"]
    result.update(
        {
            "support_point_count": int(support_x.size),
            "support_plot_point_count": int(support_x_plot.size),
            "tail_point_count": int(tail_x.size),
            "livetime": float(livetime),
            "r_tail": r_tail,
            "support_x": support_x_plot,
            "support_y": support_y_plot,
            "tail_x": tail_x,
            "tail_y": tail_y,
            "tail_x_min": float(np.nanmin(tail_x)) if tail_x.size else None,
            "tail_x_max": float(np.nanmax(tail_x)) if tail_x.size else None,
        }
    )
    if slope is None or intercept is None:
        result["tail_status"] = "failed_nonnegative_or_insufficient_tail_fit"
        return result
    slope = float(slope)
    intercept = float(intercept)
    anchored_y = slope * r_tail + intercept
    if (
        not math.isfinite(slope)
        or slope >= 0.0
        or not math.isfinite(intercept)
        or not math.isclose(
            anchored_y, TAIL_BOUNDARY_LOG10_FAR, rel_tol=0.0, abs_tol=1.0e-12
        )
    ):
        result["tail_status"] = "failed_invalid_unanchored_tail_fit"
        return result

    line_x_max = max(float(np.nanmax(tail_x)), r_tail + 1.0e-6)
    line_x = np.linspace(r_tail, line_x_max, 160)
    line_y = tail_boundary + slope * (line_x - r_tail)
    result.update(
        {
            "tail_line_x": line_x,
            "tail_line_y": line_y,
            "tail_slope": slope,
            "tail_intercept": intercept,
            "tail_status": "valid_anchored_negative_slope",
        }
    )
    return result

def format_online_label(online_summary: dict, ifo: str) -> str:
    info = online_summary.get("by_ifo", {}).get(ifo, {})
    fraction = as_float(info.get("fraction"))
    if math.isfinite(fraction):
        return f", online {100.0 * fraction:.1f}%"
    return ""


def plot_far_points(
    ax,
    points: list[dict],
    cmap,
    norm,
    xlabel: str,
    ylabel: str,
    title: str,
    view_mode: str = "fixed",
):
    eligible = eligible_far_points(points)
    view = eligible if view_mode == "all" else final_view_points(points)
    if view:
        artist = ax.scatter(
            [p["snr"] for p in view],
            [p["chisq"] for p in view],
            c=[far_bin(p["log_far"]) for p in view],
            cmap=cmap,
            norm=norm,
            s=FAR_POINT_SIZE,
            marker=".",
            linewidths=0,
            alpha=0.76,
            rasterized=True,
            zorder=2,
        )
    else:
        artist = ax.scatter([], [], c=[], cmap=cmap, norm=norm, s=FAR_POINT_SIZE, marker=".")
        empty_label = "no SNR>=4 FAR points" if view_mode == "all" else "no points in view"
        ax.text(0.5, 0.5, empty_label, ha="center", va="center", transform=ax.transAxes)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(left=SNR_XMIN)
    if view_mode != "all" or not view:
        ax.set_ylim(*CHISQ_VIEW)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    count_label = (
        f"{len(view)} plotted / {len(eligible)} total (SNR>={SNR_XMIN:g})"
        if view_mode == "all"
        else f"{len(view)} in view / {len(eligible)} total (SNR>={SNR_XMIN:g})"
    )
    ax.set_title(f"{title}\n{count_label}", fontweight="bold")
    ax.grid(True, which="both", alpha=0.18)
    return artist, view

def plot_compact_far_stores(
    ax,
    stores: Iterable[dict],
    cmap,
    norm,
    xlabel: str,
    ylabel: str,
    title: str,
    view_mode: str = "fixed",
):
    stores = tuple(stores)
    xs, ys, colors = compact_far_arrays(stores, view_mode)
    eligible_total = sum(store["points_total"] for store in stores)
    if xs.size:
        artist = ax.scatter(
            xs,
            ys,
            c=colors,
            cmap=cmap,
            norm=norm,
            s=FAR_POINT_SIZE,
            marker=".",
            linewidths=0,
            alpha=0.76,
            rasterized=True,
            zorder=2,
        )
    else:
        artist = ax.scatter(
            [], [], c=[], cmap=cmap, norm=norm,
            s=FAR_POINT_SIZE, marker="."
        )
        empty_label = (
            "no SNR>=4 FAR points"
            if view_mode == "all" else "no points in view"
        )
        ax.text(
            0.5, 0.5, empty_label,
            ha="center", va="center", transform=ax.transAxes
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(left=SNR_XMIN)
    if view_mode != "all" or not xs.size:
        ax.set_ylim(*CHISQ_VIEW)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    count_label = (
        f"{xs.size} plotted / {eligible_total} total (SNR>={SNR_XMIN:g})"
        if view_mode == "all"
        else f"{xs.size} in view / {eligible_total} total (SNR>={SNR_XMIN:g})"
    )
    ax.set_title(f"{title}\n{count_label}", fontweight="bold")
    ax.grid(True, which="both", alpha=0.18)
    return artist, int(xs.size)


def plot_first_2x2(
    payload: dict,
    output: Path,
    title: str,
    tail_boundary: float,
    far_point_view: str = "fixed",
) -> dict:
    zerolag_rows = payload["zerolag"].get("rows", [])
    panel_a = payload["panel_a"]
    compact_far_stores = payload.get("compact_far_stores")
    if compact_far_stores is None:
        single_points = payload["single_points"]
        multi_points = payload["multi_points"]
        single_store = None
        multi_store = None
    else:
        single_points = None
        multi_points = None
        single_store = compact_far_stores["single"]
        multi_store = compact_far_stores["multi"]
    global_online_summary = payload.get("online_summary", {})
    panel_a_online_summary = payload.get("panel_a_online_summary", {})

    fig, axes = plt.subplots(2, 2, figsize=(17.0, 12.5), constrained_layout=True)
    fig.suptitle(f"{title}: background and FAR surfaces", fontsize=17, fontweight="bold")

    ax = axes[0, 0]
    panel_a_worker = panel_a.get("worker", "000")
    panel_a_policy = panel_a.get("bg_policy", "latest")
    panel_a_source_kind = panel_a.get(
        "source_kind", "detail_calculated_far_diagnostic")
    panel_a_source_label = (
        "authoritative single_background.json FAR support"
        if panel_a_source_kind in (
            "background_json",
            "live_no_injection_single_background",
        )
        else "non-authoritative detail calculated-FAR diagnostic"
    )
    panel_a_fit_summary: dict[str, dict] = {}
    for ifo in ("H1", "L1"):
        pts = [p for p in panel_a.get("points", []) if p["ifo"] == ifo]
        if pts:
            ax.scatter(
                [p["llr"] for p in pts],
                [p["log_far"] for p in pts],
                s=2.0,
                marker=".",
                alpha=0.30,
                color=IFO_COLORS[ifo],
                rasterized=True,
                label=f"{ifo} worker{panel_a_worker} {panel_a_source_label} ({len(pts)}){format_online_label(panel_a_online_summary, ifo)}",
            )
            fit = panel_a_segmented_fit(
                pts,
                tail_boundary,
                panel_a.get("tail_fit_by_ifo", {}).get(ifo),
            )
            if fit:
                panel_a_fit_summary[ifo] = {
                    "support_point_count": fit["support_point_count"],
                    "support_plot_point_count": fit["support_plot_point_count"],
                    "tail_point_count": fit["tail_point_count"],
                    "tail_source": fit["tail_source"],
                    "tail_boundary_log10_far": fit["tail_boundary_log10_far"],
                    "r_tail": fit["r_tail"],
                    "assignment_boundary": fit["assignment_boundary"],
                    "tail_status": fit["tail_status"],
                    "livetime": fit["livetime"],
                    "tail_x_min": fit["tail_x_min"],
                    "tail_x_max": fit["tail_x_max"],
                    "tail_slope": fit["tail_slope"],
                    "tail_intercept": fit["tail_intercept"],
                }
                if fit["tail_x"].size:
                    ax.scatter(
                        fit["tail_x"],
                        fit["tail_y"],
                        s=TAIL_POINT_SIZE,
                        marker="x",
                        linewidths=0.85,
                        alpha=0.88,
                        color=IFO_COLORS[ifo],
                        rasterized=True,
                        label=f"{ifo} tail support (r >= r_tail)",
                    )
                if fit["tail_line_x"].size:
                    ax.plot(
                        fit["tail_line_x"],
                        fit["tail_line_y"],
                        color=IFO_COLORS[ifo],
                        linewidth=2.0,
                        linestyle="-",
                        alpha=0.96,
                        label=f"{ifo} tail fit (r > r_tail, anchored at {tail_boundary:g})",
                    )
        else:
            ax.text(0.03, 0.90 if ifo == "H1" else 0.82, f"{ifo}: no worker{panel_a_worker} BG support rows", transform=ax.transAxes, color=IFO_COLORS[ifo])
    ax.axhline(tail_boundary, color="0.25", linestyle="-.", linewidth=1.1, label=f"tail boundary {tail_boundary:g}")
    ax.set_xlabel("LLR")
    ax.set_ylabel("log10(Calculated FAR)")
    ax.set_xlim(LLR_XMIN, LLR_XMAX)
    ax.set_title(f"Panel (a): worker{panel_a_worker} H/L {panel_a_policy} BG support\n{panel_a_source_label}", fontweight="bold")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=7)

    cmap = ListedColormap(FAR_BIN_COLORS)
    norm = BoundaryNorm(np.arange(-0.5, len(FAR_BIN_LABELS) + 0.5), cmap.N)
    cmap2 = cmap.copy()
    cmap2.set_bad("white")

    ax = axes[0, 1]
    if compact_far_stores is None:
        artist_b, single_view = plot_far_points(
            ax,
            single_points,
            cmap2,
            norm,
            "single-detector SNR",
            "chisq",
            "Panel (b): historical assigned-FAR single total (H1/L1 only)",
            view_mode=far_point_view,
        )
        single_view_count = len(single_view)
    else:
        artist_b, single_view_count = plot_compact_far_stores(
            ax,
            (single_store,),
            cmap2,
            norm,
            "single-detector SNR",
            "chisq",
            "Panel (b): historical assigned-FAR single total (H1/L1 only)",
            view_mode=far_point_view,
        )
        single_view = None

    ax = axes[1, 0]
    if compact_far_stores is None:
        artist_c, multi_view = plot_far_points(
            ax,
            multi_points,
            cmap2,
            norm,
            "coherent SNR",
            "cmbchisq",
            "Panel (c): historical assigned-FAR multi total (all detector combos)",
            view_mode=far_point_view,
        )
        multi_view_count = len(multi_view)
    else:
        artist_c, multi_view_count = plot_compact_far_stores(
            ax,
            (multi_store,),
            cmap2,
            norm,
            "coherent SNR",
            "cmbchisq",
            "Panel (c): historical assigned-FAR multi total (all detector combos)",
            view_mode=far_point_view,
        )
        multi_view = None

    ax = axes[1, 1]
    if compact_far_stores is None:
        combined_points = single_points + multi_points
        artist_d, combined_view = plot_far_points(
            ax,
            combined_points,
            cmap2,
            norm,
            "SNR (single or coherent)",
            "chisq / cmbchisq",
            "Panel (d): historical assigned-FAR combination (single + multi)",
            view_mode=far_point_view,
        )
        combined_view_count = len(combined_view)
    else:
        combined_points = None
        artist_d, combined_view_count = plot_compact_far_stores(
            ax,
            (single_store, multi_store),
            cmap2,
            norm,
            "SNR (single or coherent)",
            "chisq / cmbchisq",
            "Panel (d): historical assigned-FAR combination (single + multi)",
            view_mode=far_point_view,
        )
        combined_view = None

    add_discrete_colorbar(fig, axes, artist_d)

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(fig)
    if compact_far_stores is None:
        single_count = len(single_points)
        multi_count = len(multi_points)
        single_summary = point_summary(single_points, single_view)
        multi_summary = point_summary(multi_points, multi_view)
        combined_summary = point_summary(combined_points, combined_view)
    else:
        single_count = single_store["points_raw_total"]
        multi_count = multi_store["points_raw_total"]
        single_summary = compact_point_summary((single_store,), far_point_view)
        multi_summary = compact_point_summary((multi_store,), far_point_view)
        combined_summary = compact_point_summary(
            (single_store, multi_store), far_point_view
        )
    return {
        "plot": str(output),
        "zerolag_glob": payload["zerolag"]["glob"],
        "zerolag_file_count": payload["zerolag"]["file_count"],
        "zerolag_rows": payload["zerolag"].get(
            "row_count", len(zerolag_rows)
        ),
        "zerolag_rows_by_ifos": payload["zerolag"]["rows_by_ifos"],
        "historical_single_points": single_count,
        "historical_multi_points": multi_count,
        "h_l_single_points": single_count,
        "h_l_multi_points": multi_count,
        "single_points_in_view": single_view_count,
        "multi_points_in_view": multi_view_count,
        "combined_points_in_view": combined_view_count,
        "single_summary": single_summary,
        "multi_summary": multi_summary,
        "combined_summary": combined_summary,
        "panel_b_artist": "small_points",
        "panel_c_artist": "small_points",
        "panel_d_artist": "small_points",
        "panel_b_plotted_points": single_view_count,
        "panel_c_plotted_points": multi_view_count,
        "panel_d_plotted_points": combined_view_count,
        "llr_xmin": LLR_XMIN,
        "llr_xmax": LLR_XMAX,
        "snr_xmin": SNR_XMIN,
        "colorbar_count": 1,
        "colorbar_location": "right",
        "far_point_size": FAR_POINT_SIZE,
        "far_point_view": far_point_view,
        "tail_point_size": TAIL_POINT_SIZE,
        "worker000_panel_a_counts": panel_a.get("counts_ready", {}),
        "worker000_panel_a_counts_selected": panel_a.get("counts_ready_selected", {}),
        "worker000_panel_a_bg_policy": panel_a.get("bg_policy", "latest"),
        "worker000_panel_a_latest_total_window_count_by_ifo": panel_a.get("latest_total_window_count_by_ifo", {}),
        "worker000_panel_a_latest_bg_end_by_ifo": panel_a.get("latest_bg_end_by_ifo", {}),
        "worker000_panel_a_ready_windows": panel_a.get("ready_windows", []),
        "worker000_panel_a_points_plotted": panel_a.get("points_plotted", 0),
        "worker000_panel_a_points_original": panel_a.get("points_original", 0),
        "worker000_panel_a_segmented_fit": panel_a_fit_summary,
        "worker000_panel_a_fit_display": "tail_fit_same_color_lines_with_cross_tail_points",
        "worker000_panel_a_tail_boundary_log10_far": tail_boundary,
        "worker000_panel_a_tail_boundary_source": "shared_crashcar_numeric.TAIL_FAR",
        "worker000_panel_a_source_kind": panel_a_source_kind,
        "worker000_panel_a_min_direct_far_by_ifo": panel_a.get("min_direct_far_by_ifo", {}),
        "worker000_panel_a_floor_far_by_ifo": panel_a.get("floor_far_by_ifo", {}),
        "background_online_summary": panel_a_online_summary,
        "panel_a_background_online_summary": panel_a_online_summary,
        "global_latest_background_online_summary": global_online_summary,
        "panel_a_source": panel_a.get("files", []),
        "panel_scope": "Panel (a) requires authoritative schema4 single_background.json support unless the user explicitly selects diagnostic-only detail. Panels (b)-(d) use normal A109 zerolag rows and require a positive route-owned A107 far_sngl value.",
        "caveat": f"Current snapshot. Panel (a) uses worker{panel_a_worker} {panel_a_source_label} with bg_policy={panel_a_policy}; each Panel (a) online fraction is computed from that curve's own 3h BG window. Panels b-d aggregate only normal A109 zerolag rows with positive unique-owner single FAR across workers and bank IDs.",
    }


def row_far(row: dict, far_field: str, log_field: str) -> float | None:
    far = finite_positive(row.get(far_field))
    if far is not None:
        return far
    log_far = as_float(row.get(log_field))
    if math.isfinite(log_far):
        return 10.0**log_far
    return None


def normalized_key_value(value) -> str:
    text = str(value if value is not None else "").strip()
    if text == "":
        return ""
    try:
        number = float(text)
    except Exception:
        return text
    if math.isfinite(number) and number.is_integer():
        return str(int(number))
    return text


def table_field(row: dict, name: str, default=""):
    if name in row:
        return row[name]
    for key, value in row.items():
        if key.rsplit(":", 1)[-1] == name:
            return value
    return default


def component_time(row: dict, ifo: str) -> tuple[str, str]:
    end_time = row.get(f"end_time_sngl_{ifo}") or row.get("end_time") or ""
    end_time_ns = row.get(f"end_time_ns_sngl_{ifo}") or row.get("end_time_ns") or ""
    return normalized_key_value(end_time), normalized_key_value(end_time_ns)


def route_owned_final_far(row: dict) -> dict:
    """Return the actual FinalSink owner and its route-owned FAR payload."""
    ifos = str(row.get("_zerolag_ifos") or row.get("ifos", ""))
    try:
        route, owner_ifo = FINAL_ROUTE_BY_IFOS[ifos]
    except KeyError as exc:
        raise ValueError(f"unsupported exact FinalSink route {ifos!r}") from exc

    stored_route = row.get("_final_route")
    if stored_route not in (None, "", route):
        raise ValueError(
            f"candidate FinalSink route mismatch: stored={stored_route!r} actual={route!r}"
        )
    stored_owner = row.get("_final_owner_ifo")
    if stored_owner not in (None, "", owner_ifo):
        raise ValueError(
            "candidate FinalSink owner mismatch: "
            f"stored={stored_owner!r} actual={owner_ifo!r}"
        )

    source = "far"
    if route == "H1_SINGLE":
        source = "far_sngl_H1"
    elif route == "L1_SINGLE":
        source = "far_sngl_L1"
    raw = row.get("_route_owned_final_far")
    if raw in (None, ""):
        raw = row.get(source)
    if raw in (None, "") and route in ("H1_SINGLE", "L1_SINGLE"):
        raw = row.get("far_sngl")
    return {
        "route": route,
        "owner_ifo": owner_ifo,
        "source": source,
        "raw_value": raw,
    }


def compact_snr_row(row: dict | None) -> dict | None:
    if not row:
        return None
    keys = (
        "event_id", "ifo", "ifos", "bankid", "tmplt_idx", "end_time",
        "end_time_ns", "snglsnr", "chisq", "far_sngl", "log10_far_sngl",
        "far_multi", "log10_far_multi", "_selection_source",
        "_selection_kind", "_zerolag_source", "_zerolag_worker",
        "_zerolag_ifos", "_zerolag_event_id", "_selection_note",
        "_coincs_path", "_coincs_schema_columns", "H1_LLR", "L1_LLR",
        "_final_route", "_final_owner_ifo", "_route_owned_final_far",
        "_route_owned_final_far_source", "_writer_retention",
    )
    return {
        key: row.get(key) for key in keys if row.get(key) not in (None, "")
    }


def make_zerolag_snr_candidate(
    row: dict,
    ifo: str,
    kind: str,
    far: float,
    far_source: str | None,
    *,
    coherent_far_bases: tuple[str, ...] = DEFAULT_COHERENT_FAR_BASES,
) -> dict:
    component_end_time, component_end_time_ns = component_time(row, ifo)
    final_owner = route_owned_final_far(row)
    candidate = {
        "event_id": normalized_key_value(row.get("event_id", "")),
        "ifo": ifo,
        "ifos": str(row.get("ifos", "")),
        "bankid": normalized_key_value(row.get("bankid", "")),
        "tmplt_idx": normalized_key_value(row.get("tmplt_idx", "")),
        "end_time": normalized_key_value(row.get("end_time", "")),
        "end_time_ns": normalized_key_value(row.get("end_time_ns", "")),
        "component_end_time": component_end_time,
        "component_end_time_ns": component_end_time_ns,
        "snglsnr": row.get(f"snglsnr_{ifo}", ""),
        "chisq": row.get(f"chisq_{ifo}", row.get("cmbchisq", "")),
        "hit_single": "1" if kind == "single" else "0",
        "hit_multi": "1" if kind == "multi" else "0",
        "_selection_source": "zerolag_history",
        "_selection_kind": kind,
        "_zerolag_source": row.get("_source", ""),
        "_zerolag_worker": normalize_worker_id(row.get("_worker", "")),
        "_zerolag_ifos": row.get("ifos", ""),
        "_zerolag_event_id": normalized_key_value(row.get("event_id", "")),
        "_selection_far_source": far_source or "",
        "_final_route": final_owner["route"],
        "_final_owner_ifo": final_owner["owner_ifo"],
        "_route_owned_final_far": final_owner["raw_value"],
        "_route_owned_final_far_source": final_owner["source"],
        "LLR": table_field(row, f"{ifo}_LLR", ""),
        "H1_LLR": table_field(row, "H1_LLR", ""),
        "L1_LLR": table_field(row, "L1_LLR", ""),
    }
    if kind == "single":
        candidate["far_sngl"] = f"{far:.17g}"
        candidate["log10_far_sngl"] = f"{math.log10(far):.12g}"
        multi_far, _multi_source = first_positive_field(row, coherent_far_bases)
        if multi_far is not None:
            candidate["far_multi"] = f"{multi_far:.17g}"
            candidate["log10_far_multi"] = f"{math.log10(multi_far):.12g}"
    else:
        candidate["far_multi"] = f"{far:.17g}"
        candidate["log10_far_multi"] = f"{math.log10(far):.12g}"
    return candidate


def select_history_single_candidate(
    zerolag_rows: list[dict],
    ifo: str,
    single_far_bases: tuple[str, ...],
    coherent_far_bases: tuple[str, ...],
) -> dict | None:
    candidates = []
    final_owner_by_mask = {
        "H1": "H1",
        "H1V1": "H1",
        "L1": "L1",
        "L1V1": "L1",
    }
    for row in zerolag_rows:
        if final_owner_by_mask.get(str(row.get("ifos", ""))) != ifo:
            continue
        far, far_source = first_positive_field(
            row, [f"{base}_{ifo}" for base in single_far_bases]
        )
        if far is None or not row_contains_ifo(row, ifo):
            continue
        snr = finite_positive(row.get(f"snglsnr_{ifo}"))
        if snr is None or snr < SNR_XMIN:
            continue
        candidates.append((far, make_zerolag_snr_candidate(
            row, ifo, "single", far, far_source,
            coherent_far_bases=coherent_far_bases,
        )))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def select_history_multi_components(
    zerolag_rows: list[dict],
    coherent_far_bases: tuple[str, ...],
) -> dict[str, dict | None]:
    candidates = []
    for row in zerolag_rows:
        ifos = str(row.get("ifos", ""))
        detectors = {token for token in ("H1", "L1", "V1", "K1") if token in ifos}
        if not {"H1", "L1"}.issubset(detectors):
            continue
        far, far_source = first_positive_field(row, coherent_far_bases)
        if far is not None:
            candidates.append((far, row, far_source))
    if not candidates:
        return {"H1": None, "L1": None}
    far, best_row, far_source = min(candidates, key=lambda item: item[0])
    return {
        ifo: make_zerolag_snr_candidate(
            best_row, ifo, "multi", far, far_source,
            coherent_far_bases=coherent_far_bases,
        )
        for ifo in ("H1", "L1")
    }


def load_zerolag_compact(
    run_root: Path,
    zerolag_glob: str,
    single_far_bases: tuple[str, ...],
    coherent_far_bases: tuple[str, ...],
) -> dict:
    files = sorted(run_root.glob(zerolag_glob))
    selected_columns = zerolag_required_columns(
        single_far_bases,
        coherent_far_bases,
    )
    single_store = new_compact_far_store()
    multi_store = new_compact_far_store()
    rows_by_ifos: Counter[str] = Counter()
    columns_seen: set[str] = set()
    row_count = 0
    best_single: dict[str, tuple[float, dict] | None] = {
        "H1": None,
        "L1": None,
    }
    best_multi: tuple[float, dict, str | None] | None = None
    final_owner_by_mask = {
        "H1": "H1",
        "H1V1": "H1",
        "L1": "L1",
        "L1V1": "L1",
    }

    for path in files:
        worker = path.parent.name
        source = str(path.relative_to(run_root))
        for row in parse_postcoh_rows(
            path,
            selected_columns=selected_columns,
        ):
            columns_seen.update(row)
            row["_worker"] = worker
            row["_source"] = source
            ifos = str(row.get("ifos", ""))
            rows_by_ifos[ifos] += 1
            row_count += 1

            for ifo in ("H1", "L1"):
                snr = finite_positive(row.get(f"snglsnr_{ifo}"))
                chisq = finite_positive(row.get(f"chisq_{ifo}"))
                far, far_source = first_positive_field(
                    row,
                    [f"{base}_{ifo}" for base in single_far_bases],
                )
                if (
                    row_contains_ifo(row, ifo)
                    and snr is not None
                    and chisq is not None
                    and far is not None
                ):
                    add_compact_far_point(
                        single_store,
                        snr=snr,
                        chisq=chisq,
                        far=far,
                        worker=worker,
                        bankid=row.get("bankid", ""),
                        ifo=ifo,
                    )
                if (
                    final_owner_by_mask.get(ifos) == ifo
                    and row_contains_ifo(row, ifo)
                    and far is not None
                    and snr is not None
                    and snr >= SNR_XMIN
                ):
                    current = best_single[ifo]
                    if current is None or far < current[0]:
                        best_single[ifo] = (
                            far,
                            make_zerolag_snr_candidate(
                                row,
                                ifo,
                                "single",
                                far,
                                far_source,
                                coherent_far_bases=coherent_far_bases,
                            ),
                        )

            detectors = {
                token
                for token in ("H1", "L1", "V1", "K1")
                if token in ifos
            }
            multi_far, multi_far_source = first_positive_field(
                row, coherent_far_bases
            )
            if len(detectors) >= 2:
                snr = finite_positive(row.get("cohsnr"))
                chisq = finite_positive(row.get("cmbchisq"))
                if (
                    snr is not None
                    and chisq is not None
                    and multi_far is not None
                ):
                    add_compact_far_point(
                        multi_store,
                        snr=snr,
                        chisq=chisq,
                        far=multi_far,
                        worker=worker,
                        bankid=row.get("bankid", ""),
                        ifo="+".join(sorted(detectors)),
                    )
            if (
                {"H1", "L1"}.issubset(detectors)
                and multi_far is not None
                and (
                    best_multi is None
                    or multi_far < best_multi[0]
                )
            ):
                best_multi = (
                    multi_far,
                    dict(row),
                    multi_far_source,
                )

    snr_candidates = {
        "h1_single_min_far": (
            best_single["H1"][1]
            if best_single["H1"] is not None else None
        ),
        "l1_single_min_far": (
            best_single["L1"][1]
            if best_single["L1"] is not None else None
        ),
        "hl_multi_min_far_h1_component": None,
        "hl_multi_min_far_l1_component": None,
    }
    if best_multi is not None:
        far, row, far_source = best_multi
        for ifo, key in (
            ("H1", "hl_multi_min_far_h1_component"),
            ("L1", "hl_multi_min_far_l1_component"),
        ):
            snr_candidates[key] = make_zerolag_snr_candidate(
                row,
                ifo,
                "multi",
                far,
                far_source,
                coherent_far_bases=coherent_far_bases,
            )

    return {
        "glob": zerolag_glob,
        "files": [str(path) for path in files],
        "file_count": len(files),
        "row_count": row_count,
        "rows_by_ifos": dict(rows_by_ifos),
        "columns_seen": sorted(columns_seen),
        "single_store": single_store,
        "multi_store": multi_store,
        "snr_candidates": snr_candidates,
        "storage": "streamed_compact_arrays_v1",
    }


def open_binary_maybe_gzip(path: Path):
    with path.open("rb") as probe:
        magic = probe.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else path.open("rb")


def open_text_maybe_gzip(path: Path):
    with path.open("rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", errors="replace")
    return path.open("rt", errors="replace")


def normalize_xml_event_id(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip().rsplit(":", 1)[-1]


def standard_complex8_event_id(element: ET.Element, path: Path) -> str:
    event_params = [
        child
        for child in element.findall("Param")
        if child.attrib.get("Name", "").split(":", 1)[0] == "event_id"
    ]
    if len(event_params) != 1:
        raise ValueError(
            f"COMPLEX8 series must contain exactly one direct standard "
            f"event_id Param: {path}"
        )
    event_param = event_params[0]
    if (
        event_param.attrib.get("Name") != "event_id:param"
        or event_param.attrib.get("Type") != "ilwd:char"
    ):
        raise ValueError(f"COMPLEX8 series has nonstandard event_id Param: {path}")
    match = re.fullmatch(
        r"sngl_inspiral:event_id:(0|[1-9][0-9]*)",
        event_param.text or "",
    )
    if match is None:
        raise ValueError(f"COMPLEX8 series has noncanonical event_id value: {path}")
    return match.group(1)


def parse_standard_complex8_series(path: Path) -> dict[str, dict]:
    """Parse normal CoincsDoc series using only its standard event_id Param."""
    parsed: dict[str, dict] = {}
    with open_binary_maybe_gzip(path) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "LIGO_LW" or element.attrib.get("Name") != "COMPLEX8TimeSeries":
                continue
            event_id = standard_complex8_event_id(element, path)
            if event_id in parsed:
                raise ValueError(f"duplicate COMPLEX8 event_id {event_id} in {path}")
            array = element.find("Array")
            stream = array.find("Stream") if array is not None else None
            dims = array.findall("Dim") if array is not None else []
            if stream is None or not dims:
                raise ValueError(f"COMPLEX8 event_id {event_id} has no normal array: {path}")
            tokens = (stream.text or "").split()
            if len(tokens) % 3:
                raise ValueError(f"COMPLEX8 event_id {event_id} has malformed triplets: {path}")
            values = [float(token) for token in tokens]
            times = values[0::3]
            real = values[1::3]
            imag = values[2::3]
            declared = int((dims[0].text or "0").strip())
            if declared != len(times) or not times:
                raise ValueError(
                    f"COMPLEX8 event_id {event_id} length {len(times)} != {declared}: {path}"
                )
            epoch_node = element.find("Time")
            epoch = float((epoch_node.text or "nan").strip()) if epoch_node is not None else float("nan")
            delta_t = as_float(dims[0].attrib.get("Scale"))
            parsed[event_id] = {
                "t": times,
                "real": real,
                "imag": imag,
                "abs_snr": [math.hypot(r, i) for r, i in zip(real, imag)],
                "epoch": epoch,
                "delta_t": delta_t,
                "length": declared,
                "source": str(path),
                "kind": "normal_coincs_complex8",
                "event_id": event_id,
            }
            element.clear()
    return parsed


def worker_from_bankid(bankid: int, start_bank: int, banks_per_worker: int, worker_count: int) -> int:
    if banks_per_worker < 1 or worker_count < 1:
        raise ValueError("worker geometry must be positive")
    delta = bankid - start_bank
    if delta < 0:
        raise ValueError(f"bank {bankid} precedes start bank {start_bank}")
    worker = delta // banks_per_worker
    if worker >= worker_count:
        raise ValueError(f"bank {bankid} is outside the worker roster")
    return worker


def coincs_identity(row: dict, worker: int | str) -> tuple[str, ...]:
    return (
        normalize_worker_id(str(worker)),
        str(row.get("ifos", "")),
        normalized_key_value(row.get("end_time", "")),
        normalized_key_value(row.get("end_time_ns", "")),
        normalized_key_value(row.get("bankid", "")),
        normalized_key_value(row.get("tmplt_idx", "")),
        normalized_key_value(row.get("event_id", "")),
    )


def parse_normal_coincs_document(
    path: Path,
    *,
    start_bank: int,
    banks_per_worker: int,
    worker_count: int,
) -> dict:
    match = COINCS_NAME_RE.fullmatch(path.name)
    if match is None:
        raise ValueError(f"not a normal CoincsDoc candidate filename: {path}")
    postcoh_rows = list(parse_postcoh_rows(path))
    if len(postcoh_rows) != 1:
        raise ValueError(f"CoincsDoc must contain exactly one Postcoh row: {path}")
    postcoh = postcoh_rows[0]
    filename_key = (
        match.group("ifos"), match.group("end_time"),
        match.group("bankid"), match.group("tmplt_idx"),
    )
    row_key = (
        str(postcoh.get("ifos", "")), normalized_key_value(postcoh.get("end_time")),
        normalized_key_value(postcoh.get("bankid")), normalized_key_value(postcoh.get("tmplt_idx")),
    )
    if filename_key != row_key:
        raise ValueError(f"Coincs filename/Postcoh identity mismatch: {path}")
    bankid = int(row_key[2])
    # A109 intentionally serializes no worker column.  Worker ownership is
    # reconstructed from the formal bank roster used by this run.
    worker = worker_from_bankid(bankid, start_bank, banks_per_worker, worker_count)

    sngl_rows = list(parse_ligolw_table_rows(path, "sngl_inspiral:table"))
    sngl_by_event: dict[str, dict] = {}
    ifo_to_event: dict[str, str] = {}
    for row in sngl_rows:
        event_id = normalize_xml_event_id(table_field(row, "event_id"))
        ifo = str(table_field(row, "ifo")).strip()
        if not event_id or event_id in sngl_by_event or not ifo or ifo in ifo_to_event:
            raise ValueError(f"invalid/duplicate SnglInspiral identity in {path}")
        sngl_by_event[event_id] = row
        ifo_to_event[ifo] = event_id

    map_rows = list(parse_ligolw_table_rows(path, "coinc_event_map:table"))
    mapped_event_counts: Counter[str] = Counter()
    for row in map_rows:
        if str(table_field(row, "table_name")).strip() != "sngl_inspiral":
            continue
        event_id = normalize_xml_event_id(table_field(row, "event_id"))
        if not event_id:
            raise ValueError(f"invalid SnglInspiral CoincMap event_id in {path}")
        mapped_event_counts[event_id] += 1
    series_by_event = parse_standard_complex8_series(path)
    series_by_ifo: dict[str, dict] = {}
    for event_id, series in series_by_event.items():
        if event_id not in sngl_by_event or mapped_event_counts[event_id] == 0:
            raise ValueError(f"unmapped COMPLEX8 event_id {event_id} in {path}")
        if mapped_event_counts[event_id] != 1:
            raise ValueError(f"duplicate CoincMap for COMPLEX8 event_id {event_id} in {path}")
        ifo = str(table_field(sngl_by_event[event_id], "ifo")).strip()
        if ifo in series_by_ifo:
            raise ValueError(f"duplicate COMPLEX8 IFO {ifo} in {path}")
        series = dict(series)
        series["ifo"] = ifo
        series_by_ifo[ifo] = series
    return {
        "path": str(path),
        "identity": coincs_identity(postcoh, worker),
        "worker": normalize_worker_id(str(worker)),
        "postcoh": postcoh,
        "schema_columns": len(postcoh),
        "sngl_by_event": sngl_by_event,
        "series_by_ifo": series_by_ifo,
    }


def discover_normal_coincs(
    run_root: Path,
    *,
    start_bank: int,
    banks_per_worker: int,
    worker_count: int,
) -> dict:
    files = sorted(
        path for path in (run_root / "run").glob("*.xml*")
        if COINCS_NAME_RE.fullmatch(path.name)
    )
    by_identity: dict[tuple[str, ...], dict] = {}
    for path in files:
        document = parse_normal_coincs_document(
            path,
            start_bank=start_bank,
            banks_per_worker=banks_per_worker,
            worker_count=worker_count,
        )
        key = document["identity"]
        if key in by_identity:
            raise ValueError(f"duplicate normal CoincsDoc identity: {key}")
        by_identity[key] = document
    return {"files": [str(path) for path in files], "by_identity": by_identity}


def select_normal_coincs_single_candidates(
    coincs: dict,
    coherent_far_bases: tuple[str, ...],
    *,
    snr_series_logfar_threshold: float,
) -> dict[str, dict | None]:
    """Select retained single-owner events directly from normal CoincsDocs.

    A normal CoincsDoc is the authoritative source for a retained matched-filter
    series.  In particular, it may be written after the latest completed
    zerolag snapshot, so single-panel discovery must not depend on zerolag
    landing cadence.
    """
    threshold = float(snr_series_logfar_threshold)
    if not math.isfinite(threshold):
        raise ValueError("SNR-series log FAR threshold must be finite")

    selected: dict[str, tuple[float, str, dict] | None] = {
        "H1": None,
        "L1": None,
    }
    for document in coincs["by_identity"].values():
        row = dict(document["postcoh"])
        row["_worker"] = document["worker"]
        row["_source"] = document["path"]
        final_owner = route_owned_final_far(row)
        if final_owner["route"] not in ("H1_SINGLE", "L1_SINGLE"):
            continue
        ifo = final_owner["owner_ifo"]
        far = finite_positive(final_owner["raw_value"])
        if far is None or math.log10(far) > threshold:
            continue
        snr = finite_positive(row.get(f"snglsnr_{ifo}"))
        if snr is None or snr < SNR_XMIN:
            continue
        if ifo not in document["series_by_ifo"]:
            raise ValueError(
                "threshold-eligible normal single-owned CoincsDoc lacks "
                f"{ifo} COMPLEX8 series: {document['path']}"
            )
        candidate = make_zerolag_snr_candidate(
            row,
            ifo,
            "single",
            far,
            final_owner["source"],
            coherent_far_bases=coherent_far_bases,
        )
        candidate["_selection_source"] = "normal_coincs"
        candidate.pop("_zerolag_source", None)
        candidate["_selection_note"] = (
            "threshold-eligible normal single-owned CoincsDoc selected directly"
        )
        ranked = (far, document["path"], candidate)
        if selected[ifo] is None or ranked[:2] < selected[ifo][:2]:
            selected[ifo] = ranked
    return {
        ifo: ranked[2] if ranked is not None else None
        for ifo, ranked in selected.items()
    }


def candidate_identity(candidate: dict) -> tuple[str, ...]:
    return (
        normalize_worker_id(candidate.get("_zerolag_worker", "")),
        str(candidate.get("_zerolag_ifos") or candidate.get("ifos", "")),
        normalized_key_value(candidate.get("end_time", "")),
        normalized_key_value(candidate.get("end_time_ns", "")),
        normalized_key_value(candidate.get("bankid", "")),
        normalized_key_value(candidate.get("tmplt_idx", "")),
        normalized_key_value(candidate.get("event_id", "")),
    )


def _command_option(
    tokens: list[str],
    names: tuple[str, ...],
    default: str | None,
) -> str | None:
    values = []
    for index, token in enumerate(tokens):
        for name in names:
            if token == name:
                if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                    raise ValueError(f"missing value for recorded option {name}")
                values.append(tokens[index + 1])
            elif token.startswith(name + "="):
                values.append(token.split("=", 1)[1])
    if not values:
        return default
    if len(set(values)) != 1:
        raise ValueError(f"conflicting recorded options {names}: {values}")
    return values[0]


def _recorded_float(value: str, label: str, *, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid recorded {label}: {value!r}") from exc
    if not math.isfinite(parsed) or (minimum is not None and parsed < minimum):
        raise ValueError(f"invalid recorded {label}: {value!r}")
    return parsed


def parse_finalsink_writer_config(path: Path) -> dict:
    """Parse the exact FinalSink writer settings recorded for one worker."""
    match = re.fullmatch(r"crashcar_command_(\d{3})\.txt", path.name)
    if match is None:
        raise ValueError(f"invalid crashcar command-log filename: {path}")
    command_lines = [
        line.removeprefix("CRASHCAR_CMD ").strip()
        for line in path.read_text(errors="replace").splitlines()
        if line.startswith("CRASHCAR_CMD ")
    ]
    if len(command_lines) != 1:
        raise ValueError(
            f"expected one CRASHCAR_CMD record for worker {match.group(1)}: {path}"
        )
    tokens = shlex.split(command_lines[0])
    if not tokens or Path(tokens[0]).name != "gstlal_inspiral_postcohspiir_online":
        raise ValueError(f"unexpected recorded crashcar entrypoint: {path}")

    schema_mode = _command_option(
        tokens, ("--finalsink-postcoh-schema-mode",), "legacy-a107"
    )
    if schema_mode not in ("legacy-a107", "crashcar-a109"):
        raise ValueError(f"invalid recorded FinalSink schema mode: {schema_mode!r}")
    # These are the deployed CLI defaults when the option is absent.
    cluster_window = _recorded_float(
        _command_option(tokens, ("--finalsink-cluster-window",), "0"),
        "FinalSink cluster window",
        minimum=0.0,
    )
    snr_threshold = _recorded_float(
        _command_option(tokens, ("--snr-series-logfar-threshold",), "-4"),
        "SNR-series log FAR threshold",
    )
    gracedb_value = _command_option(
        tokens,
        (
            "--finalsink-gracedb-far-thresh",
            "--finalsink-gracedb-far-threshold",
        ),
        None,
    )
    gracedb_threshold = (
        None if gracedb_value is None
        else _recorded_float(
            gracedb_value, "GraceDB FAR threshold", minimum=0.0
        )
    )
    superevent_threshold = _recorded_float(
        _command_option(tokens, ("--finalsink-superevent-thresh",), "3.8e-7"),
        "FinalSink superevent threshold",
        minimum=0.0,
    )
    return {
        "worker": match.group(1),
        "source": str(path),
        "source_sha256": sha256_file(path),
        "schema_mode": schema_mode,
        "crashcar_enabled": schema_mode == "crashcar-a109",
        "cluster_window": cluster_window,
        "snr_series_logfar_threshold": snr_threshold,
        "gracedb_far_threshold": gracedb_threshold,
        "superevent_threshold": superevent_threshold,
    }


def discover_finalsink_writer_configs(run_root: Path) -> dict[str, dict]:
    configs = {}
    for path in sorted((run_root / "run" / "logs").glob("crashcar_command_*.txt")):
        config = parse_finalsink_writer_config(path)
        worker = config["worker"]
        if worker in configs:
            raise ValueError(f"duplicate FinalSink writer config for worker {worker}")
        configs[worker] = config
    return configs


def writer_config_for_candidate(
    candidate: dict | None,
    writer_configs: dict[str, dict] | None,
) -> dict | None:
    if candidate is None:
        return None
    worker = normalize_worker_id(candidate.get("_zerolag_worker", ""))
    if not writer_configs or worker not in writer_configs:
        raise ValueError(
            f"missing FinalSink writer config for candidate worker {worker}"
        )
    return writer_configs[worker]


def finalsink_writer_retention(candidate: dict, writer_config: dict) -> dict:
    """Classify whether the deployed FinalSink was required to retain Coincs."""
    final_owner = route_owned_final_far(candidate)
    try:
        final_far = float(final_owner["raw_value"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "invalid route-owned final FAR for writer classification: "
            f"{final_owner['raw_value']!r}"
        ) from exc
    if not math.isfinite(final_far):
        raise ValueError(
            f"invalid route-owned final FAR for writer classification: {final_far!r}"
        )

    route = final_owner["route"]
    cluster_window = _recorded_float(
        writer_config.get("cluster_window"), "FinalSink cluster window", minimum=0.0
    )
    snr_threshold = _recorded_float(
        writer_config.get("snr_series_logfar_threshold"),
        "SNR-series log FAR threshold",
    )
    gracedb_value = writer_config.get("gracedb_far_threshold")
    gracedb_threshold = (
        None if gracedb_value is None
        else _recorded_float(
            gracedb_value, "GraceDB FAR threshold", minimum=0.0
        )
    )
    if writer_config.get("superevent_threshold") is not None:
        _recorded_float(
            writer_config["superevent_threshold"],
            "FinalSink superevent threshold",
            minimum=0.0,
        )
    crashcar_enabled = writer_config.get("schema_mode") == "crashcar-a109"
    positive = final_far > 0.0
    single_threshold_write = (
        crashcar_enabled
        and route in ("H1_SINGLE", "L1_SINGLE")
        and positive
        and math.log10(final_far) <= snr_threshold
    )

    expected: bool | None = False
    reason = ""
    if cluster_window == 0.0:
        expected = single_threshold_write
        if expected:
            reason = "CRASHCAR_CLUSTER_ZERO_SINGLE_THRESHOLD_WRITER"
        elif route == "MULTI":
            reason = "CLUSTER_ZERO_MULTI_HAS_NO_COINCS_WRITER"
        elif not crashcar_enabled:
            reason = "CLUSTER_ZERO_NORMAL_A107_DIRECT_APPEND"
        elif not positive:
            reason = "ROUTE_OWNED_FINAL_FAR_NONPOSITIVE"
        else:
            reason = "SINGLE_FINAL_FAR_ABOVE_RECORDED_SNR_THRESHOLD"
    else:
        # The clustered normal writer also depends on candidate selection,
        # __pass_test veto inputs, and trigger-control history.  The plotter
        # deliberately does not reproduce that state machine.
        expected = None
        reason = "CLUSTERED_WRITER_ELIGIBILITY_REQUIRES_RUNTIME_STATE"

    return {
        "expected": expected,
        "assertion": (
            "EXPECTED" if expected is True
            else "NOT_EXPECTED" if expected is False
            else "UNKNOWN_NOT_ASSERTED"
        ),
        "reason": reason,
        "route": route,
        "owner_ifo": final_owner["owner_ifo"],
        "route_owned_final_far": final_far,
        "route_owned_final_far_source": final_owner["source"],
        "schema_mode": writer_config.get("schema_mode"),
        "worker": writer_config.get("worker"),
        "cluster_window": cluster_window,
        "snr_series_logfar_threshold": snr_threshold,
        "gracedb_far_threshold": gracedb_threshold,
        "writer_config_source": writer_config.get("source"),
        "writer_config_sha256": writer_config.get("source_sha256"),
    }


def attach_normal_coincs(
    candidate: dict | None,
    coincs: dict,
    *,
    snr_series_logfar_threshold: float,
    writer_config: dict | None = None,
) -> dict | None:
    if candidate is None:
        return None
    row = dict(candidate)
    document = coincs["by_identity"].get(candidate_identity(row))
    if document is None:
        if writer_config is None:
            raise ValueError(
                "missing FinalSink writer config for absent CoincsDoc "
                f"{candidate_identity(row)}"
            )
        retention = finalsink_writer_retention(row, writer_config)
        retention["plot_requested_logfar_threshold"] = float(
            snr_series_logfar_threshold
        )
        row["_writer_retention"] = retention
        if retention["expected"] is True:
            raise ValueError(
                "missing normal CoincsDoc for writer-eligible exact key "
                f"{candidate_identity(row)} reason={retention['reason']}"
            )
        if retention["expected"] is False:
            row["_selection_note"] = (
                "NOT_RETAINED_BY_NORMAL_WRITER: " + retention["reason"]
            )
        else:
            row["_selection_note"] = (
                "WRITER_ELIGIBILITY_UNKNOWN_NOT_ASSERTED: "
                + retention["reason"]
            )
        return row
    ifo = str(row.get("ifo", ""))
    series = document["series_by_ifo"].get(ifo)
    if series is None:
        raise ValueError(f"normal CoincsDoc lacks {ifo} COMPLEX8 series: {document['path']}")
    if row.get("_selection_source") != "normal_coincs":
        row["_selection_source"] = "zerolag_history+normal_coincs"
    row["_coincs_path"] = document["path"]
    row["_coincs_schema_columns"] = document["schema_columns"]
    row["_normal_series"] = series
    row["_coincs_postcoh"] = document["postcoh"]
    return row


def select_snr_rows(
    zerolag_rows: list[dict],
    coincs: dict,
    single_far_bases: tuple[str, ...],
    coherent_far_bases: tuple[str, ...],
    *,
    snr_series_logfar_threshold: float,
    writer_configs: dict[str, dict] | None = None,
) -> dict[str, dict | None]:
    single_candidates = select_normal_coincs_single_candidates(
        coincs,
        coherent_far_bases,
        snr_series_logfar_threshold=snr_series_logfar_threshold,
    )
    multi_components = select_history_multi_components(zerolag_rows, coherent_far_bases)
    candidates = {
        "h1_single_min_far": single_candidates["H1"],
        "l1_single_min_far": single_candidates["L1"],
        "hl_multi_min_far_h1_component": multi_components.get("H1"),
        "hl_multi_min_far_l1_component": multi_components.get("L1"),
    }
    return {
        key: attach_normal_coincs(
            candidate,
            coincs,
            snr_series_logfar_threshold=snr_series_logfar_threshold,
            writer_config=(
                None
                if candidate is not None
                and candidate.get("_selection_source") == "normal_coincs"
                else writer_config_for_candidate(candidate, writer_configs)
            ),
        )
        for key, candidate in candidates.items()
    }


def load_series_for_row(row: dict | None) -> dict | None:
    return row.get("_normal_series") if row else None

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key}")
        result[key] = value
    return result


def require_exact_keys(value: dict, expected: tuple[str, ...], label: str) -> None:
    if set(value) != set(expected):
        raise ValueError(
            f"{label} keys mismatch: missing={sorted(set(expected) - set(value))} "
            f"extra={sorted(set(value) - set(expected))}"
        )


def read_bank_autocorrelation_array(path: Path, array_name: str, tmplt_idx: int) -> tuple[list[int], list[float]]:
    in_array = False
    in_stream = False
    dims: list[int] = []
    selected: list[float] = []
    count = 0
    with open_text_maybe_gzip(path) as handle:
        for line in handle:
            if not in_array:
                if f'<Array Name="{array_name}:array"' in line:
                    in_array = True
                continue
            if not in_stream:
                for dim in re.finditer(r"<Dim(?: [^>]*)?>(\d+)</Dim>", line):
                    dims.append(int(dim.group(1)))
                if "<Stream" in line:
                    if len(dims) != 2 or not 0 <= tmplt_idx < dims[1]:
                        raise ValueError(f"{array_name} dimensions/template index invalid in {path}")
                    in_stream = True
                continue
            if "</Stream>" in line:
                break
            for token in line.split():
                value = float(token)
                if count % dims[1] == tmplt_idx:
                    selected.append(value)
                count += 1
    if len(dims) != 2 or count != dims[0] * dims[1] or len(selected) != dims[0]:
        raise ValueError(f"{array_name} shape/data mismatch in {path}")
    return dims, selected


def load_pinned_bank_autocorrelation(
    bank_dir: Path,
    row: dict,
    *,
    start_bank: int,
    banks_per_worker: int,
    worker_count: int,
) -> dict:
    ifo = str(row.get("ifo", ""))
    if ifo not in ("H1", "L1"):
        raise ValueError(f"single autocorrelation IFO is not H1/L1: {ifo}")
    bankid = int(normalized_key_value(row.get("bankid", "")))
    tmplt_idx = int(normalized_key_value(row.get("tmplt_idx", "")))
    worker = worker_from_bankid(bankid, start_bank, banks_per_worker, worker_count)
    row_worker = normalize_worker_id(row.get("_zerolag_worker", ""))
    if row_worker != f"{worker:03d}":
        raise ValueError("selected row worker/bank roster mismatch")
    directory_input = Path(bank_dir)
    directory_info = directory_input.lstat()
    if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(directory_info.st_mode):
        raise ValueError(f"bank directory is not a real directory: {directory_input}")
    directory = directory_input.resolve(strict=True)
    bank_path = directory / f"iir_{ifo}-GSTLAL_SPLIT_BANK_{bankid:04d}-a1-0-0.xml.gz"
    info = bank_path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"pinned bank is not a regular non-symlink file: {bank_path}")
    resolved = bank_path.resolve(strict=True)
    if resolved.parent != directory:
        raise ValueError("pinned bank path escaped the exact bank directory")
    real_dims, real = read_bank_autocorrelation_array(
        resolved, "autocorrelation_bank_real", tmplt_idx
    )
    imag_dims, imag = read_bank_autocorrelation_array(
        resolved, "autocorrelation_bank_imag", tmplt_idx
    )
    if real_dims != imag_dims:
        raise ValueError(f"real/imag autocorrelation shapes differ in {resolved}")
    centre = real_dims[0] // 2
    return {
        "relative_index": [index - centre for index in range(real_dims[0])],
        "real": real,
        "imag": imag,
        "abs_autocorr": [math.hypot(r, i) for r, i in zip(real, imag)],
        "source": str(resolved),
        "sha256": sha256_file(resolved),
        "kind": "normal_pinned_bank_autocorrelation",
        "ifo": ifo,
        "worker": f"{worker:03d}",
        "bankid": bankid,
        "tmplt_idx": tmplt_idx,
        "autochisq_len": real_dims[0],
        "ntmplt": real_dims[1],
        "layout": "offset=k*ntmplt+tmplt_idx",
    }

def scaled_bank_autocorrelation(
    row: dict,
    template_series: dict | None,
    data_t: np.ndarray,
    data_y: np.ndarray,
) -> tuple[list[float], list[float]] | None:
    if not template_series:
        return None
    rel_idx = np.asarray(template_series.get("relative_index", []), dtype=float)
    amps = np.asarray(template_series.get("abs_autocorr", []), dtype=float)
    mask = np.isfinite(rel_idx) & np.isfinite(amps) & (amps >= 0)
    rel_idx = rel_idx[mask]
    amps = amps[mask]
    if rel_idx.size == 0 or amps.size == 0 or np.nanmax(amps) <= 0:
        return None
    dt = None
    if data_t.size >= 2:
        diffs = np.diff(np.sort(data_t))
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        if diffs.size:
            dt = float(np.nanmedian(diffs))
    if dt is None or not math.isfinite(dt) or dt <= 0:
        dt = 1.0
    scale = finite_positive(row.get("snglsnr"))
    if scale is None and data_y.size:
        scale = float(np.nanmax(data_y))
    if scale is None or scale <= 0:
        return None
    return (rel_idx * dt).tolist(), (amps / np.nanmax(amps) * scale).tolist()


def panel_far_bits(panel_key: str, row: dict) -> list[str]:
    if panel_key.startswith("single"):
        value = as_float(row.get("log10_far_sngl"))
        return [f"single={value:.2f}"] if math.isfinite(value) else []
    if panel_key.startswith("multi"):
        value = as_float(row.get("log10_far_multi"))
        return [f"multi={value:.2f}"] if math.isfinite(value) else []
    bits = []
    for label, key in (("single", "log10_far_sngl"), ("multi", "log10_far_multi")):
        value = as_float(row.get(key))
        if math.isfinite(value):
            bits.append(f"{label}={value:.2f}")
    return bits


def display_missing_series_note(note: str) -> str:
    if "historical multi-FAR minimum selected" in note:
        return (
            "Selected multi-FAR minimum should retain both H/L component SNR series "
            "in the current runtime; this run is missing this component, likely from "
            "an older runtime product or missing artifact."
        )
    if "historical minimum selected" in note and "above SNR-series threshold" in note:
        return "Selected historical minimum is above the SNR-series retention threshold."
    return note


def plot_series_panel(
    ax,
    panel_key: str,
    title: str,
    row: dict | None,
    series: dict | None,
    bank_autocorrelation: dict | None,
) -> dict:
    if not row or not series:
        ax.set_facecolor("white")
        ax.text(0.5, 0.58, "retained SNR series not available", ha="center", va="center", transform=ax.transAxes, fontsize=11)
        if row:
            bits = panel_far_bits(panel_key, row)
            note = row.get("_selection_note") or "historical FAR event selected, but no curve file was found"
            display_note = textwrap.fill(display_missing_series_note(note), width=78)
            detail = ", ".join(bits) if bits else "selected historical FAR event"
            ax.text(0.5, 0.47, detail, ha="center", va="center", transform=ax.transAxes, fontsize=9, color="0.25", wrap=True)
            ax.text(0.5, 0.36, display_note, ha="center", va="center", transform=ax.transAxes, fontsize=8, color="0.45", wrap=False)
        else:
            missing_text = (
                "No threshold-eligible normal single-owned CoincsDoc found"
                if panel_key.startswith("single")
                else "No historical assigned FAR candidate found"
            )
            ax.text(0.5, 0.46, missing_text, ha="center", va="center", transform=ax.transAxes, fontsize=9, color="0.4")
        ax.set_title(title, fontweight="bold")
        ax.set_xticks([])
        ax.set_yticks([])
        reason = (
            "no threshold-eligible normal single-owned CoincsDoc"
            if not row and panel_key.startswith("single")
            else "missing selected normal CoincsDoc series"
        )
        return {"available": False, "reason": reason, "selected_row": compact_snr_row(row)}

    t = np.asarray(series["t"], dtype=float)
    y = np.asarray(series["abs_snr"], dtype=float)
    mask = np.isfinite(t) & np.isfinite(y)
    t = t[mask]
    y = y[mask]
    if t.size and not (np.nanmin(t) < 0 < np.nanmax(t)):
        peak_idx = int(np.nanargmax(y))
        t = t - t[peak_idx]
    window = (t >= -0.09) & (t <= 0.09)
    if np.count_nonzero(window) >= 10:
        t = t[window]
        y = y[window]
    snr_ymax = float(np.nanmax(y)) if y.size else SNR_XMIN
    template_source = None
    template = scaled_bank_autocorrelation(row, bank_autocorrelation, t, y)
    if template is not None and bank_autocorrelation:
        template_source = bank_autocorrelation.get("source")
    if template is not None:
        tx = np.asarray(template[0], dtype=float)
        ty = np.asarray(template[1], dtype=float)
        tw = (tx >= -0.09) & (tx <= 0.09)
        if np.count_nonzero(tw) >= 10:
            tx = tx[tw]
            ty = ty[tw]
        if ty.size:
            snr_ymax = max(snr_ymax, float(np.nanmax(ty)))
        ax.plot(tx, ty, color="#ff7f0e", linewidth=1.7, label="template autocorr")
        autocorr = "available"
    else:
        ax.text(0.03, 0.96, "template autocorr\nnot available", transform=ax.transAxes, ha="left", va="top", fontsize=8, bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.85, "pad": 2})
        autocorr = "missing"
    ax.plot(t, y, color="#1f77b4", linewidth=1.1, label="stored matched-filter |SNR|")
    far_bits = panel_far_bits(panel_key, row)
    ax.set_title(title + ("\n" + ", ".join(far_bits) if far_bits else ""), fontweight="bold", fontsize=10)
    ax.set_xlabel("time from local |SNR| peak (s)")
    ax.set_ylabel("|SNR|")
    ax.set_ylim(0.0, max(1.0, snr_ymax * 1.06))
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    return {
        "available": True,
        "event_id": row.get("event_id"),
        "ifo": row.get("ifo"),
        "series_source": series.get("source"),
        "normal_series_type": series.get("kind"),
        "samples": int(t.size),
        "template_autocorr": autocorr,
        "template_autocorr_source": template_source,
    }


def plot_second_2x2(
    run_root: Path,
    output: Path,
    title: str,
    zerolag_rows: list[dict] | None,
    single_far_bases: tuple[str, ...],
    coherent_far_bases: tuple[str, ...],
    *,
    snr_series_logfar_threshold: float,
    start_bank: int,
    banks_per_worker: int,
    worker_count: int,
    bank_dir: Path | None,
    preselected_candidates: dict[str, dict | None] | None = None,
) -> dict:
    coincs = discover_normal_coincs(
        run_root,
        start_bank=start_bank,
        banks_per_worker=banks_per_worker,
        worker_count=worker_count,
    )
    writer_configs = discover_finalsink_writer_configs(run_root)
    if preselected_candidates is None:
        if zerolag_rows is None:
            raise ValueError(
                "zerolag rows or preselected SNR candidates are required"
            )
        selections = select_snr_rows(
            zerolag_rows,
            coincs,
            single_far_bases,
            coherent_far_bases,
            snr_series_logfar_threshold=snr_series_logfar_threshold,
            writer_configs=writer_configs,
        )
    else:
        direct_single = select_normal_coincs_single_candidates(
            coincs,
            coherent_far_bases,
            snr_series_logfar_threshold=snr_series_logfar_threshold,
        )
        candidates = dict(preselected_candidates)
        candidates["h1_single_min_far"] = direct_single["H1"]
        candidates["l1_single_min_far"] = direct_single["L1"]
        selections = {
            key: attach_normal_coincs(
                candidate,
                coincs,
                snr_series_logfar_threshold=snr_series_logfar_threshold,
                writer_config=(
                    None
                    if candidate is not None
                    and candidate.get("_selection_source") == "normal_coincs"
                    else writer_config_for_candidate(candidate, writer_configs)
                ),
            )
            for key, candidate in candidates.items()
        }
    series = {key: load_series_for_row(row) for key, row in selections.items()}
    bank_autocorrelation: dict[str, dict | None] = {}
    for key, row in selections.items():
        if row is None or series.get(key) is None:
            bank_autocorrelation[key] = None
            continue
        if bank_dir is None:
            raise ValueError("--bank-dir is required for exact template autocorrelation")
        bank_autocorrelation[key] = load_pinned_bank_autocorrelation(
            bank_dir,
            row,
            start_bank=start_bank,
            banks_per_worker=banks_per_worker,
            worker_count=worker_count,
        )

    fig, axes = plt.subplots(2, 2, figsize=(16.5, 11.5), constrained_layout=True)
    fig.suptitle(f"{title}: normal CoincsDoc SNR series", fontsize=17, fontweight="bold")
    panels = {
        "a": plot_series_panel(axes[0, 0], "single_H1", "Panel (a): H1 single-detector selection", selections.get("h1_single_min_far"), series.get("h1_single_min_far"), bank_autocorrelation.get("h1_single_min_far")),
        "b": plot_series_panel(axes[0, 1], "single_L1", "Panel (b): L1 single-detector selection", selections.get("l1_single_min_far"), series.get("l1_single_min_far"), bank_autocorrelation.get("l1_single_min_far")),
        "c": plot_series_panel(axes[1, 0], "multi_H1", "Panel (c): H1 component of H/L multi selection", selections.get("hl_multi_min_far_h1_component"), series.get("hl_multi_min_far_h1_component"), bank_autocorrelation.get("hl_multi_min_far_h1_component")),
        "d": plot_series_panel(axes[1, 1], "multi_L1", "Panel (d): L1 component of H/L multi selection", selections.get("hl_multi_min_far_l1_component"), series.get("hl_multi_min_far_l1_component"), bank_autocorrelation.get("hl_multi_min_far_l1_component")),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return {
        "plot": str(output),
        "normal_coincs_files": coincs["files"],
        "normal_coincs_count": len(coincs["files"]),
        "panels": panels,
        "selection_policy": "Single panels select threshold-eligible single-owned events directly from current-run normal CoincsDocs, independent of zerolag landing cadence. Multi panels use the historical zerolag FAR minimum and join its exact normal CoincsDoc. All series use worker/ifos/time/bank/template/event identity and standard event_id-to-SnglInspiral IFO mapping.",
        "selected_rows": {key: compact_snr_row(row) for key, row in selections.items()},
        "bank_autocorrelation": {
            key: ({
                "source": value["source"], "sha256": value["sha256"],
                "ifo": value["ifo"], "worker": value["worker"],
                "bankid": value["bankid"], "tmplt_idx": value["tmplt_idx"],
                "autochisq_len": value["autochisq_len"], "ntmplt": value["ntmplt"],
                "layout": value["layout"],
            } if value else None)
            for key, value in bank_autocorrelation.items()
        },
        "caveat": "A threshold-eligible single-owned CoincsDoc must contain its owner IFO series, and a selected multi event required by the normal writer must contain its exact series; otherwise plotting fails closed. No substitute is synthesized.",
    }

def resolve_panel_a_background_path(
    run_root: Path,
    panel_a_worker: str,
    explicit_background_json: Path | None,
) -> Path:
    worker_id = normalize_worker_id(panel_a_worker)
    if not worker_id.isdigit():
        raise ValueError(f"invalid Panel-A worker id: {panel_a_worker!r}")
    if explicit_background_json is not None:
        return (
            explicit_background_json
            if explicit_background_json.is_absolute()
            else run_root / explicit_background_json
        )
    return run_root / "run" / worker_id / "single_background.json"


def resolve_live_producer_background_path(
    producer_root: Path,
    panel_a_worker: str,
) -> Path:
    """Resolve one worker-local live no-injection background product."""
    worker_id = normalize_worker_id(panel_a_worker)
    if not worker_id.isdigit():
        raise ValueError(f"invalid Panel-A worker id: {panel_a_worker!r}")
    root = Path(producer_root).resolve(strict=True)
    return root / "run" / worker_id / "single_background.json"


def load_requested_panel_a_source(
    run_root: Path,
    *,
    panel_a_source: str,
    explicit_background_json: Path | None,
    background_accumulation_seconds: float,
    max_points: int,
    panel_a_worker: str,
    start_bank: int,
    banks_per_worker: int,
    worker_count: int,
    detail_glob: str,
    ifo_id_map: dict[str, str],
    panel_a_bg_policy: str,
) -> dict:
    if panel_a_source == "background":
        background_path = resolve_panel_a_background_path(
            run_root, panel_a_worker, explicit_background_json
        )
        return load_panel_a_background_json(
            background_path,
            background_accumulation_seconds,
            max_points,
            panel_a_worker,
            start_bank=start_bank,
            banks_per_worker=banks_per_worker,
            worker_count=worker_count,
        )
    if panel_a_source != "detail":
        raise ValueError(f"unsupported Panel-A source: {panel_a_source!r}")
    detail = load_panel_a_detail(
        run_root,
        detail_glob,
        panel_a_worker,
        ifo_id_map,
        max_points,
        panel_a_bg_policy,
    )
    if not detail.get("exists") or not detail.get("points"):
        raise ValueError("explicit diagnostic Panel-A detail source is not plottable")
    detail = dict(detail)
    detail["authoritative"] = False
    detail["metadata_role"] = "diagnostic_only"
    detail["source_kind"] = "detail_calculated_far_diagnostic"
    return detail


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path, help="Run root containing run/, controller/, artifacts/.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory; default is RUN_ROOT/figures.")
    parser.add_argument("--run-label", default=None, help="Filename/title stem; default is run root name.")
    parser.add_argument("--stamp", default=None, help="Timestamp suffix; default is current UTC.")
    parser.add_argument("--zerolag-glob", default="run/[0-9][0-9][0-9]/*_zerolag_*.xml.gz")
    parser.add_argument("--detail-glob", default="run/crashcar_singlefar_detail_worker*.csv", help="Non-authoritative diagnostic source used only with --panel-a-source detail.")
    parser.add_argument("--segment-glob", default=DEFAULT_SEGMENT_GLOB)
    parser.add_argument("--panel-a-worker", default="000")
    parser.add_argument("--panel-a-source", choices=("background", "detail"), default="background", help="Require authoritative schema4 single_background.json by default; detail is explicit diagnostic-only mode.")
    parser.add_argument("--ifo-id-map", default="0:H1,1:L1,2:V1,3:K1")
    parser.add_argument(
        "--single-far-priority",
        default=",".join(DEFAULT_SINGLE_FAR_BASES),
        help="Comma-separated existing A107 FAR payload fields; A109 accepts a positive value only on the exact H/HV or L/LV owner route.",
    )
    parser.add_argument(
        "--coherent-far-priority",
        default=",".join(DEFAULT_COHERENT_FAR_BASES),
        help="Comma-separated coherent FAR fields; plotting uses the first positive field in this priority order.",
    )
    parser.add_argument("--snr-series-logfar-threshold", type=float, default=-4.0, help="Threshold used to decide whether a selected historical FAR event is expected to have retained SNR series.")
    parser.add_argument("--background-accumulation-seconds", type=float, default=10800.0, help="BG accumulation window used for panel (a) H/L online fractions.")
    parser.add_argument("--tail-boundary-log10-far", type=float, default=TAIL_BOUNDARY_LOG10_FAR, help=argparse.SUPPRESS)
    parser.add_argument("--max-panel-a-points", type=int, default=0, help="0 means plot all authoritative BG support points (or all explicitly selected diagnostic detail points).")
    parser.add_argument("--panel-a-bg-policy", choices=("latest", "all"), default="latest", help="Policy used only for explicitly selected diagnostic detail; schema4 authority already identifies one BG.")
    parser.add_argument("--background-json", type=Path, default=None, help="Authoritative single_background.json for Panel (a); defaults to the selected worker's normal run output.")
    parser.add_argument("--background-producer-root", type=Path, default=None, help="Continuing no-injection producer root; Panel (a) reads its worker-local live single_background.json.")
    parser.add_argument("--start-bank", type=int, default=None)
    parser.add_argument("--banks-per-worker", type=int, default=None)
    parser.add_argument("--worker-count", type=int, default=None)
    parser.add_argument("--bank-dir", type=Path, default=None)
    parser.add_argument(
        "--far-point-view",
        choices=("fixed", "all"),
        default="fixed",
        help="Use the fixed publication view or plot every finite assigned-FAR point with SNR>=4 in panels (b)-(d).",
    )
    args = parser.parse_args()
    if (
        not math.isfinite(args.tail_boundary_log10_far)
        or args.tail_boundary_log10_far >= 0.0
    ):
        parser.error("--tail-boundary-log10-far must be finite and negative")

    run_root = args.run_root.resolve()
    start_bank = 0 if args.start_bank is None else args.start_bank
    banks_per_worker = 8 if args.banks_per_worker is None else args.banks_per_worker
    worker_count = 2 if args.worker_count is None else args.worker_count

    background_producer_root = None
    explicit_background_json = args.background_json
    if args.background_producer_root is not None:
        if args.panel_a_source != "background":
            parser.error("--background-producer-root requires --panel-a-source background")
        if args.background_json is not None:
            parser.error("--background-producer-root and --background-json are mutually exclusive")
        background_producer_root = args.background_producer_root.resolve(strict=True)
        explicit_background_json = resolve_live_producer_background_path(
            background_producer_root, args.panel_a_worker
        )
    if start_bank < 0 or banks_per_worker < 1 or worker_count < 1:
        parser.error("invalid worker geometry")
    bank_dir = args.bank_dir.resolve() if args.bank_dir is not None else None
    output_dir = (args.output_dir or (run_root / "figures")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = args.run_label or run_root.name
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_")

    single_far_bases = parse_csv_list(args.single_far_priority)
    coherent_far_bases = parse_csv_list(args.coherent_far_priority)
    ifo_id_map = parse_ifo_id_map(args.ifo_id_map)
    zerolag = load_zerolag_compact(
        run_root,
        args.zerolag_glob,
        single_far_bases,
        coherent_far_bases,
    )
    panel_a = load_requested_panel_a_source(
        run_root,
        panel_a_source=args.panel_a_source,
        explicit_background_json=explicit_background_json,
        background_accumulation_seconds=args.background_accumulation_seconds,
        max_points=args.max_panel_a_points,
        panel_a_worker=args.panel_a_worker,
        start_bank=start_bank,
        banks_per_worker=banks_per_worker,
        worker_count=worker_count,
        detail_glob=args.detail_glob,
        ifo_id_map=ifo_id_map,
        panel_a_bg_policy=args.panel_a_bg_policy,
    )
    if background_producer_root is not None:
        panel_a = dict(panel_a)
        panel_a["source_kind"] = "live_no_injection_single_background"
        panel_a["metadata_role"] = "authoritative_live_no_injection_background"
        panel_a["background_producer_root"] = str(background_producer_root)
    background_window = infer_background_window(zerolag, args.background_accumulation_seconds)
    online_summary = load_online_summary(run_root, args.segment_glob, background_window)
    panel_a_online_summary = panel_a.get("online_summary") or load_panel_a_online_summary(run_root, args.segment_glob, panel_a)
    first_payload = {
        "zerolag": zerolag,
        "panel_a": panel_a,
        "compact_far_stores": {
            "single": zerolag["single_store"],
            "multi": zerolag["multi_store"],
        },
        "online_summary": online_summary,
        "panel_a_online_summary": panel_a_online_summary,
    }

    first_plot = output_dir / f"{safe_label}_first_2x2_zerolag_current_{stamp}.png"
    second_plot = output_dir / f"{safe_label}_second_2x2_snr_current_{stamp}.png"
    first = plot_first_2x2(
        first_payload,
        first_plot,
        label,
        args.tail_boundary_log10_far,
        far_point_view=args.far_point_view,
    )
    second = plot_second_2x2(
        run_root,
        second_plot,
        label,
        None,
        single_far_bases,
        coherent_far_bases,
        snr_series_logfar_threshold=args.snr_series_logfar_threshold,
        start_bank=start_bank,
        banks_per_worker=banks_per_worker,
        worker_count=worker_count,
        bank_dir=bank_dir,
        preselected_candidates=zerolag["snr_candidates"],
    )

    meta = {
        "created_utc": stamp,
        "run_root": str(run_root),
        "run_label": label,
        "note_contract": "crashcar_workflow.pdf plotting contract plus current user rulings",
        "parameters": {
            "zerolag_glob": args.zerolag_glob,
            "detail_glob": args.detail_glob,
            "segment_glob": args.segment_glob,
            "panel_a_worker": args.panel_a_worker,
            "panel_a_source": args.panel_a_source,
            "ifo_id_map": ifo_id_map,
            "single_far_priority": single_far_bases,
            "coherent_far_priority": coherent_far_bases,
            "snr_series_logfar_threshold": args.snr_series_logfar_threshold,
            "background_accumulation_seconds": args.background_accumulation_seconds,
            "tail_boundary_log10_far": args.tail_boundary_log10_far,
            "tail_boundary_source": "shared_crashcar_numeric.TAIL_FAR",
            "max_panel_a_points": args.max_panel_a_points,
            "panel_a_bg_policy": args.panel_a_bg_policy,
            "background_json": (
                str(explicit_background_json)
                if explicit_background_json is not None else None
            ),
            "background_producer_root": (
                str(background_producer_root)
                if background_producer_root is not None else None
            ),
            "start_bank": start_bank,
            "banks_per_worker": banks_per_worker,
            "worker_count": worker_count,
            "bank_dir": str(args.bank_dir) if args.bank_dir else None,
            "far_point_view": args.far_point_view,
        },
        "first": first,
        "second": second,
    }
    meta_path = output_dir / f"{safe_label}_two_2x2_current_{stamp}.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"first": first["plot"], "second": second["plot"], "meta": str(meta_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
