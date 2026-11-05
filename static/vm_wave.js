/* ValleyMind live waveform (recording) - right-to-left always while recording.
 * Fixed-interval scroll timer independent of audio; level sets newest bar height.
 * Source A: analyser RMS (smoothed, attack 0.5, release 0.12, noise gate, auto gain).
 * Source B (fallback): event-driven level (onaudiostart/onspeechstart/onresult via
 * userPulse), ~400ms decay, small variation tied to event rate (no random jitter).
 * If analyser unavailable or reads flat zero for 1s, switches to source B.
 * Never restart the wave when recognition restarts (start() is idempotent while active).
 *
 * Exposes:
 *   window.vmWave.start(kind)   kind = "user" | "ai" (idempotent for active "user")
 *   window.vmWave.stop()
 *   window.vmWave.userPulse()   raise level on recognition/audio events
 *   window.vmWave.debugInfo()   { level, source, display, loopRunning, interval }
 */
(function () {
  "use strict";

  var BAR_WIDTH = 3;
  var BAR_GAP = 3;
  var MIN_HEIGHT = 4;
  var MAX_HEIGHT = 28;
  var ACCENT = "#00d4ff";

  var QUIET_MS = 100;   // push interval when quiet
  var LOUD_MS = 45;     // push interval when level high
  var FLAT_ZERO_MS = 1000; // switch to source B if analyser flat this long
  var EVENT_DECAY_MS = 400;

  var state = {
    container: null,
    barsParent: null,
    bars: [],
    levels: [],
    activeKind: null,
    ctx: null,
    analyser: null,
    source: null,
    stream: null,
    tickTimer: null,
    loopRunning: false,
    token: 0,
    fallback: false,        // true when getUserMedia unavailable/failed
    voiceLevel: 0,
    lastPulse: 0,
    levelSource: "none",    // "analyser" | "events" | "none"
    currentLevel: 0,
    smoothedLevel: 0,
    pushIntervalMs: QUIET_MS,
    flatZeroSince: 0,       // timestamp when analyser first read flat; 0 = not flat
    useEventSource: false,  // true when analyser flat for 1s or unavailable
    eventTimes: []          // recent event timestamps for rate-based variation
  };

  function recomputeBarCount() {
    try {
      if (!state.barsParent) return state.bars.length || 40;
      var rect = state.barsParent.getBoundingClientRect();
      var width = rect.width || state.barsParent.clientWidth || 0;
      var computed = Math.floor((width - 26) / 6);
      if (computed < 20) computed = 20;
      if (computed > 200) computed = 200;
      return computed;
    } catch (e) {
      return 40;
    }
  }

  function debugInfo() {
    return {
      level: state.currentLevel || 0,
      source: state.levelSource || "none",
      display: state.container ? (state.container.style.display || "") : "",
      loopRunning: !!state.loopRunning,
      interval: state.pushIntervalMs || QUIET_MS
    };
  }

  function updateDebug() {
    try {
      if (!(/[?&]debug=1/).test(window.location.search || "")) return;
      var p = document.getElementById("sttDebugPanel");
      if (!p) return;
      var c = state.container;
      var disp = c ? (c.style.display || "") : "";
      var w = c && c.getBoundingClientRect ? Math.round(c.getBoundingClientRect().width) : (c ? (c.clientWidth || 0) : 0);
      var h = c && c.getBoundingClientRect ? Math.round(c.getBoundingClientRect().height) : (c ? (c.clientHeight || 0) : 0);
      var line = "vmWave: display=" + disp + " size=" + w + "x" + h + " loop=" + (state.loopRunning ? "yes" : "no") +
        " interval=" + (state.pushIntervalMs || 0) + "ms source=" + state.levelSource + " level=" + (state.currentLevel || 0).toFixed(3);
      p.textContent = line + (p.textContent ? " | " + p.textContent.split(" | ").slice(1).join(" | ") : "");
    } catch (e) {}
  }

  function injectCss() {
    try {
      if (typeof document === "undefined") return;
      if (document.getElementById("vmWaveCss")) return;
      var style = document.createElement("style");
      style.id = "vmWaveCss";
      style.textContent =
        "#vmWaveContainer{display:none;position:relative;z-index:50;height:34px;width:100%;max-width:100%;min-width:0;margin:0;padding:0 12px;background:rgba(0,0,0,0.35);border-radius:8px;align-items:center;gap:2px;box-sizing:border-box;overflow:hidden;}" +
        "#vmWaveBars{display:flex !important;flex-direction:row !important;flex-wrap:nowrap !important;align-items:center !important;justify-content:flex-start !important;width:100% !important;max-width:100% !important;overflow:hidden !important;height:32px !important;}" +
        ".vmWaveBar{width:3px;border-radius:2px;flex:0 0 3px;height:4px;background:" + ACCENT + ";display:block;transform-origin:bottom;margin:0 1.5px;transition:height 60ms ease;}";
      var head = document.head || document.getElementsByTagName("head")[0];
      if (head) head.appendChild(style);
    } catch (e) {}
  }

  function buildBars() {
    try {
      if (!state.barsParent) return;
      if (state.bars.length) return;
      var count = recomputeBarCount();
      var frag = (document.createDocumentFragment ? document.createDocumentFragment() : null);
      var target = frag || state.barsParent;
      for (var i = 0; i < count; i++) {
        var b = document.createElement("div");
        b.className = "vmWaveBar";
        b.style.width = BAR_WIDTH + "px";
        b.style.marginRight = BAR_GAP + "px";
        b.style.height = MIN_HEIGHT + "px";
        target.appendChild(b);
        state.bars.push(b);
      }
      if (frag) state.barsParent.appendChild(frag);
      state.levels = new Array(count).fill(0);
    } catch (e) {}
  }

  // Push newest level on the right; history scrolls right-to-left (always moves).
  function pushLevel(lvl) {
    try {
      if (!state.bars.length) return;
      if (lvl < 0) lvl = 0;
      if (lvl > 1) lvl = 1;
      state.levels.shift();
      state.levels.push(lvl);
      for (var i = 0; i < state.bars.length; i++) {
        var v = state.levels[i];
        if (v < 0) v = 0;
        if (v > 1) v = 1;
        var h = MIN_HEIGHT + v * (MAX_HEIGHT - MIN_HEIGHT);
        state.bars[i].style.height = h + "px";
      }
    } catch (e) {}
  }

  function silence() {
    try {
      if (!state.bars.length) return;
      state.levels = new Array(state.bars.length).fill(0);
      for (var i = 0; i < state.bars.length; i++) {
        state.bars[i].style.height = MIN_HEIGHT + "px";
      }
    } catch (e) {}
  }

  function ensureContext() {
    try {
      if (state.ctx) return state.ctx;
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      state.ctx = new AC();
      return state.ctx;
    } catch (e) {
      state.ctx = null;
      return null;
    }
  }

  function cancelTick() {
    try {
      if (state.tickTimer) {
        clearTimeout(state.tickTimer);
        state.tickTimer = null;
      }
      state.loopRunning = false;
    } catch (e) {}
  }

  function releaseAll() {
    cancelTick();
    try {
      if (state.stream) {
        var tracks = state.stream.getTracks ? state.stream.getTracks() : [];
        tracks.forEach(function (t) { try { t.stop(); } catch (e) {} });
        state.stream = null;
      }
    } catch (e) {}
    try {
      if (state.ctx) { try { state.ctx.close(); } catch (e) {} state.ctx = null; }
    } catch (e) {}
    try {
      if (state.source) { try { state.source.disconnect(); } catch (e) {} state.source = null; }
    } catch (e) {}
    state.analyser = null;
    state.activeKind = null;
    state.fallback = false;
    state.useEventSource = false;
    state.levelSource = "none";
    state.currentLevel = 0;
    state.smoothedLevel = 0;
    state.flatZeroSince = 0;
    state.voiceLevel = 0;
    state.lastPulse = 0;
    state.eventTimes = [];
    state.pushIntervalMs = QUIET_MS;
    state.token++;
  }

  function sampleAnalyserRms() {
    try {
      if (!state.analyser) return 0;
      var buf = new Uint8Array(state.analyser.fftSize || 2048);
      state.analyser.getByteTimeDomainData(buf);
      var sum = 0;
      for (var i = 0; i < buf.length; i++) {
        var v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      var rms = Math.sqrt(sum / buf.length);
      if (rms < 0.01) rms = 0;
      else rms = Math.min(1, rms * 4.0); // auto gain
      return rms;
    } catch (e) {
      return 0;
    }
  }

  function eventVariation(now) {
    // Small natural variation tied to event rate (no random timer jitter).
    try {
      var cutoff = now - 1500;
      var recent = 0;
      for (var i = state.eventTimes.length - 1; i >= 0; i--) {
        if (state.eventTimes[i] >= cutoff) recent++;
        else break;
      }
      // 0 events -> 0, a few events -> up to ~0.12 extra
      var v = Math.min(0.12, recent * 0.03);
      // gentle pulse tied to time-since-last-event (deterministic)
      var since = state.lastPulse ? (now - state.lastPulse) : 9999;
      if (since < 400) v += (1 - since / 400) * 0.06;
      return Math.min(0.18, v);
    } catch (e) { return 0; }
  }

  function computeLevel(now) {
    var analyserAvailable = !!state.analyser;
    var target = 0;
    var useEvents = false;

    if (analyserAvailable) {
      var rms = sampleAnalyserRms();
      var isFlat = rms <= 0.001;
      if (isFlat) {
        if (!state.flatZeroSince) state.flatZeroSince = now;
        if (now - state.flatZeroSince >= FLAT_ZERO_MS) {
          state.useEventSource = true; // analyser flat for 1s -> source B
        }
      } else {
        state.flatZeroSince = 0;
        state.useEventSource = false; // analyser has signal again
      }

      if (!state.useEventSource) {
        // Source A: analyser RMS
        target = rms;
        state.levelSource = "analyser";
      } else {
        useEvents = true;
      }
    } else {
      useEvents = true;
      state.useEventSource = true;
    }

    if (useEvents) {
      // Source B: event-driven level, ~400ms decay, variation tied to event rate
      state.levelSource = "events";
      var elapsed = now - (state.lastPulse || 0);
      var decay = Math.max(0, 1 - elapsed / EVENT_DECAY_MS);
      var base = Math.max(0, (state.voiceLevel || 0) * decay);
      target = Math.min(1, base + eventVariation(now));
      if (target < 0.01 && base < 0.01) target = 0.01; // gentle floor while recording
    }

    // Smooth RMS: attack ~0.5, release ~0.12
    if (target > state.smoothedLevel) {
      state.smoothedLevel = state.smoothedLevel * 0.5 + target * 0.5;
    } else {
      state.smoothedLevel = state.smoothedLevel * 0.88 + target * 0.12;
    }
    if (state.smoothedLevel < 0) state.smoothedLevel = 0;
    state.currentLevel = state.smoothedLevel;
    return state.smoothedLevel;
  }

  function tick() {
    state.tickTimer = null;
    try {
      if (!state.activeKind) { state.loopRunning = false; return; }
      state.loopRunning = true;
      var now = Date.now();
      var level = computeLevel(now);

      // Blend push speed by smoothed level: quiet ~100ms, loud ~45ms
      if (level < 0) level = 0;
      if (level > 1) level = 1;
      var interval = QUIET_MS + (LOUD_MS - QUIET_MS) * level;
      if (interval < LOUD_MS) interval = LOUD_MS;
      if (interval > QUIET_MS) interval = QUIET_MS;
      state.pushIntervalMs = Math.round(interval);

      // Always scroll: push newest bar = current level (height); history flows left.
      // In silence use a small floor so bars keep gentle motion visibility is via height;
      // but "quiet = slow, level sets height of newest bar" — push the level (can be low).
      pushLevel(level < 0.01 ? 0.01 : level);

      updateDebug();
      state.tickTimer = setTimeout(tick, state.pushIntervalMs);
    } catch (e) {
      state.loopRunning = false;
    }
  }

  function ensureLoop() {
    try {
      if (!state.activeKind) return;
      if (!state.tickTimer) {
        state.loopRunning = true;
        state.tickTimer = setTimeout(tick, state.pushIntervalMs || QUIET_MS);
      }
    } catch (e) {}
  }

  function startUser() {
    try {
      var nav = (typeof navigator !== "undefined") ? navigator : null;
      if (!nav || !nav.mediaDevices || !nav.mediaDevices.getUserMedia) {
        state.fallback = true;
        state.useEventSource = true;
        state.levelSource = "events";
        state.smoothedLevel = 0.01;
        ensureLoop();
        return;
      }
      var myToken = ++state.token;
      var constraints = { audio: { echoCancellation: true, noiseSuppression: true } };
      nav.mediaDevices.getUserMedia(constraints).then(function (stream) {
        if (myToken !== state.token || state.activeKind !== "user") {
          try { stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} }); } catch (e) {}
          return;
        }
        try {
          state.stream = stream;
          var ctx = ensureContext();
          if (!ctx || !ctx.createMediaStreamSource) {
            state.fallback = true; state.useEventSource = true; state.levelSource = "events";
            ensureLoop(); return;
          }
          try { if (ctx.state === "suspended") ctx.resume(); } catch (e) {}
          state.source = ctx.createMediaStreamSource(stream);
          state.analyser = ctx.createAnalyser();
          state.analyser.fftSize = 2048;
          state.source.connect(state.analyser);
          state.levelSource = "analyser";
          state.useEventSource = false;
          state.fallback = false;
          state.flatZeroSince = 0;
          ensureLoop();
        } catch (e) {
          state.fallback = true; state.useEventSource = true; state.levelSource = "events";
          ensureLoop();
        }
      }).catch(function () {
        state.fallback = true; state.useEventSource = true; state.levelSource = "events";
        ensureLoop();
      });
      // Start loop immediately (events drive level until analyser ready)
      state.levelSource = state.analyser ? "analyser" : "events";
      state.useEventSource = !state.analyser;
      ensureLoop();
    } catch (e) {
      state.fallback = true; state.useEventSource = true; state.levelSource = "events";
      ensureLoop();
    }
  }

  function startAi() {
    try {
      state.fallback = true;
      state.useEventSource = true;
      state.levelSource = "events";
      state.smoothedLevel = 0.01;
      ensureLoop();
    } catch (e) {}
  }

  function userPulse() {
    try {
      if (!state.activeKind) return;
      var now = Date.now();
      state.voiceLevel = 1.0;
      state.lastPulse = now;
      // track event rate (keep recent)
      state.eventTimes.push(now);
      if (state.eventTimes.length > 20) state.eventTimes = state.eventTimes.slice(-20);
      // if no healthy analyser, events are the source
      if (!state.analyser || state.useEventSource) {
        state.levelSource = "events";
        state.useEventSource = true;
        state.fallback = true;
      }
    } catch (e) {}
  }

  function isUserWaveHealthy() {
    return state.activeKind === "user" && (state.loopRunning || state.tickTimer || state.analyser || state.stream || state.fallback || state.useEventSource);
  }

  function start(kind) {
    try {
      if (!kind) return;
      injectCss();
      if (typeof document !== "undefined") {
        var container = document.getElementById("vmWaveContainer");
        if (container) {
          state.container = container;
          var bp = document.getElementById("vmWaveBars");
          if (bp) state.barsParent = bp;
        }
      }
      // Idempotent: never restart the wave when recognition restarts.
      if (kind === "user" && isUserWaveHealthy()) {
        if (state.container) {
          try { state.container.style.display = "flex"; } catch (e) {}
          try { state.container.classList.add("vmWaveActive"); } catch (e) {}
        }
        ensureLoop();
        updateDebug();
        return;
      }
      releaseAll();
      state.activeKind = kind;
      state.smoothedLevel = 0.01;
      state.pushIntervalMs = QUIET_MS;
      if (state.container) {
        try { state.container.style.display = "flex"; } catch (e) {}
        try { state.container.classList.add("vmWaveActive"); } catch (e) {}
        var rafFn = (typeof requestAnimationFrame !== "undefined") ? requestAnimationFrame : function (fn) { try { setTimeout(fn, 0); } catch (e) {} };
        rafFn(function () {
          try {
            var nc = recomputeBarCount();
            if (state.bars.length !== nc) {
              var oldBars = state.bars;
              state.bars = [];
              if (state.barsParent) state.barsParent.innerHTML = "";
              buildBars();
              var k = Math.min(oldBars.length, state.bars.length);
              for (var i = 0; i < k; i++) {
                if (state.bars[i]) state.bars[i].style.height = (oldBars[i] && oldBars[i].style && oldBars[i].style.height) || (MIN_HEIGHT + "px");
              }
            }
            updateDebug();
          } catch (e) {}
        });
      }
      if (kind === "user") startUser();
      else if (kind === "ai") startAi();
    } catch (e) {}
  }

  function stop() {
    try {
      releaseAll();
      if (state.container) {
        try { state.container.style.display = "none"; } catch (e) {}
        try { state.container.classList.remove("vmWaveActive"); } catch (e) {}
      }
      silence();
      state.activeKind = null;
      updateDebug();
    } catch (e) {}
  }

  var api = { start: start, stop: stop, userPulse: userPulse, debugInfo: debugInfo };
  try {
    if (typeof window !== "undefined") {
      window.vmWave = api;
      try {
        window.addEventListener("resize", function () {
          try {
            var nc = recomputeBarCount();
            if (state.barsParent && state.bars.length !== nc && state.activeKind) {
              var oldBars = state.bars;
              state.bars = [];
              if (state.barsParent) state.barsParent.innerHTML = "";
              buildBars();
              var k = Math.min(oldBars.length, state.bars.length);
              for (var i = 0; i < k; i++) {
                if (state.bars[i]) state.bars[i].style.height = (oldBars[i] && oldBars[i].style && oldBars[i].style.height) || (MIN_HEIGHT + "px");
              }
              updateDebug();
            } else { updateDebug(); }
          } catch (e) {}
        });
      } catch (e) {}
      try {
        if (typeof ResizeObserver !== "undefined") {
          var ro = new ResizeObserver(function () {
            try {
              var nc = recomputeBarCount();
              if (state.barsParent && state.bars.length !== nc && state.activeKind) {
                var oldBars = state.bars;
                state.bars = [];
                if (state.barsParent) state.barsParent.innerHTML = "";
                buildBars();
                var k = Math.min(oldBars.length, state.bars.length);
                for (var i = 0; i < k; i++) {
                  if (state.bars[i]) state.bars[i].style.height = (oldBars[i] && oldBars[i].style && oldBars[i].style.height) || (MIN_HEIGHT + "px");
                }
                updateDebug();
              } else { updateDebug(); }
            } catch (e) {}
          });
          setTimeout(function () {
            try {
              var bp = document.getElementById("vmWaveBars") || state.barsParent;
              if (bp) ro.observe(bp);
            } catch (e) {}
          }, 0);
        }
      } catch (e) {}
    }
  } catch (e) {}
  try {
    if (typeof module !== "undefined" && module.exports) {
      module.exports = api;
    }
  } catch (e) {}
})();
