"""Command line interface:  python -m qdebug <record|check|demo|doctor> ..."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from qiskit import QuantumCircuit

from . import demo_suite
from .backends import make_backend
from .checker import CheckConfig, Verdict, check
from .report import format_report, plot_reports, save_json, summary_table, to_markdown
from .snapshot import load, record, save


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--backend", choices=["local", "ionq"], default="local",
                   help="local = exact statevector + Aer noise; ionq = IonQ cloud "
                        "simulator (needs IONQ_API_KEY)")
    p.add_argument("--noise-model", default="forte-1",
                   help="IonQ noise model (forte-1, forte-enterprise-1, aria-1, ...)")
    p.add_argument("--p1", type=float, default=5e-4, help="local 1q depolarizing")
    p.add_argument("--p2", type=float, default=5e-3, help="local 2q depolarizing")
    p.add_argument("--shots", type=int, default=1000)
    p.add_argument("--seeds", type=int, default=4, help="noise-baseline repetitions")
    p.add_argument("--bases", default="ZXY", help="probe bases, e.g. ZX or ZXY")
    p.add_argument("--random-bases", type=int, default=None,
                   help="extra seeded mixed-basis probes per circuit when recording "
                        "(default: 0 up to 20 qubits, 4 above)")
    p.add_argument("--equivalence", choices=["probes", "state", "unitary"],
                   default="state",
                   help="what counts as unchanged behaviour: same probe "
                        "distributions, same output state, or same unitary")
    p.add_argument("--logic-tol", type=float, default=None)
    p.add_argument("--snapshots", default="snapshots")


def _backend(a):
    return make_backend(a.backend, noise_model=a.noise_model, p1=a.p1, p2=a.p2)


def load_suite(path: str) -> dict[str, QuantumCircuit]:
    """``--suite demo`` or a .py file defining ``circuits() -> {name: circuit}``."""
    if path == "demo":
        return demo_suite.golden_circuits()
    spec = importlib.util.spec_from_file_location("qdebug_user_suite", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import suite {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(path).resolve().parent))
    spec.loader.exec_module(mod)
    return dict(mod.circuits())


# ----------------------------------------------------------------- commands

def cmd_record(a) -> int:
    be = _backend(a)
    for name, qc in load_suite(a.suite).items():
        _say(f"recording {name} on {be.name} ...")
        snap = record(name, qc, be, bases=tuple(a.bases), shots=a.shots,
                      n_seeds=a.seeds, random_bases=a.random_bases)
        p = save(snap, a.snapshots)
        _say(f"  saved {p}  (noise baseline {snap.noise.mean:.4f} +/- {snap.noise.std:.4f})")
    return 0


def cmd_check(a) -> int:
    be = _backend(a)
    cfg = CheckConfig(logic_tol=a.logic_tol, equivalence=a.equivalence)
    reports = []
    for name, qc in load_suite(a.suite).items():
        snap = load(name, a.snapshots)
        r = check(name, qc, snap, be, cfg)
        reports.append(r)
        _say(format_report(r, verbose=a.verbose))
    _say()
    _say(summary_table(reports))
    if a.json:
        save_json(reports, a.json)
    if a.markdown:
        Path(a.markdown).write_text(to_markdown(reports, "qdebug check"))
    bad = {Verdict.LOGIC_BUG} | ({Verdict.NOISE_REGRESSION} if a.strict else set())
    return 1 if any(r.verdict in bad for r in reports) else 0


def cmd_demo(a) -> int:
    be = _backend(a)
    cfg = CheckConfig(logic_tol=a.logic_tol, equivalence=a.equivalence)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    _say(f"backend: {be.describe()}")
    _say("recording golden snapshots ...")
    snaps = {}
    for name, qc in demo_suite.golden_circuits().items():
        snaps[name] = record(name, qc, be, bases=tuple(a.bases), shots=a.shots,
                             n_seeds=a.seeds, random_bases=a.random_bases)
        save(snaps[name], out / "snapshots")
        _say(f"  {name:9s} noise baseline {snaps[name].noise.mean:.4f} "
             f"(sampling alone ~{snaps[name].noise.shot_mean:.4f})")
    _say("checking modified circuits ...")
    reports, expected = [], {}
    for m in demo_suite.mutants(a.pad_pairs):
        r = check(m.name, m.circuit, snaps[m.base], be, cfg)
        reports.append(r)
        expected[m.name] = m.expected
        _say(format_report(r, verbose=a.verbose))
    _say()
    _say(summary_table(reports, expected))
    ok = sum(r.verdict.value == expected[r.name] for r in reports)
    _say(f"\n{ok}/{len(reports)} verdicts match the expected classification")
    save_json(reports, out / "report.json")
    (out / "report.md").write_text(to_markdown(reports, "qdebug demo report"))
    png = plot_reports(reports, out / "report.png")
    _say(f"wrote {out}/report.json, report.md" + (", report.png" if png else ""))
    return 0 if ok == len(reports) or not a.assert_all else 2


def cmd_doctor(a) -> int:
    """Smoke test: connectivity, bit ordering, ideal-vs-noisy sanity."""
    from .circuits import probes
    be = _backend(a)
    _say(f"backend: {be.describe()}")
    qc = QuantumCircuit(2, name="doctor_x0")
    qc.x(0)                                   # expect bitstring '01' (q1=0, q0=1)
    pc = probes(qc, ("Z",))
    ideal = be.ideal_probs(pc)[0]
    _say(f"ideal   : {ideal}")
    counts = be.noisy_counts(pc, 500, 1)[0]
    _say(f"noisy   : {counts}")
    ok = max(ideal, key=ideal.get) == "01"
    _say("bit ordering OK (Qiskit little-endian)" if ok else
         "!! bit ordering differs from Qiskit convention - results will be mislabelled")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="qdebug", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="record golden snapshots for a suite")
    p.add_argument("--suite", required=True, help="'demo' or path to suite .py")
    _add_common(p)
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("check", help="check a (modified) suite against snapshots")
    p.add_argument("--suite", required=True)
    p.add_argument("--strict", action="store_true", help="also fail on NOISE_REGRESSION")
    p.add_argument("--json", help="write full report JSON here")
    p.add_argument("--markdown", help="write a Markdown report here (for PR comments)")
    p.add_argument("-v", "--verbose", action="store_true")
    _add_common(p)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("demo", help="run the built-in bug-injection demo")
    p.add_argument("--out", default="demo_out")
    p.add_argument("--pad-pairs", type=int, default=40)
    p.add_argument("--assert-all", action="store_true",
                   help="exit 2 if any verdict differs from the expected one")
    p.add_argument("-v", "--verbose", action="store_true")
    _add_common(p)
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("doctor", help="connectivity + bit-order smoke test")
    _add_common(p)
    p.set_defaults(fn=cmd_doctor)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
