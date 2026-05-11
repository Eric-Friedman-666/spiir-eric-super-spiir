#
# Final FAR-plane combiner for the proposed SPIIR single-detector workflow.
#
# This file is intentionally the last offline step.  It converts the coherent
# zerolag postcoh rows into the same CSV convention as the single-detector
# branch, and it can merge both branches into one final (rho, FAR) table.

from __future__ import division
from __future__ import print_function

import csv
import math
import os
import sys

from single_detector_far import (
    FLAG_FOREGROUND,
    PLOT_ROW_FIELDS,
    expand_path_patterns,
    is_finite_number,
    load_postcoh_table,
    neg_log10_far,
    write_plot_rows_csv,
)


def coherent_rows_from_postcoh_files(postcoh_filenames):
    rows = []
    for filename in postcoh_filenames:
        xmldoc, table = load_postcoh_table(filename)
        try:
            for row_number, row in enumerate(table, start=1):
                if getattr(row, "is_background", None) != FLAG_FOREGROUND:
                    continue

                source = "%s row %d" % (filename, row_number)
                far = parse_positive_finite_float(
                    getattr(row, "far", None), "far", source)
                rho = parse_finite_float(
                    getattr(row, "cohsnr", 0.0), "cohsnr", source)
                chisq = parse_finite_float(
                    getattr(row, "cmbchisq", 0.0), "cmbchisq", source)
                rank = parse_finite_float(
                    getattr(row, "rank", 0.0), "rank", source)
                rows.append({
                    "category": "%s_coh" % getattr(row, "ifos", "coh"),
                    "ifo": "",
                    "rho": rho,
                    "chisq": chisq,
                    "rank": rank,
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
    if not os.path.exists(filename):
        raise ValueError("FAR-plane CSV input does not exist: %s" % filename)
    if sys.version_info[0] >= 3:
        return open(filename, "r", newline="")
    return open(filename, "rb")


def read_plot_rows_csv(filename):
    with open_csv_for_read(filename) as input_file:
        reader = csv.DictReader(input_file)
        missing_fields = [
            field for field in PLOT_ROW_FIELDS
            if field not in (reader.fieldnames or [])
        ]
        if missing_fields:
            raise ValueError(
                "FAR-plane CSV %s is missing required columns: %s" %
                (filename, ", ".join(missing_fields)))
        return [
            validate_plot_row(row, filename, row_number)
            for row_number, row in enumerate(reader, start=2)
        ]


def parse_finite_float(value, field, source):
    if value in (None, ""):
        raise ValueError("%s is missing %s" % (source, field))
    if not is_finite_number(value):
        raise ValueError("%s has non-finite %s=%r" %
                         (source, field, value))
    return float(value)


def parse_positive_finite_float(value, field, source):
    value = parse_finite_float(value, field, source)
    if value <= 0.0:
        raise ValueError("%s must have positive %s, got %r" %
                         (source, field, value))
    return value


def validate_plot_row(row, filename, row_number):
    source = "%s row %d" % (filename, row_number)
    category = row.get("category")
    if category in (None, ""):
        raise ValueError("%s is missing category" % source)

    for field in ("rho", "chisq", "rank"):
        row[field] = parse_finite_float(row.get(field), field, source)

    far = parse_positive_finite_float(row.get("far"), "far", source)
    neg_far = parse_finite_float(
        row.get("neg_log10_far"), "neg_log10_far", source)
    expected_neg_far = -math.log10(far)
    tolerance = max(1.0e-9, abs(expected_neg_far) * 1.0e-9)
    if abs(neg_far - expected_neg_far) > tolerance:
        raise ValueError(
            "%s has neg_log10_far=%r inconsistent with far=%r" %
            (source, neg_far, far))
    row["far"] = far
    row["neg_log10_far"] = neg_far
    return row


def combine_far_plane_rows(single_csv=None,
                           multi_csv=None,
                           multi_postcoh_glob=None,
                           mode="auto"):
    validate_mode_inputs(mode, single_csv, multi_csv, multi_postcoh_glob)
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


def validate_mode_inputs(mode, single_csv, multi_csv, multi_postcoh_glob):
    mode = mode or "auto"
    if mode not in ("auto", "single", "multi", "combined"):
        raise ValueError("unsupported combine mode %r" % mode)

    have_single = bool(single_csv)
    have_coherent = bool(multi_csv or multi_postcoh_glob)
    if mode == "auto":
        if not have_single and not have_coherent:
            raise ValueError("at least one FAR-plane input is required")
        return
    if mode == "single":
        if not have_single:
            raise ValueError("single combine mode requires --single-csv")
        return
    if mode == "multi":
        if not have_coherent:
            raise ValueError(
                "multi combine mode requires --multi-csv or "
                "--multi-postcoh-glob")
        return
    if mode == "combined":
        if not have_single:
            raise ValueError("combined mode requires --single-csv")
        if not have_coherent:
            raise ValueError(
                "combined mode requires --multi-csv or --multi-postcoh-glob")


def command_combine(args):
    rows = combine_far_plane_rows(
        single_csv=args.single_csv,
        multi_csv=args.multi_csv,
        multi_postcoh_glob=args.multi_postcoh_glob,
        mode=args.mode)
    write_plot_rows_csv(rows, args.output)
    print("wrote %d FAR-plane rows to %s" % (len(rows), args.output))


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Combine coherent and single-detector SPIIR FAR-plane rows")
    parser.add_argument("--single-csv")
    parser.add_argument("--multi-csv")
    parser.add_argument(
        "--mode", choices=("auto", "single", "multi", "combined"),
        default="auto",
        help="input contract for missing coherent/single FAR-plane outputs")
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
