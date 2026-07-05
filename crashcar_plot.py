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
from datetime import datetime, timezone
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


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return {}


def is_truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "1.0", "true", "yes", "y", "on")


def is_injection_workflow(run_root: Path) -> bool:
    workflow_status = read_json(run_root / "controller" / "workflow_status.json")
    if workflow_status.get("workflow") == "frozen_background_then_injection":
        return True
    env = read_env(run_root / "scripts" / "crashcar.env")
    return is_truthy(env.get("injection_mode")) and (run_root / "bg_noinj").exists()


def stage_root_from_status(run_root: Path, key: str, fallback: str) -> Path:
    workflow_status = read_json(run_root / "controller" / "workflow_status.json")
    raw = workflow_status.get(key)
    return Path(raw).resolve() if raw else (run_root / fallback).resolve()


def current_or_first_chunk_root(run_root: Path) -> Path | None:
    workflow_status = read_json(run_root / "controller" / "workflow_status.json")
    raw = workflow_status.get("current_chunk_root")
    if raw and Path(raw).exists():
        return Path(raw).resolve()
    chunks = sorted((run_root / "inj_bns" / "chunks").glob("chunk_*"))
    return chunks[0].resolve() if chunks else None


def materialize_template_autocorr(root: Path, run_root: Path, snr_dir: Path | None = None) -> None:
    if snr_dir is not None:
        manifest_candidates = [
            snr_dir if snr_dir.is_file() else snr_dir / "manifest.csv",
        ]
    else:
        manifest_candidates = [
            run_root / "run" / "candidate_events_manifest.csv",
            run_root / "candidate_events_manifest.csv",
            run_root / "run" / "crashcar_candidate_events_manifest.csv",
            run_root / "crashcar_candidate_events_manifest.csv",
            run_root / "run" / "candidate_events" / "manifest.csv",
            run_root / "candidate_events" / "manifest.csv",
            run_root / "run" / "crashcar_snr_series" / "manifest.csv",
            run_root / "crashcar_snr_series" / "manifest.csv",
        ]
    manifest = next((path for path in manifest_candidates if path.exists()), manifest_candidates[0])
    storage_dir = manifest.parent
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
            str(storage_dir),
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
            f"summary may be at {storage_dir / 'autocorrelation_summary.json'}"
        )


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

    print(f"run_root={run_root}")
    if not is_injection_workflow(run_root):
        bg_seconds = args.background_accumulation_seconds
        if bg_seconds is None:
            bg_seconds = infer_background_seconds(root, run_root, 10800.0)
        snr_series_threshold = args.snr_series_logfar_threshold
        if snr_series_threshold is None:
            snr_series_threshold = infer_float_env(root, run_root, "SNR_series_logFAR_threshold", -4.0)

        if not args.skip_template_autocorr:
            materialize_template_autocorr(root, run_root)
        payload = run_plot_impl(
            root=root,
            impl=impl,
            run_root=run_root,
            output_dir=output_dir,
            run_label=f"crashcar_{run_id}",
            panel_a_worker=args.panel_a_worker,
            background_seconds=bg_seconds,
            snr_series_threshold=snr_series_threshold,
            stamp=args.stamp,
            no_module_load=args.no_module_load,
            passthrough=passthrough,
        )
        if payload.get("raw_stdout"):
            print(payload["raw_stdout"])
            return 0
        print(f"first_2x2={payload.get('first', '')}")
        print(f"second_2x2={payload.get('second', '')}")
        if payload.get("meta"):
            print(f"metadata={payload['meta']}")
        return 0

    bg_root = stage_root_from_status(run_root, "bg_run_root", "bg_noinj")
    inj_root = stage_root_from_status(run_root, "injection_root", "inj_bns")
    workflow_status = read_json(run_root / "controller" / "workflow_status.json")
    print("plot_mode=injection")
    print(f"bg_run_root={bg_root}")
    print(f"injection_root={inj_root}")
    print(f"frozen_single_background_json={workflow_status.get('frozen_single_background_json', '')}")
    print(f"frozen_multi_stats_dir={workflow_status.get('frozen_multi_stats_dir', '')}")

    bg_seconds = args.background_accumulation_seconds
    if bg_seconds is None:
        bg_seconds = infer_background_seconds(root, bg_root, 10800.0)
    bg_threshold = args.snr_series_logfar_threshold
    if bg_threshold is None:
        bg_threshold = infer_float_env(root, bg_root, "SNR_series_logFAR_threshold", -4.0)
    if not args.skip_template_autocorr:
        materialize_template_autocorr(root, bg_root)
    bg_payload = run_plot_impl(
        root=root,
        impl=impl,
        run_root=bg_root,
        output_dir=output_dir,
        run_label=f"crashcar_{run_id}_bg_noinj",
        panel_a_worker=args.panel_a_worker,
        background_seconds=bg_seconds,
        snr_series_threshold=bg_threshold,
        stamp=args.stamp,
        no_module_load=args.no_module_load,
        passthrough=passthrough,
    )
    print_plot_payload("bg", bg_payload)

    chunk_root = current_or_first_chunk_root(run_root)
    inj_threshold = args.snr_series_logfar_threshold
    if inj_threshold is None:
        inj_threshold = infer_float_env(root, chunk_root or run_root, "SNR_series_logFAR_threshold", bg_threshold)
    if not args.skip_template_autocorr:
        for chunk_run_dir in sorted(run_root.glob("inj_bns/chunks/chunk_*/run")):
            materialize_template_autocorr(root, chunk_run_dir.parent)
    inj_extra = [
        "--zerolag-glob",
        "inj_bns/chunks/chunk_*/run/[0-9][0-9][0-9]/*_zerolag_*.xml*",
        "--detail-glob",
        "bg_noinj/run/crashcar_singlefar_detail_worker*.csv",
        "--raw-trigger-glob",
        "bg_noinj/run/[0-9][0-9][0-9]/*_single_triggers.csv",
        "--segment-glob",
        "bg_noinj/run/[0-9][0-9][0-9]/H1L1V1_SEGMENTS_*.xml*",
        "--shape-map",
        "bg_noinj/artifacts/crashcar_template_shape_map.csv",
        "--background-json",
        "bg_noinj/artifacts/crashcar_day1_last_bg3h_full_background.json",
        "--snr-dir-glob",
        "inj_bns/chunks/chunk_*/run/candidate_events_manifest.csv",
        "--snr-dir-glob",
        "inj_bns/chunks/chunk_*/run/candidate_events",
        "--snr-dir-glob",
        "inj_bns/chunks/chunk_*/run/crashcar_candidate_events_manifest.csv",
        "--snr-dir-glob",
        "inj_bns/chunks/chunk_*/run/crashcar_snr_series",
    ]
    inj_payload = run_plot_impl(
        root=root,
        impl=impl,
        run_root=run_root,
        output_dir=output_dir,
        run_label=f"crashcar_{run_id}_injection",
        panel_a_worker=args.panel_a_worker,
        background_seconds=bg_seconds,
        snr_series_threshold=inj_threshold,
        stamp=args.stamp,
        no_module_load=args.no_module_load,
        passthrough=passthrough,
        extra_args=inj_extra,
    )
    print_plot_payload("injection", inj_payload)
    if not inj_payload.get("raw_stdout"):
        print(f"first_2x2={inj_payload.get('first', '')}")
        print(f"second_2x2={inj_payload.get('second', '')}")
        if inj_payload.get("meta"):
            print(f"metadata={inj_payload['meta']}")
    meta_path = output_dir / f"crashcar_{run_id}_workflow_plot_{args.stamp or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "run_root": str(run_root),
                "mode": "injection",
                "bg": bg_payload,
                "injection": inj_payload,
                "frozen_single_background_json": workflow_status.get("frozen_single_background_json", ""),
                "frozen_multi_stats_dir": workflow_status.get("frozen_multi_stats_dir", ""),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"workflow_metadata={meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
