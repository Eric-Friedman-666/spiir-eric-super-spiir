date
run_dir=$1

latency_list=$(ls -ltra ${run_dir}/*/latency_history.txt | awk -F ' ' '{print $9}')

echo "###############################
#       latency files
###############################" 

echo "Run directory:    $run_dir"
echo "#################################################################"
echo "Time (gps)   Latency (s)   SNR   Chi-square" 

for file in $latency_list;
do 
  echo "#################################################################"
  echo $file
  echo "#################################################################"
  echo "------------------------------------------------------------------
  Last Latency 
--------------------------------------------------------------------"
  tail  --lines=1 $file  | sort 
  echo "------------------------------------------------------------------
  Last 10 with Latency > 30 
--------------------------------------------------------------------"
  awk -F ' ' '($2 > 30 ){print}' $file | tail --lines 10
  echo ""
done

echo ""

echo "#############################################################################"
echo  " (HLV)         time  on  off gap "

for sfile in `ls ${run_dir}/000/*[HLVK]1/state*`
do
  more ${sfile}
  echo " "
done

echo ""
echo ""
