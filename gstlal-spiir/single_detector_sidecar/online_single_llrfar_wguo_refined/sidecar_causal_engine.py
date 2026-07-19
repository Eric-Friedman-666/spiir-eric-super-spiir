#!/usr/bin/env python3
"""Worker-local causal single-detector reference state for the sidecar.

The sidecar is independent of crashcar.  It consumes one versioned,
append-only, non-mutating Postcoh mirror per worker and writes only
sidecar-owned background/reference products.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
from typing import Iterable

import sidecar_segment_provenance as segment_contract
import verification_sidecar_numeric as numeric

NSEC = 1_000_000_000
MODE_BG_ONLY = "BG_ONLY"
MODE_NO_INJECTION = "NO_INJECTION"
MODE_FROZEN_ASSIGNMENT_ONLY = "FROZEN_ASSIGNMENT_ONLY"
STATUS_BG_ONLY = "BG_ONLY_SUPPORT"
STATUS_PENDING = "PENDING_BG"
STATUS_ASSIGNED_DIRECT = "ASSIGNED_DIRECT"
STATUS_ASSIGNED_TAIL = "ASSIGNED_TAIL"
STATUS_FAILED_BG = "FAILED_BG"
STATUS_FAILED_LLR = "FAILED_LLR"
STATUS_FAILED_INPUT = "FAILED_INPUT"
STATUS_NOT_ELIGIBLE = "NOT_ELIGIBLE"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_MULTI_OWNED_LLR_ONLY = "MULTI_OWNED_LLR_ONLY"
STATUS_TO_CRASHCAR_CODE = {
    STATUS_ASSIGNED_DIRECT: 1,
    STATUS_ASSIGNED_TAIL: 1,
    STATUS_PENDING: 2,
    STATUS_FAILED_BG: 3,
    STATUS_NOT_ELIGIBLE: 4,
    STATUS_UNSUPPORTED: 5,
    STATUS_FAILED_LLR: 6,
    STATUS_BG_ONLY: 9,
    STATUS_FAILED_INPUT: 10,
}
SOURCE_NONE = "NONE"
SOURCE_LIVE = "LIVE_PRIOR_BG"
SOURCE_FROZEN = "FROZEN_BG"
IFOS = ("H1", "L1")
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_CANONICAL_IFO_ORDER = ("H1", "L1", "V1", "K1")
_MAX_CANONICAL_JSON_BYTES = 268_435_456
_MAX_SUPPORT_POINTS = 1_000_000


class CausalContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupportRecord:
    identity: tuple
    gps_ns: int
    llr: float


@dataclass(frozen=True)
class AuthorityIFO:
    ranks: tuple[float, ...]
    livetime_ns: int
    r_tail: float
    slope: float
    support_count: int


@dataclass(frozen=True)
class Authority:
    version: int
    epoch_ns: int
    native_sha256: str
    by_ifo: dict[str, AuthorityIFO]


@dataclass(frozen=True)
class Component:
    worker_id: int
    worker_group: int
    source_stream_ordinal: int
    buffer_ordinal: int
    row_ordinal: int
    row_event_gps_ns: int
    ifo: str
    gps_ns: int | None
    rho: float | None
    chisq: float | None
    bankid: int
    tmplt_idx: int
    event_id: int
    stream_seq: int
    ifos: str
    route: str
    input_error: str | None
    llr_error: str | None

    @property
    def identity(self) -> tuple:
        return (
            self.worker_id,
            self.worker_group,
            self.source_stream_ordinal,
            self.buffer_ordinal,
            self.row_ordinal,
            self.stream_seq,
            self.ifo,
            self.event_id,
            self.bankid,
            self.tmplt_idx,
            self.gps_ns,
        )

    @property
    def scientific_identity(self) -> tuple:
        """Identity independent of transport ordinals.

        Buffer/row/stream ordinals remain in the emitted parity row, but a
        replay of the same physical event must not become fresh background
        support merely because it arrived at another mirror position.
        """
        return (
            self.worker_id,
            self.worker_group,
            self.ifo,
            self.event_id,
            self.bankid,
            self.tmplt_idx,
            self.gps_ns,
        )


def _strict_sha(value: object, field: str) -> str:
    text = str(value)
    if not _SHA_RE.fullmatch(text):
        raise CausalContractError(f"{field}: expected lowercase SHA-256")
    return text


def _strict_uint(value: object, field: str, maximum: int) -> int:
    text = str(value)
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)", text):
        raise CausalContractError(f"{field}: noncanonical unsigned integer")
    result = int(text, 10)
    if result > maximum:
        raise CausalContractError(f"{field}: out of range")
    return result


def _strict_hex_float(value: object, field: str) -> float:
    text = str(value)
    try:
        result = float.fromhex(text)
    except (TypeError, ValueError) as exc:
        raise CausalContractError(f"{field}: invalid binary64 hex") from exc
    if not math.isfinite(result) or result.hex() != text:
        raise CausalContractError(f"{field}: noncanonical/nonfinite hex")
    return result


def _hex(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise CausalContractError("cannot encode nonfinite binary64")
    return number.hex()


def _float32_bits(value: float) -> tuple[float | None, str]:
    """Project positive finite binary64 without making REAL4 authoritative."""
    exact = float(value)
    if not (math.isfinite(exact) and exact > 0.0):
        raise CausalContractError(
            "Assigned FAR binary64 is not positive finite")
    try:
        payload = struct.pack(">f", exact)
    except (OverflowError, struct.error):
        return None, ""
    rounded = struct.unpack(">f", payload)[0]
    if not (math.isfinite(rounded) and rounded > 0.0):
        return None, ""
    return rounded, payload.hex()


def _gps_from_row(row: dict[str, str], ifo: str) -> int:
    """Parse only the detector-local GPS; shared-time fallback is forbidden."""
    seconds = row.get(f"end_time_sngl_{ifo}")
    nanoseconds = row.get(f"end_time_ns_sngl_{ifo}")
    if seconds in (None, "") or nanoseconds in (None, ""):
        raise CausalContractError(
            f"{ifo}: missing detector-local GPS")
    gps_ns = segment_contract.gps_to_ns(
        seconds, nanoseconds, f"{ifo}.gps")
    if gps_ns == 0:
        raise CausalContractError(
            f"{ifo}: detector-local GPS 0/0 is invalid")
    return gps_ns


def parse_ifos(value: object) -> tuple[str, ...]:
    text = str(value or "")
    expected = "".join(
        ifo for ifo in _CANONICAL_IFO_ORDER if ifo in text)
    tokens = tuple(re.findall(r"H1|L1|V1|K1", text))
    if (not text or "".join(tokens) != text
            or len(tokens) != len(set(tokens))):
        raise CausalContractError(
            f"invalid detector mask {text!r}")
    if "".join(tokens) != expected:
        raise CausalContractError(
            f"noncanonical detector order {text!r}")
    return tokens


def route_for_ifos(ifos: tuple[str, ...]) -> str:
    detectors = set(ifos)
    if "K1" in detectors:
        raise CausalContractError(
            "K1 route is outside the reviewed H1/L1/V1 contract")
    h = "H1" in detectors
    l = "L1" in detectors
    v = "V1" in detectors
    if h and l:
        return "NORMAL_MULTI"
    if h:
        return "H_SINGLE"
    if l:
        return "L_SINGLE"
    if v:
        return "V_ONLY"
    raise CausalContractError("empty detector route")


def _snapshot_regular_file_bytes(
        path: str | os.PathLike[str], label: str,
        maximum_bytes: int = _MAX_CANONICAL_JSON_BYTES) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CausalContractError(
            f"{label}: O_NOFOLLOW is unavailable")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise CausalContractError(
            f"{label} cannot be opened as a non-symlink file") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise CausalContractError(
                f"{label} must be a regular file")
        if before.st_size < 0 or before.st_size > maximum_bytes:
            raise CausalContractError(
                f"{label} exceeds the reviewed size bound")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(1 << 20, remaining))
            if not chunk:
                raise CausalContractError(
                    f"{label} changed or truncated during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise CausalContractError(
                f"{label} grew during read")
        after = os.fstat(fd)
        stable = (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
        )
        if not stable:
            raise CausalContractError(
                f"{label} changed during read")
        payload = b"".join(chunks)
        if len(payload) != before.st_size:
            raise CausalContractError(
                f"{label} read length drift")
        return payload
    finally:
        os.close(fd)


def _decode_canonical_json(
        path: str | os.PathLike[str], label: str) -> tuple[dict, bytes, str]:
    payload = _snapshot_regular_file_bytes(path, label)
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise CausalContractError(
            f"{label} must have one final LF")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise CausalContractError(
                    f"duplicate {label} key {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("ascii"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CausalContractError(
            f"invalid {label} JSON") from exc
    canonical = (
        json.dumps(
            value, ensure_ascii=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    if canonical != payload:
        raise CausalContractError(
            f"noncanonical {label} bytes")
    return value, payload, hashlib.sha256(payload).hexdigest()


def _empirical_tail_and_far_by_rank(
        sorted_background_ranks: Iterable[float],
        livetime_seconds: float,
        tail_log10_far: float = -2.0,
) -> tuple[float, float, tuple[tuple[float, float], ...], dict[float, float]]:
    """Linear-time equivalent of the frozen numeric empirical-tail oracle."""
    ranks = tuple(float(value) for value in sorted_background_ranks)
    livetime = float(livetime_seconds)
    tail_anchor = float(tail_log10_far)
    if (not math.isfinite(tail_anchor) or not tail_anchor < 0.0):
        raise CausalContractError("tail_log10_far must be finite and negative")
    if (not ranks
            or any(not math.isfinite(value) for value in ranks)
            or list(ranks) != sorted(ranks)
            or not math.isfinite(livetime)
            or livetime <= 0.0):
        raise CausalContractError(
            "invalid support ranks/livetime for empirical FAR")
    points = []
    far_by_rank = {}
    support_count = len(ranks)
    for index, rank in enumerate(ranks):
        if index and rank == ranks[index - 1]:
            continue
        far = float(support_count - index) / livetime
        if not (math.isfinite(far) and far > 0.0):
            raise CausalContractError(
                "invalid empirical Calculated FAR")
        far_by_rank[rank] = far
        points.append((rank, math.log10(far)))
    r_tail = min(
        points,
        key=lambda point: (
            abs(point[1] - tail_anchor), point[0]),
    )[0]
    slope = numeric.fit_anchored_tail(
        points, r_tail, tail_anchor)
    return r_tail, slope, tuple(points), far_by_rank


def _decode_derivative(
        path: str | os.PathLike[str]) -> tuple[dict, bytes, str]:
    value, payload, digest = _decode_canonical_json(
        path, "segment derivative")
    if list(value) != [
            "schema_version", "source_xml_sha256",
            "source_xml_size", "run_start", "run_end", "targets"]:
        raise CausalContractError(
            "segment derivative root schema/order drift")
    if value["schema_version"] != 2:
        raise CausalContractError(
            "segment derivative schema version drift")
    _strict_sha(
        value["source_xml_sha256"], "source_xml_sha256")
    _strict_uint(
        value["source_xml_size"], "source_xml_size",
        (1 << 63) - 1)
    if list(value["targets"]) != ["H1", "L1"]:
        raise CausalContractError(
            "segment derivative target order drift")
    return value, payload, digest


def _gps_object_to_ns(value: dict, field: str) -> int:
    if list(value) != ["seconds", "nanoseconds"]:
        raise CausalContractError(
            f"{field}: GPS schema/order drift")
    return segment_contract.gps_to_ns(
        value["seconds"], value["nanoseconds"], field)


def _derivative_intervals(
    derivative: dict,
    ifo: str,
    run_start_ns: int,
    run_end_ns: int,
) -> tuple[tuple[int, int], ...]:
    target = derivative["targets"][ifo]
    expected = [
        "segment_def_id", "raw_row_count", "empty_row_count",
        "merged_interval_count", "livetime_ns", "intervals",
    ]
    if list(target) != expected:
        raise CausalContractError(
            f"{ifo}: segment target schema/order drift")
    raw_count = _strict_uint(
        target["raw_row_count"], f"{ifo}.raw_row_count",
        (1 << 63) - 1)
    empty_count = _strict_uint(
        target["empty_row_count"], f"{ifo}.empty_row_count",
        (1 << 63) - 1)
    merged_count = _strict_uint(
        target["merged_interval_count"],
        f"{ifo}.merged_interval_count", (1 << 63) - 1)
    livetime_ns = _strict_uint(
        target["livetime_ns"], f"{ifo}.livetime_ns",
        (1 << 63) - 1)
    if raw_count < empty_count:
        raise CausalContractError(
            f"{ifo}: raw row count is below empty row count")
    if merged_count > raw_count - empty_count:
        raise CausalContractError(
            f"{ifo}: merged count exceeds nonempty raw rows")
    if (merged_count == 0) != (livetime_ns == 0):
        raise CausalContractError(
            f"{ifo}: zero merged-count/livetime equivalence drift")
    intervals = []
    previous_end = None
    for index, interval in enumerate(target["intervals"]):
        if list(interval) != ["start", "end"]:
            raise CausalContractError(
                f"{ifo}: interval schema/order drift")
        start = _gps_object_to_ns(
            interval["start"],
            f"{ifo}.interval[{index}].start")
        end = _gps_object_to_ns(
            interval["end"],
            f"{ifo}.interval[{index}].end")
        if start >= end:
            raise CausalContractError(
                f"{ifo}: empty/reversed canonical interval")
        if start < run_start_ns or end > run_end_ns:
            raise CausalContractError(
                f"{ifo}: canonical interval outside bound run")
        if previous_end is not None and start <= previous_end:
            raise CausalContractError(
                f"{ifo}: intervals overlap or are adjacent "
                "after canonicalization")
        intervals.append((start, end))
        previous_end = end
    if len(intervals) != merged_count:
        raise CausalContractError(
            f"{ifo}: merged interval count drift")
    total = sum(end - start for start, end in intervals)
    if total != livetime_ns:
        raise CausalContractError(
            f"{ifo}: canonical livetime drift")
    if total > run_end_ns - run_start_ns:
        raise CausalContractError(
            f"{ifo}: canonical livetime exceeds run span")
    return tuple(intervals)


def window_livetime_ns(
    intervals: Iterable[tuple[int, int]],
    low_ns: int,
    high_ns: int,
) -> int:
    if high_ns <= low_ns:
        raise CausalContractError(
            "candidate window is not positive")
    total = 0
    for start, end in intervals:
        overlap_start = max(start, low_ns)
        overlap_end = min(end, high_ns)
        if overlap_start < overlap_end:
            total += overlap_end - overlap_start
    if total < 0 or total > high_ns - low_ns:
        raise CausalContractError(
            "window livetime out of range")
    return total


def _write_atomic_readonly(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_fd = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY)
    temporary = f".{path.name}.tmp.{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(
            temporary, flags, 0o600, dir_fd=parent_fd)
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.fchmod(fd, 0o444)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(
            temporary, path.name,
            src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
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


class WorkerCausalEngine:
    """One independent worker with paired H1/L1 authority publication."""

    def __init__(
        self,
        *,
        mode: str,
        worker_id: int,
        worker_count: int,
        worker_group: int,
        source_stream_bank_map: Iterable[tuple[int, int]],
        run_start_ns: int,
        run_end_ns: int,
        background_window_ns: int | None,
        update_period_ns: int | None,
        segment_derivative_path: str,
        expected_segment_xml_sha256: str,
        expected_segment_derivative_sha256: str,
        shape_source,
        background_path: str | None,
        frozen_background_path: str | None = None,
        expected_frozen_background_sha256: str | None = None,
        expected_frozen_run_namespace_sha256: str | None = None,
        run_namespace_sha256: str,
        source_manifest_sha256: str,
        runtime_manifest_sha256: str,
        config_sha256: str,
        shape_source_sha256: str,
        tail_log10_far: float = -2.0,
    ):
        self.tail_log10_far = float(tail_log10_far)
        if (not math.isfinite(self.tail_log10_far)
                or not self.tail_log10_far < 0.0):
            raise CausalContractError(
                "tail_log10_far must be finite and strictly negative")
        if mode not in (
                MODE_BG_ONLY, MODE_NO_INJECTION,
                MODE_FROZEN_ASSIGNMENT_ONLY):
            raise CausalContractError(
                "unknown sidecar state mode")
        if not (0 <= worker_id < worker_count <= 4096):
            raise CausalContractError(
                "worker identity out of range")
        if not (0 <= worker_group < worker_count):
            raise CausalContractError(
                "worker group out of range")
        raw_stream_bank_pairs = tuple(source_stream_bank_map)
        if (
                not raw_stream_bank_pairs
                or any(
                    not isinstance(pair, (tuple, list)) or len(pair) != 2
                    for pair in raw_stream_bank_pairs)
        ):
            raise CausalContractError(
                "source stream to bank map must contain exact pairs")
        stream_bank_pairs = tuple(
            (
                _strict_uint(
                    pair[0], "source_stream_id", (1 << 31) - 1),
                _strict_uint(pair[1], "source_stream_bank_id", 383),
            )
            for pair in raw_stream_bank_pairs
        )
        streams = tuple(stream for stream, _bank in stream_bank_pairs)
        stream_banks = tuple(bank for _stream, bank in stream_bank_pairs)
        if streams != tuple(sorted(set(streams))):
            raise CausalContractError(
                "source stream roster must be nonempty unique sorted")
        if len(set(stream_banks)) != len(stream_banks):
            raise CausalContractError(
                "source stream to bank map must be one-to-one")
        banks = tuple(sorted(stream_banks))
        if run_end_ns <= run_start_ns:
            raise CausalContractError(
                "run interval is not positive")

        derivative, derivative_bytes, derivative_sha = (
            _decode_derivative(segment_derivative_path))
        expected_xml_sha = _strict_sha(
            expected_segment_xml_sha256,
            "expected_segment_xml_sha256")
        expected_derivative_sha = _strict_sha(
            expected_segment_derivative_sha256,
            "expected_segment_derivative_sha256")
        if derivative["source_xml_sha256"] != expected_xml_sha:
            raise CausalContractError(
                "segment XML binding mismatch")
        if derivative_sha != expected_derivative_sha:
            raise CausalContractError(
                "segment derivative binding mismatch")
        derivative_start = _gps_object_to_ns(
            derivative["run_start"], "segment.run_start")
        derivative_end = _gps_object_to_ns(
            derivative["run_end"], "segment.run_end")
        if (derivative_start != run_start_ns
                or derivative_end != run_end_ns):
            raise CausalContractError(
                "segment derivative run binding mismatch")

        requested_shape_sha = _strict_sha(
            shape_source_sha256, "shape_source_sha256")
        actual_shape_sha = getattr(
            shape_source, "source_manifest_sha256", None)
        if actual_shape_sha != requested_shape_sha:
            raise CausalContractError(
                "shape source manifest binding mismatch")

        self.intervals = {
            ifo: _derivative_intervals(
                derivative, ifo, run_start_ns, run_end_ns)
            for ifo in IFOS
        }
        self.segment_xml_sha256 = expected_xml_sha
        self.segment_canonical_sha256 = derivative_sha
        self.segment_derivative_bytes = derivative_bytes
        self.mode = mode
        self.worker_id = worker_id
        self.worker_count = worker_count
        self.worker_group = worker_group
        self.bank_ids = banks
        self.source_stream_ids = streams
        self.source_stream_bank_pairs = stream_bank_pairs
        self.source_stream_bank_map = dict(stream_bank_pairs)
        self.run_start_ns = run_start_ns
        self.run_end_ns = run_end_ns
        self.shape_source = shape_source
        self.background_path = (
            Path(background_path) if background_path else None)
        self.run_namespace_sha256 = _strict_sha(
            run_namespace_sha256, "run_namespace_sha256")
        self.source_manifest_sha256 = _strict_sha(
            source_manifest_sha256, "source_manifest_sha256")
        self.runtime_manifest_sha256 = _strict_sha(
            runtime_manifest_sha256, "runtime_manifest_sha256")
        self.config_sha256 = _strict_sha(
            config_sha256, "config_sha256")
        self.shape_source_sha256 = requested_shape_sha
        self.support = {ifo: [] for ifo in IFOS}
        self.support_identity = {ifo: set() for ifo in IFOS}
        self.authority: Authority | None = None
        self.accepted_version = 0
        self.frozen_authority_loaded = False
        self.last_stream_seq = 0
        self.last_buffer_ordinal_by_stream: dict[int, int] = {}
        self.last_row_ordinal_by_stream: dict[int, int] = {}
        self.seen_source_streams: set[int] = set()
        self.last_row_time_ns: int | None = None
        self.raw_batch_consumed = False
        self.terminal_failed_bg_group_time_ns: int | None = None
        self.terminal_unprocessed_transport_rows = 0
        self.lifecycle = {
            "seen_rows": 0,
            "transport_rows_validated": 0,
            "seen_active_HL": 0,
            "historical_background_excluded": 0,
            "threshold_excluded": 0,
            "failed": 0,
            "failed_input": 0,
            "failed_llr": 0,
            "failed_bg": 0,
            "unsupported": 0,
            "pending": 0,
            "bg_only": 0,
            "assigned": 0,
            "multi_owned_llr_only": 0,
            "support_candidates": 0,
            "support_appended": 0,
            "support_pruned": 0,
            "support_cancelled_terminal": 0,
            "terminal_failed_bg_groups": 0,
            "candidate_accepted": 0,
            "candidate_rejected": 0,
        }
        self.candidate_rejections: list[dict] = []

        if mode == MODE_FROZEN_ASSIGNMENT_ONLY:
            if (background_window_ns is not None
                    or update_period_ns is not None
                    or self.background_path is not None):
                raise CausalContractError(
                    "frozen mode forbids live BG schedule/output")
            if not (
                frozen_background_path
                and expected_frozen_background_sha256
                and expected_frozen_run_namespace_sha256
            ):
                raise CausalContractError(
                    "frozen mode requires exact background bindings")
            self.background_window_ns = None
            self.update_period_ns = None
            self.next_epoch_ns = None
            self.authority = self._load_frozen_authority(
                frozen_background_path,
                expected_frozen_background_sha256,
                expected_frozen_run_namespace_sha256)
            self.accepted_version = self.authority.version
            self.frozen_authority_loaded = True
        else:
            if (background_window_ns is None
                    or update_period_ns is None
                    or background_window_ns <= 0
                    or update_period_ns <= 0):
                raise CausalContractError(
                    "live background schedule is not positive")
            if run_start_ns + background_window_ns > run_end_ns:
                raise CausalContractError(
                    "run cannot complete one background window")
            if self.background_path is None:
                raise CausalContractError(
                    "live modes require a background output")
            if (frozen_background_path is not None
                    or expected_frozen_background_sha256 is not None
                    or expected_frozen_run_namespace_sha256 is not None):
                raise CausalContractError(
                    "live mode received frozen background inputs")
            self.background_window_ns = background_window_ns
            self.update_period_ns = update_period_ns
            self.next_epoch_ns = (
                run_start_ns + background_window_ns)

    def _validate_ordinals(self, row: dict[str, str]) -> tuple[int, int, int]:
        stream = _strict_uint(
            row.get("source_stream_ordinal"),
            "source_stream_ordinal", (1 << 31) - 1)
        if stream not in self.source_stream_ids:
            raise CausalContractError(
                f"source stream {stream} is outside declared roster "
                f"{self.source_stream_ids}")
        buffer_ordinal = _strict_uint(
            row.get("buffer_ordinal"),
            "buffer_ordinal", (1 << 63) - 1)
        row_ordinal = _strict_uint(
            row.get("row_ordinal"),
            "row_ordinal", (1 << 63) - 1)
        last_buffer = self.last_buffer_ordinal_by_stream.get(stream)
        last_row = self.last_row_ordinal_by_stream.get(stream)
        if last_buffer is None:
            if buffer_ordinal != 0 or row_ordinal != 0:
                raise CausalContractError(
                    "first emitted row-bearing buffer for each source "
                    "stream must be buffer=0,row=0")
        elif buffer_ordinal == last_buffer:
            if row_ordinal != last_row + 1:
                raise CausalContractError(
                    "row ordinal gap/regression within source buffer")
        elif buffer_ordinal == last_buffer + 1:
            if row_ordinal != 0:
                raise CausalContractError(
                    "new source buffer must begin at row ordinal zero")
        else:
            raise CausalContractError(
                "emitted row-bearing source buffer ordinal gap/regression")
        self.last_buffer_ordinal_by_stream[stream] = buffer_ordinal
        self.last_row_ordinal_by_stream[stream] = row_ordinal
        self.seen_source_streams.add(stream)
        return stream, buffer_ordinal, row_ordinal

    def _route_and_components(
        self,
        row: dict[str, str],
        seq: int,
        source_stream_ordinal: int,
        buffer_ordinal: int,
        row_ordinal: int,
        row_event_gps_ns: int,
    ) -> tuple[str, list[Component]]:
        ifos = parse_ifos(row.get("ifos"))
        route = route_for_ifos(ifos)
        if route == "V_ONLY":
            return route, []
        bankid = _strict_uint(
            row.get("bankid"), "bankid", (1 << 31) - 1)
        tmplt_idx = _strict_uint(
            row.get("tmplt_idx"), "tmplt_idx", (1 << 31) - 1)
        event_id = _strict_uint(
            row.get("event_id"), "event_id", (1 << 63) - 1)
        components = []
        for ifo in IFOS:
            if ifo not in ifos:
                continue
            rho = None
            chisq = None
            gps_ns = None
            input_errors = []
            llr_errors = []
            try:
                rho = float(str(row.get(f"snglsnr_{ifo}")))
            except (TypeError, ValueError, OverflowError):
                input_errors.append("rho_malformed")
            if rho is not None and not math.isfinite(rho):
                input_errors.append("rho_nonfinite")
            try:
                gps_ns = _gps_from_row(row, ifo)
            except Exception as exc:
                input_errors.append(
                    f"local_gps_invalid:{type(exc).__name__}")
            try:
                chisq = float(str(row.get(f"chisq_{ifo}")))
            except (TypeError, ValueError, OverflowError):
                llr_errors.append("chisq_malformed")
            if chisq is not None and (
                    not math.isfinite(chisq) or chisq <= 0.0):
                llr_errors.append("chisq_not_positive_finite")
            components.append(Component(
                worker_id=self.worker_id,
                worker_group=self.worker_group,
                source_stream_ordinal=source_stream_ordinal,
                buffer_ordinal=buffer_ordinal,
                row_ordinal=row_ordinal,
                row_event_gps_ns=row_event_gps_ns,
                ifo=ifo,
                gps_ns=gps_ns,
                rho=rho,
                chisq=chisq,
                bankid=bankid,
                tmplt_idx=tmplt_idx,
                event_id=event_id,
                stream_seq=seq,
                ifos="".join(ifos),
                route=route,
                input_error=";".join(input_errors) or None,
                llr_error=";".join(llr_errors) or None,
            ))
        return route, components

    def _base_result(self, component: Component) -> dict:
        gps = (
            segment_contract.ns_to_gps(component.gps_ns)
            if component.gps_ns is not None else None)
        return {
            "schema_version": 3,
            "worker_id": self.worker_id,
            "worker_count": self.worker_count,
            "worker_group": self.worker_group,
            "source_stream_ordinal":
                component.source_stream_ordinal,
            "buffer_ordinal": component.buffer_ordinal,
            "row_ordinal": component.row_ordinal,
            "row_event_gps_ns": component.row_event_gps_ns,
            "stream_seq": component.stream_seq,
            "event_id": component.event_id,
            "bankid": component.bankid,
            "tmplt_idx": component.tmplt_idx,
            "ifos": component.ifos,
            "route": component.route,
            "ifo": component.ifo,
            "gps_seconds": gps["seconds"] if gps else "",
            "gps_nanoseconds": gps["nanoseconds"] if gps else "",
            "rho_hex": (
                _hex(component.rho)
                if component.rho is not None
                and math.isfinite(component.rho) else ""),
            "chisq_hex": (
                _hex(component.chisq)
                if component.chisq is not None
                and math.isfinite(component.chisq) else ""),
            "eligible": 0,
            "a_eff_hex": "",
            "dof": "",
            "llr_hex": "",
            "llr_valid": 0,
            "calculated_far_hex": "",
            "calculated_count_ge": "",
            "calculated_livetime_ns": "",
            "one_count_floor": "",
            "calculated_valid": 0,
            "assigned_far_hex": "",
            "assigned_far_real4_hex": "",
            "assigned_valid": 0,
            "source": SOURCE_NONE,
            "status": "",
            "reason": "",
            "bg_version": 0,
            "bg_epoch_seconds": 0,
            "bg_epoch_nanoseconds": 0,
            "bg_native_sha256": "",
        }

    def _failed_result(
        self,
        component: Component,
        status: str,
        reason: str,
        base: dict | None = None,
    ) -> dict:
        lifecycle_key = {
            STATUS_FAILED_INPUT: "failed_input",
            STATUS_FAILED_LLR: "failed_llr",
            STATUS_FAILED_BG: "failed_bg",
            STATUS_UNSUPPORTED: "unsupported",
        }.get(status)
        if lifecycle_key is None:
            raise CausalContractError(
                f"unreviewed sidecar failure status {status!r}")
        result = (
            base if base is not None
            else self._base_result(component))
        result["status"] = status
        result["reason"] = reason
        self.lifecycle["failed"] += 1
        self.lifecycle[lifecycle_key] += 1
        return result

    def _evaluate(
        self,
        component: Component,
        selected: Authority | None,
    ) -> tuple[dict | None, SupportRecord | None]:
        self.lifecycle["seen_active_HL"] += 1
        result = self._base_result(component)

        # Exact first-match precedence is shared with the crashcar row atom.
        if component.rho is None or not math.isfinite(component.rho):
            result["status"] = STATUS_NOT_ELIGIBLE
            result["reason"] = "rho_nonfinite_or_malformed"
            self.lifecycle["threshold_excluded"] += 1
            return result, None
        if component.rho < 4.0:
            result["status"] = STATUS_NOT_ELIGIBLE
            result["reason"] = "rho_below_inclusive_threshold"
            self.lifecycle["threshold_excluded"] += 1
            return result, None
        result["eligible"] = 1

        # Explicitly deferred BBH scope precedes local/LLR input failures.
        if component.bankid >= 384:
            return self._failed_result(
                component, STATUS_UNSUPPORTED,
                "unsupported_bank_ge_384", result), None
        if component.input_error is not None or component.gps_ns is None:
            return self._failed_result(
                component, STATUS_FAILED_INPUT,
                component.input_error or "detector_local_gps_invalid",
                result), None
        if not (
            self.run_start_ns <= component.gps_ns < self.run_end_ns
        ):
            return self._failed_result(
                component, STATUS_FAILED_INPUT,
                "component_gps_outside_run", result), None
        if component.llr_error is not None or component.chisq is None:
            return self._failed_result(
                component, STATUS_FAILED_LLR,
                component.llr_error or "chisq_invalid", result), None
        expected_stream_bank = self.source_stream_bank_map[
            component.source_stream_ordinal]
        if component.bankid != expected_stream_bank:
            return self._failed_result(
                component, STATUS_FAILED_LLR,
                "worker_bank_mapping_mismatch", result), None
        try:
            a_eff, dof = self.shape_source.a_eff_and_dof(
                component.ifo,
                component.bankid,
                component.tmplt_idx)
        except Exception as exc:
            return self._failed_result(
                component, STATUS_FAILED_LLR,
                f"template_shape_or_dof_failure:{type(exc).__name__}",
                result), None
        try:
            llr = numeric.pdf_gaussian_llr(
                component.rho, component.chisq, a_eff, dof)
        except Exception as exc:
            return self._failed_result(
                component, STATUS_FAILED_LLR,
                f"gaussian_llr_numeric_failure:{type(exc).__name__}",
                result), None
        result["a_eff_hex"] = _hex(a_eff)
        result["dof"] = int(dof)
        result["llr_hex"] = _hex(llr)
        result["llr_valid"] = 1

        support = None
        if self.mode != MODE_FROZEN_ASSIGNMENT_ONLY:
            support = SupportRecord(
                component.scientific_identity, component.gps_ns, llr)

        if self.mode == MODE_BG_ONLY:
            result["status"] = STATUS_BG_ONLY
            self.lifecycle["bg_only"] += 1
            self.lifecycle["support_candidates"] += 1
            return result, support

        # HL/HLV has one final FAR owner: unchanged normal multi/coherent.
        # The sidecar preserves H/L LLR and commits eligible no-injection
        # support only after scoring, but never queries or writes single FAR.
        if component.route == "NORMAL_MULTI":
            result["status"] = STATUS_MULTI_OWNED_LLR_ONLY
            result["reason"] = "normal_multi_is_unique_final_far_owner"
            self.lifecycle["multi_owned_llr_only"] += 1
            self.lifecycle["support_candidates"] += 1
            return result, support

        if selected is None:
            if self.mode == MODE_FROZEN_ASSIGNMENT_ONLY:
                raise CausalContractError(
                    "frozen authority vanished")
            result["status"] = STATUS_PENDING
            result["reason"] = "no_completed_prior_background"
            self.lifecycle["pending"] += 1
            self.lifecycle["support_candidates"] += 1
            return result, support

        epoch = segment_contract.ns_to_gps(selected.epoch_ns)
        result["bg_version"] = selected.version
        result["bg_epoch_seconds"] = epoch["seconds"]
        result["bg_epoch_nanoseconds"] = epoch["nanoseconds"]
        result["bg_native_sha256"] = selected.native_sha256
        try:
            authority_ifo = selected.by_ifo[component.ifo]
            if not (
                    0 < authority_ifo.livetime_ns < (1 << 53)):
                raise CausalContractError(
                    "selected_livetime_or_support_invalid")
            livetime_seconds = (
                float(authority_ifo.livetime_ns) / 1_000_000_000.0)
            calculated, count, floor = numeric.calculated_far(
                authority_ifo.ranks, llr, livetime_seconds)
            assigned, branch, assigned_count, assigned_floor = (
                numeric.assigned_far(
                    authority_ifo.ranks,
                    llr,
                    livetime_seconds,
                    authority_ifo.r_tail,
                    authority_ifo.slope,
                    self.tail_log10_far))
            if (count != assigned_count
                    or floor != assigned_floor):
                raise CausalContractError(
                    "support_metadata_drift")
            if branch not in ("direct", "tail"):
                raise CausalContractError(
                    "assigned_branch_drift")
            if not (
                    math.isfinite(calculated) and calculated > 0.0
                    and math.isfinite(assigned) and assigned > 0.0):
                raise CausalContractError(
                    "calculated_or_assigned_far_invalid")
            _rounded, real4_bits = _float32_bits(assigned)
        except Exception as exc:
            # A selected-but-invalid authority is terminal: preserve the exact
            # failed row but publish no support from this component.
            return self._failed_result(
                component, STATUS_FAILED_BG,
                f"far_failure:{type(exc).__name__}:{exc}",
                result), None

        result.update({
            "calculated_far_hex": _hex(calculated),
            "calculated_count_ge": count,
            "calculated_livetime_ns":
                authority_ifo.livetime_ns,
            "one_count_floor": int(floor),
            "calculated_valid": 1,
            "assigned_far_hex": _hex(assigned),
            "assigned_far_real4_hex": real4_bits,
            "assigned_valid": 1,
            "source": (
                SOURCE_FROZEN
                if self.mode == MODE_FROZEN_ASSIGNMENT_ONLY
                else SOURCE_LIVE),
            "status": (
                STATUS_ASSIGNED_DIRECT
                if branch == "direct"
                else STATUS_ASSIGNED_TAIL),
            "reason": "",
        })
        self.lifecycle["assigned"] += 1
        if support is not None:
            self.lifecycle["support_candidates"] += 1
        return result, support

    def _commit_group_support(
        self,
        future: Iterable[tuple[tuple, str, SupportRecord]],
    ) -> None:
        ordered = tuple(sorted(future, key=lambda item: item[0]))
        if not ordered:
            return
        if self.mode == MODE_FROZEN_ASSIGNMENT_ONLY:
            raise CausalContractError(
                "frozen mode attempted support mutation")

        pending_identities = {ifo: set() for ifo in IFOS}
        for _order, ifo, record in ordered:
            if ifo not in pending_identities:
                raise CausalContractError(
                    f"unknown support detector {ifo!r}")
            if (
                    record.identity in self.support_identity[ifo]
                    or record.identity in pending_identities[ifo]
            ):
                raise CausalContractError(
                    f"duplicate support identity for {ifo}: "
                    f"{record.identity}")
            pending_identities[ifo].add(record.identity)

        for _order, ifo, record in ordered:
            self.support_identity[ifo].add(record.identity)
            self.support[ifo].append(record)
        self.lifecycle["support_appended"] += len(ordered)

    def _validate_transport_row(self, row: dict[str, str]) -> None:
        if not isinstance(row, dict):
            raise CausalContractError("raw mirror row is not an object")
        row_worker = _strict_uint(
            row.get("worker_id"), "worker_id", 4095)
        row_group = _strict_uint(
            row.get("bank_group"), "bank_group", 4095)
        if row_worker != self.worker_id:
            raise CausalContractError(
                "raw row worker_id mismatch")
        if row_group != self.worker_group:
            raise CausalContractError(
                "raw row bank_group mismatch")
        seq = _strict_uint(
            row.get("stream_seq"),
            "stream_seq", (1 << 63) - 1)
        if seq != self.last_stream_seq + 1:
            raise CausalContractError(
                f"stream sequence regression/gap: "
                f"{seq} after {self.last_stream_seq}")
        self._validate_ordinals(row)
        self.last_stream_seq = seq
        self.lifecycle["transport_rows_validated"] += 1

    def _science_sort_key(self, row: dict[str, str]) -> tuple:
        row_time = segment_contract.gps_to_ns(
            row.get("end_time"), row.get("end_time_ns"),
            "row_event_gps")
        source_stream = _strict_uint(
            row.get("source_stream_ordinal"),
            "source_stream_ordinal", (1 << 31) - 1)
        buffer_ordinal = _strict_uint(
            row.get("buffer_ordinal"),
            "buffer_ordinal", (1 << 63) - 1)
        row_ordinal = _strict_uint(
            row.get("row_ordinal"),
            "row_ordinal", (1 << 63) - 1)
        event_id = _strict_uint(
            row.get("event_id"), "event_id", (1 << 63) - 1)
        return (
            row_time, source_stream, buffer_ordinal,
            row_ordinal, event_id,
        )

    def _preflight_scientific_identities(
        self, rows: Iterable[dict[str, str]],
    ) -> None:
        """Reject a replayed physical component before any science mutation."""
        seen: dict[tuple, tuple[int, int, int, int]] = {}
        for row in rows:
            flag = _strict_uint(
                row.get("is_background"), "is_background", 2)
            if flag != 0:
                continue
            seq = _strict_uint(
                row.get("stream_seq"),
                "stream_seq", (1 << 63) - 1)
            source_stream = _strict_uint(
                row.get("source_stream_ordinal"),
                "source_stream_ordinal", (1 << 31) - 1)
            buffer_ordinal = _strict_uint(
                row.get("buffer_ordinal"),
                "buffer_ordinal", (1 << 63) - 1)
            row_ordinal = _strict_uint(
                row.get("row_ordinal"),
                "row_ordinal", (1 << 63) - 1)
            row_time = self._science_sort_key(row)[0]
            if not self.run_start_ns <= row_time < self.run_end_ns:
                raise CausalContractError(
                    "row event time outside run before scientific mutation")
            _route, components = self._route_and_components(
                row, seq, source_stream, buffer_ordinal,
                row_ordinal, row_time)
            for component in components:
                identity = component.scientific_identity
                location = (
                    source_stream, buffer_ordinal, row_ordinal, seq)
                if identity in seen:
                    raise CausalContractError(
                        "duplicate scientific identity in completed mirror "
                        "before scientific mutation: "
                        f"{identity}; first={seen[identity]}, "
                        f"duplicate={location}")
                if identity in self.support_identity[component.ifo]:
                    raise CausalContractError(
                        "scientific identity already exists in support "
                        "before scientific mutation: "
                        f"{identity}; duplicate={location}")
                seen[identity] = location

    def process_rows(
        self, rows: Iterable[dict[str, str]]
    ) -> list[dict]:
        """Validate transport, then evaluate exact shared-time groups two-phase."""
        if self.raw_batch_consumed:
            raise CausalContractError(
                "raw mirror batch already consumed")
        materialized = list(rows)
        self.raw_batch_consumed = True

        # The complete completed mirror is a closed post-run input. Transport
        # validation happens before any authority or support mutation.
        for row in materialized:
            self._validate_transport_row(row)
        observed_streams = tuple(sorted(self.seen_source_streams))
        if observed_streams != self.source_stream_ids:
            raise CausalContractError(
                "completed mirror does not represent every declared "
                "source stream before scientific mutation")
        self._preflight_scientific_identities(materialized)
        ordered = sorted(materialized, key=self._science_sort_key)

        results = []
        index = 0
        while index < len(ordered):
            group_time = self._science_sort_key(ordered[index])[0]
            end = index + 1
            while (
                    end < len(ordered)
                    and self._science_sort_key(ordered[end])[0]
                    == group_time):
                end += 1
            if not self.run_start_ns <= group_time < self.run_end_ns:
                raise CausalContractError(
                    "row event time outside run")
            if (
                    self.last_row_time_ns is not None
                    and group_time < self.last_row_time_ns):
                raise CausalContractError(
                    "shared Postcoh group event time regression")
            self.last_row_time_ns = group_time

            # One authority is selected for every row/component in this exact
            # shared-time group. No member can see another member's support.
            self.advance_to(group_time)
            selected = self.authority
            group_results = []
            future = []
            for row in ordered[index:end]:
                row_results, row_future = self._process_science_row(
                    row, group_time, selected)
                group_results.extend(row_results)
                future.extend(row_future)
            results.extend(group_results)

            # Publish only after the complete cross-stream group finalizes.
            # A selected-authority failure is terminal and atomically cancels
            # every future-support write from this exact group, including
            # otherwise successful siblings.  Later time groups are not read.
            group_failed_bg = any(
                item["status"] == STATUS_FAILED_BG
                for item in group_results)
            if group_failed_bg:
                self.lifecycle["terminal_failed_bg_groups"] += 1
                self.lifecycle["support_cancelled_terminal"] += len(future)
                self.terminal_failed_bg_group_time_ns = group_time
                self.terminal_unprocessed_transport_rows = len(ordered) - end
                break
            self._commit_group_support(future)
            index = end
        return results

    def _process_science_row(
        self,
        row: dict[str, str],
        row_time: int,
        selected: Authority | None,
    ) -> tuple[list[dict], list[tuple[tuple, str, SupportRecord]]]:
        seq = _strict_uint(
            row.get("stream_seq"),
            "stream_seq", (1 << 63) - 1)
        source_stream = _strict_uint(
            row.get("source_stream_ordinal"),
            "source_stream_ordinal", (1 << 31) - 1)
        if source_stream not in self.source_stream_ids:
            raise CausalContractError(
                "science phase source is outside declared roster")
        buffer_ordinal = _strict_uint(
            row.get("buffer_ordinal"),
            "buffer_ordinal", (1 << 63) - 1)
        row_ordinal = _strict_uint(
            row.get("row_ordinal"),
            "row_ordinal", (1 << 63) - 1)
        if self._science_sort_key(row)[0] != row_time:
            raise CausalContractError(
                "shared Postcoh group key drift")

        flag = _strict_uint(
            row.get("is_background"),
            "is_background", 2)
        if flag != 0:
            self.lifecycle[
                "historical_background_excluded"] += 1
            return [], []

        self.lifecycle["seen_rows"] += 1
        _route, components = self._route_and_components(
            row, seq, source_stream,
            buffer_ordinal, row_ordinal, row_time)
        if not components:
            return [], []

        results = []
        future = []
        for component in components:
            result, support = self._evaluate(
                component, selected)
            if result is not None:
                results.append(result)
            if support is not None:
                ifo_rank = IFOS.index(component.ifo)
                future.append((
                    (
                        source_stream, buffer_ordinal,
                        row_ordinal, ifo_rank, component.event_id,
                    ),
                    component.ifo,
                    support,
                ))
        return results, future

    def _prune_support_before(self, low_ns: int) -> None:
        """Drop only records strictly older than the next half-open window."""
        if not isinstance(low_ns, int):
            raise CausalContractError("support prune boundary is not integer ns")
        removed = 0
        for ifo in IFOS:
            retained = [
                record for record in self.support[ifo]
                if record.gps_ns >= low_ns
            ]
            removed += len(self.support[ifo]) - len(retained)
            self.support[ifo] = retained
        self.lifecycle["support_pruned"] += removed

    def _candidate(
        self, epoch_ns: int
    ) -> tuple[dict, bytes, dict[str, AuthorityIFO]]:
        if (self.background_window_ns is None
                or self.update_period_ns is None):
            raise CausalContractError(
                "frozen mode cannot build a candidate")
        low_ns = epoch_ns - self.background_window_ns
        window_span = epoch_ns - low_ns
        authority_ifos: dict[str, AuthorityIFO] = {}
        backgrounds: dict[str, dict] = {}
        for ifo in IFOS:
            livetime = window_livetime_ns(
                self.intervals[ifo], low_ns, epoch_ns)
            if not (
                0 < livetime <= window_span
                and 5 * livetime > window_span
            ):
                raise CausalContractError(
                    f"{ifo}: occupancy not strictly "
                    "above 20 percent")
            if livetime >= (1 << 53):
                raise CausalContractError(
                    f"{ifo}: livetime not exact-convertible "
                    "for FAR")
            records = sorted(
                (
                    record for record in self.support[ifo]
                    if low_ns <= record.gps_ns < epoch_ns
                ),
                key=lambda record: (
                    record.llr, record.gps_ns, record.identity),
            )
            if len(records) < 2:
                raise CausalContractError(
                    f"{ifo}: fewer than two support records")
            if len(records) > _MAX_SUPPORT_POINTS:
                raise CausalContractError(
                    f"{ifo}: support point bound exceeded")
            ranks = tuple(record.llr for record in records)
            if len(set(ranks)) < 2:
                raise CausalContractError(
                    f"{ifo}: fewer than two unique "
                    "support ranks")
            livetime_seconds = (
                float(livetime) / 1_000_000_000.0)
            r_tail, slope, empirical, far_by_rank = (
                _empirical_tail_and_far_by_rank(
                    ranks, livetime_seconds, self.tail_log10_far))
            fit_count = sum(
                1 for rank, _log_far in empirical
                if rank >= r_tail)
            if fit_count < 2:
                raise CausalContractError(
                    f"{ifo}: invalid tail membership")
            far_llr_points = [
                {
                    "gps": segment_contract.ns_to_gps(record.gps_ns),
                    "llr": _hex(record.llr),
                    "far": _hex(far_by_rank[record.llr]),
                }
                for record in records
            ]
            backgrounds[ifo] = {
                "livetime": segment_contract.ns_to_gps(livetime),
                "support_count": len(records),
                "tail_fit": {
                    "method":
                        "anchored_ols_all_unique_ranks_ge_r_tail",
                    "r_tail": _hex(r_tail),
                    "slope": _hex(slope),
                    "fit_unique_rank_count": fit_count,
                },
                "far_llr_points": far_llr_points,
            }
            authority_ifos[ifo] = AuthorityIFO(
                ranks=ranks,
                livetime_ns=livetime,
                r_tail=r_tail,
                slope=slope,
                support_count=len(records))
        candidate = {
            "schema_version": 4,
            "background_kind": "no_injection",
            "run_namespace_sha256":
                self.run_namespace_sha256,
            "source_manifest_sha256":
                self.source_manifest_sha256,
            "runtime_manifest_sha256":
                self.runtime_manifest_sha256,
            "config_sha256": self.config_sha256,
            "segment_xml_sha256":
                self.segment_xml_sha256,
            "segment_canonical_sha256":
                self.segment_canonical_sha256,
            "template_shape_map_sha256":
                self.shape_source_sha256,
            "worker_id": self.worker_id,
            "worker_count": self.worker_count,
            "worker_bank_ids": list(self.bank_ids),
            "accepted_version": self.accepted_version + 1,
            "epoch_gps":
                segment_contract.ns_to_gps(epoch_ns),
            "window_start_gps":
                segment_contract.ns_to_gps(low_ns),
            "window_end_gps":
                segment_contract.ns_to_gps(epoch_ns),
            "window_duration": segment_contract.ns_to_gps(
                self.background_window_ns),
            "update_period": segment_contract.ns_to_gps(
                self.update_period_ns),
            "far_floor_count": 1,
            "tail_log10_far": self.tail_log10_far,
            "backgrounds": backgrounds,
        }
        payload = (
            json.dumps(
                candidate, ensure_ascii=True,
                separators=(",", ":"))
            + "\n"
        ).encode("ascii")
        return candidate, payload, authority_ifos

    def _publish_candidate(self, epoch_ns: int) -> None:
        if self.background_window_ns is None:
            raise CausalContractError("live publish lost background window")
        self._prune_support_before(epoch_ns - self.background_window_ns)
        try:
            _candidate, payload, authority_ifos = (
                self._candidate(epoch_ns))
        except Exception as exc:
            self.lifecycle["candidate_rejected"] += 1
            self.candidate_rejections.append({
                "epoch_ns": epoch_ns,
                "reason":
                    f"{type(exc).__name__}:{exc}",
            })
            return
        native_sha = hashlib.sha256(payload).hexdigest()
        if self.background_path is None:
            raise CausalContractError(
                "live background output vanished")
        _write_atomic_readonly(
            self.background_path, payload)
        published = _snapshot_regular_file_bytes(
            self.background_path,
            "published sidecar background")
        if (hashlib.sha256(published).hexdigest()
                != native_sha):
            raise CausalContractError(
                "published background hash drift")
        self.accepted_version += 1
        self.authority = Authority(
            version=self.accepted_version,
            epoch_ns=epoch_ns,
            native_sha256=native_sha,
            by_ifo=authority_ifos,
        )
        self.lifecycle["candidate_accepted"] += 1

    def _load_frozen_authority(
        self,
        path: str,
        expected_sha256: str,
        expected_run_namespace_sha256: str,
    ) -> Authority:
        value, _payload, native_sha = (
            _decode_canonical_json(
                path, "frozen sidecar background"))
        if native_sha != _strict_sha(
                expected_sha256,
                "expected_frozen_background_sha256"):
            raise CausalContractError(
                "frozen sidecar background SHA mismatch")
        root_keys = [
            "schema_version", "background_kind",
            "run_namespace_sha256",
            "source_manifest_sha256",
            "runtime_manifest_sha256", "config_sha256",
            "segment_xml_sha256",
            "segment_canonical_sha256",
            "template_shape_map_sha256", "worker_id",
            "worker_count", "worker_bank_ids", "accepted_version",
            "epoch_gps", "window_start_gps",
            "window_end_gps", "window_duration",
            "update_period", "far_floor_count",
            "tail_log10_far", "backgrounds",
        ]
        if list(value) != root_keys:
            raise CausalContractError(
                "frozen background root schema/order drift")
        if (value["schema_version"] != 4
                or value["background_kind"]
                != "no_injection"):
            raise CausalContractError(
                "frozen background kind/version drift")
        frozen_namespace = _strict_sha(
            value["run_namespace_sha256"],
            "frozen.run_namespace_sha256")
        if frozen_namespace != _strict_sha(
                expected_run_namespace_sha256,
                "expected_frozen_run_namespace_sha256"):
            raise CausalContractError(
                "frozen run namespace mismatch")
        for field in (
            "source_manifest_sha256",
            "runtime_manifest_sha256",
            "config_sha256",
            "segment_xml_sha256",
            "segment_canonical_sha256",
            "template_shape_map_sha256",
        ):
            _strict_sha(value[field], f"frozen.{field}")
        if (value["template_shape_map_sha256"]
                != self.shape_source_sha256):
            raise CausalContractError(
                "frozen shape source mismatch")
        if (
            _strict_uint(
                value["worker_id"], "frozen.worker_id", 4095)
            != self.worker_id
            or _strict_uint(
                value["worker_count"],
                "frozen.worker_count", 4096)
            != self.worker_count
        ):
            raise CausalContractError(
                "frozen worker mapping mismatch")
        frozen_banks = tuple(
            _strict_uint(
                bank, "frozen.worker_bank_id", 383)
            for bank in value["worker_bank_ids"])
        if (frozen_banks != self.bank_ids
                or frozen_banks
                != tuple(sorted(set(frozen_banks)))):
            raise CausalContractError(
                "frozen bank mapping mismatch")
        version = _strict_uint(
            value["accepted_version"],
            "frozen.accepted_version", (1 << 63) - 1)
        if version < 1:
            raise CausalContractError(
                "frozen background has no accepted version")
        epoch_ns = _gps_object_to_ns(
            value["epoch_gps"], "frozen.epoch_gps")
        window_start = _gps_object_to_ns(
            value["window_start_gps"],
            "frozen.window_start_gps")
        window_end = _gps_object_to_ns(
            value["window_end_gps"],
            "frozen.window_end_gps")
        window_duration = _gps_object_to_ns(
            value["window_duration"],
            "frozen.window_duration")
        update_period = _gps_object_to_ns(
            value["update_period"],
            "frozen.update_period")
        if (window_end != epoch_ns
                or window_start + window_duration != window_end
                or window_duration <= 0
                or update_period <= 0):
            raise CausalContractError(
                "frozen window/epoch relation drift")
        if value["far_floor_count"] != 1:
            raise CausalContractError(
                "frozen one-count floor drift")
        frozen_tail = value["tail_log10_far"]
        if (type(frozen_tail) not in (int, float)
                or isinstance(frozen_tail, bool)
                or not math.isfinite(float(frozen_tail))
                or float(frozen_tail) != self.tail_log10_far):
            raise CausalContractError(
                "frozen tail anchor drift")
        if list(value["backgrounds"]) != ["H1", "L1"]:
            raise CausalContractError(
                "frozen background IFO order drift")

        authority_ifos = {}
        total_support_count = 0
        for ifo in IFOS:
            item = value["backgrounds"][ifo]
            if not isinstance(item, dict) or list(item) != [
                "livetime", "support_count",
                "tail_fit", "far_llr_points",
            ]:
                raise CausalContractError(
                    f"frozen {ifo} schema/order drift")
            livetime_ns = _gps_object_to_ns(
                item["livetime"], f"frozen.{ifo}.livetime")
            support_count = _strict_uint(
                item["support_count"],
                f"frozen.{ifo}.support_count",
                _MAX_SUPPORT_POINTS)
            points = item["far_llr_points"]
            if (not isinstance(points, list)
                    or len(points) != support_count):
                raise CausalContractError(
                    f"frozen {ifo} point count drift")
            total_support_count += support_count
            if total_support_count > 2 * _MAX_SUPPORT_POINTS:
                raise CausalContractError(
                    "frozen total support point bound exceeded")
            if (not 0 < livetime_ns < (1 << 53)
                    or support_count < 2
                    or 5 * livetime_ns <= window_duration):
                raise CausalContractError(
                    f"frozen {ifo} support/livetime/occupancy invalid")
            ranks = []
            stored_fars = []
            previous_key = None
            for index, point in enumerate(points):
                if (not isinstance(point, dict)
                        or list(point) != ["gps", "llr", "far"]):
                    raise CausalContractError(
                        f"frozen {ifo} point schema drift")
                gps_ns = _gps_object_to_ns(
                    point["gps"],
                    f"frozen.{ifo}.point[{index}].gps")
                if not window_start <= gps_ns < window_end:
                    raise CausalContractError(
                        f"frozen {ifo} point GPS outside window")
                rank = _strict_hex_float(
                    point["llr"],
                    f"frozen.{ifo}.rank[{index}]")
                far = _strict_hex_float(
                    point["far"],
                    f"frozen.{ifo}.far[{index}]")
                if far <= 0.0:
                    raise CausalContractError(
                        f"frozen {ifo} nonpositive FAR")
                key = (rank, gps_ns, struct.pack(">d", far))
                if previous_key is not None and key < previous_key:
                    raise CausalContractError(
                        f"frozen {ifo} points not canonical sorted")
                ranks.append(rank)
                stored_fars.append(far)
                previous_key = key
            livetime_seconds = (
                float(livetime_ns) / 1_000_000_000.0)
            expected_tail, expected_slope, empirical, far_by_rank = (
                _empirical_tail_and_far_by_rank(
                    ranks, livetime_seconds, self.tail_log10_far))
            for index, (rank, stored_far) in enumerate(
                    zip(ranks, stored_fars)):
                expected_far = far_by_rank[rank]
                if stored_far.hex() != expected_far.hex():
                    raise CausalContractError(
                        f"frozen {ifo} Calculated FAR drift "
                        f"at point {index}")
            tail = item["tail_fit"]
            if not isinstance(tail, dict) or list(tail) != [
                "method", "r_tail", "slope",
                "fit_unique_rank_count",
            ]:
                raise CausalContractError(
                    f"frozen {ifo} tail schema drift")
            if tail["method"] != (
                    "anchored_ols_all_unique_ranks_ge_r_tail"):
                raise CausalContractError(
                    f"frozen {ifo} tail method drift")
            r_tail = _strict_hex_float(
                tail["r_tail"],
                f"frozen.{ifo}.r_tail")
            slope = _strict_hex_float(
                tail["slope"],
                f"frozen.{ifo}.slope")
            if slope >= 0.0:
                raise CausalContractError(
                    f"frozen {ifo} nonnegative slope")
            fit_count = sum(
                1 for rank, _far in empirical
                if rank >= expected_tail)
            if (
                r_tail.hex() != expected_tail.hex()
                or slope.hex() != expected_slope.hex()
                or _strict_uint(
                    tail["fit_unique_rank_count"],
                    f"frozen.{ifo}.fit_count",
                    (1 << 63) - 1) != fit_count
            ):
                raise CausalContractError(
                    f"frozen {ifo} tail numeric drift")
            authority_ifos[ifo] = AuthorityIFO(
                ranks=tuple(ranks),
                livetime_ns=livetime_ns,
                r_tail=r_tail,
                slope=slope,
                support_count=support_count)
        return Authority(
            version=version,
            epoch_ns=epoch_ns,
            native_sha256=native_sha,
            by_ifo=authority_ifos,
        )

    def advance_to(self, row_event_ns: int) -> None:
        if self.mode == MODE_FROZEN_ASSIGNMENT_ONLY:
            return
        if row_event_ns < self.run_start_ns:
            return
        limit = min(row_event_ns, self.run_end_ns)
        while self.next_epoch_ns <= limit:
            self._publish_candidate(
                self.next_epoch_ns)
            self.next_epoch_ns += self.update_period_ns

    def finalize(self) -> dict:
        if (
            self.mode != MODE_FROZEN_ASSIGNMENT_ONLY
            and self.terminal_failed_bg_group_time_ns is None
        ):
            self.advance_to(self.run_end_ns)
        expected = (
            self.lifecycle["threshold_excluded"]
            + self.lifecycle["failed"]
            + self.lifecycle["pending"]
            + self.lifecycle["bg_only"]
            + self.lifecycle["assigned"]
            + self.lifecycle["multi_owned_llr_only"]
        )
        if expected != self.lifecycle["seen_active_HL"]:
            raise CausalContractError(
                "lifecycle equation failed: active H/L "
                "components unclassified")
        if self.lifecycle["failed"] != (
                self.lifecycle["failed_input"]
                + self.lifecycle["failed_llr"]
                + self.lifecycle["failed_bg"]
                + self.lifecycle["unsupported"]):
            raise CausalContractError(
                "failure subtype lifecycle equation failed")
        if self.lifecycle["failed_bg"] > 0:
            if (
                self.lifecycle["terminal_failed_bg_groups"] != 1
                or self.terminal_failed_bg_group_time_ns is None
            ):
                raise CausalContractError(
                    "FAILED_BG terminal-group equation failed")
        elif (
            self.lifecycle["terminal_failed_bg_groups"] != 0
            or self.terminal_failed_bg_group_time_ns is not None
            or self.lifecycle["support_cancelled_terminal"] != 0
            or self.terminal_unprocessed_transport_rows != 0
        ):
            raise CausalContractError(
                "nonterminal run contains terminal state")
        if self.mode == MODE_FROZEN_ASSIGNMENT_ONLY:
            if (
                not self.frozen_authority_loaded
                or self.lifecycle["support_candidates"] != 0
                or self.lifecycle["support_appended"] != 0
                or self.lifecycle["candidate_accepted"] != 0
            ):
                raise CausalContractError(
                    "frozen mode mutation/load equation failed")
        elif (
                self.lifecycle["support_candidates"]
                != self.lifecycle["support_appended"]
                + self.lifecycle["support_cancelled_terminal"]):
            raise CausalContractError(
                "valid LLR support commit/cancel equation failed")
        return {
            "schema_version": 3,
            "mode": self.mode,
            "worker_id": self.worker_id,
            "worker_count": self.worker_count,
            "worker_group": self.worker_group,
            "bank_ids": list(self.bank_ids),
            "source_stream_bank_map": [
                {
                    "source_stream_ordinal": stream,
                    "bankid": bank,
                }
                for stream, bank in self.source_stream_bank_pairs
            ],
            "declared_source_stream_ids":
                list(self.source_stream_ids),
            "observed_source_stream_ids":
                sorted(self.seen_source_streams),
            "source_stream_contract":
                "producer_binding_and_full_bank_bijection_required",
            "status_to_crashcar_code":
                dict(STATUS_TO_CRASHCAR_CODE),
            "lifecycle": dict(self.lifecycle),
            "accepted_version": self.accepted_version,
            "new_authority_publications":
                self.lifecycle["candidate_accepted"],
            "frozen_authority_loaded":
                self.frozen_authority_loaded,
            "terminal_failed_bg_group_time_ns":
                self.terminal_failed_bg_group_time_ns,
            "terminal_unprocessed_transport_rows":
                self.terminal_unprocessed_transport_rows,
            "authority_epoch_ns": (
                self.authority.epoch_ns
                if self.authority else None),
            "authority_native_sha256": (
                self.authority.native_sha256
                if self.authority else None),
            "candidate_rejections":
                list(self.candidate_rejections),
        }
