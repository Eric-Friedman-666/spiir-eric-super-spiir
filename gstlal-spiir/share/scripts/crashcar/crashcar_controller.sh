#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT_DIR="${ROOT}/scripts"
RUN_DIR="${ROOT}/run"
CONTROLLER_DIR="${ROOT}/controller"
ARTIFACTS="${ROOT}/artifacts"
LOG="${CONTROLLER_DIR}/controller.log"
STATUS="${CONTROLLER_DIR}/status.json"
REPORT="${CONTROLLER_DIR}/final_report.json"
REGISTRY="${CONTROLLER_DIR}/tmux_registry.md"

CONFIG_FILE=${CRASHCAR_CONFIG_FILE:-"${SCRIPT_DIR}/crashcar.env"}
if [ ! -f "${CONFIG_FILE}" ]; then
    printf 'crashcar_controller: missing config file %s\n' "${CONFIG_FILE}" >&2
    exit 2
fi
set -a
# shellcheck source=/dev/null
source "${CONFIG_FILE}"
set +a

SOURCE_ROOT=${root:-${ROOT:-${source_root:-${SOURCE_ROOT:-}}}}
if [ -z "${SOURCE_ROOT}" ]; then
    printf 'crashcar_controller: root was not provided by launcher or config\n' >&2
    exit 2
fi
CRASH_RUNTIME_ROOT=${crash_runtime_root:-${CRASH_RUNTIME_ROOT:-"${ROOT}/crashcar_runtime"}}
CRASH_SCRIPT_DIR="${SCRIPT_DIR}"
GITHUB_REMOTE=${github_remote:-${GITHUB_REMOTE:-github}}
GITHUB_BRANCH=${github_branch:-${GITHUB_BRANCH:-Eric-bless-spiir-crashcar}}
GITHUB_REF="refs/heads/${GITHUB_BRANCH}"

START_GPS=${start_gps:-${START_GPS:-}}
: "${START_GPS:?start_gps required in ${CONFIG_FILE}}"
END_GPS=${end_gps:-${END_GPS:-}}
DURATION=${duration:-${DURATION:-}}
if [ -z "${DURATION}" ] && [ -n "${duration_hour:-}" ]; then
    DURATION=$((duration_hour * 3600))
fi
if [ -z "${END_GPS}" ]; then
    : "${DURATION:?duration_hour required when end_gps is unset}"
    END_GPS=$((START_GPS + DURATION))
fi
DURATION=$((END_GPS - START_GPS))
if [ -n "${BG_accumulation_hour:-}" ]; then
    BACKGROUND_ACCUMULATION=$((BG_accumulation_hour * 3600))
else
    BACKGROUND_ACCUMULATION=${background_accumulation:-${BACKGROUND_ACCUMULATION:-${background_accumulation_seconds:-${BACKGROUND_ACCUMULATION_SECONDS:-10800}}}}
fi
if [ -n "${BG_update_hour:-}" ]; then
    BACKGROUND_UPDATE=$((BG_update_hour * 3600))
else
    BACKGROUND_UPDATE=${background_update:-${BACKGROUND_UPDATE:-${background_update_trigger_seconds:-${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}}}}
fi
if [ -n "${zerolag_update_seconds:-}" ]; then
    ZEROLAG_UPDATE=${zerolag_update_seconds}
elif [ -n "${ZEROLAG_UPDATE_SECONDS:-}" ]; then
    ZEROLAG_UPDATE=${ZEROLAG_UPDATE_SECONDS}
elif [ -n "${zerolag_update_hour:-}" ]; then
    ZEROLAG_UPDATE=$((zerolag_update_hour * 3600))
elif [ -n "${snapshot_interval:-}" ]; then
    ZEROLAG_UPDATE=${snapshot_interval}
elif [ -n "${SNAPSHOT_INTERVAL:-}" ]; then
    ZEROLAG_UPDATE=${SNAPSHOT_INTERVAL}
else
    ZEROLAG_UPDATE=3600
fi
SNAPSHOT_INTERVAL=${ZEROLAG_UPDATE}
WORKER_COUNT=${worker_number:-${worker_count:-${WORKER_COUNT:-2}}}
BANKS_PER_WORKER=${bank_per_worker:-${banks_per_worker:-${BANKS_PER_WORKER:-8}}}
START_BANK=${start_bank:-${START_BANK:-0}}

DETRSP_MAP=${detector_response_file:-${detrsp_map:-${DETRSP_MAP:-}}}
: "${DETRSP_MAP:?detector_response_file required in ${CONFIG_FILE}}"
FRAME_CACHE=${data_file:-${frame_cache:-${FRAME_CACHE:-}}}
: "${FRAME_CACHE:?data_file required in ${CONFIG_FILE}}"

SEGMENT_XML=${segment_xml:-${SEGMENT_XML:-}}
if [ -z "${SEGMENT_XML}" ]; then
    detrsp_dir=$(dirname "${DETRSP_MAP}")
    candidate_segment=$(python3 - "${detrsp_dir}" "${START_GPS}" "${END_GPS}" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
start = int(float(sys.argv[2]))
end = int(float(sys.argv[3]))
pattern = re.compile(r"H1L1V1_SEGMENTS_(\d+)_(\d+)\.xml\.gz$")
matches = []
for path in root.glob("**/H1L1V1_SEGMENTS_*_*.xml.gz"):
    match = pattern.search(path.name)
    if not match:
        continue
    seg_start = int(match.group(1))
    seg_duration = int(match.group(2))
    seg_end = seg_start + seg_duration
    if seg_start <= start and seg_end >= end:
        matches.append((seg_start, seg_duration, str(path)))
if matches:
    print(sorted(matches, key=lambda item: (item[0], item[1], item[2]))[0][2])
PY
)
    if [ -n "${candidate_segment}" ]; then
        SEGMENT_XML=${candidate_segment}
    fi
fi
: "${SEGMENT_XML:?segment_xml could not be inferred; set segment_xml explicitly}"
LIVETIME_CSV=${livetime_csv:-${LIVETIME_CSV:-"${ARTIFACTS}/H1L1_SEGMENTS_${START_GPS}_${DURATION}_livetime.json"}}
SEGMENT_XML_CANONICAL=
SEGMENT_XML_SHA256=
SEGMENT_LIVETIME_JSON_CANONICAL=
SEGMENT_LIVETIME_JSON_SHA256=
SEGMENT_BINDING_RUN_START=
SEGMENT_BINDING_RUN_END=
RUNTIME_PROVENANCE_MANIFEST_SHA256=
SCHEMA4_RUN_NAMESPACE_SHA256=
SCHEMA4_SOURCE_MANIFEST_SHA256=
SCHEMA4_RUNTIME_MANIFEST_SHA256=
SCHEMA4_CONFIG_SHA256=
SCHEMA4_TEMPLATE_SHAPE_MAP_SHA256=
SCHEMA4_SOURCE_MANIFEST_PATH=
SCHEMA4_RUN_NAMESPACE_PATH=
CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE=${crashcar_internal_live_background_role:-${CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE:-}}
CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE=${crashcar_internal_live_background_root:-${CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROOT:-}}
CRASHCAR_LIVE_BACKGROUND_HELPER="${SCRIPT_DIR}/crashcar_live_background.py"
CRASHCAR_LIVE_BACKGROUND_HELPER_SHA256=
CRASHCAR_LIVE_BACKGROUND_HELPER_CURRENT_SHA256=
NONINJ_STATS_LOC=${noninj_stats_loc:-${NONINJ_STATS_LOC:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}}
O3_BANK_DIR=${bank_file:-${o3_bank_dir:-${O3_BANK_DIR:-}}}
: "${O3_BANK_DIR:?bank_file required in ${CONFIG_FILE}}"
WGUO_BANK_STATS_DIR=${wguo_bank_stats_dir:-${WGUO_BANK_STATS_DIR:-/fred/oz016/wguo/packages/spiir/src/spiir/search/bank_dofs}}
DOF=${dof:-${DOF:-}}
: "${DOF:?dof required in ${CONFIG_FILE}; use 120 for BNS or 600 for NSBH}"
python3 - "${DOF}" <<'PY'
import math
import sys
value = float(sys.argv[1])
if not math.isfinite(value) or value <= 0.0:
    raise SystemExit("dof must be finite and positive")
PY
NOISE_BETA=${noise_beta:-${NOISE_BETA:--1.0}}
RANK_OFFSET=${rank_offset:-${RANK_OFFSET:-0.0}}
TAIL_LOG_FAR=${tail_log_FAR:-${tai_log_FAR:-${TAIL_LOG_FAR:-}}}
if [ -z "${TAIL_LOG_FAR}" ]; then
    FAR_FIT_BOUNDARY=${tail_FAR:-${far_fit_boundary:-${FAR_FIT_BOUNDARY:-0.01}}}
    TAIL_LOG_FAR=$(python3 - "${FAR_FIT_BOUNDARY}" <<'PY'
import math
import sys
value = float(sys.argv[1])
if not math.isfinite(value) or not 0.0 < value < 1.0:
    raise SystemExit("tail_FAR must be finite and strictly between zero and one")
print("{:.17g}".format(math.log10(value)))
PY
) || exit 2
fi
TAIL_VALUES=$(python3 - "${TAIL_LOG_FAR}" <<'PY'
import math
import sys
value = float(sys.argv[1])
if not math.isfinite(value) or not value < 0.0:
    raise SystemExit("tail_log_FAR must be finite and strictly negative")
print("{:.17g} {:.17g}".format(value, math.pow(10.0, value)))
PY
) || exit 2
read -r TAIL_LOG_FAR FAR_FIT_BOUNDARY <<< "${TAIL_VALUES}"
unset TAIL_VALUES
SNR_SERIES_LOG_FAR_THRESHOLD=${SNR_series_logFAR_threshold:-${snr_series_logFAR_threshold:-${SNR_SERIES_LOG_FAR_THRESHOLD:--4}}}
if [[ ! "${SNR_SERIES_LOG_FAR_THRESHOLD}" =~ ^[+-]?(([0-9]+([.][0-9]*)?)|([.][0-9]+))([eE][+-]?[0-9]+)?$ ]] ||
   ! python3 -c "import math, sys; value = float(sys.argv[1]); raise SystemExit(0 if math.isfinite(value) else 2)" "${SNR_SERIES_LOG_FAR_THRESHOLD}"; then
    printf "crashcar_controller: SNR_series_logFAR_threshold must be a finite number, got %q\n" \
        "${SNR_SERIES_LOG_FAR_THRESHOLD}" >&2
    exit 2
