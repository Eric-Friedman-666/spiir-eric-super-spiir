#!/bin/bash
set -euo pipefail

SCRIPT_DIR=${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
RUN_DIR=${1:-$(pwd)}
cd "${RUN_DIR}"

REQUESTED_SINGLE_INPUT_KIND=${SINGLE_INPUT_KIND:-}
REQUESTED_SINGLE_WORKER_ID=${SINGLE_WORKER_ID:-}
REQUESTED_SINGLE_WORKER_GROUP=${SINGLE_WORKER_GROUP:-}
REQUESTED_SINGLE_WORKER_COUNT=${SINGLE_WORKER_COUNT:-}

if [ -f ./run_config.sh ]; then
    set -a
    source ./run_config.sh
    set +a
elif compgen -G "logs/run_config_*.env" >/dev/null; then
    # The submitted job writes the frozen runtime config here.  Source it when
    # the updater is run manually from an existing result directory.
    set -a
    source "$(ls -t logs/run_config_*.env | head -n 1)"
    set +a
fi
if [ -n "${REQUESTED_SINGLE_INPUT_KIND}" ]; then
    export SINGLE_INPUT_KIND="${REQUESTED_SINGLE_INPUT_KIND}"
fi
if [ -n "${REQUESTED_SINGLE_WORKER_ID}" ]; then
    export SINGLE_WORKER_ID="${REQUESTED_SINGLE_WORKER_ID}"
fi
if [ -n "${REQUESTED_SINGLE_WORKER_GROUP}" ]; then
    export SINGLE_WORKER_GROUP="${REQUESTED_SINGLE_WORKER_GROUP}"
fi
if [ -n "${REQUESTED_SINGLE_WORKER_COUNT}" ]; then
    export SINGLE_WORKER_COUNT="${REQUESTED_SINGLE_WORKER_COUNT}"
fi
export BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS:-10800}
SINGLE_INPUT_KIND=${SINGLE_INPUT_KIND:-singlecsv}

WORKER_ID=${SINGLE_WORKER_ID:-}
WORKER_GROUP=${SINGLE_WORKER_GROUP:-${WORKER_ID}}
WORKER_COUNT=${SINGLE_WORKER_COUNT:-${NODES_AMOUNT:-1}}
if [ -n "${WORKER_ID}" ]; then
    WORKER_TAG="worker_${WORKER_ID}"
    BRANCH_DIR="single_branch/${WORKER_TAG}"
    MONITOR_DIR="monitor/${WORKER_TAG}"
else
    WORKER_TAG=""
    BRANCH_DIR="single_branch"
    MONITOR_DIR="monitor"
fi
mkdir -p "${BRANCH_DIR}" "${MONITOR_DIR}" single_branch monitor
LOCK_DIR="${BRANCH_DIR}/.single_background_update.lockdir"
LOCK_STALE_SECONDS=${SINGLE_UPDATE_LOCK_STALE_SECONDS:-1800}
acquire_lock() {
    if mkdir "${LOCK_DIR}" 2>/dev/null; then
        echo "$$" > "${LOCK_DIR}/pid"
        hostname > "${LOCK_DIR}/host" 2>/dev/null || echo unknown > "${LOCK_DIR}/host"
        return 0
    fi

    lock_pid=$(cat "${LOCK_DIR}/pid" 2>/dev/null || true)
    lock_host=$(cat "${LOCK_DIR}/host" 2>/dev/null || true)
    this_host=$(hostname 2>/dev/null || echo unknown)
    if [ -n "${lock_pid}" ] && [ "${lock_host}" = "${this_host}" ] && kill -0 "${lock_pid}" 2>/dev/null; then
        return 1
    fi

    now=$(date +%s)
    lock_mtime=$(python3 - "${LOCK_DIR}" <<'PY'
import os
import sys
try:
    print(int(os.path.getmtime(sys.argv[1])))
except OSError:
    print(0)
PY
)
    lock_age=$(( now - lock_mtime ))
    if [ "${lock_age}" -ge "${LOCK_STALE_SECONDS}" ]; then
        rm -rf "${LOCK_DIR}"
        mkdir "${LOCK_DIR}" 2>/dev/null || return 1
        echo "$$" > "${LOCK_DIR}/pid"
        hostname > "${LOCK_DIR}/host" 2>/dev/null || echo unknown > "${LOCK_DIR}/host"
        return 0
    fi
    return 1
}

if ! acquire_lock; then
    echo "single-detector updater: another update is already running; skipping this cycle"
    exit 0
fi
trap 'rm -rf "${LOCK_DIR}"' EXIT

if command -v module >/dev/null 2>&1; then
    module load gcc/13.3.0 scipy-bundle/2024.05 >/dev/null 2>&1 || true
fi

FEATURE_CSV="${BRANCH_DIR}/single_trigger_features.csv"
SUMMARY_JSON="${MONITOR_DIR}/latest_single_summary.json"
ASSIGNMENT_FEATURE_CSV="${BRANCH_DIR}/single_trigger_features_assignment_all_visible.csv"
ASSIGNMENT_SUMMARY_JSON="${MONITOR_DIR}/latest_single_assignment_summary.json"
BACKGROUND_JSON="${BRANCH_DIR}/single_far_llr_background.json"
SUPPORT_CSV="${BRANCH_DIR}/single_llr_far_support.csv"
ASSIGNED_CSV="${BRANCH_DIR}/single_final_far_all.csv"
ASSIGNED_CANDIDATES_CSV="${BRANCH_DIR}/single_final_far_latest_candidates.csv"
ASSIGNMENT_LEDGER_JSON="${MONITOR_DIR}/latest_single_assignment_ledger.json"
BACKGROUND_ARCHIVE_DIR="${BRANCH_DIR}/backgrounds"
PLOT_PNG="${BRANCH_DIR}/single_llr_far_background.png"
PLOT_SUMMARY="${MONITOR_DIR}/latest_single_plot_summary.json"
WGUO_BANK_STATS_DIR=${WGUO_BANK_STATS_DIR:-/fred/oz016/wguo/packages/spiir/src/spiir/search/bank_dofs}
NOISE_BETA=${NOISE_BETA:--1.0}
RANK_OFFSET=${RANK_OFFSET:-0.0}
DOF=${DOF:-${CRASHCAR_DOF:-}}
: "${DOF:?DOF or CRASHCAR_DOF is required}"
PLOT_LLR_MIN=${PLOT_LLR_MIN:--10}
TAIL_LOG10_FAR=${TAIL_LOG10_FAR:--2.0}
FAR_FIT_BOUNDARY=${FAR_FIT_BOUNDARY:-0.01}
SINGLE_BACKGROUND_MODE=${SINGLE_BACKGROUND_MODE:-rolling}
SINGLE_FROZEN_BACKGROUND_JSON=${SINGLE_FROZEN_BACKGROUND_JSON:-}
SINGLE_FROZEN_BACKGROUND_RUN_DIR=${SINGLE_FROZEN_BACKGROUND_RUN_DIR:-}
SINGLE_FROZEN_BACKGROUND_ID=${SINGLE_FROZEN_BACKGROUND_ID:-BG-FROZEN}
SINGLE_FROZEN_BACKGROUND_SOURCE=${SINGLE_FROZEN_BACKGROUND_SOURCE:-}
SINGLE_SEGMENT_XML=${SINGLE_SEGMENT_XML:-${SEGMENT_XML:-${WGUO_O3A_SEGMENT_XML:-}}}

