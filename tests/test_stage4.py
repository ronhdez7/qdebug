"""Noise-aware blame, measured cost weights, parameter sweeps, IonQ native gates."""
import json

import pytest
from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
from qiskit.quantum_info import Operator

from qdebug import (CheckConfig, LocalBackend, Verdict, calibrate_cost_weights, check,
                    expand_parameters, parameter_points, record)
from qdebug.circuits import with_probe
from qdebug.demo_suite import ghz4
from qdebug.snapshot import Snapshot


# ------------------------------------------------------------- noise blame

def test_noise_regression_blames_the_costly_edit_not_the_cheap_one(snapshots, backend):
    edited = ghz4(pad_pairs=40)      # 80 redundant CX early in the circuit...
    edited.h(3)                      # ...and a separate, cheap H-H pair at the end
    edited.h(3)
    r = check("ghz4", edited, snapshots["ghz4"], backend)
    assert r.verdict is Verdict.NOISE_REGRESSION and len(r.noise_blame) == 2
    top, rest = r.noise_blame
    assert top.hunk.added == ["cx q0,q1"] * 80 and top.share > 0.99
    assert rest.hunk.added == ["h q3", "h q3"] and 0 < rest.cost_delta < 1
    assert "Costliest edit(s): hunk %d" % top.hunk.index in r.reason


def test_noise_blame_counts_removed_gates_as_negative(snapshots):
    from qdebug.checker import _noise_blame
    from qdebug.diff import diff_circuits
    golden, mod = ghz4(pad_pairs=3), ghz4()
    (b,) = _noise_blame(golden, mod, diff_circuits(golden, mod), None)
    assert b.cost_delta == pytest.approx(-6.0) and b.share == 0.0


# ------------------------------------------------------------- calibration

def test_calibration_recovers_the_backends_gate_error_ratio():
    base = calibrate_cost_weights(LocalBackend(p1=5e-4, p2=5e-3))
    assert base["rate_2q"] == pytest.approx(5e-3, rel=0.25)
    worse_1q = calibrate_cost_weights(LocalBackend(p1=2e-3, p2=5e-3))
    assert worse_1q["w1"] / base["w1"] == pytest.approx(4.0, rel=0.25)
    worse_2q = calibrate_cost_weights(LocalBackend(p1=5e-4, p2=2e-2))
    assert base["w1"] / worse_2q["w1"] == pytest.approx(4.0, rel=0.25)


def test_calibration_returns_none_without_measurable_noise():
    assert calibrate_cost_weights(LocalBackend(p1=0.0, p2=0.0)) is None


def test_measured_weights_are_stored_and_drive_the_cost(backend):
    w = {"w1": 0.5, "w2": 1.0, "wm": 6.0, "rate_1q": 1e-3, "rate_2q": 2e-3}
    snap = record("ghz4", ghz4(), backend, shots=300, n_seeds=2, cost_weights=w)
    back = Snapshot.from_dict(json.loads(snap.to_json()))
    assert back.noise.cost_weights == w
    r = check("ghz4", ghz4(hh=True), back, backend)
    assert r.cost_golden == pytest.approx(0.5 * 1 + 3)        # 1 H, 3 CX
    assert r.cost_modified == pytest.approx(0.5 * 3 + 3)
    plain = record("ghz4", ghz4(), backend, shots=300, n_seeds=2)
    assert plain.noise.cost_weights is None
    assert check("ghz4", ghz4(), plain, backend).cost_golden == pytest.approx(3.05)


# -------------------------------------------------------- parameter sweeps

def ansatz(bug: bool = False, extra: bool = False) -> QuantumCircuit:
    theta, phi = Parameter("theta"), Parameter("phi")
    qc = QuantumCircuit(2, name="ansatz")
    qc.ry(theta, 0)
    qc.cx(0, 1)
    qc.rz(phi, 1)
    qc.ry(phi if bug else theta, 1)
    if extra:
        qc.rx(Parameter("alpha"), 0)
    return qc


def test_parameter_points_are_reproducible_and_keyed_by_name():
    a, b = parameter_points(ansatz(), 3), parameter_points(ansatz(), 3)
    val = lambda pts, i, name: next(v for p, v in pts[i].items() if p.name == name)
    assert [val(a, i, "theta") for i in range(3)] == [val(b, i, "theta") for i in range(3)]
    assert len({val(a, i, "theta") for i in range(3)}) == 3
    # adding a parameter (which also changes the sort order) moves nothing else
    c = parameter_points(ansatz(extra=True), 3)
    assert all(val(a, i, n) == val(c, i, n) for i in range(3) for n in ("theta", "phi"))
    assert parameter_points(ansatz(), 1, seed=1) != parameter_points(ansatz(), 1, seed=2)


