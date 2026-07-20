#!/usr/bin/env python3
"""Static contracts for crashcar authority and sidecar isolation."""

import copy
import csv
import hashlib
import io
import os
import runpy
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


CRASHCAR_DIR = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (CRASHCAR_DIR / name).read_text(encoding="utf-8")


def test_launcher_is_crashcar_owned_and_has_no_sidecar_fallback():
    launcher = read("crashcar.sh")
    assert "SIDECAR_HELPER_DIR" not in launcher
    assert "single_detector_sidecar" not in launcher
    assert "sidecar fallback is forbidden" in launcher
    for legacy_helper in (
        "assign_frozen_far_ledger.py",
        "merge_worker_far_ledgers.py",
        "patch_zerolag_single_far_from_ledger.py",
        "update_single_background_once.sh",
        "extract_crashcar_detail_features.py",
        "extract_zerolag_features.py",
        "materialize_snr_autocorrelation.py",
    ):
        assert legacy_helper not in launcher


def test_formal_wrapper_stages_the_package_controller_only():
    repo_root = CRASHCAR_DIR.parents[3]
    formal_wrapper = (repo_root / "scripts" / "crashcar.sh").read_text(
        encoding="utf-8")
    package_launcher = read("crashcar.sh")
    assert 'LAUNCHER="${REPO_ROOT}/gstlal-spiir/share/scripts/crashcar/crashcar.sh"' in formal_wrapper
    assert 'ROOT="${ROOT_VALUE}" exec "${LAUNCHER}" "${CONFIG_FILE}"' in formal_wrapper
    assert "crashcar_controller.sh" not in formal_wrapper
    assert 'local src="${SCRIPT_DIR}/${script}"' in package_launcher
    assert 'cp "${src}" "${RUN_ROOT}/scripts/${script}"' in package_launcher
    assert 'CONTROLLER_SCRIPT="${RUN_ROOT}/scripts/crashcar_controller.sh"' in package_launcher
    assert 'CONTROLLER_SCRIPT="${RUN_ROOT}/scripts/crashcar_frozen_injection_workflow.sh"' in package_launcher


def test_controller_has_no_authoritative_posthoc_writeback():
    controller = read("crashcar_controller.sh")
    for removed_authority in (
        "run_single_ledger_final_update",
        "assign_frozen_far_ledger.py",
        "merge_worker_far_ledgers.py",
        "patch_zerolag_single_far_from_ledger.py",
        "single_final_far_all.csv",
        "materialize_snr_autocorrelation.py",
        "crashcar_candidate_events",
        "crashcar_single_ledger_final_update",
        "CRASHCAR_SINGLE_LEDGER_FINAL_UPDATE",
        "patch_zerolag_single_far",
        "PATCH_ZEROLAG_SINGLE_FAR",
        "patch_zerolag_single_snr_series",
        "PATCH_ZEROLAG_SINGLE_SNR_SERIES",
        "crashcar_snr_series_output_dir",
        "CRASHCAR_SNR_SERIES_OUTPUT_DIR",
        "crashcar_snr_series_write_csv",
        "CRASHCAR_SNR_SERIES_WRITE_CSV",
        "archive_snr_series",
        "postprocess_last_bg3h",
    ):
        assert removed_authority not in controller
    assert "acceptance_owner=external_verification_harness" in controller
    assert "parity and completeness are external acceptance work" in controller
    assert 'payload["single_background_files"]' in controller
    assert 'payload["zerolag_files"]' in controller
    assert 'payload["marginalized_stats_files"]' in controller
    assert 'payload["single_detail_files"]' in controller


def test_controller_has_no_candidate_acceptance_archive():
    controller = read("crashcar_controller.sh")
    for forbidden in (
            "candidate_events/manifest.csv",
            "candidate_events/candidate_*.xml.gz",
            "crashcar_candidate_events",
            "audit_manifest.json"):
        assert forbidden not in controller


def test_controller_rejects_stale_installed_a141_before_runtime_copy():
    controller = read("crashcar_controller.sh")
    validation = controller.index("validate_installed_runtime_contract() {")
    capture = controller.index("capture_runtime_manifest() {")
    call = controller.index(
        'validate_installed_runtime_contract "${source_install}" || exit 2')
    copy = controller.index('cp -a "${source_install}" "${runtime_staging}"')
    assert validation < capture < call < copy
    for required in (
        'choices=("legacy-a107", "crashcar-a109")',
        'POSTCOH_SCHEMA_MODE_CRASHCAR_A109 = "crashcar-a109"',
        "for symbol in H1_LLR L1_LLR",
        "reason=installed_runtime_schema_contract_mismatch",
    ):
        assert required in controller


def test_controller_completion_does_not_gate_on_pending_or_unassigned_far():
    controller = read("crashcar_controller.sh")
    monitor = controller[
        controller.index("monitor_job() {"):controller.index("\nmain() {")
    ]
    assert "write_final_report completed" in monitor
    assert "parity and completeness are external acceptance work" in monitor
    for forbidden in (
        "far_sngl",
        "assigned_far",
        "PENDING",
        "failed_postprocess",
        "single_ledger",
        "candidate_events",
        "archive_snr",
        "postprocess_last",
    ):
        assert forbidden not in monitor


def test_live_workflow_has_no_legacy_writeback_or_generation_pin_contract():
    workflow = read("crashcar_frozen_injection_workflow.sh")
    for forbidden in (
        "crashcar_single_ledger_final_update",
        "patch_zerolag_single_far",
        "patch_zerolag_single_snr_series",
        "assign_frozen_far_ledger.py",
        "single_final_far_all.csv",
        "single_background_mode=frozen",
        "frozen_bundle_manifest",
        "event_wide_generation",
        "common_generation_pin",
        "generation_commit",
    ):
        assert forbidden not in workflow
    assert '"single_background_mode=rolling"' in workflow
    assert '"single_background_mode=live_readonly"' in workflow
    assert workflow.index('"single_background_mode=rolling"') < (
        workflow.index('"single_background_mode=live_readonly"')
    )
    assert "phase=concurrent_launchers_started" in workflow
    assert "producer_consumer_overlap=true" in workflow
    assert "wait_for_first_backgrounds" not in workflow
    assert "verify_b1_worker_pair_running" not in workflow

def test_fap_collect_window_default_is_snapshot_plus_one_and_boundary_safe():
    controller = read("crashcar_controller.sh")
    workflow = read("crashcar_frozen_injection_workflow.sh")
    snapshot_interval = 1200
    current_snapshot_start = 1252193967
    current_timestamp = current_snapshot_start + snapshot_interval
    collect_window = snapshot_interval + 1
    boundary = current_timestamp - collect_window
    previous_snapshot_start = current_snapshot_start - snapshot_interval

    # FAPUpdater.get_valid_bankstats() uses a strict start_gps > boundary.
    assert current_snapshot_start > boundary
    assert not previous_snapshot_start > boundary
    assert collect_window == 1201

    assert ('FAP_COLLECT_WALLTIME_DEFAULT=$(( '
            'COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE + 1 ))'
            in controller)
    assert 'FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE=derived_snapshot_plus_one' in controller
    assert 'FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE=explicit_config' in controller
    assert 'export FINALSINK_FAPUPDATER_COLLECT_WALLTIME="${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE}"' in controller
    assert ',FINALSINK_FAPUPDATER_COLLECT_WALLTIME,ZEROLAG_SNAPSHOT_INTERVAL_SECONDS=' in controller
    assert ',FINALSINK_FAPUPDATER_COLLECT_WALLTIME="${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE}",' not in controller
    assert 'finalsink_fapupdater_collect_walltime_source="${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE}"' in controller

    assert 'MULTI_SNAPSHOT_SECONDS=${cohfar_accumbackground_snapshot_interval_seconds:-${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS:-${BG_UPDATE_SECONDS}}}' in workflow
    assert 'MULTI_COLLECT_WALLTIME_DEFAULT=$(( MULTI_SNAPSHOT_SECONDS + 1 ))' in workflow
    assert workflow.count('"finalsink_fapupdater_collect_walltime=${MULTI_COLLECT_WALLTIME}"') == 2
    assert 'finalsink_fapupdater_collect_walltime="${MULTI_COLLECT_WALLTIME}"' in workflow
    assert 'TAIL_LOG_FAR=${tail_log_FAR:-${TAIL_LOG_FAR:--2}}' in workflow


