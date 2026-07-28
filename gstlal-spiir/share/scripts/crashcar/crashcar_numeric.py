#!/usr/bin/env python3
"""Independent Python policy for the R7 crashcar single-detector numerics.

This module deliberately does not import or call the C plugin.  It is the
offline policy/oracle used by deterministic golden tests and by audit/plotting
consumers.  Production C must agree numerically with it.
"""

import math
import operator


BETA_MIN = 0.003
BETA_STEP = 0.003
BETA_COUNT = 64
TAIL_FAR = 1.0e-2
ONE_COUNT_FLOOR = 1.0
INT_MAX = 2147483647
ASCII_WHITESPACE = " \t\r\n\v\f"


def _finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _finite_positive(value):
    return _finite(value) and float(value) > 0.0


def strict_nonnegative_integer(value, name="value"):
    if isinstance(value, bool):
        raise ValueError("%s must be a canonical nonnegative integer" % name)
    if isinstance(value, str):
        text = value.strip(ASCII_WHITESPACE)
        if (not text or not all("0" <= character <= "9"
                                for character in text)
                or (len(text) > 1 and text.startswith("0"))):
            raise ValueError(
                "%s must be a canonical nonnegative integer" % name)
        integer = int(text)
        if integer > INT_MAX:
            raise ValueError("%s exceeds INT_MAX" % name)
        return integer
    try:
        integer = operator.index(value)
    except TypeError:
        if not isinstance(value, float) or not math.isfinite(value):
            raise ValueError(
                "%s must be a canonical nonnegative integer" % name)
        if not value.is_integer():
            raise ValueError(
                "%s must be a canonical nonnegative integer" % name)
        integer = int(value)
    if integer < 0 or integer > INT_MAX:
        raise ValueError("%s must be a canonical nonnegative integer" % name)
    return int(integer)


def source_class_and_dof(bankid):
    bankid = strict_nonnegative_integer(bankid, "bankid")
    if 0 <= bankid <= 99:
        return "BNS", 120.0
    if 100 <= bankid <= 383:
        return "NSBH", 600.0
    raise ValueError(
        "bank %04d has no controlled crashcar single-detector dof" % bankid)


def beta_grid():
    return [BETA_MIN + BETA_STEP * index for index in range(BETA_COUNT)]


def _normal_logpdf(value, mean, variance):
    if not (_finite(value) and _finite(mean) and _finite_positive(variance)):
        raise ValueError("nonfinite Gaussian input")
    delta = float(value) - float(mean)
    variance = float(variance)
    return -0.5 * (
        math.log(2.0 * math.pi * variance) + delta * delta / variance)


def _logsumexp(values):
    values = list(values)
    if not values or any(not _finite(value) for value in values):
        raise ValueError("invalid log-sum-exp input")
    maximum = max(values)
    return maximum + math.log(sum(math.exp(value - maximum)
                                  for value in values))


def gaussian_llr(rho, chisq, a_eff, dof):
    if not (_finite_positive(rho) and _finite_positive(chisq)
            and _finite_positive(a_eff)):
        raise ValueError("rho, chisq and A_eff must be finite and positive")
    dof = float(dof)
    if dof not in (120.0, 600.0):
        raise ValueError("dof must be the controlled BNS/NSBH value")
    rho = float(rho)
    chisq = float(chisq)
    a_eff = float(a_eff)
    value = dof * chisq
    lambda0 = rho * rho * a_eff
    noise = _normal_logpdf(
        value, dof + lambda0, 2.0 * (dof + 2.0 * lambda0))
    signal_terms = []
    for beta in beta_grid():
        lambda1 = beta * beta * lambda0
        signal_terms.append(_normal_logpdf(
            value, dof + lambda1, 2.0 * (dof + 2.0 * lambda1)))
    signal = _logsumexp(signal_terms) - math.log(BETA_COUNT)
    llr = signal - noise + 0.5 * rho * rho
    if not _finite(llr):
        raise ValueError("nonfinite Gaussian LLR")
    return llr


def gaussian_llr_for_template(rho, chisq, a_eff, bankid, mapped_dof=None):
    _source_class, required_dof = source_class_and_dof(bankid)
    if mapped_dof is not None:
        if not _finite(mapped_dof) or float(mapped_dof) != required_dof:
            raise ValueError("template dof conflicts with bank class")
    return gaussian_llr(rho, chisq, a_eff, required_dof)


