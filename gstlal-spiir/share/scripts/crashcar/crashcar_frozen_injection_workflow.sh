#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT_DIR="${ROOT}/scripts"
CONTROLLER_DIR="${ROOT}/controller"
LOG="${CONTROLLER_DIR}/workflow.log"
STATUS="${CONTROLLER_DIR}/workflow_status.json"
CONFIG_FILE=${CRASHCAR_CONFIG_FILE:-"${SCRIPT_DIR}/crashcar.env"}
LIVE_HELPER="${SCRIPT_DIR}/crashcar_live_background.py"
LIVE_HELPER_SHA256=
LIVE_HELPER_CURRENT_SHA256=
mkdir -p "${CONTROLLER_DIR}" "${ROOT}/inputs"

log() {
    printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "${LOG}"
}

write_status() {
    STATUS="${STATUS}" ROOT="${ROOT}" python3 - "$@" <<'PY_STATUS'
import json
import os
from pathlib import Path
import sys
import time
path = Path(os.environ["STATUS"])
payload = {}
if path.exists():
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
for item in sys.argv[1:]:
    key, value = item.split("=", 1)
    payload[key] = value
payload["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
payload["root"] = os.environ["ROOT"]
temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
with temporary.open("w", encoding="utf-8", newline="\n") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, path)
PY_STATUS
}

require_file() {
    local path=$1 label=$2
    if [ ! -f "${path}" ]; then
        log "ERROR missing ${label}: ${path}"
        write_status phase=failed reason="missing_${label}" missing_path="${path}"
        exit 2
    fi
}

live_helper_failure() {
    local check_phase=$1 failure=$2
    log "ERROR staged live-background helper integrity failed phase=${check_phase} reason=${failure}"
    write_status phase=failed reason=live_background_helper_integrity_failed \
        live_background_helper_check_phase="${check_phase}" \
        live_background_helper_failure="${failure}" \
        live_background_helper="${LIVE_HELPER}" \
        live_background_helper_sha256="${LIVE_HELPER_SHA256:-UNPINNED}"
    return 1
}

snapshot_live_helper() {
    local check_phase=$1 result
    if ! result=$(python3 - "${ROOT}" "${SCRIPT_DIR}" "${LIVE_HELPER}" 2>&1 <<'PY_LIVE_HELPER'
import hashlib
import os
from pathlib import Path
import stat
import sys

root, script_dir, helper = map(Path, sys.argv[1:])
try:
    if not root.is_absolute() or not script_dir.is_absolute() or not helper.is_absolute():
        raise RuntimeError("paths_must_be_absolute")
    root_real = Path(os.path.realpath(root))
    script_real = Path(os.path.realpath(script_dir))
    if script_real != root_real / "scripts":
        raise RuntimeError("script_dir_outside_run_root")
    before = os.lstat(helper)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("helper_not_regular_non_symlink")
    if before.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("helper_is_writable")
    if not before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise RuntimeError("helper_is_not_executable")
    if Path(os.path.realpath(helper)) != script_real / "crashcar_live_background.py":
        raise RuntimeError("helper_outside_run_scripts")
    fd = os.open(helper, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > 8 * 1024 * 1024:
                raise RuntimeError("helper_exceeds_size_limit")
            digest.update(block)
    finally:
        os.close(fd)
    after = os.lstat(helper)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(opened) or identity(opened) != identity(after):
        raise RuntimeError("helper_changed_during_snapshot")
    if total != opened.st_size or total == 0:
        raise RuntimeError("helper_size_mismatch")
    print(digest.hexdigest())
except (OSError, RuntimeError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
PY_LIVE_HELPER
    ); then
        live_helper_failure "${check_phase}" "${result:-snapshot_failed}"
        return 1
    fi
    LIVE_HELPER_CURRENT_SHA256=${result}
}

pin_live_helper() {
    snapshot_live_helper startup || return 1
    LIVE_HELPER_SHA256=${LIVE_HELPER_CURRENT_SHA256}
    write_status live_background_helper="${LIVE_HELPER}" \
        live_background_helper_sha256="${LIVE_HELPER_SHA256}"
}

verify_live_helper_pin() {
    local check_phase=$1
    snapshot_live_helper "${check_phase}" || return 1
    if [ "${LIVE_HELPER_CURRENT_SHA256}" != "${LIVE_HELPER_SHA256}" ]; then
        live_helper_failure "${check_phase}" helper_sha256_drift
        return 1
    fi
}

require_var() {
    local name=$1 value=${!1-}
    if [ -z "${value}" ]; then
        log "ERROR ${name} is required for injection_mode=True"
        write_status phase=failed reason="missing_${name}"
        exit 2
    fi
}

bool_true() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        true|1|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

duration_seconds_from() {
    local seconds_name=$1 hours_name=$2 label=$3
    local seconds=${!seconds_name-} hours=${!hours_name-}
    if [ -n "${seconds}" ]; then
        printf '%s\n' "${seconds}"
    elif [ -n "${hours}" ]; then
        printf '%s\n' "$((hours * 3600))"
    else
        log "ERROR ${label} requires ${seconds_name} or ${hours_name}"
        write_status phase=failed reason="missing_${label}"
        exit 2
    fi
}

write_env_file() {
    local output=$1
    shift
    : > "${output}"
    while [ "$#" -gt 0 ]; do
        printf '%s\n' "$1" >> "${output}"
        shift
    done
}

json_get() {
    python3 - "$1" "$2" <<'PY_JSON_GET'
import json
import sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], "")
    print(value if value is not None else "")
except Exception:
    print("")
PY_JSON_GET
}

