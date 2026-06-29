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
CRASHCAR_SCRIPT_DIR = SCRIPT_DIR.parents[1] / "share" / "scripts" / "crashcar"
CRASHCAR_SINGLEFAR_C = SCRIPT_DIR.parents[1] / "gst" / "cuda" / "cohfar" / "crashcar_singlefar.c"


class EngineeringFlowContractTests(unittest.TestCase):
    def test_tail_clipping_is_archived_not_active(self) -> None:
        single_source = (SCRIPT_DIR / "single_detector_far.py").read_text()
        plot_source = (SCRIPT_DIR / "plot_single_llr_far.py").read_text()
        self.assertNotIn("_clip_tail_fit_outliers", single_source)
        self.assertNotIn("RankBackground._clip_tail_fit_outliers", plot_source)
        self.assertIn("all available tail points", single_source)
        self.assertIn('"tail_clipping": "disabled"', plot_source)

    def test_o3a_online_frontier_bg_is_no_injection_chunked(self) -> None:
        source = (SCRIPT_DIR / "run_o3a_bns_online_frontier_bg.sh").read_text()
        self.assertIn("Online-frontier controller", source)
        self.assertIn("WGUO_O3A_INJECTION_MODE=none", source)
        self.assertIn('WGUO_O3A_INJECTION_FILE=""', source)
        self.assertIn("CHUNK_SECONDS=${CHUNK_SECONDS:-86400}", source)
        self.assertIn("NUM_CHUNKS=${NUM_CHUNKS:-7}", source)
        self.assertIn("stats_loc=\"${dir}\"", source)
        self.assertIn("Future online injection tests must expose injection rows chunk by chunk", source)

    def test_o3a_py3_wrapper_requires_external_multi_background(self) -> None:
        source = (SCRIPT_DIR / "wguo_o3a_bns_py3_pipeline.sh").read_text()
        self.assertIn("O3A_BNS_PIPELINE_ERROR missing external multi background stats", source)
        self.assertIn("external multi background points inside current run", source)
        self.assertIn("--cohfar-assignfar-input-fname", source)
        self.assertIn("--finalsink-fapupdater-output-fname", source)
        self.assertIn("DO_NOT_USE_AS_BACKGROUND_INJECTION_STATS.txt", source)

    def test_crashcar_launcher_routes_injection_to_frozen_workflow(self) -> None:
        source = (CRASHCAR_SCRIPT_DIR / "crashcar.sh").read_text()
        self.assertIn("crashcar.sh \\", source)
        self.assertIn("crashcar_frozen_injection_workflow.sh", source)
        self.assertIn("INJECTION_MODE_NORMALIZED", source)
        self.assertIn('INTERNAL_STAGE=${crashcar_internal_stage:-${CRASHCAR_INTERNAL_STAGE:-0}}', source)
        self.assertIn('CONTROLLER_SCRIPT="${RUN_ROOT}/scripts/crashcar_frozen_injection_workflow.sh"', source)
        self.assertIn('CONTROLLER_SCRIPT="${RUN_ROOT}/scripts/crashcar_controller.sh"', source)

    def test_crashcar_frozen_injection_workflow_uses_lower_data_contract(self) -> None:
        source = (CRASHCAR_SCRIPT_DIR / "crashcar_frozen_injection_workflow.sh").read_text()
        for required in [
            "require_var injection_data_file",
            "require_var injection_detector_response_file",
            "require_var injection_start_gps",
            "require_var injection_segment_xml",
            "require_var injection_bg_data_file",
            "require_var injection_bg_detector_response_file",
            "require_var injection_bg_start_gps",
            "require_var injection_bg_segment_xml",
        ]:
            self.assertIn(required, source)
        self.assertIn("duration_seconds_from injection_bg_duration_seconds injection_bg_duration_hour", source)
        self.assertIn("duration_seconds_from injection_duration_seconds injection_duration_hour", source)
        self.assertIn("INJ_CHUNK_SECONDS=${injection_chunk_seconds", source)
        self.assertIn("data_file=${injection_bg_data_file}", source)
        self.assertIn("data_file=${injection_data_file}", source)
        self.assertIn("detector_response_file=${injection_detector_response_file}", source)
        self.assertIn("duration=${BG_DURATION_SECONDS}", source)
        self.assertIn("background_accumulation=${BG_ACCUM_SECONDS}", source)
        self.assertIn("filter_injection_chunk", source)
        self.assertIn("noninj_stats_loc=${FROZEN_MULTI_DIR}", source)
        self.assertIn("single_background_mode=frozen", source)
        self.assertIn("single_frozen_background_json=${SINGLE_BG_JSON}", source)
        self.assertIn("crashcar_build_last_bg_artifacts=0", source)
        self.assertIn("INJ_SNR_LOG_FAR=${injection_snr_series_logFAR_threshold:-${INJECTION_SNR_SERIES_LOGFAR_THRESHOLD:-90}}", source)
        self.assertIn("crashcar_preserve_table_single_far=1", source)

    def test_crashcar_controller_forbids_unfrozen_injection_backgrounds(self) -> None:
        source = (CRASHCAR_SCRIPT_DIR / "crashcar_controller.sh").read_text()
        self.assertIn('injection_mode=True requires single_background_mode=frozen', source)
        self.assertIn('injection_bg_duration_seconds or injection_bg_duration_hour required when injection_mode=True', source)
        self.assertIn("single_background_mode=frozen requires", source)
        self.assertIn("run_single_ledger_final_update", source)
        self.assertIn("PATCH_ZEROLAG_SINGLE_SNR_SERIES_VALUE", source)
        self.assertIn('export GST_DEBUG="${GST_DEBUG:-}"', source)
        self.assertIn('sbatch_args+=(--partition="${SLURM_PARTITION}")', source)
        self.assertIn("skipping local background artifact build for this stage", source)

    def test_crashcar_can_export_full_snr_series_evidence_surface(self) -> None:
        source = CRASHCAR_SINGLEFAR_C.read_text()
        self.assertIn("write_all_snr_series", source)
        self.assertIn("element->snr_series_log10_far_threshold >= 90.0", source)
        self.assertIn("write_all_snr_series || snr_series_hit_single_far", source)

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

    def test_full_background_triggers_drive_loaded_far_assignment(self) -> None:
        sys.path.insert(0, str(SCRIPT_DIR))
        from single_detector_far import (
            SingleDetectorBranch,
            SingleDetectorFeature,
            make_default_likelihood_model,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bg_file = tmp_path / "full_background.json"
            truncated_file = tmp_path / "truncated_support_background.json"
            background_features = [
                SingleDetectorFeature(
                    ifo="H1",
                    rho=4.05 + 0.04 * idx,
                    chisq=0.8 + 0.03 * (idx % 9),
                    tmplt_idx=idx % 5,
                    bankid=0,
                    end_time=1000 + 10 * idx,
                    end_time_ns=0,
                )
                for idx in range(60)
            ]

            branch = SingleDetectorBranch(
                make_default_likelihood_model(),
                ifos=("H1",),
                min_snr=4.0,
                background_window_seconds=3600,
                fit_min_points=2,
            )
            branch.add_livetime(3600, ["H1"])
            branch.rebuild_background_support(background_features)
            branch.write_background_file(str(bg_file))

            data = json.loads(bg_file.read_text())
            h1_background = data["backgrounds"]["H1"]
            self.assertEqual(h1_background["background_trigger_count"], 60)
            self.assertEqual(len(h1_background["background_triggers"]), 60)
            self.assertEqual(len(h1_background["far_llr_points"]), 60)

            h1_background["far_llr_points"] = h1_background["far_llr_points"][:3]
            h1_background["support_count"] = 3
            truncated_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

            loaded = SingleDetectorBranch(
                make_default_likelihood_model(),
                ifos=("H1",),
                min_snr=4.0,
                background_window_seconds=3600,
                fit_min_points=2,
            )
            loaded.load_background_file(str(truncated_file))
            loaded_bg = loaded.background["H1"]
            self.assertEqual(len(loaded_bg.background_triggers), 60)
            self.assertEqual(len(loaded_bg), 60)
            self.assertEqual(len(loaded_bg.far_llr_points), 60)

            query = background_features[20]
            llr = loaded.rank_feature(query)
            expected_direct_far = (
                max(float(loaded_bg.count_ge(llr)), loaded_bg.far_floor_count)
                / loaded_bg.livetime
            )
            result = loaded.assign_feature(query)
            self.assertAlmostEqual(result.direct_far, expected_direct_far)

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

    def test_crashcar_online_cli_exposes_finalsink_single_trigger_stream(self) -> None:
        source = (
            SCRIPT_DIR.parents[1] / "bin/gstlal_inspiral_postcohspiir_online"
        ).read_text()
        self.assertIn("--finalsink-single-trigger-stream", source)
        self.assertIn("single_trigger_stream_fname=options.finalsink_single_trigger_stream", source)

    def test_finalsink_single_trigger_stream_uses_text_csv_writer(self) -> None:
        source = (
            SCRIPT_DIR.parents[1] / "python/pipemodules/postcoh_finalsink.py"
        ).read_text()
        self.assertIn('open(self.single_trigger_stream_fname, "a", newline="")', source)
        self.assertNotIn('open(self.single_trigger_stream_fname, "ab")', source)

    def test_finalsink_boundary_rows_do_not_add_detector_specific_extra_columns(self) -> None:
        source = (
            SCRIPT_DIR.parents[1] / "python/pipemodules/postcoh_finalsink.py"
        ).read_text()
        boundary_source = source.split("def _append_single_trigger_stream_boundaries", 1)[1]
        boundary_source = boundary_source.split("def run_snapshot", 1)[0]
        self.assertNotIn('row["end_time_sngl_%s" % ifo]', boundary_source)
        self.assertNotIn('row["end_time_ns_sngl_%s" % ifo]', boundary_source)

    def test_crashcar_pipeline_requires_template_shape_map_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_online = tmp_path / "gstlal_inspiral_postcohspiir_online"
            fake_online.write_text("#!/bin/sh\nprintf '%s\\n' --finalsink-single-trigger-stream\n")
            fake_online.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "SLURM_ARRAY_TASK_ID": "0",
                "BANK_DIR": str(tmp_path),
                "DATA_START_TIME": "1",
                "DATA_END_TIME": "2",
                "NONINJ_STATS_LOC": str(tmp_path),
                "DETRSP_MAP": str(tmp_path / "map.xml"),
                "FRAME_CACHE_FILE": str(tmp_path / "frames.cache"),
                "START_BANK": "0",
                "BANKS_PER_GROUP": "1",
                "ZEROLAG_SNAPSHOT_INTERVAL_SECONDS": "60",
                "BACKGROUND_STATS_WINDOWS": "1d",
                "CRASHCAR_ENABLE": "1",
                "CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP": "1",
                "CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME": str(tmp_path / "missing.csv"),
                "SPIIR_ONLINE_BIN": str(fake_online),
            })
            result = subprocess.run(
                ["bash", str(SCRIPT_DIR / "pipeline.sh")],
                check=False,
                env=env,
                cwd=str(tmp_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("crashcar requires readable CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME", result.stderr)

    def test_pipeline_rejects_missing_finalsink_single_trigger_stream_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_online = tmp_path / "gstlal_inspiral_postcohspiir_online"
            fake_online.write_text("#!/bin/sh\nexit 0\n")
            fake_online.chmod(0o755)
            shape_map = tmp_path / "shape.csv"
            shape_map.write_text("ifo_id,bankid,tmplt_idx,autocorr_power,dof\n")
            env = dict(os.environ)
            env.update({
                "SLURM_ARRAY_TASK_ID": "0",
                "BANK_DIR": str(tmp_path),
                "DATA_START_TIME": "1",
                "DATA_END_TIME": "2",
                "NONINJ_STATS_LOC": str(tmp_path),
                "DETRSP_MAP": str(tmp_path / "map.xml"),
                "FRAME_CACHE_FILE": str(tmp_path / "frames.cache"),
                "START_BANK": "0",
                "BANKS_PER_GROUP": "1",
                "ZEROLAG_SNAPSHOT_INTERVAL_SECONDS": "60",
                "BACKGROUND_STATS_WINDOWS": "1d",
                "CRASHCAR_ENABLE": "1",
                "CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP": "1",
                "CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME": str(shape_map),
                "SINGLE_TRIGGER_STREAM_ENABLE": "1",
                "SINGLE_INPUT_KIND": "singlecsv",
                "SPIIR_ONLINE_BIN": str(fake_online),
            })
            result = subprocess.run(
                ["bash", str(SCRIPT_DIR / "pipeline.sh")],
                check=False,
                env=env,
                cwd=str(tmp_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("lacks --finalsink-single-trigger-stream", result.stderr)


if __name__ == "__main__":
    unittest.main()
