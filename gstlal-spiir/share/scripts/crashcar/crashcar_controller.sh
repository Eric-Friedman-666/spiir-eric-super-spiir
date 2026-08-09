#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT_DIR=${ROOT}/scripts
RUN_DIR=${ROOT}/run
CONTROLLER_DIR=${ROOT}/controller
ARTIFACTS=${ROOT}/artifacts
STATUS=${CONTROLLER_DIR}/status.json
LOG=${CONTROLLER_DIR}/controller.log
CONFIG_FILE=${CRASHCAR_CONFIG_FILE:-${SCRIPT_DIR}/crashcar.env}
mkdir -p "${RUN_DIR}/logs" "${RUN_DIR}/monitor" "${CONTROLLER_DIR}" "${ARTIFACTS}"

set -a
# shellcheck source=/dev/null
source "${CONFIG_FILE}"
set +a

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "${LOG}"; }
status() {
    python3 - "${STATUS}" "$@" <<'PY'
import datetime, json, os, sys
path, *items = sys.argv[1:]
try:
    with open(path, encoding="utf-8") as handle: value = json.load(handle)
except (FileNotFoundError, json.JSONDecodeError):
    value = {"schema_version": 1}
for item in items:
    key, text = item.split("=", 1)
    if text in ("true", "false"): value[key] = text == "true"
    elif text.isdigit(): value[key] = int(text)
    else: value[key] = text
value["updated_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(value, handle, separators=(",", ":"), sort_keys=True); handle.write("\n")
os.replace(temporary, path)
PY
}
positive_integer() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
require_file() { [ -f "$2" ] || { log "ERROR missing $1: $2"; status phase=failed reason="missing_$1"; exit 2; }; }

SOURCE_ROOT=${root:?root required}
ROLE=${crashcar_role:?crashcar_role required}
case "${ROLE}" in A|B) ;; *) log "ERROR invalid role ${ROLE}"; exit 2 ;; esac
START_GPS=${start_gps:?start_gps required}
DURATION=${duration:?duration required}
positive_integer "${DURATION}" || { log 'ERROR duration must be positive'; exit 2; }
END_GPS=$((START_GPS + DURATION))
WORKER_COUNT=${worker_number:?worker_number required}
BANKS_PER_WORKER=${bank_per_worker:?bank_per_worker required}
START_BANK=${start_bank:-0}
BACKGROUND_ACCUMULATION=${background_accumulation:?background_accumulation required}
BACKGROUND_UPDATE=${background_update:?background_update required}
ZEROLAG_UPDATE=${zerolag_update_seconds:?zerolag_update_seconds required}
MULTI_SNAPSHOT=${cohfar_accumbackground_snapshot_interval_seconds:-${BACKGROUND_UPDATE}}
ASSIGN_REFRESH=${cohfar_assignfar_refresh_interval_seconds:-${BACKGROUND_UPDATE}}
FAP_REFRESH=${finalsink_fapupdater_interval_seconds:-${BACKGROUND_UPDATE}}
FAP_COLLECT=${finalsink_fapupdater_collect_walltime:-$((MULTI_SNAPSHOT + 1)),$((MULTI_SNAPSHOT + 1)),$((MULTI_SNAPSHOT + 1))}
FRAME_CACHE=${data_file:?data_file required}
DETRSP_MAP=${detector_response_file:?detector_response_file required}
SEGMENT_XML=${segment_xml:?segment_xml required}
INJECTION_FILE=${injection_file:-}
BACKGROUND_ROOT=${background_run_root:-}
O3_BANK_DIR=${bank_file:?bank_file required}
DOF=${dof:-120}
TAIL_LOG_FAR=${tail_log_FAR:?tail_log_FAR required}
SNR_THRESHOLD=${SNR_series_logFAR_threshold:--4}
SLURM_JOB_NAME=${slurm_job_name:-crashcar_${ROLE}}
SLURM_TIME=${slurm_time:-7-00:00:00}
SLURM_MEM=${slurm_mem:-64g}
SLURM_CPUS=${slurm_cpus_per_task:-4}
SLURM_GRES=${slurm_gres:-gpu:1}
SLURM_PARTITION=${slurm_partition:-}
WGUO_BANK_STATS_DIR=${wguo_bank_stats_dir:-/fred/oz016/wguo/packages/spiir/src/spiir/search/bank_dofs}

for value in "${WORKER_COUNT}" "${BANKS_PER_WORKER}" "${BACKGROUND_ACCUMULATION}" \
             "${BACKGROUND_UPDATE}" "${ZEROLAG_UPDATE}" "${MULTI_SNAPSHOT}"; do
    positive_integer "${value}" || { log "ERROR invalid positive integer ${value}"; exit 2; }
done
[[ "${START_GPS}" =~ ^[0-9]+$ && "${START_BANK}" =~ ^[0-9]+$ ]] || { log 'ERROR invalid start value'; exit 2; }
require_file frame_cache "${FRAME_CACHE}"
require_file detector_response "${DETRSP_MAP}"
require_file segment_xml "${SEGMENT_XML}"
[ "${ROLE}" = A ] || { require_file injection_file "${INJECTION_FILE}"; [ -d "${BACKGROUND_ROOT}/run" ] || { log 'ERROR B background_run_root has no run directory'; exit 2; }; }

