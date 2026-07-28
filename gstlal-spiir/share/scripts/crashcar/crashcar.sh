#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SOURCE_ROOT_DEFAULT=$(cd "${SCRIPT_DIR}/../../../.." && pwd)
DEFAULT_CONFIG=${CRASHCAR_CONFIG_FILE:-"${SCRIPT_DIR}/crashcar.env"}
ROLE_OVERRIDE=${crashcar_role:-}
BACKGROUND_OVERRIDE=${background_run_root:-}

die() { printf 'crashcar: %s\n' "$*" >&2; exit 2; }
require_file() { [ -f "$2" ] || die "missing $1: $2"; }
positive_integer() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
env_last() {
    awk -v key="$2" 'index($0,key "=")==1 {v=substr($0,length(key)+2)} END {print v}' "$1"
}
duration_seconds() {
    local seconds=$1 hours=$2 label=$3
    if [ -n "${seconds}" ]; then
        positive_integer "${seconds}" || die "${label} duration must be a positive integer"
        printf '%s\n' "${seconds}"
    elif [ -n "${hours}" ]; then
        positive_integer "${hours}" || die "${label} duration_hour must be a positive integer"
        printf '%s\n' "$((hours * 3600))"
    else
        die "${label} duration is required"
    fi
}
copy_helper() {
    [ -f "${SCRIPT_DIR}/$1" ] || die "missing helper ${SCRIPT_DIR}/$1"
    cp "${SCRIPT_DIR}/$1" "${RUN_ROOT}/scripts/$1"
}

CONFIG_FILE=${1:-${DEFAULT_CONFIG}}
[ "$#" -le 1 ] || die "usage: bash scripts/crashcar.sh [path/to/crashcar.env]"
require_file config "${CONFIG_FILE}"
CONFIG_FILE=$(readlink -f -- "${CONFIG_FILE}")
set -a
# shellcheck source=/dev/null
source "${CONFIG_FILE}"
set +a

ROLE=${ROLE_OVERRIDE:-${crashcar_role:-}}
ROLE=${ROLE^^}
case "${ROLE}" in A|B) ;; *) die "crashcar_role must be A or B" ;; esac
SOURCE_ROOT_VALUE=${root:-${ROOT:-${SOURCE_ROOT_DEFAULT}}}
SAVE_DIR=${save_dir:-${SAVE_DIR:-"${SOURCE_ROOT_VALUE}/runs"}}
RUN_ID_VALUE=${run_id:-${RUN_ID:-crashcar}}
RUN_TIMESTAMP_VALUE=${run_timestamp:-${RUN_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}}
SOURCE_ROOT_VALUE=$(readlink -f -- "${SOURCE_ROOT_VALUE}")
RUN_ROOT_VALUE=${run_root:-${RUN_ROOT:-}}
RUN_ROOT=${RUN_ROOT_VALUE:-}
if [ -n "${RUN_ROOT}" ]; then
    RUN_ROOT=$(readlink -m -- "${RUN_ROOT}")
elif [ "${ROLE}" = A ]; then
    RUN_ROOT=$(readlink -m -- "${SAVE_DIR}/${RUN_ID_VALUE}/${RUN_TIMESTAMP_VALUE}/A")
fi

WORKERS=${worker_number:-${worker_count:-2}}
BANKS_PER_WORKER_VALUE=${bank_per_worker:-${banks_per_worker:-8}}
START_BANK_VALUE=${start_bank:-0}
BANK_DIR=${bank_file:-${o3_bank_dir:-}}
DOF_VALUE=${dof:-120}
TAIL_VALUE=${tail_log_FAR:-${tai_log_FAR:--2}}
SNR_VALUE=${SNR_series_logFAR_threshold:-${snr_series_logFAR_threshold:--4}}
BG_ACCUM=$(duration_seconds "${background_accumulation_seconds:-}" "${single_BG_accumulation_hour:-${BG_accumulation_hour:-3}}" background)
BG_UPDATE=$(duration_seconds "${background_update_trigger_seconds:-}" "${BG_update_hour:-1}" background_update)
ZEROLAG_UPDATE=$(duration_seconds "${zerolag_update_seconds:-}" "${zerolag_update_hour:-1}" zerolag_update)
MULTI_SNAPSHOT=${cohfar_accumbackground_snapshot_interval_seconds:-${BG_UPDATE}}
ASSIGN_REFRESH=${cohfar_assignfar_refresh_interval_seconds:-${BG_UPDATE}}
FAP_REFRESH=${finalsink_fapupdater_interval_seconds:-${BG_UPDATE}}
FAP_COLLECT=${finalsink_fapupdater_collect_walltime:-$((MULTI_SNAPSHOT + 1)),$((MULTI_SNAPSHOT + 1)),$((MULTI_SNAPSHOT + 1))}
for value in "${WORKERS}" "${BANKS_PER_WORKER_VALUE}" "${BG_ACCUM}" "${BG_UPDATE}" "${ZEROLAG_UPDATE}" "${MULTI_SNAPSHOT}"; do
    positive_integer "${value}" || die "worker counts and update periods must be positive integers"
