#!/usr/bin/env python3
"""State-machine tests for the independent worker-local sidecar oracle."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import sidecar_causal_engine as subject
import sidecar_segment_provenance as segments
from test_sidecar_segment_provenance_v2 import (
    canonical_definers, segment, write_xml,
)


NSEC = 1_000_000_000
SHA = "a" * 64


class FakeShapeSource:
    source_manifest_sha256 = SHA
    source_manifest_bytes = b"fake-shape-source\n"

    def a_eff_and_dof(self, ifo, bankid, tmplt_idx):
        if ifo not in ("H1", "L1"):
            raise KeyError(ifo)
        if not 0 <= int(tmplt_idx) <= 999:
            raise ValueError("template")
        if not 0 <= int(bankid) <= 383:
            raise ValueError("bank")
        return 10.0 + (0.5 if ifo == "L1" else 0.0), (
            120 if int(bankid) <= 99 else 600)


def make_row(
    seq,
    gps,
    event,
    *,
    bank=0,
    template=0,
    ifos="H1L1V1",
    rho_h=4.0,
    rho_l=4.1,
    chisq_h=1.0,
    chisq_l=1.0,
    background="0",
    worker_id=0,
    bank_group=0,
    source_stream_ordinal=0,
    buffer_ordinal=None,
    row_ordinal=0,
    gps_ns=0,
    gps_h=None,
    gps_h_ns=0,
    gps_l=None,
    gps_l_ns=0,
):
    if buffer_ordinal is None:
        buffer_ordinal = seq - 1
    gps_h = gps if gps_h is None else gps_h
    gps_l = gps if gps_l is None else gps_l
    return {
        "stream_seq": str(seq),
        "worker_id": str(worker_id),
        "bank_group": str(bank_group),
        "source_stream_ordinal": str(source_stream_ordinal),
        "buffer_ordinal": str(buffer_ordinal),
        "row_ordinal": str(row_ordinal),
        "ifos": ifos,
        "is_background": str(background),
        "bankid": str(bank),
        "event_id": str(event),
        "tmplt_idx": str(template),
        "end_time": str(gps),
        "end_time_ns": str(gps_ns),
        "end_time_sngl_H1": str(gps_h),
        "end_time_ns_sngl_H1": str(gps_h_ns),
        "end_time_sngl_L1": str(gps_l),
        "end_time_ns_sngl_L1": str(gps_l_ns),
        "snglsnr_H1": str(rho_h),
        "snglsnr_L1": str(rho_l),
        "chisq_H1": str(chisq_h),
        "chisq_L1": str(chisq_l),
    }


class CausalEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.derivative_counter = 0

    def tearDown(self):
        self.temporary.cleanup()

    def _derivative(
        self, *, run_start=0, run_end=2500, l1_end=None,
    ):
        self.derivative_counter += 1
        if l1_end is None:
            l1_end = run_start + 1200
        xml = self.root / f"segments_{self.derivative_counter}.xml"
        rows = [
            segment(
                "segment:segment_id:0",
                "segment_definer:segment_def_id:0",
                run_start, run_end),
            segment(
                "segment:segment_id:1",
                "segment_definer:segment_def_id:1",
                run_start, l1_end),
            segment(
                "segment:segment_id:2",
                "segment_definer:segment_def_id:2",
                run_start, run_end),
        ]
        write_xml(xml, definers=canonical_definers(), segments=rows)
        xml_sha = segments.sha256_file(xml)
        _value, payload = segments.build_derivative(
            xml, run_start * NSEC, run_end * NSEC,
            expected_source_sha256=xml_sha)
        output = self.root / (
            f"segment_derivative_{self.derivative_counter}.json")
        output.write_bytes(payload)
        return output, xml_sha, hashlib.sha256(payload).hexdigest()

    def _engine(
        self,
        *,
        mode=subject.MODE_NO_INJECTION,
        banks=(0,),
        source_stream_ids=(0,),
        run_start=0,
        run_end=2500,
        l1_end=None,
        background=True,
        frozen_path=None,
        frozen_sha=None,
        frozen_namespace=None,
        shape_sha=SHA,
        tail_log10_far=-2.0,
    ):
        if len(banks) != len(source_stream_ids):
            raise ValueError("test helper requires one stream per bank")
        derivative, xml_sha, derivative_sha = self._derivative(
            run_start=run_start, run_end=run_end, l1_end=l1_end)
        frozen = mode == subject.MODE_FROZEN_ASSIGNMENT_ONLY
        return subject.WorkerCausalEngine(
            mode=mode,
            worker_id=0,
            worker_count=2,
            worker_group=0,
            source_stream_bank_map=tuple(zip(source_stream_ids, banks)),
            run_start_ns=run_start * NSEC,
            run_end_ns=run_end * NSEC,
            background_window_ns=None if frozen else 1000 * NSEC,
            update_period_ns=None if frozen else 1000 * NSEC,
            segment_derivative_path=str(derivative),
            expected_segment_xml_sha256=xml_sha,
            expected_segment_derivative_sha256=derivative_sha,
            shape_source=FakeShapeSource(),
            background_path=(
                str(self.root / "single_background.json")
                if background and not frozen else None),
            frozen_background_path=(
                str(frozen_path) if frozen_path else None),
            expected_frozen_background_sha256=frozen_sha,
            expected_frozen_run_namespace_sha256=frozen_namespace,
            run_namespace_sha256=SHA,
            source_manifest_sha256=SHA,
            runtime_manifest_sha256=SHA,
            config_sha256=SHA,
            shape_source_sha256=shape_sha,
            tail_log10_far=tail_log10_far,
        )

    def _seed_four(self):
        return [
            make_row(seq, gps, seq, rho_h=rho, rho_l=rho + 0.1)
            for seq, gps, rho in (
                (1, 100, 4.0), (2, 200, 5.0),
                (3, 300, 6.0), (4, 400, 7.0))
        ]


    def test_configurable_tail_anchor_roundtrip_boundaries_and_invalid_values(self):
        rows = self._seed_four() + [
            make_row(5, 1000, 5, ifos="H1", rho_h=4.0),
        ]
        explicit = self._engine(tail_log10_far=-1.0)
        explicit.process_rows(rows)
        background = json.loads(
            (self.root / "single_background.json").read_text())
        self.assertEqual(background["tail_log10_far"], -1.0)
        detector = background["backgrounds"]["H1"]
        ranks = sorted(float.fromhex(point["llr"])
                       for point in detector["far_llr_points"])
        r_tail = float.fromhex(detector["tail_fit"]["r_tail"])
        slope = float.fromhex(detector["tail_fit"]["slope"])
        direct = subject.numeric.assigned_far(
            ranks, r_tail, detector["livetime"]["seconds"],
            r_tail, slope, -1.0)
        tail = subject.numeric.assigned_far(
            ranks, math.nextafter(r_tail, math.inf),
            detector["livetime"]["seconds"], r_tail, slope, -1.0)
        self.assertEqual(direct[1], "direct")
        self.assertEqual(tail[1], "tail")
        self.assertGreater(direct[0], 0.0)
        self.assertGreater(tail[0], 0.0)

        for invalid in (0.0, 1.0, math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(
                    subject.CausalContractError,
                    "tail_log10_far must be finite"):
                self._engine(tail_log10_far=invalid)

        default_root = self.root / "default"
        default_root.mkdir()
        old_root, self.root = self.root, default_root
        try:
            default = self._engine()
            default.process_rows(rows)
            default_background = json.loads(
                (self.root / "single_background.json").read_text())
            self.assertEqual(default_background["tail_log10_far"], -2.0)
        finally:
            self.root = old_root

    def test_prior_completed_background_only_and_rejected_candidate_retains_it(self):
        engine = self._engine()
        rows = self._seed_four() + [
            make_row(5, 1000, 5, ifos="H1", rho_h=4.0, rho_l=4.1),
            make_row(6, 1001, 6, ifos="H1", rho_h=8.0, rho_l=8.1),
            make_row(7, 2000, 7, ifos="H1", rho_h=5.5, rho_l=5.6),
        ]
        results = engine.process_rows(rows)
        by_event = {}
        for item in results:
            by_event.setdefault(item["event_id"], []).append(item)
        self.assertEqual(engine.lifecycle["multi_owned_llr_only"], 8)

        at_epoch = by_event[5]
        self.assertEqual(len(at_epoch), 1)
        self.assertTrue(all(
            item["status"] == subject.STATUS_ASSIGNED_DIRECT
            for item in at_epoch))
        self.assertTrue(all(item["bg_version"] == 1 for item in at_epoch))
        background = json.loads(
            (self.root / "single_background.json").read_text())
        self.assertEqual(background["accepted_version"], 1)
        encoded = json.dumps(background, sort_keys=True)
        for forbidden in ("identity", "event_id", "stream_seq", "buffer_ordinal"):
            self.assertNotIn(forbidden, encoded)
        for ifo in ("H1", "L1"):
            detector = background["backgrounds"][ifo]
            points = detector["far_llr_points"]
            self.assertEqual(detector["support_count"], len(points))
            self.assertEqual(len(points), 4)
            self.assertTrue(all(
                list(point) == ["gps", "llr", "far"]
                for point in points))
            ranks = [float.fromhex(point["llr"]) for point in points]
            self.assertEqual(ranks, sorted(ranks))
            livetime = segments.gps_to_ns(
                detector["livetime"]["seconds"],
                detector["livetime"]["nanoseconds"], "livetime") / NSEC
            for point, rank in zip(points, ranks):
                expected, _count, _floor = subject.numeric.calculated_far(
                    ranks, rank, livetime)
                self.assertEqual(float.fromhex(point["far"]).hex(), expected.hex())

        above_tail = by_event[6]
        self.assertTrue(all(
            item["status"] == subject.STATUS_ASSIGNED_TAIL
            for item in above_tail))
        retained = by_event[7]
        self.assertTrue(all(item["bg_version"] == 1 for item in retained))
        self.assertEqual(engine.accepted_version, 1)
        self.assertEqual(engine.lifecycle["candidate_rejected"], 1)
        self.assertIn(
            "occupancy not strictly above 20 percent",
            engine.candidate_rejections[0]["reason"])
        summary = engine.finalize()
        self.assertEqual(summary["accepted_version"], 1)
        self.assertEqual(summary["new_authority_publications"], 1)
        self.assertEqual(
            summary["lifecycle"]["pending"]
            + summary["lifecycle"]["assigned"]
            + summary["lifecycle"]["multi_owned_llr_only"],
            summary["lifecycle"]["support_appended"])

    def test_inclusive_threshold_detector_routes_and_k1_fail_closed(self):
        engine = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        below = math.nextafter(4.0, -math.inf)
        results = engine.process_rows([
            make_row(1, 1, 1, ifos="H1", rho_h=4.0),
            make_row(2, 2, 2, ifos="H1", rho_h=below),
            make_row(3, 3, 3, ifos="V1"),
            make_row(4, 4, 4, ifos="H1L1V1", rho_h=4.0, rho_l=4.0),
            make_row(5, 5, 5, ifos="H1V1", rho_h=4.0),
            make_row(6, 6, 6, ifos="L1V1", rho_l=4.0),
        ])
        by_event = {}
        for item in results:
            by_event.setdefault(item["event_id"], []).append(item)
        exact = by_event[1]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["status"], subject.STATUS_BG_ONLY)
        self.assertEqual(exact[0]["route"], "H_SINGLE")
        self.assertEqual(exact[0]["worker_id"], 0)
        self.assertEqual(exact[0]["buffer_ordinal"], 0)
        self.assertEqual(
            by_event[2][0]["status"],
            subject.STATUS_NOT_ELIGIBLE)
        self.assertEqual(
            subject.STATUS_TO_CRASHCAR_CODE[
                by_event[2][0]["status"]], 4)
        self.assertNotIn(3, by_event)
        multi = by_event[4]
        self.assertEqual({item["ifo"] for item in multi}, {"H1", "L1"})
        self.assertTrue(all(item["route"] == "NORMAL_MULTI" for item in multi))
        h_v = by_event[5]
        l_v = by_event[6]
        self.assertEqual(h_v[0]["route"], "H_SINGLE")
        self.assertEqual(l_v[0]["route"], "L_SINGLE")
        bad = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        with self.assertRaisesRegex(subject.CausalContractError, "K1"):
            bad.process_rows([
                make_row(1, 7, 7, ifos="H1K1", rho_h=4.0)])

    def test_normal_multi_route_never_consults_single_far_authority(self):
        engine = self._engine(l1_end=2500)
        # Deliberately non-Authority: any single-FAR query would raise.
        engine.authority = object()
        engine.accepted_version = 7
        results = engine.process_rows([
            make_row(
                1, 10, 101, ifos="H1L1V1",
                rho_h=8.0, rho_l=8.1),
        ])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(
            item["route"] == "NORMAL_MULTI"
            and item["status"] == subject.STATUS_MULTI_OWNED_LLR_ONLY
            and item["llr_valid"] == 1
            and item["calculated_valid"] == 0
            and item["assigned_valid"] == 0
            and item["calculated_far_hex"] == ""
            and item["assigned_far_hex"] == ""
            and item["bg_version"] == 0
            for item in results))
        self.assertEqual(engine.lifecycle["support_candidates"], 2)
        self.assertEqual(engine.lifecycle["support_appended"], 2)

    def test_bank_dof_worker_row_map_and_numeric_states_fail_closed(self):
        engine = self._engine(
            mode=subject.MODE_BG_ONLY,
            banks=(100, 383), source_stream_ids=(0, 1), l1_end=2500)
        results = engine.process_rows([
            make_row(
                1, 1, 1, bank=100, template=999,
                ifos="H1", rho_h=4.0,
                source_stream_ordinal=0, buffer_ordinal=0),
            make_row(
                2, 2, 2, bank=383, template=0,
                ifos="L1", rho_l=4.0,
                source_stream_ordinal=1, buffer_ordinal=0),
            make_row(
                3, 3, 3, bank=384, ifos="H1", rho_h=4.0,
                source_stream_ordinal=0, buffer_ordinal=1),
            make_row(
                4, 4, 4, bank=99, ifos="H1", rho_h=4.0,
                source_stream_ordinal=0, buffer_ordinal=2),
            make_row(
                5, 5, 5, bank=100, ifos="H1", rho_h="nan",
                source_stream_ordinal=0, buffer_ordinal=3),
            make_row(
                6, 6, 6, bank=100, ifos="H1",
                rho_h=4.0, chisq_h=0.0,
                source_stream_ordinal=0, buffer_ordinal=4),
            make_row(
                7, 7, 7, bank=100, ifos="H1",
                rho_h=4.0, background="1",
                source_stream_ordinal=0, buffer_ordinal=5),
            make_row(
                8, 8, 8, bank=384, ifos="H1",
                rho_h=4.0, chisq_h=0.0,
                source_stream_ordinal=0, buffer_ordinal=6),
        ])
        by_event = {item["event_id"]: item for item in results}
        self.assertEqual(by_event[1]["dof"], 600)
        self.assertEqual(by_event[2]["dof"], 600)
        self.assertEqual(
            by_event[3]["reason"], "unsupported_bank_ge_384")
        self.assertEqual(
            by_event[3]["status"], subject.STATUS_UNSUPPORTED)
        self.assertEqual(
            by_event[4]["reason"], "worker_bank_mapping_mismatch")
        self.assertEqual(
            by_event[4]["status"], subject.STATUS_FAILED_LLR)
        self.assertEqual(
            by_event[5]["status"], subject.STATUS_NOT_ELIGIBLE)
        self.assertEqual(
            by_event[5]["reason"], "rho_nonfinite_or_malformed")
        self.assertEqual(
            by_event[6]["reason"], "chisq_not_positive_finite")
        self.assertEqual(
            by_event[6]["status"], subject.STATUS_FAILED_LLR)
        self.assertNotIn(7, by_event)
        self.assertEqual(
            by_event[8]["status"], subject.STATUS_UNSUPPORTED)
        self.assertEqual(
            by_event[8]["reason"], "unsupported_bank_ge_384")
        self.assertEqual(
            engine.lifecycle["historical_background_excluded"], 1)

    def test_derivative_count_livetime_and_run_bound_invariants(self):
        xml = self.root / "clipped_away_segments.xml"
        rows = [
            segment(
                "segment:segment_id:10",
                "segment_definer:segment_def_id:0", 20, 30),
            segment(
                "segment:segment_id:11",
                "segment_definer:segment_def_id:1", 20, 30),
        ]
        write_xml(xml, definers=canonical_definers(), segments=rows)
        xml_sha = segments.sha256_file(xml)
        derivative, payload = segments.build_derivative(
            xml, 0, 10 * NSEC,
            expected_source_sha256=xml_sha)
        self.assertEqual(json.loads(payload), derivative)
        for ifo in ("H1", "L1"):
            target = derivative["targets"][ifo]
            self.assertEqual(target["raw_row_count"], 1)
            self.assertEqual(target["empty_row_count"], 0)
            self.assertEqual(target["merged_interval_count"], 0)
            self.assertEqual(target["livetime_ns"], 0)
            self.assertEqual(
                subject._derivative_intervals(
                    derivative, ifo, 0, 10 * NSEC), ())

        def mutated_target(**updates):
            value = json.loads(payload)
            value["targets"]["H1"].update(updates)
            return value

        with self.assertRaisesRegex(
                subject.CausalContractError,
                "raw row count is below empty"):
            subject._derivative_intervals(
                mutated_target(empty_row_count=2),
                "H1", 0, 10 * NSEC)
        with self.assertRaisesRegex(
                subject.CausalContractError,
                "merged count exceeds nonempty"):
            subject._derivative_intervals(
                mutated_target(merged_interval_count=2),
                "H1", 0, 10 * NSEC)
        with self.assertRaisesRegex(
                subject.CausalContractError,
                "zero merged-count/livetime equivalence"):
            subject._derivative_intervals(
                mutated_target(livetime_ns=1),
                "H1", 0, 10 * NSEC)
        outside = mutated_target(
            merged_interval_count=1,
            livetime_ns=11 * NSEC,
            intervals=[{
                "start": {"seconds": 0, "nanoseconds": 0},
                "end": {"seconds": 11, "nanoseconds": 0},
            }])
        with self.assertRaisesRegex(
                subject.CausalContractError, "outside bound run"):
            subject._derivative_intervals(
                outside, "H1", 0, 10 * NSEC)

    def test_component_status_first_match_precedence_is_cartesian(self):
        engine = self._engine(
            mode=subject.MODE_BG_ONLY, banks=(0,), l1_end=2500)
        below = math.nextafter(4.0, -math.inf)
        rows = [
            make_row(1, 1, 1, bank=0, ifos="H1",
                     rho_h="nan", chisq_h=0.0, gps_h=0),
            make_row(2, 2, 2, bank=384, ifos="H1",
                     rho_h=below, chisq_h=0.0, gps_h=0),
            make_row(3, 3, 3, bank=384, ifos="H1",
                     rho_h=4.0, chisq_h=0.0, gps_h=0),
            make_row(4, 4, 4, bank=0, ifos="H1",
                     rho_h=4.0, chisq_h=0.0, gps_h=0),
            make_row(5, 5, 5, bank=0, template=1000, ifos="H1",
                     rho_h=4.0, chisq_h=0.0),
            make_row(6, 6, 6, bank=99, template=1000, ifos="H1",
                     rho_h=4.0, chisq_h=1.0),
        ]
        by_event = {
            item["event_id"]: item
            for item in engine.process_rows(rows)
        }
        self.assertEqual(
            (by_event[1]["status"], by_event[1]["eligible"]),
            (subject.STATUS_NOT_ELIGIBLE, 0))
        self.assertEqual(
            (by_event[2]["status"], by_event[2]["eligible"]),
            (subject.STATUS_NOT_ELIGIBLE, 0))
        self.assertEqual(
            (by_event[3]["status"], by_event[3]["eligible"]),
            (subject.STATUS_UNSUPPORTED, 1))
        self.assertEqual(
            by_event[3]["reason"], "unsupported_bank_ge_384")
        self.assertEqual(
            (by_event[4]["status"], by_event[4]["eligible"]),
            (subject.STATUS_FAILED_INPUT, 1))
        self.assertIn("local_gps_invalid", by_event[4]["reason"])
        self.assertEqual(
            by_event[5]["status"], subject.STATUS_FAILED_LLR)
        self.assertEqual(
            by_event[5]["reason"], "chisq_not_positive_finite")
        self.assertEqual(
            by_event[6]["status"], subject.STATUS_FAILED_LLR)
        self.assertEqual(
            by_event[6]["reason"], "worker_bank_mapping_mismatch")
        self.assertEqual(
            engine.lifecycle["support_candidates"], 0)
        self.assertEqual(engine.lifecycle["support_appended"], 0)

    def test_worker_group_declared_stream_roster_and_ordinals_are_exact(self):
        engine = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500,
            banks=(0, 1), source_stream_ids=(0, 1))
        results = engine.process_rows([
            make_row(1, 1, 1, ifos="H1",
                     bank=1, source_stream_ordinal=1,
                     buffer_ordinal=0, row_ordinal=0),
            make_row(2, 2, 2, ifos="H1",
                     source_stream_ordinal=0,
                     buffer_ordinal=0, row_ordinal=0),
            make_row(3, 3, 3, ifos="H1",
                     bank=1, source_stream_ordinal=1,
                     buffer_ordinal=1, row_ordinal=0),
            make_row(4, 4, 4, ifos="H1",
                     source_stream_ordinal=0,
                     buffer_ordinal=1, row_ordinal=0),
        ])
        self.assertEqual(
            sorted({item["source_stream_ordinal"] for item in results}),
            [0, 1])
        self.assertEqual(engine.seen_source_streams, {0, 1})

        missing = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500,
            banks=(0, 1), source_stream_ids=(0, 1))
        with self.assertRaisesRegex(
                subject.CausalContractError,
                "does not represent every declared"):
            missing.process_rows([make_row(
                1, 10, 10, bank=0, ifos="H1",
                source_stream_ordinal=0, buffer_ordinal=0)])
        self.assertEqual(missing.support, {"H1": [], "L1": []})
        self.assertEqual(missing.accepted_version, 0)
        self.assertIsNone(missing.authority)
        self.assertIsNone(missing.last_row_time_ns)
        self.assertEqual(missing.lifecycle["support_candidates"], 0)
        self.assertEqual(missing.lifecycle["support_appended"], 0)
        self.assertEqual(missing.lifecycle["candidate_accepted"], 0)
        self.assertEqual(missing.lifecycle["candidate_rejected"], 0)
        self.assertFalse(
            (self.root / "single_background.json").exists())

        for banks, streams, message in (
            ((0, 1), (0, 0), "roster"),
            ((0, 0), (0, 1), "one-to-one"),
            ((0, 1), (1, 0), "roster"),
        ):
            with self.assertRaisesRegex(
                    subject.CausalContractError, message):
                self._engine(
                    mode=subject.MODE_BG_ONLY, l1_end=2500,
                    banks=banks, source_stream_ids=streams)

        wrong_worker = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        with self.assertRaisesRegex(subject.CausalContractError, "worker_id"):
            wrong_worker.process_rows([make_row(
                1, 1, 1, ifos="H1", worker_id=1)])

        wrong_group = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        with self.assertRaisesRegex(
                subject.CausalContractError, "bank_group"):
            wrong_group.process_rows([make_row(
                1, 1, 1, ifos="H1", bank_group=1)])

        unknown_stream = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        with self.assertRaisesRegex(
                subject.CausalContractError, "declared roster"):
            unknown_stream.process_rows([make_row(
                1, 1, 1, ifos="H1", bank=1, source_stream_ordinal=1)])

        bad_first = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500,
            banks=(0, 1), source_stream_ids=(0, 1))
        with self.assertRaisesRegex(
                subject.CausalContractError,
                "first emitted row-bearing buffer"):
            bad_first.process_rows([make_row(
                1, 1, 1, ifos="H1",
                bank=1, source_stream_ordinal=1, buffer_ordinal=1)])

        bad_row = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        with self.assertRaisesRegex(
                subject.CausalContractError, "row ordinal"):
            bad_row.process_rows([
                make_row(1, 1, 1, ifos="H1",
                         buffer_ordinal=0, row_ordinal=0),
                make_row(2, 2, 2, ifos="H1",
                         buffer_ordinal=0, row_ordinal=2),
            ])

    def test_buffer_ordinal_counts_only_emitted_row_bearing_buffers(self):
        engine = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        results = engine.process_rows([
            make_row(1, 1, 1, ifos="H1",
                     buffer_ordinal=0, row_ordinal=0),
            # Any number of no-row GstBuffers may occur between these records;
            # they are absent from the mirror and do not consume an ordinal.
            make_row(2, 2, 2, ifos="H1",
                     buffer_ordinal=1, row_ordinal=0),
        ])
        self.assertEqual([item["event_id"] for item in results], [1, 2])
        self.assertEqual(
            engine.lifecycle["transport_rows_validated"], 2)

    def test_duplicate_identity_and_sequence_gap_stop_the_worker(self):
        def assert_no_science_mutation(engine, transport_rows):
            self.assertEqual(engine.support, {"H1": [], "L1": []})
            self.assertEqual(
                engine.support_identity, {"H1": set(), "L1": set()})
            self.assertEqual(engine.accepted_version, 0)
            self.assertIsNone(engine.authority)
            self.assertIsNone(engine.last_row_time_ns)
            self.assertIsNone(engine.terminal_failed_bg_group_time_ns)
            self.assertEqual(engine.terminal_unprocessed_transport_rows, 0)
            self.assertEqual(
                engine.lifecycle["transport_rows_validated"],
                transport_rows)
            for key, value in engine.lifecycle.items():
                if key != "transport_rows_validated":
                    self.assertEqual(value, 0, key)
            self.assertFalse(
                (self.root / "single_background.json").exists())

        engine = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        row = make_row(1, 1, 1, ifos="H1")
        duplicate = make_row(
            2, 1, 1, ifos="H1",
            buffer_ordinal=0, row_ordinal=1)
        with self.assertRaisesRegex(
                subject.CausalContractError,
                "duplicate scientific identity"):
            engine.process_rows([row, duplicate])
        assert_no_science_mutation(engine, 2)

        def cross_stream_duplicate(reverse):
            duplicate_engine = self._engine(
                mode=subject.MODE_BG_ONLY, l1_end=2500,
                banks=(0, 1), source_stream_ids=(0, 1))
            stream_zero = make_row(
                1 if not reverse else 2, 2, 2,
                bank=0, ifos="H1", source_stream_ordinal=0,
                buffer_ordinal=0)
            # The second stream deliberately replays bank 0 despite the
            # declared 1->1 map.  Full-batch scientific identity preflight
            # must catch the replay before mapping/scoring/lifecycle mutation.
            stream_one = make_row(
                2 if not reverse else 1, 2, 2,
                bank=0, ifos="H1", source_stream_ordinal=1,
                buffer_ordinal=0)
            physical = (
                [stream_one, stream_zero]
                if reverse else [stream_zero, stream_one])
            with self.assertRaisesRegex(
                    subject.CausalContractError,
                    "duplicate scientific identity"):
                duplicate_engine.process_rows(physical)
            assert_no_science_mutation(duplicate_engine, 2)

        cross_stream_duplicate(False)
        cross_stream_duplicate(True)

        other = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        with self.assertRaisesRegex(subject.CausalContractError, "sequence"):
            other.process_rows([make_row(2, 1, 1, ifos="H1")])
        assert_no_science_mutation(other, 0)

    def test_transport_order_is_validated_before_event_time_causal_sort(self):
        engine = self._engine(l1_end=2500)
        rows = [
            make_row(1, 300, 1, rho_h=4.0, rho_l=4.1),
            make_row(2, 100, 2, rho_h=5.0, rho_l=5.1),
            make_row(3, 400, 3, rho_h=6.0, rho_l=6.1),
            make_row(4, 200, 4, rho_h=7.0, rho_l=7.1),
            make_row(5, 999, 5, rho_h=8.0, rho_l=8.1,
                     gps_h=1001, gps_l=999),
            make_row(6, 1000, 6, ifos="H1", rho_h=4.0, rho_l=4.1),
        ]
        results = engine.process_rows(rows)
        event_order = []
        by_event = {}
        for item in results:
            if item["event_id"] not in by_event:
                event_order.append(item["event_id"])
            by_event.setdefault(item["event_id"], []).append(item)
        self.assertEqual(event_order, [2, 4, 1, 3, 5, 6])
        self.assertTrue(all(
            item["status"] == subject.STATUS_MULTI_OWNED_LLR_ONLY
            for item in by_event[5]))
        self.assertTrue(all(
            item["assigned_valid"] == 1
            for item in by_event[6]))
        self.assertEqual(
            engine.lifecycle["transport_rows_validated"], 6)
        background = json.loads(
            (self.root / "single_background.json").read_text())
        h_gps = [
            point["gps"]["seconds"]
            for point in background["backgrounds"]["H1"]["far_llr_points"]
        ]
        l_gps = [
            point["gps"]["seconds"]
            for point in background["backgrounds"]["L1"]["far_llr_points"]
        ]
        self.assertNotIn(1001, h_gps)
        self.assertIn(999, l_gps)
        self.assertTrue(all(value < 1000 for value in h_gps))
        self.assertTrue(all(value < 1000 for value in l_gps))

    def test_component_failures_are_exact_and_valid_siblings_continue(self):
        engine = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        missing_h = make_row(
            1, 1, 1, rho_h=4.0, rho_l=4.0)
        missing_h["end_time_sngl_H1"] = ""
        missing_h["end_time_ns_sngl_H1"] = ""

        zero_l = make_row(
            2, 2, 2, rho_h=4.0, rho_l=4.0)
        zero_l["end_time_sngl_L1"] = "0"
        zero_l["end_time_ns_sngl_L1"] = "0"

        bad_h_chisq = make_row(
            3, 3, 3, rho_h=4.0, rho_l=4.0,
            chisq_h="bad", chisq_l=1.0)

        results = engine.process_rows([
            missing_h, zero_l, bad_h_chisq])
        by_event_ifo = {
            (item["event_id"], item["ifo"]): item
            for item in results
        }
        self.assertEqual(
            by_event_ifo[(1, "H1")]["status"],
            subject.STATUS_FAILED_INPUT)
        self.assertEqual(
            by_event_ifo[(1, "L1")]["status"],
            subject.STATUS_BG_ONLY)
        self.assertEqual(
            by_event_ifo[(2, "L1")]["status"],
            subject.STATUS_FAILED_INPUT)
        self.assertEqual(
            by_event_ifo[(2, "H1")]["status"],
            subject.STATUS_BG_ONLY)
        self.assertEqual(
            by_event_ifo[(3, "H1")]["status"],
            subject.STATUS_FAILED_LLR)
        self.assertEqual(
            by_event_ifo[(3, "L1")]["status"],
            subject.STATUS_BG_ONLY)
        self.assertEqual(
            by_event_ifo[(1, "H1")]["llr_valid"], 0)
        self.assertEqual(
            by_event_ifo[(1, "L1")]["llr_valid"], 1)
        self.assertEqual(engine.lifecycle["failed_input"], 2)
        self.assertEqual(engine.lifecycle["failed_llr"], 1)
        self.assertEqual(engine.lifecycle["support_candidates"], 3)
        self.assertEqual(engine.lifecycle["support_appended"], 3)
        self.assertEqual(subject.STATUS_TO_CRASHCAR_CODE, {
            "ASSIGNED_DIRECT": 1,
            "ASSIGNED_TAIL": 1,
            "PENDING_BG": 2,
            "FAILED_BG": 3,
            "NOT_ELIGIBLE": 4,
            "UNSUPPORTED": 5,
            "FAILED_LLR": 6,
            "BG_ONLY_SUPPORT": 9,
            "FAILED_INPUT": 10,
        })

    def test_detector_local_gps_boundaries_and_precedence_are_exact(self):
        start = 1_380_000_000
        end = start + 2500
        engine = self._engine(
            mode=subject.MODE_BG_ONLY,
            run_start=start, run_end=end, l1_end=end)
        below = math.nextafter(4.0, -math.inf)
        cases = (
            ("absent", None, None, 4.0, 0, 1.0,
             subject.STATUS_FAILED_INPUT, 1, 0,
             "local_gps_invalid", False),
            ("empty", "", "", 4.0, 0, 1.0,
             subject.STATUS_FAILED_INPUT, 1, 0,
             "local_gps_invalid", False),
            ("zero", 0, 0, 4.0, 0, 1.0,
             subject.STATUS_FAILED_INPUT, 1, 0,
             "local_gps_invalid", False),
            ("ns_overflow", start, NSEC, 4.0, 0, 1.0,
             subject.STATUS_FAILED_INPUT, 1, 0,
             "local_gps_invalid", False),
            ("start_minus_1ns", start - 1, NSEC - 1, 4.0, 0, 1.0,
             subject.STATUS_FAILED_INPUT, 1, 0,
             "component_gps_outside_run", False),
            ("exact_start", start, 0, 4.0, 0, 1.0,
             subject.STATUS_BG_ONLY, 1, 1, "", True),
            ("end_minus_1ns", end - 1, NSEC - 1, 4.0, 0, 1.0,
             subject.STATUS_BG_ONLY, 1, 1, "", True),
            ("exact_end", end, 0, 4.0, 0, 1.0,
             subject.STATUS_FAILED_INPUT, 1, 0,
             "component_gps_outside_run", False),
            ("nonfinite_precedes_gps", "", "", float("nan"), 0, 1.0,
             subject.STATUS_NOT_ELIGIBLE, 0, 0,
             "rho_nonfinite_or_malformed", False),
            ("below_precedes_gps", "", "", below, 0, 1.0,
             subject.STATUS_NOT_ELIGIBLE, 0, 0,
             "rho_below_inclusive_threshold", False),
            ("deferred_bank_precedes_gps", "", "", 4.0, 384, 1.0,
             subject.STATUS_UNSUPPORTED, 1, 0,
             "unsupported_bank_ge_384", False),
            ("gps_precedes_bad_chisq", "", "", 4.0, 0, 0.0,
             subject.STATUS_FAILED_INPUT, 1, 0,
             "local_gps_invalid", False),
        )
        rows = []
        expected = {}
        expected_support = 0
        seq = 0
        for target in ("H1", "L1"):
            sibling = "L1" if target == "H1" else "H1"
            for (
                label, local_s, local_ns, target_rho, bank, target_chisq,
                status, eligible, llr_valid, reason, target_support,
            ) in cases:
                seq += 1
                rho_h = target_rho if target == "H1" else 4.25
                rho_l = target_rho if target == "L1" else 4.25
                chisq_h = target_chisq if target == "H1" else 1.0
                chisq_l = target_chisq if target == "L1" else 1.0
                row = make_row(
                    seq, start + 10, seq, bank=bank,
                    rho_h=rho_h, rho_l=rho_l,
                    chisq_h=chisq_h, chisq_l=chisq_l,
                    gps_h=start + 10, gps_l=start + 10)
                if local_s is None:
                    row.pop(f"end_time_sngl_{target}")
                    row.pop(f"end_time_ns_sngl_{target}")
                else:
                    row[f"end_time_sngl_{target}"] = str(local_s)
                    row[f"end_time_ns_sngl_{target}"] = str(local_ns)
                rows.append(row)
                sibling_status = (
                    subject.STATUS_UNSUPPORTED
                    if bank >= 384 else subject.STATUS_BG_ONLY)
                sibling_llr_valid = 0 if bank >= 384 else 1
                sibling_support = bank < 384
                expected[(seq, target)] = (
                    label, status, eligible, llr_valid,
                    reason, target_support)
                expected[(seq, sibling)] = (
                    label, sibling_status, 1, sibling_llr_valid,
                    "unsupported_bank_ge_384" if bank >= 384 else "",
                    sibling_support)
                expected_support += int(target_support)
                expected_support += int(sibling_support)
                if bank >= 384:
                    # bankid is row-wide, so both H/L components in the
                    # deferred row are necessarily UNSUPPORTED.  Add a
                    # separate valid opposite-detector row at the exact same
                    # shared time to prove group-level sibling continuation.
                    seq += 1
                    sibling_row = make_row(
                        seq, start + 10, seq, bank=0,
                        ifos=sibling, rho_h=4.25, rho_l=4.25,
                        chisq_h=1.0, chisq_l=1.0,
                        gps_h=start + 10, gps_l=start + 10)
                    rows.append(sibling_row)
                    expected[(seq, sibling)] = (
                        "deferred_bank_valid_group_sibling",
                        subject.STATUS_BG_ONLY, 1, 1, "", True)
                    expected_support += 1

        results = engine.process_rows(rows)
        self.assertEqual(len(results), len(expected))
        by_key = {
            (item["event_id"], item["ifo"]): item
            for item in results
        }
        self.assertEqual(set(by_key), set(expected))
        for key, (
                label, status, eligible, llr_valid,
                reason, contributes_support,
        ) in expected.items():
            item = by_key[key]
            self.assertEqual(item["status"], status, (key, label))
            self.assertEqual(item["eligible"], eligible, (key, label))
            self.assertEqual(item["llr_valid"], llr_valid, (key, label))
            if reason:
                self.assertIn(reason, item["reason"], (key, label))
            else:
                self.assertEqual(item["reason"], "", (key, label))
            self.assertEqual(bool(item["llr_hex"]), bool(llr_valid))
            self.assertEqual(
                contributes_support,
                status == subject.STATUS_BG_ONLY)

        self.assertEqual(
            engine.lifecycle["support_candidates"], expected_support)
        self.assertEqual(
            engine.lifecycle["support_appended"], expected_support)
        self.assertEqual(
            sum(len(records) for records in engine.support.values()),
            expected_support)
        self.assertEqual(engine.accepted_version, 0)
        self.assertFalse(
            (self.root / "single_background.json").exists())

    def test_equal_shared_time_is_cross_stream_two_phase_and_order_invariant(self):
        def run(rows):
            engine = self._engine(
                mode=subject.MODE_BG_ONLY, l1_end=2500,
                banks=(0, 1), source_stream_ids=(0, 1))
            observations = []
            original = engine._evaluate

            def observing_evaluate(component, selected):
                observations.append((
                    component.event_id,
                    len(engine.support["H1"]),
                    len(engine.support["L1"]),
                ))
                return original(component, selected)

            engine._evaluate = observing_evaluate
            results = engine.process_rows(rows)
            normalized = sorted(
                (item["event_id"], item["ifo"],
                 item["status"], item["llr_hex"])
                for item in results)
            return engine, observations, normalized

        stream_zero_first = [
            make_row(
                1, 100, 11, ifos="H1",
                source_stream_ordinal=0,
                buffer_ordinal=0),
            make_row(
                2, 100, 12, ifos="H1",
                bank=1, source_stream_ordinal=1,
                buffer_ordinal=0),
            make_row(
                3, 100, 13, ifos="H1",
                source_stream_ordinal=0,
                buffer_ordinal=1),
        ]
        stream_one_first = [
            make_row(
                1, 100, 12, ifos="H1",
                bank=1, source_stream_ordinal=1,
                buffer_ordinal=0),
            make_row(
                2, 100, 11, ifos="H1",
                source_stream_ordinal=0,
                buffer_ordinal=0),
            make_row(
                3, 100, 13, ifos="H1",
                source_stream_ordinal=0,
                buffer_ordinal=1),
        ]
        first, first_observed, first_results = run(stream_zero_first)
        second, second_observed, second_results = run(stream_one_first)
        self.assertEqual(first_results, second_results)
        self.assertTrue(all(
            h_count == 0 and l_count == 0
            for _event, h_count, l_count in first_observed))
        self.assertTrue(all(
            h_count == 0 and l_count == 0
            for _event, h_count, l_count in second_observed))
        self.assertEqual(len(first.support["H1"]), 3)
        self.assertEqual(len(second.support["H1"]), 3)
        for ifo in ("H1", "L1"):
            first_support = [
                (record.identity, record.gps_ns, record.llr.hex())
                for record in first.support[ifo]
            ]
            second_support = [
                (record.identity, record.gps_ns, record.llr.hex())
                for record in second.support[ifo]
            ]
            self.assertEqual(first_support, second_support)
        self.assertEqual(first.lifecycle, second.lifecycle)
        self.assertEqual(first.seen_source_streams, {0, 1})
        self.assertEqual(second.seen_source_streams, {0, 1})

    def test_sub_real4_exact_far_remains_assigned_with_empty_projection(self):
        engine = self._engine(l1_end=2500)
        valid = subject.AuthorityIFO(
            ranks=(1.0, 2.0, 3.0),
            livetime_ns=100 * NSEC,
            r_tail=2.0,
            slope=-1.0,
            support_count=3,
        )
        engine.authority = subject.Authority(
            version=7,
            epoch_ns=1,
            native_sha256="b" * 64,
            by_ifo={"H1": valid, "L1": valid},
        )
        engine.accepted_version = 7
        results = engine.process_rows([
            make_row(
                1, 10, 20, ifos="H1",
                rho_h=8.0, chisq_h=1.0),
        ])
        self.assertEqual(len(results), 1)
        result = results[0]
        exact = float.fromhex(result["assigned_far_hex"])
        self.assertTrue(math.isfinite(exact) and exact > 0.0)
        self.assertLess(exact, 2.0 ** -149)
        self.assertEqual(result["assigned_far_real4_hex"], "")
        self.assertEqual(result["assigned_valid"], 1)
        self.assertEqual(result["status"], subject.STATUS_ASSIGNED_TAIL)
        self.assertEqual(engine.lifecycle["failed_bg"], 0)
        self.assertEqual(engine.lifecycle["support_appended"], 1)

    def test_failed_bg_cancels_whole_group_support_and_stops_later_groups(self):
        engine = self._engine(l1_end=2500)
        invalid = subject.AuthorityIFO(
            ranks=(1.0, 2.0, 3.0),
            livetime_ns=1 << 53,
            r_tail=2.0,
            slope=-1.0,
            support_count=3,
        )
        valid = subject.AuthorityIFO(
            ranks=(1.0, 2.0, 3.0),
            livetime_ns=100 * NSEC,
            r_tail=2.0,
            slope=-1.0,
            support_count=3,
        )
        engine.authority = subject.Authority(
            version=7,
            epoch_ns=1,
            native_sha256="b" * 64,
            by_ifo={"H1": invalid, "L1": valid},
        )
        engine.accepted_version = 7
        results = engine.process_rows([
            make_row(
                1, 10, 21, ifos="H1",
                rho_h=8.0, rho_l=8.0),
            make_row(
                2, 10, 22, ifos="L1",
                rho_h=8.0, rho_l=8.0),
            make_row(
                3, 20, 23, ifos="H1",
                rho_h=9.0, rho_l=9.0),
        ])
        by_ifo = {item["ifo"]: item for item in results}
        self.assertEqual(set(by_ifo), {"H1", "L1"})
        self.assertEqual(
            by_ifo["H1"]["status"], subject.STATUS_FAILED_BG)
        self.assertIn(
            by_ifo["L1"]["status"],
            (subject.STATUS_ASSIGNED_DIRECT,
             subject.STATUS_ASSIGNED_TAIL))
        self.assertEqual(
            {item["event_id"] for item in results}, {21, 22})
        self.assertEqual(engine.lifecycle["support_candidates"], 1)
        self.assertEqual(engine.lifecycle["support_appended"], 0)
        self.assertEqual(
            engine.lifecycle["support_cancelled_terminal"], 1)
        self.assertEqual(
            engine.lifecycle["terminal_failed_bg_groups"], 1)
        self.assertEqual(
            engine.terminal_failed_bg_group_time_ns, 10 * NSEC)
        self.assertEqual(
            engine.terminal_unprocessed_transport_rows, 1)
        summary = engine.finalize()
        self.assertEqual(summary["new_authority_publications"], 0)
        self.assertEqual(
            summary["terminal_failed_bg_group_time_ns"], 10 * NSEC)
        self.assertEqual(
            summary["terminal_unprocessed_transport_rows"], 1)

    def test_selected_livetime_and_tail_failures_are_failed_bg(self):
        def run(authority_ifo, event_id):
            engine = self._engine(l1_end=2500)
            engine.authority = subject.Authority(
                version=7,
                epoch_ns=1,
                native_sha256="b" * 64,
                by_ifo={"H1": authority_ifo, "L1": authority_ifo},
            )
            result = engine.process_rows([
                make_row(
                    1, 10, event_id, ifos="H1",
                    rho_h=8.0, chisq_h=1.0),
            ])
            self.assertEqual(len(result), 1)
            self.assertEqual(
                result[0]["status"], subject.STATUS_FAILED_BG)
            self.assertEqual(result[0]["llr_valid"], 1)
            self.assertEqual(result[0]["assigned_valid"], 0)
            self.assertEqual(result[0]["bg_version"], 7)
            self.assertEqual(engine.lifecycle["failed_bg"], 1)
            self.assertEqual(engine.lifecycle["support_candidates"], 0)
            self.assertEqual(engine.lifecycle["support_appended"], 0)
            return result[0]

        invalid_livetime = subject.AuthorityIFO(
            ranks=(1.0, 2.0),
            livetime_ns=1 << 53,
            r_tail=1.0,
            slope=-1.0,
            support_count=2,
        )
        livetime_result = run(invalid_livetime, 21)
        self.assertIn(
            "selected_livetime_or_support_invalid",
            livetime_result["reason"])

        invalid_tail = subject.AuthorityIFO(
            ranks=(1.0, 2.0),
            livetime_ns=100 * NSEC,
            r_tail=-1.0e9,
            slope=0.0,
            support_count=2,
        )
        tail_result = run(invalid_tail, 22)
        self.assertIn(
            "finite negative slope", tail_result["reason"])

    def test_o3_adjacent_nanoseconds_do_not_collapse_window_or_authority(self):
        start = 1_380_000_000
        end = start + 2500
        low_ns = start * NSEC
        epoch_ns = (start + 1000) * NSEC
        self.assertEqual(
            float(low_ns) / NSEC,
            float(low_ns + 1) / NSEC)
        self.assertEqual(
            float(epoch_ns - 1) / NSEC,
            float(epoch_ns) / NSEC)

        engine = self._engine(
            run_start=start, run_end=end, l1_end=end)
        seed_points = (
            ("below_low", low_ns - 1, 1.0),
            ("at_low", low_ns, 2.0),
            ("before_epoch", epoch_ns - 1, 3.0),
            ("at_epoch", epoch_ns, 4.0),
        )
        for ifo in ("H1", "L1"):
            records = [
                subject.SupportRecord((ifo, label), gps_ns, llr)
                for label, gps_ns, llr in seed_points
            ]
            engine.support[ifo] = records
            engine.support_identity[ifo] = {
                record.identity for record in records
            }

        engine._publish_candidate(epoch_ns)
        self.assertEqual(engine.accepted_version, 1)
        self.assertEqual(engine.lifecycle["candidate_accepted"], 1)
        self.assertEqual(engine.lifecycle["support_pruned"], 2)
        self.assertIsNotNone(engine.authority)
        self.assertEqual(engine.authority.epoch_ns, epoch_ns)
        for ifo in ("H1", "L1"):
            self.assertEqual(
                [record.gps_ns for record in engine.support[ifo]],
                [low_ns, epoch_ns - 1, epoch_ns])
            self.assertEqual(len(engine.support_identity[ifo]), 4)
            authority = engine.authority.by_ifo[ifo]
            self.assertEqual(authority.ranks, (2.0, 3.0))
            self.assertEqual(authority.support_count, 2)
            self.assertEqual(authority.livetime_ns, 1000 * NSEC)

        background = json.loads(
            (self.root / "single_background.json").read_text())
        self.assertEqual(
            segments.gps_to_ns(
                background["window_start_gps"]["seconds"],
                background["window_start_gps"]["nanoseconds"],
                "window_start"),
            low_ns)
        self.assertEqual(
            segments.gps_to_ns(
                background["window_end_gps"]["seconds"],
                background["window_end_gps"]["nanoseconds"],
                "window_end"),
            epoch_ns)
        for ifo in ("H1", "L1"):
            points = background["backgrounds"][ifo]["far_llr_points"]
            point_ns = [
                segments.gps_to_ns(
                    point["gps"]["seconds"],
                    point["gps"]["nanoseconds"],
                    f"{ifo}.point")
                for point in points
            ]
            self.assertEqual(point_ns, [low_ns, epoch_ns - 1])
            self.assertNotIn(low_ns - 1, point_ns)
            self.assertNotIn(epoch_ns, point_ns)
            self.assertEqual(
                background["backgrounds"][ifo]["support_count"], 2)

        # Do not let the explicit publication repeat when the rows below are
        # processed.  Their reversed transport order differs by exactly 1 ns;
        # integer-ns science sort and selected authority must remain distinct.
        engine.next_epoch_ns = epoch_ns + 1000 * NSEC
        second = start + 1000
        results = engine.process_rows([
            make_row(
                1, second, 502, ifos="H1", rho_h=8.0,
                gps_ns=2, gps_h=second, gps_h_ns=2),
            make_row(
                2, second, 501, ifos="H1", rho_h=8.0,
                gps_ns=1, gps_h=second, gps_h_ns=1),
        ])
        self.assertEqual(
            [item["event_id"] for item in results], [501, 502])
        self.assertEqual(
            [item["row_event_gps_ns"] for item in results],
            [epoch_ns + 1, epoch_ns + 2])
        self.assertTrue(all(
            item["assigned_valid"] == 1
            and item["bg_version"] == 1
            and item["bg_epoch_seconds"] == second
            and item["bg_epoch_nanoseconds"] == 0
            and item["calculated_livetime_ns"] == 1000 * NSEC
            for item in results))
        self.assertEqual(engine.lifecycle["support_appended"], 2)
        self.assertEqual(engine.lifecycle["support_pruned"], 2)

    def test_linear_empirical_builder_is_bit_exact_to_numeric_oracle(self):
        ranks = (1.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        livetime = 1000.0
        actual_tail, actual_slope, actual_points, far_by_rank = (
            subject._empirical_tail_and_far_by_rank(ranks, livetime))
        expected_tail, expected_slope, expected_points = (
            subject.numeric.build_anchored_tail(ranks, livetime))
        self.assertEqual(actual_tail.hex(), expected_tail.hex())
        self.assertEqual(actual_slope.hex(), expected_slope.hex())
        self.assertEqual(
            [(a.hex(), b.hex()) for a, b in actual_points],
            [(a.hex(), b.hex()) for a, b in expected_points])
        for rank in sorted(set(ranks)):
            expected_far, _count, _floor = subject.numeric.calculated_far(
                ranks, rank, livetime)
            self.assertEqual(far_by_rank[rank].hex(), expected_far.hex())

    def test_shape_and_segment_hashes_are_real_bindings(self):
        with self.assertRaisesRegex(
                subject.CausalContractError, "shape source"):
            self._engine(
                mode=subject.MODE_BG_ONLY,
                l1_end=2500, shape_sha="c" * 64)
        derivative, xml_sha, derivative_sha = self._derivative(
            l1_end=2500)
        with self.assertRaisesRegex(
                subject.CausalContractError, "derivative binding"):
            subject.WorkerCausalEngine(
                mode=subject.MODE_BG_ONLY,
                worker_id=0, worker_count=2, worker_group=0,
                source_stream_bank_map=((0, 0),), run_start_ns=0,
                run_end_ns=2500 * NSEC,
                background_window_ns=1000 * NSEC,
                update_period_ns=1000 * NSEC,
                segment_derivative_path=str(derivative),
                expected_segment_xml_sha256=xml_sha,
                expected_segment_derivative_sha256="d" * 64,
                shape_source=FakeShapeSource(),
                background_path=str(self.root / "other_bg.json"),
                run_namespace_sha256=SHA,
                source_manifest_sha256=SHA,
                runtime_manifest_sha256=SHA,
                config_sha256=SHA,
                shape_source_sha256=SHA)

    def test_frozen_background_loads_exactly_and_never_mutates_support(self):
        live = self._engine(
            mode=subject.MODE_BG_ONLY, l1_end=2500)
        live.process_rows(self._seed_four() + [
            make_row(5, 1000, 5, rho_h=4.0, rho_l=4.1),
        ])
        frozen_path = self.root / "single_background.json"
        frozen_sha = hashlib.sha256(
            frozen_path.read_bytes()).hexdigest()

        frozen = self._engine(
            mode=subject.MODE_FROZEN_ASSIGNMENT_ONLY,
            l1_end=2500,
            background=False,
            frozen_path=frozen_path,
            frozen_sha=frozen_sha,
            frozen_namespace=SHA)
        assigned = frozen.process_rows([
            make_row(1, 10, 99, ifos="H1", rho_h=8.0, rho_l=8.1),
            make_row(2, 11, 100, ifos="L1", rho_h=8.0, rho_l=8.1),
        ])
        self.assertEqual(len(assigned), 2)
        self.assertTrue(all(
            item["source"] == subject.SOURCE_FROZEN
            and item["assigned_valid"] == 1
            for item in assigned))
        summary = frozen.finalize()
        self.assertTrue(summary["frozen_authority_loaded"])
        self.assertEqual(summary["new_authority_publications"], 0)
        self.assertEqual(summary["lifecycle"]["support_appended"], 0)
        self.assertEqual(summary["accepted_version"], 1)

        with self.assertRaisesRegex(
                subject.CausalContractError, "SHA mismatch"):
            self._engine(
                mode=subject.MODE_FROZEN_ASSIGNMENT_ONLY,
                l1_end=2500, background=False,
                frozen_path=frozen_path,
                frozen_sha="e" * 64,
                frozen_namespace=SHA)
        with self.assertRaisesRegex(
                subject.CausalContractError, "namespace mismatch"):
            self._engine(
                mode=subject.MODE_FROZEN_ASSIGNMENT_ONLY,
                l1_end=2500, background=False,
                frozen_path=frozen_path,
                frozen_sha=frozen_sha,
                frozen_namespace="f" * 64)

        original = json.loads(frozen_path.read_text(encoding="ascii"))
        stale = {}
        for key, value in original.items():
            stale[
                "shape_source_sha256"
                if key == "template_shape_map_sha256" else key
            ] = value
        stale_payload = (
            json.dumps(stale, ensure_ascii=True, separators=(",", ":"))
            + "\n"
        ).encode("ascii")
        stale_path = self.root / "stale_schema_background.json"
        stale_path.write_bytes(stale_payload)
        with self.assertRaisesRegex(
                subject.CausalContractError, "root schema/order drift"):
            self._engine(
                mode=subject.MODE_FROZEN_ASSIGNMENT_ONLY,
                l1_end=2500, background=False,
                frozen_path=stale_path,
                frozen_sha=hashlib.sha256(stale_payload).hexdigest(),
                frozen_namespace=SHA)


if __name__ == "__main__":
    unittest.main()
