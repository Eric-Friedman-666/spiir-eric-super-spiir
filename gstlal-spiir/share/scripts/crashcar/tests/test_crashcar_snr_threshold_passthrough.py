#!/usr/bin/env python3
"""Contract for the existing SNR-series threshold passthrough."""

from pathlib import Path


SBATCH = Path(__file__).resolve().parents[1] / "crashcar_sbatch.sh"


def test_external_zero_reaches_container_pipeline_command():
    text = SBATCH.read_text(encoding="utf-8")
    binding = '-e SNR_series_logFAR_threshold="${SNR_series_logFAR_threshold:?}"'
    assert text.count(binding) == 1
    assert 'wguo-single-det-py3 bash "${TOP_RUN_ROOT}/scripts/crashcar_pipeline.sh"' in text
