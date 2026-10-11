/* qdebug interactive demo: challenge mode.
 *
 * Shows an edited circuit and the two views with the verdict hidden, and asks
 * the reader to make the call. The right answer is whatever the engine's check
 * returns for that exact situation, so the quiz cannot disagree with the tool.
 */
(function () {
  "use strict";
  const A = window.QApp, $ = A.$;
  const pick = (xs) => xs[Math.floor(Math.random() * xs.length)];
  const between = (lo, hi) => lo + Math.floor(Math.random() * (hi - lo + 1));

  // Each template returns the state for one challenge. Several are traps.
  const TEMPLATES = [
    () => ({ circuit: pick(["ghz3", "bell"]), shots: pick([300, 1000, 3000]) }),
    () => ({ circuit: pick(["ghz3", "bell"]), hh: true }),
    () => ({ circuit: pick(["ghz3", "bell"]), cxFlip: true }),
    () => ({ circuit: pick(["ghz3", "bell"]), strayZ: true }),
    () => ({ circuit: "bell", drift: between(3, 15) / 100 }),
    () => ({ circuit: "ghz3", pad: between(32, 40) }),
    () => ({ circuit: "ghz3", pad: between(1, 3) }),
    () => ({ circuit: "bell_rot", sdg: true }),
    () => ({ circuit: "ghz3", hh: true, pad: between(34, 40) }),
    () => ({ circuit: "ghz3", strayZ: true, pad: between(30, 40) }),
  ];

  const WHY = {
    PASS: "Nothing was edited. The gap in the noisy view is ordinary shot and hardware noise, inside the budget.",
    REFACTOR_OK: "The circuit was edited, but the ideal view is identical and the noisy view is inside its budget.",
    LOGIC_BUG: "The exact output changed. That decides it on its own, whatever the noisy view looks like.",
    NOISE_REGRESSION: "The ideal view is identical, so the logic is fine. The noisy view is past its budget.",
  };
  const ORDER = ["PASS", "REFACTOR_OK", "LOGIC_BUG", "NOISE_REGRESSION"];

  let last = -1, answered = null, asked = 0, right = 0;

  function next() {
    let i;
    do { i = Math.floor(Math.random() * TEMPLATES.length); } while (i === last);
    last = i;
    answered = null;
    A.reset(Object.assign({ scenario: null, seed: 7777 + between(0, 500), viewBasis: "Z" }, TEMPLATES[i]()));
    A.render();
  }

  function answer(choice, truth) {
    answered = choice;
    asked += 1;
    if (choice === truth) right += 1;
    A.render();
  }

  function draw(c) {
    const on = A.mode === "challenge";
    document.body.classList.toggle("concealed", on && answered === null);
    if (!on) return;
    const truth = c.r.verdict;
    const head = `<div class="label">Challenge · score ${right} of ${asked}</div>`;
    const card = $("challengeCard");
    if (answered === null) {
      card.innerHTML = head +
        `<h2 class="big">What should the test report?</h2>` +
        `<p>Compare the golden and edited circuits, then read the two views. Switch between the Z, X and Y ` +
        `bases before you decide: some changes only show up in one of them.</p>` +
        `<div class="steps" id="answers">${ORDER.map((v) =>
          `<button class="chip" data-v="${v}">${A.VERDICTS[v][2]}</button>`).join("")}</div>`;
      card.querySelectorAll("#answers button").forEach((b) =>
        b.addEventListener("click", () => answer(b.dataset.v, truth)));
    } else {
      const ok = answered === truth;
      card.innerHTML = head +
        `<h2 class="big">${ok ? "Correct." : "Not quite."}</h2>` +
        `<p>${ok ? "" : `You said ${A.VERDICTS[answered][2]}. `}The answer is <b>${A.VERDICTS[truth][2]}</b>. ${WHY[truth]}</p>` +
        `<p class="muted">The naive shot test would have said ${c.r.naiveFails ? "FAIL" : "ok"} here` +
        `${(truth === "LOGIC_BUG") !== c.r.naiveFails ? ", which is the wrong call" : ""}. ` +
        `The controls below are unlocked if you want to poke at this one.</p>` +
        `<div class="row"><button class="btn primary" id="nextChallenge">Next challenge →</button></div>`;
      $("nextChallenge").addEventListener("click", next);
    }
  }

  A.onRender(draw);
  A.addMode({ id: "challenge", label: "Challenge", enter: next });
})();
