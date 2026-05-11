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
import json
import math
import os
import re
import sys

try:
    from gstlal.pipemodules import pipe_macro
except ImportError:
    import pipe_macro

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


FLAG_FOREGROUND = 0
FLAG_BACKGROUND = 1
FLAG_EMPTY = 2

LOG_ZERO = -1.0e300
DIRECT_FAR_LIVETIME_FLOOR = 1.0


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Offline single-detector FAR calculation for SPIIR postcoh rows")
    subparsers = parser.add_subparsers(dest="command")

    single = subparsers.add_parser(
        "single", help="calculate single-detector rank and FAR from raw postcoh")
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
        "--bootstrap-far", action="store_true",
        help=("allow direct rank-tail FAR assignment when no FAR-LLR fit is "
              "available; intended only for cold-start/calibration runs"))
    single.add_argument(
        "--snr-bins", default="",
        help="comma-separated SNR bin edges for nu_eff calibration, e.g. 4,6,8,12,inf")
    single.add_argument("--min-calibration-count", type=int, default=50)
    single.add_argument("--noise-dof", type=float, default=2.0)
    single.add_argument("--signal-dof", type=float, default=None)
    single.add_argument("--beta-max", type=float, default=0.03)
    single.add_argument("--beta-grid-size", type=int, default=31)
    single.add_argument(
        "--iir-bank", action="append", default=[],
        help=("IIR bank spec used by the online pipeline, for example "
              "H1:/path/bank.xml.gz,L1:/path/bank.xml.gz; may be repeated. "
              "The single-detector likelihood reads these files to load the "
              "template-dependent sum_delta |C_jm(Delta)|^2."))
    single.add_argument(
        "--default-autocorr-power", type=float, default=1.0,
        help=("fallback sum_delta |C_jm(Delta)|^2 used only when no matching "
              "template autocorrelation power can be read from --iir-bank"))
    single.add_argument("--snr-log-weight", type=float, default=0.5)
    single.set_defaults(func=command_single)
    return parser


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
        bootstrap_far=args.bootstrap_far,
        calibrate_noise_dof=args.calibrate_noise_dof,
        calibration_snr_bins=parse_snr_bins(args.snr_bins),
        min_calibration_count=args.min_calibration_count,
        iir_banks=args.iir_bank)
    write_plot_rows_csv(rows, args.output)
    print("wrote %d single-detector rows to %s" % (len(rows), args.output))


def calculate_single_detector_rows(postcoh_filenames,
                                   ifos=("H1", "L1"),
                                   min_snr=4.0,
                                   default_livetime_step=1.0,
                                   likelihood_model=None,
                                   background_input=None,
                                   background_output=None,
                                   bootstrap_far=False,
                                   calibrate_noise_dof=False,
                                   calibration_snr_bins=None,
                                   min_calibration_count=50,
                                   iir_banks=None):
    ifos = split_ifos(ifos)
    allow_direct_far = (not background_input) or bool(bootstrap_far)
    branch = SingleDetectorBranch(
        likelihood_model or make_default_likelihood_model(),
        ifos=ifos,
        min_snr=min_snr,
        allow_direct_far=allow_direct_far)
    if background_input:
        branch.load_background_file(
            background_input,
            require_fits=not bool(bootstrap_far))

    autocorr_power_map = load_iir_bank_autocorr_powers(iir_banks)

    background_features = []
    foreground_features = []
    livetime_updates = []

    for filename in postcoh_filenames:
        xmldoc, table = load_postcoh_table(filename)
        try:
            for row in table:
                is_background = getattr(row, "is_background", None)
                if is_background == FLAG_BACKGROUND:
                    for feature in features_from_postcoh_row(row, ifos, min_snr):
                        feature.source_row = None
                        background_features.append(feature)
                elif is_background == FLAG_FOREGROUND:
                    features = features_from_postcoh_row(row, ifos, min_snr)
                    for feature in features:
                        feature.source_row = None
                    foreground_features.extend(features)
                elif is_background == FLAG_EMPTY:
                    livetime_updates.append((
                        row_livetime_seconds(row, default_livetime_step),
                        active_ifos_from_row(row, ifos)))
        finally:
            xmldoc.unlink()

    if calibrate_noise_dof:
        branch.calibrate_noise_dof_from_features(
            background_features,
            snr_bins=calibration_snr_bins,
            min_count=min_calibration_count)

    for feature in background_features:
        branch.accumulate_background_feature(
            feature,
            autocorr_power=autocorr_power_for_feature(
                feature, autocorr_power_map))

    for seconds, active_ifos in livetime_updates:
        branch.add_livetime(seconds, active_ifos)

    results = []
    for feature in foreground_features:
        results.append(
            branch.assign_feature(
                feature,
                autocorr_power=autocorr_power_for_feature(
                    feature, autocorr_power_map)))
    if background_output:
        branch.write_background_file(background_output)
    return results_to_plot_rows(results)


