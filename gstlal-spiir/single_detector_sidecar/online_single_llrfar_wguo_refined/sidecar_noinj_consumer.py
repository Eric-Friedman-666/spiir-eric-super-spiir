#!/usr/bin/env python3
"""Consume one completed sidecar-owned A107 worker into an independent oracle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys

import sidecar_causal_engine as causal
import sidecar_owned_a107 as owned
import sidecar_segment_provenance as segments
import sidecar_shape_source_binding as shape_binding


NSEC = 1_000_000_000
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
UINT_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
MANIFEST_LINE_RE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)\Z")
RUNTIME_FILES = (
    "FORMAL_NOINJECTION_SIDECAR_ENTRYPOINT_V2.txt",
    "run_noinj_sidecar.sh",
    "sidecar_noinj_submit.sh",
    "sidecar_noinj_sbatch.sh",
    "sidecar_noinj_pipeline.sh",
    "sidecar_owned_a107.py",
    "sidecar_noinj_consumer.py",
    "sidecar_causal_engine.py",
    "sidecar_segment_provenance.py",
    "sidecar_shape_source_binding.py",
    "verification_sidecar_numeric.py",
)


class SidecarNoInjectionError(RuntimeError):
    pass


def _uint(value, field, maximum=(1 << 63) - 1):
    text = str(value)
    if not UINT_RE.fullmatch(text):
        raise SidecarNoInjectionError(
            f"{field} must be a canonical nonnegative integer")
    number = int(text, 10)
    if number > maximum:
        raise SidecarNoInjectionError(f"{field} is out of range")
    return number


def _sha(value, field):
    text = str(value)
    if not SHA_RE.fullmatch(text):
        raise SidecarNoInjectionError(f"{field} must be lowercase SHA-256")
    return text


def _directory(path, field):
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SidecarNoInjectionError(f"missing {field}: {candidate}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not resolved.is_dir():
        raise SidecarNoInjectionError(
            f"{field} must be a non-symlink directory")
    return resolved


def _regular_file(path, field):
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SidecarNoInjectionError(f"missing {field}: {candidate}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not resolved.is_file():
        raise SidecarNoInjectionError(
            f"{field} must be a regular non-symlink file")
    return resolved


def _canonical_bytes(value):
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True,
                   separators=(",", ":")) + "\n"
    ).encode("ascii")


def _sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _write_new_readonly(path, payload):
    destination = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify_runtime_context(args):
    """Fail closed unless this process and every local import use one seal."""
    run_root = _directory(args.run_root, "sidecar run root")
    runtime = _directory(run_root / "runtime", "staged runtime")
    consumer = _regular_file(__file__, "staged consumer")
    if consumer.parent != runtime:
        raise SidecarNoInjectionError(
            "consumer is not executing from staged runtime")
    if runtime.stat().st_mode & 0o222:
        raise SidecarNoInjectionError("staged runtime directory is writable")

    manifest = _regular_file(
        runtime / "expected_manifest.sha256", "runtime manifest")
    if manifest.parent != runtime:
        raise SidecarNoInjectionError("runtime manifest escapes staged runtime")
    try:
        manifest_payload = manifest.read_bytes()
        manifest_text = manifest_payload.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise SidecarNoInjectionError(
            "runtime manifest is not readable canonical ASCII") from exc
    if not manifest_text.endswith("\n"):
        raise SidecarNoInjectionError("runtime manifest lacks final newline")
    manifest_sha = _sha_bytes(manifest_payload)
    source_sha = _sha(
        args.source_manifest_sha256, "source_manifest_sha256")
    runtime_sha = _sha(
        args.runtime_manifest_sha256, "runtime_manifest_sha256")
    if source_sha != runtime_sha or manifest_sha != source_sha:
        raise SidecarNoInjectionError(
            "runtime manifest SHA differs from pinned source/runtime SHA")

    lines = manifest_text.splitlines()
    if len(lines) != len(RUNTIME_FILES):
        raise SidecarNoInjectionError("runtime manifest line count drift")
    seen = set()
    for expected_name, line in zip(RUNTIME_FILES, lines):
        match = MANIFEST_LINE_RE.fullmatch(line)
        if match is None:
            raise SidecarNoInjectionError(
                "runtime manifest has a malformed record")
        expected_sha, name = match.groups()
        if name != expected_name or name in seen:
            raise SidecarNoInjectionError(
                "runtime manifest names/order/uniqueness drift")
        seen.add(name)
        path = _regular_file(runtime / name, f"staged runtime {name}")
        if path.parent != runtime:
            raise SidecarNoInjectionError(
                f"staged runtime file escapes runtime: {name}")
        if path.stat().st_mode & 0o222:
            raise SidecarNoInjectionError(
                f"staged runtime file is writable: {name}")
        try:
            actual_sha = segments.sha256_file(path)
        except Exception as exc:
            raise SidecarNoInjectionError(
                f"cannot hash staged runtime file: {name}") from exc
        if actual_sha != expected_sha:
            raise SidecarNoInjectionError(
                f"staged runtime SHA drift: {name}")

    actual_names = sorted(path.name for path in runtime.iterdir())
    expected_names = sorted((*RUNTIME_FILES, "expected_manifest.sha256"))
    if actual_names != expected_names:
        raise SidecarNoInjectionError(
            "staged runtime contains an unexpected path")

    modules = {
        "consumer": sys.modules[__name__],
        "owned parser": owned,
        "causal engine": causal,
        "segment binding": segments,
        "shape binding": shape_binding,
        "causal numeric adapter": causal.numeric,
        "shape numeric adapter": shape_binding.numeric,
    }
    for label, module in modules.items():
        module_path = _regular_file(module.__file__, f"{label} module")
        if module_path.parent != runtime:
            raise SidecarNoInjectionError(
                f"{label} import did not originate in staged runtime")
    return manifest_sha

def _component_csv(results):
    if not results:
        raise SidecarNoInjectionError(
            "completed A107 worker produced no H1/L1 component results")
    fields = tuple(results[0])
    if any(tuple(result) != fields for result in results):
        raise SidecarNoInjectionError("causal-engine result schema drift")
    text = io.StringIO(newline="")
    writer = csv.DictWriter(
        text, fieldnames=fields, extrasaction="raise", lineterminator="\n")
    writer.writeheader()
    writer.writerows(results)
    return text.getvalue().encode("ascii")


def _validate_shape_inputs(h1_path, l1_path, manifest):
    sources = manifest.get("sources") if isinstance(manifest, dict) else None
    if not isinstance(sources, dict):
        raise SidecarNoInjectionError("shape manifest has no sources")
    observed = {}
    for ifo, supplied in (("H1", h1_path), ("L1", l1_path)):
        source = sources.get(ifo)
        if not isinstance(source, dict):
            raise SidecarNoInjectionError(f"shape manifest misses {ifo}")
        expected_path = _regular_file(source.get("path"), f"{ifo} shape source")
        supplied_path = _regular_file(supplied, f"{ifo} supplied shape source")
        if supplied_path != expected_path:
            raise SidecarNoInjectionError(
                f"{ifo} supplied shape source differs from pinned source")
        expected_sha = _sha(source.get("sha256"), f"{ifo} shape SHA")
        actual_sha = segments.sha256_file(supplied_path)
        if actual_sha != expected_sha:
            raise SidecarNoInjectionError(f"{ifo} shape source SHA drift")
        observed[ifo] = {
            "path": str(supplied_path),
            "sha256": actual_sha,
        }
    return observed


def consume(
    args,
    *,
    row_loader=owned.load_owned_worker,
    shape_factory=shape_binding.BoundActualPickleShapeSource,
    shape_manifest=None,
):
    worker_id = _uint(args.worker_id, "worker_id", 4095)
    worker_count = _uint(args.worker_count, "worker_count", 4096)
    worker_group = _uint(args.worker_group, "worker_group", 4095)
    start_bank = _uint(args.start_bank, "start_bank", 383)
    banks_per_worker = _uint(
        args.banks_per_worker, "banks_per_worker", 384)
    start_gps = _uint(args.start_gps, "start_gps")
    end_gps = _uint(args.end_gps, "end_gps")
    window = _uint(
        args.background_window_seconds,
        "background_window_seconds")
    update = _uint(args.update_period_seconds, "update_period_seconds")
    tail_log10_far = float(getattr(args, "tail_log10_far", "-2"))
    if not math.isfinite(tail_log10_far) or not tail_log10_far < 0.0:
        raise SidecarNoInjectionError(
            "tail_log10_far must be finite and strictly negative")
    if not (0 <= worker_id < worker_count <= 4096):
        raise SidecarNoInjectionError("worker identity is outside geometry")
    if worker_group != worker_id:
        raise SidecarNoInjectionError("worker group must equal worker id")
    if banks_per_worker <= 0:
        raise SidecarNoInjectionError("banks_per_worker must be positive")
    if start_bank + banks_per_worker * worker_count > 384:
        raise SidecarNoInjectionError("single bank geometry exceeds NSBH")
    if end_gps <= start_gps:
        raise SidecarNoInjectionError("run interval is not positive")
    if window <= 0 or update <= 0 or start_gps + window > end_gps:
        raise SidecarNoInjectionError(
            "live background schedule cannot complete")

    source_sha = _sha(
        args.source_manifest_sha256, "source_manifest_sha256")
    runtime_sha = _sha(
        args.runtime_manifest_sha256, "runtime_manifest_sha256")
    config_sha = _sha(args.config_sha256, "config_sha256")
    raw_sha = _sha(
        args.raw_input_manifest_sha256, "raw_input_manifest_sha256")

    run_root = _directory(args.run_root, "sidecar run root")
    reference_root = _directory(
        run_root / "reference", "sidecar reference root")
    segment_xml = _regular_file(args.segment_xml, "segment XML")
    manifest = (
        shape_binding.manifest_object()
        if shape_manifest is None else shape_manifest)
    shape_observations = _validate_shape_inputs(
        args.wguo_pickle_h1, args.wguo_pickle_l1, manifest)

    tag = f"{worker_id:03d}"
    final_root = reference_root / f"worker_{tag}"
    staging = reference_root / f".worker_{tag}.tmp.{os.getpid()}"
    if final_root.exists() or final_root.is_symlink():
        raise SidecarNoInjectionError("worker reference output is not fresh")
    try:
        staging.mkdir(mode=0o700)
    except OSError as exc:
        raise SidecarNoInjectionError(
            "cannot create fresh worker staging root") from exc

    try:
        segment_sha = segments.sha256_file(segment_xml)
        _derivative, derivative_payload = segments.build_derivative(
            segment_xml, start_gps * NSEC, end_gps * NSEC,
            expected_source_sha256=segment_sha)
        derivative_path = staging / "segment_derivative.json"
        segments.write_atomic_readonly(derivative_path, derivative_payload)
        derivative_sha = _sha_bytes(derivative_payload)

        bank_ids = tuple(range(
            start_bank + banks_per_worker * worker_id,
            start_bank + banks_per_worker * (worker_id + 1)))
        stream_map = tuple(enumerate(bank_ids))
        rows, parser_summary = row_loader(
            run_root=str(run_root),
            worker_id=worker_id,
            worker_count=worker_count,
            worker_group=worker_group,
            source_stream_bank_map=stream_map,
            start_bank=start_bank,
            banks_per_worker=banks_per_worker)

        namespace_object = {
            "schema_version": 1,
            "mode": causal.MODE_NO_INJECTION,
            "run_root": str(run_root),
            "worker_id": worker_id,
            "worker_count": worker_count,
            "worker_group": worker_group,
            "bank_ids": list(bank_ids),
            "run_start_gps": start_gps,
            "run_end_gps": end_gps,
            "background_window_seconds": window,
            "update_period_seconds": update,
            "tail_log10_far": tail_log10_far,
            "source_manifest_sha256": source_sha,
            "runtime_manifest_sha256": runtime_sha,
            "config_sha256": config_sha,
            "raw_input_manifest_sha256": raw_sha,
            "segment_xml_sha256": segment_sha,
            "segment_derivative_sha256": derivative_sha,
            "shape_source_manifest_sha256":
                shape_factory.source_manifest_sha256,
        }
        run_namespace_sha = _sha_bytes(
            _canonical_bytes(namespace_object))
        shape_source = shape_factory()

        background_path = staging / "single_background.json"
        engine = causal.WorkerCausalEngine(
            mode=causal.MODE_NO_INJECTION,
            worker_id=worker_id,
            worker_count=worker_count,
            worker_group=worker_group,
            source_stream_bank_map=stream_map,
            run_start_ns=start_gps * NSEC,
            run_end_ns=end_gps * NSEC,
            background_window_ns=window * NSEC,
            update_period_ns=update * NSEC,
            segment_derivative_path=str(derivative_path),
            expected_segment_xml_sha256=segment_sha,
            expected_segment_derivative_sha256=derivative_sha,
            shape_source=shape_source,
            background_path=str(background_path),
            run_namespace_sha256=run_namespace_sha,
            source_manifest_sha256=source_sha,
            runtime_manifest_sha256=runtime_sha,
            config_sha256=config_sha,
            shape_source_sha256=shape_factory.source_manifest_sha256,
            tail_log10_far=tail_log10_far,
        )
        results = engine.process_rows(rows)
        engine_summary = engine.finalize()
        if engine_summary["accepted_version"] < 1:
            raise SidecarNoInjectionError(
                "run completed without an authoritative single background")
        if not background_path.is_file() or background_path.is_symlink():
            raise SidecarNoInjectionError(
                "authoritative single background was not published")

        components_payload = _component_csv(results)
        components_path = staging / "components.csv"
        _write_new_readonly(components_path, components_payload)

        summary = {
            "schema_version": 1,
            "mode": causal.MODE_NO_INJECTION,
            "run_namespace_sha256": run_namespace_sha,
            "namespace": namespace_object,
            "shape_sources": shape_observations,
            "parser": parser_summary,
            "engine": engine_summary,
            "component_rows": len(results),
        }
        summary_payload = _canonical_bytes(summary)
        summary_path = staging / "summary.json"
        _write_new_readonly(summary_path, summary_payload)

        derivative_path.unlink()
        outputs = {}
        for name in (
                "components.csv", "summary.json",
                "single_background.json"):
            path = staging / name
            outputs[name] = {
                "bytes": path.stat().st_size,
                "sha256": segments.sha256_file(path),
            }
        status = {
            "schema_version": 1,
            "state": "COMPLETE",
            "mode": causal.MODE_NO_INJECTION,
            "worker_id": worker_id,
            "run_namespace_sha256": run_namespace_sha,
            "component_rows": len(results),
            "accepted_background_version":
                engine_summary["accepted_version"],
            "outputs": outputs,
        }
        status_payload = _canonical_bytes(status)
        _write_new_readonly(staging / "status.json", status_payload)

        directory_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.rename(staging, final_root)
        parent_fd = os.open(reference_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return status
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parser():
    parser = argparse.ArgumentParser(
        description="consume one sidecar-owned no-injection A107 worker")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--worker-count", required=True)
    parser.add_argument("--worker-group", required=True)
    parser.add_argument("--start-bank", required=True)
    parser.add_argument("--banks-per-worker", required=True)
    parser.add_argument("--start-gps", required=True)
    parser.add_argument("--end-gps", required=True)
    parser.add_argument("--background-window-seconds", required=True)
    parser.add_argument("--update-period-seconds", required=True)
    parser.add_argument("--tail-log10-far", default="-2")
    parser.add_argument("--segment-xml", required=True)
    parser.add_argument("--wguo-pickle-h1", required=True)
    parser.add_argument("--wguo-pickle-l1", required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--runtime-manifest-sha256", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--raw-input-manifest-sha256", required=True)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        _verify_runtime_context(args)
        status = consume(args)
    except Exception as exc:
        print(
            f"SIDECAR_NOINJ_CONSUMER_ERROR: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr)
        return 2
    print(
        "SIDECAR_NOINJ_CONSUMER_COMPLETE "
        f"worker={status['worker_id']} "
        f"components={status['component_rows']} "
        f"background_version={status['accepted_background_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
