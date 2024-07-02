# sinteractive --partition=skylake --gres=gpu:1 --time=01:00:00 --cpus-per-task=1 --mem-per-cpu=12000

mkdir -p $PWD/gst_debug
export GST_DEBUG_DUMP_DOT_DIR=$PWD/gst_debug

bankdir=/fred/oz996/tdavies/spiir_project/sources/resources/banks_gwdc
detrsp_mapdir=/fred/oz996/tdavies/spiir_project/sources/resources/detrsp_map

event_time=1187008882
starttime=$(( $event_time - 300))
endtime=$(( $event_time + 50 ))
bankid=0003

current_host=$HOSTNAME
ozstar_bool=false

frame_cache=/fred/oz016/data/frame.cache.C00


macro_iir_all_banks="H1:${bankdir}/iir_H1-GSTLAL_SPLIT_BANK_${bankid}-a1-0-0.xml.gz,L1:${bankdir}/iir_L1-GSTLAL_SPLIT_BANK_${bankid}-a1-0-0.xml.gz,V1:${bankdir}/iir_V1-GSTLAL_SPLIT_BANK_${bankid}-a1-0-0.xml.gz"
detrsp_map="${detrsp_mapdir}/H1L1V1_detrsp_map_${event_time}.xml"

p_astro_config_dir=/fred/oz016/dtang/pipeline/builds/p_astro_gstreamer_python_upgrade/spiir/gstlal-spiir/share/p_astro/models/
p_astro_fgmc_config="${p_astro_config_dir}/fgmc.pkl"
p_astro_mchirp_area_config="${p_astro_config_dir}/mchirp_area.pkl"

gstlal_inspiral_postcohspiir_online \
    --job-tag 000 \
    --iir-bank  $macro_iir_all_banks \
    --gpu-acc \
    --data-source frames \
    --frame-cache  $frame_cache \
    --gps-start-time $starttime \
    --gps-end-time $endtime \
    --track-psd \
    --channel-name H1=GDS-CALIB_STRAIN \
    --channel-name L1=GDS-CALIB_STRAIN \
    --channel-name V1=Hrec_hoft_16384Hz \
    --cohfar-accumbackground-output-prefix 000/bank0_stats \
    --cohfar-accumbackground-snapshot-interval 200 \
    --cohfar-assignfar-silent-time 0 \
    --cohfar-assignfar-input-fname 000/marginalized_1w.xml.gz,000/marginalized_1d.xml.gz,000/marginalized_2h.xml.gz \
    --cohfar-assignfar-refresh-interval 200 \
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
    --finalsink-gracedb-far-threshold 0.0001 \
    --finalsink-gracedb-search AllSky \
    --finalsink-gracedb-group Test \
    --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/ \
    --finalsink-gracedb-upload-attempts 2 \
    --code-version unit_testing \
    --skip-psd-upload \
    --p-astro-fgmc-config $p_astro_fgmc_config \
    --p-astro-mchirp-area-config $p_astro_mchirp_area_config \
    --write-pipeline gw170817_offline \
    --verbose
