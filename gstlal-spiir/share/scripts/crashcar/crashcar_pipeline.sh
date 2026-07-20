#!/usr/bin/env bash
set -euo pipefail

i=${SLURM_ARRAY_TASK_ID:-0}
jobno=$(seq -f "%03g" "${i}" "${i}")
mkdir -p "${jobno}" logs monitor

CRASH_ROOT=${CRASH_ROOT:?CRASH_ROOT required}
TOP_RUN_ROOT=${TOP_RUN_ROOT:?TOP_RUN_ROOT required}
export PATH="${CRASH_ROOT}/install/bin:${PATH}"
export PYTHONPATH="${CRASH_ROOT}/install/lib/python3.10/site-packages:${PYTHONPATH:-}"
export GST_PLUGIN_PATH="${CRASH_ROOT}/install/lib/gstreamer-1.0:${GST_PLUGIN_PATH:-}"
export LD_LIBRARY_PATH="${CRASH_ROOT}/install/lib:${LD_LIBRARY_PATH:-}"
export GST_REGISTRY="${PWD}/gst-registry-crashcar-${jobno}.bin"
export GST_REGISTRY_UPDATE=yes

bankdir=${WGUO_O3A_BANK_DIR:-/fred/oz016/sunil/O3b_py3_banks}
macrostart=${WGUO_O3A_START_GPS:?WGUO_O3A_START_GPS required}
macroend=${WGUO_O3A_END_GPS:?WGUO_O3A_END_GPS required}
noninj_stats_loc=${WGUO_O3A_NONINJ_STATS_LOC:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}
map=${WGUO_O3A_DETRSP_MAP:-/fred/oz016/wguo/odds_ratio/O3a/chunk14/multi_det-BNS-LVK_inj/H1L1V1_1248134334_detrsp_map.xml}
cache=${WGUO_O3A_FRAME_CACHE:-/fred/oz016/sunil/run_utils/frames_chache/frame_O3a.cache}
start=${WGUO_O3A_START_BANK:-0}
bpj=${WGUO_O3A_BANKS_PER_GROUP:-8}
zerolag_snapshot_interval=${ZEROLAG_SNAPSHOT_INTERVAL_SECONDS:-${WGUO_O3A_SNAPSHOT_INTERVAL:-3600}}
background_update_interval=${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS:-${BACKGROUND_UPDATE_TRIGGER_SECONDS:-${CRASHCAR_SNAPSHOT_INTERVAL_SECONDS:-${zerolag_snapshot_interval}}}}
assignfar_refresh_interval=${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS:-${background_update_interval}}
fapupdater_interval=${FINALSINK_FAPUPDATER_INTERVAL_SECONDS:-${background_update_interval}}
collect_walltime=${WGUO_O3A_COLLECT_WALLTIME:-10800,10800,10800}
if [[ "${collect_walltime}" != *,* ]]; then
    collect_walltime="${BACKGROUND_ACCUMULATION_SECONDS:-10800},${BACKGROUND_ACCUMULATION_SECONDS:-10800},${BACKGROUND_ACCUMULATION_SECONDS:-10800}"
fi
far_factor=${WGUO_O3A_FAR_FACTOR:-25}
gracedb_far_thresh=${WGUO_O3A_GRACEDB_FAR_THRESH:-0}
need_online=${WGUO_O3A_FINALSINK_NEED_ONLINE_PERFORM:-0}
snr_series_logfar_threshold=${SNR_series_logFAR_threshold:-${snr_series_logFAR_threshold:-${SNR_SERIES_LOG_FAR_THRESHOLD:--4}}}
if [[ ! "${snr_series_logfar_threshold}" =~ ^[+-]?(([0-9]+([.][0-9]*)?)|([.][0-9]+))([eE][+-]?[0-9]+)?$ ]] ||
   ! python3 -c "import math, sys; value = float(sys.argv[1]); raise SystemExit(0 if math.isfinite(value) else 2)" "${snr_series_logfar_threshold}"; then
  printf "crashcar_pipeline: SNR_series_logFAR_threshold must be a finite number, got %q\n" \
    "${snr_series_logfar_threshold}" >&2
  exit 2
fi
TAIL_LOG_FAR=$(python3 - "${TAIL_LOG_FAR:--2}" <<'PY'
import math
import sys
value = float(sys.argv[1])
if not math.isfinite(value) or not value < 0.0:
    raise SystemExit("tail_log_FAR must be finite and strictly negative")
print("{:.17g}".format(value))
PY
) || exit 2
export TAIL_LOG_FAR
injection_mode=${WGUO_O3A_INJECTION_MODE:-none}
injection_file=${WGUO_O3A_INJECTION_FILE:-}

