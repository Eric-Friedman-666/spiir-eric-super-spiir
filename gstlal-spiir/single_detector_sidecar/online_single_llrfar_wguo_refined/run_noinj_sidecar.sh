#!/usr/bin/env bash
set -euo pipefail
umask 077

die() {
    printf 'SIDECAR_NOINJ_LAUNCH_ERROR: %s\n' "$*" >&2
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
: "${SIDECAR_H1_STRAIN_CHANNEL:?SIDECAR_H1_STRAIN_CHANNEL required}"
: "${SIDECAR_L1_STRAIN_CHANNEL:?SIDECAR_L1_STRAIN_CHANNEL required}"
: "${SIDECAR_V1_STRAIN_CHANNEL:?SIDECAR_V1_STRAIN_CHANNEL required}"
: "${SIDECAR_H1_STATE_CHANNEL:?SIDECAR_H1_STATE_CHANNEL required}"
: "${SIDECAR_L1_STATE_CHANNEL:?SIDECAR_L1_STATE_CHANNEL required}"
: "${SIDECAR_V1_STATE_CHANNEL:?SIDECAR_V1_STATE_CHANNEL required}"
: "${SIDECAR_FINALSINK_SCHEMA_MODE:?SIDECAR_FINALSINK_SCHEMA_MODE required}"
: "${SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD:?SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD required}"

[ "$SIDECAR_PROFILE" = NOINJECTION_PARITY ] || die "profile must be NOINJECTION_PARITY"
[ "$SIDECAR_MODE" = NO_INJECTION ] || die "mode must be NO_INJECTION"
[ -z "${SIDECAR_INJECTION_FILE:-}" ] || die "injection input is forbidden"
[ "${SIDECAR_ACQUISITION_IFOS:-H1,L1,V1}" = H1,L1,V1 ] || die "acquisition IFOs must be H1,L1,V1"
[ "${SIDECAR_SINGLE_IFOS:-H1,L1}" = H1,L1 ] || die "single IFOs must be H1,L1"
[ "$SIDECAR_FINALSINK_SCHEMA_MODE" = legacy-a107 ] || die "schema must be legacy-a107"

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

[ "$SIDECAR_H1_STRAIN_CHANNEL" = GDS-CALIB_STRAIN_CLEAN ] || die "H1 strain channel drift"
[ "$SIDECAR_L1_STRAIN_CHANNEL" = GDS-CALIB_STRAIN_CLEAN ] || die "L1 strain channel drift"
[ "$SIDECAR_V1_STRAIN_CHANNEL" = Hrec_hoft_16384Hz ] || die "V1 strain channel drift"
[ "$SIDECAR_H1_STATE_CHANNEL" = GDS-CALIB_STATE_VECTOR ] || die "H1 state channel drift"
[ "$SIDECAR_L1_STATE_CHANNEL" = GDS-CALIB_STATE_VECTOR ] || die "L1 state channel drift"
[ "$SIDECAR_V1_STATE_CHANNEL" = DQ_ANALYSIS_STATE_VECTOR ] || die "V1 state channel drift"

RUN_ROOT=$(regular_dir "$SIDECAR_RUN_ROOT" SIDECAR_RUN_ROOT)
[ "$(dirname -- "$CONFIG")" = "$RUN_ROOT" ] || die "launch.env must be inside SIDECAR_RUN_ROOT"
UNEXPECTED=$(find "$RUN_ROOT" -mindepth 1 -maxdepth 1 ! -name launch.env -print -quit)
[ -z "$UNEXPECTED" ] || die "sidecar root is not fresh"

SIDECAR_FRAME_CACHE=$(regular_file "$SIDECAR_FRAME_CACHE" frame_cache)
SIDECAR_SEGMENT_XML=$(regular_file "$SIDECAR_SEGMENT_XML" segment_xml)
SIDECAR_DETRSP_MAP=$(regular_file "$SIDECAR_DETRSP_MAP" detrsp_map)
SIDECAR_MULTI_STATS_ROOT=$(regular_dir "$SIDECAR_MULTI_STATS_ROOT" multi_stats_root)
SIDECAR_WGUO_PICKLE_H1=$(regular_file "$SIDECAR_WGUO_PICKLE_H1" wguo_pickle_h1)
SIDECAR_WGUO_PICKLE_L1=$(regular_file "$SIDECAR_WGUO_PICKLE_L1" wguo_pickle_l1)
SIDECAR_BANK_DIR=$(regular_dir "$SIDECAR_BANK_DIR" bank_dir)

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

SOURCE_PATHS=()
SOURCE_DIGESTS=()
SOURCE_RECORDS=
declare -A RUNTIME_SEEN=()
for RUNTIME_NAME in "${RUNTIME_FILES[@]}"; do
    [ -n "$RUNTIME_NAME" ] && [ "$RUNTIME_NAME" != . ] && [ "$RUNTIME_NAME" != .. ] && [ "$(basename -- "$RUNTIME_NAME")" = "$RUNTIME_NAME" ] || die "runtime closure contains an external path"
    [ "${RUNTIME_SEEN[$RUNTIME_NAME]+present}" != present ] || die "runtime closure contains a duplicate filename"
    RUNTIME_SEEN[$RUNTIME_NAME]=1
    SOURCE_PATH=$(regular_file "$SCRIPT_DIR/$RUNTIME_NAME" "runtime source $RUNTIME_NAME")
    [ "$(dirname -- "$SOURCE_PATH")" = "$SCRIPT_DIR" ] || die "runtime source escapes production script directory"
    SOURCE_SUM=$(sha256sum "$SOURCE_PATH")
    SOURCE_DIGEST=${SOURCE_SUM%% *}
    SOURCE_PATHS+=("$SOURCE_PATH")
    SOURCE_DIGESTS+=("$SOURCE_DIGEST")
    if [ -z "$SOURCE_RECORDS" ]; then
        SOURCE_RECORDS="$SOURCE_DIGEST  $RUNTIME_NAME"
    else
        SOURCE_RECORDS="$SOURCE_RECORDS"$'\n'"$SOURCE_DIGEST  $RUNTIME_NAME"
    fi
done

CONFIG_SUM=$(sha256sum "$CONFIG")
CONFIG_SHA256=${CONFIG_SUM%% *}
SOURCE_SUM=$(printf '%s\n' "$SOURCE_RECORDS" | sha256sum)
SOURCE_SHA256=${SOURCE_SUM%% *}
RAW_SUM=$(for PATH_ITEM in "$SIDECAR_FRAME_CACHE" "$SIDECAR_SEGMENT_XML" "$SIDECAR_DETRSP_MAP" "${MULTI_STATS_PATHS[@]}" "$SIDECAR_WGUO_PICKLE_H1" "$SIDECAR_WGUO_PICKLE_L1" "${BANK_PATHS[@]}"; do sha256sum "$PATH_ITEM"; done | sha256sum)
RAW_SHA256=${RAW_SUM%% *}

export SIDECAR_RUN_ROOT="$RUN_ROOT"
export SIDECAR_ACQUISITION_IFOS=H1,L1,V1
export SIDECAR_SINGLE_IFOS=H1,L1
export SIDECAR_CONFIG_SHA256="$CONFIG_SHA256"
export SIDECAR_SOURCE_MANIFEST_SHA256="$SOURCE_SHA256"
export SIDECAR_RAW_INPUT_MANIFEST_SHA256="$RAW_SHA256"

if [ "${SIDECAR_DRY_RUN:-0}" = 1 ]; then
    printf 'SIDECAR_NOINJ_DRY_RUN\n'
    printf 'profile=%s\n' "$SIDECAR_PROFILE"
    printf 'run_root=%s\n' "$RUN_ROOT"
    printf 'acquisition_ifos=%s\n' "$SIDECAR_ACQUISITION_IFOS"
    printf 'single_ifos=%s\n' "$SIDECAR_SINGLE_IFOS"
    printf 'duration_seconds=%s\n' "$DURATION"
    printf 'background_window_seconds=%s\n' "$SIDECAR_BACKGROUND_WINDOW_SECONDS"
    printf 'update_period_seconds=%s\n' "$SIDECAR_UPDATE_PERIOD_SECONDS"
    printf 'zerolag_update_seconds=%s\n' "$SIDECAR_ZEROLAG_UPDATE_SECONDS"
    printf 'worker_count=%s\n' "$SIDECAR_WORKER_COUNT"
    printf 'banks_per_worker=%s\n' "$SIDECAR_BANKS_PER_WORKER"
    printf 'start_bank=%s\n' "$SIDECAR_START_BANK"
    printf 'config_sha256=%s\n' "$CONFIG_SHA256"
    printf 'source_manifest_sha256=%s\n' "$SOURCE_SHA256"
    printf 'runtime_file_count=%s\n' "${#RUNTIME_FILES[@]}"
    printf 'raw_input_manifest_sha256=%s\n' "$RAW_SHA256"
    printf 'planned_exec=%s\n' "$RUN_ROOT/runtime/sidecar_noinj_submit.sh"
    exit 0
fi
[ "${SIDECAR_DRY_RUN:-0}" = 0 ] || die "SIDECAR_DRY_RUN must be 0 or 1"

RUNTIME="$RUN_ROOT/runtime"
[ ! -e "$RUNTIME" ] && [ ! -L "$RUNTIME" ] || die "sidecar runtime snapshot is not fresh"
mkdir -m 0700 "$RUNTIME"
for INDEX in "${!RUNTIME_FILES[@]}"; do
    RUNTIME_NAME=${RUNTIME_FILES[$INDEX]}
    SOURCE_PATH=${SOURCE_PATHS[$INDEX]}
    EXPECTED_DIGEST=${SOURCE_DIGESTS[$INDEX]}
    STAGED_TMP="$RUNTIME/.$RUNTIME_NAME.$$"
    cp --no-dereference -- "$SOURCE_PATH" "$STAGED_TMP"
    [ -f "$STAGED_TMP" ] && [ ! -L "$STAGED_TMP" ] || die "staged runtime file is not a regular non-symlink file"
    SOURCE_POST=$(sha256sum "$SOURCE_PATH")
    SOURCE_POST=${SOURCE_POST%% *}
    [ "$SOURCE_POST" = "$EXPECTED_DIGEST" ] || die "production runtime source mutated during staging: $RUNTIME_NAME"
    STAGED_DIGEST=$(sha256sum "$STAGED_TMP")
    STAGED_DIGEST=${STAGED_DIGEST%% *}
    [ "$STAGED_DIGEST" = "$EXPECTED_DIGEST" ] || die "staged runtime file differs from production source: $RUNTIME_NAME"
    case "$RUNTIME_NAME" in
        *.sh|*.py) chmod 0555 "$STAGED_TMP" ;;
        *) chmod 0444 "$STAGED_TMP" ;;
    esac
    mv -- "$STAGED_TMP" "$RUNTIME/$RUNTIME_NAME"
