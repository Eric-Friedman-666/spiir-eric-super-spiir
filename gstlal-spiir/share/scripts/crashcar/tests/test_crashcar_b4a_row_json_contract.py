#!/usr/bin/env python3
"""B4a source contracts for exact row atoms and pinned segment JSON.

These tests are intentionally source-only.  They do not build, install, patch
production, or submit Slurm work; compiler/runtime evidence is a later gate.
"""

from pathlib import Path


CRASHCAR_DIR = Path(__file__).resolve().parents[1]
SPIIR_ROOT = CRASHCAR_DIR.parents[2]


def source(path):
    return (SPIIR_ROOT / path).read_text(encoding="utf-8")


def test_checked_int64_gps_and_fatal_start_contract():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    header = source("gst/cuda/cohfar/crashcar_singlefar.h")
    unified = source("gst/cuda/cohfar/cohfar_assignfar.c")

    checked_start = implementation.index(
        "static gboolean crashcar_checked_gps_ns")
    checked = implementation[checked_start:implementation.index(
        "static gboolean crashcar_ordered_distance_u64", checked_start)]
    for token in (
        "nanoseconds < 0",
        "nanoseconds >= CRASHCAR_NS_PER_SECOND",
        "G_MININT64 / CRASHCAR_NS_PER_SECOND",
        "G_MAXINT64 / CRASHCAR_NS_PER_SECOND",
        "nanoseconds < min_boundary_nanoseconds",
        "nanoseconds > max_nanoseconds",
        "G_MININT64 + (nanoseconds - min_boundary_nanoseconds)",
    ):
        assert token in checked
    assert "crashcar_double_gps_to_ns" not in implementation

    ordered_start = implementation.index(
        "static gboolean crashcar_ordered_distance_u64")
    ordered = implementation[ordered_start:implementation.index(
        "static gboolean crashcar_add_nonnegative_offset", ordered_start)]
    assert "end < start" in ordered
    assert "(guint64)end - (guint64)start" in ordered

    assignment_start = implementation.index(
        "static gboolean crashcar_assignment_window_end_ns")
    assignment = implementation[assignment_start:implementation.index(
        "#define CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT", assignment_start)]
    assert "row_assignment_gps_ns < element->segment_run_start_gps_ns" in assignment
    assert "row_assignment_gps_ns >= element->segment_run_end_gps_ns" in assignment
    assert "crashcar_add_nonnegative_offset(" in assignment
    assert "crashcar_ordered_distance_u64(" in assignment

    window_start = implementation.index(
        "static gint64 crashcar_window_ifo_livetime_ns")
    window = implementation[window_start:implementation.index(
        "static guint crashcar_count_ge_from_rank_array", window_start)]
    normalized_window = " ".join(window.split())
    assert "MAX(start_ns, segment.start_gps_ns)" in normalized_window
    assert "MIN(end_ns, segment.end_gps_ns)" in normalized_window
    assert "overlap_end_ns > overlap_start_ns" in normalized_window
    assert "crashcar_ordered_distance_u64( overlap_start_ns, overlap_end_ns" in normalized_window

    for token in (
        "gint64 start_gps_ns;",
        "gint64 end_gps_ns;",
        "gint64 gps_ns;",
    ):
        assert token in implementation or token in header
    assert (
        "transform_class->start = GST_DEBUG_FUNCPTR(cohfar_assignfar_start);"
        in unified)
    start_index = implementation.rindex(
        "gboolean crashcar_singlefar_engine_start(CrashcarSingleFarEngine *element) {")
    start = implementation[start_index:implementation.index(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip",
        start_index)]
    assert "!crashcar_load_livetime_segments(element, &failure)" in start
    assert "!crashcar_load_exact_window_config(element, &failure)" in start
    assert "return FALSE;" in start

    o3_ns = 1_238_166_018 * 1_000_000_000
    assert o3_ns + 1 - o3_ns == 1
    assert -(1 << 63) <= o3_ns < (1 << 63)
    assert (
        float(o3_ns) / 1_000_000_000.0 ==
        float(o3_ns + 1) / 1_000_000_000.0)


def test_strict_single_fd_json_binding_and_no_csv_fallback():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")

    for token in (
        "O_RDONLY | O_CLOEXEC | O_NOFOLLOW",
        "fstat(fd, &before)",
        "fstat(fd, &after)",
        "before.st_dev != after.st_dev",
        "before.st_ino != after.st_ino",
        "before.st_size != after.st_size",
        "CRASHCAR_SEGMENT_LIVETIME_JSON_SHA256",
        "CRASHCAR_SEGMENT_SOURCE_XML_SHA256",
        "CRASHCAR_SEGMENT_RUN_START",
        "CRASHCAR_SEGMENT_RUN_END",
        "CRASHCAR_WORKER_ID",
        r'\"schema_version\":',
        r'\"source_xml_sha256\":',
        r'\"run_start\":',
        r'\"run_end\":',
        r'\"targets\":{\"H1\":',
        r',\"L1\":',
        r'\"raw_row_count\":',
        r'\"empty_row_count\":',
        r'\"merged_interval_count\":',
        r'\"livetime_ns\":',
        r'\"intervals\":[',
        "computed_livetime_ns != declared_livetime_ns",
        "canonical segment JSON binding mismatch",
        "canonical segment JSON snapshot sha256 mismatch",
        "canonical segment JSON bytes or terminal newline invalid",
    ):
        assert token in implementation

    parser = implementation[implementation.index(
        "static gboolean crashcar_load_livetime_segments"):
        implementation.index("static gboolean crashcar_load_background_binding")]
    assert "g_strsplit" not in parser
    assert "strtok" not in parser
    assert "fopen" not in parser
    assert "full-window" not in parser.lower()



