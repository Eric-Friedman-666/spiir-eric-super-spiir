#!/usr/bin/env python3
"""Backfill sidecar single-detector FAR assignments into zerolag XML files.

The low-latency sidecar can assign single-detector FARs after a zerolag
snapshot has already been written.  This tool is the finalization step that
copies the run-level sidecar ledger back into the PostcohInspiralTable rows so
the final zerolag files carry the same detector-local FAR fields as crashcar.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


Key = Tuple[str, str, str, str, str]
IFO_ORDER = ("H1", "L1", "V1", "K1")


def _maybe_add_runtime_paths(script_dir: Path) -> None:
    for env_name in ("SPIIR_RUNTIME_PYTHONPATH", "PYTHONPATH"):
        for item in os.environ.get(env_name, "").split(os.pathsep):
            if item and item not in sys.path:
                sys.path.insert(0, item)

    package_root = script_dir.parents[1] if len(script_dir.parents) > 1 else None
    if package_root is not None:
        source_pipemodules = package_root / "python" / "pipemodules"
        if source_pipemodules.exists():
            sys.path.insert(0, str(source_pipemodules))


def _normalize_int(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if text == "":
        return ""
    try:
        return str(int(float(text)))
    except (TypeError, ValueError, OverflowError):
        return text


def _normalize_ifo(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if text in ("H", "L", "V", "K"):
        return text + "1"
    return text


def _is_positive(value: float) -> bool:
    return math.isfinite(value) and value > 0.0


def _float_or_none(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _parse_ifo_mask(text: object) -> set[str]:
    raw = str(text or "").strip().upper()
    if raw in ("", "NONE", "OFF", "0"):
        return set()
    raw = raw.replace("+", ",").replace("|", ",").replace("/", ",")
    if "," in raw:
        tokens = [item.strip() for item in raw.split(",") if item.strip()]
    elif raw in ("HL", "LH"):
        tokens = list(raw)
    else:
        tokens = [raw]

    out: set[str] = set()
    for token in tokens:
        if token in ("H", "H1"):
            out.add("H1")
        elif token in ("L", "L1"):
            out.add("L1")
        elif token in ("V", "V1"):
            out.add("V1")
        elif token in ("K", "K1"):
            out.add("K1")
    return out


class SingleOutputPolicy:
    """Decides when detector-local single FARs may enter final zerolag output."""

    def __init__(self, mode: str, schedule: str = ""):
        normalized = (mode or "single-only").strip().lower().replace("_", "-")
        if normalized in ("singleonly", "single-only"):
            normalized = "single-only"
        self.mode = normalized
        self.schedule_text = schedule or ""
        self.schedule = self._parse_schedule(self.schedule_text)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "SingleOutputPolicy":
        mode = (
            args.single_output_mode
            or os.environ.get("PATCH_ZEROLAG_SINGLE_OUTPUT_MODE")
            or os.environ.get("SINGLE_OUTPUT_MODE")
            or "single-only"
        )
        schedule = (
            args.active_ifo_schedule
            or os.environ.get("PATCH_ZEROLAG_SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE")
            or os.environ.get("SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE")
            or os.environ.get("SINGLE_OUTPUT_DETECTOR_SCHEDULE")
            or ""
        )
        return cls(mode=mode, schedule=schedule)

    @staticmethod
    def _parse_schedule(text: str) -> list[tuple[float, float, set[str]]]:
        windows: list[tuple[float, float, set[str]]] = []
        for item in str(text or "").replace(";", ",").split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split(":")
            if len(parts) != 3:
                continue
            try:
                start = float(parts[0])
                end = float(parts[1])
            except ValueError:
                continue
            if end <= start:
                continue
            windows.append((start, end, _parse_ifo_mask(parts[2])))
        return windows

    @staticmethod
    def _row_gps(row: object, ifo: str) -> Optional[float]:
        for attr in (f"end_time_sngl_{ifo}", "end_time"):
            if hasattr(row, attr):
                gps = _float_or_none(getattr(row, attr, None))
                if gps is not None:
                    return gps
        return None

    @staticmethod
    def _row_present_ifos(row: object) -> set[str]:
        present: set[str] = set()
        for ifo in IFO_ORDER:
            snr = _float_or_none(getattr(row, f"snglsnr_{ifo}", None))
            chisq = _float_or_none(getattr(row, f"chisq_{ifo}", None))
            if (snr is not None and snr > 0.0) or (
                    chisq is not None and chisq > 0.0):
                present.add(ifo)
        return present

    def active_ifos(self, row: object, ifo: str) -> set[str]:
        gps = self._row_gps(row, ifo)
        if gps is not None:
            for start, end, ifos in self.schedule:
                if start <= gps < end:
                    return set(ifos)
        return self._row_present_ifos(row)

    def allows(self, row: object, ifo: str) -> bool:
        if self.mode in ("all", "always", "legacy"):
            return True
        if self.mode in ("none", "never", "off"):
            return False
        active = self.active_ifos(row, ifo)
        return len(active) == 1 and ifo in active

    def summary(self) -> dict:
        return {
            "single_output_mode": self.mode,
            "single_output_active_ifo_schedule": self.schedule_text,
            "single_output_schedule_windows": [
                {"start": start, "end": end, "ifos": sorted(ifos)}
                for start, end, ifos in self.schedule
            ],
        }


def build_key(ifo: object, end_time: object, end_time_ns: object,
              bankid: object, tmplt_idx: object) -> Key:
    return (
        _normalize_ifo(ifo),
        _normalize_int(end_time),
        _normalize_int(end_time_ns),
        _normalize_int(bankid),
        _normalize_int(tmplt_idx),
    )


def load_ledger(path: Path, far_column: str) -> Tuple[Dict[Key, float], dict]:
    rows = 0
    usable = 0
    duplicates = 0
    bad_far = 0
    ledger: Dict[Key, float] = {}

    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows += 1
            try:
                far = float(row.get(far_column, "") or "")
            except ValueError:
                bad_far += 1
                continue
            if not _is_positive(far):
                bad_far += 1
                continue
            key = build_key(
                row.get("ifo"),
                row.get("end_time"),
                row.get("end_time_ns"),
                row.get("bankid"),
                row.get("tmplt_idx"),
            )
            if "" in key:
                continue
            if key in ledger:
                duplicates += 1
                if far < ledger[key]:
                    ledger[key] = far
            else:
                ledger[key] = far
            usable += 1

    return ledger, {
        "ledger_path": str(path),
        "ledger_rows": rows,
        "usable_rows": usable,
        "unique_keys": len(ledger),
        "duplicate_keys": duplicates,
        "bad_far_rows": bad_far,
        "far_column": far_column,
    }


def iter_zerolag_files(run_dir: Path, patterns: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        files.extend(Path(path) for path in glob.glob(str(run_dir / pattern)))
    return sorted(set(files))


def import_ligolw(script_dir: Path):
    _maybe_add_runtime_paths(script_dir)
    from ligo.lw import ligolw
    from ligo.lw import lsctables
    from ligo.lw import utils as ligolw_utils

    try:
        from gstlal_spiir.pipemodules.postcohtable import postcoh_table_def
    except ImportError:
        from postcohtable import postcoh_table_def

    class LIGOLWContentHandler(ligolw.LIGOLWContentHandler):
        pass

    # The py3 postcoh_table_def.use_in() path is intentionally disabled in
    # this tree.  Register the table explicitly with ligo.lw's parser instead.
    postcoh_table_def.PostcohInspiralTable.interncolumns = set()
    postcoh_table_def.PostcohInspiralTable.loadcolumns = None
    lsctables.TableByName[
        postcoh_table_def.PostcohInspiralTable.tableName
    ] = postcoh_table_def.PostcohInspiralTable
    lsctables.use_in(LIGOLWContentHandler)
    return ligolw_utils, postcoh_table_def, LIGOLWContentHandler


def row_detector_key(row: object, ifo: str) -> Key:
    return build_key(
        ifo,
        getattr(row, f"end_time_sngl_{ifo}", None),
        getattr(row, f"end_time_ns_sngl_{ifo}", None),
        getattr(row, "bankid", None),
        getattr(row, "tmplt_idx", None),
    )


def assign_single_far(row: object, ifo: str, far: float,
                      timescale_fields: Iterable[str]) -> None:
    setattr(row, f"far_sngl_{ifo}", far)
    for prefix in timescale_fields:
        attr = f"{prefix}_sngl_{ifo}"
        if hasattr(row, attr):
            setattr(row, attr, far)


def assign_row_far(row: object, far: float) -> bool:
    if not hasattr(row, "far"):
        return False
    current = getattr(row, "far", 0.0) or 0.0
    try:
        current = float(current)
    except (TypeError, ValueError):
        current = 0.0
    if abs(current - far) <= max(1e-12, abs(far) * 1e-9):
        return False
    setattr(row, "far", far)
    return True


def clear_single_far(row: object, ifo: str,
                     timescale_fields: Iterable[str]) -> int:
    cleared = 0
    for attr in [f"far_sngl_{ifo}", *(f"{prefix}_sngl_{ifo}" for prefix in timescale_fields)]:
        if not hasattr(row, attr):
            continue
        current = getattr(row, attr, 0.0) or 0.0
        try:
            current = float(current)
        except (TypeError, ValueError):
            current = 0.0
        if _is_positive(current):
            cleared += 1
        setattr(row, attr, 0.0)
    return cleared


def patch_file(path: Path, ledger: Dict[Key, float], *,
               backup_suffix: Optional[str], dry_run: bool,
               script_dir: Path, clear_existing: bool,
               output_policy: SingleOutputPolicy,
               patch_row_far: bool) -> dict:
    ligolw_utils, postcoh_table_def, content_handler = import_ligolw(script_dir)
    xmldoc = ligolw_utils.load_filename(
        str(path), verbose=False, contenthandler=content_handler)
    table = postcoh_table_def.PostcohInspiralTable.get_table(xmldoc)

    rows = len(table)
    matched = 0
    updated = 0
    already_equal = 0
    zero_before = 0
    missing = 0
    cleared = 0
    updated_row_far = 0
    allowed = 0
    suppressed = 0
    changed_keys: set[Key] = set()

    for row in table:
        for ifo in IFO_ORDER:
            if not hasattr(row, f"end_time_sngl_{ifo}"):
                continue
            if clear_existing:
                cleared += clear_single_far(row, ifo, ("far_1w", "far_1d", "far_2h"))
            key = row_detector_key(row, ifo)
            far = ledger.get(key)
            if far is None:
                missing += 1
                continue
            matched += 1
            if not output_policy.allows(row, ifo):
                suppressed += 1
                continue
            allowed += 1
            current = getattr(row, f"far_sngl_{ifo}", 0.0) or 0.0
            try:
                current = float(current)
            except (TypeError, ValueError):
                current = 0.0
            if not _is_positive(current):
                zero_before += 1
            detector_equal = abs(current - far) <= max(1e-12, abs(far) * 1e-9)
            row_far_updated = assign_row_far(row, far) if patch_row_far else False
            if row_far_updated:
                updated_row_far += 1
            if detector_equal:
                if row_far_updated:
                    changed_keys.add(key)
                else:
                    already_equal += 1
                continue
            assign_single_far(row, ifo, far, ("far_1w", "far_1d", "far_2h"))
            updated += 1
            changed_keys.add(key)

    if (updated or cleared or updated_row_far) and not dry_run:
        if backup_suffix:
            backup = path.with_name(path.name + backup_suffix)
            if not backup.exists():
                shutil.copy2(path, backup)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".tmp.", suffix=path.suffix,
            dir=str(path.parent))
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            ligolw_utils.write_filename(
                xmldoc, str(tmp_path),
                compress="gz" if path.name.endswith(".gz") else False)
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    return {
        "file": str(path),
        "postcoh_rows": rows,
        "matched_detector_rows": matched,
        "single_output_allowed_detector_rows": allowed,
        "single_output_suppressed_detector_rows": suppressed,
        "updated_detector_rows": updated,
        "updated_row_far_rows": updated_row_far,
        "cleared_single_far_values": cleared,
        "already_equal_detector_rows": already_equal,
        "zero_or_missing_before_update": zero_before,
        "missing_detector_rows": missing,
        "changed_unique_keys": len(changed_keys),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--ledger", default="single_branch/single_final_far_all.csv")
    parser.add_argument("--far-column", default="direct_far")
    parser.add_argument("--zerolag-glob", action="append",
                        default=["[0-9][0-9][0-9]/*_zerolag_*.xml.gz"])
    parser.add_argument("--summary", default="monitor/patch_zerolag_single_far_summary.json")
    parser.add_argument("--backup-suffix", default=".pre_sidecar_single_far.bak")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--clear-existing", action="store_true",
                        help="clear all existing detector-local FAR fields before applying ledger values")
    parser.add_argument("--single-output-mode",
                        default=os.environ.get("PATCH_ZEROLAG_SINGLE_OUTPUT_MODE")
                        or os.environ.get("SINGLE_OUTPUT_MODE")
                        or "single-only",
                        help="single FAR output policy: single-only, all, or never")
    parser.add_argument("--active-ifo-schedule",
                        default=os.environ.get("PATCH_ZEROLAG_SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE")
                        or os.environ.get("SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE")
                        or os.environ.get("SINGLE_OUTPUT_DETECTOR_SCHEDULE")
                        or "",
                        help="comma-separated START:END:IFOS windows, e.g. GPS:GPS:HL,GPS:GPS:H")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    run_dir = Path(args.run_dir).resolve()
    ledger_path = Path(args.ledger)
    if not ledger_path.is_absolute():
        ledger_path = run_dir / ledger_path
    summary_path = Path(args.summary)
    if not summary_path.is_absolute():
        summary_path = run_dir / summary_path

    if not ledger_path.exists() or ledger_path.stat().st_size <= 0:
        raise SystemExit(f"ledger is missing or empty: {ledger_path}")

    ledger, ledger_summary = load_ledger(ledger_path, args.far_column)
    if not ledger:
        raise SystemExit(f"ledger has no usable positive FAR rows: {ledger_path}")

    output_policy = SingleOutputPolicy.from_args(args)
    patch_row_far = args.far_column == "far"
    files = iter_zerolag_files(run_dir, args.zerolag_glob)
    if not files:
        raise SystemExit(f"no zerolag files matched under {run_dir}")

    backup_suffix = None if args.no_backup else args.backup_suffix
    file_summaries = [
        patch_file(
            path, ledger, backup_suffix=backup_suffix, dry_run=args.dry_run,
            script_dir=script_dir, clear_existing=args.clear_existing,
            output_policy=output_policy, patch_row_far=patch_row_far)
        for path in files
    ]

    total = {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_dir": str(run_dir),
        "dry_run": bool(args.dry_run),
        "backup_suffix": backup_suffix,
        "patch_row_far": patch_row_far,
        "zerolag_file_count": len(files),
        **output_policy.summary(),
        **ledger_summary,
        "postcoh_rows": sum(item["postcoh_rows"] for item in file_summaries),
        "matched_detector_rows": sum(item["matched_detector_rows"] for item in file_summaries),
        "single_output_allowed_detector_rows": sum(item["single_output_allowed_detector_rows"] for item in file_summaries),
        "single_output_suppressed_detector_rows": sum(item["single_output_suppressed_detector_rows"] for item in file_summaries),
        "updated_detector_rows": sum(item["updated_detector_rows"] for item in file_summaries),
        "updated_row_far_rows": sum(item["updated_row_far_rows"] for item in file_summaries),
        "cleared_single_far_values": sum(item["cleared_single_far_values"] for item in file_summaries),
        "already_equal_detector_rows": sum(item["already_equal_detector_rows"] for item in file_summaries),
        "zero_or_missing_before_update": sum(item["zero_or_missing_before_update"] for item in file_summaries),
        "missing_detector_rows": sum(item["missing_detector_rows"] for item in file_summaries),
        "files": file_summaries,
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_summary = summary_path.with_name(summary_path.name + ".tmp")
    with tmp_summary.open("w") as handle:
        json.dump(total, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_summary, summary_path)

    print(json.dumps({
        "summary": str(summary_path),
        "zerolag_file_count": total["zerolag_file_count"],
        "ledger_rows": total["ledger_rows"],
        "unique_keys": total["unique_keys"],
        "matched_detector_rows": total["matched_detector_rows"],
        "single_output_allowed_detector_rows": total["single_output_allowed_detector_rows"],
        "single_output_suppressed_detector_rows": total["single_output_suppressed_detector_rows"],
        "updated_detector_rows": total["updated_detector_rows"],
        "updated_row_far_rows": total["updated_row_far_rows"],
        "cleared_single_far_values": total["cleared_single_far_values"],
        "already_equal_detector_rows": total["already_equal_detector_rows"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
