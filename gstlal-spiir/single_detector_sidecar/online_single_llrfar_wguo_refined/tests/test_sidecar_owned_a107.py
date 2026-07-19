#!/usr/bin/env python3
"""Behavior gates for the sidecar-owned completed A107 reader."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

import sidecar_owned_a107 as subject


REQUIRED = (
    "bankid", "chisq_H1", "chisq_L1", "end_time", "end_time_ns",
    "end_time_ns_sngl_H1", "end_time_ns_sngl_L1",
    "end_time_sngl_H1", "end_time_sngl_L1", "event_id", "ifos",
    "is_background", "snglsnr_H1", "snglsnr_L1", "tmplt_idx",
)
COLUMNS = REQUIRED + tuple(
    f"legacy_filler_{index:03d}"
    for index in range(subject.LEGACY_COLUMN_COUNT - len(REQUIRED)))


def row(event, gps, *, ifos="H1L1V1", rho_h="4", rho_l="5"):
    value = {column: "" for column in COLUMNS}
    value.update({
        "bankid": "5",
        "event_id": str(event),
        "ifos": ifos,
        "is_background": "0",
        "end_time": str(gps),
        "end_time_ns": "0",
        "end_time_sngl_H1": str(gps),
        "end_time_ns_sngl_H1": "0",
        "end_time_sngl_L1": str(gps),
        "end_time_ns_sngl_L1": "0",
        "snglsnr_H1": str(rho_h),
        "snglsnr_L1": str(rho_l),
        "chisq_H1": "1",
        "chisq_L1": "1",
        "tmplt_idx": "0",
    })
    return value


def write_snapshot(path, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writerows(rows)
    lines = [
        "<?xml version='1.0' encoding='utf-8'?>",
        "<LIGO_LW>",
        '<Table Name="postcoh:table">',
        *(f'<Column Name="{name}" Type="lstring"/>' for name in COLUMNS),
        '<Stream Name="postcoh:table" Delimiter="," Type="Local">',
        *stream.getvalue().splitlines(),
        "</Stream>",
        "</Table>",
        "</LIGO_LW>",
        "",
    ]
    payload = "\n".join(lines).encode("utf-8")
    with gzip.GzipFile(filename=str(path), mode="wb", mtime=0) as handle:
        handle.write(payload)


def write_roster(worker):
    output = worker / "000"
    paths = sorted(output.glob("000_zerolag_*.xml.gz"))
    roster = worker / "a107_roster.tsv"
    with roster.open("w", encoding="ascii", newline="") as handle:
        handle.write("relative_path\tbytes\tsha256\n")
        for path in paths:
            relative = path.relative_to(worker)
            payload = path.read_bytes()
            handle.write(
                f"{relative}\t{len(payload)}\t"
                f"{hashlib.sha256(payload).hexdigest()}\n")
    return roster


class OwnedA107Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "run"
        self.worker = self.root / "acquisition" / "worker_000"
        (self.worker / "000").mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def load(self):
        return subject.load_owned_worker(
            run_root=self.root,
            worker_id=0,
            worker_count=1,
            worker_group=0,
            source_stream_bank_map=((0, 5),),
            start_bank=5,
            banks_per_worker=1)

    def test_order_dedupe_rho4_and_v_exclusion_metadata(self):
        duplicate = row(30, 30, rho_h="4", rho_l="3")
        write_snapshot(
            self.worker / "000" / "000_zerolag_100_100.xml.gz",
            [duplicate, row(20, 20, rho_h="3", rho_l="5")])
        write_snapshot(
            self.worker / "000" / "000_zerolag_200_100.xml.gz",
            [duplicate.copy(), row(10, 10, rho_h="6", rho_l="7"),
             row(40, 40, ifos="V1", rho_h="8", rho_l="8")])
        write_roster(self.worker)
        rows, metadata = self.load()
        self.assertEqual(
            [int(item["event_id"]) for item in rows], [10, 20, 30, 40])
        self.assertEqual(metadata["identical_duplicates_removed"], 1)
        self.assertEqual(metadata["eligible_components"], {"H1": 2, "L1": 2})
        self.assertEqual(metadata["v_only_rows"], 1)
        at_boundary = next(item for item in rows if item["event_id"] == "30")
        self.assertEqual(at_boundary["snglsnr_H1"], "4")
        self.assertEqual(at_boundary["stream_abi"], "sidecar-owned-a107-v1")

    def test_conflict_roster_tamper_and_worker_mapping_fail_closed(self):
        first = self.worker / "000" / "000_zerolag_100_100.xml.gz"
        second = self.worker / "000" / "000_zerolag_200_100.xml.gz"
        write_snapshot(first, [row(1, 10, rho_h="4")])
        write_snapshot(second, [row(1, 10, rho_h="5")])
        write_roster(self.worker)
        with self.assertRaisesRegex(
                subject.OwnedA107Error, "conflicting duplicate"):
            self.load()

        second.unlink()
        write_roster(self.worker)
        first.write_bytes(first.read_bytes() + b"x")
        with self.assertRaisesRegex(
                subject.OwnedA107Error, "size/SHA mismatch"):
            self.load()

        first.unlink()
        write_snapshot(first, [row(1, 10)])
        write_roster(self.worker)
        with self.assertRaisesRegex(
                subject.OwnedA107Error, "worker/bank mapping mismatch"):
            subject.load_owned_worker(
                run_root=self.root,
                worker_id=0,
                worker_count=1,
                worker_group=0,
                source_stream_bank_map=((0, 6),),
                start_bank=5,
                banks_per_worker=1)


if __name__ == "__main__":
    unittest.main()
