#!/bin/bash
######################################################
#  online pipeline parameter initialization
#  please refer to the documentation in the gstlal-spiir
#  package for the explanation of the options of the pipeline
######################################################
#  search type and also gracedb trigger type, highmass or lowmass
#  our lowmass includes only BNS (1-3),
#  highmass includes NSBH and BBH (<100)
#  --finalsink-gracedb-search ${SearchType}
######################################################
# You may also use LowMass, MDC, O2VirgoTest, and AllSky
######################################################
SearchType=AllSky

######################################################
#  use banks with no cut-off (0), or early warning cut-off (5, 10)
#  so the template is cut 5/10 seconds before merger
######################################################

latency=0

######################################################
#  which gracedb group to upload, test (1) or CBC (0)
######################################################

iftest=1

######################################################
#  live streaming data (1) or O2replay data (0)
######################################################

iflive=0
######################################################
# raw data ?
######################################################

ifraw=0
if ((${iflive} == 0)); then
    ifraw=0
fi

######################################################
#  which GraceDB to submit the job
#  triggers uploaded to the main grace (0), or gracedb-playground (1)
######################################################
#  DO NOT CHANGE IF YOU ARE NOT RUNNING SPIIR PIPELINE FOR OPA
######################################################

ifplayground=1

######################################################
#  --request-data=${mytag}
######################################################
#  --code-version, obtain the git commit hash for gstlal spiir branch
#  e.g. spiir-review-O3 branch
# source  ~spiir/.spiir_newrankrc
# directory has been changed to ~spiir/software
######################################################

#  Use spiir installation
if [[ -z "${SPIIR_PATH}" ]]; then
    SPIIR_PATH=/home/spiir/spiir-scripts/build/spiir-O4-EW-development
	echo \$SPIIR_PATH not set, assuming SPIIR_PATH=$SPIIR_PATH.
fi

spiir_src_dir=${SPIIR_PATH}/source

######################################################

myrundir=$(pwd)

pushd ${spiir_src_dir} >/dev/null

# use branch when not tagged
version_spiir=$(git rev-parse --short HEAD)
# use tag if tag is available
# version_spiir=$(git log tags/${spiir_tag} | head -1 | awk '{print $2}')

popd >/dev/null

######################################################
#  mylocation is used for all job executables
#  e.g. in gstlal_inspiral_postcohspiir_${user}.sub:
# executable = $mylocation/bin/gstlal_inspiral_postcohspiir_online
######################################################

# Use spiir installation:
mylocation=${SPIIR_PATH}/install

######################################################
# User Input
######################################################
#  spiir is a shared account, specify job submitter
######################################################
user=spiir
submitter=$USER

######################################################
if ((${iflive} == 1)); then
    mytag="Live_H1_L1_V1"
else
    mytag="O3Replay_H1_L1_V1"
fi

######################################################
# number of detectors
######################################################

ndet=3

######################################################
# set the location of the banks (O2 template placement with ER13 PSD)
######################################################
bankdir=/home/spiir/pre-O4/banks/O3/O3b/FB
######################################################
#  --cohfar-assignfar-silent-time ${FAR_silent}
######################################################
FAR_silent=0

######################################################
#  --finalsink-fapupdater-collect-walltime ${wtime1},${wtime2},${wtime3}
#  background accum wall time
######################################################
# Use longer background to ensure enough background data points
wtime1=604800
wtime2=86400
wtime3=7200

######################################################
#
#
# NOTE: the following settings should be the same for
# whichever configuration for ER14
#
#
######################################################

######################################################
#  starting bank number
#  the ID of last bank
######################################################
# default: two detector, fullbank
######################################################
# BBH [  384-415] [380 - 415] 32 banks = 8  /  6 nodes

#RUN_TYPE=BNS
#start=0
#njob=25
#nbank=99

#RUN_TYPE=NSBH
#start=100
#njob=71
#nbank=383

#RUN_TYPE=BBH
start=384
njob=8
nbank=415

#if (( ${ndet} == 3 )); then # if 3-detector and main search
#    if [ "${SearchType}" == "HighMass" ]; then # higmass
#       start=100
#       njob=79
#    else
#       if [ "${SearchType}" == "LowMass" ]; then # lowmass
#           start=0
#           njob=25
#       fi
#   fi
#  else # if two-detector and for the main search
#   if [ "${SearchType}" == "HighMass" ]; then # higmass
#          start=98
#        njob=53
#  else
#       if [ "${SearchType}" == "LowMass" ]; then # lowmass
#           start=0   # 0-101
#        njob=17
#   fi
#   fi
#  fi

