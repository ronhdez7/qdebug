"""Exact equivalence checks that back up the probe bases.

The probe bases (all-Z, all-X, all-Y) only see a few slices of the output
state, so two different states can agree on every one of them. When the circuit
is small enough to simulate locally we therefore also compare:

* the **output statevectors** from ``|0...0>`` (up to global phase). This is
  everything any measurement of the circuit could reveal, so it settles
  whether an edit changed the circuit's behaviour as a program.
* the **unitaries** (up to global phase). Two circuits can prepare the same
  state from ``|0...0>`` and still differ on other inputs, which matters when
  the circuit is reused as a subroutine.

Both return a distance in [0, 1] that is 0 for equivalent circuits and bounds
the TVD of any measurement from above (for states it is the trace distance).
"""
from __future__ import annotations

import numpy as np
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator, Statevector

MAX_STATE_QUBITS = 20     # 2^20 amplitudes: instant
MAX_UNITARY_QUBITS = 10   # 4^10 matrix entries: still well under a second
EXACT_TOL = 1e-9          # local linear algebra is good to ~1e-15


def _phase_free_distance(a: np.ndarray, b: np.ndarray) -> float:
    """sqrt(1 - |<a|b>|^2) for unit vectors, computed without cancellation.

    The textbook formula loses half the digits near zero (1 - 0.9999...), which
    would put numerical noise above ``EXACT_TOL``. Aligning the global phase
    first and taking the norm of the difference keeps full precision.
    """
    ov = np.vdot(b, a)
    mag = min(abs(ov), 1.0)
    phase = ov / abs(ov) if abs(ov) > 1e-12 else 1.0
    d = float(np.linalg.norm(a - phase * b))
    return min(1.0, d * float(np.sqrt((1.0 + mag) / 2.0)))


def state_distance(golden: QuantumCircuit, modified: QuantumCircuit) -> float:
    """Trace distance between the two circuits' output states from |0...0>."""
    return _phase_free_distance(Statevector(golden).data, Statevector(modified).data)


def unitary_distance(golden: QuantumCircuit, modified: QuantumCircuit) -> float:
    """Phase-free distance between the two circuits' unitaries."""
    a, b = Operator(golden).data, Operator(modified).data
    scale = np.sqrt(a.shape[0])
    return _phase_free_distance(a.ravel() / scale, b.ravel() / scale)
