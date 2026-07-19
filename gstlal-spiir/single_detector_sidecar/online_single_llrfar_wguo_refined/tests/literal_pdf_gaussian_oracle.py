#!/usr/bin/env python3
"""Literal independent PDF Gaussian oracle; never imported by production."""
import math

BETA_STEP = float.fromhex("0x1.89374bc6a7efap-9")
LOG_2PI = float.fromhex("0x1.d67f1c864beb4p+0")
LOG_64 = float.fromhex("0x1.0a2b23f3bab73p+2")
HALF = float.fromhex("0x1.0000000000000p-1")


def _finite(name, value):
    if not math.isfinite(value):
        raise ValueError("%s nonfinite" % name)
    return value


def _log_gaussian(x, mu, variance):
    delta = _finite("delta", x - mu)
    delta2 = _finite("delta2", delta * delta)
    scaled = _finite("scaled", delta2 / variance)
    logv = _finite("logV", math.log(variance))
    norm_term = _finite("norm_term", LOG_2PI + logv)
    total = _finite("total", norm_term + scaled)
    return _finite("logN", -HALF * total)


def literal_pdf_llr(rho, chi, a_eff, dof):
    rho = float(rho)
    chi = float(chi)
    a_eff = float(a_eff)
    dof = int(dof)
    if dof not in (120, 600):
        raise ValueError("unsupported fixed dof")
    if not (math.isfinite(rho) and rho >= 4.0):
        raise ValueError("rho outside inclusive score domain")
    if not (math.isfinite(chi) and chi > 0.0):
        raise ValueError("chi outside domain")
    if not (math.isfinite(a_eff) and a_eff > 0.0):
        raise ValueError("a_eff outside domain")

    nu = float(dof)
    rho2 = _finite("rho2", rho * rho)
    x = _finite("x", nu * chi)
    lambda0 = _finite("lambda0", rho2 * a_eff)
    if lambda0 <= 0.0:
        raise ValueError("lambda0 nonpositive")
    mu_n = _finite("muN", nu + lambda0)
    tmp_n = _finite("tmpN", 2.0 * lambda0)
    inner_n = _finite("innerN", nu + tmp_n)
    v_n = _finite("VN", 2.0 * inner_n)
    if v_n <= 0.0:
        raise ValueError("VN nonpositive")
    rho2_half = _finite("rho2_half", rho2 / 2.0)

    components = []
    for j in range(64):
        j_as_double = float(j)
        beta_product = _finite("beta_product", BETA_STEP * j_as_double)
        beta = _finite("beta", BETA_STEP + beta_product)
        beta2 = _finite("beta2", beta * beta)
        lambda1 = _finite("lambda1", beta2 * lambda0)
        if lambda1 <= 0.0:
            raise ValueError("lambda1 nonpositive")
        mu1 = _finite("mu1", nu + lambda1)
        tmp1 = _finite("tmp1", 2.0 * lambda1)
        inner1 = _finite("inner1", nu + tmp1)
        v1 = _finite("V1", 2.0 * inner1)
        if v1 <= 0.0:
            raise ValueError("V1 nonpositive")
        components.append(_log_gaussian(x, mu1, v1))

    maximum = components[0]
    for component in components[1:]:
        if component > maximum:
            maximum = component
    total = 0.0
    for component in components:
        shifted = _finite("shifted", component - maximum)
        term = math.exp(shifted)
        if not math.isfinite(term):
            raise ValueError("exp term nonfinite")
        total = _finite("sum", total + term)
    if total < 1.0:
        raise ValueError("logsum accumulator below one")

    log_pn = _log_gaussian(x, mu_n, v_n)
    log_sum = _finite("log_sum", math.log(total))
    log_ps_unweighted = _finite(
        "log_pS_unweighted", maximum + log_sum)
    log_ps = _finite("log_pS", log_ps_unweighted - LOG_64)
    log_ratio = _finite("log_ratio", log_ps - log_pn)
    return _finite("LLR", log_ratio + rho2_half)


if __name__ == "__main__":
    for vector in (
        (4.0, 1.0, 10.0, 120),
        (8.5, 0.8, 13.25, 120),
        (12.0, 1.2, 20.0, 600),
    ):
        print(vector, literal_pdf_llr(*vector).hex())
