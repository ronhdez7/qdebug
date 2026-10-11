"""Exercise IonQBackend against a fake qiskit-ionq provider (no network/API key).

The fake mimics what qiskit-ionq 1.x does: options set via set_options(),
ideal jobs expose exact probabilities via get_probabilities(), noisy jobs give
counts, single-circuit jobs return a dict and batches return a list.
"""
import pytest

import qiskit_ionq
from qdebug import CheckConfig, IonQBackend, Verdict, check, record
from qdebug.backends import LocalBackend, exact_probs
from qdebug.demo_suite import bell, ghz4


class FakeJob:
    def __init__(self, probs=None, counts=None):
        self._p, self._c = probs, counts

    @staticmethod
    def _shape(x):
        return x[0] if len(x) == 1 else x

    def get_probabilities(self, circuit=None):
        return self._shape(self._p)

    def get_counts(self, circuit=None):
        return self._shape(self._c)


class FakeProbabilityClient:
    def __init__(self, payload):
        self.payload = payload

    def get_results(self, results_url):
        assert results_url == "probabilities-url"
        return self.payload


class FakeRawProbabilityJob:
    def __init__(self, payload, clbits):
        self._client = FakeProbabilityClient(payload)
        self._results_urls = {"probabilities": "probabilities-url"}
        self._clbits = clbits

    def wait_for_final_state(self):
        pass

    def get_probabilities(self):
        # qiskit-ionq 1.1.1 filters probabilities by one-shot sampled counts.
        return [{"111": 0.5}, {"001": 1.0}]


class FakeIonQBackend:
    log = []

    def __init__(self):
        self.opts = {"noise_model": "ideal", "sampler_seed": None}

    def set_options(self, **kw):
        assert set(kw) <= {"noise_model", "sampler_seed", "shots"}, kw
        self.opts.update(kw)

    def run(self, circuits, shots=1024):
        circuits = circuits if isinstance(circuits, list) else [circuits]
        FakeIonQBackend.log.append((dict(self.opts), shots, len(circuits)))
        if self.opts["noise_model"] == "ideal":
            # real ideal sim returns int-keyed probs; we return bitstrings
            return FakeJob(probs=[exact_probs(c) for c in circuits])
        seed = self.opts["sampler_seed"]
        counts = LocalBackend(p2=2e-2).noisy_counts(circuits, shots, seed)
        return FakeJob(counts=counts)


class FakeProvider:
    def __init__(self, token=None):
        self.token = token

    def get_backend(self, name):
        assert name == "simulator"
        return FakeIonQBackend()


@pytest.fixture()
def ionq(monkeypatch):
    monkeypatch.setattr(qiskit_ionq, "IonQProvider", FakeProvider)
    FakeIonQBackend.log.clear()
    return IonQBackend(noise_model="forte-1", api_key="fake")


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("IONQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="IONQ_API_KEY"):
        IonQBackend()


def test_ideal_and_noisy_options(ionq):
    from qdebug.circuits import probes
    pcs = probes(bell(), ("Z", "X"))
    p = ionq.remote_ideal_probs(pcs)
    assert len(p) == 2 and abs(sum(p[0].values()) - 1) < 1e-9
    c = ionq.noisy_counts(pcs, 300, seed=42)
    assert sum(c[0].values()) == 300
    ideal_opts, noisy_opts = FakeIonQBackend.log[0][0], FakeIonQBackend.log[1][0]
    assert ideal_opts["noise_model"] == "ideal"
    assert noisy_opts == {"noise_model": "forte-1", "sampler_seed": 42}


def test_single_circuit_job_shape_is_handled(ionq):
    from qdebug.circuits import probes
    pc = probes(bell(), ("Z",))
    assert len(ionq.remote_ideal_probs(pc)) == 1
    assert len(ionq.noisy_counts(pc, 50, 1)) == 1


def test_ideal_probabilities_keep_all_raw_outcomes(ionq, monkeypatch):
    from qiskit import QuantumCircuit
    from qdebug.circuits import probes

    qc = QuantumCircuit(3, name="asym")
    qc.x(0)
    qc.h(1)
    qc.cx(1, 2)
    qc.rz(0.3, 2)
    one_hot = QuantumCircuit(3, name="one_hot")
    one_hot.x(0)
    circuits = probes(qc, ("Z",)) + probes(one_hot, ("Z",))
    payload = {"child-1": {"1": 0.5, "7": 0.5}, "child-2": {"1": 1.0}}

    def run_with_raw_probabilities(self, submitted, shots=1):
        return FakeRawProbabilityJob(payload, [[0, 1, 2] for _ in submitted])

    monkeypatch.setattr(FakeIonQBackend, "run", run_with_raw_probabilities)

    assert ionq.remote_ideal_probs(circuits) == [
        {"001": 0.5, "111": 0.5},
        {"001": 1.0},
    ]


def test_ideal_probabilities_are_exact_local_results(ionq):
    from qdebug.circuits import probes
    circuits = probes(bell(), ("Z", "X"))
    assert ionq.ideal_probs(circuits) == [exact_probs(circuit) for circuit in circuits]


def test_end_to_end_check_with_ionq_backend(ionq):
    snap = record("ghz4", ghz4(), ionq, shots=500, n_seeds=3)
    assert snap.noise.noise_model["backend"] == "ionq-simulator"
    ok = check("ghz4", ghz4(), snap, ionq, CheckConfig())
    assert ok.verdict is Verdict.PASS and not ok.warnings
    bug = check("ghz4", ghz4(flip_first_cx=True), snap, ionq, CheckConfig())
    assert bug.verdict is Verdict.LOGIC_BUG
    assert ionq.default_logic_tol == 1e-9 and bug.logic_tol == 1e-9


def test_cross_check_warns_on_bit_order_mismatch(ionq, monkeypatch):
    """If IonQ's ideal output were bit-reversed vs Qiskit, we must say so."""
    snap = record("ghz4", ghz4(flip_first_cx=False), ionq, shots=300, n_seeds=2)
    real = ionq.remote_ideal_probs

    def reversed_probs(cs):
        return [{k[::-1]: v for k, v in p.items()} for p in real(cs)]

    # asymmetric circuit so reversal is visible
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(3, name="ghz4")
    qc.x(0)
    snap2 = record("ghz4", qc, ionq, shots=300, n_seeds=2)
    monkeypatch.setattr(ionq, "remote_ideal_probs", reversed_probs)
    r = check("ghz4", qc, snap2, ionq)
    assert any("bit ordering" in w for w in r.warnings)
