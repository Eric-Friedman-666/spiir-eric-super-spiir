#!/usr/bin/env bash
set -euo pipefail

WORKER=${SLURM_ARRAY_TASK_ID:-0}
JOBNO=$(printf '%03d' "${WORKER}")
mkdir -p "${JOBNO}" logs monitor
CRASH_ROOT=${CRASH_ROOT:?CRASH_ROOT required}
ROLE=${CRASHCAR_ROLE:?CRASHCAR_ROLE required}
export PATH=${CRASH_ROOT}/install/bin:${PATH}
export PYTHONPATH=${CRASH_ROOT}/install/lib/python3.10/site-packages:${PYTHONPATH:-}
export GST_PLUGIN_PATH=${CRASH_ROOT}/install/lib/gstreamer-1.0:${GST_PLUGIN_PATH:-}
export LD_LIBRARY_PATH=${CRASH_ROOT}/install/lib:${LD_LIBRARY_PATH:-}
export GST_REGISTRY=${PWD}/gst-registry-crashcar-${JOBNO}.bin
export GST_REGISTRY_UPDATE=yes

BANK_DIR=${WGUO_O3A_BANK_DIR:?bank directory required}
GPS_START=${WGUO_O3A_START_GPS:?start GPS required}
GPS_END=${WGUO_O3A_END_GPS:?end GPS required}
STATS_ROOT=${WGUO_O3A_NONINJ_STATS_LOC:?background stats root required}
DETRSP=${WGUO_O3A_DETRSP_MAP:?detector response required}
CACHE=${WGUO_O3A_FRAME_CACHE:?frame cache required}
START_BANK=${WGUO_O3A_START_BANK:-0}
BANKS_PER_WORKER=${WGUO_O3A_BANKS_PER_GROUP:-8}
ZEROLAG_UPDATE=${CRASHCAR_SNAPSHOT_INTERVAL_SECONDS:-3600}
BG_UPDATE=${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}
MULTI_SNAPSHOT=${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS:-${BG_UPDATE}}
ASSIGN_REFRESH=${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS:-${BG_UPDATE}}
FAP_UPDATE=${FINALSINK_FAPUPDATER_INTERVAL_SECONDS:-${BG_UPDATE}}
COLLECT_WALLTIME=${FINALSINK_FAPUPDATER_COLLECT_WALLTIME:-10800,10800,10800}
SNR_THRESHOLD=${SNR_series_logFAR_threshold:--4}
INJECTION_FILE=${WGUO_O3A_INJECTION_FILE:-}

export DATA_START_TIME=${GPS_START} DATA_END_TIME=${GPS_END}
export CRASHCAR_DETAIL_OUTPUT_FNAME=${PWD}/crashcar_singlefar_detail_worker${JOBNO}.csv
export CRASHCAR_LOG10_FAR_THRESHOLD=${CRASHCAR_LOG10_FAR_THRESHOLD:-90}
export BACKGROUND_ACCUMULATION_SECONDS=${BACKGROUND_ACCUMULATION_SECONDS:-10800}
export BACKGROUND_UPDATE_TRIGGER_SECONDS=${BG_UPDATE}

case "${CRASHCAR_ENABLE:-1}" in
    0) POSTCOH_SCHEMA=legacy-a107 ;;
    1) POSTCOH_SCHEMA=crashcar-a109 ;;
    *) printf 'crashcar_pipeline: CRASHCAR_ENABLE must be 0 or 1\n' >&2; exit 2 ;;
esac
case "${ROLE}" in
    A)
        HIST_TRIALS=100
        ACCUMULATE_MULTI=1
        MULTI_INPUT=${PWD}/${JOBNO}/${JOBNO}_marginalized_stats_2w.xml.gz,${PWD}/${JOBNO}/${JOBNO}_marginalized_stats_1d.xml.gz,${PWD}/${JOBNO}/${JOBNO}_marginalized_stats_2h.xml.gz
        MULTI_OUTPUT=${MULTI_INPUT}
        [ -z "${INJECTION_FILE}" ] || { printf 'crashcar_pipeline: role A cannot use injection_file\n' >&2; exit 2; }
        ;;
    B)
        HIST_TRIALS=0
        ACCUMULATE_MULTI=0
        MULTI_INPUT=${STATS_ROOT}/${JOBNO}/${JOBNO}_marginalized_stats_2w.xml.gz,${STATS_ROOT}/${JOBNO}/${JOBNO}_marginalized_stats_1d.xml.gz,${STATS_ROOT}/${JOBNO}/${JOBNO}_marginalized_stats_2h.xml.gz
        MULTI_OUTPUT=
        [ -n "${INJECTION_FILE}" ] && [ -f "${INJECTION_FILE}" ] || { printf 'crashcar_pipeline: role B requires injection_file\n' >&2; exit 2; }
        ;;
    *) printf 'crashcar_pipeline: invalid role %s\n' "${ROLE}" >&2; exit 2 ;;
esac
[ -f "${DETRSP}" ] && [ -f "${CACHE}" ] || { printf 'crashcar_pipeline: missing input data\n' >&2; exit 2; }

