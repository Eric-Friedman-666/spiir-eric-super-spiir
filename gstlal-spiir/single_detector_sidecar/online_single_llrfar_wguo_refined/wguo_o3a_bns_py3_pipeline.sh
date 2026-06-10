#!/bin/bash
# O3a BNS py3 wrapper with optional injection and explicit external FAR input.

set -eo pipefail

i=${SLURM_ARRAY_TASK_ID:-0}
jobno=$(seq -f "%03g" "${i}" "${i}")

bankdir=${WGUO_O3A_BANK_DIR:-/fred/oz016/sunil/O3b_py3_banks}
macrostart=${WGUO_O3A_START_GPS:-1241725020}
macroend=${WGUO_O3A_END_GPS:-1241811420}

noninj_stats_loc=${WGUO_O3A_NONINJ_STATS_LOC:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}
inj_stats_loc=$(pwd)

map=${WGUO_O3A_DETRSP_MAP:-/fred/oz016/wguo/odds_ratio/O3a/chunk6/multi_det-BNS-LVK_inj/H1L1V1_1242105073_detrsp_map.xml}
cache=${WGUO_O3A_FRAME_CACHE:-/fred/oz016/sunil/run_utils/frames_chache/frame_O3a.cache}

start=${WGUO_O3A_START_BANK:-0}
bpj=${WGUO_O3A_BANKS_PER_GROUP:-4}
snapshot_interval=${WGUO_O3A_SNAPSHOT_INTERVAL:-3600}
collect_walltime=${WGUO_O3A_COLLECT_WALLTIME:-1209600,86400,7200}
far_factor=${WGUO_O3A_FAR_FACTOR:-25}
gracedb_far_thresh=${WGUO_O3A_GRACEDB_FAR_THRESH:-0.0001}
need_online=${WGUO_O3A_FINALSINK_NEED_ONLINE_PERFORM:-0}
injection_mode=${WGUO_O3A_INJECTION_MODE:-auto}
injection_file=${WGUO_O3A_INJECTION_FILE:-}

macrofarinput=${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz
macrolocfapoutput=${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz

macrojobtag=${jobno}

mkdir -p "${jobno}" logs monitor

cwd_real=$(readlink -f "$(pwd)")
IFS=, read -r -a far_files <<< "${macrofarinput}"
for far_file in "${far_files[@]}"; do
    if [ ! -f "${far_file}" ]; then
        printf 'O3A_BNS_PIPELINE_ERROR missing external multi background stats: %s\n' "${far_file}" >&2
        exit 2
    fi
    far_real=$(readlink -f "${far_file}")
    case "${far_real}" in
        "${cwd_real}"/*)
            printf 'O3A_BNS_PIPELINE_ERROR external multi background points inside current run: %s\n' "${far_file}" >&2
            exit 2
            ;;
    esac
done

cmd=(
    "$(which gstlal_inspiral_postcohspiir_online)"
    --state-channel-name H1=GDS-CALIB_STATE_VECTOR
    --state-channel-name L1=GDS-CALIB_STATE_VECTOR
    --state-channel-name V1=DQ_ANALYSIS_STATE_VECTOR
    --state-vector-on-bits H1=3
    --state-vector-on-bits L1=3
    --state-vector-on-bits V1=1027
    --state-vector-off-bits H1=0
    --state-vector-off-bits L1=0
    --state-vector-off-bits V1=0
    --job-tag "${macrojobtag}"
    --tmp-space _CONDOR_SCRATCH_DIR
)

for bank in $(seq -f "%04g" $((start + bpj * i)) $((start + bpj * (i + 1) - 1))); do
    H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
    L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
    V1bank=${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
    for bank_file in "${H1bank}" "${L1bank}" "${V1bank}"; do
        [ -f "${bank_file}" ] || {
            printf 'O3A_BNS_PIPELINE_ERROR missing bank file: %s\n' "${bank_file}" >&2
            exit 2
        }
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
    --finalsink-snapshot-interval "${snapshot_interval}"
    --cohfar-accumbackground-snapshot-interval "${snapshot_interval}"
)

for bank in $(seq -f "%04g" $((start + bpj * i)) $((start + bpj * (i + 1) - 1))); do
    cmd+=(--cohfar-accumbackground-output-prefix "${jobno}/bank${bank}_stats")
done

cmd+=(
    --cohfar-assignfar-input-fname "${macrofarinput}"
    --cohfar-assignfar-silent-time 0
    --cohfar-assignfar-refresh-interval "${snapshot_interval}"
    --finalsink-fapupdater-interval "${snapshot_interval}"
    --finalsink-cluster-window 1
    --finalsink-fapupdater-collect-walltime "${collect_walltime}"
    --finalsink-far-factor "${far_factor}"
    --finalsink-gracedb-far-thresh "${gracedb_far_thresh}"
    --finalsink-need-online-perform "${need_online}"
    --finalsink-gracedb-group Test
    --finalsink-gracedb-search MDC
    --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/
    --cuda-postcoh-detrsp-refresh-interval 86400
    --code-version spiir-O4-EW-development
    --frame-cache "${cache}"
    --gps-start-time "${macrostart}"
    --gps-end-time "${macroend}"
    --finalsink-singlefar-veto-thresh 0.5
    --track-psd
    --psd-fft-length 4
)

if [ "${injection_mode}" != "none" ]; then
    if [ -z "${injection_file}" ]; then
        printf 'O3A_BNS_PIPELINE_ERROR WGUO_O3A_INJECTION_MODE=%s but WGUO_O3A_INJECTION_FILE is empty\n' \
            "${injection_mode}" >&2
        exit 2
    fi
    [ -f "${injection_file}" ] || {
        printf 'O3A_BNS_PIPELINE_ERROR missing injection file: %s\n' "${injection_file}" >&2
        exit 2
    }
    cmd+=(--injection-file "${injection_file}")
    {
        printf 'This run includes BNS injections.\n'
        printf 'Local *_marginalized_stats_*.xml.gz files from this run must not be used as background.\n'
        printf 'External multi/coherent FAR input: %s\n' "${macrofarinput}"
    } > DO_NOT_USE_AS_BACKGROUND_INJECTION_STATS.txt
fi

cmd+=(--finalsink-fapupdater-output-fname "${macrolocfapoutput}")

{
    printf 'O3A_BNS_PY3_CMD'
    printf ' %q' "${cmd[@]}"
    printf '\n'
    printf 'O3A_BNS_MULTI_BG_INPUT=%s\n' "${macrofarinput}"
    printf 'O3A_BNS_LOCAL_STATS_OUTPUT=%s\n' "${macrolocfapoutput}"
    printf 'O3A_BNS_INJECTION_MODE=%s\n' "${injection_mode}"
    printf 'O3A_BNS_GRACEDB_FAR_THRESH=%s\n' "${gracedb_far_thresh}"
    printf 'O3A_BNS_GPS_START=%s\n' "${macrostart}"
    printf 'O3A_BNS_GPS_END=%s\n' "${macroend}"
} > "logs/wguo_o3a_bns_py3_command_${jobno}.txt"

"${cmd[@]}"