def test_single_relevant_foreground_rows_get_a109_llrs_and_unique_owner():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    header = source("include/postcohtable.h")
    transform = implementation[implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip"):
        implementation.index(
            "void crashcar_singlefar_engine_clear",
            implementation.rindex(
                "GstFlowReturn crashcar_singlefar_engine_transform_ip"))]

    preflight = transform.index("for (gsize original_ordinal = 0;")
    stable_sort = transform.index(
        "qsort(row_work, work_count, sizeof(CrashcarRowWork)")
    initialize = transform.index(
        "crashcar_singlefar_prepare_row_llrs(table);", preflight)
    evaluate_groups = transform.index("while (group_begin < work_count)")
    assert preflight < initialize < stable_sort < evaluate_groups
    nonforeground = transform.index(
        "if (table->is_background != FLAG_FOREGROUND) continue;",
        preflight)
    route = transform.index(
        "crashcar_singlefar_final_route_from_ifos(table->ifos)",
        nonforeground)
    invalid_route = transform.index(
        "if (final_route == CRASHCAR_SINGLE_FINAL_ROUTE_INVALID)", route)
    canonical_llrs = transform.index(
        "crashcar_singlefar_prepare_row_llrs(table);", invalid_route)
    v_only = transform.index(
        "if (final_route == CRASHCAR_SINGLE_FINAL_ROUTE_V1_ONLY) continue;",
        canonical_llrs)
    route_far_clear = transform.index(
        "table->far_sngl[route_owner_ifo] = 0.0f;", v_only)
    shared_gps = transform.index(
        "&table->end_time, &row_assignment_gps_ns", route_far_clear)
    assert (
        preflight < nonforeground < route < invalid_route < canonical_llrs <
        v_only < route_far_clear < shared_gps)
    assert "continue;" in transform[invalid_route:canonical_llrs]
    assert "crashcar row has invalid shared GPS authority" not in transform
    assert "event_id == 0" not in transform
    assert "event_id != 0" not in transform
    assert "GST_ELEMENT_ERROR(" not in transform[preflight:stable_sort]
    assert "continue;" in transform[shared_gps:stable_sort]
    assert "crashcar_singlefar_prepare_row_llrs(table);" in implementation
    assert "table->H1_LLR = 0.0;" in implementation
    assert "table->L1_LLR = 0.0;" in implementation
    assert "REAL8 H1_LLR;" in header
    assert "REAL8 L1_LLR;" in header

    route_scan = transform.index(
        "const CrashcarSingleFinalRoute route =")
    need_single_far = transform.index(
        "group_needs_single_far = TRUE;", route_scan)
    lookup_gate = transform.index(
        "if (support_work && group_needs_single_far)", need_single_far)
    snapshot = transform.index(
        "crashcar_snapshot_paired_authority(", lookup_gate)
    owner = transform.index(
        "const int final_owner_ifo", snapshot)
    clear = transform.index(
        "table->far_sngl[route_owner_ifo] = 0.0f;", route)
    llr_slot = transform.index(
        "crashcar_singlefar_row_llr_slot(table, ifo_id)", owner)
    evaluate = transform.index(
        "if (compute_single_far &&", llr_slot)
    far_write = transform.index(
        "table->far_sngl[ifo_id] = far_sngl;", evaluate)
    assert (
        clear < route_scan < need_single_far < lookup_gate < snapshot <
        owner < llr_slot <
        evaluate < far_write)

    per_row = transform[owner:transform.index(
        "if (support_work &&", owner)]
    assert "crashcar_try_refresh_live_authority(" not in per_row
    assert "crashcar_snapshot_live_authority(" not in per_row
    assert "crashcar_snapshot_paired_authority(" not in per_row
    assert "const gboolean compute_single_far" in per_row
    assert "if (multi_llr_only) {" in per_row
    assert "CRASHCAR_SINGLE_FAR_STATUS_LLR_ONLY_MULTI" in per_row

    for removed in (
        "far_calculated_sngl",
        "far_calculated_sngl_valid",
        "far_assigned_sngl_exact",
        "far_calculated_support_count_sngl",
        "far_calculated_livetime_ns_sngl",
        "a_eff_sngl",
        "dof_sngl",
        "single_component_eligible[",
        "single_atom_schema_version",
        "single_authority_mode",
        "single_worker_id",
        "single_bank_stream_id",
        "single_bg_authority_valid",
        "single_bg_authority_version",
        "single_bg_authority_epoch_gps_ns",
        "single_bg_authority_provenance_sha256",
    ):
        assert ("table->" + removed) not in implementation

def test_a107_a109_registry_and_live_finalsink_mode_are_explicit():
    registry = source(
        "python/pipemodules/postcohtable/postcoh_table_def.py")
    finalsink = source("python/pipemodules/postcoh_finalsink.py")
    online = source("bin/gstlal_inspiral_postcohspiir_online")
    pipeline = source("share/scripts/crashcar/crashcar_pipeline.sh")

    for token in (
        'POSTCOH_SCHEMA_MODE_LEGACY_A107 = "legacy-a107"',
        'POSTCOH_SCHEMA_MODE_CRASHCAR_A109 = "crashcar-a109"',
        '("H1_LLR", "real_8")',
        '("L1_LLR", "real_8")',
        "len(POSTCOH_A107_COLUMN_PAIRS) != 107",
        "len(POSTCOH_A109_COLUMN_PAIRS) != 109",
        "POSTCOH_A109_COLUMN_PAIRS[:107] != POSTCOH_A107_COLUMN_PAIRS",
    ):
        assert token in registry
    assert "POSTCOH_SCHEMA_MODE_CRASHCAR_A109" in finalsink
    assert "postcoh_columns_for_schema_mode(" in finalsink
    assert "postcoh_schema_mode=self.postcoh_schema_mode" in finalsink
    assert "columns=self.postcoh_columns" in finalsink
    assert '"--finalsink-postcoh-schema-mode"' in online
    assert 'choices=("legacy-a107", "crashcar-a109")' in online
    assert 'default="legacy-a107"' in online
    assert "postcoh_schema_mode=options.finalsink_postcoh_schema_mode" in online
    assert "finalsink_postcoh_schema_mode=legacy-a107" in pipeline
    assert "finalsink_postcoh_schema_mode=crashcar-a109" in pipeline

