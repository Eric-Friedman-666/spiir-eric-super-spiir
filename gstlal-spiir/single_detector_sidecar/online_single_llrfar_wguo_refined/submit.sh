#!/bin/bash
#SBATCH --job-name=single_llrfar_online
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=7-00:00:00
#SBATCH --mem=16g
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --requeue
#SBATCH -o logs/pipe_%j.out # File to which STDOUT will be written
#SBATCH -e logs/pipe_%j.err # File to which STDERR will be written

set -eo pipefail

SCRIPT_DIR=${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}
export SCRIPT_DIR
RUN_DIR=${RUN_DIR:-$(pwd)}
export RUN_DIR
cd "${RUN_DIR}"
source "${SCRIPT_DIR}/run_config.sh"

apply_frame_cache_common_clip() {
    if [ "${AUTO_CLIP_FRAME_CACHE_TO_COMMON_SEGMENT:-1}" != "1" ]; then
        return 0
    fi
    if [ ! -f "${FRAME_CACHE_FILE}" ]; then
        printf 'single_llrfar_online: frame cache does not exist: %s\n' \
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

mkdir -p logs single_branch monitor
source "${SPIIR_HELPER_FUNCTIONS}"

export GST_DEBUG=${GST_DEBUG:-}
export X509_USER_PROXY=${X509_USER_PROXY:-}
export X509_USER_KEY=${X509_USER_KEY:-}
export X509_USER_CERT=${X509_USER_CERT:-}
export KRB5_KTNAME=${KRB5_KTNAME:-}
export PKG_CONFIG_PATH=${PKG_CONFIG_PATH:-}
export GST_PLUGIN_PATH=${GST_PLUGIN_PATH:-}
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}
env_args=${env_args:-}

if [ "${ONLINE_REPLAY_SYNC:-0}" = "1" ] && [ -z "${ONLINE_REPLAY_START_WALL:-}" ]; then
    export ONLINE_REPLAY_START_WALL=$(date +%s)
fi

write_runtime_env() {
    local name=$1
    local value=${!name-}
    printf '%s=%q\n' "${name}" "${value}"
}

{
    for name in \
        SCRIPT_DIR PACKAGE_ROOT RUN_ROOT RUN_DIR DATA_DIR \
        NODES_AMOUNT FRAME_CACHE_FILE DATA_CACHE_FILE DATA_CACHE DATA_DIRECTION FRAME_CACHE \
        DATA_START_TIME MAX_DATA_DURATION_SECONDS DATA_END_TIME \
        AUTO_CLIP_FRAME_CACHE_TO_COMMON_SEGMENT FRAME_CACHE_CLIP_REQUIRED_IFOS FRAME_CACHE_CLIP_MODE \
        FRAME_CACHE_COMMON_CLIP_APPLIED FRAME_CACHE_COMMON_CLIP_ORIGINAL_START FRAME_CACHE_COMMON_CLIP_ORIGINAL_END \
        FRAME_CACHE_COMMON_CLIP_REQUIRED_IFOS \
        H1_STRAIN_CHANNEL_NAME L1_STRAIN_CHANNEL_NAME H1_STATE_CHANNEL_NAME L1_STATE_CHANNEL_NAME \
        BACKGROUND_ACCUMULATION_SECONDS FORMAL_BACKGROUND_ACCUMULATION_SECONDS ALLOW_SHORT_BACKGROUND_DEBUG \
        SINGLE_BACKGROUND_MODE SINGLE_FROZEN_BACKGROUND_JSON SINGLE_FROZEN_BACKGROUND_RUN_DIR \
        SINGLE_FROZEN_BACKGROUND_ID SINGLE_FROZEN_BACKGROUND_SOURCE \
        BACKGROUND_STATS_WINDOWS BACKGROUND_COLLECT_WALLTIME \
        COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS \
        FINALSINK_FAPUPDATER_INTERVAL_SECONDS ZEROLAG_SNAPSHOT_INTERVAL_SECONDS \
        BACKGROUND_UPDATE_TRIGGER_SECONDS FAR_INITIAL_WINDOW_POLICY MAX_GROUP BANKS_PER_GROUP \
        SPIIR_BUILD_NAME SPIIR_RUN_FUNCTION SPIIR_HELPER_FUNCTIONS SPIIR_SOURCE_DIR PIPELINE_MODE SINGLE_INPUT_KIND \
        UPDATE_INTERVAL_SECONDS MONITOR_INTERVAL_SECONDS ONLINE_REPLAY_SYNC ONLINE_REPLAY_RATE \
        ONLINE_REPLAY_ALLOWED_LAG_SECONDS ONLINE_REPLAY_START_GPS ONLINE_REPLAY_START_WALL \
        BANK_DIR BANK_DIR_SOURCE PY3_COMPAT_SOURCE_BANK_DIR PY3_COMPAT_BANK_DIR \
        FALLBACK_BANK_DIR NONINJ_STATS_LOC DETRSP_MAP WGUO_BANK_STATS_DIR \
        NOISE_BETA RANK_OFFSET DEFAULT_SHAPE_DOF PLOT_LLR_MIN TAIL_LOG10_FAR FAR_FIT_BOUNDARY \
        ASSIGNMENT_MAX_NEW_WINDOWS_PER_RUN MERGE_WORKER_FAR_OUTPUTS SINGLE_UPDATE_LOCK_STALE_SECONDS; do
        write_runtime_env "${name}"
    done
} > "logs/run_config_${SLURM_JOB_ID:-manual}.env"