segment_xml_args=()
if [ -n "${SINGLE_SEGMENT_XML}" ]; then
    IFS=',' read -r -a segment_xml_paths <<< "${SINGLE_SEGMENT_XML}"
    for segment_xml_path in "${segment_xml_paths[@]}"; do
        if [ -n "${segment_xml_path}" ]; then
            segment_xml_args+=(--segment-xml "${segment_xml_path}")
        fi
    done
fi

case "${SINGLE_BACKGROUND_MODE}" in
    rolling|frozen) ;;
    *)
        printf 'single-detector updater: invalid SINGLE_BACKGROUND_MODE=%s; expected rolling or frozen\n' \
            "${SINGLE_BACKGROUND_MODE}" >&2
        exit 2
        ;;
esac

snapshot_globs=()
if [ -n "${WORKER_ID}" ]; then
    while IFS= read -r pattern; do
        snapshot_globs+=("${pattern}")
        done < <(python3 - "${WORKER_GROUP}" "${MAX_GROUP:-0}" "${SINGLE_INPUT_KIND}" <<'PY'
import sys
worker_group = int(sys.argv[1])
max_group = int(sys.argv[2])
kind = sys.argv[3]
if kind == "sdpostcoh":
    glob_name = "sdpostcoh*.xml.gz"
elif kind in ("singlecsv", "singletriggers"):
    glob_name = "*_single_triggers.csv"
elif kind == "crashcarcsv":
    glob_name = f"../crashcar_singlefar_detail_worker{worker_group:03d}.csv"
else:
    glob_name = "*_zerolag_*.xml.gz"
if 0 <= worker_group <= max_group:
    print(f"{worker_group:03d}/{glob_name}")
PY
)
else
    if [ "${SINGLE_INPUT_KIND}" = "sdpostcoh" ]; then
        snapshot_globs=("[0-9][0-9][0-9]/sdpostcoh*.xml.gz")
    elif [ "${SINGLE_INPUT_KIND}" = "singlecsv" ] || [ "${SINGLE_INPUT_KIND}" = "singletriggers" ]; then
        snapshot_globs=("[0-9][0-9][0-9]/*_single_triggers.csv")
    elif [ "${SINGLE_INPUT_KIND}" = "crashcarcsv" ]; then
        snapshot_globs=("crashcar_singlefar_detail_worker*.csv")
    else
        snapshot_globs=("[0-9][0-9][0-9]/*_zerolag_*.xml.gz")
    fi
fi

extract_args=()
for pattern in "${snapshot_globs[@]}"; do
    extract_args+=(--glob "${pattern}")
done
extract_args+=(
    --output "${FEATURE_CSV}"
    --summary "${SUMMARY_JSON}"
    --min-snr 4
    --banks-per-group "${BANKS_PER_GROUP:-6}"
)

merge_worker_outputs() {
    if [ -n "${WORKER_ID}" ] && [ "${MERGE_WORKER_FAR_OUTPUTS:-1}" = "1" ]; then
        python3 "${SCRIPT_DIR:-.}/merge_worker_far_ledgers.py" \
            --run-dir "${RUN_DIR}" \
            --worker-count "${WORKER_COUNT}" \
            --output single_branch/single_final_far_all.csv \
            --candidate-output single_branch/single_final_far_latest_candidates.csv \
            --summary monitor/latest_single_background_status.json \
            --plot-summary monitor/latest_single_plot_summary.json || true
    fi
}

resolve_frozen_background_source() {
    if [ -n "${SINGLE_FROZEN_BACKGROUND_JSON}" ]; then
        printf '%s\n' "${SINGLE_FROZEN_BACKGROUND_JSON}"
        return 0
    fi
    if [ -n "${SINGLE_FROZEN_BACKGROUND_RUN_DIR}" ]; then
        if [ -n "${WORKER_TAG}" ]; then
            printf '%s\n' "${SINGLE_FROZEN_BACKGROUND_RUN_DIR}/single_branch/${WORKER_TAG}/single_far_llr_background.json"
        else
            printf '%s\n' "${SINGLE_FROZEN_BACKGROUND_RUN_DIR}/single_branch/single_far_llr_background.json"
        fi
        return 0
    fi
    return 1
}

