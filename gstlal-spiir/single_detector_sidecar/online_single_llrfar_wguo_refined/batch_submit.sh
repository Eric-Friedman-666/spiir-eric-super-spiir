#!/bin/bash
set -euo pipefail

SCRIPT_DIR=${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}
export SCRIPT_DIR
source "${SCRIPT_DIR}/run_config.sh"

RUN_ID=${RUN_ID:-run_$(date -u +%Y%m%dT%H%M%SZ)}
RUN_DIR=${RUN_DIR:-${RUN_ROOT}/${RUN_ID}}
export RUN_DIR

apply_frame_cache_common_clip() {
    if [ "${AUTO_CLIP_FRAME_CACHE_TO_COMMON_SEGMENT:-1}" != "1" ]; then
        return 0
    fi
    if [ ! -f "${FRAME_CACHE_FILE}" ]; then
        printf 'BATCH_SUBMIT_ERROR: frame cache does not exist: %s\n' \
            "${FRAME_CACHE_FILE}" >&2
        return 2
    fi
    local clip_env
    clip_env=$(python3 "${SCRIPT_DIR}/clip_frame_cache_common_segment.py" \
        --cache "${FRAME_CACHE_FILE}" \
        --start "${DATA_START_TIME}" \
        --end "${DATA_END_TIME}" \
        --required-ifos "${FRAME_CACHE_CLIP_REQUIRED_IFOS:-H,L}" \
        --mode "${FRAME_CACHE_CLIP_MODE:-preserve-duration}" \
        --online-replay-start-gps "${ONLINE_REPLAY_START_GPS:-}" \
        --shell)
    eval "${clip_env}"
}

ensure_py3_compatible_bank_dir() {
    if [ "${SPIIR_RUN_FUNCTION:-run_spiir_py3}" != "run_spiir_py3" ]; then
        return 0
    fi
    if [ "${BANK_DIR_SOURCE:-}" = "explicit" ]; then
        return 0
    fi
    case "${BANK_DIR_SOURCE:-}" in
        generated-wguo-py3-compat*) return 0 ;;
    esac

    local source_bank_dir=${PY3_COMPAT_SOURCE_BANK_DIR:-${BANK_DIR}}
    local target_bank_dir=${PY3_COMPAT_BANK_DIR:-${RUN_DIR}/compat_banks}
    local end_bank=$((START_BANK + BANKS_PER_GROUP * (MAX_GROUP + 1) - 1))

    export PY3_COMPAT_SOURCE_BANK_DIR="${source_bank_dir}"
    export PY3_COMPAT_BANK_DIR="${target_bank_dir}"
    export BANK_DIR="${target_bank_dir}"
    export BANK_DIR_SOURCE="generated-wguo-py3-compat-from-${BANK_DIR_SOURCE:-unknown}"

    if [ "${DRY_RUN:-0}" = "1" ]; then
        return 0
    fi

    mkdir -p "${target_bank_dir}"
    python3 "${SCRIPT_DIR}/convert_pycbc_bank_for_wguo_compat.py" \
        --input-dir "${source_bank_dir}" \
        --output-dir "${target_bank_dir}" \
        --start-bank "${START_BANK}" \
        --end-bank "${end_bank}" \
        --ifos H1,L1
}

apply_frame_cache_common_clip
ensure_py3_compatible_bank_dir

required_nodes=$((MAX_GROUP + 1))
if [ "${NODES_AMOUNT}" -lt "${required_nodes}" ]; then
    printf 'BATCH_SUBMIT_ERROR: NODES_AMOUNT=%s is insufficient for MAX_GROUP=%s; one node/worker owns exactly one bank group, so at least %s nodes are required\n' \
        "${NODES_AMOUNT}" "${MAX_GROUP}" "${required_nodes}" >&2
    exit 2
fi
if [ "${DRY_RUN:-0}" != "1" ]; then
    mkdir -p "${RUN_DIR}/logs"
fi

