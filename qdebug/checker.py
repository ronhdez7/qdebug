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
from . import equiv as E
from .backends import Backend, exact_probs, MAX_LOCAL_QUBITS
from .diff import Hunk, _instrs, build_hybrid, compress, diff_circuits
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
    # What "behaviour unchanged" means for an edited circuit:
    #   "probes"  - same distributions in the snapshot's probe bases only
    #   "state"   - same output state from |0...0> (exact, when small enough)
    #   "unitary" - same unitary, i.e. also on every other input state
    equivalence: str = "state"


@dataclass
class HunkBlame:
    hunk: Hunk
    effect: float   # max-over-bases TVD when ONLY this hunk is applied to golden
    culprit: bool


@dataclass
class NoiseBlame:
    hunk: Hunk
    cost_delta: float   # noise cost the hunk adds (negative: it removes gates)
    share: float        # fraction of all added cost this hunk accounts for


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
    noise_blame: list[NoiseBlame] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # strongest comparison that backed the verdict: probes | statevector | unitary
    equivalence: str = "probes"
    state_dist: float | None = None     # trace distance of output states
    unitary_dist: float | None = None   # phase-free distance of the unitaries

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
    if cfg.equivalence not in ("probes", "state", "unitary"):
        raise ValueError(f"unknown equivalence level {cfg.equivalence!r}")
    tol = cfg.logic_tol if cfg.logic_tol is not None else backend.default_logic_tol
    shots = cfg.shots or snap.noise.shots
    bases = tuple(snap.bases)
    warnings: list[str] = []

    golden = snap.circuit()
    mod = C.strip_measurements(qc)
    mod.name = name
    hunks = diff_circuits(golden, mod)
    g_stats, m_stats = C.circuit_stats(golden), C.circuit_stats(mod)
    weights = snap.noise.cost_weights
    cost_g = g_stats.noise_cost(**C.cost_kwargs(weights))
    cost_m = m_stats.noise_cost(**C.cost_kwargs(weights))

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
            and mod.num_qubits <= MAX_LOCAL_QUBITS \
            and hasattr(backend, "remote_ideal_probs"):
        local = [exact_probs(c) for c in probe_circs]
        remote = backend.remote_ideal_probs(probe_circs)
        gap = max(tvd(local_probs, remote_probs)
                  for local_probs, remote_probs in zip(local, remote))
        support = max(len(set(local_probs) | set(remote_probs))
                      for local_probs, remote_probs in zip(local, remote))
        rounding_bound = backend.remote_probability_resolution * support / 4
        if gap > rounding_bound:
            warnings.append(
                f"IonQ ideal simulator differs from local exact simulation by "
                f"TVD={gap:.3g}, above the {rounding_bound:.3g} bound from "
                "three-decimal API rounding: check bit ordering / compilation.")

    # ---- 1b. exact comparison: catches what the probe bases cannot see ---
    method, state_dist, unitary_dist = "probes", None, None
    if hunks and cfg.equivalence != "probes":
        if mod.num_qubits <= E.MAX_STATE_QUBITS:
            state_dist, method = E.state_distance(golden, mod), "statevector"
        if mod.num_qubits <= E.MAX_UNITARY_QUBITS:
            unitary_dist, method = E.unitary_distance(golden, mod), "unitary"
    state_bug = state_dist is not None and state_dist > E.EXACT_TOL
    unitary_only = (not state_bug and logic_dev <= tol
                    and unitary_dist is not None and unitary_dist > E.EXACT_TOL)

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
                  naive_fails=naive_dev > cfg.naive_threshold,
                  equivalence=method, state_dist=state_dist,
                  unitary_dist=unitary_dist)

    # ---- 3. verdict ------------------------------------------------------
    hidden_state_bug = state_bug and logic_dev <= tol
    unitary_bug = unitary_only and cfg.equivalence == "unitary"
    if logic_dev > tol or hidden_state_bug or unitary_bug:
        blame = _blame(golden, mod, hunks, snap, backend, bases, tol, cfg) \
            if cfg.blame else []
        if hidden_state_bug:
            common["logic_dev"] = state_dist
            reason = (f"the output state moved (trace distance {state_dist:.4g}) "
                      f"although every probed basis ({', '.join(bases)}) shows an "
                      "identical distribution - only correlations between "
                      "different bases reveal it. Exact statevector comparison "
                      "caught it; this is a logic change, not noise.")
        elif unitary_bug:
            common["logic_dev"] = unitary_dist
            reason = (f"output from |0...0> is unchanged, but the unitary differs "
                      f"(distance {unitary_dist:.4g}): other input states give a "
                      "different output. Flagged because equivalence='unitary'.")
        else:
            worst = max(per_basis, key=per_basis.get)
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

    if unitary_only:
        warnings.append(
            f"equivalent only from |0...0>: the unitaries differ (distance "
            f"{unitary_dist:.3g}), so this edit is not a drop-in replacement if "
            "the circuit is reused on other input states. Use "
            "equivalence='unitary' to treat that as a LOGIC_BUG.")
    elif hunks and method == "probes" and cfg.equivalence != "probes":
        warnings.append(
            f"{mod.num_qubits} qubits is too wide for exact comparison "
            f"(limit {E.MAX_STATE_QUBITS}); 'behaviour identical' rests on the "
            f"probe bases only ({', '.join(bases)}).")

    if noise_dev > budget:
        explained = noise_dev <= budget_scaled
        reason = (f"logic unchanged (ideal TVD={logic_dev:.2g}) but noise-induced "
                  f"deviation {noise_dev:.3f} exceeds the baseline budget "
                  f"{budget:.3f}. Cost x{ratio:.2f} "
                  f"(2q gates {g_stats.n_2q} -> {m_stats.n_2q}); "
                  + ("explained by the extra gates." if hunks and explained else
                     "not explained by gate count." if hunks else
                     "no circuit change - backend noise model drift?"))
        nblame = _noise_blame(golden, mod, hunks, weights)
        costly = [b for b in nblame if b.cost_delta > 0][:3]
        if costly:
            reason += " Costliest edit(s): " + "; ".join(
                f"hunk {b.hunk.index} ({_one_line(b.hunk)}, +{b.cost_delta:.3g} cost, "
                f"{b.share:.0%} of the increase)" for b in costly)
        return make(Verdict.NOISE_REGRESSION, reason, noise_blame=nblame, **common)

    if hunks:
        return make(Verdict.REFACTOR_OK,
                    f"{len(hunks)} edit(s), behaviour identical (ideal TVD="
                    f"{logic_dev:.2g}), noise within baseline.", **common)
    return make(Verdict.PASS, "identical circuit, behaviour and noise level.", **common)


