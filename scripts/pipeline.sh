#!/bin/bash
#
#SBATCH --ntasks=1
#SBATCH --time=100:00:00
#SBATCH --mem=16g
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --array=0
#SBATCH --requeue

SUB=$1
START=$2
DURATION=$3
END=$4
DIR=$5
PIPE_ID=$6
PARTIFOS=$7
SUF=$8

cmd () {
	start=3 #initial id of bank files
	bpj=1 #number of bank files

	jobno=`seq -f "%03g" ${i} ${i}`
	jobno="${SUF}_${jobno}"

	macrostart=$START
	macroend=$END

	cache=${DIR}/fake-frame.cache
	psd=${DIR}/H1L1V1K1-REFERENCE_PSD-${START}-${DURATION}.xml.gz
	# segs=/fred/oz016/fiona/chunk20_files/segments.xml.gz
	# vetoes=/fred/oz016/fiona/chunk20_files/vetoes.xml.gz

	map=${DIR}/H1L1V1K1_detrsp_map.xml
	macrofarinput=${DIR}/${PIPE_ID}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${DIR}/${PIPE_ID}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${DIR}/${PIPE_ID}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz

	macrolocfapoutput=${DIR}/${PIPE_ID}/${jobno}/${jobno}_marginalized_stats_2w.xml.gz,${DIR}/${PIPE_ID}/${jobno}/${jobno}_marginalized_stats_1d.xml.gz,${DIR}/${PIPE_ID}/${jobno}/${jobno}_marginalized_stats_2h.xml.gz
	macrojobtag=${DIR}/${PIPE_ID}/${jobno}
	macronodename=postcohspiir
	bankdir="${DIR}/../.."
	#"/fred/oz016/dtang/banks/filt/gstlal_iir_bank_b0optmin97pc_pycbc-test_0"
	#`pwd` 
	#/fred/oz016/chichi/o2bank/N0_newint/gstlal_iir_bank_b0optmin98pc_ER14a_0

	mkdir ${DIR}/${PIPE_ID}/${jobno}

	for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i} )) $(( ${start}+${bpj}*($i) )) ); do
		if [[ "$SUF" == *"H"* ]]; then
			macroiirbank="H1:${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$SUF" == *"L"* ]]; then
			macroiirbank="${macroiirbank},L1:${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$SUF" == *"V"* ]]; then
			macroiirbank="${macroiirbank},V1:${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$SUF" == *"K"* ]]; then
			macroiirbank="${macroiirbank},K1:${bankdir}/iir_K1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
	done

	for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i}+1 )) $(( ${start}+${bpj}*($i+1)-1 )) ); do
		if [[ "$SUF" == *"H"* ]]; then
			tmpmacroiirbank="H1:${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$SUF" == *"L"* ]]; then
			tmpmacroiirbank="${tmpmacroiirbank},L1:${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$SUF" == *"V"* ]]; then
			tmpmacroiirbank="${tmpmacroiirbank},V1:${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$SUF" == *"K"* ]]; then
			tmpmacroiirbank="${tmpmacroiirbank},K1:${bankdir}/iir_K1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		macroiirbank="${macroiirbank} --iir-bank ${tmpmacroiirbank}"
	done

	for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i} )) $(( ${start}+${bpj}*($i) )) ); do
		macrostatsprefix=${DIR}/${PIPE_ID}/${jobno}/bank${bank}_stats
	done

	for bank in $(seq -f "%04g" $(( ${start}+${bpj}*${i}+1 )) $(( ${start}+${bpj}*($i+1)-1 )) ); do
		macrostatsprefix="${macrostatsprefix} --cohfar-accumbackground-output-prefix ${DIR}/${PIPE_ID}/${jobno}/bank${bank}_stats"
	done

	macrooutprefix=${DIR}/${PIPE_ID}/${jobno}/${jobno}_zerolag

	if [[ "$SUF" == *"H"* ]]; then
		channels="--channel-name H1=FAKE_INJECTIONS "
	fi
	if [[ "$SUF" == *"L"* ]]; then
		channels="$channels --channel-name L1=FAKE_INJECTIONS "
	fi
	if [[ "$SUF" == *"V"* ]]; then
		channels="$channels --channel-name V1=FAKE_INJECTIONS "
	fi
	if [[ "$SUF" == *"K"* ]]; then
		channels="$channels --channel-name K1=FAKE_INJECTIONS "
	fi

	CMD="gstlal_inspiral_postcohspiir_online \
		--job-tag ${macrojobtag} \
		--tmp-space _CONDOR_SCRATCH_DIR \
		--iir-bank ${macroiirbank} \
		--data-source frames \
		$channels
		--gpu-acc on \
		--ht-gate-threshold 15.0 \
		--cuda-postcoh-snglsnr-thresh 4 \
		--cuda-postcoh-hist-trials 100 \
		--cuda-postcoh-detrsp-fname ${map} \
		--cuda-postcoh-output-skymap 100 \
		--cuda-postcoh-parti-ifos ${PARTIFOS} \
		--cuda-postcoh-detrsp-refresh-interval 86400 \
		--check-time-stamp \
		--finalsink-output-prefix ${macrooutprefix} \
		--finalsink-snapshot-interval 1800 \
		--cohfar-accumbackground-snapshot-interval 3600 \
		--cohfar-accumbackground-output-prefix ${macrostatsprefix} \
		--cohfar-assignfar-input-fname ${macrofarinput} \
		--cohfar-assignfar-silent-time 0 \
		--cohfar-assignfar-refresh-interval 3600 \
		--finalsink-fapupdater-interval 1800 \
		--finalsink-cluster-window 1 \
		--finalsink-fapupdater-collect-walltime 604800,86400,7200 \
		--finalsink-far-factor 1 \
		--finalsink-gracedb-far-thresh 0.0001 \
		--finalsink-need-online-perform 1 \
		--finalsink-gracedb-group Test \
		--finalsink-gracedb-search MDC \
		--finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/ \
		--code-version bypass_snrseries \
		--frame-cache ${cache} \
		--gps-start-time ${macrostart} \
		--gps-end-time ${macroend} \
		--finalsink-singlefar-veto-thresh 0.5 \
		--finalsink-superevent-thresh 0.0001 \
		--reference-psd ${psd} \
		--psd-fft-length 32"
	# if we use nxydump over the whole run we have TBs of dumped data
	# --nxydump-segment 1187006235:1187006245 \
	# --nxydump-directory ${DIR}/${PIPE_ID}/${jobno}"
	# TODO nxydump freezes if bypass for some reason

	echo $CMD
}

