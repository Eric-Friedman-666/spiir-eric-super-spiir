#!/usr/bin/env python3
"""Build the canonical integer-nanosecond crashcar segment derivative."""

from __future__ import print_function

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET

INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
NS_PER_SECOND = 1000000000
CANONICAL_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)$")
DEFINER_ID = re.compile(r"segment_definer:segment_def_id:(?:0|[1-9][0-9]*)$")
SEGMENT_ID = re.compile(r"segment:segment_id:(?:0|[1-9][0-9]*)$")
TARGET_IFOS = ("H1", "L1")

TABLE_SCHEMAS = {
    "segment_definer:table": (
        ("segment_definer:comment", "lstring"),
        ("segment_definer:ifos", "lstring"),
        ("segment_definer:name", "lstring"),
        ("segment_definer:process_id", "ilwd:char"),
        ("segment_definer:segment_def_id", "ilwd:char"),
        ("segment_definer:version", "int_4s"),
    ),
    "segment:table": (
        ("segment:end_time", "int_4s"),
        ("segment:end_time_ns", "int_4s"),
        ("segment:process_id", "ilwd:char"),
        ("segment:segment_def_id", "ilwd:char"),
        ("segment:segment_id", "ilwd:char"),
        ("segment:start_time", "int_4s"),
        ("segment:start_time_ns", "int_4s"),
    ),
}


def open_source(filename):
    if str(filename).endswith(".gz"):
        return gzip.open(filename, "rb")
    return open(filename, "rb")


def canonical_integer(text, name, minimum=INT64_MIN, maximum=INT64_MAX):
    if not isinstance(text, str):
        raise ValueError("%s is not text" % name)
    if text != text.strip():
        raise ValueError("%s has noncanonical surrounding whitespace" % name)
    value_text = text
    if not CANONICAL_INTEGER.fullmatch(value_text) or value_text == "-0":
        raise ValueError("%s is not a canonical integer" % name)
    value = int(value_text)
    if value < minimum or value > maximum:
        raise ValueError("%s is outside its signed range" % name)
    return value


def checked_gps_ns(seconds_text, nanoseconds_text, name):
    seconds = canonical_integer(
        seconds_text, name + ".seconds", INT32_MIN, INT32_MAX)
    nanoseconds = canonical_integer(
        nanoseconds_text, name + ".nanoseconds", 0, NS_PER_SECOND - 1)
    total = seconds * NS_PER_SECOND + nanoseconds
    if total < INT64_MIN or total > INT64_MAX:
        raise ValueError("%s overflows signed integer nanoseconds" % name)
    return total


def gps_object(total_ns):
    if total_ns < INT64_MIN or total_ns > INT64_MAX:
        raise ValueError("GPS value is outside signed integer nanoseconds")
    seconds, nanoseconds = divmod(total_ns, NS_PER_SECOND)
    return {"seconds": seconds, "nanoseconds": nanoseconds}


def parse_stream_rows(table, columns):
    streams = [child for child in table if child.tag == "Stream"]
    if len(streams) != 1:
        raise ValueError(
            "%s must contain exactly one Stream" % table.attrib.get("Name"))
    stream = streams[0]
    expected_attributes = {
        "Delimiter": ",",
        "Name": table.attrib.get("Name"),
        "Type": "Local",
    }
    if stream.attrib != expected_attributes:
        raise ValueError(
            "%s Stream attributes must be exactly Delimiter,Name,Type=Local" %
            table.attrib.get("Name"))
    if list(stream):
        raise ValueError("%s Stream must contain text only" % table.attrib.get("Name"))

    rows = []
    for physical_row, physical_line in enumerate(
            (stream.text or "").splitlines(), 1):
        line = physical_line.strip()
        if not line:
            continue
        try:
            parsed = next(csv.reader([line], delimiter=",", strict=True))
        except csv.Error as exc:
            raise ValueError(
                "%s Stream row %d is malformed CSV: %s" %
                (table.attrib.get("Name"), physical_row, exc))
        fields = list(parsed)
        # LIGO-LW Local streams legally terminate each row with a delimiter.
        # csv exposes that delimiter as exactly one extra empty token.
        if len(fields) == len(columns) + 1 and fields[-1] == "":
            fields.pop()
        if len(fields) != len(columns):
            raise ValueError(
                "%s Stream row %d has %d values for %d declared columns" %
                (table.attrib.get("Name"), physical_row,
                 len(fields), len(columns)))
        rows.append(dict(zip(columns, fields)))
    return rows


def load_ligolw_tables(filename):
    try:
        with open_source(filename) as handle:
            root = ET.parse(handle).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError("malformed LIGO-LW XML: %s" % exc)

    if root.tag != "LIGO_LW" or root.attrib:
        raise ValueError("root must be exactly an attribute-free LIGO_LW element")

    tables = {}
    for element in root.iter():
        if element.tag != "Table":
            continue
        name = element.attrib.get("Name")
        if name not in TABLE_SCHEMAS:
            continue
        if element.attrib != {"Name": name}:
            raise ValueError("%s Table attributes are noncanonical" % name)
        if name in tables:
            raise ValueError("duplicate %s" % name)
        children = list(element)
        expected_child_tags = (["Column"] * len(TABLE_SCHEMAS[name]) +
                               ["Stream"])
        if [child.tag for child in children] != expected_child_tags:
            raise ValueError(
                "%s children must be exact ordered Columns then Stream" % name)
        column_elements = children[:-1]
        declared_schema = []
        for column in column_elements:
            if set(column.attrib) != {"Name", "Type"}:
                raise ValueError("noncanonical Column attributes in %s" % name)
            declared_schema.append(
                (column.attrib.get("Name"), column.attrib.get("Type")))
        if (len(declared_schema) != len(TABLE_SCHEMAS[name]) or
                len({column_name for column_name, _ in declared_schema}) !=
                len(declared_schema) or
                set(declared_schema) != set(TABLE_SCHEMAS[name])):
            raise ValueError(
                "%s requires the exact unique column name:type set" % name)
        # Declared order is intentionally retained here: a legal LIGO-LW
        # column permutation also permutes each Stream row in the same way.
        columns = [column_name for column_name, _ in declared_schema]
        tables[name] = parse_stream_rows(element, columns)

    for name in TABLE_SCHEMAS:
        if name not in tables:
            raise ValueError("missing %s" % name)
    return tables


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return [(start, end) for start, end in merged]


