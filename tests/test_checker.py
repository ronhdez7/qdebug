import pytest
from qiskit import QuantumCircuit

from qdebug import CheckConfig, Verdict, check
from qdebug.demo_suite import ghz4, mutants


@pytest.mark.parametrize("m", mutants(), ids=lambda m: m.name)
def test_demo_mutants_classified_correctly(m, snapshots, backend):
    r = check(m.name, m.circuit, snapshots[m.base], backend)
    assert r.verdict.value == m.expected, r.reason


def test_logic_bug_is_blamed_on_the_right_hunk(snapshots, backend):
    m = next(x for x in mutants() if x.name == "ghz4_cx_flipped")
    r = check(m.name, m.circuit, snapshots["ghz4"], backend)
    culprits = [b for b in r.blame if b.culprit]
    assert len(culprits) == 1
    assert culprits[0].hunk.added == ["cx q1,q0"]


def test_refactor_has_no_culprits(snapshots, backend):
    m = next(x for x in mutants() if x.name == "ghz4_extra_HH")
    r = check(m.name, m.circuit, snapshots["ghz4"], backend)
    assert r.verdict is Verdict.REFACTOR_OK and r.hunks and r.logic_dev < 1e-9


def test_phase_bug_invisible_in_z_only_is_caught_by_x_probe(backend):
    from qdebug import record
    g = ghz4()
    snap_z = record("ghz4", g, backend, bases=("Z",), shots=500, n_seeds=2)
    bad = ghz4(final_z=True)
    assert check("ghz4_z", bad, snap_z, backend).verdict is not Verdict.LOGIC_BUG
    snap_zx = record("ghz4", g, backend, bases=("Z", "X"), shots=500, n_seeds=2)
    assert check("ghz4_z", bad, snap_zx, backend).verdict is Verdict.LOGIC_BUG


def test_small_bug_below_shot_noise_is_caught_where_naive_test_is_blind(snapshots, backend):
    m = next(x for x in mutants() if x.name == "bell_tiny_phase_drift")
    r = check(m.name, m.circuit, snapshots["bell"], backend)
    assert r.verdict is Verdict.LOGIC_BUG
    assert r.naive_fails is False          # a fixed-threshold shot test misses it


def test_noise_regression_is_not_called_a_logic_bug(snapshots, backend):
    m = next(x for x in mutants() if x.name == "ghz4_cx_padding")
    r = check(m.name, m.circuit, snapshots["ghz4"], backend)
    assert r.verdict is Verdict.NOISE_REGRESSION
    assert r.logic_dev < 1e-9 and r.naive_fails is True   # naive test mislabels it


def test_qubit_count_change_is_a_logic_bug(snapshots, backend):
    qc = QuantumCircuit(5, name="ghz4")
    qc.h(0)
    assert check("ghz4", qc, snapshots["ghz4"], backend).verdict is Verdict.LOGIC_BUG


@pytest.mark.parametrize("name", ["ghz4", "bv", "grover3", "qft3", "bell"])
def test_no_false_alarms_over_many_noise_seeds(name, snapshots, backend):
    """Unchanged circuit, 12 different noisy-run seeds: never flag anything."""
    from qdebug.demo_suite import golden_circuits
    qc = golden_circuits()[name]
    bad = []
    for seed in range(5000, 5012):
        r = check(name, qc, snapshots[name], backend, CheckConfig(seed=seed))
        if r.verdict is not Verdict.PASS:
            bad.append((seed, r.verdict.value, r.noise_dev, r.noise_budget))
    assert not bad, bad
