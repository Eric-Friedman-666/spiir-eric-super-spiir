#!/usr/bin/bash

set -o errexit
set -o nounset
set -o pipefail

DEP_DIR=$(dirname "$0")/fits_skymap

TEST_NAME="$1"
EVENT_TIME="$2"

if [ ! -e "$DEP_DIR/input/$TEST_NAME" ] ; then
	echo "File $DEP_DIR/input/$TEST_NAME doesn't exist"
	exit 1
fi

if [ -z "${SPIIR_PATH:-}" ] ; then
	echo "You don't currently have SPIIR loaded"
	echo "Please load_spiir first, before running this test"
	exit 1
else
	echo "You have SPIIR loaded from:"
	echo "    $SPIIR_PATH"
	echo "so I'll use that"
	. $SPIIR_PATH/../bash_helper_functions.sh
fi

mkdir -p $DEP_DIR/output

spiir_python2to3

# convert from skymap to fits
gstlal_postcoh_skymap2fits \
	--output-cohsnr $DEP_DIR/output/cohsnr.fits \
	--output-prob $DEP_DIR/output/prob.fits \
	--event-time $EVENT_TIME \
	--cuda-postcoh-detrsp-fname $DEP_DIR/input/H1L1V1_detrsp_map.xml \
	--event-id test \
	$DEP_DIR/input/$TEST_NAME

# make images for cohsnr and prob
ligo-skymap-plot -o $DEP_DIR/output/cohsnr.png --colorbar --colormap viridis $DEP_DIR/output/cohsnr.fits
ligo-skymap-plot -o $DEP_DIR/output/prob.png --colorbar --colormap viridis $DEP_DIR/output/prob.fits
