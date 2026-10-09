"""qdebug - quantum circuit debugger + regression tester.

Separates *logic bugs* (the circuit's ideal behaviour changed) from
*noise-induced deviations* (same logic, more/less noisy) using exact ideal
simulation plus a noise-model simulation, on Qiskit with the IonQ simulator.
"""
from .backends import Backend, IonQBackend, LocalBackend, QiskitBackendAdapter, make_backend
from .checker import CheckConfig, Report, Verdict, check
from .snapshot import Snapshot, load, record, save

__all__ = [
    "Backend", "IonQBackend", "LocalBackend", "QiskitBackendAdapter", "make_backend",
    "CheckConfig", "Report", "Verdict", "check",
    "Snapshot", "load", "record", "save",
]
__version__ = "0.1.0"