clear_stage_environment() {
    unset \
        root ROOT source_root SOURCE_ROOT save_dir SAVE_DIR run_parent RUN_PARENT \
        run_id RUN_ID run_slug RUN_SLUG run_root RUN_ROOT run_timestamp RUN_TIMESTAMP \
        data_file frame_cache FRAME_CACHE detector_response_file detrsp_map DETRSP_MAP \
        segment_xml SEGMENT_XML start_gps START_GPS end_gps END_GPS duration DURATION \
        duration_hour duration_seconds DURATION_HOUR DURATION_SECONDS \
        worker_number worker_count WORKER_COUNT bank_per_worker banks_per_worker BANKS_PER_WORKER \
        BG_accumulation_hour BG_update_hour background_accumulation BACKGROUND_ACCUMULATION \
        background_accumulation_seconds BACKGROUND_ACCUMULATION_SECONDS background_update BACKGROUND_UPDATE \
        background_update_trigger_seconds BACKGROUND_UPDATE_TRIGGER_SECONDS zerolag_update_hour \
        zerolag_update_seconds ZEROLAG_UPDATE_SECONDS injection_mode INJECTION_MODE \
        injection_file injection_data_file injection_detector_response_file injection_segment_xml \
        injection_start_gps injection_duration_seconds injection_duration_hour \
        injection_bg_data_file injection_bg_detector_response_file injection_bg_segment_xml \
        injection_bg_start_gps injection_bg_duration_seconds injection_bg_duration_hour \
        injection_bg_worker_number injection_bg_bank_per_worker injection_worker_number \
        injection_bank_per_worker noninj_stats_loc NONINJ_STATS_LOC \
        single_background_mode SINGLE_BACKGROUND_MODE \
        crashcar_background_required_seconds CRASHCAR_BACKGROUND_REQUIRED_SECONDS \
        crashcar_internal_live_background_root CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROOT \
        crashcar_internal_live_background_role CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE
}