######################################################
# Horizon distances given a 1.4+ 1.4 source
# in the gen_pipeline.sh: ifo_horizons=H1:${dhH},L1:${dhL},V1:${dhV}
######################################################
# TODO: Get KAGRA ifo_horizon
if ((${iflive} == 0)); then # O2replay psd
    dhL=140
    dhH=112
    dhV=50
else # ER14 psd
    dhL=140
    dhH=112
    dhV=50
fi

######################################################
# User Input
######################################################

if ((${ifplayground} == 1)); then
    GraceDB_URL=https://gracedb-playground.ligo.org/api/
    myaccgroup=ligo.dev.o4.cbc.em.gstlal_spiir
else
    GraceDB_URL=https://gracedb.ligo.org/api/
    myaccgroup=ligo.prod.o4.cbc.em.gstlal_spiir
fi

if ((${iftest} == 1)); then
    GraceDB_Group=Test
else
    GraceDB_Group=CBC
fi

######################################################
#  bpj: number of banks that can be processed in one node
#       limited by GPU memory and CPU performance
######################################################

if ((${ndet} == 2)); then
    bpj=6
elif ((${ndet} == 3)); then
    bpj=4
else
    echo "TODO: find bpj for KAGRA run."
    exit 0
fi

######################################################
#  Use FIR whitening (1) or FFT whitening (0)
######################################################
newwhiten=0

######################################################
# --finalsink-far-factor == number of total jobs
######################################################

nfac=$((njob)) # BBH on its own count.  3det8+2det6 = 15.  132 #3det79+2det53=132  #$(( njob * 2 ))

######################################################
#  --ht-gate-threshold
#  to eliminate single glitch event from the start
####################################################
htgate_thres=15

######################################################
# if failed, number of reruns for gstlal_inspiral_postcohspiir_job
######################################################
nretry=100

######################################################
mkdir -p ${myrundir}/logs
log_dir=${myrundir}/logs

######################################################
#  DataDir = where the data
#  set the lvshm options to read online data
#  --shared-memory-partition ${mymem}
######################################################
DataDir=/dev/shm
mydatasrc="lvshm"
# TODO: Find KAGRA partitions
if ((${iflive} == 0)); then # run on o2replay
    if ((${ndet} == 2)); then
        mymem="H1=R6LHO_Data --shared-memory-partition=L1=R6LLO_Data --shared-memory-block-size 500000 --shared-memory-assumed-duration 1"
    else
        mymem="L1=R6LLO_Data --shared-memory-partition=H1=R6LHO_Data --shared-memory-partition=V1=R6VIRGO_Data --shared-memory-block-size 1000000 --shared-memory-assumed-duration 1"
    fi
else # run on live
    if ((${ndet} == 2)); then
        mymem="H1=X1LHO_Data --shared-memory-partition=L1=X1LLO_Data --shared-memory-block-size 1000000 --shared-memory-assumed-duration 1"
    else
        mymem="L1=X1LLO_Data --shared-memory-partition=H1=X1LHO_Data --shared-memory-partition=V1=X1VIRGO_Data --shared-memory-block-size 1000000 --shared-memory-assumed-duration 1"
    fi
fi

######################################################
#  set channel/state name, state vector on/off bits
#  --channel-name ${mychannel}
#  --state-channel-name ${mystate}
#  onbits and offbits usage: use the data when (input & required_on) == required_on) && ((~input & required_off) == required_off
#
######################################################
mynodename="postcohspiir"
if ((${iflive} == 1)); then
    onbits=7
    onbits_V=1027
else
    onbits=7      #7
    onbits_V=1027 #1027
fi

if ((${ifraw} == 1)); then
    onbits=0
    onbits_V=0
fi

