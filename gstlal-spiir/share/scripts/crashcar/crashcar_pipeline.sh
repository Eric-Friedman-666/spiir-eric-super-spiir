#!/usr/bin/env bash
set -euo pipefail

i=${SLURM_ARRAY_TASK_ID:-0}
jobno=$(seq -f "%03g" "${i}" "${i}")
mkdir -p "${jobno}" logs monitor

CRASH_ROOT=${CRASH_ROOT:?CRASH_ROOT required}
TOP_RUN_ROOT=${TOP_RUN_ROOT:?TOP_RUN_ROOT required}
export PATH="${CRASH_ROOT}/install/bin:${PATH}"
export PATH="${TOP_RUN_ROOT}/bin:${PATH}"
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
injection_mode=${WGUO_O3A_INJECTION_MODE:-none}
injection_file=${WGUO_O3A_INJECTION_FILE:-}

export DATA_START_TIME="${macrostart}"
export MAX_DATA_DURATION_SECONDS=$((macroend - macrostart))
export DATA_END_TIME="${macroend}"
export CRASHCAR_ENABLE=${CRASHCAR_ENABLE:-1}
export CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP=${CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP:-1}
export CRASHCAR_SEGMENT_LIVETIME_CSV=${CRASHCAR_SEGMENT_LIVETIME_CSV:-}
export WGUO_O3A_SEGMENT_XML=${WGUO_O3A_SEGMENT_XML:-${SEGMENT_XML:-${SINGLE_SEGMENT_XML:-}}}
export CRASHCAR_WORKER_ID=${i}
export CRASHCAR_DETAIL_OUTPUT_FNAME="${PWD}/crashcar_singlefar_detail_worker${jobno}.csv"
export CRASHCAR_LOG10_FAR_THRESHOLD=${CRASHCAR_LOG10_FAR_THRESHOLD:-90}
export CRASHCAR_SNR_SERIES_LOG10_FAR_THRESHOLD=${CRASHCAR_SNR_SERIES_LOG10_FAR_THRESHOLD:--4}
export SPIIR_CANDIDATE_EVENT_DIR=${SPIIR_CANDIDATE_EVENT_DIR:-"${PWD}/${jobno}/candidate_events"}
export CRASHCAR_SNR_SERIES_OUTPUT_DIR=${CRASHCAR_SNR_SERIES_OUTPUT_DIR:-"${PWD}/${jobno}/crashcar_candidate_events"}
export CRASHCAR_SNR_SERIES_WRITE_CSV=${CRASHCAR_SNR_SERIES_WRITE_CSV:-0}
export CRASHCAR_PRESERVE_TABLE_SINGLE_FAR=${CRASHCAR_PRESERVE_TABLE_SINGLE_FAR:-0}
export CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR=${CRASHCAR_FINALSINK_PRESERVE_TABLE_SINGLE_FAR:-1}
export CRASHCAR_FAR_FLOOR_COUNT=${CRASHCAR_FAR_FLOOR_COUNT:-1.0}
export CRASHCAR_LIVETIME_STEP=${CRASHCAR_LIVETIME_STEP:-1.0}
export CRASHCAR_MIN_SNR=${CRASHCAR_MIN_SNR:-4.0}
export BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS:-10800}
export FORMAL_BACKGROUND_ACCUMULATION_SECONDS=${FORMAL_BACKGROUND_ACCUMULATION_SECONDS:-10800}
export CRASHCAR_BACKGROUND_REQUIRED_SECONDS=${CRASHCAR_BACKGROUND_REQUIRED_SECONDS:-10800}
export BACKGROUND_UPDATE_TRIGGER_SECONDS=${BACKGROUND_UPDATE_TRIGGER_SECONDS:-${background_update_interval}}
export CRASHCAR_SNAPSHOT_INTERVAL_SECONDS=${CRASHCAR_SNAPSHOT_INTERVAL_SECONDS:-${zerolag_snapshot_interval}}
export CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME=${CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME:-}

macrofarinput=${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz
macrolocfapoutput=${PWD}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${PWD}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${PWD}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz

for f in ${macrofarinput//,/ }; do
    [ -f "$f" ] || { echo "missing external stats $f" >&2; exit 2; }
done
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

for bank in $(seq -f "%04g" $((start + bpj * i)) $((start + bpj * (i + 1) - 1))); do
  H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
  L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
  V1bank=${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
  for bf in "$H1bank" "$L1bank" "$V1bank"; do
    [ -f "$bf" ] || { echo "missing bank $bf" >&2; exit 2; }
  done
  cmd+=(--iir-bank "H1:${H1bank},L1:${L1bank},V1:${V1bank}")
done

cmd+=(
  --data-source frames
  --channel-name H1=GDS-CALIB_STRAIN_CLEAN
  --channel-name L1=GDS-CALIB_STRAIN_CLEAN
  --channel-name V1=Hrec_hoft_16384Hz
  --gpu-acc on
  --ht-gate-threshold 15.0
  --cuda-postcoh-snglsnr-thresh 4
  --cuda-postcoh-hist-trials 100
  --cuda-postcoh-detrsp-fname "${map}"
  --cuda-postcoh-output-skymap 100
  --check-time-stamp
  --finalsink-output-prefix "${jobno}/${jobno}_zerolag"
  --finalsink-single-trigger-stream "${jobno}/${jobno}_single_triggers.csv"
  --finalsink-snapshot-interval "${zerolag_snapshot_interval}"
  --cohfar-accumbackground-snapshot-interval "${background_update_interval}"
)

for bank in $(seq -f "%04g" $((start + bpj * i)) $((start + bpj * (i + 1) - 1))); do
  cmd+=(--cohfar-accumbackground-output-prefix "${jobno}/bank${bank}_stats")
done

cmd+=(
  --cohfar-assignfar-input-fname "${macrofarinput}"
  --cohfar-assignfar-silent-time 0
  --cohfar-assignfar-refresh-interval "${assignfar_refresh_interval}"
  --finalsink-fapupdater-interval "${fapupdater_interval}"
  --finalsink-cluster-window 1
  --finalsink-fapupdater-collect-walltime "${collect_walltime}"
  --finalsink-far-factor "${far_factor}"
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
  --finalsink-fapupdater-output-fname "${macrolocfapoutput}"
)

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
  printf 'CRASHCAR_CMD'
  printf ' %q' "${cmd[@]}"
  printf '\n'
  env | grep -E '^(CRASHCAR|BACKGROUND|FORMAL|DATA_|WGUO_O3A|GST_|PYTHONPATH|LD_LIBRARY_PATH|PATH)=' | sort
} > "logs/crashcar_command_${jobno}.txt"

"${cmd[@]}"
