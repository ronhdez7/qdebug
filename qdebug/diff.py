"""Structural diff of two circuits + hunk-level blame ("which edit broke it?").

Idea: a *hunk* is a contiguous run of changed instructions between the golden
and the modified circuit. We re-apply each hunk **alone** onto the golden
circuit and measure how far the ideal output moves. Hunks that move it are the
culprits; hunks that don't (e.g. a cancelling H-H pair, commuting reorder) are
harmless refactors.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from qiskit import QuantumCircuit
from qiskit.circuit import CircuitInstruction

from .circuits import instruction_signatures


def compress(lines: list[str]) -> list[str]:
    """Collapse runs of identical lines: ['cx q0,q1'] * 80 -> ['cx q0,q1  (x80)']."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        out.append(lines[i] if j - i == 1 else f"{lines[i]}  (x{j - i})")
        i = j
    return out


@dataclass
class Hunk:
    index: int
    kind: str                 # "replace" | "insert" | "delete"
    golden_range: tuple[int, int]
    mod_range: tuple[int, int]
    removed: list[str]
    added: list[str]

    def pretty(self) -> str:
        lines = [f"@@ golden[{self.golden_range[0]}:{self.golden_range[1]}] "
                 f"-> modified[{self.mod_range[0]}:{self.mod_range[1]}] ({self.kind})"]
        lines += [f"  - {s}" for s in compress(self.removed)]
        lines += [f"  + {s}" for s in compress(self.added)]
        return "\n".join(lines)


def _instrs(qc: QuantumCircuit) -> list[CircuitInstruction]:
    return [i for i in qc.data if i.operation.name != "barrier"]


def diff_circuits(golden: QuantumCircuit, modified: QuantumCircuit) -> list[Hunk]:
    g, m = instruction_signatures(golden), instruction_signatures(modified)
    sm = difflib.SequenceMatcher(None, g, m, autojunk=False)
    hunks = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        hunks.append(Hunk(len(hunks), tag, (i1, i2), (j1, j2), g[i1:i2], m[j1:j2]))
    return hunks


def build_hybrid(golden: QuantumCircuit, modified: QuantumCircuit,
                 hunks: list[Hunk], apply: set[int]) -> QuantumCircuit:
    """Golden circuit with only the hunks in ``apply`` taken from ``modified``."""
    gi, mi = _instrs(golden), _instrs(modified)
    out = QuantumCircuit(golden.num_qubits, name=f"{golden.name}__hybrid")
    pos = 0
    for h in hunks:
        out_src = gi[pos:h.golden_range[0]]
        _extend(out, out_src, golden)
        if h.index in apply:
            _extend(out, mi[h.mod_range[0]:h.mod_range[1]], modified)
        else:
            _extend(out, gi[h.golden_range[0]:h.golden_range[1]], golden)
        pos = h.golden_range[1]
    _extend(out, gi[pos:], golden)
    return out


def _extend(out: QuantumCircuit, instrs, src: QuantumCircuit) -> None:
    for inst in instrs:
        idx = [src.find_bit(q).index for q in inst.qubits]
        out.append(inst.operation, [out.qubits[i] for i in idx])
