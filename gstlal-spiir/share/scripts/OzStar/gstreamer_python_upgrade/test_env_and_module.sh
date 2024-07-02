#!/usr/bin/env bash

# If it can't find the gstrtcuda (or something like that) element, try loading cuda 
#module load cuda/10.2.89.lua
#echo clear gst-inspect-1.0 cache (optional)
rm $HOME/.cache/gstreamer-1.0/registry.x86_64.bin
#echo gst-inspect-1.0 -b
gst-inspect-1.0 -b
#echo gst-inspect-1.0 cuda
gst-inspect-1.0 gstlal
gst-inspect-1.0 cuda
# Pass in the directory directly
#gst-inspect-1.0 --gst-plugin-path=/fred/oz996/tdavies/spiir_project/install/lib/gstreamer-1.0


# Try the gst spiir filepath directly:
#gst-inspect-1.0 /fred/oz996/tdavies/spiir_project/install/lib/gstreamer-1.0/libgstcuda.so

# Check a submodule of the gstlal_spiir module
python ./python/test/element_make.py cuda_postcoh
python ./python/test/element_make.py cuda_multiratespiir
echo $GST_PLUGIN_PATH
echo $PYTHONPATH
echo $GSTLAL_FIR_WHITEN
echo $GST_DEBUG_DUMP_DOT_DIR