def _run_fap_collect_resolution(value: str):
    controller = read("crashcar_controller.sh")
    start = controller.index("validate_fap_collect_walltime() {")
    end = controller.index("\nH_ONLY_SECONDS=", start)
    fragment = controller[start:end]
    script = (
        "set -euo pipefail\n"
        "COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE=1200\n"
        "FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE=${1-}\n"
        + fragment
        + "\nprintf '%s\\n%s\\n' "
          '"$FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE" '
          '"$FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "fap-collect-test", value],
        text=True, capture_output=True, check=False,
    )


def test_fap_collect_walltime_explicit_override_validation_remains_closed():
    controller = read("crashcar_controller.sh")
    assert 'validate_fap_collect_walltime()' in controller
    assert 'expected exactly three comma-separated positive integers' in controller

    derived = _run_fap_collect_resolution("")
    assert derived.returncode == 0, derived.stderr
    assert derived.stdout.splitlines() == [
        "1201,1201,1201", "derived_snapshot_plus_one"]

    explicit_value = "1201,3601,7201"
    explicit = _run_fap_collect_resolution(explicit_value)
    assert explicit.returncode == 0, explicit.stderr
    assert explicit.stdout.splitlines() == [explicit_value, "explicit_config"]

    for invalid in ("0,1,1", "-1,1,1", "nan,1,1", "inf,1,1", "1,1"):
        rejected = _run_fap_collect_resolution(invalid)
        assert rejected.returncode == 2, (invalid, rejected.stderr)
        assert "expected exactly three comma-separated positive integers" in rejected.stderr


def _run_submit_job_collect_capture(tmp_path, collect_value: str):
    controller = read("crashcar_controller.sh")
    start = controller.index("submit_job() {")
    end = controller.index("\nmonitor_job() {", start)
    fragment = controller[start:end]
    script = r'''set -eo pipefail
FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE=$1
ARTIFACTS="$CAPTURE_ROOT/artifacts"
RUN_DIR="$CAPTURE_ROOT/run"
CONTROLLER_DIR="$CAPTURE_ROOT/controller"
CRASH_RUNTIME_ROOT="$CAPTURE_ROOT/runtime"
SCRIPT_DIR="$CAPTURE_ROOT/scripts"
mkdir -p "$ARTIFACTS" "$RUN_DIR" "$CONTROLLER_DIR" "$CRASH_RUNTIME_ROOT" "$SCRIPT_DIR"
RUNTIME_PROVENANCE_MANIFEST_SHA256=$(printf a%.0s {1..64})
WORKER_COUNT=2
BANKS_PER_WORKER=8
START_BANK=0
SCHEMA4_RUN_NAMESPACE_SHA256=$(printf b%.0s {1..64})
SCHEMA4_SOURCE_MANIFEST_SHA256=$(printf c%.0s {1..64})
SCHEMA4_RUNTIME_MANIFEST_SHA256=$(printf d%.0s {1..64})
SCHEMA4_CONFIG_SHA256=$(printf e%.0s {1..64})
SEGMENT_XML_SHA256=$(printf f%.0s {1..64})
SEGMENT_LIVETIME_JSON_SHA256=$(printf 1%.0s {1..64})
SCHEMA4_TEMPLATE_SHAPE_MAP_SHA256=$(printf 2%.0s {1..64})
CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE=producer
CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE=
INJECTION_BG_START_GPS=100
INJECTION_PIPELINE_MODE=none
INJECTION_FILE=
START_GPS=100
END_GPS=3700
DETRSP_MAP=/tmp/map.xml
FRAME_CACHE=/tmp/frame.cache
NONINJ_STATS_LOC=/tmp/stats
O3_BANK_DIR=/tmp/banks
ZEROLAG_UPDATE=1200
DOF=120
BACKGROUND_ACCUMULATION=1200
CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE=1200
BACKGROUND_UPDATE=1200
COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE=1200
COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS_VALUE=1200
FINALSINK_FAPUPDATER_INTERVAL_SECONDS_VALUE=1200
TAIL_LOG_FAR=-2
CRASHCAR_CODE_VERSION=test
SEGMENT_XML=/tmp/segments.xml
SINGLE_BACKGROUND_MODE_VALUE=rolling
CRASHCAR_BG_ONLY_VALUE=0
LIVETIME_CSV=/tmp/livetime.csv
SLURM_JOB_NAME=test
SLURM_MEM=1G
SLURM_CPUS_PER_TASK=1
SLURM_GRES=
SLURM_PARTITION=
SLURM_TIME=
SNR_SERIES_LOG_FAR_THRESHOLD=0
verify_segment_derivative_binding() { :; }
verify_runtime_provenance_manifest_pin() { :; }
write_status() { :; }
log() { :; }
sbatch() {
    printf '%s\n' "${FINALSINK_FAPUPDATER_COLLECT_WALLTIME-}" > "$CAPTURE_ENV"
    printf '%s\n' "$@" > "$CAPTURE_ARGS"
    printf '777\n'
}
''' + fragment + "\nsubmit_job\n"
    env = os.environ.copy()
    env.update({
        "CAPTURE_ROOT": str(tmp_path),
        "CAPTURE_ENV": str(tmp_path / "captured.env"),
        "CAPTURE_ARGS": str(tmp_path / "captured.args"),
    })
    result = subprocess.run(
        ["bash", "-c", script, "submit-collect-test", collect_value],
        env=env, text=True, capture_output=True, check=False,
    )
    captured_env = (tmp_path / "captured.env").read_text(
        encoding="utf-8").strip() if (tmp_path / "captured.env").exists() else ""
    captured_args = (tmp_path / "captured.args").read_text(
        encoding="utf-8").splitlines() if (tmp_path / "captured.args").exists() else []
    return result, captured_env, captured_args


def test_submit_job_exports_comma_collect_value_without_slurm_token_splitting(tmp_path):
    for index, collect_value in enumerate((
            "1201,1201,1201", "1201,3601,7201")):
        case_root = tmp_path / f"case{index}"
        case_root.mkdir()
        result, captured_env, captured_args = _run_submit_job_collect_capture(
            case_root, collect_value)
        assert result.returncode == 0, result.stderr
        assert captured_env == collect_value
        export_arg = next(
            value for value in captured_args if value.startswith("--export="))
        export_tokens = export_arg.removeprefix("--export=").split(",")
        assert "FINALSINK_FAPUPDATER_COLLECT_WALLTIME" in export_tokens
        assert "SNR_series_logFAR_threshold=0" in export_tokens
        assert not any(value.startswith(
            "FINALSINK_FAPUPDATER_COLLECT_WALLTIME=")
            for value in export_tokens)


def test_controller_stages_a_passive_immutable_run_root_runtime():
    controller = read("crashcar_controller.sh")
    pipeline = read("crashcar_pipeline.sh")
    assert "capture_runtime_manifest()" in controller
    assert "acceptance_owner=external_verification_harness" in controller
    assert "manifest_kind=passive_runtime_snapshot" in controller
    assert 'cp -a "${source_install}" "${runtime_staging}"' in controller
    assert 'chmod -R a-w "${runtime_install}"' in controller
    assert "runtime_files.sha256" in controller
    assert "git -C \"${SOURCE_ROOT}\" fetch" not in controller
    assert 'cp "${SOURCE_ROOT}/gstlal-spiir/bin/gstlal_inspiral_postcohspiir_online"' not in controller
    assert "finalsink_dst=" not in controller
    assert 'ln -s "${SOURCE_ROOT}/install_local"' not in controller
    assert '${ROOT}/bin/gstlal_inspiral_postcohspiir_online' not in controller
    assert 'export PATH="${CRASH_ROOT}/install/bin:${PATH}"' in pipeline
    assert '${TOP_RUN_ROOT}/bin' not in pipeline

def test_package_has_no_dedicated_final_writeback_system():
    retired = (
        "assign_frozen_far_ledger.py",
        "merge_worker_far_ledgers.py",
        "patch_zerolag_single_far_from_ledger.py",
        "update_single_background_once.sh",
        "materialize_snr_autocorrelation.py",
    )
    for name in retired:
        assert not (CRASHCAR_DIR / name).exists(), name
    for package_file in CRASHCAR_DIR.iterdir():
        if not package_file.is_file() or package_file.suffix not in {".py", ".sh"}:
            continue
        package_text = package_file.read_text(encoding="utf-8")
        for name in retired:
            assert name not in package_text, (package_file, name)