def build_canonical_payload(filename, run_start_s, run_end_s):
    run_start_ns = run_start_s * NS_PER_SECOND
    run_end_ns = run_end_s * NS_PER_SECOND
    if not (INT64_MIN <= run_start_ns < run_end_ns <= INT64_MAX):
        raise ValueError("run interval is empty or overflows signed nanoseconds")

    tables = load_ligolw_tables(filename)
    definers = {}
    target_ids = {ifo: [] for ifo in TARGET_IFOS}
    for row_index, row in enumerate(tables["segment_definer:table"]):
        definer_id = row["segment_definer:segment_def_id"]
        if not DEFINER_ID.fullmatch(definer_id):
            raise ValueError(
                "noncanonical segment_definer ID at row %d" % row_index)
        if definer_id in definers:
            raise ValueError("duplicate segment_definer ID %s" % definer_id)
        ifos = row["segment_definer:ifos"]
        name = row["segment_definer:name"]
        definers[definer_id] = (ifos, name)
        if name == "postcohprocessed" and ifos in TARGET_IFOS:
            target_ids[ifos].append(definer_id)

    for ifo in TARGET_IFOS:
        if len(target_ids[ifo]) != 1:
            raise ValueError(
                "expected exactly one %s postcohprocessed definer, got %d" %
                (ifo, len(target_ids[ifo])))

    target_by_id = {target_ids[ifo][0]: ifo for ifo in TARGET_IFOS}
    raw_counts = {ifo: 0 for ifo in TARGET_IFOS}
    empty_counts = {ifo: 0 for ifo in TARGET_IFOS}
    intervals = {ifo: [] for ifo in TARGET_IFOS}
    segment_ids = set()

    for row_index, row in enumerate(tables["segment:table"]):
        segment_id = row["segment:segment_id"]
        if not SEGMENT_ID.fullmatch(segment_id):
            raise ValueError("noncanonical segment ID at row %d" % row_index)
        if segment_id in segment_ids:
            raise ValueError("duplicate segment ID %s" % segment_id)
        segment_ids.add(segment_id)

        definer_id = row["segment:segment_def_id"]
        if not DEFINER_ID.fullmatch(definer_id):
            raise ValueError(
                "noncanonical segment FK at row %d" % row_index)
        if definer_id not in definers:
            raise ValueError("unresolved segment_def_id %s" % definer_id)
        ifo = target_by_id.get(definer_id)

        start_ns = checked_gps_ns(
            row["segment:start_time"], row["segment:start_time_ns"],
            "segment[%d].start" % row_index)
        end_ns = checked_gps_ns(
            row["segment:end_time"], row["segment:end_time_ns"],
            "segment[%d].end" % row_index)
        if start_ns > end_ns:
            raise ValueError("segment %s has start after end" % segment_id)
        if ifo is None:
            continue
        raw_counts[ifo] += 1
        if start_ns == end_ns:
            empty_counts[ifo] += 1
            continue
        clipped_start = max(start_ns, run_start_ns)
        clipped_end = min(end_ns, run_end_ns)
        if clipped_start < clipped_end:
            intervals[ifo].append((clipped_start, clipped_end))

    with open(filename, "rb") as source:
        source_sha256 = hashlib.sha256(source.read()).hexdigest()
    targets = {}
    for ifo in TARGET_IFOS:
        merged = merge_intervals(intervals[ifo])
        livetime_ns = sum(end - start for start, end in merged)
        if livetime_ns < 0 or livetime_ns > run_end_ns - run_start_ns:
            raise ValueError("%s merged livetime is out of range" % ifo)
        targets[ifo] = {
            "segment_def_id": target_ids[ifo][0],
            "raw_row_count": raw_counts[ifo],
            "empty_row_count": empty_counts[ifo],
            "merged_interval_count": len(merged),
            "livetime_ns": livetime_ns,
            "intervals": [
                {"start": gps_object(start), "end": gps_object(end)}
                for start, end in merged
            ],
        }

    return {
        "schema_version": 1,
        "source_xml_sha256": source_sha256,
        "run_start": gps_object(run_start_ns),
        "run_end": gps_object(run_end_ns),
        "targets": targets,
    }


def canonical_bytes(payload):
    return (json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False) + "\n").encode("utf-8")


def write_atomic(output, payload):
    output = os.path.abspath(output)
    outdir = os.path.dirname(output)
    os.makedirs(outdir, exist_ok=True)
    data = canonical_bytes(payload)
    fd, temporary = tempfile.mkstemp(prefix=".segment_livetime_", dir=outdir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(data), hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segment_xml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-start", required=True)
    parser.add_argument("--run-end", required=True)
    args = parser.parse_args()
    try:
        run_start = canonical_integer(args.run_start, "run_start")
        run_end = canonical_integer(args.run_end, "run_end")
        payload = build_canonical_payload(args.segment_xml, run_start, run_end)
        byte_count, digest = write_atomic(args.output, payload)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))
    print("wrote canonical segment derivative bytes=%d sha256=%s path=%s" %
          (byte_count, digest, args.output))


if __name__ == "__main__":
    main()
