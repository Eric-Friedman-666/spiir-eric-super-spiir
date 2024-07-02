#!/bin/bash
#
#SBATCH --job-name=bbh_inj_rework
#SBATCH --ntasks=1
#SBATCH --time=100:00:00
#SBATCH --mem=12g
#SBATCH --cpus-per-task=2
#SBATCH --gres=gpu:1
#SBATCH --array=13,14,19
#SBATCH --requeue

SUF=$1
SUB=$2

cmd () {

jobno=`seq -f "%03g" ${i} ${i}`

bankdir=/fred/oz996/tdavies/spiir_project/sources/resources/banks/

injs=/fred/oz996/tdavies/spiir_project/sources/resources/injs/gstlal_bbh_astrophysical_imf-1186624850-1187312718.xml.gz

macrostart=1186642720
# Full test: 1187312718
macroend=1186650720 #$(($macrostart + 8000))
noninj_stats_loc=/fred/oz996/tdavies/spiir_project/sources/resources/noninjs # Generated using spiir-O4-EW-Development as of b23a5be5f8f85d62c0b0cdba30fb64560384c853
inj_stats_loc=`pwd`

map=H1L1_detrsp_map.xml

cache=/fred/oz016/data_cleaned/frame.cache.cleaned
psd=/fred/oz996/tdavies/spiir_project/sources/resources/psd/gstlal_H1L1V1-REFERENCE_PSD-1186624818-687900.xml.gz
segs=/fred/oz996/tdavies/spiir_project/sources/resources/segs/segments.xml.gz
vetoes=/fred/oz996/tdavies/spiir_project/sources/resources/vetoes/vetoes.xml.gz

macronodename=postcohspiir

start=333
bpj=4
nretry=0



macrofarinput=${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz

macrolocfapoutput=${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz

macrojobtag=${jobno}

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i} )) $(( ${start}+${bpj}*($i) )) ); do
    H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        macroiirbank=H1:${H1bank},L1:${L1bank}
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i}+1 )) $(( ${start}+${bpj}*($i+1)-1 )) ); do
    H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        macroiirbank="${macroiirbank} --iir-bank H1:${H1bank},L1:${L1bank}"
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i} )) $(( ${start}+${bpj}*($i) )) ); do
	macrostatsprefix=${jobno}/bank${bank}_stats
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i}+1 )) $(( ${start}+${bpj}*($i+1)-1 )) ); do
	macrostatsprefix="${macrostatsprefix} --cohfar-accumbackground-output-prefix ${jobno}/bank${bank}_stats"
done

macrooutprefix=${jobno}/${jobno}_zerolag


CMD="$(which gstlal_inspiral_postcohspiir_online) \
        --job-tag ${macrojobtag} \
        --iir-bank ${macroiirbank} \
        --data-source frames \
        --frame-cache ${cache} \
        --gps-start-time ${macrostart} \
        --gps-end-time ${macroend} \
        --reference-psd ${psd} \
        --channel-name H1=DCH-CLEAN_STRAIN_C02 \
        --channel-name L1=DCH-CLEAN_STRAIN_C02 \
        --cohfar-accumbackground-output-prefix ${macrostatsprefix} \
        --cohfar-accumbackground-snapshot-interval 3600 \
        --cohfar-assignfar-silent-time 0 \
        --cohfar-assignfar-input-fname ${macrofarinput} \
        --cohfar-assignfar-refresh-interval 3600 \
        --gpu-acc on \
        --ht-gate-threshold 15.0 \
        --cuda-postcoh-snglsnr-thresh 4 \
        --cuda-postcoh-hist-trials 100 \
        --cuda-postcoh-detrsp-fname ${map} \
        --cuda-postcoh-detrsp-refresh-interval 86400 \
        --cuda-postcoh-output-skymap 100 \
        --check-time-stamp \
        --finalsink-fapupdater-collect-walltime 604800,86400,7200 \
        --finalsink-fapupdater-interval 1800 \
        --finalsink-output-prefix ${macrooutprefix} \
        --finalsink-snapshot-interval 86400 \
        --finalsink-cluster-window 1 \
        --finalsink-far-factor 2 \
        --finalsink-singlefar-veto-thresh 0.5 \
        --finalsink-superevent-thresh 0.0001 \
        --finalsink-need-online-perform 0 \
        --finalsink-gracedb-far-threshold 0.0001 \
        --code-version spiir-review-O3-EW \
        --psd-fft-length 32 \
        --injection-file ${injs} \
        --frame-segments-file ${segs} \
        --frame-segments-name datasegments \
        --veto-segments-file ${vetoes} \
        --finalsink-gracedb-group Test \
        --finalsink-gracedb-search MDC \
        --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/ \
        --finalsink-fapupdater-output-fname ${macrolocfapoutput} \
        "
}

if [ $SUB -eq 1 ]
then
  i=$SLURM_ARRAY_TASK_ID
  cmd
  srun $CMD
else
  echo "Loop"
  arrays=$(grep -m 1 "SBATCH --array" pipeline.sh | sed 's/.*=//' | sed 's/-/\ /')
  for TID in ${arrays//,/ }; do
    i=$TID
    echo "Run: "$i
    cmd
    echo $CMD
    $CMD > logs_${SUF}/pipe_${TID}.out 2>logs_${SUF}/pipe_${TID}.err
    #disown
  done
fi
