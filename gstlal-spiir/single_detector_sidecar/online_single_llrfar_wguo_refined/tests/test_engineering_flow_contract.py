#!/usr/bin/env python3
"""Engineering-flow contract tests for the online single-detector sidecar."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
USER_CRASHCAR_ENV = SCRIPT_DIR.parents[2] / "scripts" / "crashcar.env"
CRASHCAR_SCRIPT_DIR = SCRIPT_DIR.parents[1] / "share" / "scripts" / "crashcar"
CRASHCAR_SINGLEFAR_C = SCRIPT_DIR.parents[1] / "gst" / "cuda" / "cohfar" / "crashcar_singlefar.c"
POSTCOHSPIIR_ONLINE = SCRIPT_DIR.parents[1] / "bin" / "gstlal_inspiral_postcohspiir_online"
CRASHCAR_SPIIRPARTS = SCRIPT_DIR.parents[1] / "python" / "pipemodules" / "spiirparts.py"
CRASHCAR_PLOT = SCRIPT_DIR.parents[1] / "bin" / "crashcar_plot.py"


class EngineeringFlowContractTests(unittest.TestCase):
    def _causal_case(self):
        tests_dir = SCRIPT_DIR / "tests"
        for path in (tests_dir, SCRIPT_DIR):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        import test_sidecar_causal_engine as causal_tests

        case = causal_tests.CausalEngineTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        return causal_tests, case
    def test_single_detector_branch_requires_exact_h1_l1(self) -> None:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        from single_detector_far import (
            SingleDetectorBranch,
            make_default_likelihood_model,
        )

        branch = SingleDetectorBranch(
            make_default_likelihood_model(),
            ifos=("H1", "L1"),
            min_snr=4.0,
            background_window_seconds=3600,
            fit_min_points=2,
        )
        self.assertEqual(branch.ifos, ("H1", "L1"))
        for invalid_ifos in (("H1",), ("L1",), ("H1", "L1", "V1")):
            with self.assertRaisesRegex(
                    ValueError,
                    "formal single-detector branch requires exactly H1,L1"):
                SingleDetectorBranch(
                    make_default_likelihood_model(),
                    ifos=invalid_ifos,
                    min_snr=4.0,
                    background_window_seconds=3600,
                    fit_min_points=2,
                )

    def test_tail_clipping_is_archived_not_active(self) -> None:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import verification_sidecar_numeric as numeric

        ranks = [0.0, 1.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 100.0]
        r_tail, slope, points = numeric.build_anchored_tail(ranks, 1000.0)
        self.assertEqual(len(points), len(set(ranks)))
        self.assertEqual(points[-1][0], 100.0)
        self.assertEqual(slope, numeric.fit_anchored_tail(points, r_tail))
        direct, direct_branch, _count, _floor = numeric.assigned_far(
            ranks, r_tail, 1000.0, r_tail, slope)
        expected, _count, _floor = numeric.calculated_far(
            ranks, r_tail, 1000.0)
        self.assertEqual(direct_branch, "direct")
        self.assertEqual(direct.hex(), expected.hex())
        tail, tail_branch, _count, _floor = numeric.assigned_far(
            ranks, math.nextafter(r_tail, math.inf),
            1000.0, r_tail, slope)
        self.assertEqual(tail_branch, "tail")
        self.assertTrue(math.isfinite(tail) and tail > 0.0)

    def test_formal_workflow_runs_one_continuous_pipeline_per_stage(self) -> None:
        workflow = (
            CRASHCAR_SCRIPT_DIR / "crashcar_frozen_injection_workflow.sh"
        ).read_text()
        pipeline = (CRASHCAR_SCRIPT_DIR / "crashcar_pipeline.sh").read_text()
        self.assertIn(
            'run_stage "${BG_CONFIG}" "no-injection background"',
            workflow)
        self.assertIn(
            'run_stage "${INJ_CONFIG}" "continuous injection"',
            workflow)
        self.assertIn("injection_chunks=disabled", workflow)
        self.assertNotIn("filter_injection_chunk", workflow)
        self.assertNotIn('"injection_chunk_seconds=', workflow)
        self.assertNotIn('"injection_chunk_hour=', workflow)
        self.assertEqual(
            [line.strip() for line in pipeline.splitlines()].count(
                '"${cmd[@]}"'),
            1)

    def test_o3a_py3_wrapper_requires_external_multi_background(self) -> None:
        source = (SCRIPT_DIR / "wguo_o3a_bns_py3_pipeline.sh").read_text()
        self.assertIn("O3A_BNS_PIPELINE_ERROR missing external multi background stats", source)
        self.assertIn("external multi background points inside current run", source)
        self.assertIn("--cohfar-assignfar-input-fname", source)

    def test_postcohspiir_blind_injections_parser_has_py3_guard(self) -> None:
        source = POSTCOHSPIIR_ONLINE.read_text()
        self.assertIn('"--blind-injections"', source)
        self.assertIn('if not hasattr(options, "injections"):', source)
        self.assertIn("options.injections = None", source)
        self.assertIn("--finalsink-fapupdater-output-fname", source)

    def test_crashcar_launcher_routes_injection_to_frozen_workflow(self) -> None:
        source = (CRASHCAR_SCRIPT_DIR / "crashcar.sh").read_text()
        self.assertIn("crashcar.sh \\", source)
        self.assertIn("crashcar_frozen_injection_workflow.sh", source)
        self.assertIn("INJECTION_MODE_NORMALIZED", source)
        self.assertIn('INTERNAL_STAGE=${crashcar_internal_stage:-${CRASHCAR_INTERNAL_STAGE:-0}}', source)
        self.assertIn('CONTROLLER_SCRIPT="${RUN_ROOT}/scripts/crashcar_frozen_injection_workflow.sh"', source)
        self.assertIn('CONTROLLER_SCRIPT="${RUN_ROOT}/scripts/crashcar_controller.sh"', source)

    def test_crashcar_env_uses_common_noninject_inject_sections(self) -> None:
        source = USER_CRASHCAR_ENV.read_text()
        common_pos = source.index("injection_mode=")
        noninject_pos = source.index("#non-inject")
        inject_pos = source.index("#inject")
        self.assertLess(common_pos, noninject_pos)
        self.assertLess(noninject_pos, inject_pos)
        self.assertIn("bank_file=", source[:common_pos])
        self.assertNotIn("slurm_partition=", source)
        noninject_block = source[noninject_pos:inject_pos]
        inject_block = source[inject_pos:]
        self.assertIn("data_file=", noninject_block)
        self.assertIn("detector_response_file=", noninject_block)
        self.assertIn("start_gps=", noninject_block)
        self.assertIn("duration_hour=", noninject_block)
        self.assertIn("zerolag_update_hour=", source[:common_pos])
        self.assertNotIn("injection_bg_", noninject_block)
        self.assertIn("injection_bg_start_gps=", inject_block)
        self.assertIn("injection_bg_duration_hour=", inject_block)
        self.assertNotIn("injection_bg_BG_accumulation_hour=", inject_block)
        self.assertNotIn("injection_bg_BG_update_hour=", inject_block)
        self.assertIn("injection_file=", inject_block)
        foreground_block = inject_block[inject_block.index("injection_file="):]
        for required in [
            "injection_file=",
            "injection_data_file=",
            "injection_detector_response_file=",
            "injection_segment_xml=",
            "injection_start_gps=",
            "injection_duration_hour=",
            "injection_worker_number=",
            "injection_bank_per_worker=",
        ]:
            self.assertIn(required, foreground_block)
        self.assertNotIn("injection_BG_accumulation_hour=", foreground_block)
        self.assertNotIn("injection_BG_update_hour=", foreground_block)
        self.assertNotIn("injection_chunk_hour=", foreground_block)
        self.assertNotIn("injection_SNR_series_logFAR_threshold=", foreground_block)
        self.assertNotIn("injection_bg_seed_noninj_stats_loc=", inject_block)
        self.assertNotIn("injection_bg_accumulation_hour=", inject_block)
        self.assertNotIn("injection_snr_series_logFAR_threshold=", inject_block)
        self.assertNotIn("Sentinels", source)
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            self.assertNotEqual(value, "", f"{key} must have an explicit default")

    def test_crashcar_launcher_keeps_normal_o3_path_when_injection_mode_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            save_dir = tmp_path / "saved_runs"
            run_root = save_dir / "normal_o3" / "20260629T000000Z"
            config = tmp_path / "normal_o3.env"
            config.write_text(
                "\n".join([
                    f"root={SCRIPT_DIR.parents[1]}",
                    f"save_dir={save_dir}",
                    "run_id=normal_o3",
                    "run_timestamp=20260629T000000Z",
                    "crashcar_dry_run=1",
                    "injection_mode=False",
                    "data_file=/normal/o3/frame.cache",
                    "detector_response_file=/normal/o3/detrsp.xml",
                    "start_gps=1246886767",
                    "duration=3600",
                    "segment_xml=/normal/o3/segments.xml",
                    "injection_data_file=/must/not/control/normal/mode.cache",
                    "injection_detector_response_file=/must/not/control/normal/mode_detrsp.xml",
                ]) + "\n"
            )
            result = subprocess.run(
                ["bash", str(CRASHCAR_SCRIPT_DIR / "crashcar.sh"), str(config)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            readme = (run_root / "README.crashcar_launch.txt").read_text()
            staged_config = (run_root / "scripts" / "crashcar.env").read_text()
            self.assertIn("single-stage controller", readme)
            self.assertIn(str(run_root), result.stdout)
            self.assertIn("crashcar_controller.sh", result.stdout)
            self.assertNotIn("crashcar_frozen_injection_workflow.sh", result.stdout)
            self.assertIn(f"save_dir={save_dir}", staged_config)
            self.assertIn("data_file=/normal/o3/frame.cache", staged_config)
            self.assertIn("detector_response_file=/normal/o3/detrsp.xml", staged_config)
            self.assertNotIn("source_root=", staged_config)

    def test_crashcar_frozen_injection_workflow_freezes_matching_bundle(self) -> None:
        source = (
            CRASHCAR_SCRIPT_DIR / "crashcar_frozen_injection_workflow.sh"
        ).read_text()
        for required in (
            "require_var injection_data_file",
            "require_var injection_detector_response_file",
            "require_var injection_start_gps",
            "require_var injection_segment_xml",
            "require_var injection_bg_data_file",
            "require_var injection_bg_detector_response_file",
            "require_var injection_bg_start_gps",
            "require_var injection_bg_segment_xml",
        ):
            self.assertIn(required, source)
        self.assertIn(
            "duration_seconds_from injection_bg_duration_seconds "
            "injection_bg_duration_hour", source)
        self.assertIn(
            "duration_seconds_from injection_duration_seconds "
            "injection_duration_hour", source)
        self.assertIn(
            'freeze_background_bundle "${BG_RUN_ROOT}" "${FROZEN_BUNDLE_DIR}"',
            source)
        self.assertIn(
            'source_single = worker_run / "single_background.json"', source)
        self.assertIn(
            'source = worker_run / f"{jobno}_marginalized_stats_{span}.xml.gz"',
            source)
        self.assertIn('"background_run_root": str(bg_root)', source)
        self.assertIn("single_background_mode=frozen", source)
        self.assertIn("noninj_stats_loc=${FROZEN_MULTI_DIR}", source)
        self.assertIn("injection_chunks=disabled", source)
        self.assertIn('"continuous injection"', source)
        self.assertNotIn("filter_injection_chunk", source)
        self.assertNotIn("FROZEN_MULTI_FALLBACK_DIR", source)
        self.assertNotIn("fallback_src=", source)

    def test_crashcar_controller_forbids_unfrozen_injection_backgrounds(self) -> None:
        controller_source = (
            CRASHCAR_SCRIPT_DIR / "crashcar_controller.sh").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            controller = scripts / "crashcar_controller.sh"
            controller.write_text(controller_source)
            controller.chmod(0o755)
            for name in (
                    "frames.cache", "detrsp.xml", "segments.xml",
                    "injections.xml"):
                (root / name).write_text("fixture\n")
            (root / "banks").mkdir()
            config = scripts / "crashcar.env"
            config.write_text("\n".join((
                f"root={SCRIPT_DIR.parents[1]}",
                "start_gps=100",
                "duration=10",
                f"detector_response_file={root / 'detrsp.xml'}",
                f"data_file={root / 'frames.cache'}",
                f"segment_xml={root / 'segments.xml'}",
                f"bank_file={root / 'banks'}",
                "dof=120",
                "injection_mode=True",
                f"injection_file={root / 'injections.xml'}",
                f"injection_bg_data_file={root / 'frames.cache'}",
                f"injection_bg_detector_response_file={root / 'detrsp.xml'}",
                "injection_bg_start_gps=100",
                "injection_bg_duration_seconds=10",
                f"injection_bg_segment_xml={root / 'segments.xml'}",
                "single_background_mode=rolling",
                "",
            )))
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            sbatch_marker = root / "sbatch_was_called"
            fake_sbatch = fake_bin / "sbatch"
            fake_sbatch.write_text(
                "#!/bin/sh\nprintf called > \"$SBATCH_MARKER\"\nexit 99\n")
            fake_sbatch.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "CRASHCAR_CONFIG_FILE": str(config),
                "PATH": f"{fake_bin}:{env['PATH']}",
                "SBATCH_MARKER": str(sbatch_marker),
            })
            result = subprocess.run(
                ["bash", str(controller)],
                cwd=str(root), env=env, check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(
                "requires single_background_mode=frozen", result.stderr)
            self.assertFalse(sbatch_marker.exists())

    def test_crashcar_pipeline_marks_injection_stats_as_non_background(self) -> None:
        source = (CRASHCAR_SCRIPT_DIR / "crashcar_pipeline.sh").read_text()
        self.assertIn("DO_NOT_USE_AS_BACKGROUND_INJECTION_STATS.txt", source)
        self.assertIn("Do not use local accumulated backgrounds from this injection foreground", source)

    def test_normal_finalsink_owns_snr_series_evidence(self) -> None:
        crashcar_c = CRASHCAR_SINGLEFAR_C.read_text()
        finalsink = (
            SCRIPT_DIR.parents[1] /
            "python/pipemodules/postcoh_finalsink.py").read_text()
        online = POSTCOHSPIIR_ONLINE.read_text()
        pipeline = (CRASHCAR_SCRIPT_DIR / "crashcar_pipeline.sh").read_text()
        self.assertIn('"--snr-series-logfar-threshold"', online)
        self.assertIn(
            "snr_series_logfar_threshold="
            "options.snr_series_logfar_threshold", online)
        self.assertIn(
            '--snr-series-logfar-threshold "${snr_series_logfar_threshold}"',
            pipeline)
        self.assertIn("def __write_cluster_zero_coinc_if_needed", finalsink)
        self.assertIn("assemble_ligolw_snr_series_arrays", finalsink)
        self.assertIn("lal.series.build_COMPLEX8TimeSeries", finalsink)
        self.assertNotIn("write_all_snr_series", crashcar_c)
        self.assertNotIn("snr_series_output_dir", crashcar_c)

    def test_causal_worker_bank_mapping_fails_closed(self) -> None:
        causal, case = self._causal_case()
        engine = case._engine()
        results = engine.process_rows([
            causal.make_row(1, 100, 1, bank=1),
        ])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(
            item["status"] == causal.subject.STATUS_FAILED_LLR
            and item["reason"] == "worker_bank_mapping_mismatch"
            for item in results))

    def test_causal_live_run_must_complete_one_background_window(self) -> None:
        causal, case = self._causal_case()
        with self.assertRaisesRegex(
                causal.subject.CausalContractError,
                "run cannot complete one background window"):
            case._engine(run_end=500, l1_end=500)

    def test_causal_epoch_scores_before_same_event_support_commit(self) -> None:
        causal, case = self._causal_case()
        engine = case._engine()
        rows = case._seed_four() + [
            causal.make_row(5, 1000, 5, rho_h=4.0, rho_l=4.1),
        ]
        results = engine.process_rows(rows)
        at_epoch = [item for item in results if item["event_id"] == 5]
        self.assertEqual(len(at_epoch), 2)
        self.assertTrue(all(
            item["status"] == causal.subject.STATUS_ASSIGNED_DIRECT
            and item["bg_epoch_seconds"] == 1000
            and item["calculated_count_ge"] == 4
            for item in at_epoch))
        background = json.loads(
            (case.root / "single_background.json").read_text())
        self.assertTrue(all(
            background["backgrounds"][ifo]["support_count"] == 4
            for ifo in ("H1", "L1")))
        self.assertEqual(
            {ifo: len(records) for ifo, records in engine.support.items()},
            {"H1": 5, "L1": 5})

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
                ifos=("H1", "L1"),
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
                ifos=("H1", "L1"),
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

    def test_frozen_assignment_keeps_injection_out_of_background(self) -> None:
        causal, case = self._causal_case()
        live = case._engine()
        live.process_rows(case._seed_four() + [
            causal.make_row(5, 1000, 5, rho_h=4.0, rho_l=4.1),
        ])
        background_path = case.root / "single_background.json"
        before_bytes = background_path.read_bytes()
        before_sha = hashlib.sha256(before_bytes).hexdigest()
        before_version = json.loads(before_bytes)["accepted_version"]

        frozen = case._engine(
            mode=causal.subject.MODE_FROZEN_ASSIGNMENT_ONLY,
            frozen_path=background_path,
            frozen_sha=before_sha,
            frozen_namespace=causal.SHA,
        )
        results = frozen.process_rows([
            causal.make_row(1, 1100, 99, rho_h=20.0, rho_l=21.0),
        ])
        self.assertTrue(all(
            item["status"] in (
                causal.subject.STATUS_ASSIGNED_DIRECT,
                causal.subject.STATUS_ASSIGNED_TAIL)
            and item["source"] == causal.subject.SOURCE_FROZEN
            and item["bg_version"] == before_version
            for item in results))
        self.assertEqual(
            {ifo: len(records) for ifo, records in frozen.support.items()},
            {"H1": 0, "L1": 0})
        self.assertEqual(frozen.lifecycle["support_candidates"], 0)
        self.assertEqual(frozen.lifecycle["support_appended"], 0)
        self.assertEqual(frozen.accepted_version, before_version)
        self.assertEqual(
            hashlib.sha256(background_path.read_bytes()).hexdigest(),
            before_sha)

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

    def test_crashcar_online_cli_binds_normal_postcoh_schema(self) -> None:
        source = POSTCOHSPIIR_ONLINE.read_text()
        pipeline = (CRASHCAR_SCRIPT_DIR / "crashcar_pipeline.sh").read_text()
        self.assertIn('"--finalsink-postcoh-schema-mode"', source)
        self.assertIn('choices=("legacy-a107", "crashcar-a109")', source)
        self.assertIn(
            "postcoh_schema_mode=options.finalsink_postcoh_schema_mode",
            source)
        self.assertIn(
            '--finalsink-postcoh-schema-mode '
            '"${finalsink_postcoh_schema_mode}"', pipeline)
        self.assertNotIn("--finalsink-single-trigger-stream", source)
        self.assertNotIn("single_trigger_stream_fname", source)

    def test_finalsink_uses_normal_coincsdoc_snr_series_path(self) -> None:
        source = (
            SCRIPT_DIR.parents[1] /
            "python/pipemodules/postcoh_finalsink.py").read_text()
        self.assertIn("def __write_cluster_zero_coinc_if_needed", source)
        self.assertIn("self.__write_candidate_coinc_xml(", source)
        self.assertIn("def assemble_ligolw_snr_series_arrays", source)
        self.assertIn("lal.series.build_COMPLEX8TimeSeries", source)
        self.assertNotIn("single_trigger_stream_fname", source)

    def test_finalsink_a107_a109_serialization_is_registry_driven(self) -> None:
        finalsink = (
            SCRIPT_DIR.parents[1] /
            "python/pipemodules/postcoh_finalsink.py").read_text()
        registry = (
            SCRIPT_DIR.parents[1] /
            "python/pipemodules/postcohtable/postcoh_table_def.py").read_text()
        self.assertIn("def _postcoh_row_for_serialization", finalsink)
        self.assertIn("postcoh_columns_for_schema_mode(", finalsink)
        self.assertIn("POSTCOH_SCHEMA_MODE_CRASHCAR_A109", finalsink)
        self.assertIn("POSTCOH_SCHEMA_MODE_LEGACY_A107", registry)
        self.assertIn("POSTCOH_SCHEMA_MODE_CRASHCAR_A109", registry)
        self.assertIn("len(POSTCOH_A107_COLUMN_PAIRS) != 107", registry)
        self.assertIn("len(POSTCOH_A109_COLUMN_PAIRS) != 109", registry)
        self.assertNotIn("_append_single_trigger_stream_boundaries", finalsink)

    def test_crashcar_pipeline_requires_template_shape_map_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            online_marker = root / "online_was_launched"
            fake_online = root / "gstlal_inspiral_postcohspiir_online"
            fake_online.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = --help ]; then\n"
                "  printf '%s\\n' --finalsink-postcoh-schema-mode "
                "--snr-series-logfar-threshold\n"
                "  exit 0\n"
                "fi\n"
                "printf launched > \"$ONLINE_MARKER\"\n"
                "exit 99\n")
            fake_online.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "SLURM_ARRAY_TASK_ID": "0",
                "BANK_DIR": str(root),
                "DATA_START_TIME": "1",
                "DATA_END_TIME": "2",
                "NONINJ_STATS_LOC": str(root),
                "DETRSP_MAP": str(root / "map.xml"),
                "FRAME_CACHE_FILE": str(root / "frames.cache"),
                "START_BANK": "0",
                "BANKS_PER_GROUP": "1",
                "ZEROLAG_SNAPSHOT_INTERVAL_SECONDS": "60",
                "BACKGROUND_STATS_WINDOWS": "1d",
                "CRASHCAR_ENABLE": "1",
                "CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP": "1",
                "CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME": str(
                    root / "missing.csv"),
                "SPIIR_ONLINE_BIN": str(fake_online),
                "ONLINE_MARKER": str(online_marker),
            })
            result = subprocess.run(
                ["bash", str(SCRIPT_DIR / "pipeline.sh")],
                check=False, env=env, cwd=str(root),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "crashcar requires readable "
                "CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME",
                result.stderr)
            self.assertFalse(online_marker.exists())

    def test_formal_pipeline_uses_normal_postcoh_without_dedicated_stream(self) -> None:
        pipeline = (CRASHCAR_SCRIPT_DIR / "crashcar_pipeline.sh").read_text()
        online = POSTCOHSPIIR_ONLINE.read_text()
        finalsink = (
            SCRIPT_DIR.parents[1] /
            "python/pipemodules/postcoh_finalsink.py").read_text()
        self.assertIn("--finalsink-postcoh-schema-mode", pipeline)
        self.assertIn("--snr-series-logfar-threshold", pipeline)
        for source in (pipeline, online, finalsink):
            self.assertNotIn("--finalsink-single-trigger-stream", source)
            self.assertNotIn("single_trigger_stream_fname", source)


if __name__ == "__main__":
    unittest.main()
