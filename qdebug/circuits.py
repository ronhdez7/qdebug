"""Circuit utilities: normalisation, signatures, basis probes, serialisation."""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from qiskit import QuantumCircuit, qpy

# Measurement bases we can probe. Measuring only in Z hides phase bugs
# (e.g. a stray Z before readout), so by default we also measure in X and Y.
PROBE_BASES = ("Z", "X", "Y")


def strip_measurements(qc: QuantumCircuit) -> QuantumCircuit:
    """Return a unitary-only copy on a single flat qubit register.

    Final measurements and barriers are dropped (barriers have no logical effect
    and clutter diffs). Circuits with mid-circuit measurement or classical
    control are rejected: the debugger compares output distributions of
    unitary circuits, and silently stripping those would change semantics.
    """
    if qc.parameters:
        names = ", ".join(sorted(p.name for p in qc.parameters))
        raise ValueError(
            f"{qc.name!r} has unbound parameters ({names}); bind them, or use "
            "qdebug.expand_parameters() to check it at several parameter points.")
    out = QuantumCircuit(qc.num_qubits, name=qc.name)
    measured: set[int] = set()
    for inst in qc.data:
        op = inst.operation
        idx = [qc.find_bit(q).index for q in inst.qubits]
        if op.name == "barrier":
            continue
        if op.name == "measure":
            measured.update(idx)
            continue
        if inst.clbits or getattr(op, "condition", None) is not None:
            raise ValueError(
                f"{qc.name!r}: classical control ({op.name}) is not supported; "
                "pass a unitary circuit (measurements are added automatically)."
            )
        if measured.intersection(idx):
            raise ValueError(
                f"{qc.name!r}: gate '{op.name}' acts on a qubit after it was "
                "measured (mid-circuit measurement is not supported)."
            )
        out.append(op, [out.qubits[i] for i in idx])
    return out


def with_probe(qc: QuantumCircuit, basis: str) -> QuantumCircuit:
    """Append a basis change + measure_all so the output reveals ``basis``.

    ``basis`` is either one letter applied to every qubit (``"X"``) or one
    letter per qubit, qubit 0 first (``"XZY"`` measures q0 in X, q1 in Z, q2 in
    Y). Mixed bases see correlations the uniform ones cannot.
    """
    basis = basis.upper()
    per_qubit = basis * qc.num_qubits if len(basis) == 1 else basis
    if len(per_qubit) != qc.num_qubits or set(per_qubit) - set(PROBE_BASES):
        raise ValueError(
            f"bad basis {basis!r} for {qc.num_qubits} qubits: use one of "
            f"{PROBE_BASES} or one letter per qubit")
    probe = qc.copy()
    probe.name = f"{qc.name}__{basis}"
    for q, b in enumerate(per_qubit):
        if b == "X":
            probe.h(q)
        elif b == "Y":
            probe.sdg(q)
            probe.h(q)
    probe.measure_all()
    return probe


def random_bases(n_qubits: int, k: int, seed: int) -> list[str]:
    """Up to ``k`` distinct seeded per-qubit bases, none of them uniform."""
    import numpy as np

    if n_qubits < 2 or k <= 0:
        return []
    rng = np.random.default_rng(seed)
    out: list[str] = []
    limit = min(k, 3 ** n_qubits - 3)
    while len(out) < limit:
        b = "".join(rng.choice(PROBE_BASES, size=n_qubits))
        if len(set(b)) > 1 and b not in out:
            out.append(b)
    return out


def probes(qc: QuantumCircuit, bases: tuple[str, ...]) -> list[QuantumCircuit]:
    return [with_probe(qc, b) for b in bases]


# ------------------------------------------------------- parameter sweeps

def parameter_points(qc: QuantumCircuit, n_points: int = 3, seed: int = 0) -> list[dict]:
    """``n_points`` reproducible bindings for a parameterized circuit.

    Each value depends only on ``(seed, parameter name, point index)``, so the
    golden and the edited circuit get the same angle for every parameter they
    share, even if an edit adds, removes or reorders parameters. Angles are
    generic (never 0 or a multiple of pi/2), where many bugs are invisible.
    """
    import hashlib
    from math import pi

    points = []
    for i in range(n_points):
        binding = {}
        for p in qc.parameters:
            h = hashlib.sha256(f"{seed}:{p.name}:{i}".encode()).digest()
            u = int.from_bytes(h[:8], "big") / 2 ** 64          # [0, 1)
            binding[p] = (2 * u - 1) * pi
        points.append(binding)
    return points