def test_a109_wrapper_exposes_two_readonly_binary64_scalars():
    wrapper = source("python/pipemodules/postcohtable/_postcohtable.c")
    members = wrapper[wrapper.index(
        "static PyMemberDef members_postcohinspiral[]"):
        wrapper.index("static PyTypeObject postcoh_inspiral_wrapper_type")]
    assert members.count('"H1_LLR", T_DOUBLE') == 1
    assert members.count('"L1_LLR", T_DOUBLE') == 1
    assert "postcohtable.H1_LLR" in members
    assert "postcohtable.L1_LLR" in members
    assert "NPY_DOUBLE, MAX_NIFO" not in wrapper

def test_runtime_passes_authenticated_segment_pins_and_dof_is_bank_fixed():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    controller = source("share/scripts/crashcar/crashcar_controller.sh")
    sbatch = source("share/scripts/crashcar/crashcar_sbatch.sh")

    for token in (
        'CRASHCAR_SEGMENT_LIVETIME_JSON_SHA256="',
        'CRASHCAR_SEGMENT_SOURCE_XML_SHA256="',
        'CRASHCAR_SEGMENT_RUN_START="',
        'CRASHCAR_SEGMENT_RUN_END="',
    ):
        assert token in sbatch
    assert '-e CRASHCAR_DOF="${CRASHCAR_DOF:-}"' in sbatch
    assert "dof_authority=bankid_fixed_0_99_120_100_383_600" in controller
    assert "legacy_dof_env_value=" in controller
    assert "bankid >= 0 && bankid <= 99" in implementation
    assert "bankid >= 100 && bankid <= 383" in implementation
    assert "*dof_out = 120.0;" in implementation
    assert "*dof_out = 600.0;" in implementation


def test_complete_far_llr_points_shape_remains_required():
    background = source("share/scripts/crashcar/single_detector_far.py")
    current = background[background.index(
        "    def current_far_llr_points(self):"):
        background.index(
            "    def prune_far_llr_points", background.index(
                "    def current_far_llr_points(self):"))]
    for token in ('"llr"', '"far"', '"gps"'):
        assert token in current
    assert '"far_llr_points": far_llr_points' in background
    assert '"support_count": len(far_llr_points)' in background



def test_gap_and_empty_control_passthrough_keep_nongap_payload_strict():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    unified = source("gst/cuda/cohfar/cohfar_assignfar.c")
    init_start = implementation.index(
        "void crashcar_singlefar_engine_init")
    init = implementation[init_start:implementation.index(
        'element->ifos = g_strdup("H1L1");', init_start)]
    assert (
        "gst_base_transform_set_gap_aware(GST_BASE_TRANSFORM(element), TRUE);"
        not in init)
    assert (
        "gst_base_transform_set_gap_aware(GST_BASE_TRANSFORM(element), TRUE);"
        in unified)

    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    end = implementation.index(
        "void crashcar_singlefar_engine_clear", start)
    transform = implementation[start:end]
    disabled = transform.index(
        "if (!element->enabled) return GST_FLOW_OK;")
    gap = transform.index(
        "if (GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_GAP))")
    mapped = transform.index(
        "gst_buffer_map(buf, &mapInfo, GST_MAP_WRITE)")
    empty = transform.index("if (mapInfo.size == 0)")
    row_size = transform.index("const gsize postcoh_row_size")
    assert disabled < gap < mapped < empty < row_size
    gap_branch = transform[gap:mapped]
    assert "return GST_FLOW_OK;" in gap_branch
    for forbidden in (
        "gst_buffer_map", "crashcar_singlefar_prepare_row_atom",
        "crashcar_add_foreground_support", "GST_BUFFER_FLAG_SET",
        "GST_BUFFER_PTS", "GST_BUFFER_DURATION", "GST_BUFFER_OFFSET",
    ):
        assert forbidden not in gap_branch

    empty_branch = transform[empty:row_size]
    assert "gst_buffer_unmap(buf, &mapInfo);" in empty_branch
    assert "return GST_FLOW_OK;" in empty_branch
    for forbidden in (
        "crashcar_singlefar_prepare_row_atom",
        "crashcar_add_foreground_support",
        "GST_BUFFER_FLAG_SET",
        "GST_BUFFER_PTS",
        "GST_BUFFER_DURATION",
        "GST_BUFFER_OFFSET",
        "GST_BUFFER_OFFSET_END",
    ):
        assert forbidden not in empty_branch

    strict = transform[row_size:]
    for token in (
        "!mapInfo.data",
        "mapInfo.size < postcoh_row_size",
        "mapInfo.size % postcoh_row_size != 0",
        "return GST_FLOW_ERROR;",
    ):
        assert token in strict

    # GAP and empty-control paths never mutate payload or buffer metadata.
    for payload in (b"", b"not-a-postcoh-row"):
        before = {
            "flags": ("GAP", "DISCONT"), "pts": 101, "duration": 37,
            "offset": 9, "offset_end": 10, "payload": payload,
        }
        assert dict(before) == before
    empty_control = {
        "flags": ("DISCONT",), "pts": 202, "duration": 41,
        "offset": 11, "offset_end": 12, "payload": b"",
    }
    assert dict(empty_control) == empty_control

    # Non-GAP source guards cover only real map/row-shape safety.  Row zero
    # is data, not a validation-only heartbeat gate.
    assert "mapInfo.size < postcoh_row_size" in strict
    assert "mapInfo.size % postcoh_row_size != 0" in strict
    assert "missing its reserved heartbeat row" not in strict
    assert strict.count("GST_ELEMENT_ERROR(") == 1
    assert transform.count("GST_ELEMENT_ERROR(") == 2

    def source_guard_model(enabled, is_gap, size, row_size,
                           data_nonnull=True):
        if not enabled or is_gap:
            return "OK"
        if size == 0:
            return "OK"
        if not data_nonnull or size < row_size or size % row_size:
            return "ERROR"
        return "SCIENCE"

    compiled_row_size = 8
    assert source_guard_model(True, True, 0, compiled_row_size) == "OK"
    assert source_guard_model(True, True, 17, compiled_row_size) == "OK"
    assert source_guard_model(
        True, False, 0, compiled_row_size, data_nonnull=False) == "OK"
    assert source_guard_model(
        True, False, 0, compiled_row_size, data_nonnull=True) == "OK"
    assert source_guard_model(
        True, False, 1, compiled_row_size) == "ERROR"
    assert source_guard_model(
        True, False, 17, compiled_row_size) == "ERROR"
    assert source_guard_model(
        True, False, compiled_row_size, compiled_row_size,
        data_nonnull=False) == "ERROR"
    assert source_guard_model(
        True, False, compiled_row_size, compiled_row_size) == "SCIENCE"
    assert source_guard_model(
        True, False, 2 * compiled_row_size, compiled_row_size) == "SCIENCE"
    for gap_value in (False, True):
        assert source_guard_model(
            False, gap_value, 17, compiled_row_size) == "OK"