def expand_path_patterns(patterns):
    filenames = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            filenames.extend(matches)
        elif os.path.exists(pattern):
            filenames.append(pattern)
    return filenames


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


def parse_iir_bank_specs(iir_bank_specs):
    """Return ``(ifo, filename)`` pairs from repeated pipeline --iir-bank args."""

    pairs = []
    for spec in iir_bank_specs or ():
        for piece in str(spec).split(","):
            piece = piece.strip()
            if not piece:
                continue
            if ":" not in piece:
                raise ValueError("invalid --iir-bank entry %r" % piece)
            ifo, filename = piece.split(":", 1)
            ifo = ifo.strip()
            filename = filename.strip()
            if not ifo or not filename:
                raise ValueError("invalid --iir-bank entry %r" % piece)
            pairs.append((ifo, filename))
    return pairs


def bankid_from_bank_filename(bank_filename):
    """Infer the SPIIR bank id using the same filename convention as spiirparts."""

    tmp_name = os.path.split(bank_filename)[-1]
    tmp_name = re.sub(r"[HLVK]1", "", tmp_name)
    match = re.search(r"\d{1,4}", tmp_name)
    if match is None:
        raise ValueError("cannot infer bank id from IIR bank filename %s" %
                         bank_filename)
    stripped = match.group().lstrip("0")
    if not stripped:
        return 0
    return int(stripped)


def xml_local_name(tag):
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def array_name_matches(xml_name, requested_name):
    xml_name = str(xml_name or "")
    return (xml_name == requested_name
            or xml_name == requested_name + ":array"
            or xml_name.split(":", 1)[0] == requested_name)


def reshape_flat_values(values, dims):
    if not dims:
        return [values]
    total = 1
    for dim in dims:
        total *= int(dim)
    if total != len(values):
        raise ValueError("array dimension product %d does not match %d values"
                         % (total, len(values)))
    if len(dims) == 1:
        return [values]
    nrow = int(dims[0])
    ncol = int(len(values) / nrow)
    return [values[i * ncol:(i + 1) * ncol] for i in range(nrow)]


def read_ligolw_array_rows(filename, requested_name):
    """Read a numeric LIGO-LW Array as row-major Python lists.

    This intentionally avoids importing spiirbank.cbc_template_iir.  That
    module is Python-2-style in the current SPIIR tree, while this launcher
    often runs under Python 3 on OzSTAR.
    """

    import gzip
    import xml.etree.ElementTree as ElementTree

    opener = gzip.open if filename.endswith(".gz") else open
    with opener(filename, "rb") as input_file:
        tree = ElementTree.parse(input_file)

    for elem in tree.getroot().iter():
        if xml_local_name(elem.tag) != "Array":
            continue
        if not array_name_matches(elem.attrib.get("Name"), requested_name):
            continue

        dims = []
        stream_text = []
        for child in elem:
            child_name = xml_local_name(child.tag)
            if child_name == "Dim":
                dim_text = (child.text or child.attrib.get("Length")
                            or child.attrib.get("length") or "").strip()
                if dim_text:
                    dims.append(int(dim_text))
            elif child_name == "Stream":
                stream_text.append(child.text or "")

        text = " ".join(stream_text).replace(",", " ")
        values = [float(piece) for piece in text.split()]
        return reshape_flat_values(values, dims)

    raise ValueError("IIR bank %s does not contain array %s" %
                     (filename, requested_name))


