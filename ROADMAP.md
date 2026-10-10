# qdebug roadmap

Project for the **Qollab x IonQ Quantum Hackathon** (<https://qollab.xyz/programs/hackathon>).
48-hour build of an open-source quantum project "worth forking".

| | |
|---|---|
| Build window | Oct 9, 19:00 ET to **Oct 11, 19:00 ET (submissions close)** |
| Team size | solo or up to 4 |
| Judging | local node panel first, top projects advance to a global panel; same rubric everywhere |
| Roadmap written | Oct 9, 2026, against commit `3364591` |
| Theme | **Quantum Unlocked** (decided Oct 9) |

---

## 0. Status after the Oct 9 session

| Stage | Status |
|---|---|
| 0. Honest and forkable | **Done.** MIT license was picked as a default; change it if you prefer another. |
| 1. Live IonQ validation | **Not started. Needs the API key.** Opt-in tests are ready (`pytest -m ionq`). |
| 2. Correctness holes | **Done.** |
| 3. CI story | **Written, not yet run on GitHub** (nothing is pushed). |
| 4. Better noise analysis | **Done locally.** Native-gate mode and calibration still need a live IonQ run. |
| 5. Interactive demo | **First version done** (`docs/index.html`), checked in a headless browser. |
| 6. Submission | Not started. |

Verified at the end of the session: default suite 70 passed, slow test passed (1,020 checks, no
false alarms), demo 12/12 on the local backend.

### Handoff: what to do next, in order

1. **Push** `main` and check that both GitHub workflows go green. Open a throwaway PR that sets the
   `cx_flip` bug in `examples/suite.py` to see the `qdebug check` job fail and comment.
2. **Stage 1** with the IonQ key:
   ```bash
   export IONQ_API_KEY=...
   python -m qdebug doctor --backend ionq
   python -m pytest -m ionq -v
   python -m qdebug demo --backend ionq --noise-model forte-1 --seeds 2 -v
   ```
   Expect `ghz4_cx_padding` to be the risky row (IonQ may cancel the redundant CX pairs). If it comes
   back `REFACTOR_OK`, rerun with native gates, which is what Stage 4 added for this case:
   ```bash
   python -m qdebug demo --backend ionq --noise-model forte-1 --seeds 2 --native --calibrate -v
   ```
   No native-gate job has been submitted yet, so treat the first one as a test: check that IonQ
   accepts it and that the calibration line prints a sensible 1q/2q ratio rather than "no
   measurable noise". Then put the real table in the README and fix the fake provider if the real
   API behaved differently.
3. **Read the rubric and deliverables** behind the Qollab login and adjust Stage 6.
4. **Demo polish (Stage 5):** enable GitHub Pages on `/docs` so the demo has a public URL, add the
   repo link to the page footer, and have someone without a quantum background try it.
5. **Stage 6**, with the feature freeze at Oct 11, 15:00 ET.

---

## 1. What this is right now

`qdebug` is a Python library + CLI that regression-tests quantum circuits and says whether a
change in output is a **logic bug** or **just noise**. It records a golden snapshot of a circuit
(exact ideal distribution in Z/X/Y probe bases + a seeded noise baseline), then classifies an
edited circuit as `PASS`, `REFACTOR_OK`, `LOGIC_BUG` or `NOISE_REGRESSION`, and blames the
specific edit (diff hunk) that moved the ideal output.

> This section describes the repo as audited at `3364591`, before the Oct 9 work. Section 0 has the
> current status; gaps closed since then are marked.

### Verified on Oct 9 (fresh venv, Python 3.14, qiskit 2.5.2, qiskit-aer 0.17.2, qiskit-ionq 1.1.1)

| Check | Result |
|---|---|
| `python -m pytest -q` | 35 passed, ~72 s |
| `python -m qdebug doctor --backend local` | bit ordering OK |
| `python -m qdebug demo --backend local --assert-all` | 10/10 verdicts match, exit 0 |

### What exists

| Area | State |
|---|---|
| Core classifier (`checker.py`, `snapshot.py`, `stats.py`) | Working, tested |
| Structural diff + per-hunk blame (`diff.py`) | Working, tested |
| Local backend: exact statevector + Aer depolarizing noise | Working, tested |
| IonQ backend (`qiskit-ionq`, ideal + noise models) | Written, tested **only against a fake provider** |
| Generic `QiskitBackendAdapter` | Written, **no tests** |
| CLI: `record`, `check`, `demo`, `doctor` | Working; exercised by hand, **no CLI tests** |
| Reports: console, Markdown, JSON, scatter PNG | Working, **no tests** |
| Demo suite: 5 golden circuits, 10 mutants with expected verdicts | Working |
| Quickstart notebook | Written, not executed in this audit |

### Known gaps and inconsistencies

These were found by reading the code against the README. Each is picked up in a stage below.

1. **Never run against live IonQ.** No API key was available. Bit ordering, probability key
   format, job shapes and server-side compilation are all assumptions until `doctor --backend ionq`
   passes for real.
2. *(closed)* **No LICENSE file.** The hackathon asks for open-source projects; without a license the repo
   is not legally forkable.
3. *(closed: unitary comparison up to 10 qubits; warning by default, failure with
   `--equivalence unitary`)* **Logic check is state-based, not unitary-based.** Everything runs from `|0...0>`. A bug in a
   gate that the input state never exercises (e.g. a wrong angle on a controlled gate whose control
   is `|0>`) is reported as `REFACTOR_OK`. The `qft3` comment in `demo_suite.py` acknowledges this.
4. *(closed: exact statevector comparison up to 20 qubits, random mixed bases above)*
   **Probe bases are uniform.** All qubits are measured in the same basis (all-Z, all-X, all-Y).
   That is not tomographically complete: a change that only shows up in mixed-basis correlations
   (e.g. `<X⊗Z>`) can slip through.
5. *(closed: the slow test now runs 1,020)* **README overstates one test claim.** It says "1,000 repeated checks of unchanged circuits";
   the test suite runs 5 circuits x 12 seeds = 60.
6. *(closed)* **`snapshots/` is gitignored, but the README tells users to commit `snapshots/*.json`.**
7. *(closed)* **Default probe bases disagree.** `record()` defaults to `("Z", "X")`; the CLI and README say
   Z, X, Y.
8. *(partly closed: `--calibrate` measures the weights; not yet run on an IonQ noise model)*
   **Noise budget is a heuristic.** Hardware noise is scaled by a gate-count cost ratio with
   hand-picked weights. Not calibrated against any IonQ noise model.
9. *(closed, pending first push)* **No CI.** Nothing runs the tests on push.
12. *(found by the benchmark, open)* **Noise is invisible on circuits whose output is uniform in
    every probed basis** (`bell_rot`): `NOISE_REGRESSION` is never raised for them. Adding a mixed
    probe basis automatically when the uniform ones are all flat would fix it.
10. **Scaling limits.** Exact local simulation caps at 24 qubits; blame is skipped above 12 hunks;
    mid-circuit measurement, classical control and parameterized circuits are unsupported
    *(parameterized circuits are now supported through parameter sweeps)*.
11. *(closed)* `stats.hellinger_fidelity` is unused.

---

## 2. What it wants to be

**The `pytest` of quantum circuits:** a tool a team drops into a repo and CI so that every circuit
change gets a trustworthy answer to "did I break the logic, or is this only noise?", on IonQ's
simulators and noise models, with an explanation pointing at the edit responsible.

For the hackathon, "done" means a judge can do all of this in under five minutes:

1. Clone, install, run one command, and see the demo table reproduce.
2. See the same demo run on the **real IonQ cloud simulator** with a hardware noise model.
3. Understand from one picture why a naive shot-based test gets it wrong and qdebug gets it right.
4. Fork it and point it at their own circuits.

### Theme (decided: Quantum Unlocked)

The hackathon has three themes. Projects that fit none are still eligible for placement and local
awards, but not for theme specialty awards.

| Theme | Fit |
|---|---|
| Quantum Impact (industry problem, classical baseline, quantum subroutine) | Poor. qdebug is tooling, not an application. |
| Quantum Playground (quantum as creative medium) | None. |
| **Quantum Unlocked** (teach a hard topic through an interactive demo or visualization) | Reachable. "Logic errors vs noise: why testing quantum programs is hard" is a real teaching topic, and Stage 5 builds the interactive piece. |

Recommendation: submit under **Quantum Unlocked** with Stage 5 as the interactive demo, and keep
the developer-tool story as the "worth forking" pitch. If the team would rather stay a pure dev
tool, drop the interactive part of Stage 5 and submit as unthemed.

**Not yet known:** the judging rubric and exact submission deliverables (video? write-up?). They
sit behind the Qollab login ("Rules & terms", "How it works"). A developer needs to read them and
update Stage 6.

---

## 3. Stages

Ordered by value per hour for a 48-hour event. Stages 0, 1 and 6 are mandatory. Each stage ends
with a commit and a stage report (format in `AGENTS.md`).

### Stage 0: Make the repo honest and forkable (about 1 h)

- Add a `LICENSE` (developers pick; MIT or Apache-2.0 are the usual choices).
- Fix the README claims in gaps 5, 6 and 7. Pick one default for probe bases and use it everywhere.
- Stop ignoring `snapshots/` for the example suite, or change the README advice. Keep `demo_out/` ignored.
- Add a GitHub Actions workflow that runs `pytest` on push and pull request.
- Remove or use `hellinger_fidelity`.

**Done when:** tests pass, README matches the code, CI file is present, license is in the tree.

### Stage 1: Live IonQ validation (about 2 to 3 h, needs a human-provided `IONQ_API_KEY`)

- `qdebug doctor --backend ionq`: confirm bit ordering and probability key format.
- `qdebug demo --backend ionq --noise-model forte-1 --seeds 2`: record the real table.
- Fix whatever differs from the fake provider, then update the fake in `tests/test_ionq_adapter.py`
  so it mimics what the real API did.
- Find out whether IonQ's compiler cancels the redundant CX pairs in `ghz4_cx_padding`. If it does,
  that mutant needs a noise regression the compiler cannot remove (deeper non-cancelling circuit).
- Add an opt-in live test (`pytest -m ionq`, skipped without a key).
- Replace the "not yet exercised against live IonQ" README section with real output.

**Done when:** the demo table in the README comes from a real IonQ run and names the noise model.
**Blocked on:** an API key from the developers. Agents must not create accounts or invent results.

### Stage 2: Close the correctness holes (about 4 to 6 h)

This is the technical core of the "worth forking" claim.

- **Unitary equivalence for `REFACTOR_OK`** (gap 3). For small circuits compare
  `Operator(golden).equiv(Operator(modified))`. Above that size, probe from several random product
  input states instead of only `|0...0>`. Report which method backed the verdict.
- **Stronger probes** (gap 4). Add randomized per-qubit bases (seeded, stored in the snapshot) on
  top of the uniform Z/X/Y set.
- Add mutants that the current version misses, with their expected verdicts: a wrong angle behind
  a `|0>` control, and a change visible only in a mixed-basis correlation. They must fail before
  the fix and pass after.
- Make the false-alarm claim true: a slow-marked test with 1,000 unchanged-circuit checks.
- Tests for `QiskitBackendAdapter`, the CLI exit codes, and the report formatters.

**Done when:** the new mutants are classified correctly and the false-alarm test passes.

### Stage 3: The CI story (about 3 to 4 h)

- A reusable GitHub Action (or documented workflow) that runs `qdebug check` on pull requests and
  posts the Markdown report as a PR comment.
- A `--markdown` output flag on `check` (today only `demo` writes Markdown).
- A small example repo layout under `examples/` with committed snapshots, plus a branch or script
  that shows a failing check.
- Snapshot versioning: reject or migrate snapshots whose `version` does not match.

**Done when:** a sample PR with an injected bug shows a red check and a readable comment.

### Stage 4: Better noise analysis (stretch, about 4 to 6 h)

- Noise-aware blame: rank hunks by estimated cost increase for `NOISE_REGRESSION`.
- Native-gate mode on IonQ so server-side optimization cannot hide the circuit being measured.
- Calibrate the noise-budget weights against IonQ noise models instead of fixed guesses (gap 8).
- Parameterized circuits: snapshot at several parameter points.

**Done when:** each item has a test and a line in the README. Take them one at a time; any subset
is shippable.

### Stage 5: Interactive demo (about 4 to 6 h, only if targeting Quantum Unlocked)

- A small interactive page or notebook widget: pick a circuit, inject a bug or add noise with a
  slider, and watch the ideal view and the noisy view move separately, with the verdict updating.
- Side-by-side "naive test says / qdebug says" so the teaching point lands without reading code.
- Short explainer text: shot noise, hardware noise, why phase bugs hide in the Z basis.

**Done when:** someone with no quantum background can use it and explain the difference between a
logic bug and noise afterwards.

### Stage 6: Submission (reserve the last 3 to 4 h, hard stop Oct 11, 19:00 ET)

- Execute the notebook top to bottom and commit it with outputs.
- README: lead with the picture and the one-command demo; move internals lower.
- Fresh-clone test on a clean machine or container.
- Whatever the rubric requires (video, write-up, theme selection). **A developer must confirm these
  from the Qollab "Rules & terms" page.**
- Tag the submitted commit.

**Done when:** it is submitted. Freeze new features at Oct 11, 15:00 ET.

---

## 4. Suggested schedule

| When (ET) | Work |
|---|---|
| Oct 9 night | Stage 0. Developers: get the IonQ key, read the rubric, pick the theme. |
| Oct 10 morning | Stage 1 |
| Oct 10 afternoon to evening | Stage 2, then Stage 3 |
| Oct 11 morning | Stage 5 (or Stage 4 if staying unthemed) |
| Oct 11, 15:00 | Feature freeze, Stage 6 |
| Oct 11, 19:00 | Submissions close |

If time runs short, cut in this order: Stage 4, Stage 3's PR-comment bot, Stage 5's polish.
Never cut Stage 1 or Stage 6.

## 5. Needs a human

- IonQ API key (Stage 1).
- License choice (Stage 0).
- Theme choice and the rubric/deliverables from behind the Qollab login (Stages 5 and 6).
- Pushing to the remote and creating the GitHub repo/PRs. Agents only commit locally.
