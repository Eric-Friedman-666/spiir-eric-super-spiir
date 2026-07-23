from pathlib import Path
import ast
import ctypes
import math

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPIIRPARTS = ROOT / "gstlal-spiir/python/pipemodules/spiirparts.py"
SINGLEFAR = ROOT / "gstlal-spiir/gst/cuda/cohfar/crashcar_singlefar.c"
ASSIGNFAR = ROOT / "gstlal-spiir/gst/cuda/cohfar/cohfar_assignfar.c"
PLUGIN = (
    ROOT / "gstlal-spiir/gst/cuda/.libs/libgstcuda.so.0.0.0"
)


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
    assert "single_enabled=crashcar_enabled" in graph
    assert "crashcar_singlefar_engine_transform_ip(&element->single, buf)" in unified
    assert "for (int ifo_id = 0; ifo_id < 2; ++ifo_id)" in engine


def test_exported_singlefar_ifo_validator_accepts_only_canonical_h1l1():
    if not PLUGIN.is_file():
        pytest.skip("requires built OzSTAR crashcar plugin")
    library = ctypes.CDLL(str(PLUGIN), mode=ctypes.RTLD_GLOBAL)
    validator = library.crashcar_singlefar_ifos_valid
    validator.argtypes = [ctypes.c_char_p]
    validator.restype = ctypes.c_int
    expected = {
        "H1L1": True,
        "H1": False,
        "L1": False,
        "H1H1": False,
        "H1L1H1": False,
        "L1H1": False,
        "H1L1V1": False,
        "H1V1": False,
        "H1K1": False,
        "V1": False,
        "K1": False,
    }
    for ifos, accepted in expected.items():
        assert bool(validator(ifos.encode("ascii"))) is accepted


def test_snr_boundary_is_finite_and_includes_four():
    text = _text(SINGLEFAR)
    assert "#define CRASHCAR_MIN_SNR 0x1.0000000000000p+2" in text
    assert "table->snglsnr[ifo_id] >= CRASHCAR_MIN_SNR" in text
    assert "!isfinite(table->snglsnr[ifo_id])" in text


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