done

for INDEX in "${!RUNTIME_FILES[@]}"; do
    SOURCE_POST=$(sha256sum "${SOURCE_PATHS[$INDEX]}")
    SOURCE_POST=${SOURCE_POST%% *}
    [ "$SOURCE_POST" = "${SOURCE_DIGESTS[$INDEX]}" ] || die "production runtime closure mutated before seal: ${RUNTIME_FILES[$INDEX]}"
done

MANIFEST_TMP="$RUNTIME/.expected_manifest.sha256.$$"
printf '%s\n' "$SOURCE_RECORDS" > "$MANIFEST_TMP"
chmod 0444 "$MANIFEST_TMP"
mv -- "$MANIFEST_TMP" "$RUNTIME/expected_manifest.sha256"
ACTUAL_COUNT=$(find "$RUNTIME" -mindepth 1 -maxdepth 1 -printf x | wc -c)
EXPECTED_COUNT=$((${#RUNTIME_FILES[@]} + 1))
[ "$ACTUAL_COUNT" -eq "$EXPECTED_COUNT" ] || die "staged runtime closure contains an unexpected path"
for INDEX in "${!RUNTIME_FILES[@]}"; do
    STAGED="$RUNTIME/${RUNTIME_FILES[$INDEX]}"
    [ -f "$STAGED" ] && [ ! -L "$STAGED" ] || die "sealed runtime file type drift"
    STAGED_DIGEST=$(sha256sum "$STAGED")
    STAGED_DIGEST=${STAGED_DIGEST%% *}
    [ "$STAGED_DIGEST" = "${SOURCE_DIGESTS[$INDEX]}" ] || die "sealed runtime file hash drift"
done
MANIFEST_SUM=$(sha256sum "$RUNTIME/expected_manifest.sha256")
MANIFEST_SHA256=${MANIFEST_SUM%% *}
[ "$MANIFEST_SHA256" = "$SOURCE_SHA256" ] || die "staged runtime manifest SHA drift"
(cd "$RUNTIME" && sha256sum -c --strict expected_manifest.sha256 >/dev/null) || die "staged runtime manifest verification failed"
chmod 0555 "$RUNTIME"

exec bash "$RUNTIME/sidecar_noinj_submit.sh" "$CONFIG"