def test_compiled_evidence_requires_explicit_fresh_path_bindings():
    repo_root = CRASHCAR_DIR.parents[3]
    runner = (
        repo_root / "tests" / "crashcar" / "run_compiled_contracts.py"
    ).read_text(encoding="utf-8")
    live_probe = (
        repo_root / "tests" / "crashcar" / "support"
        / "run_crashcar_live_reader_probe.sh"
    ).read_text(encoding="utf-8")

    for option in (
        "--source-root",
        "--build-root",
        "--install-root",
        "--staged-plugin",
        "--support-library",
    ):
        assert 'parser.add_argument("%s", required=True)' % option in runner
    for token in (
        "fresh_source_root",
        "fresh_build_root",
        "fresh_install_root",
        "staged_plugin",
        "support_library",
        "_require_within",
    ):
        assert token in runner
    for forbidden in (
        "plugin.parents[3]",
        "ROOT /",
        "/fred/oz016/qliang/Eric_bless_SPIIR",
        "wguo-single-det-py3/install",
    ):
        assert forbidden not in runner
        assert forbidden not in live_probe

    assert "if (( $# != 8 )); then" in live_probe
    assert "exit 64" in live_probe
    for token in (
        "FRESH_SOURCE_ROOT",
        "FRESH_BUILD_ROOT",
        "FRESH_INSTALL_ROOT",
        "STAGED_PLUGIN",
        "SUPPORT_LIBRARY",
        "GSTCOMMON_LIBRARY",
        "CONTAINER",
        "requested_fresh_bindings.txt",
    ):
        assert token in live_probe


def test_detector_mask_route_is_closed_and_not_environment_selected():
    source = (CRASHCAR_DIR.parents[2] / "gst" / "cuda" / "cohfar" / "crashcar_singlefar.c").read_text(encoding="utf-8")
    header = (CRASHCAR_DIR.parents[2] / "gst" / "cuda" / "cohfar" / "crashcar_singlefar.h").read_text(encoding="utf-8")
    controller = read("crashcar_controller.sh")
    pipeline = read("crashcar_pipeline.sh")
    sbatch = read("crashcar_sbatch.sh")
    for exact_mask in ("H1", "H1V1", "L1", "L1V1", "H1L1", "H1L1V1", "V1"):
        assert 'g_strcmp0(ifos, "%s")' % exact_mask in source
    for route_name in (
        "CRASHCAR_SINGLE_FINAL_ROUTE_INVALID",
        "CRASHCAR_SINGLE_FINAL_ROUTE_H1",
        "CRASHCAR_SINGLE_FINAL_ROUTE_L1",
        "CRASHCAR_SINGLE_FINAL_ROUTE_MULTI",
        "CRASHCAR_SINGLE_FINAL_ROUTE_V1_ONLY",
    ):
        assert route_name in header
        assert route_name in source
    assert "final_far_route_authority=row_ifos_exact_mask" in controller
    assert "PRESERVED_LEGACY" not in source
    for text in (source, controller, pipeline, sbatch):
        assert "CRASHCAR_PRESERVE_TABLE_SINGLE_FAR" not in text
        assert "crashcar_preserve_table_single_far" not in text
    for forbidden in (
        "CRASHCAR_SINGLE_OUTPUT_MODE",
        "SINGLE_OUTPUT_MODE",
        "SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE",
        "crashcar_single_output_allows",
    ):
        assert forbidden not in source
        assert forbidden not in controller
        assert forbidden not in sbatch

def test_finalsink_uses_exact_route_union_and_normal_output_path_without_multi_mutation():
    finalsink = (
        CRASHCAR_DIR.parents[2] / "python" / "pipemodules" /
        "postcoh_finalsink.py").read_text(encoding="utf-8")
    online = (
        CRASHCAR_DIR.parents[2] / "bin" /
        "gstlal_inspiral_postcohspiir_online").read_text(encoding="utf-8")
    pipeline = read("crashcar_pipeline.sh")
    controller = read("crashcar_controller.sh")
    sbatch = read("crashcar_sbatch.sh")

    assert pipeline.count("--finalsink-cluster-window 1") == 1
    assert '"H1V1": "H1_SINGLE"' in finalsink
    assert '"L1V1": "L1_SINGLE"' in finalsink
    assert '"H1L1V1": "MULTI"' in finalsink
    assert '"V1": "V1_ONLY"' in finalsink
    assert 'if route == "H1_SINGLE":' in finalsink
    assert 'elif route == "L1_SINGLE":' in finalsink
    assert "raw_value = postcoh_inspiral.far" in finalsink
    assert "never compare single with multi" in finalsink
    assert "value >= minimum" in finalsink
    assert "value > minimum" not in finalsink
    assert "POSTCOH_SCHEMA_MODE_CRASHCAR_A109" in finalsink
    assert finalsink.count("_postcoh_row_for_serialization(") == 4
    assert "self.coincs_document.assemble_ligolw_xmldoc(" in finalsink

    forced_writer = "__write_" + "pending_coinc_if_needed"
    assert forced_writer not in finalsink
    for forbidden in (
        "candidate_event_manifest",
        "__write_crashcar_retained_coinc_xml",
        "single_trigger_stream_fname",
        "CRASHCAR_RETAINED_EVENT",
        "--finalsink-single-trigger-stream",
        "CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR",
        "crashcar_single_far_1w",
        "crashcar_single_far_1d",
        "crashcar_single_far_2h",
    ):
        assert forbidden not in finalsink
        assert forbidden not in pipeline
        assert forbidden not in controller
        assert forbidden not in online
        assert forbidden not in sbatch

def test_snr_series_logfar_threshold_existing_contract_is_bound_end_to_end():
    import ast
    import math

    finalsink = (
        CRASHCAR_DIR.parents[2]
        / "python"
        / "pipemodules"
        / "postcoh_finalsink.py"
    ).read_text(encoding="utf-8")
    online = (
        CRASHCAR_DIR.parents[2]
        / "bin"
        / "gstlal_inspiral_postcohspiir_online"
    ).read_text(encoding="utf-8")
    pipeline = read("crashcar_pipeline.sh")
    controller = read("crashcar_controller.sh")
    sbatch = read("crashcar_sbatch.sh")

    tree = ast.parse(finalsink)
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_crashcar_far_meets_log_threshold"
    )
    namespace = {"math": math}
    module = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "threshold_helper", "exec"), namespace)
    meets = namespace["_crashcar_far_meets_log_threshold"]

    assert meets(1.0e-5, -4.0)
    assert meets(1.0e-4, -4.0)
    assert not meets(1.01e-4, -4.0)
    for value in (0.0, -1.0, float("nan"), float("inf")):
        assert not meets(value, -4.0)
    assert not meets(1.0e-5, float("nan"))

    assert '"--snr-series-logfar-threshold"' in online
    assert (
        "snr_series_logfar_threshold=options.snr_series_logfar_threshold"
        in online
    )
    option_line = next(
        line for line in pipeline.splitlines()
        if "--snr-series-logfar-threshold" in line
    )
    assert "snr_series_logfar_threshold" in option_line
    option_start = online.index('"--snr-series-logfar-threshold"')
    option_block = online[option_start:option_start + 500]
    assert 'type="float"' in option_block
    assert "default=-4" in option_block
    assert "snr_series_logfar_threshold=-4" in finalsink
    assert "SNR_series_logFAR_threshold" in pipeline
    assert "SNR_SERIES_LOG_FAR_THRESHOLD" in pipeline
    assert "math.isfinite(value)" in pipeline
    assert "must be a finite number" in pipeline
    assert "SNR_SERIES_LOG_FAR_THRESHOLD=" in controller
    assert "SNR_series_logFAR_threshold=" in controller
    assert "--export=ALL" in controller
    assert "sbatch_export_bound=1" in controller
    assert "crashcar_pipeline.sh" in sbatch



