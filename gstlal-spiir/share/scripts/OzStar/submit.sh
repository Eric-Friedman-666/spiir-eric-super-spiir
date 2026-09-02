#!/bin/bash
#SBATCH --job-name=ck2-multi_det-BNS
#SBATCH --ntasks=1
#SBATCH --time=168:00:00
#SBATCH --mem=14g
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --array=0-1
#SBATCH --requeue
#SBATCH -o logs/pipe_%A_%a.out # File to which STDOUT will be written
#SBATCH -e logs/pipe_%A_%a.err # File to which STDERR will be written

source /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh
# run_spiir -e SLURM_ARRAY_TASK_ID=$SLURM_ARRAY_TASK_ID schoudhary_jan_2024 bash pipeline.sh
# run_spiir -e SLURM_ARRAY_TASK_ID=$SLURM_ARRAY_TASK_ID manoj__spiir-O4-EW-development bash pipeline.sh
run_spiir_py3 -e SLURM_ARRAY_TASK_ID=$SLURM_ARRAY_TASK_ID Eric-crashcar-mdc bash pipeline.sh
