"""Example user suite: your circuits, under version control.

    python -m qdebug record --suite examples/suite.py          # on main: save goldens
    python -m qdebug check  --suite examples/suite.py          # on your branch: regress

The only contract is a ``circuits()`` function returning ``{name: QuantumCircuit}``.
Measurements are optional (qdebug adds its own); circuits must be unitary.

To try the workflow without editing code, flip a bug on with an env var:
    QDEBUG_DEMO_BUG=cx_flip python -m qdebug check --suite examples/suite.py -v
    QDEBUG_DEMO_BUG=padding python -m qdebug check --suite examples/suite.py -v
    QDEBUG_DEMO_BUG=wrong_param python -m qdebug check --suite examples/suite.py -v
"""
import os

from qiskit import QuantumCircuit
from qiskit.circuit import Parameter


def ghz(n: int = 4) -> QuantumCircuit:
    bug = os.environ.get("QDEBUG_DEMO_BUG", "")
    qc = QuantumCircuit(n, name="ghz")
    qc.h(0)
    for i in range(n - 1):
        if bug == "cx_flip" and i == 0:
            qc.cx(i + 1, i)            # <- the "bug": control/target swapped
        else:
            qc.cx(i, i + 1)
        if bug == "padding" and i == 0:
            for _ in range(40):        # <- the "regression": redundant gate pairs
                qc.cx(i, i + 1)
                qc.cx(i, i + 1)
    return qc


def swap_test_like() -> QuantumCircuit:
    qc = QuantumCircuit(3, name="swap_test_like")
    qc.h(0)
    qc.ry(0.7, 1)
    qc.cswap(0, 1, 2)
    qc.h(0)
    return qc


def ansatz() -> QuantumCircuit:
    """Parameterized: qdebug records and checks it at 3 seeded parameter points
    (ansatz@p0, ansatz@p1, ansatz@p2), so there is nothing to bind by hand."""
    theta, phi = Parameter("theta"), Parameter("phi")
    qc = QuantumCircuit(2, name="ansatz")
    qc.ry(theta, 0)
    qc.cx(0, 1)
    qc.rz(phi, 1)
    if os.environ.get("QDEBUG_DEMO_BUG", "") == "wrong_param":
        qc.ry(phi, 1)                  # <- the "bug": phi used where theta belongs
    else:
        qc.ry(theta, 1)
    return qc


def circuits() -> dict[str, QuantumCircuit]:
    return {"ghz": ghz(4), "swap_test_like": swap_test_like(), "ansatz": ansatz()}