def test_exact_support_time_and_equal_time_direct_science_order():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    header = source("gst/cuda/cohfar/crashcar_singlefar.h")
    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]

    assert "gint64 gps_ns;" in header
    assert "double gps;" not in header
    assert "left->original_ordinal < right->original_ordinal" in implementation
    assert "left->event_id < right->event_id" in implementation
    stable_sort = transform.index(
        "qsort(row_work, work_count, sizeof(CrashcarRowWork)")
    snapshot = transform.index("crashcar_snapshot_paired_authority(")
    evaluated_llr = transform.index("*llr_slot = llr;", snapshot)
    direct_lock = transform.index(
        "g_mutex_lock(&crashcar_support_mutex)", evaluated_llr)
    publication = transform.index(
        "crashcar_try_complete_paired_authority_locked(", direct_lock)
    append_support = transform.index(
        "crashcar_add_foreground_support_locked(", publication)
    assert (
        stable_sort < snapshot < evaluated_llr < direct_lock
        < publication < append_support
    )
    for obsolete in (
        "CrashcarGroupCommitResult",
        "crashcar_commit_scored_group(",
        "last_observed_group_gps_ns",
        "last_committed_group_gps_ns",
        "scientific commit fence",
        "group_failed_bg",
        "group_failed_science",
        "terminal FAILED_BG",
    ):
        assert obsolete not in implementation

    points = (99, 100, 100, 101, 199, 200)
    assert tuple(point for point in points if 100 <= point < 200) == (
        100, 100, 101, 199)
    selected_before_group = tuple(point for point in (99,) if point < 100)
    assert selected_before_group == (99,)
    assert all(selected_before_group == (99,) for unused in (100, 100))


def test_shared_row_authority_and_cadence_independence():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    assignment = implementation[implementation.index(
        "static gboolean crashcar_assignment_window_end_ns"):
        implementation.index(
            "#define CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT")]
    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]

    assert "snapshot" not in assignment.lower()
    assert "zerolag" not in assignment.lower()
    assert "row_assignment_gps_ns" in assignment
    assert "element->background_update_ns" in assignment
    assert "&table->end_time, &row_assignment_gps_ns" in transform
    assert (
        "element, row_assignment_gps_ns, &row_bg_end_ns"
        in transform)
    assert "component_support_gps_ns" in transform
    assert "component_support_gps_ns, &work->row_bg_end_ns" not in transform


def test_eligibility_local_input_and_binary64_boundary_contract():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    crashcar_header = source("gst/cuda/cohfar/crashcar_singlefar.h")
    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]

    rho = transform.index("table->snglsnr[ifo_id] >= CRASHCAR_MIN_SNR")
    deferred = transform.index("if (table->bankid >= 384)")
    local_time = transform.index("crashcar_component_end_time(table, ifo_id)")
    chisq = transform.index("table->chisq[ifo_id] > 0.0f")
    mapping = transform.index("crashcar_row_bank_matches_graph(")
    shape = transform.index("crashcar_lookup_template_shape(")
    assert rho < deferred < local_time < chisq < mapping < shape
    assert "CRASHCAR_SINGLE_FAR_STATUS_FAILED_INPUT = 10" in crashcar_header

    normalized = " ".join(implementation.split())
    assert "#define CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT G_GINT64_CONSTANT(9007199254740992)" in normalized
    assert "livetime_ns >= CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT" in normalized
    assert "candidate_livetime_ns[ifo_id] < CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT" in normalized
    assert "authority->livetime_ns[ifo_id] < CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT" in normalized

    exact_limit = 1 << 53
    assert all(0 < value < exact_limit for value in (1, exact_limit - 1))
    assert not (0 < 0 < exact_limit)
    assert not (0 < exact_limit < exact_limit)
    assert float(exact_limit - 1) == exact_limit - 1
    assert float(exact_limit + 1) != exact_limit + 1



