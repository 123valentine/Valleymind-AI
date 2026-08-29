// ── ValleyMind Preferences Setup Wizard ─────────────────────────────
// This is NOT a separate personalization system.  Everything this wizard
// reads and writes goes through the EXISTING Settings API
//   GET|PUT /api/settings/preferences
//   GET|PUT /api/settings/language
//   GET|PUT /api/settings/culture
// (the same endpoints already used by Settings > AI Preferences,
//  Language & Region, and Culture).  One source of truth.
//
// Entry points:
//   window.openPreferencesSetup({ source: "first_run" | "settings", clearPending })
//     - auto-opened after email verification for new signups
//     - opened from the "Complete your ValleyMind preferences" card in
//       Settings > AI Preferences for existing users
//
// Resume-after-skip:
//   Skipping never loses progress — answers are persisted on every transition.
//   Reopening derives the landing step from the persisted state via the model:
//     * first incomplete preference step (skipping already-complete pages)
//     * intro (Page 1) when nothing has been completed yet
//     * review (the completed state, never a restart) when everything is done.

(function () {

  // Single authoritative representation of the steps + completion rules lives
  // in pref_setup_model.js (PREF_SETUP_MODEL). The wizard only ever reads it —
  // total = PREFERENCE_STEPS.length, never a page-number → percentage mapping.
  // Setting up preferences is optional, so missing model breaks loudly rather
  // than silently mis-reporting progress.
  if (!window.PREF_SETUP_MODEL) {
    throw new Error("pref_setup_model.js must load before onboarding.js");
  }
  var MODEL = window.PREF_SETUP_MODEL;
  var STEP_IDS = MODEL.stepIds();

  // Every preference key maps to the EXISTING settings section it belongs to.
  var KEY_SECTION = {
    response_language: "language", language: "language",
    country: "language", state_province: "language",
    native_languages: "language", native_languages_other: "language",
    cultural_background: "language",
    prefer_not_to_say: "language",
    cultural_expression: "culture",
    communication_style: "preferences", communication_note: "preferences",
    use_cases: "preferences", use_cases_other: "preferences",
    expressive_language: "preferences", expressive_language_other: "preferences",
    multilingual_behavior: "preferences",
    preferred_characters: "preferences",
    custom_preference: "preferences", voice_style: "preferences",
    about_me: "preferences", use_case_profile: "preferences",
  };

  var OB = {
    step: 0,
    ev: null,        // { language:{}, preferences:{}, culture:{} }  — server truth
    loading: false,
    source: "settings",
    onClose: null,
  };

  window.openPreferencesSetup = function (opts) {
    opts = opts || {};
    OB.source = opts.source || "settings";
    OB.onClose = opts.onClose || null;
    OB.step = 0;
    ensureOverlay();
    // Reopening must actually SHOW the wizard again — closeOverlay() hides it
    // (display:none), so every open must reset that, not just the first one.
    _overlay.style.display = "flex";
    OB.loading = true;
    OB.ev = null;
    render();
    loadDraft();
  };

  window.prefSetupGo = function (offset) {
    if (OB.loading || !OB.ev) return;
    var next = MODEL.nextStep(OB.step, offset);
    if (next === OB.step) return;
    saveStep(OB.step);   // persist the step we are leaving (best effort)
    OB.step = next;
    render();
  };

  window.prefSetupSkip = function () {
    saveAll();           // keep anything already collected
    persistSetupStatus("skipped");
    clearPendingFlag();
    closeOverlay();
  };

  window.prefSetupFinish = function () {
    saveAll();
    persistSetupStatus("completed");
    clearPendingFlag();
    closeOverlay();
  };

  window.prefSetupClose = function () {
    closeOverlay();
  };

  // Multi-select toggle (card / chip)
  window.prefSetupToggle = function (key, value) {
    var section = sectionOf(key);
    if (!section) return;
    // A real swipe over the characters now becomes the user's own selection.
    if (key === "preferred_characters") OB._charactersSeeded = false;
    var arrKey = section + ".__" + key;
    var cur = draftArray(section, key);
    var idx = cur.indexOf(value);
    if (idx === -1) cur.push(value); else cur.splice(idx, 1);
    OB.dirty = OB.dirty || {};
    OB.dirty[section] = OB.dirty[section] || {};
    OB.dirty[section][key] = cur;
    syncDraftFromDirty();
    updatePreview();
    syncCardClass(section, key, value, idx === -1);
    // "Other" reveals a free-text box on the relevant step
    var OTHER_WRAPS = { use_cases: "obOtherWrap", native_languages: "obNatOtherWrap", expressive_language: "obExprOtherWrap" };
    var wrapId = OTHER_WRAPS[key];
    if (wrapId) {
      var wrap = document.getElementById(wrapId);
      if (wrap) wrap.style.display = cur.indexOf("Other") !== -1 ? "block" : "none";
    }
  };

  // Single-select (radio)
  window.prefSetupSetRadio = function (key, value) {
    routeDirty(key, value);
    updatePreview();
  };

  // Free-text input / textarea
  window.prefSetupInput = function (key, value) {
    routeDirty(key, value);
    updatePreview();
  };

  window.reflowChoices = function (el) {
    var wrap = el.closest(".ob-choice-list");
    if (!wrap) return;
    var all = wrap.querySelectorAll(".ob-choice");
    for (var i = 0; i < all.length; i++) {
      var input = all[i].querySelector("input[type=radio]");
      all[i].classList.toggle("selected", input && input.checked);
    }
    updatePreview();
  };

  // ── Draft plumbing ────────────────────────────────────────────────

  function sectionOf(key) { return KEY_SECTION[key] || null; }

  function routeDirty(key, value) {
    var section = sectionOf(key);
    if (!section || !OB.ev) return;
    OB.dirty = OB.dirty || {};
    OB.dirty[section] = OB.dirty[section] || {};
    OB.dirty[section][key] = value;
    syncDraftFromDirty();
  }

  function draftArray(section, key) {
    if (!OB.ev) return [];
    var v = OB.ev[section] ? OB.ev[section][key] : null;
    return Array.isArray(v) ? v.slice() : (v ? String(v).split(",").map(function (s) { return s.trim(); }).filter(Boolean) : []);
  }

  function currentLanguageValue() {
    var v = OB.ev ? OB.ev.language.response_language : "";
    if (!v || typeof v !== "string") return "en";
    for (var i = 0; i < CULTURAL_LANGUAGES.length; i++) {
      if (CULTURAL_LANGUAGES[i].value === v) return v;
    }
    return "en";
  }

  function syncDraftFromDirty() {
    if (!OB.ev || !OB.dirty) return;
    for (var s in OB.dirty) {
      if (!OB.ev[s]) continue;
      for (var k in OB.dirty[s]) OB.ev[s][k] = OB.dirty[s][k];
    }
  }

  function syncCardClass(section, key, value, selected) {
    var el = document.getElementById("ob_" + key + "_" + cssId(value));
    if (!el) return;
    el.classList.toggle("selected", selected);
    var box = el.querySelector(".ob-checkbox");
    if (box) box.innerHTML = selected ? "&#10003;" : "";
    var roleEl = el.getAttribute("role");
    if (roleEl === "checkbox") el.setAttribute("aria-checked", selected ? "true" : "false");
    else el.setAttribute("aria-pressed", selected ? "true" : "false");
  }

  function loadDraft() {
    Promise.all([
      apiFetch("/api/settings/language", { credentials: "include", headers: authHeaders() }).then(function (r) { return r.json(); }).catch(function () { return { data: {} }; }),
      apiFetch("/api/settings/preferences", { credentials: "include", headers: authHeaders() }).then(function (r) { return r.json(); }).catch(function () { return { data: {} }; }),
      apiFetch("/api/settings/culture", { credentials: "include", headers: authHeaders() }).then(function (r) { return r.json(); }).catch(function () { return { data: {} }; }),
      apiFetch("/api/settings/setup-status", { credentials: "include", headers: authHeaders() }).then(function (r) { return r.json(); }).catch(function () { return {}; }),
    ]).then(function (res) {
      var lang = (res[0] && res[0].data) || {};
      var prefs = (res[1] && res[1].data) || {};
      var culture = (res[2] && res[2].data) || {};
      var status = (res[3] && res[3].setup_status) || (res[3] && res[3].data && res[3].data.setup_status) || "";
      // Language & Region is the single source of truth for response
      // language; fall back to the AI Preferences key only for legacy accounts.
      if ((!lang.response_language || lang.response_language === "en") && prefs.language) {
        lang.response_language = prefs.language;
      }
      OB.ev = { language: lang, preferences: prefs, culture: culture, setup_status: status };
      // AI characters default to all-on by default (the same understanding is
      // shared system-wide); seed once so toggling works from a real array.
      // The seed is the user's OWN completion only once they touch the page,
      // so the model treats a freshly seeded default as "unanswered".
      if (OB.ev.preferences.preferred_characters === undefined) {
        OB.ev.preferences.preferred_characters = CHARACTER_OPTIONS.slice();
        OB._charactersSeeded = true;
      }
      OB.loading = false;
      // Resume where the user left off (derived from persisted state) instead
      // of always restarting at Page 1 — intro when nothing done yet, review
      // (completed state) when the table is already finished.
      OB.step = resumeStep();
      render();
    }, function () {
      OB.loading = false;
      render();
    });
  }

  // ── Saving (all through the EXISTING per-section endpoints) ───────

  function merged(section) { return JSON.parse(JSON.stringify(OB.ev[section] || {})); }

  function putSection(section, data) {
    return apiFetch("/api/settings/" + section, {
      method: "PUT",
      credentials: "include",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(data)
    }).then(function (r) { return r.json(); });
  }

  function saveStep(idx) {
    if (!OB.ev || OB.loading) return;
    var id = STEP_IDS[idx];
    var writes = [];
    if (id === "lang") writes.push(writeLanguage());
    else if (id === "use") writes.push(writePrefs({ use_cases: draftArray("preferences", "use_cases"), use_cases_other: str("preferences", "use_cases_other") }));
    else if (id === "style") writes.push(writePrefs({ communication_style: draftArray("preferences", "communication_style"), communication_note: str("preferences", "communication_note") }));
    else if (id === "background") writes.push(putSection("language", writeLanguageDraft()));
    else if (id === "expression") writes.push(putSection("culture", merged("culture")));
    else if (id === "expressive") writes.push(writePrefs({ expressive_language: draftArray("preferences", "expressive_language").filter(notOther), expressive_language_other: str("preferences", "expressive_language_other") }));
    else if (id === "multilingual") writes.push(writePrefs({ multilingual_behavior: str("preferences", "multilingual_behavior") }));
    else if (id === "characters") writes.push(writePrefs({ preferred_characters: charactersSelected() }));
    else if (id === "voice") writes.push(writePrefs({ voice_style: str("preferences", "voice_style") }));
    else if (id === "custom") writes.push(writePrefs({ custom_preference: str("preferences", "custom_preference") }));
    else if (id === "useprofile") writes.push(writePrefs({ use_case_profile: str("preferences", "use_case_profile") }));
    else if (id === "aboutme") writes.push(writePrefs({ about_me: str("preferences", "about_me") }));
    if (writes.length) Promise.all(writes).catch(function () { /* non-fatal */ });
  }

  function saveAll() {
    if (!OB.ev || OB.loading) return;
    Promise.all([
      writeLanguage(),
      putSection("culture", merged("culture")),
      writePrefs({
        language: currentLanguageValue(),
        communication_style: draftArray("preferences", "communication_style"),
        communication_note: str("preferences", "communication_note"),
        use_cases: draftArray("preferences", "use_cases"),
        use_cases_other: str("preferences", "use_cases_other"),
        use_case_profile: str("preferences", "use_case_profile"),
        expressive_language: draftArray("preferences", "expressive_language").filter(notOther),
        expressive_language_other: str("preferences", "expressive_language_other"),
        multilingual_behavior: str("preferences", "multilingual_behavior"),
        preferred_characters: charactersSelected(),
        custom_preference: str("preferences", "custom_preference"),
        voice_style: str("preferences", "voice_style"),
        about_me: str("preferences", "about_me"),
      }),
    ]).catch(function () { /* non-fatal */ });
  }

  function str(section, key) {
    if (!OB.ev || !OB.ev[section]) return "";
    var v = OB.ev[section][key];
    return v == null ? "" : String(v);
  }

  // Response language must land in the Language & Region section (the brain
  // reads it from there) AND mirror into AI Preferences for display.
  function writeLanguageDraft() {
    var lang = merged("language");
    if (Array.isArray(lang.native_languages)) lang.native_languages = lang.native_languages.filter(notOther);
    return lang;
  }

  function writeLanguage() {
    var lang = writeLanguageDraft();
    lang.response_language = currentLanguageValue();
    lang.language = lang.response_language;
    return putSection("language", lang);
  }

  function writePrefs(patch) {
    var p = merged("preferences");
    for (var k in patch) p[k] = patch[k];
    return putSection("preferences", p);
  }

  function clearPendingFlag() {
    try { sessionStorage.removeItem("vm_pref_setup_pending"); } catch (_) {}
    try { sessionStorage.removeItem("vm_pref_setup_dismissed"); } catch (_) {}
  }

  // Persist the setup status (completed | skipped) to the server so the
  // first-run wizard fires exactly once per user regardless of browser/session.
  function persistSetupStatus(status) {
    if (status === "completed") {
      // The chat preference banner mirrors server-side completion; when we just
      // finished, take it down immediately instead of waiting for checkSession.
      try {
        if (typeof window.hidePreferenceBanner === "function") window.hidePreferenceBanner();
      } catch (_) {}
    }
    try {
      apiFetch("/api/settings/setup-status", {
        method: "POST",
        credentials: "include",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ setup_status: status })
      }).then(function (r) { return r.json(); }).catch(function () { /* non-fatal */ });
    } catch (_) {}
  }

  // ── Overlay UI ─────────────────────────────────────────────────────

  var _overlay = null;
  function ensureOverlay() {
    if (_overlay) return _overlay;
    _overlay = document.createElement("div");
    _overlay.id = "prefSetupOverlay";
    _overlay.setAttribute("role", "dialog");
    _overlay.setAttribute("aria-modal", "true");
    _overlay.setAttribute("aria-label", "ValleyMind preferences setup");
    document.body.appendChild(_overlay);
    return _overlay;
  }

  function closeOverlay() {
    if (!_overlay) return;
    _overlay.style.display = "none";
    if (typeof OB.onClose === "function") OB.onClose();
    OB.onClose = null;
    try {
      var gear = document.getElementById("settingsGearBtn");
      if (gear) gear.focus();
    } catch (_) {}
  }

  function render() {
    var ov = ensureOverlay();
    ov.innerHTML =
      '<div class="ob-shell">' +
        '<div class="ob-head">' +
          '<div class="ob-brand">' +
            '<span class="ob-brand-mark">V</span>' +
            '<span class="ob-brand-name">ValleyMind</span>' +
            '<span class="ob-brand-tag">Preferences</span>' +
          '</div>' +
          '<button type="button" class="ob-close" onclick="prefSetupClose()" aria-label="Close">' + "&times;" + '</button>' +
        '</div>' +
        '<div class="ob-body">' +
          '<div class="ob-main">' +
            '<div class="ob-ring-wrap" data-progress>' +
              '<div class="ob-ring" aria-hidden="true">' +
                '<div class="ob-ring-fill" style="background:conic-gradient(#00d4ff ' + completionPct() + '%, rgba(255,255,255,0.07) 0);"></div>' +
                '<div class="ob-ring-hole"><span class="ob-ring-num">' + completionPct() + '</span><span class="ob-ring-pct">%</span></div>' +
              '</div>' +
              '<div class="ob-ring-label" role="status" aria-live="polite">' + completionBlurb() + '</div>' +
            '</div>' +
            '<div class="ob-content">' + renderStep() + '</div>' +
            '<div class="ob-actions">' + stepActions() + '</div>' +
          '</div>' +
          '<aside class="ob-preview" aria-label="Live preview">' + previewHtml() + '</aside>' +
        '</div>' +
      '</div>' +
      '<style>' + CSS + '</style>';
    bindAndFocus();
  }

  function modelState() {
    return {
      setup_status: (OB.ev && OB.ev.setup_status) || "",
      language: (OB.ev && OB.ev.language) || {},
      preferences: (OB.ev && OB.ev.preferences) || {},
      culture: (OB.ev && OB.ev.culture) || {},
      meta: { charactersSeeded: !!OB._charactersSeeded }
    };
  }

  // Landing step for a reopened wizard, derived from the persisted state (never
  // a stored position). Intro/review are structural: they only complete on a
  // genuinely finished wizard, and they are never "incomplete" resume targets —
  // the user resumes at the first incomplete DATA step.
  function resumeStep() {
    if (!OB.ev) return 0;
    var state = modelState();
    var anyDataComplete = false;
    var firstIncomplete = -1;
    for (var i = 1; i < STEP_IDS.length - 1; i++) {
      if (MODEL.isStepComplete(state, i)) anyDataComplete = true;
      else if (firstIncomplete === -1) firstIncomplete = i;
    }
    if (!anyDataComplete) return 0;                       // Page 1
    if (firstIncomplete === -1) return STEP_IDS.length - 1; // completed state
    return firstIncomplete;                               // first incomplete
  }

  // Completion is DERIVED from the authoritative, persisted preference state
  // (via the model) — not from the current step index.
  function completionPct() { return MODEL.percentage(modelState()); }

  function completionBlurb() {
    var done = MODEL.completedSteps(modelState());
    return done + ' of ' + MODEL.totalSteps() + ' complete';
  }

  function stepActions() {
    var back = OB.step > 0 ? '<button type="button" class="ob-btn ob-btn-ghost" onclick="prefSetupGo(-1)">Back</button>' : '';
    var isLast = OB.step === STEP_IDS.length - 1;
    var cont = isLast
      ? '<button type="button" class="ob-btn ob-btn-primary" onclick="prefSetupFinish()">Finish</button>'
      : '<button type="button" class="ob-btn ob-btn-primary" onclick="prefSetupGo(1)">Continue</button>';
    var skip = '<button type="button" class="ob-btn ob-btn-ghost" onclick="prefSetupSkip()">Skip for now</button>';
    var close = OB.source === "settings" ? '<button type="button" class="ob-btn ob-btn-ghost" onclick="prefSetupClose()">Close</button>' : '';
    return '<div class="ob-actions-left">' + back + '</div>' +
           '<div class="ob-actions-right">' + skip + close + cont + '</div>';
  }

  function renderStep() {
    if (OB.loading) return '<div class="ob-loading">Loading your current preferences…</div>';
    if (!OB.ev) {
      return '<div class="ob-loading"><p>Could not load preferences. Please try again.</p>' +
        '<button type="button" class="ob-btn ob-btn-primary" onclick="prefSetupClose()" style="margin-top:14px;">Close</button></div>';
    }
    var id = STEP_IDS[OB.step];
    if (id === "intro") return stepIntro();
    if (id === "use") return stepUse();
    if (id === "useprofile") return stepUseProfile();
    if (id === "lang") return stepLang();
    if (id === "style") return stepStyle();
    if (id === "background") return stepBackground();
    if (id === "expression") return stepExpression();
    if (id === "expressive") return stepExpressive();
    if (id === "multilingual") return stepMultilingual();
    if (id === "characters") return stepCharacters();
    if (id === "voice") return stepVoice();
    if (id === "custom") return stepCustom();
    if (id === "aboutme") return stepAboutMe();
    return stepReview();
  }

  function stepIntro() {
    return '<div class="ob-center">' +
      '<h1 class="ob-h1">Let\'s set up how ValleyMind works with you.</h1>' +
      '<p class="ob-sub">Choose how you want ValleyMind to communicate, what you use it for, and the languages and expressions you prefer. You can change these anytime in Settings.</p>' +
    '</div>';
  }

  var USE_OPTIONS = [
    "Personal AI assistant",
    "Studying",
    "Writing",
    "Coding",
    "Business & productivity",
    "Content creation",
    "Graphic design",
    "Video creation",
    "Research",
    "Planning & productivity",
    "Journaling",
  ];

  function stepUse() {
    var selected = draftArray("preferences", "use_cases");
    var cards = USE_OPTIONS.map(function (opt) {
      return toggleCard("use_cases", opt, selected.indexOf(opt) !== -1);
    }).join("");
    cards += toggleCard("use_cases", "Other", selected.indexOf("Other") !== -1);
    var otherVal = str("preferences", "use_cases_other");
    return '<h1 class="ob-h1">What do you mainly use ValleyMind for?</h1>' +
      '<p class="ob-sub">Select everything that fits — you can change this later.</p>' +
      '<div class="ob-card-grid" role="group" aria-label="Primary uses">' + cards + '</div>' +
      '<div id="obOtherWrap" style="display:' + (selected.indexOf("Other") !== -1 ? "block" : "none") + ';margin-top:14px;">' +
        '<label class="ob-label" for="ob_use_other">Tell us more</label>' +
        '<input id="ob_use_other" class="ob-input" type="text" value="' + escAttr(otherVal) + '" placeholder="What are you planning to use ValleyMind for?" oninput="prefSetupInput(\'use_cases_other\', this.value)" autocomplete="off">' +
      '</div>';
  }

  function stepUseProfile() {
    var val = str("preferences", "use_case_profile");
    return '<h1 class="ob-h1">Tell ValleyMind more about how you plan to use it</h1>' +
      '<p class="ob-sub">Optional but helpful. The more you share, the better ValleyMind can tailor its responses to your workflow and goals.</p>' +
      '<label class="ob-label" for="ob_use_profile">Your use-case profile</label>' +
      '<textarea id="ob_use_profile" class="ob-input ob-textarea" placeholder="e.g. I\'m a content creator making short-form videos for social media. I need help with scripting, captions, and brainstorming ideas. I usually work in English but sometimes mix in Pidgin." style="min-height:120px;" oninput="prefSetupInput(\'use_case_profile\', this.value)">' + escHtml(val) + '</textarea>' +
      '<p class="ob-note">This helps ValleyMind understand your goals — not just what you do, but how and why you do it.</p>';
  }

  var COUNTRY_OPTIONS = [
    "Nigeria", "Ghana", "Kenya", "South Africa", "Ethiopia", "Tanzania", "Uganda",
    "Cameroon", "Senegal", "Egypt", "Morocco", "Rwanda", "Zimbabwe", "Zambia",
    "Ivory Coast", "Congo", "Angola", "Mali", "Sudan", "Somalia", "Liberia",
    "Sierra Leone", "United States", "United Kingdom", "Canada", "Australia",
    "Germany", "France", "Netherlands", "Spain", "Portugal", "Italy", "Ireland",
    "Sweden", "India", "Japan", "China", "Brazil", "Mexico", "Other",
  ];

  var SPEAK_OPTIONS = [
    "English", "Igbo", "Yoruba", "Hausa", "Edo", "Ibibio", "Efik", "Pidgin English",
    "French", "Spanish", "Portuguese", "Arabic", "Hindi", "Japanese", "Chinese", "German",
  ];

  function secTitle(num, title) {
    return '<div class="ob-sec"><span class="ob-sec-num">' + num + '</span><span class="ob-sec-title">' + title + '</span></div>';
  }

  // Country dropdown: "Other" reveals a free-text box that overwrites the value.
  window.prefSetupCountry = function (val) {
    routeDirty("country", val);
    var wrap = document.getElementById("obCountryWrap");
    if (wrap) wrap.style.display = val === "Other" ? "block" : "none";
    updatePreview();
  };

  function stepLang() {
    var l = OB.ev.language;
    var curCountry = str("language", "country");
    var inList = COUNTRY_OPTIONS.indexOf(curCountry) !== -1;
    var isOther = !inList && curCountry !== "";
    var spoken = draftArray("language", "native_languages");
    var otherLangVal = str("language", "native_languages_other");
    var chips = SPEAK_OPTIONS.map(function (opt) {
      return toggleChip("native_languages", opt, spoken.indexOf(opt) !== -1);
    }).join("");
    chips += toggleChip("native_languages", "Other", spoken.indexOf("Other") !== -1);
    return '<h1 class="ob-h1">Help ValleyMind understand how you communicate</h1>' +
      '<p class="ob-sub">ValleyMind adapts how it talks to you based on the languages, cultural background and style you choose. These preferences are voluntary &mdash; ValleyMind never guesses your ethnicity, culture or identity from your name, location, IP address or language.</p>' +
      secTitle("1", "Where are you from?") +
      '<label class="ob-label" for="ob_country">Country</label>' +
      _SH.select(COUNTRY_OPTIONS, "ob_country", isOther ? "Other" : curCountry, "prefSetupCountry(this.value)") +
      '<div id="obCountryWrap" style="display:' + (isOther ? "block" : "none") + ';margin-top:10px;">' +
        '<input class="ob-input" type="text" value="' + escAttr(isOther ? curCountry : "") + '" placeholder="Type your country" autocomplete="off" oninput="prefSetupInput(\'country\', this.value)">' +
      '</div>' +
      '<div style="height:14px;"></div>' +
      '<label class="ob-label" for="ob_state">State / Province / Region</label>' +
      '<input id="ob_state" class="ob-input" type="text" value="' + escAttr(str("language", "state_province")) + '" placeholder="Select or type your state / region" autocomplete="off" oninput="prefSetupInput(\'state_province\', this.value)">' +
      '<p class="ob-note">Your location helps ValleyMind understand regional context. It does not automatically determine your ethnicity or culture.</p>' +
      secTitle("2", "What languages do you speak?") +
      '<p class="ob-sub" style="margin-bottom:10px;">Select all that apply</p>' +
      '<div class="ob-chip-wrap" role="group" aria-label="Languages you speak">' + chips + '</div>' +
      '<div id="obNatOtherWrap" style="display:' + (spoken.indexOf("Other") !== -1 ? "block" : "none") + ';margin-top:10px;">' +
        '<input class="ob-input" type="text" value="' + escAttr(otherLangVal) + '" placeholder="Other language" autocomplete="off" oninput="prefSetupInput(\'native_languages_other\', this.value)">' +
      '</div>' +
      '<div style="height:14px;"></div>' +
      '<label class="ob-label" for="ob_lang">Your primary response language</label>' +
      _SH.select(CULTURAL_LANGUAGES, "ob_lang", currentLanguageValue(), "prefSetupInput('response_language', this.value)") +
      '<p class="ob-note">You can speak one language and still have a different cultural background. ValleyMind keeps these preferences separate.</p>';
  }

  var STYLE_OPTIONS = [
    "Straight to the point", "Friendly", "Casual", "Professional", "Detailed",
    "Simple & easy to understand", "Creative", "Playful", "Analytical", "Let ValleyMind adapt",
  ];

  function stepStyle() {
    var selected = draftArray("preferences", "communication_style");
    var chips = STYLE_OPTIONS.map(function (opt) {
      return toggleChip("communication_style", opt, selected.indexOf(opt) !== -1);
    }).join("");
    var note = str("preferences", "communication_note");
    return '<h1 class="ob-h1">How should ValleyMind communicate with you?</h1>' +
      '<p class="ob-sub">Pick the styles that feel right. ValleyMind blends them to fit each conversation.</p>' +
      '<div class="ob-chip-wrap" role="group" aria-label="Communication styles">' + chips + '</div>' +
      '<label class="ob-label" for="ob_style_note" style="margin-top:18px;">Anything else about how you like ValleyMind to communicate?</label>' +
      '<textarea id="ob_style_note" class="ob-input ob-textarea" placeholder="Keep things simple and don\'t use too much technical language." oninput="prefSetupInput(\'communication_note\', this.value)">' + escHtml(note) + '</textarea>';
  }

  function radioChoice(name, value, label, checked, onclick) {
    return '<label class="ob-choice ' + (checked ? "selected" : "") + '" onclick="' + onclick + '">' +
      '<input type="radio" name="' + name + '" value="' + value + '" ' + (checked ? "checked" : "") + ' style="accent-color:#00d4ff;width:16px;height:16px;">' +
      '<span class="ob-choice-body"><b class="ob-choice-label">' + escHtml(label) + '</b></span>' +
    '</label>';
  }

  function notOther(s) { return s !== "Other"; }

  // "I want to share" / "I prefer not to say" — the latter disables the fields.
  window.prefSetupShare = function (share) {
    routeDirty("prefer_not_to_say", !share);
    var w = document.getElementById("obBgFields");
    if (w) {
      w.style.opacity = share ? 1 : 0.35;
      var inps = w.querySelectorAll("input");
      for (var i = 0; i < inps.length; i++) inps[i].disabled = !share;
    }
    updatePreview();
  };

  // The optional heritage-language box feeds the SAME native_languages store
  // (single source of truth) — typed values merge with the ones chosen earlier.
  window.prefSetupHeritage = function (text) {
    if (!OB.ev) return;
    var base = draftArray("language", "native_languages").filter(notOther);
    var parts = String(text || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    var merged = base.slice();
    for (var i = 0; i < parts.length; i++) {
      if (merged.indexOf(parts[i]) === -1) merged.push(parts[i]);
    }
    routeDirty("native_languages", merged);
  };

  function stepBackground() {
    var l = OB.ev.language;
    var pns = l.prefer_not_to_say === true || l.prefer_not_to_say === "true";
    var share = !pns;
    var heritageVal = draftArray("language", "native_languages").filter(notOther).join(", ");
    return '<h1 class="ob-h1">What is your cultural background?</h1>' +
      '<p class="ob-sub">Choose what you would like ValleyMind to understand about you.</p>' +
      '<div class="ob-choice-list" role="radiogroup" aria-label="Share cultural background">' +
        radioChoice("ob_share", "share", "I want to share my cultural background", share, "prefSetupShare(true); reflowChoices(this)") +
        radioChoice("ob_share", "pns", "I prefer not to say", pns, "prefSetupShare(false); reflowChoices(this)") +
      '</div>' +
      '<div id="obBgFields" style="display:flex;flex-direction:column;gap:14px;transition:opacity 0.2s;margin-top:14px;' + (pns ? "opacity:0.35;" : "") + '">' +
        '<div><label class="ob-label" for="ob_bg_ctx">Cultural background</label>' +
          '<input id="ob_bg_ctx" class="ob-input" type="text" value="' + escAttr(str("language", "cultural_background")) + '" placeholder="Example: Igbo, Yoruba, Hausa, Edo, mixed heritage, Nigerian, Ghanaian, etc." autocomplete="off" ' + (pns ? "disabled" : "") + ' oninput="prefSetupInput(\'cultural_background\', this.value)"></div>' +
        '<div><label class="ob-label" for="ob_bg_heritage">Optional: Native / heritage language</label>' +
          '<input id="ob_bg_heritage" class="ob-input" type="text" value="' + escAttr(heritageVal) + '" placeholder="e.g. Igbo" autocomplete="off" ' + (pns ? "disabled" : "") + ' oninput="prefSetupHeritage(this.value)"></div>' +
      '</div>' +
      '<p class="ob-note">This information is provided by you. ValleyMind does not infer or assign cultural identity automatically.</p>';
  }

  var EXPRESSION_OPTIONS = [
    { value: "off", label: "Off", desc: "Keep responses culturally neutral unless I explicitly ask.", reco: false },
    { value: "natural", label: "Natural", desc: "Use cultural references, expressions, sayings and context when they genuinely fit the conversation.", reco: true },
    { value: "deep", label: "Deep", desc: "Allow stronger use of relevant cultural expressions, proverbs, sayings, humor, language patterns and regional context when appropriate.", reco: false },
  ];

  function stepExpression() {
    var cur = str("culture", "cultural_expression") || "natural";
    var radios = EXPRESSION_OPTIONS.map(function (o) {
      var on = cur === o.value;
      var reco = o.reco ? '<span class="ob-reco">Recommended</span>' : "";
      return '<label class="ob-choice ' + (on ? "selected" : "") + '" onclick="prefSetupSetRadio(\'cultural_expression\',\'' + o.value + '\'); reflowChoices(this)">' +
        '<input type="radio" name="ob_cultural_expression" value="' + o.value + '" ' + (on ? "checked" : "") + ' style="accent-color:#00d4ff;width:16px;height:16px;">' +
        '<span class="ob-choice-body"><b class="ob-choice-label">' + o.label + reco + '</b><span class="ob-choice-desc">' + o.desc + '</span></span>' +
      '</label>';
    }).join("");
    return '<h1 class="ob-h1">How deeply should ValleyMind use cultural expression?</h1>' +
      '<p class="ob-sub">Choose how much cultural context should naturally appear in conversations. This only saves your preference &mdash; cultural intelligence is turned on gradually in a later update.</p>' +
      '<div class="ob-choice-list" role="radiogroup" aria-label="Cultural expression">' + radios + '</div>';
  }

  var EXPRESSIVE_OPTIONS = [
    "Proverbs & traditional sayings", "Local expressions", "Nigerian Pidgin",
    "Light teasing", "Banter", "Humor", "Sarcasm", "Savage / playful comebacks",
    "Storytelling", "Formal cultural language", "Keep it respectful and straightforward",
  ];

  function stepExpressive() {
    var selected = draftArray("preferences", "expressive_language");
    var otherVal = str("preferences", "expressive_language_other");
    var chips = EXPRESSIVE_OPTIONS.map(function (opt) {
      return toggleChip("expressive_language", opt, selected.indexOf(opt) !== -1);
    }).join("");
    chips += toggleChip("expressive_language", "Other", selected.indexOf("Other") !== -1);
    return '<h1 class="ob-h1">What kind of expressions do you enjoy?</h1>' +
      '<p class="ob-sub">Select any that fit you.</p>' +
      '<div class="ob-chip-wrap" role="group" aria-label="Expressive language">' + chips + '</div>' +
      '<div id="obExprOtherWrap" style="display:' + (selected.indexOf("Other") !== -1 ? "block" : "none") + ';margin-top:10px;">' +
        '<input class="ob-input" type="text" value="' + escAttr(otherVal) + '" placeholder="Other expression" autocomplete="off" oninput="prefSetupInput(\'expressive_language_other\', this.value)">' +
      '</div>' +
      '<p class="ob-note">ValleyMind should use these naturally, not force them into every response.</p>';
  }

  var MULTILINGUAL_OPTIONS = [
    "Stay in my selected language",
    "Follow the language I use",
    "Mix languages naturally when appropriate",
    "Ask me before switching languages",
  ];

  function stepMultilingual() {
    var cur = str("preferences", "multilingual_behavior");
    var radios = MULTILINGUAL_OPTIONS.map(function (opt) {
      var on = cur === opt;
      return radioChoice("ob_multilingual", opt, opt, on,
        "prefSetupSetRadio('multilingual_behavior','" + jsStr(opt) + "'); reflowChoices(this)");
    }).join("");
    return '<h1 class="ob-h1">How should ValleyMind handle multilingual conversations?</h1>' +
      '<p class="ob-sub">Choose how ValleyMind responds when a conversation mixes languages.</p>' +
      '<div class="ob-choice-list" role="radiogroup" aria-label="Multilingual behavior">' + radios + '</div>' +
      '<p class="ob-note">Example: if you start in English and switch to Igbo, ValleyMind can follow your change instead of automatically translating everything back to English.</p>';
  }

  var CHARACTER_OPTIONS = ["Marcus", "Angelina", "Elena"];

  // Seeded (default all-on) in loadDraft — this is the draft's single source.
  function charactersSelected() {
    return draftArray("preferences", "preferred_characters");
  }

  function stepCharacters() {
    var sel = charactersSelected();
    var cards = CHARACTER_OPTIONS.map(function (c) {
      return toggleCard("preferred_characters", c, sel.indexOf(c) !== -1);
    }).join("");
    return '<h1 class="ob-h1">Who should apply these preferences?</h1>' +
      '<p class="ob-sub">Your language and cultural preferences can be used across your ValleyMind characters when relevant.</p>' +
      '<div class="ob-card-grid" role="group" aria-label="Apply to characters">' + cards + '</div>' +
      '<p class="ob-note">The same underlying language and cultural understanding is shared across the ValleyMind AI system. Each character may express it differently according to their personality and the context of the conversation.</p>';
  }

  var VOICE_OPTIONS = [
    { value: "Natural", label: "Natural", desc: "A balanced, human tone" },
    { value: "Professional", label: "Professional", desc: "Clear, polished and precise" },
    { value: "Friendly", label: "Friendly", desc: "Warm and approachable" },
    { value: "Energetic", label: "Energetic", desc: "Lively and upbeat" },
  ];

  function stepVoice() {
    var cur = str("preferences", "voice_style") || "Natural";
    var radios = VOICE_OPTIONS.map(function (o) {
      var on = cur === o.value;
      return '<label class="ob-choice ' + (on ? "selected" : "") + '" onclick="prefSetupSetRadio(\'voice_style\',\'' + o.value + '\'); reflowChoices(this)">' +
        '<input type="radio" name="ob_voice_style" value="' + o.value + '" ' + (on ? "checked" : "") + ' style="accent-color:#00d4ff;width:16px;height:16px;">' +
        '<span class="ob-choice-body"><b class="ob-choice-label">' + o.label + '</b><span class="ob-choice-desc">' + o.desc + '</span></span>' +
      '</label>';
    }).join("");
    return '<h1 class="ob-h1">How should ValleyMind sound?</h1>' +
      '<p class="ob-sub">Your AI voice preference — the same setting as Settings → AI Preferences.</p>' +
      '<div class="ob-choice-list" role="radiogroup" aria-label="Voice style">' + radios + '</div>';
  }

  function stepCustom() {
    var val = str("preferences", "custom_preference");
    return '<h1 class="ob-h1">Anything else you want ValleyMind to know about how you like to work?</h1>' +
      '<p class="ob-sub">Optional. Add anything that helps ValleyMind support you better.</p>' +
      '<textarea id="ob_custom" class="ob-input ob-textarea" placeholder="e.g. I work in short bursts, prefer concise bullet answers for work questions, and think out loud when planning." oninput="prefSetupInput(\'custom_preference\', this.value)">' + escHtml(val) + '</textarea>';
  }

  function stepAboutMe() {
    var val = str("preferences", "about_me");
    return '<h1 class="ob-h1">Remember this about me</h1>' +
      '<p class="ob-sub">Share anything you\'d like ValleyMind to always remember about you — your interests, personality, what matters to you, or anything that helps ValleyMind understand who you are.</p>' +
      '<label class="ob-label" for="ob_about_me">About me</label>' +
      '<textarea id="ob_about_me" class="ob-input ob-textarea" placeholder="e.g. I\'m a software developer who loves Nigerian jollof rice debates. I prefer direct answers and don\'t like fluff. I\'m building a startup focused on African fintech." style="min-height:120px;" oninput="prefSetupInput(\'about_me\', this.value)">' + escHtml(val) + '</textarea>' +
      '<p class="ob-note">ValleyMind uses this to personalise your experience. You can update this anytime in Settings.</p>';
  }

  function stepReview() {
    var p = previewRows();
    return '<h1 class="ob-h1">Review your choices</h1>' +
      '<p class="ob-sub">Your ValleyMind communication profile &mdash; save when ready.</p>' +
      '<div class="ob-review-grid">' +
        obReviewRow("Response language", p.language) +
        obReviewRow("Languages", p.languages) +
        obReviewRow("Country", p.country || "&mdash;") +
        obReviewRow("Cultural background", p.background || "&mdash;") +
        obReviewRow("Cultural expression", p.expression) +
        obReviewRow("Communication style", p.style) +
        obReviewRow("Preferred expressions", p.expressive.length ? p.expressive.join(" \u00b7 ") : "&mdash;") +
        obReviewRow("Multilingual behavior", p.multilingual) +
        obReviewRow("AI characters", p.characters) +
        obReviewRow("Voice", p.voice) +
        (p.useCaseProfile ? obReviewRow("Use-case profile", p.useCaseProfile) : "") +
        (p.aboutMe ? obReviewRow("About me", p.aboutMe) : "") +
        (p.custom ? obReviewRow("Custom preference", p.custom) : "") +
      '</div>' +
      '<p class="ob-note" style="margin:0 0 14px;">Your preferences are always editable &mdash; change them anytime from Settings &#8594; AI Preferences &#8594; Language &amp; Culture.</p>' +
      '<div class="ob-sample" aria-hidden="true">' +
        '<span class="ob-sample-label">Preview</span>' +
        '<p class="ob-sample-text">' + escHtml(previewLine()) + '</p>' +
      '</div>';
  }

  function obReviewRow(label, value) {
    return '<div class="ob-review-row"><span class="ob-review-label">' + label + '</span><span class="ob-review-value">' + escHtml(value) + '</span></div>';
  }

  // ── Live preview (frontend-only) ──────────────────────────────────

  function previewRows() {
    var langLabel = "English";
    for (var i = 0; i < CULTURAL_LANGUAGES.length; i++) {
      if (CULTURAL_LANGUAGES[i].value === currentLanguageValue()) langLabel = CULTURAL_LANGUAGES[i].label;
    }
    var styles = draftArray("preferences", "communication_style");
    var expr = str("culture", "cultural_expression") || "natural";
    var langs = draftArray("language", "native_languages").filter(notOther);
    var langOther = str("language", "native_languages_other");
    if (langOther) langs.push(langOther);
    var exprs = draftArray("preferences", "expressive_language").filter(notOther);
    var exprOther = str("preferences", "expressive_language_other");
    if (exprOther) exprs.push(exprOther);
    var pnsPref = OB.ev && OB.ev.language ? OB.ev.language.prefer_not_to_say : false;
    var pns = pnsPref === true || pnsPref === "true";
    return {
      language: langLabel,
      languages: langs.length ? langs.join(", ") : "&mdash;",
      country: str("language", "country"),
      background: pns ? "Prefer not to say" : str("language", "cultural_background"),
      style: styles.length ? styles[0] : "Let ValleyMind adapt",
      expression: { off: "Off", natural: "Natural", deep: "Deep" }[expr] || "Natural",
      expressive: exprs,
      multilingual: str("preferences", "multilingual_behavior") || "Follow the language I use",
      characters: charactersSelected().join(", "),
      voice: str("preferences", "voice_style") || "Natural",
      useCaseProfile: str("preferences", "use_case_profile"),
      aboutMe: str("preferences", "about_me"),
      custom: str("preferences", "custom_preference"),
    };
  }

  function previewLine() {
    var p = previewRows();
    var line = "Got you. Let's break this down\u2026";
    var s = String(p.style).toLowerCase();
    if (s.indexOf("straight") === 0) line = "Here's the breakdown.";
    else if (s.indexOf("professional") === 0) line = "Let's break that down for you.";
    else if (s.indexOf("detailed") === 0) line = "Here's the full picture, step by step.";
    else if (s.indexOf("analytical") === 0) line = "Let's look at what's really going on here.";
    else if (s.indexOf("simple") === 0) line = "Here's the simple version.";
    else if (s.indexOf("creative") === 0) line = "Let's spin this into something new.";
    else if (s.indexOf("playful") === 0) line = "Okay, this one's fun \u2014 let's go!";
    if (p.expression === "Deep") line += " That brings to mind something worth holding onto.";
    else if (p.expression === "Natural") line += " Let's take it from the top.";
    return line;
  }

  function updatePreview() {
    if (!_overlay) return;
    var el = _overlay.querySelector(".ob-preview");
    if (el) el.innerHTML = previewHtml();
    updateProgress();
  }

  // Re-renders the derived completion ring/label in place (no full re-render
  // needed) so the indicator reflects state as the user types/toggles live.
  function updateProgress() {
    if (!_overlay) return;
    var wrap = _overlay.querySelector("[data-progress]");
    if (!wrap || OB.loading) return;
    var pct = completionPct();
    var fill = wrap.querySelector(".ob-ring-fill");
    if (fill) fill.style.background = "conic-gradient(#00d4ff " + pct + "%, rgba(255,255,255,0.07) 0)";
    var num = wrap.querySelector(".ob-ring-num");
    if (num) num.textContent = pct;
    var label = wrap.querySelector(".ob-ring-label");
    if (label) label.textContent = completionBlurb();
  }

  function previewHtml() {
    var p = previewRows();
    var exprNote = p.expression === "Off"
      ? "Keeps responses culturally neutral unless asked."
      : (p.expression === "Deep"
          ? "Uses cultural context more actively when appropriate."
          : "Uses expressions and context only when they genuinely fit.");
    var expBadge = p.expressive.length
      ? '<span class="ob-preview-tag">' + escHtml(p.expressive.length + " expressive touch" + (p.expressive.length > 1 ? "es" : "")) + '</span>'
      : "";
    return '<div class="ob-preview-card">' +
      '<div class="ob-preview-title">Live preview</div>' +
      '<div class="ob-preview-row"><span>Language</span><b>' + escHtml(p.language) + '</b></div>' +
      '<div class="ob-preview-row"><span>Languages</span><b>' + p.languages + '</b></div>' +
      '<div class="ob-preview-row"><span>Country</span><b>' + escHtml(p.country || "—") + '</b></div>' +
      '<div class="ob-preview-row"><span>Cultural background</span><b>' + escHtml(p.background || "—") + '</b></div>' +
      '<div class="ob-preview-row"><span>Cultural expression</span><b>' + escHtml(p.expression) + '</b></div>' +
      '<div class="ob-preview-row"><span>Style</span><b>' + escHtml(p.style) + '</b></div>' +
      '<div class="ob-preview-row"><span>Multilingual</span><b>' + escHtml(p.multilingual) + '</b></div>' +
      '<div class="ob-preview-row"><span>AI characters</span><b>' + escHtml(p.characters) + '</b></div>' +
      '<div class="ob-preview-expr">' + escHtml(exprNote) + '</div>' +
      expBadge +
      '<div class="ob-preview-bubble">“' + escHtml(previewLine()) + '”</div>' +
    '</div>';
  }

  // ── Focus / a11y ──────────────────────────────────────────────────

  function bindAndFocus() {
    var ov = ensureOverlay();
    setTimeout(function () {
      var els = ov.querySelectorAll("input, select, textarea, button");
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (el.type === "radio" && !el.checked) continue;
        if (el.type === "hidden") continue;
        el.focus();
        break;
      }
    }, 80);
  }

  // Escape closes (unless it's the forced first-run, still closable — it's optional).
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && _overlay && _overlay.style.display !== "none") {
      closeOverlay();
    }
  });

  // ── HTML rendering helpers ────────────────────────────────────────

  function toggleCard(key, value, on) {
    return '<button type="button" role="checkbox" aria-checked="' + (on ? "true" : "false") + '" id="ob_' + key + '_' + cssId(value) + '" class="ob-card ' + (on ? "selected" : "") + '" onclick="prefSetupToggle(\'' + key + '\',\'' + jsStr(value) + '\')">' +
      '<span class="ob-checkbox">' + (on ? "&#10003;" : "") + '</span>' +
      '<span class="ob-card-label">' + escHtml(value) + '</span>' +
    '</button>';
  }

  function toggleChip(key, value, on) {
    return '<button type="button" id="ob_' + key + '_' + cssId(value) + '" class="ob-chip ' + (on ? "selected" : "") + '" aria-pressed="' + (on ? "true" : "false") + '" onclick="prefSetupToggle(\'' + key + '\',\'' + jsStr(value) + '\')">' + escHtml(value) + '</button>';
  }

  function cssId(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, "_"); }
  function jsStr(s) { return String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'"); }
  function escHtml(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function escAttr(s) { return escHtml(s).replace(/"/g, "&quot;"); }

  var CSS = [
    "#prefSetupOverlay{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(2,6,23,0.96);backdrop-filter:blur(18px);z-index:100010;overflow:hidden;display:flex;font-family:'Inter',sans-serif;}",
    ".ob-shell{display:flex;flex-direction:column;width:100%;max-width:1020px;margin:0 auto;height:100%;background:rgba(2,6,23,0.6);}",
    ".ob-head{display:flex;align-items:center;justify-content:space-between;padding:14px 22px;border-bottom:1px solid rgba(255,255,255,0.06);flex-shrink:0;}",
    ".ob-brand{display:flex;align-items:center;gap:10px;}",
    ".ob-brand-mark{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#00d4ff,#0ea5e9);color:#003642;display:flex;align-items:center;justify-content:center;font-weight:900;font-family:'Space Grotesk',sans-serif;font-size:15px;}",
    ".ob-brand-name{color:#f1f5f9;font-size:15px;font-weight:700;font-family:'Inter',sans-serif;letter-spacing:0.02em;}",
    ".ob-brand-tag{color:#475569;font-size:10px;text-transform:uppercase;letter-spacing:0.15em;font-family:'Space Grotesk',sans-serif;font-weight:500;}",
    ".ob-close{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08);color:#94a3b8;font-size:18px;cursor:pointer;width:34px;height:34px;border-radius:8px;display:flex;align-items:center;justify-content:center;transition:all 0.2s;}",
    ".ob-close:hover{background:rgba(255,255,255,0.12);color:#f8fafc;border-color:rgba(255,255,255,0.2);}",
    ".ob-body{display:flex;flex:1;overflow-y:auto;}",
    ".ob-main{flex:1;max-width:640px;margin:0 auto;padding:26px 24px 18px;display:flex;flex-direction:column;min-width:0;}",
    ".ob-ring-wrap{display:flex;flex-direction:column;align-items:center;gap:10px;margin-bottom:18px;flex-shrink:0;pointer-events:none;}",
    ".ob-ring{position:relative;width:88px;height:88px;border-radius:50%;}",
    ".ob-ring-fill{position:absolute;inset:0;border-radius:50%;transition:background 0.3s;box-shadow:0 0 22px rgba(0,212,255,0.22);}",
    ".ob-ring-hole{position:absolute;inset:7px;border-radius:50%;background:rgba(2,6,23,0.98);display:flex;align-items:center;justify-content:center;gap:1px;font-family:'Space Grotesk',sans-serif;}",
    ".ob-ring-num{color:#f1f5f9;font-size:22px;font-weight:700;line-height:1;}",
    ".ob-ring-pct{color:#00d4ff;font-size:11px;font-weight:600;line-height:1;}",
    ".ob-ring-label{color:#94a3b8;font-size:11.5px;font-family:'Inter',sans-serif;letter-spacing:0.02em;}",
    ".ob-content{flex:1;overflow-y:auto;}",
    ".ob-h1{color:#f1f5f9;font-size:22px;font-weight:700;font-family:'Inter',sans-serif;margin:0 0 8px;line-height:1.3;}",
    ".ob-sub{color:#94a3b8;font-size:13.5px;line-height:1.6;margin:0 0 18px;font-family:'Inter',sans-serif;}",
    ".ob-center{display:flex;flex-direction:column;justify-content:center;min-height:60%;}",
    ".ob-label{display:block;color:#94a3b8;font-size:11px;margin:0 0 6px;font-family:'Inter',sans-serif;text-transform:uppercase;letter-spacing:0.08em;}",
    ".ob-input{width:100%;background:rgba(15,23,42,0.85);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:12px 14px;color:#e2e8f0;font-size:13.5px;outline:none;font-family:'Inter',sans-serif;box-sizing:border-box;transition:border-color 0.2s;}",
    ".ob-input:focus{border-color:rgba(0,212,255,0.5);}",
    ".ob-textarea{min-height:92px;resize:vertical;line-height:1.6;}",
    ".ob-card-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;}",
    ".ob-card{display:flex;align-items:center;gap:10px;width:100%;text-align:left;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:13px 14px;cursor:pointer;font-family:'Inter',sans-serif;color:#e2e8f0;font-size:13px;transition:all 0.15s;}",
    ".ob-card:hover{border-color:rgba(0,212,255,0.35);background:rgba(0,212,255,0.05);}",
    ".ob-card.selected{border-color:rgba(0,212,255,0.55);background:rgba(0,212,255,0.1);color:#7dd3fc;}",
    ".ob-card:focus-visible{outline:2px solid #00d4ff;outline-offset:2px;}",
    ".ob-checkbox{width:18px;height:18px;border-radius:5px;border:1.5px solid rgba(255,255,255,0.25);display:flex;align-items:center;justify-content:center;font-size:12px;color:#003642;flex-shrink:0;background:transparent;transition:all 0.15s;}",
    ".ob-card.selected .ob-checkbox{background:#00d4ff;border-color:#00d4ff;}",
    ".ob-card-label{line-height:1.4;}",
    ".ob-chip-wrap{display:flex;flex-wrap:wrap;gap:8px;}",
    ".ob-chip{display:inline-block;background:rgba(255,255,255,0.04);color:#94a3b8;border:1px solid rgba(255,255,255,0.08);border-radius:999px;padding:8px 15px;font-size:12.5px;cursor:pointer;transition:all 0.15s;font-family:'Inter',sans-serif;}",
    ".ob-chip:hover{border-color:rgba(0,212,255,0.35);color:#cbd5e1;}",
    ".ob-chip.selected{background:rgba(0,212,255,0.15);color:#00d4ff;border-color:rgba(0,212,255,0.3);}",
    ".ob-chip:focus-visible{outline:2px solid #00d4ff;outline-offset:2px;}",
    ".ob-choice-list{display:flex;flex-direction:column;gap:10px;margin-top:6px;}",
    ".ob-choice{display:flex;align-items:flex-start;gap:12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:14px;cursor:pointer;transition:all 0.15s;font-family:'Inter',sans-serif;}",
    ".ob-choice:hover{border-color:rgba(0,212,255,0.35);background:rgba(0,212,255,0.04);}",
    ".ob-choice.selected{border-color:rgba(0,212,255,0.55);background:rgba(0,212,255,0.09);}",
    ".ob-choice:focus-within{outline:2px solid #00d4ff;outline-offset:2px;}",
    ".ob-choice input{margin-top:2px;}",
    ".ob-choice-body{display:flex;flex-direction:column;gap:3px;flex:1;}",
    ".ob-choice-label{color:#e2e8f0;font-size:13.5px;}",
    ".ob-choice-desc{color:#64748b;font-size:12px;line-height:1.5;}",
    ".ob-note{color:#64748b;font-size:12px;margin:14px 0 0;font-family:'Inter',sans-serif;line-height:1.6;}",
    ".ob-sec{display:flex;align-items:center;gap:9px;margin:0 0 10px;}",
    ".ob-sec-num{width:21px;height:21px;border-radius:6px;background:rgba(0,212,255,0.12);color:#00d4ff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;font-family:'Space Grotesk',sans-serif;flex-shrink:0;}",
    ".ob-sec-title{color:#e2e8f0;font-size:13.5px;font-weight:700;font-family:'Inter',sans-serif;}",
    ".ob-reco{display:inline-block;vertical-align:middle;margin-left:8px;background:linear-gradient(135deg,rgba(0,212,255,0.22),rgba(14,165,233,0.16));color:#7dd3fc;border:1px solid rgba(0,212,255,0.35);border-radius:999px;padding:1px 8px;font-size:9px;text-transform:uppercase;letter-spacing:0.08em;font-family:'Space Grotesk',sans-serif;font-weight:700;}",
    ".ob-prefer-wrap{margin-bottom:14px;}",
    ".ob-check{display:flex;align-items:center;gap:9px;cursor:pointer;font-family:'Inter',sans-serif;color:#e2e8f0;font-size:13.5px;margin:0 0 12px;}",
    ".ob-check input{accent-color:#00d4ff;width:16px;height:16px;}",
    ".ob-check-label b{font-weight:600;}",
    ".ob-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;padding-top:16px;margin-top:14px;border-top:1px solid rgba(255,255,255,0.06);flex-shrink:0;}",
    ".ob-actions-left,.ob-actions-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}",
    ".ob-btn{border:none;border-radius:10px;padding:10px 18px;font-size:13px;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif;transition:all 0.15s;min-height:40px;display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;}",
    ".ob-btn-primary{background:#00d4ff;color:#003642;}",
    ".ob-btn-primary:hover{opacity:0.88;}",
    ".ob-btn-ghost{background:rgba(255,255,255,0.06);color:#94a3b8;border:1px solid rgba(255,255,255,0.08);}",
    ".ob-btn-ghost:hover{color:#e2e8f0;background:rgba(255,255,255,0.1);}",
    ".ob-btn:focus-visible{outline:2px solid #00d4ff;outline-offset:2px;}",
    ".ob-loading{color:#94a3b8;font-size:13px;font-family:'Inter',sans-serif;padding:40px 0;text-align:center;}",
    ".ob-review-grid{display:flex;flex-direction:column;gap:8px;margin-bottom:16px;}",
    ".ob-review-row{display:flex;align-items:center;justify-content:space-between;gap:12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:12px 14px;}",
    ".ob-review-label{color:#64748b;font-size:12px;font-family:'Inter',sans-serif;}",
    ".ob-review-value{color:#e2e8f0;font-size:13px;font-family:'Inter',sans-serif;text-align:right;}",
    ".ob-sample{background:rgba(0,212,255,0.06);border:1px solid rgba(0,212,255,0.2);border-radius:12px;padding:16px;}",
    ".ob-sample-label{color:#00d4ff;font-size:10px;text-transform:uppercase;letter-spacing:0.12em;font-family:'Space Grotesk',sans-serif;font-weight:600;}",
    ".ob-sample-text{color:#7dd3fc;font-size:14px;line-height:1.6;margin:8px 0 0;font-family:'Inter',sans-serif;}",
    // ── Preview aside ──
    ".ob-preview{width:280px;flex-shrink:0;padding:26px 22px;border-left:1px solid rgba(255,255,255,0.06);background:rgba(15,23,42,0.3);overflow-y:auto;}",
    ".ob-preview-card{position:sticky;top:0;}",
    ".ob-preview-title{color:#94a3b8;font-size:10px;text-transform:uppercase;letter-spacing:0.15em;font-family:'Space Grotesk',sans-serif;font-weight:600;margin-bottom:14px;}",
    ".ob-preview-row{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-family:'Inter',sans-serif;font-size:12px;}",
    ".ob-preview-row span{color:#64748b;}",
    ".ob-preview-row b{color:#e2e8f0;font-weight:600;text-align:right;}",
    ".ob-preview-expr{color:#475569;font-size:11px;line-height:1.5;margin-top:12px;font-family:'Inter',sans-serif;}",
    ".ob-preview-tag{display:inline-block;margin-top:8px;background:rgba(0,212,255,0.1);color:#7dd3fc;border:1px solid rgba(0,212,255,0.25);border-radius:6px;padding:3px 8px;font-size:10px;font-family:'Inter',sans-serif;}",
    ".ob-preview-bubble{background:rgba(0,212,255,0.08);border:1px solid rgba(0,212,255,0.2);border-radius:12px;padding:12px 14px;margin-top:14px;color:#a5f3fc;font-size:13px;line-height:1.6;font-family:'Inter',sans-serif;}",
    // ── Responsive ──
    "@media (max-width:820px){.ob-body{flex-direction:column;}.ob-main{max-width:none;padding:20px 16px 14px;}.ob-preview{width:auto;border-left:none;border-top:1px solid rgba(255,255,255,0.06);padding:16px;}.ob-preview-card{position:static;}.ob-card-grid{grid-template-columns:1fr;}.ob-h1{font-size:19px;}.ob-actions{flex-direction:column;align-items:stretch;}.ob-actions-left,.ob-actions-right{justify-content:space-between;}}",
    "@media (max-width:420px){.ob-brand-name{display:none;}.ob-btn{padding:10px 14px;font-size:12.5px;min-height:44px;}.ob-close{width:40px;height:40px;}.ob-chip{min-height:40px;}.ob-card{min-height:44px;}.ob-choice{min-height:44px;}}",
  ].join("\n");

})();