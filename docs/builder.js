/* qdebug interactive demo: circuit builder (Sandbox tab).
 *
 * Lets the reader leave the presets: start from the current circuit or an
 * empty one, append gates to the edited circuit, click gates to remove them,
 * and promote the edited circuit to be the new golden one.
 */
(function () {
  "use strict";
  const A = window.QApp, $ = A.$, Q = A.Q;
  const GATES = [["h", "H"], ["x", "X"], ["z", "Z"], ["s", "S"], ["sdg", "S†"], ["rz", "Rz(θ)"], ["cx", "CX"]];
  const MAX_GATES = 120;
  const clone = (gates) => gates.map((g) => Object.assign({}, g, { q: g.q.slice() }));
  let form = { gate: "h", q: 0, t: 1, theta: 0.3 };

  function start(n, golden, mod) {
    A.state.custom = { n, golden: clone(golden), mod: clone(mod) };
    A.state.scenario = null;
    A.render();
  }

  function fromCurrent() {
    const c = A.compute();
    start(c.n, c.snap.golden, c.mod);
  }

  function addGate() {
    const cu = A.state.custom;
    if (cu.mod.length >= MAX_GATES) return;
    const q = Math.min(form.q, cu.n - 1);
    if (form.gate === "cx") {
      const t = Math.min(form.t, cu.n - 1);
      if (t === q) return;
      cu.mod.push({ g: "cx", q: [q, t] });
    } else if (form.gate === "rz") {
      cu.mod.push({ g: "rz", q: [q], t: form.theta });
    } else {
      cu.mod.push({ g: form.gate, q: [q] });
    }
    A.render();
  }

  function options(n, selected) {
    return Array.from({ length: n }, (_, i) =>
      `<option value="${i}"${i === selected ? " selected" : ""}>q${i}</option>`).join("");
  }

  function draw() {
    if (A.mode !== "sandbox") return;
    const cu = A.state.custom, card = $("builderCard");
    A.onEditedGate = cu ? (i) => { cu.mod.splice(i, 1); A.render(); } : null;
    if (!cu) {
      card.innerHTML = `<h2>Build your own</h2>` +
        `<p class="muted">Leave the presets: add and remove gates freely, and decide yourself what counts as golden.</p>` +
        `<div class="row"><button class="btn primary" id="bFrom">Customize this circuit</button>` +
        `<button class="btn" id="bEmpty">Start from an empty 2-qubit circuit</button></div>`;
      $("bFrom").addEventListener("click", fromCurrent);
      $("bEmpty").addEventListener("click", () => start(2, [], []));
      return;
    }
    if (form.q >= cu.n) form.q = 0;
    if (form.t >= cu.n || form.t === form.q) form.t = (form.q + 1) % Math.max(cu.n, 1);
    const two = form.gate === "cx", rot = form.gate === "rz", full = cu.mod.length >= MAX_GATES;
    card.innerHTML = `<h2>Build your own</h2>` +
      `<p class="muted">Gates are appended to the end of the <b>edited</b> circuit. Click a gate in its diagram above to remove it.</p>` +
      `<div class="row builder">` +
      `<label>Qubits <select id="bN">${[1, 2, 3, 4].map((k) =>
        `<option${k === cu.n ? " selected" : ""}>${k}</option>`).join("")}</select></label>` +
      `<label>Gate <select id="bGate">${GATES.filter(([g]) => g !== "cx" || cu.n > 1).map(([g, name]) =>
        `<option value="${g}"${g === form.gate ? " selected" : ""}>${name}</option>`).join("")}</select></label>` +
      `<label>${two ? "Control" : "On"} <select id="bQ">${options(cu.n, form.q)}</select></label>` +
      (two ? `<label>Target <select id="bT">${options(cu.n, form.t)}</select></label>` : "") +
      (rot ? `<label>θ (rad) <input id="bTheta" type="number" step="0.05" value="${form.theta}"></label>` : "") +
      `<button class="btn primary" id="bAdd"${full ? " disabled" : ""}>Add gate</button></div>` +
      `<div class="row"><button class="btn" id="bPromote">Use the edited circuit as the new golden</button>` +
      `<button class="btn" id="bRevert">Reset edited to golden</button>` +
      `<button class="btn" id="bClear">Clear both</button>` +
      `<button class="btn" id="bLeave">Back to presets</button></div>` +
      `<p class="muted">${cu.golden.length} gates in golden, ${cu.mod.length} in edited` +
      `${full ? ` (limit of ${MAX_GATES} reached)` : ""}. Changing the number of qubits clears both circuits.</p>`;

    const on = (id, ev, fn) => { const e = $(id); if (e) e.addEventListener(ev, fn); };
    on("bN", "change", (e) => start(Number(e.target.value), [], []));
    on("bGate", "change", (e) => { form.gate = e.target.value; A.render(); });
    on("bQ", "change", (e) => { form.q = Number(e.target.value); A.render(); });
    on("bT", "change", (e) => { form.t = Number(e.target.value); A.render(); });
    on("bTheta", "change", (e) => { form.theta = Number(e.target.value) || 0; });
    on("bAdd", "click", addGate);
    on("bPromote", "click", () => { cu.golden = clone(cu.mod); A.render(); });
    on("bRevert", "click", () => { cu.mod = clone(cu.golden); A.render(); });
    on("bClear", "click", () => start(cu.n, [], []));
    on("bLeave", "click", () => { A.state.custom = null; A.render(); });
  }

  A.onRender(draw);
})();
