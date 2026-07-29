#!/usr/bin/env python3
"""Source contracts for the unified multi/single FAR GStreamer element."""

from pathlib import Path


CRASHCAR_DIR = Path(__file__).resolve().parents[1]
SPIIR_ROOT = CRASHCAR_DIR.parents[2]


def source(path):
    return (SPIIR_ROOT / path).read_text(encoding="utf-8")


def test_only_cohfar_assignfar_is_a_public_far_factory():
    plugin = source("gst/cuda/libgstcuda.c")
    single_header = source("gst/cuda/cohfar/crashcar_singlefar.h")
    makefile = source("gst/cuda/Makefile.am")

    assert plugin.count('{ "cohfar_assignfar", COHFAR_ASSIGNFAR_TYPE }') == 1
    assert "crashcar_singlefar" not in plugin
    assert "CRASHCAR_SINGLEFAR_TYPE" not in single_header
    assert "crashcar_singlefar_get_type" not in single_header
    assert "GstBaseTransform" not in single_header
    assert "cohfar/crashcar_singlefar.c" in makefile


def test_unified_element_runs_unchanged_multi_then_internal_single():
    unified = source("gst/cuda/cohfar/cohfar_assignfar.c")
    header = source("gst/cuda/cohfar/cohfar_assignfar.h")
    start = unified.rindex(
        "static GstFlowReturn cohfar_assignfar_transform_ip(")
    end = unified.index("/*\n * ==============================", start)
    transform = unified[start:end]

    multi = transform.index("cohfar_assignfar_transform_multi(element, buf)")
    single = transform.index(
        "crashcar_singlefar_engine_transform_ip(&element->single, buf)")
    gap = transform.index(
        "GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_GAP)")
    assert gap < multi < single
    assert transform[gap:multi].count("return GST_FLOW_OK;") == 1
    assert "if (result != GST_FLOW_OK) return result;" in transform
    assert "CrashcarSingleFarEngine single;" in header
    assert "crashcar_singlefar_engine_start(&element->single)" in unified
    assert "crashcar_singlefar_engine_clear(&element->single)" in unified


def test_python_graph_constructs_one_configurable_far_element_per_stream():
    wrapper = source("python/pipemodules/__init__.py")
    graph = source("python/pipemodules/spiirparts.py")

    assert "def mkcrashcar_singlefar(" not in wrapper
    assert 'Gst.ElementFactory.make("crashcar_singlefar")' not in wrapper
    assert wrapper.count('Gst.ElementFactory.make("cohfar_assignfar"') == 2

    assert graph.count("pipemodules.mkcohfar_assignfar(") == 1
    assert "pipemodules.mkcrashcar_singlefar(" not in graph
    assert 'crashcar_role = os.environ.get("CRASHCAR_ROLE", "")' in graph
    assert 'accumulate_multi_background = crashcar_role != "B"' in graph
    assert "if accumulate_multi_background:" in graph
    assert "input_fname=cohfar_assignfar_input_fname" in graph


def test_unified_properties_keep_multi_and_single_authorities_independent():
    unified = source("gst/cuda/cohfar/cohfar_assignfar.c")
    engine = source("gst/cuda/cohfar/crashcar_singlefar.c")

    assert '"input-fname"' in unified
    assert '"refresh-interval"' in unified
    assert "engine->enabled = FALSE;" in engine
    assert 'state.producer = !strcmp(role, "A")' in engine
    assert 'strcmp(role, "B")' in engine
    assert 'cfg("CRASHCAR_SINGLE_BACKGROUND_JSON", "")' in engine
    assert "write_background(&next)" in engine
    assert "read_background(&next)" in engine
    assert "if (!state.producer) refresh(group_gps);" in engine


def test_single_background_livetime_gate_is_shared_by_producer_and_consumer():
    engine = source("gst/cuda/cohfar/crashcar_singlefar.c")

    assert "seconds > (double)state.window / (5.0 * NS)" in engine
    assert engine.count("single_livetime_is_valid(") == 3
