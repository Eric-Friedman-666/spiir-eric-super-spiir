#!/usr/bin/env bash
#SBATCH --job-name=sidecar-noinj-parity
#SBATCH --ntasks=1
#SBATCH --time=01:00:00
#SBATCH --mem=64G
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --output=log/sidecar_%A_%a.out
#SBATCH --error=log/sidecar_%A_%a.err

set -euo pipefail
umask 077

die() {
    printf 'SIDECAR_NOINJ_SBATCH_ERROR: %s\n' "$*" >&2
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

hex64() {
    case "$1" in
        *[!0-9a-f]*|'') return 1 ;;
        *) [ "${#1}" -eq 64 ] ;;
    esac
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

verify_runtime_snapshot() {
    local expected_sha=$1 manifest manifest_sum manifest_sha actual_count expected_count name path file_sum file_sha records mode
    hex64 "$expected_sha" || die "runtime manifest export SHA is invalid"
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

: "${SIDECAR_CONFIG:?SIDECAR_CONFIG export required}"
: "${SIDECAR_RUN_ROOT:?SIDECAR_RUN_ROOT export required}"
: "${SIDECAR_CONFIG_SHA256:?SIDECAR_CONFIG_SHA256 export required}"
: "${SIDECAR_SOURCE_MANIFEST_SHA256:?SIDECAR_SOURCE_MANIFEST_SHA256 export required}"
: "${SIDECAR_RAW_INPUT_MANIFEST_SHA256:?SIDECAR_RAW_INPUT_MANIFEST_SHA256 export required}"
: "${SIDECAR_CONTAINER_IMAGE_IDENTITY_SHA256:?SIDECAR_CONTAINER_IMAGE_IDENTITY_SHA256 export required}"
: "${SLURM_ARRAY_TASK_ID:?SLURM_ARRAY_TASK_ID required}"

PINNED_RUN_ROOT=$SIDECAR_RUN_ROOT
PINNED_CONFIG_SHA256=$SIDECAR_CONFIG_SHA256
PINNED_SOURCE_SHA256=$SIDECAR_SOURCE_MANIFEST_SHA256
PINNED_RAW_SHA256=$SIDECAR_RAW_INPUT_MANIFEST_SHA256
PINNED_IMAGE_SHA256=$SIDECAR_CONTAINER_IMAGE_IDENTITY_SHA256
PINNED_WORKER_COUNT=${SIDECAR_WORKER_COUNT:?SIDECAR_WORKER_COUNT export required}
PINNED_BANKS_PER_WORKER=${SIDECAR_BANKS_PER_WORKER:?SIDECAR_BANKS_PER_WORKER export required}
PINNED_START_BANK=${SIDECAR_START_BANK:?SIDECAR_START_BANK export required}

CONFIG=$(regular_file "$SIDECAR_CONFIG" launch.env)
set -a
source "$CONFIG"
set +a

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
: "${SIDECAR_CONTAINER_IMAGE:?SIDECAR_CONTAINER_IMAGE required}"
: "${SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD:?SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD required}"

[ "$SIDECAR_PROFILE" = NOINJECTION_PARITY ] || die "profile must be NOINJECTION_PARITY"
[ "$SIDECAR_MODE" = NO_INJECTION ] || die "mode must be NO_INJECTION"
[ -z "${SIDECAR_INJECTION_FILE:-}" ] || die "injection input is forbidden"
[ "${SIDECAR_ACQUISITION_IFOS:-H1,L1,V1}" = H1,L1,V1 ] || die "acquisition IFOs must be H1,L1,V1"
[ "${SIDECAR_SINGLE_IFOS:-H1,L1}" = H1,L1 ] || die "single IFOs must be H1,L1"
[ "${SIDECAR_FINALSINK_SCHEMA_MODE:-}" = legacy-a107 ] || die "schema must be legacy-a107"
SIDECAR_TAIL_LOG_FAR=$(python3 -     "${tail_log_FAR:-${tai_log_FAR:-${TAIL_LOG_FAR:--2}}}" <<'PY'
import math
import sys
value=float(sys.argv[1])
if not math.isfinite(value) or not value<0.0:
    raise SystemExit("sidecar tail_log_FAR must be finite and strictly negative")
print("{:.17g}".format(value))
PY
) || die "invalid sidecar tail_log_FAR"


for PIN in "$PINNED_CONFIG_SHA256" "$PINNED_SOURCE_SHA256" "$PINNED_RAW_SHA256" "$PINNED_IMAGE_SHA256"; do
    hex64 "$PIN" || die "invalid pinned SHA256"
done
require_uint "$SLURM_ARRAY_TASK_ID" SLURM_ARRAY_TASK_ID
require_uint "$PINNED_WORKER_COUNT" SIDECAR_WORKER_COUNT
require_uint "$PINNED_BANKS_PER_WORKER" SIDECAR_BANKS_PER_WORKER
require_uint "$PINNED_START_BANK" SIDECAR_START_BANK
[ "$PINNED_WORKER_COUNT" -gt 0 ] || die "worker count must be positive"
[ "$PINNED_BANKS_PER_WORKER" -gt 0 ] || die "banks per worker must be positive"
LAST_BANK=$((PINNED_START_BANK + PINNED_WORKER_COUNT * PINNED_BANKS_PER_WORKER - 1))
[ "$LAST_BANK" -lt 384 ] || die "sidecar bank geometry reaches unsupported BBH bank >=384"
[ "$SLURM_ARRAY_TASK_ID" -lt "$PINNED_WORKER_COUNT" ] || die "array task is outside worker geometry"
[ "$SIDECAR_WORKER_COUNT" = "$PINNED_WORKER_COUNT" ] || die "config worker count differs from Slurm pin"
[ "$SIDECAR_BANKS_PER_WORKER" = "$PINNED_BANKS_PER_WORKER" ] || die "config bank span differs from Slurm pin"
[ "$SIDECAR_START_BANK" = "$PINNED_START_BANK" ] || die "config start bank differs from Slurm pin"
require_uint "$SIDECAR_START_GPS" SIDECAR_START_GPS
require_uint "$SIDECAR_END_GPS" SIDECAR_END_GPS
require_uint "$SIDECAR_BACKGROUND_WINDOW_SECONDS" SIDECAR_BACKGROUND_WINDOW_SECONDS
require_uint "$SIDECAR_UPDATE_PERIOD_SECONDS" SIDECAR_UPDATE_PERIOD_SECONDS
require_uint "$SIDECAR_ZEROLAG_UPDATE_SECONDS" SIDECAR_ZEROLAG_UPDATE_SECONDS
[ "$SIDECAR_END_GPS" -gt "$SIDECAR_START_GPS" ] || die "GPS interval must be positive"
DURATION=$((SIDECAR_END_GPS - SIDECAR_START_GPS))
[ "$SIDECAR_BACKGROUND_WINDOW_SECONDS" -gt 0 ] || die "background window must be positive"
[ "$SIDECAR_UPDATE_PERIOD_SECONDS" -gt 0 ] || die "update period must be positive"
[ "$SIDECAR_ZEROLAG_UPDATE_SECONDS" -gt 0 ] || die "zerolag update period must be positive"
[ "$SIDECAR_BACKGROUND_WINDOW_SECONDS" -le "$DURATION" ] || die "background window exceeds run duration"
[ "$SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD" = -4 ] || die "SNR-series logFAR threshold must match crashcar value -4"

RUN_ROOT=$(regular_dir "$SIDECAR_RUN_ROOT" SIDECAR_RUN_ROOT)
[ "$RUN_ROOT" = "$PINNED_RUN_ROOT" ] || die "run root differs from Slurm pin"
[ "$(dirname -- "$CONFIG")" = "$RUN_ROOT" ] || die "launch.env must be inside SIDECAR_RUN_ROOT"
for REQUIRED_DIR in status log acquisition reference provenance; do
    [ -d "$RUN_ROOT/$REQUIRED_DIR" ] && [ ! -L "$RUN_ROOT/$REQUIRED_DIR" ] || die "missing run directory $REQUIRED_DIR"
done

SIDECAR_FRAME_CACHE=$(regular_file "$SIDECAR_FRAME_CACHE" frame_cache)
SIDECAR_SEGMENT_XML=$(regular_file "$SIDECAR_SEGMENT_XML" segment_xml)
SIDECAR_DETRSP_MAP=$(regular_file "$SIDECAR_DETRSP_MAP" detrsp_map)
SIDECAR_MULTI_STATS_ROOT=$(regular_dir "$SIDECAR_MULTI_STATS_ROOT" multi_stats_root)
SIDECAR_WGUO_PICKLE_H1=$(regular_file "$SIDECAR_WGUO_PICKLE_H1" wguo_pickle_h1)
SIDECAR_WGUO_PICKLE_L1=$(regular_file "$SIDECAR_WGUO_PICKLE_L1" wguo_pickle_l1)
SIDECAR_BANK_DIR=$(regular_dir "$SIDECAR_BANK_DIR" bank_dir)
SIDECAR_CONTAINER_IMAGE=$(regular_dir "$SIDECAR_CONTAINER_IMAGE" container_image)
[ "$SIDECAR_CONTAINER_IMAGE" = /fred/oz016/singularity/spiir-base-py3 ] || die "container image must be the proven spiir-base-py3 sandbox"

WORKER_ID=$SLURM_ARRAY_TASK_ID
WORKER_FIRST_BANK=$((PINNED_START_BANK + PINNED_BANKS_PER_WORKER * WORKER_ID))
WORKER_LAST_BANK=$((WORKER_FIRST_BANK + PINNED_BANKS_PER_WORKER - 1))
WORKER_BANK_IDS=
for ((BANK_ID=WORKER_FIRST_BANK; BANK_ID<=WORKER_LAST_BANK; BANK_ID++)); do
    if [ -n "$WORKER_BANK_IDS" ]; then WORKER_BANK_IDS="$WORKER_BANK_IDS,"; fi
    WORKER_BANK_IDS="$WORKER_BANK_IDS$BANK_ID"
done
ALL_BANK_PATHS=()
ALL_MULTI_STATS_PATHS=()
for ((CHECK_WORKER=0; CHECK_WORKER<PINNED_WORKER_COUNT; CHECK_WORKER++)); do
    CHECK_TAG=$(printf '%03d' "$CHECK_WORKER")
    for SUFFIX in 2w 1d 2h; do
        ALL_MULTI_STATS_PATHS+=("$(regular_file "$SIDECAR_MULTI_STATS_ROOT/$CHECK_TAG/${CHECK_TAG}_marginalized_stats_${SUFFIX}.xml.gz" "worker_${CHECK_TAG}_multi_stats_${SUFFIX}")")
    done
    CHECK_FIRST=$((PINNED_START_BANK + PINNED_BANKS_PER_WORKER * CHECK_WORKER))
    CHECK_LAST=$((CHECK_FIRST + PINNED_BANKS_PER_WORKER - 1))
    for ((BANK_ID=CHECK_FIRST; BANK_ID<=CHECK_LAST; BANK_ID++)); do
        BANK_TAG=$(printf '%04d' "$BANK_ID")
        for IFO in H1 L1 V1; do
            ALL_BANK_PATHS+=("$(regular_file "$SIDECAR_BANK_DIR/iir_${IFO}-GSTLAL_SPLIT_BANK_${BANK_TAG}-a1-0-0.xml.gz" "${IFO}_bank_${BANK_TAG}")")
        done
    done
done

SCRIPT_DIR=$(regular_dir "$RUN_ROOT/runtime" staged_runtime)
[ "$SCRIPT_DIR" = "$PINNED_RUN_ROOT/runtime" ] || die "sbatch runtime directory differs from submitted root"
CONTRACT=$(regular_file "$SCRIPT_DIR/FORMAL_NOINJECTION_SIDECAR_ENTRYPOINT_V2.txt" formal_contract)
LAUNCHER=$(regular_file "$SCRIPT_DIR/run_noinj_sidecar.sh" formal_launcher)
SUBMIT=$(regular_file "$SCRIPT_DIR/sidecar_noinj_submit.sh" formal_submit)
SBATCH_SELF=$(regular_file "$SCRIPT_DIR/sidecar_noinj_sbatch.sh" formal_sbatch)
PIPELINE=$(regular_file "$SCRIPT_DIR/sidecar_noinj_pipeline.sh" formal_pipeline)
CONSUMER=$(regular_file "$SCRIPT_DIR/sidecar_noinj_consumer.py" formal_consumer)
OWNED_PARSER=$(regular_file "$SCRIPT_DIR/sidecar_owned_a107.py" owned_a107_parser)
CAUSAL_ENGINE=$(regular_file "$SCRIPT_DIR/sidecar_causal_engine.py" causal_engine)
SEGMENT_BINDING=$(regular_file "$SCRIPT_DIR/sidecar_segment_provenance.py" segment_binding)
SHAPE_BINDING=$(regular_file "$SCRIPT_DIR/sidecar_shape_source_binding.py" shape_binding)
NUMERIC_ADAPTER=$(regular_file "$SCRIPT_DIR/verification_sidecar_numeric.py" numeric_adapter)
RUNTIME_MANIFEST=$(regular_file "$SCRIPT_DIR/expected_manifest.sha256" runtime_manifest)
RUNTIME_SHA256=$PINNED_SOURCE_SHA256
HELPER=$(regular_file /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/bash_helper_functions.sh container_helper)
[ "$(sha256sum "$HELPER" | cut -d' ' -f1)" = 8e6c939f4b24846f08cd06cbeefe9131948770395e9d6e47dda06a1758ddba7c ] || die "container helper identity drift"
[ -d /fred/oz016/gwdc_spiir_pipeline_codebase/scripts_n_things/build/wguo-single-det-py3 ] || die "container build wguo-single-det-py3 is missing"

ACTUAL_CONFIG_SHA256=$(sha256sum "$CONFIG" | cut -d' ' -f1)
[ "$ACTUAL_CONFIG_SHA256" = "$PINNED_CONFIG_SHA256" ] || die "config SHA changed after submission"
RAW_RECORDS=$(for PATH_ITEM in "$SIDECAR_FRAME_CACHE" "$SIDECAR_SEGMENT_XML" "$SIDECAR_DETRSP_MAP" "${ALL_MULTI_STATS_PATHS[@]}" "$SIDECAR_WGUO_PICKLE_H1" "$SIDECAR_WGUO_PICKLE_L1" "${ALL_BANK_PATHS[@]}"; do sha256sum "$PATH_ITEM"; done)
ACTUAL_RAW_SHA256=$(printf '%s\n' "$RAW_RECORDS" | sha256sum | cut -d' ' -f1)
[ "$ACTUAL_RAW_SHA256" = "$PINNED_RAW_SHA256" ] || die "raw input manifest changed after submission"
IMAGE_RECORDS=$(sha256sum "$SIDECAR_CONTAINER_IMAGE/.singularity.d/runscript" "$SIDECAR_CONTAINER_IMAGE/.singularity.d/env/90-environment.sh" "$SIDECAR_CONTAINER_IMAGE/.singularity.d/labels.json")
ACTUAL_IMAGE_SHA256=$(printf '%s\n' "$IMAGE_RECORDS" | sha256sum | cut -d' ' -f1)
[ "$ACTUAL_IMAGE_SHA256" = "$PINNED_IMAGE_SHA256" ] || die "container image identity changed after submission"

PROVENANCE="$RUN_ROOT/provenance"
for FROZEN in launch.env config.sha256 source_manifest.sha256 raw_input_manifest.sha256 container_image_manifest.sha256 container_binding.env; do
    [ -f "$PROVENANCE/$FROZEN" ] && [ ! -L "$PROVENANCE/$FROZEN" ] || die "missing frozen provenance $FROZEN"
done
[ "$(sha256sum "$PROVENANCE/launch.env" | cut -d' ' -f1)" = "$PINNED_CONFIG_SHA256" ] || die "frozen launch.env mismatch"
[ "$(sha256sum "$PROVENANCE/source_manifest.sha256" | cut -d' ' -f1)" = "$PINNED_SOURCE_SHA256" ] || die "frozen source manifest mismatch"
cmp -s "$RUNTIME_MANIFEST" "$PROVENANCE/source_manifest.sha256" || die "frozen source manifest bytes differ from staged manifest"
[ "$(sha256sum "$PROVENANCE/raw_input_manifest.sha256" | cut -d' ' -f1)" = "$PINNED_RAW_SHA256" ] || die "frozen raw manifest mismatch"
[ "$(sha256sum "$PROVENANCE/container_image_manifest.sha256" | cut -d' ' -f1)" = "$PINNED_IMAGE_SHA256" ] || die "frozen image manifest mismatch"


WORKER_TAG=$(printf '%03d' "$WORKER_ID")
WORKER_ENV="$PROVENANCE/worker_${WORKER_TAG}.env"
printf 'host=%s\nstart_utc=%s\nslurm_job_id=%s\nslurm_array_task_id=%s\nworker_id=%s\nworker_count=%s\nbank_ids=%s\nacquisition_ifos=H1,L1,V1\nsingle_ifos=H1,L1\nconfig_sha256=%s\nsource_manifest_sha256=%s\nraw_input_manifest_sha256=%s\ncontainer_image_identity_sha256=%s\ncontainer_helper_sha256=%s\ncontainer_build=wguo-single-det-py3\ncontainer_image=%s\n' "$(hostname)" "$(date -u +%FT%TZ)" "${SLURM_JOB_ID:-manual}" "$SLURM_ARRAY_TASK_ID" "$WORKER_ID" "$PINNED_WORKER_COUNT" "$WORKER_BANK_IDS" "$PINNED_CONFIG_SHA256" "$PINNED_SOURCE_SHA256" "$PINNED_RAW_SHA256" "$PINNED_IMAGE_SHA256" "$(sha256sum "$HELPER" | cut -d' ' -f1)" "$SIDECAR_CONTAINER_IMAGE" > "$PROVENANCE/.worker_${WORKER_TAG}.env.$$"
printf 'runtime_manifest_sha256=%s\nruntime_manifest=%s\n' "$RUNTIME_SHA256" "$RUNTIME_MANIFEST" >> "$PROVENANCE/.worker_${WORKER_TAG}.env.$$"
chmod 0444 "$PROVENANCE/.worker_${WORKER_TAG}.env.$$"
mv -- "$PROVENANCE/.worker_${WORKER_TAG}.env.$$" "$WORKER_ENV"
printf 'state=STARTING\nutc=%s\nworker_id=%s\nbank_ids=%s\n' "$(date -u +%FT%TZ)" "$WORKER_ID" "$WORKER_BANK_IDS" > "$RUN_ROOT/status/worker_${WORKER_TAG}.env"

GST_DEBUG=${GST_DEBUG-}
X509_USER_PROXY=${X509_USER_PROXY-}
X509_USER_KEY=${X509_USER_KEY-}
X509_USER_CERT=${X509_USER_CERT-}
KRB5_KTNAME=${KRB5_KTNAME-}
source "$HELPER"
declare -F run_spiir_py3 >/dev/null || die "proven container helper lacks run_spiir_py3"

verify_runtime_snapshot "$PINNED_SOURCE_SHA256"

run_spiir_py3 \
    -e SIDECAR_CONFIG="$CONFIG" \
    -e SIDECAR_RUN_ROOT="$RUN_ROOT" \
    -e SIDECAR_WORKER_ID="$WORKER_ID" \
    -e SIDECAR_WORKER_COUNT="$PINNED_WORKER_COUNT" \
    -e SIDECAR_BANKS_PER_WORKER="$PINNED_BANKS_PER_WORKER" \
    -e SIDECAR_START_BANK="$PINNED_START_BANK" \
    -e SIDECAR_ACQUISITION_IFOS=H1,L1,V1 \
    -e SIDECAR_SINGLE_IFOS=H1,L1 \
    -e SIDECAR_CONFIG_SHA256="$PINNED_CONFIG_SHA256" \
    -e SIDECAR_SOURCE_MANIFEST_SHA256="$PINNED_SOURCE_SHA256" \
    -e SIDECAR_RAW_INPUT_MANIFEST_SHA256="$PINNED_RAW_SHA256" \
    -e SIDECAR_CONTAINER_IMAGE_IDENTITY_SHA256="$PINNED_IMAGE_SHA256" \
    -e WGUO_O3A_START_GPS="$SIDECAR_START_GPS" \
    -e WGUO_O3A_END_GPS="$SIDECAR_END_GPS" \
    -e WGUO_O3A_FRAME_CACHE="$SIDECAR_FRAME_CACHE" \
    -e WGUO_O3A_DETRSP_MAP="$SIDECAR_DETRSP_MAP" \
    -e WGUO_O3A_BANK_DIR="$SIDECAR_BANK_DIR" \
    -e WGUO_O3A_START_BANK="$PINNED_START_BANK" \
    -e WGUO_O3A_BANKS_PER_GROUP="$PINNED_BANKS_PER_WORKER" \
    -e WGUO_O3A_INJECTION_MODE=none \
    -e WGUO_O3A_INJECTION_FILE= \
    -e CRASHCAR_ENABLE=0 \
    -e SIDECAR_BACKGROUND_WINDOW_SECONDS="$SIDECAR_BACKGROUND_WINDOW_SECONDS" \
    -e SIDECAR_ZEROLAG_UPDATE_SECONDS="$SIDECAR_ZEROLAG_UPDATE_SECONDS" \
    -e SIDECAR_UPDATE_PERIOD_SECONDS="$SIDECAR_UPDATE_PERIOD_SECONDS" \
    -e SIDECAR_FINALSINK_SCHEMA_MODE=legacy-a107 \
    -e SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD="$SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD" \
    wguo-single-det-py3 bash "$PIPELINE" "$CONFIG"

ROSTER="$RUN_ROOT/acquisition/worker_${WORKER_TAG}/a107_roster.tsv"
[ -f "$ROSTER" ] && [ -s "$ROSTER" ] && [ ! -L "$ROSTER" ] || die "completed acquisition did not publish own A107 roster"
verify_runtime_snapshot "$PINNED_SOURCE_SHA256"

REFERENCE_WORKER="$RUN_ROOT/reference/worker_${WORKER_TAG}"
[ ! -e "$REFERENCE_WORKER" ] && [ ! -L "$REFERENCE_WORKER" ] || die "consumer reference root is not fresh"
printf 'state=CONSUMING\nutc=%s\nworker_id=%s\nbank_ids=%s\n' "$(date -u +%FT%TZ)" "$WORKER_ID" "$WORKER_BANK_IDS" > "$RUN_ROOT/status/worker_${WORKER_TAG}.env"

run_spiir_py3 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    wguo-single-det-py3 python3 "$CONSUMER" \
    --run-root "$RUN_ROOT" \
    --worker-id "$WORKER_ID" \
    --worker-count "$PINNED_WORKER_COUNT" \
    --worker-group "$WORKER_ID" \
    --start-bank "$PINNED_START_BANK" \
    --banks-per-worker "$PINNED_BANKS_PER_WORKER" \
    --start-gps "$SIDECAR_START_GPS" \
    --end-gps "$SIDECAR_END_GPS" \
    --background-window-seconds "$SIDECAR_BACKGROUND_WINDOW_SECONDS" \
    --update-period-seconds "$SIDECAR_UPDATE_PERIOD_SECONDS" \
    --tail-log10-far "$SIDECAR_TAIL_LOG_FAR" \
    --segment-xml "$SIDECAR_SEGMENT_XML" \
    --wguo-pickle-h1 "$SIDECAR_WGUO_PICKLE_H1" \
    --wguo-pickle-l1 "$SIDECAR_WGUO_PICKLE_L1" \
    --source-manifest-sha256 "$PINNED_SOURCE_SHA256" \
    --runtime-manifest-sha256 "$RUNTIME_SHA256" \
    --config-sha256 "$PINNED_CONFIG_SHA256" \
    --raw-input-manifest-sha256 "$PINNED_RAW_SHA256"

for OUTPUT_NAME in components.csv summary.json single_background.json status.json; do
    [ -f "$REFERENCE_WORKER/$OUTPUT_NAME" ] && [ -s "$REFERENCE_WORKER/$OUTPUT_NAME" ] && [ ! -L "$REFERENCE_WORKER/$OUTPUT_NAME" ] || die "consumer output missing: $OUTPUT_NAME"
done
grep -q '"state":"COMPLETE"' "$REFERENCE_WORKER/status.json" || die "consumer status is not COMPLETE"

printf 'state=COMPLETE\nutc=%s\nworker_id=%s\nbank_ids=%s\n' "$(date -u +%FT%TZ)" "$WORKER_ID" "$WORKER_BANK_IDS" > "$RUN_ROOT/status/worker_${WORKER_TAG}.env"
