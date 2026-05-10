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
        "--snr-bins", default="",
        help="comma-separated SNR bin edges for nu_eff calibration, e.g. 4,6,8,12,inf")
    single.add_argument("--min-calibration-count", type=int, default=50)
    single.add_argument("--noise-dof", type=float, default=2.0)
    single.add_argument("--signal-dof", type=float, default=None)
    single.add_argument("--beta-max", type=float, default=0.03)
    single.add_argument("--beta-grid-size", type=int, default=31)
    single.add_argument("--default-autocorr-power", type=float, default=1.0)
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
        calibrate_noise_dof=args.calibrate_noise_dof,
        calibration_snr_bins=parse_snr_bins(args.snr_bins),
        min_calibration_count=args.min_calibration_count)
    write_plot_rows_csv(rows, args.output)
    print("wrote %d single-detector rows to %s" % (len(rows), args.output))


def calculate_single_detector_rows(postcoh_filenames,
                                   ifos=("H1", "L1"),
                                   min_snr=4.0,
                                   default_livetime_step=1.0,
                                   likelihood_model=None,
                                   background_input=None,
                                   background_output=None,
                                   calibrate_noise_dof=False,
                                   calibration_snr_bins=None,
                                   min_calibration_count=50):
    ifos = split_ifos(ifos)
    branch = SingleDetectorBranch(
        likelihood_model or make_default_likelihood_model(),
        ifos=ifos,
        min_snr=min_snr)
    if background_input:
        branch.load_background_file(background_input)

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
        branch.accumulate_background_feature(feature)

    for seconds, active_ifos in livetime_updates:
        branch.add_livetime(seconds, active_ifos)

    results = [
        branch.assign_feature(feature) for feature in foreground_features
    ]
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


def features_from_postcoh_row(row, ifos=None, min_snr=0.0):
    """Extract all detector-local feature points from one postcoh row."""

    features = []
    ifos = tuple(ifos or pipe_macro.IFO_MAP)
    min_snr = float(min_snr)

    for ifo in ifos:
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

    def __init__(self, likelihood_model, ifos=None, min_snr=0.0):
        self.likelihood_model = likelihood_model
        self.ifos = tuple(ifos or pipe_macro.IFO_MAP)
        self.min_snr = float(min_snr)
        self.background = dict((ifo, RankBackground()) for ifo in self.ifos)

    def rank_feature(self, feature, autocorr_power=None):
        return self.likelihood_model.rank(feature.rho, feature.chisq,
                                          autocorr_power, ifo=feature.ifo)

    def add_livetime(self, seconds, ifos=None):
        for ifo in tuple(ifos or self.ifos):
            if ifo in self.background:
                self.background[ifo].add_livetime(seconds)

    def load_background_file(self, filename):
        state = SingleFarLlrBackgroundFile.load(filename)
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
        far = self.background[feature.ifo].far(rank)
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
        autocorr_power = None
        tmplt_idx = getattr(row, "tmplt_idx", None)
        if autocorr_power_by_template is not None and tmplt_idx is not None:
            autocorr_power = autocorr_power_by_template.get(tmplt_idx)

        if is_background == FLAG_BACKGROUND:
            for feature in features:
                self.accumulate_background_feature(feature,
                                                   autocorr_power=autocorr_power)
            return []

        if is_background == FLAG_FOREGROUND:
            for feature in features:
                result = self.assign_feature(feature,
                                             autocorr_power=autocorr_power)
                write_single_far_to_row(row, feature.ifo, result.far)
                results.append(result)
            return results

        if is_background == FLAG_EMPTY and livetime_step is not None:
            self.add_livetime(livetime_step, active_ifos_from_row(row,
                                                                  self.ifos))
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

    This is intentionally a one-dimensional rank/livetime file.  It is not the
    coherent two-dimensional XML statistics file used by cohfar.
    """

    VERSION = 1

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
            "description": "single-detector FAR-LLR rank background and livetime",
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

    def __init__(self):
        self._ranks = []
        self.livetime = 0.0

    def add_rank(self, rank):
        bisect.insort(self._ranks, float(rank))

    def extend_ranks(self, ranks):
        for rank in ranks:
            self.add_rank(rank)

    def add_livetime(self, seconds):
        self.livetime += float(seconds)

    def merge(self, other):
        self.extend_ranks(other._ranks)
        self.add_livetime(other.livetime)

    def count_ge(self, rank):
        idx = bisect.bisect_left(self._ranks, float(rank))
        return len(self._ranks) - idx

    def tail_probability(self, rank):
        if not self._ranks:
            return 0.0
        return self.count_ge(rank) / float(len(self._ranks))

    def far(self, rank):
        if self.livetime <= 0.0:
            return 0.0
        return self.count_ge(rank) / self.livetime

    def __len__(self):
        return len(self._ranks)

    def to_dict(self):
        return {
            "livetime": self.livetime,
            "ranks": list(self._ranks),
            "count": len(self._ranks),
        }

    @classmethod
    def from_dict(cls, data):
        bg = cls()
        bg.livetime = float(data.get("livetime", 0.0) or 0.0)
        bg._ranks = sorted(float(rank) for rank in data.get("ranks", []))
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