def test_detail_csv_keeps_full_science_off_the_authoritative_a109_row():
    source = (
        CRASHCAR_DIR.parents[2]
        / "gst"
        / "cuda"
        / "cohfar"
        / "crashcar_singlefar.c"
    ).read_text(encoding="utf-8")
    for column in (
            "far_calculated_exact",
            "far_calculated_valid",
            "far_assigned_exact",
            "far_assigned_valid",
            "far_assigned_source",
            "far_assigned_status",
            "far_sngl_legacy",
            "single_bg_authority_valid",
            "single_bg_authority_version",
            "single_bg_authority_epoch_gps_ns",
            "single_bg_authority_provenance_sha256"):
        assert column in source
    signature = source[
        source.index("static void crashcar_write_detail("):
        source.index("static void crashcar_singlefar_set_property",
                     source.index("static void crashcar_write_detail("))]
    assert "calculated_far" in signature
    assert "assigned_far" in signature
    assert "single_status" in signature
    assert "selected_authority_version" in signature
    assert "selected_authority_provenance" in signature
    assert "far_assigned_sngl_exact" not in source

def test_c_source_has_inclusive_single_threshold_and_no_dedicated_series_writer():
    source = (
        CRASHCAR_DIR.parents[2]
        / "gst"
        / "cuda"
        / "cohfar"
        / "crashcar_singlefar.c"
    ).read_text(encoding="utf-8")
    header = (
        CRASHCAR_DIR.parents[2]
        / "gst"
        / "cuda"
        / "cohfar"
        / "crashcar_singlefar.h"
    ).read_text(encoding="utf-8")

    assert "table->snglsnr[ifo_id] >= CRASHCAR_MIN_SNR" in source
    assert "#define CRASHCAR_MIN_SNR 0x1.0000000000000p+2" in source
    assert "PROP_MIN_SNR" not in source
    assert "PROP_FAR_FLOOR_COUNT" not in source
    assert "min_snr" not in header
    assert "far_floor_count" not in header
    for retired in (
        "crashcar_write_snr_series_dump",
        "CRASHCAR_SNR_SERIES_SHARD_CLOSE",
        "snr_series_log10_far_threshold",
        "CRASHCAR_SINGLE_SNR_SERIES_PRESELECT_ALL",
    ):
        assert retired not in source
        assert retired not in header


