from __future__ import division

import csv
import json
import math
import os
import shutil
import sys
import tempfile
import unittest


PIPEMODULE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "python", "pipemodules"))
if PIPEMODULE_DIR not in sys.path:
    sys.path.insert(0, PIPEMODULE_DIR)

import combine_background_far
import single_detector_far


class DummyRow(object):

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class SingleDetectorFarTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="single-far-test-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def temp_path(self, name):
        return os.path.join(self.tmpdir, name)

    def write_json(self, name, payload):
        path = self.temp_path(name)
        with open(path, "w") as output_file:
            json.dump(payload, output_file)
        return path

    def valid_background_payload(self, points=None):
        if points is None:
            points = [
                {"rank": 1.0, "far": 0.1},
                {"rank": 10.0, "far": 0.01},
            ]
        return {
            "version": single_detector_far.SingleFarLlrBackgroundFile.VERSION,
            "schema": single_detector_far.SingleFarLlrBackgroundFile.SCHEMA,
            "ifos": ["H1"],
            "likelihood_model": None,
            "backgrounds": {
                "H1": {
                    "livetime": 10.0,
                    "ranks": [1.0, 5.0],
                    "count": 2,
                    "far_fit": {
                        "kind": single_detector_far.RankBackground.FIT_KIND,
                        "created_utc": "2026-05-11T00:00:00Z",
                        "source": "unit-test",
                        "points": points,
                    },
                },
            },
        }

    def test_rank_background_uses_fit_and_requires_bootstrap_for_direct_far(self):
        bg = single_detector_far.RankBackground()
        bg.add_livetime(100.0)
        bg.add_rank(1.0)

        with self.assertRaises(ValueError):
            bg.far(2.0, allow_direct=False)

        self.assertAlmostEqual(bg.far(2.0, allow_direct=True), 0.01)

        bg.fit_points = [(0.0, 0.1), (10.0, 0.001)]
        fitted = bg.far(5.0, allow_direct=False)
        self.assertAlmostEqual(fitted, 0.01)

    def test_background_input_validation_fails_closed(self):
        valid_path = self.write_json("valid.json", self.valid_background_payload())
        loaded = single_detector_far.SingleFarLlrBackgroundFile.load(
            valid_path, required_ifos=("H1",), require_fits=True)
        self.assertIn("H1", loaded.backgrounds)

        missing_schema = self.valid_background_payload()
        del missing_schema["schema"]
        with self.assertRaises(ValueError):
            single_detector_far.SingleFarLlrBackgroundFile.load(
                self.write_json("missing-schema.json", missing_schema),
                required_ifos=("H1",),
                require_fits=True)

        zero_far = self.valid_background_payload(
            points=[{"rank": 1.0, "far": 0.0}])
        with self.assertRaises(ValueError):
            single_detector_far.SingleFarLlrBackgroundFile.load(
                self.write_json("zero-far.json", zero_far),
                required_ifos=("H1",),
                require_fits=True)

        no_fit = self.valid_background_payload(points=[])
        with self.assertRaises(ValueError):
            single_detector_far.SingleFarLlrBackgroundFile.load(
                self.write_json("no-fit.json", no_fit),
                required_ifos=("H1",),
                require_fits=True)
        loaded_bootstrap = single_detector_far.SingleFarLlrBackgroundFile.load(
            self.write_json("no-fit-bootstrap.json", no_fit),
            required_ifos=("H1",),
            require_fits=False)
        self.assertFalse(loaded_bootstrap.backgrounds["H1"].has_far_fit())

    def test_features_restrict_to_active_ifos_and_finite_positive_values(self):
        row = DummyRow(
            ifos="H1",
            snglsnr=[5.0, 9.0],
            chisq=[1.2, 1.1],
            is_background=single_detector_far.FLAG_FOREGROUND)
        features = single_detector_far.features_from_postcoh_row(
            row, ifos=("H1", "L1"), min_snr=4.0)
        self.assertEqual([feature.ifo for feature in features], ["H1"])

        bad_values = DummyRow(
            ifos="H1L1",
            snglsnr=[float("nan"), 7.0],
            chisq=[1.0, float("inf")],
            is_background=single_detector_far.FLAG_FOREGROUND)
        self.assertEqual(
            single_detector_far.features_from_postcoh_row(
                bad_values, ifos=("H1", "L1"), min_snr=4.0),
            [])

    def test_livetime_updates_only_active_ifos(self):
        branch = single_detector_far.SingleDetectorBranch(
            single_detector_far.make_default_likelihood_model(),
            ifos=("H1", "L1"),
            min_snr=4.0)
        row = DummyRow(
            ifos="H1",
            is_background=single_detector_far.FLAG_EMPTY,
            livetime=2.5)
        branch.process_row(row, livetime_step=1.0)
        self.assertAlmostEqual(branch.background["H1"].livetime, 2.5)
        self.assertAlmostEqual(branch.background["L1"].livetime, 0.0)

    def test_direct_far_floor_avoids_zero_and_infinite_significance(self):
        empty = single_detector_far.RankBackground()
        far = empty.direct_far(100.0)
        self.assertGreater(far, 0.0)
        self.assertTrue(math.isfinite(single_detector_far.neg_log10_far(far)))

        bg = single_detector_far.RankBackground()
        bg.add_livetime(100.0)
        bg.add_rank(1.0)
        no_exceedance_far = bg.direct_far(2.0)
        self.assertAlmostEqual(no_exceedance_far, 0.01)
        self.assertTrue(math.isfinite(
            single_detector_far.neg_log10_far(no_exceedance_far)))

    def write_csv(self, name, row):
        path = self.temp_path(name)
        with open(path, "w", newline="") as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=single_detector_far.PLOT_ROW_FIELDS)
            writer.writeheader()
            writer.writerow(row)
        return path

    def valid_plot_row(self):
        return {
            "category": "H1_sd",
            "ifo": "H1",
            "rho": 6.0,
            "chisq": 1.0,
            "rank": 8.0,
            "far": 0.01,
            "neg_log10_far": 2.0,
            "tmplt_idx": 1,
            "bankid": 2,
            "end_time": 3,
            "end_time_ns": 4,
        }

    def test_final_csv_validation_and_mode_contract(self):
        valid_csv = self.write_csv("valid.csv", self.valid_plot_row())
        rows = combine_background_far.combine_far_plane_rows(
            single_csv=valid_csv,
            mode="single")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["far"], 0.01)

        bad_far = self.valid_plot_row()
        bad_far["far"] = 0.0
        with self.assertRaises(ValueError):
            combine_background_far.combine_far_plane_rows(
                single_csv=self.write_csv("bad-far.csv", bad_far),
                mode="single")

        bad_neg_far = self.valid_plot_row()
        bad_neg_far["neg_log10_far"] = 9.0
        with self.assertRaises(ValueError):
            combine_background_far.combine_far_plane_rows(
                single_csv=self.write_csv("bad-neg-far.csv", bad_neg_far),
                mode="single")

        with self.assertRaises(ValueError):
            combine_background_far.combine_far_plane_rows(mode="single")


if __name__ == "__main__":
    unittest.main()