export GST_DEBUG_NO_COLOR=1
export GST_DEBUG=triggerjointer:6,cuda_postcoh:6

if [ $SUB -eq 1 ]
then
	i=$SLURM_ARRAY_TASK_ID
	source /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh
	load_spiir ldavis
	cmd
	srun $CMD
	unload_spiir
	module load python/3.8.5
	source /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/ldavis/venv/bin/activate
	cd /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/ldavis/source
	CMD2="python ./scripts/create_skymaps.py --data_dir ${DIR} --run_name ${PIPE_ID}/${jobno} --psd ${psd} --debug"
	srun $CMD2
else
	for TID in $(seq $(grep -m 1 "SBATCH --array" ./scripts/pipeline.sh | sed 's/.*=\([0-9]\).*$/\1/') $(grep -m 1 "SBATCH --array" ./scripts/pipeline.sh | sed 's/.*\([0-9]\).*$/\1/')); do
		i=$TID
		cmd
		$CMD > ${DIR}/${PIPE_ID}/logs_${SUF}/pipe_${TID}.out 2>${DIR}/${PIPE_ID}/logs_${SUF}/pipe_${TID}.err
		export PATH=$POSTPROCESS
		$POSTPROCESS/python ./scripts/create_skymaps.py --data_dir ${DIR} --run_name ${PIPE_ID}/${jobno} --psd ${psd} --debug
		#disown
	done
fi
