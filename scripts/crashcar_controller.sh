#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LAUNCHER="${SCRIPT_DIR}/crashcar.sh"

if [ ! -f "${LAUNCHER}" ]; then
    printf 'crashcar_controller: missing standard launcher %s\n' "${LAUNCHER}" >&2
    exit 2
fi

exec bash "${LAUNCHER}" "$@"
