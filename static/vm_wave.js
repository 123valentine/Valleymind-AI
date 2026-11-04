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
 *   window.vmWave.userPulse()   called on recognition events
 *                               (onspeechstart, onresult) to raise the level.
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
  var DECAY_MS = 400;       // ms for level to decay from 1 to 0 when silent

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
    voiceLevel: 0,     // current fallback level (0..1), driven by recognition events
    lastPulse: 0       // timestamp of last userPulse() call
  };

  // Measure how many 3px bars (with 3px gap) fit across the bars parent.
  // Must be called after the container is displayed (width is 0 while
  // display:none), so bars span the full composer width.
  function recomputeBarCount() {
    try {
      if (!state.barsParent) return state.bars.length || BAR_COUNT;
      var rect = state.barsParent.getBoundingClientRect();
      var width = rect.width || state.barsParent.clientWidth || 0;
      var computed = Math.floor((width - 26) / 6);
      if (computed < 20) computed = 20;
      if (computed > 200) computed = 200;
      return computed;
    } catch (e) {
      return 20;
    }
  }

  function buildBars(count) {
    try {
      if (!state.barsParent) return;
      if (state.bars.length) return;
      var frag = document.createDocumentFragment();
      for (var i = 0; i < count; i++) {
        var b = document.createElement("div");
        b.className = "vmWaveBar";
        b.style.width = BAR_WIDTH + "px";
        b.style.height = MIN_HEIGHT + "px";
        b.style.marginRight = BAR_GAP + "px";
        frag.appendChild(b);
        state.bars.push(b);
      }
      state.barsParent.appendChild(frag);
      state.levels = new Array(count).fill(0);
    } catch (e) {}
  }

  function setLevels(levels) {
    try {
      if (!state.bars.length) return;
      // Scrolling history: shift left, push new level on the right.
      state.levels = state.levels.slice(1);
      state.levels.push(levels[levels.length - 1] || 0);
      for (var i = 0; i < state.bars.length; i++) {
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
      state.levels = new Array(state.bars.length).fill(0);
      for (var i = 0; i < state.bars.length; i++) {
        state.bars[i].style.height = MIN_HEIGHT + "px";
      }
    } catch (e) {}
  }

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
        "min-width:0;" +
        "margin:0;" +
        "padding:0 12px;" +
        "background:rgba(0,0,0,0.35);" +
        "border-radius:8px;" +
        "display:flex;" +
        "align-items:center;" +
        "gap:2px;" +
        "box-sizing:border-box;" +
        "overflow:hidden;" +
        "}" +
        ".vmWaveBar{" +
        "width:" + BAR_WIDTH + "px;" +
        "border-radius:" + (BAR_WIDTH / 2) + "px;" +
        "flex:0 0 " + BAR_WIDTH + "px;" +
        "height:" + MIN_HEIGHT + "px;" +
        "background:" + ACCENT + ";" +
        "display:block;" +
        "transform-origin:bottom;" +
        "margin:0 1.5px;" +
        "}" +
        "#vmWaveContainer.vmWaveActive{" +
        "display:flex;" +
        "}" +
        "#vmWaveBars{" +
        "display:flex !important;" +
        "flex-direction:row !important;" +
        "flex-wrap:nowrap !important;" +
        "align-items:center !important;" +
        "justify-content:center !important;" +
        "width:100% !important;" +
        "max-width:100% !important;" +
        "overflow:hidden !important;" +
        "height:32px !important;" +
        "}";
      var head = document.head || document.getElementsByTagName("head")[0];
      if (head) head.appendChild(style);
    } catch (e) {}
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
        state.analyser.getByteTimeDomainData(buf);
        var sum = 0;
        for (var i = 0; i < buf.length; i++) sum += Math.abs(buf[i] - 128);
        var raw = Math.min(1, sum / buf.length / 128);
        newLevel = raw;
      } else if (state.fallback) {
        var now = Date.now();
        var elapsed = now - state.lastPulse;
        var decay = Math.max(0, 1 - elapsed / DECAY_MS);
        newLevel = Math.max(0, state.voiceLevel * decay);
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
        state.lastPulse = Date.now() - 999999;
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
            state.lastPulse = Date.now() - 999999;
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
          state.lastPulse = Date.now() - 999999;
          state.raf = requestAnimationFrame(render);
        }
      }).catch(function () {
        state.fallback = true;
        state.lastPulse = Date.now() - 999999;
        state.raf = requestAnimationFrame(render);
      });
    } catch (e) {
      state.fallback = true;
      state.lastPulse = Date.now() - 999999;
      state.raf = requestAnimationFrame(render);
    }
  }

  // ---------------------------------------------------------------- ai tts
  // NOTE: We intentionally do NOT use createMediaElementSource (it can silence
  // the audio). Use the fallback animation instead.
  function startAi() {
    try {
      state.fallback = true;
      state.lastPulse = Date.now() - 999999;
      state.raf = requestAnimationFrame(render);
    } catch (e) {}
  }

  // ------------------------------------------------------------------ public
  function userPulse() {
    try {
      if (!state.activeKind || state.activeKind !== "user") return;
      state.voiceLevel = 1;
      state.lastPulse = Date.now();
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
      }
      releaseAll();
      state.activeKind = kind;
      if (state.container) {
        try { state.container.style.display = "flex"; } catch (e) {}
        try { state.container.classList.add("vmWaveActive"); } catch (e) {}
        // Measure bar count AFTER container is displayed (width is 0 while
        // display:none), so bars span the full composer width.
        requestAnimationFrame(function () {
          try {
            var nc = recomputeBarCount();
            if (state.bars.length !== nc) {
              var oldBars = state.bars;
              state.bars = [];
              state.barsParent.innerHTML = "";
              buildBars(nc);
              // Transfer recent levels.
              var k = Math.min(oldBars.length, state.bars.length);
              for (var i = 0; i < k; i++) {
                state.bars[i].style.height = oldBars[i].style.height;
              }
            }
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
    } catch (e) {}
  }

  var api = { start: start, stop: stop, userPulse: userPulse };
  try {
    if (typeof window !== "undefined") {
      window.vmWave = api;
      // Recompute bar count on resize so the wave spans the full composer width.
      try {
        window.addEventListener("resize", function () {
          try {
            var nc = recomputeBarCount();
            if (state.barsParent && state.bars.length !== nc && state.activeKind) {
              var oldBars = state.bars;
              state.bars = [];
              state.barsParent.innerHTML = "";
              buildBars(nc);
              var k = Math.min(oldBars.length, state.bars.length);
              for (var i = 0; i < k; i++) {
                state.bars[i].style.height = oldBars[i].style.height;
              }
            }
          } catch (e) {}
        });
      } catch (e) {}
      // Recompute bar count when the container size changes (e.g. composer
      // width changes on phone vs laptop).
      try {
        if (state.barsParent && typeof ResizeObserver !== "undefined") {
          state.resizeObserver = new ResizeObserver(function () {
            try {
              var nc = recomputeBarCount();
              if (state.bars.length !== nc && state.activeKind) {
                var oldBars = state.bars;
                state.bars = [];
                state.barsParent.innerHTML = "";
                buildBars(nc);
                var k = Math.min(oldBars.length, state.bars.length);
                for (var i = 0; i < k; i++) {
                  state.bars[i].style.height = oldBars[i].style.height;
                }
              }
            } catch (e) {}
          });
          state.resizeObserver.observe(state.barsParent);
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