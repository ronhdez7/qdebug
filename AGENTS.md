# AGENTS.md

Instructions for coding agents working in this repository. Read this and `ROADMAP.md` before
touching code.

## The project

`qdebug` is a quantum circuit debugger and regression tester, built for the
**Qollab x IonQ Quantum Hackathon** (<https://qollab.xyz/programs/hackathon>): a 48-hour build of
an open-source quantum project "worth forking". **Submissions close Oct 11, 2026, 19:00 ET.**

The question it answers: when a circuit is edited and its output moves, is that a **logic bug** or
**just noise**? It answers by looking at each circuit two ways:

- **Ideal view.** Exact output probabilities of the edited circuit, compared with the golden
  circuit's exact probabilities. No shot noise, no hardware noise, so any difference is a logic
  change.
- **Noisy view.** Sampled counts under a noise model, compared with the edited circuit's *own*
  ideal distribution. This measures noise only, and is judged against a baseline recorded from the
  golden circuit.

Verdicts: `PASS`, `REFACTOR_OK`, `LOGIC_BUG` (with the diff hunk responsible), `NOISE_REGRESSION`.

Where it is going: a tool a team can drop into CI, validated on IonQ's cloud simulator, with an
interactive demo that teaches the logic-vs-noise distinction. `ROADMAP.md` has the current state,
the known gaps, and the stages.

## Your role

You implement **one roadmap stage (or the part of it you were assigned) at a time**, verify it,
commit it locally, and report back to the developers. The developers decide direction, review your
work, and push.

### Hard rules

1. **Never push.** No `git push`, no `gh pr create`, no publishing of any kind. You commit locally
   and report. The developers push.
2. **Commit only on the branch you were started on.** Do not switch, create, rebase, amend or
   force anything in history you did not write in this session unless a developer asks.
3. **Never fake a result.** If a test fails, say so and include the output. If you could not run
   something (no API key, no network), say that. Do not write IonQ output into the README that did
   not come from a real run.
4. **Do not make a test pass by weakening it.** Do not change a mutant's `expected` verdict, loosen
   a tolerance, raise a noise margin, or delete a test to get green. If you believe a test or an
   expectation is wrong, stop and put it in the report as an anomaly.
5. **No secrets in the repo.** `IONQ_API_KEY` comes from the environment only. Never print it, log
   it, or commit it. Do not create accounts or keys yourself.
6. **Stay in scope.** Do the assigned stage. If you find a bug elsewhere, fix it only if it blocks
   you; otherwise report it.
7. **Ask the developers** for anything listed under "Needs a human" in `ROADMAP.md`: API key,
   license choice, theme choice, submission rules.

## Setup and commands

```bash
python -m venv .venv && source .venv/bin/activate     # Python 3.10+
pip install -r requirements.txt                        # or: pip install -e ".[all]"

python -m pytest -q                                    # full suite, about 70 s
python -m qdebug doctor --backend local                # smoke test
python -m qdebug demo --backend local --assert-all     # must print 10/10 (or N/N) and exit 0

# only with a developer-provided key:
python -m qdebug doctor --backend ionq
python -m qdebug demo --backend ionq --noise-model forte-1 --seeds 2
```

Baseline at commit `3364591`: 35 tests pass, demo is 10/10 on the local backend. If your starting
point is worse than that, report it before changing anything.

## Code map

```
qdebug/
  stats.py        TVD, counts -> probs, shot-noise floor
  circuits.py     measurement stripping, probe bases, instruction signatures, QPY (de)serialisation
  backends.py     Backend ABC; LocalBackend, IonQBackend, QiskitBackendAdapter; make_backend()
  snapshot.py     record(), save(), load(); Snapshot and NoiseBaseline
  diff.py         difflib over signatures -> hunks; build_hybrid() for blame
  checker.py      check(), Verdict, CheckConfig, Report, noise_budget()
  report.py       console / Markdown / JSON / PNG
  cli.py          record, check, demo, doctor
  demo_suite.py   golden circuits + mutants with expected verdicts
examples/suite.py   example user suite
notebooks/          quickstart notebook
tests/              pytest; test_ionq_adapter.py uses a fake qiskit-ionq provider
```

## Invariants: do not break these

- **The two views stay separate.** Logic deviation is ideal-vs-golden-ideal. Noise deviation is
  noisy-vs-own-ideal. Never compare noisy counts with the golden ideal to reach a verdict; that is
  the "naive" column, kept only for contrast.
- **`optimization_level=0` when transpiling.** A redundant CX pair must still be executed and cost
  noise. Optimizing it away silently breaks `NOISE_REGRESSION` detection.
- **Bitstrings are Qiskit little-endian** (`"011"` means q2=0, q1=1, q0=1), with register spaces
  stripped by `normalize_key`. Every backend must return that convention.
- **Every backend implements** `ideal_probs(circuits)` and `noisy_counts(circuits, shots, seed)`
  and returns one result per circuit, in order.
- **Seeded and reproducible.** Noisy runs and the shot-noise floor take explicit seeds. Do not
  introduce unseeded randomness.
- **Ideal probabilities must be exact**, not re-sampled. On IonQ that means
  `job.get_probabilities()`, not `get_counts()`.
- **The default test suite needs no network and no API key.** Live IonQ tests are opt-in and
  skipped when the key is missing.
- **Unsupported circuits fail loudly.** Mid-circuit measurement and classical control raise a clear
  `ValueError`; never strip them silently.
- **Snapshot format changes bump `SNAPSHOT_VERSION`** and handle old files deliberately.
- **`demo_suite.mutants()` is the accuracy benchmark.** New capability means new mutants with
  expected verdicts, added in the same commit.

## Working style

- Match the existing code: small modules, type hints, docstrings that explain *why*, lazy imports
  for optional dependencies (`qiskit_aer`, `qiskit_ionq`, `matplotlib`).
- New behaviour comes with tests. For a bug fix, write the failing test first and confirm it fails
  for the right reason.
- Keep `README.md` true. If you change behaviour, defaults, or test counts, update the README in
  the same commit.
- No new dependencies without saying so in the report. Optional ones go in the matching extra in
  `pyproject.toml` and in `requirements.txt`.
- Do not commit generated output (`demo_out/`, `.venv/`, caches).

## Finishing a stage

1. Run the full suite, `doctor`, and `demo --assert-all`. Run the IonQ commands too if a key is set.
2. Commit locally. One commit per coherent change, with a message that says what changed and why.
   Do not push.
3. Send the developers a stage report in this format:

```markdown
## Stage report: <stage number and name>

**Commits:** <short hashes and subjects> (local only, not pushed)

### What was done
<the changes, grouped by file or feature; what was left out of the stage and why>

### Test results
- `pytest -q`: <N passed / M failed / K skipped, runtime>; baseline was <N0>
- `qdebug doctor --backend local`: <result>
- `qdebug demo --backend local --assert-all`: <x/y verdicts, exit code>
- IonQ runs: <result, or "not run: no API key">
<paste the output of any failure verbatim>

### Anomalies and debugging
<anything unexpected: failing or flaky tests, surprising numbers, API behaviour that differed from
the docs or the fake provider, environment problems. For each: the symptom, the cause you found,
the fix, and how you confirmed it. Write "None" if there were none.>

### How it works
<a short explanation of the new behaviour so a developer can review it without reading every
line: the idea, the main functions touched, how to try it by hand>

### Open questions / needs a human
<decisions you did not make, things that need a key or a push, follow-ups for later stages>
```

Report what happened, including the parts that did not work. A stage that ends with a known
failing test and a clear explanation is more useful than a green run nobody can trust.
