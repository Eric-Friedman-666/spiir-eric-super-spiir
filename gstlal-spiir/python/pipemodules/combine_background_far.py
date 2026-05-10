#
# Final FAR-plane combiner for the proposed SPIIR single-detector workflow.
#
# This file is intentionally the last offline step.  It converts the coherent
# zerolag postcoh rows into the same CSV convention as the single-detector
# branch, and it can merge both branches into one final (rho, FAR) table.

from __future__ import division
from __future__ import print_function

import csv
import sys

from single_detector_far import (
    FLAG_FOREGROUND,
    PLOT_ROW_FIELDS,
    expand_path_patterns,
    load_postcoh_table,
    neg_log10_far,
    write_plot_rows_csv,
)


def coherent_rows_from_postcoh_files(postcoh_filenames):
    rows = []
    for filename in postcoh_filenames:
        xmldoc, table = load_postcoh_table(filename)
        try:
            for row in table:
                if getattr(row, "is_background", None) != FLAG_FOREGROUND:
                    continue

                far = float(getattr(row, "far", 0.0) or 0.0)
                rows.append({
                    "category": "%s_coh" % getattr(row, "ifos", "coh"),
                    "ifo": "",
                    "rho": float(getattr(row, "cohsnr", 0.0) or 0.0),
                    "chisq": float(getattr(row, "cmbchisq", 0.0) or 0.0),
                    "rank": float(getattr(row, "rank", 0.0) or 0.0),
                    "far": far,
                    "neg_log10_far": neg_log10_far(far),
                    "tmplt_idx": getattr(row, "tmplt_idx", None),
                    "bankid": getattr(row, "bankid", None),
                    "end_time": getattr(row, "end_time", None),
                    "end_time_ns": getattr(row, "end_time_ns", None),
                })
        finally:
            xmldoc.unlink()
    return rows


def open_csv_for_read(filename):
    if sys.version_info[0] >= 3:
        return open(filename, "r", newline="")
    return open(filename, "rb")


def read_plot_rows_csv(filename):
    with open_csv_for_read(filename) as input_file:
        return list(csv.DictReader(input_file))


def combine_far_plane_rows(single_csv=None,
                           multi_csv=None,
                           multi_postcoh_glob=None):
    rows = []

    if single_csv:
        rows.extend(read_plot_rows_csv(single_csv))

    if multi_csv:
        rows.extend(read_plot_rows_csv(multi_csv))

    if multi_postcoh_glob:
        multi_files = expand_path_patterns(multi_postcoh_glob)
        if not multi_files:
            raise ValueError("no coherent postcoh files matched the input")
        rows.extend(coherent_rows_from_postcoh_files(multi_files))

    return rows


def command_combine(args):
    rows = combine_far_plane_rows(
        single_csv=args.single_csv,
        multi_csv=args.multi_csv,
        multi_postcoh_glob=args.multi_postcoh_glob)
    write_plot_rows_csv(rows, args.output)
    print("wrote %d FAR-plane rows to %s" % (len(rows), args.output))


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Combine coherent and single-detector SPIIR FAR-plane rows")
    parser.add_argument("--single-csv")
    parser.add_argument("--multi-csv")
    parser.add_argument(
        "--multi-postcoh-glob", action="append",
        help="coherent zerolag XML/XML.GZ filename or glob; may be repeated")
    parser.add_argument("--output", required=True)
    parser.set_defaults(func=command_combine)
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
