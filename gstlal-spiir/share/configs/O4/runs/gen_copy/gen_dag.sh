# Source configuration settings
source ./generic_ini.sh

##################################################################
##################################################################
##################################################################
#
#  NOTE: The rest would be the same for ER14/O3 settings
#  generate get_url_${user}.sub
#  to get latencies and SNRs from last 1000 triggers
#  of each job for online monitoring
#
##################################################################
##################################################################

PERMISSION_VARS="X509_USER_PROXY=$X509_USER_PROXY X509_USER_KEY=$X509_USER_KEY X509_USER_CERT=$X509_USER_CERT KRB5_KTNAME=$KRB5_KTNAME"

monitor_dir="/home/spiir/monitoring"
run_dir=$(pwd)

read -r -d '' COMMON_VARS <<- EOF
	+SingularityImage = "$SPIIR_DEP_PATH"
	accounting_group_user  =  $submitter
	accounting_group  =  $myaccgroup
	Requirements  = (Target.Online_CBC_IIR_GPU_X2200 =?= True)
	kill_sig = 15
	+Online_CBC_IIR_GPU_X2200 = True
	+OpSysAndVer = "SL7"
	environment = "$PERMISSION_VARS GST_DEBUG=$GST_DEBUG GST_REGISTRY_UPDATE=no GST_REGISTRY=$(pwd)/gst-registry.bin PATH=$SPIIR_PATH/install/bin:\$PATH PYTHONPATH=$SPIIR_PATH/install/lib/python2.7/site-packages:$SPIIR_PATH/install/lib64/python2.7/site-packages:/usr/spiir/lib/python2.7/site-packages:\$PYTHONPATH PKG_CONFIG_PATH=$SPIIR_PATH/install/lib/pkgconfig:\$PKG_CONFIG_PATH GST_PLUGIN_PATH=$SPIIR_PATH/install/lib/gstreamer-0.10:\$GST_PLUGIN_PATH LD_LIBRARY_PATH=$SPIIR_PATH/install/lib:$SPIIR_PATH/install/lib64:\$LD_LIBRARY_PATH"
	transfer_executable = False
	on_exit_hold = ExitBySignal == true
	notification = Always
	queue 1
EOF

cat <<-EOF >monitor_pipeline_${user}.sub
	universe = Vanilla
	accounting_group_user  =  $submitter
	accounting_group  =  $myaccgroup
	Requirements  = (Target.Online_CBC_IIR_GPU_X2200 =?= True)
	kill_sig = 15
	+Online_CBC_IIR_GPU_X2200 = True
	+OpSysAndVer = "SL7"
	executable = /home/spiir/.conda/envs/spiir-monitor/bin/python
	arguments = "$run_dir/monitor_pipeline.py --run_dir $run_dir"
	request_disk = 1GB
	log = ${log_dir}/monitor_pipeline_${user}.dag.log
	error = ${log_dir}/monitor_pipeline_${user}-\$(cluster)-\$(process).err
	output = ${log_dir}/monitor_pipeline_${user}-\$(cluster)-\$(process).out
	transfer_executable = False
	on_exit_hold = ExitBySignal == true
	notification = Always
	queue 1
EOF

args="000"
for (( i=1; i<${njob}; i++ )); do
    jobno=$( seq -f "%03g" ${i} ${i} )
    args="$args $jobno"
done

cat <<-EOF >get_url_${user}.sub
	universe = Vanilla
	executable = /bin/bash
	arguments = "$run_dir/monitor_pipeline.sh $monitor_dir get_url $mylocation/bin/gstlal_periodic_get_urls $args"
	request_disk = 1GB
	log = ${log_dir}/get_url_${user}.dag.log
	error = ${log_dir}/get_url_${user}-\$(cluster)-\$(process).err
	output = ${log_dir}/get_url_${user}-\$(cluster)-\$(process).out
	$COMMON_VARS
EOF

##################################################################
#  generate update_map_${user}.sub
#  this is to update the detector reponse map 
#  to capture the movement of Earch every day for the coherent search
##################################################################
if ((${ndet} == 2)); then
	ifo_horizons=H1:${dhH},L1:${dhL}
elif ((${ndet} == 3)); then
	ifo_horizons=H1:${dhH},L1:${dhL},V1:${dhV}
else
	ifo_horizons=H1:${dhH},L1:${dhL},V1:${dhV},K1:${dhK}
fi

cat <<-EOF >update_map_${user}.sub
	universe = Vanilla
	executable = /bin/bash
	arguments = "$run_dir/monitor_pipeline.sh $monitor_dir update_detrspmap $mylocation/bin/gstlal_periodic_postcoh_update_detrspmap --data-loc ${H1DataDir} --ifo-horizons ${ifo_horizons} --chealpix-order ${npix} --output-coh-coeff ${mymap} --output-prob-coeff ${mymap_prob} --period ${MapUpdate_T}"
	request_disk = 1GB
	log = ${log_dir}/update_map_${user}.dag.log
	error = ${log_dir}/update_map_${user}-\$(cluster)-\$(process).err
	output = ${log_dir}/update_map_${user}\$(cluster)-\$(process).out
	$COMMON_VARS
EOF


##################################################################
#  generate clean_skymap_${user}.sub
#  to clean up old skymaps when not used for graceDB
##################################################################

if ((${ndet} == 2)); then
	clean_place=H1L1_skymap
