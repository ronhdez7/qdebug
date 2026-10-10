# qdebug: quantum circuit debugger + regression tester

When you change a quantum circuit and the output moves, is it a **logic bug** or just **noise**?
`qdebug` answers that by looking at every circuit two ways on a simulator (Qiskit, local Aer or the
IonQ cloud simulator) and judging the two kinds of deviation separately.

| Verdict | Meaning |
|---|---|
| `PASS` | Same circuit, same ideal behaviour, noise at baseline |
| `REFACTOR_OK` | Circuit was edited, behaviour is provably unchanged (e.g. `H·H`, commuting reorder) |
| `LOGIC_BUG` | The **ideal (noise-free)** output distribution changed. Reports *which edit* did it |
| `NOISE_REGRESSION` | Logic unchanged, but the change made the circuit noticeably noisier (e.g. +80 CX) |

---

## 1. How it works

```
golden circuit ──record──► snapshot.json
                            ├─ circuit (QPY) + readable instruction list
                            ├─ exact ideal distribution in Z / X / Y probe bases
                            └─ noise baseline: N seeded noisy runs → mean, std, shot-noise floor

modified circuit ──check──►  ① ideal view   (exact probs)      vs golden ideal      → LOGIC deviation
                             ② noisy view   (sampled counts)   vs its OWN ideal     → NOISE deviation
                             ③ structural diff + hunk blame                         → which edit?
                             ④ verdict
```

**Why two views?** The simulator can give the *exact* probabilities of the circuit as well as noisy
samples. The ideal view has no shot noise and no hardware noise, so *any* difference from the golden
ideal is a logic change, even one far below the noise floor. The noisy view is compared to the
modified circuit's **own** ideal distribution, so it measures noise and nothing else. A naive test
(noisy counts vs golden, fixed threshold) mixes these up. It false-alarms on noise and misses small
bugs; the report prints that naive result next to ours so the difference is visible.

### Pipeline details

| Step | File | What it does |
|---|---|---|
| Probe bases | `circuits.py` | Appends basis changes (Z = none, X = `H`, Y = `S†·H`) + `measure_all`. Measuring only in Z hides phase bugs (a stray `Z` before readout), so by default we measure in Z, X, Y. |
| Backends | `backends.py` | `LocalBackend` (exact statevector + Aer depolarizing noise), `IonQBackend` (IonQ cloud simulator), `QiskitBackendAdapter` (any other Qiskit backend). All expose `ideal_probs()` and `noisy_counts(shots, seed)`. |
| Snapshot | `snapshot.py` | `record()` stores the golden circuit, exact ideal distributions and a noise baseline (per-seed `mean TVD(noisy, ideal)`, its std, and the sampling-only floor from multinomial simulation). |
| Diff + blame | `diff.py` | `difflib` over instruction signatures gives *hunks*. Each hunk is re-applied **alone** to the golden circuit; hunks that move the ideal output are the culprits, ones that don't are harmless. |
| Classifier | `checker.py` | Logic: `max_basis TVD(ideal_mod, ideal_golden) > tol` → `LOGIC_BUG`. Noise: `noise_dev > budget` → `NOISE_REGRESSION`, with `budget = margin·(shot_floor + (hardware_part + 3σ)·cost_ratio)`. Otherwise `REFACTOR_OK` / `PASS`. |
| Reports | `report.py` | Console table, JSON, Markdown, and a logic-vs-noise scatter PNG. |
| CLI | `cli.py` | `record`, `check`, `demo`, `doctor`. `check` exits 1 on `LOGIC_BUG` (use in CI). |

Distance metric: total variation distance (TVD) between bitstring distributions.

### What the demo shows (local backend, 10/10 correct)

```
case                   verdict           logic dev  noise dev/budget  naive says
ghz4_unchanged         PASS              0          0.031/0.082       ok
ghz4_extra_HH          REFACTOR_OK       0          0.031/0.082       ok
bv_oracle_reordered    REFACTOR_OK       0          0.047/0.097       ok
ghz4_cx_flipped        LOGIC_BUG         0.5        0.034/0.082       FAIL
ghz4_stray_Z           LOGIC_BUG         1          0.030/0.082       FAIL     <- Z-basis alone can't see it; X probe does
bv_missing_cx          LOGIC_BUG         1          0.046/0.097       FAIL
grover3_wrong_oracle   LOGIC_BUG         0.94       0.056/0.113       FAIL
qft3_phase_err_0.02    LOGIC_BUG         0.0071     0.024/0.082       ok       <- naive test blind
bell_tiny_phase_drift  LOGIC_BUG         0.00062    0.011/0.079       ok       <- naive test blind
ghz4_cx_padding        NOISE_REGRESSION  0          0.177/0.082       FAIL     <- naive says "bug", it's only noise
```

The last column is the point: the naive test fails both a real bug and a pure-noise change (it can't
tell them apart), and passes two real bugs that sit below the shot-noise floor.

---

## 2. Project layout

```
qdebug/
  stats.py        TVD, counts→probs, shot-noise floor
  circuits.py     measurement stripping, probe bases, signatures, QPY (de)serialisation
  backends.py     LocalBackend / IonQBackend / QiskitBackendAdapter
  snapshot.py     record(), save(), load()
  diff.py         structural diff, hunks, hybrid circuits for blame
  checker.py      check(), Verdict, noise budget
  report.py       console / markdown / json / png
  cli.py          python -m qdebug ...
  demo_suite.py   golden circuits + injected bugs with expected verdicts
examples/suite.py     example user suite (env-var bug toggles)
notebooks/qdebug_quickstart.ipynb
tests/                pytest suite (incl. a fake qiskit-ionq provider)
```

---

