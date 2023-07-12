date
run_dir=$1

echo "###############################
#       injection latency files
###############################" 

echo "Run directory:    $run_dir"
echo "#################################################################"

file=${run_dir}/trigger_control.txt

echo " "
echo "Last 10 triggers " 
echo "----------------------------------------------------------"
echo "Time, FAR, Submitted to GraceDB(1: yes; 0: no, vetoed)"
echo "----------------------------------------------------------"

tail  --lines=10 $file  | sort 

echo " "
echo "Last 10 with FAR < 3e-6"
echo "----------------------------------------------------------"
echo "Time, FAR, Submitted to GraceDB(1: yes; 0: no, vetoed)"
echo "----------------------------------------------------------"


awk -F ',' '($2<3e-6){print}' $file | tail --lines 10

echo " "
echo "Time of trigger and dag file "
echo "-----------------------------------"

ls -ltra $file 
ls -ltra ${run_dir}/*.dag | tail --lines 2  


echo " "
echo ""
echo ""