export DATA_START_TIME="${macrostart}"
export MAX_DATA_DURATION_SECONDS=$((macroend - macrostart))
export DATA_END_TIME="${macroend}"
export CRASHCAR_ENABLE=${CRASHCAR_ENABLE:-1}
case "${CRASHCAR_ENABLE}" in
  0)
    finalsink_postcoh_schema_mode=legacy-a107
    ;;
  1)
    finalsink_postcoh_schema_mode=crashcar-a109
    ;;
  *)
    printf 'crashcar_pipeline: CRASHCAR_ENABLE must be exactly 0 or 1, got %q\n' \
      "${CRASHCAR_ENABLE}" >&2
    exit 2
    ;;
esac
export CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP=${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}
export CRASHCAR_SEGMENT_LIVETIME_CSV=${CRASHCAR_SEGMENT_LIVETIME_CSV:-}
export WGUO_O3A_SEGMENT_XML=${WGUO_O3A_SEGMENT_XML:-${SEGMENT_XML:-${SINGLE_SEGMENT_XML:-}}}
export CRASHCAR_WORKER_ID=${i}
export CRASHCAR_DETAIL_OUTPUT_FNAME="${PWD}/crashcar_singlefar_detail_worker${jobno}.csv"
export CRASHCAR_LOG10_FAR_THRESHOLD=${CRASHCAR_LOG10_FAR_THRESHOLD:-90}
export BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS:-10800}
export FORMAL_BACKGROUND_ACCUMULATION_SECONDS=${FORMAL_BACKGROUND_ACCUMULATION_SECONDS:-10800}
export CRASHCAR_BACKGROUND_REQUIRED_SECONDS=${CRASHCAR_BACKGROUND_REQUIRED_SECONDS:-10800}
export BACKGROUND_UPDATE_TRIGGER_SECONDS=${BACKGROUND_UPDATE_TRIGGER_SECONDS:-${background_update_interval}}
export CRASHCAR_SNAPSHOT_INTERVAL_SECONDS=${CRASHCAR_SNAPSHOT_INTERVAL_SECONDS:-${zerolag_snapshot_interval}}
export CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME=${CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME:-}

