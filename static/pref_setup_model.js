// ── Preference Completion Model ──────────────────────────────────────
// The SINGLE authoritative representation of the preference setup steps.
//
// Architecture (per spec):
//   authoritative preference state
//     → completed preference steps
//     → total preference steps   (= PREFERENCE_STEPS.length)
//     → percentage               (= completed / total * 100)
//     → circular progress + number
//
// No page-to-percentage mapping.  No percentage is ever stored — it is always
// derived on the fly.  If a step is added/removed later, the denominator
// auto-adjusts because it is always PREFERENCE_STEPS.length and completion
// re-derives from state.
//
// A step is "complete" only when its OWN required data satisfies the
// existing product logic (NOT because the user visited the page, NOT
// because Continue was clicked).  The two structural pages (intro/review)
// complete only when the wizard is genuinely finished
// (preferences_preferences_setup_status === "completed"), which also covers
// optional fields the user deliberately concluded by finishing the wizard —
// but it NEVER fabricates completion for a data step whose own value is absent,
// so editing after finishing re-derives honestly.  Skipping keeps the
// semantics "leave this unanswered", so a skipped wizard never counts
// intro/review as complete.
//
// The model is pure — no DOM — so it can be unit-tested in Node and reused
// by the wizard.
(function () {
  "use strict";

  function arr(v) {
    if (Array.isArray(v)) return v;
    if (typeof v === "string" && v.trim()) return v.split(",");
    return [];
  }

  function txt(v) {
    return typeof v === "string" ? v.trim() : "";
  }

  function finished(s) {
    return String(s && s.setup_status ? s.setup_status : "").toLowerCase() === "completed";
  }

  var PREFERENCE_STEPS = [
    { id: "intro", label: "Introduction" },
    { id: "use", label: "Primary uses" },
    { id: "useprofile", label: "Use-case profile" },
    { id: "lang", label: "Languages & region" },
    { id: "style", label: "Communication style" },
    { id: "background", label: "Cultural background" },
    { id: "expression", label: "Cultural expression" },
    { id: "expressive", label: "Expressive language" },
    { id: "multilingual", label: "Multilingual behaviour" },
    { id: "characters", label: "AI characters" },
    { id: "voice", label: "Voice" },
    { id: "custom", label: "Custom preference" },
    { id: "aboutme", label: "About me" },
    { id: "review", label: "Review" },
  ];

// The wizard's default country (first option of the country selector). Seeded
// into the authoritative language state on init so review/preview agree with
// the visually-selected value — but only this untouched seed is exempt from
// counting as completion; any real persisted value (Nigeria included) counts.
var DEFAULT_COUNTRY = "Nigeria";

// Each step's completion predicate — existing product validation semantics.
var COMPLETE = {
    // Structural pages: complete only when the wizard was really finished.
    intro: finished,
    review: finished,

    // At least one primary use case selected.
    use: function (s) {
      return arr((s.preferences || {}).use_cases).length > 0;
    },

    // Optional free text — concrete text must be present.
    useprofile: function (s) {
      return !!txt((s.preferences || {}).use_case_profile);
    },

    // Languages & region: engaged when any real value was provided.  The
    // wizard seeds a UI default country (Nigeria) into the authoritative state
    // on init so review/preview agree with the selector — but like the seeded
    // characters default, that untouched default is NOT the user's completion.
    lang: function (s) {
      var l = s.language || {};
      var rl = txt(l.response_language);
      var seededDefault = txt(l.country) === DEFAULT_COUNTRY && !!(s.meta && s.meta.countrySeeded);
      return (txt(l.country) !== "" && !seededDefault) ||
             txt(l.state_province) !== "" ||
             arr(l.native_languages).length > 0 ||
             txt(l.cultural_background) !== "" ||
             l.prefer_not_to_say === true || l.prefer_not_to_say === "true" ||
             (rl !== "" && rl !== "en");
    },

    style: function (s) {
      var p = s.preferences || {};
      return arr(p.communication_style).length > 0 ||
             txt(p.communication_note) !== "";
    },

    background: function (s) {
      var l = s.language || {};
      return txt(l.cultural_background) !== "" ||
             l.prefer_not_to_say === true || l.prefer_not_to_say === "true" ||
             arr(l.native_languages).length > 0;
    },

    expression: function (s) {
      var v = (s.culture || {}).cultural_expression;
      return v != null && String(v) !== "";
    },

    expressive: function (s) {
      var p = s.preferences || {};
      return arr(p.expressive_language).length > 0 ||
             txt(p.expressive_language_other) !== "";
    },

    multilingual: function (s) {
      return txt((s.preferences || {}).multilingual_behavior) !== "";
    },

    // Characters default all-on in the draft for fresh users — that seeded
    // default is NOT the user's own completion.  Only a real selection (any
    // persisted value — array or legacy comma-string — that is not the
    // freshly-seeded default) counts.
    characters: function (s) {
      var seeded = !!(s.meta && s.meta.charactersSeeded);
      return arr((s.preferences || {}).preferred_characters).length > 0 && !seeded;
    },

    voice: function (s) {
      return txt((s.preferences || {}).voice_style) !== "";
    },

    custom: function (s) {
      return txt((s.preferences || {}).custom_preference) !== "";
    },

    aboutme: function (s) {
      return txt((s.preferences || {}).about_me) !== "";
    },
  };

  function stepIds() {
    return PREFERENCE_STEPS.map(function (st) { return st.id; });
  }

  function isStepComplete(state, stepIndex) {
    var st = PREFERENCE_STEPS[stepIndex];
    if (!st) return false;
    var fn = COMPLETE[st.id];
    return fn ? !!fn(state) : false;
  }

  function completedSteps(state) {
    state = state || {};
    for (var i = 0, n = 0; i < PREFERENCE_STEPS.length; i++) {
      if (isStepComplete(state, i)) n++;
    }
    return n;
  }

  function totalSteps() {
    return PREFERENCE_STEPS.length;
  }

  // Round the displayed percentage to the nearest whole number.
  function percentage(state) {
    var total = totalSteps();
    if (!total) return 0;
    return Math.round((completedSteps(state) / total) * 100);
  }

  function isComplete(state) {
    return completedSteps(state) === totalSteps();
  }

  // Pure navigation: next step index after offset, clamped to [0, total-1].
  // Never mutates state — Back/Continue must not reset anything.
  function nextStep(currentStep, offset) {
    var next = Number(currentStep) + Number(offset);
    if (next < 0) return 0;
    if (next >= totalSteps()) return currentStep;
    return next;
  }

  var MODEL = {
    steps: PREFERENCE_STEPS,
    stepIds: stepIds,
    isStepComplete: isStepComplete,
    completedSteps: completedSteps,
    totalSteps: totalSteps,
    percentage: percentage,
    isComplete: isComplete,
    nextStep: nextStep,
    defaultCountry: DEFAULT_COUNTRY,
  };

  if (typeof window !== "undefined") {
    window.PREF_SETUP_MODEL = MODEL;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = MODEL;
  }
})();