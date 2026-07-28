#!/usr/bin/env python3
"""Independent PDF numerical core for the separately run verification sidecar.

This file must never import crashcar code, a crashcar map, ledger, background,
helper or output. It reads original WGuo pickles directly.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import numbers
import pickle
from pathlib import Path

_PICKLE_DIR = Path(
    "/fred/oz016/wguo/packages/spiir/src/spiir/search/bank_dofs"
)
_PICKLE_SHA = {
    "H1": "edd29a0d1b614dc2de1e5fe83baf90c677489a8aa576dce0c623896d5d977c9e",
    "L1": "4217734b09c81cbe9ac75d47bdc7d0966e1690043f7870398e082d53530d488d",
}
_STEP = float.fromhex("0x1.89374bc6a7efap-9")
_LOG2PI = float.fromhex("0x1.d67f1c864beb4p+0")
_LOG64 = float.fromhex("0x1.0a2b23f3bab73p+2")
_HALF = float.fromhex("0x1.0000000000000p-1")

ASSIGNMENT_ASSIGNED = "ASSIGNED"
ASSIGNMENT_PENDING = "PENDING"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_index(value, name, upper_bound):
    if isinstance(value, bool):
        raise ValueError("%s must be a canonical integer" % name)
    if isinstance(value, numbers.Integral):
        index = int(value)
    elif isinstance(value, numbers.Real):
        numeric = float(value)
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise ValueError("%s must be an integral finite number" % name)
        index = int(numeric)
    elif isinstance(value, str):
        ascii_digits = all("0" <= character <= "9" for character in value)
        if (not value or not ascii_digits
                or (len(value) > 1 and value.startswith("0"))):
            raise ValueError(
                "%s must be a canonical decimal integer" % name)
        index = int(value)
    else:
        raise ValueError("%s has unsupported type" % name)
    if not 0 <= index <= int(upper_bound):
        raise ValueError("%s outside exact range" % name)
    return index


def fixed_dof(bankid):
    bank = _strict_index(bankid, "bankid", 383)
    if bank <= 99:
        return 120
    if bank <= 383:
        return 600
    raise AssertionError("strict bank partition incomplete")


def expected_pickle_directory():
    return str(_PICKLE_DIR)


class ActualPickleShapeSource:
    """Exact keyed H/L source; no broad, nearest, default or cross-IFO lookup."""

    def __init__(self):
        self._banks = {}
        for ifo in ("H1", "L1"):
            path = _PICKLE_DIR / (
                "%s_O3_FB_banks_magnitudes_and_dofs.pkl" % ifo)
            actual = _sha256_file(path)
            if actual != _PICKLE_SHA[ifo]:
                raise ValueError("%s pickle SHA drift" % ifo)
            with open(path, "rb") as stream:
                payload = pickle.load(stream)
            if set(payload) != set(range(416)):
                raise ValueError("%s bank key set drift" % ifo)
            for bankid in range(416):
                frame = payload[bankid]
                if (len(frame) != 1000
                        or "magnitudes" not in frame.columns):
                    raise ValueError("%s pickle shape drift" % ifo)
            self._banks[ifo] = payload

    def a_eff_and_dof(self, ifo, bankid, tmplt_idx):
        if ifo not in ("H1", "L1"):
            raise KeyError("only H1/L1 have single statistics")
        bank = _strict_index(bankid, "bankid", 383)
        template = _strict_index(tmplt_idx, "tmplt_idx", 999)
        dof = fixed_dof(bank)
        frame = self._banks[ifo][bank]
        if len(frame) != 1000 or "magnitudes" not in frame.columns:
            raise ValueError("actual pickle shape drift")
        magnitude = float(frame.iloc[template]["magnitudes"])
        if not (math.isfinite(magnitude) and magnitude > 0.0):
            raise ValueError("magnitude invalid")
        a_eff = magnitude * magnitude
        if not (math.isfinite(a_eff) and a_eff > 0.0):
            raise ValueError("A_eff invalid")
        return a_eff, dof


def _normal_log_density(value, centre, spread):
    residual = value - centre
    residual_square = residual * residual
    normalized_square = residual_square / spread
    logarithmic_spread = math.log(spread)
    normalization = _LOG2PI + logarithmic_spread
    bracket = normalization + normalized_square
    answer = -_HALF * bracket
    if not math.isfinite(answer):
        raise ArithmeticError("Gaussian term invalid")
    return answer


def pdf_gaussian_llr(rho, normalized_chisq, a_eff, dof):
    r = float(rho)
    q = float(normalized_chisq)
    shape = float(a_eff)
    nu_i = _strict_index(dof, "dof", 600)
    if nu_i not in (120, 600):
        raise ValueError("dof must be fixed 120 or 600")
    if not (math.isfinite(r) and r >= 4.0):
        raise ValueError("rho below inclusive threshold or nonfinite")
    if not (math.isfinite(q) and q > 0.0):
        raise ValueError("normalized chisq invalid")
    if not (math.isfinite(shape) and shape > 0.0):
        raise ValueError("A_eff invalid")

    nu = float(nu_i)
    r_squared = r * r
    observed = nu * q
    noncentral = r_squared * shape
    noise_mean = nu + noncentral
    twice_noncentral = 2.0 * noncentral
    noise_inner = nu + twice_noncentral
    noise_variance = 2.0 * noise_inner
    energy_term = r_squared / 2.0
    for name, value in (
        ("rho2", r_squared), ("x", observed), ("lambda0", noncentral),
        ("muN", noise_mean), ("tmpN", twice_noncentral),
        ("innerN", noise_inner), ("VN", noise_variance),
        ("rho2_half", energy_term),
    ):
        if not math.isfinite(value):
            raise ArithmeticError("%s nonfinite" % name)
    if noncentral <= 0.0 or noise_variance <= 0.0:
        raise ArithmeticError("nonpositive Gaussian domain")

    signal_logs = []
    for index in range(64):
        index_float = float(index)
        offset = _STEP * index_float
        beta = _STEP + offset
        beta_squared = beta * beta
        signal_noncentral = beta_squared * noncentral
        signal_mean = nu + signal_noncentral
        signal_twice = 2.0 * signal_noncentral
        signal_inner = nu + signal_twice
        signal_variance = 2.0 * signal_inner
        for name, value in (
            ("beta_product", offset), ("beta", beta),
            ("beta2", beta_squared), ("lambda1", signal_noncentral),
            ("mu1", signal_mean), ("tmp1", signal_twice),
            ("inner1", signal_inner), ("V1", signal_variance),
        ):
            if not math.isfinite(value):
                raise ArithmeticError("%s nonfinite" % name)
        if signal_noncentral <= 0.0 or signal_variance <= 0.0:
            raise ArithmeticError("invalid signal Gaussian domain")
        signal_logs.append(
            _normal_log_density(observed, signal_mean, signal_variance)
        )

    pivot = signal_logs[0]
    for item in signal_logs[1:]:
        if item > pivot:
            pivot = item
    accumulator = 0.0
    for item in signal_logs:
        difference = item - pivot
        exponential = math.exp(difference)
        accumulator = accumulator + exponential
        if not math.isfinite(accumulator):
            raise ArithmeticError("logsum accumulator invalid")
    if accumulator < 1.0:
        raise ArithmeticError("logsum accumulator below one")

    noise_log = _normal_log_density(
        observed, noise_mean, noise_variance)
    logarithmic_sum = math.log(accumulator)
    unweighted_signal = pivot + logarithmic_sum
    signal_log = unweighted_signal - _LOG64
    ratio_log = signal_log - noise_log
    answer = ratio_log + energy_term
    if not math.isfinite(answer):
        raise ArithmeticError("LLR invalid")
    return answer


def calculated_far(sorted_background_ranks, event_rank, livetime_seconds):
    ranks = list(float(x) for x in sorted_background_ranks)
    if any(not math.isfinite(value) for value in ranks):
        raise ValueError("background rank nonfinite")
    if ranks != sorted(ranks):
        raise ValueError("background ranks must be sorted")
    rank = float(event_rank)
    livetime = float(livetime_seconds)
    if not (math.isfinite(rank) and math.isfinite(livetime)
            and livetime > 0.0):
        raise ValueError("rank/livetime invalid")
    count = len(ranks) - bisect.bisect_left(ranks, rank)
    physical_count = count if count >= 1 else 1
    far = float(physical_count) / livetime
    if not (math.isfinite(far) and far > 0.0):
        raise ArithmeticError("Calculated FAR invalid")
    return far, count, count == 0


def nearest_background_far(sorted_background_ranks, event_rank,
                           livetime_seconds, r_tail=None):
    """Look up the empirical FAR at the background LLR nearest the event.

    The lower LLR wins an exact-distance tie, preserving the conservative
    larger FAR of the monotone empirical background curve.
    """
    ranks = [float(value) for value in sorted_background_ranks]
    if (not ranks or any(not math.isfinite(value) for value in ranks)
            or ranks != sorted(ranks)):
        raise ValueError("need finite sorted background ranks")
    rank = float(event_rank)
    if not math.isfinite(rank):
        raise ValueError("event rank nonfinite")
    candidates = sorted(set(ranks))
    if r_tail is not None:
        anchor = float(r_tail)
        if not math.isfinite(anchor):
            raise ValueError("r_tail nonfinite")
        candidates = [value for value in candidates if value <= anchor]
    if not candidates:
        raise ValueError("no eligible background LLR points")
    nearest_rank = min(
        candidates, key=lambda value: (abs(value - rank), value))
    far, _count, _floor = calculated_far(
        ranks, nearest_rank, livetime_seconds)
    return far, nearest_rank


def empirical_tail_point(
        sorted_background_ranks, livetime_seconds, tail_log10_far=-2.0):
    """Choose r_tail from empirical Calculated-FAR points only."""
    ranks = [float(value) for value in sorted_background_ranks]
    tail_anchor = float(tail_log10_far)
    if (not ranks or any(not math.isfinite(value) for value in ranks)
            or ranks != sorted(ranks)):
        raise ValueError("need finite sorted background ranks")
    if not math.isfinite(tail_anchor) or not tail_anchor < 0.0:
        raise ValueError("tail_log10_far must be finite and negative")
    unique_ranks = []
    for rank in ranks:
        if not unique_ranks or rank != unique_ranks[-1]:
            unique_ranks.append(rank)
    points = []
    for rank in unique_ranks:
        far, _count, _floor = calculated_far(
            ranks, rank, livetime_seconds)
        points.append((rank, math.log10(far)))
    tail_index = min(
        range(len(points)),
        key=lambda index: (
            abs(points[index][1] - tail_anchor), points[index][0]),
    )
    return points[tail_index][0], tuple(points)


def fit_anchored_tail(points, r_tail, tail_log10_far=-2.0):
    """Fit a negative slope through the configured fixed tail point."""
    anchor = float(r_tail)
    tail_anchor = float(tail_log10_far)
    if not math.isfinite(tail_anchor) or not tail_anchor < 0.0:
        raise ValueError("tail_log10_far must be finite and negative")
    if not math.isfinite(anchor):
        raise ValueError("r_tail nonfinite")
    numerator = 0.0
    denominator = 0.0
    for rank, log_far in points:
        rank = float(rank)
        log_far = float(log_far)
        if not (math.isfinite(rank) and math.isfinite(log_far)):
            raise ValueError("tail point nonfinite")
        if rank < anchor:
            continue
        dx = rank - anchor
        dy = log_far - tail_anchor
        denominator = denominator + dx * dx
        numerator = numerator + dx * dy
    if not (math.isfinite(numerator) and math.isfinite(denominator)
            and denominator > 0.0):
        raise ValueError("tail has insufficient finite support")
    slope = numerator / denominator
    if not (math.isfinite(slope) and slope < 0.0):
        raise ValueError("tail slope must be finite and negative")
    return slope


def build_anchored_tail(
        sorted_background_ranks, livetime_seconds, tail_log10_far=-2.0):
    r_tail, points = empirical_tail_point(
        sorted_background_ranks, livetime_seconds, tail_log10_far)
    slope = fit_anchored_tail(points, r_tail, tail_log10_far)
    return r_tail, slope, points


def assigned_far(sorted_background_ranks, event_rank, livetime_seconds,
                 r_tail, slope, tail_log10_far=-2.0):
    direct, count, floor = calculated_far(
        sorted_background_ranks, event_rank, livetime_seconds)
    rank = float(event_rank)
    anchor = float(r_tail)
    tail_anchor = float(tail_log10_far)
    if not math.isfinite(tail_anchor) or not tail_anchor < 0.0:
        raise ValueError("tail_log10_far must be finite and negative")
    if not math.isfinite(anchor):
        raise ValueError("r_tail nonfinite")
    if rank <= anchor:
        nearest, _nearest_rank = nearest_background_far(
            sorted_background_ranks, rank, livetime_seconds, anchor)
        return nearest, "direct", count, floor
    try:
        tail_slope = float(slope)
    except (TypeError, ValueError):
        raise ValueError("r>r_tail requires a finite negative slope")
    if not (math.isfinite(tail_slope) and tail_slope < 0.0):
        raise ValueError("r>r_tail requires a finite negative slope")
    dx = rank - anchor
    scaled = tail_slope * dx
    log_far = tail_anchor + scaled
    value = math.pow(10.0, log_far)
    if not (math.isfinite(value) and value > 0.0):
        raise ArithmeticError("tail Assigned FAR invalid")
    return value, "tail", count, floor


def assignment_decision(sorted_background_ranks, event_rank,
                        livetime_seconds):
    """Return one explicit Assigned-FAR decision from one frozen BG snapshot."""
    ranks = [float(value) for value in sorted_background_ranks]
    if any(not math.isfinite(value) for value in ranks):
        raise ValueError("background rank nonfinite")
    if ranks != sorted(ranks):
        raise ValueError("background ranks must be sorted")
    rank = float(event_rank)
    if not math.isfinite(rank):
        raise ValueError("event rank nonfinite")
    if livetime_seconds is None:
        return {
            "status": ASSIGNMENT_PENDING,
            "reason": "background_livetime_missing",
            "calculated_far": None,
            "assigned_far": None,
            "branch": None,
            "support_count": None,
            "one_count_floor": None,
            "r_tail": None,
            "tail_slope": None,
        }
    livetime = float(livetime_seconds)
    if not math.isfinite(livetime):
        raise ValueError("background livetime nonfinite")
    if livetime <= 0.0 or not ranks:
        return {
            "status": ASSIGNMENT_PENDING,
            "reason": ("background_livetime_not_positive"
                       if livetime <= 0.0 else "background_support_empty"),
            "calculated_far": None,
            "assigned_far": None,
            "branch": None,
            "support_count": None,
            "one_count_floor": None,
            "r_tail": None,
            "tail_slope": None,
        }

    calculated, count, floor = calculated_far(ranks, rank, livetime)
    r_tail, points = empirical_tail_point(ranks, livetime)
    if rank <= r_tail:
        assigned, branch, assigned_count, assigned_floor = assigned_far(
            ranks, rank, livetime, r_tail, None)
        if assigned_count != count or assigned_floor != floor:
            raise ArithmeticError("Calculated/Assigned support metadata drift")
        return {
            "status": ASSIGNMENT_ASSIGNED,
            "reason": None,
            "calculated_far": calculated,
            "assigned_far": assigned,
            "branch": branch,
            "support_count": count,
            "one_count_floor": floor,
            "r_tail": r_tail,
            "tail_slope": None,
        }

    try:
        slope = fit_anchored_tail(points, r_tail)
    except ValueError:
        return {
            "status": ASSIGNMENT_PENDING,
            "reason": "background_tail_invalid_for_above_tail_event",
            "calculated_far": calculated,
            "assigned_far": None,
            "branch": None,
            "support_count": count,
            "one_count_floor": floor,
            "r_tail": r_tail,
            "tail_slope": None,
        }
    assigned, branch, assigned_count, assigned_floor = assigned_far(
        ranks, rank, livetime, r_tail, slope)
    if assigned_count != count or assigned_floor != floor:
        raise ArithmeticError("Calculated/Assigned support metadata drift")
    return {
        "status": ASSIGNMENT_ASSIGNED,
        "reason": None,
        "calculated_far": calculated,
        "assigned_far": assigned,
        "branch": branch,
        "support_count": count,
        "one_count_floor": floor,
        "r_tail": r_tail,
        "tail_slope": slope,
    }


def threshold_eligible(rho):
    value = float(rho)
    return math.isfinite(value) and value >= 4.0
