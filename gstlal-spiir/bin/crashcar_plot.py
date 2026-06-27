#!/usr/bin/env python3
"""Reusable crashcar two-2x2 plotting tool.

The plotting contract follows Eric-bless-crashcar.pdf Section 4.1:

* Figure 1 panels (b)-(d) read the run-local FinalSink zerolag XML files.
* Figure 1 panel (a) reads worker-0 crashcar C detail/direct-FAR rows as the
  native-background proxy when no serialized native background object exists.
* Figure 2 reads crashcar_snr_series/manifest.csv and either per-event CSV
  series files or XML shards. Missing SNR-series inputs are reported and left
  blank; no replacement curve is synthesized.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import re
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
LLR_XMIN = -20.0
SNR_XMIN = 4.0
FAR_POINT_SIZE = 10.0
FIT_CURVE_MAX_POINTS = 700
TAIL_FIT_COLOR = "#2ca02c"
TAIL_BOUNDARY_LOG10_FAR = -2.5
DEFAULT_SEGMENT_GLOB = "run/[0-9][0-9][0-9]/H1L1V1_SEGMENTS_*.xml.gz"
DEFAULT_SINGLE_FAR_BASES = ("far_1w_sngl", "far_1d_sngl", "far_2h_sngl", "far_sngl")
DEFAULT_COHERENT_FAR_BASES = ("far_1w", "far_1d", "far_2h", "far")
ZEROLAG_NAME_RE = re.compile(r"_zerolag_(\d+)_(\d+)\.xml(?:\.gz)?$")


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


def parse_postcoh_rows(path: Path) -> Iterable[dict[str, str]]:
    columns: list[str] = []
    in_stream = False
    with gzip.open(path, "rt", errors="replace") as handle:
        for line in handle:
            match = re.search(r'<Column Name="([^"]+)"', line)
            if match:
                columns.append(match.group(1))
                continue
            if '<Stream Name="postcoh:table"' in line:
                in_stream = True
                continue
            if not in_stream:
                continue
            if "</Stream>" in line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                values = next(csv.reader([stripped]))
            except Exception:
                continue
            if len(values) == len(columns) + 1 and values[-1] == "":
                values = values[:-1]
            if len(values) == len(columns):
                yield dict(zip(columns, values))


def parse_ligolw_table_rows(path: Path, table_name: str) -> Iterable[dict[str, str]]:
    columns: list[str] = []
    in_table = False
    in_stream = False
    with gzip.open(path, "rt", errors="replace") as handle:
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
                    yield dict(zip(columns, values))
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


def load_zerolag(run_root: Path, zerolag_glob: str) -> dict:
    files = sorted(run_root.glob(zerolag_glob))
    rows: list[dict] = []
    rows_by_ifos: Counter[str] = Counter()
    columns_seen: set[str] = set()
    for path in files:
        worker = path.parent.name
        source = str(path.relative_to(run_root))
        for row in parse_postcoh_rows(path):
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


def load_panel_a_detail(
    run_root: Path,
    detail_glob: str,
    panel_a_worker: str,
    ifo_id_map: dict[str, str],
    max_points: int,
    bg_policy: str,
) -> dict:
    target_token = f"worker{panel_a_worker}"
    candidates = sorted(path for path in run_root.glob(detail_glob) if target_token in path.name)
    if not candidates:
        return {"exists": False, "files": [], "points": [], "counts": {}, "reason": f"no file matching {target_token}"}

    points: list[dict] = []
    counts_all: Counter[str] = Counter()
    counts_ready: Counter[str] = Counter()
    counts_by_window: Counter[tuple[str, int, int]] = Counter()
    rows_seen = 0
    for path in candidates:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows_seen += 1
                ifo_id = str(row.get("ifo_id", ""))
                ifo = row.get("ifo") or ifo_id_map.get(ifo_id, f"IFO{ifo_id}")
                counts_all[ifo] += 1
                direct_far = finite_positive(row.get("direct_far"))
                window_count = finite_positive(row.get("window_count"))
                total_window_count = finite_positive(row.get("total_window_count"))
                llr = as_float(row.get("llr"))
                if (
                    ifo not in ("H1", "L1")
                    or direct_far is None
                    or window_count is None
                    or total_window_count is None
                    or not math.isfinite(llr)
                ):
                    continue
                counts_ready[ifo] += 1
                window_key = (ifo, int(window_count), int(total_window_count))
                counts_by_window[window_key] += 1
                points.append(
                    {
                        "ifo": ifo,
                        "llr": llr,
                        "log_far": math.log10(direct_far),
                        "direct_far": direct_far,
                        "window_count": int(window_count),
                        "total_window_count": int(total_window_count),
                        "event_id": row.get("event_id", ""),
                        "snglsnr": as_float(row.get("snglsnr")),
                        "chisq": as_float(row.get("chisq")),
                    }
                )

    downsampled = False
    original_points = len(points)
    latest_total_by_ifo: dict[str, int] = {}
    if bg_policy == "latest":
        for point in points:
            latest_total_by_ifo[point["ifo"]] = max(latest_total_by_ifo.get(point["ifo"], -1), point["total_window_count"])
        points = [point for point in points if point["total_window_count"] == latest_total_by_ifo.get(point["ifo"])]

    if max_points > 0 and len(points) > max_points:
        step = max(1, math.ceil(len(points) / max_points))
        points = points[::step]
        downsampled = True

    return {
        "exists": True,
        "worker": panel_a_worker,
        "files": [str(path) for path in candidates],
        "rows_seen": rows_seen,
        "points": points,
        "counts_all": dict(counts_all),
        "counts_ready": dict(counts_ready),
        "counts_ready_selected": dict(Counter(point["ifo"] for point in points)),
        "bg_policy": bg_policy,
        "latest_total_window_count_by_ifo": latest_total_by_ifo,
        "ready_windows": [
            {"ifo": ifo, "window_count": window, "total_window_count": total, "rows": rows}
            for (ifo, window, total), rows in sorted(counts_by_window.items(), key=lambda item: (item[0][0], item[0][2], item[0][1]))
        ],
        "points_original": original_points,
        "points_plotted": len(points),
        "downsampled": downsampled,
    }


def far_bin(log_far: float) -> int:
    if log_far < -5.0:
        return 0
    if log_far >= 0.0:
        return len(FAR_BIN_LABELS) - 1
    return int(math.floor(log_far + 5.0)) + 1


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
            if snr is None or chisq is None or far is None:
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


def build_multi_points(zerolag_rows: list[dict], coherent_far_bases: tuple[str, ...]) -> list[dict]:
    points: list[dict] = []
    for row in zerolag_rows:
        ifos = str(row.get("ifos", ""))
        detectors = {token for token in ("H1", "L1", "V1", "K1") if token in ifos}
        if not {"H1", "L1"}.issubset(detectors) or not detectors.issubset({"H1", "L1"}):
            continue
        snr = finite_positive(row.get("cohsnr"))
        chisq = finite_positive(row.get("cmbchisq"))
        far, far_source = first_positive_field(row, coherent_far_bases)
        if snr is None or chisq is None or far is None:
            continue
        points.append(
            {
                "kind": "multi",
                "ifo": "HL",
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


def final_view_points(points: list[dict]) -> list[dict]:
    return [
        point
        for point in points
        if point["snr"] >= SNR_XMIN
        and CHISQ_VIEW[0] <= point["chisq"] <= CHISQ_VIEW[1]
        and math.isfinite(point["log_far"])
    ]


def point_summary(points: list[dict], view: list[dict]) -> dict:
    return {
        "points_total": len(points),
        "points_in_view": len(view),
        "by_worker": dict(Counter(str(point.get("worker", "")) for point in points)),
        "by_bankid": dict(Counter(str(point.get("bankid", "")) for point in points)),
        "view_by_worker": dict(Counter(str(point.get("worker", "")) for point in view)),
        "view_by_bankid": dict(Counter(str(point.get("bankid", "")) for point in view)),
        "by_ifo": dict(Counter(str(point.get("ifo", "")) for point in points)),
        "view_by_ifo": dict(Counter(str(point.get("ifo", "")) for point in view)),
    }


def thin_curve(xs: np.ndarray, ys: np.ndarray, max_points: int = FIT_CURVE_MAX_POINTS) -> tuple[np.ndarray, np.ndarray]:
    if xs.size <= max_points:
        return xs, ys
    indices = np.unique(np.linspace(0, xs.size - 1, max_points).astype(int))
    return xs[indices], ys[indices]


def panel_a_segmented_fit(points: list[dict], tail_boundary: float = TAIL_BOUNDARY_LOG10_FAR) -> dict | None:
    rows = [
        (as_float(point.get("llr")), as_float(point.get("log_far")))
        for point in points
        if math.isfinite(as_float(point.get("llr"))) and math.isfinite(as_float(point.get("log_far")))
    ]
    if len(rows) < 2:
        return None

    arr = np.asarray(rows, dtype=float)
    order = np.argsort(arr[:, 0], kind="mergesort")
    xs = arr[order, 0]
    ys = arr[order, 1]
    support_y = np.minimum.accumulate(ys)
    support_x_plot, support_y_plot = thin_curve(xs, support_y)

    tail_mask = ys <= tail_boundary
    tail_x = xs[tail_mask]
    tail_y = ys[tail_mask]
    result = {
        "support_point_count": int(xs.size),
        "support_plot_point_count": int(support_x_plot.size),
        "tail_point_count": int(tail_x.size),
        "tail_source": "fixed_log10_far_boundary",
        "tail_boundary_log10_far": float(tail_boundary),
        "support_x": support_x_plot,
        "support_y": support_y_plot,
        "tail_line_x": np.asarray([], dtype=float),
        "tail_line_y": np.asarray([], dtype=float),
        "tail_x_min": float(np.nanmin(tail_x)) if tail_x.size else None,
        "tail_x_max": float(np.nanmax(tail_x)) if tail_x.size else None,
        "tail_slope": None,
        "tail_intercept": None,
    }
    if tail_x.size >= 2 and np.unique(tail_x).size >= 2:
        slope, intercept = np.polyfit(tail_x, tail_y, 1)
        line_x_min = max(float(np.nanmin(tail_x)), LLR_XMIN)
        line_x_max = max(float(np.nanmax(tail_x)), line_x_min + 1e-6)
        line_x = np.linspace(line_x_min, line_x_max, 160)
        result.update(
            {
                "tail_line_x": line_x,
                "tail_line_y": slope * line_x + intercept,
                "tail_slope": float(slope),
                "tail_intercept": float(intercept),
            }
        )
    return result


def format_online_label(online_summary: dict, ifo: str) -> str:
    info = online_summary.get("by_ifo", {}).get(ifo, {})
    fraction = as_float(info.get("fraction"))
    if math.isfinite(fraction):
        return f", online {100.0 * fraction:.1f}%"
    return ""


def plot_far_points(ax, points: list[dict], cmap, norm, xlabel: str, ylabel: str, title: str):
    view = final_view_points(points)
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
        )
    else:
        artist = ax.scatter([], [], c=[], cmap=cmap, norm=norm, s=FAR_POINT_SIZE, marker=".")
        ax.text(0.5, 0.5, "no points in view", ha="center", va="center", transform=ax.transAxes)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(left=SNR_XMIN)
    ax.set_ylim(*CHISQ_VIEW)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(f"{title}\n{len(view)} points", fontweight="bold")
    ax.grid(True, which="both", alpha=0.18)
    return artist, view


def plot_first_2x2(payload: dict, output: Path, title: str, tail_boundary: float) -> dict:
    zerolag_rows = payload["zerolag"]["rows"]
    panel_a = payload["panel_a"]
    single_points = payload["single_points"]
    multi_points = payload["multi_points"]
    online_summary = payload.get("online_summary", {})

    fig, axes = plt.subplots(2, 2, figsize=(17.0, 12.5), constrained_layout=True)
    fig.suptitle(f"{title}: background and FAR surfaces", fontsize=17, fontweight="bold")

    ax = axes[0, 0]
    panel_a_worker = panel_a.get("worker", "000")
    panel_a_policy = panel_a.get("bg_policy", "latest")
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
                label=f"{ifo} worker{panel_a_worker} {panel_a_policy} BG rows ({len(pts)}){format_online_label(online_summary, ifo)}",
            )
            fit = panel_a_segmented_fit(pts, tail_boundary)
            if fit:
                panel_a_fit_summary[ifo] = {
                    "support_point_count": fit["support_point_count"],
                    "support_plot_point_count": fit["support_plot_point_count"],
                    "tail_point_count": fit["tail_point_count"],
                    "tail_source": fit["tail_source"],
                    "tail_boundary_log10_far": fit["tail_boundary_log10_far"],
                    "tail_x_min": fit["tail_x_min"],
                    "tail_x_max": fit["tail_x_max"],
                    "tail_slope": fit["tail_slope"],
                    "tail_intercept": fit["tail_intercept"],
                }
                if fit["tail_line_x"].size:
                    ax.plot(
                        fit["tail_line_x"],
                        fit["tail_line_y"],
                        color=TAIL_FIT_COLOR,
                        linewidth=2.0,
                        linestyle="-",
                        alpha=0.96,
                        label=f"{ifo} tail fit (log10 FAR <= {TAIL_BOUNDARY_LOG10_FAR:g})",
                    )
        else:
            ax.text(0.03, 0.90 if ifo == "H1" else 0.82, f"{ifo}: no worker{panel_a_worker} BG rows", transform=ax.transAxes, color=IFO_COLORS[ifo])
    ax.axhline(tail_boundary, color="0.25", linestyle="-.", linewidth=1.1, label=f"tail boundary {tail_boundary:g}")
    ax.set_xlabel("LLR")
    ax.set_ylabel("log10(direct FAR)")
    ax.set_xlim(left=LLR_XMIN)
    ax.set_title(f"Panel (a): worker{panel_a_worker} H/L {panel_a_policy} BG support\ncrashcar C direct-FAR/detail rows", fontweight="bold")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=7)

    cmap = ListedColormap(FAR_BIN_COLORS)
    norm = BoundaryNorm(np.arange(-0.5, len(FAR_BIN_LABELS) + 0.5), cmap.N)
    cmap2 = cmap.copy()
    cmap2.set_bad("white")

    ax = axes[0, 1]
    artist_b, single_view = plot_far_points(
        ax,
        single_points,
        cmap2,
        norm,
        "single-detector SNR",
        "chisq",
        "Panel (b): all-worker/all-bank single total",
    )

    ax = axes[1, 0]
    artist_c, multi_view = plot_far_points(
        ax,
        multi_points,
        cmap2,
        norm,
        "coherent SNR",
        "cmbchisq",
        "Panel (c): all-worker/all-bank H/L multi total",
    )

    ax = axes[1, 1]
    combined_points = single_points + multi_points
    artist_d, combined_view = plot_far_points(
        ax,
        combined_points,
        cmap2,
        norm,
        "SNR (single or coherent)",
        "chisq / cmbchisq",
        "Panel (d): single + multi combination",
    )

    add_discrete_colorbar(fig, axes, artist_d)

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return {
        "plot": str(output),
        "zerolag_glob": payload["zerolag"]["glob"],
        "zerolag_file_count": payload["zerolag"]["file_count"],
        "zerolag_rows": len(zerolag_rows),
        "zerolag_rows_by_ifos": payload["zerolag"]["rows_by_ifos"],
        "h_l_single_points": len(single_points),
        "h_l_multi_points": len(multi_points),
        "single_points_in_view": len(single_view),
        "multi_points_in_view": len(multi_view),
        "combined_points_in_view": len(combined_view),
        "single_summary": point_summary(single_points, single_view),
        "multi_summary": point_summary(multi_points, multi_view),
        "combined_summary": point_summary(combined_points, combined_view),
        "panel_b_artist": "small_points",
        "panel_c_artist": "small_points",
        "panel_d_artist": "small_points",
        "panel_b_plotted_points": len(single_view),
        "panel_c_plotted_points": len(multi_view),
        "panel_d_plotted_points": len(combined_view),
        "llr_xmin": LLR_XMIN,
        "snr_xmin": SNR_XMIN,
        "colorbar_count": 1,
        "colorbar_location": "right",
        "far_point_size": FAR_POINT_SIZE,
        "worker000_panel_a_counts": panel_a.get("counts_ready", {}),
        "worker000_panel_a_counts_selected": panel_a.get("counts_ready_selected", {}),
        "worker000_panel_a_bg_policy": panel_a.get("bg_policy", "latest"),
        "worker000_panel_a_latest_total_window_count_by_ifo": panel_a.get("latest_total_window_count_by_ifo", {}),
        "worker000_panel_a_ready_windows": panel_a.get("ready_windows", []),
        "worker000_panel_a_points_plotted": panel_a.get("points_plotted", 0),
        "worker000_panel_a_points_original": panel_a.get("points_original", 0),
        "worker000_panel_a_segmented_fit": panel_a_fit_summary,
        "worker000_panel_a_fit_display": "tail_fit_only_green_lines",
        "worker000_panel_a_tail_boundary_log10_far": TAIL_BOUNDARY_LOG10_FAR,
        "worker000_panel_a_tail_boundary_source": "fixed_code_constant",
        "background_online_summary": online_summary,
        "panel_a_source": panel_a.get("files", []),
        "caveat": f"Current snapshot. Panel (a) uses worker{panel_a_worker} crashcar C detail/direct-FAR rows with bg_policy={panel_a_policy}; panels b-d aggregate all workers and all bank IDs present in the zerolag XML glob.",
    }


def read_manifest(snr_dir: Path) -> dict:
    path = snr_dir / "manifest.csv"
    if not path.exists():
        return {"exists": False, "path": str(path), "rows": [], "row_count": 0, "ifo_counts": {}}
    rows: list[dict] = []
    counts: Counter[str] = Counter()
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
            counts[row.get("ifo", "")] += 1
    return {"exists": True, "path": str(path), "rows": rows, "row_count": len(rows), "ifo_counts": dict(counts)}


def row_far(row: dict, far_field: str, log_field: str) -> float | None:
    far = finite_positive(row.get(far_field))
    if far is not None:
        return far
    log_far = as_float(row.get(log_field))
    if math.isfinite(log_far):
        return 10.0**log_far
    return None


def select_min_row(rows: list[dict], ifo: str, hit_field: str, far_field: str, log_field: str) -> dict | None:
    candidates = []
    for row in rows:
        if row.get("ifo") != ifo or str(row.get(hit_field, "0")) not in ("1", "1.0", "true", "True"):
            continue
        far = row_far(row, far_field, log_field)
        if far is not None:
            candidates.append((far, row))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def select_snr_rows(manifest_rows: list[dict]) -> dict[str, dict | None]:
    selected: dict[str, dict | None] = {
        "h1_single_min_far": select_min_row(manifest_rows, "H1", "hit_single", "far_sngl", "log10_far_sngl"),
        "l1_single_min_far": select_min_row(manifest_rows, "L1", "hit_single", "far_sngl", "log10_far_sngl"),
        "hl_multi_min_far_h1_component": None,
        "hl_multi_min_far_l1_component": None,
    }
    multi_candidates = []
    for row in manifest_rows:
        if row.get("ifo") not in ("H1", "L1") or str(row.get("hit_multi", "0")) not in ("1", "1.0", "true", "True"):
            continue
        far = row_far(row, "far_multi", "log10_far_multi")
        if far is not None:
            multi_candidates.append((far, row))
    if multi_candidates:
        best = min(multi_candidates, key=lambda item: item[0])[1]
        key_fields = ("event_id", "bankid", "tmplt_idx")
        for ifo in ("H1", "L1"):
            matches = [
                row
                for row in manifest_rows
                if row.get("ifo") == ifo
                and str(row.get("hit_multi", "0")) in ("1", "1.0", "true", "True")
                and all(str(row.get(field, "")) == str(best.get(field, "")) for field in key_fields)
            ]
            if matches:
                selected[f"hl_multi_min_far_{ifo.lower()}_component"] = min(
                    matches,
                    key=lambda row: row_far(row, "far_multi", "log10_far_multi") or math.inf,
                )
    return selected


def read_snr_csv(path: Path) -> dict | None:
    if not path.exists():
        return None
    t: list[float] = []
    y: list[float] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            time = as_float(row.get("relative_time_s", row.get("t")))
            amp = as_float(row.get("abs", row.get("abs_snr")))
            if not math.isfinite(amp):
                real = as_float(row.get("real"))
                imag = as_float(row.get("imag"))
                if math.isfinite(real) and math.isfinite(imag):
                    amp = math.hypot(real, imag)
            if math.isfinite(time) and math.isfinite(amp):
                t.append(time)
                y.append(amp)
    return {"t": t, "abs_snr": y, "source": str(path), "kind": "csv"} if t and y else None


def parse_selected_xml_series(path: Path, ifo: str, event_id: str) -> dict | None:
    if not path.exists():
        return None
    param_re = re.compile(r'<Param Name="([^"]+):param"[^>]*>(.*?)</Param>')
    in_block = False
    in_stream = False
    times: list[float] = []
    real: list[float] = []
    imag: list[float] = []
    params: dict[str, str] = {}
    with path.open("rt", errors="replace") as handle:
        for line in handle:
            if '<LIGO_LW Name="COMPLEX8TimeSeries">' in line:
                in_block = True
                in_stream = False
                times, real, imag, params = [], [], [], {}
                continue
            if not in_block:
                continue
            if "<Stream" in line:
                in_stream = True
                continue
            if in_stream:
                if "</Stream>" in line:
                    in_stream = False
                    continue
                parts = line.strip().split()
                if len(parts) >= 3:
                    try:
                        times.append(float(parts[0]))
                        real.append(float(parts[1]))
                        imag.append(float(parts[2]))
                    except ValueError:
                        pass
                continue
            match = param_re.search(line)
            if match:
                params[match.group(1)] = match.group(2)
                continue
            if "</LIGO_LW>" in line:
                block_event = params.get("crashcar_event_id")
                block_ifo = params.get("instrument")
                if str(block_event) == str(event_id) and str(block_ifo) == str(ifo):
                    return {
                        "t": times,
                        "abs_snr": [math.hypot(r, i) for r, i in zip(real, imag)],
                        "source": str(path),
                        "kind": "xml",
                        "params": params,
                    }
                in_block = False
    return None


def load_series_for_row(snr_dir: Path, row: dict | None) -> dict | None:
    if not row:
        return None
    series_file = row.get("series_file") or row.get("snr_file")
    if series_file:
        result = read_snr_csv(snr_dir / series_file)
        if result:
            return result
    xml_file = row.get("xml_file") or row.get("series_xml")
    if xml_file:
        return parse_selected_xml_series(snr_dir / xml_file, row.get("ifo", ""), row.get("event_id", ""))
    return None


def load_template_curves(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    doc = json.loads(path.read_text())
    curves = {}
    for curve in doc.get("curves", []):
        panel = curve.get("panel")
        if not panel:
            continue
        curves[panel] = {
            "relative_time_s": [as_float(v) for v in curve.get("relative_time_s", [])],
            "autocorr_abs": [as_float(v) for v in curve.get("autocorr_abs", [])],
        }
    return curves


def scaled_template_curve(panel_key: str, row: dict, curves: dict[str, dict], data_y: list[float]) -> tuple[list[float], list[float]] | None:
    curve = curves.get(panel_key)
    if not curve:
        return None
    times = np.asarray(curve["relative_time_s"], dtype=float)
    amps = np.asarray(curve["autocorr_abs"], dtype=float)
    mask = np.isfinite(times) & np.isfinite(amps) & (amps >= 0)
    times = times[mask]
    amps = amps[mask]
    if times.size == 0 or amps.size == 0 or np.nanmax(amps) <= 0:
        return None
    scale = finite_positive(row.get("snglsnr"))
    if scale is None and data_y:
        scale = max(data_y)
    if scale is None or scale <= 0:
        return None
    return times.tolist(), (amps / np.nanmax(amps) * scale).tolist()


def plot_series_panel(ax, panel_key: str, title: str, row: dict | None, series: dict | None, template_curves: dict[str, dict]) -> dict:
    if not row or not series:
        ax.set_facecolor("white")
        ax.text(0.5, 0.54, "SNR-series manifest/XML shard not available yet", ha="center", va="center", transform=ax.transAxes, fontsize=11)
        ax.text(0.5, 0.44, "No replacement curve synthesized", ha="center", va="center", transform=ax.transAxes, fontsize=9, color="0.4")
        ax.set_title(title, fontweight="bold")
        ax.set_xticks([])
        ax.set_yticks([])
        return {"available": False, "reason": "missing selected manifest row or series file"}

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
    template = scaled_template_curve(panel_key, row, template_curves, y.tolist())
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
    far_bits = []
    for label, key in (("single", "log10_far_sngl"), ("multi", "log10_far_multi")):
        value = as_float(row.get(key))
        if math.isfinite(value):
            far_bits.append(f"{label}={value:.2f}")
    ax.set_title(title + ("\n" + ", ".join(far_bits) if far_bits else ""), fontweight="bold", fontsize=10)
    ax.set_xlabel("time from local |SNR| peak (s)")
    ax.set_ylabel("|SNR|")
    ax.set_ylim(SNR_XMIN, max(SNR_XMIN + 1.0, snr_ymax * 1.06))
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    return {
        "available": True,
        "event_id": row.get("event_id"),
        "ifo": row.get("ifo"),
        "series_source": series.get("source"),
        "series_kind": series.get("kind"),
        "samples": int(t.size),
        "template_autocorr": autocorr,
    }


def plot_second_2x2(run_root: Path, output: Path, title: str, template_autocorr: Path | None) -> dict:
    snr_dir = run_root / "run" / "crashcar_snr_series"
    if not snr_dir.exists():
        snr_dir = run_root / "crashcar_snr_series"
    manifest = read_manifest(snr_dir)
    selections = select_snr_rows(manifest["rows"]) if manifest["exists"] else {
        "h1_single_min_far": None,
        "l1_single_min_far": None,
        "hl_multi_min_far_h1_component": None,
        "hl_multi_min_far_l1_component": None,
    }
    series = {key: load_series_for_row(snr_dir, row) for key, row in selections.items()}
    template_curves = load_template_curves(template_autocorr)

    fig, axes = plt.subplots(2, 2, figsize=(16.5, 11.5), constrained_layout=True)
    fig.suptitle(f"{title}: retained SNR series", fontsize=17, fontweight="bold")
    panels = {
        "a": plot_series_panel(axes[0, 0], "single_H1", "Panel (a): H1 single-detector selection", selections.get("h1_single_min_far"), series.get("h1_single_min_far"), template_curves),
        "b": plot_series_panel(axes[0, 1], "single_L1", "Panel (b): L1 single-detector selection", selections.get("l1_single_min_far"), series.get("l1_single_min_far"), template_curves),
        "c": plot_series_panel(axes[1, 0], "multi_H1", "Panel (c): H1 component of H/L multi selection", selections.get("hl_multi_min_far_h1_component"), series.get("hl_multi_min_far_h1_component"), template_curves),
        "d": plot_series_panel(axes[1, 1], "multi_L1", "Panel (d): L1 component of H/L multi selection", selections.get("hl_multi_min_far_l1_component"), series.get("hl_multi_min_far_l1_component"), template_curves),
    }
    if not manifest["exists"]:
        fig.text(0.01, 0.01, "Current snapshot from crashcar_snr_series; manifest.csv is not present.", fontsize=9, color="0.35")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return {
        "plot": str(output),
        "manifest_exists": manifest["exists"],
        "manifest_path": manifest["path"],
        "manifest_rows": manifest["row_count"],
        "manifest_ifo_counts": manifest["ifo_counts"],
        "panels": panels,
        "caveat": "Missing retained SNR-series panels are left blank; no replacement curve is synthesized.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path, help="Run root containing run/, controller/, artifacts/.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory; default is RUN_ROOT/figures.")
    parser.add_argument("--run-label", default=None, help="Filename/title stem; default is run root name.")
    parser.add_argument("--stamp", default=None, help="Timestamp suffix; default is current UTC.")
    parser.add_argument("--zerolag-glob", default="run/[0-9][0-9][0-9]/*_zerolag_*.xml.gz")
    parser.add_argument("--detail-glob", default="run/crashcar_singlefar_detail_worker*.csv")
    parser.add_argument("--segment-glob", default=DEFAULT_SEGMENT_GLOB)
    parser.add_argument("--panel-a-worker", default="000")
    parser.add_argument("--ifo-id-map", default="0:H1,1:L1,2:V1,3:K1")
    parser.add_argument("--single-far-priority", default=",".join(DEFAULT_SINGLE_FAR_BASES))
    parser.add_argument("--coherent-far-priority", default=",".join(DEFAULT_COHERENT_FAR_BASES))
    parser.add_argument("--background-accumulation-seconds", type=float, default=10800.0, help="BG accumulation window used for panel (a) H/L online fractions.")
    parser.add_argument("--tail-boundary-log10-far", type=float, default=TAIL_BOUNDARY_LOG10_FAR, help=argparse.SUPPRESS)
    parser.add_argument("--max-panel-a-points", type=int, default=0, help="0 means plot all worker detail support points.")
    parser.add_argument("--panel-a-bg-policy", choices=("latest", "all"), default="latest", help="Panel (a) defaults to the latest worker BG support per IFO; use all to debug historical BG updates.")
    parser.add_argument("--template-autocorr-json", type=Path, default=None)
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    output_dir = (args.output_dir or (run_root / "figures")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = args.run_label or run_root.name
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_")

    single_far_bases = parse_csv_list(args.single_far_priority)
    coherent_far_bases = parse_csv_list(args.coherent_far_priority)
    ifo_id_map = parse_ifo_id_map(args.ifo_id_map)

    zerolag = load_zerolag(run_root, args.zerolag_glob)
    panel_a = load_panel_a_detail(run_root, args.detail_glob, args.panel_a_worker, ifo_id_map, args.max_panel_a_points, args.panel_a_bg_policy)
    background_window = infer_background_window(zerolag, args.background_accumulation_seconds)
    online_summary = load_online_summary(run_root, args.segment_glob, background_window)
    first_payload = {
        "zerolag": zerolag,
        "panel_a": panel_a,
        "single_points": build_single_points(zerolag["rows"], single_far_bases),
        "multi_points": build_multi_points(zerolag["rows"], coherent_far_bases),
        "online_summary": online_summary,
    }

    first_plot = output_dir / f"{safe_label}_first_2x2_zerolag_current_{stamp}.png"
    second_plot = output_dir / f"{safe_label}_second_2x2_snr_current_{stamp}.png"
    first = plot_first_2x2(first_payload, first_plot, label, TAIL_BOUNDARY_LOG10_FAR)
    second = plot_second_2x2(run_root, second_plot, label, args.template_autocorr_json)

    meta = {
        "created_utc": stamp,
        "run_root": str(run_root),
        "run_label": label,
        "note_contract": "Eric-bless-crashcar.pdf Section 4.1 two crashcar 2x2 plotting rules",
        "parameters": {
            "zerolag_glob": args.zerolag_glob,
            "detail_glob": args.detail_glob,
            "segment_glob": args.segment_glob,
            "panel_a_worker": args.panel_a_worker,
            "ifo_id_map": ifo_id_map,
            "single_far_priority": single_far_bases,
            "coherent_far_priority": coherent_far_bases,
            "background_accumulation_seconds": args.background_accumulation_seconds,
            "tail_boundary_log10_far": TAIL_BOUNDARY_LOG10_FAR,
            "tail_boundary_source": "fixed_code_constant",
            "max_panel_a_points": args.max_panel_a_points,
            "panel_a_bg_policy": args.panel_a_bg_policy,
            "template_autocorr_json": str(args.template_autocorr_json) if args.template_autocorr_json else None,
        },
        "first": first,
        "second": second,
    }
    meta_path = output_dir / f"{safe_label}_two_2x2_current_{stamp}.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"first": first["plot"], "second": second["plot"], "meta": str(meta_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
