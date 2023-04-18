#!/bin/bash

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

if ! command -v sbatch &> /dev/null
then
    export SLURM=0
else
    export SLURM=1
fi

while getopts a:s:d:j:w:l:p:g:i:c:x:y flag
do
    case "${flag}" in
        a) ARTIFACTS_DIR=$OPTARG;;
        s) START=$OPTARG;;
        d) DURATION=$OPTARG;;
        j) JOB_ID=$OPTARG;;
        w) WRAPPER=$OPTARG;;
        l) SLURM=$OPTARG;;
        p) PY3=$OPTARG;;
        g) GENERATE=$OPTARG;;
        i) ALL_IFOS=$OPTARG;;
        c) COMPARE=$OPTARG;;
        x) USE_TMUX=$OPTARG;;
        y) YES=1;;
    esac
done


export ARTIFACTS_DIR=${ARTIFACTS_DIR:-artifacts}
export START=${START:-1187006000}
export DURATION=${DURATION:-300}
export JOB_ID=${JOB_ID:-$(git rev-parse --abbrev-ref HEAD | tr '[:upper:]' '[:lower:]' | cut -c -63 | sed -E 's/[^a-z0-9-]+/-/g' | sed -E 's/^-*([a-z0-9-]+[a-z0-9])-*$$/\1/g')-$(date +%s)-$(git rev-parse --short HEAD)}
export WRAPPER=${WRAPPER:-}
export PY3=${PY3:-/fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/ldavis/venv/bin/activate}
export GENERATE=${GENERATE:-1}
export ALL_IFOS=${ALL_IFOS:-0}
export COMPARE=${COMPARE:-$(git log -n 1 --pretty=format:"%h" spiir-O4-EW-development)}
export COMPARE=$(echo $COMPARE | tr '[:upper:]' '[:lower:]' | cut -c -63 | sed -E 's/[^a-z0-9-]+/-/g' | sed -E 's/^-*([a-z0-9-]+[a-z0-9])-*$$/\1/g')
export USE_TMUX=${USE_TMUX:-0}
export YES=${YES:-0}

ARTIFACTS_DIR=$(realpath ${ARTIFACTS_DIR})

echo ""
echo "Parameters for submitting runs:"
echo "  Artifacts dir (a): $ARTIFACTS_DIR";
echo "  Start time (s): $START";
echo "  Duration (d): $DURATION";
END=$(($START+$DURATION))
echo "  End: $END";
echo "  Job ID (j): $JOB_ID";
echo "  Wrapper commands (w): $WRAPPER";
echo "  Using Slurm? (l): $SLURM";
echo "  Python 3 env (p): $PY3";
echo "  Generate inputs (g): $GENERATE";
echo "  Run all IFO combinations (i): $ALL_IFOS"
echo "  Commit/Branch to compare results against (c): $COMPARE"
echo "  TMUX the run output (x): $USE_TMUX"
echo "";

if [ $YES -eq 0 ]; then
  echo "Do you wish to continue (y)?"
  select yn in "Yes" "No"; do
      case $yn in
          Yes ) break;;
          No ) exit;;
      esac
  done
fi

mkdir -p $ARTIFACTS_DIR

if [[ -f "$ARTIFACTS_DIR/iir_H1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz" && \
      -f "$ARTIFACTS_DIR/iir_L1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz" && \
      -f "$ARTIFACTS_DIR/iir_V1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz" && \
      -f "$ARTIFACTS_DIR/iir_K1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz" ]]; then
  echo ""
else
  echo "All of these bank files must be kept in $(realpath $ARTIFACTS_DIR) as they are not yet able to be generated :"
  echo "    iir_H1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz"
  echo "    iir_L1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz"
  echo "    iir_V1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz"
  echo "    iir_K1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz"
  echo "You can copy them from /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/ldavis/source/artifacts"
  exit
fi

