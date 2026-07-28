#!/usr/bin/env python3
"""Minimal static check for the formal wrapper."""

from pathlib import Path
import subprocess


HERE = Path(__file__).resolve()
REPO_ROOT = next(parent for parent in HERE.parents
                 if (parent / "scripts" / "crashcar.sh").is_file())
WRAPPER = REPO_ROOT / "scripts" / "crashcar.sh"
LAUNCHER = REPO_ROOT / "gstlal-spiir/share/scripts/crashcar/crashcar.sh"


def test_formal_wrapper_only_delegates_a_or_b_to_packaged_launcher():
    text = WRAPPER.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 20
    assert "LAUNCHER=${REPO_ROOT}/gstlal-spiir/share/scripts/crashcar/crashcar.sh" in text
    assert '[ "$#" -eq 0 ]' in text
    assert 'CRASHCAR_CONFIG_FILE="${SCRIPT_DIR}/crashcar.env"' in text
    assert 'exec "${LAUNCHER}"' in text
    assert 'exec "${LAUNCHER}" "$@"' not in text
    assert "crashcar_controller.sh" not in text
    assert "frozen" not in text.lower()
    subprocess.run(["bash", "-n", str(WRAPPER)], check=True)


def test_role_runs_share_one_group_root():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'CONFIG_FILE=${DEFAULT_CONFIG}' in text
    assert '[ "$#" -eq 0 ]' in text
    assert "unset run_type crashcar_role background_run_root" in text
    assert 'ROLE=${run_type:-}' in text
    assert 'GROUP_ROOT=$(readlink -m -- "${SAVE_DIR}/${RUN_ID_VALUE}")' in text
    assert 'RUN_ROOT=${GROUP_ROOT}/${ROLE}' in text
    assert 'BACKGROUND_ROOT=${GROUP_ROOT}/A' in text
    assert "ROLE_OVERRIDE" not in text
    assert "BACKGROUND_OVERRIDE" not in text
    for duplicate in (
            "injection_data_file", "injection_detector_response_file",
            "injection_segment_xml", "injection_start_gps",
            "injection_duration_hour", "injection_duration_seconds"):
        assert duplicate not in text
