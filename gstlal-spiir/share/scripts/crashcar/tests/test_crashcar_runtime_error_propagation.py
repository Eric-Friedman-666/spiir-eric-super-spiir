import ast
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace


SPIIR_ROOT = Path(__file__).resolve().parents[4]
ONLINE = SPIIR_ROOT / "bin" / "gstlal_inspiral_postcohspiir_online"
PIPELINE_WRAPPER = Path(__file__).resolve().parents[1] / "crashcar_pipeline.sh"
SBATCH_WRAPPER = Path(__file__).resolve().parents[1] / "crashcar_sbatch.sh"
FINALSINK = SPIIR_ROOT / "python" / "pipemodules" / "postcoh_finalsink.py"
REPO_ROOT = SPIIR_ROOT.parent
CLOSURE_SOURCE = REPO_ROOT / "tests" / "crashcar" / "runtime_source_closure.py"
_CLOSURE_SPEC = importlib.util.spec_from_file_location(
    "crashcar_runtime_source_closure_for_error_test", CLOSURE_SOURCE
)
assert _CLOSURE_SPEC and _CLOSURE_SPEC.loader
CLOSURE = importlib.util.module_from_spec(_CLOSURE_SPEC)
_CLOSURE_SPEC.loader.exec_module(CLOSURE)


class _BaseHandler:
    def __init__(self, mainloop, pipeline):
        self.mainloop = mainloop
        self.pipeline = pipeline


class _Logger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append(message % args if args else message)


ERROR = object()
EOS = object()
LOGGER = _Logger()


def _actual_contract_namespace():
    tree = ast.parse(ONLINE.read_text(), filename=str(ONLINE))
    selected = [
        node for node in tree.body
        if ((isinstance(node, ast.ClassDef) and
             node.name == "CrashcarPipelineHandler") or
            (isinstance(node, ast.FunctionDef) and
             node.name == "_flush_remaining_finalsink_output"))
    ]
    assert [node.name for node in selected] == [
        "CrashcarPipelineHandler", "_flush_remaining_finalsink_output"]
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "simplehandler": SimpleNamespace(Handler=_BaseHandler),
        "Gst": SimpleNamespace(
            MessageType=SimpleNamespace(ERROR=ERROR, EOS=EOS)),
        "logger": LOGGER,
    }
    exec(compile(module, str(ONLINE), "exec"), namespace)
    return namespace


def test_actual_handler_records_error_and_keeps_default_handler_authoritative():
    handler_cls = _actual_contract_namespace()["CrashcarPipelineHandler"]
    handler = handler_cls(object(), object())
    error = SimpleNamespace(domain="stream", code=9, message="primary failure")
    message = SimpleNamespace(
        type=ERROR, parse_error=lambda: (error, "debug-origin"))
    assert handler.do_on_message(object(), message) is False
    assert handler.terminal_bus_error == (
        "stream", 9, "primary failure", "debug-origin")


def test_actual_handler_eos_remains_clean_and_does_not_parse_error():
    handler_cls = _actual_contract_namespace()["CrashcarPipelineHandler"]
    handler = handler_cls(object(), object())
    message = SimpleNamespace(
        type=EOS,
        parse_error=lambda: (_ for _ in ()).throw(
            AssertionError("EOS must not parse an error")))
    assert handler.do_on_message(object(), message) is False
    assert handler.terminal_bus_error is None


class _Sink:
    def __init__(self, start, end):
        self.t_snapshot_start = start
        self.last_buffer_timestamp = end
        self.filename_args = None
        self.write_args = None

    def get_output_filename(self, prefix, name, start, duration):
        self.filename_args = (prefix, name, start, duration)
        return "normal.xml.gz"

    def write_output_file(self, filename=None, verbose=False):
        self.write_args = (filename, verbose)


def test_none_timestamp_cleanup_skips_without_output_or_fake_time():
    flush = _actual_contract_namespace()["_flush_remaining_finalsink_output"]
    sink = _Sink(None, None)
    assert flush(sink, "prefix", None, verbose=True) is False
    assert sink.filename_args is None
    assert sink.write_args is None


def test_timestamped_cleanup_preserves_normal_filename_and_write_arguments():
    flush = _actual_contract_namespace()["_flush_remaining_finalsink_output"]
    sink = _Sink(100, 137)
    assert flush(sink, "prefix", "name", verbose=True) is True
    assert sink.filename_args == ("prefix", "name", 100, 37)
    assert sink.write_args == ("normal.xml.gz", True)


def test_online_epilogue_has_structured_nonzero_error_and_clean_eos_paths():
    source = ONLINE.read_text()
    assert "handler = CrashcarPipelineHandler(mainloop, pipeline)" in source
    assert "terminal_bus_error = handler.terminal_bus_error" in source
    assert "if terminal_bus_error is None:" in source
    assert "raise SystemExit(1)" in source
    assert "grep" not in source[source.index("mainloop.run()"):]


