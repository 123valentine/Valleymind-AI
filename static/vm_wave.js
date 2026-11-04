/* ValleyMind live waveform (recording) - right-to-left always while recording.
 * Claude-style scrolling waveform: bold cyan rounded bars, scrolling right to left
 * inside the composer box. Self-contained IIFE, wrapped in try/catch so a failure
 * here can never stop recognition or playback.
 *
 * Exposes:
 *   window.vmWave.start(kind)   kind = "user" | "ai"
 *   window.vmWave.stop()
 *   window.vmWave.userPulse()   called on recognition events (onaudiostart,
 *                               onspeechstart, onresult) to raise the level.
 *   window.vmWave.debugInfo()   { level, source, display } for ?debug=1 panel.
 */
(function () {
  "use strict";

  var BAR_WIDTH = 3;
  var BAR_GAP = 3;
  var MIN_HEIGHT = 4;
  var MAX_HEIGHT = 28;
  var ACCENT = "#00d4ff";   // app cyan

  var state = {
    container: null,
    barsParent: null,
    bars: [],
    levels: [],          // scrolling history (right-to-left: shift left, push right)
    activeKind: null,
    ctx: null,
    analyser: null,
    source: null,
    stream: null,
    raf: null,
    token: 0,            // guards against late getUserMedia results
    fallback: false,     // true when getUserMedia failed; use event-driven level
    voiceLevel: 0,       // event-driven level (0..1)
    lastPulse: 0,        // timestamp of last userPulse
    levelSource: "none", // "analyser" | "events" | "none"
    currentLevel: 0,     // latest raw level (0..1)
    smoothedLevel: 0,    // RMS-smoothed level (0..1)
    lastPushTime: 0      // timestamp of last history push
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
      source: state.levelSource,
      display: state.container ? (state.container.style.display || "") : ""
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
      var line = "vmWave: display=" + disp + " size=" + w + "x" + h + " source=" + state.levelSource + " level=" + (state.currentLevel || 0).toFixed(3);
      p.textContent = line + (p.textContent ? " | " + p.textContent.split(" | ").slice(1).join(" | ") : "");
    } catch (e) {}
  }

  function injectCss() {
    try {
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
      var frag = document.createDocumentFragment();
      for (var i = 0; i < count; i++) {
        var b = document.createElement("div");
        b.className = "vmWaveBar";
        b.style.width = BAR_WIDTH + "px";
        b.style.marginRight = BAR_GAP + "px";
        b.style.height = MIN_HEIGHT + "px";
        frag.appendChild(b);
        state.bars.push(b);
      }
      state.barsParent.appendChild(frag);
      state.levels = new Array(count).fill(0);
    } catch (e) {}
  }

  // Push a new level onto the right side of the history (flows right-to-left).
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
        var tracks = state.stream.getTracks ? state.stream.getTracks() : [];
        tracks.forEach(function (t) {
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
    state.levelSource = "none";
    state.currentLevel = 0;
    state.smoothedLevel = 0;
    state.token++;
  }

  function render() {
    try {
      if (!state.activeKind) return;
      var now = Date.now();
      var newLevel = 0;
      if (state.analyser) {
        state.levelSource = "analyser";
        try {
          var buf = new Uint8Array(state.analyser.fftSize || 2048);
          state.analyser.getByteTimeDomainData(buf);
          var sum = 0;
          for (var i = 0; i < buf.length; i++) {
            var v = (buf[i] - 128) / 128;
            sum += v * v;
          }
          var rms = Math.sqrt(sum / buf.length);
          // Noise gate + auto gain: soft speech still visible, shouting reaches full height.
          if (rms < 0.01) rms = 0;
          else rms = Math.min(1, rms * 4.0);
          // Smooth RMS: attack ~0.5, release ~0.12.
          if (rms > state.smoothedLevel) {
            state.smoothedLevel = state.smoothedLevel * 0.5 + rms * 0.5;
          } else {
            state.smoothedLevel = state.smoothedLevel * 0.88 + rms * 0.12;
          }
          if (state.smoothedLevel < 0.01) state.smoothedLevel = 0.01;
          newLevel = state.smoothedLevel;
          state.currentLevel = newLevel;
        } catch (e) {
          newLevel = state.smoothedLevel || 0.01;
        }
      } else if (state.fallback) {
        state.levelSource = "events";
        var elapsed = now - (state.lastPulse || 0);
        var decay = Math.max(0, 1 - elapsed / 400); // decay within ~400ms
        newLevel = Math.max(0.01, (state.voiceLevel || 0) * decay);
        if (newLevel < 0.01) newLevel = 0.01;
        if (newLevel > state.smoothedLevel) {
          state.smoothedLevel = state.smoothedLevel * 0.5 + newLevel * 0.5;
        } else {
          state.smoothedLevel = state.smoothedLevel * 0.88 + newLevel * 0.12;
        }
        state.currentLevel = state.smoothedLevel;
        newLevel = state.smoothedLevel;
      } else {
        newLevel = state.smoothedLevel || 0.01;
        state.currentLevel = newLevel;
      }

      // Blend push speed smoothly by smoothed level:
      //   quiet -> slow (~140ms), speaking -> fast (~45ms), no sudden switch.
      var level = state.smoothedLevel || newLevel || 0;
      if (level < 0) level = 0;
      if (level > 1) level = 1;
      var slowMs = 140;
      var fastMs = 45;
      var interval = slowMs + (fastMs - slowMs) * level;
      if (interval < fastMs) interval = fastMs;
      if (interval > slowMs) interval = slowMs;
      if (!state.lastPushTime || now - state.lastPushTime >= interval) {
        pushLevel(level < 0.01 ? 0.01 : level);
        state.lastPushTime = now;
      }

      updateDebug();
      state.raf = requestAnimationFrame(render);
    } catch (e) {
      state.raf = null;
    }
  }

  function startUser() {
    try {
      navigator.mediaDevices = navigator.mediaDevices || {};
      if (!navigator.mediaDevices.getUserMedia) {
        state.fallback = true;
        state.levelSource = "events";
        state.lastPulse = Date.now() - 999999;
        state.voiceLevel = 0.01;
        state.smoothedLevel = 0.01;
        if (!state.raf) state.raf = requestAnimationFrame(render);
        return;
      }
      var myToken = ++state.token;
      var constraints = { audio: { echoCancellation: true, noiseSuppression: true } };
      navigator.mediaDevices.getUserMedia(constraints).then(function (stream) {
        if (myToken !== state.token) {
          try { stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} }); } catch (e) {}
          return;
        }
        try {
          state.stream = stream;
          var ctx = ensureContext();
          if (!ctx || !ctx.createMediaStreamSource) {
            state.fallback = true;
            state.levelSource = "events";
            state.lastPulse = Date.now() - 999999;
            state.voiceLevel = 0.01;
            state.smoothedLevel = 0.01;
            if (!state.raf) state.raf = requestAnimationFrame(render);
            return;
          }
          try { if (ctx.state === "suspended") ctx.resume(); } catch (e) {}
          state.source = ctx.createMediaStreamSource(stream);
          state.analyser = ctx.createAnalyser();
          state.analyser.fftSize = 2048;
          state.source.connect(state.analyser);
          state.levelSource = "analyser";
          state.fallback = false;
          if (!state.raf) state.raf = requestAnimationFrame(render);
        } catch (e) {
          state.fallback = true;
          state.levelSource = "events";
          state.lastPulse = Date.now() - 999999;
          state.voiceLevel = 0.01;
          state.smoothedLevel = 0.01;
          if (!state.raf) state.raf = requestAnimationFrame(render);
        }
      }).catch(function () {
        state.fallback = true;
        state.levelSource = "events";
        state.lastPulse = Date.now() - 999999;
        state.voiceLevel = 0.01;
        state.smoothedLevel = 0.01;
        if (!state.raf) state.raf = requestAnimationFrame(render);
      });
    } catch (e) {
      state.fallback = true;
      state.levelSource = "events";
      state.lastPulse = Date.now() - 999999;
      state.voiceLevel = 0.01;
      state.smoothedLevel = 0.01;
      if (!state.raf) state.raf = requestAnimationFrame(render);
    }
  }

  function startAi() {
    try {
      state.fallback = true;
      state.levelSource = "events";
      state.lastPulse = Date.now() - 999999;
      state.voiceLevel = 0.01;
      state.smoothedLevel = 0.01;
      if (!state.raf) state.raf = requestAnimationFrame(render);
    } catch (e) {}
  }

  // Event-driven level boost (source B fallback): called on onaudiostart,
  // onspeechstart and every onresult. Decays within ~400ms.
  function userPulse() {
    try {
      if (!state.activeKind) return;
      state.voiceLevel = 1.0;
      state.lastPulse = Date.now();
      state.fallback = true;
      if (!state.analyser) {
        state.levelSource = "events";
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
      }
      releaseAll();
      state.activeKind = kind;
      state.lastPushTime = 0;
      state.smoothedLevel = 0.01;
      if (state.container) {
        try { state.container.style.display = "flex"; } catch (e) {}
        try { state.container.classList.add("vmWaveActive"); } catch (e) {}
        requestAnimationFrame(function () {
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
            } else {
              updateDebug();
            }
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
              } else {
                updateDebug();
              }
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
