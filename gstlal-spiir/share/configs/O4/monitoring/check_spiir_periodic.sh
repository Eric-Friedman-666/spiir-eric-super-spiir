#!/bin/sh

out_dir=$1

stale_age=$2

mkdir -p ${out_dir}

rm ${out_dir}/summary_spiir_latency.txt 2>/dev/null || true
rm ${out_dir}/summary_spiir_trigger.txt 2>/dev/null || true

x=`date` 

cat <<-EOF >${out_dir}/summary_spiir.txt
#############################################################################
#
#
#           Welcome to the SPIIR ER15/O4 Science Run Real-time Monitoring Page
#                  This page is refreshed every 2 min
#
#
#############################################################################
                        The current time is: $x

================================================================================================================
                                    Introduction
================================================================================================================
Two Labels: LowMass (BNS events) and HighMass (NSBH and BBH events).
FARs calculated separated for these two labels.
                      Current FAR threshold = 3 e-4 for each of the two labels.
                             Current Burn-in time =  4 hrs
Jobs Currently  Submitted to Main GraceDB Database (1) HighMass: BBH/NSBH
Jobs Currently Submitted to Main GraceDB Database: (2) LowMass : BNS, and NSBH
Jobs to be  Submitted to Main GraceDB Database : test_NSBH_3dethighmass.dag (Label: HighMass)

##############################################################################
================================================================================================================
                             SPIIR Condor Job Running Status
               (One should be worried if the number of idle jobs is not zero)
     (Total job submitted usally = 1, but = total jobs if interrupted in the middle but (automatically) restart)
================================================================================================================
##############################################################################


Submitter   Submitted Job+ID     Submission Time    #Job Done   #Jobs Currently Running  #Jobs Idle  #Total submitted  #IDs
----------------------------------------------------------------------------------------------------------------------------
EOF

condor_q -wide >> ${out_dir}/summary_spiir.txt
echo "" >> ${out_dir}/summary_spiir.txt
condor_q -nob >> ${out_dir}/summary_spiir.txt

condor_jobs="${@:3}"

for condor_job in ${condor_jobs[@]}; do
	# Check status of condor job
    while read -r line ; do
		regex='Iwd = "(.*)"'
		if [[ $line =~ $regex ]]
		then
            run_dir="${BASH_REMATCH[1]}"
		fi
	done <<< "$(condor_q -long ${condor_job})"

    # If condor job not spiir, do not try read latencies.
	if [ ! -d "$run_dir/000" ]; then
		continue 
	fi

    ls -ltcrah $run_dir/logs/postcohspiir* >> ${out_dir}/summary_spiir.txt
    echo "" >> ${out_dir}/summary_spiir.txt

    ~/monitoring/check_spiir_latency.sh $run_dir >> ${out_dir}/summary_spiir_latency.txt

    ~/monitoring/check_spiir_trigger.sh $run_dir >> ${out_dir}/summary_spiir_trigger.txt

	condor_jobs_string="${condor_jobs_string} --condor_jobs $condor_job"
done

~/.conda/envs/spiir-monitor/bin/python ~/monitoring/check_spiir.py --output_status ${out_dir}/status.json --stale_age ${stale_age} ${condor_jobs_string}
