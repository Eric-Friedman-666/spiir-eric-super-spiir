#!/usr/bin/env python3
"""Dump LIGO-LW segment XML/XML.GZ to a simple ifo,start,end CSV."""

import argparse
import csv
import gzip


def open_text_maybe_gzip(filename):
    if str(filename).endswith(".gz"):
        return gzip.open(filename, "rt")
    return open(filename, "r")


def segment_def_id_suffix(value):
    text = str(value or "").strip().strip('"').strip("'")
    return text.rsplit(":", 1)[-1] if text else None


def load_ligolw_segments(filename):
    definer_by_id = {}
    segments = {}
    stream = None
    with open_text_maybe_gzip(filename) as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if '<Stream' in stripped and 'Name="segment_definer:table"' in stripped:
                stream = "segment_definer"
                continue
            if '<Stream' in stripped and 'Name="segment:table"' in stripped:
                stream = "segment"
                continue
            if stream and stripped.startswith("</Stream>"):
                stream = None
                continue
            if stream not in ("segment_definer", "segment"):
                continue
            if stripped.startswith("<"):
                continue
            row = next(csv.reader([stripped]))
            if stream == "segment_definer":
                if len(row) >= 5:
                    ifo = row[1].strip().strip('"').strip("'")
                    definer_by_id[segment_def_id_suffix(row[4])] = ifo
                    segments.setdefault(ifo, [])
                continue
            if len(row) < 7:
                continue
            ifo = definer_by_id.get(segment_def_id_suffix(row[3]))
            if not ifo:
                continue
            try:
                end = float(row[0]) + float(row[1] or 0.0) * 1.0e-9
                start = float(row[5]) + float(row[6] or 0.0) * 1.0e-9
            except ValueError:
                continue
            if end > start:
                segments.setdefault(ifo, []).append((start, end))
    return segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segment_xml", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    rows = []
    for filename in args.segment_xml:
        for ifo, segments in load_ligolw_segments(filename).items():
            for start, end in segments:
                rows.append((ifo, start, end))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))

    with open(args.output, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ifo", "start", "end"])
        writer.writerows(rows)
    print("wrote %d segments to %s" % (len(rows), args.output))


if __name__ == "__main__":
    main()
