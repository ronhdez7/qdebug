"""Backend adapters.

Every backend exposes the same two primitives the debugger needs:

* ``ideal_probs(circuits)``  -> exact (shot-noise-free) output distributions
* ``noisy_counts(circuits, shots, seed)`` -> sampled counts under a noise model

Implementations
---------------
* :class:`LocalBackend`  - exact statevector probabilities + Qiskit Aer with a
  depolarizing noise model (an IonQ-*like* approximation). No account needed;
  this is what the tests and offline development use.
* :class:`IonQBackend`   - IonQ cloud simulator through ``qiskit-ionq``. Ideal
  jobs return exact probabilities (``job.get_probabilities()``); noisy jobs use
  one of IonQ's hardware noise models (e.g. ``forte-1``) with a reproducible seed.
* :class:`QiskitBackendAdapter` - wrap *any* other Qiskit backend that can
  return counts (handy if the hackathon platform ships its own simulator).
"""
from __future__ import annotations

import os
import warnings
from abc import ABC, abstractmethod

from qiskit import QuantumCircuit, transpile

from .stats import Dist, clean_probs, normalize_key

MAX_LOCAL_QUBITS = 24


def _as_list(x, n: int) -> list:
    """qiskit returns a dict for 1 circuit and a list for many; normalise."""
    if isinstance(x, (list, tuple)):
        return list(x)
    if n == 1:
        return [x]
    raise ValueError("expected one result per circuit")


class Backend(ABC):
    name: str = "backend"
    # Max TVD between two ideal distributions still treated as "identical".
    # Local statevector maths is exact to ~1e-15; IonQ returns probabilities over
    # the wire, so we allow a looser default (override with --logic-tol).
    default_logic_tol: float = 1e-9

    @abstractmethod
    def ideal_probs(self, circuits: list[QuantumCircuit]) -> list[Dist]: ...

    @abstractmethod
    def noisy_counts(self, circuits: list[QuantumCircuit], shots: int,
                     seed: int) -> list[dict]: ...

    def describe(self) -> dict:
        return {"backend": self.name}


# ------------------------------------------------------------------- local

def exact_probs(circuit: QuantumCircuit) -> Dist:
    """Exact output distribution of a measured circuit via statevector."""
    from qiskit.quantum_info import Statevector

    if circuit.num_qubits > MAX_LOCAL_QUBITS:
        raise ValueError(
            f"{circuit.num_qubits} qubits is too many for the local exact "
            f"simulator (limit {MAX_LOCAL_QUBITS}); use the IonQ simulator."
        )
    unitary_part = circuit.remove_final_measurements(inplace=False)
    sv = Statevector.from_instruction(unitary_part)
    return clean_probs(sv.probabilities_dict())


class LocalBackend(Backend):
    """Exact ideal probabilities + Aer depolarizing-noise sampling."""

    def __init__(self, p1: float = 5e-4, p2: float = 5e-3, readout: float = 0.0):
        self.p1, self.p2, self.readout = p1, p2, readout
        self.name = "local-aer"

    def describe(self) -> dict:
        return {"backend": self.name, "noise": "depolarizing",
                "p1": self.p1, "p2": self.p2, "readout": self.readout}

    def ideal_probs(self, circuits):
        return [exact_probs(c) for c in circuits]

    def _noise_model(self):
        from qiskit_aer.noise import NoiseModel, ReadoutError, depolarizing_error

        nm = NoiseModel(basis_gates=["rz", "sx", "x", "cx", "id"])
        nm.add_all_qubit_quantum_error(depolarizing_error(self.p1, 1), ["sx", "x"])
        nm.add_all_qubit_quantum_error(depolarizing_error(self.p2, 2), ["cx"])
        if self.readout:
            r = self.readout
            nm.add_all_qubit_readout_error(ReadoutError([[1 - r, r], [r, 1 - r]]))
        return nm

    def noisy_counts(self, circuits, shots, seed):
        from qiskit_aer import AerSimulator

        nm = self._noise_model()
        sim = AerSimulator(noise_model=nm)
        # optimization_level=0 keeps the circuit's structure: a CX-CX pair that
        # is logically a no-op must still cost noise (that's what we test for).
        tcs = transpile(circuits, basis_gates=["rz", "sx", "x", "cx"],
                        optimization_level=0, seed_transpiler=seed)
        res = sim.run(tcs, shots=shots, seed_simulator=seed).result()
        return [{normalize_key(k): v for k, v in res.get_counts(i).items()}
                for i in range(len(circuits))]


