#!/bin/bash
# Online-frontier controller for O3a BNS py3 no-injection background.
#
# This script is intentionally no-injection.  It is the canonical way to build
# a frozen background for later injection studies: process the background as a
# sequence of short online chunks, then freeze the single-detector and coherent
# background products.  Future injection validation should use the same frontier
# idea by exposing only the injection rows available to the current chunk.

set -euo pipefail

STAMP=${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
ROOT_DIR=${ROOT_DIR:-/fred/oz016/qliang/spiir_o3a_bns_frozen_bg/run_o3a_bns_bg1w_7d_noinj_online_chunked_${STAMP}}
BG_DIR=${BG_DIR:-${ROOT_DIR}/bg_noinj_7d}
CHUNK_ROOT=${CHUNK_ROOT:-${BG_DIR}/chunks}
PACKAGE_SCRIPT_DIR=${PACKAGE_SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}
SCRIPT_DIR=${SCRIPT_DIR:-${ROOT_DIR}/scripts}
SOURCE_SCRIPT_DIR=${SOURCE_SCRIPT_DIR:-${PACKAGE_SCRIPT_DIR}}
CONTROLLER_DIR=${CONTROLLER_DIR:-${ROOT_DIR}/controller}
LOG_DIR=${LOG_DIR:-${CONTROLLER_DIR}/logs}
STATUS_JSON=${STATUS_JSON:-${CONTROLLER_DIR}/workflow_status.json}

START_GPS=${START_GPS:-1241725020}
CHUNK_SECONDS=${CHUNK_SECONDS:-86400}
NUM_CHUNKS=${NUM_CHUNKS:-7}
DURATION=$((CHUNK_SECONDS * NUM_CHUNKS))
END_GPS=$((START_GPS + DURATION))
SNAPSHOT_INTERVAL=${SNAPSHOT_INTERVAL:-3600}
BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS:-604796}
BANKS_PER_GROUP=${BANKS_PER_GROUP:-8}
INITIAL_MULTI_STATS_LOC=${INITIAL_MULTI_STATS_LOC:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}
EXPECTED_ZEROLAG_TOTAL=$((DURATION / SNAPSHOT_INTERVAL))
EXPECTED_ZEROLAG_PER_CHUNK=$((CHUNK_SECONDS / SNAPSHOT_INTERVAL))
POLL_SECONDS=${POLL_SECONDS:-300}

mkdir -p "${ROOT_DIR}" "${BG_DIR}/000" "${BG_DIR}/logs" "${BG_DIR}/monitor" \
    "${CHUNK_ROOT}" "${SCRIPT_DIR}" "${LOG_DIR}"

log() {
    printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "${LOG_DIR}/controller.log"
}

chunk_name() {
    printf 'chunk_%03d' "$1"
}

chunk_dir() {
    printf '%s/%s' "${CHUNK_ROOT}" "$(chunk_name "$1")"
}

chunk_start() {
    echo $((START_GPS + CHUNK_SECONDS * $1))
}

chunk_end() {
    echo $((START_GPS + CHUNK_SECONDS * ($1 + 1)))
}

count_chunk_zerolag() {
    local dir=$1
    [ -d "${dir}/000" ] || { echo 0; return 0; }
    find "${dir}/000" -maxdepth 1 -type f -name '000_zerolag_*.xml*' 2>/dev/null | wc -l | awk '{print $1}'
}

count_chunk_stats() {
    local dir=$1
    [ -d "${dir}/000" ] || { echo 0; return 0; }
    find "${dir}/000" -maxdepth 1 -type f -name '000_marginalized_stats_*.xml*' 2>/dev/null | wc -l | awk '{print $1}'
}

count_aggregate_zerolag() {
    [ -d "${BG_DIR}/000" ] || { echo 0; return 0; }
    find "${BG_DIR}/000" -maxdepth 1 \( -type f -o -type l \) -name '000_zerolag_*.xml*' 2>/dev/null | wc -l | awk '{print $1}'
}

