#!/usr/bin/env python3
"""Export the canonical PDF/R10 H1/L1 A_eff map for crashcar."""

from __future__ import print_function

import argparse
import math
import operator
import os
import pickle
import re
import tempfile

IFO_ID = {"H1": 0, "L1": 1}
SUPPORTED_BANK_IDS = tuple(range(384))
TEMPLATES_PER_BANK = 1000
EXPECTED_ROW_COUNT = 2 * len(SUPPORTED_BANK_IDS) * TEMPLATES_PER_BANK
INT_MAX = 2147483647
ASCII_WHITESPACE = " \t\r\n\v\f"
HEADER = "ifo_id,bankid,tmplt_idx,a_eff,dof,ifo,source_class"
BINARY64_HEX = re.compile(r"^0x1\.[0-9a-f]{13}p[+-][0-9]+$")


def load_pickle(filename):
    with open(filename, "rb") as handle:
        try:
            return pickle.load(handle)
        except UnicodeDecodeError:
            handle.seek(0)
            return pickle.load(handle, encoding="latin1")


def column_values(bank, name):
    if hasattr(bank, "columns") and name in bank.columns:
        return list(bank[name].to_numpy())
    if isinstance(bank, dict):
        values = bank.get(name)
        return [] if values is None else list(values)
    return []


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
    else:
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
        raise ValueError("%s is outside [0,INT_MAX]" % name)
    return int(integer)


def source_class_and_dof(bankid):
    bankid = strict_nonnegative_integer(bankid, "bankid")
    if 0 <= bankid <= 99:
        return "BNS", 120
    if 100 <= bankid <= 383:
        return "NSBH", 600
    raise ValueError(
        "bank %04d has no controlled crashcar single-detector dof" % bankid)


def canonical_bank_mapping(raw_banks, ifo):
    if not hasattr(raw_banks, "items"):
        raise ValueError("%s pickle root is not a bank mapping" % ifo)
    banks = {}
    for raw_bankid, bank in raw_banks.items():
        bankid = strict_nonnegative_integer(raw_bankid, "%s bankid" % ifo)
        if bankid in banks:
            raise ValueError("duplicate canonical %s bank %d" % (ifo, bankid))
        banks[bankid] = bank
    missing = [bankid for bankid in SUPPORTED_BANK_IDS if bankid not in banks]
    if missing:
        raise ValueError(
            "%s pickle misses supported banks: %s" %
            (ifo, ",".join(str(bankid) for bankid in missing[:16])))
    return banks


def canonical_a_eff(raw_magnitude, ifo, bankid, tmplt_idx):
    try:
        magnitude = float(raw_magnitude)
    except (TypeError, ValueError):
        raise ValueError(
            "invalid magnitude for %s bank %04d template %d" %
            (ifo, bankid, tmplt_idx))
    if not math.isfinite(magnitude) or magnitude <= 0.0:
        raise ValueError(
            "invalid magnitude for %s bank %04d template %d" %
            (ifo, bankid, tmplt_idx))
    # The PDF/WGuo atom is one binary64 conversion followed by exactly one
    # separately rounded multiplication.  Pickle dof is intentionally ignored.
    a_eff = magnitude * magnitude
    if not math.isfinite(a_eff) or a_eff <= 0.0:
        raise ValueError(
            "invalid A_eff for %s bank %04d template %d" %
            (ifo, bankid, tmplt_idx))
    encoded = a_eff.hex()
    if not BINARY64_HEX.fullmatch(encoded) or float.fromhex(encoded) != a_eff:
        raise ValueError(
            "noncanonical A_eff for %s bank %04d template %d" %
            (ifo, bankid, tmplt_idx))
    return encoded


def iter_canonical_lines(bank_stats_dir):
    for ifo in ("H1", "L1"):
        filename = os.path.join(
            bank_stats_dir,
            "%s_O3_FB_banks_magnitudes_and_dofs.pkl" % ifo,
        )
        if not os.path.isfile(filename):
            raise ValueError("missing WGuo bank stats pickle: %s" % filename)
        banks = canonical_bank_mapping(load_pickle(filename), ifo)
        for bankid in SUPPORTED_BANK_IDS:
            source_class, dof = source_class_and_dof(bankid)
            magnitudes = column_values(banks[bankid], "magnitudes")
            if len(magnitudes) != TEMPLATES_PER_BANK:
                raise ValueError(
                    "%s bank %04d has %d magnitudes, expected %d" %
                    (ifo, bankid, len(magnitudes), TEMPLATES_PER_BANK))
            for tmplt_idx, raw_magnitude in enumerate(magnitudes):
                a_eff = canonical_a_eff(
                    raw_magnitude, ifo, bankid, tmplt_idx)
                yield "%d,%d,%d,%s,%d,%s,%s" % (
                    IFO_ID[ifo], bankid, tmplt_idx, a_eff, dof, ifo,
                    source_class)


def write_canonical_map(bank_stats_dir, output):
    output = os.path.abspath(output)
    outdir = os.path.dirname(output)
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir)
    fd, tmp = tempfile.mkstemp(prefix=".crashcar_a_eff_", dir=outdir)
    count = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write((HEADER + "\n").encode("ascii"))
            for line in iter_canonical_lines(bank_stats_dir):
                if (not line or line != line.strip(ASCII_WHITESPACE)
                        or '"' in line or "'" in line or "\r" in line
                        or "\x00" in line):
                    raise ValueError("noncanonical generated A_eff row")
                handle.write((line + "\n").encode("ascii"))
                count += 1
            if count != EXPECTED_ROW_COUNT:
                raise ValueError(
                    "generated %d rows, expected %d" %
                    (count, EXPECTED_ROW_COUNT))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, output)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank-stats-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ifos", default="H1,L1")
    parser.add_argument("--start-bank", type=int, default=0)
    parser.add_argument("--end-bank", type=int, default=383)
    parser.add_argument(
        "--dof", type=float, default=None,
        help="retired compatibility argument; formal export requires omission",
    )
    args = parser.parse_args()

    if args.ifos != "H1,L1":
        raise SystemExit("canonical crashcar A_eff map requires --ifos H1,L1")
    if args.start_bank != 0 or args.end_bank != 383:
        raise SystemExit("canonical crashcar A_eff map requires banks 0..383")
    if args.dof is not None:
        raise SystemExit("--dof is forbidden; dof is fixed by bank class")
    try:
        count = write_canonical_map(args.bank_stats_dir, args.output)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))
    print("exported %d canonical crashcar A_eff rows to %s" %
          (count, args.output))


if __name__ == "__main__":
    main()
