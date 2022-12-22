#!/bin/bash
#
#SBATCH --ntasks=1
#SBATCH --time=0:30:00
#SBATCH --mem-per-cpu=8g
#SBATCH --cpus-per-task=4
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

export ARTIFACTS_DIR=${ARTIFACTS_DIR:-artifacts}
export START=${START:-1187006000}
export DURATION=${DURATION:-300}
ARTIFACTS_DIR=$(realpath ${ARTIFACTS_DIR})
ARTIFACTS_DIR="$ARTIFACTS_DIR/HLVK-${START}-${DURATION}"
export SLURM=${SLURM:-0}
export SCRIPT_DIR=${SCRIPT_DIR:-$(dirname $( realpath "$0"  ) )}

while getopts a:s:d:l:c: flag
do
    case "${flag}" in
        a) ARTIFACTS_DIR=$OPTARG;;
        s) START=$OPTARG;;
        d) DURATION=$OPTARG;;
        l) SLURM=$OPTARG;;
        c) SCRIPT_DIR=$OPTARG;;
    esac
done

echo ""
echo "Generating pipeline artifacts."
echo "  Artifacts dir (a): $ARTIFACTS_DIR";
echo "  Start time (s): $START";
echo "  Duration (d): $DURATION";
echo "  Using Slurm? (l): $SLURM";
END=$(($START+$DURATION))
echo "  End: $END";
echo "	Script dir (c): $SCRIPT_DIR";
echo ""

mkdir -p $ARTIFACTS_DIR
# # generate spiir bank
# gstlal_iir_bank --reference-psd ${PSD} --template-bank /fred/oz016/manoj/test/new_split/${SBNK}/${IFO}_split_bank/${IFO}-GSTLAL_SPLIT_BANK_${BNK}-0-0.xml.gz --flow 15.0 --waveform-domain FD --padding 1.3 --instrument ${IFO} --output gstlal_iir_bank_${SUF}/iir_${IFO}-GSTLAL_SPLIT_BANK_${BNK}-a1-0-0.xml.gz --autocorrelation-length 351 --sampleRate 2048.0 -v --epsilon-options "'{"epsilon_start":1.0,"nround_max":25,"initial_overlap_min":0.95,"b0_optimized_overlap_min":0.'"${BOPTPERC}"',"epsilon_factor":1.2,"filters_max":350}'" --optimizer-options "'{"verbose":true,"passes":16,"indv":true,"hessian":true}'" --approximant ${APPROX} --negative-latency ${NEGLAT}"

# create injection xml
if test -f ${ARTIFACTS_DIR}/fake_inj.xml; then
    echo "fake_inj.xml already generated."
else
    # These values were chosen without any scientific basis, and are purely used for functionality testing. time-step is set for an injection every 80 seconds.
    CMD="lalapps_inspinj --m-distr totalMass --min-mass1 1 --max-mass1 3 --min-mass2 1 --max-mass2 3 --min-mtotal 2 --max-mtotal 6 --gps-start-time ${START} --gps-end-time ${END} --enable-spin --min-spin1 0 --max-spin1 0.4 --min-spin2 0 --max-spin2 0.4 --waveform SpinTaylorT4threePointFivePN --f-lower 20 --i-distr uniform --l-distr random --t-distr uniform --time-step 80 --taper-injection start --seed 1 --output ${ARTIFACTS_DIR}/fake_inj.xml --d-distr uniform --min-distance 5000 --max-distance 20000 --verbose"
    echo $CMD
    $CMD
fi
CMD="ligolw_print -t sim_inspiral -c h_end_time -c mass1 -c mass2 -c mchirp -c eta -c spin1z -c spin2z -c eff_dist_h -c alpha4 -c longitude -c latitude ${ARTIFACTS_DIR}/fake_inj.xml"
echo $CMD
$CMD

SCMD="sbatch --export=ALL --job-name=${START}-${DURATION}-generate --parsable --output=${ARTIFACTS_DIR}/pipe-gen.out.txt --error=${ARTIFACTS_DIR}/pipe-gen.err.txt --open-mode=append"

# create fake frames from injection xml
if [ -d "$ARTIFACTS_DIR/fake_frames_inj" ]; then
    echo "fake frames already generated."
