#!/usr/bin/env python3
"""Executable contract for the existing SNR-series threshold passthrough."""

import os
import subprocess
from pathlib import Path


SBATCH = Path(__file__).resolve().parents[1] / "crashcar_sbatch.sh"


def test_external_zero_reaches_container_pipeline_command(tmp_path):
    text = SBATCH.read_text(encoding="utf-8")
    start = text.index("run_spiir_py3 " + chr(92))
    end = text.index("\n\ncrashcar_finish_pipeline_status", start)
    command = text[start:end]
    script = r'''set -euo pipefail
SNR_series_logFAR_threshold=$1
collect_walltime_for_container=1200,1200,1200
pipeline_exit_status_file="$CAPTURE_ROOT/pipeline.status"
CRASH_ROOT="$CAPTURE_ROOT/runtime"
TOP_RUN_ROOT="$CAPTURE_ROOT/run"
WGUO_O3A_START_GPS=1252198622
WGUO_O3A_END_GPS=1252202222
crashcar_segment_livetime_json_sha256=a
crashcar_segment_xml_sha256=b
crashcar_segment_run_start=1252198622
crashcar_segment_run_end=1252202222
CRASHCAR_BG_WORKER_COUNT=1
CRASHCAR_BG_ORIGIN_GPS=1252198622
CRASHCAR_BG_RUN_NAMESPACE_SHA256=c
CRASHCAR_BG_SOURCE_MANIFEST_SHA256=d
CRASHCAR_BG_RUNTIME_MANIFEST_SHA256=e
CRASHCAR_BG_CONFIG_SHA256=f
CRASHCAR_BG_SEGMENT_XML_SHA256=1
CRASHCAR_BG_SEGMENT_CANONICAL_SHA256=2
CRASHCAR_TEMPLATE_SHAPE_MAP_SHA256=3
CRASHCAR_WORKER_BANK_IDS_EXPECTED=8,9,10,11,12,13,14,15
run_spiir_py3() {
    printf '%s\n' "$@" > "$CAPTURE_ARGS"
}
''' + command + "\n"
    capture = tmp_path / "container.args"
    env = os.environ.copy()
    env["CAPTURE_ROOT"] = str(tmp_path)
    env["CAPTURE_ARGS"] = str(capture)
    result = subprocess.run(
        ["bash", "-c", script, "snr-threshold-pass", "0"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    args = capture.read_text(encoding="utf-8").splitlines()
    threshold = "SNR_series_logFAR_threshold=0"
    assert args.count(threshold) == 1
    index = args.index(threshold)
    assert args[index - 1] == "-e"
    assert args[-3:] == [
        "wguo-single-det-py3",
        "bash",
        str(tmp_path / "run" / "scripts" / "crashcar_pipeline.sh"),
    ]
