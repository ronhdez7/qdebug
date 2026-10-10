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
    """Append a basis change + measure_all so the output reveals ``basis``."""
    basis = basis.upper()
    if basis not in PROBE_BASES:
        raise ValueError(f"unknown basis {basis!r}; choose from {PROBE_BASES}")
    probe = qc.copy()
    probe.name = f"{qc.name}__{basis}"
    if basis == "X":
        for q in range(probe.num_qubits):
            probe.h(q)
    elif basis == "Y":
        for q in range(probe.num_qubits):
            probe.sdg(q)
            probe.h(q)
    probe.measure_all()
    return probe


def probes(qc: QuantumCircuit, bases: tuple[str, ...]) -> list[QuantumCircuit]:
    return [with_probe(qc, b) for b in bases]


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


def circuit_stats(qc: QuantumCircuit) -> CircuitStats:
    n1 = n2 = nm = 0
    for inst in qc.data:
        op = inst.operation
        if op.name in ("barrier", "measure"):
            continue
        k = len(inst.qubits)
        if k == 1:
            n1 += 1
        elif k == 2:
            n2 += 1
        else:
            nm += 1
    return CircuitStats(qc.num_qubits, qc.depth(), n1, n2, nm)


# ------------------------------------------------------------ serialisation

def circuit_to_b64(qc: QuantumCircuit) -> str:
    buf = io.BytesIO()
    qpy.dump(qc, buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def circuit_from_b64(s: str) -> QuantumCircuit:
    buf = io.BytesIO(base64.b64decode(s.encode("ascii")))
    return qpy.load(buf)[0]
