# Single-detector ranking and FAR utilities for the post-cuda_postcoh branch.
#
# This module is intentionally independent of the current coherent cohfar
# elements. It starts from postcoh rows that already contain per-detector
# snglsnr[j] and chisq[j], then builds the single-detector rank and FAR. The
# final coherent/single merge is handled by combine_background_far.py.

from __future__ import division
from __future__ import print_function

import bisect
import csv
import datetime
import glob
import gzip
import json
import math
import os
import pickle
import sys
import time

try:
    from gstlal.pipemodules import pipe_macro
except ImportError:
    try:
        import pipe_macro
    except ImportError:
        class _PipeMacroFallback(object):
            IFO_MAP = ("H1", "L1", "V1", "K1")

        pipe_macro = _PipeMacroFallback()

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


FLAG_FOREGROUND = 0
FLAG_BACKGROUND = 1
FLAG_EMPTY = 2

LOG_ZERO = -1.0e300

FAR_SOURCE_FIT_LOOKUP = "fit_lookup"
FAR_SOURCE_BOOTSTRAP_DIRECT = "bootstrap_direct"
FAR_SOURCE_DIRECT_FALLBACK = "direct_fallback"
FAR_SOURCE_DIRECT_EMPIRICAL = "direct_empirical_count"
FAR_SOURCE_UNASSIGNED = "unassigned_no_livetime"


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Single-detector FAR calculation for SPIIR postcoh rows")
    subparsers = parser.add_subparsers(dest="command")

    single = subparsers.add_parser(
        "single", help="calculate single-detector rank and FAR from raw postcoh")
    add_single_analysis_arguments(single)
    single.set_defaults(func=command_single)

    feature_csv = subparsers.add_parser(
        "feature-csv",
        help=("calculate single-detector LLR/FAR from an existing detector-local "
              "feature CSV"))
    add_feature_csv_arguments(feature_csv)
    feature_csv.set_defaults(func=command_feature_csv)

    watch = subparsers.add_parser(
        "watch",
        help="watch postcoh snapshots and update single-detector FAR online")
    add_single_analysis_arguments(watch)
    watch.add_argument(
        "--state-file",
        help="JSON state file used to remember processed postcoh snapshots")
    watch.add_argument(
        "--stop-file",
        help="when this file appears, flush all remaining snapshots and exit")
    watch.add_argument("--poll-interval", type=float, default=10.0)
    watch.add_argument("--stable-age", type=float, default=5.0)
    watch.add_argument(
        "--final-flush-timeout", type=float, default=300.0,
        help="seconds to keep retrying remaining snapshots after stop-file appears")
    watch.set_defaults(func=command_watch)
    return parser


def add_single_analysis_arguments(single):
    single.add_argument(
        "--postcoh-glob", action="append", required=True,
        help="raw postcoh XML/XML.GZ filename or glob; may be repeated")
    single.add_argument("--output", required=True)
    single.add_argument("--ifos", default="H1,L1")
    single.add_argument("--min-snr", type=float, default=4.0)
    single.add_argument("--livetime-step", type=float, default=1.0)
    single.add_argument(
        "--background-input",
        help="single-detector FAR-LLR background JSON file to load before assigning FAR")
    single.add_argument(
        "--background-output",
        help="single-detector FAR-LLR background JSON file to write after the scan")
    single.add_argument(
        "--calibrate-noise-dof", action="store_true",
        help="estimate nu_eff from current background rows before ranking")
    single.add_argument(
        "--snr-bins", default="",
        help="comma-separated SNR bin edges for nu_eff calibration, e.g. 4,6,8,12,inf")
    single.add_argument("--min-calibration-count", type=int, default=50)
    single.add_argument("--noise-dof", type=float, default=2.0)
    single.add_argument("--signal-dof", type=float, default=None)
    single.add_argument("--beta-max", type=float, default=0.03)
    single.add_argument("--beta-grid-size", type=int, default=31)
    single.add_argument("--default-autocorr-power", type=float, default=1.0)
    single.add_argument(
        "--noise-beta", type=float, default=-1.0,
        help=("noncentral H0 mismatch amplitude; Wguo uses beta=-1, and "
              "only beta^2 enters lambda"))
    single.add_argument("--rank-offset", type=float, default=0.0)
    single.add_argument(
        "--autocorr-power-file",
        help=("optional JSON/CSV map for template-dependent "
              "sum_delta |C_{j,m}(Delta)|^2 values"))
    single.add_argument(
        "--bank-stats-dir",
        help=("optional Wguo bank-stat directory containing "
              "H1/L1_O3_FB_banks_magnitudes_and_dofs.pkl"))
    single.add_argument("--snr-log-weight", type=float, default=0.5)
    single.add_argument(
        "--background-window-days", type=float, default=7.0,
        help=("rolling time window kept in the single-detector FAR-LLR "
              "background file; default is the latest 7 days"))
    single.add_argument(
        "--fit-min-points", type=int, default=20,
        help="minimum FAR-LLR support points needed before using the fitted map")
    single.add_argument(
        "--far-floor-count", type=float, default=1.0,
        help="pseudo-count used to avoid zero FAR during cold-start bootstrap")
    single.add_argument(
        "--far-fit-boundary", type=float, default=1.0e-2,
        help=("FAR value where the fitted high-LLR tail is attached; "
              "default 1e-2, i.e. log10(FAR)=-2"))