else
    mkdir $ARTIFACTS_DIR/fake_frames_inj
    if [ $SLURM == 1 ]; then
        # Partition frame generation across available slurm nodes.
        DURATION_PER_JOB=2000
        SLURM_JOBS="--dependency=afterok"
    else
        # Partition frame generation across all available cores.
        NUM_IFOS=4 #HLVK
        CORES=$(grep -c ^processor /proc/cpuinfo)
        CORES_PER_IFO=$(($CORES/$NUM_IFOS))
        if [ $CORES_PER_IFO <= 1 ]; then 
            DURATION_PER_JOB=$DURATION
        else
            DURATION_PER_JOB=$(( $DURATION / $CORES_PER_IFO ))
        fi
    fi
    JOB_START=$START
    JOB_END=$(($START+$DURATION_PER_JOB))
    while true; do
        CMD="$SCRIPT_DIR/generate_fake_frames.sh -a ${ARTIFACTS_DIR} -s ${JOB_START} -e ${JOB_END}"
        if [ $SLURM == 1 ]; then
            echo "$SCMD $CMD"
            SLURM_JOBS="$SLURM_JOBS:$($SCMD $CMD)"
        else
            echo $CMD
            bash $CMD &
        fi
        if [ $JOB_END == $END ]; then break; fi
        JOB_START=$(($JOB_START+$DURATION_PER_JOB))
        JOB_END=$(($JOB_END+$DURATION_PER_JOB))
        if [[ $JOB_END > $END ]]; then JOB_END=$END; fi
    done
fi

pids=( $(jobs -p) )

echo "PIDS: ${pids[*]}"

for pid in "${pids[@]}"; do
    wait "$pid"
    echo "Exit code $?"
done

# append fake frames to cache file
if test -f ${ARTIFACTS_DIR}/fake_frames.cache; then
    echo "frame cache file already generated."
else
    CMD="ls ${ARTIFACTS_DIR}/fake_frames_inj/*/*.gwf | lalapps_path2cache >> ${ARTIFACTS_DIR}/fake_frames.cache"
    if [ $SLURM == 1 ]; then
        echo "$SCMD $SLURM_JOBS --wrap=\"$CMD\""
        SLURM_JOB="--dependency=afterok:$($SCMD $SLURM_JOBS --wrap="$CMD")"
    else
        echo $CMD
        $CMD
    fi
fi

SBIG="--mem=32g --time=10:00:00 --cpus-per-task=4"

# generate reference_psd
if test -f ${ARTIFACTS_DIR}/reference_PSD.xml.gz; then
    echo "reference psd already generated."
else
    CHANNELS="--channel-name=H1=FAKE_INJECTIONS --channel-name=L1=FAKE_INJECTIONS --channel-name=V1=FAKE_INJECTIONS --channel-name=K1=FAKE_INJECTIONS"
    CMD="gstlal_reference_psd --data-source frames --frame-cache ${ARTIFACTS_DIR}/fake_frames.cache --gps-start-time=${START} --gps-end-time=${END} ${CHANNELS} --write-psd ${ARTIFACTS_DIR}/reference_PSD.xml.gz --verbose --psd-fft-length 16"
    if [ $SLURM == 1 ]; then
        echo "$SCMD $SLURM_JOB $SBIG --wrap=\"$CMD\""
        SLURM_JOB="--dependency=afterok:$($SCMD $SLURM_JOB $SBIG --wrap="$CMD")"
    else
        echo $CMD
        $CMD
    fi
fi

# generate detrsp map
if test -f ${ARTIFACTS_DIR}/detrsp_map.xml; then
    echo "detrsp already generated."
else
    HORIZONS="--ifo-horizons H1:111,L1:212,V1:56,K1:56"
    CMD="gstlal_postcoh_gen_detrsp_map ${HORIZONS} --chealpix-order 5 --output-coh-coeff ${ARTIFACTS_DIR}/detrsp_map.xml --output-prob-coeff ${ARTIFACTS_DIR}/prob_map.xml --gps-time ${START}"
    if [ $SLURM == 1 ]; then
        echo "$SCMD $SLURM_JOB $SBIG --wrap=\"$CMD\" --wait"
        $SCMD $SLURM_JOB $SBIG --wrap="$CMD" --wait
    else
        echo $CMD
        $CMD
    fi
fi

# # generate fits from skymap binary
# gstlal_postcoh_skymap2fits --output-cohsnr cohsnr_skymap.fits.gz --output-prob spiir.fits.gz --cuda-postcoh-detrsp-fname H1L1V1K1_prob_map.xml --event-id 0 --event-time 1187006432 H1L1_skymap/H1_1187006432_89355469_3_29
