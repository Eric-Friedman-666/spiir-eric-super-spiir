#!/usr/bin/env python3
"""One-click entry point for crashcar A/B 2x2 plots.

Run from /fred/oz016/qliang/Eric_bless_SPIIR, for example:

    python3 crashcar_plot.py --run_id=20260728_1400

The equivalent forms ``--run_id VALUE`` and ``--run_id = VALUE`` are also
accepted.  Successful runs finish by printing every generated 2x2 figure as
an absolute path.

The wrapper resolves an exact run_id from immutable run-local env snapshots
below smoke_runs/ or runs/.  A role-A/role-B group is plotted with the same
implementation twice, producing one background/zerolag 2x2 and one SNR-series
2x2 for each role.  Role B reads background data from its explicitly recorded
background_run_root while its zerolag, Coinc, and SNR-series inputs remain in
the B run.  No newest-run fallback is used.  The public interface intentionally
exposes only --run_id/--run-id plus the backward-compatible --run-root selector.
Inputs and the output directory are inferred from run-local evidence.
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


def normalize_cli_argv(argv: list[str]) -> list[str]:
    """Accept an optional standalone equals token after the run-id option."""
    normalized = list(argv)
    for option in ("--run_id", "--run-id"):
        try:
            index = normalized.index(option)
        except ValueError:
            continue
        if index + 1 >= len(normalized):
            continue
        value = normalized[index + 1]
        if value == "=":
            if index + 2 >= len(normalized):
                raise SystemExit(f"{option} requires a run_id after '='")
            normalized[index + 1:index + 3] = [normalized[index + 2]]
        elif value.startswith("="):
            normalized[index + 1] = value[1:]
    return normalized


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


def canonical_run_type(run_root: Path, values: dict[str, str] | None = None) -> str:
    values = values or {}
    for key in ("run_type", "crashcar_role"):
        run_type = values.get(key, "").strip().upper()
        if run_type in {"A", "B"}:
            return run_type
    if run_root.name in {"A", "B"}:
        return run_root.name
    if run_root.name == "B1_noinj_producer":
        return "A"
    if run_root.name == "B2_injection_consumer":
        return "B"
    return ""


def exact_run_roots(root: Path, run_id: str) -> list[dict]:
    """Resolve all role roots matching one exact run_id."""
    search_roots = tuple(
        candidate.resolve()
        for candidate in (root / "smoke_runs", root / "runs")
        if candidate.is_dir()
    )
    if not search_roots:
        raise SystemExit(
            f"Neither smoke_runs/ nor runs/ exists below repository root: {root}"
        )
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
    for search_root in search_roots:
        for pattern in patterns:
            candidates.update(search_root.glob(pattern))

    roots: dict[Path, list[Path]] = {}
    root_types: dict[Path, set[str]] = {}
    invalid: list[str] = []
    for env_path in sorted(candidates):
        values = read_env(env_path)
        if values.get("run_id") != run_id:
            continue
        raw_root = values.get("run_root", "").strip()
        if raw_root:
            candidate = Path(os.path.expandvars(raw_root)).expanduser()
            if not candidate.is_absolute():
                candidate = env_path.parent / candidate
        elif env_path.parent.name == "scripts":
            candidate = env_path.parent.parent
        else:
            invalid.append(f"{env_path}: missing run_root")
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if not any(
                resolved == search_root or search_root in resolved.parents
                for search_root in search_roots
            ):
                raise ValueError("resolved path is outside smoke_runs/ and runs/")
        except (FileNotFoundError, ValueError) as exc:
            invalid.append(f"{env_path}: invalid run_root {candidate} ({exc})")
            continue

        if not resolved.is_dir() or not (resolved / "run").is_dir():
            consumer_roots = (
                resolved / "B",
                resolved / "B2_injection_consumer",
            )
            consumer_root = next(
                (
                    candidate
                    for candidate in consumer_roots
                    if candidate.is_dir() and (candidate / "run").is_dir()
                ),
                None,
            )
            if consumer_root is not None:
                resolved = consumer_root.resolve(strict=True)
            else:
                invalid.append(
                    f"{env_path}: run_root has no run/ directory: {resolved}"
                )
                continue
        roots.setdefault(resolved, []).append(env_path.resolve())
        run_type = canonical_run_type(resolved, values)
        if run_type:
            root_types.setdefault(resolved, set()).add(run_type)

    if invalid:
        raise SystemExit(
            f"Invalid exact run_id records for {run_id!r}:\n  "
            + "\n  ".join(invalid)
        )
    if not roots:
        searched = ", ".join(str(path) for path in search_roots)
        raise SystemExit(
            f"No env snapshot below [{searched}] has exact run_id={run_id!r}"
        )
    records: list[dict] = []
    seen_types: dict[str, Path] = {}
    for run_root, sources in roots.items():
        types = root_types.get(run_root, set())
        if len(types) > 1:
            raise SystemExit(
                f"Exact run_id={run_id!r} has conflicting A/B role records for "
                f"{run_root}: {sorted(types)}"
            )
        run_type = next(iter(types), canonical_run_type(run_root))
        if run_type and run_type in seen_types and seen_types[run_type] != run_root:
            raise SystemExit(
                f"Exact run_id={run_id!r} resolves role {run_type} to multiple "
                f"run roots: {seen_types[run_type]} and {run_root}"
            )
        if run_type:
            seen_types[run_type] = run_root
        records.append({
            "run_type": run_type,
            "run_root": run_root,
            "matched_env_files": sorted(set(sources)),
        })

    if len(records) > 1 and any(not record["run_type"] for record in records):
        details = []
        for run_root, sources in sorted(roots.items(), key=lambda item: str(item[0])):
            details.append(
                f"  {run_root}\n    "
                + "\n    ".join(str(path) for path in sorted(sources))
            )
        raise SystemExit(
            f"Exact run_id={run_id!r} resolves to multiple distinct run roots; "
            "the roots are not an unambiguous A/B pair:\n"
            + "\n".join(details)
        )
    return sorted(
        records,
        key=lambda record: ({"A": 0, "B": 1}.get(record["run_type"], 2),
                            str(record["run_root"])),
    )


def exact_run_root(root: Path, run_id: str) -> tuple[Path, list[Path]]:
    """Backward-compatible resolver for callers that require one run root."""
    records = exact_run_roots(root, run_id)
    if len(records) != 1:
        raise SystemExit(
            f"Exact run_id={run_id!r} contains A/B role roots; use "
            "exact_run_roots() or the command-line entry point"
        )
    record = records[0]
    return record["run_root"], record["matched_env_files"]


def explicit_run_roots(path: Path) -> list[dict]:
    """Resolve one legacy run root or the A/B roots below one group."""
    resolved = path.resolve(strict=True)
    if (resolved / "run").is_dir():
        group = resolved.parent if resolved.name in {"A", "B"} else None
    else:
        group = resolved

    candidates: list[Path] = []
    if group is not None:
        candidates = [
            child.resolve(strict=True)
            for child in (group / "A", group / "B")
            if child.is_dir() and (child / "run").is_dir()
        ]
    if not candidates and (resolved / "run").is_dir():
        candidates = [resolved]
    if not candidates:
        raise SystemExit(
            f"Explicit path is neither a run root nor an A/B group: {resolved}"
        )

    records = []
    for run_root in candidates:
        values, _ = merged_run_env(run_root)
        records.append({
            "run_type": canonical_run_type(run_root, values),
            "run_root": run_root,
            "matched_env_files": [],
        })
    return records


def shared_output_dir(records: list[dict]) -> Path:
    """Keep all four A/B figures together below the shared group root."""
    parents = {record["run_root"].parent for record in records}
    run_types = {record["run_type"] for record in records}
    if len(parents) == 1 and run_types and run_types <= {"A", "B"}:
        return next(iter(parents)) / "figures"
    return records[0]["run_root"] / "figures"


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
    run_type = canonical_run_type(run_root, values)
    role = values.get("crashcar_internal_live_background_role", "").strip().lower()
    if role not in {"producer", "consumer"}:
        if run_type == "B" or injection_mode:
            role = "consumer"
        elif run_type == "A":
            role = "producer"
        else:
            role = "no-injection"

    producer_root: Path | None = None
    producer_source: str | None = None
    if role == "consumer":
        for key in (
            "background_run_root",
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
        "run_type": run_type,
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
    key = f"{prefix}_" if prefix else ""
    print(f"{key}first_2x2={payload.get('first', '')}")
    print(f"{key}second_2x2={payload.get('second', '')}")
    print(f"{key}background_2x2={payload.get('first', '')}")
    print(f"{key}snr_series_2x2={payload.get('second', '')}")
    if payload.get("meta"):
        print(f"{key}metadata={payload['meta']}")


def plot_record(
    *,
    root: Path,
    impl: Path,
    record: dict,
    records: list[dict],
    run_id: str,
    requested_run_id: str | None,
    resolution_method: str,
    output_dir: Path,
) -> dict:
    run_root = record["run_root"]
    role_info = infer_run_role(run_root)
    run_type = record["run_type"] or role_info["run_type"]
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

    role_roots = {
        item["run_type"] or "run": str(item["run_root"])
        for item in records
    }
    if run_type == "B" and "A" in role_roots:
        if producer_root != Path(role_roots["A"]):
            raise SystemExit(
                f"Role B background_run_root {producer_root} does not match "
                f"the resolved role A root {role_roots['A']}"
            )

    payload = run_plot_impl(
        root=root,
        impl=impl,
        run_root=run_root,
        output_dir=output_dir,
        run_label=(
            f"crashcar_{run_id}_{run_type}_{plot_mode}"
            if run_type else f"crashcar_{run_id}_{plot_mode}"
        ),
        panel_a_worker="000",
        background_seconds=infer_background_seconds(root, run_root, 10800.0),
        snr_series_threshold=infer_float_env(
            root, run_root, "SNR_series_logFAR_threshold", -4.0
        ),
        tail_boundary=infer_float_env(root, run_root, "tail_log_FAR", -2.0),
        extra_args=extra_args,
    )
    if payload.get("raw_stdout"):
        return payload

    resolution = {
        "method": resolution_method,
        "requested_run_id": requested_run_id,
        "resolved_run_id": run_id,
        "resolved_run_type": run_type,
        "resolved_run_root": str(run_root),
        "resolved_run_roots": role_roots,
        "matched_run_id_env_files": [
            str(path) for path in record["matched_env_files"]
        ],
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
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--run_id", "--run-id", dest="run_id",
        help="Exact run_id found below ROOT/smoke_runs or ROOT/runs.",
    )
    selector.add_argument(
        "--run-root", type=Path,
        help="Explicit A/B group or role root; retained for advanced use.",
    )
    cli_argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(normalize_cli_argv(cli_argv))

    root = Path(__file__).resolve().parent
    if args.run_id is not None:
        run_id = clean_run_id(args.run_id)
        records = exact_run_roots(root, run_id)
        resolution_method = "exact_env_run_id_ab_group"
    else:
        records = explicit_run_roots(args.run_root)
        values, _ = merged_run_env(records[0]["run_root"])
        run_id = values.get("run_id", "").strip()
        if not run_id:
            group = records[0]["run_root"].parent
            run_id = group.name if records[0]["run_type"] else records[0]["run_root"].name
        resolution_method = "explicit_run_root_or_ab_group"

    impl = root / "gstlal-spiir" / "bin" / "crashcar_plot.py"
    if not impl.exists():
        raise SystemExit(f"Plot implementation not found: {impl}")
    output_dir = shared_output_dir(records)

    print(f"run_id={run_id}")
    print(f"output_dir={output_dir}")
    generated_paths: list[Path] = []
    for record in records:
        prefix = record["run_type"]
        payload = plot_record(
            root=root,
            impl=impl,
            record=record,
            records=records,
            run_id=run_id,
            requested_run_id=args.run_id,
            resolution_method=resolution_method,
            output_dir=output_dir,
        )
        root_key = f"{prefix}_run_root" if prefix else "run_root"
        print(f"{root_key}={record['run_root']}")
        print_plot_payload(prefix, payload)
        if not payload.get("raw_stdout"):
            generated_paths.extend(
                Path(payload[key]).expanduser().resolve()
                for key in ("first", "second")
                if payload.get(key)
            )
    print(f"generated_2x2_count={len(generated_paths)}")
    for path in generated_paths:
        print(f"generated_2x2={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