rm -f STOP_SINGLE_UPDATE.flag STOP_REALTIME_MONITOR.flag

worker_count=${NODES_AMOUNT:-1}
if [ "${worker_count}" -lt 1 ]; then
    worker_count=1
fi
required_worker_count=$((MAX_GROUP + 1))
if [ "${worker_count}" -lt "${required_worker_count}" ]; then
    printf 'single_llrfar_online: NODES_AMOUNT=%s is insufficient for MAX_GROUP=%s; one node/worker owns exactly one bank group, so at least %s nodes are required\n' \
        "${worker_count}" "${MAX_GROUP}" "${required_worker_count}" >&2
    exit 2
fi
allocated_nodes=${SLURM_JOB_NUM_NODES:-${SLURM_NNODES:-${worker_count}}}
if [ "${allocated_nodes}" -lt "${worker_count}" ]; then
    printf 'single_llrfar_online: Slurm allocated %s nodes but NODES_AMOUNT=%s; submit with batch_submit.sh or request enough nodes for one worker per bank group\n' \
        "${allocated_nodes}" "${worker_count}" >&2
    exit 2
fi

"${SCRIPT_DIR}/realtime_single_monitor.py" \
    --run-dir "$(pwd)" \
    --interval "${MONITOR_INTERVAL_SECONDS}" \
    --job-id "${SLURM_JOB_ID}" \
    > logs/realtime_monitor_${SLURM_JOB_ID}.out \
    2> logs/realtime_monitor_${SLURM_JOB_ID}.err &
monitor_pid=$!

updater_pids=()
for worker_id in $(seq 0 $((worker_count - 1))); do
    SINGLE_WORKER_ID="${worker_id}" \
    SINGLE_WORKER_GROUP="${worker_id}" \
    SINGLE_WORKER_COUNT="${worker_count}" \
        "${SCRIPT_DIR}/update_single_background_loop.sh" \
        "${UPDATE_INTERVAL_SECONDS}" "${RUN_DIR}" \
        > "logs/single_update_loop_${SLURM_JOB_ID}_worker_${worker_id}.out" \
        2> "logs/single_update_loop_${SLURM_JOB_ID}_worker_${worker_id}.err" &
    updater_pids+=("$!")
done

cleanup() {
    touch STOP_SINGLE_UPDATE.flag
    touch STOP_REALTIME_MONITOR.flag
    for pid in "${updater_pids[@]}"; do
        wait "${pid}" || true
    done
    wait "${monitor_pid}" || true
}
trap cleanup EXIT

worker_pids=()
for worker_id in $(seq 0 $((worker_count - 1))); do
    if [ "${worker_count}" -eq 1 ]; then
        SINGLE_WORKER_GROUP="${worker_id}" "${SCRIPT_DIR}/run_bank_group_worker.sh" "${worker_id}" "${worker_count}" &
    else
        srun --nodes=1 --ntasks=1 --cpus-per-task="${SLURM_CPUS_PER_TASK:-4}" \
            --gres=gpu:1 --exclusive \
            env SINGLE_WORKER_GROUP="${worker_id}" "${SCRIPT_DIR}/run_bank_group_worker.sh" "${worker_id}" "${worker_count}" &
    fi
    worker_pids+=("$!")
done

worker_status=0
for pid in "${worker_pids[@]}"; do
    wait "${pid}" || worker_status=$?
done

exit "${worker_status}"