# TODO: Find KAGRA channels
if ((${iflive} == 1)); then
    if ((${ndet} == 2)); then
        mychannel="H1=GDS-GATED_STRAIN --channel-name L1=GDS-GATED_STRAIN"
        mystate="H1=GDS-CALIB_STATE_VECTOR --state-channel-name L1=GDS-CALIB_STATE_VECTOR  --state-vector-on-bits H1=${onbits} --state-vector-on-bits L1=${onbits}  --state-vector-off-bits H1=0 --state-vector-off-bits L1=0"
    else
        mychannel="H1=GDS-GATED_STRAIN --channel-name L1=GDS-GATED_STRAIN  --channel-name V1=Hrec_hoft_16384Hz_Gated"
        mystate="H1=GDS-CALIB_STATE_VECTOR --state-channel-name L1=GDS-CALIB_STATE_VECTOR  --state-channel-name V1=DQ_ANALYSIS_STATE_VECTOR --state-vector-on-bits H1=${onbits} --state-vector-on-bits L1=${onbits} --state-vector-on-bits V1=${onbits_V}  --state-vector-off-bits H1=0 --state-vector-off-bits L1=0  --state-vector-off-bits V1=0"
    fi
else # O2 replay
    if ((${ndet} == 2)); then
        mychannel="H1=GDS-CALIB_STRAIN_O3Replay --channel-name L1=GDS-CALIB_STRAIN_O3Replay"
        mystate="H1=GDS-CALIB_STATE_VECTOR --state-channel-name L1=GDS-CALIB_STATE_VECTOR  --state-vector-on-bits H1=${onbits} --state-vector-on-bits L1=${onbits}  --state-vector-off-bits H1=0 --state-vector-off-bits L1=0"
    else
        mychannel="H1=GDS-CALIB_STRAIN_INJ1_O3Replay --channel-name L1=GDS-CALIB_STRAIN_INJ1_O3Replay  --channel-name V1=Hrec_hoft_16384Hz_INJ1_O3Replay"
        mystate="H1=GDS-CALIB_STATE_VECTOR --state-channel-name L1=GDS-CALIB_STATE_VECTOR  --state-channel-name V1=DQ_ANALYSIS_STATE_VECTOR --state-vector-on-bits H1=${onbits} --state-vector-on-bits L1=${onbits} --state-vector-on-bits V1=${onbits_V}  --state-vector-off-bits H1=0 --state-vector-off-bits L1=0  --state-vector-off-bits V1=0"
		mydq="H1=GDS-CALIB_STATE_VECTOR --dq-channel-name L1=GDS-CALIB_STATE_VECTOR  --dq-channel-name V1=DQ_ANALYSIS_STATE_VECTOR"
    fi
fi

######################################################
#  --cuda-postcoh-hist-trials ${Nhist}
######################################################
Nhist=100

######################################################
#  --finalsink-snapshot-interval ${ZeroLag_T}
#  Uplate Zerolag output internal to files
######################################################
ZeroLag_T=43200

######################################################
#  --cuda-postcoh-snglsnr-thresh ${snr_thres}
######################################################
snr_thres=4

######################################################
#  --cohfar-accumbackground-snapshot-interval ${FAR_T}
#  background snapshot time
######################################################
FAR_T=3600

######################################################
#  --cohfar-assignfar-refresh-interval ${FAR_refresh}
######################################################
FAR_refresh=1800

######################################################
#  --finalsink-gracedb-far-thresh ${far_thres}
######################################################
far_thres=0.0001

######################################################
#  --finalsink-singlefar-veto-thresh ${FAR_single_thres}
#  apply single-detector-veto threshold
# fix code is different using 0.0001
# change it to 0.5  for production code
######################################################
#FAR_single_thres=0.02
FAR_single_thres=0.5
#FAR_single_thres=0.001

######################################################
#  --finalsink-superevent-thresh ${FAR_event_thres}
#  the event FAR threshold (after applying nfac) that we will apply single-detector-veto
######################################################

FAR_event_thres=0.0001

######################################################
#  --finalsink-fapupdater-interval ${Tfapupdate}
######################################################
Tfapupdate=1800

######################################################
#  --finalsink-cluster-window ${tcluster}
######################################################
tcluster=1

######################################################
#  set parameters for: gstlal_inspiral_postcohspiir.sub
#  --cuda-postcoh-detrsp-fname ${mymap}
#  --cuda-postcoh-detrsp-refresh-interval ${Tmap}
#  and update_map_${user.sub}
#  --output-prob-coeff ${mymap_prob}
#  --output-coh-coeff ${mymap}
#  --data-loc ${H1DataDir}
#  --chealpix-order ${npix}
#  --period ${MapUpdate_T}
######################################################

