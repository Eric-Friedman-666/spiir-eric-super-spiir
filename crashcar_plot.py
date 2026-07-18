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
    candidates = sorted(candidates, key=lambda path: path.name)
    if len(candidates) == 1:
        return candidates[0]

    completed_noinj = []
    descriptions = []
    for path in candidates:
        status = read_json(path / "controller" / "status.json")
        workflow_status = read_json(path / "controller" / "workflow_status.json")
        injection = is_injection_workflow(path)
        phase = (
            workflow_status.get("phase")
            or workflow_status.get("status")
            or status.get("phase")
            or status.get("status")
            or "unknown"
        )
        mode = "injection-workflow" if injection else "no-injection"
        descriptions.append(f"  {path} [{mode}, phase={phase}]")
        if not injection and str(phase).lower() == "completed":
            completed_noinj.append(path)

    if len(completed_noinj) == 1:
        sys.stderr.write(
            "crashcar_plot.py: multiple timestamped roots found; "
            f"using the only completed no-injection root {completed_noinj[0]}\n"
        )
        return completed_noinj[0]

    raise SystemExit(
        "Multiple timestamped run roots found for this run_id; please pass --run-root explicitly:\n"
        + "\n".join(descriptions)
    )


def infer_background_seconds(root: Path, run_root: Path, fallback: float) -> float:
    status = read_json(run_root / "controller" / "status.json")
    for key in ("background_accumulation_seconds", "crashcar_background_required_seconds"):
        raw = status.get(key)
        if raw:
            try:
                value = float(raw)
            except ValueError:
                continue
            if value > 0:
                return value
    for env_path in (run_root / "scripts" / "crashcar.env", root / "scripts" / "crashcar.env"):
        values = read_env(env_path)
        seconds = values.get("background_accumulation")
        if seconds:
            try:
                value = float(seconds)
            except ValueError:
                value = 0.0
            if value > 0:
                return value
        hours = values.get("BG_accumulation_hour")
        if hours:
            try:
                return float(hours) * 3600.0
            except ValueError:
                pass
    return fallback


def infer_float_env(root: Path, run_root: Path, key: str, fallback: float) -> float:
    status = read_json(run_root / "controller" / "status.json")
    raw = status.get(key)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
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


def infer_integer_setting(
    root: Path,
    run_root: Path,
    *,
    status_key: str,
    env_keys: tuple[str, ...],
    fallback: int,
) -> int:
    status = read_json(run_root / "controller" / "status.json")
    raw = status.get(status_key)
    if raw not in (None, ""):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = -1
        if value >= 0:
            return value
    for key in env_keys:
        raw = infer_env_value(root, run_root, key)
        if raw not in (None, ""):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return value
    return fallback


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return {}


def is_truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "1.0", "true", "yes", "y", "on")


def normalize_worker_id(value: str) -> str:
    text = str(value).strip()
    return text.zfill(3) if text.isdigit() else text


def is_injection_workflow(run_root: Path) -> bool:
    workflow_status = read_json(run_root / "controller" / "workflow_status.json")
    if workflow_status.get("workflow") == "frozen_background_then_injection":
        return True
    env = read_env(run_root / "scripts" / "crashcar.env")
    return is_truthy(env.get("injection_mode")) and (run_root / "inj_bns" / "run").is_dir()

