import sys

import pytest

from numeric_test_support import load_exporter, load_numeric


@pytest.mark.parametrize(
    "bankid,source_class,dof",
    [(0, "BNS", 120.0), (99, "BNS", 120.0),
     (100, "NSBH", 600.0), (383, "NSBH", 600.0)],
)
def test_bank_class_boundaries(bankid, source_class, dof):
    numeric = load_numeric()
    exporter = load_exporter()
    assert numeric.source_class_and_dof(bankid) == (source_class, dof)
    assert exporter.source_class_and_dof(bankid) == (source_class, dof)


@pytest.mark.parametrize("bankid", [-1, 384, 415, 416, 9999, "junk"])
def test_unsupported_bank_class_fails_closed(bankid):
    numeric = load_numeric()
    exporter = load_exporter()
    with pytest.raises((TypeError, ValueError)):
        numeric.source_class_and_dof(bankid)
    with pytest.raises((TypeError, ValueError)):
        exporter.source_class_and_dof(bankid)


def test_formal_cli_dof_override_is_forbidden(monkeypatch, tmp_path):
    exporter = load_exporter()
    assert exporter.source_class_and_dof(99) == ("BNS", 120)
    assert exporter.source_class_and_dof(100) == ("NSBH", 600)
    output = tmp_path / "must_not_exist.csv"
    monkeypatch.setattr(sys, "argv", [
        "export_template_shape_map.py",
        "--bank-stats-dir", str(tmp_path),
        "--output", str(output),
        "--dof", "600",
    ])
    with pytest.raises(
            SystemExit,
            match="--dof is forbidden; dof is fixed by bank class"):
        exporter.main()
    assert not output.exists()