elif ((${ndet} == 3)); then
	clean_place=H1L1V1_skymap,H1L1_skymap,H1V1_skymap,L1V1_skymap
else
	clean_place=H1L1V1K1_skymap,H1L1V1_skymap,H1L1K1_skymap,H1V1K1_skymap,L1V1K1_skymap,H1L1_skymap,H1V1_skymap,H1K1_skymap,L1V1_skymap,L1K1_skymap,V1K1_skymap
fi

cat <<-EOF >clean_skymap_${user}.sub
	universe = Vanilla
	executable = /bin/bash
	arguments = "$run_dir/monitor_pipeline.sh $monitor_dir clean_skymap ${mylocation}/bin/gstlal_periodic_clean_skymap --data-loc ${H1DataDir} --clean-days-ago 0.5 --period 1200 --skymap-loc $clean_place"
	request_disk = 1GB
	log = ${log_dir}/clean_skymap_${user}.dag.log
	error = ${log_dir}/clean_skymap_${user}-\$(cluster)-\$(process).err
	output = ${log_dir}/clean_skymap_${user}-\$(cluster)-\$(process).out
	$COMMON_VARS
EOF


##################################################################
#
#  generate gstlal_inspiral_postcohspiir_${user}.sub
#  please see documentation of the spiir-review-O3 at git.ligo.org page
#  for explanation of the options.
#
##################################################################

# Depending on the code version these arguments will be required.
maybe_args_list=( "cohfar-assignfar-refresh-offset" "cuda-postcoh-detrsp-refresh-offset" "finalsink-superevent-thresh" )
maybe_vals_list=( "${FAR_refresh_offset}"           "${Tmap_offset}"                     "${FAR_event_thres}" )
maybe_args=""

for idx in "${!maybe_args_list[@]}"
do
	if grep -Fq "${maybe_args_list[$idx]}" "$mylocation/bin/gstlal_inspiral_postcohspiir_online"
	then
	    maybe_args="${maybe_args} --${maybe_args_list[$idx]} ${maybe_vals_list[$idx]}"
	fi
done

cat <<-EOF >gstlal_inspiral_postcohspiir_${user}.sub
	universe = vanilla
	executable = /bin/bash
	arguments = "$run_dir/monitor_pipeline.sh $monitor_dir \$(macrojobtag) $mylocation/bin/gstlal_inspiral_postcohspiir_online \\
		--job-tag \$(macrojobtag) \\
		--tmp-space _CONDOR_SCRATCH_DIR \\
		--iir-bank \$(macroiirbank) \\
		--data-source ${mydatasrc} \\
		--request-data ${mytag} \\
		--track-psd \\
		--psd-fft-length ${psd_len} \\
		--channel-name ${mychannel} \\
		--state-channel-name ${mystate} \\
		--gpu-acc on \\
		--ht-gate-threshold ${htgate_thres} \\
		--shared-memory-partition ${mymem} \\
		--cuda-postcoh-snglsnr-thresh ${snr_thres} \\
		--cuda-postcoh-hist-trials ${Nhist} \\
		--cuda-postcoh-detrsp-fname ${mymap} \\
		--cuda-postcoh-output-skymap ${SNRmap} \\
		--check-time-stamp \\
		--finalsink-output-prefix \$(macrooutprefix) \\
		--finalsink-snapshot-interval ${ZeroLag_T} \\
		--cohfar-accumbackground-snapshot-interval ${FAR_T} \\
		--cohfar-accumbackground-output-prefix \$(macrostatsprefix) \\
		--cohfar-assignfar-input-fname \$(macrofarinput) \\
		--finalsink-fapupdater-output-fname \$(macrolocfapoutput) \\
		--cohfar-assignfar-silent-time ${FAR_silent} \\
		--cohfar-assignfar-refresh-interval ${FAR_refresh} \\
		--cohfar-assignfar-refresh-offset $(({FAR_refresh}/10)) \\
		--finalsink-cluster-window ${tcluster} \\
		--finalsink-fapupdater-interval ${Tfapupdate} \\
		--finalsink-fapupdater-collect-walltime ${wtime1},${wtime2},${wtime3} \\
		--finalsink-far-factor $nfac \\
		--finalsink-gracedb-far-thresh ${far_thres} \\
		--finalsink-need-online-perform 1 \\
		--finalsink-gracedb-group ${GraceDB_Group} \\
		--finalsink-gracedb-search ${SearchType} \\
		--finalsink-gracedb-service-url ${GraceDB_URL} \\
		--cuda-postcoh-detrsp-refresh-interval ${Tmap} \\
		--cuda-postcoh-detrsp-refresh-offset $(({Tmap}/10)) \\
		--code-version ${version_spiir} \\
		--finalsink-singlefar-veto-thresh ${FAR_single_thres} \\
		--fir-whitener ${newwhiten} \\
		${maybe_args}"
	want_graceful_removal = True
	+General_Use_AMD = True
	request_cpus = Target.Cpus
	request_gpus = Target.Gpus
	request_memory = 19GB
	request_disk = 20GB
	log = ${log_dir}/\$(macronodename)_${user}.dag.log
	error = ${log_dir}/\$(macronodename)-\$(cluster)-job\$(macrojobtag).err
	output = ${log_dir}/\$(macronodename)-\$(cluster)-job\$(macrojobtag).out
	stream_output = True
	$COMMON_VARS
EOF
