#!/usr/bin/bash

# This used to be the main bash script for this task, but now we use
# 'run_skymap' (which is more generic).

# For backwards compatibility this script still exists and does the relevant
# skymap comparison

bash $(dirname "$0")/run_skymap.sh gw170817_L1_1187008882_445312500_3_806 1187008882

