#!/bin/bash

set -o errexit
set -o pipefail
# set -o nounset
set -o xtrace

monitor_dir=$1
run_dir=$2
node=$3
host=$(hostname)
node_sd_dir="${monitor_dir}/prometheus-2.42.0.linux-amd64/node_sd"
node_exporter_exe="${monitor_dir}/node_exporter-1.5.0.linux-amd64/node_exporter --collector.systemd --collector.processes"
process_exporter_exe="${monitor_dir}/process-exporter-0.7.10.linux-amd64/process-exporter -config.path ${monitor_dir}/process-exporter-0.7.10.linux-amd64/config.yaml"
nvidia_gpu_exporter_exe="${monitor_dir}/nvidia_gpu_exporter"

target_file="${node_sd_dir}/${host}_target.json"
if [ -f $target_file ]; then
    echo "target_file for host already exists."
    cat $target_file
fi

OLD_UMASK=$(umask)

umask 0000

cat <<- EOF > $target_file
[{
    "labels": {
        "job": "node",
        "pipeline": "$(basename $run_dir)",
        "node_id": "$node"
        },
    "targets": [
        "$host:9100",
        "$host:9256",
        "$host:9835"
    ]
}]
EOF

umask $OLD_UMASK

(while true; do 
    echo "starting node_exporter";
    $node_exporter_exe 2> "${run_dir}/logs/node_exporter-job${node}.err.txt" > "${run_dir}/logs/node_exporter-job${node}.out.txt";
    sleep 60;
done) &
pid=($!)

(while true; do 
    echo "starting process_exporter";
    $process_exporter_exe 2> "${run_dir}/logs/process_exporter-job${node}.err.txt" > "${run_dir}/logs/process_exporter-job${node}.out.txt";
    sleep 60;
done) &
pid+=($!)

(while true; do 
    echo "starting nvidia_gpu_exporter";
    $nvidia_gpu_exporter_exe 2> "${run_dir}/logs/nvidia_gpu_exporter-job${node}.err.txt" > "${run_dir}/logs/nvidia_gpu_exporter-job${node}.out.txt";
    sleep 60;
done) &
pid+=($!)

trap cleanup 1 2 3 6

cleanup()
{
    kill ${pid[@]}
    rm $target_file
    exit
}

export PATH="$SPIIR_PATH/install/bin:$PATH"
export PYTHONPATH="$SPIIR_PATH/install/lib/python2.7/site-packages:$SPIIR_PATH/install/lib64/python2.7/site-packages:/usr/spiir/lib/python2.7/site-packages:$PYTHONPATH"
export PKG_CONFIG_PATH="$SPIIR_PATH/install/lib/pkgconfig:$PKG_CONFIG_PATH"
export GST_PLUGIN_PATH="$SPIIR_PATH/install/lib/gstreamer-0.10:$GST_PLUGIN_PATH"
export LD_LIBRARY_PATH="$SPIIR_PATH/install/lib:$SPIIR_PATH/install/lib64:$LD_LIBRARY_PATH"

${@:4}

cleanup()