write_frozen_blocked_status() {
    local reason=$1
    local source=${2:-}
    WORKER_ID="${WORKER_ID}" WORKER_GROUP="${WORKER_GROUP}" WORKER_COUNT="${WORKER_COUNT}" STATUS_JSON="${MONITOR_DIR}/latest_single_background_status.json" REASON="${reason}" FROZEN_SOURCE="${source}" python3 - <<'PY'
import json
import os
import pathlib
import time

status = {
    "worker_id": os.environ.get("WORKER_ID") or None,
    "worker_group": os.environ.get("WORKER_GROUP") or None,
    "worker_count": os.environ.get("WORKER_COUNT") or None,
    "background_mode": "frozen",
    "background_accumulation_disabled": True,
    "background_ready": False,
    "background_file": None,
    "assigned_file": None,
    "support_file": None,
    "plot_file": None,
    "support_points": 0,
    "assigned_points": 0,
    "formal_assigned_far_rows_H1": 0,
    "formal_assigned_far_rows_L1": 0,
    "formal_assigned_far_rows_total": 0,
    "far_assignment_blocked": True,
    "calculated_far_blocked": True,
    "fixed_background_source": os.environ.get("FROZEN_SOURCE") or None,
    "reason": os.environ["REASON"],
    "updated_unix": time.time(),
}
path = pathlib.Path(os.environ["STATUS_JSON"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
print(json.dumps(status, sort_keys=True))
PY
}

if [ "${ONLINE_REPLAY_SYNC:-0}" = "1" ] && [ "${SINGLE_IGNORE_ONLINE_REPLAY_GATE:-0}" != "1" ] && [ -z "${ONLINE_REPLAY_START_WALL:-}" ]; then
    export ONLINE_REPLAY_START_WALL=$(date +%s)
fi

eval "$(python3 - "${snapshot_globs[@]}" <<'PY'
import csv
import glob
import math
import os
import re
import time
import sys

snapshot_re = re.compile(r"(?:_zerolag_|sdpostcoh[^/_]*_|single_postcoh[^/_]*_)(\d+)_(\d+)\.xml(?:\.gz)?$")

raw_latest_end = None
filenames = []
for pattern in sys.argv[1:]:
    filenames.extend(glob.glob(pattern))
for filename in sorted(set(filenames)):
    match = snapshot_re.search(filename)
    if match:
        end = float(int(match.group(1)) + int(match.group(2)))
    elif filename.endswith(".csv"):
        end = None
        try:
            with open(filename, newline="") as input_file:
                for row in csv.DictReader(input_file):
                    value = row.get("end_time")
                    try:
                        row_end = float(value) if value not in (None, "") else None
                    except ValueError:
                        row_end = None
                    if row_end is not None and (end is None or row_end > end):
                        end = row_end
        except OSError:
            continue
        if end is None:
            continue
    else:
        continue
    if raw_latest_end is None or end > raw_latest_end:
        raw_latest_end = end

if (filenames and os.environ.get("SINGLE_INPUT_KIND") == "crashcarcsv"
        and os.environ.get("CRASHCAR_FINAL_POSTPROCESS") == "1"
        and os.environ.get("DATA_END_TIME")):
    try:
        raw_latest_end = float(os.environ["DATA_END_TIME"])
    except ValueError:
        pass

online_upper = None
ignore_online_gate = os.environ.get("SINGLE_IGNORE_ONLINE_REPLAY_GATE", "0") == "1"
if not ignore_online_gate and os.environ.get("ONLINE_REPLAY_SYNC", "0") == "1":
    start_gps = float(os.environ.get("ONLINE_REPLAY_START_GPS") or os.environ["DATA_START_TIME"])
    start_wall = float(os.environ.get("ONLINE_REPLAY_START_WALL") or time.time())
    rate = float(os.environ.get("ONLINE_REPLAY_RATE", "1.0"))
    lag = float(os.environ.get("ONLINE_REPLAY_ALLOWED_LAG_SECONDS", "0"))
    elapsed = max(0.0, time.time() - start_wall)
    online_upper = start_gps + elapsed * rate + lag

assignment_upper = raw_latest_end
if online_upper is not None:
    assignment_upper = min(online_upper, raw_latest_end) if raw_latest_end is not None else online_upper

window = float(os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0)
configured_start = os.environ.get("DATA_START_TIME")
update = float(os.environ.get("BACKGROUND_UPDATE_TRIGGER_SECONDS", "3600") or 3600.0)
upper = assignment_upper
if upper is not None and configured_start and update > 0.0:
    first_full_end = float(configured_start) + window
    if upper >= first_full_end:
        steps = math.floor((upper - first_full_end + 1.0e-6) / update)
        upper = first_full_end + steps * update

lower = None
if upper is not None and window > 0:
    lower = upper - window
    if configured_start:
        lower = max(lower, float(configured_start))

def emit(name, value):
    if value is None:
        print(f"{name}=")
    else:
        print(f"{name}={value}")

emit("min_snapshot_end_gps", lower)
emit("max_snapshot_end_gps", upper)
emit("assignment_max_snapshot_end_gps", assignment_upper)
emit("background_window_seconds", window)
PY
)"

if [ -n "${ONLINE_REPLAY_START_WALL:-}" ]; then
    export ONLINE_REPLAY_START_WALL
fi

if [ "${SINGLE_INPUT_KIND}" = "crashcarcsv" ]; then
    crashcar_extract_args=()
    for pattern in "${snapshot_globs[@]}"; do
        crashcar_extract_args+=(--glob "${pattern}")
    done
    crashcar_extract_args+=(
        --output "${ASSIGNMENT_FEATURE_CSV}"
        --summary "${ASSIGNMENT_SUMMARY_JSON}"
        --min-snr 4
        --banks-per-group "${BANKS_PER_GROUP:-6}"
    )
    if [ -n "${assignment_max_snapshot_end_gps:-}" ]; then
        crashcar_extract_args+=(--max-snapshot-end-gps "${assignment_max_snapshot_end_gps}")
    fi
    if [ -n "${min_snapshot_end_gps:-}" ]; then
        crashcar_extract_args+=(--min-snapshot-end-gps "${min_snapshot_end_gps}")
    fi

    python3 "${SCRIPT_DIR:-.}/extract_crashcar_detail_features.py" "${crashcar_extract_args[@]}"
    cp "${ASSIGNMENT_FEATURE_CSV}" "${FEATURE_CSV}"
    cp "${ASSIGNMENT_SUMMARY_JSON}" "${SUMMARY_JSON}"

    foreground_feature_count=$(FEATURE_CSV="${ASSIGNMENT_FEATURE_CSV}" python3 - <<'PY'
import csv
import os
count = 0
try:
    with open(os.environ["FEATURE_CSV"], newline="") as handle:
        for _row in csv.DictReader(handle):
            count += 1
except FileNotFoundError:
    pass
print(count)
PY
    )

    if [ "${SINGLE_BACKGROUND_MODE}" = "frozen" ]; then
        frozen_source=$(resolve_frozen_background_source || true)
        if [ -f "${BACKGROUND_JSON}" ]; then
            frozen_input="${BACKGROUND_JSON}"
        elif [ -n "${frozen_source}" ] && [ -f "${frozen_source}" ]; then
            cp "${frozen_source}" "${BACKGROUND_JSON}"
            frozen_input="${BACKGROUND_JSON}"
        else
            write_frozen_blocked_status \
                "SINGLE_BACKGROUND_MODE=frozen requires an existing no-injection background; set SINGLE_FROZEN_BACKGROUND_JSON or SINGLE_FROZEN_BACKGROUND_RUN_DIR before assigning injection triggers" \
                "${frozen_source}"
            merge_worker_outputs
            exit 2
        fi

        ledger_args=(
            --feature-csv "${ASSIGNMENT_FEATURE_CSV}"
            --output "${ASSIGNED_CSV}"
            --candidate-output "${ASSIGNED_CANDIDATES_CSV}"
            --summary "${ASSIGNMENT_LEDGER_JSON}"
            --ifos H1,L1
            --min-snr 4
            --background-window-seconds "${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
            --background-required-seconds "${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
            --background-update-seconds "${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}"
            --initial-window-policy "${FAR_INITIAL_WINDOW_POLICY:-skip}"
            --bank-stats-dir "${WGUO_BANK_STATS_DIR}"
            --dof "${DOF}"
            --noise-beta "${NOISE_BETA}"
            --rank-offset "${RANK_OFFSET}"
            --fit-min-points 20
            --far-fit-boundary "${FAR_FIT_BOUNDARY}"
            --fixed-background-input "${frozen_input}"
            --fixed-background-id "${SINGLE_FROZEN_BACKGROUND_ID}"
            --fixed-background-source "${SINGLE_FROZEN_BACKGROUND_SOURCE:-${frozen_source}}"
        )
        if [ "${#segment_xml_args[@]}" -gt 0 ]; then
            ledger_args+=("${segment_xml_args[@]}")
        fi
        if [ -n "${DATA_START_TIME:-}" ]; then
            ledger_args+=(--data-start-gps "${DATA_START_TIME}")
        fi
        if [ "${PREFER_FEATURE_SINGLE_FAR:-0}" = "1" ]; then
            ledger_args+=(--prefer-feature-single-far)
        fi

        python3 "${SCRIPT_DIR:-.}/assign_frozen_far_ledger.py" "${ledger_args[@]}"

        python3 "${SCRIPT_DIR:-.}/plot_single_llr_far.py" \
            --background "${BACKGROUND_JSON}" \
            --assigned "${ASSIGNED_CSV}" \
            --output "${PLOT_PNG}" \
            --summary "${PLOT_SUMMARY}" \
            --llr-min "${PLOT_LLR_MIN}" \
            --tail-log10-far "${TAIL_LOG10_FAR}"

        WORKER_ID="${WORKER_ID}" WORKER_GROUP="${WORKER_GROUP}" WORKER_COUNT="${WORKER_COUNT}" ASSIGNMENT_SUMMARY_JSON="${ASSIGNMENT_SUMMARY_JSON}" ASSIGNMENT_LEDGER_JSON="${ASSIGNMENT_LEDGER_JSON}" PLOT_SUMMARY="${PLOT_SUMMARY}" STATUS_JSON="${MONITOR_DIR}/latest_single_background_status.json" BACKGROUND_JSON="${BACKGROUND_JSON}" ASSIGNED_CSV="${ASSIGNED_CSV}" SUPPORT_CSV="${SUPPORT_CSV}" PLOT_PNG="${PLOT_PNG}" ASSIGNMENT_FEATURE_CSV="${ASSIGNMENT_FEATURE_CSV}" FROZEN_SOURCE="${frozen_source}" python3 - <<'PY'
import csv
import json
import os
import pathlib
import time

assignment_summary_path = pathlib.Path(os.environ["ASSIGNMENT_SUMMARY_JSON"])
assignment_summary = (
    json.loads(assignment_summary_path.read_text())
    if assignment_summary_path.exists() else {})
ledger = json.loads(pathlib.Path(os.environ["ASSIGNMENT_LEDGER_JSON"]).read_text())
plot = json.loads(pathlib.Path(os.environ["PLOT_SUMMARY"]).read_text())
counts = {"H1": 0, "L1": 0, "total": 0}
with pathlib.Path(os.environ["ASSIGNED_CSV"]).open(newline="") as handle:
    reader = csv.DictReader(handle)
    for row in reader:
        ifo = (row.get("ifo") or row.get("ifos") or "").strip()
        if ifo in ("H1", "L1"):
            counts[ifo] += 1
        counts["total"] += 1
support_path = pathlib.Path(os.environ["SUPPORT_CSV"])
summary = dict(assignment_summary)
summary.update({
    "worker_id": os.environ.get("WORKER_ID") or None,
    "worker_group": os.environ.get("WORKER_GROUP") or None,
    "worker_count": os.environ.get("WORKER_COUNT") or None,
    "input_kind": "crashcarcsv",
    "background_mode": "frozen",
    "background_accumulation_disabled": True,
    "background_ready": True,
    "background_file": os.environ["BACKGROUND_JSON"],
    "fixed_background_file": os.environ["BACKGROUND_JSON"],
    "fixed_background_source": os.environ.get("FROZEN_SOURCE") or None,
    "assigned_file": os.environ["ASSIGNED_CSV"],
    "support_file": str(support_path) if support_path.exists() else None,
    "plot_file": os.environ["PLOT_PNG"],
    "support_points": plot.get("support_points"),
    "assigned_points": plot.get("assigned_points"),
    "assignment_feature_file": os.environ["ASSIGNMENT_FEATURE_CSV"],
    "assignment_feature_rows_total": assignment_summary.get("feature_rows_total"),
    "assignment_input_files": assignment_summary.get("input_files"),
    "assignment_files": assignment_summary.get("files"),
    "assignment_gps_start_utc": (
        assignment_summary.get("data_gps_start_utc")
        or assignment_summary.get("gps_start_utc")),
    "assignment_gps_end_utc": (
        assignment_summary.get("data_gps_end_utc")
        or assignment_summary.get("gps_end_utc")),
    "assignment_new_rows": ledger.get("newly_assigned_rows"),
    "assignment_duplicate_candidate_rows": ledger.get("duplicate_candidate_rows"),
    "assignment_skipped_not_ready_rows": ledger.get("skipped_not_ready_rows"),
    "assignment_deferred_window_rows": ledger.get("deferred_window_rows"),
    "assignment_background_windows_used": ledger.get("background_windows_used"),
    "assignment_background_files": ledger.get("background_files"),
    "assignment_policy": ledger.get("policy"),
    "formal_assigned_far_rows_H1": counts["H1"],
    "formal_assigned_far_rows_L1": counts["L1"],
    "formal_assigned_far_rows_total": counts["total"],
    "far_assignment_blocked": False,
    "calculated_far_blocked": False,
    "updated_unix": time.time(),
})
pathlib.Path(os.environ["STATUS_JSON"]).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps(summary, sort_keys=True))
PY

        merge_worker_outputs
        exit 0
    fi

    background_duration=$(SUMMARY_JSON="${ASSIGNMENT_SUMMARY_JSON}" python3 - <<'PY'
import json
import os
try:
    with open(os.environ["SUMMARY_JSON"]) as handle:
        data = json.load(handle)
except FileNotFoundError:
    data = {}
print(float(data.get("background_duration_seconds") or data.get("duration_seconds") or 0.0))
PY
    )
    background_required=$(python3 - <<'PY'
import os
print(float(os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0))
PY
    )
    background_is_ready=$(python3 - <<PY
duration = float("${background_duration}")
required = float("${background_required}")
print("1" if duration >= required else "0")
PY
    )

    if [ "${background_is_ready}" != "1" ] || [ "${foreground_feature_count}" -le 30 ]; then
        WORKER_COUNT="${WORKER_COUNT}" SUMMARY_JSON="${ASSIGNMENT_SUMMARY_JSON}" STATUS_JSON="${MONITOR_DIR}/latest_single_background_status.json" FOREGROUND_FEATURE_COUNT="${foreground_feature_count}" BACKGROUND_READY="${background_is_ready}" python3 - <<'PY'
