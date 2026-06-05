#!/bin/bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

legacy_monitor() {
    local job_id=${1:-}

    printf 'WORKDIR=%s\n' "$(pwd)"
    printf 'UTC=%s\n' "$(date -u +%FT%TZ)"
    if [ -n "${job_id}" ]; then
        printf 'SLURM_SQUEUE\n'
        squeue -j "${job_id}" -o '%.18i %.9P %.40j %.8T %.10M %.6D %R' || true
        printf 'SLURM_SACCT\n'
        sacct -j "${job_id}" --format=JobID,JobName%30,State,Elapsed,AllocTRES%60,ExitCode -P 2>/dev/null || true
    fi

    printf 'ZEROLAG_FILES=%s\n' "$(find . -path './[0-9][0-9][0-9]/*_zerolag_*.xml.gz' 2>/dev/null | wc -l | tr -d ' ')"
    printf 'BANK_GROUP_DIRS=%s\n' "$(find . -maxdepth 1 -type d -regex './[0-9][0-9][0-9]' | sed 's#./##' | sort | tr '\n' ' ')"

    if [ -f monitor/latest_single_background_status.json ]; then
        printf 'SINGLE_BACKGROUND_STATUS\n'
        python3 - <<'PY'
import json
with open("monitor/latest_single_background_status.json") as f:
    d=json.load(f)
keys=[
 "duration_seconds","duration_hours","bank_groups","bank_ranges","postcoh_rows",
 "feature_rows_H1","feature_rows_L1","feature_rows_total","background_ready",
 "support_points","assigned_points","background_file","assigned_file","plot_file"
]
for k in keys:
    print(f"{k}={d.get(k)}")
PY
    fi

    printf 'LATEST_LOG_TAIL\n'
    latest_out=$(ls -t logs/pipe_*.out 2>/dev/null | head -n 1 || true)
    latest_err=$(ls -t logs/pipe_*.err 2>/dev/null | head -n 1 || true)
    if [ -n "${latest_out}" ]; then
        printf 'OUT=%s\n' "${latest_out}"
        tail -n 30 "${latest_out}" || true
    fi
    if [ -n "${latest_err}" ]; then
        printf 'ERR=%s\n' "${latest_err}"
        tail -n 30 "${latest_err}" || true
    fi
}

if [ "${1:-}" = "--legacy" ]; then
    shift
    legacy_monitor "$@"
    exit 0
fi

job_id=""
if [ "$#" -gt 0 ] && [[ "${1}" != -* ]]; then
    job_id=$1
    shift
fi

python3 "${script_dir}/monitor_run_table.py" \
    --run-dir "$(pwd)" \
    ${job_id:+--job-id "${job_id}"} \
    "$@"
