#!/usr/bin/env python3
"""Plot the single-detector FAR-LLR background file."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
from pathlib import Path

try:
    from single_detector_far import RankBackground
except Exception:
    RankBackground = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", required=True)
    parser.add_argument("--assigned", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--llr-min", type=float, default=-10.0)
    parser.add_argument("--h1-tail-start", type=float, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--l1-tail-start", type=float, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument(
        "--tail-log10-far", type=float, default=-2.5,
        help="Wguo-style tail boundary: choose the LLR where empirical "
             "log10(FAR) is closest to this value, then fit the tail "
             "constrained through that boundary.")
    return parser.parse_args()


def safe_log10(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x <= 0.0:
        return None
    return math.log10(x)


def safe_float(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def load_support(background_file):
    data = json.loads(Path(background_file).read_text())
    points = []
    for ifo, bg in data.get("backgrounds", {}).items():
        support_rows = bg.get("far_llr_points", [])
        if bg.get("background_triggers") and RankBackground is not None:
            try:
                support_rows = RankBackground.from_dict(bg).current_far_llr_points()
            except Exception:
                support_rows = bg.get("far_llr_points", [])
        for point in support_rows:
            llr = safe_float(point.get("llr"))
            log_far = safe_log10(point.get("far"))
            if llr is None or log_far is None:
                continue
            points.append((ifo, llr, log_far, point.get("llr"), point.get("far")))
    return data, points


def load_assigned(filename):
    rows = []
    path = Path(filename)
    if not path.exists():
        return rows
    with path.open(newline="") as input_file:
        reader = csv.DictReader(input_file)
        for row in reader:
            llr = safe_float(row.get("rank") or row.get("llr"))
            log_far = safe_log10(row.get("far"))
            if llr is None or log_far is None:
                continue
            rows.append((row.get("ifo") or row.get("category") or "", llr, log_far))
    return rows


def median(values):
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return 0.5 * (values[mid - 1] + values[mid])


def linear_fit(points):
    if len(points) < 2:
        return None, None
    n = float(len(points))
    sx = sum(point[0] for point in points)
    sy = sum(point[1] for point in points)
    sxx = sum(point[0] * point[0] for point in points)
    sxy = sum(point[0] * point[1] for point in points)
    denom = n * sxx - sx * sx
    if denom == 0.0:
        return None, None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def fit_line_through_fixed_point(points, x0, y0):
    """Fit y=a*x+b constrained through the point (x0, y0).

    This mirrors Wguo's FAR extrapolation helper: write the fit as
    y - y0 = a * (x - x0), then solve the one-parameter least-squares problem.
    """

    if len(points) < 2:
        return None, None
    denom = sum((x - x0) * (x - x0) for x, _y in points)
    if denom <= 0.0:
        return None, None
    slope = sum((x - x0) * (y - y0) for x, y in points) / denom
    if not math.isfinite(slope):
        return None, None
    intercept = y0 - slope * x0
    return slope, intercept


def far_boundary_from_log10(points, boundary_log10_far):
    """Return the LLR whose empirical log10(FAR) is closest to boundary."""

    raw = sorted((float(x), float(y)) for x, y in points)
    if len(raw) < 2:
        return None
    return min(raw, key=lambda item: abs(item[1] - boundary_log10_far))


def fit_tail_line(points, boundary_log10_far):
    """Wguo-style constrained tail fit chosen by FAR boundary, not fixed LLR."""

    raw = sorted((float(x), float(y)) for x, y in points)
    if len(raw) < 2:
        return None
    boundary = far_boundary_from_log10(raw, float(boundary_log10_far))
    if boundary is None:
        return None
    tail_start, empirical_boundary_log10_far = boundary
    y0 = float(boundary_log10_far)
    tail = [(x, y) for x, y in raw if x >= tail_start]
    if len(tail) < 2:
        return None
    slope, intercept = fit_line_through_fixed_point(tail, tail_start, y0)
    if not math.isfinite(slope) or slope >= 0.0:
        return None
    return {
        "tail_from": float(tail_start),
        "tail_slope": slope,
        "tail_intercept": intercept,
        "tail_points": len(tail),
        "boundary_log10_far": y0,
        "empirical_boundary_log10_far": empirical_boundary_log10_far,
        "x_max": max(x for x, _y in raw),
    }


def monotonic_log_fars(values):
    running = None
    output = []
    for value in values:
        running = value if running is None else min(running, value)
        output.append(running)
    return output


def robust_curve_for_plot(background, points, boundary_log10_far):
    """Build the same display shape used by RankBackground.fitted_far.

    The function name is kept for compatibility with existing plot scripts.
    Production tail clipping is disabled; ``tail_kept`` now means the all-point
    tail support used for the displayed fit and ``tail_rejected`` is empty.
    """

    raw = sorted((float(point[0]), float(point[1])) for point in points)
    if len(raw) < 2 or RankBackground is None:
        return {
            "raw": raw,
            "before_tail": [],
            "tail_kept": [],
            "tail_rejected": [],
            "tail_line": None,
            "metadata": fit_tail_line(raw, boundary_log10_far),
        }

    raw_xs = []
    raw_log_fars = []
    for llr, log_far in raw:
        if raw_xs and llr == raw_xs[-1]:
            raw_log_fars[-1] = min(raw_log_fars[-1], log_far)
        else:
            raw_xs.append(llr)
            raw_log_fars.append(log_far)
    raw_monotonic = monotonic_log_fars(raw_log_fars)

    tail_far = safe_float(background.get("far_fit_boundary")) if background else None
    if tail_far is None or tail_far <= 0.0:
        tail_far = math.pow(10.0, float(boundary_log10_far))
    tail_log_far = math.log10(tail_far)

    tail_idx = min(
        range(len(raw_xs)),
        key=lambda idx: abs(raw_monotonic[idx] - tail_log_far))
    x_tail = raw_xs[tail_idx]

    if RankBackground is not None:
        try:
            bg = RankBackground.from_dict(background)
            fit = bg._fitted_log10_far_curve()
        except Exception:
            fit = None
        if fit is not None:
            fit_xs, fit_log_fars, slope, intercept = fit
            before_tail = [
                (x, y) for x, y in zip(fit_xs, fit_log_fars)
                if x <= x_tail
            ]
            if not before_tail or before_tail[-1][0] != x_tail:
                before_tail.append((x_tail, tail_log_far))

            tail_raw = [
                (x, y) for x, y in zip(raw_xs, raw_monotonic)
                if x >= x_tail
            ]
            min_tail_points = max(2, min(
                int(background.get("fit_min_points", 20) or 20),
                20,
                len(tail_raw)))
            tail_kept = []
            if slope is not None and intercept is not None and len(tail_raw) >= min_tail_points:
                tail_kept = list(tail_raw)
            tail_rejected = []
            tail_line = None
            if slope is not None and intercept is not None:
                tail_line = (
                    (x_tail, slope * x_tail + intercept),
                    (raw_xs[-1], slope * raw_xs[-1] + intercept),
                )
            return {
                "raw": list(zip(raw_xs, raw_monotonic)),
                "before_tail": before_tail,
                "tail_kept": tail_kept,
                "tail_rejected": tail_rejected,
                "tail_line": tail_line,
                "metadata": {
                    "tail_from": x_tail,
                    "tail_slope": slope,
                    "tail_intercept": intercept,
                    "tail_points": len(tail_raw),
                    "tail_kept": len(tail_kept),
                    "tail_rejected": len(tail_rejected),
                    "tail_clipping": "disabled",
                    "boundary_log10_far": tail_log_far,
                    "before_tail_from": raw_xs[0],
                    "x_max": raw_xs[-1],
                    "fit_source": "RankBackground._fitted_log10_far_curve",
                },
            }

    before_tail = RankBackground._smooth_before_tail_curve(
        raw_xs, raw_monotonic, raw_xs[0], x_tail)
    if before_tail:
        pxs = [point[0] for point in before_tail]
        pys = [point[1] for point in before_tail]
        pys[-1] = tail_log_far
        pys = RankBackground._monotonic_log_fars(pys)
        before_tail = list(zip(pxs, pys))

    tail_raw = [
        (x, y) for x, y in zip(raw_xs, raw_monotonic)
        if x >= x_tail
    ]
    tail_bins = RankBackground._binned_curve_by_x(tail_raw, 80)
    fit_tail_points = [(x, y) for x, y in tail_bins if x > x_tail]
    min_tail_points = max(2, min(
        int(background.get("fit_min_points", 20) or 20),
        20,
        len(fit_tail_points)))
    kept = []
    slope = intercept = None
    if len(fit_tail_points) >= min_tail_points:
        kept = list(fit_tail_points)
        slope, intercept = RankBackground._fit_line_through_fixed_point(
            kept, x_tail, tail_log_far)

    kept_set = set((round(x, 12), round(y, 12)) for x, y in kept)
    tail_kept = []
    tail_rejected = []
    for point in fit_tail_points:
        key = (round(point[0], 12), round(point[1], 12))
        if key in kept_set:
            tail_kept.append(point)
        else:
            tail_rejected.append(point)

    tail_line = None
    if slope is not None and intercept is not None:
        tail_line = (
            (x_tail, slope * x_tail + intercept),
            (raw_xs[-1], slope * raw_xs[-1] + intercept),
        )

    return {
        "raw": list(zip(raw_xs, raw_monotonic)),
        "before_tail": before_tail,
        "tail_kept": tail_kept,
        "tail_rejected": tail_rejected,
        "tail_line": tail_line,
        "metadata": {
            "tail_from": x_tail,
            "tail_slope": slope,
            "tail_intercept": intercept,
            "tail_points": len(fit_tail_points),
            "tail_kept": len(tail_kept),
            "tail_rejected": len(tail_rejected),
            "tail_clipping": "disabled",
            "boundary_log10_far": tail_log_far,
            "before_tail_from": raw_xs[0],
            "x_max": raw_xs[-1],
        },
    }


def interpolate(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo = 0
    hi = len(xs) - 1
    while hi - lo > 1:
        mid = (hi + lo) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    if xs[hi] == xs[lo]:
        return min(ys[lo], ys[hi])
    weight = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + weight * (ys[hi] - ys[lo])


def write_dat_files(output_png, support, assigned, boundary_log10_far):
    stem = Path(output_png).with_suffix("")
    support_files = {}
    tail_files = {}
    fit_metadata = {}
    for ifo in sorted(set(p[0] for p in support)):
        path = stem.parent / f"{stem.name}_{ifo}_support.dat"
        ifo_support = sorted(
            (p for p in support if p[0] == ifo), key=lambda item: item[1])
        with path.open("w") as output_file:
            for _, llr, log_far, _, _ in ifo_support:
                output_file.write(f"{llr:.12g} {log_far:.12g}\n")
        support_files[ifo] = path
        metadata = fit_tail_line(
            [(p[1], p[2]) for p in ifo_support],
            boundary_log10_far)
        fit_metadata[ifo] = metadata
        if metadata is not None:
            tail_path = stem.parent / f"{stem.name}_{ifo}_tail.dat"
            x_tail = metadata["tail_from"]
            x_max = metadata["x_max"]
            slope = metadata["tail_slope"]
            intercept = metadata["tail_intercept"]
            with tail_path.open("w") as output_file:
                output_file.write(f"{x_tail:.12g} {slope * x_tail + intercept:.12g}\n")
                output_file.write(f"{x_max:.12g} {slope * x_max + intercept:.12g}\n")
            tail_files[ifo] = tail_path
    assigned_path = stem.parent / f"{stem.name}_assigned.dat"
    with assigned_path.open("w") as output_file:
        for _, llr, log_far in assigned:
            output_file.write(f"{llr:.12g} {log_far:.12g}\n")
    return support_files, tail_files, fit_metadata, assigned_path


def plot_with_gnuplot(output_png, support, assigned, boundary_log10_far):
    if shutil.which("gnuplot") is None:
        raise RuntimeError("neither matplotlib nor gnuplot is available")
    support_files, tail_files, fit_metadata, assigned_path = write_dat_files(
        output_png, support, assigned, boundary_log10_far)
    stem = Path(output_png).with_suffix("")
    script = stem.parent / f"{stem.name}.gp"
    plots = []
    palette = {"H1": "#2563eb", "L1": "#7c3aed"}
    for ifo, path in support_files.items():
        color = palette.get(ifo, "#64748b")
        plots.append(
            f"'{path}' using 1:2 with points pt 7 ps 0.35 lc rgb '{color}' "
            f"title '{ifo} background support'")
    for ifo, path in tail_files.items():
        metadata = fit_metadata.get(ifo) or {}
        slope = metadata.get("tail_slope")
        intercept = metadata.get("tail_intercept")
        if slope is None or intercept is None:
            title = f"{ifo} tail"
        else:
            title = f"{ifo} tail: y={slope:.3f}x+{intercept:.3f}"
        plots.append(
            f"'{path}' using 1:2 with lines lw 2 dt 2 lc rgb '#f59e0b' "
            f"title '{title}'")
    # The diagnostic plot is for the FAR-LLR background calibration itself.
    # Assigned foreground/holdout rows are counted in the JSON summary, but not
    # drawn, otherwise they visually obscure the blue background support curve.
    script.write_text(
        "\n".join([
            "set terminal pngcairo size 1280,880 enhanced font 'Arial,14'",
            f"set output '{output_png}'",
            "set title 'Single-detector FAR-LLR background'",
            "set xlabel 'LLR'",
            "set ylabel 'log10(FAR)'",
            "set grid",
            "set key left bottom",
            "plot " + ", \\\n+     ".join(plots),
            "",
        ]))
    subprocess.check_call(["gnuplot", str(script)])
    return fit_metadata


def main() -> int:
    args = parse_args()
    data, support = load_support(args.background)
    assigned = load_assigned(args.assigned)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)

    plot_error = None
    fit_metadata = {}
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        plot_error = str(exc)
        fit_metadata = plot_with_gnuplot(
            args.output, support, assigned, args.tail_log10_far)
    else:
        ifos = sorted(set(p[0] for p in support))
        fig, axes = plt.subplots(
            1, max(1, len(ifos)), figsize=(7.6 * max(1, len(ifos)), 5.6),
            dpi=160, squeeze=False)
        axes = list(axes[0])
        for ax, ifo in zip(axes, ifos):
            background = data.get("backgrounds", {}).get(ifo, {})
            curve = robust_curve_for_plot(
                background,
                [(p[1], p[2]) for p in support if p[0] == ifo],
                args.tail_log10_far)
            fit_metadata[ifo] = curve.get("metadata")

            raw = [(x, y) for x, y in curve["raw"] if x >= args.llr_min]
            if raw:
                ax.scatter([x for x, _y in raw], [y for _x, y in raw],
                           s=4, alpha=0.16, color="#707070",
                           label="empirical support")
            if curve["before_tail"]:
                ax.plot([x for x, _y in curve["before_tail"]],
                        [y for _x, y in curve["before_tail"]],
                        color="#2ca25f", linewidth=2.2,
                        label="smoothed before-tail")
            if curve["tail_kept"]:
                ax.scatter([x for x, _y in curve["tail_kept"]],
                           [y for _x, y in curve["tail_kept"]],
                           s=18, color="#1f78b4", label="tail fit points")
            if curve["tail_line"] is not None:
                (x0, y0), (x1, y1) = curve["tail_line"]
                ax.plot([x0, x1], [y0, y1], color="#f28e2b",
                        linewidth=2.2, label="linear tail fit (all points)")

            metadata = curve.get("metadata") or {}
            tail_from = metadata.get("tail_from")
            boundary_log10_far = metadata.get("boundary_log10_far")
            if tail_from is not None:
                ax.axvline(tail_from, color="#f28e2b",
                           linestyle=":", linewidth=1.0)
            if boundary_log10_far is not None:
                ax.axhline(boundary_log10_far, color="#f28e2b",
                           linestyle=":", linewidth=0.9)

            ax.set_title(f"{ifo}: before-tail smoothing + all-point tail")
            ax.set_xlabel("LLR")
            ax.set_ylabel(r"$\log_{10}(\mathrm{FAR})$")
            ax.set_xlim(left=args.llr_min)
            ax.grid(True, alpha=0.22)

        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=5, frameon=False,
                   bbox_to_anchor=(0.5, 0.02))
        fig.suptitle(
            "Single-detector FAR-LLR background: smoothed before-tail and all-point tail",
            y=0.98)
        fig.subplots_adjust(left=0.06, right=0.99, top=0.88,
                            bottom=0.18, wspace=0.18)
        fig.savefig(args.output)

    support_llr = [p[1] for p in support]
    support_far = [p[2] for p in support]
    background_trigger_counts = {}
    for ifo, bg in data.get("backgrounds", {}).items():
        background_trigger_counts[ifo] = len(bg.get("background_triggers", []))
    summary = {
        "background": args.background,
        "assigned": args.assigned,
        "support_points": len(support),
        "background_trigger_counts": background_trigger_counts,
        "assigned_points": len(assigned),
        "plot": args.output,
        "ifos": sorted(data.get("backgrounds", {}).keys()),
        "ln_llr_min": min(support_llr) if support_llr else None,
        "ln_llr_max": max(support_llr) if support_llr else None,
        "log10_far_min": min(support_far) if support_far else None,
        "log10_far_max": max(support_far) if support_far else None,
        "fit_metadata": fit_metadata,
        "tail_log10_far": args.tail_log10_far,
        "matplotlib_error": plot_error,
    }
    Path(args.summary).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
