from pathlib import Path
import ast
import math

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPIIRPARTS = ROOT / "gstlal-spiir/python/pipemodules/spiirparts.py"
SINGLEFAR = ROOT / "gstlal-spiir/gst/cuda/cohfar/crashcar_singlefar.c"
ASSIGNFAR = ROOT / "gstlal-spiir/gst/cuda/cohfar/cohfar_assignfar.c"
def _text(path):
    return path.read_text(encoding="utf-8")


def test_pipeline_keeps_single_authority_h1_l1_inside_unified_element():
    graph = _text(SPIIRPARTS)
    unified = _text(ASSIGNFAR)
    engine = _text(SINGLEFAR)

    # The graph now creates one public FAR element.  The internal single
    # module remains explicitly enabled there and never widens its authority
    # beyond the first two canonical H1/L1 slots.
    assert graph.count("pipemodules.mkcohfar_assignfar(") == 1
    assert "pipemodules.mkcrashcar_singlefar(" not in graph
    assert 'engine->enabled = !strcmp(cfg("CRASHCAR_ENABLE", "0"), "1")' in engine
    assert 'state.producer = !strcmp(role, "A")' in engine
    assert 'if (!state.producer && strcmp(role, "B")) return FALSE' in engine
    assert "crashcar_singlefar_engine_transform_ip(&element->single, buf)" in unified
    assert "for (int ifo = 0; ifo < 2; ++ifo)" in engine


def test_single_routes_are_h1_l1_parameterized_and_multi_owned_is_separate():
    text = _text(SINGLEFAR)
    assert '!strcmp(ifos, "H1") || !strcmp(ifos, "H1V1")' in text
    assert '!strcmp(ifos, "L1") || !strcmp(ifos, "L1V1")' in text
    assert '!strcmp(ifos, "H1L1") || !strcmp(ifos, "H1L1V1")' in text
    assert 'return !strcmp(ifos, "V1") ? 3 : -1' in text


def test_snr_boundary_is_finite_and_includes_four():
    text = _text(SINGLEFAR)
    assert "#define MIN_SNR 0x1p+2" in text
    assert "rho < MIN_SNR" in text
    assert "!isfinite(rho)" in text


def _load_finalsink_snr_predicate():
    finalsink = ROOT / "gstlal-spiir/python/pipemodules/postcoh_finalsink.py"
    tree = ast.parse(_text(finalsink), filename=str(finalsink))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_crashcar_single_snr_eligible"
    )
    namespace = {"math": math}
    exec(compile(ast.Module(body=[function], type_ignores=[]),
                 str(finalsink), "exec"), namespace)
    return namespace["_crashcar_single_snr_eligible"]


@pytest.mark.parametrize(
    "snr,expected",
    [(3.9999, False), (4.0, True), (4.0001, True)],
)
def test_finalsink_snr_boundary_values(snr, expected):
    assert _load_finalsink_snr_predicate()(snr) is expected
