#!/bin/bash
# Shared runtime knobs for batch_submit.sh, submit.sh, pipeline.sh, and monitors.

export SCRIPT_DIR=${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
export PACKAGE_ROOT=${PACKAGE_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}
export RUN_ROOT=${RUN_ROOT:-${PACKAGE_ROOT}/results}
export DATA_DIR=${DATA_DIR:-${PACKAGE_ROOT}/data}

export NODES_AMOUNT=${NODES_AMOUNT:-${NODES:-}}

export SPIIR_BUILD_NAME=${SPIIR_BUILD_NAME:-wguo-single-det-py3}
export SPIIR_RUN_FUNCTION=${SPIIR_RUN_FUNCTION:-run_spiir_py3}
export SPIIR_SOURCE_DIR=${SPIIR_SOURCE_DIR:-/fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/${SPIIR_BUILD_NAME}/source}
export SPIIR_HELPER_FUNCTIONS=${SPIIR_HELPER_FUNCTIONS:-/fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh}

export FRAME_CACHE_FILE=${FRAME_CACHE_FILE:-${DATA_CACHE_FILE:-${FRAME_CACHE:-${DATA_CACHE:-${DATA_DIRECTION:-${DATA_DIR}/frame_cache_hoft_C00_AR_1372808984_1375400984.cache}}}}}
export DATA_CACHE_FILE=${DATA_CACHE_FILE:-${FRAME_CACHE_FILE}}
export FRAME_CACHE=${FRAME_CACHE:-${FRAME_CACHE_FILE}}
export DATA_CACHE=${DATA_CACHE:-${FRAME_CACHE_FILE}}
export DATA_DIRECTION=${DATA_DIRECTION:-${FRAME_CACHE_FILE}}

export H1_STRAIN_CHANNEL_NAME=${H1_STRAIN_CHANNEL_NAME:-GDS-CALIB_STRAIN_AR}
export L1_STRAIN_CHANNEL_NAME=${L1_STRAIN_CHANNEL_NAME:-GDS-CALIB_STRAIN_AR}
export H1_STATE_CHANNEL_NAME=${H1_STATE_CHANNEL_NAME:-GDS-CALIB_STATE_VECTOR_AR}
export L1_STATE_CHANNEL_NAME=${L1_STATE_CHANNEL_NAME:-GDS-CALIB_STATE_VECTOR_AR}

export DATA_START_TIME=${DATA_START_TIME:-1372808984}
export MAX_DATA_DURATION_SECONDS=${MAX_DATA_DURATION_SECONDS:-2592000}
export DATA_END_TIME=${DATA_END_TIME:-$((DATA_START_TIME + MAX_DATA_DURATION_SECONDS))}
export AUTO_CLIP_FRAME_CACHE_TO_COMMON_SEGMENT=${AUTO_CLIP_FRAME_CACHE_TO_COMMON_SEGMENT:-1}
export FRAME_CACHE_CLIP_REQUIRED_IFOS=${FRAME_CACHE_CLIP_REQUIRED_IFOS:-H,L}
export FRAME_CACHE_CLIP_MODE=${FRAME_CACHE_CLIP_MODE:-preserve-duration}

export BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS:-10800}
export FORMAL_BACKGROUND_ACCUMULATION_SECONDS=${FORMAL_BACKGROUND_ACCUMULATION_SECONDS:-10800}
export ALLOW_SHORT_BACKGROUND_DEBUG=${ALLOW_SHORT_BACKGROUND_DEBUG:-0}
export SINGLE_BACKGROUND_MODE=${SINGLE_BACKGROUND_MODE:-rolling}
export SINGLE_FROZEN_BACKGROUND_JSON=${SINGLE_FROZEN_BACKGROUND_JSON:-}
export SINGLE_FROZEN_BACKGROUND_RUN_DIR=${SINGLE_FROZEN_BACKGROUND_RUN_DIR:-}
export SINGLE_FROZEN_BACKGROUND_ID=${SINGLE_FROZEN_BACKGROUND_ID:-BG-FROZEN}
export SINGLE_FROZEN_BACKGROUND_SOURCE=${SINGLE_FROZEN_BACKGROUND_SOURCE:-}
# The coherent FAR assignment element expects the standard three statistics
# files.  Use BACKGROUND_COLLECT_WALLTIME / BACKGROUND_ACCUMULATION_SECONDS to
# choose the active background timescale, not to shrink this file list.
export BACKGROUND_STATS_WINDOWS=${BACKGROUND_STATS_WINDOWS:-2w,1d,2h}

validate_background_contract() {
    python3 - <<'PY'
import os
import sys

actual = float(os.environ.get("BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0)
required = float(os.environ.get("FORMAL_BACKGROUND_ACCUMULATION_SECONDS", "10800") or 10800.0)
allow_debug = os.environ.get("ALLOW_SHORT_BACKGROUND_DEBUG", "0") == "1"

if actual < required and not allow_debug:
    sys.stderr.write(
        "BACKGROUND_CONTRACT_ERROR: BACKGROUND_ACCUMULATION_SECONDS="
        f"{actual:.0f}s is shorter than the formal required window "
        f"{required:.0f}s. Formal single-detector FAR runs must use the "
        "full three-hour background. Set ALLOW_SHORT_BACKGROUND_DEBUG=1 only "
        "for explicitly marked non-formal developer tests.\\n"
    )
    raise SystemExit(2)
PY
}

background_window_seconds() {
    local window=$1
    local number=${window%[wdhs]}
    local suffix=${window:${#window}-1}

    case "${suffix}" in
        w) printf '%s\n' "$((number * 7 * 24 * 3600))" ;;
        d) printf '%s\n' "$((number * 24 * 3600))" ;;
        h) printf '%s\n' "$((number * 3600))" ;;
        s) printf '%s\n' "${number}" ;;
        *) printf '%s\n' "${window}" ;;
    esac
}

