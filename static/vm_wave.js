/* ValleyMind live waveform (user mic + AI TTS playback).
 * Claude-style scrolling waveform: 40 bars, width 3px, gap 3px, rounded,
 * height up to 28px, solid cyan accent, centered vertically.
 * Scrolling history: each frame pushes a new level on the right and shifts
 * old levels left, so the wave flows.
 *
 * Self-contained IIFE. Everything wrapped in try/catch so a failure here
 * can never stop recognition or playback.
 *
 * Exposes:
 *   window.vmWave.start(kind)   kind = "user" | "ai"
 *   window.vmWave.stop()
 *   window.vmWave.userPulse()   called on recognition interim/final events
 *
 * The container element (#vmWaveContainer) is created/owned by index.html.
 */
(function () {
  "use strict";

  var BAR_COUNT = 40;
  var BAR_WIDTH = 3;
  var BAR_GAP = 3;
  var MIN_HEIGHT = 4;
  var MAX_HEIGHT = 28;
  var ACCENT = "#00d4ff";   // app cyan
  var FALLBACK_JITTER = 0.15;

  var state = {
    container: null,
    barsParent: null,
    bars: [],          // bar elements
    levels: [],        // current per-bar levels (0..1), scrolling history
    activeKind: null,
    ctx: null,
    analyser: null,
    source: null,
    stream: null,
    raf: null,
    token: 0,          // guards against late getUserMedia results
    fallback: false,
    fallLevel: 0,
    fallDir: 1
  };

  // ------------------------------------------------------------------ DOM/CSS
  function injectCss() {
    try {
      if (document.getElementById("vmWaveCss")) return;
      var style = document.createElement("style");
      style.id = "vmWaveCss";
      style.textContent =
        "#vmWaveContainer{" +
        "display:none;" +
        "position:relative;" +
        "z-index:50;" +
        "height:34px;" +
        "width:100%;" +
        "max-width:100%;" +
        "margin:4px 0 0;" +
        "padding:2px 8px;" +
        "background:rgba(0,0,0,0.35);" +
        "border-radius:8px;" +
        "display:flex;" +
        "align-items:center;" +
        "gap:2px;" +
        "box-sizing:border-box;" +
        "}" +
        ".vmWaveBar{" +
        "width:" + BAR_WIDTH + "px;" +
        "border-radius:" + (BAR_WIDTH / 2) + "px;" +
        "flex:0 0 " + BAR_WIDTH + "px;" +
        "height:" + MIN_HEIGHT + "px;" +
        "background:" + ACCENT + ";" +
        "display:block;" +
        "transform-origin:bottom;" +
        "}" +
        "#vmWaveContainer.vmWaveActive{" +
        "display:flex;" +
        "}";
      var head = document.head || document.getElementsByTagName("head")[0];
      if (head) head.appendChild(style);
    } catch (e) {}
  }

  function buildBars() {
    try {
      if (!state.barsParent) return;
      if (state.bars.length) return;
      var frag = document.createDocumentFragment();
      for (var i = 0; i < BAR_COUNT; i++) {
        var b = document.createElement("div");
        b.className = "vmWaveBar";
        b.style.width = BAR_WIDTH + "px";
        b.style.marginRight = BAR_GAP + "px";
        b.style.height = MIN_HEIGHT + "px";
        frag.appendChild(b);
        state.bars.push(b);
      }
      state.barsParent.appendChild(frag);
      state.levels = new Array(BAR_COUNT).fill(0);
    } catch (e) {}
  }

  function setLevels(levels) {
    try {
      if (!state.bars.length) return;
      // Scrolling history: shift left, push new level on the right.
      state.levels = state.levels.slice(1);
      state.levels.push(levels[levels.length - 1] || 0);
      for (var i = 0; i < BAR_COUNT; i++) {
        var lvl = state.levels[i];
        if (lvl < 0) lvl = 0;
        if (lvl > 1) lvl = 1;
        var h = MIN_HEIGHT + lvl * (MAX_HEIGHT - MIN_HEIGHT);
        state.bars[i].style.height = h + "px";
      }
    } catch (e) {}
  }

  function silence() {
    try {
      state.levels = new Array(BAR_COUNT).fill(0);
      for (var i = 0; i < BAR_COUNT; i++) {
        state.bars[i].style.height = MIN_HEIGHT + "px";
      }
    } catch (e) {}
  }

  // ------------------------------------------------------------------ context
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

  function cancelRaf() {
    try {
      if (state.raf) {
        cancelAnimationFrame(state.raf);
        state.raf = null;
      }
    } catch (e) {}
  }

  function releaseAll() {
    cancelRaf();
    try {
      if (state.stream) {
        var tracks = state.stream.getTracks || function () { return []; };
        tracks().forEach(function (t) {
          try { t.stop(); } catch (e) {}
        });
        state.stream = null;
      }
    } catch (e) {}
    try {
      if (state.ctx) {
        try { state.ctx.close(); } catch (e) {}
        state.ctx = null;
      }
    } catch (e) {}
    try {
      if (state.source) {
        try { state.source.disconnect(); } catch (e) {}
        state.source = null;
      }
    } catch (e) {}
    state.analyser = null;
    state.activeKind = null;
    state.fallback = false;
    state.token++;
  }

  // --------------------------------------------------------------- render loop
  function render() {
    try {
      if (!state.activeKind) return;
      var newLevel = 0;
      if (state.analyser) {
        var buf = new Uint8Array(state.analyser.frequencyBinCount);
        state.analyser.getByteFrequencyData(buf);
        var n = Math.min(buf.length, 48);
        var sum = 0;
        for (var i = 0; i < n; i++) sum += buf[i];
        var raw = Math.min(1, (sum / n / 255) * 2.2);
        newLevel = raw;
      } else if (state.fallback) {
        state.fallLevel += state.fallDir * FALLBACK_JITTER;
        if (state.fallLevel >= 1) { state.fallLevel = 1; state.fallDir = -1; }
        if (state.fallLevel <= 0) { state.fallLevel = 0; state.fallDir = 1; }
        // random jitter for organic feel
        newLevel = state.fallLevel + (Math.random() * 0.1 - 0.05);
        if (newLevel < 0) newLevel = 0;
        if (newLevel > 1) newLevel = 1;
      }
      setLevels([newLevel]);
      state.raf = requestAnimationFrame(render);
    } catch (e) {
      state.raf = null;
    }
  }

  // ---------------------------------------------------------------- user mic
  function startUser() {
    try {
      navigator.mediaDevices = navigator.mediaDevices || {};
      if (!navigator.mediaDevices.getUserMedia) {
        state.fallback = true;
        state.raf = requestAnimationFrame(render);
        return;
      }
      var myToken = ++state.token;
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        // Guard against late results: if stop() was called meanwhile, ignore.
        if (myToken !== state.token) {
          try { stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} }); } catch (e) {}
          return;
        }
        try {
          state.stream = stream;
          var ctx = ensureContext();
          if (!ctx || !ctx.createMediaStreamSource) {
            state.fallback = true;
            state.raf = requestAnimationFrame(render);
            return;
          }
          state.source = ctx.createMediaStreamSource(stream);
          state.analyser = ctx.createAnalyser();
          state.analyser.fftSize = 2048;
          state.source.connect(state.analyser);
          state.raf = requestAnimationFrame(render);
        } catch (e) {
          state.fallback = true;
          state.raf = requestAnimationFrame(render);
        }
      }).catch(function () {
        state.fallback = true;
        state.raf = requestAnimationFrame(render);
      });
    } catch (e) {
      state.fallback = true;
      state.raf = requestAnimationFrame(render);
    }
  }

  // ---------------------------------------------------------------- ai tts
  // NOTE: We intentionally do NOT use createMediaElementSource (it can silence
  // the audio). Use the fallback animation instead.
  function startAi() {
    try {
      state.fallback = true;
      state.raf = requestAnimationFrame(render);
    } catch (e) {}
  }

  // ------------------------------------------------------------------ public
  function userPulse() {
    try {
      if (!state.activeKind || state.activeKind !== "user") return;
      if (!state.analyser && state.fallback) {
        state.fallLevel = 1;
      }
    } catch (e) {}
  }

  function start(kind) {
    try {
      if (!kind) return;
      injectCss();
      var container = document.getElementById("vmWaveContainer");
      if (container) {
        state.container = container;
        var bp = document.getElementById("vmWaveBars");
        if (bp) state.barsParent = bp;
        buildBars();
      }
      releaseAll();
      state.activeKind = kind;
      if (state.container) {
        try { state.container.style.display = "flex"; } catch (e) {}
        try { state.container.classList.add("vmWaveActive"); } catch (e) {}
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
    } catch (e) {}
  }

  var api = { start: start, stop: stop, userPulse: userPulse };
  try {
    if (typeof window !== "undefined") {
      window.vmWave = api;
    }
  } catch (e) {}
  try {
    if (typeof module !== "undefined" && module.exports) {
      module.exports = api;
    }
  } catch (e) {}
})();
