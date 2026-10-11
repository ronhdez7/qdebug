"""Console / Markdown / JSON / plot reporting."""
from __future__ import annotations

import json
from pathlib import Path

from .checker import Report, Verdict
from .diff import compress

ICON = {Verdict.PASS: "PASS ", Verdict.REFACTOR_OK: "REFAC", Verdict.LOGIC_BUG: "LOGIC",
        Verdict.NOISE_REGRESSION: "NOISE"}


def format_report(r: Report, verbose: bool = True) -> str:
    lines = [f"[{r.verdict.value}] {r.name}", f"  {r.reason}"]
    if verbose:
        nd = f"{r.noise_dev:.4f}" if r.noise_dev is not None else "-"
        nb = f"{r.noise_budget:.4f}" if r.noise_budget is not None else "-"
        lines.append(f"  logic dev (exact ideal TVD): {r.logic_dev:.3g}  tol {r.logic_tol:g}"
                     f"   |  noise dev: {nd}  budget: {nb}"
                     f"   |  naive noisy-vs-golden: "
                     + (f"{r.naive_dev:.4f}" if r.naive_dev is not None else "-"))
        if r.state_dist is not None:
            ud = f"{r.unitary_dist:.3g}" if r.unitary_dist is not None else "-"
            lines.append(f"  exact check ({r.equivalence}): state distance "
                         f"{r.state_dist:.3g}  |  unitary distance {ud}")
        for h in r.hunks:
            lines.append("  " + h.pretty().replace("\n", "\n  "))
        for b in r.blame:
            lines.append(f"  blame: hunk {b.hunk.index} effect={b.effect:.3g}"
                         + ("  <-- culprit" if b.culprit else ""))
        for b in r.noise_blame:
            lines.append(f"  noise blame: hunk {b.hunk.index} cost {b.cost_delta:+.3g}"
                         + (f"  ({b.share:.0%} of the increase)" if b.share else ""))
    for w in r.warnings:
        lines.append(f"  WARNING: {w}")
    return "\n".join(lines)


def summary_table(reports: list[Report], expected: dict[str, str] | None = None) -> str:
    head = ["case", "verdict", "logic dev", "noise dev/budget", "naive says"]
    if expected:
        head.append("expected")
    rows = [head]
    for r in reports:
        nd = "-" if r.noise_dev is None else f"{r.noise_dev:.3f}/{r.noise_budget:.3f}"
        naive = "-" if r.naive_fails is None else ("FAIL" if r.naive_fails else "ok")
        row = [r.name, r.verdict.value, f"{r.logic_dev:.2g}", nd, naive]
        if expected:
            exp = expected.get(r.name, "")
            row.append(exp + ("" if exp == r.verdict.value else "  <-- MISMATCH"))
        rows.append(row)
    widths = [max(len(str(x[i])) for x in rows) for i in range(len(head))]
    sep = "  "
    out = [sep.join(str(c).ljust(w) for c, w in zip(rows[0], widths)),
           sep.join("-" * w for w in widths)]
    out += [sep.join(str(c).ljust(w) for c, w in zip(row, widths)) for row in rows[1:]]
    return "\n".join(out)


def to_markdown(reports: list[Report], title: str = "qdebug report") -> str:
    out = [f"# {title}", "", "| case | verdict | logic dev | noise dev / budget | naive says |",
           "|---|---|---|---|---|"]
    for r in reports:
        nd = "-" if r.noise_dev is None else f"{r.noise_dev:.3f} / {r.noise_budget:.3f}"
        naive = "-" if r.naive_fails is None else ("FAIL" if r.naive_fails else "ok")
        out.append(f"| {r.name} | **{r.verdict.value}** | {r.logic_dev:.2g} | {nd} | {naive} |")
    out.append("")
    for r in reports:
        out += [f"## {r.name}: {r.verdict.value}", "", r.reason, ""]
        if r.hunks:
            out.append("```diff")
            for h in r.hunks:
                out += [f"@@ {h.kind} @@"] + [f"- {s}" for s in compress(h.removed)] \
                       + [f"+ {s}" for s in compress(h.added)]
            out += ["```", ""]
        for b in r.blame:
            out.append(f"- hunk {b.hunk.index}: effect {b.effect:.3g}"
                       + (" **(culprit)**" if b.culprit else ""))
        for b in r.noise_blame:
            out.append(f"- hunk {b.hunk.index}: noise cost {b.cost_delta:+.3g}"
                       + (f" ({b.share:.0%} of the increase)" if b.share else ""))
        if r.blame or r.noise_blame:
            out.append("")
        for w in r.warnings:
            out.append(f"> WARNING: {w}\n")
    return "\n".join(out)


def save_json(reports: list[Report], path: str | Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps([r.to_dict() for r in reports], indent=2, default=str))
    return path


def plot_reports(reports: list[Report], path: str | Path) -> Path | None:
    """Scatter of logic deviation (x, log) vs noise deviation (y). Bugs live far
    right of the tolerance line; noisy-but-correct circuits live high up."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    colors = {Verdict.PASS: "tab:green", Verdict.REFACTOR_OK: "tab:blue",
              Verdict.LOGIC_BUG: "tab:red", Verdict.NOISE_REGRESSION: "tab:orange"}
    fig, ax = plt.subplots(figsize=(8, 5.5))
    floor = 1e-8
    for r in reports:
        if r.noise_dev is None or not r.noise_budget:
            continue
        x = max(r.logic_dev, floor)
        y = r.noise_dev / r.noise_budget
        ax.scatter(x, y, s=70, color=colors[r.verdict], zorder=3)
        ax.annotate(r.name, (x, y), fontsize=7, xytext=(4, 4),
                    textcoords="offset points")
    ax.axvline(reports[0].logic_tol, ls="--", color="gray")
    ax.axhline(1.0, ls="--", color="gray")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("logic deviation (exact ideal TVD vs golden)  ->  bug")
    ax.set_ylabel("noise deviation / noise budget  ->  too noisy")
    ax.set_title("qdebug: logic bugs vs noise (dashed = thresholds)")
    handles = [plt.Line2D([], [], marker="o", ls="", color=c, label=v.value)
               for v, c in colors.items()]
    ax.legend(handles=handles, loc="center left", fontsize=8)
    fig.tight_layout()
    path = Path(path)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path
