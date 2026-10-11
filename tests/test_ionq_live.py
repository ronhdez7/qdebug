"""Opt-in checks against the real IonQ cloud simulator.

    IONQ_API_KEY=... python -m pytest -m ionq -v

Deselected by default and skipped without a key, so the normal suite never
touches the network. These exist to confirm the assumptions the fake provider
in test_ionq_adapter.py encodes (bit order, exact probabilities, job shapes).
"""
import os

import pytest

from qdebug import Verdict, check, make_backend, record
from qdebug.backends import exact_probs
from qdebug.circuits import probes
from qdebug.demo_suite import ghz4
from qdebug.stats import tvd

pytestmark = [
    pytest.mark.ionq,
    pytest.mark.skipif(not os.environ.get("IONQ_API_KEY"), reason="IONQ_API_KEY not set"),
]


@pytest.fixture(scope="module")
def ionq():
    return make_backend("ionq", noise_model=os.environ.get("QDEBUG_NOISE_MODEL", "forte-1"))


def test_doctor_passes_on_ionq():
    from qdebug.cli import main
    assert main(["doctor", "--backend", "ionq"]) == 0


def test_ideal_probabilities_and_remote_support(ionq):
    """Verdicts use exact local probabilities; remote probes preserve support."""
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(3, name="asym")
    qc.x(0)
    qc.h(1)
    qc.cx(1, 2)
    qc.rz(0.3, 2)
    pcs = probes(qc, ("Z", "X", "Y"))
    local = ionq.ideal_probs(pcs)
    remote = ionq.remote_ideal_probs(pcs)
    assert len(remote) == len(pcs)
    for p, exact, c in zip(remote, local, pcs):
        assert set(p) == set(exact)
        assert abs(sum(p.values()) - 1.0) < 1e-9
        assert tvd(exact, exact_probs(c)) < 1e-12


def test_noisy_counts_shape_and_seed_reproducibility(ionq):
    pcs = probes(ghz4(), ("Z", "X"))
    a = ionq.noisy_counts(pcs, 200, seed=11)
    b = ionq.noisy_counts(pcs, 200, seed=11)
    assert len(a) == 2 and all(sum(c.values()) == 200 for c in a)
    assert all(len(k) == 4 and set(k) <= {"0", "1"} for c in a for k in c)
    assert a == b


def test_end_to_end_verdicts_on_ionq(ionq):
    snap = record("ghz4", ghz4(), ionq, shots=500, n_seeds=2)
    ok = check("ghz4", ghz4(), snap, ionq)
    assert ok.verdict is Verdict.PASS and not ok.warnings, ok.reason
    bug = check("ghz4", ghz4(flip_first_cx=True), snap, ionq)
    assert bug.verdict is Verdict.LOGIC_BUG, bug.reason
