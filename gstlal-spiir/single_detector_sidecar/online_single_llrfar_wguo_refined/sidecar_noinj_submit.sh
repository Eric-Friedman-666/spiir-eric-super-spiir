#!/usr/bin/env bash
set -euo pipefail
umask 077

die() {
    printf 'SIDECAR_NOINJ_SUBMIT_ERROR: %s\n' "$*" >&2
    exit 2
}

require_uint() {
    local value=$1 label=$2
    case "$value" in
        0|[1-9]|[1-9][0-9]*) ;;
        *) die "$label must be a canonical nonnegative integer" ;;
    esac
}

regular_file() {
    local value=$1 label=$2
    case "$value" in /*) ;; *) die "$label path must be absolute" ;; esac
    [ -f "$value" ] && [ ! -L "$value" ] || die "$label must be a regular non-symlink file"
    readlink -e -- "$value"
}

regular_dir() {
    local value=$1 label=$2
    case "$value" in /*) ;; *) die "$label path must be absolute" ;; esac
    [ -d "$value" ] && [ ! -L "$value" ] || die "$label must be a non-symlink directory"
    readlink -e -- "$value"
}

require_safe_export_value() {
    local value=$1 label=$2
    case "$value" in *','*|*$'\n'*|*$'\r'*) die "$label contains an unsafe Slurm export character" ;; esac
}

RUNTIME_FILES=(
    FORMAL_NOINJECTION_SIDECAR_ENTRYPOINT_V2.txt
    run_noinj_sidecar.sh
    sidecar_noinj_submit.sh
    sidecar_noinj_sbatch.sh
    sidecar_noinj_pipeline.sh
    sidecar_owned_a107.py
    sidecar_noinj_consumer.py
    sidecar_causal_engine.py
    sidecar_segment_provenance.py
    sidecar_shape_source_binding.py
    verification_sidecar_numeric.py
)

hex64() {
    case "$1" in *[!0-9a-f]*|'') return 1 ;; *) [ "${#1}" -eq 64 ] ;; esac
}

verify_runtime_snapshot() {
    local expected_sha=$1 manifest manifest_sum manifest_sha actual_count expected_count name path file_sum file_sha records mode
    hex64 "$expected_sha" || die "runtime manifest export SHA is invalid"
    [ "$(basename -- "$SCRIPT_DIR")" = runtime ] || die "submit is not executing from staged runtime"
    manifest=$(regular_file "$SCRIPT_DIR/expected_manifest.sha256" runtime_manifest)
    [ "$(dirname -- "$manifest")" = "$SCRIPT_DIR" ] || die "runtime manifest escapes staged directory"
    manifest_sum=$(sha256sum "$manifest")
    manifest_sha=${manifest_sum%% *}
    [ "$manifest_sha" = "$expected_sha" ] || die "runtime manifest export SHA mismatch"
    mode=$(stat -c %a "$SCRIPT_DIR")
    [ $((8#$mode & 0222)) -eq 0 ] || die "staged runtime directory is writable"
    records=
    declare -A seen=()
    for name in "${RUNTIME_FILES[@]}"; do
        [ -n "$name" ] && [ "$(basename -- "$name")" = "$name" ] || die "runtime manifest contains an external path"
        [ "${seen[$name]+present}" != present ] || die "runtime manifest contains a duplicate filename"
        seen[$name]=1
        path=$(regular_file "$SCRIPT_DIR/$name" "staged runtime $name")
        [ "$(dirname -- "$path")" = "$SCRIPT_DIR" ] || die "staged runtime file escapes runtime directory"
        mode=$(stat -c %a "$path")
        [ $((8#$mode & 0222)) -eq 0 ] || die "staged runtime file is writable: $name"
        file_sum=$(sha256sum "$path")
        file_sha=${file_sum%% *}
        if [ -z "$records" ]; then records="$file_sha  $name"; else records="$records"$'\n'"$file_sha  $name"; fi
    done
    [ "$(sed -n '$=' "$manifest")" -eq "${#RUNTIME_FILES[@]}" ] || die "runtime manifest line count drift"
    [ "$(sed -n '1,$p' "$manifest")" = "$records" ] || die "runtime manifest names/order/hash drift"
    actual_count=$(find "$SCRIPT_DIR" -mindepth 1 -maxdepth 1 -printf x | wc -c)
    expected_count=$((${#RUNTIME_FILES[@]} + 1))
    [ "$actual_count" -eq "$expected_count" ] || die "staged runtime contains an unexpected path"
    (cd "$SCRIPT_DIR" && sha256sum -c --strict expected_manifest.sha256 >/dev/null) || die "staged runtime checksum verification failed"
}

[ "$#" -eq 1 ] || die "usage: $0 <fresh-sidecar-root/launch.env>"
SCRIPT_DIR=$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CONFIG=$(regular_file "$1" launch.env)
[ "$(basename -- "$CONFIG")" = launch.env ] || die "config filename must be launch.env"

set -a
source "$CONFIG"
set +a

: "${SIDECAR_RUN_ROOT:?SIDECAR_RUN_ROOT required}"
: "${SIDECAR_PROFILE:?SIDECAR_PROFILE required}"
: "${SIDECAR_MODE:?SIDECAR_MODE required}"
: "${SIDECAR_FRAME_CACHE:?SIDECAR_FRAME_CACHE required}"
: "${SIDECAR_SEGMENT_XML:?SIDECAR_SEGMENT_XML required}"
: "${SIDECAR_DETRSP_MAP:?SIDECAR_DETRSP_MAP required}"
: "${SIDECAR_BANK_DIR:?SIDECAR_BANK_DIR required}"
: "${SIDECAR_MULTI_STATS_ROOT:?SIDECAR_MULTI_STATS_ROOT required}"
: "${SIDECAR_WGUO_PICKLE_H1:?SIDECAR_WGUO_PICKLE_H1 required}"
: "${SIDECAR_WGUO_PICKLE_L1:?SIDECAR_WGUO_PICKLE_L1 required}"
: "${SIDECAR_START_GPS:?SIDECAR_START_GPS required}"
: "${SIDECAR_END_GPS:?SIDECAR_END_GPS required}"
: "${SIDECAR_BACKGROUND_WINDOW_SECONDS:?SIDECAR_BACKGROUND_WINDOW_SECONDS required}"
: "${SIDECAR_UPDATE_PERIOD_SECONDS:?SIDECAR_UPDATE_PERIOD_SECONDS required}"
: "${SIDECAR_ZEROLAG_UPDATE_SECONDS:?SIDECAR_ZEROLAG_UPDATE_SECONDS required}"
: "${SIDECAR_WORKER_COUNT:?SIDECAR_WORKER_COUNT required}"
: "${SIDECAR_BANKS_PER_WORKER:?SIDECAR_BANKS_PER_WORKER required}"
: "${SIDECAR_START_BANK:?SIDECAR_START_BANK required}"
: "${SIDECAR_CONTAINER_IMAGE:?SIDECAR_CONTAINER_IMAGE required}"
: "${SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD:?SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD required}"

[ "$SIDECAR_PROFILE" = NOINJECTION_PARITY ] || die "profile must be NOINJECTION_PARITY"
[ "$SIDECAR_MODE" = NO_INJECTION ] || die "mode must be NO_INJECTION"
[ -z "${SIDECAR_INJECTION_FILE:-}" ] || die "injection input is forbidden"
[ "${SIDECAR_ACQUISITION_IFOS:-H1,L1,V1}" = H1,L1,V1 ] || die "acquisition IFOs must be H1,L1,V1"
[ "${SIDECAR_SINGLE_IFOS:-H1,L1}" = H1,L1 ] || die "single IFOs must be H1,L1"
[ "${SIDECAR_FINALSINK_SCHEMA_MODE:-}" = legacy-a107 ] || die "schema must be legacy-a107"


require_uint "$SIDECAR_START_GPS" SIDECAR_START_GPS
require_uint "$SIDECAR_END_GPS" SIDECAR_END_GPS
require_uint "$SIDECAR_BACKGROUND_WINDOW_SECONDS" SIDECAR_BACKGROUND_WINDOW_SECONDS
require_uint "$SIDECAR_UPDATE_PERIOD_SECONDS" SIDECAR_UPDATE_PERIOD_SECONDS
require_uint "$SIDECAR_ZEROLAG_UPDATE_SECONDS" SIDECAR_ZEROLAG_UPDATE_SECONDS
require_uint "$SIDECAR_WORKER_COUNT" SIDECAR_WORKER_COUNT
require_uint "$SIDECAR_BANKS_PER_WORKER" SIDECAR_BANKS_PER_WORKER
require_uint "$SIDECAR_START_BANK" SIDECAR_START_BANK
[ "$SIDECAR_END_GPS" -gt "$SIDECAR_START_GPS" ] || die "GPS interval must be positive"
DURATION=$((SIDECAR_END_GPS - SIDECAR_START_GPS))
[ "$SIDECAR_BACKGROUND_WINDOW_SECONDS" -gt 0 ] || die "background window must be positive"
[ "$SIDECAR_UPDATE_PERIOD_SECONDS" -gt 0 ] || die "update period must be positive"
[ "$SIDECAR_ZEROLAG_UPDATE_SECONDS" -gt 0 ] || die "zerolag update period must be positive"
[ "$SIDECAR_BACKGROUND_WINDOW_SECONDS" -le "$DURATION" ] || die "background window exceeds run duration"
[ "$SIDECAR_WORKER_COUNT" -gt 0 ] || die "worker count must be positive"
[ "$SIDECAR_BANKS_PER_WORKER" -gt 0 ] || die "banks per worker must be positive"
LAST_BANK=$((SIDECAR_START_BANK + SIDECAR_WORKER_COUNT * SIDECAR_BANKS_PER_WORKER - 1))
[ "$LAST_BANK" -lt 384 ] || die "sidecar bank geometry reaches unsupported BBH bank >=384"
[ "$SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD" = -4 ] || die "SNR-series logFAR threshold must match crashcar value -4"

RUN_ROOT=$(regular_dir "$SIDECAR_RUN_ROOT" SIDECAR_RUN_ROOT)
[ "$(dirname -- "$CONFIG")" = "$RUN_ROOT" ] || die "launch.env must be inside SIDECAR_RUN_ROOT"
[ "$(stat -c %u "$RUN_ROOT")" = "$(id -u)" ] || die "sidecar root is not owned by the submitting user"
RUNTIME=$(regular_dir "$RUN_ROOT/runtime" staged_runtime)
[ "$SCRIPT_DIR" = "$RUNTIME" ] || die "submit must execute only from staged runtime"
UNEXPECTED=$(find "$RUN_ROOT" -mindepth 1 -maxdepth 1 ! -name launch.env ! -name runtime -print -quit)
[ -z "$UNEXPECTED" ] || die "sidecar root is not fresh apart from sealed runtime"
: "${SIDECAR_SOURCE_MANIFEST_SHA256:?SIDECAR_SOURCE_MANIFEST_SHA256 required}"
verify_runtime_snapshot "$SIDECAR_SOURCE_MANIFEST_SHA256"

SIDECAR_FRAME_CACHE=$(regular_file "$SIDECAR_FRAME_CACHE" frame_cache)
SIDECAR_SEGMENT_XML=$(regular_file "$SIDECAR_SEGMENT_XML" segment_xml)
SIDECAR_DETRSP_MAP=$(regular_file "$SIDECAR_DETRSP_MAP" detrsp_map)
SIDECAR_MULTI_STATS_ROOT=$(regular_dir "$SIDECAR_MULTI_STATS_ROOT" multi_stats_root)
SIDECAR_WGUO_PICKLE_H1=$(regular_file "$SIDECAR_WGUO_PICKLE_H1" wguo_pickle_h1)
SIDECAR_WGUO_PICKLE_L1=$(regular_file "$SIDECAR_WGUO_PICKLE_L1" wguo_pickle_l1)
SIDECAR_BANK_DIR=$(regular_dir "$SIDECAR_BANK_DIR" bank_dir)
SIDECAR_CONTAINER_IMAGE=$(regular_dir "$SIDECAR_CONTAINER_IMAGE" container_image)
[ "$SIDECAR_CONTAINER_IMAGE" = /fred/oz016/singularity/spiir-base-py3 ] || die "container image must be the proven spiir-base-py3 sandbox"

BANK_PATHS=()
MULTI_STATS_PATHS=()
for ((WORKER_ID=0; WORKER_ID<SIDECAR_WORKER_COUNT; WORKER_ID++)); do
    WORKER_TAG=$(printf '%03d' "$WORKER_ID")
    for SUFFIX in 2w 1d 2h; do
        MULTI_STATS_PATHS+=("$(regular_file "$SIDECAR_MULTI_STATS_ROOT/$WORKER_TAG/${WORKER_TAG}_marginalized_stats_${SUFFIX}.xml.gz" "worker_${WORKER_TAG}_multi_stats_${SUFFIX}")")
    done
    FIRST_BANK=$((SIDECAR_START_BANK + SIDECAR_BANKS_PER_WORKER * WORKER_ID))
    LAST_WORKER_BANK=$((FIRST_BANK + SIDECAR_BANKS_PER_WORKER - 1))
    for ((BANK_ID=FIRST_BANK; BANK_ID<=LAST_WORKER_BANK; BANK_ID++)); do
        BANK_TAG=$(printf '%04d' "$BANK_ID")
        for IFO in H1 L1 V1; do
            BANK_PATHS+=("$(regular_file "$SIDECAR_BANK_DIR/iir_${IFO}-GSTLAL_SPLIT_BANK_${BANK_TAG}-a1-0-0.xml.gz" "${IFO}_bank_${BANK_TAG}")")
        done
    done
done

CONTRACT=$(regular_file "$SCRIPT_DIR/FORMAL_NOINJECTION_SIDECAR_ENTRYPOINT_V2.txt" formal_contract)
LAUNCHER=$(regular_file "$SCRIPT_DIR/run_noinj_sidecar.sh" formal_launcher)
SUBMIT_SELF=$(regular_file "$SCRIPT_DIR/sidecar_noinj_submit.sh" formal_submit)
SBATCH=$(regular_file "$SCRIPT_DIR/sidecar_noinj_sbatch.sh" formal_sbatch)
SOURCE_MANIFEST=$(regular_file "$SCRIPT_DIR/expected_manifest.sha256" runtime_manifest)
HELPER=$(regular_file /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh container_helper)
[ "$(sha256sum "$HELPER" | cut -d' ' -f1)" = 8e6c939f4b24846f08cd06cbeefe9131948770395e9d6e47dda06a1758ddba7c ] || die "container helper identity drift"
[ -d /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/wguo-single-det-py3 ] || die "container build wguo-single-det-py3 is missing"

CONFIG_SHA256=$(sha256sum "$CONFIG" | cut -d' ' -f1)
SOURCE_SHA256=$SIDECAR_SOURCE_MANIFEST_SHA256
SOURCE_RECORDS=$(sed -n '1,$p' "$SOURCE_MANIFEST")
RAW_RECORDS=$(for PATH_ITEM in "$SIDECAR_FRAME_CACHE" "$SIDECAR_SEGMENT_XML" "$SIDECAR_DETRSP_MAP" "${MULTI_STATS_PATHS[@]}" "$SIDECAR_WGUO_PICKLE_H1" "$SIDECAR_WGUO_PICKLE_L1" "${BANK_PATHS[@]}"; do sha256sum "$PATH_ITEM"; done)
RAW_SHA256=$(printf '%s\n' "$RAW_RECORDS" | sha256sum | cut -d' ' -f1)
IMAGE_RECORDS=$(sha256sum "$SIDECAR_CONTAINER_IMAGE/.singularity.d/runscript" "$SIDECAR_CONTAINER_IMAGE/.singularity.d/env/90-environment.sh" "$SIDECAR_CONTAINER_IMAGE/.singularity.d/labels.json")
IMAGE_SHA256=$(printf '%s\n' "$IMAGE_RECORDS" | sha256sum | cut -d' ' -f1)

if [ -n "${SIDECAR_CONFIG_SHA256:-}" ] && [ "$SIDECAR_CONFIG_SHA256" != "$CONFIG_SHA256" ]; then die "inherited config SHA mismatch"; fi
if [ -n "${SIDECAR_RAW_INPUT_MANIFEST_SHA256:-}" ] && [ "$SIDECAR_RAW_INPUT_MANIFEST_SHA256" != "$RAW_SHA256" ]; then die "inherited raw input SHA mismatch"; fi
export SIDECAR_CONFIG_SHA256="$CONFIG_SHA256"
export SIDECAR_SOURCE_MANIFEST_SHA256="$SOURCE_SHA256"
export SIDECAR_RAW_INPUT_MANIFEST_SHA256="$RAW_SHA256"
export SIDECAR_CONTAINER_IMAGE_IDENTITY_SHA256="$IMAGE_SHA256"

for SAFE_VALUE in "$CONFIG" "$RUN_ROOT" "$CONFIG_SHA256" "$SOURCE_SHA256" "$RAW_SHA256" "$IMAGE_SHA256"; do
    require_safe_export_value "$SAFE_VALUE" sbatch_export
done
EXPORTS="ALL,SIDECAR_CONFIG=$CONFIG,SIDECAR_RUN_ROOT=$RUN_ROOT,SIDECAR_CONFIG_SHA256=$CONFIG_SHA256,SIDECAR_SOURCE_MANIFEST_SHA256=$SOURCE_SHA256,SIDECAR_RAW_INPUT_MANIFEST_SHA256=$RAW_SHA256,SIDECAR_CONTAINER_IMAGE_IDENTITY_SHA256=$IMAGE_SHA256,SIDECAR_WORKER_COUNT=$SIDECAR_WORKER_COUNT,SIDECAR_BANKS_PER_WORKER=$SIDECAR_BANKS_PER_WORKER,SIDECAR_START_BANK=$SIDECAR_START_BANK"
SBATCH_ARGV=(
    sbatch --parsable
    --array "0-$((SIDECAR_WORKER_COUNT - 1))"
    --chdir "$RUN_ROOT"
    --job-name sidecar-noinj-parity
    --partition skylake
    --time 01:00:00
    --mem 64G
    --cpus-per-task 4
    --gres gpu:1
    --ntasks 1
    --output "$RUN_ROOT/log/sidecar_%A_%a.out"
    --error "$RUN_ROOT/log/sidecar_%A_%a.err"
    --export "$EXPORTS"
    "$SBATCH"
)

if [ "${SIDECAR_DRY_RUN:-0}" = 1 ]; then
    printf 'SIDECAR_NOINJ_SBATCH_ARGV'
    printf ' %q' "${SBATCH_ARGV[@]}"
    printf '\n'
    for ((ARG_INDEX=0; ARG_INDEX<${#SBATCH_ARGV[@]}; ARG_INDEX++)); do
        printf 'argv[%d]=%s\n' "$ARG_INDEX" "${SBATCH_ARGV[$ARG_INDEX]}"
    done
    exit 0
fi
[ "${SIDECAR_DRY_RUN:-0}" = 0 ] || die "SIDECAR_DRY_RUN must be 0 or 1"

command -v squeue >/dev/null 2>&1 || die "squeue is unavailable"
command -v sbatch >/dev/null 2>&1 || die "sbatch is unavailable"
if ! SQUEUE_OUTPUT=$(squeue -h -u "$(id -un)" -o '%i|%u|%Z'); then
    die "squeue ownership preflight failed"
fi
while IFS='|' read -r EXISTING_JOB EXISTING_USER EXISTING_WORKDIR; do
    [ -n "$EXISTING_JOB" ] || continue
    if [ "$EXISTING_USER" = "$(id -un)" ] && [ "$EXISTING_WORKDIR" = "$RUN_ROOT" ]; then
        die "Slurm job $EXISTING_JOB already owns this sidecar root"
    fi
done <<< "$SQUEUE_OUTPUT"

mkdir -p "$RUN_ROOT/status" "$RUN_ROOT/log" "$RUN_ROOT/acquisition" "$RUN_ROOT/reference" "$RUN_ROOT/provenance"
PROVENANCE="$RUN_ROOT/provenance"
cp -- "$CONFIG" "$PROVENANCE/.launch.env.$$"
chmod 0444 "$PROVENANCE/.launch.env.$$"
mv -- "$PROVENANCE/.launch.env.$$" "$PROVENANCE/launch.env"
printf '%s  %s\n' "$CONFIG_SHA256" "$CONFIG" > "$PROVENANCE/.config.sha256.$$"
cp -- "$SOURCE_MANIFEST" "$PROVENANCE/.source_manifest.sha256.$$"
printf '%s\n' "$RAW_RECORDS" > "$PROVENANCE/.raw_input_manifest.sha256.$$"
printf '%s\n' "$IMAGE_RECORDS" > "$PROVENANCE/.container_image_manifest.sha256.$$"
printf 'container_helper=%s\ncontainer_helper_sha256=%s\ncontainer_build=%s\ncontainer_image=%s\ncontainer_image_identity_sha256=%s\n' "$HELPER" "$(sha256sum "$HELPER" | cut -d' ' -f1)" wguo-single-det-py3 "$SIDECAR_CONTAINER_IMAGE" "$IMAGE_SHA256" > "$PROVENANCE/.container_binding.env.$$"
for FROZEN in config.sha256 source_manifest.sha256 raw_input_manifest.sha256 container_image_manifest.sha256 container_binding.env; do
    chmod 0444 "$PROVENANCE/.$FROZEN.$$"
    mv -- "$PROVENANCE/.$FROZEN.$$" "$PROVENANCE/$FROZEN"
done
cmp -s "$SOURCE_MANIFEST" "$PROVENANCE/source_manifest.sha256" || die "frozen source manifest differs from staged manifest"

if ! JOB_ID=$("${SBATCH_ARGV[@]}"); then
    die "sbatch submission failed"
fi
case "$JOB_ID" in 0|[1-9]|[1-9][0-9]*) ;; *) die "sbatch returned a noncanonical job id" ;; esac
printf 'job_id=%s\nsubmitted_utc=%s\nrun_root=%s\narray=0-%s\n' "$JOB_ID" "$(date -u +%FT%TZ)" "$RUN_ROOT" "$((SIDECAR_WORKER_COUNT - 1))" > "$RUN_ROOT/status/.submission.env.$$"
chmod 0444 "$RUN_ROOT/status/.submission.env.$$"
mv -- "$RUN_ROOT/status/.submission.env.$$" "$RUN_ROOT/status/submission.env"
printf 'SIDECAR_NOINJ_SUBMITTED job_id=%s run_root=%s\n' "$JOB_ID" "$RUN_ROOT"
