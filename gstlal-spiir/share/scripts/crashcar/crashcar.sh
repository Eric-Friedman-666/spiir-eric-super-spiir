#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SOURCE_ROOT_DEFAULT=$(cd "${SCRIPT_DIR}/../../../.." && pwd)
CONFIG_FILE=${CRASHCAR_CONFIG_FILE:-"${SCRIPT_DIR}/crashcar.env"}

usage() {
    cat <<EOF
Usage:
  bash scripts/crashcar.sh [path/to/crashcar.env]

The config defaults to scripts/crashcar.env.  The launcher creates a fresh
run root under run_parent/run_id/<UTC timestamp>, copies the fixed crashcar
scripts into that root, snapshots the config, then runs
scripts/crashcar_controller.sh there.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi
if [ $# -gt 0 ]; then
    CONFIG_FILE=$1
fi
if [ ! -f "${CONFIG_FILE}" ]; then
    printf 'crashcar: missing config file %s\n' "${CONFIG_FILE}" >&2
    exit 2
fi

set -a
# shellcheck source=/dev/null
source "${CONFIG_FILE}"
set +a

ROOT_VALUE=${root:-${ROOT:-${source_root:-${SOURCE_ROOT:-"${SOURCE_ROOT_DEFAULT}"}}}}
RUN_PARENT=${run_parent:-${RUN_PARENT:-"${ROOT_VALUE}/runs"}}
RUN_ID=${run_id:-${RUN_ID:-${run_slug:-${RUN_SLUG:-crashcar}}}}
RUN_TIMESTAMP=${run_timestamp:-${RUN_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}}
RUN_ROOT=${run_root:-${RUN_ROOT:-"${RUN_PARENT}/${RUN_ID}/${RUN_TIMESTAMP}"}}
SOURCE_ROOT_VALUE=${ROOT_VALUE}

if [ -e "${RUN_ROOT}" ] && [ "${crashcar_allow_existing_run_root:-${CRASHCAR_ALLOW_EXISTING_RUN_ROOT:-0}}" != "1" ]; then
    printf 'crashcar: run root already exists: %s\n' "${RUN_ROOT}" >&2
    printf 'Set CRASHCAR_ALLOW_EXISTING_RUN_ROOT=1 only if you know it is safe.\n' >&2
    exit 2
fi

mkdir -p "${RUN_ROOT}/scripts"
for script in \
    crashcar.sh \
    crashcar_controller.sh \
    crashcar_frozen_injection_workflow.sh \
    crashcar_sbatch.sh \
    crashcar_pipeline.sh \
    filter_injection_xml_by_gps.py \
    materialize_snr_autocorrelation.py; do
    cp "${SCRIPT_DIR}/${script}" "${RUN_ROOT}/scripts/${script}"
done
cp "${CONFIG_FILE}" "${RUN_ROOT}/scripts/crashcar.env"
chmod +x \
    "${RUN_ROOT}/scripts/crashcar.sh" \
    "${RUN_ROOT}/scripts/crashcar_controller.sh" \
    "${RUN_ROOT}/scripts/crashcar_frozen_injection_workflow.sh" \
    "${RUN_ROOT}/scripts/crashcar_sbatch.sh" \
    "${RUN_ROOT}/scripts/crashcar_pipeline.sh" \
    "${RUN_ROOT}/scripts/filter_injection_xml_by_gps.py" \
    "${RUN_ROOT}/scripts/materialize_snr_autocorrelation.py"

INJECTION_MODE_RAW=${injection_mode:-${INJECTION_MODE:-False}}
case "$(printf '%s' "${INJECTION_MODE_RAW}" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes|on) INJECTION_MODE_NORMALIZED=True ;;
    false|0|no|off|"") INJECTION_MODE_NORMALIZED=False ;;
    *)
        printf 'crashcar: invalid injection_mode=%s; expected True or False\n' "${INJECTION_MODE_RAW}" >&2
        exit 2
        ;;
esac
INTERNAL_STAGE=${crashcar_internal_stage:-${CRASHCAR_INTERNAL_STAGE:-0}}
if [ "${INJECTION_MODE_NORMALIZED}" = "True" ] && [ "${INTERNAL_STAGE}" != "1" ]; then
    CONTROLLER_SCRIPT="${RUN_ROOT}/scripts/crashcar_frozen_injection_workflow.sh"
    CONTROLLER_NAME="frozen injection workflow"
else
    CONTROLLER_SCRIPT="${RUN_ROOT}/scripts/crashcar_controller.sh"
    CONTROLLER_NAME="single-stage controller"
fi

cat > "${RUN_ROOT}/README.crashcar_launch.txt" <<EOF
Crashcar launch root

Run id:
  ${RUN_ID}

Start command:
  cd ${SOURCE_ROOT_VALUE}
  bash scripts/crashcar.sh

Controller command used inside this staged run:
  ROOT=${SOURCE_ROOT_VALUE} CRASHCAR_CONFIG_FILE=${RUN_ROOT}/scripts/crashcar.env bash ${CONTROLLER_SCRIPT}

Controller type:
  ${CONTROLLER_NAME}

Config snapshot:
  ${RUN_ROOT}/scripts/crashcar.env
EOF

printf 'crashcar: staged run root %s\n' "${RUN_ROOT}"
printf 'crashcar: config snapshot %s\n' "${RUN_ROOT}/scripts/crashcar.env"

if [ "${crashcar_dry_run:-${CRASHCAR_DRY_RUN:-0}}" = "1" ]; then
    printf 'crashcar: dry run requested; not starting controller or submitting Slurm\n'
    printf 'crashcar: controller command would be:\n'
    printf '  ROOT=%q CRASHCAR_CONFIG_FILE=%q bash %q\n' \
        "${SOURCE_ROOT_VALUE}" \
        "${RUN_ROOT}/scripts/crashcar.env" \
        "${CONTROLLER_SCRIPT}"
    exit 0
fi

ROOT="${SOURCE_ROOT_VALUE}" \
    CRASHCAR_SOURCE_CONFIG_FILE="${CONFIG_FILE}" \
    CRASHCAR_CONFIG_FILE="${RUN_ROOT}/scripts/crashcar.env" \
    bash "${CONTROLLER_SCRIPT}"
