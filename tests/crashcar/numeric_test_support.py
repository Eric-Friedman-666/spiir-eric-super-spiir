import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NUMERIC_PATH = (
    ROOT / "gstlal-spiir/share/scripts/crashcar/crashcar_numeric.py")
EXPORT_PATH = (
    ROOT / "gstlal-spiir/share/scripts/crashcar/export_template_shape_map.py")
SINGLE_FAR_PATH = (
    ROOT / "gstlal-spiir/share/scripts/crashcar/single_detector_far.py")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_numeric():
    return load_module("crashcar_numeric_under_test", NUMERIC_PATH)


def load_exporter():
    return load_module("crashcar_export_under_test", EXPORT_PATH)


def load_single_far():
    return load_module("crashcar_single_far_under_test", SINGLE_FAR_PATH)