import json
import os
import pathlib
import time

summary_path = pathlib.Path(os.environ["SUMMARY_JSON"])
summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
required = float(os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0)
duration = float(summary.get("background_duration_seconds") or summary.get("duration_seconds") or 0.0)
foreground = int(os.environ.get("FOREGROUND_FEATURE_COUNT") or 0)
if os.environ.get("BACKGROUND_READY") != "1":
    reason = (
        f"crashcar global detail background window {duration:.1f}s is below "
        f"BACKGROUND_ACCUMULATION_SECONDS={required:.1f}s"
    )
else:
    reason = (
        "not enough crashcar detector-local detail rows for a stable cold-start "
        f"FAR-LLR support curve; have {foreground} rows"
    )
summary.update({
    "worker_id": None,
    "worker_group": None,
    "worker_count": os.environ.get("WORKER_COUNT") or None,
    "input_kind": "crashcarcsv",
    "background_ready": False,
    "background_file": None,
    "assigned_file": None,
    "support_file": None,
    "plot_file": None,
    "support_points": 0,
    "assigned_points": 0,
    "formal_assigned_far_rows_H1": 0,
    "formal_assigned_far_rows_L1": 0,
    "formal_assigned_far_rows_total": 0,
    "foreground_feature_rows_total": foreground,
    "accumulated_background_time_seconds": duration,
    "accumulated_background_time_hours": duration / 3600.0 if duration else 0.0,
    "background_accumulation_seconds_required": required,
    "far_assignment_blocked": True,
    "calculated_far_blocked": True,
    "reason": reason,
    "updated_unix": time.time(),
})
pathlib.Path(os.environ["STATUS_JSON"]).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps(summary, sort_keys=True))
PY
        exit 0
    fi

    ledger_args=(
        --feature-csv "${ASSIGNMENT_FEATURE_CSV}"
        --output "${ASSIGNED_CSV}"
        --candidate-output "${ASSIGNED_CANDIDATES_CSV}"
        --summary "${ASSIGNMENT_LEDGER_JSON}"
        --ifos H1,L1
        --min-snr 4
        --background-window-seconds "${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
        --background-required-seconds "${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
        --background-update-seconds "${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}"
        --initial-window-policy "${FAR_INITIAL_WINDOW_POLICY:-skip}"
        --bank-stats-dir "${WGUO_BANK_STATS_DIR}"
        --dof "${DOF}"
        --noise-beta "${NOISE_BETA}"
        --rank-offset "${RANK_OFFSET}"
        --fit-min-points 20
        --far-fit-boundary "${FAR_FIT_BOUNDARY}"
        --max-new-windows-per-run "${ASSIGNMENT_MAX_NEW_WINDOWS_PER_RUN:-20}"
        --background-archive-dir "${BACKGROUND_ARCHIVE_DIR}"
    )
    if [ "${#segment_xml_args[@]}" -gt 0 ]; then
        ledger_args+=("${segment_xml_args[@]}")
    fi
    if [ -n "${DATA_START_TIME:-}" ]; then
        ledger_args+=(--data-start-gps "${DATA_START_TIME}")
    fi
    if [ "${PREFER_FEATURE_SINGLE_FAR:-0}" = "1" ]; then
        ledger_args+=(--prefer-feature-single-far)
    fi

    python3 "${SCRIPT_DIR:-.}/assign_frozen_far_ledger.py" "${ledger_args[@]}"

    latest_background=$(ASSIGNMENT_LEDGER_JSON="${ASSIGNMENT_LEDGER_JSON}" python3 - <<'PY'
import json
import os
import pathlib
path = pathlib.Path(os.environ["ASSIGNMENT_LEDGER_JSON"])
if not path.exists():
    raise SystemExit(0)
data = json.loads(path.read_text())
files = data.get("background_files") or []
if not files:
    print("")
else:
    latest = files[-1]
    if isinstance(latest, dict):
        print(latest.get("background_file") or "")
    else:
        print(latest)
PY
    )
    if [ -n "${latest_background}" ] && [ -f "${latest_background}" ]; then
        cp "${latest_background}" "${BACKGROUND_JSON}"
    fi

    if [ -f "${BACKGROUND_JSON}" ]; then
        python3 "${SCRIPT_DIR:-.}/plot_single_llr_far.py" \
            --background "${BACKGROUND_JSON}" \
            --assigned "${ASSIGNED_CSV}" \
            --output "${PLOT_PNG}" \
            --summary "${PLOT_SUMMARY}" \
            --llr-min "${PLOT_LLR_MIN}" \
            --tail-log10-far "${TAIL_LOG10_FAR}"
    else
        python3 - <<PY