def test_finalsink_and_wrappers_remain_normal_owned_and_direct():
    finalsink_sha = subprocess.check_output(
        ["sha256sum", str(FINALSINK)], text=True).split()[0]
    closure_report = CLOSURE.build_report()
    finalsink_relative = str(FINALSINK.relative_to(REPO_ROOT))
    assert closure_report["all_passed"], closure_report["errors"]
    assert (
        closure_report["production_runtime_hashes"][finalsink_relative]
        == finalsink_sha
    )
    pipeline = PIPELINE_WRAPPER.read_text()
    sbatch = SBATCH_WRAPPER.read_text()
    assert pipeline.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert '"${CMD[@]}" || PIPELINE_RC=$?' in pipeline
    assert "CRASHCAR_PIPELINE_EXIT_STATUS_FILE" in pipeline
    assert 'exit "${PIPELINE_RC}"' in pipeline
    assert "run_spiir_py3" in sbatch
    assert "wguo-single-det-py3 bash" in sbatch
    assert "-e CRASHCAR_PIPELINE_EXIT_STATUS_FILE=" in sbatch
    assert '[ -s "${STATUS_FILE}" ]' in sbatch
    assert 'exit "${PIPELINE_RC}"' in sbatch


def _fake_pipeline_env(tmp_path, exit_code):
    crash_root = tmp_path / "runtime"
    install_bin = crash_root / "install" / "bin"
    install_bin.mkdir(parents=True)
    executable = install_bin / "gstlal_inspiral_postcohspiir_online"
    executable.write_text(
        "#!/usr/bin/env bash\nexit \"\u0024{SYNTHETIC_PIPELINE_EXIT:?}\"\n")
    executable.chmod(0o755)
    bank_dir = tmp_path / "banks"
    bank_dir.mkdir()
    for ifo in ("H1", "L1", "V1"):
        (bank_dir / f"iir_{ifo}-GSTLAL_SPLIT_BANK_0008-a1-0-0.xml.gz").touch()
    detector_response = tmp_path / "detrsp.xml"
    frame_cache = tmp_path / "frames.cache"
    detector_response.touch()
    frame_cache.touch()
    stats_root = tmp_path / "stats"
    stats_root.mkdir()
    env = os.environ.copy()
    env.update({
        "CRASH_ROOT": str(crash_root),
        "TOP_RUN_ROOT": str(tmp_path),
        "CRASHCAR_ROLE": "A",
        "CRASHCAR_ENABLE": "1",
        "SLURM_ARRAY_TASK_ID": "0",
        "WGUO_O3A_START_GPS": "1252193967",
        "WGUO_O3A_END_GPS": "1252193968",
        "WGUO_O3A_START_BANK": "8",
        "WGUO_O3A_BANKS_PER_GROUP": "1",
        "CRASHCAR_WORKER_BANK_IDS_EXPECTED": "8",
        "WGUO_O3A_INJECTION_FILE": "",
        "WGUO_O3A_BANK_DIR": str(bank_dir),
        "WGUO_O3A_NONINJ_STATS_LOC": str(stats_root),
        "WGUO_O3A_DETRSP_MAP": str(detector_response),
        "WGUO_O3A_FRAME_CACHE": str(frame_cache),
        "SYNTHETIC_PIPELINE_EXIT": str(exit_code),
    })
    return env


def test_production_pipeline_wrapper_propagates_nonzero_and_clean_exit(tmp_path):
    error_root = tmp_path / "error"
    error_root.mkdir()
    error_status = error_root / "logs" / "pipeline.status"
    error_env = _fake_pipeline_env(tmp_path / "error_env", 23)
    error_env["CRASHCAR_PIPELINE_EXIT_STATUS_FILE"] = str(error_status)
    error = subprocess.run(
        ["bash", str(PIPELINE_WRAPPER)], cwd=error_root,
        env=error_env, text=True, capture_output=True)
    assert error.returncode == 23
    assert error_status.read_text() == "23\n"

    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    clean_status = clean_root / "logs" / "pipeline.status"
    clean_env = _fake_pipeline_env(tmp_path / "clean_env", 0)
    clean_env["CRASHCAR_PIPELINE_EXIT_STATUS_FILE"] = str(clean_status)
    clean = subprocess.run(
        ["bash", str(PIPELINE_WRAPPER)], cwd=clean_root,
        env=clean_env, text=True, capture_output=True)
    assert clean.returncode == 0
    assert clean_status.read_text() == "0\n"


def test_sbatch_propagates_pipeline_status_directly():
    source = SBATCH_WRAPPER.read_text()
    assert '[ -s "${STATUS_FILE}" ]' in source
    assert 'IFS= read -r PIPELINE_RC < "${STATUS_FILE}"' in source
    assert 'rm -f -- "${STATUS_FILE}"' in source
    assert '[[ "${PIPELINE_RC}" =~ ^(0|[1-9][0-9]{0,2})$ ]]' in source
    assert source.rstrip().endswith('exit "${PIPELINE_RC}"')
