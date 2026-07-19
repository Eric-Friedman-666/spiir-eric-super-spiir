#!/usr/bin/env python3
"""Exercise the built A109 wrapper and normal LIGO-LW output constructors."""

import argparse
import ctypes
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SITE_REL = Path("lib/python3.10/site-packages")
PACKAGE_REL = Path("gstlal_spiir/pipemodules")
IFOS = ("H1", "L1", "V1", "K1")
CASE_TYPES = {
    4242: "single_assigned",
    4243: "single_no_valid_far",
    4244: "multi_owned",
}
EXPECTED_ROWS = {
    4242: {
        "ifos": "H1",
        "H1_LLR": 101.25,
        "L1_LLR": 0.0,
        "far_sngl": (1.25e-5, 0.0, 0.0, 0.0),
    },
    4243: {
        "ifos": "H1",
        "H1_LLR": 111.5,
        "L1_LLR": 0.0,
        "far_sngl": (0.0, 0.0, 0.0, 0.0),
    },
    4244: {
        "ifos": "H1L1V1",
        "H1_LLR": 301.25,
        "L1_LLR": 302.5,
        "far_sngl": (11.0, 12.0, 13.0, 0.0),
    },
}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def snapshot_row(row, ligolw_row=False):
    if ligolw_row:
        far = tuple(
            float(getattr(row, "far_sngl_" + ifo)) for ifo in IFOS)
    else:
        far = tuple(float(row.far_sngl[index]) for index in range(4))
    return {
        "event_id": int(row.event_id),
        "bankid": int(row.bankid),
        "tmplt_idx": int(row.tmplt_idx),
        "ifos": str(row.ifos),
        "H1_LLR": float(row.H1_LLR),
        "L1_LLR": float(row.L1_LLR),
        "far_sngl": list(far),
        "normal_far": float(row.far),
    }


def assert_a109_column_contract(columns):
    from gstlal_spiir.pipemodules.postcohtable import postcoh_table_def

    columns = tuple(columns)
    expected = postcoh_table_def.postcoh_columns_for_schema_mode(
        postcoh_table_def.POSTCOH_SCHEMA_MODE_CRASHCAR_A109)
    assert columns == expected
    assert len(columns) == 109
    assert columns[-2:] == ("H1_LLR", "L1_LLR")
    return {
        "normal_prefix_count": 107,
        "crashcar_suffix": list(columns[-2:]),
        "total_count": 109,
    }


def assert_values(row, ligolw_row=False):
    event_id = int(row.event_id)
    assert event_id in EXPECTED_ROWS
    expected = EXPECTED_ROWS[event_id]
    actual = snapshot_row(row, ligolw_row=ligolw_row)
    assert actual["event_id"] == event_id
    assert actual["bankid"] == 7
    assert actual["tmplt_idx"] == 11
    assert actual["ifos"] == expected["ifos"]
    assert math.isclose(
        actual["H1_LLR"], expected["H1_LLR"],
        rel_tol=0.0, abs_tol=0.0)
    assert math.isclose(
        actual["L1_LLR"], expected["L1_LLR"],
        rel_tol=0.0, abs_tol=0.0)
    for observed, wanted in zip(
            actual["far_sngl"], expected["far_sngl"]):
        assert math.isclose(
            observed, wanted, rel_tol=1.0e-6, abs_tol=1.0e-12)


def pkg_config(*packages):
    output = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", *packages], text=True)
    return shlex.split(output)


def build_test_site(temp_root):
    source_site = ROOT / "install_local" / SITE_REL
    if not source_site.is_dir():
        raise RuntimeError("installed test package missing: %s" % source_site)
    site = temp_root / "site"
    shutil.copytree(source_site, site, symlinks=True)
    package = site / PACKAGE_REL
    for relative in (
        "postcoh_finalsink.py",
        "postcohtable/postcohtable.py",
        "postcohtable/postcoh_table_def.py",
    ):
        shutil.copy2(
            ROOT / "gstlal-spiir/python/pipemodules" / relative,
            package / relative)
    built_wrapper = (
        ROOT /
        "gstlal-spiir/python/pipemodules/postcohtable/.libs/_postcohtable.so")
    if not built_wrapper.is_file():
        raise RuntimeError("built wrapper missing: %s" % built_wrapper)
    shutil.copy2(
        built_wrapper, package / "postcohtable/_postcohtable.so")
    return site


