#!/usr/bin/env python3
"""Execute the exact FinalSink route methods without importing runtime deps."""

import ast
import copy
import math
import os
from pathlib import Path
from itertools import chain
import re
import subprocess
from types import SimpleNamespace
import unittest


SPIIR_ROOT = Path(__file__).resolve().parents[4]
SOURCE = SPIIR_ROOT / "python" / "pipemodules" / "postcoh_finalsink.py"


class _PipeMacro:
    IFO_MAP = ("H1", "L1", "V1", "K1")

    @classmethod
    def get_ifo_id(cls, ifo):
        return cls.IFO_MAP.index(ifo)


PIPE_MACRO = _PipeMacro()


SCHEMA_SOURCE = (
    SPIIR_ROOT / "python" / "pipemodules" / "postcohtable"
    / "postcoh_table_def.py"
)


def _a107_registry_from_source(source_text):
    tree = ast.parse(source_text, filename=str(SCHEMA_SOURCE))
    table_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "PostcohInspiralTable"
    )
    assignment = next(
        node for node in table_class.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "validcolumns"
            for target in node.targets
        )
    )
    expression = ast.Expression(body=copy.deepcopy(assignment.value))
    ast.fix_missing_locations(expression)
    namespace = {
        "__builtins__": {},
        "chain": chain,
        "dict": dict,
        "list": list,
        "pipe_macro": PIPE_MACRO,
    }
    return tuple(dict(eval(
        compile(expression, str(SCHEMA_SOURCE), "eval"), namespace
    )).items())


_CURRENT_A107 = _a107_registry_from_source(SCHEMA_SOURCE.read_text())
_HEAD_A107 = _a107_registry_from_source(subprocess.check_output(
    [
        "git", "show",
        "HEAD:gstlal-spiir/python/pipemodules/postcohtable/"
        "postcoh_table_def.py",
    ],
    cwd=SPIIR_ROOT.parent,
    text=True,
))
assert len(_CURRENT_A107) == 107
assert _CURRENT_A107 == _HEAD_A107
A107_REGISTRY = _CURRENT_A107
IFO_INDEX = {ifo: index for index, ifo in enumerate(PIPE_MACRO.IFO_MAP)}


def _seed_a107_fields(row):
    arrays = {}
    for index, (name, kind) in enumerate(A107_REGISTRY):
        if kind == "lstring":
            value = "typed_%03d" % index
        elif kind.startswith("int_"):
            value = 1000 + index
        else:
            value = float(index) + 0.125
        matched = False
        for ifo, ifo_index in IFO_INDEX.items():
            suffix = "_" + ifo
            if name.endswith(suffix):
                base = name[:-len(suffix)]
                arrays.setdefault(base, [None] * len(IFO_INDEX))[ifo_index] = value
                matched = True
                break
        if not matched:
            setattr(row, name, value)
    for name, values in arrays.items():
        assert all(value is not None for value in values)
        setattr(row, name, values)


def _typed_a107_snapshot(row):
    output = {}
    for name, kind in A107_REGISTRY:
        value = None
        found = False
        for ifo, ifo_index in IFO_INDEX.items():
            suffix = "_" + ifo
            if name.endswith(suffix):
                value = getattr(row, name[:-len(suffix)])[ifo_index]
                found = True
                break
        if not found:
            value = getattr(row, name)
        output[name] = (kind, type(value).__name__, copy.deepcopy(value))
    assert len(output) == 107
    return output


class _Schema:
    POSTCOH_SCHEMA_MODE_LEGACY_A107 = "legacy_a107"
    POSTCOH_SCHEMA_MODE_CRASHCAR_A109 = "crashcar_a109"


HELPERS = (
    "_crashcar_active_ifos_and_route",
    "_crashcar_protected_ifos_for_route",
    "_crashcar_far_meets_log_threshold",
    "_crashcar_a109_llrs_and_route",
    "_crashcar_final_far_decision",
    "_crashcar_cluster_zero_dispatch",
    "_postcoh_row_for_serialization",
)
METHODS = (
    "cluster_and_process_significant_triggers",
    "__set_far",
    "__write_crashcar_single_coinc_if_needed",
)