## 3. Run it locally (no account needed)

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.10+
pip install -r requirements.txt                         # or: pip install -e ".[all]"

python -m pytest -q                                     # about a minute
python -m qdebug doctor --backend local                 # smoke test
python -m qdebug demo   --backend local --assert-all    # bug-injection demo → demo_out/report.{json,md,png}
```

Use it on your own circuits (this is the actual regression workflow):

```bash
# 1. on main: save golden behaviour (write a file with `def circuits() -> {name: QuantumCircuit}`)
python -m qdebug record --suite examples/suite.py --snapshots snapshots

# 2. on your branch, after editing circuits: regress
python -m qdebug check  --suite examples/suite.py --snapshots snapshots -v   # exit 1 on LOGIC_BUG
python -m qdebug check  --suite examples/suite.py --strict                   # also fail on NOISE_REGRESSION

# no-edit demo of step 2:
QDEBUG_DEMO_BUG=cx_flip python -m qdebug check --suite examples/suite.py -v
QDEBUG_DEMO_BUG=padding python -m qdebug check --suite examples/suite.py --strict
```

Commit `snapshots/*.json` so CI can run `qdebug check` (the demo writes its own under `demo_out/`,
which is gitignored). Library use:

```python
from qdebug import make_backend, record, check
from qdebug.report import format_report

be   = make_backend("local")                       # or make_backend("ionq", noise_model="forte-1")
snap = record("my_circuit", golden_circuit, be)
print(format_report(check("my_circuit", edited_circuit, snap, be)))
```

Circuits must be unitary (measurements are added automatically; mid-circuit measurement and classical
control are rejected with a clear error).

---

## 4. Run it on IonQ's simulator (and on Qollab)

IonQ's cloud simulators (ideal, up to 29 qubits, plus the hardware noise models) are free with an
IonQ Quantum Cloud account.

1. Create an API key at <https://cloud.ionq.com/settings/keys>.
2. In whatever environment you're using (your laptop, a hosted notebook, a Qollab-provided environment):

```bash
pip install qiskit qiskit-ionq qiskit-aer numpy matplotlib
export IONQ_API_KEY="your_key"            # in a notebook:  %env IONQ_API_KEY=...  (or use getpass)

python -m qdebug doctor --backend ionq                    # connectivity + bit-order check
python -m qdebug demo   --backend ionq --noise-model forte-1 --seeds 2
```

* Noise models: `forte-1`, `forte-enterprise-1` (current), `aria-1`, `aria-2` (legacy). Pass with `--noise-model`.
* The demo submits ~50 small jobs (batched per circuit). `--seeds 2` and `--bases ZX` reduce that.
* **Notebook route (hosted environments):** open `notebooks/qdebug_quickstart.ipynb`. It prompts for
  the key with `getpass`, falls back to the local simulator if you skip it, and runs the demo.
* **A different simulator provided by the hackathon platform?** Wrap any Qiskit backend:

```python
from qdebug import QiskitBackendAdapter, record, check
be = QiskitBackendAdapter(platform_backend)      # noisy counts from it, exact ideal probs computed locally
```

**IonQ-specific behaviour built in**

* Ideal probabilities come from `job.get_probabilities()` (exact; `get_counts()` on the ideal simulator
  would re-sample and add shot noise).
* Noisy runs use `backend.set_options(noise_model=..., sampler_seed=...)` for reproducible noise.
* Circuits are transpiled to `qiskit_ionq`'s accepted gate list at `optimization_level=0`.
* `doctor`/`check` cross-check IonQ's ideal output against local exact simulation and **warn on
  mismatch** (e.g. bit-ordering), so a wrong convention can't silently flip verdicts.
* Default logic tolerance is `1e-4` on IonQ (probabilities travel over the API) vs `1e-9` locally;
  override with `--logic-tol`.

---

## 5. Honest status and limitations

* **Tested:** the suite passes locally (`pytest`), including 60 repeated checks of unchanged circuits
  (5 circuits x 12 noise seeds) with zero false alarms, and the full IonQ code path against a **fake** `qiskit-ionq` provider that
  mimics its documented API (options, job shapes).
* **Not yet exercised against live IonQ**: this was built without an IonQ API key. Run
  `qdebug doctor --backend ionq` first; it is designed to expose bit-order or API surprises immediately.
* **Qollab:** I could not find public documentation of Qollab's own runtime, so the instructions above
  rely only on what is documented for IonQ + Qiskit. Check your hackathon's Qollab page for how keys or
  credits are provisioned.
* **IonQ compiles abstract-gate circuits server-side.** The padding demo (`ghz4_cx_padding`, 80 redundant
  CX) is guaranteed on the local backend; IonQ's optimizer may cancel the redundant pairs, in which case
  you'll see `REFACTOR_OK` (also a correct answer: nothing extra is executed).
* The noise budget scales hardware noise by a simple cost ratio (2q-gate-dominated); it's a heuristic.
  IonQ notes its noise models are approximations and shouldn't be used to predict specific hardware results.
* Local noise is a plain depolarizing model (`--p1/--p2`); good for development, not hardware-faithful.
* Exact local ideal simulation is limited to 24 qubits; IonQ's ideal simulator goes to 29.

## 6. Ideas for the remaining hackathon hours

1. Equivalence proof via unitary/stabilizer checks for `REFACTOR_OK` (stronger than probe bases).
2. A GitHub Action that runs `qdebug check` on PRs and comments the Markdown report.
3. Noise-aware blame: rank culprit hunks by estimated cost increase for `NOISE_REGRESSION`.
4. Parameter sweeps (variational circuits): snapshot at several parameter points.
5. Native-gate mode on IonQ to stop server-side optimization when you want to measure raw noise.

## License

MIT, see [LICENSE](LICENSE).
