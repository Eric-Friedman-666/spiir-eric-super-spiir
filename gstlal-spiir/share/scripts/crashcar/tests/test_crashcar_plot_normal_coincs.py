from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]
PLOT = REPO / "gstlal-spiir" / "bin" / "crashcar_plot.py"
spec = importlib.util.spec_from_file_location("crashcar_plot_b7", PLOT)
assert spec and spec.loader
plot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plot)


def table(name: str, columns: list[str], values: list[str]) -> str:
    assert len(columns) == len(values)
    encoded = ",".join(f'"{value}"' if "," in value else value for value in values) + ","
    cols = "\n".join(f'<Column Name="{column}" Type="lstring"/>' for column in columns)
    return (
        f'<Table Name="{name}">\n{cols}\n'
        f'<Stream Name="{name}" Type="Local" Delimiter=",">\n{encoded}\n</Stream>\n</Table>'
    )


def complex8(event_id: str | None, *, offset: float = 0.0) -> str:
    param = "" if event_id is None else (
        f'<Param Name="event_id:param" Type="ilwd:char">sngl_inspiral:event_id:{event_id}</Param>'
    )
    return (
        '<LIGO_LW Name="COMPLEX8TimeSeries">\n'
        '  <Time Type="GPS" Name="epoch">100.25</Time>\n'
        '  <Param Name="f0:param" Type="real_8">0</Param>\n'
        '  <Array Name="snr:array" Type="real_8">\n'
        '    <Dim Name="Time" Start="0" Scale="0.5">2</Dim>\n'
        '    <Dim Name="Time,Real,Imaginary">3</Dim>\n'
        '    <Stream Type="Local" Delimiter=" ">\n'
        f'      0 {1.0 + offset} 0\n'
        f'      0.5 0 {2.0 + offset}\n'
        '    </Stream>\n'
        '  </Array>\n'
        f'  {param}\n'
        '</LIGO_LW>\n'
    )


def coincs_xml(*, post_columns=None, post_values=None, series=("0", "1"), map_events=("0", "1")) -> str:
    columns = post_columns or [
        "ifos", "end_time", "end_time_ns", "bankid", "tmplt_idx",
        "event_id", "far_sngl_H1", "H1_LLR", "L1_LLR",
    ]
    values = post_values or [
        "H1L1", "100", "5", "3", "7", "9", "0", "12.5", "0",
    ]
    sngl = table(
        "sngl_inspiral:table",
        ["sngl_inspiral:event_id", "sngl_inspiral:ifo"],
        ["0", "H1"],
    ).replace("0,H1,\n", "0,H1,\n1,L1,\n")
    maps = table(
        "coinc_event_map:table",
        ["coinc_event_map:event_id", "coinc_event_map:table_name"],
        [str(map_events[0]), "sngl_inspiral"],
    ).replace(
        f"{map_events[0]},sngl_inspiral,\n",
        "".join(f"{event},sngl_inspiral,\n" for event in map_events),
    )
    blocks = "\n".join(complex8(event, offset=index) for index, event in enumerate(series))
    return (
        "<?xml version='1.0'?>\n<LIGO_LW>\n"
        + sngl + maps + blocks
        + table("postcoh:table", columns, values)
        + "\n</LIGO_LW>\n"
    )


def write_doc(root: Path, xml: str, name="H1L1_100_000000005_3_7_9.xml") -> Path:
    run = root / "run"
    run.mkdir(parents=True, exist_ok=True)
    path = run / name
    path.write_text(xml)
    return path


@pytest.mark.parametrize("column_count", [107, 109])
def test_parse_postcoh_a107_a109(column_count, tmp_path):
    required = ["ifos", "end_time", "end_time_ns", "bankid", "tmplt_idx", "event_id"]
    columns = required + [f"field_{index}" for index in range(column_count - len(required))]
    values = ["H1L1", "100", "5", "3", "7", "9"] + [str(index) for index in range(column_count - len(required))]
    path = tmp_path / "rows.xml"
    path.write_text("<LIGO_LW>" + table("postcoh:table", columns, values) + "</LIGO_LW>")
    rows = list(plot.parse_postcoh_rows(path))
    assert len(rows) == 1
    assert len(rows[0]) == column_count
    assert rows[0]["event_id"] == "9"