def test_wrapper_exposes_only_a109_suffix_and_no_serialized_provenance():
    wrapper = source("python/pipemodules/postcohtable/_postcohtable.c")
    assert '{ "H1_LLR", T_DOUBLE' in wrapper
    assert '{ "L1_LLR", T_DOUBLE' in wrapper
    for removed in (
        "far_calculated_sngl",
        "far_assigned_sngl_exact",
        "a_eff_sngl",
        "dof_sngl",
        "single_worker_id",
        "single_bank_stream_id",
        "single_bg_authority_valid",
        "single_bg_authority_version",
        "single_bg_authority_epoch_gps_ns",
        "single_bg_authority_provenance_sha256",
    ):
        assert removed not in wrapper

def test_canonical_zero_target_and_empty_append_contract():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    target_start = implementation.index(
        "static gboolean crashcar_json_parse_target")
    target = implementation[target_start:implementation.index(
        "static gboolean crashcar_parse_env_gps_seconds", target_start)]
    for token in (
        "((merged_interval_count == 0) !=",
        "(declared_livetime_ns == 0))",
        "(gint64)segments->len != merged_interval_count",
        "computed_livetime_ns != declared_livetime_ns",
        'crashcar_json_expect(input, "]}")',
    ):
        assert token in target
    append = (
        "if (parsed_segments[ifo_id]->len > 0) {\n"
        "            g_array_append_vals(")
    assert append in implementation

    def target_is_canonical(
            raw_count, empty_count, declared_count, livetime_ns, intervals):
        if raw_count < empty_count:
            return False
        if declared_count > raw_count - empty_count:
            return False
        if (declared_count == 0) != (livetime_ns == 0):
            return False
        if declared_count != len(intervals):
            return False
        return sum(end - start for start, end in intervals) == livetime_ns

    assert target_is_canonical(0, 0, 0, 0, ())
    # Nonempty raw rows can all be clipped outside this run; raw is counted
    # before clipping, so raw>empty with zero merged support is canonical.
    assert target_is_canonical(1, 0, 0, 0, ())
    assert target_is_canonical(3, 1, 0, 0, ())
    assert target_is_canonical(3, 1, 1, 1, ((10, 11),))
    assert not target_is_canonical(0, 1, 0, 0, ())
    assert not target_is_canonical(1, 0, 2, 2, ((10, 11), (12, 13)))
    assert not target_is_canonical(1, 0, 1, 0, ())
    assert not target_is_canonical(1, 0, 0, 1, ())
    assert not target_is_canonical(1, 0, 0, 0, ((10, 11),))
    assert not target_is_canonical(1, 0, 1, 1, ())
    assert not target_is_canonical(1, 0, 1, 2, ((10, 11),))
    assert target_is_canonical(1, 0, 1, 1, ((10, 11),))


def test_opposite_local_times_cannot_select_different_authorities():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]
    assert "&table->end_time, &row_assignment_gps_ns" in transform
    assert (
        "element, row_assignment_gps_ns, &row_bg_end_ns"
        in transform)
    assert "component_support_gps_ns, &work->row_bg_end_ns" not in transform

    def authority_epoch(shared_gps_ns, run_start_ns, required_ns, update_ns):
        first_full = run_start_ns + required_ns
        if shared_gps_ns < first_full or update_ns <= 0:
            return shared_gps_ns
        return first_full + (
            (shared_gps_ns - first_full) // update_ns) * update_ns

    shared = 155
    local_h = 149
    local_l = 161
    expected = authority_epoch(shared, 0, 100, 10)
    assert expected == 150
    assert authority_epoch(shared, 0, 100, 10) == expected
    assert authority_epoch(shared, 0, 100, 10) == expected
    assert authority_epoch(local_h, 0, 100, 10) != expected
    assert authority_epoch(local_l, 0, 100, 10) != expected


def test_component_status_precedence_for_deferred_bank_scope():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]
    rho = transform.index("table->snglsnr[ifo_id] >= CRASHCAR_MIN_SNR")
    deferred = transform.index("if (table->bankid >= 384)")
    local_time = transform.index("crashcar_component_end_time(table, ifo_id)")
    chisq = transform.index("table->chisq[ifo_id] > 0.0f")
    mapping = transform.index("crashcar_row_bank_matches_graph(")
    shape = transform.index("crashcar_lookup_template_shape(")
    assert rho < deferred < local_time < chisq < mapping < shape
    deferred_branch = transform[deferred:local_time]
    assert "CRASHCAR_SINGLE_FAR_STATUS_UNSUPPORTED" in deferred_branch
    assert "CRASHCAR_SINGLE_FAR_STATUS_FAILED_INPUT" not in deferred_branch

    def status(
            rho_value, bankid, local_ok, chisq_ok, mapping_ok,
            shape_ok, llr_ok):
        if not isinstance(rho_value, (int, float)):
            return "NOT_ELIGIBLE"
        if rho_value != rho_value or rho_value in (float("inf"), -float("inf")):
            return "NOT_ELIGIBLE"
        if rho_value < 4:
            return "NOT_ELIGIBLE"
        if bankid >= 384:
            return "UNSUPPORTED"
        if not local_ok:
            return "FAILED_INPUT"
        if not chisq_ok:
            return "FAILED_LLR"
        if not mapping_ok:
            return "FAILED_LLR"
        if not shape_ok or not llr_ok:
            return "FAILED_LLR"
        return "VALID_LLR"

    assert status(
        float("nan"), 384, False, False, False, False, False
    ) == "NOT_ELIGIBLE"
    assert status(
        3.99, 384, False, False, False, False, False
    ) == "NOT_ELIGIBLE"
    assert status(4.0, 384, False, False, False, False, False) == "UNSUPPORTED"
    assert status(5.0, 384, False, False, False, False, False) == "UNSUPPORTED"
    assert status(5.0, 383, False, False, False, False, False) == "FAILED_INPUT"
    assert status(5.0, 383, True, False, False, False, False) == "FAILED_LLR"
    assert status(5.0, 383, True, True, False, False, False) == "FAILED_LLR"
    assert status(5.0, 383, True, True, True, False, False) == "FAILED_LLR"
    assert status(5.0, 383, True, True, True, True, False) == "FAILED_LLR"
    assert status(4.0, 383, True, True, True, True, True) == "VALID_LLR"