def read_iir_bank_autocorr_powers(bank_filename):
    """Read per-template sum_delta |C_jm(Delta)|^2 from an IIR bank XML."""

    real_rows = read_ligolw_array_rows(
        bank_filename, "autocorrelation_bank_real")
    imag_rows = read_ligolw_array_rows(
        bank_filename, "autocorrelation_bank_imag")
    if len(real_rows) != len(imag_rows):
        raise ValueError("IIR bank %s has inconsistent autocorrelation real "
                         "and imaginary arrays" % bank_filename)

    powers = []
    for real_row, imag_row in zip(real_rows, imag_rows):
        if len(real_row) != len(imag_row):
            raise ValueError(
                "IIR bank %s has inconsistent autocorrelation row lengths" %
                bank_filename)
        powers.append(float(sum(real * real + imag * imag
                                for real, imag in zip(real_row, imag_row))))
    return powers


def load_iir_bank_autocorr_powers(iir_bank_specs):
    """Build lookup keys for template-dependent autocorrelation power."""

    autocorr_powers = {}
    for ifo, bank_filename in parse_iir_bank_specs(iir_bank_specs):
        bankid = bankid_from_bank_filename(bank_filename)
        for tmplt_idx, power in enumerate(
                read_iir_bank_autocorr_powers(bank_filename)):
            autocorr_powers[(ifo, bankid, tmplt_idx)] = power
    return autocorr_powers


def int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def autocorr_power_for_feature(feature, autocorr_power_map):
    """Find the best available autocorrelation power for a feature."""

    if not autocorr_power_map:
        return None
    tmplt_idx = int_or_none(feature.tmplt_idx)
    bankid = int_or_none(feature.bankid)
    if tmplt_idx is None:
        return None

    keys = (
        (feature.ifo, bankid, tmplt_idx),
        (None, bankid, tmplt_idx),
        (feature.ifo, None, tmplt_idx),
        (None, None, tmplt_idx),
        (bankid, tmplt_idx),
        tmplt_idx,
    )
    for key in keys:
        if key in autocorr_power_map:
            return autocorr_power_map[key]
    return None


def is_finite_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return not (math.isnan(value) or math.isinf(value))


