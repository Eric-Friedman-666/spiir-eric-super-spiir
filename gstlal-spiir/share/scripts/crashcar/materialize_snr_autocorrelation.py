#!/usr/bin/env python3
"""Add template-autocorrelation companions to retained crashcar candidate XML."""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import json
import math
import re
import os
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off", "none"}


def read_text_maybe_gzip(path: Path) -> str:
    with path.open("rb") as handle:
        magic = handle.read(2)
    if magic == b"\x1f\x8b":
        with gzip.open(path, "rt", errors="ignore") as handle:
            return handle.read()
    return path.read_text(errors="ignore")


def parse_array(text: str, name: str) -> Tuple[int, int, List[float]]:
    match = re.search(
        r"<Array\b[^>]*Name=\"%s\"[^>]*>(.*?)</Array>"
        % re.escape(name),
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"array {name} not found")
    block = match.group(1)
    dims = [int(value) for value in re.findall(r"<Dim\b[^>]*>\s*(\d+)\s*</Dim>", block)]
    if len(dims) < 2:
        raise ValueError(f"array {name} has fewer than two dimensions")
    stream = re.search(r"<Stream\b[^>]*>(.*?)</Stream>", block, flags=re.DOTALL)
    if not stream:
        raise ValueError(f"array {name} has no stream")
    values = [float(token) for token in stream.group(1).split()]
    expected = dims[0] * dims[1]
    if len(values) < expected:
        raise ValueError(
            f"array {name} has {len(values)} values but expected {expected}"
        )
    return dims[0], dims[1], values


def load_autocorrelation_bank(path: Path) -> Tuple[int, int, List[float], List[float]]:
    text = read_text_maybe_gzip(path)
    real_len, real_ntemplate, real_values = parse_array(
        text, "autocorrelation_bank_real:array"
    )
    imag_len, imag_ntemplate, imag_values = parse_array(
        text, "autocorrelation_bank_imag:array"
    )
    if real_len != imag_len or real_ntemplate != imag_ntemplate:
        raise ValueError("real/imag autocorrelation dimensions differ")
    return real_len, real_ntemplate, real_values, imag_values


def bank_path(bank_dir: Path, ifo: str, bankid: int) -> Path:
    bank = f"{bankid:04d}"
    return bank_dir / f"iir_{ifo}-GSTLAL_SPLIT_BANK_{bank}-a1-0-0.xml.gz"


def template_series(
    cache: Dict[Tuple[str, int], Tuple[int, int, List[float], List[float]]],
    bank_dir: Path,
    ifo: str,
    bankid: int,
    tmplt_idx: int,
) -> List[Tuple[int, int, float, float, float]]:
    key = (ifo, bankid)
    if key not in cache:
        cache[key] = load_autocorrelation_bank(bank_path(bank_dir, ifo, bankid))
    length, ntemplate, real_values, imag_values = cache[key]
    if tmplt_idx < 0 or tmplt_idx >= ntemplate:
        raise ValueError(
            f"template index {tmplt_idx} outside autocorrelation bank {ifo} {bankid}"
        )
    center = (length - 1) // 2
    rows = []
    for sample_index in range(length):
        offset = sample_index * ntemplate + tmplt_idx
        real = real_values[offset]
        imag = imag_values[offset]
        rows.append(
            (
                sample_index,
                sample_index - center,
                real,
                imag,
                math.hypot(real, imag),
            )
        )
    return rows