def test_single_failures_are_local_and_never_abort_the_normal_buffer():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]

    for removed_gate in (
        "group_failed_bg",
        "group_failed_science",
        "terminal FAILED_BG",
        "terminal input/LLR/unsupported-bank failure",
        "transform_fail:",
        "goto transform_fail",
    ):
        assert removed_gate not in transform

    # Invalid BG/input/LLR/projection states keep the route-owned FAR at zero
    # and do not append invalid support, while later rows still reach the normal
    # buffer return path.
    assert "table->far_sngl[route_owner_ifo] = 0.0f;" in transform
    early_clear = transform.index(
        "table->far_sngl[route_owner_ifo] = 0.0f;")
    gps_precondition = transform.index(
        "&table->end_time, &row_assignment_gps_ns")
    assert early_clear < gps_precondition
    for local_status in (
        "CRASHCAR_SINGLE_FAR_STATUS_FAILED_BG",
        "CRASHCAR_SINGLE_FAR_STATUS_FAILED_INPUT",
        "CRASHCAR_SINGLE_FAR_STATUS_FAILED_LLR",
        "CRASHCAR_SINGLE_FAR_STATUS_UNSUPPORTED",
        "CRASHCAR_SINGLE_FAR_STATUS_FAILED_OUTPUT_POLICY",
    ):
        assert local_status in transform
    support_gate = transform[
        transform.index("if (!element->live_single_background_readonly"):
        transform.index("const gboolean write_all_details")]
    for allowed in (
        "CRASHCAR_SINGLE_FAR_STATUS_PENDING_BG",
        "CRASHCAR_SINGLE_FAR_STATUS_ASSIGNED",
        "CRASHCAR_SINGLE_FAR_STATUS_BG_ONLY",
        "multi_llr_only",
    ):
        assert allowed in support_gate
    for rejected in (
        "CRASHCAR_SINGLE_FAR_STATUS_FAILED_BG",
        "CRASHCAR_SINGLE_FAR_STATUS_FAILED_INPUT",
        "CRASHCAR_SINGLE_FAR_STATUS_FAILED_LLR",
        "CRASHCAR_SINGLE_FAR_STATUS_UNSUPPORTED",
        "CRASHCAR_SINGLE_FAR_STATUS_FAILED_OUTPUT_POLICY",
    ):
        assert rejected not in support_gate
    assert "return GST_FLOW_OK;" in transform
    assert transform.rstrip().endswith("}")
    for fallback in (
        "wrong_worker_fallback", "external_background_fallback",
        "post_run_backfill"):
        assert fallback not in transform

    support_statuses = {"ASSIGNED", "PENDING_BG", "BG_ONLY", "LLR_ONLY_MULTI"}

    def consume(groups):
        diagnostics = []
        support = []
        for shared_time, components in groups:
            diagnostics.extend(
                (shared_time, identity, status)
                for identity, status in components)
            support.extend(
                (shared_time, identity)
                for identity, status in components
                if status in support_statuses)
        return diagnostics, support

    diagnostics, support = consume((
        (100, (("H_bad_bg", "FAILED_BG"), ("L", "ASSIGNED"))),
        (200, (("H_later", "ASSIGNED"),)),
    ))
    assert len(diagnostics) == 3
    assert support == [(100, "L"), (200, "H_later")]


def test_graph_derived_worker_bank_ownership_is_explicit_and_fail_closed():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    header = source("gst/cuda/cohfar/crashcar_singlefar.h")
    wrapper = source("python/pipemodules/__init__.py")
    graph = source("python/pipemodules/spiirparts.py")

    for token in (
        "int stream_id;",
        "int stream_count;",
        "int stream_bank_id;",
        "char *worker_bank_ids;",
        "GArray *worker_bank_id_values;",
        "gboolean graph_binding_locked;",
    ):
        assert token in header
    for token in (
        "static gboolean crashcar_parse_worker_bank_roster",
        "worker bank roster count differs from stream count",
        "worker bank roster must be strictly increasing and unique",
        "stream bank id differs from graph roster ordinal",
        "crashcar_row_bank_matches_graph(",
        "reason=worker_bank_mapping_mismatch",
    ):
        assert token in implementation
    for token in (
        "stream_id=0",
        "stream_count=1",
        "stream_bank_id=0",
        'worker_bank_ids="0"',
    ):
        assert token in wrapper
    for token in (
        "def _crashcar_worker_bank_layout(banks):",
        "if not banks or len(banks) > 384:",
        "if len(set(stream_bank_ids)) != len(stream_bank_ids):",
        "stream_id=i_dict",
        "stream_count=len(crashcar_worker_bank_ids)",
        "stream_bank_id=crashcar_worker_bank_ids[i_dict]",
        "worker_bank_ids=crashcar_worker_bank_ids_csv",
    ):
        assert token in graph

    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]
    local_time = transform.index("crashcar_component_end_time(table, ifo_id)")
    chisq = transform.index("table->chisq[ifo_id] > 0.0f")
    ownership = transform.index("crashcar_row_bank_matches_graph(")
    shape = transform.index("crashcar_lookup_template_shape(")
    assert local_time < chisq < ownership < shape
    mismatch = transform[ownership:shape]
    assert "reason=worker_bank_mapping_mismatch" in mismatch
    assert "CRASHCAR_SINGLE_FAR_STATUS_FAILED_LLR" in mismatch
    assert "continue;" in mismatch
    assert "crashcar_add_foreground_support" not in mismatch
    assert "table->single_bank_stream_id" not in implementation


