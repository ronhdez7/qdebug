"""Measure how much noise one gate costs on a backend.

The noise budget scales the golden circuit's hardware noise by a *cost ratio*
(``CircuitStats.noise_cost``). Its default weights are a guess: a 1-qubit gate
counts 0.05 of a 2-qubit gate. This module replaces the guess with a
measurement on the backend actually in use.

Method: two 2-qubit calibration circuits that are logically the identity, one
made of X-X pairs and one of CX-CX pairs. Their ideal output is exactly
``00``, so every other outcome is noise and the measured TVD needs no
shot-noise correction. Dividing by the gate count (after undoing saturation
towards the fully mixed state) gives a per-gate rate for each gate type.

Caveat: a backend that optimises circuits before running them (IonQ's cloud
compiler on the default ``qis`` gateset) cancels these pairs and reports no
noise. Calibrate with native gates there (``--native``), or keep the defaults.
"""
from __future__ import annotations

import math

from qiskit import QuantumCircuit

from .backends import Backend
from .circuits import with_probe
from .stats import counts_to_probs, tvd

DEFAULT_WEIGHTS = {"w1": 0.05, "w2": 1.0, "wm": 6.0}
_SATURATION = 0.75      # TVD from |00> once two qubits are fully mixed
_MIN_SIGNAL = 0.01      # below this the measurement is indistinguishable from none


def _identity_circuit(kind: str, pairs: int) -> tuple[QuantumCircuit, int]:
    qc = QuantumCircuit(2, name=f"calib_{kind}")
    for _ in range(pairs):
        if kind == "1q":
            qc.x(0)
            qc.x(1)
            qc.x(0)
            qc.x(1)
        else:
            qc.cx(0, 1)
            qc.cx(0, 1)
    return qc, (4 if kind == "1q" else 2) * pairs


def _per_gate_rate(deviation: float, n_gates: int) -> float:
    d = min(max(deviation, 0.0), _SATURATION * 0.98)
    return -math.log(1.0 - d / _SATURATION) / n_gates


def calibrate_cost_weights(backend: Backend, *, shots: int = 4000, seed: int = 4242,
                           pairs_1q: int = 100, pairs_2q: int = 10) -> dict | None:
    """Return measured ``{"w1", "w2", "wm", "rate_1q", "rate_2q"}`` or ``None``.

    ``None`` means the 2-qubit calibration circuit showed no measurable noise
    (noiseless backend, or the backend cancelled the redundant gates), so there
    is nothing to scale by and the caller should keep the default weights.
    """
    c1, n1 = _identity_circuit("1q", pairs_1q)
    c2, n2 = _identity_circuit("2q", pairs_2q)
    probes = [with_probe(c1, "Z"), with_probe(c2, "Z")]
    counts = backend.noisy_counts(probes, shots, seed)
    dev1, dev2 = (tvd(counts_to_probs(c), {"00": 1.0}) for c in counts)
    if dev2 < _MIN_SIGNAL:
        return None
    r1, r2 = _per_gate_rate(dev1, n1), _per_gate_rate(dev2, n2)
    return {"w1": r1 / r2, "w2": 1.0, "wm": DEFAULT_WEIGHTS["wm"],
            "rate_1q": r1, "rate_2q": r2}
