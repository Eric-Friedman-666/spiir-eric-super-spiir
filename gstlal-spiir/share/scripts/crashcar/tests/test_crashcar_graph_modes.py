#!/usr/bin/env python3
"""Graph-mode contracts for the unified FAR stage in crashcar workflows."""

import ast
from pathlib import Path

import pytest

CRASHCAR_DIR = Path(__file__).resolve().parents[1]
SPIIR_ROOT = CRASHCAR_DIR.parents[2]
SPIIRPARTS_PATH = SPIIR_ROOT / "python" / "pipemodules" / "spiirparts.py"
SPIIRPARTS_SOURCE = SPIIRPARTS_PATH.read_text(encoding="utf-8")


def _load_stage_policy():
    tree = ast.parse(SPIIRPARTS_SOURCE)
    node = next(item for item in tree.body
                if isinstance(item, ast.FunctionDef)
                and item.name == "_cohfar_graph_stage_policy")
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]),
                 str(SPIIRPARTS_PATH), "exec"), namespace)
    return namespace["_cohfar_graph_stage_policy"]


STAGE_POLICY = _load_stage_policy()


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
    assert STAGE_POLICY(
        multi_background_frozen, background_only) == expected


def test_cohfar_graph_stage_policy_rejects_frozen_bg_only():
    with pytest.raises(ValueError, match="background-only mode cannot use frozen"):
        STAGE_POLICY(True, True)


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


def test_graph_wires_policy_to_accumulator_and_one_unified_far_element():
    source = SPIIRPARTS_SOURCE
    assert "if accumulate_multi_background:" in source
    assert "pipemodules.mkcohfar_accumbackground(" in source
    assert "if assign_multi_far or crashcar_enabled:" in source
    assert source.count("pipemodules.mkcohfar_assignfar(") == 1
    assert "pipemodules.mkcrashcar_singlefar(" not in source
    assert "assign_multi_far=assign_multi_far" in source
    assert "single_enabled=crashcar_enabled" in source
    assert source.index("if accumulate_multi_background:") < source.index(
        "if assign_multi_far or crashcar_enabled:")
