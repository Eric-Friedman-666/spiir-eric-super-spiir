# sinteractive --partition=skylake --gres=gpu:1 --time=01:00:00 --cpus-per-task=1 --mem-per-cpu=12000

#TODO: maybe also add an ndet variable and an if-else loop for the data and banks to use if there are 
#only 2 detectors? IMPORTANT: If you run the bypass you need a HL skymap, not an HLV skymap
#THIS EVENT IS A 2 DETECTOR HL EVENT
event_time=1246527224 
starttime=$(( $event_time - 300))
endtime=$(( $event_time + 50 ))
bankid=0383
parti_ifos=H1L1

current_host=$HOSTNAME
ozstar_bool=false

for local_id in "farnarkle" "john" "bryan"
do
  if [[ "$current_host" == *$local_id* ]]; then
    echo "you're working on $local_id node on the OzStar cluster."
    ozstar_bool=true
  fi
done

#   If dependencies is not passed as an argument then pass default values
# and point to the appropriate frame.cache file.
#
#   On ozstar:
# default frame file is: $dir_containing_script/dependencies/frame_cache_OzStar_O3_C00_with_V.cache
# dependencies directory is: /fred/oz016/gwdc_spiir_pipeline_codebase/gwdc_utils/unit_testing/dependencies
#
#   On cit:
# default frame file is packaged within the dependencies folder as: frame_for_unit_test.cache
# dependencies directory is: dependencies_dir=/home/alex.codoreanu/gwdc_utils/unit_testing/dependencies
dependencies_dir=$1
dependencies_bool=false
if [ ${#dependencies_dir} -ge 1 ]; then
  dependencies_bool=true
fi

if [ "$dependencies_bool" = false ]; then
  echo "You did not pass me a dependencies directory so I'll just use default values."
  if [ "$ozstar_bool" = true ]; then
    dependencies_dir=/fred/oz016/gwdc_spiir_pipeline_codebase/gwdc_utils/unit_testing/dependencies
    frame_cache=/fred/oz016/gwdc_spiir_pipeline_codebase/gwdc_utils/unit_testing/dependencies/frame_cache_OzStar_O3_C00_with_V.cache
	#$(dirname "$0")/dependencies/frame_cache_OzStar_O3_C00_with_V.cache
  else
    echo "I assume you're working on the ldas CIT cluster."
    dependencies_dir=/home/alex.codoreanu/gwdc_utils/unit_testing/dependencies
    frame_cache="${dependencies_dir}/frame_for_unit_test.cache"
  fi
fi

# if someone has given an explicit dependencies dir, respect that
frame_cache=${frame_cache:-${dependencies_dir}/frame_cache_OzStar_O3_C00_with_V.cache}

echo "reading dependencies from: ${dependencies_dir}"

macro_iir_all_banks="H1:${dependencies_dir}/iir_H1-GSTLAL_SPLIT_BANK_${bankid}-a1-0-0.xml.gz,L1:${dependencies_dir}/iir_L1-GSTLAL_SPLIT_BANK_${bankid}-a1-0-0.xml.gz"
detrsp_map="${dependencies_dir}/H1L1_detrsp_map_${event_time}.xml"

gstlal_inspiral_postcohspiir_online \
	--job-tag 000 \
	--iir-bank  $macro_iir_all_banks \
	--gpu-acc on \
	--data-source frames \
	--frame-cache  $frame_cache \
	--gps-start-time $starttime \
	--gps-end-time $endtime \
	--track-psd \
	--channel-name H1=GDS-CALIB_STRAIN \
	--channel-name L1=GDS-CALIB_STRAIN \
	--cohfar-accumbackground-output-prefix 000/bank0_stats \
	--cohfar-accumbackground-output-prefix 000/bank1_stats \
	--cohfar-accumbackground-snapshot-interval 200 \
	--cohfar-assignfar-silent-time 0 \
	--cohfar-assignfar-input-fname 000/marginalized_1w.xml.gz,000/marginalized_1d.xml.gz,000/marginalized_2h.xml.gz \
	--cohfar-assignfar-refresh-interval 200 \
	--gpu-acc on  \
	--ht-gate-threshold 15.0 \
	--cuda-postcoh-snglsnr-thresh 4 \
	--cuda-postcoh-hist-trials 100 \
	--cuda-postcoh-detrsp-fname $detrsp_map \
	--cuda-postcoh-detrsp-refresh-interval 86400 \
	--cuda-postcoh-output-skymap 7 \
	--check-time-stamp \
	--finalsink-fapupdater-collect-walltime 604800,86400,7200 \
	--finalsink-fapupdater-interval 1800 \
	--finalsink-output-prefix 000/000_zerolag \
	--finalsink-snapshot-interval 1200 \
	--finalsink-cluster-window 1 \
	--finalsink-far-factor 2 \
	--finalsink-singlefar-veto-thresh 0.5 \
	--finalsink-superevent-thresh 0.0001 \
	--finalsink-need-online-perform 1 \
	--finalsink-gracedb-far-threshold 0.0001 \
	--code-version unit_testing \
	--verbose
	#	--cuda-postcoh-parti-ifos $parti_ifos \
