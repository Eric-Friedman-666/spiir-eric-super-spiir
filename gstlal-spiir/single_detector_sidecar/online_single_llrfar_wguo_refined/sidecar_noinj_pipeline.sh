#!/usr/bin/env bash
set -euo pipefail
umask 077

die() {
    printf 'SIDECAR_NOINJ_PIPELINE_ERROR: %s\n' "$*" >&2
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

[ "$#" -eq 1 ] || die "usage: $0 <sidecar-root/launch.env>"
CONFIG=$(regular_file "$1" launch.env)
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
: "${SIDECAR_START_GPS:?SIDECAR_START_GPS required}"
: "${SIDECAR_END_GPS:?SIDECAR_END_GPS required}"
: "${SIDECAR_BACKGROUND_WINDOW_SECONDS:?SIDECAR_BACKGROUND_WINDOW_SECONDS required}"
: "${SIDECAR_UPDATE_PERIOD_SECONDS:?SIDECAR_UPDATE_PERIOD_SECONDS required}"
: "${SIDECAR_ZEROLAG_UPDATE_SECONDS:?SIDECAR_ZEROLAG_UPDATE_SECONDS required}"
: "${SIDECAR_WORKER_COUNT:?SIDECAR_WORKER_COUNT required}"
: "${SIDECAR_BANKS_PER_WORKER:?SIDECAR_BANKS_PER_WORKER required}"
: "${SIDECAR_START_BANK:?SIDECAR_START_BANK required}"
: "${SIDECAR_WORKER_ID:?SIDECAR_WORKER_ID required}"
: "${SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD:?SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD required}"

[ "$SIDECAR_PROFILE" = NOINJECTION_PARITY ] || die "profile must be NOINJECTION_PARITY"
[ "$SIDECAR_MODE" = NO_INJECTION ] || die "mode must be NO_INJECTION"
[ -z "${SIDECAR_INJECTION_FILE:-}" ] || die "injection input is forbidden"
[ "${WGUO_O3A_INJECTION_MODE:-none}" = none ] || die "WGuo injection mode must be none"
[ -z "${WGUO_O3A_INJECTION_FILE:-}" ] || die "WGuo injection file is forbidden"
[ "${CRASHCAR_ENABLE:-0}" = 0 ] || die "CRASHCAR_ENABLE must be 0"
[ "${SIDECAR_ACQUISITION_IFOS:-H1,L1,V1}" = H1,L1,V1 ] || die "acquisition IFOs must be H1,L1,V1"
[ "${SIDECAR_SINGLE_IFOS:-H1,L1}" = H1,L1 ] || die "single IFOs must be H1,L1"
[ "${SIDECAR_FINALSINK_SCHEMA_MODE:-}" = legacy-a107 ] || die "schema must be legacy-a107"

[ "${SIDECAR_H1_STRAIN_CHANNEL:-}" = GDS-CALIB_STRAIN_CLEAN ] || die "H1 strain channel drift"
[ "${SIDECAR_L1_STRAIN_CHANNEL:-}" = GDS-CALIB_STRAIN_CLEAN ] || die "L1 strain channel drift"
[ "${SIDECAR_V1_STRAIN_CHANNEL:-}" = Hrec_hoft_16384Hz ] || die "V1 strain channel drift"
[ "${SIDECAR_H1_STATE_CHANNEL:-}" = GDS-CALIB_STATE_VECTOR ] || die "H1 state channel drift"
[ "${SIDECAR_L1_STATE_CHANNEL:-}" = GDS-CALIB_STATE_VECTOR ] || die "L1 state channel drift"
[ "${SIDECAR_V1_STATE_CHANNEL:-}" = DQ_ANALYSIS_STATE_VECTOR ] || die "V1 state channel drift"

for ITEM in "$SIDECAR_START_GPS" "$SIDECAR_END_GPS" "$SIDECAR_BACKGROUND_WINDOW_SECONDS" "$SIDECAR_UPDATE_PERIOD_SECONDS" "$SIDECAR_ZEROLAG_UPDATE_SECONDS" "$SIDECAR_WORKER_COUNT" "$SIDECAR_BANKS_PER_WORKER" "$SIDECAR_START_BANK" "$SIDECAR_WORKER_ID"; do
    require_uint "$ITEM" sidecar_integer
done
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
[ "$SIDECAR_WORKER_ID" -lt "$SIDECAR_WORKER_COUNT" ] || die "worker id is outside geometry"
[ "$SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD" = -4 ] || die "SNR-series logFAR threshold must match crashcar value -4"

RUN_ROOT=$(regular_dir "$SIDECAR_RUN_ROOT" SIDECAR_RUN_ROOT)
[ "$(dirname -- "$CONFIG")" = "$RUN_ROOT" ] || die "launch.env must be inside SIDECAR_RUN_ROOT"
for REQUIRED_DIR in status log acquisition reference provenance; do
    [ -d "$RUN_ROOT/$REQUIRED_DIR" ] && [ ! -L "$RUN_ROOT/$REQUIRED_DIR" ] || die "missing run directory $REQUIRED_DIR"
done
SIDECAR_FRAME_CACHE=$(regular_file "$SIDECAR_FRAME_CACHE" frame_cache)
SIDECAR_SEGMENT_XML=$(regular_file "$SIDECAR_SEGMENT_XML" segment_xml)
SIDECAR_DETRSP_MAP=$(regular_file "$SIDECAR_DETRSP_MAP" detrsp_map)
SIDECAR_MULTI_STATS_ROOT=$(regular_dir "$SIDECAR_MULTI_STATS_ROOT" multi_stats_root)
SIDECAR_BANK_DIR=$(regular_dir "$SIDECAR_BANK_DIR" bank_dir)

WORKER_FIRST_BANK=$((SIDECAR_START_BANK + SIDECAR_BANKS_PER_WORKER * SIDECAR_WORKER_ID))
WORKER_LAST_BANK=$((WORKER_FIRST_BANK + SIDECAR_BANKS_PER_WORKER - 1))
WORKER_TAG=$(printf '%03d' "$SIDECAR_WORKER_ID")
WORKER_ROOT="$RUN_ROOT/acquisition/worker_${WORKER_TAG}"
OUTPUT_DIR="$WORKER_ROOT/$WORKER_TAG"
ROSTER="$WORKER_ROOT/a107_roster.tsv"

IIR_BANK_ARGS=()
ACCUM_BACKGROUND_ARGS=()
WORKER_BANK_IDS=
for ((BANK_ID=WORKER_FIRST_BANK; BANK_ID<=WORKER_LAST_BANK; BANK_ID++)); do
    BANK_TAG=$(printf '%04d' "$BANK_ID")
    H1_BANK=$(regular_file "$SIDECAR_BANK_DIR/iir_H1-GSTLAL_SPLIT_BANK_${BANK_TAG}-a1-0-0.xml.gz" "H1_worker_bank_${BANK_TAG}")
    L1_BANK=$(regular_file "$SIDECAR_BANK_DIR/iir_L1-GSTLAL_SPLIT_BANK_${BANK_TAG}-a1-0-0.xml.gz" "L1_worker_bank_${BANK_TAG}")
    V1_BANK=$(regular_file "$SIDECAR_BANK_DIR/iir_V1-GSTLAL_SPLIT_BANK_${BANK_TAG}-a1-0-0.xml.gz" "V1_worker_bank_${BANK_TAG}")
    IIR_BANK_ARGS+=(--iir-bank "H1:$H1_BANK,L1:$L1_BANK,V1:$V1_BANK")
    ACCUM_BACKGROUND_ARGS+=(--cohfar-accumbackground-output-prefix "$OUTPUT_DIR/bank${BANK_TAG}_stats")
    if [ -n "$WORKER_BANK_IDS" ]; then WORKER_BANK_IDS="$WORKER_BANK_IDS,"; fi
    WORKER_BANK_IDS="$WORKER_BANK_IDS$BANK_ID"
done
SIDECAR_MULTI_STATS_2W=$(regular_file "$SIDECAR_MULTI_STATS_ROOT/$WORKER_TAG/${WORKER_TAG}_marginalized_stats_2w.xml.gz" worker_multi_stats_2w)
SIDECAR_MULTI_STATS_1D=$(regular_file "$SIDECAR_MULTI_STATS_ROOT/$WORKER_TAG/${WORKER_TAG}_marginalized_stats_1d.xml.gz" worker_multi_stats_1d)
SIDECAR_MULTI_STATS_2H=$(regular_file "$SIDECAR_MULTI_STATS_ROOT/$WORKER_TAG/${WORKER_TAG}_marginalized_stats_2h.xml.gz" worker_multi_stats_2h)
GSTLAL_ONLINE=$(command -v gstlal_inspiral_postcohspiir_online || true)
[ -n "$GSTLAL_ONLINE" ] || die "gstlal_inspiral_postcohspiir_online is unavailable"
GSTLAL_ONLINE=$(regular_file "$GSTLAL_ONLINE" gstlal_online)

MULTI_INPUT="$SIDECAR_MULTI_STATS_2W,$SIDECAR_MULTI_STATS_1D,$SIDECAR_MULTI_STATS_2H"
LOCAL_STATS="$OUTPUT_DIR/${WORKER_TAG}_marginalized_stats_2w.xml.gz,$OUTPUT_DIR/${WORKER_TAG}_marginalized_stats_1d.xml.gz,$OUTPUT_DIR/${WORKER_TAG}_marginalized_stats_2h.xml.gz"
CMD=(
    "$GSTLAL_ONLINE"
    --state-channel-name H1=GDS-CALIB_STATE_VECTOR
    --state-channel-name L1=GDS-CALIB_STATE_VECTOR
    --state-channel-name V1=DQ_ANALYSIS_STATE_VECTOR
    --state-vector-on-bits H1=3
    --state-vector-on-bits L1=3
    --state-vector-on-bits V1=1027
    --state-vector-off-bits H1=0
    --state-vector-off-bits L1=0
    --state-vector-off-bits V1=0
    --job-tag "$WORKER_TAG"
    --tmp-space _CONDOR_SCRATCH_DIR
    "${IIR_BANK_ARGS[@]}"
    --data-source frames
    --channel-name H1=GDS-CALIB_STRAIN_CLEAN
    --channel-name L1=GDS-CALIB_STRAIN_CLEAN
    --channel-name V1=Hrec_hoft_16384Hz
    --gpu-acc on
    --ht-gate-threshold 15.0
    --cuda-postcoh-snglsnr-thresh 4
    --cuda-postcoh-hist-trials 100
    --cuda-postcoh-detrsp-fname "$SIDECAR_DETRSP_MAP"
    --cuda-postcoh-output-skymap 100
    --check-time-stamp
    --finalsink-output-prefix "$OUTPUT_DIR/${WORKER_TAG}_zerolag"
    --finalsink-snapshot-interval "$SIDECAR_ZEROLAG_UPDATE_SECONDS"
    --cohfar-accumbackground-snapshot-interval "$SIDECAR_UPDATE_PERIOD_SECONDS"
    "${ACCUM_BACKGROUND_ARGS[@]}"
    --cohfar-assignfar-input-fname "$MULTI_INPUT"
    --cohfar-assignfar-silent-time 0
    --cohfar-assignfar-refresh-interval "$SIDECAR_UPDATE_PERIOD_SECONDS"
    --finalsink-fapupdater-interval "$SIDECAR_UPDATE_PERIOD_SECONDS"
    --finalsink-fapupdater-collect-walltime "$SIDECAR_BACKGROUND_WINDOW_SECONDS,$SIDECAR_BACKGROUND_WINDOW_SECONDS,$SIDECAR_BACKGROUND_WINDOW_SECONDS"
    --finalsink-far-factor 25
    --finalsink-gracedb-far-thresh 0
    --finalsink-need-online-perform 0
    --finalsink-gracedb-group Test
    --finalsink-gracedb-search MDC
    --finalsink-gracedb-service-url https://gracedb-playground.ligo.org/api/
    --cuda-postcoh-detrsp-refresh-interval 86400
    --code-version spiir-sidecar-reference
    --frame-cache "$SIDECAR_FRAME_CACHE"
    --gps-start-time "$SIDECAR_START_GPS"
    --gps-end-time "$SIDECAR_END_GPS"
    --finalsink-singlefar-veto-thresh 0.5
    --track-psd
    --psd-fft-length 4
    --finalsink-fapupdater-output-fname "$LOCAL_STATS"
)

if [ "${SIDECAR_DRY_RUN:-0}" = 1 ]; then
    printf 'SIDECAR_NOINJ_ACQUISITION_ARGV'
    printf ' %q' "${CMD[@]}"
    printf '\n'
    for ((ARG_INDEX=0; ARG_INDEX<${#CMD[@]}; ARG_INDEX++)); do
        printf 'argv[%d]=%s\n' "$ARG_INDEX" "${CMD[$ARG_INDEX]}"
    done
    printf 'worker_root=%s\noutput_dir=%s\nroster_path=%s\nroster_glob=%s\nsegment_xml=%s\ncrashcar_enable=0\n' "$WORKER_ROOT" "$OUTPUT_DIR" "$ROSTER" "${WORKER_TAG}_zerolag_*.xml*" "$SIDECAR_SEGMENT_XML"
    exit 0
fi
[ "${SIDECAR_DRY_RUN:-0}" = 0 ] || die "SIDECAR_DRY_RUN must be 0 or 1"
[ ! -e "$WORKER_ROOT" ] || { [ -d "$WORKER_ROOT" ] && [ ! -L "$WORKER_ROOT" ]; } || die "worker root conflicts with a non-directory or symlink"
mkdir -p "$OUTPUT_DIR"
WORKER_ROOT=$(regular_dir "$WORKER_ROOT" worker_root)
OUTPUT_DIR=$(regular_dir "$OUTPUT_DIR" output_dir)
case "$OUTPUT_DIR" in "$WORKER_ROOT"/*) ;; *) die "output directory escapes worker root" ;; esac
[ ! -e "$ROSTER" ] || die "a107 roster already exists"

COMMAND_LOG="$WORKER_ROOT/acquisition_command.txt"
{
    printf 'SIDECAR_NOINJ_ACQUISITION_ARGV'
    printf ' %q' "${CMD[@]}"
    printf '\nsegment_xml=%s\nCRASHCAR_ENABLE=0\nschema=legacy-a107\nbank_ids=%s\n' "$SIDECAR_SEGMENT_XML" "$WORKER_BANK_IDS"
} > "$COMMAND_LOG"

set +e
(cd "$WORKER_ROOT" && "${CMD[@]}")
PIPELINE_RC=$?
set -e
if [ "$PIPELINE_RC" -ne 0 ]; then
    rm -f -- "$ROSTER" "$WORKER_ROOT/.a107_roster.tsv."*
    exit "$PIPELINE_RC"
fi

SYMLINK_MATCH=$(find "$OUTPUT_DIR" -maxdepth 1 -type l -name "${WORKER_TAG}_zerolag_*.xml*" -print -quit)
[ -z "$SYMLINK_MATCH" ] || die "A107 candidate is a symlink"
mapfile -d '' A107_FILES < <(find "$OUTPUT_DIR" -maxdepth 1 -type f -name "${WORKER_TAG}_zerolag_*.xml*" -print0 | LC_ALL=C sort -z)
[ "${#A107_FILES[@]}" -gt 0 ] || die "EOS completed without A107 zerolag candidates"
ROSTER_TMP="$WORKER_ROOT/.a107_roster.tsv.$$"
trap 'rm -f -- "$ROSTER_TMP"' EXIT
printf 'relative_path\tbytes\tsha256\n' > "$ROSTER_TMP"
PREVIOUS_RELATIVE=
for CANDIDATE in "${A107_FILES[@]}"; do
    [ -f "$CANDIDATE" ] && [ ! -L "$CANDIDATE" ] || die "A107 candidate must be a regular non-symlink file"
    [ -s "$CANDIDATE" ] || die "A107 candidate is zero bytes"
    CANDIDATE_REAL=$(readlink -e -- "$CANDIDATE") || die "A107 candidate cannot be canonicalized"
    case "$CANDIDATE_REAL" in "$WORKER_ROOT"/*) ;; *) die "A107 candidate escapes worker root" ;; esac
    RELATIVE=${CANDIDATE_REAL#"$WORKER_ROOT/"}
    case "$RELATIVE" in /*|../*|*/../*|'') die "invalid A107 relative path" ;; esac
    [ "$RELATIVE" != "$PREVIOUS_RELATIVE" ] || die "duplicate A107 relative path conflict"
    PREVIOUS_RELATIVE=$RELATIVE
    BYTES=$(stat -c %s "$CANDIDATE_REAL")
    DIGEST=$(sha256sum "$CANDIDATE_REAL" | cut -d' ' -f1)
    printf '%s\t%s\t%s\n' "$RELATIVE" "$BYTES" "$DIGEST" >> "$ROSTER_TMP"
done
chmod 0444 "$ROSTER_TMP"
mv -- "$ROSTER_TMP" "$ROSTER"
trap - EXIT
printf 'SIDECAR_NOINJ_ACQUISITION_COMPLETE worker=%s roster=%s files=%s\n' "$SIDECAR_WORKER_ID" "$ROSTER" "${#A107_FILES[@]}"
