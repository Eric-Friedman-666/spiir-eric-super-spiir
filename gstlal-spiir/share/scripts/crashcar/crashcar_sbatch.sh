#!/usr/bin/env bash
#SBATCH --job-name=crashcar
#SBATCH --ntasks=1
#SBATCH --time=24:00:00
#SBATCH --mem=32g
#SBATCH --cpus-per-task=4
#SBATCH --array=0-1
#SBATCH -o logs/crashcar_%A_%a.out
#SBATCH -e logs/crashcar_%A_%a.err

set -euo pipefail

RUN_DIR=${RUN_DIR:?RUN_DIR required}
CRASH_ROOT=${CRASH_ROOT:?CRASH_ROOT required}
TOP_RUN_ROOT=${TOP_RUN_ROOT:?TOP_RUN_ROOT required}

crashcar_binding_error() {
    printf 'crashcar_sbatch: %s\n' "$*" >&2
    return 2
}

crashcar_finish_pipeline_status() {
    local status_file=$1
    local done_file=$2
    local pipeline_rc
    if [ ! -s "${status_file}" ]; then
        printf 'crashcar_sbatch: pipeline exited without a direct status record\n' >&2
        return 125
    fi
    IFS= read -r pipeline_rc < "${status_file}"
    if [[ ! "${pipeline_rc}" =~ ^(0|[1-9][0-9]{0,2})$ ]] ||
       [ "${pipeline_rc}" -gt 255 ]; then
        printf 'crashcar_sbatch: invalid direct pipeline status %q\n' "${pipeline_rc}" >&2
        return 125
    fi
    if [ "${pipeline_rc}" -ne 0 ]; then
        return "${pipeline_rc}"
    fi
    rm -f -- "${status_file}"
    printf 'DONE %s\n' "$(date -u +%FT%TZ)" > "${done_file}"
}

