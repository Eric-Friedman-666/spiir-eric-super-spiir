#!/bin/bash
SUB="${1:-0}"
START="${2:-1187006000}"
DURATION="${3:-300}"
END=$(($START+$DURATION))
ARTIFACTS="${4:-$PWD}"
ARTIFACTS=${ARTIFACTS%/}
PIPE_ID="${5:-0}"
DIR="$ARTIFACTS/${START}-${DURATION}"

./scripts/generate_pipeline_artifacts.sh ${START} ${DURATION} ${END} ${DIR}

IFOS_LIST=( "HLVK" "HLVK" "HLVK" "HLV" "HLV" "HL" )
PARTIFOS_LIST=( "H1L1V1K1" "H1L1V1" "H1L1" "H1L1V1" "H1L1" "H1L1" )

for idx in "${!IFOS_LIST[@]}"; do
  IFOS=${IFOS_LIST[$idx]}
  PARTIFOS=${PARTIFOS_LIST[$idx]}
  SUF="${IFOS}_${PARTIFOS}"
  mkdir -p "${DIR}/${PIPE_ID}/logs_${SUF}"
  if [ $SUB -eq 1 ]
  then
    sbatch --output=${DIR}/${PIPE_ID}/logs_${SUF}/pipe_%A_%a.out --error=${DIR}/${PIPE_ID}/logs_${SUF}/pipe_%A_%a.err ./scripts/pipeline.sh ${SUB} ${START} ${DURATION} ${END} ${DIR} ${PIPE_ID} ${PARTIFOS} ${SUF}
  else
    ./scripts/pipeline.sh ${SUB} ${START} ${DURATION} ${END} ${DIR} ${PIPE_ID} ${PARTIFOS} ${SUF}
  fi
done
