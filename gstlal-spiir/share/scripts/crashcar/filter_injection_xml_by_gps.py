#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--gps-start", type=int, required=True)
    parser.add_argument("--gps-end", type=int, required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()

    text = gzip.open(args.input, "rt").read()
    output: list[str] = []
    in_sim = False
    in_stream = False
    columns: list[str] = []
    geoidx = None
    kept = 0
    total = 0

    for line in text.splitlines(True):
        if '<Table Name="sim_inspiral:table"' in line:
            in_sim = True
            columns = []
            geoidx = None
            output.append(line)
            continue
        if in_sim and "<Column Name=" in line:
            match = re.search(r'Name="(?:sim_inspiral:)?([^"]+)"', line)
            if match:
                columns.append(match.group(1))
                if match.group(1) == "geocent_end_time":
                    geoidx = len(columns) - 1
            output.append(line)
            continue
        if in_sim and "<Stream " in line:
            in_stream = True
            output.append(line)
            continue
        if in_sim and "</Stream>" in line:
            in_stream = False
            output.append(line)
            continue
        if in_sim and "</Table>" in line:
            in_sim = False
            output.append(line)
            continue
        if in_sim and in_stream:
            stripped = line.strip()
            if not stripped:
                output.append(line)
                continue
            row = next(csv.reader([stripped]))
            total += 1
            try:
                gps = int(float(row[geoidx])) if geoidx is not None else -1
            except Exception:
                gps = -1
            if args.gps_start <= gps < args.gps_end:
                output.append(line)
                kept += 1
            continue
        output.append(line)

    with gzip.open(args.output, "wt") as handle:
        handle.write("".join(output))
    Path(args.summary).write_text(
        json.dumps(
            {
                "input": args.input,
                "output": args.output,
                "gps_start": args.gps_start,
                "gps_end": args.gps_end,
                "total_sim_rows": total,
                "kept_sim_rows": kept,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
