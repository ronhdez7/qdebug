"""Built-in golden circuits and deliberately-modified variants for the demo/tests.

Each mutant carries the verdict a *correct* debugger should return, so the demo
doubles as an accuracy test.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import pi

from qiskit import QuantumCircuit

# ----------------------------------------------------------------- builders

def bell(rz: float = 0.0) -> QuantumCircuit:
    qc = QuantumCircuit(2, name="bell")
    qc.h(0)
    qc.cx(0, 1)
    if rz:
        qc.rz(rz, 0)
    return qc


def ghz4(*, flip_first_cx=False, pad_pairs=0, hh=False, final_z=False) -> QuantumCircuit:
    qc = QuantumCircuit(4, name="ghz4")
    qc.h(0)
    qc.cx(1, 0) if flip_first_cx else qc.cx(0, 1)
    for _ in range(pad_pairs):          # logically a no-op, physically noisy
        qc.cx(0, 1)
        qc.cx(0, 1)
    if hh:
        qc.h(3)
        qc.h(3)
    qc.cx(1, 2)
    qc.cx(2, 3)
    if final_z:
        qc.z(0)
    return qc


def qft3(angle_err: float = 0.0) -> QuantumCircuit:
    """QFT on 3 qubits applied to the basis state |101>."""
    n = 3
    qc = QuantumCircuit(n, name="qft3")
    qc.x(0)
    qc.x(2)
    for j in range(n):
        qc.h(j)
        for k in range(j + 1, n):
            # (0, 2): control is q2 = |1>, so the angle really matters. (A bad angle
            # on a gate whose control is |0> is unobservable, and rightly passes.)
            err = angle_err if (j, k) == (0, 2) else 0.0
            qc.cp(pi / 2 ** (k - j) + err, k, j)
    qc.swap(0, 2)
    return qc


def grover3(flip_qubit: int = 1) -> QuantumCircuit:
    """2 Grover iterations over 3 qubits, marking |101>. ``flip_qubit`` is the
    qubit sandwiched by X gates in the oracle (1 is correct for |101>)."""
    qc = QuantumCircuit(3, name="grover3")
    qc.h([0, 1, 2])

    def ccz():
        qc.h(2)
        qc.ccx(0, 1, 2)
        qc.h(2)

    for _ in range(2):
        qc.x(flip_qubit)
        ccz()
        qc.x(flip_qubit)
        qc.h([0, 1, 2])
        qc.x([0, 1, 2])
        ccz()
        qc.x([0, 1, 2])
        qc.h([0, 1, 2])
    return qc


def bv(secret=(1, 1, 0, 1), *, drop=None, reverse=False) -> QuantumCircuit:
    """Bernstein-Vazirani. ``reverse`` re-orders the (commuting) oracle CXs."""
    n = len(secret)
    qc = QuantumCircuit(n + 1, name="bv")
    qc.x(n)
    qc.h(range(n + 1))
    idx = [i for i, s in enumerate(secret) if s and i != drop]
    for i in (reversed(idx) if reverse else idx):
        qc.cx(i, n)
    qc.h(range(n + 1))
    return qc


# ------------------------------------------------------------------- suites

def golden_circuits() -> dict[str, QuantumCircuit]:
    return {"bell": bell(), "ghz4": ghz4(), "qft3": qft3(),
            "grover3": grover3(), "bv": bv()}


@dataclass
class Mutant:
    name: str
    base: str            # which golden snapshot it is checked against
    circuit: QuantumCircuit
    expected: str        # expected Verdict value
    note: str = ""


def mutants(pad_pairs: int = 40) -> list[Mutant]:
    return [
        # ---- identical -> PASS
        Mutant("ghz4_unchanged", "ghz4", ghz4(), "PASS"),
        # ---- harmless edits -> REFACTOR_OK
        Mutant("ghz4_extra_HH", "ghz4", ghz4(hh=True), "REFACTOR_OK",
               "H-H pair inserted: structural change, same behaviour"),
        Mutant("bv_oracle_reordered", "bv", bv(reverse=True), "REFACTOR_OK",
               "commuting CX gates reordered"),
        # ---- logic bugs
        Mutant("ghz4_cx_flipped", "ghz4", ghz4(flip_first_cx=True), "LOGIC_BUG",
               "control/target swapped on the first CX"),
        Mutant("ghz4_stray_Z", "ghz4", ghz4(final_z=True), "LOGIC_BUG",
               "phase bug: invisible in the Z basis, caught by the X probe"),
        Mutant("bv_missing_cx", "bv", bv(drop=3), "LOGIC_BUG",
               "one oracle CX dropped"),
        Mutant("grover3_wrong_oracle", "grover3", grover3(flip_qubit=0), "LOGIC_BUG",
               "oracle marks the wrong basis state"),
        Mutant("qft3_phase_err_0.02", "qft3", qft3(angle_err=0.02), "LOGIC_BUG",
               "controlled-phase angle off by 0.02 rad"),
        Mutant("bell_tiny_phase_drift", "bell", bell(rz=0.05), "LOGIC_BUG",
               "RZ(0.05) drift: far below the shot-noise floor, but exact-sim catches it"),
        # ---- logic fine, noisier
        Mutant("ghz4_cx_padding", "ghz4", ghz4(pad_pairs=pad_pairs), "NOISE_REGRESSION",
               f"{2 * pad_pairs} redundant CX gates (logical identity). On IonQ the cloud "
               "compiler may optimise them away -> REFACTOR_OK, which is also correct."),
    ]