def add_feature_csv_arguments(parser):
    parser.add_argument(
        "--feature-csv", action="append", required=True,
        help=("CSV produced after the detector-local products are already "
              "available; may be repeated"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--ifos", default="H1,L1")
    parser.add_argument("--min-snr", type=float, default=4.0)
    parser.add_argument(
        "--foreground-count", type=int, default=1000,
        help="number of latest foreground features to assign FAR to")
    parser.add_argument(
        "--bootstrap-background-from-foreground", action="store_true",
        help=("cold-start test mode: use earlier foreground rows as a temporary "
              "LLR-FAR background support before assigning the latest rows"))
    parser.add_argument(
        "--background-livetime", type=float, default=None,
        help=("livetime in seconds for the bootstrap/support background; "
              "default is inferred from feature GPS span"))
    parser.add_argument(
        "--segment-xml", action="append", default=[],
        help=("LIGO-LW segment XML/XML.GZ used to compute detector-specific "
              "background livetime denominators; may be repeated"))
    parser.add_argument(
        "--background-start-gps", type=float, default=None,
        help="GPS start of the background window used with --segment-xml")
    parser.add_argument(
        "--background-end-gps", type=float, default=None,
        help="GPS end of the background window used with --segment-xml")
    parser.add_argument(
        "--background-input",
        help="single-detector FAR-LLR background JSON file to load before assigning FAR")
    parser.add_argument(
        "--background-output",
        help="single-detector FAR-LLR background JSON file to write after the scan")
    parser.add_argument(
        "--support-output",
        help="optional CSV dump of the fitted LLR-FAR support points")
    parser.add_argument(
        "--calibrate-noise-dof", action="store_true",
        help="estimate nu_eff from the background features before ranking")
    parser.add_argument(
        "--snr-bins", default="",
        help="comma-separated SNR bin edges for nu_eff calibration, e.g. 4,6,8,12,inf")
    parser.add_argument("--min-calibration-count", type=int, default=50)
    parser.add_argument("--noise-dof", type=float, default=2.0)
    parser.add_argument("--signal-dof", type=float, default=None)
    parser.add_argument("--beta-max", type=float, default=0.03)
    parser.add_argument("--beta-grid-size", type=int, default=31)
    parser.add_argument("--default-autocorr-power", type=float, default=1.0)
    parser.add_argument(
        "--noise-beta", type=float, default=-1.0,
        help=("noncentral H0 mismatch amplitude; Wguo uses beta=-1, and "
              "only beta^2 enters lambda"))
    parser.add_argument("--rank-offset", type=float, default=0.0)
    parser.add_argument(
        "--autocorr-power-file",
        help=("optional JSON/CSV map for template-dependent "
              "sum_delta |C_{j,m}(Delta)|^2 values"))
    parser.add_argument(
        "--bank-stats-dir",
        help=("optional Wguo bank-stat directory containing "
              "H1/L1_O3_FB_banks_magnitudes_and_dofs.pkl"))
    parser.add_argument("--snr-log-weight", type=float, default=0.5)
    parser.add_argument(
        "--background-window-days", type=float, default=7.0,
        help=("rolling time window kept in the single-detector FAR-LLR "
              "background file; default is the latest 7 days"))
    parser.add_argument(
        "--fit-min-points", type=int, default=20,
        help="minimum FAR-LLR support points needed before using the fitted map")
    parser.add_argument(
        "--far-floor-count", type=float, default=1.0,
        help="pseudo-count used to avoid zero FAR during cold-start bootstrap")
    parser.add_argument(
        "--far-fit-boundary", type=float, default=1.0e-2,
        help=("FAR value where the fitted high-LLR tail is attached; "
              "default 1e-2, i.e. log10(FAR)=-2"))


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    args.func(args)
    return 0


def command_single(args):
    postcoh_filenames = expand_path_patterns(args.postcoh_glob)
    if not postcoh_filenames:
        raise ValueError("no postcoh files matched the requested input")
    likelihood_model = make_likelihood_model_from_args(args)
    rows = calculate_single_detector_rows(
        postcoh_filenames,
        ifos=split_ifos(args.ifos),
        min_snr=args.min_snr,
        default_livetime_step=args.livetime_step,
        likelihood_model=likelihood_model,
        background_input=args.background_input,
        background_output=args.background_output,
        calibrate_noise_dof=args.calibrate_noise_dof,
        calibration_snr_bins=parse_snr_bins(args.snr_bins),
        min_calibration_count=args.min_calibration_count,
        autocorr_power_file=args.autocorr_power_file,
        bank_stats_dir=args.bank_stats_dir,
        background_window_days=args.background_window_days,
        fit_min_points=args.fit_min_points,
        far_floor_count=args.far_floor_count,
        far_fit_boundary=args.far_fit_boundary)
    write_plot_rows_csv(rows, args.output)
    print("wrote %d single-detector rows to %s" % (len(rows), args.output))


def command_feature_csv(args):
    feature_filenames = expand_path_patterns(args.feature_csv)
    if not feature_filenames:
        raise ValueError("no feature CSV files matched the requested input")
    likelihood_model = make_likelihood_model_from_args(args)
    rows, branch = calculate_single_detector_rows_from_feature_csv(
        feature_filenames,
        ifos=split_ifos(args.ifos),
        min_snr=args.min_snr,
        foreground_count=args.foreground_count,
        bootstrap_background_from_foreground=(
            args.bootstrap_background_from_foreground),
        background_livetime=args.background_livetime,
        segment_xml_files=args.segment_xml,
        background_start_gps=args.background_start_gps,
        background_end_gps=args.background_end_gps,
        likelihood_model=likelihood_model,
        background_input=args.background_input,
        background_output=args.background_output,
        calibrate_noise_dof=args.calibrate_noise_dof,
        calibration_snr_bins=parse_snr_bins(args.snr_bins),
        min_calibration_count=args.min_calibration_count,
        autocorr_power_file=args.autocorr_power_file,
        bank_stats_dir=args.bank_stats_dir,
        background_window_days=args.background_window_days,
        fit_min_points=args.fit_min_points,
        far_floor_count=args.far_floor_count,
        far_fit_boundary=args.far_fit_boundary)
    write_plot_rows_csv(rows, args.output)
    if args.support_output:
        write_far_llr_support_csv(branch, args.support_output)
    print("wrote %d single-detector FAR rows to %s" %
          (len(rows), args.output))


def command_watch(args):
    likelihood_model = make_likelihood_model_from_args(args)
    watch_single_detector_stream(
        args.postcoh_glob,
        ifos=split_ifos(args.ifos),
        min_snr=args.min_snr,
        default_livetime_step=args.livetime_step,
        likelihood_model=likelihood_model,
        background_input=args.background_input,
        background_output=args.background_output,
        output=args.output,
        calibrate_noise_dof=args.calibrate_noise_dof,
        calibration_snr_bins=parse_snr_bins(args.snr_bins),
        min_calibration_count=args.min_calibration_count,
        autocorr_power_file=args.autocorr_power_file,
        bank_stats_dir=args.bank_stats_dir,
        background_window_days=args.background_window_days,
        fit_min_points=args.fit_min_points,
        far_floor_count=args.far_floor_count,
        far_fit_boundary=args.far_fit_boundary,
        state_file=args.state_file,
        stop_file=args.stop_file,
        poll_interval=args.poll_interval,
        stable_age=args.stable_age,
        final_flush_timeout=args.final_flush_timeout)


def calculate_single_detector_rows(postcoh_filenames,
                                   ifos=("H1", "L1"),
                                   min_snr=4.0,
                                   default_livetime_step=1.0,
                                   likelihood_model=None,
                                   background_input=None,
                                   background_output=None,
                                   calibrate_noise_dof=False,
                                   calibration_snr_bins=None,
                                   min_calibration_count=50,
                                   autocorr_power_file=None,
                                   bank_stats_dir=None,
                                   background_window_days=7.0,
                                   fit_min_points=20,
                                   far_floor_count=1.0,
                                   far_fit_boundary=1.0e-2):
    ifos = split_ifos(ifos)
    autocorr_power_by_template = load_template_shape_map(
        autocorr_power_file, bank_stats_dir, ifos)
    branch = SingleDetectorBranch(
        likelihood_model or make_default_likelihood_model(),
        ifos=ifos,
        min_snr=min_snr,
        background_window_seconds=days_to_seconds(background_window_days),
        fit_min_points=fit_min_points,
        far_floor_count=far_floor_count,
        far_fit_boundary=far_fit_boundary)
    if background_input:
        branch.load_background_file(background_input)

    background_features, foreground_features, livetime_updates = scan_postcoh_files(
        postcoh_filenames,
        ifos,
        min_snr,
        autocorr_power_by_template,
        default_livetime_step)

    if calibrate_noise_dof:
        background_features = prune_features_by_gps(
            background_features, branch.background_window_seconds)
        branch.calibrate_noise_dof_from_features(
            background_features,
            snr_bins=calibration_snr_bins,
            min_count=min_calibration_count)

    if calibrate_noise_dof:
        for update in livetime_updates:
            if len(update) == 2:
                seconds, active_ifos = update
                gps = None
            else:
                seconds, active_ifos, gps = update
            branch.add_livetime(seconds, active_ifos, gps=gps)
        branch.rebuild_background_support(background_features)
    else:
        update_branch_with_scanned_rows(
            branch, background_features, livetime_updates)

    results = [
        branch.assign_feature(feature) for feature in foreground_features
    ]
    if background_output:
        branch.write_background_file(background_output)
    return results_to_plot_rows(results)


def calculate_single_detector_rows_from_feature_csv(
        feature_filenames,
        ifos=("H1", "L1"),
        min_snr=4.0,
        foreground_count=1000,
        bootstrap_background_from_foreground=False,
        background_livetime=None,
        segment_xml_files=None,
        background_start_gps=None,
        background_end_gps=None,
        likelihood_model=None,
        background_input=None,
        background_output=None,
        calibrate_noise_dof=False,
        calibration_snr_bins=None,
        min_calibration_count=50,
        autocorr_power_file=None,
        bank_stats_dir=None,
        background_window_days=7.0,
        fit_min_points=20,
        far_floor_count=1.0,
        far_fit_boundary=1.0e-2):
    """Assign single-detector FAR after detector-local rows already exist.

    This is the downstream-only interface for the wguo-style branch boundary:
    matched filtering, postcoh row formation, and detector-local extraction have
    already happened.  The CSV must contain either detector-specific columns
    such as snglsnr_H1/chisq_H1 or generic ifo/rho/chisq columns.
    """

    ifos = split_ifos(ifos)
    autocorr_power_by_template = load_template_shape_map(
        autocorr_power_file, bank_stats_dir, ifos)
    branch = SingleDetectorBranch(
        likelihood_model or make_default_likelihood_model(),
        ifos=ifos,
        min_snr=min_snr,
        background_window_seconds=days_to_seconds(background_window_days),
        fit_min_points=fit_min_points,
        far_floor_count=far_floor_count,
        far_fit_boundary=far_fit_boundary)
    if background_input:
        branch.load_background_file(background_input)

    features = scan_feature_csv_files(
        feature_filenames, ifos, min_snr, autocorr_power_by_template)
    if not features:
        raise ValueError("no usable single-detector features found in CSV")

    foreground_count = int(foreground_count)
    if foreground_count <= 0:
        raise ValueError("foreground-count must be positive")

    explicit_background = [
        feature for feature in features
        if feature.is_background == FLAG_BACKGROUND
    ]
    explicit_foreground = [
        feature for feature in features
        if feature.is_background != FLAG_BACKGROUND
    ]

    if background_input:
        background_features = explicit_background
        foreground_features = explicit_foreground[-foreground_count:]
    elif bootstrap_background_from_foreground:
        if len(explicit_foreground) <= foreground_count:
            raise ValueError(
                "not enough features for bootstrap background: %d <= %d" %
                (len(explicit_foreground), foreground_count))
        background_features = explicit_foreground[:-foreground_count]
        foreground_features = explicit_foreground[-foreground_count:]
    elif explicit_background:
        background_features = explicit_background
        foreground_features = explicit_foreground[-foreground_count:]
    else:
        raise ValueError(
            "feature CSV has no background rows and no background-input; "
            "use --bootstrap-background-from-foreground only for cold-start "
            "integration tests")

    if calibrate_noise_dof and background_features:
        background_features = prune_features_by_gps(
            background_features, branch.background_window_seconds)
        branch.calibrate_noise_dof_from_features(
            background_features,
            snr_bins=calibration_snr_bins,
            min_count=min_calibration_count)

    segment_map = {}
    if segment_xml_files:
        if background_start_gps is None or background_end_gps is None:
            raise ValueError(
                "--segment-xml requires --background-start-gps and "
                "--background-end-gps")
        segment_map = merge_segment_maps(
            [load_ligolw_segment_xml(path) for path in segment_xml_files])

    if background_features:
        fallback_livetime = background_livetime
        if fallback_livetime is None:
            fallback_livetime = infer_feature_livetime_seconds(background_features)
        if fallback_livetime is None or float(fallback_livetime) <= 0.0:
            raise ValueError(
                "background livetime is required to assign FAR from LLR")
        for ifo in ifos:
            if not any(feature.ifo == ifo for feature in background_features):
                continue
            if segment_map:
                livetime = segment_livetime_seconds(
                    segment_map, ifo, background_start_gps, background_end_gps)
            else:
                livetime = fallback_livetime
            if float(livetime) > 0.0:
                branch.add_livetime(livetime, [ifo])
        if calibrate_noise_dof:
            branch.rebuild_background_support(background_features)
        else:
            for feature in background_features:
                branch.accumulate_background_feature(feature)
            branch.update_far_llr_support(background_features)

    if background_output:
        branch.write_background_file(background_output)

    if background_input:
        branch.use_fitted_far = True
    elif branch.background:
        # Force the assignment stage to exercise the LLR-FAR support curve that
        # was just built, matching the production lookup path.
        branch.use_fitted_far = True

    results = [branch.assign_feature(feature) for feature in foreground_features]
    if background_output:
        branch.write_background_file(background_output)
    return results_to_plot_rows(results), branch


def scan_postcoh_files(postcoh_filenames,
                       ifos,
                       min_snr,
                       autocorr_power_by_template,
                       default_livetime_step=1.0):
    background_features = []
    foreground_features = []
    livetime_updates = []

    for filename in postcoh_filenames:
        xmldoc, table = load_postcoh_table(filename)
        try:
            for row in table:
                is_background = getattr(row, "is_background", None)
                if is_background == FLAG_BACKGROUND:
                    for feature in features_from_postcoh_row(
                            row, ifos, min_snr, autocorr_power_by_template):
                        feature.source_row = None
                        background_features.append(feature)
                elif is_background == FLAG_FOREGROUND:
                    features = features_from_postcoh_row(
                        row, ifos, min_snr, autocorr_power_by_template)
                    for feature in features:
                        feature.source_row = None
                    foreground_features.extend(features)
                elif is_background == FLAG_EMPTY:
                    livetime_updates.append((
                        row_livetime_seconds(row, default_livetime_step),
                        active_ifos_from_row(row, ifos),
                        row_gps_seconds(row)))
        finally:
            xmldoc.unlink()

    return background_features, foreground_features, livetime_updates


def scan_feature_csv_files(feature_filenames,
                           ifos,
                           min_snr,
                           autocorr_power_by_template=None):
    features = []
    for filename in feature_filenames:
        with open_csv_for_read(filename) as input_file:
            reader = csv.DictReader(input_file)
            for row_index, row in enumerate(reader, 1):
                features.extend(features_from_feature_csv_row(
                    row, ifos, min_snr, autocorr_power_by_template,
                    source_row_index=row_index))
    features.sort(key=feature_sort_key)
    return features


def features_from_feature_csv_row(row,
                                  ifos,
                                  min_snr,
                                  autocorr_power_by_template=None,
                                  source_row_index=None):
    features = []
    min_snr = float(min_snr)
    row_ifos = row.get("ifos")
    tmplt_idx = row.get("tmplt_idx") or row.get("template_id")
    bankid = row.get("bankid") or row.get("bank_id")
    is_background = parse_row_flag(row.get("is_background"))

    generic_ifo = row.get("ifo")
    if generic_ifo:
        candidate_ifos = (generic_ifo,)
    else:
        candidate_ifos = tuple(ifos)

    for ifo in candidate_ifos:
        if row_ifos and ifo not in row_ifos:
            continue
        rho = first_present(row, [
            "snglsnr_%s" % ifo,
            "rho_%s" % ifo,
            "rho",
            "snglsnr",
        ])
        chisq = first_present(row, [
            "chisq_%s" % ifo,
            "chi2_%s" % ifo,
            "chisq",
            "chi2",
        ])
        if not is_finite_positive(rho) or not is_finite_positive(chisq):
            continue
        if float(rho) < min_snr:
            continue
        source_info = dict(row)
        if source_row_index is not None:
            source_info["_feature_csv_row_index"] = source_row_index
        feature = SingleDetectorFeature(
            ifo=ifo,
            rho=float(rho),
            chisq=float(chisq),
            tmplt_idx=tmplt_idx,
            bankid=bankid,
            autocorr_power=lookup_autocorr_power(
                autocorr_power_by_template, ifo, bankid, tmplt_idx),
            dof=lookup_template_dof(
                autocorr_power_by_template, ifo, bankid, tmplt_idx),
            end_time=first_present(row, [
                "end_time_sngl_%s" % ifo,
                "end_time_%s" % ifo,
                "end_time",
            ]),
            end_time_ns=first_present(row, [
                "end_time_ns_sngl_%s" % ifo,
                "end_time_ns_%s" % ifo,
                "end_time_ns",
            ]),
            is_background=is_background,
            source_row=source_info)
        features.append(feature)
    return features


def first_present(row, names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def segment_def_id_suffix(value):
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    return text.rsplit(":", 1)[-1]


def open_text_maybe_gzip(filename):
    if str(filename).endswith(".gz"):
        return gzip.open(filename, "rt")
    return open(filename, "r")


def load_ligolw_segment_xml(filename):
    """Return {ifo: [(start, end), ...]} from a LIGO-LW segment XML file."""

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
                if len(row) < 5:
                    continue
                ifo = row[1].strip().strip('"').strip("'")
                def_id = segment_def_id_suffix(row[4])
                if ifo and def_id is not None:
                    definer_by_id[def_id] = ifo
                    segments.setdefault(ifo, [])
            else:
                if len(row) < 7:
                    continue
                def_id = segment_def_id_suffix(row[3])
                ifo = definer_by_id.get(def_id)
                if not ifo:
                    continue
                try:
                    end = float(row[0])
                    end += float(row[1] or 0.0) * 1.0e-9
                    start = float(row[5])
                    start += float(row[6] or 0.0) * 1.0e-9
                except (TypeError, ValueError):
                    continue
                if end > start:
                    segments.setdefault(ifo, []).append((start, end))
    for ifo in segments:
        segments[ifo].sort()
    return segments


def merge_segment_maps(segment_maps):
    merged = {}
    for segment_map in segment_maps:
        for ifo, values in segment_map.items():
            merged.setdefault(ifo, []).extend(values)
    for ifo in merged:
        merged[ifo].sort()
    return merged


def segment_livetime_seconds(segments, ifo, start, end):
    if start is None or end is None or end <= start:
        return 0.0
    livetime = 0.0
    for seg_start, seg_end in segments.get(ifo, []):
        overlap_start = max(float(start), float(seg_start))
        overlap_end = min(float(end), float(seg_end))
        if overlap_end > overlap_start:
            livetime += overlap_end - overlap_start
    return livetime


def parse_row_flag(value):
    if value in (None, ""):
        return FLAG_FOREGROUND
    try:
        return int(float(value))
    except (TypeError, ValueError):
        lowered = str(value).strip().lower()
        if lowered in ("background", "bg"):
            return FLAG_BACKGROUND
        if lowered in ("empty", "livetime"):
            return FLAG_EMPTY
        return FLAG_FOREGROUND


def feature_sort_key(feature):
    gps = feature_gps_seconds(feature)
    if gps is None:
        gps = -float("inf")
    return (gps, feature.ifo, str(feature.bankid), str(feature.tmplt_idx))


def infer_feature_livetime_seconds(features):
    gps_values = [
        feature_gps_seconds(feature) for feature in features
        if feature_gps_seconds(feature) is not None
    ]
    if len(gps_values) < 2:
        return None
    return max(gps_values) - min(gps_values)


def update_branch_with_scanned_rows(branch, background_features,
                                    livetime_updates):
    for feature in background_features:
        branch.accumulate_background_feature(feature)

    for update in livetime_updates:
        if len(update) == 2:
            seconds, active_ifos = update
            gps = None
        else:
            seconds, active_ifos, gps = update
        branch.add_livetime(seconds, active_ifos, gps=gps)

    branch.update_far_llr_support(background_features)


def prune_features_by_gps(features, max_age_seconds, reference_gps=None):
    if max_age_seconds is None or max_age_seconds <= 0.0:
        return list(features)

    gps_values = [
        feature_gps_seconds(feature) for feature in features
        if feature_gps_seconds(feature) is not None
    ]
    if not gps_values:
        return list(features)
    if reference_gps is None:
        reference_gps = max(gps_values)
    cutoff = float(reference_gps) - float(max_age_seconds)
    return [
        feature for feature in features
        if feature_gps_seconds(feature) is None
        or feature_gps_seconds(feature) >= cutoff
    ]


def watch_single_detector_stream(postcoh_patterns,
                                 ifos=("H1", "L1"),
                                 min_snr=4.0,
                                 default_livetime_step=1.0,
                                 likelihood_model=None,
                                 background_input=None,
                                 background_output=None,
                                 output=None,
                                 calibrate_noise_dof=False,
                                 calibration_snr_bins=None,
                                 min_calibration_count=50,
                                 autocorr_power_file=None,
                                 bank_stats_dir=None,
                                 background_window_days=7.0,
                                 fit_min_points=20,
                                 far_floor_count=1.0,
                                 far_fit_boundary=1.0e-2,
                                 state_file=None,
                                 stop_file=None,
                                 poll_interval=10.0,
                                 stable_age=5.0,
                                 final_flush_timeout=300.0):
    """Process new postcoh snapshots as they appear.

    This is the online sidecar mode.  The GStreamer graph keeps producing
    sdpostcoh*.xml.gz snapshots while this loop detects newly closed/stable
    files, updates the LLR/FAR background, appends new foreground rows, and
    periodically rewrites the FAR-LLR calibration JSON.
    """

    if output is None:
        raise ValueError("watch mode requires --output")

    ifos = split_ifos(ifos)
    autocorr_power_by_template = load_template_shape_map(
        autocorr_power_file, bank_stats_dir, ifos)
    branch = SingleDetectorBranch(
        likelihood_model or make_default_likelihood_model(),
        ifos=ifos,
        min_snr=min_snr,
        background_window_seconds=days_to_seconds(background_window_days),
        fit_min_points=fit_min_points,
        far_floor_count=far_floor_count,
        far_fit_boundary=far_fit_boundary)
    if background_input:
        branch.load_background_file(background_input)

    processed = load_watch_state(state_file)
    calibration_background_features = []
    ensure_plot_csv(output)
    stop_seen_at = None

    while True:
        stop_requested = bool(stop_file and os.path.exists(stop_file))
        if stop_requested and stop_seen_at is None:
            stop_seen_at = time.time()
        filenames = expand_path_patterns(postcoh_patterns)
        new_files = []
        for filename in filenames:
            if postcoh_snapshot_ready(filename, stable_age):
                signature = file_signature(filename)
                if processed.get(filename) == signature:
                    continue
                new_files.append(filename)

        made_progress = False
        for filename in sorted(new_files):
            try:
                rows, background_features = process_single_detector_snapshot(
                    branch,
                    filename,
                    ifos,
                    min_snr,
                    autocorr_power_by_template,
                    default_livetime_step,
                    calibrate_noise_dof,
                    calibration_background_features,
                    calibration_snr_bins,
                    min_calibration_count)
            except Exception as exc:
                # A file can be visible before XML close/rename is fully complete.
                # Leave it unprocessed so the next poll can retry it.
                print("single-detector watcher: skip %s for now: %s" %
                      (filename, exc), file=sys.stderr)
                continue

            append_plot_rows_csv(rows, output)
            if background_output:
                branch.write_background_file(background_output)
            processed[filename] = file_signature(filename)
            save_watch_state(state_file, processed)
            made_progress = True
            print("single-detector watcher: processed %s, %d foreground rows, "
                  "%d background features" %
                  (filename, len(rows), len(background_features)))

        if stop_requested:
            remaining = []
            for filename in expand_path_patterns(postcoh_patterns):
                if not postcoh_snapshot_ready(filename, stable_age):
                    remaining.append(filename)
                    continue
                if processed.get(filename) != file_signature(filename):
                    remaining.append(filename)
            timed_out = (
                stop_seen_at is not None
                and (time.time() - stop_seen_at) >= float(final_flush_timeout))
            if not remaining:
                break
            if timed_out:
                print("single-detector watcher: final flush timed out with "
                      "%d unprocessed snapshots" % len(remaining),
                      file=sys.stderr)
                break

        time.sleep(max(0.1, float(poll_interval)))

    if background_output:
        branch.write_background_file(background_output)
    save_watch_state(state_file, processed)


def process_single_detector_snapshot(branch,
                                     filename,
                                     ifos,
                                     min_snr,
                                     autocorr_power_by_template,
                                     default_livetime_step,
                                     calibrate_noise_dof,
                                     calibration_background_features,
                                     calibration_snr_bins,
                                     min_calibration_count):
    background_features, foreground_features, livetime_updates = scan_postcoh_files(
        [filename],
        ifos,
        min_snr,
        autocorr_power_by_template,
        default_livetime_step)

    if calibrate_noise_dof:
        calibration_background_features.extend(background_features)
        calibration_background_features[:] = prune_features_by_gps(
            calibration_background_features, branch.background_window_seconds)
        branch.calibrate_noise_dof_from_features(
            calibration_background_features,
            snr_bins=calibration_snr_bins,
            min_count=min_calibration_count)

    if calibrate_noise_dof:
        for update in livetime_updates:
            if len(update) == 2:
                seconds, active_ifos = update
                gps = None
            else:
                seconds, active_ifos, gps = update
            branch.add_livetime(seconds, active_ifos, gps=gps)
        branch.rebuild_background_support(calibration_background_features)
    else:
        update_branch_with_scanned_rows(
            branch, background_features, livetime_updates)
    results = [branch.assign_feature(feature) for feature in foreground_features]
    return results_to_plot_rows(results), background_features


def expand_path_patterns(patterns):
    filenames = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            filenames.extend(matches)
        elif os.path.exists(pattern):
            filenames.append(pattern)
    return filenames


def file_signature(filename):
    stat = os.stat(filename)
    return {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def postcoh_snapshot_ready(filename, stable_age=5.0):
    if not os.path.exists(filename):
        return False
    try:
        stat = os.stat(filename)
    except OSError:
        return False
    if stat.st_size <= 0:
        return False
    return (time.time() - stat.st_mtime) >= float(stable_age)


def load_watch_state(filename):
    if not filename or not os.path.exists(filename):
        return {}
    with open(filename, "r") as input_file:
        data = json.load(input_file)
    return data.get("processed", {})


def save_watch_state(filename, processed):
    if not filename:
        return
    directory = os.path.dirname(os.path.abspath(filename))
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    tmp_filename = filename + ".tmp"
    with open(tmp_filename, "w") as output_file:
        json.dump({
            "updated_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "processed": processed,
        }, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    os.rename(tmp_filename, filename)


def days_to_seconds(days):
    if days is None:
        return None
    days = float(days)
    if days <= 0.0:
        return None
    return days * 86400.0


def load_postcoh_table(filename):
    from glue.ligolw import ligolw
    from glue.ligolw import utils as ligolw_utils

    if sys.version_info[0] >= 3:
        import builtins
        if not hasattr(builtins, "unicode"):
            builtins.unicode = str

    try:
        from gstlal.pipemodules.postcohtable import postcoh_table_def
    except ImportError:
        from postcohtable import postcoh_table_def

    class LIGOLWContentHandler(ligolw.LIGOLWContentHandler):
        pass

    postcoh_table_def.use_in(LIGOLWContentHandler)
    xmldoc = ligolw_utils.load_filename(
        filename, verbose=False, contenthandler=LIGOLWContentHandler)
    return xmldoc, postcoh_table_def.PostcohInspiralTable.get_table(xmldoc)


def split_ifos(ifos):
    if ifos is None:
        return ("H1", "L1")
    if isinstance(ifos, string_types):
        return tuple(ifo.strip() for ifo in ifos.split(",") if ifo.strip())
    return tuple(ifos)


def load_autocorr_power_map(filename):
    """Load optional template-dependent sum_delta |C|^2 values.

    The map is deliberately permissive because the exact bank-side export format
    may evolve.  JSON may be either a dictionary of key -> value or a list of
    objects with fields such as ifo, bankid, tmplt_idx, and autocorr_power.  CSV
    uses the same object-style fields.
    """

    if not filename:
        return {}
    if not os.path.exists(filename):
        raise ValueError("autocorr power file does not exist: %s" % filename)

    if filename.endswith(".json"):
        with open(filename, "r") as input_file:
            data = json.load(input_file)
        if isinstance(data, dict):
            return dict((str(key), float(value))
                        for key, value in data.items()
                        if is_finite_positive(value))
        if isinstance(data, list):
            return autocorr_power_rows_to_map(data)
        raise ValueError("unsupported autocorr power JSON structure")

    with open_csv_for_read(filename) as input_file:
        return autocorr_power_rows_to_map(csv.DictReader(input_file))


def load_template_shape_map(autocorr_power_file=None, bank_stats_dir=None,
                            ifos=None):
    """Load per-template shape amplitude and effective degrees of freedom.

    The original prototype accepted a simple key -> A_m map.  Wguo's branch also
    carries template-dependent degrees of freedom in pickle files.  The online
    single branch can use both without changing the upstream postcoh stream.
    """

    mapping = {}
    mapping.update(load_autocorr_power_map(autocorr_power_file))
    mapping.update(load_wguo_bank_stats_map(bank_stats_dir, ifos=ifos))
    return mapping


def load_wguo_bank_stats_map(directory, ifos=None):
    if not directory:
        return {}
    mapping = {}
    for ifo in split_ifos(ifos or ("H1", "L1")):
        filename = os.path.join(
            directory, "%s_O3_FB_banks_magnitudes_and_dofs.pkl" % ifo)
        if not os.path.exists(filename):
            continue
        with open(filename, "rb") as input_file:
            try:
                banks = pickle.load(input_file)
            except UnicodeDecodeError:
                input_file.seek(0)
                banks = pickle.load(input_file, encoding="latin1")
        for bankid, bank in banks.items():
            if hasattr(bank, "columns") and "dofs" in bank.columns:
                dofs = list(bank["dofs"].to_numpy())
            elif isinstance(bank, dict):
                dofs = list(bank.get("dofs") or [])
            else:
                dofs = []
            if hasattr(bank, "columns") and "magnitudes" in bank.columns:
                magnitudes = list(bank["magnitudes"].to_numpy())
            elif isinstance(bank, dict):
                magnitudes = list(bank.get("magnitudes") or [])
            else:
                magnitudes = []
            ntemplate = max(len(dofs), len(magnitudes))
            for tmplt_idx in range(ntemplate):
                entry = {}
                if tmplt_idx < len(magnitudes) and is_finite_positive(
                        magnitudes[tmplt_idx]):
                    # Wguo stores sqrt(sum_delta |C(delta)|^2).  The
                    # noncentrality calculation needs sum_delta |C(delta)|^2.
                    magnitude = float(magnitudes[tmplt_idx])
                    entry["autocorr_power"] = magnitude * magnitude
                if tmplt_idx < len(dofs) and is_finite_positive(
                        dofs[tmplt_idx]):
                    entry["dof"] = float(dofs[tmplt_idx])
                if not entry:
                    continue
                for key in autocorr_power_keys(ifo, bankid, tmplt_idx):
                    merged = {}
                    previous = mapping.get(key)
                    if isinstance(previous, dict):
                        merged.update(previous)
                    elif is_finite_positive(previous):
                        merged["autocorr_power"] = float(previous)
                    merged.update(entry)
                    mapping[key] = merged
    return mapping


def autocorr_power_rows_to_map(rows):
    mapping = {}
    for row in rows:
        value = (row.get("autocorr_power") or row.get("power")
                 or row.get("sum_abs_c_sq") or row.get("sum_c_sq"))
        if not is_finite_positive(value):
            continue
        ifo = row.get("ifo") or row.get("instrument")
        bankid = row.get("bankid") or row.get("bank_id")
        tmplt_idx = row.get("tmplt_idx") or row.get("template_id")
        direct_key = row.get("key")
        for key in autocorr_power_keys(ifo, bankid, tmplt_idx):
            mapping[key] = float(value)
        if direct_key is not None:
            mapping[str(direct_key)] = float(value)
    return mapping


def autocorr_power_keys(ifo, bankid, tmplt_idx):
    keys = []
    ifo_values = [str(ifo)] if ifo is not None else []
    bank_values = normalized_index_strings(bankid, width=4)
    template_values = normalized_index_strings(tmplt_idx)
    for ifo_value in ifo_values:
        for bank_value in bank_values:
            for template_value in template_values:
                keys.append("%s:%s:%s" % (ifo_value, bank_value,
                                          template_value))
    for ifo_value in ifo_values:
        for template_value in template_values:
            keys.append("%s:%s" % (ifo_value, template_value))
    for bank_value in bank_values:
        for template_value in template_values:
            keys.append("%s:%s" % (bank_value, template_value))
    keys.extend(template_values)
    return keys


def normalized_index_strings(value, width=None):
    if value is None:
        return []
    values = [str(value)]
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return values
    values.append(str(number))
    if width:
        values.append(("%0*d" % (int(width), number)))
    output = []
    for item in values:
        if item not in output:
            output.append(item)
    return output


def lookup_autocorr_power(mapping, ifo, bankid, tmplt_idx):
    if not mapping:
        return None
    for key in autocorr_power_keys(ifo, bankid, tmplt_idx):
        if key in mapping:
            value = mapping[key]
            if isinstance(value, dict):
                value = (value.get("autocorr_power") or value.get("power")
                         or value.get("am"))
            if is_finite_positive(value):
                return float(value)
    return None


def lookup_template_dof(mapping, ifo, bankid, tmplt_idx):
    if not mapping:
        return None
    for key in autocorr_power_keys(ifo, bankid, tmplt_idx):
        if key in mapping and isinstance(mapping[key], dict):
            value = (mapping[key].get("dof") or mapping[key].get("nu")
                     or mapping[key].get("effective_dof"))
            if is_finite_positive(value):
                return float(value)
    return None


def is_finite_positive(value):
    if value is None:
        return False
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return (not math.isnan(value)) and (not math.isinf(value)) and value > 0.0


def is_finite_number(value):
    if value is None:
        return False
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return (not math.isnan(value)) and (not math.isinf(value))


def features_from_postcoh_row(row, ifos=None, min_snr=0.0,
                              autocorr_power_by_template=None):
    """Extract all detector-local feature points from one postcoh row."""

    features = []
    ifos = tuple(ifos or pipe_macro.IFO_MAP)
    active_ifos = active_ifos_from_row(row, ifos)
    min_snr = float(min_snr)
    tmplt_idx = getattr(row, "tmplt_idx", None)
    bankid = getattr(row, "bankid", None)

    for ifo in active_ifos:
        rho = read_detector_value(row, "snglsnr", ifo, default=0.0)
        chisq = read_detector_value(row, "chisq", ifo, default=0.0)

        if rho is None or chisq is None:
            continue
        if float(rho) < min_snr or float(chisq) <= 0.0:
            continue

        features.append(
            SingleDetectorFeature(
                ifo=ifo,
                rho=rho,
                chisq=chisq,
                tmplt_idx=tmplt_idx,
                bankid=bankid,
                autocorr_power=lookup_autocorr_power(
                    autocorr_power_by_template, ifo, bankid, tmplt_idx),
                dof=lookup_template_dof(
                    autocorr_power_by_template, ifo, bankid, tmplt_idx),
                end_time=read_detector_value(row, "end_time_sngl", ifo,
                                             getattr(row, "end_time", None)),
                end_time_ns=read_detector_value(
                    row, "end_time_ns_sngl", ifo,
                    getattr(row, "end_time_ns", None)),
                is_background=getattr(row, "is_background", None),
                source_row=row))

    return features


class SingleDetectorFeature(object):
    """One detector-specific feature point from a postcoh trigger row."""

    __slots__ = ("ifo", "category", "rho", "chisq", "tmplt_idx", "bankid",
                 "autocorr_power", "dof", "end_time", "end_time_ns",
                 "is_background", "source_row")

    def __init__(self,
                 ifo,
                 rho,
                 chisq,
                 tmplt_idx=None,
                 bankid=None,
                 autocorr_power=None,
                 dof=None,
                 end_time=None,
                 end_time_ns=None,
                 is_background=None,
                 source_row=None):
        self.ifo = ifo
        self.category = "%s_sd" % ifo
        self.rho = float(rho)
        self.chisq = float(chisq)
        self.tmplt_idx = tmplt_idx
        self.bankid = bankid
        self.autocorr_power = autocorr_power
        self.dof = dof
        self.end_time = end_time
        self.end_time_ns = end_time_ns
        self.is_background = is_background
        self.source_row = source_row

    def to_background_dict(self, rank=None):
        row = {
            "ifo": self.ifo,
            "rho": json_safe_float(self.rho),
            "chisq": json_safe_float(self.chisq),
            "tmplt_idx": self.tmplt_idx,
            "bankid": self.bankid,
            "autocorr_power": json_safe_float(self.autocorr_power),
            "dof": json_safe_float(self.dof),
            "end_time": json_safe_float(json_load_float(self.end_time)
                                        if self.end_time is not None else None),
            "end_time_ns": json_safe_float(json_load_float(self.end_time_ns)
                                           if self.end_time_ns is not None else None),
            "is_background": FLAG_BACKGROUND,
        }
        if rank is not None and is_finite_number(rank):
            row["llr"] = float(rank)
            row["rank"] = float(rank)
        source = self.source_row if isinstance(self.source_row, dict) else {}
        for key in ("source_file", "source_row", "_feature_csv_row_index"):
            if key in source:
                row[key] = source.get(key)
        return row

    @classmethod
    def from_background_dict(cls, row):
        return cls(
            ifo=row.get("ifo"),
            rho=json_load_float(row.get("rho")),
            chisq=json_load_float(row.get("chisq")),
            tmplt_idx=row.get("tmplt_idx"),
            bankid=row.get("bankid"),
            autocorr_power=json_load_float(row.get("autocorr_power"))
            if row.get("autocorr_power") is not None else None,
            dof=json_load_float(row.get("dof"))
            if row.get("dof") is not None else None,
            end_time=json_load_float(row.get("end_time"))
            if row.get("end_time") is not None else None,
            end_time_ns=json_load_float(row.get("end_time_ns"))
            if row.get("end_time_ns") is not None else None,
            is_background=FLAG_BACKGROUND,
            source_row=row)


class SingleDetectorBranch(object):
    """Prototype branch after cuda_postcoh for one or more detectors."""

    def __init__(self,
                 likelihood_model,
                 ifos=None,
                 min_snr=0.0,
                 background_window_seconds=None,
                 fit_min_points=20,
                 far_floor_count=1.0,
                 far_fit_boundary=1.0e-2):
        self.likelihood_model = likelihood_model
        self.ifos = tuple(ifos or pipe_macro.IFO_MAP)
        self.min_snr = float(min_snr)
        self.background_window_seconds = background_window_seconds
        self.fit_min_points = int(fit_min_points)
        self.far_floor_count = float(far_floor_count)
        self.far_fit_boundary = float(far_fit_boundary)
        self.use_fitted_far = False
        self.background = dict(
            (ifo, RankBackground(fit_min_points=self.fit_min_points,
                                 far_floor_count=self.far_floor_count,
                                 far_fit_boundary=self.far_fit_boundary))
            for ifo in self.ifos)

    def rank_feature(self, feature, autocorr_power=None):
        return self.llr_feature(feature, autocorr_power)

    def llr_feature(self, feature, autocorr_power=None):
        if autocorr_power is None:
            autocorr_power = feature.autocorr_power
        return self.likelihood_model.rank(feature.rho, feature.chisq,
                                          autocorr_power, ifo=feature.ifo,
                                          dof=feature.dof)

    def add_livetime(self, seconds, ifos=None, gps=None):
        for ifo in tuple(ifos or self.ifos):
            if ifo in self.background:
                self.background[ifo].add_livetime(seconds, gps=gps)

    def load_background_file(self, filename):
        state = SingleFarLlrBackgroundFile.load(filename)
        if state.model is not None:
            self.likelihood_model = state.model
        has_raw_triggers = False
        for ifo, background in state.backgrounds.items():
            if ifo in self.background:
                background.fit_min_points = self.fit_min_points
                background.far_floor_count = self.far_floor_count
                background._invalidate_fit_cache()
                if background.background_triggers:
                    has_raw_triggers = True
                self.background[ifo].merge(background)
        if has_raw_triggers:
            self.rebuild_background_support_from_stored_triggers()
        self.prune_background_support()
        self.use_fitted_far = True

    def write_background_file(self, filename):
        state = SingleFarLlrBackgroundFile(
            ifos=self.ifos,
            model=self.likelihood_model,
            backgrounds=self.background)
        state.dump(filename)

    def calibrate_noise_dof_from_features(self,
                                          features,
                                          snr_bins=None,
                                          min_count=50):
        calibration = estimate_noise_dof_by_category(
            features,
            self.ifos,
            snr_bins=snr_bins,
            default_dof=self.likelihood_model.noise_dof,
            min_count=min_count)
        self.likelihood_model.set_noise_dof_calibration(calibration)
        self.likelihood_model.set_signal_dof_calibration(calibration)

    def accumulate_background_feature(self, feature, livetime=None,
                                      autocorr_power=None):
        rank = self.rank_feature(feature, autocorr_power)
        bg = self.background[feature.ifo]
        bg.add_rank(rank)
        bg.add_background_trigger(feature, rank)
        if livetime is not None:
            bg.add_livetime(livetime)
        return rank

    def update_far_llr_support(self, features):
        for feature in features:
            rank = self.rank_feature(feature)
            bg = self.background[feature.ifo]
            far = bg.direct_far(rank)
            bg.add_far_llr_point(rank, far, feature_gps_seconds(feature))
        self.prune_background_support()

    def rebuild_background_support(self, features):
        """Recompute all LLR support using the current likelihood model."""

        for bg in self.background.values():
            bg.reset_rank_support()
        for feature in features:
            if feature.ifo in self.background:
                rank = self.rank_feature(feature)
                self.background[feature.ifo].add_rank(rank)
                self.background[feature.ifo].add_background_trigger(
                    feature, rank)
        self.update_far_llr_support(features)

    def rebuild_background_support_from_stored_triggers(self):
        """Recompute support curves from raw triggers loaded from JSON."""

        for ifo, bg in self.background.items():
            if not bg.background_triggers:
                continue
            trigger_rows = list(bg.background_triggers)
            features = []
            bg.reset_rank_support()
            for row in trigger_rows:
                try:
                    feature = SingleDetectorFeature.from_background_dict(row)
                except (TypeError, ValueError):
                    continue
                if feature.ifo != ifo:
                    continue
                if not (is_finite_positive(feature.rho)
                        and is_finite_positive(feature.chisq)):
                    continue
                rank = self.rank_feature(feature)
                bg.add_rank(rank)
                bg.add_background_trigger(feature, rank)
                features.append(feature)
            for feature in features:
                rank = self.rank_feature(feature)
                bg.add_far_llr_point(
                    rank, bg.direct_far(rank), feature_gps_seconds(feature))
        self.prune_background_support()

    def prune_background_support(self):
        for bg in self.background.values():
            bg.prune_far_llr_points(self.background_window_seconds)

    def assign_feature(self, feature, autocorr_power=None):
        llr = self.llr_feature(feature, autocorr_power)
        bg = self.background[feature.ifo]
        far, far_source = bg.far_with_source(llr, use_fit=self.use_fitted_far)
        direct_far = bg.direct_far(llr)
        direct_source = (FAR_SOURCE_UNASSIGNED
                         if direct_far == float("inf")
                         else FAR_SOURCE_DIRECT_EMPIRICAL)
        return SingleDetectorResult(
            feature, llr, far, far_source,
            direct_far=direct_far,
            direct_far_source=direct_source)

    def process_row(self,
                    row,
                    autocorr_power_by_template=None,
                    livetime_step=None):
        """Process one postcoh row.

        Background rows update the branch background and return [].
        Foreground rows return SingleDetectorResult objects.
        Empty rows carry livetime/IFO state and return [].

        The caller should pass livetime_step in seconds when the row represents
        one live analysis interval. This keeps T_bg separate from the number of
        background triggers, which is required for FAR = N_bg(>= r*) / T_bg.
        """

        results = []
        features = features_from_postcoh_row(
            row, self.ifos, self.min_snr, autocorr_power_by_template)
        is_background = getattr(row, "is_background", None)

        if is_background == FLAG_BACKGROUND:
            for feature in features:
                self.accumulate_background_feature(feature)
            return []

        if is_background == FLAG_FOREGROUND:
            for feature in features:
                result = self.assign_feature(feature)
                write_single_far_to_row(row, feature.ifo, result.far)
                results.append(result)
            return results

        if is_background == FLAG_EMPTY and livetime_step is not None:
            self.add_livetime(
                livetime_step, active_ifos_from_row(row, self.ifos),
                gps=row_gps_seconds(row))
            return []

        return []


def make_default_likelihood_model():
    return SingleDetectorLikelihoodModel(
        signal_dof=2.0,
        noise_dof=2.0,
        beta_grid=uniform_beta_grid(0.03, 31),
        beta_weights=None,
        default_autocorr_power=1.0,
        snr_log_weight=0.5,
        rank_offset=0.0,
        noise_beta=-1.0)


def make_likelihood_model_from_args(args):
    signal_dof = args.signal_dof
    if signal_dof is None:
        signal_dof = args.noise_dof
    return SingleDetectorLikelihoodModel(
        signal_dof=signal_dof,
        noise_dof=args.noise_dof,
        beta_grid=uniform_beta_grid(args.beta_max, args.beta_grid_size),
        beta_weights=None,
        default_autocorr_power=args.default_autocorr_power,
        snr_log_weight=args.snr_log_weight,
        rank_offset=args.rank_offset,
        noise_beta=args.noise_beta)


def uniform_beta_grid(beta_max, grid_size):
    beta_max = float(beta_max)
    grid_size = max(1, int(grid_size))
    if grid_size == 1:
        return [0.5 * beta_max]
    step = beta_max / float(grid_size - 1)
    return [i * step for i in range(grid_size)]


def parse_snr_bins(text):
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    values = []
    for piece in text.split(","):
        piece = piece.strip().lower()
        if not piece:
            continue
        if piece in ("inf", "+inf", "infinity", "+infinity"):
            values.append(float("inf"))
        else:
            values.append(float(piece))
    if len(values) < 2:
        return None
    for i in range(1, len(values)):
        if values[i] <= values[i - 1]:
            raise ValueError("SNR bin edges must be strictly increasing")
    return values


def default_single_snr_bins():
    return [0.0, float("inf")]


def estimate_noise_dof_by_category(features,
                                   ifos,
                                   snr_bins=None,
                                   default_dof=2.0,
                                   min_count=50):
    snr_bins = list(snr_bins or default_single_snr_bins())
    calibration = dict((ifo, []) for ifo in ifos)
    by_category = dict((ifo, [[] for _ in range(len(snr_bins) - 1)])
                       for ifo in ifos)

    for feature in features:
        if feature.ifo not in by_category:
            continue
        idx = snr_bin_index(feature.rho, snr_bins)
        if idx is not None:
            by_category[feature.ifo][idx].append(feature.chisq)

    for ifo in ifos:
        for idx in range(len(snr_bins) - 1):
            values = by_category[ifo][idx]
            dof = float(default_dof)
            mean = None
            variance = None
            if len(values) >= int(min_count):
                mean = sum(values) / float(len(values))
                variance = sample_variance(values, mean)
                if variance is not None and variance > 0.0:
                    dof = max(0.1, min(1.0e6, 2.0 / variance))
            calibration[ifo].append({
                "rho_min": snr_bins[idx],
                "rho_max": snr_bins[idx + 1],
                "dof": dof,
                "count": len(values),
                "mean": mean,
                "variance": variance,
            })
    return calibration


def snr_bin_index(rho, snr_bins):
    rho = float(rho)
    for idx in range(len(snr_bins) - 1):
        lo = snr_bins[idx]
        hi = snr_bins[idx + 1]
        if rho >= lo and rho < hi:
            return idx
    if rho == snr_bins[-1] and len(snr_bins) > 1:
        return len(snr_bins) - 2
    return None


def sample_variance(values, mean=None):
    nval = len(values)
    if nval < 2:
        return None
    if mean is None:
        mean = sum(values) / float(nval)
    return sum((x - mean) * (x - mean) for x in values) / float(nval - 1)


def json_safe_float(value):
    if value is None:
        return None
    value = float(value)
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    if math.isnan(value):
        return None
    return value


def json_load_float(value):
    if isinstance(value, string_types):
        lowered = value.lower()
        if lowered in ("inf", "+inf", "infinity", "+infinity"):
            return float("inf")
        if lowered in ("-inf", "-infinity"):
            return -float("inf")
    if value is None:
        return None
    return float(value)


class SingleFarLlrBackgroundFile(object):
    """Persistent single-detector FAR-LLR background.

    This is intentionally a one-dimensional LLR-to-FAR calibration file.  It is
    not the coherent two-dimensional XML statistics file used by cohfar, but it
    does carry the full raw detector-local trigger background needed to rebuild
    the empirical FAR surface.
    """

    VERSION = 3

    def __init__(self, ifos=None, model=None, backgrounds=None):
        self.ifos = tuple(ifos or ())
        self.model = model
        self.backgrounds = dict(backgrounds or {})

    @classmethod
    def load(cls, filename):
        with open(filename, "r") as input_file:
            data = json.load(input_file)
        model_data = data.get("likelihood_model")
        model = None
        if model_data:
            model = SingleDetectorLikelihoodModel.from_dict(model_data)
        backgrounds = {}
        for ifo, bg_data in data.get("backgrounds", {}).items():
            backgrounds[ifo] = RankBackground.from_dict(bg_data)
        return cls(ifos=data.get("ifos", ()), model=model,
                   backgrounds=backgrounds)

    def dump(self, filename):
        directory = os.path.dirname(os.path.abspath(filename))
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        data = {
            "version": self.VERSION,
            "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "description": (
                "single-detector FAR-LLR calibration file; stores full raw "
                "detector-local background triggers plus derived support "
                "points (LLR, FAR, GPS) and category livetime"),
            "ifos": list(self.ifos),
            "likelihood_model": (self.model.to_dict()
                                 if self.model is not None else None),
            "backgrounds": dict(
                (ifo, bg.to_dict()) for ifo, bg in self.backgrounds.items()),
        }
        tmp_filename = filename + ".tmp"
        with open(tmp_filename, "w") as output_file:
            json.dump(data, output_file, indent=2, sort_keys=True)
            output_file.write("\n")
        os.rename(tmp_filename, filename)


class SingleDetectorLikelihoodModel(object):
    """Likelihood model used to map (rho_m, chisq) to a scalar rank.

    The notation follows single_detector_notes.tex:

        d = (rho_m, chi_r^2)
        r = ln L
          = ln P(chi_r^2 | rho_m, H1)
            - ln P(chi_r^2 | rho_m, H0)
            + 0.5 rho_m^2 + const.

    The H1 term is a beta-mixture of noncentral chi-square distributions with
    lambda = beta^2 rho_m^2 sum_delta |C_{j,m}(Delta)|^2.
    The refined H0 term follows Wguo's convention and is also noncentral,
    with a fixed high-mismatch noise beta.  Large chi-square therefore receives
    a much stronger penalty in the likelihood ratio.
    """

    def __init__(self,
                 signal_dof,
                 noise_dof,
                 beta_grid=None,
                 beta_weights=None,
                 default_autocorr_power=1.0,
                 snr_log_weight=0.5,
                 rank_offset=0.0,
                 noise_beta=-1.0,
                 ncx2_max_terms=200):
        self.signal_dof = float(signal_dof)
        self.noise_dof = float(noise_dof)
        self.beta_grid = list(beta_grid or [0.0])
        if beta_weights is None:
            self.beta_weights = [1.0] * len(self.beta_grid)
        else:
            self.beta_weights = list(beta_weights)
        self.default_autocorr_power = float(default_autocorr_power)
        self.snr_log_weight = float(snr_log_weight)
        self.rank_offset = float(rank_offset)
        self.noise_beta = float(noise_beta)
        self.ncx2_max_terms = int(ncx2_max_terms)
        self.noise_dof_calibration = {}
        self.signal_dof_calibration = {}

        if len(self.beta_grid) != len(self.beta_weights):
            raise ValueError("beta_grid and beta_weights must have same length")
        if sum(self.beta_weights) <= 0.0:
            raise ValueError("beta_weights must have positive total weight")

        total_weight = float(sum(self.beta_weights))
        self.beta_weights = [float(w) / total_weight
                             for w in self.beta_weights]

    def to_dict(self):
        return {
            "signal_dof": self.signal_dof,
            "noise_dof": self.noise_dof,
            "beta_grid": self.beta_grid,
            "beta_weights": self.beta_weights,
            "default_autocorr_power": self.default_autocorr_power,
            "snr_log_weight": self.snr_log_weight,
            "rank_offset": self.rank_offset,
            "noise_beta": self.noise_beta,
            "ncx2_max_terms": self.ncx2_max_terms,
            "noise_dof_calibration": calibration_to_json(
                self.noise_dof_calibration),
            "signal_dof_calibration": calibration_to_json(
                self.signal_dof_calibration),
        }

    @classmethod
    def from_dict(cls, data):
        model = cls(
            signal_dof=data.get("signal_dof", 2.0),
            noise_dof=data.get("noise_dof", 2.0),
            beta_grid=data.get("beta_grid") or uniform_beta_grid(0.03, 31),
            beta_weights=data.get("beta_weights"),
            default_autocorr_power=data.get("default_autocorr_power", 1.0),
            snr_log_weight=data.get("snr_log_weight", 0.5),
            rank_offset=data.get("rank_offset", 0.0),
            noise_beta=data.get("noise_beta", -1.0),
            ncx2_max_terms=data.get("ncx2_max_terms", 200))
        model.noise_dof_calibration = calibration_from_json(
            data.get("noise_dof_calibration", {}))
        model.signal_dof_calibration = calibration_from_json(
            data.get("signal_dof_calibration", {}))
        return model

    def set_noise_dof_calibration(self, calibration):
        self.noise_dof_calibration = calibration or {}

    def set_signal_dof_calibration(self, calibration):
        self.signal_dof_calibration = calibration or {}

    def noise_dof_for(self, rho, ifo=None, dof=None):
        if is_finite_positive(dof):
            return float(dof)
        return self._dof_for(rho, ifo, self.noise_dof_calibration,
                             self.noise_dof)

    def signal_dof_for(self, rho, ifo=None, dof=None):
        if is_finite_positive(dof):
            return float(dof)
        return self._dof_for(rho, ifo, self.signal_dof_calibration,
                             self.signal_dof)

    def _dof_for(self, rho, ifo, calibration, default):
        candidates = []
        if ifo is not None and ifo in calibration:
            candidates.extend(calibration[ifo])
        if "default" in calibration:
            candidates.extend(calibration["default"])
        for entry in candidates:
            lo = json_load_float(entry.get("rho_min"))
            hi = json_load_float(entry.get("rho_max"))
            if lo is None:
                lo = -float("inf")
            if hi is None:
                hi = float("inf")
            if float(rho) >= lo and float(rho) < hi:
                return float(entry.get("dof", default))
        return float(default)

    def noncentrality(self, rho, beta, autocorr_power=None):
        power = self.default_autocorr_power
        if autocorr_power is not None:
            power = float(autocorr_power)
        return float(beta) * float(beta) * float(rho) * float(rho) * power

    def log_signal_shape_pdf(self, rho, chisq, autocorr_power=None, ifo=None,
                             dof=None):
        dof = self.signal_dof_for(rho, ifo, dof=dof)
        x = dof * float(chisq)
        terms = []
        for beta, weight in zip(self.beta_grid, self.beta_weights):
            if weight <= 0.0:
                continue
            lam = self.noncentrality(rho, beta, autocorr_power)
            terms.append(math.log(weight) + noncentral_chisq_logpdf(
                x, dof, lam, max_terms=self.ncx2_max_terms))
        return math.log(dof) + logsumexp(terms)

    def log_noise_shape_pdf(self, rho, chisq, autocorr_power=None, ifo=None,
                            dof=None):
        # Wguo's method uses a noncentral chi-square H0 shape model.  The
        # noncentrality is intentionally large compared with the H1 beta-grid,
        # which penalizes triggers whose local chi-square shape is too glitchy.
        dof = self.noise_dof_for(rho, ifo, dof=dof)
        x = dof * float(chisq)
        lam = self.noncentrality(rho, self.noise_beta, autocorr_power)
        return math.log(dof) + noncentral_chisq_logpdf(
            x, dof, lam, max_terms=self.ncx2_max_terms)

    def log_likelihood_ratio(self, rho, chisq, autocorr_power=None, ifo=None,
                             dof=None):
        shape_llr = (
            self.log_signal_shape_pdf(rho, chisq, autocorr_power, ifo=ifo,
                                      dof=dof)
            - self.log_noise_shape_pdf(rho, chisq, autocorr_power, ifo=ifo,
                                       dof=dof))
        snr_llr = self.snr_log_weight * float(rho) * float(rho)
        return shape_llr + snr_llr + self.rank_offset

    def rank(self, rho, chisq, autocorr_power=None, ifo=None, dof=None):
        return self.log_likelihood_ratio(rho, chisq, autocorr_power, ifo=ifo,
                                         dof=dof)


def calibration_to_json(calibration):
    output = {}
    for ifo, entries in (calibration or {}).items():
        output[ifo] = []
        for entry in entries:
            copied = dict(entry)
            copied["rho_min"] = json_safe_float(copied.get("rho_min"))
            copied["rho_max"] = json_safe_float(copied.get("rho_max"))
            output[ifo].append(copied)
    return output


def calibration_from_json(calibration):
    output = {}
    for ifo, entries in (calibration or {}).items():
        output[ifo] = []
        for entry in entries:
            copied = dict(entry)
            copied["rho_min"] = json_load_float(copied.get("rho_min"))
            copied["rho_max"] = json_load_float(copied.get("rho_max"))
            output[ifo].append(copied)
    return output


def central_chisq_logpdf(x, dof):
    """Log PDF of a central chi-square distribution."""

    x = float(x)
    dof = float(dof)
    if x <= 0.0 or dof <= 0.0:
        return LOG_ZERO

    half_dof = 0.5 * dof
    return ((half_dof - 1.0) * math.log(x) - 0.5 * x
            - half_dof * math.log(2.0) - math.lgamma(half_dof))


def noncentral_chisq_term_mode_guess(x, dof, noncentrality):
    """Approximate the dominant Poisson-mixture index.

    Consecutive terms in the noncentral chi-square mixture satisfy

        t[n + 1] / t[n] ~= lambda * x / (2 * (n + 1) * (dof + 2n)).

    Solving this ratio equal to one gives a good starting point even when the
    dominant term is far from n = 0.
    """

    x = float(x)
    dof = float(dof)
    lam = float(noncentrality)
    if x <= 0.0 or dof <= 0.0 or lam <= 0.0:
        return 0
    b = 2.0 * (dof + 2.0)
    c = 2.0 * dof - lam * x
    disc = b * b - 16.0 * c
    if disc <= 0.0:
        return 0
    return max(0, int((-b + math.sqrt(disc)) / 8.0))


def poisson_logpmf(n, mean):
    if n < 0:
        return LOG_ZERO
    mean = float(mean)
    if mean <= 0.0:
        return 0.0 if n == 0 else LOG_ZERO
    return -mean + float(n) * math.log(mean) - math.lgamma(float(n) + 1.0)


def noncentral_chisq_logpdf(x,
                            dof,
                            noncentrality,
                            max_terms=200,
                            rel_tol=1.0e-12):
    """Log PDF of a noncentral chi-square using a Poisson mixture.

    The identity used here is

        chi2_ncx2(x; nu, lambda)
        = sum_n Pois(n; lambda/2) chi2(x; nu + 2n).

    This avoids a scipy dependency and keeps the first prototype usable in the
    older SPIIR Python environment.
    """

    x = float(x)
    dof = float(dof)
    lam = float(noncentrality)
    if lam <= 0.0:
        return central_chisq_logpdf(x, dof)

    half_lam = 0.5 * lam

    def log_term(n):
        return (poisson_logpmf(n, half_lam)
                + central_chisq_logpdf(x, dof + 2.0 * n))

    mode = noncentral_chisq_term_mode_guess(x, dof, lam)
    mode_term = log_term(mode)

    while mode > 0:
        previous_term = log_term(mode - 1)
        if previous_term <= mode_term:
            break
        mode -= 1
        mode_term = previous_term

    while True:
        next_term = log_term(mode + 1)
        if next_term <= mode_term:
            break
        mode += 1
        mode_term = next_term

    cutoff = mode_term + math.log(rel_tol)
    side_limit = max(int(max_terms),
                     int(12.0 * math.sqrt(max(1.0, half_lam))) + 50)

    terms = [mode_term]

    n = mode - 1
    steps = 0
    while n >= 0 and steps < side_limit:
        term = log_term(n)
        if term < cutoff:
            break
        terms.append(term)
        n -= 1
        steps += 1

    n = mode + 1
    steps = 0
    while steps < side_limit:
        term = log_term(n)
        if term < cutoff:
            break
        terms.append(term)
        n += 1
        steps += 1

    return logsumexp(terms)


def logsumexp(values):
    if not values:
        return LOG_ZERO
    max_value = max(values)
    if max_value <= LOG_ZERO / 2.0:
        return LOG_ZERO
    return max_value + math.log(sum(math.exp(v - max_value) for v in values))


class SingleDetectorResult(object):
    """Final single-detector result point for plotting or alert merging."""

    __slots__ = ("ifo", "category", "rho", "chisq", "llr", "rank", "far",
                 "far_source", "neg_log10_far", "direct_far",
                 "direct_far_source", "direct_neg_log10_far",
                 "calculated_far", "calculated_far_source",
                 "calculated_neg_log10_far", "tmplt_idx", "bankid",
                 "end_time", "end_time_ns", "source_row")

    def __init__(self, feature, llr, far, far_source=None,
                 direct_far=None, direct_far_source=None):
        self.ifo = feature.ifo
        self.category = feature.category
        self.rho = feature.rho
        self.chisq = feature.chisq
        self.llr = llr
        # Keep rank as a compatibility alias for older plotting scripts.  In
        # the single-detector branch this value is the LLR coordinate.
        self.rank = llr
        self.far = far
        self.far_source = far_source or FAR_SOURCE_UNASSIGNED
        self.neg_log10_far = neg_log10_far(far)
        self.direct_far = direct_far
        self.direct_far_source = direct_far_source or FAR_SOURCE_UNASSIGNED
        self.direct_neg_log10_far = (
            neg_log10_far(direct_far)
            if direct_far is not None
            else "")
        self.calculated_far = self.direct_far
        self.calculated_far_source = self.direct_far_source
        self.calculated_neg_log10_far = self.direct_neg_log10_far
        self.tmplt_idx = feature.tmplt_idx
        self.bankid = feature.bankid
        self.end_time = feature.end_time
        self.end_time_ns = feature.end_time_ns
        self.source_row = feature.source_row


class RankBackground(object):
    """One-dimensional single-detector FAR-LLR calibration model.

    During cold start, the current artificial-shift background ranks provide a
    direct empirical FAR.  The persistent background file stores the full raw
    detector-local trigger background, plus derived LLR/FAR support points.  On
    load, the support curve is rebuilt from the full trigger set so assignment
    never depends on a thinned plotting proxy.
    """

    DEFAULT_FAR_FIT_BOUNDARY = 1.0e-2
    DEFAULT_FAR_PRETAIL_BOUNDARY = 1.0e-1

    def __init__(self, fit_min_points=20, far_floor_count=1.0,
                 far_fit_boundary=DEFAULT_FAR_FIT_BOUNDARY,
                 far_fit_pretail_boundary=DEFAULT_FAR_PRETAIL_BOUNDARY):
        self._ranks = []
        self.livetime = 0.0
        self.livetime_segments = []
        self.far_llr_points = []
        self.background_triggers = []
        self.fit_min_points = int(fit_min_points)
        self.far_floor_count = float(far_floor_count)
        self.far_fit_boundary = float(far_fit_boundary)
        self.far_fit_pretail_boundary = float(far_fit_pretail_boundary)
        self._fit_cache = None

    def _invalidate_fit_cache(self):
        self._fit_cache = None

    def add_rank(self, rank):
        bisect.insort(self._ranks, float(rank))
        self._invalidate_fit_cache()

    def extend_ranks(self, ranks):
        for rank in ranks:
            bisect.insort(self._ranks, float(rank))
        self._invalidate_fit_cache()

    def reset_rank_support(self):
        self._ranks = []
        self.far_llr_points = []
        self.background_triggers = []
        self._invalidate_fit_cache()

    def add_livetime(self, seconds, gps=None):
        seconds = float(seconds)
        self.livetime += seconds
        if gps is not None:
            self.livetime_segments.append({
                "gps": json_safe_float(gps),
                "seconds": seconds,
            })
        self._invalidate_fit_cache()

    def add_far_llr_point(self, llr, far, gps=None):
        if not is_finite_number(llr):
            return
        point = {
            "llr": float(llr),
            "far": float(far) if is_finite_positive(far) else None,
            "gps": json_safe_float(gps),
        }
        self.far_llr_points.append(point)
        self._invalidate_fit_cache()

    def add_background_trigger(self, feature, rank=None):
        if feature is None:
            return
        self.background_triggers.append(feature.to_background_dict(rank=rank))
        self._invalidate_fit_cache()

    def current_background_triggers(self):
        rows = []
        for row in self.background_triggers:
            if not isinstance(row, dict):
                continue
            if row.get("ifo") is None:
                continue
            try:
                rho = json_load_float(row.get("rho"))
                chisq = json_load_float(row.get("chisq"))
            except (TypeError, ValueError):
                continue
            if not (is_finite_positive(rho) and is_finite_positive(chisq)):
                continue
            copied = dict(row)
            copied["rho"] = json_safe_float(rho)
            copied["chisq"] = json_safe_float(chisq)
            for key in ("llr", "rank", "autocorr_power", "dof",
                        "end_time", "end_time_ns"):
                if copied.get(key) is not None:
                    copied[key] = json_safe_float(json_load_float(
                        copied.get(key)))
            rows.append(copied)
        return rows

    def current_far_llr_points(self):
        points = []
        for point in self.far_llr_points:
            llr = point.get("llr")
            if not is_finite_number(llr):
                continue
            far = self.direct_far(llr)
            if not is_finite_positive(far):
                far = point.get("far")
            points.append({
                "llr": float(llr),
                "far": float(far) if is_finite_positive(far) else None,
                "gps": json_safe_float(point.get("gps")),
            })
        return points

    def prune_far_llr_points(self, max_age_seconds=None, reference_gps=None):
        if max_age_seconds is None or max_age_seconds <= 0.0:
            return
        gps_values = [
            json_load_float(point.get("gps"))
            for point in self.far_llr_points
            if point.get("gps") is not None
        ]
        gps_values.extend(
            json_load_float(segment.get("gps"))
            for segment in self.livetime_segments
            if segment.get("gps") is not None)
        for row in self.background_triggers:
            if row.get("end_time") is None:
                continue
            try:
                gps = feature_gps_seconds(
                    SingleDetectorFeature.from_background_dict(row))
            except (TypeError, ValueError):
                gps = None
            if gps is not None:
                gps_values.append(gps)
        if not gps_values:
            return
        if reference_gps is None:
            reference_gps = max(gps_values)
        cutoff = float(reference_gps) - float(max_age_seconds)
        kept = []
        for point in self.far_llr_points:
            gps = json_load_float(point.get("gps"))
            if gps is None or gps >= cutoff:
                kept.append(point)
        self.far_llr_points = kept
        self._ranks = sorted(float(point["llr"]) for point in kept
                             if point.get("llr") is not None)
        self._invalidate_fit_cache()

        if self.background_triggers:
            kept_triggers = []
            for row in self.background_triggers:
                try:
                    feature = SingleDetectorFeature.from_background_dict(row)
                except (TypeError, ValueError):
                    continue
                gps = feature_gps_seconds(feature)
                if gps is None or gps >= cutoff:
                    kept_triggers.append(row)
            self.background_triggers = kept_triggers
            self._invalidate_fit_cache()

        if self.livetime_segments:
            kept_segments = []
            for segment in self.livetime_segments:
                gps = json_load_float(segment.get("gps"))
                if gps is None or gps >= cutoff:
                    kept_segments.append(segment)
            self.livetime_segments = kept_segments
            self.livetime = sum(float(segment.get("seconds", 0.0) or 0.0)
                                for segment in self.livetime_segments)
            self._invalidate_fit_cache()

    def merge(self, other):
        self.extend_ranks(other._ranks)
        self.far_llr_points.extend(other.far_llr_points)
        self.background_triggers.extend(other.background_triggers)
        if other.livetime_segments:
            for segment in other.livetime_segments:
                self.add_livetime(segment.get("seconds", 0.0),
                                  gps=json_load_float(segment.get("gps")))
        else:
            self.add_livetime(other.livetime)

    def count_ge(self, rank):
        idx = bisect.bisect_left(self._ranks, float(rank))
        return len(self._ranks) - idx

    def tail_probability(self, rank):
        if not self._ranks:
            return 0.0
        return self.count_ge(rank) / float(len(self._ranks))

    def direct_far(self, rank):
        if self.livetime <= 0.0:
            return float("inf")
        count = max(float(self.count_ge(rank)), self.far_floor_count)
        return count / self.livetime

    def far(self, rank, use_fit=True):
        return self.far_with_source(rank, use_fit=use_fit)[0]

    def far_with_source(self, rank, use_fit=True):
        if use_fit:
            fitted = self.fitted_far(rank)
            if fitted is not None:
                return fitted, FAR_SOURCE_FIT_LOOKUP

        direct = self.direct_far(rank)
        if direct == float("inf"):
            return direct, FAR_SOURCE_UNASSIGNED
        if use_fit:
            return direct, FAR_SOURCE_DIRECT_FALLBACK
        return direct, FAR_SOURCE_BOOTSTRAP_DIRECT

    def fitted_far(self, llr):
        fit = self._fitted_log10_far_curve()
        if fit is None:
            return None
        xs, log_fars, tail_slope, tail_intercept = fit
        llr = float(llr)
        idx = bisect.bisect_left(xs, llr)
        if idx <= 0:
            log_far = log_fars[0]
        elif idx >= len(xs):
            if tail_slope is not None and tail_intercept is not None:
                log_far = tail_slope * llr + tail_intercept
            else:
                log_far = log_fars[-1]
        else:
            x0 = xs[idx - 1]
            x1 = xs[idx]
            y0 = log_fars[idx - 1]
            y1 = log_fars[idx]
            if x1 == x0:
                log_far = min(y0, y1)
            else:
                weight = (llr - x0) / (x1 - x0)
                log_far = y0 + weight * (y1 - y0)
        return math.pow(10.0, log_far)

    @staticmethod
    def _median(values):
        values = sorted(values)
        if not values:
            return None
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return 0.5 * (values[mid - 1] + values[mid])

    @staticmethod
    def _interpolate(xs, ys, x):
        idx = bisect.bisect_left(xs, x)
        if idx <= 0:
            return ys[0]
        if idx >= len(xs):
            return ys[-1]
        x0 = xs[idx - 1]
        x1 = xs[idx]
        y0 = ys[idx - 1]
        y1 = ys[idx]
        if x1 == x0:
            return min(y0, y1)
        weight = (x - x0) / (x1 - x0)
        return y0 + weight * (y1 - y0)

    @staticmethod
    def _linear_fit(points):
        if len(points) < 2:
            return None, None
        n = float(len(points))
        sx = sum(point[0] for point in points)
        sy = sum(point[1] for point in points)
        sxx = sum(point[0] * point[0] for point in points)
        sxy = sum(point[0] * point[1] for point in points)
        denom = n * sxx - sx * sx
        if denom == 0.0:
            return None, None
        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
        return slope, intercept

    @staticmethod
    def _fit_line_through_fixed_point(points, x0, y0):
        if len(points) < 2:
            return None, None
        denom = sum((x - x0) * (x - x0) for x, _y in points)
        if denom <= 0.0:
            return None, None
        slope = sum((x - x0) * (y - y0) for x, y in points) / denom
        if not is_finite_number(slope) or slope >= 0.0:
            return None, None
        return slope, y0 - slope * x0

    @staticmethod
    def _monotonic_log_fars(values):
        running = None
        output = []
        for value in values:
            running = value if running is None else min(running, value)
            output.append(running)
        return output

    @classmethod
    def _finalize_log10_fit_curve(cls, fit_xs, fit_log_fars):
        final_xs = []
        final_log_fars = []
        running = None
        for x, y in sorted(zip(fit_xs, fit_log_fars)):
            if final_xs and x == final_xs[-1]:
                final_log_fars[-1] = min(final_log_fars[-1], y)
                continue
            running = y if running is None else min(running, y)
            final_xs.append(x)
            final_log_fars.append(running)
        return final_xs, final_log_fars

    @classmethod
    def _binned_curve_by_index(cls, points, max_bins):
        points = list(points)
        if len(points) <= max_bins:
            xs = [point[0] for point in points]
            ys = cls._monotonic_log_fars(point[1] for point in points)
            return list(zip(xs, ys))
        output = []
        npoints = len(points)
        for ibin in range(int(max_bins)):
            start = int(round(ibin * npoints / float(max_bins)))
            end = int(round((ibin + 1) * npoints / float(max_bins)))
            if end <= start:
                continue
            chunk = points[start:end]
            output.append((
                float(cls._median(point[0] for point in chunk)),
                float(cls._median(point[1] for point in chunk)),
            ))
        xs = [point[0] for point in output]
        ys = cls._monotonic_log_fars(point[1] for point in output)
        return list(zip(xs, ys))

    @classmethod
    def _binned_curve_by_x(cls, points, max_bins):
        points = list(points)
        if len(points) <= max_bins:
            xs = [point[0] for point in points]
            ys = cls._monotonic_log_fars(point[1] for point in points)
            return list(zip(xs, ys))
        x_min = points[0][0]
        x_max = points[-1][0]
        if x_max <= x_min:
            return points
        bins = [[] for _idx in range(int(max_bins))]
        scale = float(max_bins) / (x_max - x_min)
        for point in points:
            idx = int((point[0] - x_min) * scale)
            if idx < 0:
                idx = 0
            elif idx >= max_bins:
                idx = int(max_bins) - 1
            bins[idx].append(point)
        output = []
        for chunk in bins:
            if not chunk:
                continue
            output.append((
                float(cls._median(point[0] for point in chunk)),
                float(cls._median(point[1] for point in chunk)),
            ))
        xs = [point[0] for point in output]
        ys = cls._monotonic_log_fars(point[1] for point in output)
        return list(zip(xs, ys))

    @staticmethod
    def _weighted_local_linear_y(xs, ys, robust_weights, idx, frac):
        npoints = len(xs)
        if npoints == 0:
            return None
        x0 = xs[idx]
        nearest = sorted(abs(x - x0) for x in xs)
        window = max(3, int(math.ceil(float(frac) * npoints)))
        window = min(window, npoints)
        radius = nearest[window - 1]
        if radius <= 0.0:
            return ys[idx]

        sw = swx = swy = swxx = swxy = 0.0
        for x, y, robust_weight in zip(xs, ys, robust_weights):
            distance = abs(x - x0)
            if distance > radius:
                continue
            u = distance / radius
            weight = (1.0 - u * u * u)
            weight = weight * weight * weight * robust_weight
            if weight <= 0.0:
                continue
            dx = x - x0
            sw += weight
            swx += weight * dx
            swy += weight * y
            swxx += weight * dx * dx
            swxy += weight * dx * y
        if sw <= 0.0:
            return ys[idx]
        denom = sw * swxx - swx * swx
        if denom <= 0.0:
            return swy / sw
        return (swy * swxx - swx * swxy) / denom

    @classmethod
    def _lowess_smooth(cls, points, frac=0.22, robust_iterations=2):
        if len(points) < 4:
            return list(points)
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        robust_weights = [1.0] * len(points)
        smooth = list(ys)
        for _iteration in range(max(1, int(robust_iterations))):
            smooth = [
                cls._weighted_local_linear_y(xs, ys, robust_weights, idx, frac)
                for idx in range(len(points))
            ]
            residuals = [y - yhat for y, yhat in zip(ys, smooth)]
            median_residual = cls._median(residuals)
            mad = cls._median(abs(value - median_residual)
                              for value in residuals)
            if mad is None or mad <= 0.0:
                break
            scale = 6.0 * 1.4826 * mad
            new_weights = []
            for residual in residuals:
                u = abs(residual - median_residual) / scale
                if u >= 1.0:
                    new_weights.append(0.0)
                else:
                    weight = 1.0 - u * u
                    new_weights.append(weight * weight)
            robust_weights = new_weights
        smooth[0] = ys[0]
        smooth[-1] = ys[-1]
        smooth = cls._monotonic_log_fars(smooth)
        return list(zip(xs, smooth))

    @classmethod
    def _smooth_before_tail_curve(cls, raw_xs, raw_log_fars, x_left, x_right):
        points = [
            (x, y) for x, y in zip(raw_xs, raw_log_fars)
            if x_left <= x <= x_right
        ]
        if len(points) < 4:
            return points
        binned = cls._binned_curve_by_index(points, 220)
        smoothed = cls._lowess_smooth(binned, frac=0.22, robust_iterations=2)
        if smoothed:
            smoothed[0] = (smoothed[0][0], binned[0][1])
            smoothed[-1] = (smoothed[-1][0], binned[-1][1])
            xs = [point[0] for point in smoothed]
            ys = cls._monotonic_log_fars(point[1] for point in smoothed)
            smoothed = list(zip(xs, ys))
        return smoothed

    def _wguo_broken_log10_far_curve(self, raw_xs, raw_monotonic,
                                     pretail_far, tail_far):
        if not is_finite_positive(tail_far):
            return None
        _pretail_log_far = (
            math.log10(float(pretail_far))
            if is_finite_positive(pretail_far) else None)
        tail_log_far = math.log10(float(tail_far))

        tail_idx = min(
            range(len(raw_xs)),
            key=lambda idx: abs(raw_monotonic[idx] - tail_log_far))
        x_handoff = raw_xs[tail_idx]

        tail_points = [
            (x, y) for x, y in zip(raw_xs, raw_monotonic)
            if x >= x_handoff
        ]
        min_tail_points = max(2, min(self.fit_min_points, 20,
                                     len(tail_points)))
        if len(tail_points) < min_tail_points:
            return None

        fit_tail_points = list(tail_points)
        tail_slope, tail_intercept = self._fit_line_through_fixed_point(
            fit_tail_points, x_handoff, tail_log_far)
        if (tail_slope is None or not is_finite_number(tail_slope)
                or tail_slope >= 0.0 or tail_intercept is None):
            return None
        tail_intercept = tail_log_far - tail_slope * x_handoff

        x_end = raw_xs[-1]
        fit_xs = list(raw_xs[:tail_idx + 1])
        fit_log_fars = list(raw_monotonic[:tail_idx + 1])
        if fit_xs:
            fit_log_fars[-1] = tail_log_far
        if x_end > x_handoff:
            fit_xs.append(x_end)
            fit_log_fars.append(tail_slope * x_end + tail_intercept)

        final_xs, final_log_fars = self._finalize_log10_fit_curve(
            fit_xs, fit_log_fars)
        return final_xs, final_log_fars, tail_slope, tail_intercept

    def _fitted_log10_far_curve(self):
        if self._fit_cache is not None:
            return self._fit_cache
        points = []
        for point in self.current_far_llr_points():
            far = point.get("far")
            llr = point.get("llr")
            if is_finite_positive(far) and llr is not None:
                points.append((float(llr), math.log10(float(far))))
        if len(points) < self.fit_min_points:
            return None
        points.sort()

        # Collapse duplicate LLR values and keep the most conservative FAR.
        raw_xs = []
        raw_log_fars = []
        for llr, log_far in points:
            if raw_xs and llr == raw_xs[-1]:
                raw_log_fars[-1] = min(raw_log_fars[-1], log_far)
            else:
                raw_xs.append(llr)
                raw_log_fars.append(log_far)

        # Enforce the physical monotonicity: larger LLR cannot imply larger FAR.
        raw_monotonic = self._monotonic_log_fars(raw_log_fars)

        if len(raw_xs) < self.fit_min_points:
            self._fit_cache = (raw_xs, raw_monotonic, None, None)
            return self._fit_cache

        # Wguo-style FAR assignment: use the empirical/interpolated background
        # curve before the tail, then fit the high-LLR tail as a constrained
        # line using all available tail points. The old robust tail clipping
        # path is archived out of the production code.
        boundary_far = self.far_fit_boundary
        if not is_finite_positive(boundary_far):
            boundary_far = self.DEFAULT_FAR_FIT_BOUNDARY
        pretail_far = self.far_fit_pretail_boundary
        if not is_finite_positive(pretail_far):
            pretail_far = 10.0 * boundary_far

        broken_fit = self._wguo_broken_log10_far_curve(
            raw_xs, raw_monotonic, pretail_far, boundary_far)
        if broken_fit is not None:
            self._fit_cache = broken_fit
            return self._fit_cache

        # Fall back to the smoothed one-boundary fit if the current support does
        # not contain enough points for the broken-tail path.
        boundary_log_far = math.log10(boundary_far)
        boundary_idx = min(
            range(len(raw_xs)),
            key=lambda idx: abs(raw_monotonic[idx] - boundary_log_far))
        x_boundary = raw_xs[boundary_idx]
        before_tail_points = self._smooth_before_tail_curve(
            raw_xs, raw_monotonic, raw_xs[0], x_boundary)
        if before_tail_points:
            pxs = [point[0] for point in before_tail_points]
            pys = [point[1] for point in before_tail_points]
            pys[-1] = boundary_log_far
            pys = self._monotonic_log_fars(pys)
            before_tail_points = list(zip(pxs, pys))

        raw_tail_points = [
            (x, y) for x, y in zip(raw_xs, raw_monotonic)
            if x >= x_boundary
        ]
        tail_points = self._binned_curve_by_x(raw_tail_points, 80)
        fit_tail_points = [
            (x, y) for x, y in tail_points
            if x > x_boundary
        ]

        tail_slope = None
        tail_intercept = None
        min_tail_points = max(2, min(self.fit_min_points, 20,
                                     len(fit_tail_points)))
        if len(fit_tail_points) >= min_tail_points:
            fit_tail_points = list(fit_tail_points)
            tail_slope, tail_intercept = self._fit_line_through_fixed_point(
                fit_tail_points, x_boundary, boundary_log_far)

        fit_xs = []
        fit_log_fars = []
        if before_tail_points:
            for x, y in before_tail_points:
                fit_xs.append(x)
                fit_log_fars.append(y)
        else:
            fit_xs = list(raw_xs[:boundary_idx + 1])
            fit_log_fars = list(raw_monotonic[:boundary_idx + 1])
            if fit_log_fars:
                fit_log_fars[-1] = boundary_log_far
        if tail_slope is not None and raw_xs[-1] > x_boundary:
            fit_xs.append(raw_xs[-1])
            fit_log_fars.append(tail_slope * raw_xs[-1] + tail_intercept)

        # Remove duplicate x values and enforce monotonicity one last time.
        final_xs, final_log_fars = self._finalize_log10_fit_curve(
            fit_xs, fit_log_fars)
        self._fit_cache = (final_xs, final_log_fars,
                           tail_slope, tail_intercept)
        return self._fit_cache

    def __len__(self):
        return len(self._ranks)

    def to_dict(self):
        far_llr_points = self.current_far_llr_points()
        background_triggers = self.current_background_triggers()
        return {
            "livetime": self.livetime,
            "livetime_segments": list(self.livetime_segments),
            "background_triggers": background_triggers,
            "background_trigger_count": len(background_triggers),
            "far_llr_points": far_llr_points,
            "support_count": len(far_llr_points),
            "fit_min_points": self.fit_min_points,
            "far_floor_count": self.far_floor_count,
            "far_fit_boundary": self.far_fit_boundary,
            "far_fit_pretail_boundary": self.far_fit_pretail_boundary,
        }

    @classmethod
    def from_dict(cls, data):
        bg = cls(fit_min_points=data.get("fit_min_points", 20),
                 far_floor_count=data.get("far_floor_count", 1.0),
                 far_fit_boundary=data.get(
                     "far_fit_boundary", cls.DEFAULT_FAR_FIT_BOUNDARY),
                 far_fit_pretail_boundary=data.get(
                     "far_fit_pretail_boundary",
                     cls.DEFAULT_FAR_PRETAIL_BOUNDARY))
        bg.livetime = float(data.get("livetime", 0.0) or 0.0)
        bg.livetime_segments = []
        for segment in data.get("livetime_segments", []):
            seconds = float(segment.get("seconds", 0.0) or 0.0)
            gps = json_safe_float(json_load_float(segment.get("gps")))
            bg.livetime_segments.append({"gps": gps, "seconds": seconds})
        if bg.livetime_segments:
            bg.livetime = sum(segment["seconds"]
                              for segment in bg.livetime_segments)
        bg.background_triggers = []
        for row in data.get("background_triggers", []):
            if not isinstance(row, dict):
                continue
            rank_value = row.get("rank")
            if rank_value is None:
                rank_value = row.get("llr")
            try:
                bg.background_triggers.append(
                    SingleDetectorFeature.from_background_dict(
                        row).to_background_dict(
                            rank=(json_load_float(rank_value)
                                  if rank_value is not None else None)))
            except (TypeError, ValueError):
                continue
        bg._ranks = sorted(float(rank) for rank in data.get("ranks", []))
        bg.far_llr_points = []
        for point in data.get("far_llr_points", []):
            if is_finite_number(point.get("llr")):
                bg.far_llr_points.append({
                    "llr": float(point.get("llr")),
                    "far": (float(point.get("far"))
                            if is_finite_positive(point.get("far")) else None),
                    "gps": json_safe_float(point.get("gps")),
                })
        if not bg.far_llr_points and bg._ranks:
            bg.far_llr_points = [
                {"llr": float(rank), "far": None, "gps": None}
                for rank in bg._ranks
            ]
        if not bg._ranks:
            bg._ranks = sorted(float(point["llr"])
                               for point in bg.far_llr_points)
        if bg.background_triggers and not bg._ranks:
            ranks = []
            for row in bg.background_triggers:
                rank = row.get("rank")
                if rank is None:
                    rank = row.get("llr")
                if is_finite_number(rank):
                    ranks.append(float(rank))
            bg._ranks = sorted(ranks)
        if bg.background_triggers and not bg.far_llr_points and bg._ranks:
            bg.far_llr_points = []
            for row in bg.background_triggers:
                rank = row.get("rank")
                if rank is None:
                    rank = row.get("llr")
                if not is_finite_number(rank):
                    continue
                bg.far_llr_points.append({
                    "llr": float(rank),
                    "far": None,
                    "gps": json_safe_float(feature_gps_seconds(
                        SingleDetectorFeature.from_background_dict(row))),
                })
        bg._invalidate_fit_cache()
        return bg


def neg_log10_far(far):
    far = float(far)
    if far <= 0.0:
        return float("inf")
    return -math.log10(far)


def detector_index(ifo):
    return pipe_macro.IFO_MAP.index(ifo)


def active_ifos_from_row(row, candidate_ifos=None):
    ifos = tuple(candidate_ifos or pipe_macro.IFO_MAP)
    row_ifos = getattr(row, "ifos", None)
    if row_ifos is None:
        return ifos
    if not isinstance(row_ifos, string_types):
        row_ifos = str(row_ifos)
    return tuple(ifo for ifo in ifos if ifo in row_ifos)


def read_detector_value(row, field, ifo, default=None):
    """Read detector-indexed values from wrapped C rows or XML table rows."""

    ifo_id = detector_index(ifo)

    if field == "end_time_sngl" and hasattr(row, "_end_time_sngl"):
        return getattr(row, "_end_time_sngl")[ifo_id]

    if hasattr(row, field):
        value = getattr(row, field)
        try:
            return value[ifo_id]
        except (TypeError, IndexError):
            pass

    xml_name = "%s_%s" % (field, ifo)
    if hasattr(row, xml_name):
        return getattr(row, xml_name)

    return default


def write_single_far_to_row(row, ifo, far):
    """Write the assigned single-detector FAR back when the row supports it."""

    ifo_id = detector_index(ifo)
    if hasattr(row, "far_sngl"):
        row.far_sngl[ifo_id] = far
        return

    xml_name = "far_sngl_%s" % ifo
    if hasattr(row, xml_name):
        setattr(row, xml_name, far)


def row_livetime_seconds(row, default_livetime_step=1.0):
    livetime = getattr(row, "livetime", None)
    if livetime is None or float(livetime) <= 0.0:
        return default_livetime_step
    return float(livetime)


def row_gps_seconds(row):
    seconds = getattr(row, "end_time", None)
    if seconds is None:
        seconds = getattr(row, "timestamp", None)
    if seconds is None:
        return None
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return None
    nanoseconds = getattr(row, "end_time_ns", None)
    if nanoseconds is not None:
        try:
            seconds += float(nanoseconds) * 1.0e-9
        except (TypeError, ValueError):
            pass
    return seconds


def feature_gps_seconds(feature):
    if feature.end_time is None:
        return None
    try:
        seconds = float(feature.end_time)
    except (TypeError, ValueError):
        return None
    if feature.end_time_ns is not None:
        try:
            seconds += float(feature.end_time_ns) * 1.0e-9
        except (TypeError, ValueError):
            pass
    return seconds


PLOT_ROW_FIELDS = [
    "source_file",
    "source_row",
    "feature_csv_row",
    "category",
    "ifo",
    "rho",
    "chisq",
    "llr",
    "rank",
    "far",
    "far_source",
    "neg_log10_far",
    "assigned_far",
    "assigned_far_source",
    "assigned_neg_log10_far",
    "direct_far",
    "direct_far_source",
    "direct_neg_log10_far",
    "calculated_far",
    "calculated_far_source",
    "calculated_neg_log10_far",
    "assign_bg_id",
    "assign_bg_file",
    "assign_bg_start",
    "assign_bg_end",
    "assign_bg_livetime_seconds",
    "assign_bg_update_utc",
    "assign_bg_update_unix",
    "assignment_utc",
    "assignment_unix",
    "tmplt_idx",
    "bankid",
    "end_time",
    "end_time_ns",
]


def results_to_plot_rows(results):
    """Return dictionaries for the final (rho, -log10 FAR) plane."""

    rows = []
    for result in results:
        source = result.source_row if isinstance(result.source_row, dict) else {}
        rows.append({
            "source_file": source.get("source_file", ""),
            "source_row": source.get("source_row", ""),
            "feature_csv_row": source.get("_feature_csv_row_index", ""),
            "category": result.category,
            "ifo": result.ifo,
            "rho": result.rho,
            "chisq": result.chisq,
            "llr": result.llr,
            "rank": result.rank,
            "far": result.far,
            "far_source": result.far_source,
            "neg_log10_far": result.neg_log10_far,
            "assigned_far": result.far,
            "assigned_far_source": result.far_source,
            "assigned_neg_log10_far": result.neg_log10_far,
            "direct_far": result.direct_far,
            "direct_far_source": result.direct_far_source,
            "direct_neg_log10_far": result.direct_neg_log10_far,
            "calculated_far": result.calculated_far,
            "calculated_far_source": result.calculated_far_source,
            "calculated_neg_log10_far": result.calculated_neg_log10_far,
            "assign_bg_id": "",
            "assign_bg_file": "",
            "assign_bg_start": "",
            "assign_bg_end": "",
            "assign_bg_livetime_seconds": "",
            "assign_bg_update_utc": "",
            "assign_bg_update_unix": "",
            "assignment_utc": "",
            "assignment_unix": "",
            "tmplt_idx": result.tmplt_idx,
            "bankid": result.bankid,
            "end_time": result.end_time,
            "end_time_ns": result.end_time_ns,
        })
    return rows


def open_csv_for_write(filename):
    if sys.version_info[0] >= 3:
        return open(filename, "w", newline="")
    return open(filename, "wb")


def open_csv_for_read(filename):
    if sys.version_info[0] >= 3:
        return open(filename, "r", newline="")
    return open(filename, "rb")


def open_csv_for_append(filename):
    if sys.version_info[0] >= 3:
        return open(filename, "a", newline="")
    return open(filename, "ab")


def ensure_parent_directory(filename):
    directory = os.path.dirname(os.path.abspath(filename))
    if directory and not os.path.exists(directory):
        os.makedirs(directory)


def ensure_plot_csv(output_filename):
    ensure_parent_directory(output_filename)
    if os.path.exists(output_filename) and os.path.getsize(output_filename) > 0:
        with open_csv_for_read(output_filename) as input_file:
            reader = csv.reader(input_file)
            try:
                header = next(reader)
            except StopIteration:
                header = []
        if list(header) != list(PLOT_ROW_FIELDS):
            raise ValueError(
                "existing single-detector CSV has incompatible columns: %s" %
                output_filename)
        return
    with open_csv_for_write(output_filename) as output_file:
        writer = csv.DictWriter(output_file, fieldnames=PLOT_ROW_FIELDS)
        writer.writeheader()


def write_plot_rows_csv(rows, output_filename):
    ensure_parent_directory(output_filename)
    with open_csv_for_write(output_filename) as output_file:
        writer = csv.DictWriter(output_file, fieldnames=PLOT_ROW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict((field, row.get(field, "")) for field in
                                 PLOT_ROW_FIELDS))


def write_far_llr_support_csv(branch, output_filename):
    fields = ["ifo", "llr", "far", "gps", "livetime"]
    ensure_parent_directory(output_filename)
    with open_csv_for_write(output_filename) as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        for ifo in sorted(branch.background):
            background = branch.background[ifo]
            for point in background.current_far_llr_points():
                writer.writerow({
                    "ifo": ifo,
                    "llr": point.get("llr"),
                    "far": point.get("far"),
                    "gps": point.get("gps"),
                    "livetime": background.livetime,
                })


def append_plot_rows_csv(rows, output_filename):
    if not rows:
        ensure_plot_csv(output_filename)
        return
    ensure_plot_csv(output_filename)
    with open_csv_for_append(output_filename) as output_file:
        writer = csv.DictWriter(output_file, fieldnames=PLOT_ROW_FIELDS)
        for row in rows:
            writer.writerow(dict((field, row.get(field, "")) for field in
                                 PLOT_ROW_FIELDS))


if __name__ == "__main__":
    sys.exit(main())