def _compile_harness(source_text, *, current):
    tree = ast.parse(source_text, filename=str(SOURCE))
    body = []
    if current:
        route_map = next(
            node for node in tree.body
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name)
                    and target.id == "_CRASHCAR_FINAL_ROUTE_BY_IFOS"
                    for target in node.targets
                )
            )
        )
        body.append(copy.deepcopy(route_map))
        by_name = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        body.extend(copy.deepcopy(by_name[name]) for name in HELPERS)
    source_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FinalSink"
    )
    method_by_name = {
        node.name: node
        for node in source_class.body
        if isinstance(node, ast.FunctionDef)
    }
    selected = [
        copy.deepcopy(method_by_name[name])
        for name in METHODS
        if name in method_by_name
    ]
    expected = METHODS if current else METHODS[:2]
    assert tuple(node.name for node in selected) == expected
    body.append(ast.ClassDef(
        name="FinalSink",
        bases=[],
        keywords=[],
        body=selected,
        decorator_list=[],
    ))
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "math": math,
        "os": os,
        "re": re,
        "pipe_macro": PIPE_MACRO,
        "postcoh_table_def": _Schema,
        "LIGOTimeGPS": lambda seconds=0, nanoseconds=0:
            seconds + nanoseconds / 1_000_000_000.0,
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace


def _load_harnesses():
    current_text = SOURCE.read_text()
    head_text = subprocess.check_output(
        ["git", "show", "HEAD:gstlal-spiir/python/pipemodules/postcoh_finalsink.py"],
        cwd=SPIIR_ROOT.parent,
        text=True,
    )
    return (
        _compile_harness(current_text, current=True),
        _compile_harness(head_text, current=False),
    )


CURRENT, BASELINE = _load_harnesses()


class Row:
    def __init__(self, ifos, event_id=1):
        _seed_a107_fields(self)
        self.ifos = ifos
        self.event_id = event_id
        self.end_time = 1252194000
        self.end_time_ns = event_id
        self.bankid = 5
        self.tmplt_idx = 9
        self.H1_LLR = 0.0
        self.L1_LLR = 0.0
        self.far = 19.0
        self.far_1w = 3.0
        self.far_1d = 4.0
        self.far_2h = 5.0
        self.nevent_1w = 1
        self.nevent_1d = 1
        self.nevent_2h = 1


class Event:
    def __init__(self, row):
        self.postcoh_inspiral = row


class ObservedList(list):
    def __init__(self, trace):
        super().__init__()
        self.trace = trace

    def append(self, value):
        self.trace.append(("append", value.event_id))
        return super().append(value)

    def extend(self, values):
        materialized = list(values)
        self.trace.append(("extend", tuple(value.event_id for value in materialized)))
        return super().extend(materialized)


def snapshot(row):
    return copy.deepcopy(vars(row))


def make_sink(cls, *, crashcar_enabled, cluster_window, trace):
    sink = cls()
    sink.current_timestamp = None
    sink.num_current_buffers = 0
    sink.expected_buffers_per_timestamp = 99
    sink.cur_event_table = []
    sink.negative_latency = 0.0
    sink.is_first_event = False
    sink.cluster_boundary = 1.0 if cluster_window else None
    sink.cluster_window = cluster_window
    sink.postcoh_table = ObservedList(trace)
    sink.crashcar_enabled = crashcar_enabled
    sink.postcoh_schema_mode = (
        _Schema.POSTCOH_SCHEMA_MODE_CRASHCAR_A109
        if crashcar_enabled else _Schema.POSTCOH_SCHEMA_MODE_LEGACY_A107
    )
    sink.enable_feature_best_far = False
    sink.best_far_threshold = 0
    sink.gracedb_far_threshold = 1.0
    sink.gracedb_upload_attempts = 1
    sink.need_online_perform = False
    sink.far_factor = 1.0
    sink.snr_series_logfar_threshold = -4.0
    return sink


def attach_cluster_hooks(sink, row, trace, *, baseline):
    calls = {"try": 0}

    def try_get():
        trace.append(("try_get", calls["try"]))
        calls["try"] += 1
        if calls["try"] == 1:
            sink.candidate = Event(row)
            return True
        sink.cluster_boundary = 100.0
        return False

    sink.try_get_cluster_candidate = try_get
    raw_set_far = getattr(sink, "_FinalSink__set_far")

    def observed_set_far(postcoh, *args, **kwargs):
        trace.append((
            "set_far",
            tuple(kwargs.get("protected_ifos", ())),
        ))
        return raw_set_far(postcoh, *args, **kwargs)

    setattr(sink, "_FinalSink__set_far", observed_set_far)
    setattr(
        sink,
        "_FinalSink__pass_test",
        lambda *args, **kwargs: trace.append(("pass_test", None)) or False,
    )
    setattr(
        sink,
        "_FinalSink__do_gracedb_alert",
        lambda *args, **kwargs: trace.append(("gracedb_alert", None)),
    )
    if baseline:
        setattr(
            sink,
            "_FinalSink__maybe_retain_crashcar_candidate_event",
            lambda *args, **kwargs:
                trace.append(("disabled_validation_noop", None)),
        )
        sink._append_single_trigger_stream_rows = (
            lambda *args, **kwargs:
                trace.append(("disabled_validation_noop", None))
        )