import json, pathlib
path = pathlib.Path("${PLOT_SUMMARY}")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({"support_points": 0, "assigned_points": 0}, sort_keys=True) + "\\n")
PY
    fi

    WORKER_COUNT="${WORKER_COUNT}" SUMMARY_JSON="${SUMMARY_JSON}" ASSIGNMENT_SUMMARY_JSON="${ASSIGNMENT_SUMMARY_JSON}" ASSIGNMENT_LEDGER_JSON="${ASSIGNMENT_LEDGER_JSON}" PLOT_SUMMARY="${PLOT_SUMMARY}" STATUS_JSON="${MONITOR_DIR}/latest_single_background_status.json" BACKGROUND_JSON="${BACKGROUND_JSON}" ASSIGNED_CSV="${ASSIGNED_CSV}" SUPPORT_CSV="${SUPPORT_CSV}" PLOT_PNG="${PLOT_PNG}" ASSIGNMENT_FEATURE_CSV="${ASSIGNMENT_FEATURE_CSV}" python3 - <<'PY'
import csv
import json
import os
import pathlib
import time

summary_path = pathlib.Path(os.environ["SUMMARY_JSON"])
summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
assignment_summary_path = pathlib.Path(os.environ["ASSIGNMENT_SUMMARY_JSON"])
assignment_summary = (
    json.loads(assignment_summary_path.read_text())
    if assignment_summary_path.exists() else {})
ledger_path = pathlib.Path(os.environ["ASSIGNMENT_LEDGER_JSON"])
ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
plot_path = pathlib.Path(os.environ["PLOT_SUMMARY"])
plot = json.loads(plot_path.read_text()) if plot_path.exists() else {}
counts = {"H1": 0, "L1": 0, "total": 0}
assigned_path = pathlib.Path(os.environ["ASSIGNED_CSV"])
if assigned_path.exists():
    with assigned_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            ifo = (row.get("ifo") or row.get("ifos") or "").strip()
            if ifo in ("H1", "L1"):
                counts[ifo] += 1
            counts["total"] += 1
