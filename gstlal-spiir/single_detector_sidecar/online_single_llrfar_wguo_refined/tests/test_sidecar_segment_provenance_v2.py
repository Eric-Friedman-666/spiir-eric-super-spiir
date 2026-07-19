#!/usr/bin/env python3
"""Executable contract tests for the independent segment derivative."""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sidecar_segment_provenance as subject


FORMAL_XML = Path(
    "/fred/oz016/wguo/odds_ratio/O3a/chunk20/"
    "multi_det-BNS-LVK_inj/000/"
    "H1L1V1_SEGMENTS_1252187822_86400.xml.gz")
FORMAL_XML_SHA = (
    "52adca35f0c579d3e55e17b4f07561b3d0da9467c1c3138a7696832775e60b78"
)
PROCESS_ID = "process:process_id:13"


def _stream_text(columns, rows):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns] + [""])
    return "\n" + output.getvalue()


def process_row():
    return dict((name, "") for name, _kind in
                subject.TABLE_SCHEMAS["process:table"]) | {
        "process:end_time": "1450168926",
        "process:is_online": "0",
        "process:jobid": "0",
        "process:node": "test-node",
        "process:process_id": PROCESS_ID,
        "process:program": "gstlal_inspiral_postcohspiir_online",
        "process:start_time": "1450129145",
        "process:unix_procid": "1",
        "process:username": "test",
    }


def canonical_definers():
    rows = []
    for index, ifo in enumerate(("H1", "L1", "V1")):
        rows.append({
            "segment_definer:comment": "SPIIR postcoh snapshot",
            "segment_definer:ifos": ifo,
            "segment_definer:name": "postcohprocessed",
            "segment_definer:process_id": PROCESS_ID,
            "segment_definer:segment_def_id":
                f"segment_definer:segment_def_id:{index}",
            "segment_definer:version": "",
        })
    return rows


def segment(identity, foreign, start, end, start_ns=0, end_ns=0,
            process_id=PROCESS_ID):
    return {
        "segment:end_time": str(end),
        "segment:end_time_ns": str(end_ns),
        "segment:process_id": process_id,
        "segment:segment_def_id": foreign,
        "segment:segment_id": identity,
        "segment:start_time": str(start),
        "segment:start_time_ns": str(start_ns),
    }


def write_xml(
    path,
    *,
    definers,
    segments,
    definer_columns=None,
    segment_columns=None,
    schema_override=None,
    stream_attrs_override=None,
    root_tag="LIGO_LW",
    root_attrs=None,
    extra_table=None,
):
    schemas = {
        name: list(columns)
        for name, columns in subject.TABLE_SCHEMAS.items()
    }
    if definer_columns is not None:
        expected = dict(subject.TABLE_SCHEMAS["segment_definer:table"])
        schemas["segment_definer:table"] = [
            (name, expected[name]) for name in definer_columns
        ]
    if segment_columns is not None:
        expected = dict(subject.TABLE_SCHEMAS["segment:table"])
        schemas["segment:table"] = [
            (name, expected[name]) for name in segment_columns
        ]
    for table_name, column_name, kind in schema_override or ():
        schema = schemas[table_name]
        for index, (name, _old_kind) in enumerate(schema):
            if name == column_name:
                schema[index] = (name, kind)
                break
        else:
            schema.append((column_name, kind))

    rows = {
        "process:table": [process_row()],
        "process_params:table": [],
        "segment_definer:table": list(definers),
        "segment_summary:table": [],
        "segment:table": list(segments),
    }
    root = ET.Element(root_tag, root_attrs or {})
    for table_name, schema in schemas.items():
        table = ET.SubElement(root, "Table", {"Name": table_name})
        columns = [name for name, _kind in schema]
        for name, kind in schema:
            ET.SubElement(
                table, "Column", {"Name": name, "Type": kind})
        attrs = {"Delimiter": ",", "Name": table_name, "Type": "Local"}
        attrs.update((stream_attrs_override or {}).get(table_name, {}))
        stream = ET.SubElement(table, "Stream", attrs)
        stream.text = _stream_text(columns, rows[table_name])
    if extra_table:
        ET.SubElement(root, "Table", {"Name": extra_table})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build(path, start, end, expected=None):
    return subject.build_derivative(
        path, start, end,
        expected_source_sha256=expected or subject.sha256_file(path))


class SegmentDerivativeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _path(self, name="segments.xml"):
        return self.root / name

    def test_formal_xml_exact_binding_and_known_livetime(self):
        self.assertTrue(FORMAL_XML.is_file())
        derivative, payload = subject.build_derivative(
            FORMAL_XML,
            1252187822 * subject.NSEC,
            1252274222 * subject.NSEC,
            expected_source_sha256=FORMAL_XML_SHA)
        self.assertEqual(derivative["schema_version"], 2)
        self.assertEqual(derivative["source_xml_sha256"], FORMAL_XML_SHA)
        self.assertEqual(
            derivative["source_xml_size"], FORMAL_XML.stat().st_size)
        self.assertEqual(
            derivative["targets"]["H1"]["livetime_ns"],
            31_126_000_000_000)
        self.assertEqual(
            derivative["targets"]["L1"]["livetime_ns"],
            77_843_000_000_000)
        self.assertEqual(
            subject.canonical_sha256(payload),
            "155af66c9a9e246dc7695dfebb07f041c156faf8f66121a825d64c533490aed7")

    def test_schema_aware_reordered_columns_merge_clip_and_ignore_v(self):
        path = self._path()
        rows = [
            segment(
                "segment:segment_id:0",
                "segment_definer:segment_def_id:0", 0, 5),
            segment(
                "segment:segment_id:1",
                "segment_definer:segment_def_id:0", 5, 10),
            segment(
                "segment:segment_id:2",
                "segment_definer:segment_def_id:1", 0, 6),
            segment(
                "segment:segment_id:3",
                "segment_definer:segment_def_id:1", 5, 10),
            segment(
                "segment:segment_id:4",
                "segment_definer:segment_def_id:1", 7, 7),
            segment(
                "segment:segment_id:5",
                "segment_definer:segment_def_id:2", 0, 10),
        ]
        write_xml(
            path, definers=canonical_definers(), segments=rows,
            definer_columns=tuple(reversed([
                name for name, _kind in
                subject.TABLE_SCHEMAS["segment_definer:table"]])),
            segment_columns=tuple(reversed([
                name for name, _kind in
                subject.TABLE_SCHEMAS["segment:table"]])))
        derivative, payload = build(
            path, 2 * subject.NSEC, 9 * subject.NSEC)
        self.assertEqual(list(derivative["targets"]), ["H1", "L1"])
        for ifo in ("H1", "L1"):
            target = derivative["targets"][ifo]
            self.assertEqual(target["merged_interval_count"], 1)
            self.assertEqual(target["livetime_ns"], 7 * subject.NSEC)
            self.assertEqual(target["intervals"], [{
                "start": {"seconds": 2, "nanoseconds": 0},
                "end": {"seconds": 9, "nanoseconds": 0},
            }])
        self.assertEqual(derivative["targets"]["L1"]["empty_row_count"], 1)
        self.assertEqual(
            payload,
            (json.dumps(derivative, ensure_ascii=True, separators=(",", ":"))
             + "\n").encode("ascii"))

    def test_gzip_and_integer_nanosecond_boundaries(self):
        plain = self._path()
        rows = [
            segment(
                "segment:segment_id:0",
                "segment_definer:segment_def_id:0", 10, 11, 2, 3),
            segment(
                "segment:segment_id:1",
                "segment_definer:segment_def_id:1", 10, 11, 2, 3),
        ]
        write_xml(plain, definers=canonical_definers(), segments=rows)
        compressed = self._path("segments.xml.gz")
        compressed.write_bytes(gzip.compress(plain.read_bytes()))
        derivative, _payload = build(
            compressed, 10 * subject.NSEC + 2,
            11 * subject.NSEC + 3)
        for ifo in ("H1", "L1"):
            self.assertEqual(
                derivative["targets"][ifo]["livetime_ns"],
                subject.NSEC + 1)

    def test_exact_gps_parser_and_bounds(self):
        self.assertEqual(subject.parse_gps_text("1", "gps"), subject.NSEC)
        self.assertEqual(
            subject.parse_gps_text("1.000000001", "gps"),
            subject.NSEC + 1)
        for bad in ("1.", "1.0000000000", "1e3", "+1", " 1"):
            with self.assertRaises(subject.SegmentContractError):
                subject.parse_gps_text(bad, "gps")
        with self.assertRaises(subject.SegmentContractError):
            subject.gps_to_ns("1", "1000000000", "gps")

    def test_source_hash_root_table_stream_and_column_drift_fail_closed(self):
        path = self._path()
        write_xml(path, definers=canonical_definers(), segments=[])
        with self.assertRaisesRegex(
                subject.SegmentContractError, "source SHA"):
            build(path, 0, 10 * subject.NSEC, "0" * 64)

        write_xml(
            path, definers=canonical_definers(), segments=[],
            root_tag="{urn:drift}LIGO_LW")
        with self.assertRaisesRegex(subject.SegmentContractError, "root"):
            build(path, 0, 10 * subject.NSEC)

        write_xml(
            path, definers=canonical_definers(), segments=[],
            stream_attrs_override={
                "segment:table": {"Type": "Remote"}})
        with self.assertRaisesRegex(
                subject.SegmentContractError, "Stream attributes"):
            build(path, 0, 10 * subject.NSEC)

        write_xml(
            path, definers=canonical_definers(), segments=[],
            schema_override=[
                ("segment:table", "segment:start_time", "real_8")])
        with self.assertRaisesRegex(
                subject.SegmentContractError, "column name:type"):
            build(path, 0, 10 * subject.NSEC)

        write_xml(
            path, definers=canonical_definers(), segments=[],
            extra_table="unexpected:table")
        with self.assertRaisesRegex(
                subject.SegmentContractError, "unexpected table"):
            build(path, 0, 10 * subject.NSEC)

    def test_id_foreign_key_duplicate_and_target_fail_closed(self):
        path = self._path()
        only_h = canonical_definers()[:1]
        write_xml(path, definers=only_h, segments=[])
        with self.assertRaisesRegex(
                subject.SegmentContractError, "exactly one H1/L1/V1"):
            build(path, 0, 10 * subject.NSEC)

        duplicates = canonical_definers() + [{
            **canonical_definers()[0],
            "segment_definer:segment_def_id":
                "segment_definer:segment_def_id:9",
        }]
        write_xml(path, definers=duplicates, segments=[])
        with self.assertRaisesRegex(subject.SegmentContractError, "duplicate"):
            build(path, 0, 10 * subject.NSEC)

        unresolved = [segment(
            "segment:segment_id:0",
            "segment_definer:segment_def_id:99", 0, 1)]
        write_xml(path, definers=canonical_definers(), segments=unresolved)
        with self.assertRaisesRegex(subject.SegmentContractError, "unresolved"):
            build(path, 0, 10 * subject.NSEC)

        malformed = [segment(
            "segment:0",
            "segment_definer:segment_def_id:0", 0, 1)]
        write_xml(path, definers=canonical_definers(), segments=malformed)
        with self.assertRaisesRegex(subject.SegmentContractError, "malformed"):
            build(path, 0, 10 * subject.NSEC)

        duplicate = [
            segment(
                "segment:segment_id:0",
                "segment_definer:segment_def_id:0", 0, 1),
            segment(
                "segment:segment_id:0",
                "segment_definer:segment_def_id:1", 0, 1),
        ]
        write_xml(path, definers=canonical_definers(), segments=duplicate)
        with self.assertRaisesRegex(subject.SegmentContractError, "duplicate"):
            build(path, 0, 10 * subject.NSEC)

    def test_column_stream_nested_children_fail_closed(self):
        path = self._path()
        for tag in ("Column", "Stream"):
            write_xml(path, definers=canonical_definers(), segments=[])
            tree = ET.parse(path)
            target = next(
                element for element in tree.getroot().iter()
                if element.tag == tag)
            ET.SubElement(target, "Nested")
            tree.write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(
                    subject.SegmentContractError,
                    "cannot contain child|must be (?:an )?empty"):
                build(path, 0, 10 * subject.NSEC)

    def test_summary_id_duplicate_and_foreign_key_fail_closed(self):
        path = self._path()

        def summary(identity, foreign="segment_definer:segment_def_id:0"):
            return {
                "segment_summary:comment": "test",
                "segment_summary:end_time": "10",
                "segment_summary:end_time_ns": "0",
                "segment_summary:process_id": PROCESS_ID,
                "segment_summary:segment_def_id": foreign,
                "segment_summary:segment_sum_id": identity,
                "segment_summary:start_time": "0",
                "segment_summary:start_time_ns": "0",
            }

        def install(rows):
            write_xml(path, definers=canonical_definers(), segments=[])
            tree = ET.parse(path)
            table = next(
                element for element in tree.getroot()
                if element.tag == "Table"
                and element.attrib["Name"] == "segment_summary:table")
            columns = [
                element.attrib["Name"] for element in table
                if element.tag == "Column"
            ]
            stream = next(
                element for element in table if element.tag == "Stream")
            stream.text = _stream_text(columns, rows)
            tree.write(path, encoding="utf-8", xml_declaration=True)

        install([summary("segment_summary:bad:0")])
        with self.assertRaisesRegex(
                subject.SegmentContractError, "malformed"):
            build(path, 0, 10 * subject.NSEC)

        identity = "segment_summary:segment_sum_id:0"
        install([summary(identity), summary(identity)])
        with self.assertRaisesRegex(
                subject.SegmentContractError, "duplicate segment_summary"):
            build(path, 0, 10 * subject.NSEC)

        install([summary(
            identity, "segment_definer:segment_def_id:99")])
        with self.assertRaisesRegex(
                subject.SegmentContractError,
                "unresolved segment_summary segment_def_id"):
            build(path, 0, 10 * subject.NSEC)

    def test_symlink_source_is_rejected_by_single_fd_open(self):
        real = self._path("real_segments.xml")
        write_xml(real, definers=canonical_definers(), segments=[])
        link = self._path("segments_link.xml")
        link.symlink_to(real)
        expected = subject.sha256_file(real)
        with self.assertRaisesRegex(
                subject.SegmentContractError,
                "cannot open immutable source"):
            subject.build_derivative(
                link, 0, 10 * subject.NSEC,
                expected_source_sha256=expected)


if __name__ == "__main__":
    unittest.main()
