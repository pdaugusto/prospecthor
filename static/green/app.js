const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const reveals = document.querySelectorAll(".reveal");
if (reduceMotion || !("IntersectionObserver" in window)) {
  reveals.forEach((element) => element.classList.add("is-visible"));
} else {
  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      revealObserver.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -7% 0px" });
  reveals.forEach((element) => revealObserver.observe(element));
}

const progress = document.querySelector(".scroll-progress i");
const product = document.querySelector("[data-tilt]");
const stage = document.querySelector("[data-score-stage]");
const journey = document.querySelector("[data-journey]");
const journeyTrack = document.querySelector("[data-journey-track]");
const journeySteps = [...document.querySelectorAll("[data-journey-step]")];
const journeyBar = document.querySelector("[data-journey-progress]");
const journeyCurrent = document.querySelector("[data-journey-current]");
let journeyActiveIndex = 0;
let journeyTargetShift = 0;
let journeyRenderedShift = 0;
let journeyTargetProgress = 0;
let journeyRenderedProgress = 0;
let journeyMotionFrame = 0;
let stageTargetY = 0;
let stageRenderedY = 0;

function renderJourneyMotion() {
  const shiftDelta = journeyTargetShift - journeyRenderedShift;
  const progressDelta = journeyTargetProgress - journeyRenderedProgress;
  const stageDelta = stageTargetY - stageRenderedY;
  journeyRenderedShift += shiftDelta * .115;
  journeyRenderedProgress += progressDelta * .115;
  stageRenderedY += stageDelta * .075;
  journey?.style.setProperty("--journey-shift", `${journeyRenderedShift}px`);
  if (journeyBar) journeyBar.style.transform = `scaleX(${journeyRenderedProgress})`;
  if (stage) stage.style.transform = `translateY(${stageRenderedY}px)`;
  if (Math.abs(shiftDelta) > .25 || Math.abs(progressDelta) > .001 || Math.abs(stageDelta) > .05) {
    journeyMotionFrame = requestAnimationFrame(renderJourneyMotion);
  } else {
    journeyRenderedShift = journeyTargetShift;
    journeyRenderedProgress = journeyTargetProgress;
    stageRenderedY = stageTargetY;
    journey?.style.setProperty("--journey-shift", `${journeyRenderedShift}px`);
    if (journeyBar) journeyBar.style.transform = `scaleX(${journeyRenderedProgress})`;
    if (stage) stage.style.transform = `translateY(${stageRenderedY}px)`;
    journeyMotionFrame = 0;
  }
}

function paintScroll() {
  const scrollable = document.documentElement.scrollHeight - window.innerHeight;
  const value = scrollable > 0 ? window.scrollY / scrollable : 0;
  progress.style.transform = `scaleX(${value})`;

  if (!reduceMotion && product) {
    const rect = product.getBoundingClientRect();
    const offset = Math.max(-1, Math.min(1, (window.innerHeight * 0.5 - rect.top) / window.innerHeight));
    product.style.setProperty("--parallax", `${offset * -16}px`);
  }

  if (!reduceMotion && stage) {
    const rect = stage.getBoundingClientRect();
    const offset = Math.max(-1, Math.min(1, (window.innerHeight * 0.55 - rect.top) / window.innerHeight));
    stageTargetY = offset * -6;
    if (!journeyMotionFrame) journeyMotionFrame = requestAnimationFrame(renderJourneyMotion);
  }

  if (journey && journeyTrack && journeyBar && !reduceMotion && window.innerWidth > 760) {
    const rect = journey.getBoundingClientRect();
    const range = Math.max(1, journey.offsetHeight - window.innerHeight);
    const journeyProgress = Math.max(0, Math.min(1, (78 - rect.top) / range));
    const sideInset = Math.max(20, (window.innerWidth - 1280) / 2);
    const travel = Math.max(0, journeyTrack.scrollWidth - window.innerWidth + sideInset);
    journeyTargetShift = journeyProgress * -travel;
    journeyTargetProgress = journeyProgress;
    if (!journeyMotionFrame) journeyMotionFrame = requestAnimationFrame(renderJourneyMotion);

    const nextIndex = Math.min(journeySteps.length - 1, Math.round(journeyProgress * (journeySteps.length - 1)));
    if (nextIndex !== journeyActiveIndex) {
      journeyActiveIndex = nextIndex;
      journeySteps.forEach((step, index) => {
        const active = index === nextIndex;
        step.classList.toggle("is-active", active);
        if (active) step.setAttribute("aria-current", "step");
        else step.removeAttribute("aria-current");
      });
      if (journeyCurrent) journeyCurrent.textContent = String(nextIndex + 1).padStart(2, "0");
    }
  }
}

