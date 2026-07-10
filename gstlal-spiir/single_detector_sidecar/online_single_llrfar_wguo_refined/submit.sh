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

if [ "${CRASHCAR_ENABLE:-0}" = "1" ] && [ -n "${CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME:-}" ] && [ ! -s "${CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME}" ]; then
    mkdir -p "$(dirname "${CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME}")"
    end_bank=$((START_BANK + BANKS_PER_GROUP * (MAX_GROUP + 1) - 1))
    "${SPIIR_RUN_FUNCTION}" "${SPIIR_BUILD_NAME}" python3 \
        "${SCRIPT_DIR}/export_template_shape_map.py" \
        --bank-stats-dir "${WGUO_BANK_STATS_DIR}" \
        --output "${CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME}" \
        --ifos H1,L1 \
        --start-bank "${START_BANK}" \
        --end-bank "${end_bank}"
fi

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
        COHFAR_ASSIGNFAR_LATENCY_CSV \
        FINALSINK_FAPUPDATER_INTERVAL_SECONDS ZEROLAG_SNAPSHOT_INTERVAL_SECONDS \
        BACKGROUND_UPDATE_TRIGGER_SECONDS FAR_INITIAL_WINDOW_POLICY MAX_GROUP BANKS_PER_GROUP \
        SPIIR_BUILD_NAME SPIIR_RUN_FUNCTION SPIIR_HELPER_FUNCTIONS SPIIR_SOURCE_DIR SPIIR_ONLINE_BIN \
        SPIIR_RUNTIME_PYTHONPATH SPIIR_RUNTIME_GST_PLUGIN_PATH SPIIR_RUNTIME_LD_LIBRARY_PATH \
        PIPELINE_MODE SINGLE_INPUT_KIND FINAL_SINGLE_INPUT_KIND FINAL_SINGLE_RESET_LEDGER FINAL_SINGLE_IGNORE_ONLINE_REPLAY_GATE \
        SINGLE_TRIGGER_STREAM_ENABLE SINGLE_TRIGGER_STREAM_FILE \
        SIDECAR_PRESERVE_TABLE_SINGLE_FAR SIDECAR_SINGLE_FAR_LEDGER \
        SIDECAR_SINGLE_FAR_LOOKUP_INTERVAL_SECONDS SIDECAR_PATCH_ZEROLAG_SINGLE_FAR \
        PATCH_ZEROLAG_SINGLE_FAR PATCH_ZEROLAG_SINGLE_FAR_LEDGER PATCH_ZEROLAG_SINGLE_FAR_COLUMN PREFER_FEATURE_SINGLE_FAR \
        SINGLE_OUTPUT_MODE SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE \
        PATCH_ZEROLAG_SINGLE_OUTPUT_MODE PATCH_ZEROLAG_SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE \
        CRASHCAR_ENABLE CRASHCAR_PRESERVE_TABLE_SINGLE_FAR CRASHCAR_DETAIL_OUTPUT_FNAME CRASHCAR_LOG10_FAR_THRESHOLD CRASHCAR_MIN_SNR \
        CRASHCAR_FAR_FLOOR_COUNT CRASHCAR_LIVETIME_STEP CRASHCAR_BACKGROUND_REQUIRED_SECONDS \
        CRASHCAR_MULTI_FAR_FACTOR CRASHCAR_MULTI_BEST_FAR_NEVENT_THRESHOLD CRASHCAR_MULTI_FAR_COMBINE_MODE \
        CRASHCAR_EXPECTED_BUFFERS_PER_TIMESTAMP CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME \
        CRASHCAR_SUPPORT_DEBUG CRASHCAR_SUPPORT_DEBUG_FNAME \
        CRASHCAR_CLUSTER_DEBUG CRASHCAR_CLUSTER_DEBUG_FNAME \
        UPDATE_INTERVAL_SECONDS MONITOR_INTERVAL_SECONDS ONLINE_REPLAY_SYNC ONLINE_REPLAY_RATE \
        ONLINE_REPLAY_ALLOWED_LAG_SECONDS ONLINE_REPLAY_START_GPS ONLINE_REPLAY_START_WALL \
        BANK_DIR BANK_DIR_SOURCE PY3_COMPAT_SOURCE_BANK_DIR PY3_COMPAT_BANK_DIR \
        FALLBACK_BANK_DIR NONINJ_STATS_LOC DETRSP_MAP WGUO_BANK_STATS_DIR \
        DOF NOISE_BETA RANK_OFFSET DEFAULT_SHAPE_DOF PLOT_LLR_MIN TAIL_LOG10_FAR FAR_FIT_BOUNDARY \
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

