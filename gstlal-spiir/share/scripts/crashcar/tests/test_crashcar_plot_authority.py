from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]
PLOT = REPO / "gstlal-spiir" / "bin" / "crashcar_plot.py"
spec = importlib.util.spec_from_file_location("crashcar_plot_b7_authority", PLOT)
assert spec and spec.loader
plot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plot)

TOP_PLOT = REPO / "crashcar_plot.py"
top_spec = importlib.util.spec_from_file_location("crashcar_plot_b7_top", TOP_PLOT)
assert top_spec and top_spec.loader
top_plot = importlib.util.module_from_spec(top_spec)
top_spec.loader.exec_module(top_plot)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_bank(bank_dir: Path, ifo="H1", bankid=0) -> Path:
    bank_dir.mkdir(parents=True, exist_ok=True)
    path = bank_dir / f"iir_{ifo}-GSTLAL_SPLIT_BANK_{bankid:04d}-a1-0-0.xml.gz"
    path.write_text(
        '<?xml version="1.0"?>\n<LIGO_LW>\n'
        '<Array Name="autocorrelation_bank_real:array" Type="real_8">\n'
        '<Dim>3</Dim><Dim>2</Dim><Stream Delimiter=" " Type="Local">\n'
        '1 10 2 20 3 30\n</Stream></Array>\n'
        '<Array Name="autocorrelation_bank_imag:array" Type="real_8">\n'
        '<Dim>3</Dim><Dim>2</Dim><Stream Delimiter=" " Type="Local">\n'
        '4 40 5 50 6 60\n</Stream></Array>\n</LIGO_LW>\n'
    )
    assert path.read_bytes()[:2] != b"\x1f\x8b"
    return path


def selected_row(**updates):
    row = {
        "ifo": "H1", "bankid": "0", "tmplt_idx": "1",
        "_zerolag_worker": "000",
    }
    row.update(updates)
    return row


def test_top_wrapper_infers_nondefault_one_worker_geometry(tmp_path):
    run_root = tmp_path / "run_root"
    scripts = run_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "crashcar.env").write_text(
        "worker_number=1\nbank_per_worker=3\nstart_bank=4\n"
    )
    assert top_plot.infer_integer_setting(
        tmp_path, run_root, status_key="worker_count",
        env_keys=("worker_number",), fallback=2,
    ) == 1
    assert top_plot.infer_integer_setting(
        tmp_path, run_root, status_key="banks_per_worker",
        env_keys=("bank_per_worker",), fallback=8,
    ) == 3
    assert top_plot.infer_integer_setting(
        tmp_path, run_root, status_key="start_bank",
        env_keys=("start_bank",), fallback=0,
    ) == 4


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--run_id", "=", "20260729_1500"],
         ["--run_id", "20260729_1500"]),
        (["--run-id", "=20260729_1500"],
         ["--run-id", "20260729_1500"]),
        (["--run_id=20260729_1500"], ["--run_id=20260729_1500"]),
    ],
)
def test_top_wrapper_normalizes_run_id_equals_forms(argv, expected):
    assert top_plot.normalize_cli_argv(argv) == expected


def test_top_wrapper_plots_a_and_b_with_role_correct_sources(
    tmp_path, monkeypatch, capsys
):
    run_id = "20260728_1400"
    group = tmp_path / "runs" / run_id
    roots = {role: group / role for role in ("A", "B")}
    for role, run_root in roots.items():
        (run_root / "run").mkdir(parents=True)
        (run_root / "scripts").mkdir()
        background_root = roots["A"] if role == "B" else ""
        (run_root / "scripts" / "crashcar.env").write_text(
            f"run_id={run_id}\n"
            f"run_type={role}\n"
            f"crashcar_role={role}\n"
            f"run_root={run_root}\n"
            f"background_run_root={background_root}\n"
            "worker_number=2\n"
            "bank_per_worker=8\n"
            "start_bank=0\n"
            "background_accumulation=10800\n"
            "tail_log_FAR=-2\n"
            "SNR_series_logFAR_threshold=-1\n"
        )

    impl = tmp_path / "gstlal-spiir" / "bin" / "crashcar_plot.py"
    impl.parent.mkdir(parents=True)
    impl.write_text("# test implementation placeholder\n")
    monkeypatch.setattr(top_plot, "__file__", str(tmp_path / "crashcar_plot.py"))

    calls = []

    def fake_run_plot_impl(**kwargs):
        calls.append(kwargs)
        label = kwargs["run_label"]
        return {
            "first": f"{label}_background.png",
            "second": f"{label}_snr.png",
        }

    monkeypatch.setattr(top_plot, "run_plot_impl", fake_run_plot_impl)
    monkeypatch.setattr(
        top_plot.sys, "argv", ["crashcar_plot.py", "--run_id", "=", run_id]
    )

    assert top_plot.main() == 0
    assert [call["run_root"] for call in calls] == [roots["A"], roots["B"]]
    assert {call["output_dir"] for call in calls} == {group / "figures"}
    assert calls[0]["run_label"].endswith("_A_no-injection")
    assert calls[1]["run_label"].endswith("_B_injection")
    assert "--background-producer-root" not in calls[0]["extra_args"]
    producer_index = calls[1]["extra_args"].index("--background-producer-root")
    assert calls[1]["extra_args"][producer_index + 1] == str(roots["A"])

    output = capsys.readouterr().out
    for key in (
        "A_background_2x2=",
        "A_snr_series_2x2=",
        "B_background_2x2=",
        "B_snr_series_2x2=",
    ):
        assert key in output
    assert "generated_2x2_count=4" in output
    generated = [
        line.split("=", 1)[1]
        for line in output.splitlines()
        if line.startswith("generated_2x2=")
    ]
    assert len(generated) == 4
    assert all(Path(path).is_absolute() for path in generated)