resolve_crashcar_background_binding() {
    local worker_id=${SLURM_ARRAY_TASK_ID:?SLURM_ARRAY_TASK_ID required}
    local worker_count=${CRASHCAR_CURRENT_WORKER_COUNT:?current worker count required}
    local banks_per_worker=${CRASHCAR_CURRENT_BANKS_PER_WORKER:?current banks per worker required}
    local start_bank=${CRASHCAR_CURRENT_START_BANK:?current start bank required}
    local mode=${CRASHCAR_SINGLE_BACKGROUND_MODE:-${SINGLE_BACKGROUND_MODE:-rolling}}
    local worker_jobno expected_roster="" bank_id value_name canonical_root
    local -a live_values=()
    for value_name in worker_id worker_count banks_per_worker start_bank; do
        if [[ ! "${!value_name}" =~ ^(0|[1-9][0-9]*)$ ]]; then
            crashcar_binding_error "${value_name} must be a canonical nonnegative integer"; return 2
        fi
    done
    if [ "${worker_count}" -lt 1 ] || [ "${worker_count}" -gt 4096 ] ||
       [ "${banks_per_worker}" -lt 1 ] || [ "${worker_id}" -ge "${worker_count}" ]; then
        crashcar_binding_error "current worker geometry is invalid"; return 2
    fi
    worker_jobno=$(printf '%03d' "${worker_id}")
    for bank_id in $(seq $((start_bank+banks_per_worker*worker_id)) $((start_bank+banks_per_worker*(worker_id+1)-1))); do
        [ -z "${expected_roster}" ] || expected_roster="${expected_roster},"
        expected_roster="${expected_roster}${bank_id}"
    done
    case "${CRASHCAR_BG_ONLY:-0}" in
        0) ;;
        1)
            if [ "${mode}" != rolling ] || [ "${WGUO_O3A_INJECTION_MODE:-none}" != none ] ||
               [ -n "${WGUO_O3A_INJECTION_FILE:-}" ]; then
                crashcar_binding_error "BG-only requires rolling mode and no injection input"; return 2
            fi ;;
        *) crashcar_binding_error "CRASHCAR_BG_ONLY must be exactly 0 or 1"; return 2 ;;
    esac
    case "${mode}" in
        rolling)
            if [ "${WGUO_O3A_INJECTION_MODE:-none}" != none ]; then
                crashcar_binding_error "injection foreground cannot use rolling background mode"; return 2
            fi
            for value_name in CRASHCAR_CURRENT_RUN_NAMESPACE_SHA256 \
                CRASHCAR_CURRENT_SOURCE_MANIFEST_SHA256 CRASHCAR_CURRENT_RUNTIME_MANIFEST_SHA256 \
                CRASHCAR_CURRENT_CONFIG_SHA256 CRASHCAR_CURRENT_SEGMENT_XML_SHA256 \
                CRASHCAR_CURRENT_SEGMENT_CANONICAL_SHA256 CRASHCAR_CURRENT_TEMPLATE_SHAPE_MAP_SHA256; do
                if [[ ! "${!value_name:-}" =~ ^[0-9a-f]{64}$ ]]; then
                    crashcar_binding_error "invalid rolling schema4 pin ${value_name}"; return 2
                fi
            done
            export CRASHCAR_BG_WORKER_COUNT="${worker_count}"
            export CRASHCAR_BG_ORIGIN_GPS="${WGUO_O3A_START_GPS:?start GPS required}"
            export CRASHCAR_BG_RUN_NAMESPACE_SHA256="${CRASHCAR_CURRENT_RUN_NAMESPACE_SHA256}"
            export CRASHCAR_BG_SOURCE_MANIFEST_SHA256="${CRASHCAR_CURRENT_SOURCE_MANIFEST_SHA256}"
            export CRASHCAR_BG_RUNTIME_MANIFEST_SHA256="${CRASHCAR_CURRENT_RUNTIME_MANIFEST_SHA256}"
            export CRASHCAR_BG_CONFIG_SHA256="${CRASHCAR_CURRENT_CONFIG_SHA256}"
            export CRASHCAR_BG_SEGMENT_XML_SHA256="${CRASHCAR_CURRENT_SEGMENT_XML_SHA256}"
            export CRASHCAR_BG_SEGMENT_CANONICAL_SHA256="${CRASHCAR_CURRENT_SEGMENT_CANONICAL_SHA256}"
            export CRASHCAR_TEMPLATE_SHAPE_MAP_SHA256="${CRASHCAR_CURRENT_TEMPLATE_SHAPE_MAP_SHA256}"
            export CRASHCAR_SINGLE_BACKGROUND_JSON="${RUN_DIR}/${worker_jobno}/single_background.json"
            unset CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON CRASHCAR_LIVE_BACKGROUND_ROOT
            ;;
        live_readonly)
            if [ "${CRASHCAR_BG_ONLY:-0}" != 0 ] || [ "${WGUO_O3A_INJECTION_MODE:-none}" = none ] ||
               [ -z "${WGUO_O3A_INJECTION_FILE:-}" ] ||
               [ "${CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE:-}" != consumer ]; then
                crashcar_binding_error "live_readonly requires injection consumer mode without accumulators"; return 2
            fi
            if [[ "${CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROOT:-}" != /* ]] ||
               ! canonical_root=$(readlink -f -- "${CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROOT}") ||
               [ ! -d "${canonical_root}/run" ] ||
               [ "${canonical_root}" = "$(readlink -f -- "${TOP_RUN_ROOT}")" ]; then
                crashcar_binding_error "live producer root is invalid"; return 2
            fi
            if [ ! -f "${CRASHCAR_LIVE_SINGLE_BINDING_JSON:-}" ] ||
               [ -L "${CRASHCAR_LIVE_SINGLE_BINDING_JSON:-}" ]; then
                crashcar_binding_error "live single producer binding is unavailable"; return 2
            fi
            mapfile -d '' live_values < <(
                python3 - "${CRASHCAR_LIVE_SINGLE_BINDING_JSON}" "${canonical_root}" \
                    "${worker_id}" "${worker_count}" "${banks_per_worker}" \
                    "${start_bank}" "${expected_roster}" \
                    "${CRASHCAR_LIVE_BG_ORIGIN_GPS:?producer origin GPS required}" <<'PY_LIVE'
import json, math, os, re, stat, sys
(path, root, worker_text, count_text, bpw_text, start_text,
 roster_text, origin_text) = sys.argv[1:]
def fail(msg): raise SystemExit("crashcar_sbatch live binding: "+msg)
def strict(pairs):
    out={}
    for k,v in pairs:
        if k in out: fail("duplicate key "+k)
        out[k]=v
    return out
info=os.lstat(path)
if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode): fail("binding contract is not regular")
with open(path,encoding="utf-8") as h: value=json.load(h,object_pairs_hook=strict)
worker=int(worker_text); count=int(count_text); bpw=int(bpw_text); start=int(start_text); origin=int(origin_text)
if value.get("kind")!="crashcar_live_single_binding_contract_v1" or value.get("producer_root")!=root: fail("producer mismatch")
if value.get("producer_origin_gps")!=origin: fail("producer origin mismatch")
if value.get("background_files_required_at_submit") is not False: fail("soft-start contract mismatch")
if (value.get("worker_count"),value.get("banks_per_worker"),value.get("start_bank"))!=(count,bpw,start): fail("geometry mismatch")
workers=value.get("workers")
if type(workers) is not list or len(workers)!=count: fail("worker list mismatch")
item=workers[worker]
if item.get("worker_id")!=worker or item.get("worker_count")!=count: fail("worker mismatch")
if item.get("worker_bank_ids")!=[int(x) for x in roster_text.split(",")]: fail("bank roster mismatch")
expected=os.path.join(root,"run",f"{worker:03d}","single_background.json")
if item.get("single_background_path")!=expected: fail("single path mismatch")
ids=value.get("identities"); keys=("run_namespace_sha256","source_manifest_sha256","runtime_manifest_sha256","config_sha256","segment_xml_sha256","segment_canonical_sha256","template_shape_map_sha256")
hex64=re.compile(r"^[0-9a-f]{64}$")
if type(ids) is not dict or any(not hex64.fullmatch(ids.get(k,"")) for k in keys): fail("provenance mismatch")
tail=value.get("tail_log10_far")
if (type(tail) not in (int,float) or isinstance(tail,bool)
        or not math.isfinite(float(tail)) or not float(tail)<0.0):
    fail("producer tail anchor mismatch")
tail_text=format(float(tail),".17g")
items=[expected]+[ids[k] for k in keys]+[tail_text,"OK"]
sys.stdout.buffer.write(b"\0".join(x.encode() for x in items)+b"\0")
PY_LIVE
            )
            if [ "${#live_values[@]}" -ne 10 ] || [ "${live_values[9]}" != OK ]; then
                crashcar_binding_error "strict live single producer binding validation failed"; return 2
            fi
            export CRASHCAR_LIVE_BACKGROUND_ROOT="${canonical_root}"
            export CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON="${live_values[0]}"
            unset CRASHCAR_SINGLE_BACKGROUND_JSON
            export CRASHCAR_BG_WORKER_COUNT="${worker_count}"
            export CRASHCAR_BG_ORIGIN_GPS="${CRASHCAR_LIVE_BG_ORIGIN_GPS:?producer origin GPS required}"
            export CRASHCAR_BG_RUN_NAMESPACE_SHA256="${live_values[1]}"
            export CRASHCAR_BG_SOURCE_MANIFEST_SHA256="${live_values[2]}"
            export CRASHCAR_BG_RUNTIME_MANIFEST_SHA256="${live_values[3]}"
            export CRASHCAR_BG_CONFIG_SHA256="${live_values[4]}"
            export CRASHCAR_BG_SEGMENT_XML_SHA256="${live_values[5]}"
            export CRASHCAR_BG_SEGMENT_CANONICAL_SHA256="${live_values[6]}"
            export CRASHCAR_TEMPLATE_SHAPE_MAP_SHA256="${live_values[7]}"
            export TAIL_LOG_FAR="${live_values[8]}"
            ;;
        *) crashcar_binding_error "single background mode must be rolling or live_readonly"; return 2 ;;
    esac
    export CRASHCAR_WORKER_BANK_IDS_EXPECTED="${expected_roster}"
}

validate_live_multi_worker_inputs() {
    local root=${CRASHCAR_LIVE_BACKGROUND_ROOT:?live background root required}
    local worker=${SLURM_ARRAY_TASK_ID:?SLURM_ARRAY_TASK_ID required}
    python3 - "${root}" "${worker}" <<'PY_MULTI_READY'
import gzip
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import xml.etree.ElementTree as ET

root = Path(sys.argv[1]).resolve(strict=True)
worker = int(sys.argv[2])
if worker < 0 or worker > 4095:
    raise SystemExit("invalid worker id")
jobno = f"{worker:03d}"
worker_root = root / "run" / jobno
if worker_root.resolve(strict=True) != worker_root:
    raise SystemExit(f"multi worker {worker} directory is not direct")

INT = re.compile(r"[+-]?[0-9]+")
IFOS = ("H1", "L1", "V1", "H1L1V1")
ARRAYS = (
    ("background_feature:{ifo}_lgsnr_rate:array", "int_8s", (300,), "count"),
    ("background_feature:{ifo}_lgchisq_rate:array", "int_8s", (300,), "count"),
    ("background_feature:{ifo}_lgsnr_lgchisq_rate:array",
     "int_8s", (300, 300), "count"),
    ("background_feature:{ifo}_lgsnr_lgchisq_pdf:array",
     "real_8", (300, 300), "nonnegative"),
    ("background_rank:{ifo}_rank_map:array",
     "real_8", (300, 300), "finite"),
    ("background_rank:{ifo}_rank_rate:array", "int_8s", (300,), "count"),
    ("background_rank:{ifo}_rank_pdf:array",
     "real_8", (300,), "nonnegative"),
    ("background_rank:{ifo}_rank_fap:array",
     "real_8", (300,), "probability"),
)
TABLE_COLUMNS = (
    ("background_rank:rank_rate:cmin", "real_4"),
    ("background_rank:rank_rate:cmax", "real_4"),
    ("background_rank:rank_rate:nbin", "int_4s"),
)

def fail(message):
    raise SystemExit(f"multi {worker}: {message}")

def named(parent, tag, name):
    matches = [
        child for child in parent.findall(tag)
        if child.get("Name") == name
    ]
    if len(matches) != 1:
        fail(f"{tag} {name} count is {len(matches)}, expected 1")
    return matches[0]

def parse_integer(token, label, bits, nonnegative=False):
    if not INT.fullmatch(token):
        fail(f"{label} contains a non-integer token")
    value = int(token)
    low = -(1 << (bits - 1))
    high = (1 << (bits - 1)) - 1
    if value < low or value > high or (nonnegative and value < 0):
        fail(f"{label} integer is outside its physical/type range")
    return value

def parse_float(token, label, nonnegative=False, probability=False):
    try:
        value = float(token)
    except ValueError:
        fail(f"{label} contains a non-numeric token")
    if not math.isfinite(value):
        fail(f"{label} contains a non-finite token")
    if nonnegative and value < 0:
        fail(f"{label} contains a negative density")
    if probability and not 0 <= value <= 1.000001:
        fail(f"{label} contains a value outside probability range")
    return value

def validate_table(stats):
    table = named(stats, "Table", "background_rank:rank_rate:table")
    columns = tuple(
        (item.get("Name"), item.get("Type"))
        for item in table.findall("Column")
    )
    if columns != TABLE_COLUMNS:
        fail("background rank-rate table columns/type/order mismatch")
    stream = named(table, "Stream", "background_rank:rank_rate:table")
    if stream.get("Type") != "Local" or stream.get("Delimiter") != ",":
        fail("background rank-rate table Stream attributes mismatch")
    raw = (stream.text or "").strip()
    tokens = [token.strip() for token in raw.split(",")]
    while tokens and tokens[-1] == "":
        tokens.pop()
    if len(tokens) != 3 or any(token == "" for token in tokens):
        fail("background rank-rate table Stream is empty or malformed")
    try:
        cmin = float(tokens[0])
        cmax = float(tokens[1])
    except ValueError:
        fail("background rank-rate table bounds are non-numeric")
    nbin = parse_integer(tokens[2], "background rank-rate nbin", 32)
    if not math.isfinite(cmin) or not math.isfinite(cmax):
        fail("background rank-rate table bounds are non-finite")
    if (cmin, cmax, nbin) != (-30.0, 0.0, 300):
        fail("background rank-rate table values mismatch loader constants")

def validate_array(stats, name, type_name, dims, value_kind):
    array = named(stats, "Array", name)
    if array.get("Type") != type_name:
        fail(f"{name} Type mismatch")
    dim_nodes = array.findall("Dim")
    actual_dims = tuple(
        parse_integer((node.text or "").strip(), name + " Dim", 32)
        for node in dim_nodes
    )
    if actual_dims != dims:
        fail(f"{name} Dim mismatch: {actual_dims!r}")
    streams = array.findall("Stream")
    if len(streams) != 1:
        fail(f"{name} Stream count is {len(streams)}, expected 1")
    stream = streams[0]
    if stream.get("Type") != "Local" or stream.get("Delimiter") != " ":
        fail(f"{name} Stream attributes mismatch")
    tokens = (stream.text or "").split()
    expected = math.prod(dims)
    if len(tokens) != expected:
        fail(f"{name} Stream token count {len(tokens)} != {expected}")
    if type_name == "int_8s":
        for token in tokens:
            parse_integer(token, name, 64, nonnegative=True)
    else:
        for token in tokens:
            parse_float(
                token, name,
                nonnegative=value_kind == "nonnegative",
                probability=value_kind == "probability",
            )

def validate_param(stats, name, type_name, bits, positive=False):
    param = named(stats, "Param", name)
    if param.get("Type") != type_name:
        fail(f"{name} Type mismatch")
    token = (param.text or "").strip()
    value = parse_integer(token, name, bits, nonnegative=True)
    if positive and value <= 0:
        fail(f"{name} must be positive")
    return value

def validate_loader_contract(document):
    if document.tag != "LIGO_LW":
        fail("outer LIGO_LW is missing")
    matches = [
        child for child in document.findall("LIGO_LW")
        if child.get("Name") == "gstlal_postcohspiir_stats"
    ]
    if len(matches) != 1:
        fail("nested gstlal_postcohspiir_stats node is missing or duplicated")
    stats = matches[0]
    validate_table(stats)
    for ifo in IFOS:
        for pattern, type_name, dims, value_kind in ARRAYS:
            validate_array(
                stats, pattern.format(ifo=ifo), type_name, dims, value_kind)
        validate_param(
            stats, f"background_feature:{ifo}_nevent:param",
            "int_8s", 64)
        validate_param(
            stats, f"background_feature:{ifo}_livetime:param",
            "int_8s", 64)
    validate_param(
        stats, "background_feature:hist_trials:param",
        "int_4s", 32, positive=True)

records = []
for span in ("2w", "1d", "2h"):
    path = worker_root / f"{jobno}_marginalized_stats_{span}.xml.gz"
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise SystemExit(f"multi {worker}/{span} is unavailable: {exc}")
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise SystemExit(f"multi {worker}/{span} is not a regular non-symlink file")
    if before.st_size < 1:
        raise SystemExit(f"multi {worker}/{span} is empty")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        with os.fdopen(fd, "rb", closefd=False) as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
                document = ET.parse(stream).getroot()
        validate_loader_contract(document)
    except (OSError, EOFError, ET.ParseError) as exc:
        raise SystemExit(
            f"multi {worker}/{span} is not complete gzip/XML: {exc}")
    finally:
        os.close(fd)
    after = os.lstat(path)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(opened) or identity(opened) != identity(after):
        raise SystemExit(f"multi {worker}/{span} changed during validation")
    records.append({
        "span": span, "path": str(path), "size": opened.st_size,
        "mtime_ns": opened.st_mtime_ns,
    })
print(json.dumps({
    "kind": "crashcar_live_multi_worker_readiness",
    "producer_root": str(root), "worker_id": worker, "files": records,
}, separators=(",", ":"), sort_keys=True))
PY_MULTI_READY
}

write_live_background_wait_status() {
    local status_file=$1 phase=$2 attempt=$3
    local single_ready=$4 multi_ready=$5
    local single_snapshot=${6:-} multi_snapshot=${7:-}
    python3 - "${status_file}" "${phase}" "${attempt}" \
        "${single_ready}" "${multi_ready}" \
        "${CRASHCAR_LIVE_BACKGROUND_ROOT:?}" \
        "${SLURM_ARRAY_TASK_ID:?}" \
        "${CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON:?}" \
        "${single_snapshot}" "${multi_snapshot}" <<'PY_WAIT_STATUS'
import datetime
import json
import os
from pathlib import Path
import sys

(status_text, phase, attempt_text, single_text, multi_text,
 root, worker_text, single_path, single_snapshot, multi_snapshot) = sys.argv[1:]
worker = int(worker_text)
jobno = f"{worker:03d}"
payload = {
    "schema_version": 1,
    "phase": phase,
    "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "attempt": int(attempt_text),
    "worker_id": worker,
    "producer_root": root,
    "single_background_path": single_path,
    "normal_multi_paths": [
        os.path.join(root, "run", jobno,
                     f"{jobno}_marginalized_stats_{span}.xml.gz")
        for span in ("2w", "1d", "2h")
    ],
    "single_ready": single_text == "1",
    "normal_multi_ready": multi_text == "1",
}
if phase == "live_backgrounds_ready":
    with open(single_snapshot, encoding="utf-8") as handle:
        single = json.load(handle)
    with open(multi_snapshot, encoding="utf-8") as handle:
        multi = json.load(handle)
    payload["single_background"] = {
        key: single[key] for key in (
            "worker_id", "accepted_version", "coverage_end_gps_ns",
            "single_background_path", "single_background_sha256")
    }
    payload["normal_multi"] = multi["files"]
status = Path(status_text)
temporary = status.with_name(status.name + ".tmp")
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
    handle.write("\n")
os.replace(temporary, status)
PY_WAIT_STATUS
}

wait_for_live_background_inputs() {
    local mode=${CRASHCAR_SINGLE_BACKGROUND_MODE:-${SINGLE_BACKGROUND_MODE:-rolling}}
    [ "${mode}" = live_readonly ] || return 0
    local helper="${TOP_RUN_ROOT}/scripts/crashcar_live_background.py"
    if [ ! -x "${helper}" ] || [ -L "${helper}" ]; then
        crashcar_binding_error "staged live single validator is unavailable"; return 2
    fi
    local worker=${SLURM_ARRAY_TASK_ID:?SLURM_ARRAY_TASK_ID required}
    local worker_jobno status_file single_tmp multi_tmp
    worker_jobno=$(printf '%03d' "${worker}")
    status_file="${RUN_DIR}/monitor/live_background_wait_${worker_jobno}.json"
    single_tmp="${status_file}.single.tmp"
    multi_tmp="${status_file}.multi.tmp"
    local attempt=0 single_ready=0 multi_ready=0
    write_live_background_wait_status \
        "${status_file}" waiting_live_backgrounds 0 0 0
    while true; do
        attempt=$((attempt + 1))
        single_ready=0
        multi_ready=0
        if "${helper}" validate-single \
            --producer-root "${CRASHCAR_LIVE_BACKGROUND_ROOT}" \
            --worker "${worker}" \
            --worker-count "${CRASHCAR_CURRENT_WORKER_COUNT}" \
            --banks-per-worker "${CRASHCAR_CURRENT_BANKS_PER_WORKER}" \
            --start-bank "${CRASHCAR_CURRENT_START_BANK}" \
            >"${single_tmp}" 2>/dev/null; then
            single_ready=1
        fi
        if [ "${single_ready}" = 1 ] &&
           validate_live_multi_worker_inputs >"${multi_tmp}" 2>/dev/null; then
            multi_ready=1
        fi
        if [ "${single_ready}" = 1 ] && [ "${multi_ready}" = 1 ]; then
            write_live_background_wait_status \
                "${status_file}" live_backgrounds_ready "${attempt}" 1 1 \
                "${single_tmp}" "${multi_tmp}"
            rm -f -- "${single_tmp}" "${multi_tmp}"
            printf 'crashcar_sbatch: worker %s live backgrounds are ready; starting pipeline\n' \
                "${worker_jobno}"
            return 0
        fi
        write_live_background_wait_status \
            "${status_file}" waiting_live_backgrounds "${attempt}" \
            "${single_ready}" "${multi_ready}"
        rm -f -- "${single_tmp}" "${multi_tmp}"
        if [ "${attempt}" -eq 1 ] || [ $((attempt % 6)) -eq 0 ]; then
            printf 'crashcar_sbatch: worker %s waiting for live backgrounds single=%s multi=%s\n' \
                "${worker_jobno}" "${single_ready}" "${multi_ready}"
        fi
        sleep 10
    done
}

# BEGIN_CRASHCAR_SEGMENT_RUNTIME_BINDING
verify_segment_runtime_binding() {
    local runtime_manifest="${TOP_RUN_ROOT}/provenance/runtime_snapshot/runtime_manifest.env"
    local expected_manifest_sha="${CRASHCAR_RUNTIME_PROVENANCE_MANIFEST_SHA256:-}"
    local manifest_snapshot actual_path actual_sha variable

    if [[ ! "${expected_manifest_sha}" =~ ^[0-9a-f]{64}$ ]]; then
        printf 'crashcar_sbatch: pinned runtime manifest sha256 must be exact lowercase64\n' >&2
        return 2
    fi
    if [ ! -r "${runtime_manifest}" ]; then
        printf 'crashcar_sbatch: missing segment runtime manifest %s\n' "${runtime_manifest}" >&2
        return 2
    fi
    if [ -w "${runtime_manifest}" ] || [ -w "$(dirname "${runtime_manifest}")" ]; then
        printf 'crashcar_sbatch: segment runtime manifest is not immutable\n' >&2
        return 2
    fi
    # Read the manifest path exactly once.  Hash and source the same in-memory
    # snapshot so a queued-run path replacement cannot create a hash/source
    # time-of-check/time-of-use split.
    if ! manifest_snapshot=$(<"${runtime_manifest}"); then
        printf 'crashcar_sbatch: unable to snapshot runtime manifest\n' >&2
        return 2
    fi
    actual_sha=$(printf '%s\n' "${manifest_snapshot}" | sha256sum | awk '{print $1}')
    if [ "${actual_sha}" != "${expected_manifest_sha}" ]; then
        printf 'crashcar_sbatch: pinned runtime manifest sha256 mismatch\n' >&2
        return 2
    fi
    # These names are owned only by the pinned manifest.  Slurm exports the
    # submit environment, so clear any inherited values before sourcing the
    # authenticated snapshot; a missing field must remain missing/fail closed.
    for variable in \
        crashcar_segment_xml_absolute_path crashcar_segment_xml_sha256 \
        crashcar_segment_livetime_json_absolute_path \
        crashcar_segment_livetime_json_sha256 crashcar_segment_run_start \
        crashcar_segment_run_end; do
        unset "${variable}"
    done
    # The controller emits exactly one trailing newline. Command substitution
    # strips it and the here-string restores that same byte before sourcing.
    # shellcheck source=/dev/null
    if ! source /dev/stdin <<< "${manifest_snapshot}"; then
        printf 'crashcar_sbatch: pinned runtime manifest parse failed\n' >&2
        return 2
    fi
    unset manifest_snapshot
    for variable in \
        crashcar_segment_xml_absolute_path crashcar_segment_xml_sha256 \
        crashcar_segment_livetime_json_absolute_path \
        crashcar_segment_livetime_json_sha256 crashcar_segment_run_start \
        crashcar_segment_run_end; do
        if [ -z "${!variable:-}" ]; then
            printf 'crashcar_sbatch: segment binding manifest missing %s\n' "${variable}" >&2
            return 2
        fi
    done
    for variable in WGUO_O3A_SEGMENT_XML SEGMENT_XML SINGLE_SEGMENT_XML; do
        if ! actual_path=$(readlink -f -- "${!variable:-}"); then
            printf 'crashcar_sbatch: segment binding path missing for %s\n' "${variable}" >&2
            return 2
        fi
        if [ "${actual_path}" != "${crashcar_segment_xml_absolute_path}" ]; then
            printf 'crashcar_sbatch: raw XML path mismatch for %s\n' "${variable}" >&2
            return 2
        fi
    done
    if ! actual_path=$(readlink -f -- "${CRASHCAR_SEGMENT_LIVETIME_CSV:-}"); then
        printf 'crashcar_sbatch: canonical derivative path missing\n' >&2
        return 2
    fi
    if [ "${actual_path}" != "${crashcar_segment_livetime_json_absolute_path}" ]; then
        printf 'crashcar_sbatch: canonical derivative path mismatch\n' >&2
        return 2
    fi
    if [ "${WGUO_O3A_START_GPS:-}" != "${crashcar_segment_run_start}" ] ||
       [ "${WGUO_O3A_END_GPS:-}" != "${crashcar_segment_run_end}" ]; then
        printf 'crashcar_sbatch: segment run interval mismatch\n' >&2
        return 2
    fi
    actual_sha=$(sha256sum "${crashcar_segment_xml_absolute_path}" | awk '{print $1}')
    if [ "${actual_sha}" != "${crashcar_segment_xml_sha256}" ]; then
        printf 'crashcar_sbatch: raw XML sha256 mismatch\n' >&2
        return 2
    fi
    actual_sha=$(sha256sum "${crashcar_segment_livetime_json_absolute_path}" | awk '{print $1}')
    if [ "${actual_sha}" != "${crashcar_segment_livetime_json_sha256}" ]; then
        printf 'crashcar_sbatch: derivative sha256 mismatch\n' >&2
        return 2
    fi
}
# END_CRASHCAR_SEGMENT_RUNTIME_BINDING

verify_segment_runtime_binding
resolve_crashcar_background_binding

cd "${RUN_DIR}"
mkdir -p logs monitor

export GST_DEBUG=${GST_DEBUG:-}
export X509_USER_PROXY=${X509_USER_PROXY:-}
export X509_USER_KEY=${X509_USER_KEY:-}
export X509_USER_CERT=${X509_USER_CERT:-}
export KRB5_KTNAME=${KRB5_KTNAME:-}
export PYTHONPATH=${PYTHONPATH:-}
export PKG_CONFIG_PATH=${PKG_CONFIG_PATH:-}
export GST_PLUGIN_PATH=${GST_PLUGIN_PATH:-}
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}

collect_walltime_for_container=${FINALSINK_FAPUPDATER_COLLECT_WALLTIME:-}
if [ -z "${collect_walltime_for_container}" ]; then
  if [ "${SINGLE_BACKGROUND_MODE:-${CRASHCAR_SINGLE_BACKGROUND_MODE:-rolling}}" = "live_readonly" ] || [ "${CRASHCAR_SINGLE_BACKGROUND_MODE:-}" = "live_readonly" ]; then
    collect_interval=${FINALSINK_FAPUPDATER_INTERVAL_SECONDS:-${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}}
    collect_walltime_for_container="${collect_interval},${collect_interval},${collect_interval}"
  else
    collect_interval=${BACKGROUND_ACCUMULATION_SECONDS:-${FORMAL_BACKGROUND_ACCUMULATION_SECONDS:-10800}}
    collect_walltime_for_container="${collect_interval},${collect_interval},${collect_interval}"
  fi
fi
export WGUO_O3A_COLLECT_WALLTIME="${collect_walltime_for_container}"
export FINALSINK_FAPUPDATER_COLLECT_WALLTIME="${collect_walltime_for_container}"

source /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh

{
  printf 'HOST=%s\n' "$(hostname)"
  printf 'UTC=%s\n' "$(date -u +%FT%TZ)"
  printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:-manual}"
  printf 'SLURM_ARRAY_TASK_ID=%s\n' "${SLURM_ARRAY_TASK_ID:-0}"
  env | grep -E '^(WGUO_O3A|CRASHCAR|BACKGROUND|FORMAL|COHFAR|FINALSINK|DATA_|RUN_DIR|CRASH_ROOT|TOP_RUN_ROOT|SINGLE_)=' | sort
} > "logs/env_${SLURM_JOB_ID:-manual}_${SLURM_ARRAY_TASK_ID:-0}.env"

wait_for_live_background_inputs

pipeline_exit_status_file="${RUN_DIR}/logs/pipeline_exit_${SLURM_JOB_ID:-manual}_${SLURM_ARRAY_TASK_ID:-0}.status"
rm -f -- "${pipeline_exit_status_file}"

run_spiir_py3 \
	  -e SLURM_ARRAY_TASK_ID="${SLURM_ARRAY_TASK_ID:-0}" \
	  -e CRASH_ROOT="${CRASH_ROOT}" \
	  -e TOP_RUN_ROOT="${TOP_RUN_ROOT}" \
	  -e CRASHCAR_ENABLE="${CRASHCAR_ENABLE:-1}" \
	  -e WGUO_O3A_INJECTION_MODE="${WGUO_O3A_INJECTION_MODE:-none}" \
	  -e WGUO_O3A_INJECTION_FILE="${WGUO_O3A_INJECTION_FILE:-}" \
	  -e WGUO_O3A_START_GPS="${WGUO_O3A_START_GPS:?}" \
  -e WGUO_O3A_END_GPS="${WGUO_O3A_END_GPS:?}" \
  -e WGUO_O3A_DETRSP_MAP="${WGUO_O3A_DETRSP_MAP:-/fred/oz016/wguo/odds_ratio/O3a/chunk14/multi_det-BNS-LVK_inj/H1L1V1_1248134334_detrsp_map.xml}" \
  -e WGUO_O3A_FRAME_CACHE="${WGUO_O3A_FRAME_CACHE:-/fred/oz016/sunil/run_utils/frames_chache/frame_O3a.cache}" \
  -e WGUO_O3A_NONINJ_STATS_LOC="${WGUO_O3A_NONINJ_STATS_LOC:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}" \
  -e WGUO_O3A_BANK_DIR="${WGUO_O3A_BANK_DIR:-/fred/oz016/sunil/O3b_py3_banks}" \
  -e WGUO_O3A_START_BANK="${WGUO_O3A_START_BANK:-0}" \
  -e WGUO_O3A_BANKS_PER_GROUP="${WGUO_O3A_BANKS_PER_GROUP:-8}" \
  -e WGUO_O3A_SNAPSHOT_INTERVAL="${WGUO_O3A_SNAPSHOT_INTERVAL:-3600}" \
  -e WGUO_O3A_COLLECT_WALLTIME="${collect_walltime_for_container}" \
  -e FINALSINK_FAPUPDATER_COLLECT_WALLTIME="${collect_walltime_for_container}" \
  -e SNR_series_logFAR_threshold="${SNR_series_logFAR_threshold:?}" \
  -e WGUO_O3A_GRACEDB_FAR_THRESH="${WGUO_O3A_GRACEDB_FAR_THRESH:-0}" \
  -e WGUO_O3A_FINALSINK_NEED_ONLINE_PERFORM="${WGUO_O3A_FINALSINK_NEED_ONLINE_PERFORM:-0}" \
  -e BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION_SECONDS:-10800}" \
  -e FORMAL_BACKGROUND_ACCUMULATION_SECONDS="${FORMAL_BACKGROUND_ACCUMULATION_SECONDS:-10800}" \
  -e CRASHCAR_BACKGROUND_REQUIRED_SECONDS="${CRASHCAR_BACKGROUND_REQUIRED_SECONDS:-10800}" \
  -e BACKGROUND_UPDATE_TRIGGER_SECONDS="${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}" \
  -e CRASHCAR_DOF="${CRASHCAR_DOF:-}" \
  -e COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS="${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS:-${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}}" \
  -e COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS="${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS:-${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}}" \
  -e FINALSINK_FAPUPDATER_INTERVAL_SECONDS="${FINALSINK_FAPUPDATER_INTERVAL_SECONDS:-${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}}" \
  -e CRASHCAR_SNAPSHOT_INTERVAL_SECONDS="${CRASHCAR_SNAPSHOT_INTERVAL_SECONDS:-3600}" \
  -e CRASHCAR_LOG10_FAR_THRESHOLD="${CRASHCAR_LOG10_FAR_THRESHOLD:-90}" \
  -e TAIL_LOG_FAR="${TAIL_LOG_FAR:--2}" \
  -e CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME="${CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME:-}" \
  -e CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP="${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}" \
  -e CRASHCAR_SEGMENT_LIVETIME_CSV="${CRASHCAR_SEGMENT_LIVETIME_CSV:-}" \
  -e CRASHCAR_SEGMENT_LIVETIME_JSON_SHA256="${crashcar_segment_livetime_json_sha256:?pinned segment JSON sha256 required}" \
  -e CRASHCAR_SEGMENT_SOURCE_XML_SHA256="${crashcar_segment_xml_sha256:?pinned segment XML sha256 required}" \
  -e CRASHCAR_SEGMENT_RUN_START="${crashcar_segment_run_start:?pinned segment run start required}" \
  -e CRASHCAR_SEGMENT_RUN_END="${crashcar_segment_run_end:?pinned segment run end required}" \
  -e CRASHCAR_SUPPORT_DEBUG="${CRASHCAR_SUPPORT_DEBUG:-0}" \
	  -e CRASHCAR_SUPPORT_DEBUG_FNAME="${CRASHCAR_SUPPORT_DEBUG_FNAME:-}" \
	  -e CRASHCAR_CLUSTER_DEBUG="${CRASHCAR_CLUSTER_DEBUG:-0}" \
	  -e CRASHCAR_CLUSTER_DEBUG_FNAME="${CRASHCAR_CLUSTER_DEBUG_FNAME:-}" \
	  -e CRASHCAR_BG_ONLY="${CRASHCAR_BG_ONLY:-0}" \
	  -e CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE="${CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE:-}" \
	  -e CRASHCAR_BG_WORKER_COUNT="${CRASHCAR_BG_WORKER_COUNT:?}" \
	  -e CRASHCAR_BG_ORIGIN_GPS="${CRASHCAR_BG_ORIGIN_GPS:?}" \
	  -e CRASHCAR_BG_RUN_NAMESPACE_SHA256="${CRASHCAR_BG_RUN_NAMESPACE_SHA256:?}" \
	  -e CRASHCAR_BG_SOURCE_MANIFEST_SHA256="${CRASHCAR_BG_SOURCE_MANIFEST_SHA256:?}" \
	  -e CRASHCAR_BG_RUNTIME_MANIFEST_SHA256="${CRASHCAR_BG_RUNTIME_MANIFEST_SHA256:?}" \
	  -e CRASHCAR_BG_CONFIG_SHA256="${CRASHCAR_BG_CONFIG_SHA256:?}" \
	  -e CRASHCAR_BG_SEGMENT_XML_SHA256="${CRASHCAR_BG_SEGMENT_XML_SHA256:?}" \
	  -e CRASHCAR_BG_SEGMENT_CANONICAL_SHA256="${CRASHCAR_BG_SEGMENT_CANONICAL_SHA256:?}" \
	  -e CRASHCAR_TEMPLATE_SHAPE_MAP_SHA256="${CRASHCAR_TEMPLATE_SHAPE_MAP_SHA256:?}" \
	  -e CRASHCAR_WORKER_BANK_IDS_EXPECTED="${CRASHCAR_WORKER_BANK_IDS_EXPECTED:?}" \
	  -e CRASHCAR_SINGLE_BACKGROUND_JSON="${CRASHCAR_SINGLE_BACKGROUND_JSON:-}" \
	  -e CRASHCAR_LIVE_BACKGROUND_ROOT="${CRASHCAR_LIVE_BACKGROUND_ROOT:-}" \
	  -e CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON="${CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON:-}" \
	  -e SINGLE_BACKGROUND_MODE="${SINGLE_BACKGROUND_MODE:-rolling}" \
	  -e CRASHCAR_SINGLE_BACKGROUND_MODE="${CRASHCAR_SINGLE_BACKGROUND_MODE:-${SINGLE_BACKGROUND_MODE:-rolling}}" \
	  -e CRASHCAR_CODE_VERSION="${CRASHCAR_CODE_VERSION:-spiir-crashcar}" \
  -e CRASHCAR_PIPELINE_EXIT_STATUS_FILE="${pipeline_exit_status_file}" \
  -e WGUO_O3A_SEGMENT_XML="${WGUO_O3A_SEGMENT_XML:-}" \
  -e SEGMENT_XML="${SEGMENT_XML:-}" \
  -e SINGLE_SEGMENT_XML="${SINGLE_SEGMENT_XML:-}" \
  wguo-single-det-py3 bash "${TOP_RUN_ROOT}/scripts/crashcar_pipeline.sh"

crashcar_finish_pipeline_status \
  "${pipeline_exit_status_file}" \
  "logs/done_${SLURM_JOB_ID:-manual}_${SLURM_ARRAY_TASK_ID:-0}.txt"
