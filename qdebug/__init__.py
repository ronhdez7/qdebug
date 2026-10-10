"""qdebug - quantum circuit debugger + regression tester.

Separates *logic bugs* (the circuit's ideal behaviour changed) from
*noise-induced deviations* (same logic, more/less noisy) using exact ideal
simulation plus a noise-model simulation, on Qiskit with the IonQ simulator.
"""
from .backends import Backend, IonQBackend, LocalBackend, QiskitBackendAdapter, make_backend
from .calibrate import calibrate_cost_weights
from .checker import CheckConfig, Report, Verdict, check
from .circuits import expand_parameters, parameter_points
from .snapshot import Snapshot, load, record, save

__all__ = [
    "Backend", "IonQBackend", "LocalBackend", "QiskitBackendAdapter", "make_backend",
    "CheckConfig", "Report", "Verdict", "check",
    "Snapshot", "load", "record", "save",
    "calibrate_cost_weights", "expand_parameters", "parameter_points",
]
__version__ = "0.1.0"
