#!/usr/bin/env python3
"""Bounded crashcar source closure rooted only at formal repository entries."""

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SPIIR = ROOT / "gstlal-spiir"
CUDA = SPIIR / "gst/cuda"
PYROOT = SPIIR / "python"

AUTHORITY = ("AGENTS.md",)

LAUNCH_RUNTIME = (
    "scripts/crashcar.sh",
    "scripts/crashcar.env",
    "gstlal-spiir/share/scripts/crashcar/crashcar.env",
    "gstlal-spiir/share/scripts/crashcar/crashcar.sh",
    "gstlal-spiir/share/scripts/crashcar/crashcar_controller.sh",
    "gstlal-spiir/share/scripts/crashcar/crashcar_frozen_injection_workflow.sh",
    "gstlal-spiir/share/scripts/crashcar/crashcar_live_background.py",
    "gstlal-spiir/share/scripts/crashcar/crashcar_sbatch.sh",
    "gstlal-spiir/share/scripts/crashcar/crashcar_pipeline.sh",
    "gstlal-spiir/share/scripts/crashcar/dump_segment_livetime_csv.py",
    "gstlal-spiir/share/scripts/crashcar/export_template_shape_map.py",
    "gstlal-spiir/bin/gstlal_inspiral_postcohspiir_online",
)

PYTHON_RUNTIME = (
    "gstlal-spiir/python/__init__.py",
    "gstlal-spiir/python/pipemodules/__init__.py",
    "gstlal-spiir/python/pipemodules/homomorphic.py",
    "gstlal-spiir/python/pipemodules/snglrate_datasource.py",
    "gstlal-spiir/python/pipemodules/postcoh_finalsink.py",
    "gstlal-spiir/python/pipemodules/spiirparts.py",
    "gstlal-spiir/python/pipemodules/pipe_macro.py",
    "gstlal-spiir/python/pipemodules/stats.py",
    "gstlal-spiir/python/pipemodules/spiir_utils.py",
    "gstlal-spiir/python/pipemodules/postcohtable/__init__.py",
    "gstlal-spiir/python/pipemodules/postcohtable/postcoh_table_def.py",
    "gstlal-spiir/python/pipemodules/postcohtable/postcohtable.py",
    "gstlal-spiir/python/pipemodules/postcohtable/_postcohtable.c",
)

C_AUTOMAKE_SOURCES = (
    "gstlal-spiir/gst/cuda/libgstcuda.c",
    "gstlal-spiir/gst/cuda/multiratespiir/multiratespiir_kernel.cu",
    "gstlal-spiir/gst/cuda/multiratespiir/multiratespiir_utils.c",
    "gstlal-spiir/gst/cuda/multiratespiir/multiratespiir.c",
    "gstlal-spiir/gst/cuda/postcoh/postcoh_kernel.cu",
    "gstlal-spiir/gst/cuda/postcoh/postcoh_utils.c",
    "gstlal-spiir/gst/cuda/postcoh/postcoh.c",
    "gstlal-spiir/gst/cuda/postcoh/postcohtable_utils.c",
    "gstlal-spiir/gst/cuda/postcoh/postcoh_filesink.c",
    "gstlal-spiir/gst/cuda/cohfar/knn_kde.c",
    "gstlal-spiir/gst/cuda/cohfar/ssvkernel.c",
    "gstlal-spiir/gst/cuda/cohfar/background_stats_utils.c",
    "gstlal-spiir/gst/cuda/cohfar/cohfar_accumbackground.c",
    "gstlal-spiir/gst/cuda/cohfar/cohfar_assignfar.c",
    "gstlal-spiir/gst/cuda/cohfar/crashcar_singlefar.c",
)

C_AUTOMAKE_HEADERS = (
    "gstlal-spiir/gst/cuda/complex_f.h",
    "gstlal-spiir/gst/cuda/multiratespiir/multiratespiir_state.h",
    "gstlal-spiir/gst/cuda/multiratespiir/multiratespiir_kernel.h",
    "gstlal-spiir/gst/cuda/multiratespiir/multiratespiir_utils.h",
    "gstlal-spiir/gst/cuda/multiratespiir/multiratespiir.h",
    "gstlal-spiir/gst/cuda/postcoh/postcoh_state.h",
    "gstlal-spiir/gst/cuda/postcoh/postcoh_kernel.h",
    "gstlal-spiir/gst/cuda/postcoh/postcoh_utils.h",
    "gstlal-spiir/gst/cuda/postcoh/postcoh.h",
    "gstlal-spiir/gst/cuda/postcoh/postcohtable_utils.h",
    "gstlal-spiir/gst/cuda/cohfar/background_stats.h",
    "gstlal-spiir/gst/cuda/cohfar/background_stats_utils.h",
    "gstlal-spiir/gst/cuda/cohfar/cohfar_accumbackground.h",
    "gstlal-spiir/gst/cuda/cohfar/cohfar_assignfar.h",
    "gstlal-spiir/gst/cuda/cohfar/crashcar_singlefar.h",
)