for worker in $(seq 0 $((WORKER_COUNT - 1))); do
    for bank in $(seq $((START_BANK + BANKS_PER_WORKER * worker)) $((START_BANK + BANKS_PER_WORKER * (worker + 1) - 1))); do
        bank4=$(printf '%04d' "${bank}")
        for ifo in H1 L1 V1; do
            require_file bank "${O3_BANK_DIR}/iir_${ifo}-GSTLAL_SPLIT_BANK_${bank4}-a1-0-0.xml.gz"
        done
    done
done

LIVETIME_JSON=${ARTIFACTS}/H1L1_SEGMENTS_${START_GPS}_${DURATION}_livetime.json
python3 "${SCRIPT_DIR}/dump_segment_livetime_csv.py" "${SEGMENT_XML}" \
    --run-start "${START_GPS}" --run-end "${END_GPS}" --output "${LIVETIME_JSON}" \
    >"${CONTROLLER_DIR}/segment_livetime.log" 2>&1
require_file segment_livetime "${LIVETIME_JSON}"

module load gcc/13.3.0 scipy-bundle/2024.05 >/dev/null 2>&1
SHAPE_MAP=${ARTIFACTS}/crashcar_template_shape_map.csv
python3 "${SCRIPT_DIR}/export_template_shape_map.py" --bank-stats-dir "${WGUO_BANK_STATS_DIR}" \
    --output "${SHAPE_MAP}" --ifos H1,L1 --start-bank 0 --end-bank 383 \
    >"${CONTROLLER_DIR}/template_shape_map.log" 2>&1
require_file template_shape_map "${SHAPE_MAP}"

SOURCE_HEAD=$(git -C "${SOURCE_ROOT}" rev-parse HEAD)
SOURCE_BRANCH=$(git -C "${SOURCE_ROOT}" symbolic-ref --quiet --short HEAD 2>/dev/null || printf DETACHED)
REMOTE_HEAD=$(git -C "${SOURCE_ROOT}" rev-parse --verify --quiet "@{upstream}" 2>/dev/null || printf UNAVAILABLE)
DIRTY=$(git -C "${SOURCE_ROOT}" status --porcelain=v1 --untracked-files=no)
DIRTY_COUNT=$(printf '%s\n' "${DIRTY}" | awk 'NF{n++} END{print n+0}')
SOURCE_MANIFEST_SHA=$(printf '%s\n%s\n%s\n%s\n' "${SOURCE_BRANCH}" "${SOURCE_HEAD}" "${REMOTE_HEAD}" "${DIRTY}" | sha256sum | awk '{print $1}')
CONFIG_SHA=$(sha256sum "${CONFIG_FILE}" | awk '{print $1}')
RUN_NAMESPACE_SHA=$(printf '%s\n' "$(readlink -f -- "${ROOT}")" | sha256sum | awk '{print $1}')
SEGMENT_SHA=$(sha256sum "${SEGMENT_XML}" | awk '{print $1}')
SEGMENT_CANONICAL_SHA=$(sha256sum "${LIVETIME_JSON}" | awk '{print $1}')
SHAPE_SHA=$(sha256sum "${SHAPE_MAP}" | awk '{print $1}')

SOURCE_INSTALL=${SOURCE_ROOT}/install_local
require_file runtime_plugin "${SOURCE_INSTALL}/lib/gstreamer-1.0/libgstcuda.so.0.0.0"
require_file runtime_wrapper "${SOURCE_INSTALL}/bin/gstlal_inspiral_postcohspiir_online"
CRASH_ROOT=${ROOT}/crashcar_runtime
[ ! -e "${CRASH_ROOT}" ] || { log "ERROR runtime path already exists ${CRASH_ROOT}"; exit 2; }
mkdir "${CRASH_ROOT}"
cp -a "${SOURCE_INSTALL}" "${CRASH_ROOT}/install"
chmod -R a-w "${CRASH_ROOT}/install"
RUNTIME_MANIFEST_SHA=$(
    cd "${CRASH_ROOT}/install"
    find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
)
PLUGIN_SHA=$(sha256sum "${CRASH_ROOT}/install/lib/gstreamer-1.0/libgstcuda.so.0.0.0" | awk '{print $1}')

