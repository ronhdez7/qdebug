"""Core regression check: classify a circuit change as PASS / REFACTOR_OK /
LOGIC_BUG / NOISE_REGRESSION.

Why this separates logic from noise
-----------------------------------
A simulator gives us two views of the *same modified circuit*:

1. **Ideal view** - exact output probabilities (no shot noise, no hardware
   noise). Comparing this to the golden ideal distribution measures *only*
   logic changes. Because it's exact, bugs far smaller than any noise floor
   (a 0.01 rad rotation error) are still caught.
2. **Noisy view** - sampled counts under a hardware noise model. Comparing
   this to the *modified circuit's own* ideal distribution measures *only*
   noise (+ shot sampling), which we judge against a baseline recorded from the
   golden circuit and scaled by the circuit's cost.

A naive test (noisy counts vs golden ideal, fixed threshold) mixes the two and
therefore both false-alarms on noise and misses small bugs. We report it too so
the difference is visible.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

import numpy as np

from . import circuits as C
from .backends import Backend, exact_probs, MAX_LOCAL_QUBITS
from .diff import Hunk, build_hybrid, compress, diff_circuits
from .snapshot import Snapshot, noise_score
from .stats import counts_to_probs, tvd


class Verdict(str, Enum):
    PASS = "PASS"                        # same structure, same behaviour
    REFACTOR_OK = "REFACTOR_OK"          # edited, but behaviour identical
    LOGIC_BUG = "LOGIC_BUG"              # ideal output changed
    NOISE_REGRESSION = "NOISE_REGRESSION"  # logic fine, noisier than baseline


@dataclass
class CheckConfig:
    shots: int | None = None        # default: the snapshot's shot count
    seed: int = 7777                # noisy-run seed (reproducible on IonQ + Aer)
    logic_tol: float | None = None  # default: backend.default_logic_tol
    noise_margin: float = 1.25      # multiplicative slack on the noise budget
    naive_threshold: float = 0.05   # for the "what a naive test says" column
    blame: bool = True              # hunk-level culprit search on LOGIC_BUG
    max_hunks: int = 12
    cross_check_local: bool = True  # compare IonQ ideal vs local exact


@dataclass
class HunkBlame:
    hunk: Hunk
    effect: float   # max-over-bases TVD when ONLY this hunk is applied to golden
    culprit: bool


@dataclass
class Report:
    name: str
    verdict: Verdict
    reason: str
    backend: dict
    logic_dev: float
    logic_tol: float
    per_basis_logic_dev: dict[str, float]
    noise_dev: float | None
    noise_budget: float | None          # budget for a golden-sized circuit
    noise_budget_scaled: float | None   # budget scaled by cost ratio
    naive_dev: float | None
    naive_fails: bool | None
    cost_golden: float
    cost_modified: float
    stats_golden: dict
    stats_modified: dict
    hunks: list[Hunk] = field(default_factory=list)
    blame: list[HunkBlame] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


def noise_budget(snap: Snapshot, scale: float, margin: float) -> float:
    """Allowed noise-induced TVD for a circuit ``scale`` x as costly as golden.

    Sampling noise does not grow with circuit size, hardware noise roughly does,
    so only the hardware part (and its spread) is scaled.
    """
    nb = snap.noise
    gate_part = max(nb.mean - nb.shot_mean, 0.0)
    return margin * (nb.shot_floor + (gate_part + 3.0 * nb.std) * scale)


def check(name: str, qc, snap: Snapshot, backend: Backend,
          cfg: CheckConfig | None = None) -> Report:
    cfg = cfg or CheckConfig()
    tol = cfg.logic_tol if cfg.logic_tol is not None else backend.default_logic_tol
    shots = cfg.shots or snap.noise.shots
    bases = tuple(snap.bases)
    warnings: list[str] = []

    golden = snap.circuit()
    mod = C.strip_measurements(qc)
    mod.name = name
    hunks = diff_circuits(golden, mod)
    g_stats, m_stats = C.circuit_stats(golden), C.circuit_stats(mod)
    cost_g, cost_m = g_stats.noise_cost(), m_stats.noise_cost()

    def make(verdict, reason, **kw) -> Report:
        base = dict(
            name=name, verdict=verdict, reason=reason, backend=backend.describe(),
            logic_dev=0.0, logic_tol=tol, per_basis_logic_dev={}, noise_dev=None,
            noise_budget=None, noise_budget_scaled=None, naive_dev=None,
            naive_fails=None, cost_golden=cost_g, cost_modified=cost_m,
            stats_golden=asdict(g_stats), stats_modified=asdict(m_stats),
            hunks=hunks, warnings=warnings)
        base.update(kw)
        return Report(**base)

    # width change is a logic change by definition (different output space)
    if mod.num_qubits != golden.num_qubits:
        return make(Verdict.LOGIC_BUG,
                    f"qubit count changed {golden.num_qubits} -> {mod.num_qubits}",
                    logic_dev=1.0)

    # ---- 1. logic: exact ideal distributions, modified vs golden --------
    probe_circs = C.probes(mod, bases)
    ideal_mod = backend.ideal_probs(probe_circs)
    per_basis = {b: tvd(pm, snap.ideal[b]) for b, pm in zip(bases, ideal_mod)}
    logic_dev = max(per_basis.values())

    if cfg.cross_check_local and backend.name.startswith("ionq") \
            and mod.num_qubits <= MAX_LOCAL_QUBITS:
        local = [exact_probs(c) for c in probe_circs]
        gap = max(tvd(a, b) for a, b in zip(local, ideal_mod))
        if gap > 1e-3:
            warnings.append(
                f"IonQ ideal simulator differs from local exact simulation by "
                f"TVD={gap:.3g}: check bit ordering / compilation before trusting "
                f"verdicts.")

    # ---- 2. noise: sampled counts vs the circuit's OWN ideal ------------
    counts = backend.noisy_counts(probe_circs, shots, cfg.seed)
    noise_dev = noise_score(counts, ideal_mod)
    naive_dev = float(np.mean([tvd(counts_to_probs(c), snap.ideal[b])
                               for c, b in zip(counts, bases)]))
    ratio = cost_m / cost_g if cost_g > 0 else 1.0
    budget = noise_budget(snap, 1.0, cfg.noise_margin)
    budget_scaled = noise_budget(snap, max(1.0, ratio), cfg.noise_margin)

    common = dict(logic_dev=logic_dev, per_basis_logic_dev=per_basis,
                  noise_dev=noise_dev, noise_budget=budget,
                  noise_budget_scaled=budget_scaled, naive_dev=naive_dev,
                  naive_fails=naive_dev > cfg.naive_threshold)

    # ---- 3. verdict ------------------------------------------------------
    if logic_dev > tol:
        worst = max(per_basis, key=per_basis.get)
        blame = _blame(golden, mod, hunks, snap, backend, bases, tol, cfg) \
            if cfg.blame else []
        reason = (f"ideal (noise-free) output moved by TVD={logic_dev:.4g} in the "
                  f"{worst} basis (tol {tol:g}) - this is a logic change, not noise.")
        culprits = [b for b in blame if b.culprit]
        if culprits:
            reason += " Culprit edit(s): " + "; ".join(
                f"hunk {b.hunk.index} ({_one_line(b.hunk)}, effect {b.effect:.3g})"
                for b in sorted(culprits, key=lambda b: -b.effect))
        elif blame:
            reason += " No single edit explains it - the edits interact."
        return make(Verdict.LOGIC_BUG, reason, blame=blame, **common)

    if noise_dev > budget:
        explained = noise_dev <= budget_scaled
        reason = (f"logic unchanged (ideal TVD={logic_dev:.2g}) but noise-induced "
                  f"deviation {noise_dev:.3f} exceeds the baseline budget "
                  f"{budget:.3f}. Cost x{ratio:.2f} "
                  f"(2q gates {g_stats.n_2q} -> {m_stats.n_2q}); "
                  + ("explained by the extra gates." if hunks and explained else
                     "not explained by gate count." if hunks else
                     "no circuit change - backend noise model drift?"))
        return make(Verdict.NOISE_REGRESSION, reason, **common)

    if hunks:
        return make(Verdict.REFACTOR_OK,
                    f"{len(hunks)} edit(s), behaviour identical (ideal TVD="
                    f"{logic_dev:.2g}), noise within baseline.", **common)
    return make(Verdict.PASS, "identical circuit, behaviour and noise level.", **common)


def _one_line(h: Hunk) -> str:
    rm = ", ".join(compress(h.removed)) or "-"
    ad = ", ".join(compress(h.added)) or "-"
    return f"[{rm}] -> [{ad}]"


def _blame(golden, mod, hunks, snap, backend, bases, tol, cfg) -> list[HunkBlame]:
    if not hunks or len(hunks) > cfg.max_hunks:
        return []
    batch, owner = [], []
    for h in hunks:
        hyb = build_hybrid(golden, mod, hunks, {h.index})
        hyb.name = f"{mod.name}__hunk{h.index}"
        for pc in C.probes(hyb, bases):
            batch.append(pc)
            owner.append(h.index)
    ideals = backend.ideal_probs(batch)
    effects = {h.index: 0.0 for h in hunks}
    for k, (hid, p) in enumerate(zip(owner, ideals)):
        b = bases[k % len(bases)]
        effects[hid] = max(effects[hid], tvd(p, snap.ideal[b]))
    return [HunkBlame(h, effects[h.index], effects[h.index] > tol) for h in hunks]
