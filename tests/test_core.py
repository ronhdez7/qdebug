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