def compile_emitter(output):
    source = ROOT / "tests/crashcar/support/emit_postcoh_schema_rows.c"
    command = [
        os.environ.get("CC", "gcc"),
        "-Wall", "-Wextra", "-Werror",
        "-shared", "-fPIC", str(source),
        "-o", str(output),
        "-I", str(ROOT / "gstlal-spiir/include"),
    ] + pkg_config("lal")
    result = subprocess.run(
        command, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result.check_returncode()
    return command, result


def content_handler():
    from ligo.lw import ligolw
    from ligo.lw import lsctables
    from gstlal_spiir.pipemodules.postcohtable import postcoh_table_def

    @postcoh_table_def.use_in
    @lsctables.use_in
    class ContentHandler(ligolw.LIGOLWContentHandler):
        pass
    return ContentHandler


def write_fileobj_artifact(xmldoc, path):
    from ligo.lw import utils as ligolw_utils

    payload = BytesIO()
    ligolw_utils.write_fileobj(xmldoc, payload)
    Path(path).write_bytes(payload.getvalue())


def load_postcoh_rows(path):
    from ligo.lw import utils as ligolw_utils
    from gstlal_spiir.pipemodules.postcohtable import postcoh_table_def

    loaded = ligolw_utils.load_fileobj(
        BytesIO(Path(path).read_bytes()), contenthandler=content_handler())
    xmldoc = loaded[0] if isinstance(loaded, (tuple, list)) else loaded
    table = postcoh_table_def.PostcohInspiralTable.get_table(xmldoc)
    return xmldoc, table


def inner(support_library, postcoh_xml, coinc_dir, stage_evidence):
    if os.environ.get("CRASHCAR_TEST_FORCE_INNER_FAILURE") == "1":
        raise RuntimeError("forced inner serialization failure")

    from gstlal_spiir.pipemodules.postcohtable import postcoh_table_def
    from gstlal_spiir.pipemodules.postcohtable import postcohtable
    from gstlal_spiir.pipemodules.postcoh_finalsink import (
        CoincsDocFromPostcoh, PostcohDocument)
    import lal
    assert hasattr(lal, "series")

    support = ctypes.CDLL(str(Path(support_library).resolve()))
    support.crashcar_schema_rows_create.argtypes = [
        ctypes.POINTER(ctypes.c_size_t)]
    support.crashcar_schema_rows_create.restype = ctypes.c_void_p
    support.crashcar_schema_rows_release.argtypes = [ctypes.c_void_p]
    support.crashcar_schema_rows_release.restype = None

    row_bytes = ctypes.c_size_t(0)
    row_pointer = support.crashcar_schema_rows_create(ctypes.byref(row_bytes))
    if not row_pointer or row_bytes.value == 0:
        raise RuntimeError("C support library failed to allocate schema rows")
    try:
        raw_rows = ctypes.string_at(row_pointer, row_bytes.value)
        triggers = postcohtable.from_buffer(raw_rows)
    finally:
        support.crashcar_schema_rows_release(row_pointer)

    assert len(triggers) == 4
    foreground = triggers[1:]
    from_buffer = {}
    for trigger in foreground:
        row = trigger.postcoh_inspiral
        assert_values(row)
        from_buffer[int(row.event_id)] = snapshot_row(row)

    schema_mode = (
        postcoh_table_def.POSTCOH_SCHEMA_MODE_CRASHCAR_A109)
    document = PostcohDocument(postcoh_schema_mode=schema_mode)
    schema_contract = assert_a109_column_contract(document.postcoh_columns)
    table = postcoh_table_def.PostcohInspiralTable.get_table(document.xmldoc)
    for trigger in foreground:
        table.append(trigger.postcoh_inspiral)
    write_fileobj_artifact(document.xmldoc, postcoh_xml)
    xmldoc, rows = load_postcoh_rows(postcoh_xml)
    assert len(rows) == len(EXPECTED_ROWS)
    postcoh_readback = {}
    for row in rows:
        assert_values(row, ligolw_row=True)
        postcoh_readback[int(row.event_id)] = snapshot_row(
            row, ligolw_row=True)
    xmldoc.unlink()
    document.close()

    Path(coinc_dir).mkdir(parents=True, exist_ok=True)
    channel_dict = {
        "H1": "H1:TEST", "L1": "L1:TEST", "V1": "V1:TEST",
    }
    cases = []
    for trigger in foreground:
        event_id = int(trigger.postcoh_inspiral.event_id)
        coinc_xml = Path(coinc_dir) / ("coinc_%d.xml" % event_id)
        coinc = CoincsDocFromPostcoh(
            str(coinc_dir), {}, channel_dict,
            postcoh_schema_mode=schema_mode)
        assert (
            assert_a109_column_contract(coinc.postcoh_columns) ==
            schema_contract)
        coinc.assemble_ligolw_xmldoc(trigger)
        write_fileobj_artifact(coinc.xmldoc, coinc_xml)
        xmldoc, rows = load_postcoh_rows(coinc_xml)
        assert len(rows) == 1
        assert_values(rows[0], ligolw_row=True)
        coinc_readback = snapshot_row(rows[0], ligolw_row=True)
        xmldoc.unlink()
        coinc.close()
        cases.append({
            "name": "event-%d" % event_id,
            "case_type": CASE_TYPES[event_id],
            "from_buffer": from_buffer[event_id],
            "postcoh_document_readback": postcoh_readback[event_id],
            "coincs_document_readback": coinc_readback,
            "artifacts": {
                "postcoh_xml": {
                    "path": str(Path(postcoh_xml).resolve()),
                    "sha256": sha256_file(postcoh_xml),
                },
                "coincs_xml": {
                    "path": str(coinc_xml.resolve()),
                    "sha256": sha256_file(coinc_xml),
                },
            },
        })

    Path(stage_evidence).write_text(
        json.dumps({
            "schema_version": 4,
            "a109_column_contract": schema_contract,
            "cases": cases,
        }, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    print(
        "PASS A109 scalar rows: built wrapper -> PostcohDocument and "
        "CoincsDocFromPostcoh readback")


def outer(evidence_path=None):
    with tempfile.TemporaryDirectory(prefix="crashcar-schema-") as name:
        temp_root = Path(name)
        if evidence_path is None:
            artifact_root = temp_root / "artifacts"
            evidence_path = temp_root / "serialization_evidence.json"
            execution_log = temp_root / "serialization_execution.log"
        else:
            evidence_path = Path(evidence_path).resolve()
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_root = evidence_path.parent / (
                evidence_path.stem + "_artifacts")
            if artifact_root.exists():
                shutil.rmtree(artifact_root)
            execution_log = evidence_path.with_suffix(
                evidence_path.suffix + ".execution.log")
        artifact_root.mkdir(parents=True, exist_ok=True)

        support_library = artifact_root / "libemit_postcoh_schema_rows.so"
        postcoh_xml = artifact_root / "postcoh_document.xml"
        coinc_dir = artifact_root / "coincs"
        stage_evidence = temp_root / "stage_evidence.json"
        compile_command, compile_result = compile_emitter(support_library)
        site = build_test_site(temp_root)
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(site) + os.pathsep + env.get("PYTHONPATH", ""))
        build_libs = [
            ROOT / "gstlal-spiir/lib/.libs",
            ROOT / "gstlal-spiir/gst/lib/.libs",
        ]
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            [str(path) for path in build_libs] +
            [env.get("LD_LIBRARY_PATH", "")])
        inner_command = [
            sys.executable, str(Path(__file__).resolve()), "--inner",
            str(support_library), str(postcoh_xml), str(coinc_dir),
            str(stage_evidence),
        ]
        result = subprocess.run(
            inner_command, check=False, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        command_records = [
            {
                "name": "compile_support_library",
                "command": compile_command,
                "returncode": compile_result.returncode,
                "stdout_sha256": sha256_text(compile_result.stdout),
                "stderr_sha256": sha256_text(compile_result.stderr),
            },
            {
                "name": "inner_serialization",
                "command": inner_command,
                "returncode": result.returncode,
                "stdout_sha256": sha256_text(result.stdout),
                "stderr_sha256": sha256_text(result.stderr),
            },
        ]
        command_status = evidence_path.with_suffix(
            evidence_path.suffix + ".command_status.json")
        command_status.write_text(
            json.dumps({"schema_version": 1, "commands": command_records},
                       indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8")
        execution_log.write_text(
            "COMPILE_EMITTER\n" + shlex.join(compile_command) +
            "\nCOMPILE_RETURN_CODE\n%d" % compile_result.returncode +
            "\nCOMPILE_STDOUT\n" + compile_result.stdout +
            "\nCOMPILE_STDERR\n" + compile_result.stderr +
            "\nINNER\n" + shlex.join(inner_command) +
            "\nINNER_RETURN_CODE\n%d" % result.returncode +
            "\nSTDOUT\n" + result.stdout +
            "\nSTDERR\n" + result.stderr,
            encoding="utf-8")
        result.check_returncode()
        evidence = json.loads(stage_evidence.read_text(encoding="utf-8"))
        extension = (
            ROOT /
            "gstlal-spiir/python/pipemodules/postcohtable/.libs/"
            "_postcohtable.so")
        evidence["producer"] = {
            "python_extension_path": str(extension.resolve()),
            "python_extension_sha256": sha256_file(extension),
            "execution_log_path": str(execution_log.resolve()),
            "execution_log_sha256": sha256_file(execution_log),
            "command_status_path": str(command_status.resolve()),
            "command_status_sha256": sha256_file(command_status),
            "support_library_path": str(support_library),
            "support_library_sha256": sha256_file(support_library),
            "synthetic_result_only": False,
            "command_records": command_records,
            "executed_calls": [
                "postcohtable.from_buffer",
                "PostcohDocument",
                "CoincsDocFromPostcoh.assemble_ligolw_xmldoc",
                "ligolw_utils.write_fileobj",
                "ligolw_utils.load_fileobj",
            ],
        }
        Path(evidence_path).write_text(
            json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) +
            "\n", encoding="utf-8")
        print("PASS serialization evidence: %s" % evidence_path)


def negative_self_test(report_path):
    with tempfile.TemporaryDirectory(
            prefix="crashcar-schema-negative-") as name:
        temp_root = Path(name)
        evidence = temp_root / "must_not_exist.json"
        command = [
            sys.executable, str(Path(__file__).resolve()),
            "--evidence", str(evidence),
        ]
        env = os.environ.copy()
        env["CRASHCAR_TEST_FORCE_INNER_FAILURE"] = "1"
        result = subprocess.run(
            command, check=False, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        execution_log = evidence.with_suffix(
            evidence.suffix + ".execution.log")
        command_status = evidence.with_suffix(
            evidence.suffix + ".command_status.json")
        execution_text = (
            execution_log.read_text(encoding="utf-8")
            if execution_log.is_file() else "")
        command_data = (
            json.loads(command_status.read_text(encoding="utf-8"))
            if command_status.is_file() else {})
        commands = {
            item.get("name"): item
            for item in command_data.get("commands", [])
            if isinstance(item, dict)
        }
        compile_rc = commands.get(
            "compile_support_library", {}).get("returncode")
        inner_rc = commands.get(
            "inner_serialization", {}).get("returncode")
        forced_branch_reached = (
            "forced inner serialization failure" in execution_text)
        passed = (
            result.returncode != 0
            and not evidence.exists()
            and execution_log.is_file()
            and command_status.is_file()
            and compile_rc == 0
            and isinstance(inner_rc, int)
            and inner_rc != 0
            and forced_branch_reached)
        report = {
            "schema_version": 1,
            "test": "forced_inner_failure_propagates_to_top_level",
            "status": "PASS" if passed else "FAIL",
            "command": command,
            "returncode": result.returncode,
            "evidence_created": evidence.exists(),
            "compile_returncode": compile_rc,
            "inner_returncode": inner_rc,
            "forced_branch_reached": forced_branch_reached,
        }
        Path(report_path).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if not passed:
            raise RuntimeError("forced inner failure did not fail closed")
        print("PASS negative runner self-test: %s" % report_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inner", action="store_true")
    parser.add_argument("--evidence")
    parser.add_argument("--negative-self-test")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    if args.inner:
        if len(args.paths) != 4:
            parser.error(
                "--inner requires support-library.so postcoh.xml coinc-dir "
                "stage.json")
        inner(*map(Path, args.paths))
    elif args.negative_self_test:
        negative_self_test(Path(args.negative_self_test))
    else:
        outer(Path(args.evidence) if args.evidence else None)


if __name__ == "__main__":
    main()
