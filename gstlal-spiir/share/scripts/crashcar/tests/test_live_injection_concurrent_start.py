#!/usr/bin/env python3
"""Focused contracts for concurrent submit and worker-local readiness."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW = SCRIPT_DIR / "crashcar_frozen_injection_workflow.sh"
CONTROLLER = SCRIPT_DIR / "crashcar_controller.sh"
SBATCH = SCRIPT_DIR / "crashcar_sbatch.sh"
PIPELINE = SCRIPT_DIR / "crashcar_pipeline.sh"
REPO_ROOT = SCRIPT_DIR.parents[3]
SINGLE_C = REPO_ROOT / "gstlal-spiir" / "gst" / "cuda" / "cohfar" / "crashcar_singlefar.c"
MULTI_C = REPO_ROOT / "gstlal-spiir" / "gst" / "cuda" / "cohfar" / "cohfar_assignfar.c"


def _read(path):
    return path.read_text(encoding="utf-8")


def _function(text, name, next_name):
    start = text.index(f"{name}() {{")
    end = text.index(f"\n{next_name}() {{", start)
    return text[start:end]


def _heredoc(text, marker):
    match = re.search(rf"<<'{re.escape(marker)}'\n(.*?)\n{re.escape(marker)}", text, re.S)
    assert match, marker
    return match.group(1)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mock_producer(tmp_path, worker_count=2, banks_per_worker=8, start_bank=0):
    root = (tmp_path / "B1_noinj_producer").resolve()
    (root / "run").mkdir(parents=True)
    (root / "controller").mkdir()
    (root / "provenance" / "schema4").mkdir(parents=True)
    (root / "provenance" / "runtime_snapshot").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "artifacts").mkdir()
    namespace = root / "provenance" / "schema4" / "run_namespace.txt"
    source = root / "provenance" / "schema4" / "source_manifest.env"
    runtime = root / "provenance" / "runtime_snapshot" / "runtime_manifest.env"
    config = root / "scripts" / "crashcar.env"
    template = root / "artifacts" / "crashcar_template_shape_map.csv"
    namespace.write_text(f"run_root={root}\n", encoding="utf-8")
    source.write_text("manifest_kind=test\n", encoding="utf-8")
    config.write_text("injection_mode=False\n", encoding="utf-8")
    template.write_text("ifo,bank_id,template_id,magnitude\n", encoding="utf-8")
    runtime_files_sha, segment_xml_sha, segment_canonical_sha = (
        "c" * 64, "d" * 64, "e" * 64)
    runtime.write_text(
        "runtime_files_manifest_sha256=%s\n"
        "crashcar_segment_xml_sha256=%s\n"
        "crashcar_segment_livetime_json_sha256=%s\n" % (
            runtime_files_sha, segment_xml_sha, segment_canonical_sha),
        encoding="utf-8")
    status = {
        "phase": "schema4_provenance_ready", "root": str(root),
        "live_background_role": "producer",
        "single_background_mode": "rolling", "injection_mode": "False",
        "worker_count": str(worker_count),
        "banks_per_worker": str(banks_per_worker),
        "start_bank": str(start_bank), "start_gps": "100",
        "schema4_run_namespace_sha256": _sha(namespace),
        "schema4_source_manifest_sha256": _sha(source),
        "schema4_runtime_manifest_sha256": runtime_files_sha,
        "schema4_config_sha256": _sha(config),
        "schema4_template_shape_map_sha256": _sha(template),
        "crashcar_segment_livetime_sha256": segment_canonical_sha,
    }
    (root / "controller" / "status.json").write_text(
        json.dumps(status), encoding="utf-8")
    return root


def _run_contract(root, worker_count=2, banks_per_worker=8, start_bank=0):
    program = _heredoc(_read(CONTROLLER), "PY_LIVE_BINDING")
    return subprocess.run(
        [sys.executable, "-c", program, str(root), str(worker_count),
         str(banks_per_worker), str(start_bank), "100"],
        text=True, capture_output=True, check=False)


def test_one_click_writes_both_configs_then_starts_both_without_bg_gate():
    workflow = _read(WORKFLOW)
    tail = workflow[workflow.index('write_env_file "${BG_CONFIG}"'):]
    positions = [
        tail.index('write_env_file "${BG_CONFIG}"'),
        tail.index('write_env_file "${INJ_CONFIG}"'),
        tail.index('start_stage_async "${BG_CONFIG}"'),
        tail.index('start_stage_async "${INJ_CONFIG}"'),
        tail.index('monitor_overlapping_stages'),
    ]
    assert positions == sorted(positions)
    for forbidden in (
        "wait_for_first_backgrounds", "verify_b1_worker_pair_running",
        "B1_pre_B2_submit", "first_single_readiness",
        "first_multi_readiness"):
        assert forbidden not in workflow
    assert "initial_backgrounds_required=false" in tail
    assert 'BG_DURATION_SECONDS=${BG_DURATION_MINIMUM}' in workflow
    sbatch = _read(SBATCH)
    execution = sbatch[sbatch.rindex("\nverify_segment_runtime_binding\n"):]
    positions = [
        execution.index("resolve_crashcar_background_binding"),
        execution.index("wait_for_live_background_inputs"),
        execution.index("run_spiir_py3"),
    ]
    assert positions == sorted(positions)


def test_binding_contract_needs_only_staged_provenance_not_bg(tmp_path):
    root = _mock_producer(tmp_path)
    result = _run_contract(root)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["kind"] == "crashcar_live_single_binding_contract_v1"
    assert payload["background_files_required_at_submit"] is False
    assert payload["producer_root"] == str(root)
    expected = root / "run" / "000" / "single_background.json"
    assert payload["workers"][0]["single_background_path"] == str(expected)
    assert not expected.exists()
    assert not list((root / "run").glob("**/*marginalized_stats*"))


def test_binding_contract_rejects_wrong_geometry(tmp_path):
    root = _mock_producer(tmp_path)
    wrong = _run_contract(root, worker_count=3)
    assert wrong.returncode == 2
    assert "worker count mismatch" in wrong.stderr


def _binding_payload(root, *, worker_id=0, worker_count=2,
                     banks_per_worker=8, start_bank=0):
    keys = (
        "run_namespace_sha256", "source_manifest_sha256",
        "runtime_manifest_sha256", "config_sha256",
        "segment_xml_sha256", "segment_canonical_sha256",
        "template_shape_map_sha256")
    hex_chars = "abcdef0"
    ids = {key: hex_chars[index] * 64
           for index, key in enumerate(keys)}
    workers = []
    for worker in range(worker_count):
        first = start_bank + worker * banks_per_worker
        workers.append({
            "worker_id": worker, "worker_count": worker_count,
            "worker_bank_ids": list(range(first, first + banks_per_worker)),
            "single_background_path": str(
                root / "run" / f"{worker:03d}" / "single_background.json"),
        })
    workers[0]["worker_id"] = worker_id
    return {
        "kind": "crashcar_live_single_binding_contract_v1",
        "producer_root": str(root), "producer_origin_gps": 100,
        "worker_count": worker_count, "banks_per_worker": banks_per_worker,
        "start_bank": start_bank, "identities": ids,
        "tail_log10_far": -2, "workers": workers,
        "background_files_required_at_submit": False,
    }


def _run_sbatch_binding(tmp_path, payload):
    root = (tmp_path / "producer").resolve()
    (root / "run").mkdir(parents=True, exist_ok=True)
    consumer = (tmp_path / "consumer").resolve()
    consumer.mkdir(exist_ok=True)
    contract = tmp_path / "binding.json"
    contract.write_text(json.dumps(payload), encoding="utf-8")
    text = _read(SBATCH)
    fragment = text[text.index("crashcar_binding_error() {"):
                    text.index("# BEGIN_CRASHCAR_SEGMENT_RUNTIME_BINDING")]
    script = "set -euo pipefail\n" + fragment + r"""