def test_direct_discovery_and_nonpositive_single_far(tmp_path):
    path = write_doc(tmp_path, coincs_xml())
    found = plot.discover_normal_coincs(
        tmp_path, start_bank=0, banks_per_worker=8, worker_count=2
    )
    assert found["files"] == [str(path)]
    doc = next(iter(found["by_identity"].values()))
    assert doc["postcoh"]["H1_LLR"] == "12.5"
    assert doc["postcoh"]["L1_LLR"] == "0"
    assert doc["postcoh"]["far_sngl_H1"] == "0"
    assert doc["series_by_ifo"]["H1"]["event_id"] == "0"
    assert doc["series_by_ifo"]["L1"]["length"] == 2
    assert doc["series_by_ifo"]["H1"]["delta_t"] == 0.5
    candidate = {
        "event_id": "9", "ifo": "H1", "_zerolag_ifos": "H1L1", "bankid": "3",
        "tmplt_idx": "7", "end_time": "100", "end_time_ns": "5",
        "far_sngl": "1e-6", "_selection_kind": "single", "_zerolag_worker": "000",
    }
    joined = plot.attach_normal_coincs(
        candidate, found, snr_series_logfar_threshold=-4.0
    )
    assert joined["_normal_series"]["ifo"] == "H1"
    assert joined["_coincs_path"] == str(path)


def test_a107_absent_worker_uses_exact_bank_roster_fallback(tmp_path):
    required = ["ifos", "end_time", "end_time_ns", "bankid", "tmplt_idx", "event_id"]
    columns = required + [f"a107_field_{index}" for index in range(107 - len(required))]
    values = ["H1L1", "100", "5", "3", "7", "9"] + [
        str(index) for index in range(107 - len(required))
    ]
    path = write_doc(
        tmp_path, coincs_xml(post_columns=columns, post_values=values)
    )
    result = plot.parse_normal_coincs_document(
        path, start_bank=0, banks_per_worker=8, worker_count=2
    )
    assert result["schema_columns"] == 107
    assert result["worker"] == "000"


def test_a109_omits_worker_and_reconstructs_roster_ownership(tmp_path):
    path = write_doc(tmp_path, coincs_xml())
    result = plot.parse_normal_coincs_document(
        path, start_bank=0, banks_per_worker=8, worker_count=2
    )
    assert result["worker"] == "000"
    assert "single_worker_id" not in result["postcoh"]
    assert result["postcoh"]["H1_LLR"] == "12.5"
    assert result["postcoh"]["L1_LLR"] == "0"


_EVENT_PARAM = (
    '<Param Name="event_id:param" Type="ilwd:char">'
    'sngl_inspiral:event_id:0</Param>'
)


@pytest.mark.parametrize(
    "replacement, message",
    [
        (
            '<Param Name="event_id:param" Type="ilwd:char">'
            'sngl_inspiral:event_id:999</Param>' + _EVENT_PARAM,
            "exactly one direct standard event_id",
        ),
        (
            _EVENT_PARAM.replace("event_id:param", "event_id:bogus"),
            "nonstandard event_id Param",
        ),
        (
            _EVENT_PARAM.replace("ilwd:char", "lstring"),
            "nonstandard event_id Param",
        ),
        (
            _EVENT_PARAM.replace("sngl_inspiral:event_id:0", "other_table:event_id:0"),
            "noncanonical event_id value",
        ),
        (
            _EVENT_PARAM.replace("sngl_inspiral:event_id:0", "sngl_inspiral:event_id:-1"),
            "noncanonical event_id value",
        ),
        (
            _EVENT_PARAM.replace("sngl_inspiral:event_id:0", "sngl_inspiral:event_id:0.0"),
            "noncanonical event_id value",
        ),
        (
            _EVENT_PARAM.replace("sngl_inspiral:event_id:0", "sngl_inspiral:event_id:1e0"),
            "noncanonical event_id value",
        ),
        (
            _EVENT_PARAM.replace("sngl_inspiral:event_id:0", "sngl_inspiral:event_id:00"),
            "noncanonical event_id value",
        ),
        (
            f'<LIGO_LW>{_EVENT_PARAM}</LIGO_LW>',
            "exactly one direct standard event_id",
        ),
    ],
)
def test_standard_complex8_event_id_param_is_exact(tmp_path, replacement, message):
    xml = coincs_xml()
    assert _EVENT_PARAM in xml
    path = write_doc(tmp_path, xml.replace(_EVENT_PARAM, replacement, 1))
    with pytest.raises(ValueError, match=message):
        plot.parse_normal_coincs_document(
            path, start_bank=0, banks_per_worker=8, worker_count=2
        )


@pytest.mark.parametrize(
    "xml, message",
    [
        (coincs_xml(series=(None, "1")), "exactly one direct standard event_id"),
        (coincs_xml(series=("0", "0")), "duplicate COMPLEX8 event_id"),
        (coincs_xml(map_events=("0", "0", "1")), "duplicate CoincMap"),
        (coincs_xml(series=("0", "2")), "unmapped COMPLEX8 event_id"),
    ],
)
def test_series_mapping_fail_closed(tmp_path, xml, message):
    path = write_doc(tmp_path, xml)
    with pytest.raises(ValueError, match=message):
        plot.parse_normal_coincs_document(
            path, start_bank=0, banks_per_worker=8, worker_count=2
        )


