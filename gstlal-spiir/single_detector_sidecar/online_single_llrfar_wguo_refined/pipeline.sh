SCRIPT_DIR=${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
export SCRIPT_DIR
source "${SCRIPT_DIR}/run_config.sh"
export PIPELINE_MODE=${PIPELINE_MODE:-single}

i=$SLURM_ARRAY_TASK_ID

jobno=`seq -f "%03g" ${i} ${i}`


#bankdir=/fred/oz016/manoj/O4_Banks/O4_New/filt/gstlal_iir_bank_b0optmin97pc_O4a_0
bankdir=${BANK_DIR}
#bankdir=/fred/oz016/sunil/s240422ed/py3-bank

#injs=/fred/oz016/sunil/run_utils/injection_files/bbh/SG_RM-1241710700-1242315500.xml

macrostart=${DATA_START_TIME}
macroend=${DATA_END_TIME}

noninj_stats_loc=${NONINJ_STATS_LOC}
inj_stats_loc=`pwd`

map=${DETRSP_MAP}

cache=${FRAME_CACHE_FILE}

macronodename=postcohspiir

start=${START_BANK}
bpj=${BANKS_PER_GROUP}
zerolag_snapshot_interval=${ZEROLAG_SNAPSHOT_INTERVAL_SECONDS}
nretry=0

stats_suffix_for_window() {
    case "$1" in
        604800|2w) printf '2w' ;;
        86400|1d) printf '1d' ;;
        7200|2h) printf '2h' ;;
        *) printf '%s' "$1" ;;
    esac
}

build_stats_file_list() {
    stats_base=$1
    stats_job=$2
    stats_windows=$3
    stats_list=""
    old_ifs=${IFS}
    IFS=,
    set -- ${stats_windows}
    IFS=${old_ifs}
    for stats_window in "$@"; do
        stats_suffix=$(stats_suffix_for_window "${stats_window}")
        stats_path=${stats_base}/${stats_job}/${stats_job}_marginalized_stats_${stats_suffix}.xml.gz
        stats_list=${stats_list:+${stats_list},}${stats_path}
    done
    printf '%s' "${stats_list}"
}

macrofarinput=$(build_stats_file_list "${noninj_stats_loc}" "${jobno}" "${BACKGROUND_STATS_WINDOWS}")
macrolocfapoutput=$(build_stats_file_list "${inj_stats_loc}" "${jobno}" "${BACKGROUND_STATS_WINDOWS}")

macrojobtag=${jobno}

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i} )) $(( ${start}+${bpj}*($i) )) ); do
    H1bank=${bankdir}/iir_H1-PYCBC_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	L1bank=${bankdir}/iir_L1-PYCBC_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	macroiirbank=H1:${H1bank},L1:${L1bank}
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i}+1 )) $(( ${start}+${bpj}*($i+1)-1 )) ); do
    H1bank=${bankdir}/iir_H1-PYCBC_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	L1bank=${bankdir}/iir_L1-PYCBC_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	macroiirbank="${macroiirbank} --iir-bank H1:${H1bank},L1:${L1bank}"
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i} )) $(( ${start}+${bpj}*($i) )) ); do
	macrostatsprefix=${jobno}/bank${bank}_stats
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i}+1 )) $(( ${start}+${bpj}*($i+1)-1 )) ); do
	macrostatsprefix="${macrostatsprefix} --cohfar-accumbackground-output-prefix ${jobno}/bank${bank}_stats"
done

macrooutprefix=${jobno}/${jobno}_zerolag
single_trigger_stream_arg=""
if [ "${SINGLE_TRIGGER_STREAM_ENABLE:-1}" = "1" ] || [ "${SINGLE_INPUT_KIND:-singlecsv}" = "singlecsv" ] || [ "${SINGLE_INPUT_KIND:-singlecsv}" = "singletriggers" ]; then
	single_trigger_stream_file=${SINGLE_TRIGGER_STREAM_FILE:-${jobno}/${jobno}_single_triggers.csv}
	single_trigger_stream_arg=" --finalsink-single-trigger-stream ${single_trigger_stream_file}"
fi

if [ -n "${SPIIR_RUNTIME_PYTHONPATH:-}" ]; then
	export PYTHONPATH="${SPIIR_RUNTIME_PYTHONPATH}:${PYTHONPATH:-}"
fi
gstlal_online=${SPIIR_ONLINE_BIN:-$(which gstlal_inspiral_postcohspiir_online)}

CMD="${gstlal_online} --state-channel-name H1=${H1_STATE_CHANNEL_NAME} --state-channel-name L1=${L1_STATE_CHANNEL_NAME} --state-vector-on-bits H1=3 --state-vector-on-bits L1=3 --state-vector-off-bits H1=0 --state-vector-off-bits L1=0 --job-tag ${macrojobtag} --tmp-space _CONDOR_SCRATCH_DIR --iir-bank ${macroiirbank} --data-source frames --channel-name H1=${H1_STRAIN_CHANNEL_NAME} --channel-name L1=${L1_STRAIN_CHANNEL_NAME} --gpu-acc on  --ht-gate-threshold 15.0 --cuda-postcoh-snglsnr-thresh 4 --cuda-postcoh-hist-trials 100 --cuda-postcoh-detrsp-fname ${map} --cuda-postcoh-output-skymap 100 --check-time-stamp --finalsink-output-prefix ${macrooutprefix} --finalsink-snapshot-interval ${zerolag_snapshot_interval} --cohfar-accumbackground-snapshot-interval ${COHFAR_ACCUMBACKGROUND_SNAPSHOT_INTERVAL_SECONDS} --cohfar-accumbackground-output-prefix ${macrostatsprefix} --cohfar-assignfar-input-fname ${macrofarinput} --cohfar-assignfar-silent-time 0 --cohfar-assignfar-refresh-interval ${COHFAR_ASSIGNFAR_REFRESH_INTERVAL_SECONDS} --finalsink-fapupdater-interval ${FINALSINK_FAPUPDATER_INTERVAL_SECONDS} --finalsink-cluster-window 1 --finalsink-fapupdater-collect-walltime ${BACKGROUND_COLLECT_WALLTIME} --finalsink-far-factor 94 --finalsink-gracedb-far-thresh 0.0001 --finalsink-need-online-perform 1 --finalsink-gracedb-group Test --finalsink-gracedb-search MDC --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/ --cuda-postcoh-detrsp-refresh-interval 86400 --code-version spiir-O4-EW-development --frame-cache ${cache} --gps-start-time ${macrostart} --gps-end-time ${macroend} --finalsink-singlefar-veto-thresh 0.5 --track-psd ${psd} --psd-fft-length 4 --finalsink-fapupdater-output-fname ${macrolocfapoutput}${single_trigger_stream_arg}"

$CMD