C_DIRECT_HEADERS = (
    "gstlal-spiir/gst/cuda/postcoh/postcoh_filesink.h",
    "gstlal-spiir/gst/cuda/cohfar/knn_kde.h",
    "gstlal-spiir/gst/cuda/cohfar/ssvkernel.h",
    "gstlal-spiir/gst/cuda/cuda_debug.h",
    "gstlal-spiir/gst/lib/include/ifo_set.h",
    "gstlal-spiir/gst/lib/include/flag_segment.h",
    "gstlal-spiir/lib/include/IFOMap.h",
    "gstlal-spiir/lib/include/LIGOLwHeader.h",
    "gstlal-spiir/include/postcohtable.h",
    "gstlal-spiir/include/pipe_macro.h",
)

BUILD_BINDINGS = (
    "gstlal-spiir/Makefile.am",
    "gstlal-spiir/configure.ac",
    "gstlal-spiir/gst/Makefile.am",
    "gstlal-spiir/gst/cuda/Makefile.am",
    "gstlal-spiir/python/Makefile.am",
    "gstlal-spiir/python/pipemodules/Makefile.am",
    "gstlal-spiir/python/pipemodules/postcohtable/Makefile.am",
    "gstlal-spiir/bin/Makefile.am",
    "gstlal-spiir/gnuscripts/cudalt.py",
)

EXTERNAL_ACCEPTANCE_RUNTIME = (
    "gstlal-spiir/bin/crashcar_plot.py",
    "gstlal-spiir/share/scripts/crashcar/crashcar_plot_support.py",
    "gstlal-spiir/share/scripts/crashcar/crashcar_numeric.py",
    "gstlal-spiir/share/scripts/crashcar/single_detector_far.py",
)

TEST_PROBE_EVIDENCE = (
    "tests/crashcar/data/template_shape_map_corpus.json",
    "tests/crashcar/numeric_oracle.py",
    "tests/crashcar/numeric_test_support.py",
    "tests/crashcar/run_checked_command.sh",
    "tests/crashcar/run_compiled_contracts.py",
    "tests/crashcar/run_numeric_contracts.py",
    "tests/crashcar/run_schema_roundtrip.py",
    "tests/crashcar/runtime_source_closure.py",
    "tests/crashcar/test_runtime_source_closure.py",
    "tests/crashcar/support/crashcar_contract_probe.c",
    "tests/crashcar/support/crashcar_live_reader_probe.c",
    "tests/crashcar/support/emit_postcoh_schema_rows.c",
    "tests/crashcar/support/run_crashcar_live_reader_probe.sh",
    "tests/crashcar/test_assigned_far_piecewise.py",
    "tests/crashcar/test_assignment_failures.py",
    "tests/crashcar/test_beta_grid.py",
    "tests/crashcar/test_calculated_far_golden.py",
    "tests/crashcar/test_dof_by_source_class.py",
    "tests/crashcar/test_fail_closed_numeric_state.py",
    "tests/crashcar/test_gaussian_llr_golden.py",
    "tests/crashcar/test_hl_only_contract.py",
    "tests/crashcar/test_postcoh_schema_roundtrip.py",
    "tests/crashcar/test_template_shape_map_contract.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_authority_contract.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_b4a_row_json_contract.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_bg_only_seed_gate.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_finalsink_pending_normal_path.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_finalsink_source_behavior.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_graph_modes.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_plot_authority.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_plot_live_source_behavior.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_plot_normal_coincs.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_crashcar_runtime_error_propagation.py",
    "gstlal-spiir/share/scripts/crashcar/tests/test_wguo_gaussian_llr.py",
)

PRODUCTION_RUNTIME = tuple(sorted(set(
    LAUNCH_RUNTIME + PYTHON_RUNTIME + C_AUTOMAKE_SOURCES +
    C_AUTOMAKE_HEADERS + C_DIRECT_HEADERS + BUILD_BINDINGS
)))
EVIDENCE_RUNTIME = tuple(sorted(set(
    AUTHORITY + EXTERNAL_ACCEPTANCE_RUNTIME + TEST_PROBE_EVIDENCE
)))

