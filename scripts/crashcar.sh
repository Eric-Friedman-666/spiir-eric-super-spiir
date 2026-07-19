#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
CONFIG_FILE=${1:-${CRASHCAR_CONFIG_FILE:-"${SCRIPT_DIR}/crashcar.env"}}
LAUNCHER="${REPO_ROOT}/gstlal-spiir/share/scripts/crashcar/crashcar.sh"

if [ ! -x "${LAUNCHER}" ]; then
    printf 'crashcar: missing launcher %s\n' "${LAUNCHER}" >&2
    exit 2
fi

if [ -f "${CONFIG_FILE}" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
    set +a
fi

ROOT_VALUE=${root:-${ROOT:-${source_root:-${SOURCE_ROOT:-"${REPO_ROOT}"}}}}
ROOT="${ROOT_VALUE}" exec "${LAUNCHER}" "${CONFIG_FILE}"