count_aggregate_stats() {
    [ -d "${BG_DIR}/000" ] || { echo 0; return 0; }
    find "${BG_DIR}/000" -maxdepth 1 \( -type f -o -type l \) -name '000_marginalized_stats_*.xml*' 2>/dev/null | wc -l | awk '{print $1}'
}

queue_state() {
    local job_id=$1
    timeout 20 squeue -j "${job_id}" -h -o "%T|%R|%M|%N" 2>/dev/null | sort -u | tr '\n' ';' | sed 's/;$//'
}

sacct_states() {
    local job_id=$1
    timeout 20 sacct -j "${job_id}" --format=JobID,State,ExitCode,Elapsed,NodeList%24 -P 2>/dev/null \
        | awk -F'|' 'NR > 1 && $1 !~ /\.batch|\.extern/ {print $1 "=" $2 "(" $3 "," $4 "," $5 ")"}' \
        | tr '\n' ';' | sed 's/;$//'
}

write_status() {
    local phase=$1
    local state=$2
    local reason=$3
    local chunk_index=${4:-}
    local chunk_job=${5:-}
    python3 - "${STATUS_JSON}" "${ROOT_DIR}" "${BG_DIR}" "${phase}" "${state}" "${reason}" \
        "${EXPECTED_ZEROLAG_TOTAL}" "${EXPECTED_ZEROLAG_PER_CHUNK}" "${chunk_index}" "${chunk_job}" <<'PY'
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
bg_dir = pathlib.Path(sys.argv[3])
phase, state, reason = sys.argv[4:7]
expected_total = int(sys.argv[7])
expected_chunk = int(sys.argv[8])
chunk_index = sys.argv[9] or None
chunk_job = sys.argv[10] or None
payload = {}
if path.exists():
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        payload = {}
worker = bg_dir / "000"
chunks = []
chunk_root = bg_dir / "chunks"
if chunk_root.exists():
    for chunk_dir in sorted(chunk_root.glob("chunk_*")):
        cworker = chunk_dir / "000"
        chunks.append({
            "chunk": chunk_dir.name,
            "job_id": (chunk_dir / "job_id.txt").read_text().strip() if (chunk_dir / "job_id.txt").exists() else None,
            "zerolag": len(list(cworker.glob("000_zerolag_*.xml*"))) if cworker.exists() else 0,
            "stats": len(list(cworker.glob("000_marginalized_stats_*.xml*"))) if cworker.exists() else 0,
        })
payload.update({
    "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "root_dir": str(root),
    "bg_dir": str(bg_dir),
    "phase": phase,
    "state": state,
    "reason": reason,
    "workers": 1,
    "banks_per_group": int(os.environ.get("BANKS_PER_GROUP", "8")),
    "injection_mode": "none",
    "online_frontier": "24h chunks submitted sequentially; no future injection rows are exposed",
    "start_gps": int(os.environ.get("START_GPS", "1241725020")),
    "end_gps": int(os.environ.get("END_GPS", "1242329820")),
    "chunk_seconds": int(os.environ.get("CHUNK_SECONDS", "86400")),
    "num_chunks": int(os.environ.get("NUM_CHUNKS", "7")),
    "background_accumulation_seconds": float(os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "604796")),
    "expected_zerolag_per_worker": expected_total,
    "expected_zerolag_per_chunk": expected_chunk,
    "current_chunk": int(chunk_index) if chunk_index is not None else None,
    "current_chunk_job_id": chunk_job,
    "zerolag_000": len(list(worker.glob("000_zerolag_*.xml*"))) if worker.exists() else 0,
    "stats_000": len(list(worker.glob("000_marginalized_stats_*.xml*"))) if worker.exists() else 0,
    "single_background_json": str(bg_dir / "single_branch" / "single_far_llr_background.json"),
    "single_background_exists": (bg_dir / "single_branch" / "single_far_llr_background.json").exists(),
    "chunks": chunks,
})
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
}