def test_parameterized_bug_is_caught_at_every_point(backend):
    golden, edited = expand_parameters("ansatz", ansatz()), expand_parameters("ansatz", ansatz(bug=True))
    assert list(golden) == ["ansatz@p0", "ansatz@p1", "ansatz@p2"] == list(edited)
    for name in golden:
        snap = record(name, golden[name], backend, shots=300, n_seeds=2)
        assert check(name, golden[name], snap, backend).verdict is Verdict.PASS
        assert check(name, edited[name], snap, backend).verdict is Verdict.LOGIC_BUG


def test_unbound_parameters_fail_with_a_pointer_to_the_fix(backend):
    assert expand_parameters("ghz4", ghz4()) == {"ghz4": expand_parameters("ghz4", ghz4())["ghz4"]}
    with pytest.raises(ValueError, match="expand_parameters"):
        record("ansatz", ansatz(), backend)


def test_cli_expands_parameterized_suite_circuits(tmp_path, monkeypatch):
    from pathlib import Path
    from qdebug.cli import main
    suite = str(Path(__file__).resolve().parents[1] / "examples" / "suite.py")
    snaps = str(tmp_path / "s")
    monkeypatch.delenv("QDEBUG_DEMO_BUG", raising=False)
    assert main(["record", "--suite", suite, "--snapshots", snaps, "--seeds", "2",
                 "--param-points", "2", "--calibrate"]) == 0
    assert sorted(p.name for p in (tmp_path / "s").iterdir()) == [
        "ansatz@p0.json", "ansatz@p1.json", "ghz.json", "swap_test_like.json"]
    assert json.loads((tmp_path / "s" / "ghz.json").read_text())["noise"]["cost_weights"]
    base = ["check", "--suite", suite, "--snapshots", snaps, "--param-points", "2"]
    assert main(base) == 0
    monkeypatch.setenv("QDEBUG_DEMO_BUG", "wrong_param")
    assert main(base) == 1


# ---------------------------------------------------- IonQ native gateset

@pytest.mark.parametrize("noise_model, two_q", [("aria-1", "ms"), ("forte-1", "zz")])
def test_native_transpile_keeps_every_gate_and_the_logic(noise_model, two_q):
    """Offline: qiskit-ionq builds the native target without contacting IonQ."""
    from qiskit_ionq import IonQProvider
    from qdebug.backends import to_native
    be = IonQProvider("offline-no-requests").get_backend("simulator", gateset="native")
    be.set_options(noise_model=noise_model)
    plain, padded = with_probe(ghz4(), "Y"), with_probe(ghz4(pad_pairs=5), "Y")
    n_plain, n_padded = to_native([plain, padded], be)
    for src, nat in ((plain, n_plain), (padded, n_padded)):
        ops = nat.count_ops()
        assert set(ops) <= {"gpi", "gpi2", two_q, "measure", "barrier"}
        assert nat.num_qubits == 4 and ops["measure"] == 4
        assert Operator(nat.remove_final_measurements(inplace=False)).equiv(
            Operator(src.remove_final_measurements(inplace=False)))
    # the 10 redundant CX survive as 10 extra native 2-qubit gates
    assert n_padded.count_ops()[two_q] - n_plain.count_ops()[two_q] == 10


def test_native_mode_requests_native_gateset_for_noisy_runs_only(monkeypatch):
    import qiskit_ionq
    from qdebug import IonQBackend, backends
    from test_ionq_adapter import FakeIonQBackend

    asked = []

    class Provider:
        def __init__(self, token=None):
            pass

        def get_backend(self, name, gateset="qis"):
            asked.append(gateset)
            return FakeIonQBackend()

    monkeypatch.setattr(qiskit_ionq, "IonQProvider", Provider)
    monkeypatch.setattr(backends, "to_native", lambda circuits, be: circuits)
    be = IonQBackend(noise_model="forte-1", api_key="fake", native=True)
    assert be.describe()["gateset"] == "native"
    pcs = [with_probe(ghz4(), "Z")]
    be.ideal_probs(pcs)
    be.noisy_counts(pcs, 100, seed=1)
    assert asked == ["native"]
    snap = record("ghz4", ghz4(), be, shots=200, n_seeds=2)
    assert check("ghz4", ghz4(), snap, be, CheckConfig()).verdict is Verdict.PASS
