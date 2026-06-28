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

SOURCE_ROOT=${source_root:-${SOURCE_ROOT:-}}
: "${SOURCE_ROOT:?source_root required in ${CONFIG_FILE}}"
CRASH_RUNTIME_ROOT=${crash_runtime_root:-${CRASH_RUNTIME_ROOT:-"${ROOT}/crashcar_runtime"}}
CRASH_SCRIPT_DIR="${SOURCE_ROOT}/gstlal-spiir/single_detector_sidecar/online_single_llrfar_wguo_refined"
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
SNAPSHOT_INTERVAL=${snapshot_interval:-${SNAPSHOT_INTERVAL:-${BACKGROUND_UPDATE}}}
WORKER_COUNT=${worker_number:-${worker_count:-${WORKER_COUNT:-2}}}
BANKS_PER_WORKER=${bank_per_worker:-${banks_per_worker:-${BANKS_PER_WORKER:-8}}}
START_BANK=${start_bank:-${START_BANK:-0}}

SEGMENT_XML=${segment_xml:-${SEGMENT_XML:-}}
: "${SEGMENT_XML:?segment_xml required in ${CONFIG_FILE}}"
LIVETIME_CSV=${livetime_csv:-${LIVETIME_CSV:-"${ARTIFACTS}/H1L1V1_SEGMENTS_${START_GPS}_${DURATION}_livetime.csv"}}
DETRSP_MAP=${detector_response_file:-${detrsp_map:-${DETRSP_MAP:-}}}
: "${DETRSP_MAP:?detector_response_file required in ${CONFIG_FILE}}"
FRAME_CACHE=${data_file:-${frame_cache:-${FRAME_CACHE:-}}}
: "${FRAME_CACHE:?data_file required in ${CONFIG_FILE}}"
NONINJ_STATS_LOC=${noninj_stats_loc:-${NONINJ_STATS_LOC:-}}
: "${NONINJ_STATS_LOC:?noninj_stats_loc required in ${CONFIG_FILE}}"
O3_BANK_DIR=${bank_file:-${o3_bank_dir:-${O3_BANK_DIR:-}}}
: "${O3_BANK_DIR:?bank_file required in ${CONFIG_FILE}}"
WGUO_BANK_STATS_DIR=${wguo_bank_stats_dir:-${WGUO_BANK_STATS_DIR:-}}
: "${WGUO_BANK_STATS_DIR:?wguo_bank_stats_dir required in ${CONFIG_FILE}}"
DEFAULT_SHAPE_DOF=${default_shape_dof:-${DEFAULT_SHAPE_DOF:-74.30962572260326}}
NOISE_BETA=${noise_beta:-${NOISE_BETA:--1.0}}
RANK_OFFSET=${rank_offset:-${RANK_OFFSET:-0.0}}
FAR_FIT_BOUNDARY=${tail_FAR:-${far_fit_boundary:-${FAR_FIT_BOUNDARY:-0.01}}}
CRASHCAR_CODE_VERSION=${crashcar_code_version:-${CRASHCAR_CODE_VERSION:-"spiir-crashcar-${GITHUB_BRANCH}"}}
SLURM_JOB_NAME=${slurm_job_name:-${SLURM_JOB_NAME:-crashcar}}
SLURM_TIME=${slurm_time:-${SLURM_TIME:-24:00:00}}
SLURM_MEM=${slurm_mem:-${SLURM_MEM:-32g}}
SLURM_CPUS_PER_TASK=${slurm_cpus_per_task:-${SLURM_CPUS_PER_TASK:-4}}
SLURM_GRES=${slurm_gres:-${SLURM_GRES:-gpu:1}}
TMUX_SESSION=${tmux_session:-${TMUX_SESSION:-codex1}}
CRASHCAR_LOG10_FAR_THRESHOLD=${crashcar_log10_far_threshold:-${CRASHCAR_LOG10_FAR_THRESHOLD:-90}}
CRASHCAR_PRESERVE_TABLE_SINGLE_FAR=${crashcar_preserve_table_single_far:-${CRASHCAR_PRESERVE_TABLE_SINGLE_FAR:-0}}
CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP=${crashcar_require_template_shape_map:-${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}}

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
INJECTION_BG_SEGMENT_XML=${injection_bg_segment_xml:-}
INJECTION_BG_DURATION_SECONDS=
INJECTION_BG_END_GPS=
INJECTION_PIPELINE_MODE=none
if [ "${INJECTION_MODE}" = "True" ]; then
    : "${INJECTION_FILE:?injection_file required when injection_mode=True}"
    : "${INJECTION_BG_DATA_FILE:?injection_bg_data_file required when injection_mode=True}"
    : "${INJECTION_BG_DETRSP_MAP:?injection_bg_detector_response_file required when injection_mode=True}"
    : "${INJECTION_BG_START_GPS:?injection_bg_start_gps required when injection_mode=True}"
    : "${INJECTION_BG_DURATION_HOUR:?injection_bg_duration_hour required when injection_mode=True}"
    : "${INJECTION_BG_SEGMENT_XML:?injection_bg_segment_xml required when injection_mode=True}"
    INJECTION_BG_DURATION_SECONDS=$((INJECTION_BG_DURATION_HOUR * 3600))
    INJECTION_BG_END_GPS=$((INJECTION_BG_START_GPS + INJECTION_BG_DURATION_SECONDS))
    INJECTION_PIPELINE_MODE=blind
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
    log "fetch GitHub ${GITHUB_REMOTE}/${GITHUB_BRANCH}"
    git -C "${SOURCE_ROOT}" fetch "${GITHUB_REMOTE}" "${GITHUB_BRANCH}"
    local remote_head source_head dirty
    remote_head=$(git -C "${SOURCE_ROOT}" rev-parse FETCH_HEAD)
    source_head=$(git -C "${SOURCE_ROOT}" rev-parse HEAD)
    dirty=$(git -C "${SOURCE_ROOT}" status --porcelain --untracked-files=no)
    if [ "${source_head}" != "${remote_head}" ]; then
        log "ERROR source head ${source_head} != GitHub latest ${remote_head}"
        write_status phase=failed reason=source_not_github_latest source_head="${source_head}" github_head="${remote_head}"
        exit 2
    fi
    if [ -n "${dirty}" ]; then
        log "ERROR tracked source worktree is dirty"
        write_status phase=failed reason=source_tracked_dirty source_head="${source_head}" github_head="${remote_head}"
        exit 2
    fi
    if [ ! -x "${SOURCE_ROOT}/install_local/bin/gstlal_inspiral_postcohspiir_online" ]; then
        log "ERROR missing latest install_local runtime under ${SOURCE_ROOT}"
        write_status phase=failed reason=missing_install_local source_head="${source_head}" github_head="${remote_head}"
        exit 2
    fi
    printf '%s\n' "${remote_head}" > "${ROOT}/provenance/github_${GITHUB_BRANCH}_head.txt"
    rm -f "${CRASH_RUNTIME_ROOT}/install"
    ln -s "${SOURCE_ROOT}/install_local" "${CRASH_RUNTIME_ROOT}/install"
    {
        printf 'github_remote=%s\n' "$(git -C "${SOURCE_ROOT}" remote get-url "${GITHUB_REMOTE}")"
        printf 'github_branch=%s\n' "${GITHUB_BRANCH}"
        printf 'github_head=%s\n' "${remote_head}"
        printf 'source_root=%s\n' "${SOURCE_ROOT}"
        printf 'source_head=%s\n' "${source_head}"
        printf 'runtime_install_symlink=%s/install\n' "${CRASH_RUNTIME_ROOT}"
    } > "${ROOT}/provenance/source_and_runtime.env"
    write_status phase=source_ready github_branch="${GITHUB_BRANCH}" github_head="${remote_head}" source_root="${SOURCE_ROOT}" runtime_root="${CRASH_RUNTIME_ROOT}"
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
    python3 "${CRASH_SCRIPT_DIR}/dump_segment_livetime_csv.py" \
        "${SEGMENT_XML}" \
        --output "${LIVETIME_CSV}" \
        > "${CONTROLLER_DIR}/dump_segment_livetime_csv.log" \
        2>&1
    [ -s "${LIVETIME_CSV}" ] || { log "ERROR livetime CSV not created"; write_status phase=failed reason=livetime_csv_missing; exit 2; }
    bash -n "${SCRIPT_DIR}/crashcar_pipeline.sh"
    bash -n "${SCRIPT_DIR}/crashcar_sbatch.sh"
    write_status phase=inputs_validated segment_xml="${SEGMENT_XML}" crashcar_segment_livetime_csv="${LIVETIME_CSV}"
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
        WORKER_COUNT="${WORKER_COUNT}" BANKS_PER_WORKER="${BANKS_PER_WORKER}" \
        SINGLE_ONLY_SECONDS="${SINGLE_ONLY_SECONDS}" SINGLE_ONLY_FRACTION="${SINGLE_ONLY_FRACTION}" \
        HL_UNION_FRACTION="${HL_UNION_FRACTION}" H_ONLY_SECONDS="${H_ONLY_SECONDS}" \
        L_ONLY_SECONDS="${L_ONLY_SECONDS}" HL_SECONDS="${HL_SECONDS}" HL_NONE_SECONDS="${HL_NONE_SECONDS}" \
        FIRST3_H_ONLY_SECONDS="${FIRST3_H_ONLY_SECONDS}" FIRST3_L_ONLY_SECONDS="${FIRST3_L_ONLY_SECONDS}" \
        FIRST3_HL_SECONDS="${FIRST3_HL_SECONDS}" FIRST3_HL_NONE_SECONDS="${FIRST3_HL_NONE_SECONDS}" \
        CRASHCAR_LOG10_FAR_THRESHOLD="${CRASHCAR_LOG10_FAR_THRESHOLD:-90}" \
        CRASHCAR_PRESERVE_TABLE_SINGLE_FAR="${CRASHCAR_PRESERVE_TABLE_SINGLE_FAR:-0}" \
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