start_stage_async() {
    local config=$1 label=$2 pid_file=$3 output_file=$4
    log "starting ${label} asynchronously with config ${config}"
    (
        clear_stage_environment
        ROOT="${SOURCE_ROOT_VALUE}" CRASHCAR_CONFIG_FILE="${config}" \
            bash "${SCRIPT_DIR}/crashcar.sh" "${config}"
    ) >"${output_file}" 2>&1 &
    local pid=$!
    printf '%s\n' "${pid}" > "${pid_file}"
    write_status "${label}_launcher_pid=${pid}" "${label}_launcher_log=${output_file}"
}

stage_pid_active() {
    local pid_file=$1
    [ -s "${pid_file}" ] && kill -0 "$(cat "${pid_file}")" 2>/dev/null
}

stage_phase() {
    json_get "$1/controller/status.json" phase
}

stage_job_id() {
    if [ -s "$1/controller/job_id.txt" ]; then
        cat "$1/controller/job_id.txt"
    fi
}

validate_live_singles() {
    verify_live_helper_pin live_single_validation || exit 2
    "${LIVE_HELPER}" validate-all-singles \
        --producer-root "${BG_RUN_ROOT}" \
        --worker-count "${BG_WORKERS}" \
        --banks-per-worker "${BG_BANKS_PER_WORKER}" \
        --start-bank "${START_BANK_VALUE}"
}

validate_live_multi_inputs() {
    python3 - "${BG_RUN_ROOT}" "${BG_WORKERS}" <<'PY_MULTI_READY'
import gzip
import json
import os
from pathlib import Path
import stat
import sys
from xml.parsers import expat

root = Path(sys.argv[1]).resolve(strict=True)
worker_count = int(sys.argv[2])
if worker_count < 1 or worker_count > 4096:
    raise SystemExit("invalid worker count")

records = []
for worker in range(worker_count):
    jobno = f"{worker:03d}"
    worker_records = []
    for span in ("2w", "1d", "2h"):
        path = root / "run" / jobno / f"{jobno}_marginalized_stats_{span}.xml.gz"
        try:
            before = os.lstat(path)
        except OSError as exc:
            raise SystemExit(f"multi {worker}/{span} is unavailable: {exc}")
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise SystemExit(f"multi {worker}/{span} is not a regular non-symlink file")
        if before.st_size < 1:
            raise SystemExit(f"multi {worker}/{span} is empty")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            parser = expat.ParserCreate()
            with os.fdopen(fd, "rb", closefd=False) as raw:
                with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
                    while True:
                        block = stream.read(1024 * 1024)
                        if not block:
                            break
                        parser.Parse(block, False)
                    parser.Parse(b"", True)
        except (OSError, EOFError, expat.ExpatError) as exc:
            raise SystemExit(f"multi {worker}/{span} is not complete gzip/XML: {exc}")
        finally:
            os.close(fd)
        after = os.lstat(path)
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(before) != identity(opened) or identity(opened) != identity(after):
            raise SystemExit(f"multi {worker}/{span} changed during validation")
        worker_records.append({
            "span": span, "path": str(path), "size": opened.st_size,
            "mtime_ns": opened.st_mtime_ns,
        })
    records.append({"worker_id": worker, "files": worker_records})
print(json.dumps({
    "kind": "crashcar_live_multi_readiness", "producer_root": str(root),
    "worker_count": worker_count, "workers": records,
}, separators=(",", ":"), sort_keys=True))
PY_MULTI_READY
}