copy_scripts() {
    cp "${SOURCE_SCRIPT_DIR}/"*.sh "${SCRIPT_DIR}/"
    cp "${SOURCE_SCRIPT_DIR}/"*.py "${SCRIPT_DIR}/"
    chmod +x "${SCRIPT_DIR}/"*.sh "${SCRIPT_DIR}/"*.py
}

validate_stats_loc() {
    local stats_loc=$1
    local suffix
    for suffix in 2w 1d 2h; do
        local p="${stats_loc}/000/000_marginalized_stats_${suffix}.xml.gz"
        [ -f "${p}" ] || { log "ERROR missing multi stats ${p}"; exit 2; }
    done
}

validate_inputs() {
    local p
    for p in \
        "/fred/oz016/sunil/run_utils/frames_chache/frame_O3a.cache" \
        "/fred/oz016/wguo/odds_ratio/O3a/chunk6/multi_det-BNS-LVK_inj/H1L1V1_1242105073_detrsp_map.xml"; do
        [ -f "${p}" ] || { log "ERROR missing input ${p}"; exit 2; }
    done
    validate_stats_loc "${INITIAL_MULTI_STATS_LOC}"
}

write_run_config() {
    cat > "${BG_DIR}/run_config.sh" <<EOF
export SCRIPT_DIR=${SCRIPT_DIR}
export RUN_DIR=${BG_DIR}
export DATA_START_TIME=${START_GPS}
export MAX_DATA_DURATION_SECONDS=${DURATION}
export DATA_END_TIME=${END_GPS}
export NODES_AMOUNT=1
export MAX_GROUP=0
export BANKS_PER_GROUP=${BANKS_PER_GROUP}
export SINGLE_INPUT_KIND=zerolag
export BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS}
export FORMAL_BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS}
export ALLOW_SHORT_BACKGROUND_DEBUG=0
export BACKGROUND_UPDATE_TRIGGER_SECONDS=3600
export FAR_INITIAL_WINDOW_POLICY=skip
export SINGLE_BACKGROUND_MODE=rolling
export WGUO_BANK_STATS_DIR=/fred/oz016/wguo/packages/spiir/src/spiir/search/bank_dofs
export PLOT_LLR_MIN=-20
export TAIL_LOG10_FAR=-6
export FAR_FIT_BOUNDARY=0.01
EOF
}

write_chunk_note() {
    local idx=$1
    local dir=$2
    local start=$3
    local end=$4
    local stats_loc=$5
    cat > "${dir}/ONLINE_CHUNK_PROVENANCE.txt" <<EOF
Canonical 7d no-injection BG, online-frontier chunk ${idx}.
Injection mode is none. No injection XML is visible in this run.
Chunk GPS: ${start}-${end}
External multi/coherent stats input: ${stats_loc}
Future online injection validation must expose sim_inspiral rows chunk by chunk.
EOF
}

submit_chunk() {
    local idx=$1
    local dir=$2
    local start=$3
    local end=$4
    local stats_loc=$5
    if [ -f "${dir}/job_id.txt" ]; then
        log "chunk=${idx} job already submitted: $(cat "${dir}/job_id.txt")"
        return 0
    fi
    mkdir -p "${dir}/logs" "${dir}/monitor"
    write_chunk_note "${idx}" "${dir}" "${start}" "${end}" "${stats_loc}"
    local job_id
    job_id=$(
        cd "${dir}"
        sbatch --parsable \
            --job-name="o3a_bns_bg1w_7d_c${idx}" \
            --time=36:00:00 \
            --array=0-0 \
            --export=ALL,SCRIPT_DIR="${SCRIPT_DIR}",RUN_DIR="${dir}",WGUO_O3A_INJECTION_MODE=none,WGUO_O3A_INJECTION_FILE="",WGUO_O3A_START_GPS="${start}",WGUO_O3A_END_GPS="${end}",WGUO_O3A_SNAPSHOT_INTERVAL="${SNAPSHOT_INTERVAL}",WGUO_O3A_NONINJ_STATS_LOC="${stats_loc}",WGUO_O3A_BANKS_PER_GROUP="${BANKS_PER_GROUP}",WGUO_O3A_FINALSINK_NEED_ONLINE_PERFORM=0,WGUO_O3A_GRACEDB_FAR_THRESH=0 \
            "${SCRIPT_DIR}/wguo_o3a_bns_py3_sbatch.sh"
    )
    printf '%s\n' "${job_id}" > "${dir}/job_id.txt"
    log "submitted chunk=${idx} job=${job_id} run_dir=${dir} gps=${start}-${end} stats_input=${stats_loc}"
}

