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
    # equivalence="probes" switches off the exact statevector comparison, which
    # would otherwise catch this regardless of the probe bases
    probes_only = CheckConfig(equivalence="probes")
    assert check("ghz4_z", bad, snap_z, backend, probes_only).verdict \
        is not Verdict.LOGIC_BUG
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


def test_bug_hidden_from_uniform_bases_is_caught_by_exact_state_check(snapshots, backend):
    m = next(x for x in mutants() if x.name == "bell_rot_s_to_sdg")
    snap = snapshots["bell_rot"]
    r = check(m.name, m.circuit, snap, backend)
    assert all(d < 1e-9 for d in r.per_basis_logic_dev.values())   # probes blind
    assert r.verdict is Verdict.LOGIC_BUG and r.state_dist == pytest.approx(1.0)
    assert [b.hunk.added for b in r.blame if b.culprit] == [["sdg q1"]]
    # without the exact check the uniform probes wave it through
    blind = check(m.name, m.circuit, snap, backend, CheckConfig(equivalence="probes"))
    assert blind.verdict is Verdict.REFACTOR_OK


def test_bug_hidden_from_uniform_bases_is_caught_by_mixed_probe(backend):
    """Same bug, exact check off: a per-qubit mixed basis is enough."""
    from qdebug import record
    from qdebug.demo_suite import bell_rot
    probes_only = CheckConfig(equivalence="probes")
    snap = record("bell_rot", bell_rot(), backend, bases=("Z", "X", "Y", "ZY"),
                  shots=500, n_seeds=2)
    r = check("bell_rot", bell_rot(sdg=True), snap, backend, probes_only)
    assert r.verdict is Verdict.LOGIC_BUG
    assert max(r.per_basis_logic_dev, key=r.per_basis_logic_dev.get) == "ZY"


def test_unitary_only_difference_warns_or_fails_by_equivalence_level(snapshots, backend):
    m = next(x for x in mutants() if x.name == "qft3_dead_angle")
    r = check(m.name, m.circuit, snapshots["qft3"], backend)
    assert r.verdict is Verdict.REFACTOR_OK and r.state_dist < 1e-9
    assert r.unitary_dist > 1e-3 and any("unitaries differ" in w for w in r.warnings)
    strict = check(m.name, m.circuit, snapshots["qft3"], backend,
                   CheckConfig(equivalence="unitary"))
    assert strict.verdict is Verdict.LOGIC_BUG
    assert [b.hunk.added for b in strict.blame if b.culprit] == [["cp(1.8708) q1,q0"]]


def test_true_refactors_are_unitarily_equivalent(snapshots, backend):
    for name in ("ghz4_extra_HH", "bv_oracle_reordered"):
        m = next(x for x in mutants() if x.name == name)
        r = check(m.name, m.circuit, snapshots[m.base], backend,
                  CheckConfig(equivalence="unitary"))
        assert r.verdict is Verdict.REFACTOR_OK and not r.warnings
        assert r.equivalence == "unitary" and r.unitary_dist < 1e-9


def test_random_bases_are_seeded_mixed_and_recorded(backend):
    from qdebug import record
    from qdebug.circuits import random_bases
    assert random_bases(4, 3, seed=1) == random_bases(4, 3, seed=1)
    assert all(len(b) == 4 and len(set(b)) > 1 for b in random_bases(4, 3, seed=1))
    assert random_bases(1, 3, seed=1) == []
    assert len(random_bases(2, 99, seed=1)) == 6          # 3^2 minus 3 uniform
    snap = record("ghz4", ghz4(), backend, shots=200, n_seeds=2, random_bases=2)
    assert snap.bases[:3] == ["Z", "X", "Y"] and len(snap.bases) == 5
    assert check("ghz4", ghz4(), snap, backend).verdict is Verdict.PASS


def test_unknown_equivalence_level_is_rejected(snapshots, backend):
    with pytest.raises(ValueError, match="equivalence"):
        check("ghz4", ghz4(), snapshots["ghz4"], backend, CheckConfig(equivalence="x"))


@pytest.mark.slow
def test_no_false_alarms_over_a_thousand_checks(snapshots, backend):
    """Every golden circuit, 170 noisy-run seeds each (1,020 checks): all PASS."""
    from qdebug.demo_suite import golden_circuits
    bad, n = [], 0
    for name, qc in golden_circuits().items():
        for seed in range(9000, 9170):
            n += 1
            r = check(name, qc, snapshots[name], backend, CheckConfig(seed=seed))
            if r.verdict is not Verdict.PASS:
                bad.append((name, seed, r.verdict.value, r.noise_dev, r.noise_budget))
    assert n >= 1000 and not bad, bad


@pytest.mark.parametrize("name", ["ghz4", "bv", "grover3", "qft3", "bell", "bell_rot"])
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
