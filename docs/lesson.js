/* qdebug interactive demo: guided lesson.
 *
 * Eight short steps, each of which sets up one situation and teaches one idea.
 * Every step leaves the controls live, so the "Try it" line is something the
 * reader can actually do.
 */
(function () {
  "use strict";
  const A = window.QApp, $ = A.$;

  const STEPS = [
    { title: "Every measurement is a coin flip",
      set: { circuit: "ghz3", shots: 100 },
      text: "This circuit makes a GHZ state: all three qubits read 000 or all read 111, half the time each. " +
        "Nothing has been edited. Yet the green bars (100 real shots) do not match the exact orange ones. " +
        "That mismatch is <b>shot noise</b>: you are estimating a probability from a finite number of samples.",
      tryIt: "Press “Run the shots again” a few times, then drag “Shots per run” up to 5,000 and watch the wobble shrink." },
    { title: "Real hardware adds its own noise",
      set: { circuit: "ghz3", shots: 2000, p2: 0.03 },
      text: "Now every two-qubit gate has a 3% chance of scrambling its qubits. Outcomes that the circuit can never " +
        "produce, like 001 or 110, show up anyway. This is <b>hardware noise</b>, and unlike shot noise it does not " +
        "go away when you take more shots.",
      tryIt: "Drag “Two-qubit gate error” down to 0 and back up. More shots will not remove those extra bars; less gate error will." },
    { title: "So a test needs a tolerance. That is the trap.",
      set: { circuit: "bell", drift: 0.05, viewBasis: "X" },
      text: "Since the output always wobbles, the obvious test is: run the circuit, compare with the expected " +
        "distribution, and fail if the distance is above some threshold. Here a small rotation was added by " +
        "mistake. The naive test says <b>ok</b>, because the bug is smaller than the wobble it has to tolerate.",
      tryIt: "Drag “Phase drift” from 0.05 upward. Notice how large the bug must get before the naive test finally fails." },
    { title: "Look twice: first without any noise",
      set: { circuit: "ghz3", cxFlip: true },
      text: "A simulator can do something hardware cannot: report the <b>exact</b> probabilities, with no shots and " +
        "no noise. Compare the edited circuit's exact output with the golden one (left chart). There is nothing " +
        "random in that comparison, so any gap at all is a change in logic. Here a CX had its control and target swapped.",
      tryIt: "Go back to step 3. The ideal view catches that tiny bug too, at any size." },
    { title: "Some bugs hide from the standard measurement",
      set: { circuit: "ghz3", strayZ: true, viewBasis: "Z" },
      text: "A stray Z gate flips a <b>phase</b>, not a bit. Measured the usual way (the Z basis) the exact " +
        "distributions are identical, and a test that only looks there would pass. Rotating the qubits before " +
        "measuring (the X or Y basis) turns the phase into something you can count.",
      tryIt: "Switch “Show measurement basis” from Z to X. Then tick “Probe in the Z basis only” and untick “Also compare the exact output states” to see the bug slip through." },
    { title: "Three bases are still not the whole state",
      set: { circuit: "bell_rot", sdg: true, viewBasis: "X" },
      text: "Here S was replaced by S†. Look at Z, X and Y: flat, and identical to the golden circuit in all three. " +
        "But the two states are as different as two states can be. The difference lives only in how one qubit's " +
        "result in one basis relates to the other qubit's result in a <em>different</em> basis. Comparing the exact " +
        "states directly settles it.",
      tryIt: "Untick “Also compare the exact output states”. The verdict becomes REFACTOR OK: three bases were not enough." },
    { title: "Look twice: now only at the noise",
      set: { circuit: "ghz3", pad: 40 },
      text: "This edit adds 80 gates that cancel each other exactly. The ideal view does not move, so the logic is " +
        "fine. For the second look, compare the noisy shots with the edited circuit's <b>own</b> exact output " +
        "(right chart). That gap contains no logic, only noise, and it is now well above what the golden circuit " +
        "ever showed. The naive test also fails here, but it cannot tell you the logic is intact.",
      tryIt: "Drag “Redundant CX·CX pairs” down until the verdict changes. A few extra gates stay inside the noise budget." },
    { title: "Which means you can refactor without fear",
      set: { circuit: "ghz3", hh: true },
      text: "Two H gates in a row cancel. The gate list changed, the ideal view did not, and the noise is within " +
        "budget. With the two looks separated, a harmless edit is <b>provably</b> harmless, a logic bug is caught " +
        "at any size, and a noisier circuit is reported as exactly that.",
      tryIt: "That is the whole method. Try the Challenge tab, or open the Sandbox and break a circuit yourself." },
  ];

  let step = 0;

  function go(i) {
    step = Math.max(0, Math.min(STEPS.length - 1, i));
    A.reset(Object.assign({}, STEPS[step].set, { scenario: null }));
    A.render();
  }

  function draw() {
    if (A.mode !== "lesson") return;
    const s = STEPS[step];
    $("lessonCard").innerHTML =
      `<div class="label">Lesson · step ${step + 1} of ${STEPS.length}</div>` +
      `<h2 class="big">${s.title}</h2><p>${s.text}</p>` +
      `<p class="try"><b>Try it.</b> ${s.tryIt}</p>` +
      `<div class="row nav"><button class="btn" id="prevStep"${step === 0 ? " disabled" : ""}>← Back</button>` +
      `<div class="dots" aria-hidden="true">${STEPS.map((_, i) =>
        `<i class="${i === step ? "on" : ""}"></i>`).join("")}</div>` +
      (step === STEPS.length - 1
        ? `<button class="btn primary" id="restart">Start over</button>`
        : `<button class="btn primary" id="nextStep">Next →</button>`) + `</div>`;
    const on = (id, fn) => { const e = $(id); if (e) e.addEventListener("click", fn); };
    on("prevStep", () => go(step - 1));
    on("nextStep", () => go(step + 1));
    on("restart", () => go(0));
  }

  A.onRender(draw);
  A.addMode({ id: "lesson", label: "Lesson", enter: () => go(step) });
})();
