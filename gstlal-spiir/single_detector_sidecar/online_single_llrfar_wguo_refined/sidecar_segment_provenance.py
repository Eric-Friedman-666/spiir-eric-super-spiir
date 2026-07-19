#!/usr/bin/env python3
"""Strict, independent H1/L1 segment provenance for the sidecar.

This module does not import crashcar code.  It binds the exact LIGO-LW
segment schema and source bytes, keeps GPS arithmetic in signed integer
nanoseconds, and emits one canonical derivative used by every worker.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat as stat_module
import xml.etree.ElementTree as ET

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
NSEC = 1_000_000_000
TARGET_IFOS = ("H1", "L1")
ALL_SEGMENT_IFOS = ("H1", "L1", "V1")
_INT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
_PROCESS_ID_RE = re.compile(r"process:process_id:(?:0|[1-9][0-9]*)\Z")
_DEFINER_ID_RE = re.compile(
    r"segment_definer:segment_def_id:(?:0|[1-9][0-9]*)\Z")
_SEGMENT_ID_RE = re.compile(r"segment:segment_id:(?:0|[1-9][0-9]*)\Z")
_SEGMENT_SUMMARY_ID_RE = re.compile(
    r"segment_summary:segment_sum_id:(?:0|[1-9][0-9]*)\Z")


TABLE_SCHEMAS = {
    "process:table": (
        ("process:comment", "lstring"),
        ("process:cvs_entry_time", "int_4s"),
        ("process:cvs_repository", "lstring"),
        ("process:domain", "lstring"),
        ("process:end_time", "int_4s"),
        ("process:ifos", "lstring"),
        ("process:is_online", "int_4s"),
        ("process:jobid", "int_4s"),
        ("process:node", "lstring"),
        ("process:process_id", "ilwd:char"),
        ("process:program", "lstring"),
        ("process:start_time", "int_4s"),
        ("process:unix_procid", "int_4s"),
        ("process:username", "lstring"),
        ("process:version", "lstring"),
    ),
    "process_params:table": (
        ("process_params:param", "lstring"),
        ("process_params:process_id", "ilwd:char"),
        ("process_params:program", "lstring"),
        ("process_params:type", "lstring"),
        ("process_params:value", "lstring"),
    ),
    "segment_definer:table": (
        ("segment_definer:comment", "lstring"),
        ("segment_definer:ifos", "lstring"),
        ("segment_definer:name", "lstring"),
        ("segment_definer:process_id", "ilwd:char"),
        ("segment_definer:segment_def_id", "ilwd:char"),
        ("segment_definer:version", "int_4s"),
    ),
    "segment_summary:table": (
        ("segment_summary:comment", "lstring"),
        ("segment_summary:end_time", "int_4s"),
        ("segment_summary:end_time_ns", "int_4s"),
        ("segment_summary:process_id", "ilwd:char"),
        ("segment_summary:segment_def_id", "ilwd:char"),
        ("segment_summary:segment_sum_id", "ilwd:char"),
        ("segment_summary:start_time", "int_4s"),
        ("segment_summary:start_time_ns", "int_4s"),
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


class SegmentContractError(ValueError):
    pass


def _snapshot_file_bytes(
        path: str | os.PathLike[str], field: str) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise SegmentContractError(f"{field}: O_NOFOLLOW unavailable")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise SegmentContractError(
            f"{field}: cannot open immutable source") from exc
    try:
        metadata = os.fstat(fd)
        if not stat_module.S_ISREG(metadata.st_mode):
            raise SegmentContractError(
                f"{field}: source is not a regular file")
        chunks = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        payload = b"".join(chunks)
        after = os.fstat(fd)
        stable = (
            metadata.st_dev == after.st_dev
            and metadata.st_ino == after.st_ino
            and metadata.st_size == after.st_size
            and metadata.st_mtime_ns == after.st_mtime_ns
            and metadata.st_ctime_ns == after.st_ctime_ns
        )
        if len(payload) != metadata.st_size or not stable:
            raise SegmentContractError(
                f"{field}: source changed during single-fd snapshot")
        return payload
    finally:
        os.close(fd)


def sha256_file(path: str | os.PathLike[str]) -> str:
    payload = _snapshot_file_bytes(path, "sha256 source")
    return hashlib.sha256(payload).hexdigest()


def strict_sha256(value: object, field: str) -> str:
    text = str(value)
    if not _HEX64_RE.fullmatch(text):
        raise SegmentContractError(f"{field}: expected lowercase SHA-256")
    return text


def strict_int(value: object, field: str) -> int:
    text = str(value)
    if not _INT_RE.fullmatch(text):
        raise SegmentContractError(f"{field}: noncanonical integer {text!r}")
    result = int(text, 10)
    if result < INT64_MIN or result > INT64_MAX:
        raise SegmentContractError(f"{field}: signed INT64 overflow")
    return result


def gps_to_ns(seconds: object, nanoseconds: object, field: str) -> int:
    sec = strict_int(seconds, f"{field}.seconds")
    ns = strict_int(
        nanoseconds if nanoseconds not in (None, "") else "0",
        f"{field}.nanoseconds")
    if ns < 0 or ns >= NSEC:
        raise SegmentContractError(f"{field}: nanoseconds out of range")
    total = sec * NSEC + ns
    if total < INT64_MIN or total > INT64_MAX:
        raise SegmentContractError(f"{field}: normalized GPS overflows INT64 ns")
    return total


def ns_to_gps(value: int) -> dict[str, int]:
    if not isinstance(value, int) or value < INT64_MIN or value > INT64_MAX:
        raise SegmentContractError("normalized GPS is not signed INT64 ns")
    seconds, nanoseconds = divmod(value, NSEC)
    return {"seconds": seconds, "nanoseconds": nanoseconds}


def _decode_xml_snapshot(raw: bytes, field: str) -> bytes:
    if raw.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(raw)
        except OSError as exc:
            raise SegmentContractError(
                f"{field}: invalid gzip XML") from exc
    return raw


def _validated_tables(root: ET.Element) -> dict[str, ET.Element]:
    if root.tag != "LIGO_LW" or root.attrib:
        raise SegmentContractError(
            "root must be unnamespaced LIGO_LW with no attributes")
    tables: dict[str, ET.Element] = {}
    for table in list(root):
        if table.tag != "Table" or set(table.attrib) != {"Name"}:
            raise SegmentContractError(
                "root children must be unnamespaced Table elements "
                "with exactly Name")
        name = table.attrib["Name"]
        if name in tables:
            raise SegmentContractError(f"duplicate table {name!r}")
        if name not in TABLE_SCHEMAS:
            raise SegmentContractError(f"unexpected table {name!r}")
        columns: list[tuple[str, str]] = []
        streams: list[ET.Element] = []
        saw_stream = False
        for child in list(table):
            if child.tag == "Column":
                if saw_stream:
                    raise SegmentContractError(
                        f"{name}: Column appears after Stream")
                if set(child.attrib) != {"Name", "Type"}:
                    raise SegmentContractError(
                        f"{name}: Column attributes drift")
                if list(child) or (child.text or "").strip():
                    raise SegmentContractError(
                        f"{name}: Column must be an empty element")
                columns.append(
                    (child.attrib["Name"], child.attrib["Type"]))
            elif child.tag == "Stream":
                if list(child):
                    raise SegmentContractError(
                        f"{name}: Stream cannot contain child elements")
                saw_stream = True
                streams.append(child)
            else:
                raise SegmentContractError(
                    f"{name}: unexpected child tag {child.tag!r}")
        if len(columns) != len(set(column for column, _kind in columns)):
            raise SegmentContractError(f"{name}: duplicate column name")
        expected = dict(TABLE_SCHEMAS[name])
        actual = dict(columns)
        if actual != expected or len(columns) != len(expected):
            raise SegmentContractError(
                f"{name}: exact column name:type set drift")
        if len(streams) != 1:
            raise SegmentContractError(
                f"{name}: expected exactly one Stream")
        expected_stream_attrs = {
            "Delimiter": ",", "Name": name, "Type": "Local"}
        if streams[0].attrib != expected_stream_attrs:
            raise SegmentContractError(
                f"{name}: exact Stream attributes drift")
        tables[name] = table
    if set(tables) != set(TABLE_SCHEMAS):
        missing = sorted(set(TABLE_SCHEMAS) - set(tables))
        raise SegmentContractError(
            f"exact table set drift; missing={missing!r}")
    return tables


def _table_rows(table: ET.Element) -> list[dict[str, str]]:
    columns = [
        child.attrib["Name"] for child in list(table)
        if child.tag == "Column"
    ]
    stream = next(child for child in list(table) if child.tag == "Stream")
    rows: list[dict[str, str]] = []
    reader = csv.reader(io.StringIO(stream.text or ""), delimiter=",")
    for raw in reader:
        if not raw or all(not value.strip() for value in raw):
            continue
        if len(raw) == len(columns) + 1 and raw[-1].strip() == "":
            raw = raw[:-1]
        if len(raw) != len(columns):
            raise SegmentContractError(
                f"{table.attrib['Name']}: row has {len(raw)} values "
                f"for {len(columns)} columns")
        rows.append({
            name: value.strip()
            for name, value in zip(columns, raw)
        })
    return rows


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if start > end:
            raise SegmentContractError("segment start is after end")
        if start == end:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return [(start, end) for start, end in merged]


def _checked_sum(values: list[int], field: str) -> int:
    result = 0
    for value in values:
        if value < 0:
            raise SegmentContractError(f"{field}: negative addend")
        result += value
        if result > INT64_MAX:
            raise SegmentContractError(f"{field}: INT64 overflow")
    return result


def _require_id(value: str, pattern: re.Pattern[str], field: str) -> str:
    if not pattern.fullmatch(value):
        raise SegmentContractError(f"{field}: malformed ilwd:char ID")
    return value


def build_derivative(
    segment_xml: str | os.PathLike[str],
    run_start_ns: int,
    run_end_ns: int,
    *,
    expected_source_sha256: str,
) -> tuple[dict, bytes]:
    xml_path = Path(segment_xml)
    expected_sha = strict_sha256(
        expected_source_sha256, "expected_source_sha256")
    raw_source = _snapshot_file_bytes(
        xml_path, "segment XML")
    actual_sha = hashlib.sha256(raw_source).hexdigest()
    if actual_sha != expected_sha:
        raise SegmentContractError(
            "segment XML source SHA-256 does not match immutable binding")
    source_size = len(raw_source)
    if source_size <= 0:
        raise SegmentContractError("segment XML is empty")
    if (not isinstance(run_start_ns, int)
            or not isinstance(run_end_ns, int)
            or run_start_ns < INT64_MIN or run_end_ns > INT64_MAX):
        raise SegmentContractError("run boundary outside signed INT64 ns")
    if run_end_ns <= run_start_ns:
        raise SegmentContractError("run interval must be positive")
    try:
        root = ET.fromstring(
            _decode_xml_snapshot(raw_source, "segment XML"))
    except ET.ParseError as exc:
        raise SegmentContractError(f"malformed XML: {xml_path}") from exc
    tables = _validated_tables(root)
    rows_by_table = {
        name: _table_rows(table) for name, table in tables.items()
    }

    process_ids: set[str] = set()
    for row in rows_by_table["process:table"]:
        identity = _require_id(
            row["process:process_id"], _PROCESS_ID_RE,
            "process.process_id")
        if identity in process_ids:
            raise SegmentContractError(f"duplicate process id {identity!r}")
        process_ids.add(identity)
    if not process_ids:
        raise SegmentContractError("process table has no process IDs")

    for row in rows_by_table["process_params:table"]:
        foreign = _require_id(
            row["process_params:process_id"], _PROCESS_ID_RE,
            "process_params.process_id")
        if foreign not in process_ids:
            raise SegmentContractError(
                f"unresolved process_params process_id {foreign!r}")

    definers: dict[str, tuple[str, str]] = {}
    targets: dict[str, str] = {}
    definer_rows = rows_by_table["segment_definer:table"]
    for row in definer_rows:
        identity = _require_id(
            row["segment_definer:segment_def_id"], _DEFINER_ID_RE,
            "segment_definer.segment_def_id")
        if identity in definers:
            raise SegmentContractError(
                f"duplicate segment_definer id {identity!r}")
        process_id = _require_id(
            row["segment_definer:process_id"], _PROCESS_ID_RE,
            "segment_definer.process_id")
        if process_id not in process_ids:
            raise SegmentContractError(
                f"unresolved segment_definer process_id {process_id!r}")
        ifo = row["segment_definer:ifos"]
        name = row["segment_definer:name"]
        if ifo not in ALL_SEGMENT_IFOS or name != "postcohprocessed":
            raise SegmentContractError(
                "segment_definer rows must be exactly H1/L1/V1 "
                "postcohprocessed")
        if ifo in targets:
            raise SegmentContractError(
                f"duplicate postcohprocessed definer for {ifo}")
        definers[identity] = (ifo, name)
        targets[ifo] = identity
    if set(targets) != set(ALL_SEGMENT_IFOS) or len(definer_rows) != 3:
        raise SegmentContractError(
            "need exactly one H1/L1/V1 postcohprocessed definer")

    seen_summary_ids: set[str] = set()
    for row in rows_by_table["segment_summary:table"]:
        summary_id = _require_id(
            row["segment_summary:segment_sum_id"],
            _SEGMENT_SUMMARY_ID_RE,
            "segment_summary.segment_sum_id")
        if summary_id in seen_summary_ids:
            raise SegmentContractError(
                f"duplicate segment_summary id {summary_id!r}")
        seen_summary_ids.add(summary_id)
        process_id = _require_id(
            row["segment_summary:process_id"], _PROCESS_ID_RE,
            "segment_summary.process_id")
        if process_id not in process_ids:
            raise SegmentContractError(
                f"unresolved segment_summary process_id {process_id!r}")
        foreign = _require_id(
            row["segment_summary:segment_def_id"], _DEFINER_ID_RE,
            "segment_summary.segment_def_id")
        if foreign not in definers:
            raise SegmentContractError(
                f"unresolved segment_summary segment_def_id {foreign!r}")

    seen_segment_ids: set[str] = set()
    raw_by_ifo = {ifo: 0 for ifo in TARGET_IFOS}
    empty_by_ifo = {ifo: 0 for ifo in TARGET_IFOS}
    intervals_by_ifo: dict[str, list[tuple[int, int]]] = {
        ifo: [] for ifo in TARGET_IFOS
    }
    for row in rows_by_table["segment:table"]:
        segment_id = _require_id(
            row["segment:segment_id"], _SEGMENT_ID_RE,
            "segment.segment_id")
        if segment_id in seen_segment_ids:
            raise SegmentContractError(
                f"duplicate segment id {segment_id!r}")
        seen_segment_ids.add(segment_id)
        process_id = _require_id(
            row["segment:process_id"], _PROCESS_ID_RE,
            "segment.process_id")
        if process_id not in process_ids:
            raise SegmentContractError(
                f"unresolved segment process_id {process_id!r}")
        foreign = _require_id(
            row["segment:segment_def_id"], _DEFINER_ID_RE,
            "segment.segment_def_id")
        if foreign not in definers:
            raise SegmentContractError(
                f"unresolved segment_def_id {foreign!r}")
        target_ifo = definers[foreign][0]
        start = gps_to_ns(
            row["segment:start_time"], row["segment:start_time_ns"],
            f"{segment_id}.start")
        end = gps_to_ns(
            row["segment:end_time"], row["segment:end_time_ns"],
            f"{segment_id}.end")
        if start > end:
            raise SegmentContractError(
                f"{segment_id}: start is after end")
        if target_ifo not in TARGET_IFOS:
            continue
        raw_by_ifo[target_ifo] += 1
        if start == end:
            empty_by_ifo[target_ifo] += 1
            continue
        clipped_start = max(start, run_start_ns)
        clipped_end = min(end, run_end_ns)
        if clipped_start < clipped_end:
            intervals_by_ifo[target_ifo].append(
                (clipped_start, clipped_end))

    targets_object: dict[str, dict] = {}
    for ifo in TARGET_IFOS:
        merged = _merge(intervals_by_ifo[ifo])
        livetime = _checked_sum(
            [end - start for start, end in merged],
            f"{ifo}.livetime")
        if livetime > run_end_ns - run_start_ns:
            raise SegmentContractError(f"{ifo}: livetime exceeds run span")
        targets_object[ifo] = {
            "segment_def_id": targets[ifo],
            "raw_row_count": raw_by_ifo[ifo],
            "empty_row_count": empty_by_ifo[ifo],
            "merged_interval_count": len(merged),
            "livetime_ns": livetime,
            "intervals": [
                {"start": ns_to_gps(start), "end": ns_to_gps(end)}
                for start, end in merged
            ],
        }
    derivative = {
        "schema_version": 2,
        "source_xml_sha256": actual_sha,
        "source_xml_size": source_size,
        "run_start": ns_to_gps(run_start_ns),
        "run_end": ns_to_gps(run_end_ns),
        "targets": targets_object,
    }
    encoded = (
        json.dumps(derivative, ensure_ascii=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    return derivative, encoded


def canonical_sha256(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def write_atomic_readonly(output: str | os.PathLike[str], payload: bytes) -> None:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = destination.parent
    name = destination.name
    if (not name or name in {".", ".."} or "/" in name
            or destination.exists()):
        raise SegmentContractError(
            "segment derivative output must be a fresh basename")
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    temporary = f".{name}.tmp.{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.fchmod(fd, 0o444)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except Exception:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(parent_fd)


def parse_gps_text(value: str, field: str) -> int:
    if "." not in value:
        return gps_to_ns(value, "0", field)
    seconds, fraction = value.split(".", 1)
    if not fraction.isdigit() or len(fraction) > 9:
        raise SegmentContractError(f"{field}: invalid decimal GPS")
    padded = fraction.ljust(9, "0")
    canonical_nanoseconds = str(int(padded, 10))
    return gps_to_ns(seconds, canonical_nanoseconds, field)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment-xml", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--run-start", required=True)
    parser.add_argument("--run-end", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = parse_gps_text(args.run_start, "run_start")
    end = parse_gps_text(args.run_end, "run_end")
    derivative, payload = build_derivative(
        args.segment_xml, start, end,
        expected_source_sha256=args.expected_source_sha256)
    write_atomic_readonly(args.output, payload)
    print(json.dumps({
        "output": str(Path(args.output)),
        "source_xml_sha256": derivative["source_xml_sha256"],
        "source_xml_size": derivative["source_xml_size"],
        "segment_canonical_sha256": canonical_sha256(payload),
        "H1_livetime_ns": derivative["targets"]["H1"]["livetime_ns"],
        "L1_livetime_ns": derivative["targets"]["L1"]["livetime_ns"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
