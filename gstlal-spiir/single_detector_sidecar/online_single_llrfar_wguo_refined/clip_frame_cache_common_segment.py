#!/usr/bin/env python3
"""Clip archived-frame replay starts to the first common detector segment."""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True)
    parser.add_argument("--start", required=True, type=float)
    parser.add_argument("--end", required=True, type=float)
    parser.add_argument("--required-ifos", default="H,L")
    parser.add_argument(
        "--mode",
        choices=("preserve-duration", "clip-end"),
        default="preserve-duration",
    )
    parser.add_argument("--online-replay-start-gps", default="")
    parser.add_argument("--shell", action="store_true")
    return parser.parse_args()


def normalize_ifo(value: str) -> str:
    value = value.strip().upper()
    if value in ("H", "H1"):
        return "H"
    if value in ("L", "L1"):
        return "L"
    return value


def read_intervals(cache_path: Path) -> dict[str, list[tuple[int, int]]]:
    intervals: dict[str, list[tuple[int, int]]] = {}
    for raw_line in cache_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        ifo = normalize_ifo(parts[0])
        try:
            start = int(float(parts[2]))
            duration = int(float(parts[3]))
        except ValueError:
            continue
        if duration <= 0:
            continue
        intervals.setdefault(ifo, []).append((start, start + duration))
    for ifo in intervals:
        intervals[ifo].sort()
    return intervals


def covers(intervals: list[tuple[int, int]], gps: int) -> bool:
    return any(start <= gps < end for start, end in intervals)


def earliest_common_start(intervals_by_ifo: dict[str, list[tuple[int, int]]],
                          required_ifos: list[str],
                          requested_start: int) -> int:
    missing = [ifo for ifo in required_ifos if ifo not in intervals_by_ifo]
    if missing:
        raise SystemExit(
            "frame cache has no intervals for required IFO(s): %s"
            % ",".join(missing)
        )
    candidates = {requested_start}
    for ifo in required_ifos:
        for start, end in intervals_by_ifo[ifo]:
            if end > requested_start:
                candidates.add(max(start, requested_start))
    for candidate in sorted(candidates):
        if all(covers(intervals_by_ifo[ifo], candidate) for ifo in required_ifos):
            return candidate
    raise SystemExit(
        "frame cache has no common start for %s at or after GPS %d"
        % (",".join(required_ifos), requested_start)
    )


def quote_shell(name: str, value) -> str:
    return "export %s=%s" % (name, shlex.quote(str(value)))


def main() -> int:
    args = parse_args()
    requested_start = int(args.start)
    requested_end = int(args.end)
    if requested_end <= requested_start:
        raise SystemExit("requested end must be greater than start")
    required_ifos = [
        normalize_ifo(item)
        for item in args.required_ifos.split(",")
        if item.strip()
    ]
    intervals_by_ifo = read_intervals(Path(args.cache))
    clipped_start = earliest_common_start(
        intervals_by_ifo, required_ifos, requested_start)
    requested_duration = requested_end - requested_start
    if args.mode == "preserve-duration":
        clipped_end = clipped_start + requested_duration
    else:
        clipped_end = requested_end
        if clipped_end <= clipped_start:
            raise SystemExit(
                "common start GPS %d is not before requested end GPS %d"
                % (clipped_start, requested_end)
            )
    applied = clipped_start != requested_start or clipped_end != requested_end
    replay_start = args.online_replay_start_gps
    if replay_start in ("", str(requested_start), str(float(requested_start))):
        replay_start = str(clipped_start)

    values = {
        "FRAME_CACHE_COMMON_CLIP_APPLIED": 1 if applied else 0,
        "FRAME_CACHE_COMMON_CLIP_ORIGINAL_START": requested_start,
        "FRAME_CACHE_COMMON_CLIP_ORIGINAL_END": requested_end,
        "FRAME_CACHE_COMMON_CLIP_REQUIRED_IFOS": ",".join(required_ifos),
        "DATA_START_TIME": clipped_start,
        "DATA_END_TIME": clipped_end,
        "MAX_DATA_DURATION_SECONDS": clipped_end - clipped_start,
        "ONLINE_REPLAY_START_GPS": replay_start,
    }
    if args.shell:
        for name, value in values.items():
            print(quote_shell(name, value))
    else:
        for name, value in values.items():
            print("%s=%s" % (name, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
