#!/bin/bash
#
#SBATCH --ntasks=1
#SBATCH --time=100:00:00
#SBATCH --mem=16g
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --array=0
#SBATCH --requeue

start=$1
length=$2
end=$3
dir=$4

mkdir -p $dir
# # generate spiir bank
# gstlal_iir_bank --reference-psd ${PSD} --template-bank /fred/oz016/manoj/test/new_split/${SBNK}/${IFO}_split_bank/${IFO}-GSTLAL_SPLIT_BANK_${BNK}-0-0.xml.gz --flow 15.0 --waveform-domain FD --padding 1.3 --instrument ${IFO} --output gstlal_iir_bank_${SUF}/iir_${IFO}-GSTLAL_SPLIT_BANK_${BNK}-a1-0-0.xml.gz --autocorrelation-length 351 --sampleRate 2048.0 -v --epsilon-options "'{"epsilon_start":1.0,"nround_max":25,"initial_overlap_min":0.95,"b0_optimized_overlap_min":0.'"${BOPTPERC}"',"epsilon_factor":1.2,"filters_max":350}'" --optimizer-options "'{"verbose":true,"passes":16,"indv":true,"hessian":true}'" --approximant ${APPROX} --negative-latency ${NEGLAT}"

# create injection xml
if test -f ${dir}/fake_inj.xml; then
    echo "fake_inj.xml already generated."
else
    lalapps_inspinj --m-distr totalMass --min-mass1 1 --max-mass1 3 --min-mass2 1 --max-mass2 3 --min-mtotal 2 --max-mtotal 6 --gps-start-time ${start} --gps-end-time ${end} --enable-spin --min-spin1 0 --max-spin1 0.4 --min-spin2 0 --max-spin2 0.4 --waveform SpinTaylorT4threePointFivePN --f-lower 20 --i-distr uniform --l-distr random --t-distr uniform --time-step 30 --taper-injection start --seed 1 --output ${dir}/fake_inj.xml --d-distr uniform --min-distance 5000 --max-distance 20000 --verbose
fi
ligolw_print -t sim_inspiral -c h_end_time -c mass1 -c mass2 -c mchirp -c eta -c spin1z -c spin2z -c eff_dist_h -c alpha4 -c longitude -c latitude ${dir}/fake_inj.xml

# create fake frames from injection xml
if [ -d "$dir/fake_frames_inj" ]; then
    echo "fake frames already generated."
else
    mkdir $dir/fake_frames_inj
    gstlal_fake_frames --data-source AdvLIGO --output-path ${dir}/fake_frames_inj --gps-start-time ${start} --frame-type H1_INJECTIONS --gps-end-time ${end} --frame-duration 16 --frames-per-file 125 --verbose --channel-name=H1=FAKE_INJECTIONS --injections ${dir}/fake_inj.xml &
    gstlal_fake_frames --data-source AdvLIGO --output-path ${dir}/fake_frames_inj --gps-start-time ${start} --frame-type L1_INJECTIONS --gps-end-time ${end} --frame-duration 16 --frames-per-file 125 --verbose --channel-name=L1=FAKE_INJECTIONS --injections ${dir}/fake_inj.xml &
    gstlal_fake_frames --data-source AdvVirgo --output-path ${dir}/fake_frames_inj --gps-start-time ${start} --frame-type V1_INJECTIONS --gps-end-time ${end} --frame-duration 16 --frames-per-file 125 --verbose --channel-name=V1=FAKE_INJECTIONS --injections ${dir}/fake_inj.xml &
    gstlal_fake_frames --data-source AdvVirgo --output-path ${dir}/fake_frames_inj --gps-start-time ${start} --frame-type K1_INJECTIONS --gps-end-time ${end} --frame-duration 16 --frames-per-file 125 --verbose --channel-name=K1=FAKE_INJECTIONS --injections ${dir}/fake_inj.xml &
fi

pids=( $(jobs -p) )

echo "PIDS: ${pids[*]}"

for pid in "${pids[@]}"; do
    wait "$pid"
    echo "Exit code $?"
done

# append fake frames to cache file
if test -f ${dir}/fake-frame.cache; then
    echo "frame cache file already generated."
else
    ls ${dir}/fake_frames_inj/*/*.gwf | lalapps_path2cache >> ${dir}/fake-frame.cache
fi

# generate reference_psd
if test -f ${dir}/H1L1V1K1-REFERENCE_PSD-${start}-${length}.xml.gz; then
    echo "reference psd already generated."
else
    gstlal_reference_psd --data-source frames --frame-cache ${dir}/fake-frame.cache --gps-start-time=${start} --gps-end-time=${end} --channel-name=H1=FAKE_INJECTIONS --channel-name=L1=FAKE_INJECTIONS --channel-name=V1=FAKE_INJECTIONS --channel-name=K1=FAKE_INJECTIONS --write-psd ${dir}/H1L1V1K1-REFERENCE_PSD-${start}-${length}.xml.gz --verbose --psd-fft-length 16
fi

# generate detrsp map
if test -f ${dir}/H1L1V1K1_detrsp_map.xml; then
    echo "detrsp already generated."
else
    gstlal_postcoh_gen_detrsp_map --ifo-horizons H1:111,L1:212,V1:56,K1:56 --chealpix-order 5 --output-coh-coeff ${dir}/H1L1V1K1_detrsp_map.xml --output-prob-coeff ${dir}/H1L1V1K1_prob_map.xml --gps-time ${start}
fi

# # generate fits from skymap binary
# gstlal_postcoh_skymap2fits --output-cohsnr cohsnr_skymap.fits.gz --output-prob spiir.fits.gz --cuda-postcoh-detrsp-fname H1L1V1K1_prob_map.xml --event-id 0 --event-time 1187006432 H1L1_skymap/H1_1187006432_89355469_3_29