def features_from_postcoh_row(row, ifos=None, min_snr=0.0):
    """Extract all detector-local feature points from one postcoh row."""

    features = []
    ifos = tuple(ifos or pipe_macro.IFO_MAP)
    min_snr = float(min_snr)
    active_ifos = active_ifos_from_row(row, ifos)

    for ifo in active_ifos:
        rho = read_detector_value(row, "snglsnr", ifo, default=0.0)
        chisq = read_detector_value(row, "chisq", ifo, default=0.0)

        if rho is None or chisq is None:
            continue
        if not is_finite_number(rho) or not is_finite_number(chisq):
            continue
        rho = float(rho)
        chisq = float(chisq)
        if rho <= 0.0 or rho < min_snr or chisq <= 0.0:
            continue

        features.append(
            SingleDetectorFeature(
                ifo=ifo,
                rho=rho,
                chisq=chisq,
                tmplt_idx=getattr(row, "tmplt_idx", None),
                bankid=getattr(row, "bankid", None),
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
                 "end_time", "end_time_ns", "is_background", "source_row")

    def __init__(self,
                 ifo,
                 rho,
                 chisq,
                 tmplt_idx=None,
                 bankid=None,
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
        self.end_time = end_time
        self.end_time_ns = end_time_ns
        self.is_background = is_background
        self.source_row = source_row


class SingleDetectorBranch(object):
    """Prototype branch after cuda_postcoh for one or more detectors."""

    def __init__(self,
                 likelihood_model,
                 ifos=None,
                 min_snr=0.0,
                 allow_direct_far=True):
        self.likelihood_model = likelihood_model
        self.ifos = tuple(ifos or pipe_macro.IFO_MAP)
        self.min_snr = float(min_snr)
        self.allow_direct_far = bool(allow_direct_far)
        self.background = dict((ifo, RankBackground()) for ifo in self.ifos)

    def rank_feature(self, feature, autocorr_power=None):
        return self.likelihood_model.rank(feature.rho, feature.chisq,
                                          autocorr_power, ifo=feature.ifo)

    def add_livetime(self, seconds, ifos=None):
        for ifo in tuple(ifos or self.ifos):
            if ifo in self.background:
                self.background[ifo].add_livetime(seconds)

    def load_background_file(self, filename, require_fits=True):
        state = SingleFarLlrBackgroundFile.load(
            filename,
            required_ifos=self.ifos,
            require_fits=require_fits,
            allow_partial_ifos=True)
        if state.model is not None:
            self.likelihood_model = state.model
        for ifo, background in state.backgrounds.items():
            if ifo in self.background:
                self.background[ifo].merge(background)

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
        if livetime is not None:
            bg.add_livetime(livetime)
        return rank

    def assign_feature(self, feature, autocorr_power=None):
        rank = self.rank_feature(feature, autocorr_power)
        far = self.background[feature.ifo].far(
            rank,
            allow_direct=self.allow_direct_far)
        return SingleDetectorResult(feature, rank, far)

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
        features = features_from_postcoh_row(row, self.ifos, self.min_snr)
        is_background = getattr(row, "is_background", None)

        if is_background == FLAG_BACKGROUND:
            for feature in features:
                autocorr_power = autocorr_power_for_feature(
                    feature, autocorr_power_by_template)
                self.accumulate_background_feature(feature,
                                                   autocorr_power=autocorr_power)
            return []

        if is_background == FLAG_FOREGROUND:
            for feature in features:
                autocorr_power = autocorr_power_for_feature(
                    feature, autocorr_power_by_template)
                result = self.assign_feature(feature,
                                             autocorr_power=autocorr_power)
                write_single_far_to_row(row, feature.ifo, result.far)
                results.append(result)
            return results

        if is_background == FLAG_EMPTY and livetime_step is not None:
            self.add_livetime(
                row_livetime_seconds(row, livetime_step),
                active_ifos_from_row(row, self.ifos))
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
        rank_offset=0.0)


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
        rank_offset=0.0)


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
    not the coherent two-dimensional XML statistics file used by cohfar, and it
    is not the raw trigger-background stream.  Cold-start runs can build the
    first fit from direct rank-tail FAR points; later runs should assign FAR by
    evaluating the stored fitted mapping.
    """

    VERSION = 3
    SCHEMA = "spiir.single_detector_far_llr_background"

    def __init__(self, ifos=None, model=None, backgrounds=None):
        self.ifos = tuple(ifos or ())
        self.model = model
        self.backgrounds = dict(backgrounds or {})

    @classmethod
    def load(cls,
             filename,
             required_ifos=None,
             require_fits=False,
             allow_partial_ifos=False):
        with open(filename, "r") as input_file:
            data = json.load(input_file)
        cls.validate_top_level(data, filename)

        required_ifos = split_ifos(required_ifos or ())
        file_ifos = split_ifos(data.get("ifos", ()))
        backgrounds_data = data.get("backgrounds")

        model_data = data.get("likelihood_model")
        model = None
        if model_data:
            model = SingleDetectorLikelihoodModel.from_dict(model_data)
        backgrounds = {}
        for ifo, bg_data in backgrounds_data.items():
            backgrounds[ifo] = RankBackground.from_dict(
                bg_data,
                ifo=ifo,
                require_fit=False)

        if required_ifos:
            present_ifos = [
                ifo for ifo in required_ifos
                if ifo in file_ifos and ifo in backgrounds
            ]
            missing_ifos = [
                ifo for ifo in required_ifos
                if ifo not in present_ifos
            ]
            if missing_ifos and not allow_partial_ifos:
                raise ValueError(
                    "single FAR-LLR background %s is missing required IFO(s) "
                    "%s" % (filename, ",".join(missing_ifos)))
            if allow_partial_ifos and not present_ifos:
                raise ValueError(
                    "single FAR-LLR background %s contains none of the "
                    "requested IFOs %s" %
                    (filename, ",".join(required_ifos)))
            if require_fits:
                fitted_ifos = [
                    ifo for ifo in present_ifos
                    if backgrounds[ifo].has_far_fit()
                ]
                if not allow_partial_ifos:
                    missing_fit_ifos = [
                        ifo for ifo in present_ifos
                        if not backgrounds[ifo].has_far_fit()
                    ]
                    if missing_fit_ifos:
                        raise ValueError(
                            "single FAR-LLR background %s is missing FAR-LLR "
                            "fit(s) for required IFO(s) %s" %
                            (filename, ",".join(missing_fit_ifos)))
                elif not fitted_ifos:
                    raise ValueError(
                        "single FAR-LLR background %s contains no requested "
                        "IFO with FAR-LLR fit points" % filename)
        return cls(ifos=file_ifos, model=model,
                   backgrounds=backgrounds)

    @classmethod
    def validate_top_level(cls, data, filename="<memory>"):
        if not isinstance(data, dict):
            raise ValueError(
                "single FAR-LLR background %s must be a JSON object" %
                filename)
        if data.get("version") != cls.VERSION:
            raise ValueError(
                "single FAR-LLR background %s has unsupported version %r; "
                "expected %r" %
                (filename, data.get("version"), cls.VERSION))
        if data.get("schema") != cls.SCHEMA:
            raise ValueError(
                "single FAR-LLR background %s has unsupported schema %r; "
                "expected %r" %
                (filename, data.get("schema"), cls.SCHEMA))
        if not isinstance(data.get("backgrounds"), dict):
            raise ValueError(
                "single FAR-LLR background %s must contain a backgrounds map"
                % filename)
        if "ifos" not in data:
            raise ValueError(
                "single FAR-LLR background %s must contain an ifos list"
                % filename)

    def dump(self, filename):
        directory = os.path.dirname(os.path.abspath(filename))
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        for background in self.backgrounds.values():
            background.prepare_for_dump()
        data = {
            "version": self.VERSION,
            "schema": self.SCHEMA,
            "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "description": "single-detector FAR-LLR fit background",
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
    """

    def __init__(self,
                 signal_dof,
                 noise_dof,
                 beta_grid=None,
                 beta_weights=None,
                 default_autocorr_power=1.0,
                 snr_log_weight=0.5,
                 rank_offset=0.0,
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

    def noise_dof_for(self, rho, ifo=None):
        return self._dof_for(rho, ifo, self.noise_dof_calibration,
                             self.noise_dof)

    def signal_dof_for(self, rho, ifo=None):
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

    def log_signal_shape_pdf(self, rho, chisq, autocorr_power=None, ifo=None):
        dof = self.signal_dof_for(rho, ifo)
        x = dof * float(chisq)
        terms = []
        for beta, weight in zip(self.beta_grid, self.beta_weights):
            if weight <= 0.0:
                continue
            lam = self.noncentrality(rho, beta, autocorr_power)
            terms.append(math.log(weight) + noncentral_chisq_logpdf(
                x, dof, lam, max_terms=self.ncx2_max_terms))
        return math.log(dof) + logsumexp(terms)

    def log_noise_shape_pdf(self, rho, chisq, ifo=None):
        # The first implementation uses a broadened central chi-square model.
        # Later this can be replaced by a rho-dependent empirical fit.
        dof = self.noise_dof_for(rho, ifo)
        x = dof * float(chisq)
        return math.log(dof) + central_chisq_logpdf(x, dof)

    def log_likelihood_ratio(self, rho, chisq, autocorr_power=None, ifo=None):
        shape_llr = (
            self.log_signal_shape_pdf(rho, chisq, autocorr_power, ifo=ifo)
            - self.log_noise_shape_pdf(rho, chisq, ifo=ifo))
        snr_llr = self.snr_log_weight * float(rho) * float(rho)
        return shape_llr + snr_llr + self.rank_offset

    def rank(self, rho, chisq, autocorr_power=None, ifo=None):
        return self.log_likelihood_ratio(rho, chisq, autocorr_power, ifo=ifo)


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
    log_half_lam = math.log(half_lam)
    log_weight = -half_lam
    terms = []

    best_term = LOG_ZERO
    for n in range(max_terms):
        term = log_weight + central_chisq_logpdf(x, dof + 2.0 * n)
        terms.append(term)
        if term > best_term:
            best_term = term
        elif best_term - term > -math.log(rel_tol) and n > half_lam:
            break

        log_weight += log_half_lam - math.log(n + 1.0)

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

    __slots__ = ("ifo", "category", "rho", "chisq", "rank", "far",
                 "neg_log10_far", "tmplt_idx", "bankid", "end_time",
                 "end_time_ns", "source_row")

    def __init__(self, feature, rank, far):
        self.ifo = feature.ifo
        self.category = feature.category
        self.rho = feature.rho
        self.chisq = feature.chisq
        self.rank = rank
        self.far = far
        self.neg_log10_far = neg_log10_far(far)
        self.tmplt_idx = feature.tmplt_idx
        self.bankid = feature.bankid
        self.end_time = feature.end_time
        self.end_time_ns = feature.end_time_ns
        self.source_row = feature.source_row


class RankBackground(object):
    """One-dimensional background accumulator in rank space."""

    FIT_KIND = "log_linear_llr_far"

    def __init__(self):
        self._ranks = []
        self.livetime = 0.0
        self.fit_points = []
        self.fit_kind = self.FIT_KIND
        self.fit_created_utc = None
        self.fit_source = "none"

    def add_rank(self, rank):
        if not is_finite_number(rank):
            raise ValueError("background rank must be finite")
        bisect.insort(self._ranks, float(rank))

    def extend_ranks(self, ranks):
        for rank in ranks:
            self.add_rank(rank)

    def add_livetime(self, seconds):
        if not is_finite_number(seconds):
            raise ValueError("background livetime must be finite")
        seconds = float(seconds)
        if seconds < 0.0:
            raise ValueError("background livetime must be non-negative")
        self.livetime += seconds

    def merge(self, other):
        self.extend_ranks(other._ranks)
        self.add_livetime(other.livetime)
        if other.fit_points:
            self.fit_points = list(other.fit_points)
            self.fit_kind = other.fit_kind
            self.fit_created_utc = other.fit_created_utc
            self.fit_source = other.fit_source

    def count_ge(self, rank):
        if not is_finite_number(rank):
            raise ValueError("background query rank must be finite")
        idx = bisect.bisect_left(self._ranks, float(rank))
        return len(self._ranks) - idx

    def tail_probability(self, rank):
        if not self._ranks:
            return 0.0
        return self.count_ge(rank) / float(len(self._ranks))

    def direct_far(self, rank):
        livetime = max(self.livetime, DIRECT_FAR_LIVETIME_FLOOR)
        return max(self.count_ge(rank), 1) / livetime

    def has_far_fit(self):
        return len(self.fit_points) > 0

    def far(self, rank, allow_direct=True):
        if self.has_far_fit():
            return self.fitted_far(rank)
        if not allow_direct:
            raise ValueError(
                "single-detector FAR requested without a FAR-LLR fit; "
                "direct rank-tail FAR is disabled outside bootstrap mode")
        return self.direct_far(rank)

    def fitted_far(self, rank):
        """Evaluate the stored LLR-to-FAR fit.

        The fit is a conservative piecewise log-linear interpolation of
        calibration points (rank, FAR).  Outside the calibrated rank range it
        clamps to the nearest calibrated FAR instead of extrapolating beyond
        the available background support.
        """

        if not self.fit_points:
            return self.direct_far(rank)

        rank = float(rank)
        points = self.fit_points
        if rank <= points[0][0]:
            return points[0][1]
        if rank >= points[-1][0]:
            return points[-1][1]

        ranks = [point[0] for point in points]
        idx = bisect.bisect_right(ranks, rank)
        left_rank, left_far = points[idx - 1]
        right_rank, right_far = points[idx]
        if right_rank == left_rank:
            return min(left_far, right_far)

        log_left = safe_log_far(left_far)
        log_right = safe_log_far(right_far)
        frac = (rank - left_rank) / (right_rank - left_rank)
        return math.exp(log_left + frac * (log_right - log_left))

    def prepare_for_dump(self, max_points=2000):
        if self._ranks:
            self.rebuild_far_fit(max_points=max_points)

    def rebuild_far_fit(self, max_points=2000):
        """Build calibration points for the production LLR-to-FAR lookup."""

        if not self._ranks:
            return

        points = []
        livetime = max(self.livetime, DIRECT_FAR_LIVETIME_FLOOR)
        n_ranks = len(self._ranks)
        idx = 0
        while idx < n_ranks:
            rank = self._ranks[idx]
            next_idx = bisect.bisect_right(self._ranks, rank, idx)
            count_ge = n_ranks - idx
            points.append((rank, count_ge / livetime))
            idx = next_idx

        self.fit_points = compact_fit_points(points, max_points=max_points)
        self.fit_kind = self.FIT_KIND
        self.fit_created_utc = datetime.datetime.utcnow().isoformat() + "Z"
        self.fit_source = "direct_rank_tail_bootstrap_or_update"

    def __len__(self):
        return len(self._ranks)

    def to_dict(self):
        return {
            "livetime": self.livetime,
            "ranks": list(self._ranks),
            "count": len(self._ranks),
            "far_fit": {
                "kind": self.fit_kind,
                "created_utc": self.fit_created_utc,
                "source": self.fit_source,
                "points": [
                    {"rank": rank, "far": far}
                    for rank, far in self.fit_points
                ],
            },
        }

    @classmethod
    def from_dict(cls, data, ifo=None, require_fit=False):
        if not isinstance(data, dict):
            raise ValueError("background for %s must be a JSON object" %
                             (ifo or "<unknown>"))
        bg = cls()
        livetime = data.get("livetime", 0.0) or 0.0
        if not is_finite_number(livetime) or float(livetime) < 0.0:
            raise ValueError("background livetime for %s must be finite and "
                             "non-negative" % (ifo or "<unknown>"))
        bg.livetime = float(livetime)

        ranks = data.get("ranks", []) or []
        if not isinstance(ranks, list):
            raise ValueError("background ranks for %s must be a list" %
                             (ifo or "<unknown>"))
        for rank in ranks:
            bg.add_rank(rank)

        fit_data = data.get("far_fit")
        if not isinstance(fit_data, dict):
            raise ValueError("background far_fit for %s must be an object" %
                             (ifo or "<unknown>"))
        if fit_data.get("kind") != cls.FIT_KIND:
            raise ValueError(
                "background far_fit kind for %s must be %r, got %r" %
                (ifo or "<unknown>", cls.FIT_KIND, fit_data.get("kind")))
        bg.fit_kind = fit_data.get("kind", cls.FIT_KIND)
        bg.fit_created_utc = fit_data.get("created_utc")
        bg.fit_source = fit_data.get("source", "loaded")
        points = fit_data.get("points", []) or []
        if not isinstance(points, list):
            raise ValueError("background far_fit points for %s must be a list"
                             % (ifo or "<unknown>"))
        parsed_points = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError(
                    "background far_fit point for %s must be an object" %
                    (ifo or "<unknown>"))
            rank = point.get("rank")
            far = point.get("far")
            if (not is_finite_number(rank) or not is_finite_number(far)
                    or float(far) <= 0.0):
                raise ValueError(
                    "background far_fit points for %s must contain finite "
                    "rank and positive finite FAR" % (ifo or "<unknown>"))
            parsed_points.append((float(rank), float(far)))
        bg.fit_points = sorted(parsed_points)
        bg.validate_fit(require_fit=require_fit, ifo=ifo)
        return bg

    def validate_fit(self, require_fit=False, ifo=None):
        label = ifo or "<unknown>"
        if self.fit_kind != self.FIT_KIND:
            raise ValueError("background far_fit kind for %s must be %r" %
                             (label, self.FIT_KIND))
        if require_fit and not self.fit_points:
            raise ValueError(
                "background for %s must contain positive FAR-LLR fit points" %
                label)
        previous_rank = None
        for rank, far in self.fit_points:
            if (not is_finite_number(rank) or not is_finite_number(far)
                    or float(far) <= 0.0):
                raise ValueError(
                    "background far_fit points for %s must be finite with "
                    "positive FAR" % label)
            if previous_rank is not None and rank <= previous_rank:
                raise ValueError(
                    "background far_fit ranks for %s must be strictly "
                    "increasing" % label)
            previous_rank = rank


def safe_log_far(far):
    far = float(far)
    if far <= 0.0:
        far = 1.0e-300
    return math.log(far)


def compact_fit_points(points, max_points=2000):
    points = list(points)
    if not points:
        return []
    max_points = max(2, int(max_points))
    if len(points) <= max_points:
        return points

    selected = []
    last_idx = len(points) - 1
    for out_idx in range(max_points):
        source_idx = int(round(out_idx * last_idx / float(max_points - 1)))
        if not selected or selected[-1] != points[source_idx]:
            selected.append(points[source_idx])
    return selected


def neg_log10_far(far):
    far = float(far)
    if far <= 0.0:
        return float("inf")
    return -math.log10(far)


def detector_index(ifo):
    return pipe_macro.IFO_MAP.index(ifo)


def active_ifos_from_row(row, candidate_ifos=None):
    """Return requested IFOs that are active on this row.

    Older postcoh rows may not carry an ``ifos`` field.  For those legacy rows
    the safest available interpretation is that all requested IFOs were active;
    rows with an ``ifos`` field are restricted to that row-local active set so
    stale detector slots from lock loss do not produce features or livetime.
    """

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
    if is_finite_number(livetime) and float(livetime) > 0.0:
        return float(livetime)
    if is_finite_number(default_livetime_step):
        default_livetime_step = float(default_livetime_step)
        if default_livetime_step > 0.0:
            return default_livetime_step
    return DIRECT_FAR_LIVETIME_FLOOR


PLOT_ROW_FIELDS = [
    "category",
    "ifo",
    "rho",
    "chisq",
    "rank",
    "far",
    "neg_log10_far",
    "tmplt_idx",
    "bankid",
    "end_time",
    "end_time_ns",
]


def results_to_plot_rows(results):
    """Return dictionaries for the final (rho, -log10 FAR) plane."""

    rows = []
    for result in results:
        rows.append({
            "category": result.category,
            "ifo": result.ifo,
            "rho": result.rho,
            "chisq": result.chisq,
            "rank": result.rank,
            "far": result.far,
            "neg_log10_far": result.neg_log10_far,
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


def write_plot_rows_csv(rows, output_filename):
    with open_csv_for_write(output_filename) as output_file:
        writer = csv.DictWriter(output_file, fieldnames=PLOT_ROW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict((field, row.get(field, "")) for field in
                                 PLOT_ROW_FIELDS))


if __name__ == "__main__":
    sys.exit(main())
