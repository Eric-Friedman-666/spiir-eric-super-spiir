#!/usr/bin/env python3
"""Plot a 2x2 final summary: background plus single/multi/combined FAR maps."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass
class PointSet:
    label: str
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray

    @property
    def count(self) -> int:
        return int(self.x.size)


def as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def log10_positive(value: str | None) -> float | None:
    number = as_float(value)
    if number is None or number <= 0.0:
        return None
    return math.log10(number)


def positive_float(value: str | None) -> float | None:
    number = as_float(value)
    if number is None or number <= 0.0:
        return None
    return number


def read_csv_points(
    filename: str,
    *,
    label: str,
    snr_field: str,
    chisq_field: str,
    far_fields: tuple[str, ...],
) -> PointSet:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    with open(filename, newline="") as input_file:
        for row in csv.DictReader(input_file):
            x = positive_float(row.get(snr_field))
            y = positive_float(row.get(chisq_field))
            far_value = next(
                (row.get(field) for field in far_fields
                 if row.get(field) not in (None, "")),
                None,
            )
            z = log10_positive(far_value)
            if x is None or y is None or z is None:
                continue
            xs.append(x)
            ys.append(y)
            zs.append(z)
    return PointSet(label, np.asarray(xs), np.asarray(ys), np.asarray(zs))


def combine_point_sets(label: str, point_sets: Iterable[PointSet]) -> PointSet:
    usable = [points for points in point_sets if points.count]
    if not usable:
        return PointSet(label, np.array([]), np.array([]), np.array([]))
    return PointSet(
        label,
        np.concatenate([points.x for points in usable]),
        np.concatenate([points.y for points in usable]),
        np.concatenate([points.z for points in usable]),
    )


def quantile_limits(values: np.ndarray, low: float = 0.001, high: float = 0.999) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    lo, hi = np.quantile(finite, [low, high])
    if not math.isfinite(lo) or not math.isfinite(hi) or lo == hi:
        lo = float(np.min(finite))
        hi = float(np.max(finite))
    if lo == hi:
        lo -= 0.5
        hi += 0.5
    margin = 0.03 * (hi - lo)
    return float(lo - margin), float(hi + margin)


def min_heatmap(
    points: PointSet,
    *,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    bins_x: int,
    bins_y: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xedges = np.linspace(xlim[0], xlim[1], bins_x + 1)
    yedges = np.linspace(ylim[0], ylim[1], bins_y + 1)
    grid = np.full((bins_x, bins_y), np.nan)
    if points.count == 0:
        return xedges, yedges, grid

    xi = np.searchsorted(xedges, points.x, side="right") - 1
    yi = np.searchsorted(yedges, points.y, side="right") - 1
    mask = (
        (xi >= 0)
        & (xi < bins_x)
        & (yi >= 0)
        & (yi < bins_y)
        & np.isfinite(points.z)
    )
    if np.any(mask):
        flat = grid.ravel()
        flat_index = xi[mask] * bins_y + yi[mask]
        flat[np.unique(flat_index)] = np.inf
        np.minimum.at(flat, flat_index, points.z[mask])
        grid = flat.reshape((bins_x, bins_y))
        grid[~np.isfinite(grid)] = np.nan
    return xedges, yedges, grid


def load_json(filename: str | None) -> dict:
    if not filename or not os.path.exists(filename):
        return {}
    with open(filename) as input_file:
        return json.load(input_file)


def draw_summary(
    *,
    background_png: str,
    single: PointSet,
    multi: PointSet,
    combined: PointSet,
    output: str,
    summary: str | None,
    source_label: str,
    status: dict,
    background_status: dict,
    bins_x: int,
    bins_y: int,
) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xlim = quantile_limits(combined.x)
    ylim = quantile_limits(combined.y)
    zfinite = combined.z[np.isfinite(combined.z)]
    vmin, vmax = (-1.0, 1.0)
    if zfinite.size:
        vmin, vmax = np.quantile(zfinite, [0.01, 0.99])
        if vmin == vmax:
            vmin -= 0.5
            vmax += 0.5

    fig, axes = plt.subplots(2, 2, figsize=(17.5, 13.0), constrained_layout=False)
    fig.suptitle("Final result summary: background and FAR heat maps", fontsize=20, fontweight="bold")

    background_ax = axes[0, 0]
    background_image = plt.imread(background_png)
    background_ax.imshow(background_image)
    background_ax.set_title("Background FAR-LLR fit", fontsize=14, fontweight="bold")
    background_ax.axis("off")

    heat_axes = [
        (axes[0, 1], "Single only", single, "single-detector rows"),
        (axes[1, 0], "Multi only", multi, "coherent/postcoh rows"),
        (axes[1, 1], "Combined", combined, "best FAR from either branch"),
    ]
    last_mesh = None
    for ax, title, points, subtitle in heat_axes:
        xedges, yedges, grid = min_heatmap(points, xlim=xlim, ylim=ylim, bins_x=bins_x, bins_y=bins_y)
        last_mesh = ax.pcolormesh(
            xedges,
            yedges,
            grid.T,
            cmap="YlGnBu_r",
            shading="auto",
            vmin=vmin,
            vmax=vmax,
        )
        ax.set_title(f"{title}\n{points.count:,} rows; cell = {subtitle}", fontsize=14, fontweight="bold")
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.grid(True, color="0.85", linewidth=0.5)
        ax.tick_params(labelsize=10)

    axes[0, 1].set_xlabel("SNR (rho)", fontsize=12)
    axes[1, 0].set_xlabel("SNR (cohsnr)", fontsize=12)
    axes[1, 1].set_xlabel("SNR (single rho / multi cohsnr)", fontsize=12)
    axes[0, 1].set_ylabel("chi-square (chisq)", fontsize=12)
    axes[1, 0].set_ylabel("chi-square (cmbchisq)", fontsize=12)
    axes[1, 1].set_ylabel("chi-square", fontsize=12)

    if last_mesh is not None:
        cbar_ax = fig.add_axes([0.925, 0.18, 0.018, 0.62])
        cbar = fig.colorbar(last_mesh, cax=cbar_ax)
        cbar.set_label("log10(FAR); lower is more significant", fontsize=12)

    detail_bits = []
    if status:
        detail_bits.append(
            "injected GPS %s (%.2f h)"
            % (
                status.get("current_injected_gps", "unknown"),
                float(status.get("current_injected_duration_hours", 0.0) or 0.0),
            )
        )
        detail_bits.append("group %s" % status.get("current_bank_group", "unknown"))
    if background_status:
        detail_bits.append(
            "background %.2f h; support %s"
            % (
                float(background_status.get("duration_hours", 0.0) or 0.0),
                f"{int(background_status.get('support_points', 0) or 0):,}",
            )
        )
    detail_text = "; ".join(detail_bits)
    if detail_text:
        detail_text = " Current snapshot: " + detail_text + "."
    fig.text(
        0.02,
        0.02,
        f"Source: {source_label}.{detail_text} SNR and chi-square axes are linear; cell color is the lowest log10(FAR) in each bin.",
        fontsize=10,
        color="0.35",
    )
    fig.subplots_adjust(left=0.07, right=0.905, bottom=0.08, top=0.91, wspace=0.20, hspace=0.24)

    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    fig.savefig(output, dpi=170)
    plt.close(fig)

    result = {
        "output": output,
        "background_png": background_png,
        "single_rows": single.count,
        "multi_rows": multi.count,
        "combined_rows": combined.count,
        "bins_x": bins_x,
        "bins_y": bins_y,
        "x_axis": "raw_snr",
        "y_axis": "raw_chisq",
        "z_axis": "log10_far",
        "single_far_field_preference": ["assigned_far", "far"],
        "multi_far_field_preference": ["far"],
        "xlim": xlim,
        "ylim": ylim,
        "zlim": (float(vmin), float(vmax)),
        "source_label": source_label,
    }
    if summary:
        os.makedirs(os.path.dirname(os.path.abspath(summary)), exist_ok=True)
        with open(summary, "w") as output_file:
            json.dump(result, output_file, indent=2, sort_keys=True)
            output_file.write("\n")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", default=None, help="run directory containing single_branch/")
    parser.add_argument("--background", default=None, help="single_llr_far_background.png")
    parser.add_argument("--single", default=None, help="single_final_far_all.csv")
    parser.add_argument("--features", default=None, help="zerolag_features.csv")
    parser.add_argument("--status", default=None, help="optional realtime_status.json")
    parser.add_argument("--background-status", default=None, help="optional latest_single_background_status.json")
    parser.add_argument("--output", default=None, help="output PNG")
    parser.add_argument("--summary", default=None, help="optional JSON summary")
    parser.add_argument("--bins-x", type=int, default=150)
    parser.add_argument("--bins-y", type=int, default=120)
    parser.add_argument("--source-label", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.run_dir
    background = args.background or (os.path.join(run_dir, "single_branch/single_llr_far_background.png") if run_dir else None)
    single_file = args.single or (os.path.join(run_dir, "single_branch/single_final_far_all.csv") if run_dir else None)
    feature_file = args.features or (os.path.join(run_dir, "single_branch/zerolag_features.csv") if run_dir else None)
    status_file = args.status or (os.path.join(run_dir, "monitor/realtime_status.json") if run_dir else None)
    background_status_file = args.background_status or (os.path.join(run_dir, "monitor/latest_single_background_status.json") if run_dir else None)
    output = args.output or (os.path.join(run_dir, "single_branch/final_result_four_panel_summary.png") if run_dir else None)
    summary = args.summary or (os.path.splitext(output)[0] + ".json" if output else None)
    if not background or not single_file or not feature_file or not output:
        raise SystemExit("Provide --run-dir or explicit --background, --single, --features, and --output.")

    single = read_csv_points(
        single_file,
        label="single",
        snr_field="rho",
        chisq_field="chisq",
        far_fields=("assigned_far", "far"),
    )
    multi = read_csv_points(
        feature_file,
        label="multi",
        snr_field="cohsnr",
        chisq_field="cmbchisq",
        far_fields=("far",),
    )
    combined = combine_point_sets("combined", [single, multi])
    source_label = args.source_label or (run_dir if run_dir else os.path.commonpath([single_file, feature_file]))
    result = draw_summary(
        background_png=background,
        single=single,
        multi=multi,
        combined=combined,
        output=output,
        summary=summary,
        source_label=source_label,
        status=load_json(status_file),
        background_status=load_json(background_status_file),
        bins_x=args.bins_x,
        bins_y=args.bins_y,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
