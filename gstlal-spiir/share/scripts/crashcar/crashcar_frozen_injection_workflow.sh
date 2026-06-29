#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT_DIR="${ROOT}/scripts"
CONTROLLER_DIR="${ROOT}/controller"
LOG="${CONTROLLER_DIR}/workflow.log"
STATUS="${CONTROLLER_DIR}/workflow_status.json"
CONFIG_FILE=${CRASHCAR_CONFIG_FILE:-"${SCRIPT_DIR}/crashcar.env"}

mkdir -p "${CONTROLLER_DIR}" "${ROOT}/inputs" "${ROOT}/frozen_multi_stats"

log() {
    printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "${LOG}"
}

write_status() {
    STATUS="${STATUS}" ROOT="${ROOT}" python3 - "$@" <<'PY'
import json
import os
import time
import sys

payload = {}
status = os.environ["STATUS"]
if os.path.exists(status):
    try:
        payload = json.load(open(status, "r", encoding="utf-8"))
    except Exception:
        payload = {}
for item in sys.argv[1:]:
    key, value = item.split("=", 1)
    payload[key] = value
payload["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
payload["root"] = os.environ["ROOT"]
with open(status, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

require_file() {
    local path=$1 label=$2
    if [ ! -f "${path}" ]; then
        log "ERROR missing ${label}: ${path}"
        write_status phase=failed reason="missing_${label}" missing_path="${path}"
        exit 2
    fi
}

require_var() {
    local name=$1 value=${!1-}
    if [ -z "${value}" ]; then
        log "ERROR ${name} is required for injection_mode=True"
        write_status phase=failed reason="missing_${name}"
        exit 2
    fi
}

bool_true() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        true|1|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

duration_seconds_from() {
    local seconds_name=$1
    local hours_name=$2
    local label=$3
    local seconds=${!seconds_name-}
    local hours=${!hours_name-}
    if [ -n "${seconds}" ]; then
        printf '%s\n' "${seconds}"
        return 0
    fi
    if [ -n "${hours}" ]; then
        printf '%s\n' "$((hours * 3600))"
        return 0
    fi
    log "ERROR ${label} requires ${seconds_name} or ${hours_name}"
    write_status phase=failed reason="missing_${label}"
    exit 2
}

write_env_file() {
    local output=$1
    shift
    : > "${output}"
    while [ "$#" -gt 0 ]; do
        printf '%s\n' "$1" >> "${output}"
        shift
    done
}

json_get_phase() {
    python3 - "$1" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("phase", ""))
except Exception:
    print("")
PY
}

run_stage() {
    local config=$1
    local label=$2
    log "starting ${label} with config ${config}"
    ROOT="${SOURCE_ROOT_VALUE}" bash "${SCRIPT_DIR}/crashcar.sh" "${config}"
    local stage_root
    stage_root=$(awk -F= '$1=="run_root"{print $2}' "${config}" | tail -n 1)
    local phase
    phase=$(json_get_phase "${stage_root}/controller/status.json")
    if [ "${phase}" != "completed" ]; then
        log "ERROR ${label} ended with phase=${phase}; status=${stage_root}/controller/status.json"
        write_status phase=failed failed_stage="${label}" failed_stage_phase="${phase}" failed_stage_root="${stage_root}"
        exit 3
    fi
    log "${label} completed at ${stage_root}"
}

materialize_frozen_multi_stats() {
    local bg_run_dir=$1
    local worker_count=$2
    local bg_worker_count=$3
    local output_dir=$4
    local fallback_dir=${5:-}
    local worker jobno src_worker src_jobno suffix src fallback_src dst

    mkdir -p "${output_dir}"
    for worker in $(seq 0 $((worker_count - 1))); do
        jobno=$(printf '%03d' "${worker}")
        src_worker=$((worker % bg_worker_count))
        src_jobno=$(printf '%03d' "${src_worker}")
        mkdir -p "${output_dir}/${jobno}"
        for suffix in 2w 1d 2h; do
            src="${bg_run_dir}/${src_jobno}/${src_jobno}_marginalized_stats_${suffix}.xml.gz"
            dst="${output_dir}/${jobno}/${jobno}_marginalized_stats_${suffix}.xml.gz"
            if [ ! -f "${src}" ] && [ -n "${fallback_dir}" ]; then
                fallback_src="${fallback_dir}/${src_jobno}/${src_jobno}_marginalized_stats_${suffix}.xml.gz"
                if [ -f "${fallback_src}" ]; then
                    src="${fallback_src}"
                fi
            fi
            require_file "${src}" "frozen_multi_stats_${src_jobno}_${suffix}"
            ln -sfn "${src}" "${dst}"
        done
    done
}

filter_injection_chunk() {
    local input_xml=$1 output_xml=$2 start_gps=$3 end_gps=$4 summary=$5
    python3 "${SCRIPT_DIR}/filter_injection_xml_by_gps.py" \
        --input "${input_xml}" \
        --output "${output_xml}" \
        --gps-start "${start_gps}" \
        --gps-end "${end_gps}" \
        --summary "${summary}"
}

if [ ! -f "${CONFIG_FILE}" ]; then
    printf 'crashcar_frozen_injection_workflow: missing config %s\n' "${CONFIG_FILE}" >&2
    exit 2
fi

set -a
# shellcheck source=/dev/null
source "${CONFIG_FILE}"
set +a

SOURCE_ROOT_VALUE=${root:-${ROOT:-${source_root:-${SOURCE_ROOT:-}}}}
if [ -z "${SOURCE_ROOT_VALUE}" ]; then
    printf 'crashcar_frozen_injection_workflow: root required\n' >&2
    exit 2
fi

if ! bool_true "${injection_mode:-${INJECTION_MODE:-False}}"; then
    printf 'crashcar_frozen_injection_workflow: injection_mode=True required\n' >&2
    exit 2
fi

COMMON_DATA_FILE=${data_file:-${frame_cache:-${FRAME_CACHE:-}}}
COMMON_DETECTOR_RESPONSE_FILE=${detector_response_file:-${detrsp_map:-${DETRSP_MAP:-}}}
COMMON_SEGMENT_XML=${segment_xml:-${SEGMENT_XML:-}}

injection_data_file=${injection_data_file:-${COMMON_DATA_FILE}}
injection_detector_response_file=${injection_detector_response_file:-${COMMON_DETECTOR_RESPONSE_FILE}}
injection_segment_xml=${injection_segment_xml:-${COMMON_SEGMENT_XML}}
injection_start_gps=${injection_start_gps:-${start_gps:-${START_GPS:-}}}
injection_duration_seconds=${injection_duration_seconds:-${duration_seconds:-${DURATION_SECONDS:-}}}
injection_duration_hour=${injection_duration_hour:-${duration_hour:-${DURATION_HOUR:-}}}

injection_bg_data_file=${injection_bg_data_file:-${COMMON_DATA_FILE}}
injection_bg_detector_response_file=${injection_bg_detector_response_file:-${COMMON_DETECTOR_RESPONSE_FILE}}
injection_bg_segment_xml=${injection_bg_segment_xml:-${COMMON_SEGMENT_XML}}
injection_bg_start_gps=${injection_bg_start_gps:-${start_gps:-${START_GPS:-}}}
injection_bg_duration_seconds=${injection_bg_duration_seconds:-${duration_seconds:-${DURATION_SECONDS:-}}}
injection_bg_duration_hour=${injection_bg_duration_hour:-${duration_hour:-${DURATION_HOUR:-}}}

require_var injection_file
require_var injection_data_file
require_var injection_detector_response_file
require_var injection_start_gps
require_var injection_segment_xml
require_var injection_bg_data_file
require_var injection_bg_detector_response_file
require_var injection_bg_start_gps
require_var injection_bg_segment_xml

require_file "${injection_file}" "injection_file"
require_file "${injection_data_file}" "injection_data_file"
require_file "${injection_detector_response_file}" "injection_detector_response_file"
require_file "${injection_segment_xml}" "injection_segment_xml"
require_file "${injection_bg_data_file}" "injection_bg_data_file"
require_file "${injection_bg_detector_response_file}" "injection_bg_detector_response_file"
require_file "${injection_bg_segment_xml}" "injection_bg_segment_xml"

O3_BANK_DIR=${bank_file:-${o3_bank_dir:-${O3_BANK_DIR:-}}}
require_var O3_BANK_DIR
BG_WORKERS=${injection_bg_worker_number:-${INJECTION_BG_WORKER_NUMBER:-${worker_number:-1}}}
BG_BANKS_PER_WORKER=${injection_bg_bank_per_worker:-${INJECTION_BG_BANK_PER_WORKER:-${bank_per_worker:-8}}}
INJ_WORKERS=${injection_worker_number:-${INJECTION_WORKER_NUMBER:-${worker_number:-2}}}
INJ_BANKS_PER_WORKER=${injection_bank_per_worker:-${INJECTION_BANK_PER_WORKER:-${bank_per_worker:-8}}}
BG_DURATION_SECONDS=$(duration_seconds_from injection_bg_duration_seconds injection_bg_duration_hour injection_bg_duration)
INJ_TOTAL_SECONDS=$(duration_seconds_from injection_duration_seconds injection_duration_hour injection_duration)
if [ -n "${injection_chunk_seconds:-${INJECTION_CHUNK_SECONDS:-}}" ]; then
    INJ_CHUNK_SECONDS=${injection_chunk_seconds:-${INJECTION_CHUNK_SECONDS:-}}
else
    INJ_CHUNK_HOUR=${injection_chunk_hour:-${INJECTION_CHUNK_HOUR:-1}}
    if [ "${INJ_CHUNK_HOUR}" -le 0 ]; then
        INJ_CHUNK_SECONDS=${INJ_TOTAL_SECONDS}
    else
        INJ_CHUNK_SECONDS=$((INJ_CHUNK_HOUR * 3600))
    fi
fi
BG_ACCUM_SECONDS=${injection_bg_BG_accumulation_seconds:-${injection_bg_accumulation_seconds:-${background_accumulation_seconds:-${BACKGROUND_ACCUMULATION_SECONDS:-}}}}
if [ -z "${BG_ACCUM_SECONDS}" ]; then
    BG_ACCUM_HOUR=${injection_bg_BG_accumulation_hour:-${injection_bg_accumulation_hour:-${BG_accumulation_hour:-${background_accumulation_hour:-${BACKGROUND_ACCUMULATION_HOUR:-}}}}}
    if [ -n "${BG_ACCUM_HOUR}" ]; then
        BG_ACCUM_SECONDS=$((BG_ACCUM_HOUR * 3600))
    else
        BG_ACCUM_SECONDS=${BG_DURATION_SECONDS}
    fi
fi
BG_UPDATE_SECONDS=${injection_bg_BG_update_seconds:-${injection_bg_zerolag_update_seconds:-${BG_update_seconds:-${background_update_seconds:-${BACKGROUND_UPDATE_SECONDS:-}}}}}
if [ -z "${BG_UPDATE_SECONDS}" ]; then
    BG_UPDATE_HOUR=${injection_bg_BG_update_hour:-${injection_bg_zerolag_update_hour:-${BG_update_hour:-1}}}
    BG_UPDATE_SECONDS=$((BG_UPDATE_HOUR * 3600))
fi
INJ_ACCUM_SECONDS=${injection_BG_accumulation_seconds:-${injection_accumulation_seconds:-}}
if [ -z "${INJ_ACCUM_SECONDS}" ]; then
    INJ_ACCUM_HOUR=${injection_BG_accumulation_hour:-${injection_accumulation_hour:-}}
    if [ -n "${INJ_ACCUM_HOUR}" ]; then
        INJ_ACCUM_SECONDS=$((INJ_ACCUM_HOUR * 3600))
    else
        INJ_ACCUM_SECONDS=${BG_ACCUM_SECONDS}
    fi
fi
INJ_BG_UPDATE_SECONDS=${injection_BG_update_seconds:-${injection_zerolag_update_seconds:-}}
if [ -z "${INJ_BG_UPDATE_SECONDS}" ]; then
    INJ_BG_UPDATE_HOUR=${injection_BG_update_hour:-${injection_zerolag_update_hour:-}}
    if [ -n "${INJ_BG_UPDATE_HOUR}" ]; then
        INJ_BG_UPDATE_SECONDS=$((INJ_BG_UPDATE_HOUR * 3600))
    else
        INJ_BG_UPDATE_SECONDS=${BG_UPDATE_SECONDS}
    fi
fi
ZEROLAG_UPDATE_SECONDS=${zerolag_update_seconds:-${ZEROLAG_UPDATE_SECONDS:-}}
if [ -z "${ZEROLAG_UPDATE_SECONDS}" ]; then
    ZEROLAG_UPDATE_HOUR=${zerolag_update_hour:-1}
    ZEROLAG_UPDATE_SECONDS=$((ZEROLAG_UPDATE_HOUR * 3600))
fi
TAIL_LOG_FAR=${tail_log_FAR:-${TAIL_LOG_FAR:--2.5}}
SNR_LOG_FAR=${SNR_series_logFAR_threshold:-${snr_series_logFAR_threshold:-${SNR_SERIES_LOG_FAR_THRESHOLD:--4}}}
INJ_SNR_LOG_FAR=${injection_SNR_series_logFAR_threshold:-${injection_snr_series_logFAR_threshold:-${INJECTION_SNR_SERIES_LOGFAR_THRESHOLD:-90}}}
RUN_ID=${run_id:-${RUN_ID:-crashcar_frozen_injection}}
SLURM_PARTITION_VALUE=${slurm_partition:-${SLURM_PARTITION:-}}
SLURM_TIME_VALUE=${slurm_time:-${SLURM_TIME:-}}
SLURM_MEM_VALUE=${slurm_mem:-${SLURM_MEM:-}}
SLURM_GRES_VALUE=${slurm_gres:-${SLURM_GRES:-}}
SLURM_CPUS_PER_TASK_VALUE=${slurm_cpus_per_task:-${SLURM_CPUS_PER_TASK:-}}

BG_RUN_ROOT="${ROOT}/bg_noinj"
INJ_ROOT="${ROOT}/inj_bns"
FROZEN_MULTI_DIR="${ROOT}/frozen_multi_stats"
BG_CONFIG="${CONTROLLER_DIR}/bg_noinj.env"
FROZEN_MULTI_FALLBACK_DIR=${injection_bg_seed_noninj_stats_loc:-${noninj_stats_loc:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}}

write_status \
    phase=starting \
    workflow=frozen_background_then_injection \
    source_root="${SOURCE_ROOT_VALUE}" \
    bg_run_root="${BG_RUN_ROOT}" \
    injection_root="${INJ_ROOT}" \
    injection_chunks="pending" \
    bg_workers="${BG_WORKERS}" \
    injection_workers="${INJ_WORKERS}" \
    injection_data_file="${injection_data_file}" \
    injection_bg_data_file="${injection_bg_data_file}"

write_env_file "${BG_CONFIG}" \
    "root=${SOURCE_ROOT_VALUE}" \
    "run_root=${BG_RUN_ROOT}" \
    "run_id=${RUN_ID}_bg_noinj" \
    "crashcar_internal_stage=1" \
    "crashcar_allow_existing_run_root=1" \
    "slurm_partition=${SLURM_PARTITION_VALUE}" \
    "slurm_time=${SLURM_TIME_VALUE}" \
    "slurm_mem=${SLURM_MEM_VALUE}" \
    "slurm_gres=${SLURM_GRES_VALUE}" \
    "slurm_cpus_per_task=${SLURM_CPUS_PER_TASK_VALUE}" \
    "data_file=${injection_bg_data_file}" \
    "detector_response_file=${injection_bg_detector_response_file}" \
    "start_gps=${injection_bg_start_gps}" \
    "duration=${BG_DURATION_SECONDS}" \
    "segment_xml=${injection_bg_segment_xml}" \
    "worker_number=${BG_WORKERS}" \
    "bank_per_worker=${BG_BANKS_PER_WORKER}" \
    "bank_file=${O3_BANK_DIR}" \
    "background_accumulation=${BG_ACCUM_SECONDS}" \
    "background_update=${BG_UPDATE_SECONDS}" \
    "zerolag_update_seconds=${ZEROLAG_UPDATE_SECONDS}" \
    "tail_log_FAR=${TAIL_LOG_FAR}" \
    "SNR_series_logFAR_threshold=${SNR_LOG_FAR}" \
    "injection_mode=False" \
    "noninj_stats_loc=${injection_bg_seed_noninj_stats_loc:-${noninj_stats_loc:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}}" \
    "single_background_mode=rolling" \
    "crashcar_single_ledger_final_update=1" \
    "crashcar_build_last_bg_artifacts=1" \
    "single_input_kind=crashcarcsv" \
    "final_single_input_kind=crashcarcsv" \
    "patch_zerolag_single_far=1" \
    "patch_zerolag_single_snr_series=1"

run_stage "${BG_CONFIG}" "no-injection background"

SINGLE_BG_JSON="${BG_RUN_ROOT}/run/single_branch/worker_0/single_far_llr_background.json"
if [ ! -f "${SINGLE_BG_JSON}" ]; then
    SINGLE_BG_JSON="${BG_RUN_ROOT}/artifacts/crashcar_day1_last_bg3h_full_background.json"
fi
require_file "${SINGLE_BG_JSON}" "frozen_single_background_json"
materialize_frozen_multi_stats "${BG_RUN_ROOT}/run" "${INJ_WORKERS}" "${BG_WORKERS}" "${FROZEN_MULTI_DIR}" "${FROZEN_MULTI_FALLBACK_DIR}"

write_status \
    phase=background_frozen \
    bg_run_root="${BG_RUN_ROOT}" \
    frozen_single_background_json="${SINGLE_BG_JSON}" \
    frozen_multi_stats_dir="${FROZEN_MULTI_DIR}" \
    injection_workers="${INJ_WORKERS}"

INJ_START=${injection_start_gps}
INJ_END=$((INJ_START + INJ_TOTAL_SECONDS))
mkdir -p "${INJ_ROOT}/chunks"

chunk_index=0
chunk_start=${INJ_START}
while [ "${chunk_start}" -lt "${INJ_END}" ]; do
    chunk_end=$((chunk_start + INJ_CHUNK_SECONDS))
    if [ "${chunk_end}" -gt "${INJ_END}" ]; then
        chunk_end=${INJ_END}
    fi
    chunk_duration=$((chunk_end - chunk_start))
    chunk_tag=$(printf 'chunk_%03d' "${chunk_index}")
    chunk_root="${INJ_ROOT}/chunks/${chunk_tag}"
    chunk_config="${CONTROLLER_DIR}/${chunk_tag}.env"
    chunk_xml="${ROOT}/inputs/${chunk_tag}_injections.xml.gz"
    chunk_summary="${ROOT}/inputs/${chunk_tag}_injections.summary.json"

    filter_injection_chunk "${injection_file}" "${chunk_xml}" "${chunk_start}" "${chunk_end}" "${chunk_summary}"

    write_env_file "${chunk_config}" \
        "root=${SOURCE_ROOT_VALUE}" \
        "run_root=${chunk_root}" \
        "run_id=${RUN_ID}_${chunk_tag}" \
        "crashcar_internal_stage=1" \
        "crashcar_allow_existing_run_root=1" \
        "slurm_partition=${SLURM_PARTITION_VALUE}" \
        "slurm_time=${SLURM_TIME_VALUE}" \
        "slurm_mem=${SLURM_MEM_VALUE}" \
        "slurm_gres=${SLURM_GRES_VALUE}" \
        "slurm_cpus_per_task=${SLURM_CPUS_PER_TASK_VALUE}" \
        "data_file=${injection_data_file}" \
        "detector_response_file=${injection_detector_response_file}" \
        "start_gps=${chunk_start}" \
        "duration=${chunk_duration}" \
        "segment_xml=${injection_segment_xml}" \
        "worker_number=${INJ_WORKERS}" \
        "bank_per_worker=${INJ_BANKS_PER_WORKER}" \
        "bank_file=${O3_BANK_DIR}" \
        "background_accumulation=${INJ_ACCUM_SECONDS}" \
        "background_update=${INJ_BG_UPDATE_SECONDS}" \
        "zerolag_update_seconds=${ZEROLAG_UPDATE_SECONDS}" \
        "tail_log_FAR=${TAIL_LOG_FAR}" \
        "SNR_series_logFAR_threshold=${INJ_SNR_LOG_FAR}" \
        "injection_mode=True" \
        "injection_file=${chunk_xml}" \
        "injection_bg_data_file=${injection_bg_data_file}" \
        "injection_bg_detector_response_file=${injection_bg_detector_response_file}" \
        "injection_bg_start_gps=${injection_bg_start_gps}" \
        "injection_bg_duration_seconds=${BG_DURATION_SECONDS}" \
        "injection_bg_segment_xml=${injection_bg_segment_xml}" \
        "noninj_stats_loc=${FROZEN_MULTI_DIR}" \
        "single_background_mode=frozen" \
        "single_frozen_background_json=${SINGLE_BG_JSON}" \
        "single_frozen_background_source=${BG_RUN_ROOT}" \
        "single_frozen_background_id=BG-NOINJ-FROZEN" \
        "crashcar_single_ledger_final_update=1" \
        "crashcar_build_last_bg_artifacts=0" \
        "single_input_kind=crashcarcsv" \
        "final_single_input_kind=crashcarcsv" \
        "patch_zerolag_single_far=1" \
        "patch_zerolag_single_snr_series=1" \
        "crashcar_preserve_table_single_far=1" \
        "crashcar_background_required_seconds=0"

    write_status phase=injection_chunk_running current_chunk="${chunk_tag}" current_chunk_start="${chunk_start}" current_chunk_end="${chunk_end}" current_chunk_root="${chunk_root}" frozen_single_background_json="${SINGLE_BG_JSON}" frozen_multi_stats_dir="${FROZEN_MULTI_DIR}"
    run_stage "${chunk_config}" "injection ${chunk_tag}"
    write_status phase=injection_chunk_completed completed_chunk="${chunk_tag}" completed_chunk_root="${chunk_root}"

    chunk_index=$((chunk_index + 1))
    chunk_start=${chunk_end}
done

write_status phase=completed bg_run_root="${BG_RUN_ROOT}" injection_root="${INJ_ROOT}" injection_chunks="${chunk_index}" frozen_single_background_json="${SINGLE_BG_JSON}" frozen_multi_stats_dir="${FROZEN_MULTI_DIR}"
log "frozen injection workflow completed: bg=${BG_RUN_ROOT} chunks=${chunk_index}"
