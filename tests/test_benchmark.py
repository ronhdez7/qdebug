import json

import numpy as np
from qiskit.quantum_info import Operator

from qdebug import benchmark as B
from qdebug.circuits import instruction_signatures
from qdebug.demo_suite import ghz4, grover3


def test_mutants_are_reproducible_distinct_single_edits():
    a = B.generate_mutants(ghz4(), 40, np.random.default_rng(1))
    b = B.generate_mutants(ghz4(), 40, np.random.default_rng(1))
    sigs = [tuple(instruction_signatures(qc)) for _, qc in a]
    assert sigs == [tuple(instruction_signatures(qc)) for _, qc in b]
    assert len(set(sigs)) == 40 and tuple(instruction_signatures(ghz4())) not in sigs
    assert {op for op, _ in a} <= set(B.OPERATORS) and len({op for op, _ in a}) >= 6
    assert all(qc.num_qubits == 4 for _, qc in a)


def test_padding_adds_gates_without_changing_the_unitary():
    for golden in (ghz4(), grover3()):
        padded = B.pad(golden, 3)
        assert len(padded.data) == len(golden.data) + 6
        assert Operator(padded).equiv(Operator(golden))


def test_small_benchmark_run_and_report(backend, tmp_path):
    results = B.run(backend, per_circuit=8, seed=5, pad_seeds=1, shots=300)
    summary = B.write(results, tmp_path)
    assert summary["n_mutants"] == summary["n_changed"] + summary["n_equivalent"] == 48
    # ground truth is the exact state comparison, so qdebug must agree with it
    assert summary["detection"]["all"]["qdebug"] == 1.0
    assert summary["equivalent"]["qdebug_logic_bug"] == 0.0
    assert set(summary["padding"]) == {str(k) for k in B.PAD_LEVELS}
    assert all(r["qdebug_logic"] == 0.0 for r in summary["padding"].values())
    md = (tmp_path / "benchmark.md").read_text()
    assert "## Logic changes caught" in md and "## By circuit" in md
    assert json.loads((tmp_path / "summary.json").read_text())["config"]["seed"] == 5
    assert (tmp_path / "benchmark.png").stat().st_size > 10_000
