"""Independent R7 numerical oracle; never imports or calls production code."""

import math


BETAS = tuple(0.003 + 0.003 * index for index in range(64))


def gaussian_llr(rho, chisq, a_eff, dof):
    value = float(dof) * float(chisq)
    lambda0 = float(rho) ** 2 * float(a_eff)

    def logpdf(mean, variance):
        delta = value - mean
        return -0.5 * (
            math.log(2.0 * math.pi * variance) + delta * delta / variance)

    noise = logpdf(
        dof + lambda0, 2.0 * (dof + 2.0 * lambda0))
    terms = []
    for beta in BETAS:
        lambda1 = beta * beta * lambda0
        terms.append(logpdf(
            dof + lambda1, 2.0 * (dof + 2.0 * lambda1)))
    maximum = max(terms)
    signal = maximum + math.log(sum(
        math.exp(value - maximum) for value in terms)) - math.log(len(terms))
    return signal - noise + 0.5 * float(rho) ** 2


def calculated_far(ranks, livetime, rank):
    count_ge = sum(float(value) >= float(rank) for value in ranks)
    return max(float(count_ge), 1.0) / float(livetime)


def evaluate_far(ranks, livetime, rank):
    ranks = sorted(float(value) for value in ranks)
    direct = calculated_far(ranks, livetime, rank)
    xs = []
    log_fars = []
    index = 0
    while index < len(ranks):
        following = index + 1
        while following < len(ranks) and ranks[following] == ranks[index]:
            following += 1
        xs.append(ranks[index])
        log_fars.append(math.log10((len(ranks) - index) / float(livetime)))
        index = following
    tail_log_far = -2.0
    tail_index = min(
        range(len(xs)), key=lambda idx: abs(log_fars[idx] - tail_log_far))
    r_tail = xs[tail_index]
    tail_xs = xs[tail_index:]
    tail_ys = log_fars[tail_index:]
    denominator = sum((value - r_tail) ** 2 for value in tail_xs)
    slope = None
    intercept = None
    if len(tail_xs) >= 2 and denominator > 0.0:
        numerator = sum(
            (value - r_tail) * (log_far - tail_log_far)
            for value, log_far in zip(tail_xs, tail_ys))
        candidate = numerator / denominator
        if math.isfinite(candidate) and candidate < 0.0:
            slope = candidate
            intercept = tail_log_far - slope * r_tail
    assigned = None
    used_tail = False
    if float(rank) <= r_tail:
        assigned = direct
    elif slope is not None:
        candidate = 10.0 ** (slope * float(rank) + intercept)
        if math.isfinite(candidate) and candidate > 0.0:
            assigned = candidate
            used_tail = True
    return {
        "calculated_far": direct,
        "assigned_far": assigned,
        "r_tail": r_tail,
        "tail_slope": slope,
        "tail_intercept": intercept,
        "used_tail_fit": used_tail,
    }