resolve_crashcar_background_binding
printf '%s\n' "$CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON"
printf '%s\n' "$CRASHCAR_BG_RUN_NAMESPACE_SHA256"
printf '%s\n' "$TAIL_LOG_FAR"
"""
    env = os.environ.copy()
    env.update({
        "SLURM_ARRAY_TASK_ID": "0",
        "CRASHCAR_CURRENT_WORKER_COUNT": "2",
        "CRASHCAR_CURRENT_BANKS_PER_WORKER": "8",
        "CRASHCAR_CURRENT_START_BANK": "0",
        "CRASHCAR_SINGLE_BACKGROUND_MODE": "live_readonly",
        "CRASHCAR_BG_ONLY": "0", "WGUO_O3A_INJECTION_MODE": "blind",
        "WGUO_O3A_INJECTION_FILE": str(tmp_path / "injections.xml"),
        "CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE": "consumer",
        "CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROOT": str(root),
        "CRASHCAR_LIVE_SINGLE_BINDING_JSON": str(contract),
        "CRASHCAR_LIVE_BG_ORIGIN_GPS": "100",
        "TOP_RUN_ROOT": str(consumer),
        "TAIL_LOG_FAR": "-1",
    })
    return subprocess.run(["bash", "-c", script], env=env, text=True,
                          capture_output=True, check=False)


def test_sbatch_binding_accepts_absent_same_worker_file(tmp_path):
    root = (tmp_path / "producer").resolve()
    result = _run_sbatch_binding(tmp_path, _binding_payload(root))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == str(root / "run" / "000" / "single_background.json")
    assert not Path(lines[0]).exists()
    assert lines[1] == "a" * 64
    assert lines[2] == "-2"


def test_sbatch_binding_rejects_invalid_producer_tail(tmp_path):
    root = (tmp_path / "producer").resolve()
    payload = _binding_payload(root)
    payload["tail_log10_far"] = 0
    result = _run_sbatch_binding(tmp_path, payload)
    assert result.returncode != 0
    assert "producer tail anchor mismatch" in result.stderr


def test_sbatch_binding_rejects_wrong_worker_contract(tmp_path):
    root = (tmp_path / "producer").resolve()
    result = _run_sbatch_binding(
        tmp_path, _binding_payload(root, worker_id=1))
    assert result.returncode != 0
    assert "worker mismatch" in result.stderr


def test_pipeline_soft_start_guards_allow_missing_direct_inputs():
    pipeline = _read(PIPELINE)
    live_start = pipeline.index("  live_readonly)")
    live = pipeline[live_start:pipeline.index("  *)", live_start)]
    assert '[ -e "${CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON:-}" ] &&' in live
    assert '[ ! -f "${CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON:-}" ]; };' in live
    verify_start = pipeline.index("verify_scientific_input() {")
    verify = pipeline[verify_start:pipeline.index(
        'if [ "${multi_assignfar_enabled}"', verify_start)]
    assert '[ ! -e "${input_path}" ] || [ -f "${input_path}" ]' in verify
    assert "soft-start path is not a regular producer path" in verify


def test_existing_plugins_retry_missing_backgrounds_without_fatal():
    single = _read(SINGLE_C)
    start = single.index("if (!crashcar_read_schema4_file(")
    read_failure = single[start:single.index(
        "CrashcarParsedBackground candidate", start)]
    assert "CRASHCAR_LIVE_REFRESH_REJECTED_READ" in read_failure
    assert "return;" in read_failure
    assert "GST_ELEMENT_ERROR" not in read_failure
    multi = _read(MULTI_C)
    start = multi.index("/* Check that we have collected enough backgrounds */")
    initial = multi[start:multi.index("/* Check if it is time to refresh", start)]
    assert "element->pass_silent_time = FALSE" in initial
    assert "element->t_roll_start     = GST_CLOCK_TIME_NONE" in initial
    assert "GST_FLOW_ERROR" not in initial


def _write_fake_live_validator(top_run_root):
    scripts = top_run_root / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "crashcar_live_background.py"
    helper.write_text(
        """#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import sys