wait_for_first_backgrounds() {
    local producer_pid_file=$1
    local singles="${CONTROLLER_DIR}/first_single_readiness.json"
    local multi="${CONTROLLER_DIR}/first_multi_readiness.json"
    while true; do
        if validate_live_singles >"${singles}.tmp" 2>"${singles}.err" && \
           validate_live_multi_inputs >"${multi}.tmp" 2>"${multi}.err"; then
            if ! stage_pid_active "${producer_pid_file}"; then
                rm -f "${singles}.tmp" "${multi}.tmp"
                log "ERROR producer stopped before injection launch"
                write_status phase=failed reason=producer_not_active_at_first_readiness
                exit 3
            fi
            mv "${singles}.tmp" "${singles}"
            mv "${multi}.tmp" "${multi}"
            write_status phase=first_live_backgrounds_ready \
                producer_job_id="$(stage_job_id "${BG_RUN_ROOT}")" \
                single_readiness="${singles}" multi_readiness="${multi}" \
                producer_active=true
            log "first complete single and independent normal multi inputs are ready; producer remains active"
            return 0
        fi
        rm -f "${singles}.tmp" "${multi}.tmp"
        local phase
        phase=$(stage_phase "${BG_RUN_ROOT}")
        case "${phase}" in
            failed*|completed)
                log "ERROR producer reached ${phase:-unknown} before usable backgrounds"
                write_status phase=failed reason=no_live_background_before_producer_exit \
                    producer_phase="${phase:-unknown}"
                exit 3
                ;;
        esac
        if ! stage_pid_active "${producer_pid_file}"; then
            log "ERROR producer launcher exited before usable backgrounds"
            write_status phase=failed reason=producer_launcher_exited_before_ready
            exit 3
        fi
        write_status phase=waiting_first_live_backgrounds producer_phase="${phase:-starting}"
        sleep 10
    done
}

single_versions_from_snapshot() {
    python3 - "$1" <<'PY_VERSIONS'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print(",".join(str(item["accepted_version"]) for item in value["workers"]))
PY_VERSIONS
}

monitor_overlapping_stages() {
    local producer_pid_file=$1 consumer_pid_file=$2
    local saw_later_single_version=0 first_versions current_versions
    first_versions=$(single_versions_from_snapshot \
        "${CONTROLLER_DIR}/first_single_readiness.json")
    current_versions=${first_versions}
    while true; do
        local bg_phase inj_phase bg_job inj_job
        bg_phase=$(stage_phase "${BG_RUN_ROOT}")
        inj_phase=$(stage_phase "${INJ_ROOT}")
        bg_job=$(stage_job_id "${BG_RUN_ROOT}")
        inj_job=$(stage_job_id "${INJ_ROOT}")
        if validate_live_singles >"${CONTROLLER_DIR}/latest_single_readiness.tmp" 2>/dev/null; then
            mv "${CONTROLLER_DIR}/latest_single_readiness.tmp" \
                "${CONTROLLER_DIR}/latest_single_readiness.json"
            current_versions=$(single_versions_from_snapshot \
                "${CONTROLLER_DIR}/latest_single_readiness.json")
            if [ "${current_versions}" != "${first_versions}" ]; then
                saw_later_single_version=1
            fi
        else
            rm -f "${CONTROLLER_DIR}/latest_single_readiness.tmp"
        fi
        write_status phase=overlapping_live_runs \
            producer_phase="${bg_phase:-starting}" consumer_phase="${inj_phase:-starting}" \
            producer_job_id="${bg_job}" consumer_job_id="${inj_job}" \
            observed_single_versions="${current_versions}" \
            later_single_version_observed="${saw_later_single_version}" \
            producer_active="$(stage_pid_active "${producer_pid_file}" && printf true || printf false)"
        case "${bg_phase}" in
            failed*)
                log "ERROR producer failed while consumer phase=${inj_phase:-starting}"
                write_status phase=failed reason=producer_failed_during_live_consumer
                exit 3
                ;;
        esac
        case "${inj_phase}" in
            failed*)
                log "ERROR injection consumer failed"
                write_status phase=failed reason=live_consumer_failed
                exit 3
                ;;
            completed)
                if [ "${bg_phase}" != "completed" ]; then
                    if ! stage_pid_active "${producer_pid_file}"; then
                        # Resolve the same status/PID race at producer shutdown.
                        sleep 1
                        bg_phase=$(stage_phase "${BG_RUN_ROOT}")
                        if [ "${bg_phase}" != "completed" ]; then
                            log "ERROR producer is not active when consumer completed"
                            write_status phase=failed reason=producer_lost_before_consumer_completion
                            exit 3
                        fi
                    fi
                    sleep 10
                    continue
                fi
                wait "$(cat "${consumer_pid_file}")"
                wait "$(cat "${producer_pid_file}")"
                write_status phase=completed producer_job_id="${bg_job}" \
                    consumer_job_id="${inj_job}" producer_consumer_overlap=true \
                    later_single_version_observed="${saw_later_single_version}" \
                    background_mode=live_no_injection
                log "live no-injection producer and injection consumer completed"
                return 0
                ;;
        esac
        if [ "${bg_phase}" = "completed" ]; then
            log "ERROR producer completed before the injection consumer"
            write_status phase=failed reason=producer_completed_before_consumer
            exit 3
        fi
        if ! stage_pid_active "${producer_pid_file}"; then
            log "ERROR producer launcher is no longer active"
            write_status phase=failed reason=producer_launcher_lost
            exit 3
        fi
        if ! stage_pid_active "${consumer_pid_file}" && [ -n "${inj_phase}" ]; then
            # The launcher can exit between the status read and this PID probe.
            # Re-read once so a just-published terminal status is not mistaken
            # for an unexplained launcher loss.
            sleep 1
            inj_phase=$(stage_phase "${INJ_ROOT}")
            case "${inj_phase}" in
                completed|failed*) continue ;;
                *)
                    log "ERROR consumer launcher exited unexpectedly phase=${inj_phase}"
                    write_status phase=failed reason=consumer_launcher_lost
                    exit 3
                    ;;
            esac
        fi
        sleep 30
    done
}