ARTIFACTS_DIR="$ARTIFACTS_DIR/HLVK-${START}-${DURATION}"

if test -d $ARTIFACTS_DIR; then GENERATE=0; fi

export SCRIPT_DIR=$(dirname $( realpath "$0"  ) )
export SPIIR_DIR=$(dirname $SCRIPT_DIR)
if [ $GENERATE -eq 1 ]; then
  echo "Generating frame files."
  mkdir -p $ARTIFACTS_DIR
  echo "STD output at ${ARTIFACTS_DIR}/pipe-gen.out.txt" > ${ARTIFACTS_DIR}/pipe-gen.out.txt
	echo "ERR output at ${ARTIFACTS_DIR}/pipe-gen.err.txt" > ${ARTIFACTS_DIR}/pipe-gen.err.txt
  CMD="$SCRIPT_DIR/generate_pipeline_artifacts.sh"
  if [ $SLURM -eq 1 ]; then
    SCMD="sbatch --export=ALL --job-name=${START}-${DURATION}-generate --parsable --output=${ARTIFACTS_DIR}/pipe-gen.out.txt --error=${ARTIFACTS_DIR}/pipe-gen.err.txt --open-mode=append"
    echo $SCMD $CMD
    GENERATE_JOB="--dependency=afterok:$($SCMD $CMD)"
  else
    echo $CMD
    bash $CMD 1>> ${ARTIFACTS_DIR}/pipe-gen.out.txt 2>> ${ARTIFACTS_DIR}/pipe-gen.err.txt &
    pid=$!
    tail --pid=$pid -F ${ARTIFACTS_DIR}/pipe-gen.out.txt ${ARTIFACTS_DIR}/pipe-gen.err.txt
  fi
fi

if [ $ALL_IFOS -eq 1 ]; then
  IFOS_LIST=( "HLVK" "HLV" "HLK" "HVK" "LVK" "HL" "HV" "HK" "LV" "LK" "VK" )
  POSTCOH_IFOS_LIST=( "H1L1V1K1" "H1L1V1" "H1L1K1" "H1V1K1" "L1V1K1" "H1L1" "H1V1" "H1K1" "L1V1" "L1K1" "V1K1" )
  # For Bypass:
  # IFOS_LIST=("HLVK" "HLVK" "HLVK" "HLVK" "HLVK" "HLVK" "HLVK" "HLVK" "HLVK" "HLVK" "HLVK" \
  #             "HLV" "HLV" "HLV" "HLV" \
  #             "HLK" "HLK" "HLK" "HLK" \
  #             "HVK" "HVK" "HVK" "HVK" \
  #             "LVK" "LVK" "LVK" "LVK" \
  #             "HL" "HV" "HK" "LV" "VK")
  # POSTCOH_IFOS_LIST=("H1L1V1K1" "H1L1V1" "H1L1K1" "H1V1K1" "L1V1K1" "H1L1" "H1V1" "H1K1" "L1V1" "L1K1" "V1K1" \
  #             "H1L1V1" "H1L1" "H1V1" "L1V1" \
  #             "H1L1K1" "H1L1" "H1K1" "L1K1" \
  #             "H1V1K1" "H1V1" "H1K1" "V1K1" \
  #             "L1V1K1" "L1V1" "L1K1" "V1K1" \
  #             "H1L1" "H1V1" "H1K1" "L1V1" "V1K1")
else
  # Default is KHV to catch backwards and non-sequential IFO issues.
  IFOS_LIST=( "KHV" )
  POSTCOH_IFOS_LIST=( "K1H1V1" )
  # For Bypass:
  # IFOS_LIST=( "KHV" )
  # POSTCOH_IFOS_LIST=( "H1V1" )
fi

mkdir -p ${ARTIFACTS_DIR}/${JOB_ID}
pushd ${ARTIFACTS_DIR}/${JOB_ID}