# -------------------------------------------------------------------- IonQ

class IonQBackend(Backend):
    """IonQ cloud simulator via qiskit-ionq (ideal + hardware noise models).

    The API key is read from ``IONQ_API_KEY`` unless passed explicitly. Ideal
    and noisy simulation are free on IonQ's cloud simulator.
    """

    def __init__(self, noise_model: str = "forte-1", api_key: str | None = None,
                 backend_name: str = "simulator"):
        try:
            from qiskit_ionq import IonQProvider
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pip install qiskit-ionq") from e
        key = api_key or os.environ.get("IONQ_API_KEY")
        if not key:
            raise RuntimeError(
                "IONQ_API_KEY is not set. Create a free key at "
                "https://cloud.ionq.com/settings/keys and export it, or use "
                "--backend local."
            )
        self.provider = IonQProvider(key)
        self.default_logic_tol = 1e-4
        self.noise_model = noise_model
        self.backend_name = backend_name
        self.name = f"ionq-{backend_name}"

    def describe(self) -> dict:
        return {"backend": self.name, "noise_model": self.noise_model}

    def _backend(self, noise_model: str, seed: int | None = None):
        # fresh backend object per call so options never leak between jobs
        be = self.provider.get_backend(self.backend_name)
        opts = {"noise_model": noise_model}
        if seed is not None:
            opts["sampler_seed"] = seed
        be.set_options(**opts)
        return be

    def _prepare(self, circuits):
        """Rewrite to IonQ's accepted gate set without optimising away structure."""
        try:
            from qiskit_ionq.helpers import ionq_basis_gates

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return transpile(circuits, basis_gates=list(ionq_basis_gates),
                                 optimization_level=0)
        except Exception:  # fall back to submitting circuits as-is
            return circuits

    def ideal_probs(self, circuits):
        be = self._backend("ideal")
        job = be.run(self._prepare(circuits), shots=1)
        probs = _as_list(job.get_probabilities(), len(circuits))
        return [clean_probs(dict(p)) for p in probs]

    def noisy_counts(self, circuits, shots, seed):
        be = self._backend(self.noise_model, seed=seed)
        job = be.run(self._prepare(circuits), shots=shots)
        counts = _as_list(job.get_counts(), len(circuits))
        return [{normalize_key(k): v for k, v in dict(c).items()} for c in counts]


# ----------------------------------------------------------- generic Qiskit

class QiskitBackendAdapter(Backend):
    """Use any Qiskit backend for noisy counts; ideal probs come from the
    exact local simulator. Pass e.g. a vendor simulator provided by your
    hackathon platform."""

    def __init__(self, backend, name: str | None = None):
        self.backend = backend
        self.name = name or getattr(backend, "name", "qiskit-backend")

    def ideal_probs(self, circuits):
        return [exact_probs(c) for c in circuits]

    def noisy_counts(self, circuits, shots, seed):
        tcs = transpile(circuits, self.backend, optimization_level=0)
        try:
            job = self.backend.run(tcs, shots=shots, seed_simulator=seed)
        except TypeError:
            job = self.backend.run(tcs, shots=shots)
        counts = _as_list(job.result().get_counts(), len(circuits))
        return [{normalize_key(k): v for k, v in dict(c).items()} for c in counts]


def make_backend(kind: str, noise_model: str = "forte-1", **kw) -> Backend:
    kind = kind.lower()
    if kind == "local":
        return LocalBackend(**{k: v for k, v in kw.items()
                               if k in ("p1", "p2", "readout")})
    if kind == "ionq":
        return IonQBackend(noise_model=noise_model, api_key=kw.get("api_key"))
    raise ValueError(f"unknown backend {kind!r} (use 'local' or 'ionq')")
