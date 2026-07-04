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
LIVETIME_CSV=${livetime_csv:-${LIVETIME_CSV:-"${ARTIFACTS}/H1L1V1_SEGMENTS_${START_GPS}_${DURATION}_livetime.csv"}}
NONINJ_STATS_LOC=${noninj_stats_loc:-${NONINJ_STATS_LOC:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}}
O3_BANK_DIR=${bank_file:-${o3_bank_dir:-${O3_BANK_DIR:-}}}
: "${O3_BANK_DIR:?bank_file required in ${CONFIG_FILE}}"
WGUO_BANK_STATS_DIR=${wguo_bank_stats_dir:-${WGUO_BANK_STATS_DIR:-/fred/oz016/wguo/packages/spiir/src/spiir/search/bank_dofs}}
DEFAULT_SHAPE_DOF=${default_shape_dof:-${DEFAULT_SHAPE_DOF:-74.30962572260326}}
NOISE_BETA=${noise_beta:-${NOISE_BETA:--1.0}}
RANK_OFFSET=${rank_offset:-${RANK_OFFSET:-0.0}}
TAIL_LOG_FAR=${tail_log_FAR:-${tai_log_FAR:-${TAIL_LOG_FAR:-}}}
if [ -n "${TAIL_LOG_FAR}" ]; then
    FAR_FIT_BOUNDARY=$(python3 - "${TAIL_LOG_FAR}" <<'PY'
import math
import sys
print("{:.17g}".format(math.pow(10.0, float(sys.argv[1]))))
PY
)
else
    FAR_FIT_BOUNDARY=${tail_FAR:-${far_fit_boundary:-${FAR_FIT_BOUNDARY:-0.01}}}
    TAIL_LOG_FAR=$(python3 - "${FAR_FIT_BOUNDARY}" <<'PY'
import math
import sys
value = float(sys.argv[1])
print("{:.17g}".format(math.log10(value))) if value > 0 else print("")
PY
)
fi
SNR_SERIES_LOG_FAR_THRESHOLD=${SNR_series_logFAR_threshold:-${snr_series_logFAR_threshold:-${SNR_SERIES_LOG_FAR_THRESHOLD:--4}}}
CRASHCAR_CODE_VERSION=${crashcar_code_version:-${CRASHCAR_CODE_VERSION:-"spiir-crashcar-${GITHUB_BRANCH}"}}
SLURM_JOB_NAME=${slurm_job_name:-${SLURM_JOB_NAME:-crashcar}}
SLURM_PARTITION=${slurm_partition:-${SLURM_PARTITION:-}}
SLURM_TIME=${slurm_time:-${SLURM_TIME:-7-00:00:00}}
SLURM_MEM=${slurm_mem:-${SLURM_MEM:-64g}}
SLURM_CPUS_PER_TASK=${slurm_cpus_per_task:-${SLURM_CPUS_PER_TASK:-4}}
SLURM_GRES=${slurm_gres:-${SLURM_GRES:-gpu:1}}
TMUX_SESSION=${tmux_session:-${TMUX_SESSION:-codex1}}
CRASHCAR_LOG10_FAR_THRESHOLD=${crashcar_log10_far_threshold:-${CRASHCAR_LOG10_FAR_THRESHOLD:-90}}
CRASHCAR_PRESERVE_TABLE_SINGLE_FAR=${crashcar_preserve_table_single_far:-${CRASHCAR_PRESERVE_TABLE_SINGLE_FAR:-0}}
CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR=${crashcar_finalsink_preserve_table_single_far:-${CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR:-1}}
CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP=${crashcar_require_template_shape_map:-${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}}
CRASHCAR_SINGLE_LEDGER_FINAL_UPDATE=${crashcar_single_ledger_final_update:-${CRASHCAR_SINGLE_LEDGER_FINAL_UPDATE:-0}}

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
SINGLE_FROZEN_BACKGROUND_JSON_VALUE=${single_frozen_background_json:-${SINGLE_FROZEN_BACKGROUND_JSON:-}}
SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE=${single_frozen_background_run_dir:-${SINGLE_FROZEN_BACKGROUND_RUN_DIR:-}}
SINGLE_FROZEN_BACKGROUND_ID_VALUE=${single_frozen_background_id:-${SINGLE_FROZEN_BACKGROUND_ID:-BG-FROZEN}}
SINGLE_FROZEN_BACKGROUND_SOURCE_VALUE=${single_frozen_background_source:-${SINGLE_FROZEN_BACKGROUND_SOURCE:-}}
SINGLE_INPUT_KIND_VALUE=${single_input_kind:-${SINGLE_INPUT_KIND:-crashcarcsv}}
FINAL_SINGLE_INPUT_KIND_VALUE=${final_single_input_kind:-${FINAL_SINGLE_INPUT_KIND:-crashcarcsv}}
PATCH_ZEROLAG_SINGLE_FAR_VALUE=${patch_zerolag_single_far:-${PATCH_ZEROLAG_SINGLE_FAR:-1}}
PATCH_ZEROLAG_SINGLE_FAR_COLUMN_VALUE=${patch_zerolag_single_far_column:-${PATCH_ZEROLAG_SINGLE_FAR_COLUMN:-direct_far}}
PATCH_ZEROLAG_SINGLE_SNR_SERIES_VALUE=${patch_zerolag_single_snr_series:-${PATCH_ZEROLAG_SINGLE_SNR_SERIES:-1}}
CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE=${crashcar_background_required_seconds:-${CRASHCAR_BACKGROUND_REQUIRED_SECONDS:-${BACKGROUND_ACCUMULATION}}}
if [ -n "${crashcar_build_last_bg_artifacts:-${CRASHCAR_BUILD_LAST_BG_ARTIFACTS:-}}" ]; then
    CRASHCAR_BUILD_LAST_BG_ARTIFACTS=${crashcar_build_last_bg_artifacts:-${CRASHCAR_BUILD_LAST_BG_ARTIFACTS:-}}
elif [ "${INJECTION_MODE}" = "True" ]; then
    CRASHCAR_BUILD_LAST_BG_ARTIFACTS=0
else
    CRASHCAR_BUILD_LAST_BG_ARTIFACTS=1
fi
case "${SINGLE_BACKGROUND_MODE_VALUE}" in
    rolling|frozen) ;;
    *)
        printf 'crashcar_controller: invalid single_background_mode=%s; expected rolling or frozen\n' \
            "${SINGLE_BACKGROUND_MODE_VALUE}" >&2
        exit 2
        ;;
esac
if [ "${INJECTION_MODE}" = "True" ] && [ "${SINGLE_BACKGROUND_MODE_VALUE}" != "frozen" ]; then
    printf 'crashcar_controller: injection_mode=True requires single_background_mode=frozen\n' >&2
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
SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE=${single_output_active_ifo_schedule:-${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE:-}}
SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE=${single_output_active_ifo_schedule_file:-${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE:-"${ARTIFACTS}/single_output_active_ifo_schedule.txt"}}

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

