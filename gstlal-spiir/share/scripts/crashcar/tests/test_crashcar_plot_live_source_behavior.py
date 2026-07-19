#!/usr/bin/env python3
"""Executable live producer authority checks for the formal plotter."""

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[5]
PLOT = REPO / "gstlal-spiir" / "bin" / "crashcar_plot.py"
spec = importlib.util.spec_from_file_location("crashcar_plot_live_behavior", PLOT)
assert spec and spec.loader
plot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plot)


def gps(seconds):
    return {"seconds": seconds, "nanoseconds": 0}


def detector(livetime):
    ranks = [float(value) for value in range(1, 7)]
    log_fars = [
        math.log10((len(ranks) - index) / float(livetime))
        for index in range(len(ranks))
    ]
    tail_index = min(
        range(len(ranks)), key=lambda index: abs(log_fars[index] + 2.0)
    )
    r_tail = ranks[tail_index]
    slope = sum(
        (rank - r_tail) * (log_far + 2.0)
        for rank, log_far in zip(ranks[tail_index:], log_fars[tail_index:])
    ) / sum((rank - r_tail) ** 2 for rank in ranks[tail_index:])
    return {
        "livetime": gps(livetime),
        "support_count": len(ranks),
        "tail_fit": {
            "method": "anchored_ols_all_unique_ranks_ge_r_tail",
            "r_tail": r_tail.hex(),
            "slope": slope.hex(),
            "fit_unique_rank_count": len(ranks) - tail_index,
        },
        "far_llr_points": [
            {
                "gps": gps(101 + index),
                "llr": rank.hex(),
                "far": ((len(ranks) - index) / float(livetime)).hex(),
            }
            for index, rank in enumerate(ranks)
        ],
    }


def document():
    sha = "a" * 64
    return {
        "schema_version": 4,
        "background_kind": "no_injection",
        "run_namespace_sha256": sha,
        "source_manifest_sha256": sha,
        "runtime_manifest_sha256": sha,
        "config_sha256": sha,
        "segment_xml_sha256": sha,
        "segment_canonical_sha256": sha,
        "template_shape_map_sha256": sha,
        "worker_id": 0,
        "worker_count": 1,
        "worker_bank_ids": [0, 1],
        "accepted_version": 2,
        "epoch_gps": gps(1100),
        "window_start_gps": gps(100),
        "window_end_gps": gps(1100),
        "window_duration": gps(1000),
        "update_period": gps(100),
        "far_floor_count": 1,
        "tail_log10_far": -2,
        "backgrounds": {
            "H1": detector(500),
            "L1": detector(600),
        },
    }


def write_doc(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_symlink():
        path.chmod(0o644)
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    path.chmod(0o444)


def load(path, worker="000"):
    return plot.load_panel_a_background_json(
        path,
        1000.0,
        0,
        worker,
        start_bank=0,
        banks_per_worker=2,
        worker_count=1,
    )


class LivePlotAuthorityTests(unittest.TestCase):
    def test_live_same_worker_provenance_and_coverage(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            producer = root / "continuing_no_injection"
            consumer = root / "injection"
            consumer.mkdir()
            path = producer / "run" / "000" / "single_background.json"
            value = document()
            write_doc(path, value)

            resolved = plot.resolve_live_producer_background_path(
                producer, "000"
            )
            self.assertEqual(resolved, path)
            panel = plot.load_requested_panel_a_source(
                consumer,
                panel_a_source="background",
                explicit_background_json=resolved,
                background_accumulation_seconds=1000.0,
                max_points=0,
                panel_a_worker="000",
                start_bank=0,
                banks_per_worker=2,
                worker_count=1,
                detail_glob="run/detail*.csv",
                ifo_id_map={"0": "H1", "1": "L1"},
                panel_a_bg_policy="latest",
            )
            authority = panel["schema4_authority"]
            self.assertTrue(panel["authoritative"])
            self.assertEqual(authority["worker_id"], 0)
            self.assertEqual(authority["worker_bank_ids"], [0, 1])
            self.assertEqual(authority["accepted_version"], 2)
            self.assertEqual(authority["epoch_gps_ns"], 1100_000_000_000)
            self.assertEqual(
                authority["provenance"]["run_namespace_sha256"], "a" * 64
            )
            self.assertEqual(
                panel["source_sha256"],
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)

    def test_wrong_worker_provenance_and_future_coverage_fail_closed(self):
        with tempfile.TemporaryDirectory() as root_text:
            path = (
                Path(root_text) / "producer" / "run" / "000"
                / "single_background.json"
            )
            value = document()
            write_doc(path, value)
            with self.assertRaisesRegex(ValueError, "worker mismatch|geometry is invalid"):
                load(path, "001")

            value["run_namespace_sha256"] = "a" * 63
            write_doc(path, value)
            with self.assertRaisesRegex(ValueError, "provenance"):
                load(path)

            value = document()
            value["backgrounds"]["H1"]["far_llr_points"][0]["gps"] = gps(1101)
            write_doc(path, value)
            with self.assertRaisesRegex(ValueError, "outside the authority window"):
                load(path)

    def test_atomic_readonly_open_and_external_only_binding(self):
        formal = PLOT.read_text()
        self.assertNotIn("frozen", formal.lower())
        self.assertIn("background-producer-root", formal)
        self.assertIn("O_NOFOLLOW", formal)

        runtime_sources = (
            REPO / "gstlal-spiir" / "share" / "scripts" / "crashcar" / "crashcar.sh",
            REPO / "gstlal-spiir" / "share" / "scripts" / "crashcar" / "crashcar_pipeline.sh",
            REPO / "gstlal-spiir" / "share" / "scripts" / "crashcar" / "crashcar_controller.sh",
            REPO / "gstlal-spiir" / "bin" / "gstlal_inspiral_postcohspiir_online",
        )
        for source in runtime_sources:
            text = source.read_text()
            self.assertNotIn("background-producer-root", text)
            self.assertNotIn("crashcar_plot.py", text)

        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            path = root / "single_background.json"
            write_doc(path, document())
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "mode-0444"):
                plot.read_strict_schema4_background(path)
            path.chmod(0o444)
            link = root / "link.json"
            link.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "O_NOFOLLOW"):
                plot.read_strict_schema4_background(link)


if __name__ == "__main__":
    unittest.main(verbosity=2)