def option(name):
    return sys.argv[sys.argv.index(name) + 1]

if sys.argv[1] != "validate-single":
    raise SystemExit(2)
root = Path(option("--producer-root")).resolve()
worker = int(option("--worker"))
path = root / "run" / f"{worker:03d}" / "single_background.json"
if not path.is_file():
    raise SystemExit(2)
data = path.read_bytes()
print(json.dumps({
    "worker_id": worker,
    "accepted_version": 1,
    "coverage_end_gps_ns": 123000000000,
    "single_background_path": str(path),
    "single_background_sha256": hashlib.sha256(data).hexdigest(),
}))
""",
        encoding="utf-8",
    )
    helper.chmod(0o755)


def _multi_stats_xml(mutation=None):
    if mutation == "empty_document":
        return "<LIGO_LW/>"
    vector_count = "1 " + "0 " * 299
    matrix_count = "1 " + "0 " * 89999
    matrix_density = "0.1 " + "0 " * 89999
    matrix_rank = "-1 " * 90000
    vector_density = "0.1 " + "0 " * 299
    vector_probability = "0.5 " * 300
    pieces = [
        "<LIGO_LW>",
        '<LIGO_LW Name="gstlal_postcohspiir_stats">',
        '<Table Name="background_rank:rank_rate:table">',
        '<Column Type="real_4" '
        'Name="background_rank:rank_rate:cmin"/>',
        '<Column Type="real_4" '
        'Name="background_rank:rank_rate:cmax"/>',
        '<Column Type="int_4s" '
        'Name="background_rank:rank_rate:nbin"/>',
        '<Stream Delimiter="," Type="Local" '
        'Name="background_rank:rank_rate:table">'
        + ("" if mutation == "empty_table_stream" else "-30,0,300,")
        + "</Stream></Table>",
    ]
    specifications = (
        ("background_feature:{ifo}_lgsnr_rate:array",
         "int_8s", (300,), vector_count),
        ("background_feature:{ifo}_lgchisq_rate:array",
         "int_8s", (300,), vector_count),
        ("background_feature:{ifo}_lgsnr_lgchisq_rate:array",
         "int_8s", (300, 300), matrix_count),
        ("background_feature:{ifo}_lgsnr_lgchisq_pdf:array",
         "real_8", (300, 300), matrix_density),
        ("background_rank:{ifo}_rank_map:array",
         "real_8", (300, 300), matrix_rank),
        ("background_rank:{ifo}_rank_rate:array",
         "int_8s", (300,), vector_count),
        ("background_rank:{ifo}_rank_pdf:array",
         "real_8", (300,), vector_density),
        ("background_rank:{ifo}_rank_fap:array",
         "real_8", (300,), vector_probability),
    )
    target = "background_rank:H1_rank_rate:array"
    for ifo in ("H1", "L1", "V1", "H1L1V1"):
        for pattern, type_name, dims, payload in specifications:
            name = pattern.format(ifo=ifo)
            if mutation == "missing_array" and name == target:
                continue
            actual_type = (
                "real_8"
                if mutation == "wrong_type" and name == target
                else type_name)
            actual_dims = (
                (299,)
                if mutation == "wrong_dim" and name == target
                else dims)
            actual_payload = payload
            if mutation == "wrong_token_count" and name == target:
                actual_payload = "1 " + "0 " * 298
            if mutation == "non_numeric" and name == target:
                actual_payload = "not-a-number " + "0 " * 299
            dim_xml = "".join(f"<Dim>{value}</Dim>" for value in actual_dims)
            pieces.append(
                f'<Array Type="{actual_type}" Name="{name}">'
                f'{dim_xml}<Stream Type="Local" Delimiter=" ">'
                f'{actual_payload}</Stream></Array>')
        for suffix in ("nevent", "livetime"):
            name = f"background_feature:{ifo}_{suffix}:param"
            if mutation == "missing_param" and name == (
                    "background_feature:H1_livetime:param"):
                continue
            pieces.append(
                f'<Param Type="int_8s" Name="{name}">1</Param>')
    hist_trials = "0" if mutation == "invalid_hist_trials" else "100"
    pieces.extend([
        '<Param Type="int_4s" '
        'Name="background_feature:hist_trials:param">'
        + hist_trials + "</Param>",
        "</LIGO_LW>",
        "</LIGO_LW>",
    ])
    return "".join(pieces)


def _write_multi_worker_inputs(
        producer_root, worker=0, corrupt_span=None, mutation=None):
    jobno = f"{worker:03d}"
    worker_root = producer_root / "run" / jobno
    worker_root.mkdir(parents=True, exist_ok=True)
    valid_xml = _multi_stats_xml()
    mutated_xml = _multi_stats_xml(mutation) if mutation else valid_xml
    for span in ("2w", "1d", "2h"):
        path = worker_root / f"{jobno}_marginalized_stats_{span}.xml.gz"
        if span == corrupt_span:
            path.write_bytes(b"not-a-complete-gzip")
        else:
            payload = mutated_xml if span == "2w" else valid_xml
            with gzip.open(path, "wb") as handle:
                handle.write(payload.encode("ascii"))


def _run_multi_validator(producer_root, worker=0):
    program = _heredoc(_read(SBATCH), "PY_MULTI_READY")
    return subprocess.run(
        [sys.executable, "-c", program, str(producer_root), str(worker)],
        text=True, capture_output=True, check=False)


def test_multi_validator_matches_normal_loader_contract(tmp_path):
    valid_root = (tmp_path / "valid").resolve()
    _write_multi_worker_inputs(valid_root)
    valid = _run_multi_validator(valid_root)
    assert valid.returncode == 0, valid.stderr
    payload = json.loads(valid.stdout)
    assert payload["kind"] == "crashcar_live_multi_worker_readiness"
    assert payload["worker_id"] == 0
    assert [item["span"] for item in payload["files"]] == [
        "2w", "1d", "2h"]

    failures = {
        "empty_document":
            "nested gstlal_postcohspiir_stats node is missing",
        "empty_table_stream":
            "table Stream is empty or malformed",
        "missing_array":
            "Array background_rank:H1_rank_rate:array count is 0",
        "missing_param":
            "Param background_feature:H1_livetime:param count is 0",
        "wrong_type":
            "background_rank:H1_rank_rate:array Type mismatch",
        "wrong_dim":
            "background_rank:H1_rank_rate:array Dim mismatch",
        "wrong_token_count":
            "background_rank:H1_rank_rate:array Stream token count",
        "non_numeric":
            "contains a non-integer token",
        "invalid_hist_trials":
            "background_feature:hist_trials:param must be positive",
    }
    for mutation, expected in failures.items():
        root = (tmp_path / mutation).resolve()
        _write_multi_worker_inputs(root, mutation=mutation)
        result = _run_multi_validator(root)
        assert result.returncode != 0, mutation
        assert expected in result.stderr, (mutation, result.stderr)

    truncated_root = (tmp_path / "truncated").resolve()
    _write_multi_worker_inputs(truncated_root, corrupt_span="2w")
    truncated = _run_multi_validator(truncated_root)
    assert truncated.returncode != 0
    assert "not complete gzip/XML" in truncated.stderr

def _wait_for_status(path, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.02)
            continue
        if predicate(value):
            return value
        time.sleep(0.02)
    raise AssertionError(f"status did not reach expected state: {path}")


def test_worker_wait_blocks_before_pipeline_until_own_inputs_are_complete(tmp_path):
    producer = (tmp_path / "producer").resolve()
    (producer / "run" / "000").mkdir(parents=True)
    consumer = (tmp_path / "consumer").resolve()
    run_dir = consumer / "run"
    (run_dir / "monitor").mkdir(parents=True)
    _write_fake_live_validator(consumer)
    contract = tmp_path / "binding.json"
    contract.write_text(
        json.dumps(_binding_payload(producer)), encoding="utf-8")

    sbatch = _read(SBATCH)
    fragment = sbatch[sbatch.index("crashcar_binding_error() {"):
                      sbatch.index("# BEGIN_CRASHCAR_SEGMENT_RUNTIME_BINDING")]
    script = "set -euo pipefail\n" + fragment + r"""
