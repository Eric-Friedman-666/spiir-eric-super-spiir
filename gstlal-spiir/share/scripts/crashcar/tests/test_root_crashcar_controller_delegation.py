#!/usr/bin/env python3
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve()
REPO_ROOT = next(parent for parent in HERE.parents
                 if (parent / "scripts" / "crashcar.sh").is_file())
WRAPPER = REPO_ROOT / "scripts" / "crashcar_controller.sh"
STANDARD = REPO_ROOT / "scripts" / "crashcar.sh"
PACKAGED = (REPO_ROOT / "gstlal-spiir" / "share" / "scripts" /
            "crashcar" / "crashcar_controller.sh")
PACKAGED_SHA256 = "ec448761c6ca0a23655caa00c25f4f265a2c451ff09696ff3d8e15ccefe360da"


class RootControllerDelegationTest(unittest.TestCase):
    def test_static_delegation_contract(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 20)
        self.assertIn('LAUNCHER="${SCRIPT_DIR}/crashcar.sh"', text)
        self.assertIn('exec bash "${LAUNCHER}" "$@"', text)
        lowered = text.lower()
        for forbidden in ("frozen", "ledger", "patch_zerolag", "backfill"):
            self.assertNotIn(forbidden, lowered)
        subprocess.run(["bash", "-n", str(WRAPPER)], check=True)

    def test_packaged_controller_remains_live_implementation(self):
        self.assertNotEqual(WRAPPER.resolve(), PACKAGED.resolve())
        data = PACKAGED.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), PACKAGED_SHA256)
        text = data.decode("utf-8")
        self.assertIn("rolling|live_readonly", text)
        self.assertNotIn("rolling|frozen", text)

    def _dry_run(self, use_positional):
        with tempfile.TemporaryDirectory(prefix="cc-root-controller-") as raw:
            root = Path(raw)
            save = root / "runs"
            config = root / "launch.env"
            config.write_text(
                f"root={REPO_ROOT}\n"
                f"save_dir={save}\n"
                f"run_id={'positional' if use_positional else 'environment'}\n"
                "injection_mode=True\n"
                "crashcar_dry_run=1\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            if use_positional:
                env["CRASHCAR_CONFIG_FILE"] = str(root / "must_not_be_used.env")
                command = ["bash", str(WRAPPER), str(config)]
            else:
                env["CRASHCAR_CONFIG_FILE"] = str(config)
                command = ["bash", str(WRAPPER)]
            result = subprocess.run(
                command, env=env, text=True, capture_output=True,
                timeout=20, check=True,
            )
            self.assertEqual(result.stdout.count("crashcar: staged run root "), 1)
            self.assertIn(
                "dry run requested; not starting controller or submitting Slurm",
                result.stdout,
            )
            run_parent = save / ("positional" if use_positional else "environment")
            roots = [path for path in run_parent.iterdir() if path.is_dir()]
            self.assertEqual(len(roots), 1)
            staged = roots[0]
            readme = (staged / "README.crashcar_launch.txt").read_text(encoding="utf-8")
            self.assertIn("live background injection workflow", readme)
            self.assertFalse((staged / "controller").exists())
            self.assertTrue((staged / "scripts" / "crashcar_controller.sh").is_file())

    def test_positional_config_delegates_once_without_recursion_or_slurm(self):
        self._dry_run(use_positional=True)

    def test_environment_config_delegates_once_without_recursion_or_slurm(self):
        self._dry_run(use_positional=False)


if __name__ == "__main__":
    unittest.main()