reset_final_single_ledgers() {
    [ "${FINAL_SINGLE_RESET_LEDGER:-0}" = "1" ] || return 0
    printf "single_llrfar_online: resetting generated final single FAR products at %s\n" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
    rm -f \
        single_branch/single_final_far_all.csv \
        single_branch/single_final_far_latest_candidates.csv
    local worker_dir
    for worker_dir in single_branch/worker_*; do
        [ -d "${worker_dir}" ] || continue
        rm -f \
            "${worker_dir}/single_final_far_all.csv" \
            "${worker_dir}/single_final_far_latest_candidates.csv" \
            "${worker_dir}/single_trigger_features.csv" \
            "${worker_dir}/single_trigger_features_assignment_all_visible.csv" \
            "${worker_dir}/single_far_llr_background.json" \
            "${worker_dir}/single_llr_far_support.csv" \
            "${worker_dir}/single_llr_far_background.png" \
            "${worker_dir}/bootstrap_latest_holdout.csv"
        rm -rf "${worker_dir}/backgrounds"
    done
}

run_final_single_update() {
    local final_input_kind=${FINAL_SINGLE_INPUT_KIND:-${SINGLE_INPUT_KIND:-zerolag}}
    case "${final_input_kind}" in
        crashcarcsv|singlecsv|singletriggers|zerolag|sdpostcoh) ;;
        *) return 0 ;;
    esac
    printf "single_llrfar_online: running final %s single FAR update at %s\n" \
        "${final_input_kind}" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
    local final_status=0
    local worker_id
    reset_final_single_ledgers
    for worker_id in $(seq 0 $((worker_count - 1))); do
        SINGLE_INPUT_KIND="${final_input_kind}" \
        SINGLE_IGNORE_ONLINE_REPLAY_GATE="${FINAL_SINGLE_IGNORE_ONLINE_REPLAY_GATE:-1}" \
        SINGLE_WORKER_ID="${worker_id}" \
        SINGLE_WORKER_GROUP="${worker_id}" \
        SINGLE_WORKER_COUNT="${worker_count}" \
        ASSIGNMENT_MAX_NEW_WINDOWS_PER_RUN="${ASSIGNMENT_MAX_NEW_WINDOWS_PER_RUN:-99}" \
            "${SCRIPT_DIR}/update_single_background_once.sh" "${RUN_DIR}" \
            > "logs/final_single_update_${SLURM_JOB_ID:-manual}_worker_${worker_id}.out" \
            2> "logs/final_single_update_${SLURM_JOB_ID:-manual}_worker_${worker_id}.err" \
            || final_status=$?
    done
    if [ "${MERGE_WORKER_FAR_OUTPUTS:-1}" = "1" ]; then
        python3 "${SCRIPT_DIR}/merge_worker_far_ledgers.py" \
            --run-dir "${RUN_DIR}" \
            --worker-count "${worker_count}" \
            --output single_branch/single_final_far_all.csv \
            --candidate-output single_branch/single_final_far_latest_candidates.csv \
            --summary monitor/latest_single_background_status.json \
            --plot-summary monitor/latest_single_plot_summary.json \
            > "logs/final_single_merge_${SLURM_JOB_ID:-manual}.out" \
            2> "logs/final_single_merge_${SLURM_JOB_ID:-manual}.err" \
            || final_status=$?
    fi
    if [ "${PATCH_ZEROLAG_SINGLE_FAR:-${SIDECAR_PATCH_ZEROLAG_SINGLE_FAR:-1}}" = "1" ]; then
        printf "single_llrfar_online: patching final single FAR into zerolag at %s\n" \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
        patch_args=(
            --run-dir "${RUN_DIR}"
            --ledger "${PATCH_ZEROLAG_SINGLE_FAR_LEDGER:-single_branch/single_final_far_all.csv}"
            --far-column "${PATCH_ZEROLAG_SINGLE_FAR_COLUMN:-direct_far}"
            --summary monitor/patch_zerolag_single_far_summary.json
            --single-output-mode "${PATCH_ZEROLAG_SINGLE_OUTPUT_MODE:-${SINGLE_OUTPUT_MODE:-single-only}}"
            --clear-existing
        )
        if [ -n "${PATCH_ZEROLAG_SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE:-${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE:-}}" ]; then
            patch_args+=(
                --active-ifo-schedule "${PATCH_ZEROLAG_SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE:-${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE:-}}"
            )
        fi
        PYTHONPATH="${SPIIR_RUNTIME_PYTHONPATH:-}${PYTHONPATH:+:${PYTHONPATH}}" \
            "${SPIIR_RUN_FUNCTION}" "${SPIIR_BUILD_NAME}" python3 \
            "${SCRIPT_DIR}/patch_zerolag_single_far_from_ledger.py" \
            "${patch_args[@]}" \
            > "logs/patch_zerolag_single_far_${SLURM_JOB_ID:-manual}.out" \
            2> "logs/patch_zerolag_single_far_${SLURM_JOB_ID:-manual}.err" \
            || final_status=$?
    fi
    return "${final_status}"
}

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

cleanup
trap - EXIT
if [ "${worker_status}" -eq 0 ]; then
    run_final_single_update || worker_status=$?
fi

exit "${worker_status}"