def test_top_wrapper_explicit_role_root_discovers_ab_sibling(tmp_path):
    group = tmp_path / "group"
    for role in ("A", "B"):
        (group / role / "run").mkdir(parents=True)
    records = top_plot.explicit_run_roots(group / "A")
    assert [record["run_type"] for record in records] == ["A", "B"]
    assert [record["run_root"] for record in records] == [group / "A", group / "B"]


def test_plain_xml_gz_bank_uses_c_layout_and_records_sha(tmp_path):
    bank = write_bank(tmp_path / "banks")
    result = plot.load_pinned_bank_autocorrelation(
        bank.parent, selected_row(),
        start_bank=0, banks_per_worker=2, worker_count=1,
    )
    assert result["real"] == [10.0, 20.0, 30.0]
    assert result["imag"] == [40.0, 50.0, 60.0]
    assert result["relative_index"] == [-1, 0, 1]
    assert result["layout"] == "offset=k*ntmplt+tmplt_idx"
    assert result["sha256"] == digest(bank)
    assert result["ntmplt"] == 2


@pytest.mark.parametrize(
    "row, message",
    [
        (selected_row(_zerolag_worker="001"), "worker/bank roster mismatch"),
        (selected_row(bankid="1"), "No such file|pinned bank"),
        (selected_row(ifo="V1"), "not H1/L1"),
        (selected_row(tmplt_idx="2"), "dimensions/template index invalid"),
    ],
)
def test_bank_mapping_fail_closed(tmp_path, row, message):
    bank_dir = tmp_path / "banks"
    write_bank(bank_dir)
    with pytest.raises((ValueError, FileNotFoundError), match=message):
        plot.load_pinned_bank_autocorrelation(
            bank_dir, row, start_bank=0, banks_per_worker=2, worker_count=1
        )


def gps(seconds: int, nanoseconds: int = 0) -> dict:
    return {"seconds": seconds, "nanoseconds": nanoseconds}


def detector_payload(livetime_seconds: int, gps_start: int = 101) -> dict:
    ranks = [float(value) for value in range(1, 7)]
    log_fars = [
        math.log10((len(ranks) - index) / float(livetime_seconds))
        for index in range(len(ranks))
    ]
    tail_index = min(
        range(len(ranks)), key=lambda index: abs(log_fars[index] + 2.0)
    )
    r_tail = ranks[tail_index]
    denominator = sum(
        (rank - r_tail) ** 2 for rank in ranks[tail_index:]
    )
    slope = sum(
        (rank - r_tail) * (log_far + 2.0)
        for rank, log_far in zip(ranks[tail_index:], log_fars[tail_index:])
    ) / denominator
    assert slope < 0.0
    points = []
    for index, rank in enumerate(ranks):
        far = (len(ranks) - index) / float(livetime_seconds)
        points.append({
            "gps": gps(gps_start + index),
            "llr": rank.hex(),
            "far": far.hex(),
        })
    return {
        "livetime": gps(livetime_seconds),
        "support_count": len(points),
        "tail_fit": {
            "method": "anchored_ols_all_unique_ranks_ge_r_tail",
            "r_tail": r_tail.hex(),
            "slope": slope.hex(),
            "fit_unique_rank_count": len(ranks) - tail_index,
        },
        "far_llr_points": points,
    }