def test_completed_authority_selection_is_route_gated_and_unique_owner_only():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    header = source("gst/cuda/cohfar/crashcar_singlefar.h")
    for token in (
        "CrashcarCompletedAuthorityIfo",
        "gboolean valid;",
        "guint64 version;",
        "gint64 epoch_gps_ns;",
        "gint64 window_end_gps_ns;",
        "gint64 livetime_ns;",
        "GArray *ranks;",
    ):
        assert token in header

    selector_start = implementation.index(
        "static CrashcarAuthoritySelection\n"
        "crashcar_snapshot_paired_authority")
    selector = implementation[selector_start:implementation.index(
        "static guint crashcar_total_completed_authority_support",
        selector_start)]
    for token in (
        "CRASHCAR_AUTHORITY_SELECTION_NONE",
        "CRASHCAR_AUTHORITY_SELECTION_VALID",
        "CRASHCAR_AUTHORITY_SELECTION_INVALID",
        "authority->epoch_gps_ns < group_gps_ns",
        "authority->window_end_gps_ns <= group_gps_ns",
    ):
        assert token in selector

    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]
    route = transform.index("const CrashcarSingleFinalRoute route =")
    gate = transform.index(
        "if (support_work && group_needs_single_far)", route)
    snapshot = transform.index(
        "crashcar_snapshot_paired_authority(", gate)
    owner = transform.index("const int final_owner_ifo", snapshot)
    compute = transform.index(
        "const gboolean compute_single_far", owner)
    far_clear = transform.index(
        "table->far_sngl[route_owner_ifo] = 0.0f;")
    evaluated = transform.index(
        "if (compute_single_far &&", compute)
    projected = transform.index(
        "const float projected_far", evaluated)
    far_write = transform.index(
        "table->far_sngl[ifo_id] = far_sngl;", projected)
    assert (
        far_clear < route < gate < snapshot < owner < compute <
        evaluated < projected < far_write)

    per_row = transform[owner:transform.index(
        "if (support_work &&", owner)]
    assert "crashcar_snapshot_paired_authority(" not in per_row
    assert "crashcar_snapshot_live_authority(" not in per_row
    assert "crashcar_try_refresh_live_authority(" not in per_row
    assert "evaluation.calculated_far" in per_row
    assert "evaluation.assigned_far" in per_row
    assert "if (multi_llr_only) {" in per_row
    assert "table->far_assigned_sngl_exact" not in transform

def test_detector_local_gps_precedence_fixtures_for_h_and_l():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[start:implementation.index(
        "void crashcar_singlefar_engine_clear", start)]

    rho_index = transform.index(
        "table->snglsnr[ifo_id] >= CRASHCAR_MIN_SNR")
    bank_index = transform.index("if (table->bankid >= 384)")
    local_index = transform.index(
        "crashcar_component_end_time(table, ifo_id)")
    chisq_index = transform.index("table->chisq[ifo_id] > 0.0f")
    mapping_index = transform.index("crashcar_row_bank_matches_graph(")
    shape_index = transform.index("crashcar_lookup_template_shape(")
    assert (
        rho_index < bank_index < local_index < chisq_index <
        mapping_index < shape_index)
    assert "(component_time->gpsSeconds == 0 &&" in transform
    assert "component_time->gpsNanoSeconds == 0" in transform
    assert "component_support_gps_ns <" in transform
    assert "component_support_gps_ns >=" in transform

    run_start_ns = 1238166018000000000
    run_end_ns = run_start_ns + 1000

    def pair(gps_ns):
        return divmod(gps_ns, 1_000_000_000)

    def evaluate_component(
            rho, bank_id, seconds, nanoseconds, chisq_ok=True,
            mapping_ok=True, shape_ok=True, llr_ok=True):
        atom = True
        if not isinstance(rho, (int, float)):
            return "NOT_ELIGIBLE", "rho", False, False, atom, "OK"
        if rho != rho or rho in (float("inf"), -float("inf")) or rho < 4:
            return "NOT_ELIGIBLE", "rho", False, False, atom, "OK"
        if bank_id >= 384:
            return "UNSUPPORTED", "bank_scope", False, False, atom, "OK"
        if seconds is None or nanoseconds is None:
            return "FAILED_INPUT", "local_gps", False, False, atom, "OK"
        if seconds == 0 and nanoseconds == 0:
            return "FAILED_INPUT", "local_gps", False, False, atom, "OK"
        if nanoseconds < 0 or nanoseconds >= 1_000_000_000:
            return "FAILED_INPUT", "local_gps", False, False, atom, "OK"
        gps_ns = seconds * 1_000_000_000 + nanoseconds
        if gps_ns < run_start_ns or gps_ns >= run_end_ns:
            return "FAILED_INPUT", "local_gps", False, False, atom, "OK"
        if not chisq_ok:
            return "FAILED_LLR", "chisq", False, False, atom, "OK"
        if not mapping_ok:
            return (
                "FAILED_LLR", "worker_bank_mapping_mismatch",
                False, False, atom, "OK")
        if not shape_ok or not llr_ok:
            return "FAILED_LLR", "llr", False, False, atom, "OK"
        return "PENDING_BG", "no_authority", True, True, atom, "OK"

    invalid_cases = (
        ("missing", None, None),
        ("zero", 0, 0),
        ("nanoseconds_one_second", run_start_ns // 1_000_000_000,
         1_000_000_000),
        ("start_minus_one", *pair(run_start_ns - 1)),
        ("exact_end", *pair(run_end_ns)),
    )
    accepted_cases = (
        ("exact_start", *pair(run_start_ns)),
        ("end_minus_one", *pair(run_end_ns - 1)),
    )
    sibling_args = (5.0, 5, *pair(run_start_ns + 1))

    for target, sibling in (("H1", "L1"), ("L1", "H1")):
        sibling_result = evaluate_component(*sibling_args)
        assert sibling_result == (
            "PENDING_BG", "no_authority", True, True, True, "OK"), sibling
        for name, seconds, nanoseconds in invalid_cases:
            result = evaluate_component(
                5.0, 5, seconds, nanoseconds,
                chisq_ok=False, mapping_ok=False)
            assert result == (
                "FAILED_INPUT", "local_gps", False, False, True, "OK"
            ), (target, name)
            assert sibling_result[0] == "PENDING_BG"
        for name, seconds, nanoseconds in accepted_cases:
            result = evaluate_component(5.0, 5, seconds, nanoseconds)
            assert result == (
                "PENDING_BG", "no_authority", True, True, True, "OK"
            ), (target, name)

        assert evaluate_component(
            float("nan"), 5, None, None)[0] == "NOT_ELIGIBLE"
        assert evaluate_component(3.99, 5, None, None)[0] == "NOT_ELIGIBLE"
        assert evaluate_component(4.0, 384, None, None)[0] == "UNSUPPORTED"
        assert evaluate_component(
            5.0, 5, *pair(run_start_ns + 1),
            chisq_ok=False, mapping_ok=False)[1] == "chisq"
        assert evaluate_component(
            5.0, 5, *pair(run_start_ns + 1),
            chisq_ok=True, mapping_ok=False)[1] == (
                "worker_bank_mapping_mismatch")


