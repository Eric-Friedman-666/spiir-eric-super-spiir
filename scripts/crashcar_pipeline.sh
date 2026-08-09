#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
PIPELINE=${REPO_ROOT}/gstlal-spiir/share/scripts/crashcar/crashcar_pipeline.sh

[ -x "${PIPELINE}" ] || {
    printf 'crashcar: missing pipeline %s\n' "${PIPELINE}" >&2
    exit 2
}

exec "${PIPELINE}" "$@"
