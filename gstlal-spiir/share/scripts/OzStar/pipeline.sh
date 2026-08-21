
i=$SLURM_ARRAY_TASK_ID

jobno=`seq -f "%03g" ${i} ${i}`


#bankdir=/fred/oz016/manoj/O4_Banks/O4_New/filt/gstlal_iir_bank_b0optmin97pc_O4a_0
bankdir=/fred/oz016/sunil/O3b_py2_banks/FB/
#bankdir=/fred/oz016/sunil/s240422ed/py3-bank

#injs=/fred/oz016/sunil/run_utils/injection_files/bbh/SG_RM-1241710700-1242315500.xml

macrostart=1238787954 # Apr 08 2019 19:45:36 UTC
macroend=1239641219  # Apr 18 2019 16:46:41 UTC

noninj_stats_loc=`pwd`
inj_stats_loc=`pwd`

map=/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS/H1L1V1_1238787954_detrsp_map.xml

cache=/fred/oz016/sunil/run_utils/frames_chache/frame_O3a.cache

macronodename=postcohspiir

start=0
bpj=4
nretry=0

macrofarinput=${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${noninj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz

macrolocfapoutput=${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${inj_stats_loc}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz

macrojobtag=${jobno}

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i} )) $(( ${start}+${bpj}*($i) )) ); do
    H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	V1bank=${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	macroiirbank=H1:${H1bank},L1:${L1bank},V1:${V1bank}
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i}+1 )) $(( ${start}+${bpj}*($i+1)-1 )) ); do
    H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	V1bank=${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
	macroiirbank="${macroiirbank} --iir-bank H1:${H1bank},L1:${L1bank},V1:${V1bank}"
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i} )) $(( ${start}+${bpj}*($i) )) ); do
	macrostatsprefix=${jobno}/bank${bank}_stats
done

for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i}+1 )) $(( ${start}+${bpj}*($i+1)-1 )) ); do
	macrostatsprefix="${macrostatsprefix} --cohfar-accumbackground-output-prefix ${jobno}/bank${bank}_stats"
done

macrooutprefix=${jobno}/${jobno}_zerolag

CMD="$(which gstlal_inspiral_postcohspiir_online) --state-channel-name H1=GDS-CALIB_STATE_VECTOR --state-channel-name L1=GDS-CALIB_STATE_VECTOR --state-channel-name V1=DQ_ANALYSIS_STATE_VECTOR --state-vector-on-bits H1=3 --state-vector-on-bits L1=3 --state-vector-on-bits V1=1027 --state-vector-off-bits H1=0 --state-vector-off-bits L1=0 --state-vector-off-bits V1=0 --job-tag ${macrojobtag} --tmp-space _CONDOR_SCRATCH_DIR --iir-bank ${macroiirbank} --data-source frames --channel-name H1=GDS-CALIB_STRAIN_CLEAN --channel-name L1=GDS-CALIB_STRAIN_CLEAN --channel-name V1=Hrec_hoft_16384Hz --gpu-acc on  --ht-gate-threshold 15.0 --cuda-postcoh-snglsnr-thresh 4 --cuda-postcoh-hist-trials 100 --cuda-postcoh-detrsp-fname ${map} --cuda-postcoh-output-skymap 100 --check-time-stamp --finalsink-output-prefix ${macrooutprefix} --finalsink-snapshot-interval 86400 --cohfar-accumbackground-snapshot-interval 3600 --cohfar-accumbackground-output-prefix ${macrostatsprefix} --cohfar-assignfar-input-fname ${macrofarinput} --cohfar-assignfar-silent-time 0 --cohfar-assignfar-refresh-interval 3600 --finalsink-fapupdater-interval 1800 --finalsink-cluster-window 1 --finalsink-fapupdater-collect-walltime 604800,86400,7200 --finalsink-far-factor 25 --finalsink-gracedb-far-thresh 0.0001 --finalsink-need-online-perform 1 --finalsink-gracedb-group Test --finalsink-gracedb-search MDC --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/ --cuda-postcoh-detrsp-refresh-interval 86400 --code-version spiir-O4-EW-development --frame-cache ${cache} --gps-start-time ${macrostart} --gps-end-time ${macroend} --finalsink-singlefar-veto-thresh 0.5 --track-psd ${psd} --psd-fft-length 4 --finalsink-fapupdater-output-fname ${macrolocfapoutput}"
CMD="${CMD} --single-background-read-dir ${noninj_stats_loc}/${jobno}"
CMD="${CMD} --single-background-write-dir ${inj_stats_loc}/${jobno}"

$CMD