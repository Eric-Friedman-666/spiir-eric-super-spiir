#!/usr/bin/env python3
"""Engineering-flow contract tests for the online single-detector sidecar."""

from __future__ import annotations

import csv
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]


class EngineeringFlowContractTests(unittest.TestCase):
    def test_tail_clipping_is_archived_not_active(self) -> None:
        single_source = (SCRIPT_DIR / "single_detector_far.py").read_text()
        plot_source = (SCRIPT_DIR / "plot_single_llr_far.py").read_text()
        self.assertNotIn("_clip_tail_fit_outliers", single_source)
        self.assertNotIn("RankBackground._clip_tail_fit_outliers", plot_source)
        self.assertIn("all available tail points", single_source)
        self.assertIn('"tail_clipping": "disabled"', plot_source)

    def test_worker_owns_exactly_one_bank_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            helper = tmp_path / "helper.sh"
            helper.write_text(
                'run_spiir_py3() { printf "task=%s args=%s\\n" "${SLURM_ARRAY_TASK_ID}" "$#"; }\n'
            )
            env = dict(os.environ)
            env.update({
                "RUN_DIR": str(tmp_path),
                "MAX_GROUP": "5",
                "NODES_AMOUNT": "6",
                "SPIIR_HELPER_FUNCTIONS": str(helper),
            })
            result = subprocess.run(
                ["bash", str(SCRIPT_DIR / "run_bank_group_worker.sh"), "2", "6"],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIn("owns bank group 002", result.stdout)
            self.assertIn("task=2", result.stdout)
            self.assertNotIn("004", result.stdout)

    def test_out_of_range_worker_does_not_run_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            helper = tmp_path / "helper.sh"
            helper.write_text('run_spiir_py3() { printf "SHOULD_NOT_RUN\\n"; }\n')
            env = dict(os.environ)
            env.update({
                "RUN_DIR": str(tmp_path),
                "MAX_GROUP": "5",
                "NODES_AMOUNT": "7",
                "SPIIR_HELPER_FUNCTIONS": str(helper),
            })
            result = subprocess.run(
                ["bash", str(SCRIPT_DIR / "run_bank_group_worker.sh"), "6", "7"],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIn("has no bank group", result.stdout)
            self.assertNotIn("SHOULD_NOT_RUN", result.stdout)

    def test_run_config_defaults_to_wguo_py3_frontend(self) -> None:
        env = dict(os.environ)
        env.update({
            "SPIIR_BUILD_NAME": "",
            "SPIIR_RUN_FUNCTION": "",
            "SPIIR_SOURCE_DIR": "",
        })
        result = subprocess.run(
            [
                "bash",
                "-lc",
                (
                    f"source {shlex.quote(str(SCRIPT_DIR / 'run_config.sh'))}; "
                    'printf "build=%s\\nrunner=%s\\nsource=%s\\n" '
                    '"${SPIIR_BUILD_NAME}" "${SPIIR_RUN_FUNCTION}" "${SPIIR_SOURCE_DIR}"'
                ),
            ],
            check=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertIn("build=wguo-single-det-py3", result.stdout)
        self.assertIn("runner=run_spiir_py3", result.stdout)
        self.assertIn("/build/wguo-single-det-py3/source", result.stdout)

    def test_submit_fails_when_slurm_allocation_has_too_few_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            helper = tmp_path / "helper.sh"
            helper.write_text("# no-op helper for allocation guard test\n")
            env = dict(os.environ)
            env.update({
                "RUN_DIR": str(tmp_path),
                "SCRIPT_DIR": str(SCRIPT_DIR),
                "MAX_GROUP": "1",
                "NODES_AMOUNT": "2",
                "SLURM_JOB_NUM_NODES": "1",
                "SLURM_JOB_ID": "allocation_guard",
                "SPIIR_HELPER_FUNCTIONS": str(helper),
                "SPIIR_RUN_FUNCTION": "run_spiir",
                "AUTO_CLIP_FRAME_CACHE_TO_COMMON_SEGMENT": "0",
            })
            result = subprocess.run(
                ["bash", str(SCRIPT_DIR / "submit.sh")],
                check=False,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("allocated 1 nodes", result.stderr)

    def test_formal_run_config_rejects_short_background_window(self) -> None:
        env = dict(os.environ)
        env.update({
            "BACKGROUND_ACCUMULATION_SECONDS": "600",
            "FORMAL_BACKGROUND_ACCUMULATION_SECONDS": "10800",
            "ALLOW_SHORT_BACKGROUND_DEBUG": "0",
        })
        result = subprocess.run(
            ["bash", "-lc", f"source {shlex.quote(str(SCRIPT_DIR / 'run_config.sh'))}"],
            check=False,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("BACKGROUND_CONTRACT_ERROR", result.stderr)

    def test_short_background_window_requires_explicit_debug_flag(self) -> None:
        env = dict(os.environ)
        env.update({
            "BACKGROUND_ACCUMULATION_SECONDS": "600",
            "FORMAL_BACKGROUND_ACCUMULATION_SECONDS": "10800",
            "ALLOW_SHORT_BACKGROUND_DEBUG": "1",
        })
        result = subprocess.run(
            ["bash", "-lc", f"source {shlex.quote(str(SCRIPT_DIR / 'run_config.sh'))}"],
            check=False,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_direct_ledger_rejects_short_background_without_debug_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "assign_frozen_far_ledger.py"),
                    "--feature-csv",
                    str(tmp_path / "features.csv"),
                    "--output",
                    str(tmp_path / "single_final_far_all.csv"),
                    "--summary",
                    str(tmp_path / "summary.json"),
                    "--background-window-seconds",
                    "600",
                    "--background-required-seconds",
                    "600",
                    "--background-update-seconds",
                    "600",
                ],
                check=False,
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("BACKGROUND_CONTRACT_ERROR", result.stderr)

    def test_append_only_assignment_records_bg_ids_and_calculated_far(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            feature_csv = tmp_path / "features.csv"
            fields = [
                "source_file",
                "source_row",
                "ifo",
                "rho",
                "chisq",
                "tmplt_idx",
                "bankid",
                "end_time",
                "end_time_ns",
                "is_background",
            ]
            rows = []
            for idx in range(70):
                rows.append({
                    "source_file": "000/000_zerolag_0_300.xml.gz",
                    "source_row": idx,
                    "ifo": "H1",
                    "rho": 5.0 + (idx % 8) * 0.2,
                    "chisq": 1.0 + (idx % 6) * 0.1,
                    "tmplt_idx": idx % 5,
                    "bankid": 0,
                    "end_time": 1 + idx * 4,
                    "end_time_ns": 0,
                    "is_background": 0,
                })
            rows.extend([
                {
                    "source_file": "000/000_zerolag_300_100.xml.gz",
                    "source_row": 1000,
                    "ifo": "H1",
                    "rho": 8.5,
                    "chisq": 1.1,
                    "tmplt_idx": 2,
                    "bankid": 0,
                    "end_time": 320,
                    "end_time_ns": 0,
                    "is_background": 0,
                },
                {
                    "source_file": "000/000_zerolag_400_100.xml.gz",
                    "source_row": 1001,
                    "ifo": "H1",
                    "rho": 8.9,
                    "chisq": 1.2,
                    "tmplt_idx": 3,
                    "bankid": 0,
                    "end_time": 420,
                    "end_time_ns": 0,
                    "is_background": 0,
                },
            ])
            with feature_csv.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            output = tmp_path / "single_final_far_all.csv"
            archive = tmp_path / "backgrounds"
            summaries = []
            for run_index in (1, 2):
                summary = tmp_path / f"summary{run_index}.json"
                subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT_DIR / "assign_frozen_far_ledger.py"),
                        "--feature-csv",
                        str(feature_csv),
                        "--output",
                        str(output),
                        "--candidate-output",
                        str(tmp_path / "candidates.csv"),
                        "--summary",
                        str(summary),
                        "--ifos",
                        "H1,L1",
                        "--min-snr",
                        "4",
                        "--background-window-seconds",
                        "300",
                        "--background-required-seconds",
                        "300",
                        "--background-update-seconds",
                        "100",
                        "--allow-short-background-debug",
                        "--data-start-gps",
                        "0",
                        "--fit-min-points",
                        "2",
                        "--background-archive-dir",
                        str(archive),
                    ],
                    check=True,
                    cwd=str(SCRIPT_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                summaries.append(json.loads(summary.read_text()))

            with output.open(newline="") as handle:
                assigned_rows = list(csv.DictReader(handle))
            self.assertEqual(summaries[0]["newly_assigned_rows"], 2)
            self.assertEqual(summaries[1]["newly_assigned_rows"], 0)
            self.assertEqual(summaries[1]["duplicate_candidate_rows"], 2)
            self.assertEqual([row["assign_bg_id"] for row in assigned_rows], ["BG-000", "BG-001"])
            self.assertTrue((archive / "BG-000.json").exists())
            self.assertTrue((archive / "BG-001.json").exists())
            for row in assigned_rows:
                self.assertTrue(row["assigned_far"])
                self.assertTrue(row["calculated_far"])
                self.assertTrue(row["assign_bg_file"])

    def test_fixed_background_assignment_does_not_build_bg_from_injections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fields = [
                "source_file",
                "source_row",
                "ifo",
                "rho",
                "chisq",
                "tmplt_idx",
                "bankid",
                "end_time",
                "end_time_ns",
                "is_background",
            ]

            noinj_features = tmp_path / "noinj_features.csv"
            rows = []
            for idx in range(80):
                rows.append({
                    "source_file": "000/000_zerolag_0_600.xml.gz",
                    "source_row": idx,
                    "ifo": "H1",
                    "rho": 4.5 + (idx % 10) * 0.1,
                    "chisq": 1.0 + (idx % 7) * 0.05,
                    "tmplt_idx": idx % 5,
                    "bankid": 0,
                    "end_time": 1 + idx * 5,
                    "end_time_ns": 0,
                    "is_background": 0,
                })
            rows.append({
                "source_file": "000/000_zerolag_600_100.xml.gz",
                "source_row": 1000,
                "ifo": "H1",
                "rho": 6.2,
                "chisq": 1.1,
                "tmplt_idx": 2,
                "bankid": 0,
                "end_time": 620,
                "end_time_ns": 0,
                "is_background": 0,
            })
            with noinj_features.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            fixed_bg = tmp_path / "fixed_noinj_background.json"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "single_detector_far.py"),
                    "feature-csv",
                    "--feature-csv",
                    str(noinj_features),
                    "--output",
                    str(tmp_path / "bootstrap.csv"),
                    "--background-output",
                    str(fixed_bg),
                    "--ifos",
                    "H1,L1",
                    "--min-snr",
                    "4",
                    "--foreground-count",
                    "1",
                    "--bootstrap-background-from-foreground",
                    "--background-livetime",
                    "600",
                    "--fit-min-points",
                    "2",
                ],
                check=True,
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertTrue(fixed_bg.exists())

            inj_features = tmp_path / "inj_features.csv"
            with inj_features.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows([
                    {
                        "source_file": "000/000_zerolag_700_100.xml.gz",
                        "source_row": 2000,
                        "ifo": "H1",
                        "rho": 35.0,
                        "chisq": 0.8,
                        "tmplt_idx": 1,
                        "bankid": 0,
                        "end_time": 720,
                        "end_time_ns": 0,
                        "is_background": 0,
                    },
                    {
                        "source_file": "000/000_zerolag_800_100.xml.gz",
                        "source_row": 2001,
                        "ifo": "H1",
                        "rho": 42.0,
                        "chisq": 0.7,
                        "tmplt_idx": 2,
                        "bankid": 0,
                        "end_time": 830,
                        "end_time_ns": 0,
                        "is_background": 0,
                    },
                ])

            output = tmp_path / "single_final_far_all.csv"
            summary = tmp_path / "fixed_summary.json"
            archive = tmp_path / "should_not_be_created"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "assign_frozen_far_ledger.py"),
                    "--feature-csv",
                    str(inj_features),
                    "--output",
                    str(output),
                    "--candidate-output",
                    str(tmp_path / "fixed_candidates.csv"),
                    "--summary",
                    str(summary),
                    "--ifos",
                    "H1,L1",
                    "--min-snr",
                    "4",
                    "--background-window-seconds",
                    "300",
                    "--background-required-seconds",
                    "300",
                    "--background-update-seconds",
                    "100",
                    "--allow-short-background-debug",
                    "--fit-min-points",
                    "2",
                    "--background-archive-dir",
                    str(archive),
                    "--fixed-background-input",
                    str(fixed_bg),
                    "--fixed-background-id",
                    "NOINJ-BG",
                    "--fixed-background-source",
                    "unit-test-noinj",
                ],
                check=True,
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            fixed_summary = json.loads(summary.read_text())
            self.assertTrue(fixed_summary["fixed_background"])
            self.assertTrue(fixed_summary["background_accumulation_disabled"])
            self.assertEqual(fixed_summary["newly_assigned_rows"], 2)
            self.assertFalse(archive.exists())
            with output.open(newline="") as handle:
                assigned_rows = list(csv.DictReader(handle))
            self.assertEqual(len(assigned_rows), 2)
            for row in assigned_rows:
                self.assertEqual(row["assign_bg_id"], "NOINJ-BG")
                self.assertEqual(row["assign_bg_file"], str(fixed_bg))
                self.assertTrue(row["assigned_far"])

    def test_manual_update_uses_script_dir_from_frozen_run_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            logs = tmp_path / "logs"
            logs.mkdir()
            (logs / "run_config_1.env").write_text(
                "\n".join([
                    f"SCRIPT_DIR={shlex.quote(str(SCRIPT_DIR))}",
                    "NODES_AMOUNT=6",
                    "MAX_GROUP=5",
                    "SINGLE_INPUT_KIND=zerolag",
                    "BANKS_PER_GROUP=6",
                    "BACKGROUND_ACCUMULATION_SECONDS=300",
                    "BACKGROUND_UPDATE_TRIGGER_SECONDS=100",
                    "MERGE_WORKER_FAR_OUTPUTS=0",
                    "",
                ])
            )
            env = dict(os.environ)
            env.update({
                "SINGLE_WORKER_ID": "2",
                "SINGLE_WORKER_GROUP": "2",
                "SINGLE_WORKER_COUNT": "6",
            })
            subprocess.run(
                [
                    "bash",
                    str(SCRIPT_DIR / "update_single_background_once.sh"),
                    str(tmp_path),
                ],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            status = json.loads(
                (tmp_path / "monitor/worker_2/latest_single_background_status.json").read_text()
            )
            self.assertEqual(status["worker_group"], "2")
            self.assertFalse(status["background_ready"])
            self.assertEqual(status["foreground_feature_rows_total"], 0)

    def test_monitor_reads_shell_quoted_run_config_paths(self) -> None:
        sys.path.insert(0, str(SCRIPT_DIR))
        import monitor_run_table

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            logs = tmp_path / "logs"
            logs.mkdir()
            expected = "/tmp/Eric super spiir/results/run_x"
            (logs / "run_config_1.env").write_text(
                f"RUN_DIR={shlex.quote(expected)}\nONLINE_REPLAY_START_WALL=''\n"
            )
            config = monitor_run_table.read_run_config(tmp_path)
            self.assertEqual(config["RUN_DIR"], expected)
            self.assertEqual(config["ONLINE_REPLAY_START_WALL"], "")

    def test_four_panel_prefers_assigned_far_for_single_rows(self) -> None:
        sys.path.insert(0, str(SCRIPT_DIR))
        import plot_final_four_panel_summary as plot_summary

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "single.csv"
            with csv_path.open("w", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["rho", "chisq", "far", "assigned_far"],
                )
                writer.writeheader()
                writer.writerow({
                    "rho": "8",
                    "chisq": "1.5",
                    "far": "",
                    "assigned_far": "0.001",
                })
            points = plot_summary.read_csv_points(
                str(csv_path),
                label="single",
                snr_field="rho",
                chisq_field="chisq",
                far_fields=("assigned_far", "far"),
            )
            self.assertEqual(points.count, 1)
            self.assertAlmostEqual(float(points.z[0]), -3.0)


if __name__ == "__main__":
    unittest.main()
