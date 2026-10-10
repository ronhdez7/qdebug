"""Mutation benchmark: how often does each kind of test get it right?

Generates hundreds of random edits of the demo circuits and asks four tests
whether the circuit's logic changed:

* **naive shot test** - noisy counts vs the golden distribution, fixed threshold
* **exact, Z basis only** - exact distributions, standard measurement
* **exact, Z/X/Y probes** - exact distributions in three bases
* **qdebug** - the full check (probes + exact state comparison + noise budget)

Ground truth is mathematical, not an opinion of any of the four: an edit
*changed the logic* iff the output state from ``|0...0>`` moved (trace distance
above 1e-9). So edits that look like bugs but are not (a Z on ``|0>``) count as
equivalent, as they should.

Read the qdebug column with that in mind: for circuits small enough for the
exact state comparison (all of these), catching every logic change is true by
construction. What the benchmark measures is (1) how much the other three miss
or false-alarm, and (2) qdebug's *noise* verdicts, which are statistical and
not guaranteed by anything.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit.library import (CXGate, CZGate, HGate, RZGate, SdgGate, SGate,
                                    TdgGate, TGate, XGate, YGate, ZGate)

from . import equiv as E
from .backends import Backend
from .checker import CheckConfig, Verdict, check
from .circuits import instruction_signatures
from .demo_suite import golden_circuits
from .snapshot import record

EPSILONS = (1e-3, 1e-2, 1e-1)
SWAPS = {"h": XGate, "x": ZGate, "z": XGate, "s": SdgGate, "sdg": SGate,
         "cx": CZGate, "cz": CXGate, "t": TdgGate, "tdg": TGate}
# (label, upper bound on trace distance); the naive test's blind spot is the first
BUCKETS = (("< 0.02", 0.02), ("0.02 to 0.2", 0.2), ("> 0.2", float("inf")))
NAIVE_THRESHOLDS = (0.02, 0.05, 0.1)
PAD_LEVELS = (0, 1, 2, 4, 8, 16, 32)

Instr = tuple  # (operation, [qubit indices])


def _instrs(qc: QuantumCircuit) -> list[Instr]:
    return [(i.operation, [qc.find_bit(q).index for q in i.qubits]) for i in qc.data]


def _build(n: int, name: str, instrs: list[Instr]) -> QuantumCircuit:
    qc = QuantumCircuit(n, name=name)
    for op, qs in instrs:
        qc.append(op, qs)
    return qc


# ------------------------------------------------------------ mutation operators
# Each takes (instrs, n_qubits, rng) and returns a new list, or None if it does
# not apply to this circuit.

def _delete(ins, n, rng):
    i = int(rng.integers(len(ins)))
    return ins[:i] + ins[i + 1:]


def _flip(ins, n, rng):
    idx = [i for i, (_, qs) in enumerate(ins) if len(qs) == 2]
    if not idx:
        return None
    i = int(rng.choice(idx))
    return ins[:i] + [(ins[i][0], ins[i][1][::-1])] + ins[i + 1:]


def _retarget(ins, n, rng):
    idx = [i for i, (_, qs) in enumerate(ins) if len(qs) < n]
    if not idx:
        return None
    i = int(rng.choice(idx))
    op, qs = ins[i]
    free = [q for q in range(n) if q not in qs]
    qs = list(qs)
    qs[int(rng.integers(len(qs)))] = int(rng.choice(free))
    return ins[:i] + [(op, qs)] + ins[i + 1:]


def _replace(ins, n, rng):
    idx = [i for i, (op, _) in enumerate(ins) if op.name in SWAPS]
    if not idx:
        return None
    i = int(rng.choice(idx))
    return ins[:i] + [(SWAPS[ins[i][0].name](), ins[i][1])] + ins[i + 1:]


def _angle(ins, n, rng):
    idx = [i for i, (op, _) in enumerate(ins) if op.params]
    if not idx:
        return _insert_rz(ins, n, rng)
    i = int(rng.choice(idx))
    op = ins[i][0].copy()
    op.params = [float(op.params[0]) + float(rng.choice(EPSILONS))] + list(op.params[1:])
    return ins[:i] + [(op, ins[i][1])] + ins[i + 1:]


def _insert_rz(ins, n, rng):
    i = int(rng.integers(len(ins) + 1))
    return ins[:i] + [(RZGate(float(rng.choice(EPSILONS))), [int(rng.integers(n))])] + ins[i:]


def _insert_pauli(ins, n, rng):
    i = int(rng.integers(len(ins) + 1))
    gate = (XGate, YGate, ZGate)[int(rng.integers(3))]()
    return ins[:i] + [(gate, [int(rng.integers(n))])] + ins[i:]


def _cancelling_pair(ins, n, rng):
    i = int(rng.integers(len(ins) + 1))
    kind = int(rng.integers(3))
    if kind == 2 and n >= 2:
        a, b = (int(q) for q in rng.choice(n, size=2, replace=False))
        pair = [(CXGate(), [a, b])] * 2
    else:
        q = int(rng.integers(n))
        pair = [((HGate, XGate)[kind % 2](), [q])] * 2
    return ins[:i] + pair + ins[i:]


def _commute_swap(ins, n, rng):
    idx = [i for i in range(len(ins) - 1) if not set(ins[i][1]) & set(ins[i + 1][1])]
    if not idx:
        return None
    i = int(rng.choice(idx))
    return ins[:i] + [ins[i + 1], ins[i]] + ins[i + 2:]


OPERATORS = {
    "delete gate": _delete, "swap control/target": _flip, "wrong qubit": _retarget,
    "wrong gate": _replace, "angle error": _angle, "stray rotation": _insert_rz,
    "stray Pauli": _insert_pauli, "cancelling pair": _cancelling_pair,
    "reorder disjoint gates": _commute_swap,
}


def generate_mutants(golden: QuantumCircuit, count: int, rng) -> list[tuple[str, QuantumCircuit]]:
    """``count`` distinct single-edit mutants of ``golden`` as (operator, circuit)."""
    base, n = _instrs(golden), golden.num_qubits
    seen = {tuple(instruction_signatures(golden))}
    names, out, attempts = list(OPERATORS), [], 0
    while len(out) < count and attempts < count * 50:
        attempts += 1
        name = names[int(rng.integers(len(names)))]
        mutated = OPERATORS[name](list(base), n, rng)
        if not mutated:
            continue
        qc = _build(n, golden.name, mutated)
        sig = tuple(instruction_signatures(qc))
        if sig in seen:
            continue
        seen.add(sig)
        out.append((name, qc))
    return out


def pad(golden: QuantumCircuit, pairs: int) -> QuantumCircuit:
    """``golden`` plus ``pairs`` cancelling CX-CX pairs right after its first
    2-qubit gate: logically the identity, physically ``2 * pairs`` extra gates."""
    ins = _instrs(golden)
    i, (_, qs) = next((i, x) for i, x in enumerate(ins) if len(x[1]) >= 2)
    extra = [(CXGate(), qs[:2])] * (2 * pairs)
    return _build(golden.num_qubits, golden.name, ins[:i + 1] + extra + ins[i + 1:])


# ------------------------------------------------------------------- the runs

@dataclass
class MutantResult:
    circuit: str
    operator: str
    state_dist: float
    changed: bool           # ground truth: the output state moved
    verdict: str
    z_dev: float            # exact TVD in the Z basis
    probe_dev: float        # max exact TVD over Z, X, Y
    naive_dev: float        # noisy counts vs golden ideal


def run(backend: Backend, per_circuit: int = 150, seed: int = 2026,
        pad_seeds: int = 20, shots: int = 1000) -> dict:
    """Run the benchmark and return ``{"mutants": [...], "padding": [...], ...}``."""
    rng = np.random.default_rng(seed)
    cfg_kwargs = dict(blame=False)
    mutants: list[MutantResult] = []
    padding: list[dict] = []
    goldens = golden_circuits()
    for name, golden in goldens.items():
        snap = record(name, golden, backend, shots=shots)
        for k, (op, qc) in enumerate(generate_mutants(golden, per_circuit, rng)):
            r = check(name, qc, snap, backend, CheckConfig(seed=10_000 + k, **cfg_kwargs))
            dist = E.state_distance(golden, qc)
            mutants.append(MutantResult(
                name, op, dist, dist > E.EXACT_TOL, r.verdict.value,
                r.per_basis_logic_dev["Z"], max(r.per_basis_logic_dev.values()),
                r.naive_dev))
        for pairs in PAD_LEVELS:
            padded = pad(golden, pairs)
            for s in range(pad_seeds):
                r = check(name, padded, snap, backend,
                          CheckConfig(seed=20_000 + s, **cfg_kwargs))
                padding.append({"circuit": name, "pairs": pairs, "verdict": r.verdict.value,
                                "naive_fails": bool(r.naive_fails)})
    return {"config": {"backend": backend.describe(), "per_circuit": per_circuit,
                       "seed": seed, "pad_seeds": pad_seeds, "shots": shots,
                       "circuits": list(goldens)},
            "mutants": [asdict(m) for m in mutants], "padding": padding}


def _rate(flags) -> float:
    flags = list(flags)
    return float(np.mean(flags)) if flags else float("nan")


def summarize(results: dict, logic_tol: float = 1e-9) -> dict:
    ms = results["mutants"]
    changed = [m for m in ms if m["changed"]]
    same = [m for m in ms if not m["changed"]]

    def methods(group, naive_threshold=0.05):
        return {"n": len(group),
                "naive": _rate(m["naive_dev"] > naive_threshold for m in group),
                "z_only": _rate(m["z_dev"] > logic_tol for m in group),
                "zxy": _rate(m["probe_dev"] > logic_tol for m in group),
                "qdebug": _rate(m["verdict"] == Verdict.LOGIC_BUG.value for m in group)}

    buckets, lo = {}, 0.0
    for label, hi in BUCKETS:
        buckets[label] = methods([m for m in changed if lo <= m["state_dist"] < hi])
        lo = hi
    pad_rows = {}
    for pairs in sorted({p["pairs"] for p in results["padding"]}):
        group = [p for p in results["padding"] if p["pairs"] == pairs]
        pad_rows[str(pairs)] = {
            "n": len(group),
            "qdebug_noise": _rate(p["verdict"] == Verdict.NOISE_REGRESSION.value for p in group),
            "qdebug_logic": _rate(p["verdict"] == Verdict.LOGIC_BUG.value for p in group),
            "naive_fail": _rate(p["naive_fails"] for p in group)}
    return {
        "n_mutants": len(ms), "n_changed": len(changed), "n_equivalent": len(same),
        "detection": {"all": methods(changed), **buckets},
        "equivalent": {
            "n": len(same),
            "naive_flags": _rate(m["naive_dev"] > 0.05 for m in same),
            "qdebug_logic_bug": _rate(m["verdict"] == Verdict.LOGIC_BUG.value for m in same),
            "qdebug_noise_regression":
                _rate(m["verdict"] == Verdict.NOISE_REGRESSION.value for m in same)},
        "naive_thresholds": {
            str(t): {"misses_bugs": 1 - _rate(m["naive_dev"] > t for m in changed),
                     "flags_equivalent": _rate(m["naive_dev"] > t for m in same)}
            for t in NAIVE_THRESHOLDS},
        "by_operator": {
            op: {"n": len(g), "changed": sum(m["changed"] for m in g),
                 "naive": _rate(m["naive_dev"] > 0.05 for m in g if m["changed"]),
                 "z_only": _rate(m["z_dev"] > logic_tol for m in g if m["changed"]),
                 "zxy": _rate(m["probe_dev"] > logic_tol for m in g if m["changed"]),
                 "qdebug": _rate(m["verdict"] == Verdict.LOGIC_BUG.value
                                 for m in g if m["changed"])}
            for op in OPERATORS for g in [[m for m in ms if m["operator"] == op]]},
        "padding": pad_rows,
        "by_circuit": {
            name: {
                "changed": len(g),
                "zxy_misses": sum(m["probe_dev"] <= logic_tol for m in g),
                "noise_at_max_padding": _rate(
                    p["verdict"] == Verdict.NOISE_REGRESSION.value
                    for p in results["padding"]
                    if p["circuit"] == name and p["pairs"] == max(PAD_LEVELS))}
            for name in results["config"]["circuits"]
            for g in [[m for m in changed if m["circuit"] == name]]},
    }


# ------------------------------------------------------------------- reporting

def _pct(x: float) -> str:
    return "-" if x != x else f"{100 * x:.0f}%"


def to_markdown(results: dict, summary: dict) -> str:
    c, d, e = results["config"], summary["detection"], summary["equivalent"]
    out = [
        "# qdebug mutation benchmark", "",
        f"{summary['n_mutants']} random single-edit mutants of {len(c['circuits'])} circuits "
        f"({', '.join(c['circuits'])}), {c['shots']} shots, backend `{c['backend']}`, "
        f"seed {c['seed']}. Reproduce with `python -m qdebug benchmark`.", "",
        f"Ground truth: {summary['n_changed']} mutants changed the output state, "
        f"{summary['n_equivalent']} did not.", "",
        "## Logic changes caught", "",
        "| size of the change (trace distance) | mutants | naive shot test | exact, Z only "
        "| exact, Z/X/Y | qdebug |", "|---|---|---|---|---|---|"]
    for label in ["all"] + [b[0] for b in BUCKETS]:
        r = d[label]
        out.append(f"| {label} | {r['n']} | {_pct(r['naive'])} | {_pct(r['z_only'])} | "
                   f"{_pct(r['zxy'])} | {_pct(r['qdebug'])} |")
    out += ["",
            "qdebug's 100% is true by construction on circuits this small: it compares the output "
            "states exactly, which is also how the ground truth is defined. The measurement here "
            "is how much the other tests miss.", "",
            "## Edits that changed nothing", "",
            f"{e['n']} mutants left the output state untouched. Flagged as a failure by the "
            f"naive test: {_pct(e['naive_flags'])}. Called LOGIC_BUG by qdebug: "
            f"{_pct(e['qdebug_logic_bug'])}. Called NOISE_REGRESSION by qdebug: "
            f"{_pct(e['qdebug_noise_regression'])}.", "",
            "## The naive test cannot be tuned out of the problem", "",
            "| threshold | logic changes missed | equivalent edits flagged |", "|---|---|---|"]
    for t, r in summary["naive_thresholds"].items():
        out.append(f"| {t} | {_pct(r['misses_bugs'])} | {_pct(r['flags_equivalent'])} |")
    out += ["", "## By kind of edit", "",
            "| edit | mutants | changed the logic | naive | exact, Z only | exact, Z/X/Y | qdebug |",
            "|---|---|---|---|---|---|---|"]
    for op, r in summary["by_operator"].items():
        out.append(f"| {op} | {r['n']} | {r['changed']} | {_pct(r['naive'])} | "
                   f"{_pct(r['z_only'])} | {_pct(r['zxy'])} | {_pct(r['qdebug'])} |")
    out += ["", "## Noise only: redundant CX pairs", "",
            f"Each circuit padded with cancelling CX-CX pairs, {c['pad_seeds']} noisy runs per "
            "level. The logic is unchanged at every level, so LOGIC_BUG would be wrong and the "
            "naive test's FAIL carries no information about why.", "",
            "| added CX pairs | runs | qdebug: NOISE_REGRESSION | qdebug: LOGIC_BUG | naive: FAIL |",
            "|---|---|---|---|---|"]
    for k, r in summary["padding"].items():
        out.append(f"| {k} | {r['n']} | {_pct(r['qdebug_noise'])} | {_pct(r['qdebug_logic'])} | "
                   f"{_pct(r['naive_fail'])} |")
    out += ["", "## By circuit", "",
            "Averages hide where a method fails, so here is the same data per circuit.", "",
            "| circuit | logic changes | missed by exact Z/X/Y probes | NOISE_REGRESSION at "
            f"{max(PAD_LEVELS)} pairs |", "|---|---|---|---|"]
    for name, r in summary["by_circuit"].items():
        out.append(f"| {name} | {r['changed']} | {r['zxy_misses']} | "
                   f"{_pct(r['noise_at_max_padding'])} |")
    out += ["",
            "`bell_rot` was built so that its output is uniform in the all-Z, all-X and all-Y "
            "bases. That explains both of its rows: probes in those bases cannot see changes to "
            "it, and depolarizing noise (which pushes towards uniform) cannot be seen in them "
            "either. The exact state comparison fixes the first; the second is an open "
            "limitation of the noise check for circuits like this."]
    return "\n".join(out) + "\n"


def plot(summary: dict, path: str | Path) -> Path | None:
    """Two panels: logic changes caught by size, and the noise-only sweep."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    ink, muted, grid, surface = "#0b0b0b", "#52514e", "#e7e6e1", "#fcfcfb"
    series = [("naive", "Naive shot test", "#eb6834"), ("z_only", "Exact, Z basis only", "#1baf7a"),
              ("zxy", "Exact, Z/X/Y probes", "#eda100"), ("qdebug", "qdebug", "#2a78d6")]
    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.6), facecolor=surface)
    for ax in (a, b):
        ax.set_facecolor(surface)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(grid)
        ax.tick_params(colors=muted, length=0)
        ax.set_ylim(0, 108)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_yticklabels(["0", "25", "50", "75", "100%"])
        ax.yaxis.grid(True, color=grid, linewidth=1)
        ax.set_axisbelow(True)

    labels = [b_[0] for b_ in BUCKETS]
    width = 0.19
    for j, (key, name, color) in enumerate(series):
        vals = [100 * summary["detection"][lab][key] for lab in labels]
        xs = [i + (j - 1.5) * (width + 0.015) for i in range(len(labels))]
        a.bar(xs, vals, width, color=color, label=name)
        if key in ("naive", "qdebug"):               # the two the story is about
            for x, v in zip(xs, vals):
                a.text(x, v + 2, f"{v:.0f}", ha="center", va="bottom", fontsize=9, color=ink)
    a.set_xticks(range(len(labels)))
    a.set_xticklabels([f"{lab}\n(n = {summary['detection'][lab]['n']})" for lab in labels])
    a.set_xlabel("size of the logic change (trace distance of the output state)", color=muted)
    a.set_title("Logic changes caught", loc="left", color=ink, fontsize=12, fontweight="bold")
    a.legend(frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.24),
             labelcolor=muted)

    ks = list(summary["padding"])
    xs = range(len(ks))
    for key, name, color in (("naive_fail", "Naive shot test says FAIL", "#eb6834"),
                             ("qdebug_noise", "qdebug says NOISE_REGRESSION", "#2a78d6"),
                             ("qdebug_logic", "qdebug says LOGIC_BUG", "#52514e")):
        vals = [100 * summary["padding"][k][key] for k in ks]
        b.plot(xs, vals, color=color, linewidth=2, marker="o", markersize=7,
               markeredgecolor=surface, markeredgewidth=2, label=name)
    b.set_xticks(list(xs))
    b.set_xticklabels(ks)
    b.set_xlabel("redundant CX-CX pairs added (logic unchanged)", color=muted)
    b.set_title("Noise only: what each test reports", loc="left", color=ink, fontsize=12,
                fontweight="bold")
    b.legend(frameon=False, ncol=1, loc="upper center", bbox_to_anchor=(0.5, -0.2),
             labelcolor=muted)
    fig.tight_layout()
    path = Path(path)
    fig.savefig(path, dpi=140, facecolor=surface, bbox_inches="tight")
    plt.close(fig)
    return path


def write(results: dict, out_dir: str | Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)
    (out_dir / "summary.json").write_text(
        json.dumps({"config": results["config"], **summary}, indent=2))
    (out_dir / "results.json").write_text(json.dumps(results))
    (out_dir / "benchmark.md").write_text(to_markdown(results, summary))
    plot(summary, out_dir / "benchmark.png")
    return summary