background_collect_walltime_default() {
    local windows=$1
    local old_ifs=${IFS}
    local result=
    local window
    local seconds

    IFS=,
    for window in ${windows}; do
        IFS=${old_ifs}
        seconds=$(background_window_seconds "${window}")
        if [ -n "${result}" ]; then
            result="${result},${seconds}"
        else
            result="${seconds}"
        fi
        IFS=,
    done
    IFS=${old_ifs}

    printf '%s\n' "${result}"
}

export BACKGROUND_COLLECT_WALLTIME=${BACKGROUND_COLLECT_WALLTIME:-$(background_collect_walltime_default "${BACKGROUND_STATS_WINDOWS}")}
export COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS=${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS:-${BACKGROUND_ACCUMULATION_SECONDS}}
export COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS=${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS:-${BACKGROUND_ACCUMULATION_SECONDS}}
export FINALSINK_FAPUPDATER_INTERVAL_SECONDS=${FINALSINK_FAPUPDATER_INTERVAL_SECONDS:-1800}

case "${SINGLE_BACKGROUND_MODE}" in
    rolling|frozen) ;;
    *)
        printf 'BACKGROUND_CONTRACT_ERROR: SINGLE_BACKGROUND_MODE=%s is invalid; expected rolling or frozen\n' \
            "${SINGLE_BACKGROUND_MODE}" >&2
        return 2 2>/dev/null || exit 2
        ;;
esac

bank_dir_has_required_files() {
    local dir=$1
    [ -f "${dir}/iir_H1-PYCBC_SPLIT_BANK_0000-a1-0-0.xml.gz" ] && \
        [ -f "${dir}/iir_L1-PYCBC_SPLIT_BANK_0000-a1-0-0.xml.gz" ]
}

DEFAULT_BANK_DIR=${PACKAGE_ROOT}/data/py2_compat_wguo_pycbc_split_0000_0095
FALLBACK_BANK_DIR=${FALLBACK_BANK_DIR:-/home/qliang/god_bless_spiir/data/py2_compat_wguo_pycbc_split_0000_0095}
if [ -n "${BANK_DIR:-}" ]; then
    export BANK_DIR_SOURCE=${BANK_DIR_SOURCE:-explicit}
elif bank_dir_has_required_files "${DEFAULT_BANK_DIR}"; then
    export BANK_DIR=${DEFAULT_BANK_DIR}
    export BANK_DIR_SOURCE=${BANK_DIR_SOURCE:-package-root}
elif bank_dir_has_required_files "${FALLBACK_BANK_DIR}"; then
    export BANK_DIR=${FALLBACK_BANK_DIR}
    export BANK_DIR_SOURCE=${BANK_DIR_SOURCE:-fallback-existing-bank-files}
else
    export BANK_DIR=${DEFAULT_BANK_DIR}
    export BANK_DIR_SOURCE=${BANK_DIR_SOURCE:-package-root-missing-required-files}
fi
export NONINJ_STATS_LOC=${NONINJ_STATS_LOC:-/fred/oz016/wguo/SSM/runs/O4a/chunk3/part2}
export DETRSP_MAP=${DETRSP_MAP:-/fred/oz016/wguo/SSM/runs/O4a/chunk4/part1/H1L1_1372837467_detrsp_map.xml}
export WGUO_BANK_STATS_DIR=${WGUO_BANK_STATS_DIR:-/fred/oz016/wguo/packages/spiir/src/spiir/search/bank_dofs}

export MAX_GROUP=${MAX_GROUP:-15}
export NODES_AMOUNT=${NODES_AMOUNT:-$((MAX_GROUP + 1))}
export UPDATE_INTERVAL_SECONDS=${UPDATE_INTERVAL_SECONDS:-10}
export MONITOR_INTERVAL_SECONDS=${MONITOR_INTERVAL_SECONDS:-60}
export ZEROLAG_SNAPSHOT_INTERVAL_SECONDS=${ZEROLAG_SNAPSHOT_INTERVAL_SECONDS:-600}
export BACKGROUND_UPDATE_TRIGGER_SECONDS=${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}
if validate_background_contract; then
    status=0
else
    status=$?
fi
if [ "${status}" -ne 0 ]; then
    return "${status}" 2>/dev/null || exit "${status}"
fi
export BANKS_PER_GROUP=${BANKS_PER_GROUP:-6}
export FAR_INITIAL_WINDOW_POLICY=${FAR_INITIAL_WINDOW_POLICY:-skip}
export START_BANK=${START_BANK:-0}
export PIPELINE_MODE=${PIPELINE_MODE:-single}
export SINGLE_INPUT_KIND=${SINGLE_INPUT_KIND:-zerolag}

# Optional wall-clock gate for archived-frame replay.  The SPIIR command still
# reads a frame cache, but the single-detector sidecar and monitor only expose
# zerolag snapshots whose GPS end time has been reached by this simulated
# online clock.
export ONLINE_REPLAY_SYNC=${ONLINE_REPLAY_SYNC:-0}
export ONLINE_REPLAY_RATE=${ONLINE_REPLAY_RATE:-1.0}
export ONLINE_REPLAY_ALLOWED_LAG_SECONDS=${ONLINE_REPLAY_ALLOWED_LAG_SECONDS:-0}
export ONLINE_REPLAY_START_GPS=${ONLINE_REPLAY_START_GPS:-${DATA_START_TIME}}
export ONLINE_REPLAY_START_WALL=${ONLINE_REPLAY_START_WALL:-}

export psd=${psd:-}