if [ ! -f "${CONFIG_FILE}" ]; then
    printf 'crashcar_live_injection_workflow: missing config %s\n' "${CONFIG_FILE}" >&2
    exit 2
fi
set -a
# shellcheck source=/dev/null
source "${CONFIG_FILE}"
set +a
SOURCE_ROOT_VALUE=${root:-${ROOT:-${source_root:-${SOURCE_ROOT:-}}}}
if [ -z "${SOURCE_ROOT_VALUE}" ]; then
    printf 'crashcar_live_injection_workflow: root required\n' >&2
    exit 2
fi
if ! bool_true "${injection_mode:-${INJECTION_MODE:-False}}"; then
    printf 'crashcar_live_injection_workflow: injection_mode=True required\n' >&2
    exit 2
fi
pin_live_helper || exit 2

COMMON_DATA_FILE=${data_file:-${frame_cache:-${FRAME_CACHE:-}}}
COMMON_DETECTOR_RESPONSE_FILE=${detector_response_file:-${detrsp_map:-${DETRSP_MAP:-}}}
COMMON_SEGMENT_XML=${segment_xml:-${SEGMENT_XML:-}}
injection_data_file=${injection_data_file:-${COMMON_DATA_FILE}}
injection_detector_response_file=${injection_detector_response_file:-${COMMON_DETECTOR_RESPONSE_FILE}}
injection_segment_xml=${injection_segment_xml:-${COMMON_SEGMENT_XML}}
injection_start_gps=${injection_start_gps:-${start_gps:-${START_GPS:-}}}
injection_duration_seconds=${injection_duration_seconds:-${duration_seconds:-${DURATION_SECONDS:-}}}
injection_duration_hour=${injection_duration_hour:-${duration_hour:-${DURATION_HOUR:-}}}
injection_bg_data_file=${injection_bg_data_file:-${COMMON_DATA_FILE}}
injection_bg_detector_response_file=${injection_bg_detector_response_file:-${COMMON_DETECTOR_RESPONSE_FILE}}
injection_bg_segment_xml=${injection_bg_segment_xml:-${COMMON_SEGMENT_XML}}
injection_bg_start_gps=${injection_bg_start_gps:-${start_gps:-${START_GPS:-}}}
if [ -z "${injection_bg_duration_seconds:-}" ] && [ -z "${injection_bg_duration_hour:-}" ]; then
    injection_bg_duration_seconds=${duration_seconds:-${DURATION_SECONDS:-}}
    injection_bg_duration_hour=${duration_hour:-${DURATION_HOUR:-}}
