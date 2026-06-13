#!/bin/bash
# Bare WGuo-style reference run: run postcoh only and write zerolag snapshots.
# This intentionally does not start the Eric single-detector sidecar updater.
#SBATCH --job-name=wguo_ref_cmp
#SBATCH --ntasks=1
#SBATCH --time=7-00:00:00
#SBATCH --mem=18g
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --array=0-1
#SBATCH --requeue
#SBATCH -o logs/wguo_ref_%A_%a.out
#SBATCH -e logs/wguo_ref_%A_%a.err

set -eo pipefail

SCRIPT_DIR=${SCRIPT_DIR:?SCRIPT_DIR required}
RUN_DIR=${RUN_DIR:?RUN_DIR required}
cd "${RUN_DIR}"
mkdir -p logs monitor

source "${SCRIPT_DIR}/run_config.sh"
source "${SPIIR_HELPER_FUNCTIONS}"

write_runtime_env() {
    local name=$1
    local value=${!name-}
    printf '%s=%q\n' "${name}" "${value}"
}

{
    printf 'REFERENCE_ROLE=wguo_bare_postcoh\n'
    printf 'REFERENCE_HOST=%q\n' "$(hostname)"
    printf 'REFERENCE_START_UTC=%q\n' "$(date -u +%FT%TZ)"
    printf 'REFERENCE_SLURM_JOB_ID=%q\n' "${SLURM_JOB_ID:-manual}"
    printf 'REFERENCE_SLURM_ARRAY_TASK_ID=%q\n' "${SLURM_ARRAY_TASK_ID:-manual}"
    for name in \
        SCRIPT_DIR RUN_DIR FRAME_CACHE_FILE DATA_START_TIME DATA_END_TIME \
        MAX_DATA_DURATION_SECONDS H1_STRAIN_CHANNEL_NAME L1_STRAIN_CHANNEL_NAME \
        H1_STATE_CHANNEL_NAME L1_STATE_CHANNEL_NAME BACKGROUND_STATS_WINDOWS \
        BACKGROUND_COLLECT_WALLTIME COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS \
        COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS FINALSINK_FAPUPDATER_INTERVAL_SECONDS \
        ZEROLAG_SNAPSHOT_INTERVAL_SECONDS BANK_DIR NONINJ_STATS_LOC DETRSP_MAP \
        BANKS_PER_GROUP START_BANK SPIIR_BUILD_NAME SPIIR_RUN_FUNCTION; do
        write_runtime_env "${name}"
    done
} > "logs/wguo_reference_config_${SLURM_JOB_ID:-manual}_${SLURM_ARRAY_TASK_ID:-manual}.env"

export GST_DEBUG=${GST_DEBUG:-}
export X509_USER_PROXY=${X509_USER_PROXY:-}
export X509_USER_KEY=${X509_USER_KEY:-}
export X509_USER_CERT=${X509_USER_CERT:-}
export KRB5_KTNAME=${KRB5_KTNAME:-}
export PKG_CONFIG_PATH=${PKG_CONFIG_PATH:-}
export GST_PLUGIN_PATH=${GST_PLUGIN_PATH:-}
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}
env_args=${env_args:-}

run_function=${SPIIR_RUN_FUNCTION:-run_spiir_py3}
case "${run_function}" in
    run_spiir|run_spiir_py3) ;;
    *)
        printf 'REFERENCE_ERROR: unsupported SPIIR_RUN_FUNCTION=%s\n' "${run_function}" >&2
        exit 2
        ;;
esac

"${run_function}" \
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
    -e BACKGROUND_STATS_WINDOWS="${BACKGROUND_STATS_WINDOWS}" \
    -e BACKGROUND_COLLECT_WALLTIME="${BACKGROUND_COLLECT_WALLTIME}" \
    -e COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS="${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS}" \
    -e COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS="${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS}" \
    -e FINALSINK_FAPUPDATER_INTERVAL_SECONDS="${FINALSINK_FAPUPDATER_INTERVAL_SECONDS}" \
    -e BANK_DIR="${BANK_DIR}" \
    -e NONINJ_STATS_LOC="${NONINJ_STATS_LOC}" \
    -e DETRSP_MAP="${DETRSP_MAP}" \
    -e ZEROLAG_SNAPSHOT_INTERVAL_SECONDS="${ZEROLAG_SNAPSHOT_INTERVAL_SECONDS}" \
    -e BANKS_PER_GROUP="${BANKS_PER_GROUP}" \
    -e START_BANK="${START_BANK}" \
    -e PIPELINE_MODE="multi" \
    "${SPIIR_BUILD_NAME}" bash "${SCRIPT_DIR}/pipeline.sh"

printf 'REFERENCE_DONE job=%s task=%s host=%s utc=%s\n' \
    "${SLURM_JOB_ID:-manual}" "${SLURM_ARRAY_TASK_ID:-manual}" \
    "$(hostname)" "$(date -u +%FT%TZ)" \
    >> "logs/wguo_reference_done_${SLURM_JOB_ID:-manual}_${SLURM_ARRAY_TASK_ID:-manual}.log"
