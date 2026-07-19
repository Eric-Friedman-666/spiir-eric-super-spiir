#!/usr/bin/env python3
"""Atomic publication and causal-state tests for the no-injection consumer."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import sidecar_causal_engine as causal
import sidecar_noinj_consumer as subject
import sidecar_segment_provenance as segments
from test_sidecar_causal_engine import FakeShapeSource, make_row
from test_sidecar_segment_provenance_v2 import (
    canonical_definers, segment, write_xml,
)


SHA = "a" * 64


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "run"
        (self.root / "acquisition" / "worker_000").mkdir(parents=True)
        (self.root / "reference").mkdir()
        self.raw = Path(self.temporary.name) / "raw"
        self.raw.mkdir()
        self.h1 = self.raw / "H1.pkl"
        self.l1 = self.raw / "L1.pkl"
        self.h1.write_bytes(b"H1-sidecar-shape\n")
        self.l1.write_bytes(b"L1-sidecar-shape\n")
        self.segment_xml = self.raw / "segments.xml"
        write_xml(
            self.segment_xml,
            definers=canonical_definers(),
            segments=[
                segment(
                    f"segment:segment_id:{index}",
                    f"segment_definer:segment_def_id:{index}",
                    0, 900)
                for index in range(3)
            ])
        self.manifest = {
            "sources": {
                "H1": {
                    "path": str(self.h1),
                    "sha256": hashlib.sha256(
                        self.h1.read_bytes()).hexdigest(),
                },
                "L1": {
                    "path": str(self.l1),
                    "sha256": hashlib.sha256(
                        self.l1.read_bytes()).hexdigest(),
                },
            },
        }
        self.args = argparse.Namespace(
            run_root=str(self.root),
            worker_id="0",
            worker_count="1",
            worker_group="0",
            start_bank="5",
            banks_per_worker="1",
            start_gps="0",
            end_gps="900",
            background_window_seconds="300",
            update_period_seconds="60",
            segment_xml=str(self.segment_xml),
            wguo_pickle_h1=str(self.h1),
            wguo_pickle_l1=str(self.l1),
            source_manifest_sha256=SHA,
            runtime_manifest_sha256=SHA,
            config_sha256=SHA,
            raw_input_manifest_sha256=SHA,
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def rows():
        values = [
            make_row(
                seq, gps, event, bank=5,
                rho_h=rho, rho_l=rho + 0.1)
            for seq, gps, event, rho in (
                (1, 10, 10, 4.0),
                (2, 20, 20, 5.0),
                (3, 30, 30, 6.0),
                (4, 40, 40, 7.0),
                (5, 300, 300, 5.5),
            )
        ]
        values.append(make_row(
            6, 301, 301, bank=5, ifos="H1",
            rho_h=8.0, rho_l=8.0))
        values.append(make_row(
            7, 302, 302, bank=5, ifos="L1",
            rho_h=8.0, rho_l=8.0))
        values.append(make_row(
            8, 303, 303, bank=5, ifos="V1",
            rho_h=8.0, rho_l=8.0))
        return values

    def test_route_owned_llr_then_single_assignment_v_ignored_and_atomic_outputs(self):
        def loader(**kwargs):
            self.assertEqual(kwargs["source_stream_bank_map"], ((0, 5),))
            return self.rows(), {
                "input_abi": "sidecar-owned-a107-v1",
                "eligible_components": {"H1": 5, "L1": 5},
                "v_only_rows": 1,
            }

        status = subject.consume(
            self.args,
            row_loader=loader,
            shape_factory=FakeShapeSource,
            shape_manifest=self.manifest)
        output = self.root / "reference" / "worker_000"
        self.assertEqual(
            sorted(path.name for path in output.iterdir()),
            ["components.csv", "single_background.json",
             "status.json", "summary.json"])
        self.assertEqual(status["state"], "COMPLETE")
        self.assertGreaterEqual(status["accepted_background_version"], 1)
        with (output / "components.csv").open(
                encoding="ascii", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 12)
        early = [item for item in rows if int(item["event_id"]) < 300]
        current = [item for item in rows if item["event_id"] == "300"]
        self.assertEqual(len(early), 8)
        self.assertTrue(all(
            item["status"] == causal.STATUS_MULTI_OWNED_LLR_ONLY
            for item in early))
        self.assertTrue(all(
            item["calculated_far_hex"] == ""
            and item["assigned_far_hex"] == ""
            for item in early))
        self.assertEqual(len(current), 2)
        self.assertTrue(all(
            item["status"] == causal.STATUS_MULTI_OWNED_LLR_ONLY
            and item["calculated_far_hex"] == ""
            and item["assigned_far_hex"] == ""
            for item in current))
        singles = [
            item for item in rows if item["event_id"] in ("301", "302")]
        self.assertEqual(len(singles), 2)
        self.assertTrue(all(
            item["status"] in (
                causal.STATUS_ASSIGNED_DIRECT,
                causal.STATUS_ASSIGNED_TAIL)
            and int(item["bg_version"]) >= 1
            for item in singles))
        self.assertFalse(any(
            item["event_id"] == "303" for item in rows))
        boundary = [
            item for item in rows
            if item["event_id"] == "10" and item["ifo"] == "H1"][0]
        self.assertEqual(boundary["rho_hex"], float(4.0).hex())
        summary = json.loads(
            (output / "summary.json").read_text(encoding="ascii"))
        self.assertEqual(
            summary["engine"]["lifecycle"]["multi_owned_llr_only"], 10)
        self.assertEqual(summary["engine"]["lifecycle"]["assigned"], 2)
        background = json.loads(
            (output / "single_background.json").read_text(
                encoding="ascii"))
        self.assertGreaterEqual(background["accepted_version"], 1)

    def test_failure_has_no_completed_worker_or_staging_residue(self):
        def broken_loader(**_kwargs):
            raise RuntimeError("synthetic parser failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic parser failure"):
            subject.consume(
                self.args,
                row_loader=broken_loader,
                shape_factory=FakeShapeSource,
                shape_manifest=self.manifest)
        reference = self.root / "reference"
        self.assertFalse((reference / "worker_000").exists())
        self.assertEqual(list(reference.iterdir()), [])


    def test_runtime_context_rejects_nonstaged_execution_without_output(self):
        with self.assertRaisesRegex(
                subject.SidecarNoInjectionError, "missing staged runtime"):
            subject._verify_runtime_context(self.args)
        reference = self.root / "reference"
        self.assertFalse((reference / "worker_000").exists())
        self.assertEqual(list(reference.iterdir()), [])

if __name__ == "__main__":
    unittest.main()
