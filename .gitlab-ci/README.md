These scripts are primarily used by the CICD system to automatically build and run the pipeline
on a static set of inputs to compare the results between merge requests.  

A patch file is included to modify finalsink such that coinc.xmls are produced within
a small window of time without having to wait for the FAR values to be updated.  
To apply these run:  
```
git apply .gitlab-ci/patches/force_early_uploads.patch
cd gstlal-spiir
make install
cd ..
```

Once you would like to revert these changes run:  
```
git apply -R .gitlab-ci/patches/force_early_uploads.patch
cd gstlal-spiir
make install
cd ..
```

The entry script to test run the spiir pipeline is `submit_runs.sh` and accepts several
arguments to modify the execution parameters.

Artifacts directory (a):  
    This directory holds all inputs and outputs associated with a SPIIR pipeline run.  
    By default the artifacts directory is `./artifacts`.  

Start time (s):  
    The default start timestamp is 1187006000  

Duration (d):  
    The default duration is 300  

Job ID (j):  
    The Job ID indicates where the runs' results are saved.  
    By default the Job ID is the branch name (or HEAD), concatenated with the current short commit sha.  

Wrapper commands (w):  
    This is used by the gitlab CICD to prepend the SPIIR binary with sanitizer/memcheck wrappers such as `valgrind` or `compute-sanitizer`.  

Using Slurm? (l):  
    If set to 1, each `run_pipeline.sh` call will be `sbatch`'d.  
    At the moment each run will only use one node.  

Python 3 env (p):  
    Set this to the path of an `activate` binary in a python 3 venv setup with at least `ligo.skymap`, `pandas` and `python=3.8`.  
    By default this is set to `/fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/ldavis/venv/bin/activate`  

Generate inputs (g):  
    If set to 1 (the default), the script should generate the appropriate input artifacts if they do not already exist.  
    The inputs dir is suffixed with `/${IFOS_USED}-${START}-${DURATION}` as a pseudo-hash of the only inputs to generation at the moment.  
    These inputs are generated using `generate_pipeline_artifacts.sh` and include;  
        An injection xml: `fake_inj.xml`  
        Frames with the above injections: `fake_frames_inj/`  
        A cache file with the above frames: `fake-frames.cache`  
        A reference PSD file based on the above frames: `reference_PSD.xml.gz`  
        A response map: `detrsp_map.xml`  
        A probability map: `prob_map.xml`  
        Some pipe.txt files if using slurm to generate the inputs.  

Run pipeline on all IFO combinations (i):  
    If set to 1 (default is 0), the script will run the pipeline on all IFO combinations instead of just a single test combination.  

Compare pipeline output with a control (c):
    Set this to a short commit SHA or branch name to compare this run with the last run pipeline that matches the chosen string.  
    By default this is set to `spiir-O4-EW-development`.

Use Tmux (x):  
    If set to 1 (defualt is 0), the script will run Tmux and tail the .out and .err logs as they are produced.  

Skip confirmation check (y):  
    If set, the script will skip a comfirmation check and assume all input arguments are correct.  

The only artifacts not able to be generated at the moment are the template bank files.  
These have to be copied over into the parent `./artifacts` directory from here:  
`/fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/ldavis/source/artifacts/iir_*`  
The pipeline is then run for each combination of IFOs.  
The script should output the full commands it uses to run the pipeline, and start a tmux session tailing the stdout and stderr of the currently running pipeline (if not using slurm).  
Once complete the output folder structure should look like this:  
```
${ARTIFACTS_DIR}/  
    iir_H1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz  
    iir_L1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz  
    iir_V1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz  
    iir_K1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz  
    ${INPUT_IFOS}-${START}-${DUR}/  
        fake_inj.xml  
        fake_frames.cache  
        fake_frames_inj/  
            <generated input frames>  
        detrsp_map.xml  
        prob_map.xml  
        pipe-gen.err.txt  
        pipe-gen.out.txt  
        pipe.err.txt  
        pipe.out.txt  
        ${JOB_ID}/  
            <{HLVK_H1L1V1K1}>/  
                {H1L1V1K1}_skymap/  
                trigger_control.txt  
                <{H1L1V1K1}_{}_{}_{}.xml coinc files>  
                <{H1L1V1K1}_{}_{}_{}.xml_out_bayestar.png skymaps>  
                logs/  
                    pipe_{}.err.txt  
                    pipe_{}.out.txt  
                ${NODE_ID}/  
                    bank0003_stats_{}_{}.xml.gz  
                    finalsink_debug_log  
                    {H1L1V1K1}_SEGMENTS_{}_{}.xml.gz  
                    zerolag_{}_{}.xml.gz  
```

A python 2 and python 3 dockerfile is available that should be able to run the pipeline on any architecture that supports docker, as long as a GPU is passed into it.  
GPU usage requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) to be installed.  
The command for building the docker container is:  
`DOCKER_BUILDKIT=1 docker build -t spiir:<ANY_TAG_HERE> -f .gitlab-ci/Dockerfile --progress=plain --build-arg BUILDKIT_INLINE_CACHE=1 .`  
Once the image is built and you have an ./artifacts directory containing the split bank files, you can run the container with the artifacts folder mounted:  
`docker run --gpus all -it --mount=type=bind,src=<ARTIFACTS DIR PATH>,dst=/repo/artifacts --rm --entrypoint /repo/.gitlab-ci/submit_runs.sh spiir:<ANY_TAG_HERE>`  