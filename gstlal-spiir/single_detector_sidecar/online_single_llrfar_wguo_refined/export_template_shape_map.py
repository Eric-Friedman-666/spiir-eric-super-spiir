#!/usr/bin/env python3
"""Export WGuo per-template magnitude/dof stats for crashcar C lookup."""

from __future__ import print_function

import argparse
import csv
import math
import os
import pickle

IFO_ID = {"H1": 0, "L1": 1, "V1": 2, "K1": 3}


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
        return list(bank.get(name) or [])
    return []


def finite_positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value > 0.0


def iter_rows(bank_stats_dir, ifos, start_bank, end_bank):
    for ifo in ifos:
        ifo = ifo.strip()
        if not ifo:
            continue
        ifo_id = IFO_ID.get(ifo)
        if ifo_id is None:
            raise SystemExit("unsupported IFO for crashcar template map: %s" % ifo)
        filename = os.path.join(bank_stats_dir, "%s_O3_FB_banks_magnitudes_and_dofs.pkl" % ifo)
        if not os.path.exists(filename):
            raise SystemExit("missing WGuo bank stats pickle: %s" % filename)
        banks = load_pickle(filename)
        for bankid, bank in sorted(banks.items(), key=lambda item: int(item[0])):
            bankid = int(bankid)
            if bankid < start_bank or bankid > end_bank:
                continue
            magnitudes = column_values(bank, "magnitudes")
            dofs = column_values(bank, "dofs")
            ntemplate = max(len(magnitudes), len(dofs))
            for tmplt_idx in range(ntemplate):
                magnitude = magnitudes[tmplt_idx] if tmplt_idx < len(magnitudes) else None
                dof = dofs[tmplt_idx] if tmplt_idx < len(dofs) else None
                if not finite_positive(magnitude) and not finite_positive(dof):
                    continue
                power = float(magnitude) * float(magnitude) if finite_positive(magnitude) else ""
                dof_value = float(dof) if finite_positive(dof) else ""
                yield {
                    "ifo_id": ifo_id,
                    "bankid": bankid,
                    "tmplt_idx": tmplt_idx,
                    "autocorr_power": power,
                    "dof": dof_value,
                    "ifo": ifo,
                }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank-stats-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ifos", default="H1,L1")
    parser.add_argument("--start-bank", type=int, default=0)
    parser.add_argument("--end-bank", type=int, default=95)
    args = parser.parse_args()

    ifos = [ifo.strip() for ifo in args.ifos.split(",") if ifo.strip()]
    outdir = os.path.dirname(os.path.abspath(args.output))
    if outdir and not os.path.exists(outdir):
        os.makedirs(outdir)
    tmp = args.output + ".tmp"
    count = 0
    with open(tmp, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "ifo_id", "bankid", "tmplt_idx", "autocorr_power", "dof", "ifo"
        ])
        writer.writeheader()
        for row in iter_rows(args.bank_stats_dir, ifos, args.start_bank, args.end_bank):
            writer.writerow(row)
            count += 1
    os.replace(tmp, args.output)
    print("exported %d crashcar template-shape rows to %s" % (count, args.output))


if __name__ == "__main__":
    main()