fi
CRASHCAR_CODE_VERSION=${crashcar_code_version:-${CRASHCAR_CODE_VERSION:-"spiir-crashcar-${GITHUB_BRANCH}"}}
SLURM_JOB_NAME=${slurm_job_name:-${SLURM_JOB_NAME:-crashcar}}
SLURM_PARTITION=${slurm_partition:-${SLURM_PARTITION:-}}
SLURM_TIME=${slurm_time:-${SLURM_TIME:-7-00:00:00}}
SLURM_MEM=${slurm_mem:-${SLURM_MEM:-64g}}
SLURM_CPUS_PER_TASK=${slurm_cpus_per_task:-${SLURM_CPUS_PER_TASK:-4}}
SLURM_GRES=${slurm_gres:-${SLURM_GRES:-gpu:1}}
case "$(printf '%s' "${SLURM_GRES}" | tr '[:upper:]' '[:lower:]')" in
    none|no|false|0) SLURM_GRES="" ;;
esac
TMUX_SESSION=${tmux_session:-${TMUX_SESSION:-codex1}}
CRASHCAR_LOG10_FAR_THRESHOLD=${crashcar_log10_far_threshold:-${CRASHCAR_LOG10_FAR_THRESHOLD:-90}}
CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP=${crashcar_require_template_shape_map:-${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}}
# Production science authority is the live Postcoh row followed by the normal
# FinalSink/CoincsDoc path.  Post-run ledgers, output patchers, validation
# archives, and acceptance checkers are intentionally outside this controller.

INJECTION_MODE_RAW=${injection_mode:-${INJECTION_MODE:-False}}
case "$(printf '%s' "${INJECTION_MODE_RAW}" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes) INJECTION_MODE=True ;;
    false|0|no|"") INJECTION_MODE=False ;;
    *)
        printf 'crashcar_controller: invalid injection_mode=%s; expected True or False\n' "${INJECTION_MODE_RAW}" >&2
        exit 2
        ;;
esac
INJECTION_FILE=${injection_file:-${WGUO_O3A_INJECTION_FILE:-}}
INJECTION_BG_DATA_FILE=${injection_bg_data_file:-}
INJECTION_BG_DETRSP_MAP=${injection_bg_detector_response_file:-}
INJECTION_BG_START_GPS=${injection_bg_start_gps:-}
INJECTION_BG_DURATION_HOUR=${injection_bg_duration_hour:-}
INJECTION_BG_DURATION_SECONDS=${injection_bg_duration_seconds:-}
INJECTION_BG_SEGMENT_XML=${injection_bg_segment_xml:-}
INJECTION_BG_END_GPS=
INJECTION_PIPELINE_MODE=none
if [ "${INJECTION_MODE}" = "True" ]; then
    : "${INJECTION_FILE:?injection_file required when injection_mode=True}"
    : "${INJECTION_BG_DATA_FILE:?injection_bg_data_file required when injection_mode=True}"
    : "${INJECTION_BG_DETRSP_MAP:?injection_bg_detector_response_file required when injection_mode=True}"
    : "${INJECTION_BG_START_GPS:?injection_bg_start_gps required when injection_mode=True}"
    : "${INJECTION_BG_SEGMENT_XML:?injection_bg_segment_xml required when injection_mode=True}"
    if [ -z "${INJECTION_BG_DURATION_SECONDS}" ]; then
        : "${INJECTION_BG_DURATION_HOUR:?injection_bg_duration_seconds or injection_bg_duration_hour required when injection_mode=True}"
        INJECTION_BG_DURATION_SECONDS=$((INJECTION_BG_DURATION_HOUR * 3600))
    fi
    INJECTION_BG_END_GPS=$((INJECTION_BG_START_GPS + INJECTION_BG_DURATION_SECONDS))
    INJECTION_PIPELINE_MODE=blind
fi

SINGLE_BACKGROUND_MODE_VALUE=${single_background_mode:-${SINGLE_BACKGROUND_MODE:-rolling}}
CRASHCAR_BG_ONLY_VALUE=${crashcar_internal_bg_only:-${CRASHCAR_INTERNAL_BG_ONLY:-0}}
case "$(printf '%s' "${CRASHCAR_BG_ONLY_VALUE}" | tr '[:upper:]' '[:lower:]')" in
    ""|0|false|no|off) CRASHCAR_BG_ONLY_VALUE=0 ;;
    1|true|yes|on) CRASHCAR_BG_ONLY_VALUE=1 ;;
    *)
        printf 'crashcar_controller: internal BG-only value must be boolean\n' >&2
        exit 2
        ;;
esac
CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE=${crashcar_background_required_seconds:-${CRASHCAR_BACKGROUND_REQUIRED_SECONDS:-${BACKGROUND_ACCUMULATION}}}
COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS_VALUE=${cohfar_assignfar_refresh_interval_seconds:-${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS:-}}
FINALSINK_FAPUPDATER_INTERVAL_SECONDS_VALUE=${finalsink_fapupdater_interval_seconds:-${FINALSINK_FAPUPDATER_INTERVAL_SECONDS:-}}
FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE=${finalsink_fapupdater_collect_walltime:-${FINALSINK_FAPUPDATER_COLLECT_WALLTIME:-}}
COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE=${cohfar_accumbackground_snapshot_interval_seconds:-${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS:-${BACKGROUND_UPDATE}}}
case "${SINGLE_BACKGROUND_MODE_VALUE}" in
    rolling|live_readonly) ;;
    *)
        printf 'crashcar_controller: invalid single_background_mode=%s; expected rolling or live_readonly\n' \
            "${SINGLE_BACKGROUND_MODE_VALUE}" >&2
        exit 2
        ;;
esac
case "${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" in
    ""|producer|consumer) ;;
    *)
        printf 'crashcar_controller: internal live background role must be producer or consumer\n' >&2
        exit 2
        ;;
esac
if [ "${INJECTION_MODE}" = "True" ] &&
   { [ "${SINGLE_BACKGROUND_MODE_VALUE}" != "live_readonly" ] ||
     [ "${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" != "consumer" ]; }; then
    printf 'crashcar_controller: injection_mode=True requires the internal live_readonly consumer\n' >&2
    exit 2
fi
if [ "${CRASHCAR_BG_ONLY_VALUE}" = "1" ] &&
   { [ "${INJECTION_MODE}" != "False" ] ||
     [ "${SINGLE_BACKGROUND_MODE_VALUE}" != "rolling" ] ||
     [ -n "${INJECTION_FILE}" ]; }; then
    printf 'crashcar_controller: BG-only requires no injection input and rolling background mode\n' >&2
    exit 2
fi
if [ "${SINGLE_BACKGROUND_MODE_VALUE}" = "live_readonly" ] &&
   [ "${CRASHCAR_BG_ONLY_VALUE}" != "0" ]; then
    printf 'crashcar_controller: live read-only assignment cannot be BG-only\n' >&2
    exit 2
fi
if [ "${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" = "producer" ] &&
   { [ "${INJECTION_MODE}" != "False" ] ||
     [ "${SINGLE_BACKGROUND_MODE_VALUE}" != "rolling" ]; }; then
    printf 'crashcar_controller: live producer must be a rolling no-injection run\n' >&2
    exit 2