def schema4_background_doc() -> dict:
    provenance = "a" * 64
    return {
        "schema_version": 4,
        "background_kind": "no_injection",
        "run_namespace_sha256": provenance,
        "source_manifest_sha256": provenance,
        "runtime_manifest_sha256": provenance,
        "config_sha256": provenance,
        "segment_xml_sha256": provenance,
        "segment_canonical_sha256": provenance,
        "template_shape_map_sha256": provenance,
        "worker_id": 0,
        "worker_count": 1,
        "worker_bank_ids": [0, 1],
        "accepted_version": 2,
        "epoch_gps": gps(1100),
        "window_start_gps": gps(100),
        "window_end_gps": gps(1100),
        "window_duration": gps(1000),
        "update_period": gps(100),
        "far_floor_count": 1,
        "tail_log10_far": -2,
        "backgrounds": {
            "H1": detector_payload(500),
            "L1": detector_payload(600),
        },
    }


def clone(value):
    return json.loads(json.dumps(value))


def write_schema4(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_symlink():
        path.chmod(0o644)
    path.write_text(json.dumps(doc, separators=(",", ":")) + "\n")
    path.chmod(0o444)


def load_schema4(path: Path, doc: dict | None = None, *, worker="000"):
    if doc is not None:
        write_schema4(path, doc)
    return plot.load_panel_a_background_json(
        path,
        1000.0,
        0,
        worker,
        start_bank=0,
        banks_per_worker=2,
        worker_count=1,
    )


def panel_source(run_root: Path, source: str, explicit=None):
    return plot.load_requested_panel_a_source(
        run_root,
        panel_a_source=source,
        explicit_background_json=explicit,
        background_accumulation_seconds=1000.0,
        max_points=0,
        panel_a_worker="000",
        start_bank=0,
        banks_per_worker=2,
        worker_count=1,
        detail_glob="run/crashcar_singlefar_detail_worker*.csv",
        ifo_id_map={"0": "H1", "1": "L1"},
        panel_a_bg_policy="latest",
    )


def test_live_producer_path_is_worker_local_readonly_and_authoritative(tmp_path):
    producer = tmp_path / "continuing_no_injection"
    consumer = tmp_path / "injection"
    consumer.mkdir()
    background = producer / "run" / "000" / "single_background.json"
    doc = schema4_background_doc()
    write_schema4(background, doc)

    resolved = plot.resolve_live_producer_background_path(producer, "000")
    assert resolved == background
    panel = panel_source(consumer, "background", resolved)
    assert panel["authoritative"] is True
    assert panel["source"] == str(background)
    assert panel["source_sha256"] == digest(background)
    assert panel["schema4_authority"]["worker_id"] == 0
    assert panel["schema4_authority"]["accepted_version"] == 2
    assert panel["schema4_authority"]["epoch_gps_ns"] == 1_100_000_000_000
    assert panel["schema4_authority"]["provenance"]["run_namespace_sha256"] == "a" * 64
    assert (background.stat().st_mode & 0o777) == 0o444


def test_live_producer_same_worker_geometry_and_provenance_fail_closed(tmp_path):
    producer = tmp_path / "producer"
    background = producer / "run" / "000" / "single_background.json"
    doc = schema4_background_doc()
    write_schema4(background, doc)

    with pytest.raises(ValueError, match="worker mismatch|geometry is invalid"):
        load_schema4(background, worker="001")

    wrong_geometry = clone(doc)
    wrong_geometry["worker_bank_ids"] = [2, 3]
    write_schema4(background, wrong_geometry)
    with pytest.raises(ValueError, match="geometry/roster mismatch"):
        load_schema4(background)

    bad_provenance = clone(doc)
    bad_provenance["run_namespace_sha256"] = "a" * 63
    write_schema4(background, bad_provenance)
    with pytest.raises(ValueError, match="provenance"):
        load_schema4(background)


@pytest.mark.parametrize(
    "case, message",
    [
        ("future_point", "outside the authority window"),
        ("bad_epoch", "window/epoch/update"),
        ("bad_duration", "window/epoch/update"),
        ("bad_update", "window/epoch/update"),
    ],
)
def test_live_background_coverage_contract_fails_closed(tmp_path, case, message):
    path = tmp_path / "producer" / "run" / "000" / "single_background.json"
    doc = schema4_background_doc()
    if case == "future_point":
        doc["backgrounds"]["H1"]["far_llr_points"][0]["gps"] = gps(1101)
    elif case == "bad_epoch":
        doc["epoch_gps"] = gps(1099)
    elif case == "bad_duration":
        doc["window_duration"] = gps(999)
    elif case == "bad_update":
        doc["update_period"] = gps(0)
    with pytest.raises(ValueError, match=message):
        load_schema4(path, doc)


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("schema_version", 4.0, "exact integer"),
        ("schema_version", True, "exact integer"),
        ("far_floor_count", 1.0, "exact integer"),
        ("tail_log10_far", True, "finite and negative"),
        ("tail_log10_far", 0.0, "finite and negative"),
        ("tail_log10_far", float("nan"), "finite and negative"),
        ("schema_version", 3, "schema/kind mismatch"),
        ("far_floor_count", 2, "FAR floor mismatch"),
    ],
)
def test_schema4_contract_constants_are_exact_integers(
    tmp_path, field, value, message
):
    path = tmp_path / "single_background.json"
    doc = schema4_background_doc()
    doc[field] = value
    with pytest.raises(ValueError, match=message):
        load_schema4(path, doc)