for idx in "${!IFOS_LIST[@]}"; do
  export IFOS=${IFOS_LIST[$idx]}
  export POSTCOH_IFOS=${POSTCOH_IFOS_LIST[$idx]}
  export RUN_ID="${IFOS}_${POSTCOH_IFOS}"
  export RUN_DIR="${ARTIFACTS_DIR}/${JOB_ID}/${RUN_ID}"
  mkdir -p "${RUN_DIR}/logs"
  CMD="$SCRIPT_DIR/run_pipeline.sh"
  CMD2="python3 $SCRIPT_DIR/create_bayestar_skymaps.py --data_dir ${ARTIFACTS_DIR} --run_name ${JOB_ID}/${RUN_ID} --debug"
  CONTROL_DIR=$(ls -td ${ARTIFACTS_DIR}/*${COMPARE}*/${RUN_ID} | head -1) || CONTROL_DIR="NONE_FOUND"
  CMD3="python3 $SCRIPT_DIR/compare_runs.py --control ${CONTROL_DIR} --test ${RUN_DIR}"
  CMD4="python3 $SPIIR_DIR/gstlal-spiir/share/spiir/share/scripts/tests/test_zerolags.py ${CONTROL_DIR} ${RUN_DIR} --verbose"
  if [ $SLURM -eq 1 ]; then
    SCMD="sbatch --export=ALL --job-name=${START}-${DURATION}-${JOB_ID}-${RUN_ID} --parsable --output=${RUN_DIR}/logs/pipe-run.out.txt --error=${RUN_DIR}/logs/pipe-run.err.txt --open-mode=append"
    echo $SCMD $GENERATE_JOB $CMD
    RUN_JOB="--dependency=afterok:$($SCMD $GENERATE_JOB $CMD)"
    PY3_ENV="unload_spiir && module load python/3.8.5 && source $PY3 &&"
    echo $SCMD $RUN_JOB --wrap="$PY3_ENV $CMD2"
    $SCMD $RUN_JOB --wrap="$PY3_ENV $CMD2"
    if [ "$CONTROL_DIR" != "NONE_FOUND" ]; then
      echo $SCMD $RUN_JOB --wrap="$PY3_ENV $CMD3"
      $SCMD $RUN_JOB --wrap="$PY3_ENV $CMD3"
      echo $SCMD $RUN_JOB --wrap="$PY3_ENV $CMD4"
      $SCMD $RUN_JOB --wrap="$PY3_ENV $CMD4"
    else
      echo "Commit/Branch ${COMPARE} with run ${RUN_ID} not found in artifacts dir ${ARTIFACTS_DIR}."
    fi
  else
    echo $CMD
    bash $CMD
    unset PYTHONPATH
		echo $CMD2
		$CMD2 1>> ${RUN_DIR}/logs/pipe-run.out.txt 2>> ${RUN_DIR}/logs/pipe-run.err.txt &
    pid=$!
    tail --pid=$pid -F ${RUN_DIR}/logs/pipe-run.out.txt ${RUN_DIR}/logs/pipe-run.err.txt
    if [ "$CONTROL_DIR" != "NONE_FOUND" ]; then
      echo $CMD3
      $CMD3 1>> ${RUN_DIR}/logs/pipe-run.out.txt 2>> ${RUN_DIR}/logs/pipe-run.err.txt &
      pid=$!
      tail --pid=$pid -F ${RUN_DIR}/logs/pipe-run.out.txt ${RUN_DIR}/logs/pipe-run.err.txt
      echo $CMD4
      $CMD4 1>> ${RUN_DIR}/logs/pipe-run.out.txt 2>> ${RUN_DIR}/logs/pipe-run.err.txt &
      pid=$!
      tail --pid=$pid -F ${RUN_DIR}/logs/pipe-run.out.txt ${RUN_DIR}/logs/pipe-run.err.txt
    else
      echo "Commit/Branch ${COMPARE} not found in artifacts dir ${ARTIFACTS_DIR}."
    fi
  fi
done
