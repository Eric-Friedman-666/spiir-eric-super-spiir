#!/bin/bash
set -o errexit
set -o pipefail
# set -o nounset

if [[ "${USER}" == "spiir" ]]; then
	echo Error: Must set USER to something other than spiir.
    echo Set USER to accounting_group_user like:
    echo USER=luke.davis SPIIR_NAME=spiir-O4-EW-development bash new-run.sh
    exit 0
fi

if [[ -z "${SPIIR_NAME}" ]]; then
	echo Error: Must set SPIIR_NAME.
    echo Set SPIIR_NAME to point to a spiir folder in $SPIIR_SCRIPTS_DIR like:
    echo SPIIR_NAME=spiir-O4-EW-development bash new-run.sh
    exit 0
fi

export SPIIR_DEP_PATH=${SPIIR_DEP_PATH:=/home/spiir/singularity/spiir-base-py2}
echo Singularity sandbox for spiir dependencies is SPIIR_DEP_PATH=$SPIIR_DEP_PATH
echo 

SPIIR_SCRIPTS_DIR=${SPIIR_SCRIPTS_DIR:=/home/spiir/spiir-scripts/build}
echo Environment installation dir is SPIIR_SCRIPTS_DIR=$SPIIR_SCRIPTS_DIR
echo

export SPIIR_PATH=$SPIIR_SCRIPTS_DIR/$SPIIR_NAME

echo Deploying spiir installed at $SPIIR_PATH/install
pushd $SPIIR_PATH/source
commit=$(git rev-parse --short HEAD)
commit_subject=$(git log -1 --pretty=format:%s HEAD)
echo Commit subject: $commit_subject
regex="Merge branch '(.*)' into .*"
[[ $commit_subject =~ $regex ]] || true
branch_name=$(git rev-parse --abbrev-ref HEAD | sed -E 's/\//__/g')
if [[ -z "${BASH_REMATCH[1]}" ]]; then
    echo "Not a merge commit."
else
    branch_name=$branch_name-$(echo ${BASH_REMATCH[1]} | sed -E 's/\//__/g')
fi
popd

if [[ -n "${TAG}" ]]; then
    branch_name=$TAG-$branch_name
fi

if [[ -z "${RUN_DIR}" ]]; then
    RUN_DIR=run-$branch_name-$(date +%s)-$commit
fi

mkdir -p $RUN_DIR
cp gen_copy/* $RUN_DIR/
pushd $RUN_DIR
bash gen_dag.sh > $RUN_DIR.dag
condor_submit_dag $RUN_DIR.dag
popd