done
[[ "${START_BANK_VALUE}" =~ ^[0-9]+$ ]] || die "start_bank must be non-negative"
[ -n "${BANK_DIR}" ] || die "bank_file is required"

BACKGROUND_ROOT=
if [ "${ROLE}" = A ]; then
    ROLE_DATA=${data_file:-}
    ROLE_DETRSP=${detector_response_file:-}
    ROLE_SEGMENT=${segment_xml:-}
    ROLE_START=${start_gps:-}
    ROLE_DURATION=$(duration_seconds "${duration_seconds:-${duration:-}}" "${duration_hour:-}" A)
    ROLE_INJECTION=
else
    ROLE_DATA=${injection_data_file:-}
    ROLE_DETRSP=${injection_detector_response_file:-}
    ROLE_SEGMENT=${injection_segment_xml:-}
    ROLE_START=${injection_start_gps:-}
    ROLE_DURATION=$(duration_seconds "${injection_duration_seconds:-}" "${injection_duration_hour:-}" B)
    ROLE_INJECTION=${injection_file:-}
    SNR_VALUE=90
    BACKGROUND_ROOT=${BACKGROUND_OVERRIDE:-${background_run_root:-}}
    [[ "${BACKGROUND_ROOT}" = /* ]] || die "B requires an absolute background_run_root"
    [ -d "${BACKGROUND_ROOT}" ] || die "background_run_root does not exist: ${BACKGROUND_ROOT}"
    BACKGROUND_ROOT=$(readlink -f -- "${BACKGROUND_ROOT}")
    if [ -z "${RUN_ROOT}" ]; then
        RUN_ROOT=$(readlink -m -- "$(dirname "${BACKGROUND_ROOT}")/B")
    fi
    [ "${BACKGROUND_ROOT}" != "${RUN_ROOT}" ] || die "B cannot read its own run root"
    A_CONFIG=${BACKGROUND_ROOT}/scripts/crashcar.env
    require_file A_config "${A_CONFIG}"
    [ "$(env_last "${A_CONFIG}" crashcar_role)" = A ] || die "background_run_root is not an A run"
    A_ROOT=$(readlink -f -- "$(env_last "${A_CONFIG}" run_root)")
    [ "${A_ROOT}" = "${BACKGROUND_ROOT}" ] || die "A config/run root mismatch"
    [ "$(env_last "${A_CONFIG}" worker_number)" = "${WORKERS}" ] || die "B worker_number differs from A"
    [ "$(env_last "${A_CONFIG}" bank_per_worker)" = "${BANKS_PER_WORKER_VALUE}" ] || die "B bank_per_worker differs from A"
    [ "$(env_last "${A_CONFIG}" start_bank)" = "${START_BANK_VALUE}" ] || die "B start_bank differs from A"
fi
[[ "${ROLE_START}" =~ ^[0-9]+$ ]] || die "start GPS must be non-negative"
require_file data_file "${ROLE_DATA}"
require_file detector_response_file "${ROLE_DETRSP}"
require_file segment_xml "${ROLE_SEGMENT}"
[ "${ROLE}" = A ] || require_file injection_file "${ROLE_INJECTION}"

if [ -e "${RUN_ROOT}" ] && [ "${crashcar_allow_existing_run_root:-0}" != 1 ]; then
    die "run root already exists: ${RUN_ROOT}"
fi
mkdir -p "${RUN_ROOT}/scripts"
for helper in crashcar.sh crashcar_controller.sh crashcar_sbatch.sh crashcar_pipeline.sh \
              dump_segment_livetime_csv.py export_template_shape_map.py; do
    copy_helper "${helper}"
done
cp "${CONFIG_FILE}" "${RUN_ROOT}/scripts/crashcar.user.env"
awk -F= '
BEGIN {
 split("root run_root run_id slurm_job_name crashcar_role background_run_root data_file detector_response_file segment_xml start_gps duration duration_seconds duration_hour worker_number bank_per_worker start_bank bank_file dof background_accumulation background_accumulation_seconds background_update background_update_trigger_seconds zerolag_update_seconds cohfar_accumbackground_snapshot_interval_seconds cohfar_assignfar_refresh_interval_seconds finalsink_fapupdater_interval_seconds finalsink_fapupdater_collect_walltime tail_log_FAR SNR_series_logFAR_threshold injection_file",a," ");
 for(i in a) drop[a[i]]=1
}
/^[A-Za-z_][A-Za-z0-9_]*=/ && drop[$1] {next} {print}
' "${RUN_ROOT}/scripts/crashcar.user.env" > "${RUN_ROOT}/scripts/crashcar.env"
{
    printf '\n# Effective immutable values generated by crashcar.sh.\n'
    printf 'root=%s\nrun_root=%s\nrun_id=%s_%s\nslurm_job_name=crashcar_%s\n' \
        "${SOURCE_ROOT_VALUE}" "${RUN_ROOT}" "${RUN_ID_VALUE}" "${ROLE}" "${ROLE}"
    printf 'crashcar_role=%s\nbackground_run_root=%s\n' "${ROLE}" "${BACKGROUND_ROOT}"
    printf 'data_file=%s\ndetector_response_file=%s\nsegment_xml=%s\n' \
        "${ROLE_DATA}" "${ROLE_DETRSP}" "${ROLE_SEGMENT}"
    printf 'start_gps=%s\nduration=%s\ninjection_file=%s\n' \
        "${ROLE_START}" "${ROLE_DURATION}" "${ROLE_INJECTION}"
    printf 'worker_number=%s\nbank_per_worker=%s\nstart_bank=%s\nbank_file=%s\ndof=%s\n' \
        "${WORKERS}" "${BANKS_PER_WORKER_VALUE}" "${START_BANK_VALUE}" "${BANK_DIR}" "${DOF_VALUE}"
    printf 'background_accumulation=%s\nbackground_update=%s\nzerolag_update_seconds=%s\n' \
        "${BG_ACCUM}" "${BG_UPDATE}" "${ZEROLAG_UPDATE}"
    printf 'cohfar_accumbackground_snapshot_interval_seconds=%s\ncohfar_assignfar_refresh_interval_seconds=%s\n' \
        "${MULTI_SNAPSHOT}" "${ASSIGN_REFRESH}"
    printf 'finalsink_fapupdater_interval_seconds=%s\nfinalsink_fapupdater_collect_walltime=%s\n' \
        "${FAP_REFRESH}" "${FAP_COLLECT}"
    printf 'tail_log_FAR=%s\nSNR_series_logFAR_threshold=%s\n' "${TAIL_VALUE}" "${SNR_VALUE}"
} >> "${RUN_ROOT}/scripts/crashcar.env"

chmod +x "${RUN_ROOT}/scripts/"*.sh "${RUN_ROOT}/scripts/"*.py
chmod 0444 "${RUN_ROOT}/scripts/crashcar.user.env" "${RUN_ROOT}/scripts/crashcar.env"
printf 'crashcar: staged role %s run root %s\n' "${ROLE}" "${RUN_ROOT}"
printf 'crashcar: config snapshot %s\n' "${RUN_ROOT}/scripts/crashcar.env"
if [ "${crashcar_dry_run:-0}" = 1 ]; then
    printf 'crashcar: dry run requested; Slurm was not submitted\n'
    exit 0
fi
ROOT="${SOURCE_ROOT_VALUE}" CRASHCAR_SOURCE_CONFIG_FILE="${CONFIG_FILE}" \
CRASHCAR_CONFIG_FILE="${RUN_ROOT}/scripts/crashcar.env" \
bash "${RUN_ROOT}/scripts/crashcar_controller.sh"
