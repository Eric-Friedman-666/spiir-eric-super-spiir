#!/usr/bin/env python3
"""Materialize crashcar frozen single-detector BG support for the C runtime."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def _as_float(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _row_rank(row):
    for key in ("rank", "llr"):
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def _row_gps(row):
    for key in ("gps", "end_gps", "time"):
        value = _as_float(row.get(key))
        if value is not None:
            return value
    end_time = _as_float(row.get("end_time"))
    end_time_ns = _as_float(row.get("end_time_ns"))
    if end_time is not None:
        return end_time + 1.0e-9 * (end_time_ns or 0.0)
    return ""


def materialize(background: Path, output: Path, summary: Path | None) -> int:
    payload = json.loads(background.read_text())
    backgrounds = payload.get("backgrounds") or {}
    output.parent.mkdir(parents=True, exist_ok=True)

    counts = {}
    total_support = 0
    total_fit = 0
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["kind", "ifo", "rank", "far", "gps", "livetime"]
        )
        writer.writeheader()
        for ifo, bg in sorted(backgrounds.items()):
            livetime = _as_float(bg.get("livetime")) or 0.0
            rows = bg.get("background_triggers") or bg.get("support") or []
            count = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rank = _row_rank(row)
                if rank is None:
                    continue
                writer.writerow(
                    {
                        "kind": "support",
                        "ifo": ifo,
                        "rank": "%.17g" % rank,
                        "far": "",
                        "gps": _row_gps(row),
                        "livetime": "%.17g" % livetime,
                    }
                )
                count += 1
            fit_count = 0
            fit_rows = []
            for row in bg.get("far_llr_points") or []:
                if not isinstance(row, dict):
                    continue
                rank = _row_rank(row)
                far = _as_float(row.get("far"))
                if rank is None or far is None or far <= 0.0:
                    continue
                fit_rows.append((rank, far, _row_gps(row)))
            for rank, far, gps in sorted(fit_rows, key=lambda item: item[0]):
                writer.writerow(
                    {
                        "kind": "fit",
                        "ifo": ifo,
                        "rank": "%.17g" % rank,
                        "far": "%.17g" % far,
                        "gps": gps,
                        "livetime": "%.17g" % livetime,
                    }
                )
                fit_count += 1
            counts[ifo] = {
                "livetime": livetime,
                "support_rows": count,
                "fit_rows": fit_count,
            }
            total_support += count
            total_fit += fit_count

    if summary:
        summary.write_text(
            json.dumps(
                {
                    "background": str(background),
                    "output": str(output),
                    "ifos": counts,
                    "support_rows": total_support,
                    "fit_rows": total_fit,
                    "rows": total_support + total_fit,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    return 0 if total_support > 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary")
    args = parser.parse_args()
    return materialize(
        Path(args.background),
        Path(args.output),
        Path(args.summary) if args.summary else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