if ((${ndet} == 2)); then
    mymap=H1L1_detrsp_map.xml
    mymap_prob=H1L1_prob_map.xml
elif ((${ndet} == 3)); then
    mymap=H1L1V1_detrsp_map.xml
    mymap_prob=H1L1V1_prob_map.xml
else
    mymap=H1L1V1K1_detrsp_map.xml
    mymap_prob=H1L1V1K1_prob_map.xml
fi

Tmap=86400
H1DataDir=${DataDir}/kafka/H1_O3ReplayMDC
npix=5
MapUpdate_T=43201

######################################################
#  --cuda-postcoh-output-skymap ${SNRmap}
######################################################
SNRmap=7
############################
#  --psd-fft-length ${psd_len}
############################
psd_len=4

##################################################################
#
#
#  generate the dag file for all jobs
#
#
##################################################################

for ((i = 0; i < ${njob}; i++)); do
    jobno=$(seq -f "%03g" ${i} ${i})
    # set the names for the three-scale FAR files
    statsdir=${jobno}
    stats_2w=${statsdir}/${jobno}_marginalized_stats_2w.xml.gz
    stats_1d=${statsdir}/${jobno}_marginalized_stats_1d.xml.gz
    stats_2h=${statsdir}/${jobno}_marginalized_stats_2h.xml.gz
    echo "JOB gstlal_inspiral_postcohspiir_${jobno} gstlal_inspiral_postcohspiir_${user}.sub"
    echo "RETRY gstlal_inspiral_postcohspiir_${jobno} ${nretry}"
    echo -n "VARS gstlal_inspiral_postcohspiir_${jobno}
        macrofarinput=\"${stats_2w},${stats_1d},${stats_2h}\" 
        macrolocfapoutput=\"${stats_2w},${stats_1d},${stats_2h}\"
        macrojobtag=\"${jobno}\" 
        macronodename=\"${mynodename}\"
        " | /bin/tr '\n' ' ' | /bin/tr -s " "

    for bank in $(seq -f "%04g" $((${start} + ${bpj} * ${i})) $((${start} + ${bpj} * ($i)))); do
        H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        V1bank=${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        K1bank=${bankdir}/iir_K1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        if ((${ndet} == 2)); then
            echo -n " macroiirbank=\"H1:${H1bank},L1:${L1bank}"
        elif ((${ndet} == 3)); then
            echo -n " macroiirbank=\"H1:${H1bank},L1:${L1bank},V1:${V1bank}"
        else
            echo -n " macroiirbank=\"H1:${H1bank},L1:${L1bank},V1:${V1bank},K1:${K1bank}"
        fi
    done

    endbank=$((${start} + ${bpj} * ($i + 1) - 1))
    if ((${endbank} > ${nbank})); then
        endbank=${nbank}
    fi
    for bank in $(seq -f "%04g" $((${start} + ${bpj} * ${i} + 1)) $((${endbank}))); do
        H1bank=${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        L1bank=${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        V1bank=${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        K1bank=${bankdir}/iir_K1-GSTLAL_SPLIT_BANK_${bank}-a1-0-0.xml.gz
        if ((${ndet} == 2)); then
            echo -n " --iir-bank H1:${H1bank},L1:${L1bank}"
        elif ((${ndet} == 3)); then
            echo -n " --iir-bank H1:${H1bank},L1:${L1bank},V1:${V1bank}"
        else
            echo -n " --iir-bank H1:${H1bank},L1:${L1bank},V1:${V1bank},K1:${K1bank}"
        fi
    done
    for bank in $(seq -f "%04g" $((${start} + ${bpj} * ${i})) $((${start} + ${bpj} * ($i)))); do
        echo -n "\" macrostatsprefix=\"${jobno}/bank${bank}_stats"
    done

    for bank in $(seq -f "%04g" $((${start} + ${bpj} * ${i} + 1)) $((${endbank}))); do
        echo -n " --cohfar-accumbackground-output-prefix ${jobno}/bank${bank}_stats"
    done

    echo "\" macrooutprefix=\"${jobno}/${jobno}_zerolag\""
done

cat <<-EOF
	JOB get_url_0001 get_url_${user}.sub
	RETRY get_url_0001 10

	JOB update_map_0001 update_map_${user}.sub
	RETRY update_map_0001 10

	JOB monitor_pipeline_0001 monitor_pipeline_${user}.sub
	RETRY monitor_pipeline_0001 100
EOF

