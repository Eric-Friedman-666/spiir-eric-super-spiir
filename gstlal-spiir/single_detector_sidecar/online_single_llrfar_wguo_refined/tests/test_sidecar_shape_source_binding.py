#!/usr/bin/env python3
"""Source-binding tests for the independent H1/L1 sidecar oracle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sidecar_shape_source_binding as subject


class ShapeSourceBindingTests(unittest.TestCase):
    def test_manifest_is_exact_canonical_h1_l1_binding(self):
        self.assertEqual(
            subject.SOURCE_MANIFEST_SHA256,
            "edac34040d4a0ba73e0af54e8d89fe2b08d108bced8bbf9a44909416fe416336")
        self.assertEqual(
            hashlib.sha256(subject.SOURCE_MANIFEST_BYTES).hexdigest(),
            subject.SOURCE_MANIFEST_SHA256)
        value = json.loads(subject.SOURCE_MANIFEST_BYTES)
        self.assertEqual(list(value), ["schema_version", "kind", "sources"])
        self.assertEqual(list(value["sources"]), ["H1", "L1"])
        self.assertEqual(
            value["sources"]["H1"]["sha256"],
            "edd29a0d1b614dc2de1e5fe83baf90c677489a8aa576dce0c623896d5d977c9e")
        self.assertEqual(
            value["sources"]["L1"]["sha256"],
            "4217734b09c81cbe9ac75d47bdc7d0966e1690043f7870398e082d53530d488d")

    def test_single_fd_helper_hashes_regular_file_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "shape.pkl"
            target.write_bytes(b"exact-shape-bytes\n")
            digest, size = subject._single_fd_sha256(target)
            self.assertEqual(digest, hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(size, target.stat().st_size)
            link = root / "shape-link.pkl"
            link.symlink_to(target)
            with self.assertRaisesRegex(
                    subject.ShapeBindingError, "cannot open"):
                subject._single_fd_sha256(link)

    def test_adapter_load_is_bracketed_by_identical_source_snapshots(self):
        stable = {
            "H1": {"path": "/H1", "sha256": "1" * 64, "size": 1},
            "L1": {"path": "/L1", "sha256": "2" * 64, "size": 1},
        }
        changed = {
            "H1": {"path": "/H1", "sha256": "3" * 64, "size": 1},
            "L1": stable["L1"],
        }
        original_verify = subject.verify_physical_sources
        original_adapter = subject.numeric.ActualPickleShapeSource
        try:
            snapshots = iter((stable, changed))
            subject.verify_physical_sources = lambda: next(snapshots)
            subject.numeric.ActualPickleShapeSource = object
            with self.assertRaisesRegex(
                    subject.ShapeBindingError, "changed while"):
                subject.BoundActualPickleShapeSource()

            snapshots = iter((stable, stable))
            subject.verify_physical_sources = lambda: next(snapshots)
            bound = subject.BoundActualPickleShapeSource()
            self.assertEqual(bound.source_observations, stable)
        finally:
            subject.verify_physical_sources = original_verify
            subject.numeric.ActualPickleShapeSource = original_adapter

    def test_current_physical_sources_match_the_frozen_manifest(self):
        observations = subject.verify_physical_sources()
        self.assertEqual(list(observations), ["H1", "L1"])
        for ifo in ("H1", "L1"):
            self.assertEqual(
                observations[ifo]["sha256"],
                subject._PICKLE_SHA256[ifo])
            self.assertGreater(observations[ifo]["size"], 0)


if __name__ == "__main__":
    unittest.main()
