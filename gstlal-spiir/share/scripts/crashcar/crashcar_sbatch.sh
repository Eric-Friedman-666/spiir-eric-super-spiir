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
            if [ ! -f "${CRASHCAR_LIVE_SINGLE_READINESS_JSON:-}" ] ||
               [ -L "${CRASHCAR_LIVE_SINGLE_READINESS_JSON:-}" ]; then
                crashcar_binding_error "live single readiness snapshot is unavailable"; return 2
            fi
            mapfile -d '' live_values < <(
                python3 - "${CRASHCAR_LIVE_SINGLE_READINESS_JSON}" "${canonical_root}" \
                    "${worker_id}" "${worker_count}" "${banks_per_worker}" \
                    "${start_bank}" "${expected_roster}" <<'PY_LIVE'
import json, os, re, stat, sys
path, root, worker_text, count_text, bpw_text, start_text, roster_text=sys.argv[1:]
def fail(msg): raise SystemExit("crashcar_sbatch live binding: "+msg)
def strict(pairs):
    out={}
    for k,v in pairs:
        if k in out: fail("duplicate key "+k)
        out[k]=v
    return out
info=os.lstat(path)
if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode): fail("readiness snapshot is not regular")
with open(path,encoding="utf-8") as h: value=json.load(h,object_pairs_hook=strict)
worker=int(worker_text); count=int(count_text); bpw=int(bpw_text); start=int(start_text)
if value.get("kind")!="crashcar_live_single_validation" or value.get("producer_root")!=root: fail("producer mismatch")
if (value.get("worker_count"),value.get("banks_per_worker"),value.get("start_bank"))!=(count,bpw,start): fail("geometry mismatch")
workers=value.get("workers")
if type(workers) is not list or len(workers)!=count: fail("worker list mismatch")
item=workers[worker]
if item.get("worker_id")!=worker or item.get("worker_count")!=count: fail("worker mismatch")
if item.get("worker_bank_ids")!=[int(x) for x in roster_text.split(",")]: fail("bank roster mismatch")
expected=os.path.join(root,"run",f"{worker:03d}","single_background.json")
if item.get("single_background_path")!=expected: fail("single path mismatch")
ids=item.get("identities"); keys=("run_namespace_sha256","source_manifest_sha256","runtime_manifest_sha256","config_sha256","segment_xml_sha256","segment_canonical_sha256","template_shape_map_sha256")
hex64=re.compile(r"^[0-9a-f]{64}$")
if type(ids) is not dict or any(not hex64.fullmatch(ids.get(k,"")) for k in keys): fail("provenance mismatch")
items=[expected]+[ids[k] for k in keys]+["OK"]
sys.stdout.buffer.write(b"\0".join(x.encode() for x in items)+b"\0")
PY_LIVE
            )
            if [ "${#live_values[@]}" -ne 9 ] || [ "${live_values[8]}" != OK ]; then
                crashcar_binding_error "strict live single readiness validation failed"; return 2
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
            ;;
        *) crashcar_binding_error "single background mode must be rolling or live_readonly"; return 2 ;;
    esac
    export CRASHCAR_WORKER_BANK_IDS_EXPECTED="${expected_roster}"
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
