#!/bin/bash
#SBATCH --job-name=o3a_bns_py3
#SBATCH --ntasks=1
#SBATCH --time=7-00:00:00
#SBATCH --mem=18g
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --array=0-1
#SBATCH --requeue
#SBATCH -o logs/o3a_bns_py3_%A_%a.out
#SBATCH -e logs/o3a_bns_py3_%A_%a.err

set -eo pipefail

SCRIPT_DIR=${SCRIPT_DIR:?SCRIPT_DIR required}
RUN_DIR=${RUN_DIR:?RUN_DIR required}

cd "${RUN_DIR}"
mkdir -p logs monitor

source /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh

{
    printf 'O3A_BNS_HOST=%q\n' "$(hostname)"
    printf 'O3A_BNS_START_UTC=%q\n' "$(date -u +%FT%TZ)"
    printf 'SLURM_JOB_ID=%q\n' "${SLURM_JOB_ID:-manual}"
    printf 'SLURM_ARRAY_TASK_ID=%q\n' "${SLURM_ARRAY_TASK_ID:-0}"
    env | grep '^WGUO_O3A_' | sort
} > "logs/wguo_o3a_bns_py3_env_${SLURM_JOB_ID:-manual}_${SLURM_ARRAY_TASK_ID:-0}.env"

run_spiir_py3 \
    -e SLURM_ARRAY_TASK_ID="${SLURM_ARRAY_TASK_ID:-0}" \
    -e WGUO_O3A_START_GPS="${WGUO_O3A_START_GPS:-1241725020}" \
    -e WGUO_O3A_END_GPS="${WGUO_O3A_END_GPS:-1241811420}" \
    -e WGUO_O3A_SNAPSHOT_INTERVAL="${WGUO_O3A_SNAPSHOT_INTERVAL:-3600}" \
    -e WGUO_O3A_COLLECT_WALLTIME="${WGUO_O3A_COLLECT_WALLTIME:-1209600,86400,7200}" \
    -e WGUO_O3A_BANK_DIR="${WGUO_O3A_BANK_DIR:-/fred/oz016/sunil/O3b_py3_banks}" \
    -e WGUO_O3A_FRAME_CACHE="${WGUO_O3A_FRAME_CACHE:-/fred/oz016/sunil/run_utils/frames_chache/frame_O3a.cache}" \
    -e WGUO_O3A_DETRSP_MAP="${WGUO_O3A_DETRSP_MAP:-/fred/oz016/wguo/odds_ratio/O3a/chunk6/multi_det-BNS-LVK_inj/H1L1V1_1242105073_detrsp_map.xml}" \
    -e WGUO_O3A_INJECTION_MODE="${WGUO_O3A_INJECTION_MODE:-none}" \
    -e WGUO_O3A_INJECTION_FILE="${WGUO_O3A_INJECTION_FILE:-}" \
    -e WGUO_O3A_NONINJ_STATS_LOC="${WGUO_O3A_NONINJ_STATS_LOC:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}" \
    -e WGUO_O3A_BANKS_PER_GROUP="${WGUO_O3A_BANKS_PER_GROUP:-4}" \
    -e WGUO_O3A_START_BANK="${WGUO_O3A_START_BANK:-0}" \
    -e WGUO_O3A_FINALSINK_NEED_ONLINE_PERFORM="${WGUO_O3A_FINALSINK_NEED_ONLINE_PERFORM:-0}" \
    -e WGUO_O3A_GRACEDB_FAR_THRESH="${WGUO_O3A_GRACEDB_FAR_THRESH:-0.0001}" \
    wguo-single-det-py3 bash "${SCRIPT_DIR}/wguo_o3a_bns_py3_pipeline.sh"

printf 'O3A_BNS_PY3_DONE job=%s task=%s utc=%s\n' \
    "${SLURM_JOB_ID:-manual}" "${SLURM_ARRAY_TASK_ID:-0}" "$(date -u +%FT%TZ)" \
    >> "logs/wguo_o3a_bns_py3_done_${SLURM_JOB_ID:-manual}_${SLURM_ARRAY_TASK_ID:-0}.log"
