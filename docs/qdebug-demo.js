/* qdebug interactive demo engine.
 *
 * A small, dependency-free re-implementation of the qdebug idea for circuits of
 * up to 4 qubits, so the teaching demo runs in a browser with nothing installed.
 * It mirrors the Python package (qdebug/checker.py, snapshot.py, stats.py):
 * same probe bases, same noise budget formula, same verdict rules. The Python
 * package is the real tool; tests/test_web_demo.py checks this file against it.
 *
 * Works as a plain <script> (exposes window.QDebugDemo) and under Node.
 */
(function (root) {
  "use strict";

  // ------------------------------------------------------------ gate tables
  const R = Math.SQRT1_2;
  const GATES = {
    h: [[R, 0], [R, 0], [R, 0], [-R, 0]],
    x: [[0, 0], [1, 0], [1, 0], [0, 0]],
    z: [[1, 0], [0, 0], [0, 0], [-1, 0]],
    s: [[1, 0], [0, 0], [0, 0], [0, 1]],
    sdg: [[1, 0], [0, 0], [0, 0], [0, -1]],
  };
  // Which gates cost noise. Matches the Python LocalBackend after transpiling to
  // {rz, sx, x, cx}: H is one sx, X is one x, and Z / S / S-dagger / RZ are
  // virtual (free) rz rotations.
  const NOISY_1Q = { h: true, x: true };

  function matrixFor(gate) {
    if (gate.g === "rz") {
      const c = Math.cos(gate.t / 2), s = Math.sin(gate.t / 2);
      return [[c, -s], [0, 0], [0, 0], [c, s]];
    }
    return GATES[gate.g];
  }

  function conj(m) {
    return m.map(([re, im]) => [re, -im]);
  }

  // Apply a 2x2 matrix to bit `q` of a complex vector (re, im arrays).
  function apply1(re, im, q, m) {
    const bit = 1 << q, n = re.length;
    const [a, b, c, d] = m;
    for (let i = 0; i < n; i++) {
      if (i & bit) continue;
      const j = i | bit;
      const xr = re[i], xi = im[i], yr = re[j], yi = im[j];
      re[i] = a[0] * xr - a[1] * xi + b[0] * yr - b[1] * yi;
      im[i] = a[0] * xi + a[1] * xr + b[0] * yi + b[1] * yr;
      re[j] = c[0] * xr - c[1] * xi + d[0] * yr - d[1] * yi;
      im[j] = c[0] * xi + c[1] * xr + d[0] * yi + d[1] * yr;
    }
  }

  function applyCX(re, im, ctl, tgt) {
    const cb = 1 << ctl, tb = 1 << tgt, n = re.length;
    for (let i = 0; i < n; i++) {
      if ((i & cb) && !(i & tb)) {
        const j = i | tb;
        let t = re[i]; re[i] = re[j]; re[j] = t;
        t = im[i]; im[i] = im[j]; im[j] = t;
      }
    }
  }

  // Basis-change gates appended before a Z measurement of every qubit.
  function probeGates(n, basis) {
    const out = [];
    for (let q = 0; q < n; q++) {
      if (basis === "X") out.push({ g: "h", q: [q] });
      else if (basis === "Y") out.push({ g: "sdg", q: [q] }, { g: "h", q: [q] });
    }
    return out;
  }

  // ------------------------------------------------------- ideal simulation
  function statevector(n, gates) {
    const dim = 1 << n;
    const re = new Float64Array(dim), im = new Float64Array(dim);
    re[0] = 1;
    for (const g of gates) {
      if (g.g === "cx") applyCX(re, im, g.q[0], g.q[1]);
      else apply1(re, im, g.q[0], matrixFor(g));
    }
    return { re, im };
  }

  // Exact output distribution; index i is the bitstring with qubit 0 as the
  // least significant bit (Qiskit's little-endian convention).
  function idealProbs(n, gates, basis) {
    const { re, im } = statevector(n, gates.concat(probeGates(n, basis)));
    return Array.from(re, (r, i) => r * r + im[i] * im[i]);
  }

  // Trace distance between output states, global phase removed, computed
  // without cancellation (see qdebug/equiv.py).
  function stateDistance(n, a, b) {
    const A = statevector(n, a), B = statevector(n, b);
    let ovr = 0, ovi = 0;
    for (let i = 0; i < A.re.length; i++) {      // <B|A>
      ovr += B.re[i] * A.re[i] + B.im[i] * A.im[i];
      ovi += B.re[i] * A.im[i] - B.im[i] * A.re[i];
    }
    const mag = Math.min(Math.hypot(ovr, ovi), 1);
    const pr = mag > 1e-12 ? ovr / mag : 1, pi = mag > 1e-12 ? ovi / mag : 0;
    let d2 = 0;
    for (let i = 0; i < A.re.length; i++) {
      const dr = A.re[i] - (pr * B.re[i] - pi * B.im[i]);
      const di = A.im[i] - (pr * B.im[i] + pi * B.re[i]);
      d2 += dr * dr + di * di;
    }
    return Math.min(1, Math.sqrt(d2) * Math.sqrt((1 + mag) / 2));
  }

  // ------------------------------------------------------- noisy simulation
  // The density matrix is stored as a vector over 2n bits: row bits 0..n-1,
  // column bits n..2n-1. A gate U acts as U on the row bit and conj(U) on the
  // column bit.
  function depolarize(re, im, n, qubits, p) {
    if (p <= 0) return;
    const dim = 1 << n, k = qubits.length, combos = 1 << k;
    let mask = 0;
    for (const q of qubits) mask |= 1 << q;
    const outRe = new Float64Array(re.length), outIm = new Float64Array(im.length);
    const spread = (x) => {                      // place k bits of x on `qubits`
      let v = 0;
      for (let b = 0; b < k; b++) if (x & (1 << b)) v |= 1 << qubits[b];
      return v;
    };
    for (let r = 0; r < dim; r++) {
      for (let c = 0; c < dim; c++) {
        const idx = r | (c << n);
        let vr = (1 - p) * re[idx], vi = (1 - p) * im[idx];
        if ((r & mask) === (c & mask)) {         // I/2^k (x) partial trace
          const rb = r & ~mask, cb = c & ~mask;
          let sr = 0, si = 0;
          for (let x = 0; x < combos; x++) {
            const s = spread(x), j = (rb | s) | ((cb | s) << n);
            sr += re[j]; si += im[j];
          }
          vr += (p / combos) * sr; vi += (p / combos) * si;
        }
        outRe[idx] = vr; outIm[idx] = vi;
      }
    }
    re.set(outRe); im.set(outIm);
  }

  // Exact output distribution under depolarizing noise (p1 after each noisy
  // 1-qubit gate, p2 after each CX).
  function noisyProbs(n, gates, basis, p1, p2) {
    const dim = 1 << n;
    const re = new Float64Array(dim * dim), im = new Float64Array(dim * dim);
    re[0] = 1;
    for (const g of gates.concat(probeGates(n, basis))) {
      if (g.g === "cx") {
        applyCX(re, im, g.q[0], g.q[1]);
        applyCX(re, im, g.q[0] + n, g.q[1] + n);
        depolarize(re, im, n, g.q, p2);
      } else {
        const m = matrixFor(g);
        apply1(re, im, g.q[0], m);
        apply1(re, im, g.q[0] + n, conj(m));
        if (NOISY_1Q[g.g]) depolarize(re, im, n, g.q, p1);
      }
    }
    const out = new Array(dim);
    for (let i = 0; i < dim; i++) out[i] = Math.max(0, re[i | (i << n)]);
    return out;
  }

  // ------------------------------------------------------------- statistics
  function rng(seed) {                            // mulberry32
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Draw `shots` samples from `probs`; returns observed frequencies.
  function sample(probs, shots, rand) {
    const cum = [];
    let acc = 0;
    for (const p of probs) { acc += p; cum.push(acc); }
    const counts = new Array(probs.length).fill(0);
    for (let s = 0; s < shots; s++) {
      const u = rand() * acc;
      let lo = 0, hi = cum.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (u < cum[mid]) hi = mid; else lo = mid + 1;
      }
      counts[lo]++;
    }
    return counts.map((c) => c / shots);
  }

  function tvd(p, q) {
    let s = 0;
    for (let i = 0; i < p.length; i++) s += Math.abs(p[i] - q[i]);
    return s / 2;
  }

  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;

  function std(xs) {
    if (xs.length < 2) return 0;
    const m = mean(xs);
    return Math.sqrt(xs.reduce((a, x) => a + (x - m) * (x - m), 0) / (xs.length - 1));
  }

  function quantile(xs, q) {                      // linear interpolation, like numpy
    const s = xs.slice().sort((a, b) => a - b);
    const pos = (s.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
    return s[lo] + (s[hi] - s[lo]) * (pos - lo);
  }

  // ------------------------------------------------------ circuits and cost
  function signature(g) {
    const qs = g.q.map((q) => "q" + q).join(",");
    return g.g === "rz" ? `rz(${+g.t.toPrecision(6)}) ${qs}` : `${g.g} ${qs}`;
  }

  function noiseCost(gates) {                     // 2-qubit gates dominate
    let c = 0;
    for (const g of gates) c += g.q.length === 2 ? 1 : 0.05;
    return c;
  }

  // Line diff of two gate lists (LCS). Returns [{op: " " | "-" | "+", text}].
  function diff(a, b) {
    const A = a.map(signature), B = b.map(signature);
    const L = Array.from({ length: A.length + 1 }, () => new Array(B.length + 1).fill(0));
    for (let i = A.length - 1; i >= 0; i--) {
      for (let j = B.length - 1; j >= 0; j--) {
        L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
      }
    }
    const out = [];
    let i = 0, j = 0;
    while (i < A.length && j < B.length) {
      if (A[i] === B[j]) { out.push({ op: " ", text: A[i] }); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) out.push({ op: "-", text: A[i++] });
      else out.push({ op: "+", text: B[j++] });
    }
    while (i < A.length) out.push({ op: "-", text: A[i++] });
    while (j < B.length) out.push({ op: "+", text: B[j++] });
    return out;
  }

  // The demo circuits. `edits` switches on deliberate changes:
  //   cxFlip  swap control/target of the first CX          (logic bug)
  //   strayZ  Z on q0 before readout                        (phase bug)
  //   drift   RZ(drift) on q0 before readout                (small logic bug)
  //   sdg     S -> S-dagger (rotated Bell only)             (hidden from Z, X, Y)
  //   hh      a cancelling H-H pair on the last qubit       (harmless refactor)
  //   pad     that many cancelling CX-CX pairs              (noise only)
  const CIRCUITS = {
    bell: { label: "Bell pair", n: 2 },
    ghz3: { label: "GHZ state (3 qubits)", n: 3 },
    bell_rot: { label: "Rotated Bell pair", n: 2 },
  };

  function build(name, edits) {
    const e = edits || {};
    const n = CIRCUITS[name].n, last = n - 1;
    const g = [{ g: "h", q: [0] }];
    g.push({ g: "cx", q: e.cxFlip ? [1, 0] : [0, 1] });
    for (let i = 0; i < (e.pad || 0); i++) g.push({ g: "cx", q: [0, 1] }, { g: "cx", q: [0, 1] });
    if (e.hh) g.push({ g: "h", q: [last] }, { g: "h", q: [last] });
    if (name === "ghz3") g.push({ g: "cx", q: [1, 2] });
    if (name === "bell_rot") g.push({ g: "h", q: [1] }, { g: e.sdg ? "sdg" : "s", q: [1] });
    if (e.strayZ) g.push({ g: "z", q: [0] });
    if (e.drift) g.push({ g: "rz", q: [0], t: e.drift });
    return g;
  }

  // ------------------------------------------------------- record and check
  const DEFAULTS = {
    bases: ["Z", "X", "Y"], shots: 1000, nSeeds: 4, baseSeed: 1000,
    p1: 5e-4, p2: 5e-3, logicTol: 1e-9, exactTol: 1e-9, noiseMargin: 1.25,
    naiveThreshold: 0.05, exactCheck: true, seed: 7777,
  };

  function noiseScore(sampled, ideal) {
    return mean(sampled.map((s, i) => tvd(s, ideal[i])));
  }

  // Golden snapshot: exact ideal distributions + a seeded noise baseline.
  function record(n, golden, options) {
    const o = Object.assign({}, DEFAULTS, options);
    const ideal = o.bases.map((b) => idealProbs(n, golden, b));
    const noisy = o.bases.map((b) => noisyProbs(n, golden, b, o.p1, o.p2));
    const scores = [];
    for (let s = 0; s < o.nSeeds; s++) {
      const rand = rng(o.baseSeed + s);
      scores.push(noiseScore(noisy.map((p) => sample(p, o.shots, rand)), ideal));
    }
    // what finite sampling alone does, with no hardware noise at all
    const rand = rng(99);
    const shotOnly = ideal.map((p) => {
      const vals = [];
      for (let t = 0; t < 400; t++) vals.push(tvd(sample(p, o.shots, rand), p));
      return vals;
    });
    return {
      n, golden, bases: o.bases, shots: o.shots, ideal,
      mean: mean(scores), std: std(scores), scores,
      shotMean: mean(shotOnly.map(mean)),
      shotFloor: mean(shotOnly.map((v) => quantile(v, 0.99))),
    };
  }

  function noiseBudget(snap, scale, margin) {
    const gatePart = Math.max(snap.mean - snap.shotMean, 0);
    return margin * (snap.shotFloor + (gatePart + 3 * snap.std) * scale);
  }

  function check(mod, snap, options) {
    const o = Object.assign({}, DEFAULTS, options);
    const { n, golden, bases } = snap;
    const edited = diff(golden, mod).some((d) => d.op !== " ");

    // 1. ideal view: exact distributions, edited vs golden
    const idealMod = bases.map((b) => idealProbs(n, mod, b));
    const perBasis = {};
    bases.forEach((b, i) => { perBasis[b] = tvd(idealMod[i], snap.ideal[i]); });
    const probeDev = Math.max(...Object.values(perBasis));
    const stateDist = o.exactCheck && edited ? stateDistance(n, golden, mod) : null;
    const hiddenBug = stateDist !== null && stateDist > o.exactTol && probeDev <= o.logicTol;
    const logicDev = hiddenBug ? stateDist : probeDev;

    // 2. noisy view: sampled counts vs the edited circuit's OWN ideal
    const rand = rng(o.seed);
    const noisyExact = bases.map((b) => noisyProbs(n, mod, b, o.p1, o.p2));
    const sampled = noisyExact.map((p) => sample(p, snap.shots, rand));
    const noiseDev = noiseScore(sampled, idealMod);
    const naiveDev = noiseScore(sampled, snap.ideal);
    const costGolden = noiseCost(golden), costMod = noiseCost(mod);
    const ratio = costGolden > 0 ? costMod / costGolden : 1;
    const budget = noiseBudget(snap, 1, o.noiseMargin);

    // 3. verdict
    let verdict, reason;
    if (probeDev > o.logicTol || hiddenBug) {
      verdict = "LOGIC_BUG";
      if (hiddenBug) {
        reason = `The output state moved (trace distance ${fmt(stateDist)}), yet every ` +
          `probed basis (${bases.join(", ")}) shows the same distribution. Only the exact ` +
          "state comparison sees it. This is a logic change, not noise.";
      } else {
        const worst = bases.reduce((a, b) => (perBasis[b] > perBasis[a] ? b : a));
        reason = `The ideal (noise-free) output moved by TVD ${fmt(probeDev)} in the ` +
          `${worst} basis. No noise is involved in that comparison, so this is a logic change.`;
      }
    } else if (noiseDev > budget) {
      verdict = "NOISE_REGRESSION";
      reason = `The logic is unchanged (ideal TVD ${fmt(probeDev)}), but the sampled output ` +
        `sits ${noiseDev.toFixed(3)} from the circuit's own ideal, above the budget of ` +
        `${budget.toFixed(3)}. Noise cost is ${ratio.toFixed(1)}x the golden circuit's.`;
    } else if (edited) {
      verdict = "REFACTOR_OK";
      reason = "The circuit was edited, the ideal output is identical, and the noise is " +
        "within the baseline. A safe change.";
    } else {
      verdict = "PASS";
      reason = "Identical circuit, identical behaviour, noise at the baseline.";
    }

    return {
      verdict, reason, edited, perBasis, probeDev, logicDev, stateDist, hiddenBug,
      idealMod, sampled, noiseDev, naiveDev, budget, ratio,
      naiveFails: naiveDev > o.naiveThreshold, naiveThreshold: o.naiveThreshold,
    };
  }

  function fmt(x) {
    if (x === 0) return "0";
    return Math.abs(x) < 0.001 ? x.toExponential(1) : String(+x.toPrecision(3));
  }

  function bitLabels(n) {
    return Array.from({ length: 1 << n }, (_, i) => i.toString(2).padStart(n, "0"));
  }

  const api = {
    CIRCUITS, DEFAULTS, build, signature, diff, idealProbs, noisyProbs, stateDistance,
    record, check, noiseBudget, noiseCost, tvd, sample, rng, bitLabels, fmt,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.QDebugDemo = api;
})(typeof window !== "undefined" ? window : globalThis);
