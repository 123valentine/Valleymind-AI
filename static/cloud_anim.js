(function () {
  "use strict";

  // ValleyMind Cloud — ONE centralized animation/state engine with THREE
  // cleanly separated layers:
  //
  //   1) Emotion/state layer — cloudSetState(...) drives a small emotional
  //      state machine; transient states auto-return to the last stable one.
  //   2) Movement layer — a standalone walking controller
  //      (walk / walkTo / stop / turn) that moves Cloud around the viewport,
  //      respects boundaries/safe-areas, pauses on user drag, and never walks
  //      off-screen.
  //   3) Rig/animation layer — drives the layered SVG rig (static/cloud_rig.js)
  //      — the original cloud companion visual — so eyes, eyebrows, nose,
  //      mouth, arms, hands and legs genuinely move independently. If the rig
  //      is absent (only the flattened PNG is available) it falls back to
  //      honest whole-character body articulation.
  //
  //   4) Autonomous drift layer — when nothing else commands movement, Cloud
  //      picks a nearby point in the walkable area and eases itself there, so
  //      it drifts around on its own between user interactions.
  //
  // The rig is an ORIGINAL cloud companion drawn as addressable vector parts
  // (NOT a redraw of static/cloud.png; cloud.png remains only the no-JS
  // fallback, hidden the moment the rig mounts). Whole-body transforms
  // (breathing, bob, lean, squash) apply to the character container; part
  // transforms (eyes/brows/nose/mouth/arms/hands/legs) apply to the rig
  // groups. This module never mounts or creates the character — the element
  // owner (static/cloud.js) and static/index.html markup handle that.

  var CHARACTER_ID = "vmCloudCharacter";
  var MINI_ORB_ID = "vmCloudCompanionMiniOrb";
  var TAU = Math.PI * 2;

  // ───────────────────────────────────────────────────────────────────────
  // Layer 1 — Emotion/state machine
  // ───────────────────────────────────────────────────────────────────────
  var STATES = [
    { key: "idle", label: "Idle", transient: false, hold: 0 },
    { key: "listening", label: "Listening", transient: false, hold: 0 },
    { key: "thinking", label: "Thinking", transient: true, hold: 7000 },
    { key: "happy", label: "Happy", transient: true, hold: 5000 },
    { key: "excited", label: "Excited", transient: true, hold: 4500 },
    { key: "sad", label: "Sad", transient: true, hold: 5500 },
    { key: "surprised", label: "Surprised", transient: true, hold: 2600 },
    { key: "confused", label: "Confused", transient: true, hold: 5000 },
    { key: "curious", label: "Curious", transient: true, hold: 4600 },
    { key: "concerned", label: "Concerned", transient: true, hold: 5200 },
    { key: "greeting", label: "Greeting", transient: true, hold: 4000 },
    { key: "speaking", label: "Speaking", transient: false, hold: 0 },
    { key: "waving", label: "Waving", transient: true, hold: 2400 },
    { key: "pointing", label: "Pointing", transient: true, hold: 2400 },
    { key: "sleeping", label: "Sleeping", transient: false, hold: 0 }
  ];

  var STATE_KEYS = {};
  STATES.forEach(function (s) { STATE_KEYS[s.key] = s; });

  // Whole-character pose: tx/ty px, rot deg, sx/sy scale, breathing/bob
  // amplitudes, gaze, and face/arm/leg descriptor sets resolved by the rig
  // layer below (kept for backward compatibility and honest part targets).
  var POSES = {
    idle: {
      tx: 0, ty: 0, rot: 0, sx: 1, sy: 1,
      breath: 0.014, breatheHz: 0.9, bob: 0.9,
      gaze: { x: 0, y: 0 },
      face: { eyes: "center", brows: "neutral", mouth: "neutral" },
      arms: "rest", legs: "stand"
    },
    listening: {
      tx: 0, ty: 0, rot: 0, sx: 1, sy: 1,
      breath: 0.019, breatheHz: 1.2, bob: 1,
      gaze: { x: 0.1, y: 0 },
      face: { eyes: "listening", brows: "gentle", mouth: "neutral" },
      arms: "rest", legs: "stand"
    },
    thinking: {
      tx: 0, ty: -3, rot: -2.5, sx: 1, sy: 0.99,
      breath: 0.011, breatheHz: 0.62, bob: 0.4,
      gaze: { x: 0.4, y: -0.3 },
      face: { eyes: "up_away", brows: "knit", mouth: "thin" },
      arms: "chin", legs: "stand"
    },
    happy: {
      tx: 0, ty: -1, rot: 0, sx: 1.02, sy: 1.02,
      breath: 0.02, breatheHz: 1.35, bob: 1.5,
      gaze: { x: 0, y: -0.15 },
      face: { eyes: "soft", brows: "gentle", mouth: "smile" },
      arms: "rest", legs: "stand"
    },
    // Excited reads as eager and open — wide eyes, lifted brows, a big smile
    // and raised arms — but it stays grounded: no upward offset, no bigger bob
    // and no bouncing legs, so excitement can never look like jumping.
    excited: {
      tx: 0, ty: 0, rot: 0, sx: 1.02, sy: 1.03,
      breath: 0.024, breatheHz: 1.6, bob: 1.5,
      gaze: { x: 0, y: -0.2 },
      face: { eyes: "wide", brows: "lifted", mouth: "big_smile" },
      arms: "lift", legs: "stand"
    },
    sad: {
      tx: 0, ty: 1, rot: 0, sx: 0.98, sy: 0.97,
      breath: 0.011, breatheHz: 0.72, bob: 0.4,
      gaze: { x: 0, y: 0.15 },
      face: { eyes: "lowered", brows: "knit", mouth: "frown" },
      arms: "droop", legs: "stand"
    },
    surprised: {
      tx: 0, ty: -2, rot: 2, sx: 1.05, sy: 1.06,
      breath: 0.028, breatheHz: 1.9, bob: 2.6,
      gaze: { x: 0, y: 0 },
      face: { eyes: "wide", brows: "arched", mouth: "open" },
      arms: "recoil", legs: "stand"
    },
    confused: {
      tx: -1, ty: 0, rot: -3, sx: 0.99, sy: 0.99,
      breath: 0.015, breatheHz: 1, bob: 0.7,
      gaze: { x: -0.3, y: -0.1 },
      face: { eyes: "squint", brows: "asym", mouth: "wry" },
      arms: "chin", legs: "shift"
    },
    concerned: {
      tx: 0, ty: 0.5, rot: 1, sx: 0.995, sy: 0.99,
      breath: 0.012, breatheHz: 0.82, bob: 0.5,
      gaze: { x: -0.2, y: 0.1 },
      face: { eyes: "narrow", brows: "worried", mouth: "concern" },
      arms: "clasp", legs: "stand"
    },
    curious: {
      tx: 1, ty: -3, rot: 2.5, sx: 1.01, sy: 1,
      breath: 0.015, breatheHz: 1.1, bob: 1.1,
      gaze: { x: 0.3, y: -0.25 },
      face: { eyes: "wide_half", brows: "one_up", mouth: "open_small" },
      arms: "chin", legs: "lean"
    },
    greeting: {
      tx: 0, ty: -1, rot: 1.5, sx: 1.01, sy: 1.01,
      breath: 0.018, breatheHz: 1.25, bob: 1.6,
      gaze: { x: -0.4, y: 0.1 },
      face: { eyes: "soft", brows: "gentle", mouth: "smile" },
      arms: "wave", legs: "stand"
    },
    waving: {
      tx: 0, ty: -1, rot: 0, sx: 1.01, sy: 1.01,
      breath: 0.02, breatheHz: 1.3, bob: 1.6,
      gaze: { x: 0.3, y: -0.1 },
      face: { eyes: "soft", brows: "gentle", mouth: "smile" },
      arms: "wave", legs: "stand"
    },
    pointing: {
      tx: 0, ty: -1, rot: 1, sx: 1.01, sy: 1,
      breath: 0.017, breatheHz: 1.1, bob: 1.2,
      gaze: { x: 0.8, y: 0 },
      face: { eyes: "center", brows: "gentle", mouth: "neutral" },
      arms: "point_l", legs: "stand"
    },
    speaking: {
      tx: 0, ty: 0, rot: 0, sx: 1, sy: 1,
      breath: 0.024, breatheHz: 1.6, bob: 1.3,
      gaze: { x: 0, y: -0.1 },
      face: { eyes: "center", brows: "neutral", mouth: "speak" },
      arms: "rest", legs: "stand"
    },
    sleeping: {
      tx: 0, ty: 3, rot: 5, sx: 0.99, sy: 0.94,
      breath: 0.03, breatheHz: 0.4, bob: 0.2,
      gaze: { x: 0, y: 1 },
      face: { eyes: "closed", brows: "neutral", mouth: "soft" },
      arms: "rest", legs: "sit"
    }
  };

  // Mood multipliers on breath/bob per state.
  var MOOD = {
    idle: 1, listening: 1.1, thinking: 0.8, happy: 1.3, excited: 1.6,
    sad: 0.7, surprised: 1.5, confused: 1, concerned: 0.85,
    curious: 1.15, greeting: 1.4, speaking: 1.3, waving: 1.4, pointing: 1.2,
    sleeping: 0.5
  };

  // One-shot whole-body gestures (idle life + state entry).
  var GESTURES = {
    blink: {
      dur: 150, originY: 35,
      keys: [
        { at: 0, sy: 1 },
        { at: 0.3, sy: 0.93 },
        { at: 0.55, sy: 0.97 },
        { at: 1, sy: 1 }
      ]
    },
    look: {
      dur: 1100,
      keys: [
        { at: 0, rot: 0, tx: 0 },
        { at: 0.2, rot: 3.5, tx: 1.5 },
        { at: 0.8, rot: 3.5, tx: 1.5 },
        { at: 1, rot: 0, tx: 0 }
      ]
    },
    lookAlt: {
      dur: 1100,
      keys: [
        { at: 0, rot: 0, tx: 0 },
        { at: 0.2, rot: -3, tx: -1.5 },
        { at: 0.8, rot: -3, tx: -1.5 },
        { at: 1, rot: 0, tx: 0 }
      ]
    },
    shift: {
      dur: 1500,
      keys: [
        { at: 0, tx: 0, ty: 0 },
        { at: 0.15, tx: 1.5, ty: 0.5 },
        { at: 0.85, tx: 1.5, ty: 0.5 },
        { at: 1, tx: 0, ty: 0 }
      ]
    },
    nod: {
      dur: 850,
      keys: [
        { at: 0, rot: 0, ty: 0 },
        { at: 0.25, rot: 2.5, ty: -0.5 },
        { at: 0.55, rot: -1.5, ty: 0 },
        { at: 1, rot: 0, ty: 0 }
      ]
    },
    tiltHead: {
      dur: 1500,
      keys: [
        { at: 0, rot: 0 },
        { at: 0.2, rot: 4 },
        { at: 0.85, rot: 4 },
        { at: 1, rot: 0 }
      ]
    },
    leanFwd: {
      dur: 1200,
      keys: [
        { at: 0, tx: 0, rot: 0 },
        { at: 0.2, tx: 2, rot: -2 },
        { at: 0.85, tx: 2, rot: -2 },
        { at: 1, tx: 0, rot: 0 }
      ]
    },
    recoil: {
      dur: 600,
      keys: [
        { at: 0, tx: 0, ty: 0, rot: 0, sy: 1 },
        { at: 0.25, tx: -4, ty: -2, rot: -4, sy: 0.96 },
        { at: 1, tx: 0, ty: 0, rot: 0, sy: 1 }
      ]
    },
    leanBack: {
      dur: 1300,
      keys: [
        { at: 0, tx: 0, ty: 0, rot: 0 },
        { at: 0.2, tx: -2, ty: -1, rot: 3 },
        { at: 0.85, tx: -2, ty: -1, rot: 3 },
        { at: 1, tx: 0, ty: 0, rot: 0 }
      ]
    },
    wave: {
      dur: 2600,
      keys: [
        { at: 0, rot: 0 },
        { at: 0.1, rot: 1 },
        { at: 1, rot: 0 }
      ]
    }
  };

  // Entry gestures are all grounded, in-place reactions. There is deliberately
  // no "hop"/jump entry gesture: Cloud greets and reacts without ever leaving
  // the ground.
  var STATE_ENTRY_GESTURES = {
    thinking: ["leanBack"],
    surprised: ["recoil"],
    greeting: ["nod", "wave"],
    curious: ["look"],
    confused: ["tiltHead"],
    happy: ["nod"],
    excited: ["leanFwd"],
    sad: ["leanBack"],
    concerned: ["tiltHead"]
  };

  var IDLE_GESTURES = ["look", "lookAlt", "shift", "nod", "tiltHead", "leanFwd"];

  // ───────────────────────────────────────────────────────────────────────
  // Layer 3 — Rig descriptors → concrete part targets
  // ───────────────────────────────────────────────────────────────────────
  // eyes: pupil offsets (px, py) and openness (1 open … 0 closed).
  var EYE = {
    center:     { px: 0, py: 0, open: 1 },
    listening:  { px: 2.5, py: -0.6, open: 1 },
    up_away:    { px: 3, py: -3.2, open: 0.9 },
    soft:       { px: 0, py: -1, open: 1 },
    wide:       { px: 0, py: -2.2, open: 1 },
    lowered:    { px: 0, py: 1, open: 0.55 },
    squint:     { px: 0, py: -0.5, open: 0.45 },
    narrow:     { px: 0, py: 0.6, open: 0.5 },
    wide_half:  { px: 2.2, py: -2, open: 0.8 },
    closed:     { px: 0, py: 2, open: 0.05 }
  };

  // brows: per-side translateY (ty) and rotation (rot) in degrees.
  var BROW = {
    neutral: { lty: 0, rty: 0, lrot: 0, rrot: 0 },
    gentle:  { lty: -1, rty: -1, lrot: 0, rrot: 0 },
    knit:    { lty: -1, rty: -1, lrot: 3, rrot: -3 },
    lifted:  { lty: -4, rty: -4, lrot: 0, rrot: 0 },
    arched:  { lty: -5, rty: -4, lrot: -1, rrot: 1 },
    asym:    { lty: -3, rty: -1, lrot: 1, rrot: 0 },
    worried: { lty: 1, rty: 1, lrot: -2, rrot: 2 },
    one_up:  { lty: -4, rty: 0, lrot: 1, rrot: 0 }
  };

  // mouth: expression path `d` values anchored on the cloud face
  // (smile centered at ~216,346, half-width ~18, depth ~7).
  var MOUTH = {
    neutral:    "M198 344 C205 351 227 351 234 344",
    smile:      "M196 342 C204 354 228 354 236 342",
    big_smile:  "M194 340 C203 360 229 360 238 340",
    thin:       "M203 348 L229 348",
    frown:      "M198 349 C205 341 227 341 234 349",
    open:       "M198 344 C201 354 231 354 234 344 C231 335 201 335 198 344",
    wry:        "M198 344 C202 351 226 345 235 342",
    concern:    "M199 347 C216 347 222 342 233 340",
    open_small: "M201 345 C203 352 229 352 231 345 C230 340 202 340 201 345",
    soft:       "M199 344 C204 348 228 348 233 344",
    speak:      "M199 342 C201 352 231 352 233 342"
  };

  // Amplitude-driven talk shapes. Level 0 is byte-identical to MOUTH.speak so a
  // silent mouth is indistinguishable from the pre-amplitude behaviour. Each
  // entry is a flat [x1,y1,cx1,cy1,cx2,cy2,x2,y2] cubic so levels can be lerped
  // numerically instead of by string surgery.
  var MOUTH_TALK = [
    [199, 342, 201, 352, 231, 352, 233, 342],
    [199, 342, 200, 356, 232, 356, 233, 342],
    [198, 341, 199, 361, 233, 361, 234, 341],
    [197, 341, 197, 366, 235, 366, 236, 341]
  ];

  function talkMouthPath(level) {
    var lv = level < 0 ? 0 : level > 1 ? 1 : level;
    var span = MOUTH_TALK.length - 1;
    var pos = lv * span;
    var i = Math.floor(pos);
    var frac = pos - i;
    if (i >= span) return talkPath(MOUTH_TALK[span]);
    var a = MOUTH_TALK[i], b = MOUTH_TALK[i + 1];
    var out = [];
    for (var n = 0; n < a.length; n++) out.push(a[n] + (b[n] - a[n]) * frac);
    return talkPath(out);
  }

  function talkPath(p) {
    // Rounded so the per-frame attribute stays short and stable; float noise
    // from the ease would otherwise bloat the DOM string every frame.
    var r = function (n) { return Math.round(n * 100) / 100; };
    return "M" + r(p[0]) + " " + r(p[1]) +
      " C" + r(p[2]) + " " + r(p[3]) + " " + r(p[4]) + " " + r(p[5]) + " " + r(p[6]) + " " + r(p[7]);
  }

  // ───────────────────────────────────────────────────────────────────────
  // Engine state
  // ───────────────────────────────────────────────────────────────────────
  var _state = "idle";
  var _stable = "idle";
  var _el = null;
  var _rig = null;          // resolved rig { parts } or null (flat fallback)
  var _running = false;
  var _dragging = false;
  var _dragListenersBound = false;
  var _dragEndAt = 0;       // timestamp the last drag ended (grace period)
  var _framesDrawn = 0;     // diagnostic: frames that fully rendered
  var _lastFramesAt = 0;    // diagnostic: timestamp of last rendered frame
  var _intensity = 0.5;
  var _reduced = false;
  var _rafId = 0;
  var _lastT = 0;
  var _clock = 0;
  var _returnTimer = 0;
  var _onChangeCb = null;
  var _demoTimer = 0;

  // Amplitude-driven speech level (0..1). _speechLive is true only while an
  // external tap (the TTS player analyser in static/cloud_voice.js) is feeding
  // real audio levels. While it is false the mouth falls back to the original
  // synthetic pulse, so nothing changes for callers that do not wire a tap.
  var _speechTarget = 0;
  var _speechLevel = 0;
  var _speechLive = false;
  var _speechMuteAt = 0;
  var SPEECH_STALE_MS = 1200;

  function setSpeechLevel(v) {
    var n = Number(v);
    if (!isFinite(n)) return false;
    _speechTarget = n < 0 ? 0 : n > 1 ? 1 : n;
    _speechLive = true;
    // Every push refreshes the window, so pauses BETWEEN words keep the mouth
    // amplitude-driven. The timeout only catches a tap that actually died.
    _speechMuteAt = performance.now() + SPEECH_STALE_MS;
    return true;
  }

  function clearSpeechLevel() {
    _speechTarget = 0;
    _speechLive = false;
    _speechMuteAt = 0;
    return true;
  }

  // The tap can go silent mid-utterance (a pause between sentences). Drop back
  // to the synthetic pulse only after a real gap, never mid-word.
  function speechActive(now) {
    if (_speechLive && _speechMuteAt && now > _speechMuteAt) {
      _speechLive = false;
      _speechTarget = 0;
    }
    return _speechLive;
  }

  function decaySpeechLevel(dt) {
    var k = dt > 0 ? Math.min(1, dt / 60) : 1;
    if (_speechLevel < _speechTarget) {
      // Fast attack so plosives read crisply.
      _speechLevel += (_speechTarget - _speechLevel) * (k * 0.65);
    } else {
      // Slower release so the mouth closes naturally between syllables.
      _speechLevel += (_speechTarget - _speechLevel) * (k * 0.3);
    }
    if (_speechLevel < 0.0005) _speechLevel = 0;
    return _speechLevel;
  }

  // Smoothed whole-body pose easing.
  var _cur = {
    tx: 0, ty: 0, rot: 0, sx: 1, sy: 1, breath: 0.014, breatheHz: 0.9,
    bob: 0.9, gx: 0, gy: 0,
    eyePx: 0, eyePy: 0, eyeOpen: 1,
    blTy: 0, brTy: 0, blRot: 0, brRot: 0,
    hdTx: 0, hdTy: 0, hdRot: 0
  };

  // Active one-shot gesture.
  var _gesture = null;
  var _gestureStart = 0;
  // Blink timing so we can do quick vs. slow (sleepy/long) blinks.
  var _blinkDur = 150;

  // ─ Explicit gaze control (independent of pose presets).
  // A normalized target (x,y in -1..1) the eyes/head lean toward, set via
  // lookToward/lookAt. null = follow the pose's built-in gaze. Values ease
  // (`_cur.eyePx/eyePy`) so pupils never snap.
  var _lookTarget = null;      // { x, y } normalized or null
  var _lookHoldUntil = 0;      // ms timestamp the explicit gaze should end (0 = hold)
  var _lookSeq = 0;            // monotonic guard so a finished gaze isn't reapplied

  // One-shot arm gesture (wave / point / welcome / talk), timed independently
  // of the emotional state so Cloud can gesture on demand without a state swap.
  var _armGest = null;         // { kind, start, dur } or null

  // Randomized idle-life scheduler timestamps (ms).
  var _nextBlink = 0;
  var _nextLook = 0;
  var _nextShift = 0;
  var _nextGesture = 0;

  // ───────────────────────────────────────────────────────────────────────
  // Layer 2 — Movement / walking controller
  // ───────────────────────────────────────────────────────────────────────
  var MOVE_MARGIN = 16;                 // minimum px from viewport edges
  var WALK_SPEED = 34;                  // px / second
  var DRAG_GRACE_MS = 700;              // don't dart away right after a drag

  var _walk = {
    active: false,       // currently moving
    paused: false,       // paused due to drag/hidden
    dir: -1,             // -1 left, 1 right (for continuous walking)
    speed: WALK_SPEED,
    target: null,        // {x, y} optional walk-to-target
    baseX: 0, baseY: 0,  // where walking started (reserved for wander re-anchor)
    acc: 0               // sub-pixel step accumulator for reduced-motion amble
  };

  // Emotional walking profiles — cadence (steps/sec), step amplitude (deg),
  // speed multiplier, and a vertical bounce added on top of the step bob.
  // No profile may bounce: Cloud glides and breathes, it never jumps or skips.
  // A thinking Cloud still potters and a sad Cloud still drags, but every
  // profile stays grounded (bounce <= 0) so the cloud never hops.
  var WALK_PROFILES = {
    idle:      { cadence: 2.4, amp: 16, speed: 1.0,  bounce: 0.0 },
    calm:      { cadence: 2.4, amp: 16, speed: 1.0,  bounce: 0.0 },
    thinking:  { cadence: 1.5, amp: 10, speed: 0.65, bounce: 0.0 },
    happy:     { cadence: 2.6, amp: 15, speed: 1.1,  bounce: 0.0 },
    excited:   { cadence: 2.8, amp: 15, speed: 1.15, bounce: 0.0 },
    surprised: { cadence: 2.1, amp: 12, speed: 0.8,  bounce: 0.0 },
    sad:       { cadence: 1.2, amp: 8,  speed: 0.5,  bounce: 0.0 },
    sleepy:    { cadence: 1.1, amp: 7,  speed: 0.4,  bounce: 0.0 }
  };

  // prefers-reduced-motion must DAMPEN the loop, never stop it. A completely
  // still Cloud looks broken; under reduce we keep the engine alive and states
  // distinguishable with only a fraction of the continuous motion.
  function motionScale() {
    return _reduced ? 0.12 : 1;
  }

  function walkProfile() {
    return WALK_PROFILES[_state] || WALK_PROFILES.idle;
  }

  // Extra life factor for arm swings while walking (0.5 = dragging arms).
  function moodScale() {
    return Math.max(0.4, walkProfile().amp / 16);
  }

  function safeMargin() {
    // Respect mobile safe areas when present (bottom inset), else padding.
    var inset = 0;
    if (_el) {
      var cs = _el && window.getComputedStyle ? window.getComputedStyle(_el) : null;
      if (cs && cs.bottom && /env\(safe-area-inset-bottom/.test(cs.bottom)) inset = 0;
    }
    return MOVE_MARGIN;
  }

  function viewportRect() {
    var w = window.innerWidth || document.documentElement.clientWidth || 0;
    var h = window.innerHeight || document.documentElement.clientHeight || 0;
    var m = safeMargin();
    return { x: m, y: m, w: Math.max(1, w - m * 2), h: Math.max(1, h - m * 2) };
  }

  function elementPos() {
    if (!_el) return { x: 0, y: 0 };
    var r = _el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  }

  function clampToViewport(x, y) {
    var v = viewportRect();
    var p = elementPos();
    var w = p.w || 96, h = p.h || 140;
    var maxX = Math.max(v.x, window.innerWidth - w - v.x);
    var maxY = Math.max(v.y, window.innerHeight - h - v.y);
    var nx = Math.max(v.x, Math.min(maxX, x));
    var ny = Math.max(v.y, Math.min(maxY, y));
    return { x: nx, y: ny };
  }

  function placeElement(x, y) {
    if (!_el) return;
    var c = clampToViewport(x, y);
    _el.style.right = "auto";
    _el.style.bottom = "auto";
    _el.style.left = Math.round(c.x) + "px";
    _el.style.top = Math.round(c.y) + "px";
  }

  function beginWalkTo(x, y) {
    var v = viewportRect();
    _walk.active = true;
    _walk.paused = false;
    _walk.target = { x: x, y: y };
    _walk.acc = 0;
    var p = elementPos();
    _walk.baseX = p.x;
    _walk.baseY = p.y;
  }

  // ───────────────────────────────────────────────────────────────────────
  // Utility
  // ───────────────────────────────────────────────────────────────────────
  function rand(min, max) { return min + Math.random() * (max - min); }
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function poseFor(key) { return POSES[key] || POSES.idle; }

  function entryGesture(key) {
    var g = STATE_ENTRY_GESTURES[key];
    if (!g) return null;
    return GESTURES[g[Math.floor(Math.random() * g.length)]];
  }

  // Evaluate a gesture at normalized time 0..1 into additive fields.
  function evalGesture(gest, at) {
    var out = { tx: 0, ty: 0, rot: 0, sx: 0, sy: 0, originY: null };
    if (!gest) return out;
    if (gest.originY != null) out.originY = gest.originY;
    var keys = gest.keys;
    if (!keys || !keys.length) return out;
    var t = Math.max(0, Math.min(1, at));
    var l = 0, r = keys.length - 1;
    if (t <= keys[0].at) l = r = 0;
    else if (t >= keys[r].at) l = r = r;
    else {
      for (var i = 0; i < keys.length - 1; i++) {
        if (t >= keys[i].at && t <= keys[i + 1].at) { l = i; r = i + 1; break; }
      }
    }
    var k0 = keys[l], k1 = keys[r], span = (k1.at - k0.at) || 1;
    var et = easeInOutCubic((t - k0.at) / span);
    out.tx = lerp(k0.tx || 0, k1.tx || 0, et);
    out.ty = lerp(k0.ty || 0, k1.ty || 0, et);
    out.rot = lerp(k0.rot || 0, k1.rot || 0, et);
    out.sx = lerp((k0.sx || 0) - 1, (k1.sx || 0) - 1, et);
    out.sy = lerp((k0.sy || 0) - 1, (k1.sy || 0) - 1, et);
    return out;
  }

  // Gesture keyframe expansion into an optional arm-channel (used by wave).
  function gestureArmState(gest, at) {
    if (!gest || gest !== GESTURES.wave) return 0;
    var t = Math.max(0, Math.min(1, at || 0));
    var phase = Math.sin(t * Math.PI * 4); // 2 full waves
    return phase * 1;
  }

  function isCharacterHidden(el) {
    if (!el || !el.isConnected) return true;
    var cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return true;
    if (cs.opacity === "0") return true;
    return false;
  }

  function scheduleIdleLife(now) {
    _nextBlink = now + rand(2600, 6800);
    _nextLook = now + rand(7000, 16000);
    _nextShift = now + rand(11000, 22000);
    _nextGesture = now + rand(24000, 50000);
  }

  function triggerBlink(speed) {
    // speed: "quick" (fast blink), "slow" (long sleepy blink), "none" for a
    // barely-there micro-blink; default normal.
    _blinkDur = speed === "quick" ? 90 : (speed === "slow" ? 340 : (speed === "none" ? 45 : 150));
    _gesture = GESTURES.blink;
    _gestureStart = performance.now();
    _nextBlink = _gestureStart + rand(2600, 6800);
  }

  function triggerIdleGesture(kind) {
    var g = GESTURES[kind];
    if (!g) return;
    _gesture = g;
    _gestureStart = performance.now();
  }

  // Evaluate a timed one-shot arm gesture (wave / point / welcome / talk) into
  // per-arm degrees. Returns null when the gesture has finished.
  function armGestureAt(g, elapsed) {
    if (!g) return null;
    var KIND = g.kind;
    var dur = g.dur || 2400;
    var t = elapsed / dur;
    if (t >= 1) { _armGest = null; return null; }
    var restL = 4, restR = -4;
    if (KIND === "wave") {
      return { left: restL, right: 30 + Math.sin(t * TAU * 7) * 14 };
    }
    if (KIND === "welcome") {
      return { left: -74 + Math.sin(t * TAU) * 6, right: 74 - Math.sin(t * TAU) * 6 };
    }
    if (KIND === "point_up") {
      return { left: -95, right: 95 };
    }
    if (KIND === "point_down") {
      return { left: 20, right: -20 };
    }
    if (KIND === "point_l") {
      return { left: restL, right: 112 };
    }
    if (KIND === "point_r") {
      return { left: -112, right: restR };
    }
    if (KIND === "talk") {
      return {
        left: restL + Math.sin(t * TAU * 6) * 6,
        right: restR - Math.sin(t * TAU * 6) * 6
      };
    }
    return null;
  }

  function triggerArmGesture(kind, dur) {
    _armGest = { kind: kind, start: performance.now(), dur: dur || 2400 };
  }

  // ───────────────────────────────────────────────────────────────────────
  // Element + rig resolution
  // ───────────────────────────────────────────────────────────────────────
  function resolveElement() {
    var a = document.getElementById(CHARACTER_ID);
    if (a && a.isConnected) return a;
    var b = document.getElementById(MINI_ORB_ID);
    if (b && b.isConnected) return b;
    return null;
  }

  function resolveRig(el) {
    if (!el) return null;
    if (window.VMCloudRig && typeof window.VMCloudRig.mount === "function") {
      try {
        var rig = window.VMCloudRig.mount(el);
        if (rig && rig.parts) return rig;
      } catch (e) { }
    }
    return null;
  }

  function adopt(el) {
    if (el.getAttribute && el.getAttribute("data-vm-anim") === "1") return;
    el.setAttribute("data-vm-anim", "1");
    el.style.transformOrigin = "50% 100%";
    el.style.willChange = "transform";
    el.addEventListener("pointerdown", function () {
      _dragging = true;
      if (_walk.active) { _walk.paused = true; _walk.pausedAt = performance.now(); }
    });
    // Drag-release is cleared document-wide so a pointerup that lands outside
    // the character (a real browser goes there all the time) can never leave
    // the frame loop permanently gated.
    if (!_dragListenersBound) {
      _dragListenersBound = true;
      document.addEventListener("pointerup", endDrag);
      document.addEventListener("pointercancel", endDrag);
    }
  }

  function endDrag() {
    _dragging = false;
    _dragEndAt = performance.now();
    if (_walk.active) _walk.paused = true; // hold after drag
  }

  // ───────────────────────────────────────────────────────────────────────
  // Rig part rendering (Layer 3)
  // ───────────────────────────────────────────────────────────────────────
  function rigPartTransform(g, tx, ty, rot, sx, sy) {
    if (!g) return;
    var parts = [];
    if (sx != null || sy != null) {
      parts.push("translate(" + (tx || 0).toFixed(2) + "px," + (ty || 0).toFixed(2) + "px)");
      parts.push("rotate(" + (rot || 0).toFixed(2) + "deg)");
    } else {
      parts.push("translate(" + (tx || 0).toFixed(2) + "px," + (ty || 0).toFixed(2) + "px)");
      parts.push("rotate(" + (rot || 0).toFixed(2) + "deg)");
    }
    if (sx != null && sy != null) parts.push("scale(" + sx.toFixed(3) + "," + sy.toFixed(3) + ")");
    g.style.transform = parts.join(" ");
  }

  function renderRig(pose, now) {
    if (!_rig || !_rig.parts) return;
    var p = _rig.parts;
    var amp = motionScale();

    // Face descriptors → targets.
    var eyeKey = pose.face.eyes;
    var eyeT = EYE[eyeKey] || EYE.center;
    var browKey = pose.face.brows;
    var browT = BROW[browKey] || BROW.neutral;

    // Speaking pulses the mouth open/closed. When a real audio tap is feeding
    // levels the mouth is driven by actual TTS amplitude (mouth sync); without
    // one it falls back to the original synthetic pulse.
    var mouthD = MOUTH[pose.face.mouth] || MOUTH.neutral;
    if (pose.face.mouth === "speak") {
      if (speechActive(now)) {
        mouthD = talkMouthPath(_speechLevel);
      } else {
        var pulse = Math.abs(Math.sin(_clock / 1000 * TAU * 4));
        mouthD = pulse > 0.45 ? MOUTH.big_smile : MOUTH.smile;
      }
    }

    // Ease eye/brow targets for natural, non-snapping motion. When an explicit
    // look target is active the eyes ease toward it instead of the pose preset.
    var k = 0.14;
    var eyeTx = (_lookTarget ? (_lookTarget.x * 2.2) : eyeT.px) * amp;
    var eyeTy = (_lookTarget ? (_lookTarget.y * 2.2) : eyeT.py) * amp;
    _cur.eyePx += (eyeTx - _cur.eyePx) * k;
    _cur.eyePy += (eyeTy - _cur.eyePy) * k;
    _cur.eyeOpen += (eyeT.open - _cur.eyeOpen) * k;

    // Blink collapses eye openness briefly (duration picked when triggered).
    var blinkScale = 1;
    if (_gesture === GESTURES.blink) {
      var bElapsed = (now - _gestureStart) / _blinkDur;
      if (bElapsed >= 0 && bElapsed < 1) blinkScale = Math.max(0.08, 1 - Math.abs(Math.sin(bElapsed * Math.PI * 2)) * 0.9);
    }
    var eyeOpen = clamp(_cur.eyeOpen * blinkScale, 0.04, 1);

    // Pupil gaze. _cur.eyePx/eyePy are already eased toward the active look
    // target (or the pose preset); the pose gaze nudges them a little further.
    var gx = _cur.eyePx;
    var gy = _cur.eyePy;
    gx += pose.gaze.x * 2 * amp;
    gy += pose.gaze.y * 2 * amp;

    if (p.leftPupil) p.leftPupil.style.transform = "translate(" + gx.toFixed(2) + "px," + gy.toFixed(2) + "px)";
    if (p.rightPupil) p.rightPupil.style.transform = "translate(" + gx.toFixed(2) + "px," + gy.toFixed(2) + "px)";

    // Blink / eye openness via scaleY on each eye group.
    if (p.leftEye) p.leftEye.style.transform = "scaleY(" + eyeOpen.toFixed(3) + ")";
    if (p.rightEye) p.rightEye.style.transform = "scaleY(" + eyeOpen.toFixed(3) + ")";

    // Eyebrows.
    _cur.blTy += (browT.lty * amp - _cur.blTy) * k;
    _cur.brTy += (browT.rty * amp - _cur.brTy) * k;
    _cur.blRot += (browT.lrot * amp - _cur.blRot) * k;
    _cur.brRot += (browT.rrot * amp - _cur.brRot) * k;
    if (p.leftEyebrow) rigPartTransform(p.leftEyebrow, 0, _cur.blTy, _cur.blRot, null, null);
    if (p.rightEyebrow) rigPartTransform(p.rightEyebrow, 0, _cur.brTy, _cur.brRot, null, null);

    // Mouth expression path swap.
    var mouthPath = p.mouth && p.mouth.querySelector ? p.mouth.querySelector("path") : null;
    if (mouthPath && mouthPath.getAttribute("data-expression") !== pose.face.mouth) {
      mouthPath.setAttribute("d", mouthD);
      mouthPath.setAttribute("data-expression", pose.face.mouth);
    } else if (mouthPath && pose.face.mouth === "speak") {
      mouthPath.setAttribute("d", mouthD);
    }

    // Ease eye/brow targets, then (when a look target or pose gaze is active)
    // bump the body toward it so the eyes lead and the cloud follows, giving a
    // lead -> follow chain without a separate head to turn.
    var lookX = _lookTarget ? _lookTarget.x : pose.gaze.x;
    var lookY = _lookTarget ? _lookTarget.y : pose.gaze.y;
    // Moving: the cloud leans slightly toward the direction of travel.
    if (_walk.active && !_walk.paused) lookX += _walk.dir * 0.35;
    // Autonomous drift: the body lags a touch behind the drift direction so
    // the cloud reads as a physical body being carried along.
    lookX += clamp(_drift.vx / DRIFT_SPEED_MAX, -1, 1) * 0.22;
    var headTxT = (lookX * 7) * amp;
    var headTyT = (lookY * 5 - 1) * amp;
    var headRotT = (lookX * 6) * amp;
    var headK = 0.07;
    _cur.hdTx += (headTxT - _cur.hdTx) * headK;
    _cur.hdTy += (headTyT - _cur.hdTy) * headK;
    _cur.hdRot += (headRotT - _cur.hdRot) * headK;
    if (p.body) rigPartTransform(p.body, _cur.hdTx, _cur.hdTy, _cur.hdRot, null, null);

    // Arms — rotation around the shoulder pivot (degrees).
    renderArms(pose, now);

    // Legs — dangle / trail while floating.
    renderLegs(pose, now, eyeOpen);

    // Cloud detail layer (cheeks, nose, hands, shadow).
    renderCloudParts(pose, now);
  }

  function armAngle(pose, t, now) {
    // Returns [leftDeg, rightDeg] relative to rest. Sign convention follows the
    // existing known-good poses ("lift" = both inward-up): left arm counter-
    // clockwise is inward-up, right arm clockwise is inward-up. The cloud's
    // arms are soft puffs, so the range stays small and the big values are
    // only used for the deliberate one-shot gestures.
    var amp = motionScale();
    var restL = 4, restR = -4; // slight neutral outward hang
    var left = restL, right = restR;
    switch (pose.arms) {
      case "rest":
        break;
      case "chin": // thinking hand-to-face: right arm up toward chin
        left = restL;
        right = 24 + Math.sin(_clock / 1000 * 0.6) * 2 * amp;
        break;
      case "lift": // excited: both up
        left = -34; right = 34;
        break;
      case "droop": // sad: both hang heavy / out
        left = 12; right = -12;
        break;
      case "recoil": // surprised recoil
        left = -16; right = 16;
        break;
      case "clasp": // concerned: both brought inward-forward
        left = -12; right = 12;
        break;
      case "wave": // greeting: one arm waves in the air
        left = restL;
        right = 30 + Math.sin(_clock / 1000 * TAU * 3.2) * 11 * amp;
        break;
      case "point_up":
        left = -95; right = 95;
        break;
      case "point_l": // point toward image-left (west)
        left = restL;
        right = 112 + Math.sin(_clock / 1000 * 2.4) * 3 * amp;
        break;
      case "point_r": // point toward image-right (east)
        left = -112 + Math.sin(_clock / 1000 * 2.4) * 3 * amp;
        right = restR;
        break;
      case "point_d": // point down
        left = 20; right = -20;
        break;
      case "welcome": // open arms wide (welcoming)
        left = -64; right = 64;
        break;
      case "talk": // conversational hand gestures
        left = restL + Math.sin(_clock / 1000 * TAU * 2.8) * 5 * amp;
        right = restR - Math.sin(_clock / 1000 * TAU * 2.8) * 5 * amp;
        break;
      default:
        left = restL; right = restR;
    }
    // Occasional speaking gesture adds a little arm life.
    if (pose.arms === "rest" && _state === "speaking") {
      left += Math.sin(_clock / 1000 * TAU * 2.6) * 3 * amp;
    }
    // One-shot arm gestures override the pose (they run a timed script of
    // arm poses so a future Brain can trigger wave/point/welcome on demand).
    if (_armGest) {
      var am = armGestureAt(_armGest, (now - _armGest.start));
      if (am) { left = am.left; right = am.right; }
    }
    return [left, right];
  }

  function renderArms(pose, now) {
    if (!_rig || !_rig.parts) return;
    var p = _rig.parts;
    var ang = armAngle(pose, 0, now);
    // Counter-swing the arms while actually stepping (emotionally paced).
    if (_walk.active && !_walk.paused) {
      var s = Math.sin(_clock / 1000 * TAU * walkProfile().cadence) * (9 * moodScale()) * motionScale();
      ang[0] += -s;
      ang[1] += s;
    }
    if (p.leftArm) rigPartTransform(p.leftArm, 0, 0, ang[0], null, null);
    if (p.rightArm) rigPartTransform(p.rightArm, 0, 0, ang[1], null, null);
  }

  // Legs hang beneath the cloud and dangle — they never step. Horizontal
  // travel (walk or autonomous drift) trails them behind, and the pose adds a
  // small personality swing on top.
  function renderLegs(pose, now, eyeOpen) {
    if (!_rig.parts) return;
    var p = _rig.parts;
    var t = _clock / 1000;
    var amp = motionScale();

    // Idle dangle: a lazy pendulum either side of centre.
    var dangle = Math.sin(t * TAU * 0.6) * 2.4 * amp;
    var leftLeg = dangle, rightLeg = -dangle;

    // Travel: legs trail the direction of motion (normalized), eased.
    var travel = clamp((_walk.active && !_walk.paused ? _walk.dir * 0.7 : 0) +
      clamp(_drift.vx / DRIFT_SPEED_MAX, -1, 1) * 0.5, -1, 1) * 9 * amp;
    leftLeg += travel;
    rightLeg += travel;

// No "bounce" leg mode: Cloud never bounces its legs (that read as jumping).
    if (pose.legs === "sit") {
      leftLeg += 4; rightLeg -= 2;             // relaxed / sleepy droop
    } else if (pose.legs === "shift") {
      leftLeg += 3; rightLeg -= 1;
    } else if (pose.legs === "lean") {
      leftLeg -= 4; rightLeg += 4;
    }

    leftLeg = clamp(leftLeg, -18, 18);
    rightLeg = clamp(rightLeg, -18, 18);
    if (p.leftLeg) rigPartTransform(p.leftLeg, 0, 0, leftLeg, null, null);
    if (p.rightLeg) rigPartTransform(p.rightLeg, 0, 0, rightLeg, null, null);
  }

  // Cloud detail layer (runs inside the SAME frame loop — never a second
  // engine): the ground shadow tracks how high the cloud is floating, the
  // cheeks flush while expressive, the nose twitches, and the hands waggle
  // during arm gestures.
  function renderCloudParts(pose, now) {
    if (!_rig || !_rig.parts) return;
    var p = _rig.parts;
    var t = _clock / 1000;
    var amp = motionScale();

    // Ground shadow: shrinks and fades as the cloud floats higher, so the
    // hover height reads as real altitude rather than a static decoration.
    if (p.shadow) {
      var lift = clamp(-_cur.bob * 0.06, -1, 1.2);
      var sScale = 1 - lift * 0.16;
      var sOpacity = (0.95 - lift * 0.45).toFixed(3);
      p.shadow.style.transform = "scale(" + sScale.toFixed(3) + ",1)";
      p.shadow.style.opacity = sOpacity;
    }

    // Cheeks: flush a little while expressive, calm at rest.
    var expressive = pose.face.mouth === "smile" || pose.face.mouth === "big_smile" ||
      pose.face.mouth === "open" || _state === "happy" || _state === "excited" ||
      _state === "greeting" || _state === "speaking";
    var cheekScale = expressive
      ? 1 + Math.abs(Math.sin(t * TAU * 1.7)) * 0.10 * amp
      : 1;
    if (p.leftCheek) rigPartTransform(p.leftCheek, 0, 0, 0, cheekScale, cheekScale);
    if (p.rightCheek) rigPartTransform(p.rightCheek, 0, 0, 0, cheekScale, cheekScale);

    // Nose: tiny twitch while talking or thinking — the only "micro" motion.
    if (p.nose) {
      var noseY = (_state === "speaking"
        ? Math.sin(t * TAU * 6) * 1.6 * amp
        : Math.sin(t * TAU * 0.7) * 0.8 * amp);
      rigPartTransform(p.nose, 0, noseY, 0, null, null);
    }

    // Hands: waggle when an arm gesture is running or the cloud is talking;
    // otherwise a tiny idle sway keeps it alive but calm.
    var gesturing = !!_armGest || pose.arms !== "rest" || _state === "speaking";
    var wag = gesturing
      ? Math.sin(t * TAU * 9) * 26 * amp
      : Math.sin(t * TAU * 1.4) * 2 * amp;
    if (p.leftHand) rigPartTransform(p.leftHand, 0, 0, wag, null, null);
    if (p.rightHand) rigPartTransform(p.rightHand, 0, 0, wag, null, null);
  }

  // ───────────────────────────────────────────────────────────────────────
  // Movement stepping (Layer 2) — runs inside the frame loop
  // ───────────────────────────────────────────────────────────────────────
  function stepMovement(dt) {
    if (!_walk.active || _dragging || document.hidden) return;
    if (_walk.paused) {
      // Allow resume after a drag grace period if a target still exists.
      if (_walk.target && (performance.now() - _dragEndAt) > DRAG_GRACE_MS) _walk.paused = false;
      else return;
    }
    var p = elementPos();
    // Emotion paces the gait (happy/excited move quicker, sad/thinking drag);
  // prefers-reduced-motion slows everything to a gentle amble.
    var dist = WALK_SPEED * _walk.speed * walkProfile().speed * motionScale() * (dt / 1000);

    if (_walk.target) {
      var dx = _walk.target.x - p.x;
      var dy = _walk.target.y - p.y;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d < 2) { // arrived — Cloud settles in place, does not dart away.
        _walk.active = false;
        _walk.target = null;
        placeElement(p.x, p.y);
        return;
      }
      var nx = p.x + (dx / d) * dist;
      var ny = p.y + (dy / d) * dist;
      placeElement(nx, ny);
    } else {
      // Continuous wander in _walk.dir; bounce off viewport edges. Sub-pixel
      // steps (reduced motion makes them tiny) accumulate so tiny-but-real
      // movement still tips over into a visible tick.
      _walk.acc = _walk.acc || 0;
      _walk.acc += _walk.dir * dist;
      var step = Math.round(_walk.acc);
      if (step !== 0) {
        var c = clampToViewport(p.x + step, p.y);
        if (c.x === p.x || c.x <= viewportRect().x ||
            c.x >= window.innerWidth - (p.w || 96) - viewportRect().x) {
          _walk.dir = -_walk.dir;            // turn around at a boundary
          _walk.acc = 0;
        } else {
          placeElement(c.x, p.y);
          _walk.acc -= step;
        }
      }
    }
  }

  // ───────────────────────────────────────────────────────────────────────
  // Autonomous drift (Cloud floats on its own)
  // ───────────────────────────────────────────────────────────────────────
  // When nobody has told Cloud where to go, it should still feel alive: it
  // picks a slow point inside the viewport and eases toward it, pauses, then
  // picks another. This shares the ONE existing movement loop (placeElement)
  // and the ONE element position, so it can never fight a drag, a commanded
  // walk, or the persisted position.
  //
  // prefers-reduced-motion damps the drift to a slow in-place hover: the cloud
  // still breathes and blinks, but it does not travel across the screen.
  var DRIFT_ENABLED = true;
  var DRIFT_SPEED_MAX = 26;                  // px / second (the `norm` reference)
  var DRIFT_MIN_DWELL = 4200;               // ms hovering at a spot
  var DRIFT_MAX_DWELL = 11000;
  var DRIFT_REACH = 6;                      // px — close enough to arrive
  var DRIFT_POST_DRAG_MS = 2600;            // settle time after the user drops it

  var _drift = {
    enabled: true,
    on: true,          // autonomous travel is currently allowed
    target: null,      // { x, y } or null when just hovering
    vx: 0, vy: 0,      // last applied velocity (px/s), drives lean + legs
    accX: 0, accY: 0, // sub-pixel accumulators (placeElement rounds to whole px)
    nextAt: 0          // ms timestamp to pick the next drift target
  };

  // Drift never fights an explicit instruction, a drag, or the walk controller.
  function driftSuppressed() {
    // A short grace after a drag: Cloud stays put while the user reads where
    // they dropped it instead of immediately wandering off.
    if (_dragEndAt && (performance.now() - _dragEndAt) < DRIFT_POST_DRAG_MS) return true;
    return !_drift.enabled || !_drift.on || _walk.active || _dragging ||
      _walk.paused || document.hidden;
  }

  function clampTarget(x, y) {
    var v = viewportRect();
    var p = elementPos();
    var w = p.w || 132, h = p.h || 176;
    // Stay inside the walkable area so drift can never shove the cloud under
    // a nav bar or off-screen.
    var maxX = Math.max(v.x, window.innerWidth - w - v.x);
    var maxY = Math.max(v.y, window.innerHeight - h - v.y);
    return {
      x: Math.max(v.x, Math.min(maxX, x)),
      y: Math.max(v.y, Math.min(maxY, y))
    };
  }

  function pickDriftTarget() {
    var v = viewportRect();
    var p = elementPos();
    var w = p.w || 132, h = p.h || 176;
    // A wander step is deliberately short — Cloud should meander, not patrol.
    var spanX = Math.max(0, (window.innerWidth - w) - v.x * 2);
    var spanY = Math.max(0, (window.innerHeight - h) - v.y * 2);
    var reach = 210;
    // Sample a few candidates and keep the longest one: a single sample can
    // land on top of the cloud, which would read as "arrived" and silently
    // swallow the whole wander, leaving Cloud parked for another full dwell.
    var best = { x: p.x, y: p.y }, bestD = 0;
    for (var i = 0; i < 6; i++) {
      var nx = spanX ? clamp(p.x + rand(-reach, reach), v.x, v.x + spanX) : p.x;
      var ny = spanY ? clamp(p.y + rand(-reach * 0.7, reach * 0.7), v.y, v.y + spanY) : p.y;
      var dx = nx - p.x, dy = ny - p.y;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d > bestD) { bestD = d; best = { x: nx, y: ny }; }
      if (bestD > DRIFT_REACH * 3) break;
    }
    if (bestD > DRIFT_REACH) return best;
    // Still no room to wander (tiny viewport, or a very tight clamp): step a
    // guaranteed distance along whichever axis actually has space.
    var step = DRIFT_REACH * 3, dir = rand() < 0.5 ? -1 : 1;
    if (spanX >= step) return clampTarget(p.x + step * dir, p.y);
    if (spanY >= step) return clampTarget(p.x, p.y + step * dir);
    return clampTarget(p.x, p.y);
  }

  function scheduleDrift(now) {
    _drift.target = null;
    _drift.accX = 0; _drift.accY = 0;
    _drift.nextAt = now + rand(DRIFT_MIN_DWELL, DRIFT_MAX_DWELL);
  }

  // Ease toward the current drift target; on arrival schedule the next one.
  function stepDrift(dt, now) {
    if (!DRIFT_ENABLED) return;
    if (driftSuppressed()) {
      // Decay velocity so a stopped cloud doesn't keep leaning.
      _drift.vx = lerp(_drift.vx, 0, 0.2);
      _drift.vy = lerp(_drift.vy, 0, 0.2);
      return;
    }
    if (!_drift.target) {
      if (now < _drift.nextAt) return;
      _drift.target = pickDriftTarget();
    }
    var p = elementPos();
    var dx = _drift.target.x - p.x;
    var dy = _drift.target.y - p.y;
    var d = Math.sqrt(dx * dx + dy * dy);
    if (d <= DRIFT_REACH) {
      _drift.vx = 0; _drift.vy = 0;
      scheduleDrift(now);
      return;
    }
    // Under reduced motion the drift slows to a fraction of its pace.
    var speed = DRIFT_SPEED_MAX * (0.28 + walkProfile().speed * 0.4) * motionScale();
    var dist = Math.min(speed * (dt / 1000), d);
    var ux = dx / d, uy = dy / d;
    // placeElement rounds to whole pixels, and a slow drift covers well under
    // one pixel per frame — so the remainder is accumulated and only actually
    // placed once it tips over into a visible tick (same trick the walk
    // controller uses for reduced-motion ambling).
    _drift.accX += ux * dist;
    _drift.accY += uy * dist;
    var stepX = Math.round(_drift.accX);
    var stepY = Math.round(_drift.accY);
    if (stepX !== 0 || stepY !== 0) {
      var c = clampToViewport(p.x + stepX, p.y + stepY);
      // If an edge blocked us, drop the target and re-pick so the cloud
      // turns around instead of grinding against the margin.
      if ((c.x === p.x && stepX !== 0) || (c.y === p.y && stepY !== 0)) {
        _drift.accX = 0; _drift.accY = 0;
        _drift.target = null;
        _drift.nextAt = now + rand(DRIFT_MIN_DWELL, DRIFT_MAX_DWELL);
      } else {
        placeElement(c.x, c.y);
        _drift.accX -= stepX;
        _drift.accY -= stepY;
      }
    }
    // Record the intended velocity (px/s) so the body lean, legs and shadow
    // can read the drift direction even between visible ticks.
    _drift.vx = lerp(_drift.vx, ux * speed, 0.15);
    _drift.vy = lerp(_drift.vy, uy * speed, 0.15);
  }

  function setDrift(on) {
    _drift.on = !!on;
    if (!_drift.on) {
      _drift.target = null;
      _drift.accX = 0; _drift.accY = 0;
      _drift.vx = 0; _drift.vy = 0;
    }
    return _drift.on;
  }

  // ───────────────────────────────────────────────────────────────────────
  // Main loop
  // ───────────────────────────────────────────────────────────────────────
  function frame(now) {
    if (!_running) return;
    if (_rafId) cancelAnimationFrame(_rafId);
    if (!_el) {
      _el = resolveElement();
      if (!_el) { _rafId = requestAnimationFrame(frame); return; }
      adopt(_el);
    }
    if (_dragging || document.hidden || isCharacterHidden(_el)) {
      _rafId = requestAnimationFrame(frame);
      return;
    }
    var dt = Math.min(64, (now - _lastT) || 16);
    decaySpeechLevel(dt);
    _lastT = now;
    _clock += dt;
    _framesDrawn++;
    _lastFramesAt = now;

    if (!_rig) _rig = resolveRig(_el);

    // Auto-return from transient states to the last stable state.
    if (_returnTimer > 0) {
      _returnTimer -= dt;
      if (_returnTimer <= 0) {
        setState(_stable);
        _returnTimer = 0;
      }
    }

    // Randomized idle life (only for stable states).
    if (STATE_KEYS[_state] && !STATE_KEYS[_state].transient) {
      if (now >= _nextBlink) {
        // Mostly normal blinks, an occasional quick double-blink, a rare slow
        // sleepy one — blink tempo is part of Cloud's expressiveness.
        var r = Math.random();
        triggerBlink(r < 0.70 ? "normal" : (r < 0.88 ? "quick" : "slow"));
      }
      if (now >= _nextLook && !_gesture) {
        triggerIdleGesture(pick(["look", "lookAlt"]));
        _nextLook = now + rand(7000, 16000);
      }
      if (now >= _nextShift && !_gesture) {
        triggerIdleGesture("shift");
        _nextShift = now + rand(11000, 22000);
      }
      if (now >= _nextGesture && !_gesture && _state === "idle") {
        triggerIdleGesture(pick(IDLE_GESTURES));
        _nextGesture = now + rand(24000, 50000);
      }
    }

    // Movement layer: an explicit walk wins, otherwise Cloud drifts on its own.
    stepMovement(dt);
    stepDrift(dt, now);

    // Explicit gaze expiry: a timed lookToward drops back to pose gaze.
    if (_lookTarget && _lookHoldUntil > 0 && now >= _lookHoldUntil) {
      _lookTarget = null;
      _lookHoldUntil = 0;
    }

    // Rig layer.
    renderRig(poseFor(_state), now);

    // Whole-body transform (both rig and flat-fallback paths).
    renderBody(poseFor(_state), now);

    _rafId = requestAnimationFrame(frame);
  }

  function renderBody(pose, now) {
    var amp = motionScale();
    var mood = MOOD[_state] || 1;
    var wrap = pose.breath * mood * _intensity * amp;
    var stride = pose.bob * mood * _intensity * amp;

    var gx = pose.gaze.x * 2 * _intensity * amp;
    var gy = pose.gaze.y * 1.5 * _intensity * amp;

    // Soft eased settling on the target pose.
    _cur.tx += ((pose.tx + gx) - _cur.tx) * 0.045;
    _cur.ty += ((pose.ty + gy) - _cur.ty) * 0.045;
    _cur.rot += (pose.rot - _cur.rot) * 0.045;
    _cur.sx += (pose.sx - _cur.sx) * 0.045;
    _cur.sy += (pose.sy - _cur.sy) * 0.045;
    _cur.breath += (wrap - _cur.breath) * 0.045;
    _cur.breatheHz += (pose.breatheHz - _cur.breatheHz) * 0.045;
    _cur.bob += (stride - _cur.bob) * 0.045;

    var breathePx = _cur.breath * 120;
    var bobPx = (_cur.bob * (1 + Math.sin(_clock / 1000 * TAU * _cur.breatheHz)) * 0.5) * amp;

    // A walking Cloud gently bobs with each step. The profile "bounce" term is
    // always 0 (every profile is grounded), so this never produces a hop.
    if (_walk.active && !_walk.paused) {
      var wpr = walkProfile();
      var stepWave = Math.abs(Math.sin(_clock / 1000 * TAU * wpr.cadence));
      bobPx += ((1.4 + wpr.bounce * 2.2) * stepWave) * amp;
    }

    var tx = _cur.tx + breathePx * 0.3 * Math.sin(_clock / 1000 * TAU * _cur.breatheHz);
    var ty = _cur.ty + bobPx;
    var rot = _cur.rot;
    var sx = _cur.sx + breathePx * 0.2 * Math.cos(_clock / 1000 * TAU * _cur.breatheHz);
    var sy = _cur.sy;

    var originY = null;

    // One-shot gesture overlay.
    if (_gesture) {
      var dur = _gesture.dur || 600;
      var elapsed = now - _gestureStart;
      var at = elapsed / dur;
      if (at >= 1) {
        _gesture = null;
      } else {
        var g = evalGesture(_gesture, at);
        tx += g.tx * amp;
        ty += g.ty * amp;
        rot += g.rot * amp;
        sx += (g.sx - 1) * amp + 1;
        sy += (g.sy - 1) * amp + 1;
        if (g.originY != null) originY = g.originY;
      }
    }

    var trStr = "translate3d(" + tx.toFixed(2) + "px," + ty.toFixed(2) + "px,0)" +
      " rotate(" + rot.toFixed(2) + "deg)" +
      " scale(" + sx.toFixed(3) + "," + sy.toFixed(3) + ")";
    _el.style.transform = trStr;
    _el.style.transformOrigin = originY != null ? ("50% " + originY + "%") : "50% 100%";
  }

  // ───────────────────────────────────────────────────────────────────────
  // Lifecycle
  // ───────────────────────────────────────────────────────────────────────
  function boot() {
    _lastT = performance.now();
    scheduleIdleLife(_lastT);
    if (!_running) {
      _running = true;
      _rafId = requestAnimationFrame(frame);
    }
  }

  function halt() {
    _running = false;
    if (_rafId) cancelAnimationFrame(_rafId);
    _rafId = 0;
  }

  function setState(key) {
    if (!STATE_KEYS[key]) return false;
    var was = _state;
    if (key !== was) {
      _state = key;
      var def = STATE_KEYS[key];
      _returnTimer = def.transient ? def.hold : 0;
      if (def.transient) _stable = was;
      var ge = entryGesture(key);
      if (ge) { _gesture = ge; _gestureStart = performance.now(); }
      if (_onChangeCb) _onChangeCb(key, was);
    }
    return true;
  }

  function getCapabilities() {
    var rigged = !!(window.VMCloudRig);
    return {
      independentEyes: true,
      independentEyebrows: true,
      independentNose: true,
      independentCheeks: true,
      independentMouth: true,
      independentArms: true,
      independentHands: true,
      independentLegs: true,
      wholeBodyArticulation: true,
      needsPartAssetsForFaceAndLimbs: false,
      asset: "static/cloud_rig.js",
      assetType: "layered SVG rig (original cloud companion visual)",
      character: "cloud",
      hasMovementController: true,
      movementBoundaries: true,
      autonomousDrift: true,
      driftSpeedMax: DRIFT_SPEED_MAX,
      standingAssetFallback: "static/cloud.png",
      // Explicit, runtime steerable capabilities (Layer 3 / future Brain).
      gazeDirections: ["left", "right", "up", "down", "toward-point", "follow-pose", "walk-direction", "drift-direction"],
      gazeInterpolation: true,
      blinkVariants: ["normal", "quick", "slow"],
      armGestures: ["wave", "point_up", "point_down", "point_l", "point_r", "welcome", "talk", "chin", "lift"],
      emotionalWalking: true,
      bodyFollowsGaze: true,
      mouthSync: "amplitude-driven (setSpeechLevel); synthetic pulse when unwired",
      emotionalFace: true,
      brainAPI: "vmCloudAnim.BrainAPI — driven by static/cloud.js (live AI state)"
    };
  }

  // Movement public API (Layer 2).
  function walkTo(x, y) {
    if (!_running) boot();
    if (x == null || y == null) return false;
    beginWalkTo(x, y);
    return true;
  }
  function walk(dir) {
    if (!_running) boot();
    if (dir !== 1 && dir !== -1) dir = -1;
    _walk.active = true;
    _walk.paused = false;
    _walk.dir = dir;
    _walk.target = null;
    _walk.acc = 0;
    return true;
  }
  function stop() {
    _walk.active = false;
    _walk.target = null;
    _walk.paused = false;
    _walk.acc = 0;
    // Hand control back to the drift layer, but let Cloud settle first.
    _drift.target = null;
    _drift.accX = 0; _drift.accY = 0;
    _drift.nextAt = performance.now() + DRIFT_POST_DRAG_MS;
    _drift.vx = 0; _drift.vy = 0;
  }
  function turn(dir) {
    if (!_running) boot();
    _walk.dir = dir === 1 ? 1 : -1;
    return true;
  }
  function setSpeed(v) {
    _walk.speed = Math.max(0.2, Math.min(3, v == null ? 1 : v));
    return _walk.speed;
  }
  function isMoving() { return _walk.active && !_walk.paused && !_dragging; }

  // Gaze control (Layer 3 → explicit). Drives pupils (and via coordination,
  // head + body lean) toward a normalized direction in Cloud's local space:
  // x -1 = image-left, +1 = image-right; y -1 = up, +1 = down.
  function lookToward(x, y, holdMs) {
    if (!_running) boot();
    if (x == null || y == null) return false;
    _lookSeq++;
    _lookTarget = { x: clamp(x, -1, 1), y: clamp(y, -1, 1), seq: _lookSeq };
    _lookHoldUntil = holdMs > 0 ? (performance.now() + holdMs) : 0;
    return true;
  }
  // Point look at a nearby element (or {x,y} screen coords). What matters for
  // now is the normalized direction toward the target; a future Brain wires
  // real world coords here.
  function lookAt(x, y, holdMs) {
    if (!_running) boot();
    if (x == null || y == null) return false;
    var rx = (typeof x === "number") ? x : 0;
    var ry = (typeof y === "number") ? y : 0;
    return lookToward(clamp(rx / 120, -1, 1), clamp(ry / 120, -1, 1), holdMs);
  }
  function clearLook() {
    _lookTarget = null;
    _lookHoldUntil = 0;
    return true;
  }

  // One-shot arm gestures (Layer 3 → explicit). These run independently of the
  // current emotion so Cloud can wave/point/welcome without a state swap.
  function wave(dur) { if (!_running) boot(); triggerArmGesture("wave", dur); return true; }
  function point(dir, dur) {
    if (!_running) boot();
    var kind = dir === "up" ? "point_up" : (dir === "down" ? "point_down" : (dir === "left" || dir === -1 ? "point_l" : "point_r"));
    triggerArmGesture(kind, dur);
    return true;
  }
  function welcome(dur) { if (!_running) boot(); triggerArmGesture("welcome", dur); return true; }
  function stopGestures() { _armGest = null; return true; }

  function isGesturing() { return !!_armGest; }

  // ───────────────────────────────────────────────────────────────────────
  // Public API
  // ───────────────────────────────────────────────────────────────────────
  window.cloudSetState = function (key) {
    if (!_running) boot();
    return setState(key);
  };

  window.vmCloudAnim = {
    setState: setState,
    getState: function () { return _state; },
    listStates: function () {
      return STATES.map(function (s) { return s.key; });
    },
    debug: function () {
      var el = _el || resolveElement();
      return {
        running: _running,
        reduced: _reduced,
        dragging: _dragging,
        framesDrawn: _framesDrawn,
        lastFrameAt: _lastFramesAt,
        now: performance.now(),
        state: _state,
        stable: _stable,
        elFound: !!el,
        elConnected: !!(el && el.isConnected),
        elId: el ? el.id : null,
        rigParts: _rig && _rig.parts ? Object.keys(_rig.parts).length : 0,
        hidden: el ? isCharacterHidden(el) : true,
        walkActive: _walk.active,
        walkPaused: _walk.paused,
        walkHasTarget: !!_walk.target,
        driftOn: _drift.on,
        driftHasTarget: !!_drift.target,
        driftVx: _drift.vx,
        dragging2: _dragging
      };
    },
    getCapabilities: getCapabilities,
    start: function () { boot(); },
    stop: function () { halt(); },
    reset: function () { setState("idle"); _stable = "idle"; },
    interrupt: function () {
      if (!_running) boot();
    },
    setIntensity: function (v) {
      _intensity = Math.max(0, Math.min(1, v != null ? v : 0.5));
      return _intensity;
    },
    // Real TTS amplitude (0..1) so the mouth tracks actual audio.
    setSpeechLevel: setSpeechLevel,
    clearSpeechLevel: clearSpeechLevel,
    getSpeechLevel: function () { return _speechLevel; },
    isSpeechDriven: function () { return _speechLive; },
    onStateChange: function (cb) { _onChangeCb = typeof cb === "function" ? cb : null; },
    demo: function (total) {
      var self = this;
      var steps = total || 1;
      var all = self.listStates();
      self.setState(all[0]);
      if (_demoTimer) clearInterval(_demoTimer);
      var i = 0;
      _demoTimer = setInterval(function () {
        i++;
        if (i >= all.length) { clearInterval(_demoTimer); return; }
        self.setState(all[i]);
      }, Math.max(800, 2200 / Math.max(1, steps)));
      return all.length;
    },
    // Movement controller (Layer 2).
    walk: walk,
    walkTo: walkTo,
    stop: stop,
    turn: turn,
    setSpeed: setSpeed,
    isMoving: isMoving,
    // Autonomous drift (Cloud floats on its own when not commanded).
    setDrift: setDrift,
    isDrifting: function () { return _drift.on && !driftSuppressed(); },
    driftSpeedMax: DRIFT_SPEED_MAX,
    // Explicit gaze (Layer 3).
    lookToward: lookToward,
    lookAt: lookAt,
    clearLook: clearLook,
    // One-shot gestures (Layer 3).
    wave: wave,
    point: point,
    welcome: welcome,
    stopGestures: stopGestures,
    isGesturing: isGesturing,
    // Future-Brain surface (Layer 4 — reserved, no Brain wiring today). These
    // are the semantic verbs a Brain library will drive; they already animate.
    BrainAPI: {
      setEmotion: function (key) { return setState(key); },
      startThinking: function () { return setState("thinking"); },
      startSpeaking: function (text) { if (typeof text === "string" && text) triggerArmGesture("talk", Math.min(6000, 900 + text.length * 22)); return setState("speaking"); },
      setSpeechLevel: setSpeechLevel,
      clearSpeechLevel: clearSpeechLevel,
      stopTalking: function () { clearSpeechLevel(); setState(_stable); return true; },
      stopSpeaking: function () { clearSpeechLevel(); setState(_stable); return true; },
      walkTo: walkTo,
      walkToIdle: function () { stop(); return true; },
      lookToward: lookToward,
      lookAt: lookAt,
      wave: wave,
      point: point,
      welcome: welcome,
      stop: function () { stop(); clearLook(); stopGestures(); return true; }
    }
  };

  // Top-level movement convenience aliases.
  window.vmCloudWalk = walk;
  window.vmCloudWalkTo = walkTo;
  window.vmCloudStop = stop;
  window.vmCloudTurn = turn;

  function reduceMedia() {
    var mq = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    _reduced = mq.matches;
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", function (e) { _reduced = e.matches; });
    }
  }

  reduceMedia();

  document.addEventListener("visibilitychange", function () {
    _lastT = performance.now();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // The element owner (static/cloud.js) removes the character on logout; the
  // loop self-recovers when a character appears again.
})();