run_summary_json() {
    RUN_DIR="${RUN_DIR}" python3 - <<'PY'
import csv
import json
import math
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
payload = {
    "exists": False,
    "files": [],
    "rows": 0,
    "non_boundary_rows": 0,
    "chunk_boundary_rows": 0,
    "candidate_counts": {"H1": 0, "L1": 0},
    "min_end_time": None,
    "max_end_time": None,
}
for path in sorted(run_dir.glob("[0-9][0-9][0-9]/*_single_triggers.csv")):
    item = {
        "path": str(path),
        "rows": 0,
        "non_boundary_rows": 0,
        "chunk_boundary_rows": 0,
        "candidate_counts": {"H1": 0, "L1": 0},
        "min_end_time": None,
        "max_end_time": None,
    }
    try:
        handle = path.open(newline="", encoding="utf-8")
    except FileNotFoundError:
        continue
    with handle:
        for row in csv.DictReader(handle):
            item["rows"] += 1
            payload["rows"] += 1
            try:
                t = int(float(row.get("end_time") or "nan"))
            except ValueError:
                t = None
            if t is not None:
                for target in (item, payload):
                    if target["min_end_time"] is None or t < target["min_end_time"]:
                        target["min_end_time"] = t
                    if target["max_end_time"] is None or t > target["max_end_time"]:
                        target["max_end_time"] = t
            if (row.get("source_kind") or "").strip() == "chunk_boundary":
                item["chunk_boundary_rows"] += 1
                payload["chunk_boundary_rows"] += 1
                continue
            item["non_boundary_rows"] += 1
            payload["non_boundary_rows"] += 1
            for ifo in ("H1", "L1"):
                try:
                    snr = float(row.get(f"snglsnr_{ifo}") or "nan")
                    chisq = float(row.get(f"chisq_{ifo}") or "nan")
                except ValueError:
                    continue
                if math.isfinite(snr) and math.isfinite(chisq) and snr >= 4.0 and chisq > 0.0:
                    item["candidate_counts"][ifo] += 1
                    payload["candidate_counts"][ifo] += 1
    payload["files"].append(item)
payload["exists"] = bool(payload["files"])
print(json.dumps(payload, sort_keys=True))
PY
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
    "finite_direct_far_rows": 0,
    "ready_window_rows": 0,
    "min_direct_far": None,
    "max_window_count": 0,
    "max_total_window_count": 0,
}
for path in sorted(Path(os.environ["RUN_DIR"]).glob("crashcar_singlefar_detail_worker*.csv")):
    item = {
        "path": str(path),
        "rows": 0,
        "finite_direct_far_rows": 0,
        "ready_window_rows": 0,
        "min_direct_far": None,
        "max_window_count": 0,
        "max_total_window_count": 0,
    }
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            item["rows"] += 1
            payload["rows"] += 1
            try:
                direct = float(row.get("direct_far", "inf") or "inf")
            except ValueError:
                direct = math.inf
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
            if math.isfinite(direct):
                item["finite_direct_far_rows"] += 1
                payload["finite_direct_far_rows"] += 1
                if item["min_direct_far"] is None or direct < item["min_direct_far"]:
                    item["min_direct_far"] = direct
                if payload["min_direct_far"] is None or direct < payload["min_direct_far"]:
                    payload["min_direct_far"] = direct
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

