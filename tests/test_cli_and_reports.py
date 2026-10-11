"""CLI exit codes, report formatters and the generic Qiskit backend adapter."""
import json
from pathlib import Path

import pytest

from qdebug import QiskitBackendAdapter, Verdict, check, record
from qdebug.cli import main
from qdebug.demo_suite import ghz4, mutants
from qdebug.report import format_report, summary_table, to_markdown

SUITE = str(Path(__file__).resolve().parents[1] / "examples" / "suite.py")


@pytest.fixture()
def recorded(tmp_path, monkeypatch):
    monkeypatch.delenv("QDEBUG_DEMO_BUG", raising=False)
    snaps = str(tmp_path / "snaps")
    assert main(["record", "--suite", SUITE, "--snapshots", snaps, "--seeds", "3"]) == 0
    return snaps


def test_check_exit_codes_follow_the_verdict(recorded, monkeypatch, tmp_path):
    base = ["check", "--suite", SUITE, "--snapshots", recorded]
    assert main(base) == 0                                   # unchanged -> PASS
    monkeypatch.setenv("QDEBUG_DEMO_BUG", "cx_flip")
    assert main(base) == 1                                   # LOGIC_BUG fails CI
    monkeypatch.setenv("QDEBUG_DEMO_BUG", "padding")
    assert main(base) == 0                                   # noise alone does not...
    assert main(base + ["--strict"]) == 1                    # ...unless --strict


def test_check_writes_json_and_markdown(recorded, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QDEBUG_DEMO_BUG", "cx_flip")
    js, md = tmp_path / "r.json", tmp_path / "r.md"
    main(["check", "--suite", SUITE, "--snapshots", recorded,
          "--json", str(js), "--markdown", str(md), "-v"])
    data = {r["name"]: r for r in json.loads(js.read_text())}
    assert data["ghz"]["verdict"] == "LOGIC_BUG"
    assert data["swap_test_like"]["verdict"] == "PASS"
    text = md.read_text()
    assert "**LOGIC_BUG**" in text and "+ cx q1,q0" in text and "(culprit)" in text
    assert "<-- culprit" in capsys.readouterr().out


def test_check_without_snapshot_says_how_to_fix_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="qdebug record"):
        main(["check", "--suite", SUITE, "--snapshots", str(tmp_path / "none")])


def test_doctor_and_demo_succeed_locally(tmp_path):
    assert main(["doctor"]) == 0
    out = tmp_path / "demo"
    assert main(["demo", "--out", str(out), "--assert-all"]) == 0
    assert (out / "report.json").exists() and (out / "report.md").exists()
    assert len(json.loads((out / "report.json").read_text())) == len(mutants())


def test_report_formatters(snapshots, backend):
    reports, expected = [], {}
    for m in mutants():
        if m.name in ("ghz4_unchanged", "ghz4_cx_flipped", "qft3_dead_angle"):
            reports.append(check(m.name, m.circuit, snapshots[m.base], backend))
            expected[m.name] = m.expected
    table = summary_table(reports, expected)
    assert "MISMATCH" not in table and table.count("\n") == len(reports) + 1
    expected["ghz4_unchanged"] = "LOGIC_BUG"
    assert "MISMATCH" in summary_table(reports, expected)
    by_name = {r.name: r for r in reports}
    assert "WARNING" in format_report(by_name["qft3_dead_angle"], verbose=False)
    assert "blame: hunk 0" in format_report(by_name["ghz4_cx_flipped"])
    md = to_markdown(reports, "t")
    assert md.startswith("# t") and "```diff" in md and "> WARNING" in md


def test_generic_qiskit_backend_adapter_runs_the_full_workflow():
    from qiskit_aer import AerSimulator
    be = QiskitBackendAdapter(AerSimulator(), name="aer-ideal")
    snap = record("ghz4", ghz4(), be, shots=500, n_seeds=3)
    assert snap.noise.noise_model == {"backend": "aer-ideal"}
    # noiseless sampler: the baseline is pure shot noise
    assert snap.noise.mean < snap.noise.shot_floor
    assert check("ghz4", ghz4(), snap, be).verdict is Verdict.PASS
    assert check("ghz4", ghz4(flip_first_cx=True), snap, be).verdict is Verdict.LOGIC_BUG