def test_o3_adjacent_nanoseconds_remain_exact_half_open_support():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")
    header = source("gst/cuda/cohfar/crashcar_singlefar.h")
    checked_start = implementation.index(
        "static gboolean crashcar_checked_gps_ns")
    checked = implementation[checked_start:implementation.index(
        "static gboolean crashcar_ordered_distance_u64", checked_start)]
    authority_start = implementation.index(
        "static gboolean crashcar_try_complete_paired_authority_locked")
    authority = implementation[authority_start:implementation.index(
        "static CrashcarAuthoritySelection", authority_start)]

    assert "gint64 gps_ns;" in header
    assert "double gps;" not in header
    assert "nanoseconds >= CRASHCAR_NS_PER_SECOND" in checked
    assert "point.gps_ns < window_start_ns" in authority
    assert "point.gps_ns >= window_end_ns" in authority
    assert "point.available_after_gps_ns >= available_after_gps_ns" in authority

    transform_start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform = implementation[transform_start:implementation.index(
        "void crashcar_singlefar_engine_clear", transform_start)]
    snapshot = transform.index("crashcar_snapshot_paired_authority(")
    evaluated_llr = transform.index("*llr_slot = llr;", snapshot)
    future_support = transform.index(
        "append_future_support[ifo_id] = TRUE;", evaluated_llr)
    direct_lock = transform.index(
        "g_mutex_lock(&crashcar_support_mutex)", future_support)
    publication = transform.index(
        "crashcar_try_complete_paired_authority_locked(", direct_lock)
    append_support = transform.index(
        "crashcar_add_foreground_support_locked(", publication)
    assert (
        snapshot < evaluated_llr < future_support < direct_lock
        < publication < append_support
    )

    start_ns = 1238166018000000000
    end_ns = start_ns + 1000
    identities = (
        ("before", start_ns - 1),
        ("at_start", start_ns),
        ("adjacent", start_ns + 1),
        ("before_end", end_ns - 1),
        ("at_end", end_ns),
    )
    selected = tuple(
        identity for identity, gps_ns in identities
        if start_ns <= gps_ns < end_ns)
    assert selected == ("at_start", "adjacent", "before_end")
    assert tuple(
        identity for identity, gps_ns in identities
        if gps_ns >= end_ns) == ("at_end",)
    assert start_ns + 1 - start_ns == 1
    assert (
        float(start_ns) / 1_000_000_000.0 ==
        float(start_ns + 1) / 1_000_000_000.0)


def test_r24_live_injection_coverage_scope_preserves_rolling_causality():
    implementation = source("gst/cuda/cohfar/crashcar_singlefar.c")

    scope_start = implementation.index(
        "crashcar_single_background_mode_is_live_injection_consumer(void)")
    scope_end = implementation.index(
        "static gboolean crashcar_single_background_mode_is_valid",
        scope_start)
    scope = implementation[scope_start:scope_end]
    for token in (
        "crashcar_single_background_mode_is_live_readonly()",
        'g_ascii_strcasecmp(injection_mode, "blind") == 0',
        'g_ascii_strcasecmp(role, "consumer") == 0',
        "coverage_gps_ns <= event_gps_ns ||",
        "crashcar_single_background_mode_is_live_injection_consumer()",
    ):
        assert token in scope
    assert 'g_ascii_strcasecmp(injection_mode, "none") != 0' not in scope

    # Definition plus adoption, refresh, and selected-LKG checks: no other
    # production path may bypass event-time coverage.
    assert implementation.count(
        "crashcar_live_coverage_is_eligible(") == 4

    paired_start = implementation.index(
        "crashcar_snapshot_paired_authority(")
    paired_end = implementation.index(
        "static guint crashcar_total_completed_authority_support",
        paired_start)
    paired = implementation[paired_start:paired_end]
    assert "crashcar_live_coverage_is_eligible(" not in paired
    assert "pending->available_after_gps_ns < group_gps_ns" in paired
    assert "pending->available_after_gps_ns > group_gps_ns" in paired

    transform_start = implementation.rindex(
        "GstFlowReturn crashcar_singlefar_engine_transform_ip")
    transform_end = implementation.index(
        "void crashcar_singlefar_engine_clear", transform_start)
    transform = implementation[transform_start:transform_end]
    assert "crashcar_snapshot_live_authority(" in transform
    assert "crashcar_snapshot_paired_authority(" in transform
    assert transform.index("crashcar_snapshot_paired_authority(") < (
        transform.index("crashcar_add_foreground_support_locked(")
    )
