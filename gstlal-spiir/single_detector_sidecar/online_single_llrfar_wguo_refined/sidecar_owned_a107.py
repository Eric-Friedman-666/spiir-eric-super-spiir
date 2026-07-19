#!/usr/bin/env python3
"""Strict reader for sidecar-owned completed normal-SPIIR A107 snapshots."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import math
import os
from pathlib import Path
import re
import stat
import struct

LEGACY_COLUMN_COUNT = 107
UINT_RE = re.compile(r'(?:0|[1-9][0-9]*)\Z')
SHA_RE = re.compile(r'[0-9a-f]{64}\Z')
SNAPSHOT_RE = re.compile(
    r'(?P<worker>[0-9]{3})_zerolag_(?P<start>[0-9]+)_'
    r'(?P<duration>[0-9]+)\.xml(?:\.gz)?\Z')
REQUIRED_COLUMNS = frozenset((
    'bankid', 'event_id', 'ifos', 'is_background',
    'end_time', 'end_time_ns',
    'end_time_sngl_H1', 'end_time_ns_sngl_H1',
    'end_time_sngl_L1', 'end_time_ns_sngl_L1',
    'snglsnr_H1', 'snglsnr_L1', 'chisq_H1', 'chisq_L1',
    'tmplt_idx',
))


class OwnedA107Error(RuntimeError):
    pass


def _strict_uint(value, field, maximum=(1 << 63) - 1):
    text = str(value)
    if not UINT_RE.fullmatch(text):
        raise OwnedA107Error(f'{field}: noncanonical unsigned integer')
    result = int(text, 10)
    if result > maximum:
        raise OwnedA107Error(f'{field}: out of range')
    return result


def _snapshot(path, label, maximum=536_870_912):
    path = Path(path)
    nofollow = getattr(os, 'O_NOFOLLOW', None)
    if nofollow is None:
        raise OwnedA107Error('O_NOFOLLOW is unavailable')
    try:
        fd = os.open(os.fspath(path), os.O_RDONLY | nofollow |
                     getattr(os, 'O_CLOEXEC', 0))
    except OSError as exc:
        raise OwnedA107Error(f'cannot open {label}: {path}') from exc
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_size <= 0
                or before.st_size > maximum):
            raise OwnedA107Error(
                f'{label} must be a bounded nonempty regular file')
        blocks = []
        remaining = before.st_size
        while remaining:
            block = os.read(fd, min(1 << 20, remaining))
            if not block:
                raise OwnedA107Error(f'{label} changed during read')
            blocks.append(block)
            remaining -= len(block)
        if os.read(fd, 1):
            raise OwnedA107Error(f'{label} grew during read')
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size,
                before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns):
            raise OwnedA107Error(f'{label} changed during read')
        payload = b''.join(blocks)
        return payload, hashlib.sha256(payload).hexdigest()
    finally:
        os.close(fd)


def _directory(path, label):
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise OwnedA107Error(f'missing {label}: {candidate}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not resolved.is_dir():
        raise OwnedA107Error(f'{label} must be a non-symlink directory')
    return resolved


def _canonical_real4(value, field):
    try:
        number = float(str(value))
        rounded = struct.unpack('>f', struct.pack('>f', number))[0]
    except (TypeError, ValueError, OverflowError, struct.error) as exc:
        raise OwnedA107Error(f'{field}: malformed REAL4') from exc
    if math.isnan(rounded):
        return 'nan'
    if math.isinf(rounded):
        return 'inf' if rounded > 0 else '-inf'
    return format(rounded, '.9g')


def _column_name(line):
    match = re.fullmatch(
        r'<Column Name="(?:postcoh:)?([^"]+)" Type="[^"]+"/>', line)
    return match.group(1) if match else None


def _parse_snapshot(payload, path):
    try:
        decoded = gzip.decompress(payload) if path.name.endswith('.gz') else payload
        text = decoded.decode('utf-8')
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise OwnedA107Error(f'invalid A107 snapshot: {path}') from exc
    if '\x00' in text:
        raise OwnedA107Error(f'NUL in A107 snapshot: {path}')
    columns = []
    rows = []
    in_table = False
    in_stream = False
    saw_close = False
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not in_table:
            if line == '<Table Name="postcoh:table">':
                in_table = True
            continue
        if not in_stream:
            column = _column_name(line)
            if column is not None:
                columns.append(column)
                continue
            if line.startswith('<Stream Name="postcoh:table"'):
                in_stream = True
                continue
            if line.startswith('</Table'):
                break
            continue
        if line.startswith('</Stream'):
            saw_close = True
            break
        if not line:
            continue
        try:
            values = next(csv.reader([line]))
        except csv.Error as exc:
            raise OwnedA107Error(
                f'CSV parse failure in {path}:{line_number}') from exc
        if len(values) == len(columns) + 1 and values[-1] == '':
            values.pop()
        if len(values) != len(columns):
            raise OwnedA107Error(
                f'A107 row width drift in {path}:{line_number}')
        rows.append(dict(zip(columns, values)))
    if not saw_close:
        raise OwnedA107Error(f'A107 postcoh stream is incomplete: {path}')
    schema = tuple(columns)
    if len(schema) != LEGACY_COLUMN_COUNT:
        raise OwnedA107Error(
            f'A107 schema width is {len(schema)}, expected 107')
    if len(set(schema)) != len(schema):
        raise OwnedA107Error('A107 schema contains duplicate columns')
    missing = sorted(REQUIRED_COLUMNS - set(schema))
    if missing:
        raise OwnedA107Error(
            'A107 schema missing required columns: ' + ','.join(missing))
    return schema, rows


def _event_ns(row):
    seconds = _strict_uint(row['end_time'], 'end_time')
    nanoseconds = _strict_uint(
        row['end_time_ns'], 'end_time_ns', 999_999_999)
    return seconds * 1_000_000_000 + nanoseconds


def _parse_roster(worker_root, roster):
    payload, roster_sha = _snapshot(roster, 'A107 roster', 16_777_216)
    try:
        text = payload.decode('ascii')
    except UnicodeDecodeError as exc:
        raise OwnedA107Error('A107 roster must be ASCII') from exc
    reader = csv.reader(io.StringIO(text), delimiter='\t')
    rows = list(reader)
    if not rows or rows[0] != ['relative_path', 'bytes', 'sha256']:
        raise OwnedA107Error('A107 roster header drift')
    records = []
    seen = set()
    for index, row in enumerate(rows[1:], start=2):
        if len(row) != 3:
            raise OwnedA107Error(f'A107 roster row {index} width drift')
        relative, size_text, digest = row
        relative_path = Path(relative)
        if (not relative or relative_path.is_absolute()
                or '..' in relative_path.parts
                or len(relative_path.parts) != 2):
            raise OwnedA107Error('A107 roster path is not a safe relative child')
        if relative in seen:
            raise OwnedA107Error('duplicate A107 roster path')
        seen.add(relative)
        size = _strict_uint(size_text, 'roster.bytes')
        if size <= 0 or not SHA_RE.fullmatch(digest):
            raise OwnedA107Error('A107 roster size/SHA invalid')
        path = worker_root / relative_path
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
            resolved.relative_to(worker_root)
        except (OSError, ValueError) as exc:
            raise OwnedA107Error('A107 roster path escapes worker root') from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OwnedA107Error('A107 roster path is not a regular non-symlink file')
        match = SNAPSHOT_RE.fullmatch(path.name)
        if match is None or int(match.group('worker')) != int(worker_root.name[-3:]):
            raise OwnedA107Error('A107 roster filename/worker mismatch')
        actual_payload, actual_sha = _snapshot(path, 'A107 snapshot')
        if len(actual_payload) != size or actual_sha != digest:
            raise OwnedA107Error('A107 roster size/SHA mismatch')
        records.append((relative, path, actual_payload, actual_sha))
    if not records:
        raise OwnedA107Error('A107 roster contains no snapshots')
    if [record[0] for record in records] != sorted(record[0] for record in records):
        raise OwnedA107Error('A107 roster paths are not sorted')
    output = worker_root / f'{worker_root.name[-3:]}'
    actual = set()
    for path in output.glob(f'{worker_root.name[-3:]}_zerolag_*.xml*'):
        actual.add(str(path.relative_to(worker_root)))
    if actual != seen:
        raise OwnedA107Error('A107 roster differs from own worker output')
    return records, roster_sha


def load_owned_worker(*, run_root, worker_id, worker_count, worker_group,
                      source_stream_bank_map, start_bank, banks_per_worker):
    root = _directory(run_root, 'sidecar run root')
    if not (0 <= int(worker_id) < int(worker_count) <= 4096):
        raise OwnedA107Error('worker identity out of range')
    if int(worker_group) != int(worker_id):
        raise OwnedA107Error('worker group must equal worker id')
    pairs = tuple((int(stream), int(bank))
                  for stream, bank in source_stream_bank_map)
    streams = tuple(stream for stream, _bank in pairs)
    banks = tuple(bank for _stream, bank in pairs)
    if (not pairs or streams != tuple(range(len(pairs)))
            or len(set(banks)) != len(banks)):
        raise OwnedA107Error('source stream map must be contiguous and bijective')
    expected_banks = tuple(range(
        int(start_bank) + int(banks_per_worker) * int(worker_id),
        int(start_bank) + int(banks_per_worker) * (int(worker_id) + 1)))
    if banks != expected_banks:
        raise OwnedA107Error('worker/bank mapping mismatch')
    worker_tag = f'{int(worker_id):03d}'
    worker_root = _directory(
        root / 'acquisition' / f'worker_{worker_tag}', 'own worker root')
    roster = worker_root / 'a107_roster.tsv'
    records, roster_sha = _parse_roster(worker_root, roster)

    schema = None
    unique = {}
    duplicate_rows = 0
    file_records = []
    for relative, path, payload, digest in records:
        columns, rows = _parse_snapshot(payload, path)
        if schema is None:
            schema = columns
        elif columns != schema:
            raise OwnedA107Error('A107 schema/order differs across snapshots')
        file_records.append({
            'relative_path': relative, 'size': len(payload),
            'sha256': digest, 'rows': len(rows),
        })
        for row in rows:
            bank = _strict_uint(row['bankid'], 'bankid', (1 << 31) - 1)
            event = _strict_uint(row['event_id'], 'event_id')
            template = _strict_uint(
                row['tmplt_idx'], 'tmplt_idx', (1 << 31) - 1)
            gps_ns = _event_ns(row)
            identity = (gps_ns, bank, event, template, row['ifos'])
            fingerprint = tuple(row[column] for column in schema)
            previous = unique.get(identity)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise OwnedA107Error(
                        'conflicting duplicate A107 scientific identity')
                duplicate_rows += 1
                continue
            unique[identity] = (fingerprint, row)

    observed_banks = {int(item[1]['bankid']) for item in unique.values()}
    if observed_banks != set(banks):
        raise OwnedA107Error('A107 rows do not cover every declared worker bank')
    ordered = sorted(
        (item[1] for item in unique.values()),
        key=lambda row: (
            _event_ns(row), int(row['bankid']), int(row['event_id']),
            int(row['tmplt_idx']), row['ifos']))
    bank_to_stream = {bank: stream for stream, bank in pairs}
    row_ordinal = {stream: 0 for stream in streams}
    normalized = []
    eligible = {'H1': 0, 'L1': 0}
    v_only_rows = 0
    for sequence, row in enumerate(ordered, start=1):
        bank = int(row['bankid'])
        if bank not in bank_to_stream:
            raise OwnedA107Error('A107 row bank outside worker mapping')
        stream = bank_to_stream[bank]
        event_gps_ns = _event_ns(row)
        normalized_row = {
            'stream_abi': 'sidecar-owned-a107-v1',
            'worker_id': str(worker_id),
            'bank_group': str(worker_group),
            'source_stream_ordinal': str(stream),
            'buffer_ordinal': '0',
            'row_ordinal': str(row_ordinal[stream]),
            'stream_seq': str(sequence),
            'row_identity': (
                f'{event_gps_ns}:{bank}:{row["event_id"]}:'
                f'{row["tmplt_idx"]}:{row["ifos"]}'),
            'bankid': row['bankid'],
            'event_id': row['event_id'],
            'ifos': row['ifos'],
            'is_background': row['is_background'],
            'end_time': row['end_time'],
            'end_time_ns': row['end_time_ns'],
            'end_time_sngl_H1': row['end_time_sngl_H1'],
            'end_time_ns_sngl_H1': row['end_time_ns_sngl_H1'],
            'end_time_sngl_L1': row['end_time_sngl_L1'],
            'end_time_ns_sngl_L1': row['end_time_ns_sngl_L1'],
            'snglsnr_H1': _canonical_real4(row['snglsnr_H1'], 'snglsnr_H1'),
            'snglsnr_L1': _canonical_real4(row['snglsnr_L1'], 'snglsnr_L1'),
            'chisq_H1': _canonical_real4(row['chisq_H1'], 'chisq_H1'),
            'chisq_L1': _canonical_real4(row['chisq_L1'], 'chisq_L1'),
            'tmplt_idx': row['tmplt_idx'],
        }
        row_ordinal[stream] += 1
        if row['ifos'] == 'V1':
            v_only_rows += 1
        for ifo in ('H1', 'L1'):
            if ifo in row['ifos']:
                rho = float(normalized_row[f'snglsnr_{ifo}'])
                chisq = float(normalized_row[f'chisq_{ifo}'])
                if (math.isfinite(rho) and rho >= 4.0
                        and math.isfinite(chisq) and chisq > 0.0):
                    eligible[ifo] += 1
        normalized.append(normalized_row)
    metadata = {
        'input_kind': 'sidecar-owned-legacy-a107',
        'input_abi': 'sidecar-owned-a107-v1',
        'run_root': str(root),
        'worker_root': str(worker_root),
        'worker_id': int(worker_id),
        'worker_count': int(worker_count),
        'worker_group': int(worker_group),
        'worker_bank_ids': list(banks),
        'a107_roster': str(roster),
        'a107_roster_sha256': roster_sha,
        'a107_schema_columns': len(schema or ()),
        'a107_files': file_records,
        'foreground_rows': len(normalized),
        'identical_duplicates_removed': duplicate_rows,
        'eligible_components': eligible,
        'v_only_rows': v_only_rows,
        'science_order': 'event_gps_ns_bank_event_template_ifos',
    }
    return normalized, metadata
