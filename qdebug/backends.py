"""Backend adapters.

Every backend exposes the same two primitives the debugger needs:

* ``ideal_probs(circuits)``  -> exact (shot-noise-free) output distributions
* ``noisy_counts(circuits, shots, seed)`` -> sampled counts under a noise model

Implementations
---------------
* :class:`LocalBackend`  - exact statevector probabilities + Qiskit Aer with a
  depolarizing noise model (an IonQ-*like* approximation). No account needed;
  this is what the tests and offline development use.
* :class:`IonQBackend`   - IonQ cloud simulator through ``qiskit-ionq``. Exact
    ideal probabilities come from local statevector simulation; remote ideal
    probabilities are available for diagnostics. Noisy jobs use one of IonQ's
    hardware noise models (e.g. ``forte-1``) with a reproducible seed.
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


# Every transpile() call passes num_processes=1. Batches here are a handful of
# tiny circuits, and qiskit's default process pool costs far more than it saves;
# on Python 3.14 (forkserver start method) it also re-imports the caller's
# __main__, which breaks scripts and notebooks without a __main__ guard.


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
                        optimization_level=0, seed_transpiler=seed,
                        num_processes=1)
        res = sim.run(tcs, shots=shots, seed_simulator=seed).result()
        return [{normalize_key(k): v for k, v in res.get_counts(i).items()}
                for i in range(len(circuits))]


# -------------------------------------------------------------------- IonQ

def to_native(circuits: list[QuantumCircuit], backend) -> list[QuantumCircuit]:
    """Rewrite circuits to IonQ native gates (gpi, gpi2, ms or zz) for a
    ``gateset="native"`` qiskit-ionq backend, keeping every gate.

    IonQ's cloud compiler optimises circuits sent in the default ``qis``
    gateset, so redundant gates may never run and their noise goes unmeasured.
    Native-gate circuits are executed as written. ``optimization_level=0``
    translates gate by gate without cancelling anything.

    Transpiling against the backend pads the circuit to the device width; the
    idle wires are dropped again so the job stays the size of the circuit.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wide = transpile(circuits, backend=backend, optimization_level=0,
                         num_processes=1)
    out = []
    for src, tc in zip(circuits, wide):
        n = src.num_qubits
        slim = QuantumCircuit(n, src.num_clbits, name=src.name)
        for inst in tc.data:
            qs = [tc.find_bit(q).index for q in inst.qubits]
            if inst.operation.name == "barrier":
                qs = [q for q in qs if q < n]
                if not qs:
                    continue
                slim.barrier(qs)
                continue
            if max(qs) >= n:
                raise RuntimeError(
                    f"native transpilation moved {src.name!r} onto wire {max(qs)}; "
                    "expected the trivial layout")
            cs = [tc.find_bit(c).index for c in inst.clbits]
            slim.append(inst.operation, qs, cs)
        out.append(slim)
    return out


class IonQBackend(Backend):
    """IonQ cloud simulator via qiskit-ionq (ideal + hardware noise models).

    The API key is read from ``IONQ_API_KEY`` unless passed explicitly. Ideal
    and noisy simulation are free on IonQ's cloud simulator.

    Ideal probabilities are computed locally because qiskit-ionq's ideal
    simulator result formatting samples one shot and drops unobserved outcomes.
    ``remote_ideal_probs`` is retained for checking the cloud result; IonQ's
    returned probabilities are rounded to three decimal places.
    """

    remote_probability_resolution = 1e-3

    def __init__(self, noise_model: str = "forte-1", api_key: str | None = None,
                 backend_name: str = "simulator", native: bool = False):
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
        self.noise_model = noise_model
        self.backend_name = backend_name
        # native=True submits the *noisy* runs in IonQ's native gates so the
        # cloud compiler cannot optimise the circuit under test. Ideal runs stay
        # on the default gateset: they only need the circuit's logic.
        self.native = native
        self.name = f"ionq-{backend_name}"

    def describe(self) -> dict:
        return {"backend": self.name, "noise_model": self.noise_model,
                "gateset": "native" if self.native else "qis"}

    def _backend(self, noise_model: str, seed: int | None = None,
                 gateset: str = "qis"):
        # fresh backend object per call so options never leak between jobs
        if gateset == "qis":
            be = self.provider.get_backend(self.backend_name)
        else:
            be = self.provider.get_backend(self.backend_name, gateset=gateset)
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
                                 optimization_level=0, num_processes=1)
        except Exception:  # fall back to submitting circuits as-is
            return circuits

    def ideal_probs(self, circuits):
        return [exact_probs(circuit) for circuit in circuits]

    def remote_ideal_probs(self, circuits):
        be = self._backend("ideal")
        job = be.run(self._prepare(circuits), shots=1)
        client = getattr(job, "_client", None)
        if client is None:
            probs = _as_list(job.get_probabilities(), len(circuits))
            return [clean_probs(dict(p)) for p in probs]

        # qiskit-ionq 1.1.1 removes exact-probability bins absent from its
        # client-side one-shot sample. Read the same raw payload before that
        # lossy formatting step, then apply the SDK's recorded measurement map.
        job.wait_for_final_state()
        results_url = job._results_urls.get("probabilities")
        if not results_url:
            probs = _as_list(job.get_probabilities(), len(circuits))
            return [clean_probs(dict(p)) for p in probs]
        payload = client.get_results(results_url=results_url)

        def is_histogram(value):
            return isinstance(value, dict) and all(
                isinstance(probability, (int, float))
                for probability in value.values()
            )

        if isinstance(payload, list):
            raw_probs = payload
        elif isinstance(payload, dict) and payload and all(
                is_histogram(value) for value in payload.values()):
            raw_probs = list(payload.values())
        elif isinstance(payload, dict):
            raw_probs = [payload]
        else:
            raise ValueError("unexpected IonQ probability payload")
        if len(raw_probs) != len(circuits):
            raise ValueError(
                f"IonQ returned {len(raw_probs)} probability distributions "
                f"for {len(circuits)} circuits"
            )

        from qiskit_ionq.ionq_job import map_output

        clbits_per_circuit = job._clbits
        if len(clbits_per_circuit) != len(circuits):
            raise ValueError("IonQ result is missing per-circuit measurement maps")
        output = []
        for circuit, clbits, raw in zip(circuits, clbits_per_circuit, raw_probs):
            measured_bits = clbits or list(range(circuit.num_qubits))
            mapped = map_output(raw, measured_bits, circuit.num_qubits)
            width = len(measured_bits)
            output.append(clean_probs({
                format(int(key), f"0{width}b"): probability
                for key, probability in mapped.items()
            }))
        return output

    def noisy_counts(self, circuits, shots, seed):
        if self.native:
            be = self._backend(self.noise_model, seed=seed, gateset="native")
            job = be.run(to_native(circuits, be), shots=shots)
        else:
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
        tcs = transpile(circuits, self.backend, optimization_level=0,
                        num_processes=1)
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
        return IonQBackend(noise_model=noise_model, api_key=kw.get("api_key"),
                           native=bool(kw.get("native")))
    raise ValueError(f"unknown backend {kind!r} (use 'local' or 'ionq')")
