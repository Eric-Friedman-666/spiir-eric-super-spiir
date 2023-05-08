#!/bin/bash
#
#SBATCH --ntasks=1
#SBATCH --time=160:00:00
#SBATCH --mem=16g
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --array=0
#SBATCH --requeue

set -o errexit
set -o pipefail
# set -o nounset

if ! command -v gstlal_inspiral_postcohspiir_online &> /dev/null
then
    echo "gstlal_inspiral_postcohspiir_online could not be found, have you set up your environment?"
    echo "Run this before running this script:"
    echo "module purge"
    echo "source /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh"
    echo "load_spiir <SPIIR_ENV>"
    exit
fi

while getopts a:s:d:j:w:l:t:r:u:x:c: flag
do
    case "${flag}" in
        a) ARTIFACTS_DIR=$OPTARG;;
        s) START=$OPTARG;;
        d) DURATION=$OPTARG;;
        j) JOB_ID=$OPTARG;;
        w) WRAPPER=$OPTARG;;
        l) SLURM=$OPTARG;;
        t) POSTCOH_IFOS=$OPTARG;;
		r) RUN_ID=$OPTARG;;
		u) RUN_DIR=$OPTARG;;
		x) USE_TMUX=$OPTARG;;
		c) SCRIPT_DIR=$OPTARG;;
    esac
done

RUN_DIR="${RUN_DIR:-${ARTIFACTS_DIR}/${JOB_ID}/${RUN_ID}}"

echo "";
echo "Running pipeline.";
echo "	Artifacts dir (a): $ARTIFACTS_DIR";
echo "	Start time (s): $START";
echo "	Duration (d): $DURATION";
END=$(($START+$DURATION))
echo "	End: $END";
echo "	Job ID (j): $JOB_ID";
echo "	Wrapper commands (w): $WRAPPER";
echo "	Using Slurm? (l): $SLURM";
echo "	Postcoh IFOs (t): $POSTCOH_IFOS";
echo "	Run ID (r): $RUN_ID";
echo "	Run dir (u): $RUN_DIR";
echo "  TMUX the run output (x): $USE_TMUX";
echo "	Script dir (c): $SCRIPT_DIR";
echo "";

cmd () {
	bank_start=3 #initial id of bank files
	bank_count=1 #number of bank files

	NODE_ID=`seq -f "%03g" ${i} ${i}`

	cache=${ARTIFACTS_DIR}/fake_frames.cache
	psd=${ARTIFACTS_DIR}/reference_PSD.xml.gz

	map=${ARTIFACTS_DIR}/detrsp_map.xml
	macrofarinput=${RUN_DIR}/${NODE_ID}/marginalized_stats_2w.xml.gz,${RUN_DIR}/${NODE_ID}/marginalized_stats_1d.xml.gz,${RUN_DIR}/${NODE_ID}/marginalized_stats_2h.xml.gz

	macrolocfapoutput=${RUN_DIR}/${NODE_ID}/marginalized_stats_2w.xml.gz,${RUN_DIR}/${NODE_ID}/marginalized_stats_1d.xml.gz,${RUN_DIR}/${NODE_ID}/marginalized_stats_2h.xml.gz
	macrojobtag=${RUN_DIR}/${NODE_ID}

	# Banks are the only artifact which are not yet generated and so a single set of banks are available for every job in the top-most folder.
	bankdir="${ARTIFACTS_DIR}/.."

	mkdir -p ${RUN_DIR}/${NODE_ID}

	for bank in $(seq -f "%04g" $(( ${bank_start}+${bank_count}*${i} )) $(( ${bank_start}+${bank_count}*($i+1)-1 )) ); do
		if [[ "$RUN_ID" == *"H"* ]]; then
			macroiirbank="H1:${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$RUN_ID" == *"L"* ]]; then
			if [[ -n $macroiirbank ]]; then macroiirbank="${macroiirbank},"; fi
			macroiirbank="${macroiirbank}L1:${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$RUN_ID" == *"V"* ]]; then
			if [[ -n $macroiirbank ]]; then macroiirbank="${macroiirbank},"; fi
			macroiirbank="${macroiirbank}V1:${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		if [[ "$RUN_ID" == *"K"* ]]; then
			if [[ -n $macroiirbank ]]; then macroiirbank="${macroiirbank},"; fi
			macroiirbank="${macroiirbank}K1:${bankdir}/iir_K1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz"
		fi
		macroiirbanks="$macroiirbanks --iir-bank ${macroiirbank}"
		unset macroiirbank
	done

	for bank in $(seq -f "%04g" $(( ${bank_start}+${bank_count}*${i} )) $(( ${bank_start}+${bank_count}*($i+1)-1 )) ); do
		macrostatsprefix="${RUN_DIR}/${NODE_ID}/bank${bank}_stats"
		macrostatsprefixes="${macrostatsprefixes} --cohfar-accumbackground-output-prefix ${macrostatsprefix}"
	done

	macrooutprefix=${RUN_DIR}/${NODE_ID}/zerolag

	if [[ "$RUN_ID" == *"H"* ]]; then
		channels="--channel-name H1=FAKE_INJECTIONS"
	fi
	if [[ "$RUN_ID" == *"L"* ]]; then
		channels="${channels} --channel-name L1=FAKE_INJECTIONS"
	fi
	if [[ "$RUN_ID" == *"V"* ]]; then
		channels="${channels} --channel-name V1=FAKE_INJECTIONS"
	fi
	if [[ "$RUN_ID" == *"K"* ]]; then
		channels="${channels} --channel-name K1=FAKE_INJECTIONS"
	fi

	export GSTLAL_LL_JOB=${macrojobtag}

	# These values were chosen without any scientific basis, and are purely used for functionality testing.
	CMD="$WRAPPER python2 $(type -p gstlal_inspiral_postcohspiir_online) \
		--job-tag ${macrojobtag} \
		--tmp-space _CONDOR_SCRATCH_ARTIFACTS_DIR \
		${macroiirbanks} \
		--data-source frames \
		${channels} \
		--gpu-acc on \
		--ht-gate-threshold 15.0 \
		--cuda-postcoh-snglsnr-thresh 4 \
		--cuda-postcoh-hist-trials 100 \
		--cuda-postcoh-detrsp-fname ${map} \
		--cuda-postcoh-output-skymap 100 \
		--cuda-postcoh-detrsp-refresh-interval 3600 \
		--cuda-postcoh-detrsp-refresh-offset 360 \
		--check-time-stamp \
		--finalsink-output-prefix ${macrooutprefix} \
		--finalsink-snapshot-interval 43200 \
		--cohfar-accumbackground-snapshot-interval 3600 \
		${macrostatsprefixes} \
		--cohfar-assignfar-input-fname ${macrofarinput} \
		--cohfar-assignfar-silent-time 0 \
		--cohfar-assignfar-refresh-interval 1800 \
		--cohfar-assignfar-refresh-offset 180 \
		--finalsink-fapupdater-interval 1800 \
		--finalsink-cluster-window 1 \
		--finalsink-fapupdater-collect-walltime 604800,86400,7200 \
		--finalsink-far-factor ${n} \
		--finalsink-gracedb-far-thresh 0.00014 \
		--finalsink-need-online-perform 0 \
		--finalsink-gracedb-group Test \
		--finalsink-gracedb-search AllSky \
		--finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/ \
		--code-version ${RUN_DIR} \
		--frame-cache ${cache} \
		--gps-start-time ${START} \
		--gps-end-time ${END} \
		--finalsink-singlefar-veto-thresh 0.5 \
		--finalsink-superevent-thresh 0.0001 \
		--reference-psd ${psd} \
		--finalsink-gracedb-upload-attempts 0 \
		--psd-fft-length 32"
		# For bypass: --coherent-search-ifos ${POSTCOH_IFOS}"
	echo $CMD
}