def test_exact_a_eff_export_call_and_tail_anchor_uses_existing_runtime_knob():
    controller = read("crashcar_controller.sh")
    source = (
        CRASHCAR_DIR.parents[2]
        / "gst"
        / "cuda"
        / "cohfar"
        / "crashcar_singlefar.c"
    ).read_text(encoding="utf-8")
    wrapper = (
        CRASHCAR_DIR.parents[2]
        / "python"
        / "pipemodules"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    spiirparts = (
        CRASHCAR_DIR.parents[2]
        / "python"
        / "pipemodules"
        / "spiirparts.py"
    ).read_text(encoding="utf-8")

    export_call = controller[controller.index("export_template_map()"):
                             controller.index("job_snapshot()")]
    assert '--ifos H1,L1' in export_call
    assert '--start-bank 0' in export_call
    assert '--end-bank 383' in export_call
    assert '--dof' not in export_call
    assert 'module load gcc/13.3.0 scipy-bundle/2024.05' in export_call
    assert '|| true' not in export_call
    assert 'import numpy,pandas' in export_call
    assert 'template_map_python=' in export_call
    assert '"${template_map_python}" "${CRASH_SCRIPT_DIR}/export_template_shape_map.py"' in export_call
    assert '"tail-log10-far"' in source
    assert "element->tail_log10_far = -2.0;" in source
    assert "tail_log10_far +" in source
    assert "evaluation->tail_slope * rank + evaluation->tail_intercept" not in source
    assert 'os.environ.get("TAIL_LOG_FAR", "-2.0")' in spiirparts
    assert '"tail_log10_far": tail_log10_far' in wrapper
    assert '"min_snr"' not in wrapper
    assert '"far_floor_count"' not in wrapper
    assert "min_snr=" not in wrapper
    assert "far_floor_count=" not in wrapper
    assert "min_snr=" not in spiirparts
    assert "far_floor_count=" not in spiirparts


def test_segment_derivative_is_schema_aware_integer_json():
    parser = read("dump_segment_livetime_csv.py")
    controller = read("crashcar_controller.sh")

    for token in (
        '"schema_version": 1',
        '"source_xml_sha256": source_sha256',
        '"run_start": gps_object(run_start_ns)',
        '"run_end": gps_object(run_end_ns)',
        '"targets": targets',
        'TARGET_IFOS = ("H1", "L1")',
        'if start_ns > end_ns:',
        'if start_ns == end_ns:',
        'merge_intervals(intervals[ifo])',
        'separators=(",", ":")',
        'allow_nan=False',
    ):
        assert token in parser
    assert "float(" not in parser
    assert "set(declared_schema) != set(TABLE_SCHEMAS[name])" in parser
    assert "tuple(declared_schema) != TABLE_SCHEMAS[name]" not in parser
    assert "columns = [column_name for column_name, _ in declared_schema]" in parser
    assert "schema-aware" not in parser.lower() or "LIGO-LW" in parser
    assert '--run-start "${START_GPS}"' in controller
    assert '--run-end "${END_GPS}"' in controller
    assert '_livetime.json' in controller
    assert 'crashcar_segment_livetime_json=' in controller
    assert 'crashcar_segment_livetime_sha256=' in controller


def test_segment_binding_is_immutable_manifest_and_runtime_fail_closed():
    controller = read("crashcar_controller.sh")
    sbatch = read("crashcar_sbatch.sh")
    for token in (
        "crashcar_segment_xml_absolute_path=%q",
        "crashcar_segment_xml_sha256=%s",
        "crashcar_segment_livetime_json_absolute_path=%q",
        "crashcar_segment_livetime_json_sha256=%s",
        "crashcar_segment_run_start=%s",
        "crashcar_segment_run_end=%s",
        'chmod -R a-w "${runtime_snapshot_dir}"',
        'runtime_snapshot_dir="${ROOT}/provenance/runtime_snapshot"',
        "verify_segment_derivative_binding runtime_staging || exit 2",
        "verify_segment_derivative_binding pre_slurm_submit || exit 2",
        "verify_runtime_provenance_manifest_pin pre_slurm_submit || exit 2",
        "RUNTIME_PROVENANCE_MANIFEST_SHA256=$(sha256sum",
        'CRASHCAR_RUNTIME_PROVENANCE_MANIFEST_SHA256="${RUNTIME_PROVENANCE_MANIFEST_SHA256}"',
    ):
        assert token in controller
    assert "RUNTIME_PROVENANCE_MANIFEST_SHA256=\n" in controller
    assert "CRASHCAR_RUNTIME_PROVENANCE_MANIFEST_SHA256:-" not in controller
    main_body = controller[controller.rindex("main() {"):]
    assert main_body.index("    validate_inputs\n") < main_body.index(
        "    capture_runtime_manifest\n")
    assert "# BEGIN_CRASHCAR_SEGMENT_RUNTIME_BINDING" in sbatch
    assert "# END_CRASHCAR_SEGMENT_RUNTIME_BINDING" in sbatch
    assert sbatch.index("pinned runtime manifest sha256 mismatch") < sbatch.index(
        "source /dev/stdin")
    assert 'source "${runtime_manifest}"' not in sbatch
    assert 'manifest_snapshot=$(<"${runtime_manifest}")' in sbatch
    assert sbatch.count(
        'manifest_snapshot=$(<"${runtime_manifest}")') == 1
    assert sbatch.count(
        'source /dev/stdin <<< "${manifest_snapshot}"') == 1
    assert sbatch.index('unset "${variable}"') < sbatch.index(
        "source /dev/stdin")

    begin = sbatch.index("# BEGIN_CRASHCAR_SEGMENT_RUNTIME_BINDING")
    end = sbatch.index("# END_CRASHCAR_SEGMENT_RUNTIME_BINDING")
    worker_function_source = sbatch[begin:end]
    controller_begin = controller.index("segment_binding_failure() {")
    controller_end = controller.index("\ncapture_runtime_manifest() {")
    controller_functions = controller[controller_begin:controller_end]
    worker_command = worker_function_source + "\nverify_segment_runtime_binding\n"
    parser_api = _segment_parser_api()
    binding_fields = (
        "crashcar_segment_xml_absolute_path",
        "crashcar_segment_xml_sha256",
        "crashcar_segment_livetime_json_absolute_path",
        "crashcar_segment_livetime_json_sha256",
        "crashcar_segment_run_start",
        "crashcar_segment_run_end",
    )

    def write_manifest(ctx, values, update_pin):
        snapshot = ctx["snapshot"]
        manifest = ctx["manifest"]
        snapshot.chmod(0o755)
        if manifest.exists():
            manifest.chmod(0o644)
        manifest.write_text("\n".join(
            name + "=" + shlex.quote(str(values[name]))
            for name in values) + "\n", encoding="utf-8")
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        manifest.chmod(0o444)
        snapshot.chmod(0o555)
        ctx["values"] = dict(values)
        if update_pin:
            ctx["env"]["CRASHCAR_RUNTIME_PROVENANCE_MANIFEST_SHA256"] = digest
        return digest

    def build_fixture(root):
        provenance = root / "provenance"
        snapshot = provenance / "runtime_snapshot"
        snapshot.mkdir(parents=True)
        quoted_path_root = root / "path with spaces $and;metacharacters"
        quoted_path_root.mkdir()
        raw_xml = quoted_path_root / "segments file.xml"
        derivative = quoted_path_root / "segment livetime.json"
        raw_xml.write_text(_base_segment_xml(), encoding="utf-8")
        parser_payload = parser_api["build_canonical_payload"](
            str(raw_xml), 100, 200)
        parser_api["write_atomic"](str(derivative), parser_payload)
        derivative.chmod(0o644)
        values = {
            "crashcar_segment_xml_absolute_path": str(raw_xml),
            "crashcar_segment_xml_sha256": hashlib.sha256(
                raw_xml.read_bytes()).hexdigest(),
            "crashcar_segment_livetime_json_absolute_path": str(derivative),
            "crashcar_segment_livetime_json_sha256": hashlib.sha256(
                derivative.read_bytes()).hexdigest(),
            "crashcar_segment_run_start": "100",
            "crashcar_segment_run_end": "200",
        }
        manifest = snapshot / "runtime_manifest.env"
        env = os.environ.copy()
        env.update({
            "TOP_RUN_ROOT": str(root),
            "WGUO_O3A_SEGMENT_XML": str(raw_xml),
            "SEGMENT_XML": str(raw_xml),
            "SINGLE_SEGMENT_XML": str(raw_xml),
            "CRASHCAR_SEGMENT_LIVETIME_CSV": str(derivative),
            "WGUO_O3A_START_GPS": "100",
            "WGUO_O3A_END_GPS": "200",
        })
        ctx = {
            "root": root,
            "provenance": provenance,
            "snapshot": snapshot,
            "raw_xml": raw_xml,
            "derivative": derivative,
            "manifest": manifest,
            "env": env,
            "values": values,
        }
        digest = write_manifest(ctx, values, update_pin=True)
        ctx["base_manifest_sha"] = digest
        assert provenance.stat().st_mode & 0o222
        assert not (snapshot.stat().st_mode & 0o222)
        assert not (manifest.stat().st_mode & 0o222)
        later_record = provenance / "later_unrelated_record.txt"
        later_record.write_text("later provenance remains appendable\n",
                                encoding="utf-8")
        try:
            manifest.write_text("forbidden overwrite\n", encoding="utf-8")
        except PermissionError:
            pass
        else:
            raise AssertionError("sealed binding manifest remained writable")
        return ctx

    def rewrite_fields(ctx, updates=None, drop=None, update_pin=True):
        values = dict(ctx["values"])
        if updates:
            values.update(updates)
        if drop:
            values.pop(drop)
        return write_manifest(ctx, values, update_pin=update_pin)

    def run_worker_case(name, mutate=None, success=False, stderr_contains=None,
                        post_check=None):
        with tempfile.TemporaryDirectory(
                prefix="crashcar_binding_%s_" % name) as tmp:
            ctx = build_fixture(Path(tmp))
            if mutate:
                mutate(ctx)
            result = subprocess.run(
                ["bash", "-c", worker_command], env=ctx["env"], check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if success:
                assert result.returncode == 0, (name, result.stderr.decode())
            else:
                assert result.returncode != 0, name
            if stderr_contains is not None:
                assert stderr_contains in result.stderr, (
                    name, result.stderr.decode())
            if post_check:
                post_check(ctx, result)
            print("worker_case_%s=PASS" % name)

    run_worker_case("valid_quoted_paths", success=True)

    run_worker_case(
        "missing_pin",
        lambda ctx: ctx["env"].pop(
            "CRASHCAR_RUNTIME_PROVENANCE_MANIFEST_SHA256"),
        stderr_contains=b"pinned runtime manifest sha256 must be exact lowercase64")
    run_worker_case(
        "uppercase_pin",
        lambda ctx: ctx["env"].__setitem__(
            "CRASHCAR_RUNTIME_PROVENANCE_MANIFEST_SHA256",
            ctx["base_manifest_sha"].upper()),
        stderr_contains=b"pinned runtime manifest sha256 must be exact lowercase64")

    def missing_manifest(ctx):
        ctx["snapshot"].chmod(0o755)
        ctx["manifest"].unlink()
        ctx["snapshot"].chmod(0o555)

    run_worker_case("missing_manifest", missing_manifest,
                    stderr_contains=b"missing segment runtime manifest")
    run_worker_case(
        "writable_manifest", lambda ctx: ctx["manifest"].chmod(0o644),
        stderr_contains=b"segment runtime manifest is not immutable")
    run_worker_case(
        "writable_snapshot", lambda ctx: ctx["snapshot"].chmod(0o755),
        stderr_contains=b"segment runtime manifest is not immutable")

    def omit_with_poisoned_inherited_env(ctx, field):
        ctx["env"].update({
            name: "inherited-poison-for-" + name for name in binding_fields})
        rewrite_fields(ctx, drop=field, update_pin=True)

    for missing_field in binding_fields:
        run_worker_case(
            "missing_%s_with_poisoned_inherited_env" % missing_field,
            lambda ctx, field=missing_field: omit_with_poisoned_inherited_env(
                ctx, field),
            stderr_contains=("segment binding manifest missing %s" %
                             missing_field).encode())

    for raw_env_name in (
            "WGUO_O3A_SEGMENT_XML", "SEGMENT_XML", "SINGLE_SEGMENT_XML"):
        def drift_raw_path(ctx, variable=raw_env_name):
            alternate = ctx["root"] / (variable + ".alternate.xml")
            alternate.write_text(_base_segment_xml(), encoding="utf-8")
            ctx["env"][variable] = str(alternate)
        run_worker_case(
            "raw_path_%s" % raw_env_name, drift_raw_path,
            stderr_contains=("raw XML path mismatch for %s" %
                             raw_env_name).encode())

    def drift_derivative_path(ctx):
        alternate = ctx["root"] / "alternate_derivative.json"
        alternate.write_bytes(ctx["derivative"].read_bytes())
        ctx["env"]["CRASHCAR_SEGMENT_LIVETIME_CSV"] = str(alternate)

    run_worker_case("derivative_path", drift_derivative_path,
                    stderr_contains=b"canonical derivative path mismatch")
    run_worker_case(
        "run_start", lambda ctx: ctx["env"].__setitem__(
            "WGUO_O3A_START_GPS", "101"),
        stderr_contains=b"segment run interval mismatch")
    run_worker_case(
        "run_end", lambda ctx: ctx["env"].__setitem__(
            "WGUO_O3A_END_GPS", "201"),
        stderr_contains=b"segment run interval mismatch")
    run_worker_case(
        "raw_hash_field",
        lambda ctx: rewrite_fields(
            ctx, updates={"crashcar_segment_xml_sha256": "0" * 64},
            update_pin=True),
        stderr_contains=b"raw XML sha256 mismatch")
    run_worker_case(
        "derivative_hash_field",
        lambda ctx: rewrite_fields(
            ctx,
            updates={"crashcar_segment_livetime_json_sha256": "0" * 64},
            update_pin=True),
        stderr_contains=b"derivative sha256 mismatch")
    run_worker_case(
        "raw_content_drift",
        lambda ctx: ctx["raw_xml"].write_text(
            _base_segment_xml() + "\n", encoding="utf-8"),
        stderr_contains=b"raw XML sha256 mismatch")
    run_worker_case(
        "derivative_content_drift",
        lambda ctx: ctx["derivative"].write_bytes(
            b"drifted derivative bytes\n"),
        stderr_contains=b"derivative sha256 mismatch")

    def replace_manifest_and_derivative(ctx):
        ctx["derivative"].write_bytes(b"attacker derivative\n")
        rewrite_fields(
            ctx,
            updates={
                "crashcar_segment_livetime_json_sha256": hashlib.sha256(
                    ctx["derivative"].read_bytes()).hexdigest(),
                "crashcar_segment_run_start": "999",
            },
            update_pin=False)

    run_worker_case(
        "owner_reseal_replace_manifest_and_derivative",
        replace_manifest_and_derivative,
        stderr_contains=b"pinned runtime manifest sha256 mismatch")

    def swap_after_snapshot(ctx):
        replacement = ctx["root"] / "malicious_replacement.env"
        malicious = dict(ctx["values"])
        malicious["crashcar_segment_run_start"] = "999"
        replacement.write_text("\n".join(
            name + "=" + shlex.quote(str(malicious[name]))
            for name in malicious) + "\n", encoding="utf-8")
        shim_dir = ctx["root"] / "shim"
        shim_dir.mkdir()
        marker = ctx["root"] / "swap_completed.marker"
        shim = shim_dir / "sha256sum"
        shim.write_text("""#!/usr/bin/env bash
set -euo pipefail
if [ \"$#\" -eq 0 ]; then
    data=$(mktemp)
    cat > \"${{data}}\"
    digest=$(/usr/bin/sha256sum \"${{data}}\" | awk '{{print $1}}')
    if [ ! -e {marker} ]; then
        chmod 0755 {snapshot}
        rm -f {manifest}
        cp {replacement} {manifest}
        chmod 0444 {manifest}
        chmod 0555 {snapshot}
        : > {marker}
    fi
    rm -f \"${{data}}\"
    printf '%s  -\\n' \"${{digest}}\"
else
    exec /usr/bin/sha256sum \"$@\"
fi
""".format(
            marker=shlex.quote(str(marker)),
            snapshot=shlex.quote(str(ctx["snapshot"])),
            manifest=shlex.quote(str(ctx["manifest"])),
            replacement=shlex.quote(str(replacement))), encoding="utf-8")
        shim.chmod(0o755)
        ctx["env"]["PATH"] = str(shim_dir) + os.pathsep + ctx["env"]["PATH"]
        ctx["swap_marker"] = marker

    run_worker_case(
        "swap_after_snapshot", swap_after_snapshot, success=True,
        post_check=lambda ctx, result: (
            (_ for _ in ()).throw(AssertionError("swap hook did not run"))
            if not ctx["swap_marker"].exists() else None))

    with tempfile.TemporaryDirectory(
            prefix="crashcar_controller_binding_") as tmp:
        ctx = build_fixture(Path(tmp))
        controller_dir = ctx["root"] / "controller"
        controller_dir.mkdir()
        controller_env = ctx["env"].copy()
        controller_env.update({
            "ROOT": str(ctx["root"]),
            "CRASH_SCRIPT_DIR": str(CRASHCAR_DIR),
            "CONTROLLER_DIR": str(controller_dir),
            "START_GPS": "100",
            "END_GPS": "200",
            "SEGMENT_XML_CANONICAL": str(ctx["raw_xml"]),
            "SEGMENT_XML_SHA256": ctx["values"][
                "crashcar_segment_xml_sha256"],
            "SEGMENT_LIVETIME_JSON_CANONICAL": str(ctx["derivative"]),
            "SEGMENT_LIVETIME_JSON_SHA256": ctx["values"][
                "crashcar_segment_livetime_json_sha256"],
            "SEGMENT_BINDING_RUN_START": "100",
            "SEGMENT_BINDING_RUN_END": "200",
            "LIVETIME_CSV": str(ctx["derivative"]),
            "RUNTIME_PROVENANCE_MANIFEST_SHA256": ctx[
                "base_manifest_sha"],
        })
        functions = "log() { :; }\nwrite_status() { :; }\n" + controller_functions
        derivative_command = (
            functions +
            "\nverify_segment_derivative_binding executable_test\n")
        manifest_command = (
            functions +
            "\nverify_runtime_provenance_manifest_pin executable_test\n")
        for command in (derivative_command, manifest_command):
            valid = subprocess.run(
                ["bash", "-c", command], env=controller_env, check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert valid.returncode == 0, valid.stderr.decode()

        ctx["derivative"].write_bytes(b"noncanonical but pinned derivative\n")
        controller_env["SEGMENT_LIVETIME_JSON_SHA256"] = hashlib.sha256(
            ctx["derivative"].read_bytes()).hexdigest()
        regeneration_mismatch = subprocess.run(
            ["bash", "-c", derivative_command], env=controller_env,
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert regeneration_mismatch.returncode != 0

        rewrite_fields(
            ctx, updates={"crashcar_segment_run_start": "999"},
            update_pin=False)
        pinned_manifest_mismatch = subprocess.run(
            ["bash", "-c", manifest_command], env=controller_env,
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert pinned_manifest_mismatch.returncode != 0


_FORMAL_SEGMENT_XML = Path(
    "/fred/oz016/wguo/odds_ratio/O3a/chunk20/"
    "multi_det-BNS-LVK_inj/000/H1L1V1_SEGMENTS_1252187822_86400.xml.gz"
)
_FORMAL_SEGMENT_SOURCE_SHA256 = (
    "52adca35f0c579d3e55e17b4f07561b3d0da9467c1c3138a7696832775e60b78"
)
_FORMAL_SEGMENT_DERIVATIVE_SHA256 = (
    "41981a5bf36be34f4f4ba6e9d2f42bdf1c6212be00fd58b5e7e25d6ff07b1588"
)


def _segment_parser_api():
    return runpy.run_path(str(CRASHCAR_DIR / "dump_segment_livetime_csv.py"),
                          run_name="crashcar_segment_parser_contract")


def _base_segment_xml():
    return """<LIGO_LW>
<Table Name="segment_definer:table">
<Column Name="segment_definer:comment" Type="lstring"/><Column Name="segment_definer:ifos" Type="lstring"/><Column Name="segment_definer:name" Type="lstring"/><Column Name="segment_definer:process_id" Type="ilwd:char"/><Column Name="segment_definer:segment_def_id" Type="ilwd:char"/><Column Name="segment_definer:version" Type="int_4s"/>
<Stream Delimiter="," Name="segment_definer:table" Type="Local">
"x","H1","postcohprocessed","process:process_id:1","segment_definer:segment_def_id:0",1,
"x","L1","postcohprocessed","process:process_id:1","segment_definer:segment_def_id:1",1,
"x","V1","postcohprocessed","process:process_id:1","segment_definer:segment_def_id:2",1,
</Stream></Table>
<Table Name="segment:table">
<Column Name="segment:end_time" Type="int_4s"/><Column Name="segment:end_time_ns" Type="int_4s"/><Column Name="segment:process_id" Type="ilwd:char"/><Column Name="segment:segment_def_id" Type="ilwd:char"/><Column Name="segment:segment_id" Type="ilwd:char"/><Column Name="segment:start_time" Type="int_4s"/><Column Name="segment:start_time_ns" Type="int_4s"/>
<Stream Delimiter="," Name="segment:table" Type="Local">
150,0,"process:process_id:1","segment_definer:segment_def_id:0","segment:segment_id:0",110,0,
160,0,"process:process_id:1","segment_definer:segment_def_id:1","segment:segment_id:1",120,0,
170,0,"process:process_id:1","segment_definer:segment_def_id:2","segment:segment_id:2",130,0,
</Stream></Table></LIGO_LW>"""


def _payload_from_xml_text(api, directory, name, xml_text):
    path = Path(directory) / name
    path.write_text(xml_text, encoding="utf-8")
    return api["build_canonical_payload"](str(path), 100, 200)


def _csv_stream_text(rows):
    lines = []
    for row in rows:
        output = io.StringIO()
        csv.writer(output, lineterminator="").writerow(list(row) + [""])
        lines.append(output.getvalue())
    return "\n" + "\n".join(lines) + "\n"


def _permute_columns_and_values(xml_text, table_name, permutation,
                                permute_values=True):
    root = ET.fromstring(xml_text)
    table = next(table for table in root.findall("Table")
                 if table.attrib["Name"] == table_name)
    children = list(table)
    columns = children[:-1]
    stream = children[-1]
    old_rows = []
    for line in (stream.text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        fields = next(csv.reader([line], strict=True))
        assert len(fields) == len(columns) + 1 and fields[-1] == ""
        old_rows.append(fields[:-1])
    for child in children:
        table.remove(child)
    for index in permutation:
        table.append(columns[index])
    table.append(stream)
    if permute_values:
        stream.text = _csv_stream_text(
            [[row[index] for index in permutation] for row in old_rows])
    else:
        stream.text = _csv_stream_text(old_rows)
    return ET.tostring(root, encoding="unicode")


def _permute_tables(xml_text):
    root = ET.fromstring(xml_text)
    tables = list(root)
    for table in tables:
        root.remove(table)
    for table in reversed(tables):
        root.append(table)
    return ET.tostring(root, encoding="unicode")


def _stream_before_columns(xml_text, table_name):
    root = ET.fromstring(xml_text)
    table = next(table for table in root.findall("Table")
                 if table.attrib["Name"] == table_name)
    stream = list(table)[-1]
    table.remove(stream)
    table.insert(0, stream)
    return ET.tostring(root, encoding="unicode")


def _add_unknown_table_child(xml_text, table_name):
    root = ET.fromstring(xml_text)
    table = next(table for table in root.findall("Table")
                 if table.attrib["Name"] == table_name)
    table.insert(len(table) - 1, ET.Element("Unknown"))
    return ET.tostring(root, encoding="unicode")


def _target_table_count_variant(xml_text, table_name, duplicate):
    root = ET.fromstring(xml_text)
    table = next(table for table in root.findall("Table")
                 if table.attrib["Name"] == table_name)
    if duplicate:
        root.append(copy.deepcopy(table))
    else:
        root.remove(table)
    return ET.tostring(root, encoding="unicode")


def _replace_segment_rows(xml_text, rows):
    root = ET.fromstring(xml_text)
    table = next(table for table in root.findall("Table")
                 if table.attrib["Name"] == "segment:table")
    list(table)[-1].text = _csv_stream_text(rows)
    return ET.tostring(root, encoding="unicode")


def _assert_segment_rejected(api, directory, name, xml_text):
    try:
        _payload_from_xml_text(api, directory, name + ".xml", xml_text)
    except ValueError:
        return
    raise AssertionError("segment fixture unexpectedly accepted: %s" % name)


def _normalize_source_digest(api, payload):
    normalized = copy.deepcopy(payload)
    normalized["source_xml_sha256"] = "0" * 64
    return api["canonical_bytes"](normalized)


def test_segment_parser_actual_formal_xml_canonical_bytes():
    api = _segment_parser_api()
    assert _FORMAL_SEGMENT_XML.is_file()
    payload = api["build_canonical_payload"](
        str(_FORMAL_SEGMENT_XML), 1252187822, 1252274222)
    data = api["canonical_bytes"](payload)
    assert payload["source_xml_sha256"] == _FORMAL_SEGMENT_SOURCE_SHA256
    assert api["hashlib"].sha256(data).hexdigest() == _FORMAL_SEGMENT_DERIVATIVE_SHA256
    assert payload["targets"]["H1"]["raw_row_count"] == 30
    assert payload["targets"]["H1"]["merged_interval_count"] == 30
    assert payload["targets"]["H1"]["livetime_ns"] == 31126000000000
    assert payload["targets"]["L1"]["raw_row_count"] == 82
    assert payload["targets"]["L1"]["merged_interval_count"] == 82
    assert payload["targets"]["L1"]["livetime_ns"] == 77843000000000


def test_segment_parser_semantic_column_and_table_permutations():
    api = _segment_parser_api()
    base_xml = _base_segment_xml()
    with tempfile.TemporaryDirectory(prefix="crashcar_segment_permute_") as tmp:
        baseline = _payload_from_xml_text(api, tmp, "base.xml", base_xml)
        column_permuted = _payload_from_xml_text(
            api, tmp, "columns.xml",
            _permute_columns_and_values(
                base_xml, "segment_definer:table", (1, 0, 2, 3, 4, 5)))
        table_permuted = _payload_from_xml_text(
            api, tmp, "tables.xml", _permute_tables(base_xml))
        quoted_numeric = _payload_from_xml_text(
            api, tmp, "quoted_numeric.xml",
            base_xml.replace("150,0,", '"150","0",', 1).replace(
                ",110,0,", ',"110","0",', 1))
        for candidate in (column_permuted, table_permuted, quoted_numeric):
            assert candidate["source_xml_sha256"] != baseline["source_xml_sha256"]
            assert _normalize_source_digest(api, candidate) == _normalize_source_digest(
                api, baseline)
            rebound = copy.deepcopy(candidate)
            rebound["source_xml_sha256"] = baseline["source_xml_sha256"]
            assert api["canonical_bytes"](rebound) == api["canonical_bytes"](
                baseline)


def test_segment_parser_merge_clip_and_failure_contract():
    api = _segment_parser_api()
    base = _base_segment_xml()
    process_id = "process:process_id:1"
    h = "segment_definer:segment_def_id:0"
    l = "segment_definer:segment_def_id:1"
    v = "segment_definer:segment_def_id:2"
    rows = [
        (105, 0, process_id, h, "segment:segment_id:0", 90, 0),
        (120, 0, process_id, h, "segment:segment_id:1", 110, 0),
        (120, 0, process_id, h, "segment:segment_id:2", 110, 0),
        (130, 0, process_id, h, "segment:segment_id:3", 115, 0),
        (140, 0, process_id, h, "segment:segment_id:4", 130, 0),
        (145, 0, process_id, h, "segment:segment_id:5", 145, 0),
        (210, 0, process_id, h, "segment:segment_id:6", 195, 0),
        (160, 0, process_id, l, "segment:segment_id:7", 120, 0),
        (170, 0, process_id, v, "segment:segment_id:8", 130, 0),
    ]
    with tempfile.TemporaryDirectory(prefix="crashcar_segment_edges_") as tmp:
        payload = _payload_from_xml_text(
            api, tmp, "edges.xml", _replace_segment_rows(base, rows))
        h_target = payload["targets"]["H1"]
        assert h_target["raw_row_count"] == 7
        assert h_target["empty_row_count"] == 1
        assert h_target["merged_interval_count"] == 3
        assert h_target["livetime_ns"] == 40000000000
        assert h_target["intervals"] == [
            {"start": {"seconds": 100, "nanoseconds": 0},
             "end": {"seconds": 105, "nanoseconds": 0}},
            {"start": {"seconds": 110, "nanoseconds": 0},
             "end": {"seconds": 140, "nanoseconds": 0}},
            {"start": {"seconds": 195, "nanoseconds": 0},
             "end": {"seconds": 200, "nanoseconds": 0}},
        ]

        fixtures = {
            "column_value_mismatch": _permute_columns_and_values(
                base, "segment_definer:table", (1, 0, 2, 3, 4, 5),
                permute_values=False),
            "stream_before_columns": _stream_before_columns(
                base, "segment_definer:table"),
            "extra_table_child": _add_unknown_table_child(
                base, "segment_definer:table"),
            "missing_definer_table": _target_table_count_variant(
                base, "segment_definer:table", False),
            "duplicate_definer_table": _target_table_count_variant(
                base, "segment_definer:table", True),
            "missing_segment_table": _target_table_count_variant(
                base, "segment:table", False),
            "duplicate_segment_table": _target_table_count_variant(
                base, "segment:table", True),
            "extra_column": base.replace(
                '<Column Name="segment_definer:version" Type="int_4s"/>',
                '<Column Name="segment_definer:version" Type="int_4s"/>'
                '<Column Name="segment_definer:extra" Type="lstring"/>', 1),
            "missing_column": base.replace(
                '<Column Name="segment_definer:comment" Type="lstring"/>',
                "", 1),
            "duplicate_column": base.replace(
                '<Column Name="segment_definer:comment" Type="lstring"/>',
                '<Column Name="segment_definer:comment" Type="lstring"/>'
                '<Column Name="segment_definer:comment" Type="lstring"/>', 1),
            "wrong_type": base.replace(
                'Name="segment_definer:comment" Type="lstring"',
                'Name="segment_definer:comment" Type="int_4s"', 1),
            "extra_table_attribute": base.replace(
                '<Table Name="segment_definer:table">',
                '<Table Name="segment_definer:table" bad="1">', 1),
            "extra_column_attribute": base.replace(
                'Name="segment_definer:comment" Type="lstring"',
                'Name="segment_definer:comment" Type="lstring" bad="1"', 1),
            "extra_stream_attribute": base.replace(
                'Name="segment:table" Type="Local"',
                'Name="segment:table" Type="Local" bad="1"', 1),
            "nonlocal_stream": base.replace(
                'Name="segment:table" Type="Local"',
                'Name="segment:table" Type="Remote"', 1),
            "wrong_root": base.replace(
                "<LIGO_LW>", "<WRONG>", 1).replace(
                "</LIGO_LW>", "</WRONG>", 1),
            "namespaced_root": base.replace(
                "<LIGO_LW>", '<x:LIGO_LW xmlns:x="urn:bad">', 1).replace(
                "</LIGO_LW>", "</x:LIGO_LW>", 1),
            "root_attribute": base.replace(
                "<LIGO_LW>", '<LIGO_LW bad="1">', 1),
            "malformed_csv": base.replace(
                '"x","H1",', '"x,"H1",', 1),
            "missing_stream_field": base.replace(
                '"x","H1",', '"H1",', 1),
            "extra_stream_field": base.replace(
                '"x","H1",', '"x","H1","extra",', 1),
            "quoted_ifo_whitespace": base.replace(
                '"H1","postcohprocessed"',
                '" H1 ","postcohprocessed"', 1),
            "definer_id_whitespace": base.replace(
                '"segment_definer:segment_def_id:0"',
                '" segment_definer:segment_def_id:0 "', 1),
            "segment_id_whitespace": base.replace(
                '"segment:segment_id:0"', '" segment:segment_id:0 "', 1),
            "unresolved_fk": base.replace(
                '"segment_definer:segment_def_id:0","segment:segment_id:0"',
                '"segment_definer:segment_def_id:99","segment:segment_id:0"',
                1),
            "duplicate_segment_id": base.replace(
                '"segment:segment_id:1"', '"segment:segment_id:0"', 1),
            "duplicate_definer_id": base.replace(
                '"segment_definer:segment_def_id:1"',
                '"segment_definer:segment_def_id:0"', 1),
            "missing_h_target": base.replace('"H1"', '"V1"', 1),
            "duplicate_h_target": base.replace('"L1"', '"H1"', 1),
            "numeric_whitespace": base.replace(
                "150,0,", '" 150 ",0,', 1),
            "numeric_plus": base.replace("150,0,", "+150,0,", 1),
            "numeric_leading_zero": base.replace("150,0,", "0150,0,", 1),
            "numeric_negative_zero": base.replace("150,0,", "-0,0,", 1),
            "nanoseconds_high": base.replace("150,0,", "150,1000000000,", 1),
            "nanoseconds_negative": base.replace(",110,0,", ",110,-1,", 1),
            "start_after_end": base.replace(",110,0,", ",151,0,", 1),
        }
        for name, xml_text in fixtures.items():
            _assert_segment_rejected(api, tmp, name, xml_text)


def test_c_source_uses_direct_science_order_without_validation_commit_state():
    source = (
        CRASHCAR_DIR.parents[2] / "gst" / "cuda" / "cohfar"
        / "crashcar_singlefar.c"
    ).read_text(encoding="utf-8")
    header = (
        CRASHCAR_DIR.parents[2] / "gst" / "cuda" / "cohfar"
        / "crashcar_singlefar.h"
    ).read_text(encoding="utf-8")
    transform = source[source.rindex(
        "static GstFlowReturn crashcar_singlefar_transform_ip"
    ):]
    publish_locked = source[
        source.index("crashcar_try_complete_paired_authority_locked("):
        source.index("static CrashcarAuthoritySelection")
    ]
    append_locked = source[
        source.index("crashcar_add_foreground_support_locked("):
        source.index("static gboolean crashcar_row_has_ifo(")
    ]

    for obsolete in (
        "CrashcarGroupCommitResult",
        "CRASHCAR_GROUP_COMMIT_",
        "crashcar_commit_scored_group(",
        "last_observed_group_gps_ns",
        "last_committed_group_gps_ns",
        "scientific commit fence",
        "earlier callback after a strictly later GPS was observed",
        "durable paired-authority publication failed",
    ):
        assert obsolete not in source
    assert "crashcar_cluster_" not in source
    assert "CRASHCAR_CLUSTER_WINDOW_SECONDS" not in source
    assert "GArray *buffer_events" not in transform
    assert "GCond" not in source
    assert "g_cond_wait" not in source

    snapshot = transform.index("crashcar_snapshot_paired_authority(")
    evaluated_llr = transform.index("*llr_slot = llr;", snapshot)
    detail = transform.index("crashcar_write_detail(", evaluated_llr)
    direct_guard = transform.index("if (support_work &&", detail)
    lock = transform.index("g_mutex_lock(&crashcar_support_mutex)", direct_guard)
    publish = transform.index(
        "crashcar_try_complete_paired_authority_locked(", lock)
    append = transform.index("crashcar_add_foreground_support_locked(", publish)
    unlock = transform.index("g_mutex_unlock(&crashcar_support_mutex)", append)
    assert snapshot < evaluated_llr < detail < direct_guard
    assert direct_guard < lock < publish < append < unlock
    assert "!element->live_single_background_readonly" in transform[
        direct_guard:lock]
    assert transform.count("g_mutex_lock(&crashcar_support_mutex)") == 1
    assert transform.count(
        "crashcar_try_complete_paired_authority_locked(") == 1
    assert transform.count("crashcar_add_foreground_support_locked(") == 1
    assert "g_mutex_" not in publish_locked
    assert "g_mutex_" not in append_locked

    assert "gint64 available_after_gps_ns;" in header
    assert "point.available_after_gps_ns >= available_after_gps_ns" in source
    assert "pending->available_after_gps_ns < group_gps_ns" in source
    assert "authority->epoch_gps_ns < group_gps_ns" in source
    assert "transform_fail:" not in transform
    assert "group_failed_bg" not in transform
    assert "group_failed_science" not in transform
    assert transform.count("g_free(row_work);") == 1
    assert transform.count("gst_buffer_unmap(buf, &mapInfo);") >= 2


def test_a109_route_owned_far_projection_fails_closed_when_real4_cannot_hold_it():
    source = (
        CRASHCAR_DIR.parents[2]
        / "gst"
        / "cuda"
        / "cohfar"
        / "crashcar_singlefar.c"
    ).read_text(encoding="utf-8")
    finalsink = (
        CRASHCAR_DIR.parents[2]
        / "python"
        / "pipemodules"
        / "postcoh_finalsink.py"
    ).read_text(encoding="utf-8")

    projection_start = source.index(
        "const float projected_far = (float)fitted_far")
    projection_block = source[
        projection_start:source.index(
            "if (!element->live_single_background_readonly",
            projection_start)]
    assert "if (crashcar_far_is_valid(projected_far))" in projection_block
    assert "table->far_sngl[ifo_id] = far_sngl;" in projection_block
    assert "CRASHCAR_SINGLE_FAR_STATUS_FAILED_OUTPUT_POLICY" in projection_block
    assert "group_failed_science" not in projection_block
    assert "GST_ELEMENT_ERROR(" not in projection_block
    assert "table->far_assigned_sngl_exact" not in source

    decision = finalsink[
        finalsink.index("def _crashcar_final_far_decision("):
        finalsink.index("def _crashcar_candidate_output_dispatch(")]
    assert 'if route == "H1_SINGLE":' in decision
    assert 'elif route == "L1_SINGLE":' in decision
    assert "raw_value = postcoh_inspiral.far" in decision
    assert "math.isfinite(value)" in decision
    assert "valid = int(value > 0.0)" in decision
    assert "far_assigned_sngl_exact" not in finalsink
