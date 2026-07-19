#!/usr/bin/env python3
"""Immutable source binding for the frozen sidecar numeric shape adapter.

The reviewed numeric adapter bytes remain untouched.  This wrapper independently
pins the two physical H1/L1 pickle sources and exposes one canonical manifest
hash that the causal engine can compare to runtime provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat as stat_module

import verification_sidecar_numeric as numeric


_PICKLE_DIR = Path(
    "/fred/oz016/wguo/packages/spiir/src/spiir/search/bank_dofs")
_PICKLE_SHA256 = {
    "H1": "edd29a0d1b614dc2de1e5fe83baf90c677489a8aa576dce0c623896d5d977c9e",
    "L1": "4217734b09c81cbe9ac75d47bdc7d0966e1690043f7870398e082d53530d488d",
}
_SOURCE_MANIFEST = {
    "schema_version": 1,
    "kind": "wguo_o3_fixed_h1_l1_template_shapes",
    "sources": {
        ifo: {
            "path": str(_PICKLE_DIR / (
                f"{ifo}_O3_FB_banks_magnitudes_and_dofs.pkl")),
            "sha256": _PICKLE_SHA256[ifo],
        }
        for ifo in ("H1", "L1")
    },
}
SOURCE_MANIFEST_BYTES = (
    json.dumps(
        _SOURCE_MANIFEST, ensure_ascii=True,
        separators=(",", ":")) + "\n"
).encode("ascii")
SOURCE_MANIFEST_SHA256 = hashlib.sha256(
    SOURCE_MANIFEST_BYTES).hexdigest()


class ShapeBindingError(RuntimeError):
    pass


def _single_fd_sha256(path):
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ShapeBindingError("O_NOFOLLOW is unavailable")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise ShapeBindingError(
            f"cannot open pinned shape source {path}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat_module.S_ISREG(metadata.st_mode):
            raise ShapeBindingError(
                f"shape source is not a regular file: {path}")
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(fd)
        stable = (
            metadata.st_dev == after.st_dev
            and metadata.st_ino == after.st_ino
            and metadata.st_size == after.st_size
            and metadata.st_mtime_ns == after.st_mtime_ns
            and metadata.st_ctime_ns == after.st_ctime_ns
        )
        if size != metadata.st_size or not stable:
            raise ShapeBindingError(
                f"shape source size changed during snapshot: {path}")
        return digest.hexdigest(), size
    finally:
        os.close(fd)


def verify_physical_sources():
    observations = {}
    for ifo in ("H1", "L1"):
        path = Path(_SOURCE_MANIFEST["sources"][ifo]["path"])
        actual, size = _single_fd_sha256(path)
        expected = _PICKLE_SHA256[ifo]
        if actual != expected:
            raise ShapeBindingError(
                f"{ifo} shape pickle SHA-256 drift")
        observations[ifo] = {
            "path": str(path),
            "sha256": actual,
            "size": size,
        }
    return observations


class BoundActualPickleShapeSource:
    """Exact manifest binding plus the immutable reviewed numeric adapter."""

    source_manifest_bytes = SOURCE_MANIFEST_BYTES
    source_manifest_sha256 = SOURCE_MANIFEST_SHA256

    def __init__(self):
        before = verify_physical_sources()
        self._delegate = numeric.ActualPickleShapeSource()
        after = verify_physical_sources()
        if before != after:
            raise ShapeBindingError(
                "shape sources changed while the numeric adapter loaded")
        self.source_observations = after

    def a_eff_and_dof(self, ifo, bankid, tmplt_idx):
        return self._delegate.a_eff_and_dof(
            ifo, bankid, tmplt_idx)


def manifest_object():
    return json.loads(SOURCE_MANIFEST_BYTES.decode("ascii"))


if __name__ == "__main__":
    observations = verify_physical_sources()
    print(json.dumps({
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "sources": observations,
    }, sort_keys=True))