@pytest.mark.parametrize(
    "old_rows, new_rows",
    [
        ("0,H1,\n1,L1,\n", "0,H1,\n0,L1,\n"),
        ("0,H1,\n1,L1,\n", "0,H1,\n1,H1,\n"),
    ],
)
def test_duplicate_sngl_event_or_ifo_fails_closed(tmp_path, old_rows, new_rows):
    xml = coincs_xml()
    assert old_rows in xml
    path = write_doc(tmp_path, xml.replace(old_rows, new_rows))
    with pytest.raises(ValueError, match="invalid/duplicate SnglInspiral identity"):
        plot.parse_normal_coincs_document(
            path, start_bank=0, banks_per_worker=8, worker_count=2
        )


def test_filename_and_exact_zerolag_key_fail_closed(tmp_path):
    path = write_doc(
        tmp_path, coincs_xml(), name="H1L1_101_000000005_3_7_9.xml"
    )
    with pytest.raises(ValueError, match="filename/Postcoh identity mismatch"):
        plot.parse_normal_coincs_document(
            path, start_bank=0, banks_per_worker=8, worker_count=2
        )

    path.unlink()
    write_doc(tmp_path, coincs_xml())
    found = plot.discover_normal_coincs(
        tmp_path, start_bank=0, banks_per_worker=8, worker_count=2
    )
    candidate = {
        "event_id": "9", "ifo": "H1", "_zerolag_ifos": "H1", "bankid": "3",
        "tmplt_idx": "8", "end_time": "100", "end_time_ns": "5",
        "far_sngl_H1": "1e-6", "_selection_kind": "single",
        "_zerolag_worker": "000",
    }
    with pytest.raises(ValueError, match="writer-eligible exact key"):
        plot.attach_normal_coincs(
            candidate,
            found,
            snr_series_logfar_threshold=-4.0,
            writer_config=writer_config(),
        )


def writer_config(
    *,
    schema_mode: str = "crashcar-a109",
    cluster_window: float = 0.0,
    snr_threshold: float = -4.0,
    gracedb_threshold: float = 0.0,
) -> dict:
    return {
        "worker": "000",
        "source": "focused-test-command-log",
        "source_sha256": "0" * 64,
        "schema_mode": schema_mode,
        "cluster_window": cluster_window,
        "snr_series_logfar_threshold": snr_threshold,
        "gracedb_far_threshold": gracedb_threshold,
        "superevent_threshold": 3.8e-7,
    }


def writer_candidate(
    ifos: str,
    *,
    ifo: str = "H1",
    far: str = "0",
    far_sngl_h1: str = "0",
    selected_far: str = "1e-6",
) -> dict:
    return {
        "event_id": "453599",
        "ifo": ifo,
        "_zerolag_ifos": ifos,
        "bankid": "1",
        "tmplt_idx": "434",
        "end_time": "1252265298",
        "end_time_ns": "240234375",
        "far": far,
        "far_sngl_H1": far_sngl_h1,
        "far_multi": selected_far,
        "far_sngl": selected_far,
        "_selection_kind": "multi" if "H1L1" in ifos else "single",
        "_zerolag_worker": "000",
    }


def test_parse_recorded_writer_config_uses_deployed_cluster_zero_default(tmp_path):
    command = tmp_path / "crashcar_command_000.txt"
    command.write_text(
        "CRASHCAR_CMD /runtime/bin/gstlal_inspiral_postcohspiir_online "
        "--finalsink-postcoh-schema-mode crashcar-a109 "
        "--snr-series-logfar-threshold -4 "
        "--finalsink-gracedb-far-thresh 0\n"
    )
    config = plot.parse_finalsink_writer_config(command)
    assert config["worker"] == "000"
    assert config["schema_mode"] == "crashcar-a109"
    assert config["cluster_window"] == 0.0
    assert config["snr_series_logfar_threshold"] == -4.0
    assert config["gracedb_far_threshold"] == 0.0
    assert len(config["source_sha256"]) == 64


def test_conflicting_recorded_writer_config_fails_closed(tmp_path):
    command = tmp_path / "crashcar_command_000.txt"
    command.write_text(
        "CRASHCAR_CMD /runtime/bin/gstlal_inspiral_postcohspiir_online "
        "--finalsink-cluster-window=0 --finalsink-cluster-window 1\n"
    )
    with pytest.raises(ValueError, match="conflicting recorded options"):
        plot.parse_finalsink_writer_config(command)


def test_missing_worker_writer_config_fails_closed():
    candidate = writer_candidate("H1L1V1")
    with pytest.raises(ValueError, match="missing FinalSink writer config"):
        plot.writer_config_for_candidate(candidate, {})