let scrollTicking = false;
window.addEventListener("scroll", () => {
  if (scrollTicking) return;
  scrollTicking = true;
  requestAnimationFrame(() => {
    paintScroll();
    scrollTicking = false;
  });
}, { passive: true });
paintScroll();
window.addEventListener("resize", paintScroll, { passive: true });

if (product && !reduceMotion && matchMedia("(pointer:fine)").matches) {
  product.addEventListener("pointermove", (event) => {
    const rect = product.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width - 0.5;
    const y = (event.clientY - rect.top) / rect.height - 0.5;
    product.style.setProperty("--tilt-x", `${x * 7}deg`);
    product.style.setProperty("--tilt-y", `${y * -6}deg`);
    product.style.setProperty("--float-x", `${x * 12}px`);
    product.style.setProperty("--float-y", `${y * 12}px`);
  });
  product.addEventListener("pointerleave", () => {
    product.style.setProperty("--tilt-x", "-3deg");
    product.style.setProperty("--tilt-y", "2deg");
    product.style.setProperty("--float-x", "0px");
    product.style.setProperty("--float-y", "0px");
  });
}

document.querySelectorAll(".spotlight").forEach((card) => {
  card.addEventListener("pointermove", (event) => {
    const rect = card.getBoundingClientRect();
    card.style.setProperty("--spot-x", `${event.clientX - rect.left}px`);
    card.style.setProperty("--spot-y", `${event.clientY - rect.top}px`);
  });
});

const leadRows = document.querySelectorAll("[data-score]");
const liveScore = document.querySelector("[data-live-score]");
leadRows.forEach((row) => {
  row.addEventListener("click", () => {
    leadRows.forEach((item) => item.classList.toggle("is-active", item === row));
    liveScore.textContent = row.dataset.score;
  });
});

const toggles = document.querySelectorAll("[data-plan-toggle]");
const planSets = document.querySelectorAll("[data-plan-set]");
toggles.forEach((toggle) => {
  toggle.addEventListener("click", () => {
    toggles.forEach((item) => {
      item.classList.toggle("is-active", item === toggle);
      item.setAttribute("aria-pressed", String(item === toggle));
    });
    planSets.forEach((set) => set.classList.toggle("is-active", set.dataset.planSet === toggle.dataset.planToggle));
  });
});

const rail = document.querySelector("[data-plan-rail]");
let dragging = false;
let startX = 0;
let startScroll = 0;

rail?.addEventListener("pointerdown", (event) => {
  if (event.target.closest("a, button")) return;
  dragging = true;
  startX = event.clientX;
  startScroll = rail.scrollLeft;
  rail.classList.add("is-dragging");
  rail.setPointerCapture(event.pointerId);
});

rail?.addEventListener("pointermove", (event) => {
  if (!dragging) return;
  rail.scrollLeft = startScroll - (event.clientX - startX);
});

function stopDragging() {
  dragging = false;
  rail?.classList.remove("is-dragging");
}

rail?.addEventListener("pointerup", stopDragging);
rail?.addEventListener("pointercancel", stopDragging);