check_source() {
    local remote_head source_head dirty config_relpaths config_path config_relpath github_check dirty_count
    source_head=$(git -C "${SOURCE_ROOT}" rev-parse HEAD)
    remote_head="${source_head}"
    github_check=${crashcar_check_github:-${CRASHCAR_CHECK_GITHUB:-0}}
    if [ "${github_check}" = "1" ]; then
        log "fetch GitHub ${GITHUB_REMOTE}/${GITHUB_BRANCH}"
        git -C "${SOURCE_ROOT}" fetch "${GITHUB_REMOTE}" "${GITHUB_BRANCH}"
        remote_head=$(git -C "${SOURCE_ROOT}" rev-parse FETCH_HEAD)
        if [ "${source_head}" != "${remote_head}" ]; then
            log "WARNING source head ${source_head} != GitHub latest ${remote_head}; continuing because GitHub check is non-blocking"
        fi
    else
        log "GitHub freshness check disabled; using local source head ${source_head}"
    fi
    dirty=$(git -C "${SOURCE_ROOT}" status --porcelain --untracked-files=no)
    config_relpaths=
    for config_path in "${CRASHCAR_SOURCE_CONFIG_FILE:-}" "${CONFIG_FILE}" "${SOURCE_ROOT}/scripts/crashcar.env"; do
        [ -n "${config_path}" ] || continue
        config_relpath=$(realpath --relative-to="${SOURCE_ROOT}" "${config_path}" 2>/dev/null || true)
        if [ -n "${config_relpath}" ]; then
            config_relpaths="${config_relpaths}${config_relpath}"$'\n'
        fi
    done
    if [ -n "${config_relpaths}" ]; then
        dirty=$(printf '%s\n' "${dirty}" | awk -v configs="${config_relpaths}" '
            BEGIN {
                split(configs, items, "\n")
                for (idx in items) {
                    if (items[idx] != "") {
                        allow[items[idx]] = 1
                    }
                }
            }
            NF && !allow[substr($0, 4)]
        ')
    fi
    if [ -n "${dirty}" ]; then
        dirty_count=$(printf '%s\n' "${dirty}" | awk 'NF {count++} END {print count+0}')
        log "WARNING tracked source worktree has ${dirty_count} dirty path(s); recording provenance and continuing"
        mkdir -p "${ROOT}/provenance"
        printf '%s\n' "${dirty}" > "${ROOT}/provenance/source_dirty_status.txt"
    else
        dirty_count=0
    fi
    if [ ! -x "${SOURCE_ROOT}/install_local/bin/gstlal_inspiral_postcohspiir_online" ]; then
        log "ERROR missing latest install_local runtime under ${SOURCE_ROOT}"
        write_status phase=failed reason=missing_install_local source_head="${source_head}" github_head="${remote_head}"
        exit 2
    fi
    printf '%s\n' "${remote_head}" > "${ROOT}/provenance/github_${GITHUB_BRANCH}_head.txt"
    mkdir -p "${ROOT}/bin"
    cp "${SOURCE_ROOT}/gstlal-spiir/bin/gstlal_inspiral_postcohspiir_online" \
        "${ROOT}/bin/gstlal_inspiral_postcohspiir_online"
    chmod +x "${ROOT}/bin/gstlal_inspiral_postcohspiir_online"
    finalsink_src="${SOURCE_ROOT}/gstlal-spiir/python/pipemodules/postcoh_finalsink.py"
    finalsink_dst="${SOURCE_ROOT}/install_local/lib/python3.10/site-packages/gstlal_spiir/pipemodules/postcoh_finalsink.py"
    if [ -f "${finalsink_src}" ]; then
        mkdir -p "$(dirname "${finalsink_dst}")"
        cp "${finalsink_src}" "${finalsink_dst}"
        log "staged Python finalsink ${finalsink_dst}"
    else
        log "ERROR missing source Python finalsink ${finalsink_src}"
        write_status phase=failed reason=missing_source_finalsink source_head="${source_head}" github_head="${remote_head}"
        exit 2
    fi
    rm -f "${CRASH_RUNTIME_ROOT}/install"
    ln -s "${SOURCE_ROOT}/install_local" "${CRASH_RUNTIME_ROOT}/install"
    {
        printf 'github_remote=%s\n' "$(git -C "${SOURCE_ROOT}" remote get-url "${GITHUB_REMOTE}")"
        printf 'github_branch=%s\n' "${GITHUB_BRANCH}"
        printf 'github_head=%s\n' "${remote_head}"
        printf 'github_check=%s\n' "${github_check}"
        printf 'root=%s\n' "${SOURCE_ROOT}"
        printf 'source_head=%s\n' "${source_head}"
        printf 'source_dirty_tracked_count=%s\n' "${dirty_count}"
        printf 'source_dirty_status=%s\n' "${ROOT}/provenance/source_dirty_status.txt"
        printf 'runtime_install_symlink=%s/install\n' "${CRASH_RUNTIME_ROOT}"
    } > "${ROOT}/provenance/source_and_runtime.env"
    write_status phase=source_ready github_branch="${GITHUB_BRANCH}" github_head="${remote_head}" source_head="${source_head}" source_dirty_tracked_count="${dirty_count}" source_root="${SOURCE_ROOT}" runtime_root="${CRASH_RUNTIME_ROOT}"
}

validate_inputs() {
    local p worker bank bank_id ifo bank_file
    for p in \
        "${SEGMENT_XML}" \
        "${DETRSP_MAP}" \
        "${FRAME_CACHE}" \
        "${CRASH_SCRIPT_DIR}/single_detector_far.py" \
        "${CRASH_SCRIPT_DIR}/dump_segment_livetime_csv.py" \
        "${CRASH_SCRIPT_DIR}/plot_single_llr_far.py" \
        "${CRASH_SCRIPT_DIR}/export_template_shape_map.py" \
        "${SCRIPT_DIR}/materialize_snr_autocorrelation.py" \
        "${WGUO_BANK_STATS_DIR}"; do
        [ -e "${p}" ] || { log "ERROR missing input ${p}"; write_status phase=failed reason="missing ${p}"; exit 2; }
    done
    for worker in $(seq 0 $((WORKER_COUNT - 1))); do
        local jobno
        jobno=$(printf '%03d' "${worker}")
        for suffix in 2w 1d 2h; do
            p="${NONINJ_STATS_LOC}/${jobno}/${jobno}_marginalized_stats_${suffix}.xml.gz"
            [ -e "${p}" ] || { log "ERROR missing input ${p}"; write_status phase=failed reason="missing ${p}"; exit 2; }
        done
        for bank in $(seq $((START_BANK + BANKS_PER_WORKER * worker)) $((START_BANK + BANKS_PER_WORKER * (worker + 1) - 1))); do
            bank_id=$(printf '%04d' "${bank}")
            for ifo in H1 L1 V1; do
                bank_file="${O3_BANK_DIR}/iir_${ifo}-GSTLAL_SPLIT_BANK_${bank_id}-a1-0-0.xml.gz"
                [ -e "${bank_file}" ] || { log "ERROR missing bank ${bank_file}"; write_status phase=failed reason="missing ${bank_file}"; exit 2; }
            done
        done
    done
    if [ "${SINGLE_BACKGROUND_MODE_VALUE}" = "frozen" ]; then
        if [ -n "${SINGLE_FROZEN_BACKGROUND_JSON_VALUE}" ]; then
            [ -f "${SINGLE_FROZEN_BACKGROUND_JSON_VALUE}" ] || {
                log "ERROR missing frozen single background ${SINGLE_FROZEN_BACKGROUND_JSON_VALUE}"
                write_status phase=failed reason=missing_frozen_single_background single_frozen_background_json="${SINGLE_FROZEN_BACKGROUND_JSON_VALUE}"
                exit 2
            }
        elif [ -n "${SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE}" ]; then
            [ -d "${SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE}" ] || {
                log "ERROR missing frozen single background run dir ${SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE}"
                write_status phase=failed reason=missing_frozen_single_background_run_dir single_frozen_background_run_dir="${SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE}"
                exit 2
            }
        else
            log "ERROR single_background_mode=frozen requires single_frozen_background_json or single_frozen_background_run_dir"
            write_status phase=failed reason=frozen_single_background_not_configured
            exit 2
        fi
    fi
    python3 "${CRASH_SCRIPT_DIR}/dump_segment_livetime_csv.py" \
        "${SEGMENT_XML}" \
        --output "${LIVETIME_CSV}" \
        > "${CONTROLLER_DIR}/dump_segment_livetime_csv.log" \
        2>&1
    [ -s "${LIVETIME_CSV}" ] || { log "ERROR livetime CSV not created"; write_status phase=failed reason=livetime_csv_missing; exit 2; }
    if [ -z "${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE}" ]; then
        SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE=$(python3 - "${LIVETIME_CSV}" "${START_GPS}" "${END_GPS}" <<'PY'
import csv
import sys

livetime_csv, start_text, end_text = sys.argv[1:4]
start = float(start_text)
end = float(end_text)
segments = {"H1": [], "L1": [], "V1": [], "K1": []}
with open(livetime_csv, newline="") as handle:
    for row in csv.DictReader(handle):
        ifo = (row.get("ifo") or "").strip()
        if ifo not in segments:
            continue
        try:
            seg_start = max(start, float(row["start"]))
            seg_end = min(end, float(row["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if seg_end > seg_start:
            segments[ifo].append((seg_start, seg_end))

breaks = {start, end}
for spans in segments.values():
    for seg_start, seg_end in spans:
        breaks.add(seg_start)
        breaks.add(seg_end)
points = sorted(value for value in breaks if start <= value <= end)

def active_at(midpoint):
    out = []
    for ifo in ("H1", "L1", "V1", "K1"):
        for seg_start, seg_end in segments[ifo]:
            if seg_start <= midpoint < seg_end:
                out.append(ifo[0])
                break
    return "+".join(out)

def fmt(value):
    if abs(value - round(value)) < 1.0e-6:
        return str(int(round(value)))
    return ("%.9f" % value).rstrip("0").rstrip(".")

windows = []
for left, right in zip(points, points[1:]):
    if right <= left:
        continue
    mask = active_at((left + right) / 2.0)
    if windows and windows[-1][2] == mask and abs(windows[-1][1] - left) < 1.0e-6:
        windows[-1] = (windows[-1][0], right, mask)
    else:
        windows.append((left, right, mask))
print(";".join(f"{fmt(left)}:{fmt(right)}:{mask}" for left, right, mask in windows))
PY
)
    fi
    printf '%s
' "${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE}" > "${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE}"
    export SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE
    export SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE
    bash -n "${SCRIPT_DIR}/crashcar_pipeline.sh"
    bash -n "${SCRIPT_DIR}/crashcar_sbatch.sh"
    write_status phase=inputs_validated segment_xml="${SEGMENT_XML}" crashcar_segment_livetime_csv="${LIVETIME_CSV}" single_output_active_ifo_schedule_file="${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE}" single_output_active_ifo_schedule_length="${#SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE}"
}

export_template_map() {
    module load gcc/13.3.0 scipy-bundle/2024.05 >/dev/null 2>&1 || true
    local template_map="${ARTIFACTS}/crashcar_template_shape_map.csv"
    python3 "${CRASH_SCRIPT_DIR}/export_template_shape_map.py" \
        --bank-stats-dir "${WGUO_BANK_STATS_DIR}" \
        --output "${template_map}" \
        --ifos H1,L1 \
        --start-bank "${START_BANK}" \
        --end-bank $((START_BANK + WORKER_COUNT * BANKS_PER_WORKER - 1)) \
        > "${CONTROLLER_DIR}/export_template_shape_map.log" \
        2>&1
    [ -s "${template_map}" ] || { log "ERROR template map not created"; write_status phase=failed reason=template_shape_map_missing; exit 2; }
    write_status phase=template_shape_map_ready template_shape_map="${template_map}"
    log "template shape map ready ${template_map}"
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
    local phase=$1 job=$2 sacct_text=$3 raw_json=$4 detail_json=$5
    REPORT="${REPORT}" ROOT="${ROOT}" RUN_DIR="${RUN_DIR}" ARTIFACTS="${ARTIFACTS}" SOURCE_ROOT="${SOURCE_ROOT}" \
        START_GPS="${START_GPS}" END_GPS="${END_GPS}" DURATION="${DURATION}" \
        BACKGROUND_ACCUMULATION="${BACKGROUND_ACCUMULATION}" BACKGROUND_UPDATE="${BACKGROUND_UPDATE}" \
        ZEROLAG_UPDATE="${ZEROLAG_UPDATE}" \
        WORKER_COUNT="${WORKER_COUNT}" BANKS_PER_WORKER="${BANKS_PER_WORKER}" \
        SINGLE_ONLY_SECONDS="${SINGLE_ONLY_SECONDS}" SINGLE_ONLY_FRACTION="${SINGLE_ONLY_FRACTION}" \
        HL_UNION_FRACTION="${HL_UNION_FRACTION}" H_ONLY_SECONDS="${H_ONLY_SECONDS}" \
        L_ONLY_SECONDS="${L_ONLY_SECONDS}" HL_SECONDS="${HL_SECONDS}" HL_NONE_SECONDS="${HL_NONE_SECONDS}" \
        FIRST3_H_ONLY_SECONDS="${FIRST3_H_ONLY_SECONDS}" FIRST3_L_ONLY_SECONDS="${FIRST3_L_ONLY_SECONDS}" \
        FIRST3_HL_SECONDS="${FIRST3_HL_SECONDS}" FIRST3_HL_NONE_SECONDS="${FIRST3_HL_NONE_SECONDS}" \
        TAIL_LOG_FAR="${TAIL_LOG_FAR}" FAR_FIT_BOUNDARY="${FAR_FIT_BOUNDARY}" \
        CRASHCAR_LOG10_FAR_THRESHOLD="${CRASHCAR_LOG10_FAR_THRESHOLD:-90}" \
        CRASHCAR_PRESERVE_TABLE_SINGLE_FAR="${CRASHCAR_PRESERVE_TABLE_SINGLE_FAR:-0}" \
        CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR="${CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR:-1}" \
        SINGLE_BACKGROUND_MODE_VALUE="${SINGLE_BACKGROUND_MODE_VALUE}" \
        SINGLE_FROZEN_BACKGROUND_JSON_VALUE="${SINGLE_FROZEN_BACKGROUND_JSON_VALUE}" \
        SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE="${SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE}" \
        CRASHCAR_SINGLE_LEDGER_FINAL_UPDATE="${CRASHCAR_SINGLE_LEDGER_FINAL_UPDATE}" \
        CRASHCAR_BUILD_LAST_BG_ARTIFACTS="${CRASHCAR_BUILD_LAST_BG_ARTIFACTS}" \
        CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE="${CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE}" \
        python3 - "${phase}" "${job}" "${sacct_text}" "${raw_json}" "${detail_json}" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

phase, job, sacct_text, raw_json, detail_json = sys.argv[1:6]
artifacts = Path(os.environ["ARTIFACTS"])
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
    "crashcar_preserve_table_single_far": int(os.environ["CRASHCAR_PRESERVE_TABLE_SINGLE_FAR"]),
    "crashcar_background_required_seconds": float(os.environ["CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE"]),
    "crashcar_single_ledger_final_update": os.environ["CRASHCAR_SINGLE_LEDGER_FINAL_UPDATE"],
    "crashcar_build_last_bg_artifacts": os.environ["CRASHCAR_BUILD_LAST_BG_ARTIFACTS"],
    "single_background_mode": os.environ["SINGLE_BACKGROUND_MODE_VALUE"],
    "single_frozen_background_json": os.environ["SINGLE_FROZEN_BACKGROUND_JSON_VALUE"] or None,
    "single_frozen_background_run_dir": os.environ["SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE"] or None,
    "single_ledger_patch_summary": str(Path(os.environ["RUN_DIR"]) / "monitor" / "patch_zerolag_single_far_summary.json"),
    "sacct": sacct_text,
    "raw_stream": json.loads(raw_json),
    "detail": json.loads(detail_json),
    "all_single_triggers_csv": str(artifacts / "crashcar_day1_all_single_triggers.csv"),
    "last_bg3h_features_csv": str(artifacts / "crashcar_day1_last_bg3h_single_triggers.csv"),
    "last_bg3h_background_json": str(artifacts / "crashcar_day1_last_bg3h_full_background.json"),
    "last_bg3h_plot": str(artifacts / "crashcar_day1_last_bg3h_background.png"),
    "run_summary": str(artifacts / "crashcar_run_summary.json"),
}
for key, rel in [
    ("background_summary", "crashcar_run_summary.json"),
    ("plot_summary", "crashcar_day1_last_bg3h_plot_summary.json"),
    ("snr_series_manifest", "crashcar_snr_series_manifest.json"),
]:
    path = artifacts / rel
    if path.exists():
        try:
            payload[key] = json.loads(path.read_text())
        except Exception as exc:
            payload[key] = {"error": repr(exc)}
Path(os.environ["REPORT"]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
}

synthesize_candidate_event_manifest() {
    local candidate_manifest="${RUN_DIR}/crashcar_candidate_events_manifest.csv"
    RUN_DIR="${RUN_DIR}" CANDIDATE_MANIFEST="${candidate_manifest}" python3 - <<'PY'
import csv
import json
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
manifest = Path(os.environ["CANDIDATE_MANIFEST"])
summary = run_dir / "candidate_event_manifest_summary.json"
input_manifests = sorted(
    run_dir.glob("[0-9][0-9][0-9]/crashcar_candidate_events/manifest.csv"))
fieldnames = [
    "archive_seq",
    "filename",
    "series_file",
    "xml_file",
    "candidate_xml_file",
    "archive_kind",
    "candidate_schema",
    "source_manifest",
    "reasons",
    "event_id",
    "ifos",
    "ifo",
    "end_time",
    "end_time_ns",
    "bankid",
    "tmplt_idx",
    "far",
    "far_sngl_H1",
    "far_sngl_L1",
    "code_version",
]
rows = []
for input_manifest in input_manifests:
    with input_manifest.open(newline="") as handle:
        for row in csv.DictReader(handle):
            xml_file = (row.get("filename") or row.get("xml_file") or "").strip()
            if not xml_file:
                continue
            xml_path = Path(xml_file)
            if xml_path.is_absolute():
                try:
                    xml_file = str(xml_path.relative_to(run_dir))
                except ValueError:
                    xml_file = str(xml_path)
            ifos = (row.get("ifos") or "").replace(",", "")
            active_ifos = [ifo for ifo in ("H1", "L1", "V1", "K1") if ifo in ifos]
            for ifo in active_ifos:
                out = {field: "" for field in fieldnames}
                for field in row:
                    if field in out:
                        out[field] = row.get(field, "")
                out["filename"] = xml_file
                out["series_file"] = xml_file
                out["xml_file"] = xml_file
                out["candidate_xml_file"] = xml_file
                out["archive_kind"] = row.get("archive_kind") or "candidate_event_xml"
                out["candidate_schema"] = row.get("candidate_schema") or "ligolw_coinc"
                out["source_manifest"] = str(input_manifest.relative_to(run_dir))
                out["ifo"] = ifo
                rows.append(out)

if not rows:
    for xml_path in sorted(
            run_dir.glob("[0-9][0-9][0-9]/crashcar_candidate_events/crashcar_snr_*.xml.gz")):
        rel_xml = str(xml_path.relative_to(run_dir))
        stem = xml_path.name[:-7]
        parts = stem.split("_")
        parsed = {}
        if len(parts) >= 9 and parts[0] == "crashcar" and parts[1] == "snr":
            parsed = {
                "archive_seq": parts[2],
                "ifos": parts[3],
                "end_time": parts[4],
                "end_time_ns": parts[5],
                "bankid": parts[6],
                "tmplt_idx": parts[7],
                "event_id": parts[8],
            }
        ifos = parsed.get("ifos", "")
        active_ifos = [ifo for ifo in ("H1", "L1", "V1", "K1") if ifo in ifos]
        if not active_ifos:
            active_ifos = [""]
        for ifo in active_ifos:
            out = {field: "" for field in fieldnames}
            out.update(parsed)
            out["filename"] = rel_xml
            out["series_file"] = rel_xml
            out["xml_file"] = rel_xml
            out["candidate_xml_file"] = rel_xml
            out["archive_kind"] = "candidate_event_xml"
            out["candidate_schema"] = "ligolw_coinc"
            out["source_manifest"] = ""
            out["ifo"] = ifo
            rows.append(out)

if rows:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
elif manifest.exists():
    manifest.unlink()

summary.write_text(json.dumps({
    "input_manifests": [str(path.relative_to(run_dir)) for path in input_manifests],
    "manifest": str(manifest),
    "rows": len(rows),
    "unique_candidate_xml_files": len({row["candidate_xml_file"] for row in rows}),
}, indent=2, sort_keys=True) + "\n")
raise SystemExit(0 if rows else 1)
PY
}

archive_snr_series() {
    local candidate_manifest="${RUN_DIR}/crashcar_candidate_events_manifest.csv"
    local archive="${ARTIFACTS}/crashcar_snr_series.tar.gz"
    local manifest="${ARTIFACTS}/crashcar_snr_series_manifest.json"
    if [ ! -s "${candidate_manifest}" ]; then
        if synthesize_candidate_event_manifest; then
            log "synthesized candidate-event manifest from worker crashcar_candidate_events"
        fi
    fi
    if [ ! -s "${candidate_manifest}" ]; then
        log "candidate-event manifest is absent; skipping retained candidate XML archive"
        RUN_DIR="${RUN_DIR}" CANDIDATE_MANIFEST="${candidate_manifest}" ARCHIVE="${archive}" MANIFEST="${manifest}" SNR_SERIES_LOG_FAR_THRESHOLD="${SNR_SERIES_LOG_FAR_THRESHOLD}" python3 - <<'PY'
import json
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
candidate_xml_files = sorted(
    run_dir.glob("[0-9][0-9][0-9]/crashcar_candidate_events/*.xml.gz"))
payload = {
    "archive": os.environ["ARCHIVE"],
    "archive_bytes": 0,
    "archive_kind": "candidate_coinc_xml",
    "byte_count": sum(p.stat().st_size for p in candidate_xml_files),
    "candidate_event_manifest": os.environ["CANDIDATE_MANIFEST"],
    "candidate_event_xml_files": len(candidate_xml_files),
    "data_series_files": 0,
    "exists": False,
    "file_count": len(candidate_xml_files),
    "legacy_archive_skipped": True,
    "manifest_exists": Path(os.environ["CANDIDATE_MANIFEST"]).exists(),
    "manifest_rows": 0,
    "reason": "skipped_no_candidate_event_manifest",
    "removed_csv_files": [],
    "snr_series_dir": "",
    "snr_series_log10_far_threshold": os.environ.get("SNR_SERIES_LOG_FAR_THRESHOLD"),
    "template_autocorrelation_files": 0,
}
Path(os.environ["MANIFEST"]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
        return 0
    fi
    python3 "${SCRIPT_DIR}/materialize_snr_autocorrelation.py" \
        --manifest "${candidate_manifest}" \
        --snr-dir "${RUN_DIR}" \
        --bank-dir "${O3_BANK_DIR}" \
        > "${CONTROLLER_DIR}/materialize_snr_autocorrelation.log" \
        2>&1 || {
            log "ERROR failed to materialize SNR template autocorrelation companions"
            return 1
        }
    local tar_paths=(crashcar_candidate_events_manifest.csv)
    if [ -f "${RUN_DIR}/candidate_event_manifest_summary.json" ]; then
        tar_paths+=(candidate_event_manifest_summary.json)
    fi
    if [ -f "${RUN_DIR}/autocorrelation_summary.json" ]; then
        tar_paths+=(autocorrelation_summary.json)
    fi
    if [ -f "${RUN_DIR}/crashcar_template_autocorrelation.xml" ]; then
        tar_paths+=(crashcar_template_autocorrelation.xml)
    fi
    local candidate_dir rel_candidate_dir
    while IFS= read -r candidate_dir; do
        rel_candidate_dir="${candidate_dir#${RUN_DIR}/}"
        tar_paths+=("${rel_candidate_dir}")
    done < <(find "${RUN_DIR}" -mindepth 2 -maxdepth 2 -type d -name crashcar_candidate_events | sort)
    tar -C "${RUN_DIR}" -czf "${archive}" "${tar_paths[@]}"
    RUN_DIR="${RUN_DIR}" CANDIDATE_MANIFEST="${candidate_manifest}" ARCHIVE="${archive}" MANIFEST="${manifest}" SNR_SERIES_LOG_FAR_THRESHOLD="${SNR_SERIES_LOG_FAR_THRESHOLD}" python3 - <<'PY'
import json
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
archive = Path(os.environ["ARCHIVE"])
candidate_manifest = Path(os.environ["CANDIDATE_MANIFEST"])
candidate_dirs = sorted(
    run_dir.glob("[0-9][0-9][0-9]/crashcar_candidate_events"))
files = []
for directory in candidate_dirs:
    files.extend(p for p in directory.rglob("*") if p.is_file())
for path in (
    candidate_manifest,
    run_dir / "candidate_event_manifest_summary.json",
    run_dir / "autocorrelation_summary.json",
    run_dir / "crashcar_template_autocorrelation.xml",
):
    if path.exists():
        files.append(path)
manifest_rows = 0
data_series_files = 0
template_autocorrelation_files = 0
template_autocorrelation_xml_files = 0
if candidate_manifest.exists():
    import csv
    with candidate_manifest.open(newline="") as input_file:
        for row in csv.DictReader(input_file):
            manifest_rows += 1
            if row.get("series_file"):
                data_series_files += 1
            if row.get("template_autocorrelation_file"):
                template_autocorrelation_files += 1
            if row.get("template_autocorrelation_xml_file"):
                template_autocorrelation_xml_files += 1
candidate_xml_files = sorted(
    run_dir.glob("[0-9][0-9][0-9]/crashcar_candidate_events/*.xml.gz"))
candidate_manifests = sorted(
    run_dir.glob("[0-9][0-9][0-9]/crashcar_candidate_events/manifest.csv"))
payload = {
    "archive": str(archive),
    "archive_bytes": archive.stat().st_size if archive.exists() else 0,
    "archive_kind": "candidate_coinc_xml",
    "archive_exists": archive.exists(),
    "byte_count": sum(p.stat().st_size for p in files),
    "byte_count_before_compaction": sum(p.stat().st_size for p in files),
    "candidate_event_manifest": str(candidate_manifest),
    "compacted_after_archive": True,
    "data_series_files": data_series_files,
    "exists": candidate_manifest.exists(),
    "file_count": len(files),
    "file_count_before_compaction": len(files),
    "manifest_rows": manifest_rows,
    "candidate_event_manifest_count": len(candidate_manifests),
    "candidate_event_xml_files": len(candidate_xml_files),
    "removed_small_csv_count": 0,
    "removed_small_csv_sample": [],
    "sample_files": [str(p.relative_to(run_dir)) for p in sorted(files)[:20]],
    "snr_series_dir": "",
    "snr_series_logFAR_threshold": os.environ["SNR_SERIES_LOG_FAR_THRESHOLD"],
    "template_autocorrelation_files": template_autocorrelation_files,
    "template_autocorrelation_xml_files": template_autocorrelation_xml_files,
}
Path(os.environ["MANIFEST"]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
    log "archived retained candidate/coinc XML ${archive}"
}

submit_job() {
    local template_map="${ARTIFACTS}/crashcar_template_shape_map.csv"
    cd "${RUN_DIR}"
    local job
    local sbatch_args=(
        --parsable
        --job-name="${SLURM_JOB_NAME}"
        --mem="${SLURM_MEM}"
        --cpus-per-task="${SLURM_CPUS_PER_TASK}"
        --gres="${SLURM_GRES}"
        --array="0-$((WORKER_COUNT - 1))"
        --export=ALL,TOP_RUN_ROOT="${ROOT}",RUN_DIR="${RUN_DIR}",CRASH_ROOT="${CRASH_RUNTIME_ROOT}",WGUO_O3A_INJECTION_MODE="${INJECTION_PIPELINE_MODE}",WGUO_O3A_INJECTION_FILE="${INJECTION_FILE}",WGUO_O3A_START_GPS="${START_GPS}",WGUO_O3A_END_GPS="${END_GPS}",WGUO_O3A_DETRSP_MAP="${DETRSP_MAP}",WGUO_O3A_FRAME_CACHE="${FRAME_CACHE}",WGUO_O3A_NONINJ_STATS_LOC="${NONINJ_STATS_LOC}",WGUO_O3A_BANK_DIR="${O3_BANK_DIR}",WGUO_O3A_BANKS_PER_GROUP="${BANKS_PER_WORKER}",WGUO_O3A_START_BANK="${START_BANK}",WGUO_O3A_SNAPSHOT_INTERVAL="${ZEROLAG_UPDATE}",WGUO_O3A_COLLECT_WALLTIME="${BACKGROUND_ACCUMULATION},${BACKGROUND_ACCUMULATION},${BACKGROUND_ACCUMULATION}",BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION}",FORMAL_BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION}",CRASHCAR_BACKGROUND_REQUIRED_SECONDS="${CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE}",BACKGROUND_UPDATE_TRIGGER_SECONDS="${BACKGROUND_UPDATE}",COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS="${BACKGROUND_UPDATE}",COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS="${BACKGROUND_UPDATE}",FINALSINK_FAPUPDATER_INTERVAL_SECONDS="${BACKGROUND_UPDATE}",ZEROLAG_SNAPSHOT_INTERVAL_SECONDS="${ZEROLAG_UPDATE}",CRASHCAR_SNAPSHOT_INTERVAL_SECONDS="${ZEROLAG_UPDATE}",CRASHCAR_LOG10_FAR_THRESHOLD="${CRASHCAR_LOG10_FAR_THRESHOLD:-90}",CRASHCAR_SNR_SERIES_LOG10_FAR_THRESHOLD="${SNR_SERIES_LOG_FAR_THRESHOLD}",CRASHCAR_PRESERVE_TABLE_SINGLE_FAR="${CRASHCAR_PRESERVE_TABLE_SINGLE_FAR:-0}",CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR="${CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR:-1}",CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME="${template_map}",CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP="${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}",CRASHCAR_CODE_VERSION="${CRASHCAR_CODE_VERSION}",WGUO_O3A_SEGMENT_XML="${SEGMENT_XML}",SEGMENT_XML="${SEGMENT_XML}",SINGLE_SEGMENT_XML="${SEGMENT_XML}",CRASHCAR_SEGMENT_LIVETIME_CSV="${LIVETIME_CSV}",CRASHCAR_SINGLE_OUTPUT_MODE="single-only",SINGLE_OUTPUT_MODE="single-only",CRASHCAR_SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE="${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE}",SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE="${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE}"
        --chdir="${RUN_DIR}"
    )
    if [ -n "${SLURM_PARTITION}" ]; then
        sbatch_args+=(--partition="${SLURM_PARTITION}")
    fi
    if [ -n "${SLURM_TIME}" ]; then
        sbatch_args+=(--time="${SLURM_TIME}")
    fi
    sbatch_args+=("${SCRIPT_DIR}/crashcar_sbatch.sh")
    job=$(sbatch "${sbatch_args[@]}")
    printf '%s\n' "${job}" > "${CONTROLLER_DIR}/job_id.txt"
    write_status phase=slurm_submitted job_id="${job}" run_dir="${RUN_DIR}" worker_count="${WORKER_COUNT}" banks_per_worker="${BANKS_PER_WORKER}" background_accumulation_seconds="${BACKGROUND_ACCUMULATION}" background_update_seconds="${BACKGROUND_UPDATE}" zerolag_update_seconds="${ZEROLAG_UPDATE}" tail_log_FAR="${TAIL_LOG_FAR}" tail_FAR="${FAR_FIT_BOUNDARY}" SNR_series_logFAR_threshold="${SNR_SERIES_LOG_FAR_THRESHOLD}" injection_mode="${INJECTION_MODE}" injection_pipeline_mode="${INJECTION_PIPELINE_MODE}" single_only_fraction="${SINGLE_ONLY_FRACTION}" hl_union_fraction="${HL_UNION_FRACTION}"
    log "submitted Slurm job=${job} workers=${WORKER_COUNT} banks_per_worker=${BANKS_PER_WORKER} gps=${START_GPS}-${END_GPS}"
}

postprocess_last_bg3h() {
    local combined="${ARTIFACTS}/crashcar_day1_all_single_triggers.csv"
    local last_window="${ARTIFACTS}/crashcar_day1_last_bg3h_single_triggers.csv"
    local trigger_inputs=()
    local worker jobno
    for worker in $(seq 0 $((WORKER_COUNT - 1))); do
        jobno=$(printf '%03d' "${worker}")
        trigger_inputs+=("${RUN_DIR}/${jobno}/${jobno}_single_triggers.csv")
    done
    python3 - "${combined}" "${last_window}" "${START_GPS}" "${END_GPS}" "${BACKGROUND_ACCUMULATION}" "${trigger_inputs[@]}" <<'PY'
import csv
import pathlib
import sys

combined = pathlib.Path(sys.argv[1])
last_window = pathlib.Path(sys.argv[2])
start = int(sys.argv[3])
end = int(sys.argv[4])
accum = int(sys.argv[5])
inputs = [pathlib.Path(p) for p in sys.argv[6:]]
window_start = max(start, end - accum)

all_writer = None
win_writer = None
total = 0
window_rows = 0
with combined.open("w", newline="", encoding="utf-8") as all_handle, last_window.open("w", newline="", encoding="utf-8") as win_handle:
    for path in inputs:
        if not path.exists():
            raise SystemExit(f"missing input {path}")
        with path.open(newline="", encoding="utf-8") as in_handle:
            reader = csv.DictReader(in_handle)
            fields = list(reader.fieldnames or [])
            if "is_background" not in fields:
                fields.append("is_background")
            if all_writer is None:
                all_writer = csv.DictWriter(all_handle, fieldnames=fields)
                win_writer = csv.DictWriter(win_handle, fieldnames=fields)
                all_writer.writeheader()
                win_writer.writeheader()
            for row in reader:
                row["is_background"] = "1"
                clean = {field: row.get(field, "") for field in all_writer.fieldnames}
                all_writer.writerow(clean)
                total += 1
                try:
                    t = int(float(row.get("end_time") or "nan"))
                except ValueError:
                    continue
                if window_start <= t <= end:
                    win_writer.writerow(clean)
                    window_rows += 1
print(f"combined_rows={total} last_bg3h_rows={window_rows} window_start={window_start} end={end}")
PY

    local background="${ARTIFACTS}/crashcar_day1_last_bg3h_full_background.json"
    local assigned="${ARTIFACTS}/crashcar_day1_last_bg3h_assigned_empty.csv"
    local support="${ARTIFACTS}/crashcar_day1_last_bg3h_support.csv"
    python3 "${CRASH_SCRIPT_DIR}/single_detector_far.py" feature-csv \
        --feature-csv "${last_window}" \
        --output "${assigned}" \
        --background-output "${background}" \
        --support-output "${support}" \
        --ifos H1,L1 \
        --min-snr 4 \
        --foreground-count 1 \
        --background-livetime "${BACKGROUND_ACCUMULATION}" \
        --segment-xml "${SEGMENT_XML}" \
        --background-start-gps "$((END_GPS - BACKGROUND_ACCUMULATION))" \
        --background-end-gps "${END_GPS}" \
        --calibrate-noise-dof \
        --snr-bins 4,5,6,8,inf \
        --min-calibration-count 20 \
        --bank-stats-dir "${WGUO_BANK_STATS_DIR}" \
        --signal-dof "${DEFAULT_SHAPE_DOF}" \
        --noise-dof "${DEFAULT_SHAPE_DOF}" \
        --noise-beta "${NOISE_BETA}" \
        --rank-offset "${RANK_OFFSET}" \
        --background-window-days 7 \
        --fit-min-points 20 \
        --far-fit-boundary "${FAR_FIT_BOUNDARY}" \
        > "${CONTROLLER_DIR}/single_detector_far_last_bg3h.log" \
        2>&1

    python3 "${CRASH_SCRIPT_DIR}/plot_single_llr_far.py" \
        --background "${background}" \
        --assigned "${assigned}" \
        --output "${ARTIFACTS}/crashcar_day1_last_bg3h_background.png" \
        --summary "${ARTIFACTS}/crashcar_day1_last_bg3h_plot_summary.json" \
        --llr-min -20 \
        --tail-log10-far -2.0 \
        > "${CONTROLLER_DIR}/plot_single_llr_far_last_bg3h.log" \
        2>&1

    python3 - "${ARTIFACTS}" "${combined}" "${last_window}" "${background}" "${START_GPS}" "${END_GPS}" "${BACKGROUND_ACCUMULATION}" "${BACKGROUND_UPDATE}" "$(cat "${ROOT}/provenance/github_${GITHUB_BRANCH}_head.txt")" <<'PY'
import csv
import json
import math
import pathlib
import sys

artifacts = pathlib.Path(sys.argv[1])
combined = pathlib.Path(sys.argv[2])
last_window = pathlib.Path(sys.argv[3])
background_path = pathlib.Path(sys.argv[4])
start = int(sys.argv[5])
end = int(sys.argv[6])
accum = int(sys.argv[7])
update = int(float(sys.argv[8]))
git_head = sys.argv[9]

def count_candidates(path):
    out = {"rows": 0, "non_boundary_rows": 0, "chunk_boundary_rows": 0, "candidate_counts": {"H1": 0, "L1": 0}}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            out["rows"] += 1
            if (row.get("source_kind") or "").strip() == "chunk_boundary":
                out["chunk_boundary_rows"] += 1
                continue
            out["non_boundary_rows"] += 1
            for ifo in ("H1", "L1"):
                try:
                    snr = float(row.get(f"snglsnr_{ifo}") or "nan")
                    chisq = float(row.get(f"chisq_{ifo}") or "nan")
                except ValueError:
                    continue
                if math.isfinite(snr) and math.isfinite(chisq) and snr >= 4.0 and chisq > 0.0:
                    out["candidate_counts"][ifo] += 1
    return out

background = json.loads(background_path.read_text())
summary = {
    "git_head": git_head,
    "gps_start": start,
    "gps_end": end,
    "duration": end - start,
    "background_accumulation_seconds": accum,
    "background_update_seconds": update,
    "all_stream": count_candidates(combined),
    "last_bg3h_stream": count_candidates(last_window),
    "last_bg3h_background_trigger_counts_by_ifo": {
        ifo: background["backgrounds"][ifo]["background_trigger_count"]
        for ifo in ("H1", "L1")
    },
}
(artifacts / "crashcar_run_summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n")
PY
}

run_single_ledger_final_update() {
    [ "${CRASHCAR_SINGLE_LEDGER_FINAL_UPDATE}" = "1" ] || return 0

    local worker worker_log final_status=0
    log "running final single ledger update mode=${SINGLE_BACKGROUND_MODE_VALUE} input=${FINAL_SINGLE_INPUT_KIND_VALUE}"

    rm -f \
        "${RUN_DIR}/single_branch/single_final_far_all.csv" \
        "${RUN_DIR}/single_branch/single_final_far_latest_candidates.csv" \
        "${RUN_DIR}/monitor/latest_single_background_status.json" \
        "${RUN_DIR}/monitor/patch_zerolag_single_far_summary.json"

    for worker in $(seq 0 $((WORKER_COUNT - 1))); do
        worker_log="${RUN_DIR}/logs/final_single_ledger_worker_${worker}.log"
        SCRIPT_DIR="${CRASH_SCRIPT_DIR}" \
        RUN_DIR="${RUN_DIR}" \
        SINGLE_INPUT_KIND="${FINAL_SINGLE_INPUT_KIND_VALUE}" \
        SINGLE_BACKGROUND_MODE="${SINGLE_BACKGROUND_MODE_VALUE}" \
        SINGLE_FROZEN_BACKGROUND_JSON="${SINGLE_FROZEN_BACKGROUND_JSON_VALUE}" \
        SINGLE_FROZEN_BACKGROUND_RUN_DIR="${SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE}" \
        SINGLE_FROZEN_BACKGROUND_ID="${SINGLE_FROZEN_BACKGROUND_ID_VALUE}" \
        SINGLE_FROZEN_BACKGROUND_SOURCE="${SINGLE_FROZEN_BACKGROUND_SOURCE_VALUE}" \
        SINGLE_WORKER_ID="${worker}" \
        SINGLE_WORKER_GROUP="${worker}" \
        SINGLE_WORKER_COUNT="${WORKER_COUNT}" \
        MAX_GROUP=$((WORKER_COUNT - 1)) \
        BANKS_PER_GROUP="${BANKS_PER_WORKER}" \
        DATA_START_TIME="${START_GPS}" \
        DATA_END_TIME="${END_GPS}" \
        SINGLE_SEGMENT_XML="${SEGMENT_XML}" \
        CRASHCAR_FINAL_POSTPROCESS=1 \
        BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION}" \
        FORMAL_BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION}" \
        BACKGROUND_UPDATE_TRIGGER_SECONDS="${BACKGROUND_UPDATE}" \
        CRASHCAR_SEGMENT_LIVETIME_CSV="${LIVETIME_CSV}" \
        WGUO_BANK_STATS_DIR="${WGUO_BANK_STATS_DIR}" \
        NOISE_BETA="${NOISE_BETA}" \
        RANK_OFFSET="${RANK_OFFSET}" \
        DEFAULT_SHAPE_DOF="${DEFAULT_SHAPE_DOF}" \
        TAIL_LOG10_FAR="${TAIL_LOG_FAR}" \
        FAR_FIT_BOUNDARY="${FAR_FIT_BOUNDARY}" \
        ASSIGNMENT_MAX_NEW_WINDOWS_PER_RUN="${assignment_max_new_windows_per_run:-${ASSIGNMENT_MAX_NEW_WINDOWS_PER_RUN:-99}}" \
            bash "${CRASH_SCRIPT_DIR}/update_single_background_once.sh" "${RUN_DIR}" \
            > "${worker_log}" 2>&1 || final_status=$?
    done

    python3 "${CRASH_SCRIPT_DIR}/merge_worker_far_ledgers.py" \
        --run-dir "${RUN_DIR}" \
        --worker-count "${WORKER_COUNT}" \
        --output single_branch/single_final_far_all.csv \
        --candidate-output single_branch/single_final_far_latest_candidates.csv \
        --summary monitor/latest_single_background_status.json \
        --plot-summary monitor/latest_single_plot_summary.json \
        > "${RUN_DIR}/logs/final_single_ledger_merge.log" \
        2> "${RUN_DIR}/logs/final_single_ledger_merge.err" || final_status=$?

    local final_ledger_rows=0
    final_ledger_rows=$(python3 - "${RUN_DIR}/single_branch/single_final_far_all.csv" <<'PY'
import csv
import sys
try:
    with open(sys.argv[1], newline="") as handle:
        print(sum(1 for _ in csv.DictReader(handle)))
except FileNotFoundError:
    print(0)
PY
    )

    if [ "${PATCH_ZEROLAG_SINGLE_FAR_VALUE}" = "1" ] && [ "${final_ledger_rows}" -gt 0 ]; then
        local patch_args=(
            --run-dir "${RUN_DIR}"
            --ledger single_branch/single_final_far_all.csv
            --far-column "${PATCH_ZEROLAG_SINGLE_FAR_COLUMN_VALUE}"
            --summary monitor/patch_zerolag_single_far_summary.json
            --single-output-mode single-only
            --active-ifo-schedule "${SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE}"
            --clear-existing
        )
        if [ "${PATCH_ZEROLAG_SINGLE_SNR_SERIES_VALUE}" = "1" ]; then
            patch_args+=(
                --embed-snr-series
                --snr-series-manifest "${RUN_DIR}/crashcar_candidate_events_manifest.csv"
            )
        fi
        export GST_DEBUG="${GST_DEBUG:-}"
        export X509_USER_PROXY="${X509_USER_PROXY:-}"
        export X509_USER_KEY="${X509_USER_KEY:-}"
        export X509_USER_CERT="${X509_USER_CERT:-}"
        export KRB5_KTNAME="${KRB5_KTNAME:-}"
        export PYTHONPATH="${PYTHONPATH:-}"
        export PKG_CONFIG_PATH="${PKG_CONFIG_PATH:-}"
        export GST_PLUGIN_PATH="${GST_PLUGIN_PATH:-}"
        export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
        # shellcheck source=/dev/null
        source /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh
        PYTHONPATH="${CRASH_RUNTIME_ROOT}/install/lib/python3.10/site-packages:${PYTHONPATH:-}" \
            run_spiir_py3 wguo-single-det-py3 python3 \
            "${CRASH_SCRIPT_DIR}/patch_zerolag_single_far_from_ledger.py" \
            "${patch_args[@]}" \
            > "${RUN_DIR}/logs/final_single_patch_zerolag.out" \
            2> "${RUN_DIR}/logs/final_single_patch_zerolag.err" || final_status=$?
    elif [ "${PATCH_ZEROLAG_SINGLE_FAR_VALUE}" = "1" ]; then
        log "final single ledger has no assigned rows; skipping zerolag single-FAR patch"
        RUN_DIR="${RUN_DIR}" FINAL_LEDGER_ROWS="${final_ledger_rows}" python3 - <<'PY'
import json
import os
import pathlib
import time

summary = {
    "patched_files": 0,
    "patched_rows": 0,
    "ledger_rows": int(os.environ.get("FINAL_LEDGER_ROWS") or 0),
    "skipped": True,
    "reason": "empty_single_far_ledger",
    "updated_unix": time.time(),
}
path = pathlib.Path(os.environ["RUN_DIR"]) / "monitor" / "patch_zerolag_single_far_summary.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
PY
    fi

    if [ "${final_status}" -ne 0 ]; then
        log "ERROR final single ledger update failed; see ${RUN_DIR}/logs/final_single_ledger_worker_*.log"
    else
        log "final single ledger update completed"
    fi
    return "${final_status}"
}

monitor_job() {
    local job=$1
    while true; do
        local snapshot sacct_state squeue_state zerolag stats raw detail
        snapshot=$(job_snapshot "${job}")
        sacct_state=${snapshot%%@@@*}
        squeue_state=${snapshot#*@@@}
        zerolag=$(count_zerolag)
        stats=$(count_stats)
        raw=$(run_summary_json)
        detail=$(detail_summary_json)
        write_status phase=slurm_running job_id="${job}" squeue="${squeue_state}" sacct="${sacct_state}" zerolag_count="${zerolag}" stats_count="${stats}" raw_stream_summary="${raw}" detail_summary="${detail}"
        log "job=${job} squeue=${squeue_state} sacct=${sacct_state} zerolag=${zerolag} stats=${stats} raw=${raw} detail=${detail}"
        if [ "${squeue_state}" = "none" ]; then
            local phase=slurm_completed
            if grep -Eq 'FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL|PREEMPTED|BOOT_FAIL|DEADLINE|REVOKED' <<<"${sacct_state}"; then
                phase=failed_slurm
                write_status phase="${phase}" job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}"
                write_final_report "${phase}" "${job}" "${sacct_state}" "${raw}" "${detail}"
                exit 3
            fi
            if ! archive_snr_series; then
                write_status phase=failed_postprocess job_id="${job}" reason=snr_series_archive_failed sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}" snr_series_manifest="${ARTIFACTS}/crashcar_snr_series_manifest.json"
                write_final_report failed_postprocess "${job}" "${sacct_state}" "${raw}" "${detail}"
                exit 4
            fi
            write_status phase=postprocessing_single_ledger job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}" single_background_mode="${SINGLE_BACKGROUND_MODE_VALUE}"
            if ! run_single_ledger_final_update; then
                raw=$(run_summary_json)
                detail=$(detail_summary_json)
                write_status phase=failed_postprocess job_id="${job}" reason=single_ledger_final_update_failed sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}" final_report="${REPORT}"
                write_final_report failed_postprocess "${job}" "${sacct_state}" "${raw}" "${detail}"
                exit 4
            fi
            if [ "${CRASHCAR_BUILD_LAST_BG_ARTIFACTS}" = "1" ]; then
                write_status phase=postprocessing_last_bg3h job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}"
                log "slurm completed; building final last-3h background artifacts"
                if ! postprocess_last_bg3h; then
                    raw=$(run_summary_json)
                    detail=$(detail_summary_json)
                    write_status phase=failed_postprocess job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}" final_report="${REPORT}"
                    write_final_report failed_postprocess "${job}" "${sacct_state}" "${raw}" "${detail}"
                    exit 4
                fi
            else
                log "skipping local background artifact build for this stage"
            fi
            raw=$(run_summary_json)
            detail=$(detail_summary_json)
            if [ "${CRASHCAR_BUILD_LAST_BG_ARTIFACTS}" = "1" ]; then
                write_status phase=completed job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}" final_report="${REPORT}" last_bg3h_background="${ARTIFACTS}/crashcar_day1_last_bg3h_full_background.json" last_bg3h_plot="${ARTIFACTS}/crashcar_day1_last_bg3h_background.png" run_summary="${ARTIFACTS}/crashcar_run_summary.json" snr_series_archive="${ARTIFACTS}/crashcar_snr_series.tar.gz" snr_series_manifest="${ARTIFACTS}/crashcar_snr_series_manifest.json" single_background_mode="${SINGLE_BACKGROUND_MODE_VALUE}" patch_zerolag_summary="${RUN_DIR}/monitor/patch_zerolag_single_far_summary.json"
            else
                write_status phase=completed job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}" final_report="${REPORT}" snr_series_archive="${ARTIFACTS}/crashcar_snr_series.tar.gz" snr_series_manifest="${ARTIFACTS}/crashcar_snr_series_manifest.json" single_background_mode="${SINGLE_BACKGROUND_MODE_VALUE}" patch_zerolag_summary="${RUN_DIR}/monitor/patch_zerolag_single_far_summary.json"
            fi
            write_final_report completed "${job}" "${sacct_state}" "${raw}" "${detail}"
            log "completed; report=${REPORT}"
            exit 0
        fi
        sleep 300
    done
}