def test_cluster_zero_multi_with_timescale_far_is_not_expected():
    candidate = writer_candidate(
        "H1L1V1", far="0", selected_far="1.6352540432863463e-06"
    )
    joined = plot.attach_normal_coincs(
        candidate,
        {"by_identity": {}},
        snr_series_logfar_threshold=-4.0,
        writer_config=writer_config(),
    )
    assert "_normal_series" not in joined
    assert joined["_writer_retention"]["expected"] is False
    assert joined["_writer_retention"]["route"] == "MULTI"
    assert joined["_writer_retention"]["route_owned_final_far"] == 0.0
    assert joined["_selection_note"] == (
        "NOT_RETAINED_BY_NORMAL_WRITER: "
        "CLUSTER_ZERO_MULTI_HAS_NO_COINCS_WRITER"
    )


def test_cluster_zero_single_uses_route_owned_far_and_recorded_threshold():
    eligible = writer_candidate(
        "H1", far_sngl_h1="1e-6", selected_far="1e-2"
    )
    with pytest.raises(ValueError, match="writer-eligible exact key"):
        plot.attach_normal_coincs(
            eligible,
            {"by_identity": {}},
            snr_series_logfar_threshold=-4.0,
            writer_config=writer_config(),
        )

    not_retained = writer_candidate(
        "H1", far_sngl_h1="1e-3", selected_far="1e-8"
    )
    joined = plot.attach_normal_coincs(
        not_retained,
        {"by_identity": {}},
        snr_series_logfar_threshold=-4.0,
        writer_config=writer_config(),
    )
    assert joined["_writer_retention"]["expected"] is False
    assert joined["_writer_retention"]["route_owned_final_far"] == 1e-3
    assert joined["_selection_note"].endswith(
        "SINGLE_FINAL_FAR_ABOVE_RECORDED_SNR_THRESHOLD"
    )



def test_clustered_missing_document_is_explicitly_not_asserted():
    candidate = writer_candidate("H1L1", far="1e-5", selected_far="1e-5")
    config = writer_config(cluster_window=0.5, gracedb_threshold=1e-4)
    joined = plot.attach_normal_coincs(
        candidate,
        {"by_identity": {}},
        snr_series_logfar_threshold=-4.0,
        writer_config=config,
    )
    assert joined["_writer_retention"]["expected"] is None
    assert joined["_writer_retention"]["assertion"] == "UNKNOWN_NOT_ASSERTED"
    assert joined["_selection_note"].startswith(
        "WRITER_ELIGIBILITY_UNKNOWN_NOT_ASSERTED:"
    )


@pytest.mark.parametrize(
    ("column_count", "schema_mode"),
    [(107, "legacy-a107"), (109, "crashcar-a109")],
)
def test_a107_a109_loader_preserves_route_owned_final_far(
    column_count, schema_mode, tmp_path
):
    base_columns = [
        "ifos", "end_time", "end_time_ns", "bankid", "tmplt_idx", "event_id",
        "far", "far_1w", "far_sngl_H1", "far_sngl_L1",
        "snglsnr_H1", "end_time_sngl_H1", "end_time_ns_sngl_H1",
    ]
    if column_count == 109:
        base_columns.extend(("H1_LLR", "L1_LLR"))
    columns = base_columns + [
        f"shared_field_{index}"
        for index in range(column_count - len(base_columns))
    ]
    values_by_name = {
        "ifos": "H1L1V1",
        "end_time": "1252265298",
        "end_time_ns": "240234375",
        "bankid": "1",
        "tmplt_idx": "434",
        "event_id": "453599",
        "far": "0",
        "far_1w": "1.6352540432863463e-06",
        "far_sngl_H1": "0",
        "far_sngl_L1": "0",
        "snglsnr_H1": "5.5",
        "end_time_sngl_H1": "1252265298",
        "end_time_ns_sngl_H1": "240234375",
        "H1_LLR": "12.5",
        "L1_LLR": "13.5",
    }
    values = [values_by_name.get(name, "0") for name in columns]
    xml_path = tmp_path / f"postcoh_{column_count}.xml"
    xml_path.write_text(
        "<LIGO_LW>" + table("postcoh:table", columns, values) + "</LIGO_LW>"
    )
    row = next(iter(plot.parse_postcoh_rows(xml_path)))
    row["_source"] = str(xml_path)
    row["_worker"] = "000"
    candidate = plot.make_zerolag_snr_candidate(
        row, "H1", "multi", 1.6352540432863463e-6, "far_1w"
    )
    assert len(row) >= column_count
    assert candidate["_final_route"] == "MULTI"
    assert float(candidate["_route_owned_final_far"]) == 0.0
    assert candidate["_route_owned_final_far_source"] == "far"
    config = writer_config(schema_mode=schema_mode)
    retention = plot.finalsink_writer_retention(candidate, config)
    assert retention["expected"] is False
    assert retention["route_owned_final_far"] == 0.0
