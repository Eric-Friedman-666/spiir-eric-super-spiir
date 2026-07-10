#!/usr/bin/env python3

import importlib.util
import math
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "single_detector_far.py"
if not MODULE_PATH.exists():
    MODULE_PATH = pathlib.Path(__file__).resolve().parent / "single_detector_far.py"
SPEC = importlib.util.spec_from_file_location("single_detector_far_under_test", MODULE_PATH)
sdf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdf)


def reference_llr(rho, chisq_r, a_eff, dof):
    x = dof * chisq_r

    def logpdf(mean, variance):
        variance = max(variance, 1.0e-300)
        return -0.5 * (
            math.log(2.0 * math.pi * variance)
            + (x - mean) ** 2 / variance
        )

    lambda0 = rho * rho * a_eff
    ln_h0 = logpdf(dof + lambda0, 2.0 * (dof + 2.0 * lambda0))
    terms = []
    for beta in [0.003 * i for i in range(1, 11)]:
        lambda1 = beta * beta * lambda0
        terms.append(logpdf(
            dof + lambda1,
            2.0 * (dof + 2.0 * lambda1),
        ))
    maximum = max(terms)
    ln_h1 = (
        maximum
        + math.log(sum(math.exp(value - maximum) for value in terms))
        - math.log(len(terms))
    )
    return ln_h1 - ln_h0 + 0.5 * rho * rho


class WGuoGaussianLlrTest(unittest.TestCase):
    def test_bns_and_nsbh_match_reference(self):
        for dof in (120.0, 600.0):
            model = sdf.SingleDetectorLikelihoodModel(
                signal_dof=dof,
                noise_dof=dof,
                beta_grid=sdf.uniform_beta_grid(0.03, 10),
            )
            for rho, chisq_r, a_eff in (
                    (4.0, 1.0, 3.2),
                    (8.5, 0.9, 10.555639),
                    (49.8, 1.4, 5.7)):
                self.assertAlmostEqual(
                    model.rank(rho, chisq_r, a_eff),
                    reference_llr(rho, chisq_r, a_eff, dof),
                    places=12,
                )

    def test_beta_grid_and_a_eff_semantics(self):
        for actual, expected in zip(
                sdf.uniform_beta_grid(0.03, 10),
                [0.003 * i for i in range(1, 11)]):
            self.assertAlmostEqual(actual, expected, places=15)
        model = sdf.make_default_likelihood_model()
        self.assertEqual(model.base_noncentrality(5.0, 10.0), 250.0)

    def test_template_dof_cannot_override_run_dof(self):
        model = sdf.SingleDetectorLikelihoodModel(
            signal_dof=120.0,
            noise_dof=120.0,
            beta_grid=sdf.uniform_beta_grid(0.03, 10),
        )
        expected = model.rank(8.0, 1.1, 6.0)
        self.assertEqual(expected, model.rank(8.0, 1.1, 6.0, dof=681.0))


if __name__ == "__main__":
    unittest.main()