sleep() { command sleep 0.05; }
resolve_crashcar_background_binding
wait_for_live_background_inputs
printf 'PIPELINE_READY\n'
"""
    env = os.environ.copy()
    env.update({
        "SLURM_ARRAY_TASK_ID": "0",
        "CRASHCAR_CURRENT_WORKER_COUNT": "2",
        "CRASHCAR_CURRENT_BANKS_PER_WORKER": "8",
        "CRASHCAR_CURRENT_START_BANK": "0",
        "CRASHCAR_SINGLE_BACKGROUND_MODE": "live_readonly",
        "CRASHCAR_BG_ONLY": "0",
        "WGUO_O3A_INJECTION_MODE": "blind",
        "WGUO_O3A_INJECTION_FILE": str(tmp_path / "injections.xml"),
        "CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE": "consumer",
        "CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROOT": str(producer),
        "CRASHCAR_LIVE_SINGLE_BINDING_JSON": str(contract),
        "CRASHCAR_LIVE_BG_ORIGIN_GPS": "100",
        "TOP_RUN_ROOT": str(consumer),
        "RUN_DIR": str(run_dir),
    })
    process = subprocess.Popen(
        ["bash", "-c", script], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    status_path = run_dir / "monitor" / "live_background_wait_000.json"
    waiting = _wait_for_status(
        status_path, lambda value: value["attempt"] >= 1)
    assert waiting["phase"] == "waiting_live_backgrounds"
    assert waiting["single_ready"] is False
    assert waiting["normal_multi_ready"] is False
    assert process.poll() is None

    single = producer / "run" / "000" / "single_background.json"
    single.write_text("ready\n", encoding="utf-8")
    single_only = _wait_for_status(
        status_path,
        lambda value: (
            value["single_ready"] is True
            and value["normal_multi_ready"] is False))
    assert single_only["phase"] == "waiting_live_backgrounds"
    assert process.poll() is None

    _write_multi_worker_inputs(producer, worker=0, corrupt_span="2h")
    incomplete_multi = _wait_for_status(
        status_path,
        lambda value: (
            value["attempt"] > single_only["attempt"]
            and value["normal_multi_ready"] is False))
    assert incomplete_multi["phase"] == "waiting_live_backgrounds"
    assert process.poll() is None

    _write_multi_worker_inputs(producer, worker=0)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert stdout.rstrip().endswith("PIPELINE_READY")
    ready = json.loads(status_path.read_text(encoding="utf-8"))
    assert ready["phase"] == "live_backgrounds_ready"
    assert ready["single_ready"] is True
    assert ready["normal_multi_ready"] is True
    assert ready["single_background"]["worker_id"] == 0
    assert ready["single_background"]["accepted_version"] == 1
    assert [item["span"] for item in ready["normal_multi"]] == [
        "2w", "1d", "2h"]
    assert not list((run_dir / "monitor").glob("*.tmp"))
    assert not (producer / "run" / "001" / "single_background.json").exists()
