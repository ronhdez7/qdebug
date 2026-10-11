# Qollab Project Page

Use this text for the Qollab project card and write-up. Replace the repository and video placeholders before publishing.

## Project title

qdebug: Quantum Logic Bugs vs Noise

## Short description

qdebug helps quantum developers tell logic bugs from noise. It compares exact ideal circuit behavior with noisy runs, classifies regressions, and points to the edit responsible, with an interactive lesson and runnable Qiskit demo.

**Character count:** 230 / 240

## Suggested tags

Qiskit, quantum computing, debugging, regression testing, developer tools, education

## Project write-up

### What it does

When a quantum circuit changes, its output may move because the logic changed or because the simulator introduced noise. qdebug is an open-source circuit debugger and regression tester that separates those two causes.

Record a known-good circuit as a snapshot, then check edited circuits against it. qdebug compares their ideal behavior separately from their noisy samples and reports one of four verdicts:

- `PASS`: the circuit and its behavior are unchanged.
- `REFACTOR_OK`: the circuit changed, but its output state is equivalent.
- `LOGIC_BUG`: the ideal output changed; qdebug reports the responsible circuit edit where possible.
- `NOISE_REGRESSION`: the ideal behavior is unchanged, but the circuit is measurably noisier than its baseline.

### Why this matters

A shot-based test can mistake ordinary sampling or hardware noise for a logic bug, and it can miss small but real logic changes. qdebug keeps the comparisons separate: ideal behavior is compared with the golden circuit, while noisy samples are compared with the edited circuit's own ideal behavior.

### Try it

The project includes a browser lesson, challenge, and circuit-building sandbox at [`docs/index.html`](index.html). The lesson demonstrates why Z-only measurements miss phase changes, how X/Y probes expose some of them, and why noisy counts should be judged against the circuit's own ideal output.

To run the built-in Qiskit demo locally, install the dependencies from `requirements.txt`, then run:

```bash
python -m qdebug demo --backend local --assert-all
```

The demo injects harmless refactors, logic bugs, and a noise-only regression. It checks 12 expected verdicts and exits nonzero if one does not match. No account, API key, or network access is needed for this local demo.

To use qdebug on your own circuits, record a golden snapshot and check later edits:

```bash
python -m qdebug record --suite examples/suite.py --snapshots examples/snapshots
python -m qdebug check --suite examples/suite.py --snapshots examples/snapshots -v
```

IonQ's cloud simulator is an optional backend for noisy runs. It requires a user-provided `IONQ_API_KEY`; never put that key in project files. Ideal verdict probabilities are computed locally and exactly, while remote IonQ ideal results are used as a consistency diagnostic.

### How it works

qdebug records exact ideal distributions in Z, X, and Y measurement bases, plus a seeded noisy baseline. On a check, it compares the edited circuit's ideal output with the golden snapshot, compares noisy samples with the edited circuit's own ideal output, and examines small circuits with exact statevector and unitary comparisons. Structural diffing links logic changes to the responsible edit when possible.

### Project status

- Local demo: 12 of 12 expected verdicts matched.
- IonQ live checks: 4 passed, including bit-order smoke test, remote probability support, reproducible noisy counts, and end-to-end verdicts.
- License: MIT.
- Built with Qiskit and AI assistance; the code and results were reviewed and tested by the project team.

### Links

- Source repository: **add the public repository URL**
- Demo video (2-3 minutes, public link): **add the video URL**
- Browser lesson: available in `docs/index.html` in the source repository

This effort is supported by Qollab & IonQ.

### Theme

Quantum Unlocked: an interactive lesson that teaches why debugging quantum circuits requires separating logic changes from noise.