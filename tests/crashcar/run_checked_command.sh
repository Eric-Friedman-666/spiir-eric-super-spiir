#!/usr/bin/env bash

# Preserve the command's real exit status before container/module cleanup can
# mask it. This wrapper is diagnostic-only and never installs or stages code.
if [ "$#" -lt 3 ]; then
    echo "usage: $0 LOG RC_FILE COMMAND [ARG ...]" >&2
    exit 64
fi

log_file=$1
rc_file=$2
shift 2

"$@" >"${log_file}" 2>&1
command_rc=$?
printf '%s\n' "${command_rc}" >"${rc_file}"
exit "${command_rc}"
