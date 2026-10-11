import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator, Statevector

from qdebug import circuits as C
from qdebug import stats
from qdebug.diff import build_hybrid, diff_circuits
from qdebug.demo_suite import bv, ghz4


def test_tvd_basic():
    assert stats.tvd({"0": 1.0}, {"1": 1.0}) == 1.0
    assert stats.tvd({"0": .5, "1": .5}, {"0": .5, "1": .5}) == 0.0
    assert stats.tvd({"0": .75, "1": .25}, {"0": .25, "1": .75}) == pytest.approx(0.5)


def test_counts_normalisation_strips_register_spaces():
    p = stats.counts_to_probs({"01 1": 30, "000": 70})
    assert p == {"011": 0.3, "000": 0.7}


def test_shot_noise_floor_shrinks_with_shots():
    p = {"00": .5, "11": .5}
    assert stats.shot_noise_floor(p, 100) > stats.shot_noise_floor(p, 10000)


def test_strip_measurements_and_roundtrip():
    qc = QuantumCircuit(2, 2, name="x")
    qc.h(0)
    qc.cx(0, 1)
    qc.barrier()
    qc.measure([0, 1], [0, 1])
    u = C.strip_measurements(qc)
    assert [i.operation.name for i in u.data] == ["h", "cx"]
    assert u.num_clbits == 0
    back = C.circuit_from_b64(C.circuit_to_b64(u))
    assert Operator(back).equiv(Operator(u))


def test_midcircuit_measurement_rejected():
    qc = QuantumCircuit(1, 1)
    qc.h(0)
    qc.measure(0, 0)
    qc.x(0)
    with pytest.raises(ValueError):
        C.strip_measurements(qc)


def test_probe_adds_basis_change_and_measure():
    qc = QuantumCircuit(1, name="a")
    z, x = C.with_probe(qc, "Z"), C.with_probe(qc, "X")
    assert [i.operation.name for i in x.data] == ["h", "barrier", "measure"]
    assert [i.operation.name for i in z.data] == ["barrier", "measure"]


def test_probe_accepts_one_basis_letter_per_qubit():
    qc = QuantumCircuit(3, name="a")
    names = [(i.operation.name, qc.find_bit(i.qubits[0]).index)
             for i in C.with_probe(qc, "XZY").data if i.operation.name in ("h", "sdg")]
    assert names == [("h", 0), ("sdg", 2), ("h", 2)]
    for bad in ("XZ", "XQZ", "Q"):
        with pytest.raises(ValueError):
            C.with_probe(qc, bad)


def test_exact_distances_ignore_global_phase_and_see_small_changes():
    from qdebug import equiv as E
    a = QuantumCircuit(2); a.h(0); a.cx(0, 1)
    b = a.copy(); b.global_phase = 1.234
    assert E.state_distance(a, b) < 1e-12 and E.unitary_distance(a, b) < 1e-12
    c = a.copy(); c.rz(1e-6, 0)                 # far below any probe TVD you'd trust
    assert 1e-8 < E.state_distance(a, c) < 1e-5
    d = QuantumCircuit(2); d.h(0); d.cx(0, 1); d.cz(0, 1); d.cz(0, 1)
    assert E.unitary_distance(a, d) < 1e-12
    e = QuantumCircuit(2); e.cz(0, 1)           # identity on |00>, not on |11>
    assert E.state_distance(QuantumCircuit(2), e) < 1e-12
    assert E.unitary_distance(QuantumCircuit(2), e) > 0.1


def test_snapshot_roundtrip_and_newer_version_rejected(tmp_path):
    from qdebug import LocalBackend, load, record, save
    from qdebug.snapshot import SNAPSHOT_VERSION, Snapshot
    snap = record("ghz4", ghz4(), LocalBackend(), shots=100, n_seeds=2)
    back = load("ghz4", save(snap, tmp_path).parent)
    assert back.ideal == snap.ideal and back.signature == snap.signature
    assert back.version == SNAPSHOT_VERSION
    import json
    d = json.loads(snap.to_json())
    old = Snapshot.from_dict({**d, "version": 1})          # older files still load
    assert old.bases == snap.bases
    with pytest.raises(ValueError, match="newer"):
        Snapshot.from_dict({**d, "version": SNAPSHOT_VERSION + 1})
    with pytest.raises(FileNotFoundError, match="qdebug record"):
        load("missing", tmp_path)


def test_signatures_include_params():
    qc = QuantumCircuit(2)
    qc.rz(0.5, 1)
    qc.cx(0, 1)
    assert C.instruction_signatures(qc) == ["rz(0.5) q1", "cx q0,q1"]


def test_diff_hybrids_reproduce_endpoints():
    g, m = bv(), bv(drop=3)
    m2 = ghz4()
    g2 = ghz4(flip_first_cx=True, final_z=True)
    for a, b in ((g, m), (m2, g2)):
        hunks = diff_circuits(a, b)
        assert hunks
        none = build_hybrid(a, b, hunks, set())
        allh = build_hybrid(a, b, hunks, {h.index for h in hunks})
        assert Statevector(none).equiv(Statevector(a))
        assert Statevector(allh).equiv(Statevector(b))


def test_identical_circuits_have_no_hunks():
    assert diff_circuits(ghz4(), ghz4()) == []