fi
for name in injection_file injection_data_file injection_detector_response_file \
    injection_start_gps injection_segment_xml injection_bg_data_file \
    injection_bg_detector_response_file injection_bg_start_gps \
    injection_bg_segment_xml; do
    require_var "${name}"
done
require_file "${injection_file}" injection_file
require_file "${injection_data_file}" injection_data_file
require_file "${injection_detector_response_file}" injection_detector_response_file
require_file "${injection_segment_xml}" injection_segment_xml
require_file "${injection_bg_data_file}" injection_bg_data_file
require_file "${injection_bg_detector_response_file}" injection_bg_detector_response_file
require_file "${injection_bg_segment_xml}" injection_bg_segment_xml

O3_BANK_DIR=${bank_file:-${o3_bank_dir:-${O3_BANK_DIR:-}}}
require_var O3_BANK_DIR
BG_WORKERS=${injection_bg_worker_number:-${INJECTION_BG_WORKER_NUMBER:-${worker_number:-1}}}
BG_BANKS_PER_WORKER=${injection_bg_bank_per_worker:-${INJECTION_BG_BANK_PER_WORKER:-${bank_per_worker:-8}}}
INJ_WORKERS=${injection_worker_number:-${INJECTION_WORKER_NUMBER:-${worker_number:-2}}}
INJ_BANKS_PER_WORKER=${injection_bank_per_worker:-${INJECTION_BANK_PER_WORKER:-${bank_per_worker:-8}}}
START_BANK_VALUE=${start_bank:-${START_BANK:-0}}
if [ "${BG_WORKERS}" != "${INJ_WORKERS}" ] || \
   [ "${BG_BANKS_PER_WORKER}" != "${INJ_BANKS_PER_WORKER}" ]; then
    log "ERROR live producer/consumer worker geometry differs"
    write_status phase=failed reason=live_worker_geometry_mismatch
    exit 2
fi
BG_DURATION_SECONDS=$(duration_seconds_from injection_bg_duration_seconds injection_bg_duration_hour injection_bg_duration)
INJ_TOTAL_SECONDS=$(duration_seconds_from injection_duration_seconds injection_duration_hour injection_duration)
if [ -n "${BG_accumulation_hour:-}" ]; then
    BG_ACCUM_SECONDS=$((BG_accumulation_hour * 3600))
else
    BG_ACCUM_SECONDS=${background_accumulation:-${BACKGROUND_ACCUMULATION:-${background_accumulation_seconds:-${BACKGROUND_ACCUMULATION_SECONDS:-10800}}}}
fi
if [ -n "${BG_update_hour:-}" ]; then
    BG_UPDATE_SECONDS=$((BG_update_hour * 3600))
else
    BG_UPDATE_SECONDS=${background_update:-${BACKGROUND_UPDATE:-${background_update_trigger_seconds:-${BACKGROUND_UPDATE_TRIGGER_SECONDS:-3600}}}}
fi
if ! [[ "${BG_ACCUM_SECONDS}" =~ ^[1-9][0-9]*$ && "${BG_UPDATE_SECONDS}" =~ ^[1-9][0-9]*$ ]]; then
    log "ERROR live background accumulation/update cadence must be positive integers"
    write_status phase=failed reason=invalid_live_background_cadence
    exit 2
fi
ZEROLAG_UPDATE_SECONDS=${zerolag_update_seconds:-${ZEROLAG_UPDATE_SECONDS:-}}
if [ -z "${ZEROLAG_UPDATE_SECONDS}" ]; then
    ZEROLAG_UPDATE_SECONDS=$(( ${zerolag_update_hour:-1} * 3600 ))
