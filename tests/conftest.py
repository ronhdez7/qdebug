import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from qdebug import LocalBackend, record  # noqa: E402
from qdebug.demo_suite import golden_circuits  # noqa: E402


@pytest.fixture(scope="session")
def backend():
    return LocalBackend()


@pytest.fixture(scope="session")
def snapshots(backend):
    return {n: record(n, qc, backend, shots=1000, n_seeds=4)
            for n, qc in golden_circuits().items()}