STAGED_HELPERS = (
    "crashcar.sh",
    "crashcar_controller.sh",
    "crashcar_frozen_injection_workflow.sh",
    "crashcar_live_background.py",
    "crashcar_sbatch.sh",
    "crashcar_pipeline.sh",
    "dump_segment_livetime_csv.py",
    "export_template_shape_map.py",
)

SHELL_BINDINGS = {
    "scripts/crashcar.sh": (
        "gstlal-spiir/share/scripts/crashcar/crashcar.sh",
    ),
    "gstlal-spiir/share/scripts/crashcar/crashcar.sh": STAGED_HELPERS,
    "gstlal-spiir/share/scripts/crashcar/crashcar_controller.sh": (
        "crashcar_live_background.py",
        "crashcar_sbatch.sh",
        "crashcar_pipeline.sh",
        "dump_segment_livetime_csv.py",
        "export_template_shape_map.py",
    ),
    "gstlal-spiir/share/scripts/crashcar/crashcar_frozen_injection_workflow.sh": (
        "crashcar_live_background.py",
        "crashcar.sh",
    ),
    "gstlal-spiir/share/scripts/crashcar/crashcar_sbatch.sh": (
        "crashcar_pipeline.sh",
    ),
    "gstlal-spiir/share/scripts/crashcar/crashcar_pipeline.sh": (
        "gstlal_inspiral_postcohspiir_online",
    ),
}

COMPILED_MODULE = {
    "gstlal_spiir.pipemodules.postcohtable._postcohtable":
        "gstlal-spiir/python/pipemodules/postcohtable/_postcohtable.c",
}

PROHIBITED = (".orig", ".rej", ".bak", ".pytest_cache", "__pycache__",
              ".codex_work", "smoke_runs")


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def am_values(path, variable):
    values = []
    active = False
    found = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not active:
            match = re.match(
                r"^" + re.escape(variable) + r"\s*=\s*(.*)$", line
            )
            if not match:
                continue
            found = True
            active = True
            text = match.group(1).strip()
        else:
            text = line.strip()
        continued = text.endswith("\\")
        if continued:
            text = text[:-1].strip()
        text = text.split("#", 1)[0].strip()
        if text:
            values.extend(text.split())
        active = continued
    if not found:
        raise ValueError("missing Automake variable " + variable)
    return tuple(values)


def module_map():
    output = {}
    for item in PYTHON_RUNTIME:
        path = ROOT / item
        if path.suffix != ".py":
            continue
        relative = path.resolve().relative_to(PYROOT.resolve())
        parts = list(relative.parts)
        if parts[-1] == "__init__.py":
            parts.pop()
        else:
            parts[-1] = Path(parts[-1]).stem
        output[".".join(["gstlal_spiir"] + parts)] = item
    output.update(COMPILED_MODULE)
    return output


def resolve_from(path, node, mapping):
    name = node.module or ""
    if node.level:
        relative = path.resolve().relative_to(PYROOT.resolve())
        parts = list(relative.parts)
        if parts[-1] == "__init__.py":
            parts.pop()
        else:
            parts[-1] = Path(parts[-1]).stem
            parts.pop()
        package = ".".join(["gstlal_spiir"] + parts)
        name = importlib.util.resolve_name("." * node.level + name, package)
    targets = []
    if name in mapping:
        targets.append(mapping[name])
    for alias in node.names:
        candidate = name + "." + alias.name
        if candidate in mapping:
            targets.append(mapping[candidate])
    return tuple(sorted(set(targets))), name


def bounded_python_import_closure():
    mapping = module_map()
    queue = ["gstlal-spiir/bin/gstlal_inspiral_postcohspiir_online"]
    seen = set()
    edges = {}
    unresolved = []
    while queue:
        item = queue.pop(0)
        if item in seen:
            continue
        seen.add(item)
        path = ROOT / item
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in mapping:
                        targets.add(mapping[alias.name])
                    elif alias.name.startswith("gstlal_spiir"):
                        unresolved.append(item + ": import " + alias.name)
            elif isinstance(node, ast.ImportFrom):
                found, name = resolve_from(path, node, mapping)
                targets.update(found)
                if name.startswith("gstlal_spiir") and not found:
                    unresolved.append(item + ": from " + name)
        edges[item] = sorted(targets)
        queue.extend(target for target in sorted(targets)
                     if target.endswith(".py") and target not in seen)
    return tuple(sorted(seen)), edges, tuple(sorted(set(unresolved)))