multi_assignfar_enabled=1
postcoh_hist_trials=100
export CRASHCAR_MULTI_BACKGROUND_READONLY=0
export CRASHCAR_SINGLE_BACKGROUND_READONLY=0
single_background_mode=${CRASHCAR_SINGLE_BACKGROUND_MODE:-${SINGLE_BACKGROUND_MODE:-rolling}}
case "${single_background_mode}" in
  rolling)
    # Compatibility input to the unchanged graph policy.  No immutable
    # background artifact is implied by this legacy internal boolean.
    export CRASHCAR_MULTI_BACKGROUND_FROZEN=0
    macrofarinput=${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz
    macrolocfapoutput=${PWD}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${PWD}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${PWD}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz
    ;;
  live_readonly)
    # The continuing no-injection process owns both accumulators.  This
    # injection process reads stable producer paths and keeps normal refresh.
    # Suppress local time-slide rows: without the accumulator they would reach
    # FinalSink even though such background rows intentionally have no series.
    postcoh_hist_trials=0
    export CRASHCAR_MULTI_BACKGROUND_READONLY=1
    export CRASHCAR_SINGLE_BACKGROUND_READONLY=1
    # Existing graph-stage compatibility flag: it disables mutation only and
    # does not identify, copy, or pin a background artifact.
    export CRASHCAR_MULTI_BACKGROUND_FROZEN=1
    if [ "${CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE:-}" != "consumer" ] ||
       [[ "${CRASHCAR_LIVE_BACKGROUND_ROOT:-}" != /* ]] ||
       [[ "${CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON:-}" != "${CRASHCAR_LIVE_BACKGROUND_ROOT}/run/${jobno}/single_background.json" ]] ||
       [ -L "${CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON:-}" ] ||
       { [ -e "${CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON:-}" ] &&
         [ ! -f "${CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON:-}" ]; }; then
      printf 'crashcar_pipeline: live_readonly single binding is not the direct producer worker path\n' >&2
      exit 2
    fi
    if ! [[ "${assignfar_refresh_interval}" =~ ^[1-9][0-9]*$ ]]; then
      printf 'crashcar_pipeline: live_readonly requires positive existing assignfar refresh cadence\n' >&2
      exit 2
    fi
    macrofarinput=${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz
    macrolocfapoutput=
    ;;
  *)
    printf 'crashcar_pipeline: invalid single background mode %q; expected rolling or live_readonly\n' \
      "${single_background_mode}" >&2
    exit 2
    ;;
esac

case "${CRASHCAR_BG_ONLY:-0}" in
  0) ;;
  1)
    if [ "${single_background_mode}" != "rolling" ] ||
       [ "${injection_mode}" != "none" ] ||
       [ -n "${injection_file}" ]; then
      printf 'crashcar_pipeline: BG-only requires rolling mode with no injection input\n' >&2
      exit 2
    fi
    multi_assignfar_enabled=0
    macrofarinput=
    ;;
  *)
    printf 'crashcar_pipeline: CRASHCAR_BG_ONLY must be exactly 0 or 1\n' >&2
    exit 2
    ;;
esac
if [ "${single_background_mode}" = "live_readonly" ] &&
   { [ "${injection_mode}" = "none" ] || [ -z "${injection_file}" ]; }; then
  printf 'crashcar_pipeline: live_readonly requires active injection mode and injection XML\n' >&2
  exit 2
fi
if [ "${single_background_mode}" = "rolling" ] &&
   [ "${injection_mode}" != "none" ]; then
  printf 'crashcar_pipeline: injection foreground cannot run with mutable background mode\n' >&2
  exit 2
fi

verify_scientific_input() {
    local input_path=$1 label=$2
    if [ "${single_background_mode}" = "live_readonly" ]; then
        [ ! -L "${input_path}" ] &&
            { [ ! -e "${input_path}" ] || [ -f "${input_path}" ]; } || {
            printf 'crashcar_pipeline: %s soft-start path is not a regular producer path: %s\n' \
              "${label}" "${input_path}" >&2
            return 2
        }
        return 0
    fi
    [ -f "${input_path}" ] && [ ! -L "${input_path}" ] || {
        printf 'crashcar_pipeline: %s must be a regular non-symlink file: %s\n' \
          "${label}" "${input_path}" >&2
        return 2
    }
}
if [ "${multi_assignfar_enabled}" = "1" ]; then
    for f in ${macrofarinput//,/ }; do
        verify_scientific_input "${f}" live_multi_stats
    done
fi
[ -f "${map}" ] || { echo "missing detrsp map ${map}" >&2; exit 2; }
[ -f "${cache}" ] || { echo "missing frame cache ${cache}" >&2; exit 2; }

cmd=(
  gstlal_inspiral_postcohspiir_online
  --state-channel-name H1=GDS-CALIB_STATE_VECTOR
  --state-channel-name L1=GDS-CALIB_STATE_VECTOR
  --state-channel-name V1=DQ_ANALYSIS_STATE_VECTOR
  --state-vector-on-bits H1=3
  --state-vector-on-bits L1=3
  --state-vector-on-bits V1=1027
  --state-vector-off-bits H1=0
  --state-vector-off-bits L1=0
  --state-vector-off-bits V1=0
  --job-tag "${jobno}"
  --tmp-space _CONDOR_SCRATCH_DIR
)

actual_worker_bank_ids=
for bank in $(seq -f "%04g" $((start + bpj * i)) $((start + bpj * (i + 1) - 1))); do
  bank_decimal=$((10#${bank}))
  if [ -n "${actual_worker_bank_ids}" ]; then
    actual_worker_bank_ids="${actual_worker_bank_ids},"
  fi
  actual_worker_bank_ids="${actual_worker_bank_ids}${bank_decimal}"
  H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
  L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
  V1bank=${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
  for bf in "$H1bank" "$L1bank" "$V1bank"; do
    [ -f "$bf" ] || { echo "missing bank $bf" >&2; exit 2; }
  done
  cmd+=(--iir-bank "H1:${H1bank},L1:${L1bank},V1:${V1bank}")
done
if [ "${actual_worker_bank_ids}" != "${CRASHCAR_WORKER_BANK_IDS_EXPECTED:?expected worker bank roster required}" ]; then
  printf 'crashcar_pipeline: worker bank roster differs from authenticated Slurm binding\n' >&2
  exit 2
fi

cmd+=(
  --data-source frames
  --channel-name H1=GDS-CALIB_STRAIN_CLEAN
  --channel-name L1=GDS-CALIB_STRAIN_CLEAN
  --channel-name V1=Hrec_hoft_16384Hz
  --gpu-acc on
  --ht-gate-threshold 15.0
  --cuda-postcoh-snglsnr-thresh 4
  --cuda-postcoh-hist-trials "${postcoh_hist_trials}"
  --cuda-postcoh-detrsp-fname "${map}"
  --cuda-postcoh-output-skymap 100
  --check-time-stamp
  --finalsink-cluster-window 1
  --finalsink-output-prefix "${jobno}/${jobno}_zerolag"
  --finalsink-snapshot-interval "${zerolag_snapshot_interval}"
)

if [ "${CRASHCAR_MULTI_BACKGROUND_READONLY}" = "0" ]; then
  cmd+=(--cohfar-accumbackground-snapshot-interval "${background_update_interval}")
  for bank in $(seq -f "%04g" $((start + bpj * i)) $((start + bpj * (i + 1) - 1))); do
    cmd+=(--cohfar-accumbackground-output-prefix "${jobno}/bank${bank}_stats")
  done
fi

if [ "${multi_assignfar_enabled}" = "1" ]; then
  cmd+=(
    --cohfar-assignfar-input-fname "${macrofarinput}"
    --cohfar-assignfar-silent-time 0
    --cohfar-assignfar-refresh-interval "${assignfar_refresh_interval}"
  )
fi

cmd+=(
  --finalsink-fapupdater-interval "${fapupdater_interval}"
  --finalsink-postcoh-schema-mode "${finalsink_postcoh_schema_mode}"
  --finalsink-fapupdater-collect-walltime "${collect_walltime}"
  --finalsink-far-factor "${far_factor}"
  --snr-series-logfar-threshold "${snr_series_logfar_threshold}"
  --finalsink-gracedb-far-thresh "${gracedb_far_thresh}"
  --finalsink-need-online-perform "${need_online}"
  --finalsink-gracedb-group Test
  --finalsink-gracedb-search MDC
  --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/
  --cuda-postcoh-detrsp-refresh-interval 86400
  --code-version "${CRASHCAR_CODE_VERSION:-spiir-crashcar}"
  --frame-cache "${cache}"
  --gps-start-time "${macrostart}"
  --gps-end-time "${macroend}"
  --finalsink-singlefar-veto-thresh 0.5
  --track-psd
  --psd-fft-length 4
)
if [ "${CRASHCAR_MULTI_BACKGROUND_READONLY}" = "0" ]; then
  cmd+=(--finalsink-fapupdater-output-fname "${macrolocfapoutput}")
fi

if [ "${injection_mode}" != "none" ]; then
  if [ -z "${injection_file}" ]; then
    printf 'crashcar_pipeline: WGUO_O3A_INJECTION_MODE=%s but WGUO_O3A_INJECTION_FILE is empty\n' \
      "${injection_mode}" >&2
    exit 2
  fi
  [ -f "${injection_file}" ] || { echo "missing injection file ${injection_file}" >&2; exit 2; }
  cmd+=(--blind-injections "${injection_file}")
  {
    printf 'This crashcar run includes blind injections.\n'
    printf 'Do not use local accumulated backgrounds from this injection foreground as clean background.\n'
    printf 'Injection file: %s\n' "${injection_file}"
    printf 'External multi/coherent FAR input: %s\n' "${macrofarinput}"
  } > DO_NOT_USE_AS_BACKGROUND_INJECTION_STATS.txt
fi

{
  printf 'RUN_ROOT=%s\n' "$PWD"
  printf 'CRASHCAR_MULTI_ASSIGNFAR_ENABLED=%s\n' "${multi_assignfar_enabled}"
  printf 'CRASHCAR_MULTI_BACKGROUND_READONLY=%s\n' "${CRASHCAR_MULTI_BACKGROUND_READONLY}"
  printf 'CRASHCAR_SINGLE_BACKGROUND_READONLY=%s\n' "${CRASHCAR_SINGLE_BACKGROUND_READONLY}"
  printf 'CRASHCAR_CMD'
  printf ' %q' "${cmd[@]}"
  printf '\n'
  env | grep -E '^(CRASHCAR|BACKGROUND|FORMAL|DATA_|WGUO_O3A|GST_|PYTHONPATH|LD_LIBRARY_PATH|PATH)=' | sort
} > "logs/crashcar_command_${jobno}.txt"

pipeline_rc=0
if "${cmd[@]}"; then
  pipeline_rc=0
else
  pipeline_rc=$?
fi

# The shared OzSTAR run_spiir_py3 helper performs cleanup after Apptainer and
# therefore does not preserve the container command's status.  Publish the
# direct pipeline status to the package wrapper through one fixed worker file.
if [ -n "${CRASHCAR_PIPELINE_EXIT_STATUS_FILE:-}" ]; then
  case "${CRASHCAR_PIPELINE_EXIT_STATUS_FILE}" in
    "${PWD}"/logs/*) ;;
    *)
      printf 'crashcar_pipeline: exit-status path must be in the worker logs directory\n' >&2
      exit 2
      ;;
  esac
  status_tmp="${CRASHCAR_PIPELINE_EXIT_STATUS_FILE}.tmp.$$"
  printf '%s\n' "${pipeline_rc}" > "${status_tmp}"
  mv -f -- "${status_tmp}" "${CRASHCAR_PIPELINE_EXIT_STATUS_FILE}"
fi
exit "${pipeline_rc}"
