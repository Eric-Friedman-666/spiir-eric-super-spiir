#!/usr/bin/env python3
"""Compile and validate bounded exported crashcar C API contracts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import tempfile


CRASHCAR_LLR_SCALAR_FIELDS = (
    "H1_LLR",
    "L1_LLR",
)
NORMAL_OWNED_ROW_FIELDS = (
    "far_sngl",
    "far_1w_sngl",
    "far_1d_sngl",
    "far_2h_sngl",
)

IFO_EXPECTED = {
    "H1L1": True,
    "H1": False,
    "L1": False,
    "H1H1": False,
    "H1L1H1": False,
    "H1junk": False,
    "H1V1": False,
    "H1K1": False,
    "V1": False,
    "K1": False,
    "L1H1": False,
    "H1L1V1": False,
}
ROUTE_EXPECTED = {
    "H": ("H1", 1, "H1", [True, False, False, False]),
    "Hv": ("H1V1", 1, "H1", [True, False, False, False]),
    "L": ("L1", 2, "L1", [False, True, False, False]),
    "Lv": ("L1V1", 2, "L1", [False, True, False, False]),
    "HL": ("H1L1", 3, "MULTI", [False, False, False, False]),
    "HLV": ("H1L1V1", 3, "MULTI", [False, False, False, False]),
    "V": ("V1", 4, "V1_ONLY", [False, False, False, False]),
    "invalid": ("L1H1", 0, "INVALID", [False, False, False, False]),
}
PARSER_CASES = (
    {
        "name": "valid_h1_bns_single_digit_exponent",
        "line": "0,0,0,0x1.0000000000000p+3,120,H1,BNS",
        "valid": True,
        "expected": {
            "ifo_id": 0,
            "ifo": "H1",
            "bankid": 0,
            "tmplt_idx": 0,
            "autocorr_power": 8.0,
            "dof": 120.0,
            "source_class": "BNS",
        },
    },
    {
        "name": "valid_l1_bns_single_digit_negative_exponent",
        "line": "1,99,999,0x1.0000000000000p-3,120,L1,BNS",
        "valid": True,
        "expected": {
            "ifo_id": 1,
            "ifo": "L1",
            "bankid": 99,
            "tmplt_idx": 999,
            "autocorr_power": 0.125,
            "dof": 120.0,
            "source_class": "BNS",
        },
    },
    {
        "name": "valid_h1_bns",
        "line": "0,0,7,0x1.4000000000000p+10,120,H1,BNS",
        "valid": True,
        "expected": {
            "ifo_id": 0,
            "ifo": "H1",
            "bankid": 0,
            "tmplt_idx": 7,
            "autocorr_power": 1280.0,
            "dof": 120.0,
            "source_class": "BNS",
        },
    },
    {
        "name": "valid_l1_nsbh",
        "line": "1,100,999,0x1.a000000000000p+10,600,L1,NSBH",
        "valid": True,
        "expected": {
            "ifo_id": 1,
            "ifo": "L1",
            "bankid": 100,
            "tmplt_idx": 999,
            "autocorr_power": 1664.0,
            "dof": 600.0,
            "source_class": "NSBH",
        },
    },
    {
        "name": "reject_header",
        "line": (
            "ifo_id,bankid,tmplt_idx,autocorr_power,dof,ifo,source_class"
        ),
        "valid": False,
    },
    {
        "name": "reject_missing_exponent_digit",
        "line": "0,0,7,0x1.4000000000000p+,120,H1,BNS",
        "valid": False,
    },
    {
        "name": "reject_short_mantissa",
        "line": "0,0,7,0x1.400000000000p+3,120,H1,BNS",
        "valid": False,
    },
    {
        "name": "reject_decimal_power",
        "line": "0,0,7,2.5,120,H1,BNS",
        "valid": False,
    },
    {
        "name": "reject_uppercase_hex",
        "line": "0,0,7,0x1.A000000000000p+10,120,H1,BNS",
        "valid": False,
    },
    {
        "name": "reject_leading_zero_bank",
        "line": "0,00,7,0x1.4000000000000p+10,120,H1,BNS",
        "valid": False,
    },
    {
        "name": "reject_wrong_dof",
        "line": "0,0,7,0x1.4000000000000p+10,600,H1,BNS",
        "valid": False,
    },
    {
        "name": "reject_ifo_name_conflict",
        "line": "1,100,7,0x1.4000000000000p+10,600,H1,NSBH",
        "valid": False,
    },
    {
        "name": "reject_source_class_conflict",
        "line": "0,0,7,0x1.4000000000000p+10,120,H1,NSBH",
        "valid": False,
    },
    {
        "name": "reject_template_out_of_range",
        "line": "0,0,1000,0x1.4000000000000p+10,120,H1,BNS",
        "valid": False,
    },
    {
        "name": "reject_missing_field",
        "line": "0,0,7,0x1.4000000000000p+10,120,H1",
        "valid": False,
    },
    {
        "name": "reject_embedded_newline",
        "line": "0,0,7,0x1.4000000000000p+10,120,H1,BNS\n",
        "valid": False,
    },
)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkg_config(*packages):
    output = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", *packages], text=True
    )
    return shlex.split(output)


def _required_directory(path, label):
    requested = Path(path)
    if not requested.is_absolute():
        raise SystemExit(f"{label} must be an absolute path: {requested}")
    resolved = requested.resolve()
    if not resolved.is_dir():
        raise SystemExit(f"{label} missing/not-directory: {resolved}")
    return resolved


def _required_file(path, label):
    requested = Path(path)
    if not requested.is_absolute():
        raise SystemExit(f"{label} must be an absolute path: {requested}")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise SystemExit(f"{label} missing/not-regular: {resolved}")
    return resolved


def _require_within(path, root, label):
    try:
        path.relative_to(root)
    except ValueError:
        raise SystemExit(f"{label} escapes fresh install root: {path}")
    return path


def compile_probe(
    source,
    executable,
    staged_plugin,
    support_library,
    fresh_source_root,
    fresh_build_root,
):
    package_source = fresh_source_root / "gstlal-spiir"
    plugin_dir = staged_plugin.parent
    support_dir = support_library.parent
    command = [
        os.environ.get("CC", "gcc"),
        "-Wall",
        "-Wextra",
        "-Werror",
        str(source),
        "-o",
        str(executable),
        "-I",
        str(package_source / "include"),
        "-I",
        str(package_source / "gst/cuda"),
        "-I",
        str(package_source / "gst/lib/include"),
        "-I",
        str(package_source / "lib/include"),
        "-I",
        str(fresh_build_root),
        f"-Wl,-rpath,{plugin_dir}",
        f"-Wl,-rpath,{support_dir}",
        str(staged_plugin),
        str(support_library),
        "-lm",
    ] + pkg_config(
        "lal",
        "glib-2.0",
        "gobject-2.0",
        "gstreamer-1.0",
        "gstreamer-base-1.0",
    )
    result = subprocess.run(
        command, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode != 0:
        raise RuntimeError(
            "probe compile failed\n"
            + result.stdout
            + result.stderr
        )
    return command, result


def add_check(checks, failures, name, passed):
    checks[name] = bool(passed)
    if not passed:
        failures.append(name)



def validate_row_contracts(evidence, checks, failures):
    max_nifo = evidence.get("max_nifo")
    add_check(checks, failures, "row_prepare.max_nifo", max_nifo == 4)
    row_cases = evidence.get("row_prepare", {})
    case = row_cases.get("prepare_all_llrs", {})
    before = case.get("before", {})
    after = case.get("after", {})
    expected_fields = set(
        CRASHCAR_LLR_SCALAR_FIELDS + NORMAL_OWNED_ROW_FIELDS)
    add_check(
        checks,
        failures,
        "row_prepare.prepare_all_llrs.field_set",
        set(before) == expected_fields and set(after) == expected_fields,
    )
    for field in CRASHCAR_LLR_SCALAR_FIELDS:
        add_check(
            checks,
            failures,
            "row_prepare.prepare_all_llrs.%s.canonical_zero" % field,
            isinstance(before.get(field), (int, float))
            and before.get(field) != 0
            and after.get(field) == 0,
        )
    for field in NORMAL_OWNED_ROW_FIELDS:
        add_check(
            checks,
            failures,
            "row_prepare.prepare_all_llrs.%s.a107_preserved" % field,
            before.get(field) == after.get(field)
            and len(before.get(field, [])) == max_nifo,
        )
    add_check(
        checks,
        failures,
        "row_prepare.null_table.no_op",
        row_cases.get("null_table_completed") is True,
    )

def validate_route_contracts(evidence, checks, failures):
    routes = evidence.get("routes", {})
    add_check(
        checks,
        failures,
        "routes.case_set",
        set(routes) == set(ROUTE_EXPECTED),
    )
    for label, expected in ROUTE_EXPECTED.items():
        ifos, route_id, route_name, assigns = expected
        actual = routes.get(label, {})
        add_check(
            checks,
            failures,
            f"routes.{label}.mapping",
            actual.get("ifos") == ifos
            and actual.get("route_id") == route_id
            and actual.get("route") == route_name,
        )
        add_check(
            checks,
            failures,
            f"routes.{label}.route_assigns_ifo",
            actual.get("assigns_ifo") == assigns
            and actual.get("assigns_invalid_low") is False
            and actual.get("assigns_invalid_high") is False,
        )
    add_check(
        checks,
        failures,
        "routes.invalid_enum_assigns_none",
        evidence.get("invalid_route_assigns_ifo")
        == [False, False, False, False],
    )


def validate_ifo_contracts(evidence, checks, failures):
    add_check(
        checks,
        failures,
        "ifo_validator.exact",
        evidence.get("ifo_validator") == IFO_EXPECTED,
    )


def run_parser_cases(executable, runtime_env):
    results = {}
    for case in PARSER_CASES:
        result = subprocess.run(
            [str(executable), "--parse-template-row", case["line"]],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=runtime_env,
        )
        actual = None
        if result.returncode == 0:
            try:
                actual = json.loads(result.stdout)
            except (TypeError, ValueError):
                actual = None
        if case["valid"]:
            passed = (
                result.returncode == 0
                and actual == case["expected"]
                and result.stderr == ""
            )
        else:
            passed = (
                result.returncode == 4
                and result.stdout == ""
                and result.stderr == ""
            )
        results[case["name"]] = {
            "line": case["line"],
            "expected_valid": case["valid"],
            "expected": case.get("expected"),
            "actual": actual,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "passed": passed,
        }
    return results


def run_full_template_map(executable, runtime_env, template_map):
    template_map = Path(template_map).resolve()
    expected_sha256 = sha256_file(template_map)
    result = subprocess.run(
        [
            str(executable),
            "--validate-template-map",
            str(template_map),
            expected_sha256,
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=runtime_env,
    )
    actual = None
    if result.returncode == 0:
        try:
            actual = json.loads(result.stdout)
        except (TypeError, ValueError):
            actual = None
    passed = (
        result.returncode == 0
        and result.stderr == ""
        and actual == {
            "sha256": expected_sha256,
            "line_count": 768001,
            "row_count": 768000,
            "ifos": 2,
            "banks": 384,
            "templates_per_bank": 1000,
        }
    )
    return {
        "path": str(template_map),
        "sha256": expected_sha256,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "actual": actual,
        "passed": passed,
    }


def run_ifo_cli_cases(executable, runtime_env):
    results = {}
    for value, expected in IFO_EXPECTED.items():
        result = subprocess.run(
            [str(executable), "--validate-ifos", value],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=runtime_env,
        )
        results[value] = {
            "expected": expected,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "passed": (
                result.returncode == (0 if expected else 5)
                and result.stdout == ""
                and result.stderr == ""
            ),
        }
    return results


def runtime_linked_library(ldd_output, soname):
    for line in ldd_output.splitlines():
        if f"{soname} =>" not in line:
            continue
        target = line.split("=>", 1)[1].split("(", 1)[0].strip()
        return str(Path(target).resolve())
    return None


def outer(
    staged_plugin_path,
    fresh_source_root_path,
    fresh_build_root_path,
    fresh_install_root_path,
    support_library_path,
    observed_path=None,
    template_map=None,
):
    fresh_source_root = _required_directory(
        fresh_source_root_path, "fresh source root"
    )
    fresh_build_root = _required_directory(
        fresh_build_root_path, "fresh build root"
    )
    fresh_install_root = _required_directory(
        fresh_install_root_path, "fresh install root"
    )
    source = _required_file(
        fresh_source_root
        / "tests/crashcar/support/crashcar_contract_probe.c",
        "fresh contract probe source",
    )
    staged_plugin = _require_within(
        _required_file(staged_plugin_path, "staged plugin"),
        fresh_install_root,
        "staged plugin",
    )
    support_library = _require_within(
        _required_file(support_library_path, "fresh support library"),
        fresh_install_root,
        "fresh support library",
    )
    plugin_dir = staged_plugin.parent
    support_dir = support_library.parent
    runtime_env = os.environ.copy()
    runtime_env["LD_LIBRARY_PATH"] = os.pathsep.join(
        (
            str(plugin_dir),
            str(support_dir),
            runtime_env.get("LD_LIBRARY_PATH", ""),
        )
    )

    with tempfile.TemporaryDirectory(prefix="crashcar-contract-") as name:
        temp_root = Path(name)
        executable = temp_root / "crashcar_contract_probe"
        command, compile_result = compile_probe(
            source,
            executable,
            staged_plugin,
            support_library,
            fresh_source_root,
            fresh_build_root,
        )
        run = subprocess.run(
            [str(executable)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=runtime_env,
        )
        if run.returncode != 0:
            raise RuntimeError(
                f"contract probe failed with {run.returncode}\n"
                + run.stdout
                + run.stderr
            )
        evidence = json.loads(run.stdout)
        ldd_result = subprocess.run(
            ["ldd", str(executable)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=runtime_env,
        )
        linked_plugin = runtime_linked_library(
            ldd_result.stdout, "libgstcuda.so.0"
        )
        linked_support_library = runtime_linked_library(
            ldd_result.stdout, "libgstlalspiir.so.0"
        )

        checks = {}
        failures = []
        add_check(
            checks,
            failures,
            "schema_version",
            evidence.get("schema_version") == 4,
        )
        validate_row_contracts(evidence, checks, failures)
        validate_route_contracts(evidence, checks, failures)
        validate_ifo_contracts(evidence, checks, failures)

        parser_results = run_parser_cases(executable, runtime_env)
        for case_name, result in parser_results.items():
            add_check(
                checks,
                failures,
                f"template_shape_row_parser.{case_name}",
                result["passed"],
            )
        full_map_result = None
        if template_map is not None:
            full_map_result = run_full_template_map(
                executable, runtime_env, template_map
            )
            add_check(
                checks,
                failures,
                "template_shape_full_map.real_768000_rows",
                full_map_result["passed"],
            )
        ifo_cli_results = run_ifo_cli_cases(executable, runtime_env)
        for value, result in ifo_cli_results.items():
            add_check(
                checks,
                failures,
                f"ifo_validator_cli.{value}",
                result["passed"],
            )
        add_check(
            checks,
            failures,
            "plugin.runtime_binding_exact",
            linked_plugin == str(staged_plugin),
        )
        add_check(
            checks,
            failures,
            "plugin.support_runtime_binding_exact",
            linked_support_library == str(support_library),
        )

        evidence["template_shape_row_parser"] = {
            "scope": "EXPORTED_SINGLE_ROW_PARSER_ONLY",
            "cases": parser_results,
            "all_passed": all(
                result["passed"] for result in parser_results.values()
            ),
        }
        evidence["template_shape_full_map"] = (
            full_map_result
            if full_map_result is not None
            else {"scope": "NOT_REQUESTED", "passed": False}
        )
        evidence["ifo_validator_cli"] = {
            "cases": ifo_cli_results,
            "all_passed": all(
                result["passed"] for result in ifo_cli_results.values()
            ),
        }
        evidence["validation"] = {
            "checks": checks,
            "failures": failures,
            "all_passed": not failures,
        }
        evidence["coverage"] = {
            "prepare_row_llrs_exported_api": "PASS"
            if not any(
                failure.startswith("row_prepare.") for failure in failures
            )
            else "FAIL",
            "final_route_exported_api": "PASS"
            if not any(failure.startswith("routes.") for failure in failures)
            else "FAIL",
            "ifo_validator_exported_api": "PASS"
            if not any(
                failure.startswith("ifo_validator") for failure in failures
            )
            else "FAIL",
            "template_shape_row_parser_exported_api": "PASS"
            if evidence["template_shape_row_parser"]["all_passed"]
            else "FAIL",
            "real_full_map_exported_parser_and_order_fixture":
                "PASS"
                if full_map_result is not None and full_map_result["passed"]
                else "NOT_RUN",
            "full_transform_route_matrix": "REQUIRED_OPEN_UNTIL_FRESH_CLEAN_BUILD",
        }

        if observed_path is None:
            observed_path = temp_root / "observed.json"
        else:
            observed_path = Path(observed_path).resolve()
            observed_path.parent.mkdir(parents=True, exist_ok=True)
        log_path = observed_path.with_suffix(
            observed_path.suffix + ".execution.log"
        )
        log_text = (
            "COMPILE_COMMAND\n"
            + shlex.join(command)
            + "\nCOMPILE_RETURN_CODE\n"
            + str(compile_result.returncode)
            + "\nCOMPILE_STDOUT\n"
            + compile_result.stdout
            + "COMPILE_STDERR\n"
            + compile_result.stderr
            + "PROBE_RETURN_CODE\n"
            + str(run.returncode)
            + "\nPROBE_STDOUT\n"
            + run.stdout
            + "PROBE_STDERR\n"
            + run.stderr
            + "LDD_STDOUT\n"
            + ldd_result.stdout
            + "LDD_STDERR\n"
            + ldd_result.stderr
            + "PARSER_RUNS\n"
            + json.dumps(parser_results, indent=2, sort_keys=True)
            + "\nFULL_TEMPLATE_MAP_RUN\n"
            + json.dumps(full_map_result, indent=2, sort_keys=True)
            + "\nIFO_VALIDATOR_CLI_RUNS\n"
            + json.dumps(ifo_cli_results, indent=2, sort_keys=True)
            + "\n"
        )
        log_path.write_text(log_text, encoding="utf-8")
        evidence["producer"] = {
            "functions_under_test": [
                "crashcar_singlefar_prepare_row_llrs",
                "crashcar_singlefar_final_route_from_ifos",
                "crashcar_singlefar_route_assigns_ifo",
                "crashcar_singlefar_ifos_valid",
                "crashcar_singlefar_parse_template_shape_row",
            ],
            "probe_source": str(source.resolve()),
            "probe_source_sha256": sha256_file(source),
            "fresh_source_root": str(fresh_source_root),
            "fresh_build_root": str(fresh_build_root),
            "fresh_install_root": str(fresh_install_root),
            "staged_plugin_requested": str(Path(staged_plugin_path)),
            "staged_plugin": str(staged_plugin),
            "staged_plugin_sha256": sha256_file(staged_plugin),
            "runtime_linked_object": linked_plugin,
            "support_library_requested": str(Path(support_library_path)),
            "support_library": str(support_library),
            "support_library_sha256": sha256_file(support_library),
            "runtime_support_library": linked_support_library,
            "runtime_library_path": runtime_env["LD_LIBRARY_PATH"],
            "compile_command": command,
            "warning_policy": "-Wall -Wextra -Werror",
            "execution_log": str(log_path),
            "execution_log_sha256": sha256_file(log_path),
            "synthetic_result_only": False,
        }
        observed_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        if failures:
            raise RuntimeError(
                "compiled contract failures: " + ", ".join(failures)
            )
        print(f"PASS exported compiled contracts: {observed_path}")
        if full_map_result is not None:
            print(
                "PASS real 768000-row full-map parser/order fixture: "
                + full_map_result["path"]
            )
        print("OPEN full transform route matrix: run compiled live-reader ""probe after fresh clean build")


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--staged-plugin", required=True)
    parser.add_argument("--support-library", required=True)
    parser.add_argument("--observed")
    parser.add_argument("--template-map")
    args = parser.parse_args()
    outer(
        Path(args.staged_plugin),
        Path(args.source_root),
        Path(args.build_root),
        Path(args.install_root),
        Path(args.support_library),
        Path(args.observed) if args.observed else None,
        Path(args.template_map) if args.template_map else None,
    )


if __name__ == "__main__":
    main()
