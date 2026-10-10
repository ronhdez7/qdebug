"""Golden snapshots: record ideal behaviour + a noise baseline for a circuit."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from qiskit import QuantumCircuit

from . import circuits as C
from .backends import Backend
from .equiv import MAX_STATE_QUBITS
from .stats import Dist, mean_std, sample_counts, shot_noise_floor, tvd, counts_to_probs

import numpy as np

SNAPSHOT_VERSION = 2   # 2: bases may be per-qubit strings (e.g. "XZY")


@dataclass
class NoiseBaseline:
    shots: int
    seeds: list[int]
    scores: list[float]          # per-seed mean TVD(noisy, ideal) over bases
    mean: float
    std: float
    shot_mean: float             # expected TVD from sampling alone
    shot_floor: float            # 99th percentile of sampling-only TVD
    noise_model: dict = field(default_factory=dict)


@dataclass
class Snapshot:
    name: str
    circuit_b64: str
    signature: list[str]
    stats: dict
    bases: list[str]
    ideal: dict[str, Dist]
    noise: NoiseBaseline
    created: str
    version: int = SNAPSHOT_VERSION

    def circuit(self) -> QuantumCircuit:
        return C.circuit_from_b64(self.circuit_b64)

    # -- io
    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2, sort_keys=True)

    @staticmethod
    def from_dict(d: dict) -> "Snapshot":
        d = dict(d)
        version = d.get("version", 1)
        if version > SNAPSHOT_VERSION:
            raise ValueError(
                f"snapshot {d.get('name')!r} has format version {version}, newer "
                f"than this qdebug understands ({SNAPSHOT_VERSION}); upgrade qdebug "
                "or re-record the snapshot.")
        d["noise"] = NoiseBaseline(**d["noise"])
        return Snapshot(**d)


def noise_score(counts_list: list[dict], ideal_list: list[Dist]) -> float:
    """Mean over probe bases of TVD(sampled counts, exact ideal)."""
    return float(np.mean([tvd(counts_to_probs(c), p)
                          for c, p in zip(counts_list, ideal_list)]))


def record(name: str, qc: QuantumCircuit, backend: Backend, *,
           bases: tuple[str, ...] = C.PROBE_BASES, shots: int = 1000,
           n_seeds: int = 4, base_seed: int = 1000,
           random_bases: int | None = None) -> Snapshot:
    """Run the golden circuit once ideally and ``n_seeds`` times noisily.

    ``random_bases`` adds that many seeded per-qubit (mixed) probe bases on top
    of ``bases``. By default none are added while the circuit is small enough
    for ``check`` to compare statevectors exactly, and 4 are added above that,
    where the probes are the only evidence.
    """
    unitary = C.strip_measurements(qc)
    unitary.name = name
    if random_bases is None:
        random_bases = 0 if unitary.num_qubits <= MAX_STATE_QUBITS else 4
    bases = tuple(dict.fromkeys(
        tuple(bases) + tuple(C.random_bases(unitary.num_qubits, random_bases, base_seed))))
    probe_circs = C.probes(unitary, bases)

    ideal_list = backend.ideal_probs(probe_circs)
    seeds = [base_seed + i for i in range(n_seeds)]
    scores = []
    for s in seeds:
        counts = backend.noisy_counts(probe_circs, shots, s)
        scores.append(noise_score(counts, ideal_list))
    mean, std = mean_std(scores)

    rng = np.random.default_rng(99)
    shot_mean = float(np.mean([
        np.mean([tvd(sample_counts(p, shots, rng), p) for p in ideal_list])
        for _ in range(60)]))
    shot_floor = float(np.mean([shot_noise_floor(p, shots) for p in ideal_list]))

    return Snapshot(
        name=name,
        circuit_b64=C.circuit_to_b64(unitary),
        signature=C.instruction_signatures(unitary),
        stats=asdict(C.circuit_stats(unitary)),
        bases=list(bases),
        ideal={b: p for b, p in zip(bases, ideal_list)},
        noise=NoiseBaseline(shots=shots, seeds=seeds, scores=scores, mean=mean,
                            std=std, shot_mean=shot_mean, shot_floor=shot_floor,
                            noise_model=backend.describe()),
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def save(snap: Snapshot, directory: str | Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snap.name}.json"
    path.write_text(snap.to_json())
    return path


def load(name: str, directory: str | Path) -> Snapshot:
    path = Path(directory) / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no snapshot for {name!r} in {directory} - run `qdebug record` first")
    return Snapshot.from_dict(json.loads(path.read_text()))