def attach_current_writer(sink, trace):
    setattr(
        sink,
        "_FinalSink__write_candidate_coinc_xml",
        lambda *args, **kwargs:
            trace.append(("single_owned_coinc", args[0].postcoh_inspiral.event_id)),
    )


def normal_trace(trace):
    return [
        entry for entry in trace
        if entry[0] != "disabled_validation_noop"
    ]


class FinalSinkSourceBehaviorTests(unittest.TestCase):
    def setUp(self):
        os.environ["CRASHCAR_ENABLE"] = "0"

    def test_disabled_cluster_zero_matches_git_head_return_effects_and_order(self):
        current_trace = []
        baseline_trace = []
        current_rows = [Row("H1L1", 1), Row("H1L1V1", 2)]
        baseline_rows = copy.deepcopy(current_rows)
        current_before = [snapshot(row) for row in current_rows]
        baseline_before = [snapshot(row) for row in baseline_rows]

        current = make_sink(
            CURRENT["FinalSink"],
            crashcar_enabled=False,
            cluster_window=0,
            trace=current_trace,
        )
        baseline = make_sink(
            BASELINE["FinalSink"],
            crashcar_enabled=False,
            cluster_window=0,
            trace=baseline_trace,
        )
        setattr(
            current,
            "_FinalSink__set_far",
            lambda *args, **kwargs:
                self.fail("disabled cluster-zero must not recompute FAR"),
        )
        setattr(
            current,
            "_FinalSink__write_crashcar_single_coinc_if_needed",
            lambda *args, **kwargs:
                self.fail("disabled cluster-zero must not write Coinc/SNR"),
        )
        baseline._append_single_trigger_stream_rows = (
            lambda *args, **kwargs:
                baseline_trace.append(("disabled_validation_noop", None))
        )

        current_result = current.cluster_and_process_significant_triggers(
            10.0, 0, [Event(row) for row in current_rows]
        )
        baseline_result = baseline.cluster_and_process_significant_triggers(
            10.0, 0, [Event(row) for row in baseline_rows]
        )

        self.assertIsNone(current_result)
        self.assertIsNone(baseline_result)
        self.assertEqual(current_before, [snapshot(row) for row in current_rows])
        self.assertEqual(baseline_before, [snapshot(row) for row in baseline_rows])
        self.assertEqual(
            [row.event_id for row in current.postcoh_table],
            [row.event_id for row in baseline.postcoh_table],
        )
        self.assertEqual(normal_trace(current_trace), normal_trace(baseline_trace))
        self.assertEqual(normal_trace(current_trace), [("extend", (1, 2))])
        self.assertEqual(current.cur_event_table, baseline.cur_event_table)

    def test_disabled_clustered_matches_git_head_normal_calls_and_side_effects(self):
        current_trace = []
        baseline_trace = []
        current_row = Row("H1L1", 3)
        baseline_row = copy.deepcopy(current_row)
        current = make_sink(
            CURRENT["FinalSink"],
            crashcar_enabled=False,
            cluster_window=1.0,
            trace=current_trace,
        )
        baseline = make_sink(
            BASELINE["FinalSink"],
            crashcar_enabled=False,
            cluster_window=1.0,
            trace=baseline_trace,
        )
        attach_cluster_hooks(current, current_row, current_trace, baseline=False)
        attach_cluster_hooks(baseline, baseline_row, baseline_trace, baseline=True)

        current_result = current.cluster_and_process_significant_triggers(
            10.0, 0, []
        )
        baseline_result = baseline.cluster_and_process_significant_triggers(
            10.0, 0, []
        )

        self.assertIsNone(current_result)
        self.assertIsNone(baseline_result)
        self.assertEqual(snapshot(current_row), snapshot(baseline_row))
        self.assertEqual(
            [row.event_id for row in current.postcoh_table],
            [row.event_id for row in baseline.postcoh_table],
        )
        self.assertEqual(normal_trace(current_trace), normal_trace(baseline_trace))
        self.assertEqual(
            normal_trace(current_trace),
            [
                ("try_get", 0),
                ("set_far", ()),
                ("pass_test", None),
                ("append", 3),
                ("try_get", 1),
            ],
        )

    def test_route_protection_helper_is_unique(self):
        helper = CURRENT["_crashcar_protected_ifos_for_route"]
        expected = {
            "H1": ("H1",),
            "H1V1": ("H1",),
            "L1": ("L1",),
            "L1V1": ("L1",),
            "H1L1": (),
            "H1L1V1": (),
            "V1": (),
        }
        for ifos, protected in expected.items():
            self.assertEqual(helper(Row(ifos)), protected)

    def test_cluster_zero_enabled_never_sets_far_and_has_typed_a107_oracle(self):
        routes = (
            ("H1", 0, "far_sngl_H1"),
            ("H1V1", 0, "far_sngl_H1"),
            ("L1", 1, "far_sngl_L1"),
            ("L1V1", 1, "far_sngl_L1"),
            ("H1L1", None, None),
            ("H1L1V1", None, None),
            ("V1", None, None),
        )
        for route_index, (ifos, owner_index, owner_column) in enumerate(routes):
            baseline_trace = []
            trace = []
            baseline_row = Row(ifos, 20 + route_index)
            current_row = copy.deepcopy(baseline_row)

            baseline = make_sink(
                BASELINE["FinalSink"],
                crashcar_enabled=False,
                cluster_window=0,
                trace=baseline_trace,
            )
            baseline._append_single_trigger_stream_rows = (
                lambda *args, **kwargs:
                    baseline_trace.append(("disabled_validation_noop", None))
            )
            baseline.cluster_and_process_significant_triggers(
                10.0, 0, [Event(baseline_row)]
            )
            baseline_a107 = _typed_a107_snapshot(baseline_row)

            if owner_index is not None:
                current_row.far_sngl[owner_index] = 1.0e-6
            if ifos in ("H1", "H1V1", "H1L1", "H1L1V1"):
                current_row.H1_LLR = 8.0
            if ifos in ("L1", "L1V1", "H1L1", "H1L1V1"):
                current_row.L1_LLR = 9.0

            before_a107 = _typed_a107_snapshot(current_row)
            sink = make_sink(
                CURRENT["FinalSink"],
                crashcar_enabled=True,
                cluster_window=0,
                trace=trace,
            )
            setattr(
                sink,
                "_FinalSink__set_far",
                lambda *args, **kwargs:
                    self.fail("enabled cluster-zero must never call __set_far"),
            )
            attach_current_writer(sink, trace)
            sink.cluster_and_process_significant_triggers(
                10.0, 0, [Event(current_row)]
            )
            after_a107 = _typed_a107_snapshot(current_row)

            # FinalSink itself preserves all 107 typed values exactly.
            self.assertEqual(after_a107, before_a107)
            changed_from_disabled_head = {
                name for name in baseline_a107
                if after_a107[name] != baseline_a107[name]
            }
            expected_changed = (
                {owner_column} if owner_column is not None else set()
            )
            self.assertEqual(changed_from_disabled_head, expected_changed)
            self.assertEqual(after_a107["far"], baseline_a107["far"])

            if owner_index is not None:
                opposite = 1 - owner_index
                opposite_column = "far_sngl_" + PIPE_MACRO.IFO_MAP[opposite]
                self.assertEqual(
                    after_a107[opposite_column],
                    baseline_a107[opposite_column],
                )
                self.assertEqual(
                    trace,
                    [
                        ("single_owned_coinc", current_row.event_id),
                        ("extend", (current_row.event_id,)),
                    ],
                )
            else:
                self.assertEqual(
                    trace, [("extend", (current_row.event_id,))]
                )

    def test_clustered_enabled_uses_normal_set_far_except_one_owner(self):
        for ifos, owner_index, protected in (
            ("H1", 0, ("H1",)),
            ("L1V1", 1, ("L1",)),
            ("H1L1", None, ()),
            ("H1L1V1", None, ()),
            ("V1", None, ()),
        ):
            trace = []
            row = Row(ifos, 30 + (owner_index or 0))
            if ifos in ("H1", "H1L1", "H1L1V1"):
                row.H1_LLR = 8.0
            if ifos in ("L1V1", "H1L1", "H1L1V1"):
                row.L1_LLR = 9.0
            if owner_index is not None:
                row.far_sngl[owner_index] = 1.0e-6
            before = snapshot(row)
            sink = make_sink(
                CURRENT["FinalSink"],
                crashcar_enabled=True,
                cluster_window=1.0,
                trace=trace,
            )
            attach_cluster_hooks(sink, row, trace, baseline=False)
            attach_current_writer(sink, trace)
            sink.cluster_and_process_significant_triggers(10.0, 0, [])

            set_calls = [entry for entry in trace if entry[0] == "set_far"]
            self.assertEqual(set_calls, [("set_far", protected)])
            if owner_index is not None:
                self.assertEqual(row.far_sngl[owner_index], 1.0e-6)
                self.assertIn(("single_owned_coinc", row.event_id), trace)
            else:
                self.assertNotEqual(snapshot(row), before)
                self.assertNotIn(("single_owned_coinc", row.event_id), trace)
            self.assertEqual([item.event_id for item in sink.postcoh_table], [row.event_id])


if __name__ == "__main__":
    unittest.main(verbosity=2)
