#!/usr/bin/env python3
"""Contracts for seed-free BG-only accumulation and closed assignment modes."""

import os
import shlex
import subprocess
from pathlib import Path


CRASHCAR_DIR = Path(__file__).resolve().parents[1]
CONTROLLER = CRASHCAR_DIR / "crashcar_controller.sh"
PIPELINE = CRASHCAR_DIR / "crashcar_pipeline.sh"
SBATCH = CRASHCAR_DIR / "crashcar_sbatch.sh"
WORKFLOW = CRASHCAR_DIR / "crashcar_frozen_injection_workflow.sh"


def _function_source(path: Path, name: str, next_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    end = text.index(f"\n{next_name}() {{", start)
    return text[start:end]


def _write(path: Path, text: str = "fixture\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_validate_inputs(
        tmp_path: Path,
        *,
        bg_only: bool,
        worker_count: int = 2,
        seed_workers=(),
        live_consumer: bool = False,
) -> subprocess.CompletedProcess:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    segment = _write(tmp_path / "segment.xml")
    detrsp = _write(tmp_path / "response.xml")
    cache = _write(tmp_path / "frames.cache")
    wguo = tmp_path / "wguo"
    wguo.mkdir()
    banks = tmp_path / "banks"
    banks.mkdir()
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    controller_dir = tmp_path / "controller"
    controller_dir.mkdir()
    livetime = tmp_path / "livetime.json"

    for worker in range(worker_count):
        bank_id = f"{worker:04d}"
        for ifo in ("H1", "L1", "V1"):
            _write(
                banks
                / f"iir_{ifo}-GSTLAL_SPLIT_BANK_{bank_id}-a1-0-0.xml.gz"
            )
    for worker in seed_workers:
        jobno = f"{worker:03d}"
        for suffix in ("2w", "1d", "2h"):
            _write(
                seeds
                / jobno
                / f"{jobno}_marginalized_stats_{suffix}.xml.gz"
            )

    _write(
        scripts / "dump_segment_livetime_csv.py",
        (
            "import sys\n"
            "from pathlib import Path\n"
            "out = Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "out.write_text('{}\\n', encoding='utf-8')\n"
        ),
    )
    _write(scripts / "export_template_shape_map.py", "# fixture\n")
    _write(scripts / "crashcar_pipeline.sh", "#!/usr/bin/env bash\n")
    _write(scripts / "crashcar_sbatch.sh", "#!/usr/bin/env bash\n")

    values = {
        "SEGMENT_XML": segment,
        "DETRSP_MAP": detrsp,
        "FRAME_CACHE": cache,
        "CRASH_SCRIPT_DIR": scripts,
        "SCRIPT_DIR": scripts,
        "WGUO_BANK_STATS_DIR": wguo,
        "NONINJ_STATS_LOC": seeds,
        "O3_BANK_DIR": banks,
        "CONTROLLER_DIR": controller_dir,
        "LIVETIME_CSV": livetime,
        "START_GPS": 100,
        "END_GPS": 200,
        "WORKER_COUNT": worker_count,
        "BANKS_PER_WORKER": 1,
        "START_BANK": 0,
        "CRASHCAR_BG_ONLY_VALUE": int(bg_only),
        "CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE": (
            "consumer" if live_consumer else ""
        ),
    }
    assignments = "\n".join(
        f"{key}={shlex.quote(str(value))}" for key, value in values.items()
    )
    validate_inputs = _function_source(
        CONTROLLER, "validate_inputs", "export_template_map"
    )
    script = f"""set -euo pipefail
{assignments}
log() {{ printf '%s\\n' "$*" >&2; }}
write_status() {{ :; }}
verify_segment_derivative_binding() {{ return 0; }}
{validate_inputs}
validate_inputs
"""
    return subprocess.run(
        ["bash"],
        input=script,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_bg_only_validate_inputs_accepts_no_legacy_seed(tmp_path):
    result = _run_validate_inputs(tmp_path, bg_only=True)
    assert result.returncode == 0, result.stderr
    assert "marginalized_stats" not in result.stderr


def test_non_bg_only_missing_seed_fails_closed(tmp_path):
    result = _run_validate_inputs(tmp_path, bg_only=False)
    assert result.returncode == 2
    assert "000_marginalized_stats_2w.xml.gz" in result.stderr


def test_non_bg_only_worker_geometry_mismatch_fails_closed(tmp_path):
    result = _run_validate_inputs(
        tmp_path, bg_only=False, worker_count=2, seed_workers=(0,)
    )
    assert result.returncode == 2
    assert "001_marginalized_stats_2w.xml.gz" in result.stderr


def test_non_bg_only_complete_current_worker_roster_still_passes(tmp_path):
    result = _run_validate_inputs(
        tmp_path, bg_only=False, worker_count=2, seed_workers=(0, 1)
    )
    assert result.returncode == 0, result.stderr


def test_live_consumer_validate_inputs_soft_starts_without_multi_files(tmp_path):
    result = _run_validate_inputs(
        tmp_path, bg_only=False, worker_count=2, live_consumer=True
    )
    assert result.returncode == 0, result.stderr


def test_pipeline_bg_only_accumulates_without_reading_or_passing_seed(tmp_path):
    crash_root = tmp_path / "runtime"
    fake_binary = _write(
        crash_root / "install" / "bin" / "gstlal_inspiral_postcohspiir_online",
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$CAPTURE_ARGS\"\n",
    )
    fake_binary.chmod(0o755)
    (crash_root / "install" / "lib" / "gstreamer-1.0").mkdir(
        parents=True
    )
    banks = tmp_path / "banks"
    for ifo in ("H1", "L1", "V1"):
        _write(
            banks / f"iir_{ifo}-GSTLAL_SPLIT_BANK_0000-a1-0-0.xml.gz"
        )
    response = _write(tmp_path / "response.xml")
    cache = _write(tmp_path / "frames.cache")
    old_seed = tmp_path / "old_seed"
    for suffix in ("2w", "1d", "2h"):
        _write(
            old_seed / "000" / f"000_marginalized_stats_{suffix}.xml.gz",
            "must-not-be-read-or-passed\n",
        )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    capture = tmp_path / "captured_args.txt"
    env = os.environ.copy()
    env.update(
        {
            "SLURM_ARRAY_TASK_ID": "0",
            "CRASH_ROOT": str(crash_root),
            "TOP_RUN_ROOT": str(tmp_path),
            "CAPTURE_ARGS": str(capture),
            "WGUO_O3A_BANK_DIR": str(banks),
            "WGUO_O3A_START_GPS": "100",
            "WGUO_O3A_END_GPS": "200",
            "WGUO_O3A_NONINJ_STATS_LOC": str(old_seed),
            "WGUO_O3A_DETRSP_MAP": str(response),
            "WGUO_O3A_FRAME_CACHE": str(cache),
            "WGUO_O3A_START_BANK": "0",
            "WGUO_O3A_BANKS_PER_GROUP": "1",
            "WGUO_O3A_INJECTION_MODE": "none",
            "WGUO_O3A_INJECTION_FILE": "",
            "CRASHCAR_BG_ONLY": "1",
            "CRASHCAR_SINGLE_BACKGROUND_MODE": "rolling",
            "CRASHCAR_WORKER_BANK_IDS_EXPECTED": "0",
        }
    )
    result = subprocess.run(
        ["bash", str(PIPELINE)],
        cwd=run_dir,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    args = capture.read_text(encoding="utf-8").splitlines()
    assert "--cohfar-accumbackground-output-prefix" in args
    hist_index = args.index("--cuda-postcoh-hist-trials")
    assert args[hist_index + 1] == "100"
    assert "--cohfar-assignfar-input-fname" not in args
    assert not any(str(old_seed) in arg for arg in args)
    command_log = (
        run_dir / "logs" / "crashcar_command_000.txt"
    ).read_text(encoding="utf-8")
    assert "CRASHCAR_MULTI_ASSIGNFAR_ENABLED=0" in command_log

    disabled_dir = tmp_path / "disabled_normal"
    disabled_dir.mkdir()
    disabled_capture = tmp_path / "disabled_normal_args.txt"
    disabled_env = env.copy()
    disabled_env.update(
        {"CAPTURE_ARGS": str(disabled_capture), "CRASHCAR_ENABLE": "0"}
    )
    disabled = subprocess.run(
        ["bash", str(PIPELINE)],
        cwd=disabled_dir,
        env=disabled_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert disabled.returncode == 0, disabled.stderr
    disabled_args = disabled_capture.read_text(encoding="utf-8").splitlines()
    disabled_hist_index = disabled_args.index("--cuda-postcoh-hist-trials")
    assert disabled_args[disabled_hist_index + 1] == "100"

    producer_root = tmp_path / "producer"
    producer_worker = producer_root / "run" / "000"
    producer_worker.mkdir(parents=True)
    live_single = producer_worker / "single_background.json"
    # Deliberately leave single_background.json and all three multi stats
    # absent: live_readonly must build the normal graph and retry later.
    injection = _write(tmp_path / "injections.xml")
    live_dir = tmp_path / "live_consumer"
    live_dir.mkdir()
    live_capture = tmp_path / "live_consumer_args.txt"
    live_env = env.copy()
    live_env.update(
        {
            "CAPTURE_ARGS": str(live_capture),
            "CRASHCAR_BG_ONLY": "0",
            "CRASHCAR_ENABLE": "1",
            "CRASHCAR_SINGLE_BACKGROUND_MODE": "live_readonly",
            "CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE": "consumer",
            "CRASHCAR_LIVE_BACKGROUND_ROOT": str(producer_root),
            "CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON": str(live_single),
            "WGUO_O3A_NONINJ_STATS_LOC": str(producer_root / "run"),
            "WGUO_O3A_INJECTION_MODE": "blind",
            "WGUO_O3A_INJECTION_FILE": str(injection),
        }
    )
    live = subprocess.run(
        ["bash", str(PIPELINE)],
        cwd=live_dir,
        env=live_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert live.returncode == 0, live.stderr
    live_args = live_capture.read_text(encoding="utf-8").splitlines()
    live_hist_index = live_args.index("--cuda-postcoh-hist-trials")
    assert live_args[live_hist_index + 1] == "0"
    assert "--cohfar-accumbackground-output-prefix" not in live_args
    assert "--cohfar-assignfar-input-fname" in live_args
    assert "CRASHCAR_POSTCOH_HIST_TRIALS" not in PIPELINE.read_text(
        encoding="utf-8"
    )


def test_live_assignment_geometry_and_provenance_checks_remain_closed():
    sbatch = SBATCH.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for required in (
        "live_readonly requires injection consumer mode without accumulators",
        "live producer root is invalid",
        "live single producer binding is unavailable",
        "producer mismatch",
        "producer origin mismatch",
        "soft-start contract mismatch",
        "geometry mismatch",
        "worker list mismatch",
        "worker mismatch",
        "bank roster mismatch",
        "single path mismatch",
        "provenance mismatch",
        "strict live single producer binding validation failed",
        "single background mode must be rolling or live_readonly",
    ):
        assert required in sbatch
    for obsolete in (
        "frozen authority",
        "frozen BG source",
        "frozen BG runtime",
        "bundle worker geometry",
        "background run root mismatch",
    ):
        assert obsolete not in sbatch

    # The legacy workflow filename remains an env-compatible launcher name.
    # Its behavior is continuous producer + live read-only consumer, never a
    # stop/freeze/copy transaction or a cross-branch generation pin.
    for required in (
        "producer_consumer_overlap=true",
        "phase=concurrent_launchers_started",
        "initial_backgrounds_required=false",
        "consumer_soft_start_far_semantics=far_sngl_nonpositive_until_refresh",
        '"single_background_mode=rolling"',
        '"single_background_mode=live_readonly"',
    ):
        assert required in workflow
    for obsolete in (
        "single_background_mode=frozen",
        "frozen_bundle_manifest",
        "event_wide_generation",
        "common_generation_pin",
        "matching_generation",
        "generation_commit",
        "assign_frozen_far_ledger.py",
        "copy_single_background",
        "copy_multi_background",
    ):
        assert obsolete not in workflow
