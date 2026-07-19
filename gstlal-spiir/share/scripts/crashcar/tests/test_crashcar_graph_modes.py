#!/usr/bin/env python3
"""Graph-mode contracts for normal cohfar stages in crashcar workflows."""

from pathlib import Path

import pytest

from gstlal_spiir.pipemodules import spiirparts


CRASHCAR_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("multi_background_frozen", "background_only", "expected"),
    (
        (False, False, (True, True)),
        (False, True, (True, False)),
        (True, False, (False, True)),
    ),
)
def test_cohfar_graph_stage_policy_three_modes(
        multi_background_frozen, background_only, expected):
    """rolling assigns+accumulates; BG-only only accumulates; frozen only assigns."""
    assert spiirparts._cohfar_graph_stage_policy(
        multi_background_frozen, background_only) == expected


def test_cohfar_graph_stage_policy_rejects_frozen_bg_only():
    with pytest.raises(ValueError, match="background-only mode cannot use frozen"):
        spiirparts._cohfar_graph_stage_policy(True, True)


def test_pipeline_bg_only_has_no_external_multi_assignfar_input():
    pipeline = (CRASHCAR_DIR / "crashcar_pipeline.sh").read_text(
        encoding="utf-8")
    assert "multi_assignfar_enabled=1" in pipeline
    assert "multi_assignfar_enabled=0" in pipeline
    assert "multi_assignfar_enabled=0\n    macrofarinput=" in pipeline
    assert pipeline.count('--cohfar-assignfar-input-fname') == 1
    assert 'if [ "${multi_assignfar_enabled}" = "1" ]; then' in pipeline
    assert '--cohfar-assignfar-input-fname "${macrofarinput}"' in pipeline
    assert "CRASHCAR_MULTI_ASSIGNFAR_ENABLED=%s" in pipeline


def test_graph_wires_policy_to_normal_accumulator_and_assignfar():
    source = Path(spiirparts.__file__).read_text(encoding="utf-8")
    assert "if accumulate_multi_background:" in source
    assert "pipemodules.mkcohfar_accumbackground(" in source
    assert "if assign_multi_far:" in source
    assert "pipemodules.mkcohfar_assignfar(" in source
    assert source.index("if accumulate_multi_background:") < source.index(
        "if assign_multi_far:")
