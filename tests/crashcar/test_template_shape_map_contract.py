import json
import math
from pathlib import Path

import pytest

from numeric_test_support import load_exporter, load_single_far


CORPUS_PATH = (
    Path(__file__).resolve().parent / "data/template_shape_map_corpus.json")
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _corpus_text(case):
    lines = list(case["lines"])
    if "payload_base" in case:
        base = case["payload_base"]
        padding = int(case["payload_target_bytes"]) - len(
            base.encode("utf-8"))
        assert padding >= 0
        lines.append(base + " " * padding)
    if "overlong_base" in case:
        lines.append(
            case["overlong_base"]
            + case["overlong_suffix_character"]
            * int(case["overlong_suffix_count"]))
    line_ending = case.get("line_ending", "\n")
    text = line_ending.join(lines)
    if case.get("final_newline", True):
        text += line_ending
    return text


def _corpus_bytes(case):
    payload = _corpus_text(case).encode("utf-8")
    for replacement in case.get("raw_replacements", []):
        marker = replacement["marker"].encode("ascii")
        payload = payload.replace(marker, bytes.fromhex(replacement["hex"]))
    return payload


def test_exporter_derives_dof_and_preserves_exact_a_eff():
    exporter = load_exporter()
    cases = (
        ("H1", 99, 0, 3.25, "BNS", 120),
        ("L1", 99, 1, 7.5, "BNS", 120),
        ("H1", 100, 0, 4.5, "NSBH", 600),
    )
    for ifo, bankid, tmplt_idx, raw_magnitude, source_class, dof in cases:
        encoded = exporter.canonical_a_eff(
            raw_magnitude, ifo, bankid, tmplt_idx)
        expected = float(raw_magnitude) * float(raw_magnitude)
        assert encoded == expected.hex()
        assert float.fromhex(encoded) == expected
        assert exporter.BINARY64_HEX.fullmatch(encoded)
        assert exporter.source_class_and_dof(bankid) == (source_class, dof)
    with pytest.raises(ValueError, match="invalid A_eff"):
        exporter.canonical_a_eff(
            float.fromhex("0x1.0000000000000p+1023"), "H1", 0, 0)


@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan")])
def test_invalid_a_eff_fails_closed(bad_value):
    exporter = load_exporter()
    with pytest.raises(ValueError, match="invalid magnitude"):
        exporter.canonical_a_eff(bad_value, "H1", 0, 0)


def test_duplicate_canonical_bank_id_fails_closed():
    exporter = load_exporter()
    with pytest.raises(ValueError, match="duplicate canonical H1 bank 0"):
        exporter.canonical_bank_mapping(
            {0: {"magnitudes": [1.0]},
             "0": {"magnitudes": [2.0]}},
            "H1",
        )


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["name"])
def test_shared_formal_map_corpus_python_loader(tmp_path, case):
    single_far = load_single_far()
    path = tmp_path / (case["name"] + ".csv")
    path.write_bytes(_corpus_bytes(case))
    if not case["valid"]:
        with pytest.raises(ValueError):
            single_far.load_autocorr_power_map(str(path))
        return
    mapping = single_far.load_autocorr_power_map(str(path))
    assert mapping
    assert len(mapping) == len(case["expected_rows"])
    for entry in mapping.values():
        assert isinstance(entry, dict)
        assert set(("autocorr_power", "dof", "source_class")) <= set(entry)
        assert entry["autocorr_power"] > 0.0
        assert entry["dof"] in (120.0, 600.0)
        assert entry["source_class"] in ("BNS", "NSBH")
    for expected in case["expected_rows"]:
        key = "%s:%d:%d" % (
            expected["ifo"], expected["bankid"], expected["tmplt_idx"])
        actual = mapping[key]
        for field in (
                "ifo_id", "ifo", "bankid", "tmplt_idx", "source_class"):
            assert actual[field] == expected[field]
        assert math.isclose(
            actual["autocorr_power"], expected["autocorr_power"],
            rel_tol=0.0, abs_tol=0.0)
        assert math.isclose(actual["dof"], expected["dof"],
                            rel_tol=0.0, abs_tol=0.0)


def test_lookup_has_no_missing_or_invalid_dof_fallback():
    single_far = load_single_far()
    base = {
        "ifo_id": 0,
        "ifo": "H1",
        "bankid": 0,
        "tmplt_idx": 7,
        "autocorr_power": 2.5,
        "dof": 120.0,
        "source_class": "BNS",
    }
    for bad_dof in (None, float("nan"), float("inf"), 681.0):
        mapping = {"H1:0:7": dict(base, dof=bad_dof)}
        with pytest.raises(ValueError):
            single_far.lookup_template_dof(mapping, "H1", 0, 7)


@pytest.mark.parametrize(
    "value",
    [99.9, "99x", "099", "٧", 2147483648, float("nan")],
)
def test_bankid_is_strict_integral(value):
    single_far = load_single_far()
    with pytest.raises(ValueError):
        single_far.crashcar_numeric.source_class_and_dof(value)