@pytest.mark.parametrize("tail_log10_far", [-2, -2.5, -1.0e-6])
def test_schema4_accepts_authoritative_finite_negative_tail_boundary(
    tmp_path, tail_log10_far
):
    path = tmp_path / "single_background.json"
    doc = schema4_background_doc()
    doc["tail_log10_far"] = tail_log10_far
    panel = load_schema4(path, doc)
    assert panel["schema4_authority"]["tail_log10_far"] == pytest.approx(
        float(tail_log10_far), rel=0.0, abs=0.0
    )


def test_authoritative_panel_a_tail_boundary_tracks_live_background(tmp_path):
    path = tmp_path / "single_background.json"
    doc = schema4_background_doc()
    doc["tail_log10_far"] = -1.0
    panel = load_schema4(path, doc)
    boundary, source = plot.resolve_panel_a_tail_boundary(panel, None)
    fit = plot.panel_a_segmented_fit(
        [point for point in panel["points"] if point["ifo"] == "H1"],
        boundary,
        panel["tail_fit_by_ifo"]["H1"],
    )
    assert boundary == pytest.approx(-1.0)
    assert source == "authoritative_schema4_background.tail_log10_far"
    assert fit["tail_boundary_log10_far"] == pytest.approx(-1.0)
    assert fit["tail_line_y"][0] == pytest.approx(-1.0)


def test_authoritative_panel_a_rejects_conflicting_tail_override(tmp_path):
    path = tmp_path / "single_background.json"
    doc = schema4_background_doc()
    doc["tail_log10_far"] = -1.0
    panel = load_schema4(path, doc)
    with pytest.raises(ValueError, match="conflicts with authoritative"):
        plot.resolve_panel_a_tail_boundary(panel, -2.0)


@pytest.mark.parametrize(
    "case, message",
    [
        ("missing_H1", "keys mismatch"),
        ("missing_L1", "keys mismatch"),
        ("empty_H1", "support_count"),
        ("empty_L1", "support_count"),
        ("support_mismatch", "length mismatch"),
        ("bad_far", "FAR must be positive"),
        ("bad_llr", "canonical binary64"),
        ("legacy_triggers", "keys mismatch"),
        ("tail_method", "tail method mismatch"),
        ("floor", "FAR floor mismatch"),
        ("occupancy", "occupancy/livetime"),
        ("unsorted", "not canonically sorted"),
    ],
)
def test_schema4_science_payload_fails_closed(tmp_path, case, message):
    path = tmp_path / "single_background.json"
    doc = schema4_background_doc()
    if case.startswith("missing_"):
        del doc["backgrounds"][case.removeprefix("missing_")]
    elif case.startswith("empty_"):
        payload = doc["backgrounds"][case.removeprefix("empty_")]
        payload["support_count"] = 0
        payload["far_llr_points"] = []
    elif case == "support_mismatch":
        doc["backgrounds"]["H1"]["support_count"] += 1
    elif case == "bad_far":
        doc["backgrounds"]["H1"]["far_llr_points"][0]["far"] = (-0.5).hex()
    elif case == "bad_llr":
        doc["backgrounds"]["H1"]["far_llr_points"][0]["llr"] = "nan"
    elif case == "legacy_triggers":
        doc["backgrounds"]["H1"]["background_triggers"] = []
    elif case == "tail_method":
        doc["backgrounds"]["H1"]["tail_fit"]["method"] = "legacy"
    elif case == "floor":
        doc["far_floor_count"] = 0
    elif case == "occupancy":
        doc["backgrounds"]["H1"]["livetime"] = gps(200)
    elif case == "unsorted":
        points = doc["backgrounds"]["H1"]["far_llr_points"]
        points[0], points[1] = points[1], points[0]
    with pytest.raises(ValueError, match=message):
        load_schema4(path, doc)