fi
TAIL_LOG_FAR=${tail_log_FAR:-${TAIL_LOG_FAR:--2.5}}
SNR_LOG_FAR=${SNR_series_logFAR_threshold:-${snr_series_logFAR_threshold:-${SNR_SERIES_LOG_FAR_THRESHOLD:--4}}}
INJ_SNR_LOG_FAR=${injection_SNR_series_logFAR_threshold:-${injection_snr_series_logFAR_threshold:-${INJECTION_SNR_SERIES_LOGFAR_THRESHOLD:-90}}}
RUN_ID=${run_id:-${RUN_ID:-crashcar_live_injection}}
SLURM_PARTITION_VALUE=${slurm_partition:-${SLURM_PARTITION:-}}
SLURM_TIME_VALUE=${slurm_time:-${SLURM_TIME:-}}
SLURM_MEM_VALUE=${slurm_mem:-${SLURM_MEM:-}}
SLURM_GRES_VALUE=${slurm_gres:-${SLURM_GRES:-}}
SLURM_CPUS_PER_TASK_VALUE=${slurm_cpus_per_task:-${SLURM_CPUS_PER_TASK:-}}
BG_RUN_ROOT="${ROOT}/bg_noinj"
INJ_ROOT="${ROOT}/inj_bns"
BG_CONFIG="${CONTROLLER_DIR}/bg_noinj.env"
INJ_CONFIG="${CONTROLLER_DIR}/inj_bns.env"
BG_INITIAL_MULTI_STATS=${noninj_stats_loc:-/fred/oz016/wguo/odds_ratio/O3a/chunk2/multi_det-BNS}
for fresh_path in "${BG_RUN_ROOT}" "${INJ_ROOT}"; do
    if [ -e "${fresh_path}" ] || [ -L "${fresh_path}" ]; then
        log "ERROR workflow requires fresh path ${fresh_path}"
        write_status phase=failed reason=nonfresh_workflow_path path="${fresh_path}"
        exit 2
    fi
done

write_status phase=starting workflow=live_no_injection_background_with_concurrent_injection \
    source_root="${SOURCE_ROOT_VALUE}" producer_root="${BG_RUN_ROOT}" \
    consumer_root="${INJ_ROOT}" worker_count="${BG_WORKERS}" \
    banks_per_worker="${BG_BANKS_PER_WORKER}" background_mode=live_no_injection \
    background_accumulation_seconds="${BG_ACCUM_SECONDS}" \
    background_update_seconds="${BG_UPDATE_SECONDS}"

write_env_file "${BG_CONFIG}" \
    "root=${SOURCE_ROOT_VALUE}" "run_root=${BG_RUN_ROOT}" \
    "run_id=${RUN_ID}_bg_noinj" "crashcar_internal_stage=1" \
    "crashcar_internal_bg_only=0" "crashcar_internal_live_background_role=producer" \
    "slurm_partition=${SLURM_PARTITION_VALUE}" "slurm_time=${SLURM_TIME_VALUE}" \
    "slurm_mem=${SLURM_MEM_VALUE}" "slurm_gres=${SLURM_GRES_VALUE}" \
    "slurm_cpus_per_task=${SLURM_CPUS_PER_TASK_VALUE}" \
    "data_file=${injection_bg_data_file}" \
    "detector_response_file=${injection_bg_detector_response_file}" \
    "start_gps=${injection_bg_start_gps}" "duration=${BG_DURATION_SECONDS}" \
    "segment_xml=${injection_bg_segment_xml}" "worker_number=${BG_WORKERS}" \
    "bank_per_worker=${BG_BANKS_PER_WORKER}" "bank_file=${O3_BANK_DIR}" \
    "background_accumulation=${BG_ACCUM_SECONDS}" \
    "background_update=${BG_UPDATE_SECONDS}" \
    "zerolag_update_seconds=${ZEROLAG_UPDATE_SECONDS}" \
    "tail_log_FAR=${TAIL_LOG_FAR}" "SNR_series_logFAR_threshold=${SNR_LOG_FAR}" \
    "injection_mode=False" "noninj_stats_loc=${BG_INITIAL_MULTI_STATS}" \
    "single_background_mode=rolling"