CMD=(
  gstlal_inspiral_postcohspiir_online
  --state-channel-name H1=GDS-CALIB_STATE_VECTOR
  --state-channel-name L1=GDS-CALIB_STATE_VECTOR
  --state-channel-name V1=DQ_ANALYSIS_STATE_VECTOR
  --state-vector-on-bits H1=3 --state-vector-on-bits L1=3 --state-vector-on-bits V1=1027
  --state-vector-off-bits H1=0 --state-vector-off-bits L1=0 --state-vector-off-bits V1=0
  --job-tag "${JOBNO}" --tmp-space _CONDOR_SCRATCH_DIR
)
ROSTER=
for bank in $(seq -f '%04g' $((START_BANK + BANKS_PER_WORKER * WORKER)) $((START_BANK + BANKS_PER_WORKER * (WORKER + 1) - 1))); do
    decimal=$((10#${bank})); [ -z "${ROSTER}" ] || ROSTER=${ROSTER},; ROSTER=${ROSTER}${decimal}
    H1=${BANK_DIR}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
    L1=${BANK_DIR}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
    V1=${BANK_DIR}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
    [ -f "${H1}" ] && [ -f "${L1}" ] && [ -f "${V1}" ] || { printf 'crashcar_pipeline: missing bank %s\n' "${bank}" >&2; exit 2; }
    CMD+=(--iir-bank "H1:${H1},L1:${L1},V1:${V1}")
done
[ "${ROSTER}" = "${CRASHCAR_WORKER_BANK_IDS_EXPECTED:?worker roster required}" ] || { printf 'crashcar_pipeline: worker roster mismatch\n' >&2; exit 2; }

CMD+=(
  --data-source frames
  --channel-name H1=GDS-CALIB_STRAIN_CLEAN
  --channel-name L1=GDS-CALIB_STRAIN_CLEAN
  --channel-name V1=Hrec_hoft_16384Hz
  --gpu-acc on --ht-gate-threshold 15.0
  --cuda-postcoh-snglsnr-thresh 4
  --cuda-postcoh-hist-trials "${HIST_TRIALS}"
  --cuda-postcoh-detrsp-fname "${DETRSP}"
  --cuda-postcoh-output-skymap 100
  --check-time-stamp
  --cohfar-assignfar-input-fname "${MULTI_INPUT}"
  --cohfar-assignfar-silent-time 0
  --cohfar-assignfar-refresh-interval "${ASSIGN_REFRESH}"
  --finalsink-cluster-window 1
  --finalsink-output-prefix "${JOBNO}/${JOBNO}_zerolag"
  --finalsink-snapshot-interval "${ZEROLAG_UPDATE}"
  --finalsink-fapupdater-interval "${FAP_UPDATE}"
  --finalsink-postcoh-schema-mode "${POSTCOH_SCHEMA}"
  --finalsink-fapupdater-collect-walltime "${COLLECT_WALLTIME}"
  --finalsink-far-factor 25
  --snr-series-logfar-threshold "${SNR_THRESHOLD}"
  --finalsink-gracedb-far-thresh 0
  --finalsink-need-online-perform 0
  --finalsink-gracedb-group Test --finalsink-gracedb-search MDC
  --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/
  --cuda-postcoh-detrsp-refresh-interval 86400
  --code-version "${CRASHCAR_CODE_VERSION:-spiir-crashcar-ab}"
  --frame-cache "${CACHE}" --gps-start-time "${GPS_START}" --gps-end-time "${GPS_END}"
  --finalsink-singlefar-veto-thresh 0.5
  --track-psd --psd-fft-length 4
)
if [ "${ACCUMULATE_MULTI}" = 1 ]; then
    CMD+=(--cohfar-accumbackground-snapshot-interval "${MULTI_SNAPSHOT}")
    for bank in $(seq -f '%04g' $((START_BANK + BANKS_PER_WORKER * WORKER)) $((START_BANK + BANKS_PER_WORKER * (WORKER + 1) - 1))); do
        CMD+=(--cohfar-accumbackground-output-prefix "${JOBNO}/bank${bank}_stats")
    done
    CMD+=(--finalsink-fapupdater-output-fname "${MULTI_OUTPUT}")
else
    CMD+=(--blind-injections "${INJECTION_FILE}")
fi

{
    printf 'RUN_ROOT=%s\nROLE=%s\nCRASHCAR_CMD' "${PWD}" "${ROLE}"
    printf ' %q' "${CMD[@]}"; printf '\n'
} > "logs/crashcar_command_${JOBNO}.txt"
PIPELINE_RC=0
"${CMD[@]}" || PIPELINE_RC=$?
if [ -n "${CRASHCAR_PIPELINE_EXIT_STATUS_FILE:-}" ]; then
    printf '%s\n' "${PIPELINE_RC}" > "${CRASHCAR_PIPELINE_EXIT_STATUS_FILE}.tmp"
    mv -f "${CRASHCAR_PIPELINE_EXIT_STATUS_FILE}.tmp" "${CRASHCAR_PIPELINE_EXIT_STATUS_FILE}"
fi
exit "${PIPELINE_RC}"
