#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
LAUNCHER=${REPO_ROOT}/gstlal-spiir/share/scripts/crashcar/crashcar.sh
[ -x "${LAUNCHER}" ] || { printf 'crashcar: missing launcher %s\n' "${LAUNCHER}" >&2; exit 2; }
[ "$#" -eq 0 ] || { printf 'crashcar: edit scripts/crashcar.env, then run bash scripts/crashcar.sh\n' >&2; exit 2; }
CRASHCAR_CONFIG_FILE="${SCRIPT_DIR}/crashcar.env" \
ROOT=${REPO_ROOT} exec "${LAUNCHER}"