def expand_parameters(name: str, qc: QuantumCircuit, n_points: int = 3,
                      seed: int = 0) -> dict[str, QuantumCircuit]:
    """Turn a parameterized circuit into ``{"name@p0": bound, "name@p1": ...}``.

    A circuit without free parameters is returned unchanged under ``name``.
    """
    if not qc.parameters:
        return {name: qc}
    out = {}
    for i, binding in enumerate(parameter_points(qc, n_points, seed)):
        bound = qc.assign_parameters(binding)
        bound.name = f"{name}@p{i}"
        out[bound.name] = bound
    return out


# ---------------------------------------------------------------- signatures

def _fmt_param(p) -> str:
    try:
        return f"{float(p):.6g}"
    except (TypeError, ValueError):
        return str(p)


def instruction_signatures(qc: QuantumCircuit) -> list[str]:
    """Human-readable, order-preserving signature of each instruction.

    e.g. ``["h q0", "cx q0,q1", "rz(1.5708) q2"]``. Used for diffing and for
    the snapshot's readable record.
    """
    sigs = []
    for inst in qc.data:
        op = inst.operation
        if op.name == "barrier":
            continue
        qs = ",".join(f"q{qc.find_bit(q).index}" for q in inst.qubits)
        params = ""
        if op.params:
            params = "(" + ",".join(_fmt_param(p) for p in op.params) + ")"
        sigs.append(f"{op.name}{params} {qs}")
    return sigs


@dataclass(frozen=True)
class CircuitStats:
    n_qubits: int
    depth: int
    n_1q: int
    n_2q: int
    n_multi: int  # 3+ qubit gates

    def noise_cost(self, w1: float = 0.05, w2: float = 1.0, wm: float = 6.0) -> float:
        """Heuristic 'how noisy will this be' score, dominated by 2-qubit gates.

        Trapped-ion 2q error is roughly an order of magnitude above 1q error, so
        2q gates dominate. Multi-qubit gates are weighted as ~6 two-qubit gates
        (rough CCX decomposition). It's a heuristic - tune the weights.
        """
        return w1 * self.n_1q + w2 * self.n_2q + wm * self.n_multi


def count_gates(instructions) -> tuple[int, int, int]:
    """(1-qubit, 2-qubit, 3+-qubit) gate counts of an instruction sequence."""
    n1 = n2 = nm = 0
    for inst in instructions:
        if inst.operation.name in ("barrier", "measure"):
            continue
        k = len(inst.qubits)
        if k == 1:
            n1 += 1
        elif k == 2:
            n2 += 1
        else:
            nm += 1
    return n1, n2, nm


def instructions_cost(instructions, weights: dict | None = None) -> float:
    """Noise cost of a slice of a circuit (same weights as ``noise_cost``)."""
    n1, n2, nm = count_gates(instructions)
    return CircuitStats(0, 0, n1, n2, nm).noise_cost(**cost_kwargs(weights))


def cost_kwargs(weights: dict | None) -> dict:
    """Pick the w1/w2/wm entries out of a (possibly richer) weights dict."""
    return {k: weights[k] for k in ("w1", "w2", "wm") if weights and k in weights}


def circuit_stats(qc: QuantumCircuit) -> CircuitStats:
    return CircuitStats(qc.num_qubits, qc.depth(), *count_gates(qc.data))


# ------------------------------------------------------------ serialisation

def circuit_to_b64(qc: QuantumCircuit) -> str:
    buf = io.BytesIO()
    # oldest QPY format this qiskit can write, so a committed snapshot still
    # loads for teammates and CI runners on an older qiskit
    qpy.dump(qc, buf, version=qpy.QPY_COMPATIBILITY_VERSION)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def circuit_from_b64(s: str) -> QuantumCircuit:
    buf = io.BytesIO(base64.b64decode(s.encode("ascii")))
    return qpy.load(buf)[0]