def _one_line(h: Hunk) -> str:
    rm = ", ".join(compress(h.removed)) or "-"
    ad = ", ".join(compress(h.added)) or "-"
    return f"[{rm}] -> [{ad}]"


def _noise_blame(golden, mod, hunks, weights) -> list[NoiseBlame]:
    """Rank edits by how much noise cost they add (estimate from gate counts).

    Unlike the logic blame this runs nothing: cost is additive over gates, so a
    hunk's contribution is the cost of what it adds minus what it removes.
    """
    gi, mi = _instrs(golden), _instrs(mod)
    deltas = [(h, C.instructions_cost(mi[h.mod_range[0]:h.mod_range[1]], weights)
               - C.instructions_cost(gi[h.golden_range[0]:h.golden_range[1]], weights))
              for h in hunks]
    added = sum(d for _, d in deltas if d > 0)
    out = [NoiseBlame(h, d, d / added if d > 0 and added > 0 else 0.0) for h, d in deltas]
    return sorted(out, key=lambda b: -b.cost_delta)


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
    # same exact fallback as check(): a hunk the probes cannot see may still
    # move the state (or, in unitary mode, only the unitary)
    for h in hunks:
        if effects[h.index] > tol or cfg.equivalence == "probes":
            continue
        hyb = build_hybrid(golden, mod, hunks, {h.index})
        if mod.num_qubits <= E.MAX_STATE_QUBITS:
            effects[h.index] = max(effects[h.index], E.state_distance(golden, hyb))
        if cfg.equivalence == "unitary" and effects[h.index] <= tol \
                and mod.num_qubits <= E.MAX_UNITARY_QUBITS:
            effects[h.index] = E.unitary_distance(golden, hyb)
    return [HunkBlame(h, effects[h.index], effects[h.index] > tol) for h in hunks]