support_path = pathlib.Path(os.environ["SUPPORT_CSV"])
background_path = pathlib.Path(os.environ["BACKGROUND_JSON"])
summary.update({
    "worker_id": None,
    "worker_group": None,
    "worker_count": os.environ.get("WORKER_COUNT") or None,
    "input_kind": "crashcarcsv",
    "background_ready": background_path.exists(),
    "background_file": os.environ["BACKGROUND_JSON"] if background_path.exists() else None,
    "assigned_file": os.environ["ASSIGNED_CSV"] if assigned_path.exists() else None,
    "support_file": str(support_path) if support_path.exists() else None,
    "plot_file": os.environ["PLOT_PNG"] if pathlib.Path(os.environ["PLOT_PNG"]).exists() else None,
    "support_points": plot.get("support_points"),
    "assigned_points": plot.get("assigned_points"),
    "assignment_feature_file": os.environ["ASSIGNMENT_FEATURE_CSV"],
    "assignment_feature_rows_total": assignment_summary.get("feature_rows_total"),
    "assignment_input_files": assignment_summary.get("input_files"),
    "assignment_files": assignment_summary.get("files"),
    "assignment_gps_start_utc": assignment_summary.get("data_gps_start_utc"),
    "assignment_gps_end_utc": assignment_summary.get("data_gps_end_utc"),
    "assignment_new_rows": ledger.get("newly_assigned_rows"),
    "assignment_duplicate_candidate_rows": ledger.get("duplicate_candidate_rows"),
    "assignment_skipped_not_ready_rows": ledger.get("skipped_not_ready_rows"),
    "assignment_deferred_window_rows": ledger.get("deferred_window_rows"),
    "assignment_background_windows_used": ledger.get("background_windows_used"),
    "assignment_background_files": ledger.get("background_files"),
    "assignment_max_new_windows_per_run": ledger.get("max_new_windows_per_run"),
    "assignment_policy": ledger.get("policy"),
    "formal_assigned_far_rows_H1": counts["H1"],
    "formal_assigned_far_rows_L1": counts["L1"],
    "formal_assigned_far_rows_total": counts["total"],
    "accumulated_background_time_seconds": summary.get("duration_seconds"),
    "accumulated_background_time_hours": summary.get("duration_hours"),
    "background_accumulation_seconds_required": float(os.environ.get(
        "BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0),
    "far_assignment_blocked": False,
    "calculated_far_blocked": False,
    "updated_unix": time.time(),
})
pathlib.Path(os.environ["STATUS_JSON"]).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps(summary, sort_keys=True))
PY
    exit 0
fi

if [ "${SINGLE_BACKGROUND_MODE}" = "frozen" ]; then
    assignment_extract_args=()
    for pattern in "${snapshot_globs[@]}"; do
        assignment_extract_args+=(--glob "${pattern}")
    done
    assignment_extract_args+=(
        --output "${ASSIGNMENT_FEATURE_CSV}"
        --summary "${ASSIGNMENT_SUMMARY_JSON}"
        --min-snr 4
        --banks-per-group "${BANKS_PER_GROUP:-6}"
    )
    if [ -n "${assignment_max_snapshot_end_gps:-}" ]; then
        assignment_extract_args+=(--max-snapshot-end-gps "${assignment_max_snapshot_end_gps}")
    fi
    if [ -n "${min_snapshot_end_gps:-}" ]; then
        assignment_extract_args+=(--min-snapshot-end-gps "${min_snapshot_end_gps}")
    fi

    python3 "${SCRIPT_DIR:-.}/extract_zerolag_features.py" "${assignment_extract_args[@]}"

    frozen_source=$(resolve_frozen_background_source || true)
    if [ -f "${BACKGROUND_JSON}" ]; then
        frozen_input="${BACKGROUND_JSON}"
    elif [ -n "${frozen_source}" ] && [ -f "${frozen_source}" ]; then
        cp "${frozen_source}" "${BACKGROUND_JSON}"
        frozen_input="${BACKGROUND_JSON}"
    else
        write_frozen_blocked_status \
            "SINGLE_BACKGROUND_MODE=frozen requires an existing no-injection background; set SINGLE_FROZEN_BACKGROUND_JSON or SINGLE_FROZEN_BACKGROUND_RUN_DIR before assigning injection triggers" \
            "${frozen_source}"
        merge_worker_outputs
        exit 2
    fi

    ledger_args=(
        --feature-csv "${ASSIGNMENT_FEATURE_CSV}"
        --output "${ASSIGNED_CSV}"
        --candidate-output "${ASSIGNED_CANDIDATES_CSV}"
        --summary "${ASSIGNMENT_LEDGER_JSON}"
        --ifos H1,L1
        --min-snr 4
        --background-window-seconds "${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
        --background-required-seconds "${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
        --background-update-seconds "${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}"
        --initial-window-policy "${FAR_INITIAL_WINDOW_POLICY:-skip}"
        --bank-stats-dir "${WGUO_BANK_STATS_DIR}"
        --dof "${DOF}"
        --noise-beta "${NOISE_BETA}"
        --rank-offset "${RANK_OFFSET}"
        --fit-min-points 20
        --far-fit-boundary "${FAR_FIT_BOUNDARY}"
        --fixed-background-input "${frozen_input}"
        --fixed-background-id "${SINGLE_FROZEN_BACKGROUND_ID}"
        --fixed-background-source "${SINGLE_FROZEN_BACKGROUND_SOURCE:-${frozen_source}}"
    )
    if [ "${#segment_xml_args[@]}" -gt 0 ]; then
        ledger_args+=("${segment_xml_args[@]}")
    fi

    python3 "${SCRIPT_DIR:-.}/assign_frozen_far_ledger.py" "${ledger_args[@]}"

    python3 "${SCRIPT_DIR:-.}/plot_single_llr_far.py" \
        --background "${BACKGROUND_JSON}" \
        --assigned "${ASSIGNED_CSV}" \
        --output "${PLOT_PNG}" \
        --summary "${PLOT_SUMMARY}" \
        --llr-min "${PLOT_LLR_MIN}" \
        --tail-log10-far "${TAIL_LOG10_FAR}"

    WORKER_ID="${WORKER_ID}" WORKER_GROUP="${WORKER_GROUP}" WORKER_COUNT="${WORKER_COUNT}" ASSIGNMENT_SUMMARY_JSON="${ASSIGNMENT_SUMMARY_JSON}" ASSIGNMENT_LEDGER_JSON="${ASSIGNMENT_LEDGER_JSON}" PLOT_SUMMARY="${PLOT_SUMMARY}" STATUS_JSON="${MONITOR_DIR}/latest_single_background_status.json" BACKGROUND_JSON="${BACKGROUND_JSON}" ASSIGNED_CSV="${ASSIGNED_CSV}" SUPPORT_CSV="${SUPPORT_CSV}" PLOT_PNG="${PLOT_PNG}" ASSIGNMENT_FEATURE_CSV="${ASSIGNMENT_FEATURE_CSV}" FROZEN_SOURCE="${frozen_source}" python3 - <<'PY'
import csv
import json
import os
import pathlib
import time

assignment_summary_path = pathlib.Path(os.environ["ASSIGNMENT_SUMMARY_JSON"])
assignment_summary = (
    json.loads(assignment_summary_path.read_text())
    if assignment_summary_path.exists() else {})
ledger = json.loads(pathlib.Path(os.environ["ASSIGNMENT_LEDGER_JSON"]).read_text())
plot = json.loads(pathlib.Path(os.environ["PLOT_SUMMARY"]).read_text())
counts = {"H1": 0, "L1": 0, "total": 0}
with pathlib.Path(os.environ["ASSIGNED_CSV"]).open(newline="") as handle:
    reader = csv.DictReader(handle)
    for row in reader:
        ifo = (row.get("ifo") or row.get("ifos") or "").strip()
        if ifo in ("H1", "L1"):
            counts[ifo] += 1
        counts["total"] += 1
support_path = pathlib.Path(os.environ["SUPPORT_CSV"])
summary = dict(assignment_summary)
summary.update({
    "worker_id": os.environ.get("WORKER_ID") or None,
    "worker_group": os.environ.get("WORKER_GROUP") or None,
    "worker_count": os.environ.get("WORKER_COUNT") or None,
    "background_mode": "frozen",
    "background_accumulation_disabled": True,
    "background_ready": True,
    "background_file": os.environ["BACKGROUND_JSON"],
    "fixed_background_file": os.environ["BACKGROUND_JSON"],
    "fixed_background_source": os.environ.get("FROZEN_SOURCE") or None,
    "assigned_file": os.environ["ASSIGNED_CSV"],
    "support_file": str(support_path) if support_path.exists() else None,
    "plot_file": os.environ["PLOT_PNG"],
    "support_points": plot.get("support_points"),
    "assigned_points": plot.get("assigned_points"),
    "assignment_feature_file": os.environ["ASSIGNMENT_FEATURE_CSV"],
    "assignment_feature_rows_total": assignment_summary.get("feature_rows_total"),
    "assignment_input_files": assignment_summary.get("input_files"),
    "assignment_files": assignment_summary.get("files"),
    "assignment_gps_start_utc": assignment_summary.get("gps_start_utc"),
    "assignment_gps_end_utc": assignment_summary.get("gps_end_utc"),
    "assignment_new_rows": ledger.get("newly_assigned_rows"),
    "assignment_duplicate_candidate_rows": ledger.get("duplicate_candidate_rows"),
    "assignment_skipped_not_ready_rows": ledger.get("skipped_not_ready_rows"),
    "assignment_deferred_window_rows": ledger.get("deferred_window_rows"),
    "assignment_background_windows_used": ledger.get("background_windows_used"),
    "assignment_background_files": ledger.get("background_files"),
    "assignment_policy": ledger.get("policy"),
    "formal_assigned_far_rows_H1": counts["H1"],
    "formal_assigned_far_rows_L1": counts["L1"],
    "formal_assigned_far_rows_total": counts["total"],
    "far_assignment_blocked": False,
    "calculated_far_blocked": False,
    "updated_unix": time.time(),
})
pathlib.Path(os.environ["STATUS_JSON"]).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps(summary, sort_keys=True))
PY

    merge_worker_outputs
    exit 0
fi

if [ -n "${min_snapshot_end_gps:-}" ]; then
    extract_args+=(--min-snapshot-end-gps "${min_snapshot_end_gps}")
fi
if [ -n "${max_snapshot_end_gps:-}" ]; then
    extract_args+=(--max-snapshot-end-gps "${max_snapshot_end_gps}")
fi

python3 "${SCRIPT_DIR:-.}/extract_zerolag_features.py" "${extract_args[@]}"

feature_count=$(SUMMARY_JSON="${SUMMARY_JSON}" python3 - <<'PY'
import json, os
with open(os.environ["SUMMARY_JSON"]) as f:
    data=json.load(f)
print(int(data.get("feature_rows_total") or 0))
PY
)

