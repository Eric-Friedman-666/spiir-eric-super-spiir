#!/usr/bin/env python3
"""One-click entry point for crashcar 2x2 plots.

Run from /fred/oz016/qliang/Eric_bless_SPIIR, for example:

    python3 crashcar_plot.py --run_id crashcar_noinj_24h_2node_diag_r23

The wrapper resolves an exact run_id from immutable run-local env snapshots
below smoke_runs/.  Identical run_root values are deduplicated, while zero or
multiple distinct roots fail explicitly; no newest-run fallback is used.
The public interface intentionally exposes only --run_id/--run-id plus the
backward-compatible --run-root selector.  Inputs and the output directory are
inferred from run-local evidence.
Plot construction remains in gstlal-spiir/bin/crashcar_plot.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def clean_run_id(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or cleaned == "=":
        raise SystemExit(
            "Expected one run_id, for example: "
            "python3 crashcar_plot.py --run_id crashcar_noinj_24h_2node_diag_r23"
        )
    return cleaned


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


def exact_smoke_run_root(root: Path, run_id: str) -> tuple[Path, list[Path]]:
    """Resolve one run root from exact run_id values in smoke env snapshots."""
    smoke_root = (root / "smoke_runs").resolve()
    if not smoke_root.is_dir():
        raise SystemExit(f"Smoke-run directory not found: {smoke_root}")
    patterns = (
        "*/*/launch.env",
        "*/*/scripts/crashcar.env",
        "*/*/*/scripts/crashcar.env",
        "*/*/controller/*.env",
        "*/*/*/controller/*.env",
        "*/*/provenance/*.env",
        "*/*/*/provenance/*.env",
    )
    candidates: set[Path] = set()
    for pattern in patterns:
        candidates.update(smoke_root.glob(pattern))

    roots: dict[Path, list[Path]] = {}
    invalid: list[str] = []
    for env_path in sorted(candidates):
        values = read_env(env_path)
        if values.get("run_id") != run_id:
            continue
        raw_root = values.get("run_root", "").strip()
        if not raw_root:
            invalid.append(f"{env_path}: missing run_root")
            continue
        candidate = Path(os.path.expandvars(raw_root)).expanduser()
        if not candidate.is_absolute():
            candidate = env_path.parent / candidate
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(smoke_root)
        except (FileNotFoundError, ValueError) as exc:
            invalid.append(f"{env_path}: invalid run_root {candidate} ({exc})")
            continue
        if not resolved.is_dir() or not (resolved / "run").is_dir():
            invalid.append(f"{env_path}: run_root has no run/ directory: {resolved}")
            continue
        roots.setdefault(resolved, []).append(env_path.resolve())

    if invalid:
        raise SystemExit(
            f"Invalid exact run_id records for {run_id!r}:\n  "
            + "\n  ".join(invalid)
        )
    if not roots:
        raise SystemExit(
            f"No env snapshot below {smoke_root} has exact run_id={run_id!r}"
        )
    if len(roots) != 1:
        details = []
        for run_root, sources in sorted(roots.items(), key=lambda item: str(item[0])):
            details.append(
                f"  {run_root}\n    "
                + "\n    ".join(str(path) for path in sorted(sources))
            )
        raise SystemExit(
            f"Exact run_id={run_id!r} resolves to multiple distinct run roots; "
            "pass --run-root explicitly only after choosing the intended run:\n"
            + "\n".join(details)
        )
    run_root, sources = next(iter(roots.items()))
    return run_root, sorted(set(sources))


def run_env_sources(run_root: Path) -> list[Path]:
    """Return run-local configuration/provenance env files in precedence order."""
    candidates: list[Path] = [
        run_root.parent / "launch.env",
        run_root / "launch.env",
        run_root.parent / "scripts" / "crashcar.env",
    ]
    for base in (run_root.parent, run_root):
        candidates.extend(sorted((base / "provenance").glob("*.env")))
        candidates.extend(sorted((base / "controller").glob("*.env")))
    candidates.append(run_root / "scripts" / "crashcar.env")
    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def merged_run_env(run_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    values: dict[str, str] = {}
    origins: dict[str, str] = {}
    for path in run_env_sources(run_root):
        for key, value in read_env(path).items():
            if value:
                values[key] = value
                origins[key] = str(path)
    return values, origins


def infer_background_seconds(root: Path, run_root: Path, fallback: float) -> float:
    status = read_json(run_root / "controller" / "status.json")
    for key in ("background_accumulation_seconds", "crashcar_background_required_seconds"):
        raw = status.get(key)
        if raw not in (None, ""):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    values, _ = merged_run_env(run_root)
    for key in ("background_accumulation", "crashcar_background_required_seconds"):
        raw = values.get(key)
        if raw:
            try:
                value = float(raw)
            except ValueError:
                continue
            if value > 0:
                return value
    hours = values.get("BG_accumulation_hour")
    if hours:
        try:
            value = float(hours) * 3600.0
        except ValueError:
            value = 0.0
        if value > 0:
            return value
    return fallback


def infer_float_env(root: Path, run_root: Path, key: str, fallback: float) -> float:
    status = read_json(run_root / "controller" / "status.json")
    raw = status.get(key)
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    values, _ = merged_run_env(run_root)
    raw = values.get(key)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return fallback


def infer_env_value(root: Path, run_root: Path, key: str) -> str | None:
    values, _ = merged_run_env(run_root)
    raw = values.get(key)
    return raw if raw else None


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
    values, _ = merged_run_env(run_root)
    for key in env_keys:
        raw = values.get(key)
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


def infer_run_role(run_root: Path) -> dict:
    values, origins = merged_run_env(run_root)
    injection_mode = is_truthy(values.get("injection_mode", ""))
    role = values.get("crashcar_internal_live_background_role", "").strip().lower()
    if role not in {"producer", "consumer"}:
        if injection_mode or run_root.name == "B2_injection_consumer":
            role = "consumer"
        elif run_root.name == "B1_noinj_producer":
            role = "producer"
        else:
            role = "no-injection"

    producer_root: Path | None = None
    producer_source: str | None = None
    if role == "consumer":
        for key in (
            "crashcar_internal_live_background_root",
            "live_background_root",
            "noninj_stats_loc",
        ):
            raw = values.get(key, "").strip()
            if not raw:
                continue
            candidate = Path(os.path.expandvars(raw)).expanduser()
            if not candidate.is_absolute():
                candidate = run_root / candidate
            candidate = candidate.resolve()
            if candidate.name == "run":
                candidate = candidate.parent
            producer_root = candidate
            producer_source = f"{origins.get(key, 'run env')}#{key}"
            break
        if producer_root is None:
            raise SystemExit(
                f"Injection consumer {run_root} has no live no-injection producer root"
            )
        if not producer_root.is_dir() or not (producer_root / "run").is_dir():
            raise SystemExit(
                f"Injection consumer live producer root is invalid: {producer_root}"
            )

    return {
        "role": role,
        "injection_mode": injection_mode,
        "single_background_mode": values.get("single_background_mode", ""),
        "producer_root": producer_root,
        "producer_source": producer_source,
        "env_files": [str(path) for path in run_env_sources(run_root)],
    }


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
    impl_args.extend(extra_args or [])

    command_parts = [
        "module load gcc/13.3.0 scipy-bundle/2024.05 >/dev/null 2>&1 || true"
    ]
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


def attach_resolution_metadata(meta_path: Path, resolution: dict) -> None:
    payload = read_json(meta_path)
    if not payload:
        raise SystemExit(f"Plot metadata is missing or malformed: {meta_path}")
    payload["resolution"] = resolution
    temporary = meta_path.with_name(f".{meta_path.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, meta_path)
    finally:
        temporary.unlink(missing_ok=True)


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
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--run_id", "--run-id", dest="run_id",
        help="Exact run_id found in an env snapshot below ROOT/smoke_runs.",
    )
    selector.add_argument(
        "--run-root", type=Path,
        help="Explicit run root containing run/; retained for advanced use.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    matched_env_files: list[Path] = []
    if args.run_id is not None:
        run_id = clean_run_id(args.run_id)
        run_root, matched_env_files = exact_smoke_run_root(root, run_id)
        resolution_method = "exact_env_run_id_deduplicated"
    else:
        run_root = args.run_root.resolve(strict=True)
        if not (run_root / "run").is_dir():
            raise SystemExit(f"Explicit run root has no run/ directory: {run_root}")
        values, _ = merged_run_env(run_root)
        run_id = values.get("run_id", "").strip() or run_root.name
        resolution_method = "explicit_run_root"

    impl = root / "gstlal-spiir" / "bin" / "crashcar_plot.py"
    if not impl.exists():
        raise SystemExit(f"Plot implementation not found: {impl}")
    output_dir = run_root / "figures"

    role_info = infer_run_role(run_root)
    plot_mode = "injection" if role_info["role"] == "consumer" else "no-injection"
    extra_args: list[str] = []

    bank_dir = infer_env_value(root, run_root, "bank_file") or infer_env_value(
        root, run_root, "bank_dir"
    )
    if bank_dir:
        extra_args.extend(["--bank-dir", bank_dir])

    start_bank = infer_integer_setting(
        root, run_root,
        status_key="start_bank",
        env_keys=("start_bank",),
        fallback=0,
    )
    banks_per_worker = infer_integer_setting(
        root, run_root,
        status_key="banks_per_worker",
        env_keys=("bank_per_worker", "banks_per_worker"),
        fallback=8,
    )
    worker_count = infer_integer_setting(
        root, run_root,
        status_key="worker_count",
        env_keys=("worker_number", "worker_count"),
        fallback=len([
            path for path in (run_root / "run").iterdir()
            if path.is_dir() and len(path.name) == 3 and path.name.isdigit()
        ]) or 2,
    )
    if start_bank < 0 or banks_per_worker < 1 or worker_count < 1:
        raise SystemExit("Invalid worker geometry inferred from run-local evidence")
    extra_args.extend(
        [
            "--start-bank", str(start_bank),
            "--banks-per-worker", str(banks_per_worker),
            "--worker-count", str(worker_count),
            "--max-panel-a-points", "0",
        ]
    )

    producer_root = role_info["producer_root"]
    if role_info["role"] == "consumer":
        extra_args.extend(["--background-producer-root", str(producer_root)])

    bg_seconds = infer_background_seconds(root, run_root, 10800.0)
    snr_series_threshold = infer_float_env(
        root, run_root, "SNR_series_logFAR_threshold", -4.0
    )
    tail_boundary = infer_float_env(root, run_root, "tail_log_FAR", -2.0)

    payload = run_plot_impl(
        root=root,
        impl=impl,
        run_root=run_root,
        output_dir=output_dir,
        run_label=f"crashcar_{run_id}_{plot_mode}",
        panel_a_worker="000",
        background_seconds=bg_seconds,
        snr_series_threshold=snr_series_threshold,
        tail_boundary=tail_boundary,
        extra_args=extra_args,
    )
    if payload.get("raw_stdout"):
        print(payload["raw_stdout"])
        return 0

    resolution = {
        "method": resolution_method,
        "requested_run_id": args.run_id,
        "resolved_run_id": run_id,
        "resolved_run_root": str(run_root),
        "matched_run_id_env_files": [str(path) for path in matched_env_files],
        "context_env_files": role_info["env_files"],
        "role": role_info["role"],
        "injection_mode": role_info["injection_mode"],
        "single_background_mode": role_info["single_background_mode"],
        "panel_a_worker": "000",
        "panel_a_background_root": str(producer_root or run_root),
        "panel_a_background_json": str(
            (producer_root or run_root)
            / "run" / "000"
            / "single_background.json"
        ),
        "live_producer_source": role_info["producer_source"],
        "worker_count": worker_count,
        "start_bank": start_bank,
        "banks_per_worker": banks_per_worker,
        "bank_dir": bank_dir,
        "zerolag_source": "current_run/run/NNN/*_zerolag_*.xml.gz",
        "segment_source": "current_run/run/NNN/H1L1V1_SEGMENTS_*.xml.gz",
        "coinc_snr_source": "current_run/run/*.xml[.gz] via normal CoincsDoc",
        "output_dir": str(output_dir),
    }
    if payload.get("meta"):
        attach_resolution_metadata(Path(payload["meta"]), resolution)

    print(f"run_root={run_root}")
    print(f"plot_mode={plot_mode}")
    print(f"first_2x2={payload.get('first', '')}")
    print(f"second_2x2={payload.get('second', '')}")
    if payload.get("meta"):
        print(f"metadata={payload['meta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