PRODUCER_PID_FILE="${CONTROLLER_DIR}/producer_launcher.pid"
CONSUMER_PID_FILE="${CONTROLLER_DIR}/consumer_launcher.pid"
start_stage_async "${BG_CONFIG}" producer "${PRODUCER_PID_FILE}" \
    "${CONTROLLER_DIR}/producer_launcher.log"
wait_for_first_backgrounds "${PRODUCER_PID_FILE}"

write_env_file "${INJ_CONFIG}" \
    "root=${SOURCE_ROOT_VALUE}" "run_root=${INJ_ROOT}" \
    "run_id=${RUN_ID}_inj_bns" "crashcar_internal_stage=1" \
    "crashcar_internal_bg_only=0" "crashcar_internal_live_background_role=consumer" \
    "crashcar_internal_live_background_root=${BG_RUN_ROOT}" \
    "slurm_partition=${SLURM_PARTITION_VALUE}" "slurm_time=${SLURM_TIME_VALUE}" \
    "slurm_mem=${SLURM_MEM_VALUE}" "slurm_gres=${SLURM_GRES_VALUE}" \
    "slurm_cpus_per_task=${SLURM_CPUS_PER_TASK_VALUE}" \
    "data_file=${injection_data_file}" \
    "detector_response_file=${injection_detector_response_file}" \
    "start_gps=${injection_start_gps}" "duration=${INJ_TOTAL_SECONDS}" \
    "segment_xml=${injection_segment_xml}" "worker_number=${INJ_WORKERS}" \
    "bank_per_worker=${INJ_BANKS_PER_WORKER}" "bank_file=${O3_BANK_DIR}" \
    "background_accumulation=${BG_ACCUM_SECONDS}" \
    "background_update=${BG_UPDATE_SECONDS}" \
    "cohfar_assignfar_refresh_interval_seconds=${BG_UPDATE_SECONDS}" \
    "finalsink_fapupdater_interval_seconds=${BG_UPDATE_SECONDS}" \
    "zerolag_update_seconds=${ZEROLAG_UPDATE_SECONDS}" \
    "tail_log_FAR=${TAIL_LOG_FAR}" "SNR_series_logFAR_threshold=${INJ_SNR_LOG_FAR}" \
    "injection_mode=True" "injection_file=${injection_file}" \
    "injection_bg_data_file=${injection_bg_data_file}" \
    "injection_bg_detector_response_file=${injection_bg_detector_response_file}" \
    "injection_bg_start_gps=${injection_bg_start_gps}" \
    "injection_bg_duration_seconds=${BG_DURATION_SECONDS}" \
    "injection_bg_segment_xml=${injection_bg_segment_xml}" \
    "noninj_stats_loc=${BG_RUN_ROOT}/run" \
    "single_background_mode=live_readonly" \
    "crashcar_background_required_seconds=${BG_ACCUM_SECONDS}"
start_stage_async "${INJ_CONFIG}" consumer "${CONSUMER_PID_FILE}" \
    "${CONTROLLER_DIR}/consumer_launcher.log"
if ! stage_pid_active "${PRODUCER_PID_FILE}"; then
    log "ERROR producer stopped as injection consumer started"
    write_status phase=failed reason=producer_not_active_at_consumer_start
    exit 3
fi
write_status phase=overlap_started producer_active=true \
    producer_job_id="$(stage_job_id "${BG_RUN_ROOT}")" \
    consumer_job_id="$(stage_job_id "${INJ_ROOT}")" \
    first_single_readiness="${CONTROLLER_DIR}/first_single_readiness.json" \
    first_multi_readiness="${CONTROLLER_DIR}/first_multi_readiness.json"
monitor_overlapping_stages "${PRODUCER_PID_FILE}" "${CONSUMER_PID_FILE}"