export RUN_DIR CRASH_ROOT TOP_RUN_ROOT=${ROOT} CRASHCAR_ROLE=${ROLE}
export CRASHCAR_BACKGROUND_RUN_ROOT=${BACKGROUND_ROOT}
export CRASHCAR_ENABLE=${CRASHCAR_ENABLE:-1} WGUO_O3A_START_GPS=${START_GPS} WGUO_O3A_END_GPS=${END_GPS}
export WGUO_O3A_DETRSP_MAP=${DETRSP_MAP} WGUO_O3A_FRAME_CACHE=${FRAME_CACHE}
export WGUO_O3A_BANK_DIR=${O3_BANK_DIR} WGUO_O3A_START_BANK=${START_BANK}
export WGUO_O3A_BANKS_PER_GROUP=${BANKS_PER_WORKER} WGUO_O3A_INJECTION_FILE=${INJECTION_FILE}
export CRASHCAR_CURRENT_WORKER_COUNT=${WORKER_COUNT} CRASHCAR_WORKER_COUNT=${WORKER_COUNT}
export BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION}
export BACKGROUND_UPDATE_TRIGGER_SECONDS=${BACKGROUND_UPDATE}
export COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS=${MULTI_SNAPSHOT}
export COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS=${ASSIGN_REFRESH}
export FINALSINK_FAPUPDATER_INTERVAL_SECONDS=${FAP_REFRESH}
export FINALSINK_FAPUPDATER_COLLECT_WALLTIME=${FAP_COLLECT}
export CRASHCAR_SNAPSHOT_INTERVAL_SECONDS=${ZEROLAG_UPDATE}
export SNR_series_logFAR_threshold=${SNR_THRESHOLD} TAIL_LOG_FAR
export CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME=${SHAPE_MAP}
export CRASHCAR_SEGMENT_LIVETIME_JSON=${LIVETIME_JSON} WGUO_O3A_SEGMENT_XML=${SEGMENT_XML}
export CRASHCAR_RUN_NAMESPACE_SHA256=${RUN_NAMESPACE_SHA}
export CRASHCAR_SOURCE_MANIFEST_SHA256=${SOURCE_MANIFEST_SHA}
export CRASHCAR_RUNTIME_MANIFEST_SHA256=${RUNTIME_MANIFEST_SHA}
export CRASHCAR_CONFIG_SHA256=${CONFIG_SHA} CRASHCAR_SEGMENT_XML_SHA256=${SEGMENT_SHA}
export CRASHCAR_SEGMENT_CANONICAL_SHA256=${SEGMENT_CANONICAL_SHA}
export CRASHCAR_TEMPLATE_SHAPE_MAP_SHA256=${SHAPE_SHA}
export CRASHCAR_CODE_VERSION=spiir-crashcar-ab

status phase=ready role="${ROLE}" run_root="${ROOT}" source_branch="${SOURCE_BRANCH}" \
    source_head="${SOURCE_HEAD}" remote_tracking_head="${REMOTE_HEAD}" source_dirty_tracked_count="${DIRTY_COUNT}" \
    runtime_manifest_sha256="${RUNTIME_MANIFEST_SHA}" runtime_plugin_sha256="${PLUGIN_SHA}" \
    config_sha256="${CONFIG_SHA}" background_run_root="${BACKGROUND_ROOT}" start_gps="${START_GPS}" end_gps="${END_GPS}"
log "role=${ROLE} source=${SOURCE_BRANCH}@${SOURCE_HEAD} runtime_plugin=${PLUGIN_SHA}"

SBATCH=(sbatch --parsable --job-name="${SLURM_JOB_NAME}" --array="0-$((WORKER_COUNT - 1))" \
    --time="${SLURM_TIME}" --mem="${SLURM_MEM}" --cpus-per-task="${SLURM_CPUS}" \
    --gres="${SLURM_GRES}" --chdir="${RUN_DIR}" --output='logs/crashcar_%A_%a.out' \
    --error='logs/crashcar_%A_%a.err' --export=ALL)
[ -z "${SLURM_PARTITION}" ] || SBATCH+=(--partition="${SLURM_PARTITION}")
JOB_ID=$("${SBATCH[@]}" "${SCRIPT_DIR}/crashcar_sbatch.sh")
JOB_ID=${JOB_ID%%;*}
status phase=slurm_submitted job_id="${JOB_ID}"
log "submitted role=${ROLE} Slurm array=${JOB_ID}_[0-$((WORKER_COUNT - 1))]"

while squeue -h -j "${JOB_ID}" | grep -q .; do
    SNAPSHOT=$(squeue -h -j "${JOB_ID}" -o '%i:%T:%M:%R' | paste -sd ',' -)
    status phase=slurm_running job_id="${JOB_ID}" squeue="${SNAPSHOT}"
    sleep 30
done
for _ in 1 2 3 4 5; do
    SACCT=$(sacct -X -j "${JOB_ID}" -n -P --format=JobIDRaw,State,ExitCode,Elapsed,NodeList | awk 'NF' | paste -sd ',' -)
    if sacct -X -j "${JOB_ID}" -n -P --format=JobIDRaw,State | awk -F'|' -v id="${JOB_ID}" '$1 == id || $1 ~ "^"id"_[0-9]+$" {seen=1; if ($2 !~ /^COMPLETED/) bad=1} END{exit (!seen || bad)}'; then
        status phase=completed job_id="${JOB_ID}" sacct="${SACCT}"
        log "completed role=${ROLE} job=${JOB_ID}"
        exit 0
    fi
    sleep 2
done
status phase=failed job_id="${JOB_ID}" sacct="${SACCT}"
log "failed role=${ROLE} job=${JOB_ID}"
exit 1