cd ${RUN_DIR}

if [ $SLURM -eq 1 ]
then
	i=$SLURM_ARRAY_TASK_ID
	n=$SLURM_ARRAY_TASK_COUNT
	cmd
	srun $CMD
else
	first=$(grep -m 1 "SBATCH --array" "$0" | sed 's/.*=\([0-9]\).*$/\1/')
	last=$(grep -m 1 "SBATCH --array" "$0" | sed 's/.*\([0-9]\).*$/\1/')
	nodes=$(seq $first $last)
	for TID in $nodes; do
		i=$TID
		n=${#nodes[@]}
		cmd
		echo "STD output at ${RUN_DIR}/logs/pipe_${TID}.out.txt" > ${RUN_DIR}/logs/pipe_${TID}.out.txt
		echo "ERR output at ${RUN_DIR}/logs/pipe_${TID}.err.txt" > ${RUN_DIR}/logs/pipe_${TID}.err.txt
		eval $CMD 1>> ${RUN_DIR}/logs/pipe_${TID}.out.txt 2>> ${RUN_DIR}/logs/pipe_${TID}.err.txt &
		pid=$!
		if [ $USE_TMUX -eq 1 ]; then
			SESSION=tmux-${RUN_ID}
			tmux -q new-session -d -s "$SESSION"
			tmux -q split-window -t "$SESSION" "tail --pid=$pid -F ${RUN_DIR}/logs/pipe_${TID}.out.txt"
			tmux -q select-layout -t "$SESSION" tiled
			tmux -q split-window -t "$SESSION" "tail --pid=$pid -F ${RUN_DIR}/logs/pipe_${TID}.err.txt"
			tmux -q select-layout -t "$SESSION" tiled
			tmux -q kill-pane -t "${SESSION}.0"
			tmux -q select-pane -t "${SESSION}.0"
			tmux -q select-layout -t "$SESSION" "$LAYOUT"
			tmux -q set-window-option -t "$SESSION" synchronize-panes on
			tmux -q attach -t "$SESSION" >/dev/null 2>&1
		else
			tail --pid=$pid -F ${RUN_DIR}/logs/pipe_${TID}.out.txt ${RUN_DIR}/logs/pipe_${TID}.err.txt
		fi
		wait "$pid"
	done
fi