chunk_complete() {
    local dir=$1
    [ "$(count_chunk_zerolag "${dir}")" -ge "${EXPECTED_ZEROLAG_PER_CHUNK}" ] && [ "$(count_chunk_stats "${dir}")" -ge 3 ]
}

collect_chunk_outputs() {
    local idx=$1
    local dir=$2
    mkdir -p "${BG_DIR}/000"
    find "${dir}/000" -maxdepth 1 -type f -name '000_zerolag_*.xml*' -print0 \
        | while IFS= read -r -d '' f; do
            ln -sfn "${f}" "${BG_DIR}/000/$(basename "${f}")"
        done
    if [ "${idx}" -eq $((NUM_CHUNKS - 1)) ]; then
        local suffix
        for suffix in 2w 1d 2h; do
            ln -sfn "${dir}/000/000_marginalized_stats_${suffix}.xml.gz" \
                "${BG_DIR}/000/000_marginalized_stats_${suffix}.xml.gz"
        done
    fi
}

wait_for_chunk() {
    local idx=$1
    local dir=$2
    local job_id
    job_id=$(cat "${dir}/job_id.txt")
    while true; do
        local queue states zerolag stats agg
        queue=$(queue_state "${job_id}" || true)
        states=$(sacct_states "${job_id}" || true)
        zerolag=$(count_chunk_zerolag "${dir}")
        stats=$(count_chunk_stats "${dir}")
        agg=$(count_aggregate_zerolag)
        log "chunk=${idx}/${NUM_CHUNKS} job=${job_id} queue=${queue:-none} states=${states:-none} chunk_zerolag=${zerolag}/${EXPECTED_ZEROLAG_PER_CHUNK} chunk_stats=${stats}/3 aggregate_zerolag=${agg}/${EXPECTED_ZEROLAG_TOTAL}"
        write_status chunk watching "queue=${queue:-none}; states=${states:-none}; chunk_zerolag=${zerolag}; chunk_stats=${stats}; aggregate_zerolag=${agg}" "${idx}" "${job_id}"
        if chunk_complete "${dir}"; then
            collect_chunk_outputs "${idx}" "${dir}"
            log "chunk=${idx} outputs_complete aggregate_zerolag=$(count_aggregate_zerolag)"
            write_status chunk complete "chunk ${idx} complete" "${idx}" "${job_id}"
            return 0
        fi
        if [ -z "${queue}" ] && [[ "${states}" == *"FAILED"* || "${states}" == *"CANCELLED"* || "${states}" == *"TIMEOUT"* || "${states}" == *"OUT_OF_MEMORY"* ]]; then
            log "ERROR chunk=${idx} job ended before complete outputs"
            find "${dir}/logs" -maxdepth 1 -type f \( -name '*.err' -o -name '*.out' \) -print0 \
                | xargs -0 -r tail -n 160 >> "${LOG_DIR}/controller.log" || true
            write_status chunk blocked "chunk ${idx} job ended before complete outputs; zerolag=${zerolag}; stats=${stats}" "${idx}" "${job_id}"
            exit 3
        fi
        sleep "${POLL_SECONDS}"
    done
}

