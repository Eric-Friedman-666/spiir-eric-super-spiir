#!/bin/bash
set -euo pipefail

interval=${1:-120}
RUN_DIR=${2:-$(pwd)}
cd "${RUN_DIR}"

worker_id=${SINGLE_WORKER_ID:-}
worker_group=${SINGLE_WORKER_GROUP:-${worker_id}}
worker_count=${SINGLE_WORKER_COUNT:-${NODES_AMOUNT:-1}}
if [ -n "${worker_id}" ]; then
    state_dir="monitor/worker_${worker_id}"
else
    state_dir="monitor"
fi
state_file="${state_dir}/.last_single_background_update_snapshot_state"
mkdir -p "${state_dir}"

worker_glob_args() {
    if [ -n "${worker_id}" ]; then
        python3 - "${worker_group}" "${MAX_GROUP:-0}" "${SINGLE_INPUT_KIND:-zerolag}" <<'PY'
import sys
worker_group = int(sys.argv[1])
max_group = int(sys.argv[2])
kind = sys.argv[3]
if kind == "sdpostcoh":
    glob_name = "sdpostcoh*.xml.gz"
elif kind in ("singlecsv", "singletriggers"):
    glob_name = "*_single_triggers.csv"
else:
    glob_name = "*_zerolag_*.xml.gz"
if 0 <= worker_group <= max_group:
    print(f"{worker_group:03d}/{glob_name}")
PY
    else
        if [ "${SINGLE_INPUT_KIND:-zerolag}" = "sdpostcoh" ]; then
            printf '%s\n' "[0-9][0-9][0-9]/sdpostcoh*.xml.gz"
        elif [ "${SINGLE_INPUT_KIND:-zerolag}" = "singlecsv" ] || [ "${SINGLE_INPUT_KIND:-zerolag}" = "singletriggers" ]; then
            printf '%s\n' "[0-9][0-9][0-9]/*_single_triggers.csv"
        else
            printf '%s\n' "[0-9][0-9][0-9]/*_zerolag_*.xml.gz"
        fi
    fi
}

latest_visible_snapshot_state() {
    python3 - "$@" <<'PY'
import csv
import glob
import os
import re
import time
import sys

snapshot_re = re.compile(r"(?:_zerolag_|sdpostcoh[^/_]*_|single_postcoh[^/_]*_)(\d+)_(\d+)\.xml(?:\.gz)?$")
latest = None
visible_size = 0
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
    if latest is None or end > latest:
        latest = end

if latest is None:
    raise SystemExit(0)

if os.environ.get("ONLINE_REPLAY_SYNC", "0") == "1":
    start_gps = float(os.environ.get("ONLINE_REPLAY_START_GPS") or os.environ["DATA_START_TIME"])
    start_wall = float(os.environ.get("ONLINE_REPLAY_START_WALL") or time.time())
    rate = float(os.environ.get("ONLINE_REPLAY_RATE", "1.0"))
    lag = float(os.environ.get("ONLINE_REPLAY_ALLOWED_LAG_SECONDS", "0"))
    allowed = start_gps + max(0.0, time.time() - start_wall) * rate + lag
    latest = min(latest, allowed)

for filename in sorted(set(filenames)):
    match = snapshot_re.search(filename)
    if match:
        end = float(int(match.group(1)) + int(match.group(2)))
    elif filename.endswith(".csv"):
        end = latest
    else:
        continue
    if end <= latest:
        try:
            visible_size += os.path.getsize(filename)
        except OSError:
            pass

print(f"{latest} {visible_size}")
PY
}

should_update() {
    local latest=$1
    local count=${2:-0}
    local trigger_seconds=${BACKGROUND_UPDATE_TRIGGER_SECONDS:-0}
    if [ -z "${latest}" ]; then
        [ ! -f monitor/latest_single_summary.json ]
        return
    fi
    if [ ! -s "${state_file}" ]; then
        return 0
    fi
    local previous previous_count
    read -r previous previous_count < "${state_file}" || true
    if [ -z "${previous}" ] || [ "${trigger_seconds}" = "0" ]; then
        return 0
    fi
    python3 - "${latest}" "${count}" "${previous}" "${previous_count:-0}" "${trigger_seconds}" <<'PY'
import sys
latest = float(sys.argv[1])
count = int(float(sys.argv[2]))
previous = float(sys.argv[3])
previous_count = int(float(sys.argv[4]))
trigger = float(sys.argv[5])
visible_files_changed = count != previous_count
next_trigger_window = latest - previous >= trigger
raise SystemExit(0 if visible_files_changed or next_trigger_window else 1)
PY
}

while true; do
    if [ -f STOP_SINGLE_UPDATE.flag ]; then
        "${SCRIPT_DIR:-.}/update_single_background_once.sh" "${RUN_DIR}" || true
        break
    fi
    mapfile -t glob_args < <(worker_glob_args)
    latest_state=$(latest_visible_snapshot_state "${glob_args[@]}" || true)
    latest=
    count=0
    if [ -n "${latest_state}" ]; then
        read -r latest count <<< "${latest_state}"
    fi
    if should_update "${latest}" "${count}"; then
        "${SCRIPT_DIR:-.}/update_single_background_once.sh" "${RUN_DIR}" || true
        if [ -n "${latest}" ]; then
            printf '%s %s\n' "${latest}" "${count}" > "${state_file}"
        fi
    fi
    sleep "${interval}"
done
