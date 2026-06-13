#!/usr/bin/env python3
"""Plot final single/multi/combined FAR heat maps for an Eric-super-spiir run."""

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
    point_sets = [points for points in point_sets if points.count]
    if not point_sets:
        return PointSet(label, np.array([]), np.array([]), np.array([]))
    return PointSet(
        label,
        np.concatenate([points.x for points in point_sets]),
        np.concatenate([points.y for points in point_sets]),
        np.concatenate([points.z for points in point_sets]),
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
        valid_z = points.z[mask]
        flat[np.unique(flat_index)] = np.inf
        np.minimum.at(flat, flat_index, valid_z)
        grid = flat.reshape((bins_x, bins_y))
        grid[~np.isfinite(grid)] = np.nan
    return xedges, yedges, grid


def draw_heatmaps(
    *,
    single: PointSet,
    multi: PointSet,
    combined: PointSet,
    output: str,
    summary: str | None,
    source_label: str,
    bins_x: int,
    bins_y: int,
) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xlim = quantile_limits(combined.x)
    ylim = quantile_limits(combined.y)
    zfinite = combined.z[np.isfinite(combined.z)]
    if zfinite.size:
        vmin, vmax = np.quantile(zfinite, [0.01, 0.99])
    else:
        vmin, vmax = -1.0, 1.0
    if vmin == vmax:
        vmin -= 0.5
        vmax += 0.5

    fig, axes = plt.subplots(1, 3, figsize=(21, 6.6), constrained_layout=False, sharex=True, sharey=True)
    fig.suptitle("Final result: single, multi, and combined FAR heat maps", fontsize=18, fontweight="bold")

    panels = [
        ("Single only", single, "single-detector rows"),
        ("Multi only", multi, "coherent/postcoh rows"),
        ("Combined", combined, "best branch FAR"),
    ]
    last_mesh = None
    for ax, (title, points, subtitle) in zip(axes, panels):
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
        ax.set_title(f"{title}\n{points.count:,} rows; cell = {subtitle}", fontsize=12)
        ax.grid(True, color="0.85", linewidth=0.5)
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.tick_params(labelsize=9)

    axes[0].set_ylabel("chi-square (single: chisq, multi: cmbchisq)", fontsize=12)
    for ax in axes:
        ax.set_xlabel("SNR (single: rho, multi: cohsnr)", fontsize=12)

    if last_mesh is not None:
        cbar_ax = fig.add_axes([0.91, 0.16, 0.018, 0.66])
        cbar = fig.colorbar(last_mesh, cax=cbar_ax)
        cbar.set_label("log10(FAR); lower is more significant", fontsize=11)

    fig.text(
        0.01,
        0.015,
        f"Source: {source_label}. SNR and chi-square axes are linear; cell color is the lowest log10(FAR) in each bin.",
        fontsize=9,
        color="0.35",
    )
    fig.subplots_adjust(left=0.055, right=0.895, bottom=0.13, top=0.84, wspace=0.08)

    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    fig.savefig(output, dpi=170)
    plt.close(fig)

    result = {
        "output": output,
        "single_rows": single.count,
        "multi_rows": multi.count,
        "combined_rows": combined.count,
        "bins_x": bins_x,
        "bins_y": bins_y,
        "xlim": xlim,
        "ylim": ylim,
        "zlim": (float(vmin), float(vmax)),
        "x_axis": "raw_snr",
        "y_axis": "raw_chisq",
        "z_axis": "log10_far",
        "single_far_field_preference": ["assigned_far", "far"],
        "multi_far_field_preference": ["far"],
        "source_label": source_label,
    }
    if summary:
        os.makedirs(os.path.dirname(os.path.abspath(summary)), exist_ok=True)
        with open(summary, "w") as summary_file:
            json.dump(result, summary_file, indent=2, sort_keys=True)
            summary_file.write("\n")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", default=None, help="run directory containing single_branch/")
    parser.add_argument("--single", default=None, help="single_final_far_all.csv")
    parser.add_argument("--features", default=None, help="zerolag_features.csv")
    parser.add_argument("--output", default=None, help="output PNG")
    parser.add_argument("--summary", default=None, help="optional JSON summary")
    parser.add_argument("--bins-x", type=int, default=150)
    parser.add_argument("--bins-y", type=int, default=120)
    parser.add_argument("--source-label", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.run_dir
    single_file = args.single or (os.path.join(run_dir, "single_branch/single_final_far_all.csv") if run_dir else None)
    feature_file = args.features or (os.path.join(run_dir, "single_branch/zerolag_features.csv") if run_dir else None)
    output = args.output or (os.path.join(run_dir, "single_branch/final_result_single_multi_three_panel_far_heatmap.png") if run_dir else None)
    summary = args.summary or (os.path.splitext(output)[0] + ".json" if output else None)
    if not single_file or not feature_file or not output:
        raise SystemExit("Provide --run-dir or explicit --single, --features, and --output.")

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
    result = draw_heatmaps(
        single=single,
        multi=multi,
        combined=combined,
        output=output,
        summary=summary,
        source_label=source_label,
        bins_x=args.bins_x,
        bins_y=args.bins_y,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