def run_plot_impl(
    *,
    root: Path,
    impl: Path,
    run_root: Path,
    output_dir: Path,
    run_label: str,
    panel_a_worker: str,
    background_seconds: float,
    snr_series_threshold: float,
    tail_boundary: float,
    stamp: str | None,
    no_module_load: bool,
    passthrough: list[str],
    extra_args: list[str] | None = None,
) -> dict:
    impl_args = [
        str(impl),
        "--run-root",
        str(run_root),
        "--output-dir",
        str(output_dir),
        "--run-label",
        run_label,
        "--panel-a-worker",
        panel_a_worker,
        "--background-accumulation-seconds",
        f"{background_seconds:g}",
        "--snr-series-logfar-threshold",
        f"{snr_series_threshold:g}",
        "--tail-boundary-log10-far",
        f"{tail_boundary:g}",
    ]
    if stamp:
        impl_args.extend(["--stamp", stamp])
    impl_args.extend(extra_args or [])
    impl_args.extend(passthrough)

    command_parts = []
    if not no_module_load:
        command_parts.append("module load gcc/13.3.0 scipy-bundle/2024.05 >/dev/null 2>&1 || true")
    command_parts.append("exec python3 " + " ".join(shlex.quote(part) for part in impl_args))
    proc = subprocess.run(
        ["bash", "-lc", "; ".join(command_parts)],
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
        raise SystemExit(proc.returncode)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {"raw_stdout": stdout}
    return payload


def print_plot_payload(prefix: str, payload: dict) -> None:
    if payload.get("raw_stdout"):
        print(payload["raw_stdout"])
        return
    print(f"{prefix}_first_2x2={payload.get('first', '')}")
    print(f"{prefix}_second_2x2={payload.get('second', '')}")
    if payload.get("meta"):
        print(f"{prefix}_metadata={payload['meta']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run_id", "--run-id", nargs="+", default=None, help="Run id under ROOT/runs, e.g. 20260629_2300. Forms like '--run_id = 20260629_2300' are accepted.")
    parser.add_argument("--root", type=Path, default=None, help="Eric_bless_SPIIR root; default is this script's directory.")
    parser.add_argument("--run-root", type=Path, default=None, help="Override the resolved timestamped run root.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory; default is RUN_ROOT/artifacts.")
    parser.add_argument("--stamp", default=None, help="Optional filename timestamp suffix.")
    parser.add_argument("--panel-a-worker", default="000", help="Worker used for panel (a)'s current BG support.")
    parser.add_argument("--background-accumulation-seconds", type=float, default=None)
    parser.add_argument("--snr-series-logfar-threshold", type=float, default=None)
    parser.add_argument(
        "--tail-boundary-log10-far",
        type=float,
        default=None,
        help="Override tail_log_FAR; default is inferred from this run's status/env snapshot.",
    )
    parser.add_argument("--no-module-load", action="store_true", help="Do not attempt OzSTAR scipy-bundle module load before plotting.")
    args, passthrough = parser.parse_known_args()

    root = (args.root or Path(__file__).resolve().parent).resolve()
    if args.run_id is None and args.run_root is None:
        parser.error("one of --run_id/--run-id or --run-root is required")
    run_root = args.run_root.resolve() if args.run_root else latest_run_root(root, clean_run_id(args.run_id))
    run_id = clean_run_id(args.run_id) if args.run_id is not None else run_root.parent.name
    output_dir = (args.output_dir.resolve() if args.output_dir else run_root / "artifacts")
    impl = root / "gstlal-spiir" / "bin" / "crashcar_plot.py"
    if not impl.exists():
        raise SystemExit(f"Plot implementation not found: {impl}")

    print(f"run_root={run_root}")
    plot_root = run_root
    extra_args: list[str] = []
    plot_mode = "no-injection"
    if is_injection_workflow(run_root):
        plot_mode = "injection"
        plot_root = (run_root / "inj_bns").resolve()
        if not (plot_root / "run").is_dir():
            raise SystemExit(
                "Injection plotting requires the continuous foreground root: "
                f"{plot_root / 'run'}"
            )
        bundle = (run_root / "frozen_bundle" / "frozen_bundle_manifest.json").resolve()
        if not bundle.is_file():
            raise SystemExit(f"Frozen bundle manifest not found: {bundle}")
        extra_args.extend(
            [
                "--frozen-bundle-manifest",
                str(bundle),
                "--frozen-bundle-worker",
                normalize_worker_id(args.panel_a_worker),
                "--far-point-view",
                "all",
            ]
        )

    bank_dir = infer_env_value(root, plot_root, "bank_file") or infer_env_value(
        root, run_root, "bank_file"
    )
    if bank_dir:
        extra_args.extend(["--bank-dir", bank_dir])
    if plot_mode == "no-injection":
        start_bank = infer_integer_setting(
            root, plot_root,
            status_key="start_bank",
            env_keys=("start_bank",),
            fallback=0,
        )
        banks_per_worker = infer_integer_setting(
            root, plot_root,
            status_key="banks_per_worker",
            env_keys=("bank_per_worker", "banks_per_worker"),
            fallback=8,
        )
        worker_count = infer_integer_setting(
            root, plot_root,
            status_key="worker_count",
            env_keys=("worker_number", "worker_count"),
            fallback=2,
        )
        if banks_per_worker < 1 or worker_count < 1:
            raise SystemExit("Invalid no-injection worker geometry")
        extra_args.extend(
            [
                "--start-bank", str(start_bank),
                "--banks-per-worker", str(banks_per_worker),
                "--worker-count", str(worker_count),
            ]
        )

    bg_seconds = args.background_accumulation_seconds
    if bg_seconds is None:
        bg_seconds = infer_background_seconds(root, plot_root, 10800.0)
    snr_series_threshold = args.snr_series_logfar_threshold
    if snr_series_threshold is None:
        snr_series_threshold = infer_float_env(
            root, plot_root, "SNR_series_logFAR_threshold", -4.0
        )
    tail_boundary = args.tail_boundary_log10_far
    if tail_boundary is None:
        tail_boundary = infer_float_env(root, plot_root, "tail_log_FAR", -2.0)

    print(f"plot_mode={plot_mode}")
    print(f"plot_run_root={plot_root}")
    payload = run_plot_impl(
        root=root,
        impl=impl,
        run_root=plot_root,
        output_dir=output_dir,
        run_label=f"crashcar_{run_id}_{plot_mode}",
        panel_a_worker=args.panel_a_worker,
        background_seconds=bg_seconds,
        snr_series_threshold=snr_series_threshold,
        tail_boundary=tail_boundary,
        stamp=args.stamp,
        no_module_load=args.no_module_load,
        passthrough=passthrough,
        extra_args=extra_args,
    )
    if payload.get("raw_stdout"):
        print(payload["raw_stdout"])
        return 0
    print(f"first_2x2={payload.get('first', '')}")
    print(f"second_2x2={payload.get('second', '')}")
    if payload.get("meta"):
        print(f"metadata={payload['meta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