def staged_from_launcher():
    text = (
        ROOT / "gstlal-spiir/share/scripts/crashcar/crashcar.sh"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"for script in \\\n(.*?); do\n\s*copy_crashcar_script",
        text,
        re.DOTALL,
    )
    if not match:
        return ()
    return tuple(re.findall(
        r"^\s*([A-Za-z0-9_.-]+)\s*\\?\s*$",
        match.group(1),
        re.MULTILINE,
    ))


def local_c_include(source, include_name):
    source = Path(source)
    candidates = (
        source.parent / include_name,
        CUDA / include_name,
        SPIIR / "include" / include_name,
        SPIIR / "gst/lib/include" / include_name,
        SPIIR / "lib/include" / include_name,
    )
    output = []
    for path in candidates:
        path = path.resolve()
        if path.is_file() and path not in output:
            output.append(path)
    return tuple(output)


def bounded_c_include_check():
    allowed = set(PRODUCTION_RUNTIME)
    include_re = re.compile(
        r'^\s*#\s*include\s*[<"]([^>"]+)[>"]',
        re.MULTILINE,
    )
    edges = {}
    unresolved = []
    ambiguous = []
    for item in C_AUTOMAKE_SOURCES + C_AUTOMAKE_HEADERS + C_DIRECT_HEADERS:
        path = ROOT / item
        targets = set()
        for name in include_re.findall(
            path.read_text(encoding="utf-8", errors="ignore")
        ):
            found = local_c_include(path, name)
            if len(found) > 1:
                ambiguous.append(
                    item + ": " + name + " -> " +
                    ",".join(str(value.relative_to(ROOT)) for value in found)
                )
            if found:
                target = str(found[0].relative_to(ROOT))
                targets.add(target)
                if target not in allowed:
                    unresolved.append(item + ": local include " + target)
        edges[item] = sorted(targets)
    return edges, tuple(sorted(set(unresolved))), tuple(sorted(set(ambiguous)))


def git_state(paths):
    def names(command):
        raw = subprocess.check_output(command, cwd=str(ROOT)).decode("utf-8")
        output = set(raw.rstrip("\0").split("\0"))
        output.discard("")
        return output
    tracked = names(["git", "ls-files", "-z"])
    dirty = names(["git", "diff", "--name-only", "-z"])
    staged = names(["git", "diff", "--cached", "--name-only", "-z"])
    untracked = names(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"]
    )
    selected = set(paths)
    return {
        "tracked": sorted(selected & tracked),
        "dirty": sorted(selected & dirty),
        "staged": sorted(selected & staged),
        "untracked": sorted(selected & untracked),
    }


def hashes(paths, errors):
    output = {}
    for item in paths:
        path = ROOT / item
        if any(token in item for token in PROHIBITED):
            errors.append("prohibited path in closure: " + item)
        elif not path.is_file() or path.is_symlink():
            errors.append("missing/nonregular closure path: " + item)
        else:
            output[item] = sha256(path)
    return dict(sorted(output.items()))


def manifest(value):
    return "".join(
        "{0}  {1}\n".format(value[path], path)
        for path in sorted(value)
    )