submit_job() {
    local template_map="${ARTIFACTS}/crashcar_template_shape_map.csv"
    cd "${RUN_DIR}"
    local job
    job=$(sbatch --parsable \
        --job-name="${SLURM_JOB_NAME}" \
        --time="${SLURM_TIME}" \
        --mem="${SLURM_MEM}" \
        --cpus-per-task="${SLURM_CPUS_PER_TASK}" \
        --gres="${SLURM_GRES}" \
        --array="0-$((WORKER_COUNT - 1))" \
        --export=ALL,TOP_RUN_ROOT="${ROOT}",RUN_DIR="${RUN_DIR}",CRASH_ROOT="${CRASH_RUNTIME_ROOT}",WGUO_O3A_INJECTION_MODE="${INJECTION_PIPELINE_MODE}",WGUO_O3A_INJECTION_FILE="${INJECTION_FILE}",WGUO_O3A_START_GPS="${START_GPS}",WGUO_O3A_END_GPS="${END_GPS}",WGUO_O3A_DETRSP_MAP="${DETRSP_MAP}",WGUO_O3A_FRAME_CACHE="${FRAME_CACHE}",WGUO_O3A_NONINJ_STATS_LOC="${NONINJ_STATS_LOC}",WGUO_O3A_BANK_DIR="${O3_BANK_DIR}",WGUO_O3A_BANKS_PER_GROUP="${BANKS_PER_WORKER}",WGUO_O3A_START_BANK="${START_BANK}",WGUO_O3A_SNAPSHOT_INTERVAL="${SNAPSHOT_INTERVAL}",WGUO_O3A_COLLECT_WALLTIME="${BACKGROUND_ACCUMULATION},${BACKGROUND_ACCUMULATION},${BACKGROUND_ACCUMULATION}",BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION}",FORMAL_BACKGROUND_ACCUMULATION_SECONDS="${BACKGROUND_ACCUMULATION}",CRASHCAR_BACKGROUND_REQUIRED_SECONDS="${BACKGROUND_ACCUMULATION}",BACKGROUND_UPDATE_TRIGGER_SECONDS="${BACKGROUND_UPDATE}",CRASHCAR_SNAPSHOT_INTERVAL_SECONDS="${SNAPSHOT_INTERVAL}",CRASHCAR_LOG10_FAR_THRESHOLD="${CRASHCAR_LOG10_FAR_THRESHOLD:-90}",CRASHCAR_PRESERVE_TABLE_SINGLE_FAR="${CRASHCAR_PRESERVE_TABLE_SINGLE_FAR:-0}",CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME="${template_map}",CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP="${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}",CRASHCAR_CODE_VERSION="${CRASHCAR_CODE_VERSION}",WGUO_O3A_SEGMENT_XML="${SEGMENT_XML}",SEGMENT_XML="${SEGMENT_XML}",SINGLE_SEGMENT_XML="${SEGMENT_XML}",CRASHCAR_SEGMENT_LIVETIME_CSV="${LIVETIME_CSV}" \
        --chdir="${RUN_DIR}" \
        "${SCRIPT_DIR}/crashcar_sbatch.sh")
    printf '%s\n' "${job}" > "${CONTROLLER_DIR}/job_id.txt"
    write_status phase=slurm_submitted job_id="${job}" run_dir="${RUN_DIR}" worker_count="${WORKER_COUNT}" banks_per_worker="${BANKS_PER_WORKER}" background_accumulation_seconds="${BACKGROUND_ACCUMULATION}" background_update_seconds="${BACKGROUND_UPDATE}" injection_mode="${INJECTION_MODE}" injection_pipeline_mode="${INJECTION_PIPELINE_MODE}" single_only_fraction="${SINGLE_ONLY_FRACTION}" hl_union_fraction="${HL_UNION_FRACTION}"
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
            write_status phase=postprocessing_last_bg3h job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}"
            log "slurm completed; building final last-3h background artifacts"
            if postprocess_last_bg3h; then
                raw=$(run_summary_json)
                detail=$(detail_summary_json)
                write_status phase=completed job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}" final_report="${REPORT}" last_bg3h_background="${ARTIFACTS}/crashcar_day1_last_bg3h_full_background.json" last_bg3h_plot="${ARTIFACTS}/crashcar_day1_last_bg3h_background.png" run_summary="${ARTIFACTS}/crashcar_run_summary.json"
                write_final_report completed "${job}" "${sacct_state}" "${raw}" "${detail}"
                log "completed; report=${REPORT}"
                exit 0
            else
                raw=$(run_summary_json)
                detail=$(detail_summary_json)
                write_status phase=failed_postprocess job_id="${job}" sacct="${sacct_state}" raw_stream_summary="${raw}" detail_summary="${detail}" final_report="${REPORT}"
                write_final_report failed_postprocess "${job}" "${sacct_state}" "${raw}" "${detail}"
                exit 4
            fi
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
        injection_mode="${INJECTION_MODE}" \
        injection_pipeline_mode="${INJECTION_PIPELINE_MODE}" \
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