@pytest.mark.parametrize("state", ("missing", "empty", "malformed"))
def test_authoritative_panel_a_never_falls_back_to_detail_or_legacy(
    tmp_path, state
):
    run_root = tmp_path / "run_root"
    detail = run_root / "run" / "crashcar_singlefar_detail_worker000.csv"
    detail.parent.mkdir(parents=True)
    detail.write_text(
        "ifo_id,far_calculated_valid,far_calculated_exact,window_count,"
        "total_window_count,bg_start,bg_end,llr,event_id,snglsnr,chisq\n"
        "0,1,0.01,6,12,100,1100,2.0,9,5.0,1.0\n"
    )
    canonical = run_root / "run" / "000" / "single_background.json"
    if state == "empty":
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"")
        canonical.chmod(0o444)
    elif state == "malformed":
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"{bad\n")
        canonical.chmod(0o444)
    with pytest.raises(ValueError):
        panel_source(run_root, "background")


def test_authoritative_background_uses_stored_far_and_direct_rule(tmp_path):
    path = tmp_path / "single_background.json"
    doc = schema4_background_doc()
    panel = load_schema4(path, doc)
    h1_first = next(point for point in panel["points"] if point["ifo"] == "H1")
    stored = float.fromhex(
        doc["backgrounds"]["H1"]["far_llr_points"][0]["far"]
    )
    assert h1_first["direct_far"].hex() == stored.hex()
    assert h1_first["direct_far_count_ge"] == 6
    assert panel["points_original"] == 12
    assert panel["online_summary"]["by_ifo"]["H1"]["online_seconds"] == 500.0
    assert panel["online_summary"]["by_ifo"]["H1"]["fraction"] == 0.5


def test_schema4_open_is_single_fd_readonly_and_fail_closed(tmp_path):
    path = tmp_path / "single_background.json"
    doc = schema4_background_doc()
    write_schema4(path, doc)
    good_sha = digest(path)

    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode-0444"):
        plot.read_strict_schema4_background(path)
    path.chmod(0o444)

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        plot.read_strict_schema4_background(path, expected_sha256="b" * 64)

    link = tmp_path / "single_background_link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="O_NOFOLLOW"):
        plot.read_strict_schema4_background(link)

    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as handle:
        handle.truncate(256 * 1024 * 1024 + 1)
    oversized.chmod(0o444)
    with pytest.raises(ValueError, match="bounded"):
        plot.read_strict_schema4_background(oversized)
    assert digest(path) == good_sha


def test_plotter_is_external_and_has_no_obsolete_background_copy_path():
    formal = PLOT.read_text()
    assert "background-producer-root" in formal
    assert "O_NOFOLLOW" in formal
    assert "frozen" not in formal.lower()

    runtime_sources = (
        REPO / "gstlal-spiir" / "share" / "scripts" / "crashcar" / "crashcar.sh",
        REPO / "gstlal-spiir" / "share" / "scripts" / "crashcar" / "crashcar_pipeline.sh",
        REPO / "gstlal-spiir" / "share" / "scripts" / "crashcar" / "crashcar_controller.sh",
        REPO / "gstlal-spiir" / "bin" / "gstlal_inspiral_postcohspiir_online",
    )
    for source in runtime_sources:
        text = source.read_text()
        assert "background-producer-root" not in text
        assert "crashcar_plot.py" not in text


def test_plot_sources_forbid_obsolete_runtime_dependencies():
    forbidden = (
        "current_" + "chunk_root",
        "chunks/" + "chunk_",
        "JIT_" + "XML_FRONTIER",
        "materialize_snr_" + "autocorrelation",
        "candidate_events_" + "manifest",
        "crashcar_candidate_" + "events",
        "crashcar_snr_" + "series",
        "crashcar_event_" + "id",
        "series_" + "kind",
        "background_" + "triggers",
    )
    for source in (TOP_PLOT, PLOT):
        text = source.read_text()
        assert all(token not in text for token in forbidden), source