foreground_feature_count=$(FEATURE_CSV="${FEATURE_CSV}" python3 - <<'PY'
import csv
import os

path = os.environ["FEATURE_CSV"]
count = 0
try:
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            value = str(row.get("is_background", "")).strip().lower()
            if value not in {"1", "true", "yes", "background"}:
                count += 1
except FileNotFoundError:
    pass
print(count)
PY
)

background_duration=$(SUMMARY_JSON="${SUMMARY_JSON}" python3 - <<'PY'
import json, os
with open(os.environ["SUMMARY_JSON"]) as f:
    data=json.load(f)
print(float(data.get("duration_seconds") or data.get("background_duration_seconds") or 0.0))
PY
)
background_start_gps=$(SUMMARY_JSON="${SUMMARY_JSON}" python3 - <<'PY'
import json, os
with open(os.environ["SUMMARY_JSON"]) as f:
    data=json.load(f)
value = data.get("background_start_gps", data.get("data_gps_start", data.get("gps_start", "")))
print("" if value in (None, "") else value)
PY
)
background_end_gps=$(SUMMARY_JSON="${SUMMARY_JSON}" python3 - <<'PY'
import json, os
with open(os.environ["SUMMARY_JSON"]) as f:
    data=json.load(f)
value = data.get("background_end_gps", data.get("data_gps_end", data.get("gps_end", "")))
print("" if value in (None, "") else value)
PY
)
feature_far_segment_args=("${segment_xml_args[@]}")
if [ "${#segment_xml_args[@]}" -gt 0 ]; then
    if [ -z "${background_start_gps}" ] || [ -z "${background_end_gps}" ]; then
        echo "single-detector updater: segment XML was provided but background GPS bounds are unavailable" >&2
        exit 2
    fi
    feature_far_segment_args+=(
        --background-start-gps "${background_start_gps}"
        --background-end-gps "${background_end_gps}"
    )
fi

background_required=$(python3 - <<'PY'
import os
print(float(os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0))
PY
)

background_is_ready=$(python3 - <<PY
duration = float("${background_duration}")
required = float("${background_required}")
print("1" if duration >= required else "0")
PY
)

if [ "${background_is_ready}" != "1" ]; then
    WORKER_ID="${WORKER_ID}" WORKER_GROUP="${WORKER_GROUP}" WORKER_COUNT="${WORKER_COUNT}" SUMMARY_JSON="${SUMMARY_JSON}" STATUS_JSON="${MONITOR_DIR}/latest_single_background_status.json" FOREGROUND_FEATURE_COUNT="${foreground_feature_count}" python3 - <<'PY'
import json, pathlib, time
import os

summary=json.loads(pathlib.Path(os.environ["SUMMARY_JSON"]).read_text())
required=float(os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0)
duration=float(summary.get("background_duration_seconds") or summary.get("duration_seconds") or 0.0)
summary.update({
    "worker_id": os.environ.get("WORKER_ID") or None,
    "worker_group": os.environ.get("WORKER_GROUP") or None,
    "worker_count": os.environ.get("WORKER_COUNT") or None,
    "background_ready": False,
    "background_file": None,
    "assigned_file": None,
    "support_file": None,
    "plot_file": None,
    "support_points": 0,
    "assigned_points": 0,
    "formal_assigned_far_rows_H1": 0,
    "formal_assigned_far_rows_L1": 0,
    "formal_assigned_far_rows_total": 0,
    "foreground_feature_rows_total": int(os.environ.get("FOREGROUND_FEATURE_COUNT") or 0),
    "accumulated_background_time_seconds": duration,
    "accumulated_background_time_hours": duration / 3600.0 if duration else 0.0,
    "background_accumulation_seconds_required": required,
    "far_assignment_blocked": True,
    "calculated_far_blocked": True,
    "reason": (
        f"background duration {duration:.1f}s is below "
        f"BACKGROUND_ACCUMULATION_SECONDS={required:.1f}s; "
        "background is still accumulating, so no BG JSON, LLR-FAR support, "
        "calculated FAR, or assigned FAR is produced yet"
    ),
    "updated_unix": time.time(),
})
pathlib.Path(os.environ["STATUS_JSON"]).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps(summary, sort_keys=True))
PY
    merge_worker_outputs
    exit 0
fi

if [ "${foreground_feature_count}" -le 30 ]; then
    WORKER_ID="${WORKER_ID}" WORKER_GROUP="${WORKER_GROUP}" WORKER_COUNT="${WORKER_COUNT}" SUMMARY_JSON="${SUMMARY_JSON}" STATUS_JSON="${MONITOR_DIR}/latest_single_background_status.json" FOREGROUND_FEATURE_COUNT="${foreground_feature_count}" python3 - <<'PY'
import json, pathlib, time
import os
p=pathlib.Path(os.environ["STATUS_JSON"])
with open(os.environ["SUMMARY_JSON"]) as f:
    data=json.load(f)
required=float(__import__("os").environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0)
duration=float(data.get("duration_seconds") or data.get("background_duration_seconds") or 0.0)
data.update({
    "worker_id": os.environ.get("WORKER_ID") or None,
    "worker_group": os.environ.get("WORKER_GROUP") or None,
    "worker_count": os.environ.get("WORKER_COUNT") or None,
    "background_ready": False,
    "background_file": None,
    "assigned_file": None,
    "support_file": None,
    "plot_file": None,
    "support_points": 0,
    "assigned_points": 0,
    "formal_assigned_far_rows_H1": 0,
    "formal_assigned_far_rows_L1": 0,
    "formal_assigned_far_rows_total": 0,
    "foreground_feature_rows_total": int(os.environ.get("FOREGROUND_FEATURE_COUNT") or 0),
    "accumulated_background_time_seconds": duration,
    "accumulated_background_time_hours": duration / 3600.0 if duration else 0.0,
    "background_accumulation_seconds_required": required,
    "far_assignment_blocked": True,
    "calculated_far_blocked": True,
    "reason": (
        "not enough detector-local features for a stable cold-start FAR-LLR "
        f"support curve; required background window is {required:.1f}s"
    ),
    "updated_unix": time.time(),
})
p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
print(json.dumps(data, sort_keys=True))
PY
    merge_worker_outputs
    exit 0
fi

holdout=$(( foreground_feature_count / 10 ))
if [ "${holdout}" -lt 1 ]; then
    holdout=1
fi
if [ "${holdout}" -gt 1000 ]; then
    holdout=1000
fi
if [ "${holdout}" -ge "${foreground_feature_count}" ]; then
    holdout=$(( foreground_feature_count - 1 ))
fi

python3 "${SCRIPT_DIR:-.}/single_detector_far.py" feature-csv \
    --feature-csv "${FEATURE_CSV}" \
    --output "${BRANCH_DIR}/bootstrap_latest_holdout.csv" \
    --background-output "${BACKGROUND_JSON}" \
    --support-output "${SUPPORT_CSV}" \
    --ifos H1,L1 \
    --min-snr 4 \
    --foreground-count "${holdout}" \
    --bootstrap-background-from-foreground \
    --background-livetime "${background_duration}" \
    "${feature_far_segment_args[@]}" \
    --bank-stats-dir "${WGUO_BANK_STATS_DIR}" \
    --dof "${DOF}" \
    --noise-beta "${NOISE_BETA}" \
    --rank-offset "${RANK_OFFSET}" \
    --background-window-days 7 \
    --fit-min-points 20 \
    --far-fit-boundary "${FAR_FIT_BOUNDARY}"

