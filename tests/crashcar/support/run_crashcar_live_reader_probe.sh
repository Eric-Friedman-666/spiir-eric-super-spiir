#!/usr/bin/env bash
set -euo pipefail

if (( $# != 8 )); then
  printf '%s\n' \
    'usage: run_crashcar_live_reader_probe.sh EVIDENCE_ROOT FRESH_SOURCE_ROOT FRESH_BUILD_ROOT FRESH_INSTALL_ROOT STAGED_PLUGIN SUPPORT_LIBRARY GSTCOMMON_LIBRARY CONTAINER' >&2
  exit 64
fi

evidence=$1
fresh_source_root=$(realpath -e "$2")
fresh_build_root=$(realpath -e "$3")
fresh_install_root=$(realpath -e "$4")
staged_plugin=$(realpath -e "$5")
support_library=$(realpath -e "$6")
gstcommon_library=$(realpath -e "$7")
container=$(realpath -e "$8")
repo=${fresh_source_root}
install=${fresh_install_root}
tests_dir=${evidence}/tests
binary=${tests_dir}/crashcar_live_reader_probe
runtime_dir=${tests_dir}/live_reader_runtime

[[ -d "${fresh_source_root}" ]] || { printf 'fresh source root is not a directory\n' >&2; exit 64; }
[[ -d "${fresh_build_root}" ]] || { printf 'fresh build root is not a directory\n' >&2; exit 64; }
[[ -d "${fresh_install_root}" ]] || { printf 'fresh install root is not a directory\n' >&2; exit 64; }
[[ -f "${repo}/gstlal-spiir/gst/cuda/cohfar/crashcar_singlefar.c" ]] || {
  printf 'fresh production C source is missing\n' >&2
  exit 64
}
for binding in "${staged_plugin}" "${support_library}" "${gstcommon_library}"; do
  [[ -f "${binding}" ]] || { printf 'fresh compiled binding is missing: %s\n' "${binding}" >&2; exit 64; }
  case "${binding}" in
    "${fresh_install_root}"/*) ;;
    *) printf 'fresh compiled binding escapes install root: %s\n' "${binding}" >&2; exit 64 ;;
  esac
done
[[ -e "${container}" ]] || { printf 'explicit container is missing\n' >&2; exit 64; }

mkdir -p "${tests_dir}" "${runtime_dir}"
rm -f "${binary}"

module load apptainer

{
  printf 'fresh_source_root=%s\n' "${fresh_source_root}"
  printf 'fresh_build_root=%s\n' "${fresh_build_root}"
  printf 'fresh_install_root=%s\n' "${fresh_install_root}"
  printf 'staged_plugin=%s\n' "${staged_plugin}"
  printf 'support_library=%s\n' "${support_library}"
  printf 'gstcommon_library=%s\n' "${gstcommon_library}"
  printf 'container=%s\n' "${container}"
  sha256sum \
    "${repo}/gstlal-spiir/gst/cuda/cohfar/crashcar_singlefar.c" \
    "${staged_plugin}" "${support_library}" "${gstcommon_library}"
  ldd "${staged_plugin}"
} >"${tests_dir}/requested_fresh_bindings.txt"

syntax_command=$(cat <<EOF
set -euo pipefail
export PATH=${install}/bin:/usr/spiir/bin:/usr/bin:/bin
export PKG_CONFIG_PATH=${install}/lib/pkgconfig:/usr/spiir/lib/pkgconfig:/usr/spiir/lib/x86_64-linux-gnu/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig
gcc -std=c11 -fsyntax-only -Wall -Wextra -Werror \
  -Wno-missing-field-initializers -Wno-unused-parameter \
  -Wno-unknown-pragmas -Wno-sign-compare \
  -I${repo}/gstlal-spiir/include \
  -I${repo}/gstlal-spiir/gst/cuda \
  -I${repo}/gstlal-spiir/gst/lib/include \
  -I${repo}/gstlal-spiir/lib/include \
  -I${fresh_build_root} \
  \$(pkg-config --cflags lal glib-2.0 gobject-2.0 gstreamer-1.0 gstreamer-base-1.0) \
  ${repo}/gstlal-spiir/gst/cuda/cohfar/crashcar_singlefar.c
EOF
)

set +e
apptainer exec --cleanenv --bind /fred,/home "${container}" \
  bash -lc "${syntax_command}" \
  >"${tests_dir}/syntax_final.stdout" \
  2>"${tests_dir}/syntax_final.stderr"
syntax_rc=$?
set -e
printf '%s\n' "${syntax_rc}" >"${tests_dir}/syntax_final.rc"
if (( syntax_rc != 0 )); then
  exit "${syntax_rc}"
fi

compile_command=$(cat <<EOF
set -euo pipefail
export PATH=${install}/bin:/usr/spiir/bin:/usr/bin:/bin
export PKG_CONFIG_PATH=${install}/lib/pkgconfig:/usr/spiir/lib/pkgconfig:/usr/spiir/lib/x86_64-linux-gnu/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig
gcc -std=c11 -O2 -ffunction-sections -fdata-sections \
  -Wall -Wextra -Werror \
  -Wno-missing-field-initializers -Wno-unused-parameter \
  -Wno-unknown-pragmas -Wno-sign-compare \
  '-DCRASHCAR_SINGLEFAR_SOURCE="${repo}/gstlal-spiir/gst/cuda/cohfar/crashcar_singlefar.c"' \
  -I${repo}/gstlal-spiir/include \
  -I${repo}/gstlal-spiir/gst/cuda \
  -I${repo}/gstlal-spiir/gst/lib/include \
  -I${repo}/gstlal-spiir/lib/include \
  -I${fresh_build_root} \
  \$(pkg-config --cflags lal glib-2.0 gobject-2.0 gstreamer-1.0 gstreamer-base-1.0) \
  ${repo}/tests/crashcar/support/crashcar_live_reader_probe.c \
  -o ${binary} \
  -Wl,--gc-sections -Wl,--wrap=read \
  -Wl,-rpath,$(dirname "${staged_plugin}") \
  -Wl,-rpath,$(dirname "${support_library}") \
  -Wl,-rpath,$(dirname "${gstcommon_library}") \
  ${gstcommon_library} ${support_library} \
  \$(pkg-config --libs lal glib-2.0 gobject-2.0 gstreamer-1.0 gstreamer-base-1.0) \
  -lm
EOF
)

set +e
apptainer exec --cleanenv --bind /fred,/home "${container}" \
  bash -lc "${compile_command}" \
  >"${tests_dir}/live_probe_compile.stdout" \
  2>"${tests_dir}/live_probe_compile.stderr"
compile_rc=$?
set -e
printf '%s\n' "${compile_rc}" >"${tests_dir}/live_probe_compile.rc"
if (( compile_rc != 0 )); then
  exit "${compile_rc}"
fi

if [[ ! -f "${binary}" || -L "${binary}" || ! -x "${binary}" ]]; then
  printf 'compiled probe is not a regular executable: %s\n' "${binary}" \
    >"${tests_dir}/live_probe_binary_check.stderr"
  printf '1\n' >"${tests_dir}/live_probe_binary_check.rc"
  exit 1
fi
file "${binary}" >"${tests_dir}/live_probe_binary_check.stdout"
stat --printf='mode=%A size=%s inode=%i mtime=%y path=%n\n' "${binary}" \
  >>"${tests_dir}/live_probe_binary_check.stdout"
printf '0\n' >"${tests_dir}/live_probe_binary_check.rc"
: >"${tests_dir}/live_probe_binary_check.stderr"

set +e
apptainer exec --cleanenv --bind /fred,/home \
  --env "LD_LIBRARY_PATH=$(dirname "${staged_plugin}"):$(dirname "${support_library}"):$(dirname "${gstcommon_library}"):/usr/spiir/lib:/usr/spiir/lib/x86_64-linux-gnu" \
  "${container}" "${binary}" "${runtime_dir}" \
  >"${tests_dir}/live_probe_run.stdout" \
  2>"${tests_dir}/live_probe_run.stderr"
run_rc=$?
set -e
printf '%s\n' "${run_rc}" >"${tests_dir}/live_probe_run.rc"
if (( run_rc != 0 )); then
  exit "${run_rc}"
fi
if grep -q 'FATAL' "${tests_dir}/live_probe_run.stderr"; then
  printf 'probe stderr contains FATAL despite rc=0\n' >&2
  exit 1
fi
