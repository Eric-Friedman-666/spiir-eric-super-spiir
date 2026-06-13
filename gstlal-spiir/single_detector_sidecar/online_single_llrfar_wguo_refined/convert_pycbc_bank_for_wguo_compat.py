#!/usr/bin/env python3
"""Create WGuo-compatible copies of current PYCBC split bank XML files.

The WGuo reference build uses a ligo.lw table schema where sngl_inspiral
event_id and process_id are int_8s fields.  The current bank files keep those
fields as ilwd:char strings.  Its older C bank parser also expects the
sngl_inspiral table columns to use bare names, and its historical O3b bank
files are plain XML even when the filename ends in .xml.gz.  This script
rewrites only that compatibility surface in isolated copies.
"""

from __future__ import annotations

import argparse
import gzip
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-bank", type=int, default=0)
    parser.add_argument("--end-bank", type=int, required=True)
    parser.add_argument("--ifos", default="H1,L1")
    return parser.parse_args()


def read_text(path: Path) -> str:
    with path.open("rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        with gzip.open(path, "rt") as input_file:
            return input_file.read()
    return path.read_text()


def write_gzip_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", compresslevel=6) as output_file:
        output_file.write(text)


def write_plain_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def convert_text(text: str) -> tuple[str, dict[str, int]]:
    text, event_column_count = re.subn(
        r'Name="sngl_inspiral:event_id" Type="ilwd:char"',
        'Name="sngl_inspiral:event_id" Type="int_8s"',
        text,
    )
    text, process_column_count = re.subn(
        r'Name="sngl_inspiral:process_id" Type="ilwd:char"',
        'Name="process:process_id" Type="int_8s"',
        text,
    )
    text, event_value_count = re.subn(
        r'"sngl_inspiral:event_id:(\d+)"',
        r"\1",
        text,
    )
    text, process_value_count = re.subn(
        r'"process:process_id:(\d+)"',
        r"\1",
        text,
    )
    text, stripped_column_count = re.subn(
        r'(<Column\s+Name=")sngl_inspiral:([^"]+")',
        r"\1\2",
        text,
    )
    return text, {
        "event_column": event_column_count,
        "process_column": process_column_count,
        "event_values": event_value_count,
        "process_values": process_value_count,
        "stripped_sngl_inspiral_column_prefixes": stripped_column_count,
    }


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    ifos = [ifo.strip() for ifo in args.ifos.split(",") if ifo.strip()]

    for bank in range(args.start_bank, args.end_bank + 1):
        bank_id = f"{bank:04d}"
        for ifo in ifos:
            name = f"iir_{ifo}-PYCBC_SPLIT_BANK_{bank_id}-a1-0-0.xml.gz"
            input_path = input_dir / name
            output_path = output_dir / name
            if not input_path.exists():
                raise FileNotFoundError(input_path)
            converted, counts = convert_text(read_text(input_path))
            if counts["event_column"] != 1 or counts["process_column"] != 1:
                raise RuntimeError(f"unexpected column conversion counts for {input_path}: {counts}")
            write_plain_text(output_path, converted)
            print(f"{output_path} {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