cat <<EOF
BATCH_SUBMIT_CONFIG
  RUN_DIR=${RUN_DIR}
  NODES_AMOUNT=${NODES_AMOUNT}
  FRAME_CACHE_FILE=${FRAME_CACHE_FILE}
  DATA_START_TIME=${DATA_START_TIME}
  MAX_DATA_DURATION_SECONDS=${MAX_DATA_DURATION_SECONDS}
  DATA_END_TIME=${DATA_END_TIME}
  FRAME_CACHE_COMMON_CLIP_APPLIED=${FRAME_CACHE_COMMON_CLIP_APPLIED:-0}
  FRAME_CACHE_COMMON_CLIP_ORIGINAL_START=${FRAME_CACHE_COMMON_CLIP_ORIGINAL_START:-}
  FRAME_CACHE_COMMON_CLIP_ORIGINAL_END=${FRAME_CACHE_COMMON_CLIP_ORIGINAL_END:-}
  BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS}
  FORMAL_BACKGROUND_ACCUMULATION_SECONDS=${FORMAL_BACKGROUND_ACCUMULATION_SECONDS}
  ALLOW_SHORT_BACKGROUND_DEBUG=${ALLOW_SHORT_BACKGROUND_DEBUG}
  SINGLE_BACKGROUND_MODE=${SINGLE_BACKGROUND_MODE}
  SINGLE_FROZEN_BACKGROUND_JSON=${SINGLE_FROZEN_BACKGROUND_JSON}
  SINGLE_FROZEN_BACKGROUND_RUN_DIR=${SINGLE_FROZEN_BACKGROUND_RUN_DIR}
  SINGLE_FROZEN_BACKGROUND_ID=${SINGLE_FROZEN_BACKGROUND_ID}
  SINGLE_FROZEN_BACKGROUND_SOURCE=${SINGLE_FROZEN_BACKGROUND_SOURCE}
  BACKGROUND_STATS_WINDOWS=${BACKGROUND_STATS_WINDOWS}
  BACKGROUND_COLLECT_WALLTIME=${BACKGROUND_COLLECT_WALLTIME}
  ZEROLAG_SNAPSHOT_INTERVAL_SECONDS=${ZEROLAG_SNAPSHOT_INTERVAL_SECONDS}
  BACKGROUND_UPDATE_TRIGGER_SECONDS=${BACKGROUND_UPDATE_TRIGGER_SECONDS}
  FAR_INITIAL_WINDOW_POLICY=${FAR_INITIAL_WINDOW_POLICY}
  MAX_GROUP=${MAX_GROUP}
  BANKS_PER_GROUP=${BANKS_PER_GROUP}
  BANK_DIR=${BANK_DIR}
  BANK_DIR_SOURCE=${BANK_DIR_SOURCE}
  SPIIR_BUILD_NAME=${SPIIR_BUILD_NAME}
  SPIIR_RUN_FUNCTION=${SPIIR_RUN_FUNCTION}
  SPIIR_ONLINE_BIN=${SPIIR_ONLINE_BIN}
  SPIIR_RUNTIME_PYTHONPATH=${SPIIR_RUNTIME_PYTHONPATH}
  PY3_COMPAT_SOURCE_BANK_DIR=${PY3_COMPAT_SOURCE_BANK_DIR:-}
  PY3_COMPAT_BANK_DIR=${PY3_COMPAT_BANK_DIR:-}
  PIPELINE_MODE=${PIPELINE_MODE:-single}
  SINGLE_INPUT_KIND=${SINGLE_INPUT_KIND:-singlecsv}
  SINGLE_TRIGGER_STREAM_ENABLE=${SINGLE_TRIGGER_STREAM_ENABLE:-1}
  SINGLE_TRIGGER_STREAM_FILE=${SINGLE_TRIGGER_STREAM_FILE:-}
  START_BANK=${START_BANK}
  ONLINE_REPLAY_SYNC=${ONLINE_REPLAY_SYNC}
  ONLINE_REPLAY_RATE=${ONLINE_REPLAY_RATE}
  ONLINE_REPLAY_ALLOWED_LAG_SECONDS=${ONLINE_REPLAY_ALLOWED_LAG_SECONDS}
  ONLINE_REPLAY_START_GPS=${ONLINE_REPLAY_START_GPS}
EOF

if [ "${DRY_RUN:-0}" = "1" ]; then
    printf 'DRY_RUN sbatch --chdir=%q --nodes=%s --ntasks=%s --ntasks-per-node=1 --export=ALL,SCRIPT_DIR=%q,PACKAGE_ROOT=%q,RUN_ROOT=%q,RUN_DIR=%q' \
        "${RUN_DIR}" "${NODES_AMOUNT}" "${NODES_AMOUNT}" "${SCRIPT_DIR}" "${PACKAGE_ROOT}" "${RUN_ROOT}" "${RUN_DIR}"
    for arg in "$@"; do
        printf ' %q' "${arg}"
    done
    printf ' %q\n' "${SCRIPT_DIR}/submit.sh"
    exit 0
fi

exec sbatch \
    --chdir="${RUN_DIR}" \
    --nodes="${NODES_AMOUNT}" \
    --ntasks="${NODES_AMOUNT}" \
    --ntasks-per-node=1 \
    --export=ALL,SCRIPT_DIR="${SCRIPT_DIR}",PACKAGE_ROOT="${PACKAGE_ROOT}",RUN_ROOT="${RUN_ROOT}",RUN_DIR="${RUN_DIR}" \
    "$@" "${SCRIPT_DIR}/submit.sh"
