/* ValleyMind live waveform (user mic + AI TTS playback).
 *
 * Self-contained IIFE. Everything is wrapped in try/catch and feature-checked
 * so a failure here can never stop the app from starting.
 *
 * Exposes:
 *   window.vmWave.start(kind)   kind = "user" | "ai"
 *   window.vmWave.stop()
 *
 * The container element (#vmWaveContainer) is created/owned by index.html and
 * is hidden by default. start() shows it; stop() hides it and releases every
 * resource (mic tracks, AudioContext, requestAnimationFrame).
 */
(function () {
  "use strict";

  var BAR_COUNT = 8;
  var MIN_HEIGHT = 4;
  var MAX_HEIGHT = 40;

  var state = {
    started: false,
    container: null,
    barsParent: null,
    activeKind: null,
    ctx: null,
    analyser: null,
    source: null,
    stream: null,
    raf: null,
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
        "z-index:60;" +
        "height:34px;" +
        "width:100%;" +
        "max-width:96vw;" +
        "margin:7px auto 0;" +
        "padding:4px 8px;" +
        "background:rgba(0,0,0,0.5);" +
        "border-radius:10px;" +
        "align-items:center;" +
        "gap:6px;" +
        "box-sizing:border-box;" +
        "}" +
        ".vmWaveBar{" +
        "width:6px;" +
        "min-height:4px;" +
        "height:10px;" +
        "background:#00d4ff;" +
        "border-radius:2px;" +
        "display:inline-block;" +
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
      if (state.barsParent.children && state.barsParent.children.length) return;
      for (var i = 0; i < BAR_COUNT; i++) {
        var b = document.createElement("div");
        b.className = "vmWaveBar";
        b.style.animationDelay = (i * 0.05).toFixed(2) + "s";
        state.barsParent.appendChild(b);
      }
    } catch (e) {}
  }

  function setLevels(levels) {
    try {
      if (!state.barsParent) return;
      var els = state.barsParent.querySelectorAll
        ? state.barsParent.querySelectorAll(".vmWaveBar")
        : [];
      if (!els.length) return;
      for (var i = 0; i < els.length; i++) {
        var lvl = levels[i % levels.length] || 0;
        if (lvl < 0) lvl = 0;
        if (lvl > 1) lvl = 1;
        var h = MIN_HEIGHT + lvl * (MAX_HEIGHT - MIN_HEIGHT);
        els[i].style.transform = "scaleY(" + (h / MAX_HEIGHT).toFixed(3) + ")";
      }
    } catch (e) {}
  }

  function silence() {
    try {
      var n = state.barsParent && state.barsParent.children
        ? Math.min(BAR_COUNT, state.barsParent.children.length)
        : BAR_COUNT;
      setLevels(new Array(n).fill(0));
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
    state.analyser = null;
    state.source = null;
    state.activeKind = null;
    state.fallback = false;
  }

  // --------------------------------------------------------------- render loop
  function render() {
    try {
      if (!state.activeKind) return;
      var levels = [];
      if (state.analyser) {
        var buf = new Uint8Array(state.analyser.frequencyBinCount);
        state.analyser.getByteFrequencyData(buf);
        var n = Math.min(BAR_COUNT, buf.length);
        for (var i = 0; i < BAR_COUNT; i++) {
          var idx = Math.floor((i / BAR_COUNT) * buf.length);
          levels.push(Math.min(1, (buf[idx] || 0) / 255));
        }
      } else if (state.fallback) {
        state.fallLevel += state.fallDir * 0.12;
        if (state.fallLevel >= 1) { state.fallLevel = 1; state.fallDir = -1; }
        if (state.fallLevel <= 0) { state.fallLevel = 0; state.fallDir = 1; }
        for (var j = 0; j < BAR_COUNT; j++) levels.push(state.fallLevel);
      }
      setLevels(levels);
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
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
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
  function startAi(el) {
    try {
      if (!el) {
        state.fallback = true;
        state.raf = requestAnimationFrame(render);
        return;
      }
      var ctx = ensureContext();
      if (!ctx || !ctx.createMediaElementSource) {
        state.fallback = true;
        state.raf = requestAnimationFrame(render);
        return;
      }
      // Reuse an existing source for this element if present.
      if (state.source && state.source.mediaElement === el) {
        state.analyser = ctx.createAnalyser();
        state.source.connect(state.analyser);
      } else {
        releaseSource();
        state.source = ctx.createMediaElementSource(el);
        state.analyser = ctx.createAnalyser();
        state.source.connect(state.analyser);
      }
      state.raf = requestAnimationFrame(render);
    } catch (e) {
      state.fallback = true;
      state.raf = requestAnimationFrame(render);
    }
  }

  function releaseSource() {
    try {
      if (state.source) {
        try { state.source.disconnect(); } catch (e) {}
        state.source = null;
      }
    } catch (e) {}
    state.analyser = null;
  }

  // ------------------------------------------------------------------ public
  function userPulse() {
    try {
      if (!state.activeKind || state.activeKind !== "user") return;
      if (!state.analyser && state.fallback) {
        state.fallLevel = 1;
        setLevels([1, 1, 1, 1, 1, 1, 1, 1]);
      }
    } catch (e) {}
  }

  function start(kind, el) {
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
      else if (kind === "ai") startAi(el);
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
