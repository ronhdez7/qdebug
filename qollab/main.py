"""Qollab entry point for a compact qdebug regression demonstration."""

from qiskit import transpile

from qdebug.backends import QiskitBackendAdapter
from qdebug.backends import normalize_key
from qdebug.checker import CheckConfig, Verdict, check
from qdebug.demo_suite import ghz4
from qdebug.report import format_report
from qdebug.snapshot import record


class QollabBackend(QiskitBackendAdapter):
    """Adapt Qollab's injected backend, submitting one circuit per run."""

    def noisy_counts(self, circuits, shots, seed):
        results = []
        for circuit in circuits:
            compiled = transpile(
                circuit,
                self.backend,
                optimization_level=0,
                seed_transpiler=seed,
                num_processes=1,
            )
            try:
                job = self.backend.run(
                    compiled, shots=shots, seed_simulator=seed
                )
            except TypeError:
                job = self.backend.run(compiled, shots=shots)
            counts = job.result().get_counts()
            results.append({normalize_key(key): count for key, count in counts.items()})
        return results


def main(shots: int = 256, seed: int = 37):
    """Run qdebug against Qollab's selected backend (injected as ``backend``)."""
    adapter = QollabBackend(backend, name="qollab")
    golden = ghz4()
    snapshot = record(
        "ghz4",
        golden,
        adapter,
        bases=("Z",),
        shots=shots,
        n_seeds=1,
        base_seed=seed,
    )
    cases = (
        ("Unchanged circuit", golden, Verdict.PASS),
        (
            "Control/target logic bug",
            ghz4(flip_first_cx=True),
            Verdict.LOGIC_BUG,
        ),
    )

    print(f"qdebug on Qollab backend: {adapter.name}")
    for label, circuit, expected in cases:
        report = check(
            label,
            circuit,
            snapshot,
            adapter,
            CheckConfig(seed=seed, equivalence="state"),
        )
        print(f"\n{label}: expected {expected.value}, got {report.verdict.value}")
        print(format_report(report, verbose=True))
        if report.verdict is not expected:
            raise RuntimeError(
                f"{label}: expected {expected.value}, got {report.verdict.value}"
            )
    print("\nBoth qdebug verdict checks matched.")