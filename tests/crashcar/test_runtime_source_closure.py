#!/usr/bin/env python3
"""Behavior tests for the bounded crashcar source-closure authority."""

import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "runtime_source_closure.py"
SPEC = importlib.util.spec_from_file_location(
    "crashcar_runtime_source_closure", SOURCE
)
assert SPEC and SPEC.loader
closure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(closure)


class RuntimeSourceClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = closure.build_report()

    def test_closure_is_closed_and_partitioned(self):
        report = self.report
        self.assertTrue(report["all_passed"], report["errors"])
        self.assertEqual(report["python_unresolved"], [])
        self.assertEqual(report["C_unresolved"], [])
        self.assertEqual(report["C_ambiguous"], [])
        production = set(report["production_runtime_hashes"])
        evidence = set(report["evidence_runtime_hashes"])
        self.assertFalse(production & evidence)
        self.assertEqual(len(production), report["production_runtime_count"])
        self.assertEqual(len(evidence), report["evidence_runtime_count"])

    def test_formal_dirty_and_untracked_runtime_bytes_are_bound(self):
        production = self.report["production_runtime_hashes"]
        required = (
            "gstlal-spiir/python/pipemodules/__init__.py",
            "gstlal-spiir/python/pipemodules/spiirparts.py",
            "gstlal-spiir/python/pipemodules/postcoh_finalsink.py",
            "gstlal-spiir/share/scripts/crashcar/crashcar.sh",
            "gstlal-spiir/share/scripts/crashcar/crashcar_controller.sh",
            "gstlal-spiir/share/scripts/crashcar/crashcar_sbatch.sh",
            "gstlal-spiir/share/scripts/crashcar/crashcar_numeric.py",
        )
        for item in required[:-1]:
            self.assertIn(item, production)
        self.assertIn(
            required[-1], self.report["evidence_runtime_hashes"]
        )
        self.assertIn(
            "gstlal-spiir/share/scripts/crashcar/crashcar.sh",
            self.report["production_git_state"]["dirty"],
        )

    def test_dynamic_import_copy_exec_and_build_bindings_are_exact(self):
        report = self.report
        self.assertEqual(
            tuple(report["staged_helpers"]), closure.STAGED_HELPERS
        )
        self.assertTrue(all(report["dynamic_imports"].values()))
        self.assertTrue(all(report["build_checks"].values()))
        self.assertEqual(
            set(report["formal_python_paths"]).issubset(
                set(report["production_runtime_hashes"])
            ),
            True,
        )

    def test_manifests_are_exact_and_exclude_artifacts(self):
        for key in (
            "production_runtime_hashes",
            "evidence_runtime_hashes",
        ):
            mapping = self.report[key]
            text = closure.manifest(mapping)
            self.assertEqual(len(text.splitlines()), len(mapping))
            for item, value in mapping.items():
                self.assertRegex(value, r"^[0-9a-f]{64}$")
                self.assertNotRegex(
                    item,
                    r"(?:\.orig|\.rej|\.bak|\.pytest_cache|__pycache__|"
                    r"\.codex_work|smoke_runs)",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