run_chunks() {
    local stats_loc="${INITIAL_MULTI_STATS_LOC}"
    local idx
    for idx in $(seq 0 $((NUM_CHUNKS - 1))); do
        local dir start end
        dir=$(chunk_dir "${idx}")
        start=$(chunk_start "${idx}")
        end=$(chunk_end "${idx}")
        mkdir -p "${dir}"
        validate_stats_loc "${stats_loc}"
        if chunk_complete "${dir}"; then
            collect_chunk_outputs "${idx}" "${dir}"
            log "chunk=${idx} already complete; aggregate_zerolag=$(count_aggregate_zerolag)"
        else
            submit_chunk "${idx}" "${dir}" "${start}" "${end}" "${stats_loc}"
            wait_for_chunk "${idx}" "${dir}"
        fi
        stats_loc="${dir}"
    done
}

build_single_background() {
    local zerolag stats
    zerolag=$(count_aggregate_zerolag)
    stats=$(count_aggregate_stats)
    if [ "${zerolag}" -lt "${EXPECTED_ZEROLAG_TOTAL}" ] || [ "${stats}" -lt 3 ]; then
        log "ERROR aggregate incomplete zerolag=${zerolag}/${EXPECTED_ZEROLAG_TOTAL} stats=${stats}/3"
        write_status aggregate blocked "aggregate incomplete zerolag=${zerolag}; stats=${stats}"
        exit 4
    fi
    log "building single-detector rolling background from aggregate ${BG_DIR}"
    (
        cd "${BG_DIR}"
        "${SCRIPT_DIR}/update_single_background_once.sh" "${BG_DIR}"
    ) >> "${LOG_DIR}/single_update_bg_noinj_7d.log" 2>&1
    if [ ! -f "${BG_DIR}/single_branch/single_far_llr_background.json" ]; then
        log "ERROR missing single background JSON"
        write_status single_bg blocked "missing single_far_llr_background.json"
        exit 5
    fi
    mkdir -p "${BG_DIR}/001"
    local suffix
    for suffix in 2w 1d 2h; do
        ln -sfn "../000/000_marginalized_stats_${suffix}.xml.gz" \
            "${BG_DIR}/001/001_marginalized_stats_${suffix}.xml.gz"
    done
    write_status complete complete "single and multi backgrounds ready"
    log "background ready single=${BG_DIR}/single_branch/single_far_llr_background.json multi=${BG_DIR}/{000,001}/*_marginalized_stats_{2w,1d,2h}.xml.gz"
}

main() {
    export ROOT_DIR BG_DIR START_GPS END_GPS CHUNK_SECONDS NUM_CHUNKS BACKGROUND_ACCUMULATION_SECONDS BANKS_PER_GROUP
    log "chunked_bg7d_controller_start root=${ROOT_DIR} bg=${BG_DIR} start=${START_GPS} end=${END_GPS} chunks=${NUM_CHUNKS} chunk_seconds=${CHUNK_SECONDS} workers=1 injection=none"
    copy_scripts
    validate_inputs
    write_run_config
    {
        printf 'Canonical 7d no-injection BG run using online-frontier chunking.\n'
        printf 'This run intentionally has WGUO_O3A_INJECTION_MODE=none.\n'
        printf 'Each 24h chunk is submitted only after the previous chunk completes.\n'
        printf 'Do not interpret one-shot full-XML injection diagnostics as online validation.\n'
        printf 'Future online injection tests must expose injection rows chunk by chunk.\n'
    } > "${BG_DIR}/ONLINE_RULE_AND_PROVENANCE.txt"
    bash -n "${SCRIPT_DIR}/wguo_o3a_bns_py3_pipeline.sh"
    bash -n "${SCRIPT_DIR}/wguo_o3a_bns_py3_sbatch.sh"
    write_status init prepared "chunked controller prepared"
    run_chunks
    build_single_background
}

main "$@"
