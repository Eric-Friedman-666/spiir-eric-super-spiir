#!/bin/bash
set -eo pipefail

worker_id=${1:?worker id required}
worker_count=${2:?worker count required}
worker_group=${SINGLE_WORKER_GROUP:-${worker_id}}
printf -v worker_group_padded '%03d' "${worker_group}"

expand_worker_path() {
    local path=${1:-}
    [ -z "${path}" ] && return 0
    path=${path//\{worker03d\}/${worker_group_padded}}
    path=${path//\{worker\}/${worker_group}}
    path=${path//%03d/${worker_group_padded}}
    path=${path//%d/${worker_group}}
    printf '%s' "${path}"
}

SCRIPT_DIR=${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}
export SCRIPT_DIR
RUN_DIR=${RUN_DIR:-$(pwd)}
export RUN_DIR
cd "${RUN_DIR}"
source "${SCRIPT_DIR}/run_config.sh"
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
cohfar_assignfar_latency_csv_worker=$(
    expand_worker_path "${COHFAR_ASSIGNFAR_LATENCY_CSV:-}"
)
spiir_run_function=${SPIIR_RUN_FUNCTION:-run_spiir_py3}
case "${spiir_run_function}" in
    run_spiir|run_spiir_py3) ;;
    *)
        printf 'single_llrfar_online: unsupported SPIIR_RUN_FUNCTION=%s; expected run_spiir or run_spiir_py3\n' \
            "${spiir_run_function}" >&2
        exit 2
        ;;
esac

worker_node=$(hostname 2>/dev/null || echo unknown)
printf 'single_llrfar_online: worker %s/%s on %s starting at %s\n' \
    "${worker_id}" "${worker_count}" "${worker_node}" "$(date -u +%FT%TZ)"

if [ "${worker_group}" -gt "${MAX_GROUP}" ]; then
    printf 'single_llrfar_online: worker %s on %s has no bank group because worker_group=%s > MAX_GROUP=%s; exiting at %s\n' \
        "${worker_id}" "${worker_node}" "${worker_group}" "${MAX_GROUP}" "$(date -u +%FT%TZ)"
    exit 0
fi

export SLURM_ARRAY_TASK_ID="${worker_group}"
printf 'single_llrfar_online: worker %s on %s owns bank group %03d using %s/%s at %s\n' \
    "${worker_id}" "${worker_node}" "${SLURM_ARRAY_TASK_ID}" "${SPIIR_BUILD_NAME}" "${spiir_run_function}" "$(date -u +%FT%TZ)"
"${spiir_run_function}" \
    -e SLURM_ARRAY_TASK_ID="${SLURM_ARRAY_TASK_ID}" \
    -e FRAME_CACHE_FILE="${FRAME_CACHE_FILE}" \
    -e DATA_CACHE_FILE="${DATA_CACHE_FILE}" \
    -e DATA_CACHE="${DATA_CACHE}" \
    -e DATA_DIRECTION="${DATA_DIRECTION}" \
    -e FRAME_CACHE="${FRAME_CACHE}" \
    -e DATA_START_TIME="${DATA_START_TIME}" \
    -e MAX_DATA_DURATION_SECONDS="${MAX_DATA_DURATION_SECONDS}" \
    -e DATA_END_TIME="${DATA_END_TIME}" \
    -e H1_STRAIN_CHANNEL_NAME="${H1_STRAIN_CHANNEL_NAME}" \
    -e L1_STRAIN_CHANNEL_NAME="${L1_STRAIN_CHANNEL_NAME}" \
    -e H1_STATE_CHANNEL_NAME="${H1_STATE_CHANNEL_NAME}" \
    -e L1_STATE_CHANNEL_NAME="${L1_STATE_CHANNEL_NAME}" \
    -e BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION_SECONDS}" \
    -e FORMAL_BACKGROUND_ACCUMULATION_SECONDS="${FORMAL_BACKGROUND_ACCUMULATION_SECONDS}" \
    -e ALLOW_SHORT_BACKGROUND_DEBUG="${ALLOW_SHORT_BACKGROUND_DEBUG}" \
    -e BACKGROUND_UPDATE_TRIGGER_SECONDS="${BACKGROUND_UPDATE_TRIGGER_SECONDS}" \
    -e BACKGROUND_STATS_WINDOWS="${BACKGROUND_STATS_WINDOWS}" \
    -e BACKGROUND_COLLECT_WALLTIME="${BACKGROUND_COLLECT_WALLTIME}" \
    -e COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS="${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS}" \
    -e COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS="${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS}" \
    -e COHFAR_ASSIGNFAR_LATENCY_CSV="${cohfar_assignfar_latency_csv_worker:-}" \
    -e FINALSINK_FAPUPDATER_INTERVAL_SECONDS="${FINALSINK_FAPUPDATER_INTERVAL_SECONDS}" \
    -e BANK_DIR="${BANK_DIR}" \
    -e NONINJ_STATS_LOC="${NONINJ_STATS_LOC}" \
    -e DETRSP_MAP="${DETRSP_MAP}" \
    -e ZEROLAG_SNAPSHOT_INTERVAL_SECONDS="${ZEROLAG_SNAPSHOT_INTERVAL_SECONDS}" \
    -e BANKS_PER_GROUP="${BANKS_PER_GROUP}" \
    -e START_BANK="${START_BANK}" \
    -e ONLINE_REPLAY_SYNC="${ONLINE_REPLAY_SYNC}" \
    -e ONLINE_REPLAY_RATE="${ONLINE_REPLAY_RATE}" \
    -e ONLINE_REPLAY_ALLOWED_LAG_SECONDS="${ONLINE_REPLAY_ALLOWED_LAG_SECONDS}" \
    -e ONLINE_REPLAY_START_GPS="${ONLINE_REPLAY_START_GPS}" \
	    -e ONLINE_REPLAY_START_WALL="${ONLINE_REPLAY_START_WALL:-}" \
	    -e PIPELINE_MODE="${PIPELINE_MODE:-single}" \
	    -e SINGLE_INPUT_KIND="${SINGLE_INPUT_KIND:-singlecsv}" \
	    -e SINGLE_TRIGGER_STREAM_ENABLE="${SINGLE_TRIGGER_STREAM_ENABLE:-1}" \
	    -e SINGLE_TRIGGER_STREAM_FILE="${SINGLE_TRIGGER_STREAM_FILE:-}" \
	    -e SINGLE_WORKER_ID="${worker_id}" \
	    -e SINGLE_WORKER_GROUP="${worker_group}" \
	    -e SIDECAR_PRESERVE_TABLE_SINGLE_FAR="${SIDECAR_PRESERVE_TABLE_SINGLE_FAR:-1}" \
	    -e SIDECAR_SINGLE_FAR_LEDGER="${SIDECAR_SINGLE_FAR_LEDGER:-}" \
	    -e SIDECAR_SINGLE_FAR_LOOKUP_INTERVAL_SECONDS="${SIDECAR_SINGLE_FAR_LOOKUP_INTERVAL_SECONDS:-1.0}" \
		    -e CRASHCAR_ENABLE="${CRASHCAR_ENABLE:-0}" \
		    -e CRASHCAR_PRESERVE_TABLE_SINGLE_FAR="${CRASHCAR_PRESERVE_TABLE_SINGLE_FAR:-1}" \
		    -e CRASHCAR_DETAIL_OUTPUT_FNAME="${CRASHCAR_DETAIL_OUTPUT_FNAME:-}" \
	    -e CRASHCAR_LOG10_FAR_THRESHOLD="${CRASHCAR_LOG10_FAR_THRESHOLD:--4.0}" \
	    -e CRASHCAR_MIN_SNR="${CRASHCAR_MIN_SNR:-4.0}" \
	    -e CRASHCAR_FAR_FLOOR_COUNT="${CRASHCAR_FAR_FLOOR_COUNT:-1.0}" \
			    -e CRASHCAR_LIVETIME_STEP="${CRASHCAR_LIVETIME_STEP:-1.0}" \
			    -e CRASHCAR_BACKGROUND_REQUIRED_SECONDS="${CRASHCAR_BACKGROUND_REQUIRED_SECONDS:-}" \
			    -e CRASHCAR_EXPECTED_BUFFERS_PER_TIMESTAMP="${CRASHCAR_EXPECTED_BUFFERS_PER_TIMESTAMP:-}" \
			    -e CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME="${CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME:-}" \
			    -e CRASHCAR_SUPPORT_DEBUG="${CRASHCAR_SUPPORT_DEBUG:-0}" \
			    -e CRASHCAR_SUPPORT_DEBUG_FNAME="${CRASHCAR_SUPPORT_DEBUG_FNAME:-}" \
			    -e CRASHCAR_CLUSTER_DEBUG="${CRASHCAR_CLUSTER_DEBUG:-0}" \
			    -e CRASHCAR_CLUSTER_DEBUG_FNAME="${CRASHCAR_CLUSTER_DEBUG_FNAME:-}" \
			    -e SPIIR_ONLINE_BIN="${SPIIR_ONLINE_BIN:-}" \
	    -e SPIIR_RUNTIME_PYTHONPATH="${SPIIR_RUNTIME_PYTHONPATH:-}" \
	    -e SPIIR_RUNTIME_GST_PLUGIN_PATH="${SPIIR_RUNTIME_GST_PLUGIN_PATH:-}" \
	    -e SPIIR_RUNTIME_LD_LIBRARY_PATH="${SPIIR_RUNTIME_LD_LIBRARY_PATH:-}" \
	    -e SPIIR_BUILD_NAME="${SPIIR_BUILD_NAME}" \
	    -e SPIIR_RUN_FUNCTION="${SPIIR_RUN_FUNCTION}" \
	    "${SPIIR_BUILD_NAME}" bash "${SCRIPT_DIR}/pipeline.sh"
printf 'single_llrfar_online: worker %s on %s finished bank group %03d at %s\n' \
    "${worker_id}" "${worker_node}" "${SLURM_ARRAY_TASK_ID}" "$(date -u +%FT%TZ)"

printf 'single_llrfar_online: worker %s/%s on %s finished at %s\n' \
    "${worker_id}" "${worker_count}" "${worker_node}" "$(date -u +%FT%TZ)"