def calculated_far(ranks, livetime, rank):
    ranks = list(ranks)
    if not ranks or not _finite_positive(livetime) or not _finite(rank):
        raise ValueError("support, detector livetime and rank are required")
    if any(not _finite(value) for value in ranks):
        raise ValueError("support ranks must be finite")
    count_ge = sum(float(value) >= float(rank) for value in ranks)
    far = max(float(count_ge), ONE_COUNT_FLOOR) / float(livetime)
    if not _finite_positive(far):
        raise ValueError("invalid Calculated FAR")
    return far


def _empirical_points(ranks, livetime):
    sorted_ranks = sorted(float(value) for value in ranks)
    xs = []
    log_fars = []
    index = 0
    while index < len(sorted_ranks):
        rank = sorted_ranks[index]
        following = index + 1
        while (following < len(sorted_ranks)
               and sorted_ranks[following] == rank):
            following += 1
        count_ge = len(sorted_ranks) - index
        xs.append(rank)
        log_fars.append(math.log10(
            max(float(count_ge), ONE_COUNT_FLOOR) / float(livetime)))
        index = following
    return xs, log_fars


def nearest_background_far(ranks, livetime, rank, r_tail=None):
    """Return the FAR stored at the background LLR nearest to ``rank``.

    At an exact midpoint the lower LLR is retained, which is the conservative
    (larger-FAR) choice for the monotone empirical background curve.
    """
    ranks = list(ranks)
    if not ranks or not _finite_positive(livetime) or not _finite(rank):
        raise ValueError("support, detector livetime and rank are required")
    if any(not _finite(value) for value in ranks):
        raise ValueError("support ranks must be finite")
    xs, _log_fars = _empirical_points(ranks, livetime)
    stop = len(xs)
    if r_tail is not None:
        stop = xs.index(float(r_tail)) + 1
    nearest = min(
        range(stop),
        key=lambda index: (abs(xs[index] - float(rank)), xs[index]),
    )
    far = calculated_far(ranks, livetime, xs[nearest])
    if not _finite_positive(far):
        raise ValueError("invalid nearest-background FAR")
    return far


def tail_model(ranks, livetime):
    ranks = list(ranks)
    if not ranks or not _finite_positive(livetime):
        raise ValueError("support and detector livetime are required")
    if any(not _finite(value) for value in ranks):
        raise ValueError("support ranks must be finite")
    xs, log_fars = _empirical_points(ranks, livetime)
    tail_log_far = math.log10(TAIL_FAR)
    tail_index = min(
        range(len(xs)),
        key=lambda index: abs(log_fars[index] - tail_log_far))
    r_tail = xs[tail_index]
    tail_xs = xs[tail_index:]
    tail_ys = log_fars[tail_index:]
    slope = None
    intercept = None
    if len(tail_xs) >= 2:
        denominator = sum((value - r_tail) ** 2 for value in tail_xs)
        numerator = sum(
            (value - r_tail) * (log_far - tail_log_far)
            for value, log_far in zip(tail_xs, tail_ys))
        if denominator > 0.0:
            candidate_slope = numerator / denominator
            candidate_intercept = tail_log_far - candidate_slope * r_tail
            if (_finite(candidate_slope) and candidate_slope < 0.0
                    and _finite(candidate_intercept)):
                slope = candidate_slope
                intercept = candidate_intercept
    return {
        "r_tail": r_tail,
        "tail_slope": slope,
        "tail_intercept": intercept,
        "empirical_ranks": xs,
        "empirical_log10_fars": log_fars,
        "tail_index": tail_index,
    }


def evaluate_far(ranks, livetime, rank):
    direct = calculated_far(ranks, livetime, rank)
    model = tail_model(ranks, livetime)
    rank = float(rank)
    result = dict(model)
    result.update({
        "calculated_far": direct,
        "assigned_far": None,
        "used_tail_fit": False,
        "status": "failed_tail_fit",
    })
    if rank <= model["r_tail"]:
        result.update({
            "assigned_far": nearest_background_far(
                ranks, livetime, rank, model["r_tail"]),
            "status": "assigned_direct",
        })
        return result
    slope = model["tail_slope"]
    intercept = model["tail_intercept"]
    if slope is None or intercept is None:
        return result
    assigned = math.pow(10.0, slope * rank + intercept)
    if not _finite_positive(assigned):
        return result
    result.update({
        "assigned_far": assigned,
        "used_tail_fit": True,
        "status": "assigned_tail_fit",
    })
    return result
