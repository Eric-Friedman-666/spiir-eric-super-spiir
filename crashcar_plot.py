#!/usr/bin/env python3
"""Convenience entry point for crashcar 2x2 plots.

Run from /fred/oz016/qliang/Eric_bless_SPIIR, for example:

    python3 crashcar_plot.py --run_id 20260629_2300

This wrapper locates the newest run root under runs/<run_id>/ and delegates to
gstlal-spiir/bin/crashcar_plot.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def clean_run_id(values: list[str]) -> str:
    cleaned = [value.strip() for value in values if value.strip() and value.strip() != "="]
    if len(cleaned) != 1:
        raise SystemExit(
            "Expected one run_id, for example: python3 crashcar_plot.py --run_id 20260629_2300"
        )
    return cleaned[0]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def latest_run_root(root: Path, run_id: str) -> Path:
    run_parent = root / "runs" / run_id
    if not run_parent.exists():
        raise SystemExit(f"Run id directory not found: {run_parent}")
    candidates = [
        path
        for path in run_parent.iterdir()
        if path.is_dir() and ((path / "run").exists() or (path / "controller").exists())
    ]
    if not candidates:
        raise SystemExit(f"No timestamped run roots found below: {run_parent}")
    return sorted(candidates, key=lambda path: path.name)[-1]


def infer_background_seconds(root: Path, run_root: Path, fallback: float) -> float:
    for env_path in (run_root / "scripts" / "crashcar.env", root / "scripts" / "crashcar.env"):
        values = read_env(env_path)
        hours = values.get("BG_accumulation_hour")
        if hours:
            try:
                return float(hours) * 3600.0
            except ValueError:
                pass
    return fallback


def infer_float_env(root: Path, run_root: Path, key: str, fallback: float) -> float:
    for env_path in (run_root / "scripts" / "crashcar.env", root / "scripts" / "crashcar.env"):
        values = read_env(env_path)
        raw = values.get(key)
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
    return fallback


def infer_env_value(root: Path, run_root: Path, key: str) -> str | None:
    for env_path in (run_root / "scripts" / "crashcar.env", root / "scripts" / "crashcar.env"):
        values = read_env(env_path)
        raw = values.get(key)
        if raw:
            return raw
    return None


def materialize_template_autocorr(root: Path, run_root: Path) -> None:
    snr_dir = run_root / "run" / "crashcar_snr_series"
    manifest = snr_dir / "manifest.csv"
    script = next(
        (
            path
            for path in (
                run_root / "scripts" / "materialize_snr_autocorrelation.py",
                root / "scripts" / "materialize_snr_autocorrelation.py",
            )
            if path.exists()
        ),
        None,
    )
    bank_dir = infer_env_value(root, run_root, "bank_file")
    if not manifest.exists() or script is None or not bank_dir:
        return
    proc = subprocess.run(
        [
            "python3",
            str(script),
            "--manifest",
            str(manifest),
            "--snr-dir",
            str(snr_dir),
            "--bank-dir",
            bank_dir,
        ],
        cwd=str(run_root),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if proc.stdout:
        sys.stderr.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        raise SystemExit(
            "Failed to materialize template autocorrelation companions; "
            f"summary may be at {snr_dir / 'autocorrelation_summary.json'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run_id", "--run-id", nargs="+", required=True, help="Run id under ROOT/runs, e.g. 20260629_2300. Forms like '--run_id = 20260629_2300' are accepted.")
    parser.add_argument("--root", type=Path, default=None, help="Eric_bless_SPIIR root; default is this script's directory.")
    parser.add_argument("--run-root", type=Path, default=None, help="Override the resolved timestamped run root.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory; default is RUN_ROOT/artifacts.")
    parser.add_argument("--stamp", default=None, help="Optional filename timestamp suffix.")
    parser.add_argument("--panel-a-worker", default="000", help="Worker used for panel (a)'s current BG support.")
    parser.add_argument("--background-accumulation-seconds", type=float, default=None)
    parser.add_argument("--snr-series-logfar-threshold", type=float, default=None)
    parser.add_argument("--no-module-load", action="store_true", help="Do not attempt OzSTAR scipy-bundle module load before plotting.")
    parser.add_argument("--skip-template-autocorr", action="store_true", help="Skip automatic template autocorrelation materialization for retained SNR series.")
    args, passthrough = parser.parse_known_args()

    root = (args.root or Path(__file__).resolve().parent).resolve()
    run_id = clean_run_id(args.run_id)
    run_root = (args.run_root.resolve() if args.run_root else latest_run_root(root, run_id))
    output_dir = (args.output_dir.resolve() if args.output_dir else run_root / "artifacts")
    impl = root / "gstlal-spiir" / "bin" / "crashcar_plot.py"
    if not impl.exists():
        raise SystemExit(f"Plot implementation not found: {impl}")

    bg_seconds = args.background_accumulation_seconds
    if bg_seconds is None:
        bg_seconds = infer_background_seconds(root, run_root, 10800.0)
    snr_series_threshold = args.snr_series_logfar_threshold
    if snr_series_threshold is None:
        snr_series_threshold = infer_float_env(root, run_root, "SNR_series_logFAR_threshold", -4.0)

    if not args.skip_template_autocorr:
        materialize_template_autocorr(root, run_root)

    impl_args = [
        str(impl),
        "--run-root",
        str(run_root),
        "--output-dir",
        str(output_dir),
        "--run-label",
        f"crashcar_{run_id}",
        "--panel-a-worker",
        args.panel_a_worker,
        "--background-accumulation-seconds",
        f"{bg_seconds:g}",
        "--snr-series-logfar-threshold",
        f"{snr_series_threshold:g}",
    ]
    if args.stamp:
        impl_args.extend(["--stamp", args.stamp])
    impl_args.extend(passthrough)

    command_parts = []
    if not args.no_module_load:
        command_parts.append("module load gcc/13.3.0 scipy-bundle/2024.05 >/dev/null 2>&1 || true")
    command_parts.append("exec python3 " + " ".join(shlex.quote(part) for part in impl_args))
    command = "; ".join(command_parts)

    proc = subprocess.run(
        ["bash", "-lc", command],
        cwd=str(root),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    stdout = proc.stdout.strip()
    if proc.returncode != 0:
        if stdout:
            print(stdout)
        return proc.returncode

    print(f"run_root={run_root}")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        print(stdout)
        return 0
    print(f"first_2x2={payload.get('first', '')}")
    print(f"second_2x2={payload.get('second', '')}")
    if payload.get("meta"):
        print(f"metadata={payload['meta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