main() {
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
        single_frozen_background_json="${SINGLE_FROZEN_BACKGROUND_JSON_VALUE}" \
        single_frozen_background_run_dir="${SINGLE_FROZEN_BACKGROUND_RUN_DIR_VALUE}" \
        crashcar_single_ledger_final_update="${CRASHCAR_SINGLE_LEDGER_FINAL_UPDATE}" \
        crashcar_build_last_bg_artifacts="${CRASHCAR_BUILD_LAST_BG_ARTIFACTS}" \
        crashcar_background_required_seconds="${CRASHCAR_BACKGROUND_REQUIRED_SECONDS_VALUE}" \
        injection_bg_start_gps="${INJECTION_BG_START_GPS}" \
        injection_bg_end_gps="${INJECTION_BG_END_GPS}" \
        injection_bg_duration_seconds="${INJECTION_BG_DURATION_SECONDS}" \
        worker_count="${WORKER_COUNT}" \
        banks_per_worker="${BANKS_PER_WORKER}" \
        single_only_fraction="${SINGLE_ONLY_FRACTION}" \
        hl_union_fraction="${HL_UNION_FRACTION}" \
        first3_h_only_seconds="${FIRST3_H_ONLY_SECONDS}" \
        first3_l_only_seconds="${FIRST3_L_ONLY_SECONDS}" \
        first3_hl_seconds="${FIRST3_HL_SECONDS}" \
        first3_hl_none_seconds="${FIRST3_HL_NONE_SECONDS}"
    log "controller start root=${ROOT} gps=${START_GPS}-${END_GPS}"
    check_source
    cp "${ROOT}/provenance/source_and_runtime.env" "${CONTROLLER_DIR}/source_and_runtime.env"
    cp "${ROOT}/provenance/github_${GITHUB_BRANCH}_head.txt" "${CONTROLLER_DIR}/github_head.txt" 2>/dev/null || true
    validate_inputs
    export_template_map
    submit_job
    monitor_job "$(cat "${CONTROLLER_DIR}/job_id.txt")"
}

main "$@"