assignment_extract_args=()
for pattern in "${snapshot_globs[@]}"; do
    assignment_extract_args+=(--glob "${pattern}")
done
assignment_extract_args+=(
    --output "${ASSIGNMENT_FEATURE_CSV}"
    --summary "${ASSIGNMENT_SUMMARY_JSON}"
    --min-snr 4
    --banks-per-group "${BANKS_PER_GROUP:-6}"
)
if [ -n "${assignment_max_snapshot_end_gps:-}" ]; then
    assignment_extract_args+=(--max-snapshot-end-gps "${assignment_max_snapshot_end_gps}")
fi
if [ -n "${min_snapshot_end_gps:-}" ]; then
    assignment_extract_args+=(--min-snapshot-end-gps "${min_snapshot_end_gps}")
fi

python3 "${SCRIPT_DIR:-.}/extract_zerolag_features.py" "${assignment_extract_args[@]}"

ledger_args=(
    --feature-csv "${ASSIGNMENT_FEATURE_CSV}"
    --output "${ASSIGNED_CSV}"
    --candidate-output "${ASSIGNED_CANDIDATES_CSV}"
    --summary "${ASSIGNMENT_LEDGER_JSON}"
    --ifos H1,L1
    --min-snr 4
    --background-window-seconds "${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
    --background-required-seconds "${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
    --background-update-seconds "${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}"
    --initial-window-policy "${FAR_INITIAL_WINDOW_POLICY:-skip}"
    --bank-stats-dir "${WGUO_BANK_STATS_DIR}"
    --dof "${DOF}"
    --noise-beta "${NOISE_BETA}"
    --rank-offset "${RANK_OFFSET}"
    --fit-min-points 20
    --far-fit-boundary "${FAR_FIT_BOUNDARY}"
    --max-new-windows-per-run "${ASSIGNMENT_MAX_NEW_WINDOWS_PER_RUN:-20}"
    --background-archive-dir "${BACKGROUND_ARCHIVE_DIR}"
)
if [ "${#segment_xml_args[@]}" -gt 0 ]; then
    ledger_args+=("${segment_xml_args[@]}")
fi
if [ -n "${DATA_START_TIME:-}" ]; then
    ledger_args+=(--data-start-gps "${DATA_START_TIME}")
fi
if [ "${PREFER_FEATURE_SINGLE_FAR:-0}" = "1" ]; then
    ledger_args+=(--prefer-feature-single-far)
fi

python3 "${SCRIPT_DIR:-.}/assign_frozen_far_ledger.py" "${ledger_args[@]}"

python3 "${SCRIPT_DIR:-.}/plot_single_llr_far.py" \
    --background "${BACKGROUND_JSON}" \
    --assigned "${ASSIGNED_CSV}" \
    --output "${PLOT_PNG}" \
    --summary "${PLOT_SUMMARY}" \
    --llr-min "${PLOT_LLR_MIN}" \
    --tail-log10-far "${TAIL_LOG10_FAR}"

WORKER_ID="${WORKER_ID}" WORKER_GROUP="${WORKER_GROUP}" WORKER_COUNT="${WORKER_COUNT}" SUMMARY_JSON="${SUMMARY_JSON}" ASSIGNMENT_SUMMARY_JSON="${ASSIGNMENT_SUMMARY_JSON}" ASSIGNMENT_LEDGER_JSON="${ASSIGNMENT_LEDGER_JSON}" PLOT_SUMMARY="${PLOT_SUMMARY}" STATUS_JSON="${MONITOR_DIR}/latest_single_background_status.json" BACKGROUND_JSON="${BACKGROUND_JSON}" ASSIGNED_CSV="${ASSIGNED_CSV}" SUPPORT_CSV="${SUPPORT_CSV}" PLOT_PNG="${PLOT_PNG}" ASSIGNMENT_FEATURE_CSV="${ASSIGNMENT_FEATURE_CSV}" python3 - <<'PY'
import csv, json, pathlib, time
import os
summary=json.loads(pathlib.Path(os.environ["SUMMARY_JSON"]).read_text())
assignment_summary_path=pathlib.Path(os.environ["ASSIGNMENT_SUMMARY_JSON"])
assignment_summary=(json.loads(assignment_summary_path.read_text())
                    if assignment_summary_path.exists() else {})
ledger_path=pathlib.Path(os.environ["ASSIGNMENT_LEDGER_JSON"])
ledger=(json.loads(ledger_path.read_text()) if ledger_path.exists() else {})
plot=json.loads(pathlib.Path(os.environ["PLOT_SUMMARY"]).read_text())
counts={"H1": 0, "L1": 0, "total": 0}
with pathlib.Path(os.environ["ASSIGNED_CSV"]).open(newline="") as handle:
    reader=csv.DictReader(handle)
    for row in reader:
        ifo=(row.get("ifo") or row.get("ifos") or "").strip()
        if ifo in ("H1", "L1"):
            counts[ifo] += 1
        counts["total"] += 1
summary.update({
    "worker_id": os.environ.get("WORKER_ID") or None,
    "worker_group": os.environ.get("WORKER_GROUP") or None,
    "worker_count": os.environ.get("WORKER_COUNT") or None,
    "background_ready": True,
    "background_file": os.environ["BACKGROUND_JSON"],
    "assigned_file": os.environ["ASSIGNED_CSV"],
    "support_file": os.environ["SUPPORT_CSV"],
    "plot_file": os.environ["PLOT_PNG"],
    "support_points": plot.get("support_points"),
    "assigned_points": plot.get("assigned_points"),
    "assignment_feature_file": os.environ["ASSIGNMENT_FEATURE_CSV"],
    "assignment_feature_rows_total": assignment_summary.get("feature_rows_total"),
    "assignment_input_files": assignment_summary.get("input_files"),
    "assignment_files": assignment_summary.get("files"),
    "assignment_gps_start_utc": assignment_summary.get("gps_start_utc"),
    "assignment_gps_end_utc": assignment_summary.get("gps_end_utc"),
    "assignment_new_rows": ledger.get("newly_assigned_rows"),
    "assignment_duplicate_candidate_rows": ledger.get("duplicate_candidate_rows"),
    "assignment_skipped_not_ready_rows": ledger.get("skipped_not_ready_rows"),
    "assignment_deferred_window_rows": ledger.get("deferred_window_rows"),
    "assignment_background_windows_used": ledger.get("background_windows_used"),
    "assignment_background_files": ledger.get("background_files"),
    "assignment_max_new_windows_per_run": ledger.get("max_new_windows_per_run"),
    "assignment_policy": ledger.get("policy"),
    "formal_assigned_far_rows_H1": counts["H1"],
    "formal_assigned_far_rows_L1": counts["L1"],
    "formal_assigned_far_rows_total": counts["total"],
    "accumulated_background_time_seconds": summary.get("duration_seconds"),
    "accumulated_background_time_hours": summary.get("duration_hours"),
    "background_accumulation_seconds_required": float(__import__("os").environ.get(
        "BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0),
    "far_assignment_blocked": False,
    "updated_unix": time.time(),
})
pathlib.Path(os.environ["STATUS_JSON"]).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps(summary, sort_keys=True))
PY

merge_worker_outputs