def write_template_csv(path: Path, rows: Iterable[Tuple[int, int, float, float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["sample_index", "relative_index", "real", "imag", "abs"])
        for row in rows:
            writer.writerow(
                [row[0], row[1], f"{row[2]:.17g}", f"{row[3]:.17g}", f"{row[4]:.17g}"]
            )


def xml_escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def build_xml_element(row: dict, rows: List[Tuple[int, int, float, float, float]]) -> str:
    event_id = row.get("event_id", "")
    ifo = row.get("ifo", "")
    bankid = row.get("bankid", "")
    tmplt_idx = row.get("tmplt_idx", "")
    lines = [
        '\t<LIGO_LW Name="COMPLEX8TimeSeries">',
        '\t\t<Time Type="GPS" Name="epoch">0</Time>',
        '\t\t<Param Name="f0:param" Type="real_8" Unit="s^-1">0</Param>',
        '\t\t<Array Type="real_8" Name="template_autocorrelation:array" Unit="">',
        f'\t\t\t<Dim Name="Sample" Unit="" Start="{rows[0][1] if rows else 0}" Scale="1">{len(rows)}</Dim>',
        '\t\t\t<Dim Name="Sample,Real,Imaginary">3</Dim>',
        '\t\t\t<Stream Type="Local" Delimiter=" ">',
    ]
    for _, relative_index, real, imag, _ in rows:
        lines.append(f"\t\t\t\t{relative_index} {real:.9g} {imag:.9g} ")
    lines += [
        "\t\t\t</Stream>",
        "\t\t</Array>",
        f'\t\t<Param Name="event_id:param" Type="ilwd:char">sngl_inspiral:event_id:{xml_escape(event_id)}</Param>',
        f'\t\t<Param Name="instrument:param" Type="lstring">{xml_escape(ifo)}</Param>',
        f'\t\t<Param Name="crashcar_event_id:param" Type="int_8s">{xml_escape(event_id)}</Param>',
        f'\t\t<Param Name="bankid:param" Type="int_4s">{xml_escape(bankid)}</Param>',
        f'\t\t<Param Name="tmplt_idx:param" Type="int_4s">{xml_escape(tmplt_idx)}</Param>',
        '\t\t<Param Name="series_kind:param" Type="lstring">template_autocorrelation</Param>',
        "\t</LIGO_LW>",
    ]
    return "\n".join(lines) + "\n"


def resolve_manifest_path(snr_dir: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return snr_dir / path


def _normalise_event_id(value: object) -> str:
    event = str(value or "")
    if ":" in event:
        event = event.rsplit(":", 1)[-1]
    return event


def _row_autocorr_keys(row: dict) -> Set[Tuple[str, str, str, str]]:
    event = _normalise_event_id(row.get("event_id", ""))
    ifo = str(row.get("ifo", ""))
    bank = str(row.get("bankid", ""))
    template = str(row.get("tmplt_idx", ""))
    return {
        (event, ifo, bank, template),
        ("", ifo, bank, template),
    }


def load_embedded_template_autocorr_keys(path: Path) -> Set[Tuple[str, str, str, str]]:
    if not path.exists():
        return set()
    try:
        text = read_text_maybe_gzip(path)
    except Exception:
        return set()
    if "template_autocorrelation" not in text:
        return set()
    block_re = re.compile(
        r'<LIGO_LW Name="COMPLEX8TimeSeries">(.*?)</LIGO_LW>',
        flags=re.DOTALL,
    )
    param_re = re.compile(r'<Param Name="([^"]+):param"[^>]*>(.*?)</Param>')
    keys: Set[Tuple[str, str, str, str]] = set()
    for match in block_re.finditer(text):
        block = match.group(1)
        params = dict(param_re.findall(block))
        if params.get("series_kind") != "template_autocorrelation":
            continue
        event = _normalise_event_id(
            params.get("crashcar_event_id") or params.get("event_id") or ""
        )
        ifo = str(params.get("instrument", ""))
        bank = str(params.get("bankid", ""))
        template = str(params.get("tmplt_idx", ""))
        keys.add((event, ifo, bank, template))
        keys.add(("", ifo, bank, template))
    return keys


def xml_has_embedded_template_autocorr(
    path: Path, row: dict, cache: Dict[Path, Set[Tuple[str, str, str, str]]]
) -> bool:
    if path not in cache:
        cache[path] = load_embedded_template_autocorr_keys(path)
    return bool(cache[path].intersection(_row_autocorr_keys(row)))


def write_xml_shards(snr_dir: Path, shard_elements: Dict[str, List[str]]) -> None:
    for filename, elements in shard_elements.items():
        path = snr_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as output:
            output.write("<?xml version='1.0' encoding='utf-8'?>\n")
            output.write(
                '<!DOCTYPE LIGO_LW SYSTEM "http://ldas-sw.ligo.caltech.edu/doc/ligolwAPI/html/ligolw_dtd.txt">\n'
            )
            output.write("<LIGO_LW>\n")
            output.write('\t<LIGO_LW Name="crashcar_template_autocorrelation">\n')
            for element in elements:
                output.write(element)
            output.write("\t</LIGO_LW>\n")
            output.write("</LIGO_LW>\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--snr-dir", required=True)
    parser.add_argument("--bank-dir", required=True)
    parser.add_argument(
        "--write-template-csv",
        action="store_true",
        default=env_flag("CRASHCAR_TEMPLATE_AUTOCORR_WRITE_CSV", False),
        help=(
            "also write one template_autocorrelation_*.csv per unique template; "
            "disabled by default to avoid large numbers of small files"
        ),
    )
    args = parser.parse_args()

    manifest = Path(args.manifest)
    snr_dir = Path(args.snr_dir)
    bank_dir = Path(args.bank_dir)
    summary_path = snr_dir / "autocorrelation_summary.json"

    if not manifest.exists() or manifest.stat().st_size == 0:
        summary_path.write_text(
            json.dumps(
                {
                    "manifest": str(manifest),
                    "manifest_exists": manifest.exists(),
                    "rows": 0,
                    "template_autocorrelation_files": 0,
                    "template_autocorrelation_csv_enabled": args.write_template_csv,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return 0

    with manifest.open(newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for field in ("template_autocorrelation_file", "template_autocorrelation_xml_file",
                  "template_autocorrelation_error"):
        if field not in fieldnames:
            fieldnames.append(field)

    cache: Dict[Tuple[str, int], Tuple[int, int, List[float], List[float]]] = {}
    template_files: Dict[Tuple[str, int, int], str] = {}
    xml_templates: Dict[Tuple[str, str, int, int], str] = {}
    embedded_xml_cache: Dict[Path, Set[Tuple[str, str, str, str]]] = {}
    xml_shards: Dict[str, List[str]] = {}
    materialized = 0
    xml_materialized = 0
    errors = 0

    for row in rows:
        row["template_autocorrelation_file"] = row.get("template_autocorrelation_file", "")
        row["template_autocorrelation_xml_file"] = row.get(
            "template_autocorrelation_xml_file", ""
        )
        row["template_autocorrelation_error"] = ""
        try:
            embedded_xml = (
                row.get("template_autocorrelation_xml_file")
                or row.get("candidate_xml_file")
                or row.get("xml_file")
                or row.get("series_xml")
            )
            if embedded_xml and xml_has_embedded_template_autocorr(
                resolve_manifest_path(snr_dir, embedded_xml), row, embedded_xml_cache
            ):
                row["template_autocorrelation_xml_file"] = embedded_xml
                continue
            ifo = row["ifo"]
            bankid = int(row["bankid"])
            tmplt_idx = int(row["tmplt_idx"])
            template_key = (ifo, bankid, tmplt_idx)
            out_name = ""
            if args.write_template_csv and template_key not in template_files:
                series_rows = template_series(cache, bank_dir, ifo, bankid, tmplt_idx)
                out_name = (
                    f"template_autocorrelation_{ifo}_bank{bankid:04d}_tmpl{tmplt_idx}.csv"
                )
                write_template_csv(snr_dir / out_name, series_rows)
                template_files[template_key] = out_name
                materialized += 1
            elif args.write_template_csv:
                out_name = template_files[template_key]
            xml_file = (row.get("xml_file") or "crashcar_snr_series_worker000.xml")
            template_xml = xml_file.replace(
                "crashcar_snr_series", "crashcar_template_autocorrelation"
            )
            if template_xml == xml_file:
                template_xml = "crashcar_template_autocorrelation.xml"
            xml_key = (template_xml, str(row.get("event_id", "")), ifo, bankid, tmplt_idx)
            if xml_key not in xml_templates:
                series_rows = template_series(cache, bank_dir, ifo, bankid, tmplt_idx)
                xml_shards.setdefault(template_xml, []).append(
                    build_xml_element(row, series_rows)
                )
                xml_templates[xml_key] = template_xml
                xml_materialized += 1
            row["template_autocorrelation_file"] = out_name
            row["template_autocorrelation_xml_file"] = template_xml
        except Exception as exc:  # Keep manifest usable even if one template fails.
            row["template_autocorrelation_error"] = str(exc)
            errors += 1

    write_xml_shards(snr_dir, xml_shards)
    tmp_manifest = manifest.with_suffix(".csv.tmp")
    with tmp_manifest.open("w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp_manifest.replace(manifest)

    summary_path.write_text(
        json.dumps(
            {
                "bank_dir": str(bank_dir),
                "errors": errors,
                "manifest": str(manifest),
                "rows": len(rows),
                "template_autocorrelation_csv_enabled": args.write_template_csv,
                "template_autocorrelation_files": materialized,
                "template_autocorrelation_unique_templates": len(template_files),
                "template_autocorrelation_xml_elements": xml_materialized,
                "template_autocorrelation_xml_shards": sorted(xml_shards),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
