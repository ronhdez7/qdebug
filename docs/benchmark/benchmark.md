# qdebug mutation benchmark

757 random single-edit mutants of 6 circuits (bell, bell_rot, ghz4, qft3, grover3, bv), 1000 shots, backend `{'backend': 'local-aer', 'noise': 'depolarizing', 'p1': 0.0005, 'p2': 0.005, 'readout': 0.0}`, seed 2026. Reproduce with `python -m qdebug benchmark`.

Ground truth: 440 mutants changed the output state, 317 did not.

## Logic changes caught

| size of the change (trace distance) | mutants | naive shot test | exact, Z only | exact, Z/X/Y | qdebug |
|---|---|---|---|---|---|
| all | 440 | 75% | 62% | 94% | 100% |
| < 0.02 | 90 | 37% | 46% | 100% | 100% |
| 0.02 to 0.2 | 47 | 43% | 43% | 100% | 100% |
| > 0.2 | 303 | 91% | 70% | 91% | 100% |

qdebug's 100% is true by construction on circuits this small: it compares the output states exactly, which is also how the ground truth is defined. The measurement here is how much the other tests miss.

## Edits that changed nothing

317 mutants left the output state untouched. Flagged as a failure by the naive test: 25%. Called LOGIC_BUG by qdebug: 0%. Called NOISE_REGRESSION by qdebug: 0%.

## The naive test cannot be tuned out of the problem

| threshold | logic changes missed | equivalent edits flagged |
|---|---|---|
| 0.02 | 6% | 89% |
| 0.05 | 25% | 25% |
| 0.1 | 38% | 0% |

## By kind of edit

| edit | mutants | changed the logic | naive | exact, Z only | exact, Z/X/Y | qdebug |
|---|---|---|---|---|---|---|
| delete gate | 53 | 51 | 100% | 82% | 100% | 100% |
| swap control/target | 12 | 8 | 100% | 88% | 100% | 100% |
| wrong qubit | 75 | 69 | 99% | 83% | 99% | 100% |
| wrong gate | 43 | 43 | 98% | 91% | 98% | 100% |
| angle error | 105 | 67 | 39% | 48% | 100% | 100% |
| stray rotation | 121 | 70 | 39% | 41% | 100% | 100% |
| stray Pauli | 159 | 132 | 80% | 51% | 80% | 100% |
| cancelling pair | 162 | 0 | - | - | - | - |
| reorder disjoint gates | 27 | 0 | - | - | - | - |

## Noise only: redundant CX pairs

Each circuit padded with cancelling CX-CX pairs, 20 noisy runs per level. The logic is unchanged at every level, so LOGIC_BUG would be wrong and the naive test's FAIL carries no information about why.

| added CX pairs | runs | qdebug: NOISE_REGRESSION | qdebug: LOGIC_BUG | naive: FAIL |
|---|---|---|---|---|
| 0 | 120 | 0% | 0% | 24% |
| 1 | 120 | 0% | 0% | 28% |
| 2 | 120 | 0% | 0% | 32% |
| 4 | 120 | 0% | 0% | 36% |
| 8 | 120 | 0% | 0% | 58% |
| 16 | 120 | 21% | 0% | 83% |
| 32 | 120 | 83% | 0% | 83% |

## By circuit

Averages hide where a method fails, so here is the same data per circuit.

| circuit | logic changes | missed by exact Z/X/Y probes | NOISE_REGRESSION at 32 pairs |
|---|---|---|---|
| bell | 29 | 0 | 100% |
| bell_rot | 58 | 28 | 0% |
| ghz4 | 78 | 0 | 100% |
| qft3 | 72 | 0 | 100% |
| grover3 | 111 | 0 | 100% |
| bv | 92 | 0 | 100% |

`bell_rot` was built so that its output is uniform in the all-Z, all-X and all-Y bases. That explains both of its rows: probes in those bases cannot see changes to it, and depolarizing noise (which pushes towards uniform) cannot be seen in them either. The exact state comparison fixes the first; the second is an open limitation of the noise check for circuits like this.
