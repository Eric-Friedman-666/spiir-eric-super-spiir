#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE=${CRASHCAR_CONFIG_FILE:-"${SCRIPT_DIR}/crashcar.env"}

usage() {
    cat <<EOF
Usage:
  bash scripts/crashcar.sh [path/to/crashcar.env]

The config defaults to scripts/crashcar.env.  The launcher creates a fresh
run root, copies the fixed crashcar scripts into that root, snapshots the
config, then runs scripts/crashcar_controller.sh there.
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

RUN_PARENT=${RUN_PARENT:-$(cd "${SCRIPT_DIR}/.." && pwd)}
RUN_SLUG=${RUN_SLUG:-crashcar}
RUN_TIMESTAMP=${RUN_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
RUN_ROOT=${RUN_ROOT:-"${RUN_PARENT}/run_${RUN_SLUG}_${RUN_TIMESTAMP}"}

if [ -e "${RUN_ROOT}" ] && [ "${CRASHCAR_ALLOW_EXISTING_RUN_ROOT:-0}" != "1" ]; then
    printf 'crashcar: run root already exists: %s\n' "${RUN_ROOT}" >&2
    printf 'Set CRASHCAR_ALLOW_EXISTING_RUN_ROOT=1 only if you know it is safe.\n' >&2
    exit 2
fi

mkdir -p "${RUN_ROOT}/scripts"
for script in crashcar_controller.sh crashcar_sbatch.sh crashcar_pipeline.sh; do
    cp "${SCRIPT_DIR}/${script}" "${RUN_ROOT}/scripts/${script}"
done
cp "${CONFIG_FILE}" "${RUN_ROOT}/scripts/crashcar.env"
chmod +x \
    "${RUN_ROOT}/scripts/crashcar_controller.sh" \
    "${RUN_ROOT}/scripts/crashcar_sbatch.sh" \
    "${RUN_ROOT}/scripts/crashcar_pipeline.sh"

cat > "${RUN_ROOT}/README.crashcar_launch.txt" <<EOF
Crashcar launch root

Start command:
  cd $(cd "${SCRIPT_DIR}/.." && pwd)
  bash scripts/crashcar.sh

Controller command used inside this staged run:
  CRASHCAR_CONFIG_FILE=${RUN_ROOT}/scripts/crashcar.env bash ${RUN_ROOT}/scripts/crashcar_controller.sh

Config snapshot:
  ${RUN_ROOT}/scripts/crashcar.env
EOF

printf 'crashcar: staged run root %s\n' "${RUN_ROOT}"
printf 'crashcar: config snapshot %s\n' "${RUN_ROOT}/scripts/crashcar.env"

if [ "${CRASHCAR_DRY_RUN:-0}" = "1" ]; then
    printf 'crashcar: dry run requested; not starting controller or submitting Slurm\n'
    printf 'crashcar: controller command would be:\n'
    printf '  CRASHCAR_CONFIG_FILE=%q bash %q\n' \
        "${RUN_ROOT}/scripts/crashcar.env" \
        "${RUN_ROOT}/scripts/crashcar_controller.sh"
    exit 0
fi

CRASHCAR_CONFIG_FILE="${RUN_ROOT}/scripts/crashcar.env" \
    bash "${RUN_ROOT}/scripts/crashcar_controller.sh"
