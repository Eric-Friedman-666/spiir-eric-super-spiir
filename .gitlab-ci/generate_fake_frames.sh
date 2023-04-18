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

while getopts a:s:e: flag
do
    case "${flag}" in
        a) ARTIFACTS_DIR=$OPTARG;;
        s) START=$OPTARG;;
        e) END=$OPTARG;;
    esac
done

START=${START:-1187006000}
END=${END:-1187006300}
DURATION=$(($START-$END))
ARTIFACTS_DIR=${ARTIFACTS_DIR:-artifacts/HLVK-${START}-${DURATION}}
ARTIFACTS_DIR=$(realpath ${ARTIFACTS_DIR})

echo ""
echo "Generating fake frames."
echo "  Artifacts dir (a): $ARTIFACTS_DIR";
echo "  Start time (s): $START";
echo "  End (e): $END";
echo ""

CMD="gstlal_fake_frames --data-source AdvLIGO --output-path ${ARTIFACTS_DIR}/fake_frames_inj --gps-start-time ${START} --frame-type H1_INJECTIONS --gps-end-time ${END} --frame-duration 16 --frames-per-file 125 --verbose --channel-name=H1=FAKE_INJECTIONS --injections ${ARTIFACTS_DIR}/fake_inj.xml"
echo $CMD
$CMD &

CMD="gstlal_fake_frames --data-source AdvLIGO --output-path ${ARTIFACTS_DIR}/fake_frames_inj --gps-start-time ${START} --frame-type L1_INJECTIONS --gps-end-time ${END} --frame-duration 16 --frames-per-file 125 --verbose --channel-name=L1=FAKE_INJECTIONS --injections ${ARTIFACTS_DIR}/fake_inj.xml"
echo $CMD
$CMD &

CMD="gstlal_fake_frames --data-source AdvVirgo --output-path ${ARTIFACTS_DIR}/fake_frames_inj --gps-start-time ${START} --frame-type V1_INJECTIONS --gps-end-time ${END} --frame-duration 16 --frames-per-file 125 --verbose --channel-name=V1=FAKE_INJECTIONS --injections ${ARTIFACTS_DIR}/fake_inj.xml"
echo $CMD
$CMD &

CMD="gstlal_fake_frames --data-source AdvVirgo --output-path ${ARTIFACTS_DIR}/fake_frames_inj --gps-start-time ${START} --frame-type K1_INJECTIONS --gps-end-time ${END} --frame-duration 16 --frames-per-file 125 --verbose --channel-name=K1=FAKE_INJECTIONS --injections ${ARTIFACTS_DIR}/fake_inj.xml"
echo $CMD
$CMD &

pids=( $(jobs -p) )

echo "PIDS: ${pids[*]}"

for pid in "${pids[@]}"; do
    wait "$pid"
    echo "Exit code $?"
done