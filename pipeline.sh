#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "Usage: bash pipeline.sh --single | --multi"
  echo "Optional env:"
  echo "  PIPELINE_MODE=single|multi"
}

PIPELINE_MODE="${PIPELINE_MODE:-}"
if [[ $# -gt 1 ]]; then
    echo "too many arguments"
    usage
    exit 1
fi

ARG_MODE=""
if [[ $# -eq 1 ]]; then
    case "$1" in
        --single)
            ARG_MODE="single"
            ;;
        --multi)
            ARG_MODE="multi"
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "unknown pipeline option '$1'. only --single or --multi are supported."
            usage
            exit 1
            ;;
    esac
fi

if [[ -n "${PIPELINE_MODE}" && -n "${ARG_MODE}" && "${PIPELINE_MODE}" != "${ARG_MODE}" ]]; then
    echo "pipeline mode conflict: PIPELINE_MODE=${PIPELINE_MODE}, arg mode=${ARG_MODE}"
    usage
    exit 1
fi

if [[ -z "${PIPELINE_MODE}" ]]; then
    PIPELINE_MODE="${ARG_MODE}"
fi

if [[ "${PIPELINE_MODE}" != "single" && "${PIPELINE_MODE}" != "multi" ]]; then
    echo "pipeline mode must be --single or --multi. current value='${PIPELINE_MODE}'"
    usage
    exit 1
fi

i="${SLURM_ARRAY_TASK_ID:-0}"
jobno=$(seq -f "%03g" "${i}" "${i}")
mkdir -p "${jobno}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# bankdir=/fred/oz016/manoj/O4_Banks/O4_New/filt/gstlal_iir_bank_b0optmin97pc_O4a_0
bankdir=/fred/oz016/sunil/O4b_banks/O4_spacing/filt/O4b_spacing_py2_banks
#bankdir=/fred/oz016/sunil/s240422ed/py3-bank

#injs=/fred/oz016/sunil/run_utils/injection_files/bbh/SG_RM-1241710700-1242315500.xml

macrostart=1368975618 # 2023-05-24T15:00:00
macroend=1370097052 # 2023-06-06T14:30:34

noninj_stats_loc=/fred/oz016/wguo/O4_offline/runs/O4a/ER
inj_stats_loc="$(pwd)"

map=/fred/oz016/wguo/O4_offline/runs/O4a/chunk1/H1L1_1369536335_detrsp_map.xml

cache=/fred/oz016/sunil/run_utils/frames_chache/frames_AR_O4a.cache

macronodename=postcohspiir

start=0
bpj=12
nretry=0

macrofarinput=${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz
macrolocfapoutput=${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz
macrojobtag=${jobno}

macroiirbank=()
macrostatsprefix=()
for bank in $(seq -f "%04g" $(( start+bpj*i )) $(( start+bpj*i )) ); do
    H1bank="${bankdir}/iir_H1-PYCBC_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
    L1bank="${bankdir}/iir_L1-PYCBC_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
    macroiirbank+=(--iir-bank "H1:${H1bank},L1:${L1bank}")
    macrostatsprefix+=(--cohfar-accumbackground-output-prefix "${jobno}/bank${bank}_stats")
done

for bank in $(seq -f "%04g" $(( start+bpj*i+1 )) $(( start+bpj*(i+1)-1 )) ); do
    H1bank="${bankdir}/iir_H1-PYCBC_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
    L1bank="${bankdir}/iir_L1-PYCBC_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
    macroiirbank+=(--iir-bank "H1:${H1bank},L1:${L1bank}")
    macrostatsprefix+=(--cohfar-accumbackground-output-prefix "${jobno}/bank${bank}_stats")
done

macrooutprefix=${jobno}/${jobno}_zerolag
PIPELINE_BIN="$(command -v gstlal_inspiral_postcohspiir_online || true)"
if [[ -z "${PIPELINE_BIN}" ]]; then
    echo "gstlal_inspiral_postcohspiir_online not found in PATH"
    exit 1
fi

base_cmd=(
  "${PIPELINE_BIN}"
  --job-tag "${macrojobtag}"
  --tmp-space _CONDOR_SCRATCH_DIR
  "${macroiirbank[@]}"
  --data-source frames
  --check-time-stamp
  --cuda-postcoh-snglsnr-thresh 4
  --cuda-postcoh-hist-trials 100
  --cuda-postcoh-output-skymap 0
  --finalsink-output-prefix "${macrooutprefix}"
  --finalsink-snapshot-interval 86400
  --cohfar-accumbackground-snapshot-interval 3600
  "${macrostatsprefix[@]}"
  --cohfar-assignfar-input-fname "${macrofarinput}"
  --cohfar-assignfar-silent-time 0
  --cohfar-assignfar-refresh-interval 3600
  --finalsink-fapupdater-interval 1800
  --finalsink-cluster-window 1
  --finalsink-fapupdater-collect-walltime 604800,86400,7200
  --finalsink-far-factor 94
  --finalsink-gracedb-far-thresh 0.0001
  --finalsink-need-online-perform 1
  --finalsink-gracedb-group Test
  --finalsink-gracedb-search MDC
  --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/
  --code-version spiir-O4-EW-development
  --track-psd
  --psd-fft-length 4
  --finalsink-fapupdater-output-fname "${macrolocfapoutput}"
  --frame-cache "${cache}"
  --gpu-acc on
  --ht-gate-threshold 15.0
  --gps-start-time "${macrostart}"
  --gps-end-time "${macroend}"
  --state-channel-name H1=GDS-CALIB_STATE_VECTOR_AR
  --state-channel-name L1=GDS-CALIB_STATE_VECTOR_AR
  --state-vector-on-bits H1=3
  --state-vector-on-bits L1=3
  --state-vector-off-bits H1=0
  --state-vector-off-bits L1=0
  --channel-name H1=GDS-CALIB_STRAIN_AR
  --channel-name L1=GDS-CALIB_STRAIN_AR
  --cuda-postcoh-detrsp-fname "${map}"
  --cuda-postcoh-detrsp-refresh-interval 86400
  --finalsink-singlefar-veto-thresh 0.5
)

export PIPELINE_MODE
# The GStreamer graph is launched once.  In single mode spiirparts.py adds a
# postcoh tee, so the raw single-detector dump branch and the coherent cohfar
# branch run together inside this one graph.
"${base_cmd[@]}"

# Everything below runs after the GStreamer graph exits.  These commands only
# convert already-produced outputs into common (rho, FAR) CSV files.
single_far_csv="${jobno}/${jobno}_single_detector_far.csv"
single_far_background_input="${SINGLE_FAR_BACKGROUND_INPUT:-}"
single_far_background_output="${SINGLE_FAR_BACKGROUND_OUTPUT:-${jobno}/${jobno}_single_far_llr_background.json}"
single_far_calibrate="${SINGLE_FAR_CALIBRATE:-}"
if [[ -z "${single_far_calibrate}" ]]; then
  if [[ -z "${single_far_background_input}" ]]; then
    single_far_calibrate=1
  else
    single_far_calibrate=0
  fi
fi
coherent_far_csv="${jobno}/${jobno}_coherent_far_plane.csv"
combined_far_csv="${jobno}/${jobno}_combined_far_plane.csv"
coherent_postcoh_glob="${macrooutprefix}*.xml.gz"
single_detector_py="${SCRIPT_DIR}/gstlal-spiir/python/pipemodules/single_detector_far.py"
combine_background_py="${SCRIPT_DIR}/gstlal-spiir/python/pipemodules/combine_background_far.py"

python "${combine_background_py}" \
  --multi-postcoh-glob "${coherent_postcoh_glob}" \
  --output "${coherent_far_csv}"

if [[ "${PIPELINE_MODE}" == "single" ]]; then
  single_detector_cmd=(
    python "${single_detector_py}" single
    --postcoh-glob "${jobno}/sdpostcoh*.xml.gz" \
    --output "${single_far_csv}" \
    --ifos H1,L1 \
    --min-snr 4 \
    --background-output "${single_far_background_output}" \
    --snr-bins "${SINGLE_FAR_SNR_BINS:-4,6,8,12,inf}"
  )
  if [[ -n "${single_far_background_input}" ]]; then
    single_detector_cmd+=(--background-input "${single_far_background_input}")
  fi
  if [[ "${single_far_calibrate}" == "1" ]]; then
    single_detector_cmd+=(--calibrate-noise-dof)
  fi
  "${single_detector_cmd[@]}"

  python "${combine_background_py}" \
    --single-csv "${single_far_csv}" \
    --multi-csv "${coherent_far_csv}" \
    --output "${combined_far_csv}"
fi
