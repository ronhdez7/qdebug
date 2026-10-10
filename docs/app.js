/* qdebug interactive demo: page logic.
 *
 * The maths lives in qdebug-demo.js (QDebugDemo). This file owns the shared
 * state, the two charts, the circuit diagrams and the controls, and exposes
 * window.QApp so the mode files (lesson.js, ...) can plug in.
 */
(function () {
  "use strict";
  const Q = window.QDebugDemo;
  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";

  const SCENARIOS = [
    { id: "same", label: "Nothing changed", set: { circuit: "ghz3" } },
    { id: "refactor", label: "Harmless refactor", set: { circuit: "ghz3", hh: true } },
    { id: "bug", label: "Obvious bug", set: { circuit: "ghz3", cxFlip: true } },
    { id: "phase", label: "Phase bug", set: { circuit: "ghz3", strayZ: true, viewBasis: "Z" } },
    { id: "tiny", label: "Bug smaller than shot noise", set: { circuit: "bell", drift: 0.05, viewBasis: "X" } },
    { id: "noise", label: "Only noisier", set: { circuit: "ghz3", pad: 40 } },
    { id: "hidden", label: "Hidden from Z, X and Y", set: { circuit: "bell_rot", sdg: true, viewBasis: "X" } },
  ];
  const BLANK = { circuit: "ghz3", cxFlip: false, strayZ: false, sdg: false, hh: false, drift: 0, pad: 0,
                  shots: 1000, p2: 0.005, zOnly: false, exact: true, seed: 7777, viewBasis: "Z", scenario: "same",
                  custom: null };
  let state = Object.assign({}, BLANK);
  let mode = null;
  const modes = [];            // [{id, label, enter}] in tab order
  const renderHooks = [];      // called with the computed result after each render

  const VERDICTS = {
    PASS: ["good", "✓", "PASS"],
    REFACTOR_OK: ["neutral", "✓", "REFACTOR OK"],
    LOGIC_BUG: ["critical", "✕", "LOGIC BUG"],
    NOISE_REGRESSION: ["warning", "!", "NOISE REGRESSION"],
  };

  function badge(el, [cls, icon, text]) {
    el.className = "badge " + cls;
    el.innerHTML = `<span class="dot" aria-hidden="true">${icon}</span>${text}`;
  }

  // ---------------------------------------------------------------- compute
  let snapCache = { key: null, snap: null };
  function compute() {
    // a custom circuit (from the builder) replaces the preset and its edit toggles
    const custom = state.custom;
    const n = custom ? custom.n : Q.CIRCUITS[state.circuit].n;
    const golden = custom ? custom.golden : Q.build(state.circuit, {});
    const mod = custom ? custom.mod : Q.build(state.circuit, state);
    const bases = state.zOnly ? ["Z"] : ["Z", "X", "Y"];
    const opts = { bases, shots: state.shots, p2: state.p2, exactCheck: state.exact, seed: state.seed };
    const key = [n, golden.map(Q.signature).join(";"), bases.join(""), state.shots, state.p2].join("|");
    if (snapCache.key !== key) snapCache = { key, snap: Q.record(n, golden, opts) };
    return { n, bases, snap: snapCache.snap, mod, r: Q.check(mod, snapCache.snap, opts) };
  }

  // ----------------------------------------------------------------- charts
  function el(name, attrs, parent) {
    const e = document.createElementNS(SVG, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  // Grouped columns: one group per bitstring, two series, shared 0..1 axis.
  function drawChart(host, labels, series, top) {
    const W = 480, H = 210, L = 38, Rm = 6, T = 8, B = 26;
    const pw = W - L - Rm, ph = H - T - B, k = labels.length, band = pw / k;
    const bw = Math.min(24, Math.max(4, (band - 10) / 2 - 1));
    const y = (v) => T + ph - (v / top) * ph;
    host.innerHTML = "";
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img",
      "aria-label": series.map((s) => s.name).join(" versus ") + ", probability of each measured bitstring" }, host);
    for (const g of [0, top / 2, top]) {
      el("line", { x1: L, x2: W - Rm, y1: y(g), y2: y(g), stroke: "var(--grid)", "stroke-width": 1 }, svg);
      el("text", { x: L - 6, y: y(g) + 4, "text-anchor": "end" }, svg).textContent = String(g);
    }
    labels.forEach((lab, i) => {
      const cx = L + band * (i + 0.5);
      const glow = el("rect", { x: L + band * i, y: T, width: band, height: ph, class: "glow" }, svg);
      series.forEach((s, j) => {
        const v = s.values[i], x = cx + (j === 0 ? -bw - 1 : 1), h = Math.max(0, (v / top) * ph);
        if (h < 0.5) return;
        const r = Math.min(4, h, bw / 2), y0 = T + ph, y1 = y0 - h;
        el("path", { fill: s.color, d: `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + bw - r}` +
          `Q${x + bw},${y1} ${x + bw},${y1 + r}V${y0}Z` }, svg);
      });
      if (k <= 8 || i % 2 === 0) el("text", { x: cx, y: H - 8, "text-anchor": "middle" }, svg).textContent = lab;
      const hit = el("rect", { x: L + band * i, y: T, width: band, height: ph, class: "band", tabindex: 0 }, svg);
      const show = (ev) => { glow.classList.add("on"); showTip(ev, lab, series, i, hit); };
      const hide = () => { glow.classList.remove("on"); hideTip(); };
      hit.addEventListener("pointermove", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("blur", hide);
    });
  }

  // Wire diagram, one gate per column, q0 on top. Runs of one repeated gate
  // collapse into a single glyph with a count. `marked` is a set of gate
  // indices to highlight (as "add" or "del"); `onGate(index)` makes gates clickable.
  function drawCircuit(host, n, gates, marked, kind, onGate) {
    const CW = 34, RH = 34, L = 30, T = 12;
    const cols = [];
    for (let i = 0; i < gates.length;) {
      let j = i;
      const sig = Q.signature(gates[i]);
      while (j < gates.length && Q.signature(gates[j]) === sig && marked.has(j) === marked.has(i)) j++;
      const run = j - i;
      if (run > 2) { cols.push({ g: gates[i], i, run, mark: marked.has(i) }); i = j; }
      else { cols.push({ g: gates[i], i, run: 1, mark: marked.has(i) }); i++; }
    }
    const W = L + Math.max(cols.length, 1) * CW + 12, H = T + n * RH;
    host.innerHTML = "";
    const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "circuit", role: "img",
      "aria-label": gates.length ? "Circuit: " + gates.map(Q.signature).join(", ") : "Empty circuit" }, host);
    const y = (q) => T + RH * q + RH / 2 - 4;
    for (let q = 0; q < n; q++) {
      el("text", { x: 2, y: y(q) + 4, class: "wire-label" }, svg).textContent = "q" + q;
      el("line", { x1: L - 6, x2: W - 4, y1: y(q), y2: y(q), class: "wire" }, svg);
    }
    const NAMES = { h: "H", x: "X", z: "Z", s: "S", sdg: "S\u2020", rz: "Rz" };
    cols.forEach((c, k) => {
      const x = L + CW * k + CW / 2;
      const g = el("g", { class: "gate" + (c.mark ? " " + kind : "") + (onGate ? " click" : "") }, svg);
      if (c.g.g === "cx") {
        const [a, b] = c.g.q;
        el("line", { x1: x, x2: x, y1: y(a), y2: y(b), class: "link" }, g);
        el("circle", { cx: x, cy: y(a), r: 4.5, class: "ctl" }, g);
        el("circle", { cx: x, cy: y(b), r: 9, class: "tgt" }, g);
        el("line", { x1: x - 9, x2: x + 9, y1: y(b), y2: y(b), class: "link" }, g);
        el("line", { x1: x, x2: x, y1: y(b) - 9, y2: y(b) + 9, class: "link" }, g);
      } else {
        el("rect", { x: x - 12, y: y(c.g.q[0]) - 12, width: 24, height: 24, rx: 4, class: "box" }, g);
        el("text", { x, y: y(c.g.q[0]) + 4, "text-anchor": "middle", class: "glabel" }, g).textContent = NAMES[c.g.g];
      }
      if (c.run > 1) {
        el("text", { x, y: H - 1, "text-anchor": "middle", class: "run" }, svg).textContent = "\u00d7" + c.run;
      }
      el("title", {}, g).textContent = Q.signature(c.g) + (c.run > 1 ? `  (x${c.run})` : "");
      if (onGate) {
        g.setAttribute("tabindex", 0);
        g.setAttribute("role", "button");
        g.addEventListener("click", () => onGate(c.i));
        g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onGate(c.i); } });
      }
    });
  }

  function showTip(ev, lab, series, i, hit) {
    const tip = $("tip");
    tip.innerHTML = `<strong class="mono">|${lab}⟩</strong>` + series.map((s) =>
      `<div><span><i style="background:${s.color}"></i>${s.name}</span><b>${s.values[i].toFixed(4)}</b></div>`).join("");
    tip.hidden = false;
    const box = hit.getBoundingClientRect();
    const x = ev.clientX || box.left + box.width / 2, yv = ev.clientY || box.top;
    const w = tip.offsetWidth, hgt = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, x + 14)) + "px";
    tip.style.top = Math.max(8, yv - hgt - 12) + "px";
  }
  function hideTip() { $("tip").hidden = true; }

  // ------------------------------------------------------------------ render
  function notices(c) {
    const { r, bases } = c, out = [];
    const edited = r.edited;
    if (!edited) {
      out.push("The noisy bars never match the exact ones perfectly, even with no change at all. " +
               "That gap is the baseline every test has to live with. Press “Run the shots again” and watch it wobble.");
    }
    if (r.verdict === "REFACTOR_OK") {
      out.push("The gate list changed but the ideal view did not move at all. The edit is provably harmless, " +
               "so there is nothing to investigate.");
    }
    if (r.verdict === "LOGIC_BUG" && !r.hiddenBug && r.perBasis.Z < 1e-9) {
      out.push("In the Z basis the two exact distributions are identical. Switch the chart to X: the bug is only " +
               "visible there. Phase errors hide from the standard measurement.");
    }
    if (r.verdict === "LOGIC_BUG" && !r.naiveFails) {
      out.push(`The naive test passed this bug. The change (${Q.fmt(r.logicDev)}) is smaller than the wobble from ` +
               `${state.shots.toLocaleString()} shots, so sampling can never see it. The ideal view has no sampling, so it does.`);
    }
    if (r.verdict === "LOGIC_BUG" && r.naiveFails) {
      out.push("Both tests fail here, but only the ideal view tells you why: the exact output moved, so it cannot be noise.");
    }
    if (r.hiddenBug) {
      out.push("Look at Z, X and Y in turn: all flat, all identical to the golden circuit. The two states are in fact " +
               "completely different, but only in how one qubit's X relates to the other's Z. Comparing the exact states catches it.");
    }
    if (r.verdict === "NOISE_REGRESSION") {
      out.push("The ideal view is unchanged: the extra gates cancel out exactly. The noisy view has drifted far from it. " +
               (r.naiveFails ? "The naive test reports a failure too, but it cannot tell you the logic is fine." : ""));
    }
    if (r.verdict === "REFACTOR_OK" && state.zOnly && !state.exact && (state.strayZ || state.drift || state.sdg)) {
      out.push("This edit does change the circuit, and it passed. With Z-only probing and no exact state check, " +
               "the test is blind to it. Turn either one back on.");
    } else if (r.verdict === "REFACTOR_OK" && !state.exact && state.sdg) {
      out.push("S became S† and nothing flagged it: Z, X and Y all look the same. Turn the exact state comparison back on.");
    }
    if (state.p2 === 0 && edited && state.pad > 0) {
      out.push("With the gate error at zero, redundant gates cost nothing and the noisy view is pure shot noise.");
    }
    return out;
  }

  function render() {
    const c = compute(), { r, snap, bases, n } = c;
    if (!bases.includes(state.viewBasis)) state.viewBasis = "Z";
    const bi = bases.indexOf(state.viewBasis), labels = Q.bitLabels(n);

    badge($("verdict"), VERDICTS[r.verdict]);
    $("reason").textContent = r.reason;
    badge($("naive"), r.naiveFails ? ["critical", "✕", "FAIL"] : ["good", "✓", "ok"]);
    $("naiveReason").textContent =
      `It compares the noisy shots with the golden output and fails above ${r.naiveThreshold}. ` +
      `Measured distance: ${r.naiveDev.toFixed(3)}. ` +
      (r.naiveFails ? "It cannot say whether that is a bug or noise." : "It cannot see anything smaller than its threshold.");

    const ul = $("notice");
    ul.innerHTML = "";
    const items = notices(c);
    items.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
    $("noticeCard").hidden = items.length === 0;

    // one scale for both charts, so bar heights compare across the two views
    const peak = Math.max(...snap.ideal[bi], ...r.idealMod[bi], ...r.sampled[bi]);
    const top = peak > 0.5 ? 1 : peak > 0.25 ? 0.5 : 0.25;
    drawChart($("chartIdeal"), labels, [
      { name: "Golden, exact", color: "var(--series-1)", values: snap.ideal[bi] },
      { name: "Edited, exact", color: "var(--series-2)", values: r.idealMod[bi] }], top);
    drawChart($("chartNoisy"), labels, [
      { name: "Edited, exact", color: "var(--series-2)", values: r.idealMod[bi] },
      { name: "Edited, noisy shots", color: "var(--series-3)", values: r.sampled[bi] }], top);

    $("idealStats").innerHTML = bases.map((b) => `<span>${b} basis: <b>${Q.fmt(r.perBasis[b])}</b></span>`).join("") +
      (r.stateDist === null ? "" : `<span>exact states: <b>${Q.fmt(r.stateDist < 1e-12 ? 0 : r.stateDist)}</b></span>`) +
      `<span>tolerance: <b>1e-9</b></span>`;
    const scale = Math.max(r.budget * 2, r.noiseDev * 1.1);
    $("meter").querySelector(".fill").style.width = (100 * r.noiseDev / scale) + "%";
    $("meter").querySelector(".mark").style.left = (100 * r.budget / scale) + "%";
    $("meter").setAttribute("aria-label",
      `Noise deviation ${r.noiseDev.toFixed(3)} against a budget of ${r.budget.toFixed(3)}`);
    $("noisyStats").innerHTML = `<span>noise deviation: <b>${r.noiseDev.toFixed(3)}</b></span>` +
      `<span>budget: <b>${r.budget.toFixed(3)}</b></span>` +
      `<span>shot noise alone: <b>${snap.shotMean.toFixed(3)}</b></span>` +
      `<span>noise cost: <b>${r.ratio.toFixed(1)}x</b> golden</span>`;

    $("table").innerHTML = `<table><thead><tr><th>${state.viewBasis} basis</th><th>Golden, exact</th>` +
      `<th>Edited, exact</th><th>Edited, noisy shots</th></tr></thead><tbody>` +
      labels.map((l, i) => `<tr><td class="mono">${l}</td><td>${snap.ideal[bi][i].toFixed(4)}</td>` +
        `<td>${r.idealMod[bi][i].toFixed(4)}</td><td>${r.sampled[bi][i].toFixed(4)}</td></tr>`).join("") +
      `</tbody></table>`;

    // circuit diagrams, with the edit highlighted on both sides
    const lines = Q.diff(snap.golden, c.mod), removed = new Set(), added = new Set(), outLines = [];
    let gi = 0, mi = 0;
    for (const d of lines) {
      if (d.op === "-") removed.add(gi++);
      else if (d.op === "+") added.add(mi++);
      else { gi++; mi++; }
    }
    drawCircuit($("circGolden"), n, snap.golden, removed, "del", null);
    drawCircuit($("circEdited"), n, c.mod, added, "add", api.onEditedGate);
    $("circHint").hidden = !api.onEditedGate;

    // the same edit as a gate list, with runs of identical lines collapsed
    for (let i = 0; i < lines.length;) {
      let j = i;
      while (j < lines.length && lines[j].op === lines[i].op && lines[j].text === lines[i].text) j++;
      outLines.push({ op: lines[i].op, text: lines[i].text + (j - i > 1 ? `   (x${j - i})` : "") });
      i = j;
    }
    $("diff").innerHTML = outLines.map((d) =>
      `<span class="${d.op === "-" ? "del" : d.op === "+" ? "add" : ""}">${d.op} ${d.text}</span>`).join("");

    // controls reflect state
    for (const id of ["cxFlip", "strayZ", "sdg", "hh", "zOnly", "exact"]) $(id).checked = !!state[id];
    for (const id of ["drift", "pad", "shots", "p2"]) $(id).value = state[id];
    $("circuit").value = state.circuit;
    $("presetEdits").hidden = !!state.custom;
    $("sdgRow").hidden = state.circuit !== "bell_rot";
    $("driftOut").textContent = state.drift.toFixed(2) + " rad";
    $("padOut").textContent = String(state.pad);
    $("shotsOut").textContent = state.shots.toLocaleString();
    $("p2Out").textContent = (state.p2 * 100).toFixed(1) + "%";
    $("seedOut").textContent = "run #" + (state.seed - 7776);
    document.querySelectorAll("#scenarios button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.id === state.scenario)));
    document.querySelectorAll("#viewBasis button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.b === state.viewBasis));
      b.disabled = !bases.includes(b.dataset.b);
      b.style.opacity = b.disabled ? 0.4 : 1;
    });
    document.querySelectorAll("#modes button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.id === mode)));
    document.querySelectorAll("[data-modes]").forEach((e) => {
      e.hidden = !e.dataset.modes.split(" ").includes(mode);
    });
    renderHooks.forEach((fn) => fn(c));
  }

  function setMode(id) {
    mode = id;
    const m = modes.find((x) => x.id === id);
    if (m && m.enter) m.enter();
    render();
  }

  function addMode(m) {
    modes.push(m);
    const b = document.createElement("button");
    b.className = "tab"; b.textContent = m.label; b.dataset.id = m.id;
    b.addEventListener("click", () => setMode(m.id));
    $("modes").appendChild(b);
  }

  // ------------------------------------------------------------------ wiring
  SCENARIOS.forEach((s) => {
    const b = document.createElement("button");
    b.className = "chip"; b.textContent = s.label; b.dataset.id = s.id;
    b.addEventListener("click", () => {
      api.reset(Object.assign({ shots: state.shots, p2: state.p2 }, s.set, { scenario: s.id }));
      render();
    });
    $("scenarios").appendChild(b);
  });
  ["Z", "X", "Y"].forEach((bname) => {
    const b = document.createElement("button");
    b.textContent = bname; b.dataset.b = bname;
    b.addEventListener("click", () => { state.viewBasis = bname; render(); });
    $("viewBasis").appendChild(b);
  });
  Object.keys(Q.CIRCUITS).forEach((k) => {
    const o = document.createElement("option");
    o.value = k; o.textContent = Q.CIRCUITS[k].label;
    $("circuit").appendChild(o);
  });
  const touch = (fn) => (ev) => { fn(ev.target); state.scenario = null; render(); };
  for (const id of ["cxFlip", "strayZ", "sdg", "hh", "zOnly", "exact"]) {
    $(id).addEventListener("change", touch((t) => { state[id] = t.checked; }));
  }
  for (const id of ["drift", "pad", "shots", "p2"]) {
    $(id).addEventListener("input", touch((t) => { state[id] = Number(t.value); }));
  }
  $("circuit").addEventListener("change", touch((t) => {
    state.circuit = t.value;
    state.custom = null;
    if (t.value !== "bell_rot") state.sdg = false;
  }));
  $("rerun").addEventListener("click", () => { state.seed += 1; render(); });

  const api = {
    Q, $, BLANK, addMode, setMode, render, compute,
    get state() { return state; },
    get mode() { return mode; },
    reset(values) { state = Object.assign({}, BLANK, values); },
    onRender(fn) { renderHooks.push(fn); },
    onEditedGate: null,          // set by the builder: click a gate to remove it
    badge, VERDICTS,
  };
  window.QApp = api;

  addMode({ id: "sandbox", label: "Sandbox" });
  // mode files loaded after this one register themselves; start on the first tab
  window.addEventListener("DOMContentLoaded", () => {
    const order = ["lesson", "challenge", "sandbox"];
    const tabs = Array.from($("modes").children).sort(
      (a, b) => order.indexOf(a.dataset.id) - order.indexOf(b.dataset.id));
    tabs.forEach((t) => $("modes").appendChild(t));
    setMode(tabs[0].dataset.id);
  });
})();