def build_report():
    errors = []

    expected_sources = tuple(
        "gstlal-spiir/gst/cuda/" + value
        for value in am_values(CUDA / "Makefile.am",
                               "libgstcuda_la_SOURCES")
    )
    expected_headers = tuple(
        "gstlal-spiir/gst/cuda/" + value
        for value in am_values(CUDA / "Makefile.am", "noinst_HEADERS")
    )
    if expected_sources != C_AUTOMAKE_SOURCES:
        errors.append("C Automake source allowlist mismatch")
    if expected_headers != C_AUTOMAKE_HEADERS:
        errors.append("C Automake header allowlist mismatch")

    pipe_make = SPIIR / "python/pipemodules/Makefile.am"
    post_make = SPIIR / "python/pipemodules/postcohtable/Makefile.am"
    installed_python = (
        "gstlal-spiir/python/__init__.py",
    ) + tuple(
        "gstlal-spiir/python/pipemodules/" + value
        for value in am_values(pipe_make, "pipemodules_PYTHON")
    ) + tuple(
        "gstlal-spiir/python/pipemodules/postcohtable/" + value
        for value in am_values(post_make, "postcohtable_PYTHON")
    ) + tuple(
        "gstlal-spiir/python/pipemodules/postcohtable/" + value
        for value in am_values(post_make, "_postcohtable_la_SOURCES")
    )
    if set(installed_python) != set(PYTHON_RUNTIME):
        errors.append("installed Python allowlist mismatch")

    python_paths, python_edges, python_unresolved = (
        bounded_python_import_closure()
    )
    if not set(python_paths).issubset(set(PRODUCTION_RUNTIME)):
        errors.append("formal Python closure escaped production allowlist")
    errors.extend("Python unresolved: " + value
                  for value in python_unresolved)

    c_edges, c_unresolved, c_ambiguous = bounded_c_include_check()
    errors.extend("C unresolved: " + value for value in c_unresolved)
    errors.extend("C ambiguous: " + value for value in c_ambiguous)

    staged = staged_from_launcher()
    if staged != STAGED_HELPERS:
        errors.append("launcher staged-helper allowlist mismatch")
    for source, tokens in sorted(SHELL_BINDINGS.items()):
        text = (ROOT / source).read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                errors.append(
                    "shell binding missing: " + source + " -> " + token
                )

    plot_text = (ROOT / EXTERNAL_ACCEPTANCE_RUNTIME[0]).read_text(
        encoding="utf-8"
    )
    support_text = (ROOT / EXTERNAL_ACCEPTANCE_RUNTIME[1]).read_text(
        encoding="utf-8"
    )
    dynamic_imports = {
        "plot_to_support": "crashcar_plot_support.py" in plot_text,
        "plot_to_numeric": "crashcar_numeric.py" in plot_text,
        "support_to_numeric": "crashcar_numeric.py" in support_text,
    }
    errors.extend("dynamic import missing: " + name
                  for name, valid in dynamic_imports.items() if not valid)

    bin_make = (SPIIR / "bin/Makefile.am").read_text(encoding="utf-8")
    build_checks = {
        "online_installed":
            "gstlal_inspiral_postcohspiir_online" in bin_make,
        "plot_installed": "crashcar_plot.py" in bin_make,
        "plot_helpers_installed": all(
            value in bin_make
            for value in ("crashcar_plot_support.py",
                          "crashcar_numeric.py")
        ),
        "cuda_driver_bound":
            "gnuscripts/cudalt.py" in
            (CUDA / "Makefile.am").read_text(encoding="utf-8"),
    }
    errors.extend("build binding failed: " + name
                  for name, valid in build_checks.items() if not valid)

    production_hashes = hashes(PRODUCTION_RUNTIME, errors)
    evidence_hashes = hashes(EVIDENCE_RUNTIME, errors)
    overlap = sorted(set(production_hashes) & set(evidence_hashes))
    if overlap:
        errors.append("production/evidence overlap: " + ",".join(overlap))

    return {
        "schema_version": 1,
        "production_runtime_count": len(production_hashes),
        "evidence_runtime_count": len(evidence_hashes),
        "production_runtime_hashes": production_hashes,
        "evidence_runtime_hashes": evidence_hashes,
        "production_git_state": git_state(production_hashes),
        "evidence_git_state": git_state(evidence_hashes),
        "formal_python_paths": list(python_paths),
        "formal_python_edges": python_edges,
        "python_unresolved": list(python_unresolved),
        "C_include_edges": c_edges,
        "C_unresolved": list(c_unresolved),
        "C_ambiguous": list(c_ambiguous),
        "staged_helpers": list(staged),
        "shell_bindings": {
            key: list(value) for key, value in sorted(SHELL_BINDINGS.items())
        },
        "dynamic_imports": dynamic_imports,
        "build_checks": build_checks,
        "errors": errors,
        "all_passed": not errors,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--production-manifest")
    parser.add_argument("--evidence-manifest")
    parser.add_argument("--report")
    args = parser.parse_args()

    report = build_report()
    production = manifest(report["production_runtime_hashes"])
    evidence = manifest(report["evidence_runtime_hashes"])
    if args.production_manifest:
        Path(args.production_manifest).write_text(
            production, encoding="utf-8"
        )
    if args.evidence_manifest:
        Path(args.evidence_manifest).write_text(
            evidence, encoding="utf-8"
        )
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        "all_passed": report["all_passed"],
        "production_runtime_count": report["production_runtime_count"],
        "evidence_runtime_count": report["evidence_runtime_count"],
        "production_manifest_sha256":
            hashlib.sha256(production.encode("utf-8")).hexdigest(),
        "evidence_manifest_sha256":
            hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
        "python_unresolved": report["python_unresolved"],
        "C_unresolved": report["C_unresolved"],
        "C_ambiguous": report["C_ambiguous"],
        "errors": report["errors"],
    }, indent=2, sort_keys=True))
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
