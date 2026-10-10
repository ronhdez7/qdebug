"""Check the browser demo's JavaScript engine (docs/qdebug-demo.js) against the
Python package, so the teaching demo cannot drift from the real tool.

Needs Node.js; skipped when it is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit, transpile

from qdebug import LocalBackend
from qdebug import equiv as E
from qdebug.backends import exact_probs
from qdebug.circuits import with_probe

ENGINE = Path(__file__).resolve().parents[1] / "docs" / "qdebug-demo.js"
P1, P2 = 5e-4, 2e-2

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

CASES = [
    ("bell", {}), ("bell", {"drift": 0.05}), ("bell", {"strayZ": True}),
    ("ghz3", {}), ("ghz3", {"cxFlip": True}), ("ghz3", {"hh": True}),
    ("ghz3", {"pad": 6}), ("ghz3", {"strayZ": True, "drift": 0.3}),
    ("bell_rot", {}), ("bell_rot", {"sdg": True}),
]

SCENARIOS = [                       # (circuit, edits, options, expected verdict)
    ("ghz3", {}, {}, "PASS"),
    ("ghz3", {"hh": True}, {}, "REFACTOR_OK"),
    ("ghz3", {"cxFlip": True}, {}, "LOGIC_BUG"),
    ("ghz3", {"strayZ": True}, {}, "LOGIC_BUG"),
    ("ghz3", {"strayZ": True}, {"bases": ["Z"], "exactCheck": False}, "REFACTOR_OK"),
    ("bell", {"drift": 0.05}, {}, "LOGIC_BUG"),
    ("ghz3", {"pad": 40}, {}, "NOISE_REGRESSION"),
    ("bell_rot", {"sdg": True}, {}, "LOGIC_BUG"),
    ("bell_rot", {"sdg": True}, {"exactCheck": False}, "REFACTOR_OK"),
]

SCRIPT = """
const Q = require(process.argv[1]);
const input = JSON.parse(process.argv[2]);
const out = { cases: [], scenarios: [], unchanged: [] };
for (const [name, edits] of input.cases) {
  const n = Q.CIRCUITS[name].n, g = Q.build(name, edits);
  out.cases.push({
    signature: g.map(Q.signature),
    ideal: ["Z", "X", "Y"].map((b) => Q.idealProbs(n, g, b)),
    noisy: ["Z", "X", "Y"].map((b) => Q.noisyProbs(n, g, b, input.p1, input.p2)),
    stateDist: Q.stateDistance(n, Q.build(name, {}), g),
  });
}
for (const [name, edits, opts] of input.scenarios) {
  const n = Q.CIRCUITS[name].n;
  const snap = Q.record(n, Q.build(name, {}), opts);
  const r = Q.check(Q.build(name, edits), snap, opts);
  out.scenarios.push({ verdict: r.verdict, naiveFails: r.naiveFails });
}
for (const name of Object.keys(Q.CIRCUITS)) {
  const n = Q.CIRCUITS[name].n, g = Q.build(name, {});
  for (const shots of [200, 1000, 4000]) {
    const snap = Q.record(n, g, { shots });
    for (let seed = 1; seed <= 100; seed++) {
      const r = Q.check(g, snap, { seed });
      if (r.verdict !== "PASS") out.unchanged.push([name, shots, seed, r.verdict]);
    }
  }
}
console.log(JSON.stringify(out));
"""


def build(name: str, e: dict) -> QuantumCircuit:
    """Python twin of build() in docs/qdebug-demo.js."""
    n = {"bell": 2, "ghz3": 3, "bell_rot": 2}[name]
    qc = QuantumCircuit(n, name=name)
    qc.h(0)
    qc.cx(1, 0) if e.get("cxFlip") else qc.cx(0, 1)
    for _ in range(e.get("pad", 0)):
        qc.cx(0, 1)
        qc.cx(0, 1)
    if e.get("hh"):
        qc.h(n - 1)
        qc.h(n - 1)
    if name == "ghz3":
        qc.cx(1, 2)
    if name == "bell_rot":
        qc.h(1)
        qc.sdg(1) if e.get("sdg") else qc.s(1)
    if e.get("strayZ"):
        qc.z(0)
    if e.get("drift"):
        qc.rz(e["drift"], 0)
    return qc


def as_array(dist: dict, n: int) -> np.ndarray:
    out = np.zeros(2 ** n)
    for bits, p in dist.items():
        out[int(bits, 2)] = p
    return out


def aer_noisy_probs(probe: QuantumCircuit) -> np.ndarray:
    """Exact noisy distribution from Aer, using LocalBackend's own noise model."""
    from qiskit_aer import AerSimulator
    nm = LocalBackend(p1=P1, p2=P2)._noise_model()
    tc = transpile(probe.remove_final_measurements(inplace=False),
                   basis_gates=["rz", "sx", "x", "cx"], optimization_level=0,
                   num_processes=1)
    tc.save_probabilities()
    sim = AerSimulator(method="density_matrix", noise_model=nm)
    return np.asarray(sim.run(tc).result().data(0)["probabilities"])


@pytest.fixture(scope="module")
def js():
    payload = json.dumps({"cases": CASES, "scenarios": [s[:3] for s in SCENARIOS],
                          "p1": P1, "p2": P2})
    res = subprocess.run(["node", "-e", SCRIPT, str(ENGINE), payload],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_js_ideal_distributions_match_qiskit(js):
    for (name, edits), got in zip(CASES, js["cases"]):
        qc = build(name, edits)
        for basis, probs in zip("ZXY", got["ideal"]):
            want = as_array(exact_probs(with_probe(qc, basis)), qc.num_qubits)
            assert np.allclose(probs, want, atol=1e-12), (name, edits, basis)


def test_js_noisy_distributions_match_aer_density_matrix(js):
    for (name, edits), got in zip(CASES, js["cases"]):
        qc = build(name, edits)
        for basis, probs in zip("ZXY", got["noisy"]):
            want = aer_noisy_probs(with_probe(qc, basis))
            assert np.allclose(probs, want, atol=1e-9), (name, edits, basis)


def test_js_state_distance_and_signatures_match_python(js):
    from qdebug.circuits import instruction_signatures
    for (name, edits), got in zip(CASES, js["cases"]):
        qc = build(name, edits)
        assert got["signature"] == instruction_signatures(qc)
        assert got["stateDist"] == pytest.approx(
            E.state_distance(build(name, {}), qc), abs=1e-12)


def test_js_verdicts(js):
    for (name, edits, opts, want), got in zip(SCENARIOS, js["scenarios"]):
        assert got["verdict"] == want, (name, edits, opts)
    # the teaching points: naive test blind to a tiny bug, fooled by pure noise
    by = {(s[0], json.dumps(s[1])): g for s, g in zip(SCENARIOS, js["scenarios"]) if not s[2]}
    assert by[("bell", '{"drift": 0.05}')]["naiveFails"] is False
    assert by[("ghz3", '{"pad": 40}')]["naiveFails"] is True


def test_js_never_flags_an_unchanged_circuit(js):
    """3 circuits x 3 shot counts x 100 seeds."""
    assert js["unchanged"] == []