fi
if [ "${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" = "consumer" ]; then
    if [ "${SINGLE_BACKGROUND_MODE_VALUE}" != "live_readonly" ] ||
       [ "${INJECTION_MODE}" != "True" ] ||
       [ -z "${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" ]; then
        printf 'crashcar_controller: live consumer requires injection, live_readonly mode, and producer root\n' >&2
        exit 2
    fi
    if [[ "${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" != /* ]]; then
        printf 'crashcar_controller: live producer root must be absolute\n' >&2
        exit 2
    fi
    # B1 and B2 launch concurrently.  This bounded bootstrap wait is only for
    # the staged producer root, never for Slurm RUNNING or scientific BG files.
    for _bootstrap_attempt in $(seq 1 120); do
        [ -d "${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}/run" ] && break
        sleep 1
    done
    if [ ! -d "${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}/run" ]; then
        printf 'crashcar_controller: live producer staged root did not appear\n' >&2
        exit 2
    fi
    CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE=$(readlink -f -- "${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}")
    if [ "${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" = "$(readlink -f -- "${ROOT}")" ]; then
        printf 'crashcar_controller: injection consumer must not use its own run as producer root\n' >&2
        exit 2
    fi
    NONINJ_STATS_LOC="${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}/run"
elif [ -n "${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" ]; then
    printf 'crashcar_controller: only the live consumer may receive a producer root\n' >&2
    exit 2
fi
if [ -z "${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS_VALUE}" ]; then
    COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS_VALUE=${BACKGROUND_UPDATE}
fi
if [ -z "${FINALSINK_FAPUPDATER_INTERVAL_SECONDS_VALUE}" ]; then
    FINALSINK_FAPUPDATER_INTERVAL_SECONDS_VALUE=${BACKGROUND_UPDATE}
fi
for positive_name in \
    COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS_VALUE \
    FINALSINK_FAPUPDATER_INTERVAL_SECONDS_VALUE \
    COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE; do
    if ! [[ "${!positive_name}" =~ ^[1-9][0-9]*$ ]]; then
        printf 'crashcar_controller: %s must be a positive integer\n' "${positive_name}" >&2
        exit 2
    fi
done

validate_fap_collect_walltime() {
    local raw_value=$1 collect_value
    local -a collect_values=()
    IFS=',' read -r -a collect_values <<< "${raw_value}"
    [ "${#collect_values[@]}" -eq 3 ] || return 1
    for collect_value in "${collect_values[@]}"; do
        [[ "${collect_value}" =~ ^[1-9][0-9]*$ ]] || return 1
    done
}

if [ -z "${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE}" ]; then
    # FAPUpdater selects bank snapshots with the strict predicate
    # snapshot_start_gps > event_gps - collect_walltime.  One extra second
    # includes the just-completed snapshot while excluding the previous period.
    FAP_COLLECT_WALLTIME_DEFAULT=$(( COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE + 1 ))
    FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE="${FAP_COLLECT_WALLTIME_DEFAULT},${FAP_COLLECT_WALLTIME_DEFAULT},${FAP_COLLECT_WALLTIME_DEFAULT}"
    FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE=derived_snapshot_plus_one
else
    FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE=explicit_config
fi
if ! validate_fap_collect_walltime "${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE}"; then
    printf 'crashcar_controller: finalsink_fapupdater_collect_walltime expected exactly three comma-separated positive integers\n' >&2
    exit 2
fi
H_ONLY_SECONDS=${h_only_seconds:-${H_ONLY_SECONDS:-0}}
L_ONLY_SECONDS=${l_only_seconds:-${L_ONLY_SECONDS:-0}}
HL_SECONDS=${hl_seconds:-${HL_SECONDS:-0}}
HL_NONE_SECONDS=${hl_none_seconds:-${HL_NONE_SECONDS:-0}}
SINGLE_ONLY_SECONDS=${single_only_seconds:-${SINGLE_ONLY_SECONDS:-0}}
SINGLE_ONLY_FRACTION=${single_only_fraction:-${SINGLE_ONLY_FRACTION:-}}
HL_UNION_FRACTION=${hl_union_fraction:-${HL_UNION_FRACTION:-}}
FIRST3_H_ONLY_SECONDS=${first3_h_only_seconds:-${FIRST3_H_ONLY_SECONDS:-0}}
FIRST3_L_ONLY_SECONDS=${first3_l_only_seconds:-${FIRST3_L_ONLY_SECONDS:-0}}
FIRST3_HL_SECONDS=${first3_hl_seconds:-${FIRST3_HL_SECONDS:-0}}
FIRST3_HL_NONE_SECONDS=${first3_hl_none_seconds:-${FIRST3_HL_NONE_SECONDS:-0}}

mkdir -p "${RUN_DIR}/logs" "${RUN_DIR}/monitor" "${CONTROLLER_DIR}" "${ARTIFACTS}" "${CRASH_RUNTIME_ROOT}" "${ROOT}/provenance"

log() {
    printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "${LOG}"
}

write_status() {
    ROOT="${ROOT}" STATUS="${STATUS}" python3 - "$@" <<'PY'
import json
import os
import sys
import time

payload = {}
if os.path.exists(os.environ["STATUS"]):
    try:
        payload = json.load(open(os.environ["STATUS"], "r", encoding="utf-8"))
    except Exception:
        payload = {}
for item in sys.argv[1:]:
    key, value = item.split("=", 1)
    payload[key] = value
payload["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
payload["root"] = os.environ["ROOT"]
with open(os.environ["STATUS"], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

live_background_helper_failure() {
    local check_phase=$1 failure=$2
    log "ERROR staged live-background helper integrity failed phase=${check_phase} reason=${failure}"
    write_status phase=failed reason=live_background_helper_integrity_failed \
        live_background_helper_check_phase="${check_phase}" \
        live_background_helper_failure="${failure}" \
        live_background_helper="${CRASHCAR_LIVE_BACKGROUND_HELPER}" \
        live_background_helper_sha256="${CRASHCAR_LIVE_BACKGROUND_HELPER_SHA256:-UNPINNED}"
    return 1
}

snapshot_live_background_helper() {
    local check_phase=$1 result
    if ! result=$(python3 - "${ROOT}" "${SCRIPT_DIR}" "${CRASHCAR_LIVE_BACKGROUND_HELPER}" 2>&1 <<'PY_LIVE_HELPER'
import hashlib
import os
from pathlib import Path
import stat
import sys

root, script_dir, helper = map(Path, sys.argv[1:])
try:
    if not root.is_absolute() or not script_dir.is_absolute() or not helper.is_absolute():
        raise RuntimeError("paths_must_be_absolute")
    root_real = Path(os.path.realpath(root))
    script_real = Path(os.path.realpath(script_dir))
    if script_real != root_real / "scripts":
        raise RuntimeError("script_dir_outside_run_root")
    before = os.lstat(helper)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("helper_not_regular_non_symlink")
    if before.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("helper_is_writable")
    if not before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise RuntimeError("helper_is_not_executable")
    if Path(os.path.realpath(helper)) != script_real / "crashcar_live_background.py":
        raise RuntimeError("helper_outside_run_scripts")
    fd = os.open(helper, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > 8 * 1024 * 1024:
                raise RuntimeError("helper_exceeds_size_limit")
            digest.update(block)
    finally:
        os.close(fd)
    after = os.lstat(helper)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(opened) or identity(opened) != identity(after):
        raise RuntimeError("helper_changed_during_snapshot")
    if total != opened.st_size or total == 0:
        raise RuntimeError("helper_size_mismatch")
    print(digest.hexdigest())
except (OSError, RuntimeError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
PY_LIVE_HELPER
    ); then
        live_background_helper_failure "${check_phase}" "${result:-snapshot_failed}"
        return 1
    fi
    CRASHCAR_LIVE_BACKGROUND_HELPER_CURRENT_SHA256=${result}
}

pin_live_background_helper() {
    snapshot_live_background_helper startup || return 1
    CRASHCAR_LIVE_BACKGROUND_HELPER_SHA256=${CRASHCAR_LIVE_BACKGROUND_HELPER_CURRENT_SHA256}
    write_status live_background_helper="${CRASHCAR_LIVE_BACKGROUND_HELPER}" \
        live_background_helper_sha256="${CRASHCAR_LIVE_BACKGROUND_HELPER_SHA256}"
}

verify_live_background_helper_pin() {
    local check_phase=$1
    snapshot_live_background_helper "${check_phase}" || return 1
    if [ "${CRASHCAR_LIVE_BACKGROUND_HELPER_CURRENT_SHA256}" != \
         "${CRASHCAR_LIVE_BACKGROUND_HELPER_SHA256}" ]; then
        live_background_helper_failure "${check_phase}" helper_sha256_drift
        return 1
    fi
}

prepare_live_background_contract() {
    [ "${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" = "consumer" ] || return 0
    local binding="${CONTROLLER_DIR}/live_single_binding.input.json"
    local temporary="${binding}.tmp.$$"
    local error="${CONTROLLER_DIR}/live_single_binding.input.err"
    local rc
    if python3 - "${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" \
        "${WORKER_COUNT}" "${BANKS_PER_WORKER}" "${START_BANK}" \
        "${INJECTION_BG_START_GPS}" \
        >"${temporary}" 2>"${error}" <<'PY_LIVE_BINDING'
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

root_text, count_text, bpw_text, start_text, origin_text = sys.argv[1:]
root = Path(root_text).resolve()
worker_count = int(count_text)
banks_per_worker = int(bpw_text)
start_bank = int(start_text)
origin_gps = int(origin_text)
status_path = root / "controller" / "status.json"
deadline = time.monotonic() + 600.0
hex64 = re.compile(r"^[0-9a-f]{64}$")
last_pending = "producer staged provenance has not appeared"

class Pending(Exception):
    pass

class Terminal(Exception):
    pass

def regular(path):
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise Pending(str(exc))
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise Terminal("not a regular non-symlink file: %s" % path)

def sha256(path):
    regular(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def env_values(path):
    regular(path)
    result = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.rstrip("\n")
            if not raw or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            result[key] = value
    return result

while True:
    try:
        regular(status_path)
        with open(status_path, encoding="utf-8") as handle:
            status = json.load(handle)
        phase = str(status.get("phase", ""))
        if phase.startswith("failed") or phase == "completed":
            raise Terminal("producer reached terminal phase %s" % phase)
        if status.get("root") not in (None, str(root)):
            raise Terminal("producer root mismatch")
        if status.get("live_background_role") != "producer":
            raise Terminal("producer role mismatch")
        if status.get("single_background_mode") != "rolling":
            raise Terminal("producer background mode mismatch")
        if str(status.get("injection_mode")) != "False":
            raise Terminal("producer is not no-injection")
        if int(status.get("worker_count", -1)) != worker_count:
            raise Terminal("producer worker count mismatch")
        if int(status.get("banks_per_worker", -1)) != banks_per_worker:
            raise Terminal("producer banks-per-worker mismatch")
        if int(status.get("start_bank", -1)) != start_bank:
            raise Terminal("producer start-bank mismatch")
        if int(status.get("start_gps", -1)) != origin_gps:
            raise Terminal("producer origin GPS mismatch")

        pin_names = (
            "schema4_run_namespace_sha256",
            "schema4_source_manifest_sha256",
            "schema4_runtime_manifest_sha256",
            "schema4_config_sha256",
            "schema4_template_shape_map_sha256",
        )
        if any(not hex64.fullmatch(str(status.get(name, "")))
               for name in pin_names):
            raise Pending("producer schema4 pins are not complete")

        run_namespace = root / "provenance" / "schema4" / "run_namespace.txt"
        source_manifest = root / "provenance" / "schema4" / "source_manifest.env"
        runtime_manifest = root / "provenance" / "runtime_snapshot" / "runtime_manifest.env"
        config = root / "scripts" / "crashcar.env"
        template_map = root / "artifacts" / "crashcar_template_shape_map.csv"
        config_values = env_values(config)
        tail_text = (
            config_values.get("tail_log_FAR")
            or config_values.get("tai_log_FAR")
            or config_values.get("TAIL_LOG_FAR")
        )
        if tail_text:
            producer_tail_log10_far = float(tail_text)
        else:
            boundary_text = (
                config_values.get("tail_FAR")
                or config_values.get("far_fit_boundary")
                or config_values.get("FAR_FIT_BOUNDARY")
                or "0.01"
            )
            boundary = float(boundary_text)
            if not math.isfinite(boundary) or not 0.0 < boundary < 1.0:
                raise Terminal("producer tail_FAR is invalid")
            producer_tail_log10_far = math.log10(boundary)
        if (not math.isfinite(producer_tail_log10_far)
                or not producer_tail_log10_far < 0.0):
            raise Terminal("producer tail_log_FAR is invalid")
        status_tail = float(status.get(
            "tail_log_FAR", producer_tail_log10_far))
        if status_tail != producer_tail_log10_far:
            raise Terminal("producer tail_log_FAR status/config mismatch")
        runtime = env_values(runtime_manifest)
        segment_xml_sha = runtime.get("crashcar_segment_xml_sha256", "")
        segment_canonical_sha = runtime.get(
            "crashcar_segment_livetime_json_sha256", "")
        runtime_files_sha = runtime.get("runtime_files_manifest_sha256", "")
        if not all(hex64.fullmatch(value) for value in (
                segment_xml_sha, segment_canonical_sha,
                runtime_files_sha)):
            raise Pending("producer runtime segment pins are not complete")

        identities = {
            "run_namespace_sha256": sha256(run_namespace),
            "source_manifest_sha256": sha256(source_manifest),
            "runtime_manifest_sha256": runtime_files_sha,
            "config_sha256": sha256(config),
            "segment_xml_sha256": segment_xml_sha,
            "segment_canonical_sha256": segment_canonical_sha,
            "template_shape_map_sha256": sha256(template_map),
        }
        expected = {
            "run_namespace_sha256": status["schema4_run_namespace_sha256"],
            "source_manifest_sha256": status["schema4_source_manifest_sha256"],
            "runtime_manifest_sha256": status["schema4_runtime_manifest_sha256"],
            "config_sha256": status["schema4_config_sha256"],
            "template_shape_map_sha256": status[
                "schema4_template_shape_map_sha256"],
        }
        for key, value in expected.items():
            if identities[key] != value:
                raise Terminal("producer %s mismatch" % key)
        if run_namespace.read_text(encoding="utf-8") != (
                "run_root=%s\n" % root):
            raise Terminal("producer run namespace content mismatch")
        if status.get("crashcar_segment_livetime_sha256") != (
                segment_canonical_sha):
            raise Terminal("producer segment canonical SHA mismatch")

        workers = []
        for worker in range(worker_count):
            first = start_bank + worker * banks_per_worker
            workers.append({
                "worker_id": worker,
                "worker_count": worker_count,
                "worker_bank_ids": list(range(first, first + banks_per_worker)),
                "single_background_path": str(
                    root / "run" / ("%03d" % worker) /
                    "single_background.json"),
            })
        payload = {
            "kind": "crashcar_live_single_binding_contract_v1",
            "producer_root": str(root),
            "producer_origin_gps": origin_gps,
            "worker_count": worker_count,
            "banks_per_worker": banks_per_worker,
            "start_bank": start_bank,
            "identities": identities,
            "tail_log10_far": producer_tail_log10_far,
            "workers": workers,
            "background_files_required_at_submit": False,
        }
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        break
    except Pending as exc:
        last_pending = str(exc)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        last_pending = str(exc)
    except Terminal as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
    if time.monotonic() >= deadline:
        print("timed out waiting only for producer staged provenance: %s" %
              last_pending, file=sys.stderr)
        raise SystemExit(3)
    time.sleep(1.0)
PY_LIVE_BINDING
    then
        chmod 0444 "${temporary}"
        mv -f "${temporary}" "${binding}"
        write_status phase=live_background_binding_ready \
            live_background_role=consumer \
            live_background_producer_root="${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" \
            live_single_binding="${binding}" \
            live_single_binding_sha256="$(sha256sum "${binding}" | awk '{print $1}')" \
            initial_backgrounds_required=false
        return 0
    else
        rc=$?
        rm -f "${temporary}"
        log "ERROR producer staged-provenance binding failed"
        write_status phase=failed reason=live_producer_binding_invalid \
            live_binding_error="${error}" live_binding_rc="${rc}"
        return "${rc}"
    fi
}

detail_summary_json() {
    RUN_DIR="${RUN_DIR}" python3 - <<'PY'
import csv
import json
import math
import os
from pathlib import Path

payload = {
    "exists": False,
    "files": [],
    "rows": 0,
    "finite_calculated_far_rows": 0,
    "ready_window_rows": 0,
    "min_calculated_far": None,
    "max_window_count": 0,
    "max_total_window_count": 0,
}
for path in sorted(Path(os.environ["RUN_DIR"]).glob("crashcar_singlefar_detail_worker*.csv")):
    item = {
        "path": str(path),
        "rows": 0,
        "finite_calculated_far_rows": 0,
        "ready_window_rows": 0,
        "min_calculated_far": None,
        "max_window_count": 0,
        "max_total_window_count": 0,
    }
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            item["rows"] += 1
            payload["rows"] += 1
            calculated_valid = (
                str(row.get("far_calculated_valid", "")).strip() == "1")
            try:
                calculated = float(row.get("far_calculated_exact", "") or "nan")
            except ValueError:
                calculated = math.nan
            try:
                window = int(float(row.get("window_count", "0") or 0))
                total = int(float(row.get("total_window_count", "0") or 0))
            except ValueError:
                window = 0
                total = 0
            item["max_window_count"] = max(item["max_window_count"], window)
            item["max_total_window_count"] = max(item["max_total_window_count"], total)
            payload["max_window_count"] = max(payload["max_window_count"], window)
            payload["max_total_window_count"] = max(payload["max_total_window_count"], total)
            if window > 0 and total > 0:
                item["ready_window_rows"] += 1
                payload["ready_window_rows"] += 1
            if calculated_valid and math.isfinite(calculated) and calculated > 0.0:
                item["finite_calculated_far_rows"] += 1
                payload["finite_calculated_far_rows"] += 1
                if item["min_calculated_far"] is None or calculated < item["min_calculated_far"]:
                    item["min_calculated_far"] = calculated
                if payload["min_calculated_far"] is None or calculated < payload["min_calculated_far"]:
                    payload["min_calculated_far"] = calculated
    payload["files"].append(item)
payload["exists"] = bool(payload["files"])
print(json.dumps(payload, sort_keys=True))
PY
}

count_zerolag() {
    find "${RUN_DIR}" -maxdepth 2 -type f -name '*_zerolag_*.xml*' | wc -l | awk '{print $1}'
}

count_stats() {
    find "${RUN_DIR}" -maxdepth 2 -type f -name '*_marginalized_stats_*.xml*' | wc -l | awk '{print $1}'
}

segment_binding_failure() {
    local check_phase=${1:?segment binding check phase required}
    local reason=${2:?segment binding failure reason required}
    log "ERROR segment derivative binding failed phase=${check_phase} reason=${reason}"
    write_status phase=failed reason=segment_derivative_binding_failed binding_check_phase="${check_phase}" binding_failure_reason="${reason}"
    return 1
}

verify_segment_derivative_binding() {
    local check_phase=${1:?segment binding check phase required}
    local current_xml_path current_json_path current_xml_sha current_json_sha probe

    for required in \
        SEGMENT_XML_CANONICAL SEGMENT_XML_SHA256 \
        SEGMENT_LIVETIME_JSON_CANONICAL SEGMENT_LIVETIME_JSON_SHA256 \
        SEGMENT_BINDING_RUN_START SEGMENT_BINDING_RUN_END; do
        if [ -z "${!required:-}" ]; then
            segment_binding_failure "${check_phase}" "missing_${required}"
            return 1
        fi
    done
    if ! current_xml_path=$(readlink -f -- "${SEGMENT_XML}"); then
        segment_binding_failure "${check_phase}" raw_segment_xml_unresolvable
        return 1
    fi
    if ! current_json_path=$(readlink -f -- "${LIVETIME_CSV}"); then
        segment_binding_failure "${check_phase}" canonical_derivative_unresolvable
        return 1
    fi
    if [ "${current_xml_path}" != "${SEGMENT_XML_CANONICAL}" ]; then
        segment_binding_failure "${check_phase}" raw_segment_xml_path_drift
        return 1
    fi
    if [ "${current_json_path}" != "${SEGMENT_LIVETIME_JSON_CANONICAL}" ]; then
        segment_binding_failure "${check_phase}" canonical_derivative_path_drift
        return 1
    fi
    if [ "${START_GPS}" != "${SEGMENT_BINDING_RUN_START}" ] ||
       [ "${END_GPS}" != "${SEGMENT_BINDING_RUN_END}" ]; then
        segment_binding_failure "${check_phase}" run_frontier_drift
        return 1
    fi
    current_xml_sha=$(sha256sum "${SEGMENT_XML_CANONICAL}" | awk '{print $1}')
    current_json_sha=$(sha256sum "${SEGMENT_LIVETIME_JSON_CANONICAL}" | awk '{print $1}')
    if [ "${current_xml_sha}" != "${SEGMENT_XML_SHA256}" ]; then
        segment_binding_failure "${check_phase}" raw_segment_xml_sha256_drift
        return 1
    fi
    if [ "${current_json_sha}" != "${SEGMENT_LIVETIME_JSON_SHA256}" ]; then
        segment_binding_failure "${check_phase}" canonical_derivative_sha256_drift
        return 1
    fi

    probe=$(mktemp "${CONTROLLER_DIR}/.segment_livetime.verify.XXXXXX")
    if ! python3 "${CRASH_SCRIPT_DIR}/dump_segment_livetime_csv.py" \
        "${SEGMENT_XML_CANONICAL}" \
        --run-start "${SEGMENT_BINDING_RUN_START}" \
        --run-end "${SEGMENT_BINDING_RUN_END}" \
        --output "${probe}" >/dev/null 2>&1; then
        rm -f -- "${probe}"
        segment_binding_failure "${check_phase}" canonical_derivative_regeneration_failed
        return 1
    fi
    if ! cmp -s "${probe}" "${SEGMENT_LIVETIME_JSON_CANONICAL}"; then
        rm -f -- "${probe}"
        segment_binding_failure "${check_phase}" canonical_derivative_bytes_mismatch
        return 1
    fi
    rm -f -- "${probe}"
    return 0
}

runtime_manifest_binding_failure() {
    local check_phase=${1:?runtime manifest check phase required}
    local reason=${2:?runtime manifest failure reason required}
    log "ERROR runtime provenance manifest binding failed phase=${check_phase} reason=${reason}"
    write_status phase=failed reason=runtime_provenance_manifest_binding_failed binding_check_phase="${check_phase}" binding_failure_reason="${reason}"
    return 1
}

verify_runtime_provenance_manifest_pin() {
    local check_phase=${1:?runtime manifest check phase required}
    local manifest="${ROOT}/provenance/runtime_snapshot/runtime_manifest.env"
    local current_sha
    if [[ ! "${RUNTIME_PROVENANCE_MANIFEST_SHA256:-}" =~ ^[0-9a-f]{64}$ ]]; then
        runtime_manifest_binding_failure "${check_phase}" invalid_expected_manifest_sha256
        return 1
    fi
    if [ ! -r "${manifest}" ]; then
        runtime_manifest_binding_failure "${check_phase}" manifest_missing
        return 1
    fi
    current_sha=$(sha256sum "${manifest}" | awk '{print $1}')
    if [ "${current_sha}" != "${RUNTIME_PROVENANCE_MANIFEST_SHA256}" ]; then
        runtime_manifest_binding_failure "${check_phase}" manifest_sha256_drift
        return 1
    fi
    return 0
}

validate_installed_runtime_contract() {
    local source_install=${1:?installed runtime root required}
    local wrapper schema_module extension symbol
    wrapper="${source_install}/bin/gstlal_inspiral_postcohspiir_online"
    schema_module="${source_install}/lib/python3.10/site-packages/gstlal_spiir/pipemodules/postcohtable/postcoh_table_def.py"
    extension="${source_install}/lib/python3.10/site-packages/gstlal_spiir/pipemodules/postcohtable/_postcohtable.so"

    if ! grep -Fq 'choices=("legacy-a107", "crashcar-a109")' "${wrapper}"; then
        log "ERROR installed online wrapper does not accept crashcar-a109: ${wrapper}"
        write_status phase=failed reason=installed_runtime_schema_contract_mismatch \
            installed_runtime="${source_install}" failed_artifact="${wrapper}"
        return 1
    fi
    if ! grep -Fq 'POSTCOH_SCHEMA_MODE_CRASHCAR_A109 = "crashcar-a109"' "${schema_module}"; then
        log "ERROR installed Postcoh schema module is not crashcar-a109: ${schema_module}"
        write_status phase=failed reason=installed_runtime_schema_contract_mismatch \
            installed_runtime="${source_install}" failed_artifact="${schema_module}"
        return 1
    fi
    for symbol in H1_LLR L1_LLR; do
        if ! strings "${extension}" | grep -Fx "${symbol}" >/dev/null; then
            log "ERROR installed Postcoh extension is missing ${symbol}: ${extension}"
            write_status phase=failed reason=installed_runtime_schema_contract_mismatch \
                installed_runtime="${source_install}" failed_artifact="${extension}" \
                missing_symbol="${symbol}"
            return 1
        fi
    done
    return 0
}

capture_runtime_manifest() {
    local source_head source_branch dirty dirty_count remote_url remote_tracking_head
    local source_install runtime_staging runtime_install runtime_files_manifest runtime_manifest_sha
    local runtime_snapshot_dir required_rel required_path wrapper_sha plugin_sha finalsink_sha postcohtable_sha

    verify_live_background_helper_pin runtime_manifest_capture || exit 2
    verify_segment_derivative_binding runtime_staging || exit 2
    runtime_snapshot_dir="${ROOT}/provenance/runtime_snapshot"
    if [ -e "${runtime_snapshot_dir}" ] || [ -L "${runtime_snapshot_dir}" ]; then
        log "ERROR immutable runtime snapshot path already exists ${runtime_snapshot_dir}"
        write_status phase=failed reason=runtime_snapshot_already_exists runtime_snapshot="${runtime_snapshot_dir}"
        exit 2
    fi
    mkdir "${runtime_snapshot_dir}"

    source_head=$(git -C "${SOURCE_ROOT}" rev-parse HEAD)
    source_branch=$(git -C "${SOURCE_ROOT}" symbolic-ref --quiet --short HEAD 2>/dev/null || printf 'DETACHED')
    remote_url=$(git -C "${SOURCE_ROOT}" remote get-url "${GITHUB_REMOTE}" 2>/dev/null || true)
    remote_tracking_head=$(git -C "${SOURCE_ROOT}" rev-parse --verify --quiet "refs/remotes/${GITHUB_REMOTE}/${GITHUB_BRANCH}" 2>/dev/null || true)
    [ -n "${remote_url}" ] || remote_url=UNAVAILABLE
    [ -n "${remote_tracking_head}" ] || remote_tracking_head=UNAVAILABLE

    dirty=$(git -C "${SOURCE_ROOT}" status --porcelain=v1 --untracked-files=no)
    if [ -n "${dirty}" ]; then
        dirty_count=$(printf '%s\n' "${dirty}" | awk 'NF {count++} END {print count+0}')
        printf '%s\n' "${dirty}" > "${runtime_snapshot_dir}/source_dirty_tracked.txt"
    else
        dirty_count=0
        : > "${runtime_snapshot_dir}/source_dirty_tracked.txt"
    fi

    source_install="${SOURCE_ROOT}/install_local"
    for required_rel in \
        bin/gstlal_inspiral_postcohspiir_online \
        lib/gstreamer-1.0/libgstcuda.so.0.0.0 \
        lib/python3.10/site-packages/gstlal_spiir/pipemodules/postcoh_finalsink.py \
        lib/python3.10/site-packages/gstlal_spiir/pipemodules/postcohtable/postcoh_table_def.py \
        lib/python3.10/site-packages/gstlal_spiir/pipemodules/postcohtable/_postcohtable.so; do
        required_path="${source_install}/${required_rel}"
        if [ ! -f "${required_path}" ]; then
            log "ERROR missing installed runtime artifact ${required_path}"
            write_status phase=failed reason=missing_installed_runtime_artifact source_head="${source_head}" missing_path="${required_path}"
            exit 2
        fi
    done
    if [ ! -x "${source_install}/bin/gstlal_inspiral_postcohspiir_online" ]; then
        log "ERROR installed online wrapper is not executable"
        write_status phase=failed reason=installed_wrapper_not_executable source_head="${source_head}"
        exit 2
    fi
    validate_installed_runtime_contract "${source_install}" || exit 2

    runtime_install="${CRASH_RUNTIME_ROOT}/install"
    runtime_staging="${CRASH_RUNTIME_ROOT}/.install.staging.$$"
    if [ -e "${runtime_install}" ] || [ -L "${runtime_install}" ]; then
        log "ERROR runtime install already exists; refusing to reuse or replace ${runtime_install}"
        write_status phase=failed reason=runtime_install_already_exists source_head="${source_head}" runtime_install="${runtime_install}"
        exit 2
    fi
    if [ -e "${runtime_staging}" ] || [ -L "${runtime_staging}" ]; then
        log "ERROR runtime staging path unexpectedly exists ${runtime_staging}"
        write_status phase=failed reason=runtime_staging_already_exists source_head="${source_head}" runtime_staging="${runtime_staging}"
        exit 2
    fi
    if ! cp -a "${source_install}" "${runtime_staging}"; then
        rm -rf -- "${runtime_staging}"
        log "ERROR failed to stage installed runtime"
        write_status phase=failed reason=runtime_stage_copy_failed source_head="${source_head}"
        exit 2
    fi
    mv "${runtime_staging}" "${runtime_install}"
    chmod -R a-w "${runtime_install}"

    runtime_files_manifest="${runtime_snapshot_dir}/runtime_files.sha256"
    (
        cd "${runtime_install}"
        find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
        find . -type l -printf 'SYMLINK %p -> %l\n' | LC_ALL=C sort
    ) > "${runtime_files_manifest}"
    runtime_manifest_sha=$(sha256sum "${runtime_files_manifest}" | awk '{print $1}')
    wrapper_sha=$(sha256sum "${runtime_install}/bin/gstlal_inspiral_postcohspiir_online" | awk '{print $1}')
    plugin_sha=$(sha256sum "${runtime_install}/lib/gstreamer-1.0/libgstcuda.so.0.0.0" | awk '{print $1}')
    finalsink_sha=$(sha256sum "${runtime_install}/lib/python3.10/site-packages/gstlal_spiir/pipemodules/postcoh_finalsink.py" | awk '{print $1}')
    postcohtable_sha=$(sha256sum "${runtime_install}/lib/python3.10/site-packages/gstlal_spiir/pipemodules/postcohtable/_postcohtable.so" | awk '{print $1}')

    printf '%s\n' "${source_head}" > "${runtime_snapshot_dir}/source_head.txt"
    {
        printf 'manifest_kind=passive_runtime_snapshot\n'
        printf 'acceptance_owner=external_verification_harness\n'
        printf 'source_root=%s\n' "${SOURCE_ROOT}"
        printf 'source_branch=%s\n' "${source_branch}"
        printf 'source_head=%s\n' "${source_head}"
        printf 'source_remote_name=%s\n' "${GITHUB_REMOTE}"
        printf 'source_remote_url=%s\n' "${remote_url}"
        printf 'source_remote_tracking_head_observed_without_fetch=%s\n' "${remote_tracking_head}"
        printf 'source_dirty_tracked_count=%s\n' "${dirty_count}"
        printf 'source_dirty_tracked_status=%s\n' "${runtime_snapshot_dir}/source_dirty_tracked.txt"
        printf 'runtime_install=%s\n' "${runtime_install}"
        printf 'runtime_files_manifest=%s\n' "${runtime_files_manifest}"
        printf 'runtime_files_manifest_sha256=%s\n' "${runtime_manifest_sha}"
        printf 'runtime_wrapper_sha256=%s\n' "${wrapper_sha}"
        printf 'runtime_plugin_sha256=%s\n' "${plugin_sha}"
        printf 'runtime_finalsink_sha256=%s\n' "${finalsink_sha}"
        printf 'runtime_postcohtable_sha256=%s\n' "${postcohtable_sha}"
        printf 'crashcar_segment_xml_absolute_path=%q\n' "${SEGMENT_XML_CANONICAL}"
        printf 'crashcar_segment_xml_sha256=%s\n' "${SEGMENT_XML_SHA256}"
        printf 'crashcar_segment_livetime_json_absolute_path=%q\n' "${SEGMENT_LIVETIME_JSON_CANONICAL}"
        printf 'crashcar_segment_livetime_json_sha256=%s\n' "${SEGMENT_LIVETIME_JSON_SHA256}"
        printf 'crashcar_segment_run_start=%s\n' "${SEGMENT_BINDING_RUN_START}"
        printf 'crashcar_segment_run_end=%s\n' "${SEGMENT_BINDING_RUN_END}"
        printf 'single_llr_model=wguo_gaussian_v1\n'
        printf 'legacy_dof_env_value=%s\n' "${DOF}"
        printf 'dof_authority=bankid_fixed_0_99_120_100_383_600\n'
        printf 'crashcar_live_background_helper_absolute_path=%q\n' "${CRASHCAR_LIVE_BACKGROUND_HELPER}"
        printf 'crashcar_live_background_helper_sha256=%s\n' "${CRASHCAR_LIVE_BACKGROUND_HELPER_SHA256}"
    } > "${runtime_snapshot_dir}/runtime_manifest.env"
    RUNTIME_PROVENANCE_MANIFEST_SHA256=$(sha256sum "${runtime_snapshot_dir}/runtime_manifest.env" | awk '{print $1}')
    chmod -R a-w "${runtime_snapshot_dir}"
    log "staged immutable run-root runtime snapshot ${runtime_install} manifest_sha256=${runtime_manifest_sha}"
    write_status phase=runtime_staged source_branch="${source_branch}" source_head="${source_head}" source_dirty_tracked_count="${dirty_count}" source_root="${SOURCE_ROOT}" runtime_install="${runtime_install}" runtime_manifest_sha256="${runtime_manifest_sha}" runtime_provenance_manifest_sha256="${RUNTIME_PROVENANCE_MANIFEST_SHA256}" acceptance_owner=external_verification_harness
}
validate_inputs() {
    local p worker bank bank_id ifo bank_file
    for p in \
        "${SEGMENT_XML}" \
        "${DETRSP_MAP}" \
        "${FRAME_CACHE}" \
        "${CRASH_SCRIPT_DIR}/dump_segment_livetime_csv.py" \
        "${CRASH_SCRIPT_DIR}/export_template_shape_map.py" \
        "${WGUO_BANK_STATS_DIR}"; do
        [ -e "${p}" ] || { log "ERROR missing input ${p}"; write_status phase=failed reason="missing ${p}"; exit 2; }
    done
    for worker in $(seq 0 $((WORKER_COUNT - 1))); do
        local jobno
        jobno=$(printf '%03d' "${worker}")
        if [ "${CRASHCAR_BG_ONLY_VALUE}" != "1" ] &&
           [ "${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" != "consumer" ]; then
            for suffix in 2w 1d 2h; do
                p="${NONINJ_STATS_LOC}/${jobno}/${jobno}_marginalized_stats_${suffix}.xml.gz"
                [ -e "${p}" ] || { log "ERROR missing input ${p}"; write_status phase=failed reason="missing ${p}"; exit 2; }
            done
        fi
        for bank in $(seq $((START_BANK + BANKS_PER_WORKER * worker)) $((START_BANK + BANKS_PER_WORKER * (worker + 1) - 1))); do
            bank_id=$(printf '%04d' "${bank}")
            for ifo in H1 L1 V1; do
                bank_file="${O3_BANK_DIR}/iir_${ifo}-GSTLAL_SPLIT_BANK_${bank_id}-a1-0-0.xml.gz"
                [ -e "${bank_file}" ] || { log "ERROR missing bank ${bank_file}"; write_status phase=failed reason="missing ${bank_file}"; exit 2; }
            done
        done
    done
    python3 "${CRASH_SCRIPT_DIR}/dump_segment_livetime_csv.py" \
        "${SEGMENT_XML}" \
        --run-start "${START_GPS}" \
        --run-end "${END_GPS}" \
        --output "${LIVETIME_CSV}" \
        > "${CONTROLLER_DIR}/dump_segment_livetime_csv.log" \
        2>&1
    [ -s "${LIVETIME_CSV}" ] || { log "ERROR canonical livetime JSON not created"; write_status phase=failed reason=livetime_json_missing; exit 2; }
    SEGMENT_XML_CANONICAL=$(readlink -f -- "${SEGMENT_XML}") || { log "ERROR cannot canonicalize segment XML path"; write_status phase=failed reason=segment_xml_path_unresolvable; exit 2; }
    SEGMENT_LIVETIME_JSON_CANONICAL=$(readlink -f -- "${LIVETIME_CSV}") || { log "ERROR cannot canonicalize segment derivative path"; write_status phase=failed reason=segment_derivative_path_unresolvable; exit 2; }
    SEGMENT_XML="${SEGMENT_XML_CANONICAL}"
    LIVETIME_CSV="${SEGMENT_LIVETIME_JSON_CANONICAL}"
    SEGMENT_XML_SHA256=$(sha256sum "${SEGMENT_XML_CANONICAL}" | awk '{print $1}')
    SEGMENT_LIVETIME_JSON_SHA256=$(sha256sum "${SEGMENT_LIVETIME_JSON_CANONICAL}" | awk '{print $1}')
    SEGMENT_BINDING_RUN_START="${START_GPS}"
    SEGMENT_BINDING_RUN_END="${END_GPS}"
    verify_segment_derivative_binding post_generation || exit 2
    bash -n "${SCRIPT_DIR}/crashcar_pipeline.sh"
    bash -n "${SCRIPT_DIR}/crashcar_sbatch.sh"
    write_status phase=inputs_validated segment_xml="${SEGMENT_XML}" crashcar_segment_livetime_json="${LIVETIME_CSV}" crashcar_segment_livetime_sha256="$(sha256sum "${LIVETIME_CSV}" | awk '{print $1}')" final_far_route_authority=row_ifos_exact_mask
}

export_template_map() {
    module load gcc/13.3.0 scipy-bundle/2024.05 >/dev/null 2>&1 || {
        log "ERROR failed to load controlled A_eff exporter runtime"
        write_status phase=failed reason=template_shape_runtime_module_load_failed
        exit 2
    }
    local template_map="${ARTIFACTS}/crashcar_template_shape_map.csv"
    local template_map_python template_map_python_version template_map_packages
    template_map_python=$(command -v python3) || {
        log "ERROR controlled A_eff exporter python3 is unavailable"
        write_status phase=failed reason=template_shape_python_missing
        exit 2
    }
    template_map_python_version=$("${template_map_python}" -c 'import sys; print(sys.version.split()[0])') || {
        log "ERROR cannot query controlled A_eff exporter Python"
        write_status phase=failed reason=template_shape_python_probe_failed
        exit 2
    }
    template_map_packages=$("${template_map_python}" -c 'import numpy,pandas; print("numpy="+numpy.__version__+",pandas="+pandas.__version__)') || {
        log "ERROR controlled A_eff exporter requires NumPy and pandas"
        write_status phase=failed reason=template_shape_python_dependencies_missing
        exit 2
    }
    "${template_map_python}" "${CRASH_SCRIPT_DIR}/export_template_shape_map.py" \
        --bank-stats-dir "${WGUO_BANK_STATS_DIR}" \
        --output "${template_map}" \
        --ifos H1,L1 \
        --start-bank 0 \
        --end-bank 383 \
        > "${CONTROLLER_DIR}/export_template_shape_map.log" \
        2>&1
    [ -s "${template_map}" ] || { log "ERROR template map not created"; write_status phase=failed reason=template_shape_map_missing; exit 2; }
    write_status phase=template_shape_map_ready template_shape_map="${template_map}" template_map_python="${template_map_python}" template_map_python_version="${template_map_python_version}" template_map_packages="${template_map_packages}"
    log "template shape map ready ${template_map} python=${template_map_python} version=${template_map_python_version} ${template_map_packages}"
}

prepare_schema4_provenance() {
    local provenance_dir="${ROOT}/provenance/schema4"
    local runtime_env="${ROOT}/provenance/runtime_snapshot/runtime_manifest.env"
    local runtime_files_sha current_sha
    local template_map="${ARTIFACTS}/crashcar_template_shape_map.csv"
    verify_live_background_helper_pin schema4_provenance || exit 2
    if [ -e "${provenance_dir}" ] || [ -L "${provenance_dir}" ]; then
        log "ERROR schema4 provenance path already exists ${provenance_dir}"
        write_status phase=failed reason=schema4_provenance_already_exists
        exit 2
    fi
    mkdir "${provenance_dir}"
    SCHEMA4_RUN_NAMESPACE_PATH="${provenance_dir}/run_namespace.txt"
    SCHEMA4_SOURCE_MANIFEST_PATH="${provenance_dir}/source_manifest.env"
    printf 'run_root=%s\n' "$(readlink -f -- "${ROOT}")" \
        > "${SCHEMA4_RUN_NAMESPACE_PATH}"
    {
        printf 'manifest_kind=crashcar_source_identity_v1\n'
        grep -E '^source_(branch|head|remote_name|remote_url|remote_tracking_head_observed_without_fetch|dirty_tracked_count)=' \
            "${runtime_env}"
        printf 'source_dirty_tracked_sha256=%s\n' \
            "$(sha256sum "${ROOT}/provenance/runtime_snapshot/source_dirty_tracked.txt" | awk '{print $1}')"
    } > "${SCHEMA4_SOURCE_MANIFEST_PATH}"
    chmod 0444 "${SCHEMA4_RUN_NAMESPACE_PATH}" "${SCHEMA4_SOURCE_MANIFEST_PATH}"

    SCHEMA4_RUN_NAMESPACE_SHA256=$(sha256sum "${SCHEMA4_RUN_NAMESPACE_PATH}" | awk '{print $1}')
    SCHEMA4_SOURCE_MANIFEST_SHA256=$(sha256sum "${SCHEMA4_SOURCE_MANIFEST_PATH}" | awk '{print $1}')
    runtime_files_sha=$(awk -F= '$1=="runtime_files_manifest_sha256" {print $2}' "${runtime_env}")
    if [[ ! "${runtime_files_sha}" =~ ^[0-9a-f]{64}$ ]]; then
        log "ERROR runtime artifact manifest digest is invalid"
        write_status phase=failed reason=schema4_runtime_manifest_sha_invalid
        exit 2
    fi
    SCHEMA4_RUNTIME_MANIFEST_SHA256=${runtime_files_sha}
    SCHEMA4_CONFIG_SHA256=$(sha256sum "${CONFIG_FILE}" | awk '{print $1}')
    SCHEMA4_TEMPLATE_SHAPE_MAP_SHA256=$(sha256sum "${template_map}" | awk '{print $1}')

    for current_sha in \
        "${SCHEMA4_RUN_NAMESPACE_SHA256}" \
        "${SCHEMA4_SOURCE_MANIFEST_SHA256}" \
        "${SCHEMA4_RUNTIME_MANIFEST_SHA256}" \
        "${SCHEMA4_CONFIG_SHA256}" \
        "${SCHEMA4_TEMPLATE_SHAPE_MAP_SHA256}" \
        "${SEGMENT_XML_SHA256}" \
        "${SEGMENT_LIVETIME_JSON_SHA256}"; do
        if [[ ! "${current_sha}" =~ ^[0-9a-f]{64}$ ]]; then
            log "ERROR schema4 provenance digest is not lowercase64"
            write_status phase=failed reason=schema4_provenance_digest_invalid
            exit 2
        fi
    done

    write_status \
        phase=schema4_provenance_ready \
        schema4_run_namespace_sha256="${SCHEMA4_RUN_NAMESPACE_SHA256}" \
        schema4_source_manifest_sha256="${SCHEMA4_SOURCE_MANIFEST_SHA256}" \
        schema4_runtime_manifest_sha256="${SCHEMA4_RUNTIME_MANIFEST_SHA256}" \
        schema4_config_sha256="${SCHEMA4_CONFIG_SHA256}" \
        schema4_template_shape_map_sha256="${SCHEMA4_TEMPLATE_SHAPE_MAP_SHA256}" \
        live_background_role="${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" \
        live_background_producer_root="${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" \
        live_background_helper="${CRASHCAR_LIVE_BACKGROUND_HELPER}" \
        live_background_helper_sha256="${CRASHCAR_LIVE_BACKGROUND_HELPER_SHA256}"
}

job_snapshot() {
    local job=$1
    local sacct_state queue_state
    sacct_state=$(sacct -j "${job}" -P -n --format=JobIDRaw,State,ExitCode,Elapsed,NodeList 2>/dev/null | paste -sd ',' - || true)
    queue_state=$(squeue -j "${job}" -h -o '%i:%T:%M:%R:%j' 2>/dev/null | paste -sd ',' - || true)
    [ -n "${sacct_state}" ] || sacct_state=none
    [ -n "${queue_state}" ] || queue_state=none
    printf '%s@@@%s\n' "${sacct_state}" "${queue_state}"
}

write_final_report() {
    local phase=$1 job=$2 sacct_text=$3 detail_json=$4
    REPORT="${REPORT}" ROOT="${ROOT}" RUN_DIR="${RUN_DIR}" ARTIFACTS="${ARTIFACTS}" SOURCE_ROOT="${SOURCE_ROOT}" \
        START_GPS="${START_GPS}" END_GPS="${END_GPS}" DURATION="${DURATION}" \
        BACKGROUND_ACCUMULATION="${BACKGROUND_ACCUMULATION}" BACKGROUND_UPDATE="${BACKGROUND_UPDATE}" \
        ZEROLAG_UPDATE="${ZEROLAG_UPDATE}" \
        COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE="${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE}" \
        FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE="${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE}" \
        FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE="${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE}" \
        WORKER_COUNT="${WORKER_COUNT}" BANKS_PER_WORKER="${BANKS_PER_WORKER}" \
        SINGLE_ONLY_SECONDS="${SINGLE_ONLY_SECONDS}" SINGLE_ONLY_FRACTION="${SINGLE_ONLY_FRACTION}" \
        HL_UNION_FRACTION="${HL_UNION_FRACTION}" H_ONLY_SECONDS="${H_ONLY_SECONDS}" \
        L_ONLY_SECONDS="${L_ONLY_SECONDS}" HL_SECONDS="${HL_SECONDS}" HL_NONE_SECONDS="${HL_NONE_SECONDS}" \
        FIRST3_H_ONLY_SECONDS="${FIRST3_H_ONLY_SECONDS}" FIRST3_L_ONLY_SECONDS="${FIRST3_L_ONLY_SECONDS}" \
        FIRST3_HL_SECONDS="${FIRST3_HL_SECONDS}" FIRST3_HL_NONE_SECONDS="${FIRST3_HL_NONE_SECONDS}" \
        TAIL_LOG_FAR="${TAIL_LOG_FAR}" FAR_FIT_BOUNDARY="${FAR_FIT_BOUNDARY}" \
        CRASHCAR_LOG10_FAR_THRESHOLD="${CRASHCAR_LOG10_FAR_THRESHOLD:-90}" \
        SINGLE_BACKGROUND_MODE_VALUE="${SINGLE_BACKGROUND_MODE_VALUE}" \
        CRASHCAR_BG_ONLY_VALUE="${CRASHCAR_BG_ONLY_VALUE}" \
        CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE="${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" \
        CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE="${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" \
        CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE="${CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE}" \
        python3 - "${phase}" "${job}" "${sacct_text}" "${detail_json}" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

phase, job, sacct_text, detail_json = sys.argv[1:5]
run_dir = Path(os.environ["RUN_DIR"])
payload = {
    "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "phase": phase,
    "slurm_job": job,
    "root": os.environ["ROOT"],
    "run_dir": os.environ["RUN_DIR"],
    "source_root": os.environ["SOURCE_ROOT"],
    "gps_start": int(os.environ["START_GPS"]),
    "gps_end": int(os.environ["END_GPS"]),
    "duration": int(os.environ["DURATION"]),
    "background_accumulation_seconds": float(os.environ["BACKGROUND_ACCUMULATION"]),
    "background_update_seconds": float(os.environ["BACKGROUND_UPDATE"]),
    "zerolag_update_seconds": float(os.environ["ZEROLAG_UPDATE"]),
    "cohfar_accumbackground_snapshot_interval_seconds": int(
        os.environ["COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE"]),
    "finalsink_fapupdater_collect_walltime":
        os.environ["FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE"],
    "finalsink_fapupdater_collect_walltime_source":
        os.environ["FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE"],
    "tail_log_FAR": (
        float(os.environ["TAIL_LOG_FAR"])
        if os.environ.get("TAIL_LOG_FAR") else None),
    "tail_FAR": float(os.environ["FAR_FIT_BOUNDARY"]),
    "worker_count": int(os.environ["WORKER_COUNT"]),
    "banks_per_worker": int(os.environ["BANKS_PER_WORKER"]),
    "single_only_seconds": float(os.environ["SINGLE_ONLY_SECONDS"] or 0.0),
    "single_only_fraction": (
        float(os.environ["SINGLE_ONLY_FRACTION"])
        if os.environ.get("SINGLE_ONLY_FRACTION") else None),
    "hl_union_fraction": (
        float(os.environ["HL_UNION_FRACTION"])
        if os.environ.get("HL_UNION_FRACTION") else None),
    "h_only_seconds": float(os.environ["H_ONLY_SECONDS"] or 0.0),
    "l_only_seconds": float(os.environ["L_ONLY_SECONDS"] or 0.0),
    "hl_seconds": float(os.environ["HL_SECONDS"] or 0.0),
    "hl_none_seconds": float(os.environ["HL_NONE_SECONDS"] or 0.0),
    "first3_h_only_seconds": float(os.environ["FIRST3_H_ONLY_SECONDS"] or 0.0),
    "first3_l_only_seconds": float(os.environ["FIRST3_L_ONLY_SECONDS"] or 0.0),
    "first3_hl_seconds": float(os.environ["FIRST3_HL_SECONDS"] or 0.0),
    "first3_hl_none_seconds": float(os.environ["FIRST3_HL_NONE_SECONDS"] or 0.0),
    "crashcar_log10_far_threshold": float(os.environ["CRASHCAR_LOG10_FAR_THRESHOLD"]),
    "crashcar_background_required_seconds": float(os.environ["CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE"]),
    "single_background_mode": os.environ["SINGLE_BACKGROUND_MODE_VALUE"],
    "background_only": os.environ["CRASHCAR_BG_ONLY_VALUE"] == "1",
    "live_background_role": os.environ["CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE"] or None,
    "live_background_producer_root": os.environ["CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE"] or None,
    "sacct": sacct_text,
    "detail": json.loads(detail_json),
}

def records(pattern):
    return [
        {"path": str(path), "size": path.stat().st_size}
        for path in sorted(run_dir.glob(pattern))
        if path.is_file()
    ]

payload["single_background_files"] = records(
    "[0-9][0-9][0-9]/single_background.json")
payload["zerolag_files"] = records(
    "[0-9][0-9][0-9]/*_zerolag_*.xml*")
payload["marginalized_stats_files"] = records(
    "[0-9][0-9][0-9]/*_marginalized_stats_*.xml*")
payload["single_detail_files"] = records(
    "crashcar_singlefar_detail_worker*.csv")
Path(os.environ["REPORT"]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
}

submit_job() {
    local template_map="${ARTIFACTS}/crashcar_template_shape_map.csv"
    verify_segment_derivative_binding pre_slurm_submit || exit 2
    verify_runtime_provenance_manifest_pin pre_slurm_submit || exit 2
    cd "${RUN_DIR}"
    local job
    # Slurm uses commas as --export token separators.  Keep the complete
    # three-window value in the parent environment and export only its name.
    export FINALSINK_FAPUPDATER_COLLECT_WALLTIME="${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE}"
    local sbatch_args=(
        --parsable
        --job-name="${SLURM_JOB_NAME}"
        --mem="${SLURM_MEM}"
        --cpus-per-task="${SLURM_CPUS_PER_TASK}"
        --array="0-$((WORKER_COUNT - 1))"
        --export=ALL,TOP_RUN_ROOT="${ROOT}",RUN_DIR="${RUN_DIR}",CRASH_ROOT="${CRASH_RUNTIME_ROOT}",CRASHCAR_RUNTIME_PROVENANCE_MANIFEST_SHA256="${RUNTIME_PROVENANCE_MANIFEST_SHA256}",CRASHCAR_CURRENT_WORKER_COUNT="${WORKER_COUNT}",CRASHCAR_CURRENT_BANKS_PER_WORKER="${BANKS_PER_WORKER}",CRASHCAR_CURRENT_START_BANK="${START_BANK}",CRASHCAR_CURRENT_RUN_NAMESPACE_SHA256="${SCHEMA4_RUN_NAMESPACE_SHA256}",CRASHCAR_CURRENT_SOURCE_MANIFEST_SHA256="${SCHEMA4_SOURCE_MANIFEST_SHA256}",CRASHCAR_CURRENT_RUNTIME_MANIFEST_SHA256="${SCHEMA4_RUNTIME_MANIFEST_SHA256}",CRASHCAR_CURRENT_CONFIG_SHA256="${SCHEMA4_CONFIG_SHA256}",CRASHCAR_CURRENT_SEGMENT_XML_SHA256="${SEGMENT_XML_SHA256}",CRASHCAR_CURRENT_SEGMENT_CANONICAL_SHA256="${SEGMENT_LIVETIME_JSON_SHA256}",CRASHCAR_CURRENT_TEMPLATE_SHAPE_MAP_SHA256="${SCHEMA4_TEMPLATE_SHAPE_MAP_SHA256}",CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE="${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}",CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROOT="${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}",CRASHCAR_LIVE_SINGLE_BINDING_JSON="${CONTROLLER_DIR}/live_single_binding.input.json",CRASHCAR_LIVE_BG_ORIGIN_GPS="${INJECTION_BG_START_GPS}",WGUO_O3A_INJECTION_MODE="${INJECTION_PIPELINE_MODE}",WGUO_O3A_INJECTION_FILE="${INJECTION_FILE}",WGUO_O3A_START_GPS="${START_GPS}",WGUO_O3A_END_GPS="${END_GPS}",WGUO_O3A_DETRSP_MAP="${DETRSP_MAP}",WGUO_O3A_FRAME_CACHE="${FRAME_CACHE}",WGUO_O3A_NONINJ_STATS_LOC="${NONINJ_STATS_LOC}",WGUO_O3A_BANK_DIR="${O3_BANK_DIR}",WGUO_O3A_BANKS_PER_GROUP="${BANKS_PER_WORKER}",WGUO_O3A_START_BANK="${START_BANK}",WGUO_O3A_SNAPSHOT_INTERVAL="${ZEROLAG_UPDATE}",DOF="${DOF}",CRASHCAR_DOF="${DOF}",BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION}",FORMAL_BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION}",CRASHCAR_BACKGROUND_REQUIRED_SECONDS="${CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE}",BACKGROUND_UPDATE_TRIGGER_SECONDS="${BACKGROUND_UPDATE}",COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS="${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE}",COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS="${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS_VALUE}",FINALSINK_FAPUPDATER_INTERVAL_SECONDS="${FINALSINK_FAPUPDATER_INTERVAL_SECONDS_VALUE}",FINALSINK_FAPUPDATER_COLLECT_WALLTIME,ZEROLAG_SNAPSHOT_INTERVAL_SECONDS="${ZEROLAG_UPDATE}",CRASHCAR_SNAPSHOT_INTERVAL_SECONDS="${ZEROLAG_UPDATE}",CRASHCAR_LOG10_FAR_THRESHOLD="${CRASHCAR_LOG10_FAR_THRESHOLD:-90}",TAIL_LOG_FAR="${TAIL_LOG_FAR}",CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME="${template_map}",CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP="${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}",CRASHCAR_CODE_VERSION="${CRASHCAR_CODE_VERSION}",WGUO_O3A_SEGMENT_XML="${SEGMENT_XML}",SEGMENT_XML="${SEGMENT_XML}",SINGLE_SEGMENT_XML="${SEGMENT_XML}",SINGLE_BACKGROUND_MODE="${SINGLE_BACKGROUND_MODE_VALUE}",CRASHCAR_SINGLE_BACKGROUND_MODE="${SINGLE_BACKGROUND_MODE_VALUE}",CRASHCAR_BG_ONLY="${CRASHCAR_BG_ONLY_VALUE}",CRASHCAR_SEGMENT_LIVETIME_CSV="${LIVETIME_CSV}"
        --chdir="${RUN_DIR}"
    )
    local sbatch_export_bound=0 sbatch_arg_index
    for sbatch_arg_index in "${!sbatch_args[@]}"; do
        if [[ "${sbatch_args[sbatch_arg_index]}" == --export=* ]]; then
            sbatch_args[sbatch_arg_index]+=",SNR_series_logFAR_threshold=${SNR_SERIES_LOG_FAR_THRESHOLD}"
            sbatch_export_bound=1
            break
        fi
    done
    if [ "${sbatch_export_bound}" != "1" ]; then
        printf "crashcar_controller: internal sbatch export binding is missing\n" >&2
        return 2
    fi
    if [ -n "${SLURM_GRES}" ]; then
        sbatch_args+=(--gres="${SLURM_GRES}")
    fi
    if [ -n "${SLURM_PARTITION}" ]; then
        sbatch_args+=(--partition="${SLURM_PARTITION}")
    fi
    if [ -n "${SLURM_TIME}" ]; then
        sbatch_args+=(--time="${SLURM_TIME}")
    fi
    sbatch_args+=("${SCRIPT_DIR}/crashcar_sbatch.sh")
    job=$(sbatch "${sbatch_args[@]}")
    printf '%s\n' "${job}" > "${CONTROLLER_DIR}/job_id.txt"
    write_status phase=slurm_submitted job_id="${job}" run_dir="${RUN_DIR}" worker_count="${WORKER_COUNT}" banks_per_worker="${BANKS_PER_WORKER}" single_llr_model=wguo_gaussian_v1 legacy_dof_env_value="${DOF}" dof_authority=bankid_fixed_0_99_120_100_383_600 background_accumulation_seconds="${BACKGROUND_ACCUMULATION}" background_update_seconds="${BACKGROUND_UPDATE}" zerolag_update_seconds="${ZEROLAG_UPDATE}" cohfar_assignfar_refresh_interval_seconds="${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS_VALUE}" finalsink_fapupdater_interval_seconds="${FINALSINK_FAPUPDATER_INTERVAL_SECONDS_VALUE}" finalsink_fapupdater_collect_walltime="${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_VALUE}" finalsink_fapupdater_collect_walltime_source="${FINALSINK_FAPUPDATER_COLLECT_WALLTIME_SOURCE_VALUE}" cohfar_accumbackground_snapshot_interval_seconds="${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS_VALUE}" tail_log_FAR="${TAIL_LOG_FAR}" tail_FAR="${FAR_FIT_BOUNDARY}" SNR_series_logFAR_threshold="${SNR_SERIES_LOG_FAR_THRESHOLD}" injection_mode="${INJECTION_MODE}" injection_pipeline_mode="${INJECTION_PIPELINE_MODE}" single_only_fraction="${SINGLE_ONLY_FRACTION}" hl_union_fraction="${HL_UNION_FRACTION}"
    log "submitted Slurm job=${job} workers=${WORKER_COUNT} banks_per_worker=${BANKS_PER_WORKER} gps=${START_GPS}-${END_GPS}"
}

monitor_job() {
    local job=$1
    while true; do
        local snapshot sacct_state squeue_state zerolag stats detail
        snapshot=$(job_snapshot "${job}")
        sacct_state=${snapshot%%@@@*}
        squeue_state=${snapshot#*@@@}
        zerolag=$(count_zerolag)
        stats=$(count_stats)
        detail=$(detail_summary_json)
        write_status phase=slurm_running job_id="${job}" squeue="${squeue_state}" sacct="${sacct_state}" zerolag_count="${zerolag}" stats_count="${stats}" detail_summary="${detail}"
        log "job=${job} squeue=${squeue_state} sacct=${sacct_state} zerolag=${zerolag} stats=${stats}"
        if [ "${squeue_state}" = "none" ]; then
            local phase=slurm_completed
            if grep -Eq 'FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL|PREEMPTED|BOOT_FAIL|DEADLINE|REVOKED' <<<"${sacct_state}"; then
                phase=failed_slurm
                write_status phase="${phase}" job_id="${job}" sacct="${sacct_state}" detail_summary="${detail}"
                write_final_report "${phase}" "${job}" "${sacct_state}" "${detail}"
                exit 3
            fi
            write_status phase=completed job_id="${job}" sacct="${sacct_state}" detail_summary="${detail}" final_report="${REPORT}" single_background_mode="${SINGLE_BACKGROUND_MODE_VALUE}" background_only="${CRASHCAR_BG_ONLY_VALUE}" live_background_role="${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" live_background_producer_root="${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}"
            write_final_report completed "${job}" "${sacct_state}" "${detail}"
            log "completed; report=${REPORT}; parity and completeness are external acceptance work"
            exit 0
        fi
        sleep 300
    done
}

main() {
    pin_live_background_helper || exit 2
    cat > "${REGISTRY}" <<EOF
| session | server | working directory | model | role | task | start time UTC | latest known status | output/report |
|---|---|---|---|---|---|---|---|---|
| ${TMUX_SESSION} | ozstar | ${ROOT} | GPT-5 Codex controller | main controller supervised shell | crashcar run from scripts/crashcar.env | $(date -u +%FT%TZ) | starting | ${STATUS} |
EOF

    write_status \
        phase=starting \
        server=ozstar \
        tmux="${TMUX_SESSION}" \
        role="main controller supervised shell" \
        model="GPT-5 Codex controller" \
        task="crashcar run from scripts/crashcar.env" \
        start_gps="${START_GPS}" \
        end_gps="${END_GPS}" \
        duration="${DURATION}" \
        background_accumulation_seconds="${BACKGROUND_ACCUMULATION}" \
        background_update_seconds="${BACKGROUND_UPDATE}" \
        zerolag_update_seconds="${ZEROLAG_UPDATE}" \
        tail_log_FAR="${TAIL_LOG_FAR}" \
        SNR_series_logFAR_threshold="${SNR_SERIES_LOG_FAR_THRESHOLD}" \
        tail_FAR="${FAR_FIT_BOUNDARY}" \
        injection_mode="${INJECTION_MODE}" \
        injection_pipeline_mode="${INJECTION_PIPELINE_MODE}" \
        single_background_mode="${SINGLE_BACKGROUND_MODE_VALUE}" \
        background_only="${CRASHCAR_BG_ONLY_VALUE}" \
        live_background_role="${CRASHCAR_LIVE_BACKGROUND_ROLE_VALUE}" \
        live_background_producer_root="${CRASHCAR_LIVE_BACKGROUND_ROOT_VALUE}" \
        crashcar_background_required_seconds="${CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE}" \
        injection_bg_start_gps="${INJECTION_BG_START_GPS}" \
        injection_bg_end_gps="${INJECTION_BG_END_GPS}" \
        injection_bg_duration_seconds="${INJECTION_BG_DURATION_SECONDS}" \
        worker_count="${WORKER_COUNT}" \
        banks_per_worker="${BANKS_PER_WORKER}" \
        start_bank="${START_BANK}" \
        single_only_fraction="${SINGLE_ONLY_FRACTION}" \
        hl_union_fraction="${HL_UNION_FRACTION}" \
        first3_h_only_seconds="${FIRST3_H_ONLY_SECONDS}" \
        first3_l_only_seconds="${FIRST3_L_ONLY_SECONDS}" \
        first3_hl_seconds="${FIRST3_HL_SECONDS}" \
        first3_hl_none_seconds="${FIRST3_HL_NONE_SECONDS}"
    log "controller start root=${ROOT} gps=${START_GPS}-${END_GPS}"
    validate_inputs
    capture_runtime_manifest
    cp "${ROOT}/provenance/runtime_snapshot/runtime_manifest.env" "${CONTROLLER_DIR}/runtime_manifest.env"
    cp "${ROOT}/provenance/runtime_snapshot/source_head.txt" "${CONTROLLER_DIR}/source_head.txt"
    export_template_map
    prepare_schema4_provenance
    prepare_live_background_contract
    submit_job
    monitor_job "$(cat "${CONTROLLER_DIR}/job_id.txt")"
}

main "$@"
