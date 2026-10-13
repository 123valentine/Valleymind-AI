/* ValleyMind Music Studio — AI Music Workstation
   -------------------------------------------------
   A provider-abstracted, production-oriented front end layered on top of the
   existing classic editor (music_studio.js). It does NOT replace the classic
   engine: it owns a single centralized Web-Audio session and talks exclusively
   to the ValleyMind backend (/api/music/*), which decides which provider renders
   audio. Anything that requires a server renderer is reported honestly when no
   provider is configured — the controls stay, the promise does not lie.

   Loaded after music_studio.js. Exposes window.VMWs for wiring/tests.
*/
(function () {
  "use strict";

  if (window.VMWs) return;

  var API = "/api/music";
  var TERMINAL = { COMPLETED: 1, FAILED: 1, CANCELLED: 1 };
  var VIEWS = [
    { id: "create", label: "Create", icon: "sparkles" },
    { id: "parts", label: "Track Parts", icon: "cpu" },
    { id: "reference", label: "Reference", icon: "disc" },
    { id: "remix", label: "Remix", icon: "shuffle" },
    { id: "extend", label: "Continue", icon: "plus-circle" },
    { id: "vocal", label: "Vocal Lab", icon: "mic" },
    { id: "stems", label: "Stem Lab", icon: "layers" },
    { id: "lyrics", label: "Lyrics", icon: "file-text" },
    { id: "timeline", label: "Timeline", icon: "sliders" },
    { id: "tools", label: "Audio Tools", icon: "wrench" },
    { id: "master", label: "Master", icon: "gauge" },
    { id: "library", label: "Library", icon: "folder" },
    { id: "export", label: "Export", icon: "download" }
  ];
  var TRACK_COLORS = ["#3b82f6", "#8b5cf6", "#06b6d4", "#ec4899", "#f59e0b",
    "#10b981", "#ef4444", "#14b8a6", "#a855f7", "#eab308"];
  var STEM_ORDER = ["vocals", "drums", "bass", "melody", "other"];

  var S = null;
  var origOnShow = null;
  var els = {};

  /* ───────────────────────────── helpers ───────────────────────────── */
  function h(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function fmtTime(s) {
    s = Math.max(0, Math.round(s || 0));
    var m = Math.floor(s / 60), ss = s % 60;
    return (m < 10 ? "0" + m : m) + ":" + (ss < 10 ? "0" + ss : ss);
  }
  function uid(p) { return (p || "id") + "_" + Math.random().toString(36).slice(2, 9); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function toast(msg, kind) {
    var el = document.getElementById("vmwsToast");
    if (!el) {
      el = document.createElement("div");
      el.id = "vmwsToast";
      document.body.appendChild(el);
    }
    el.className = "vmws-toast" + (kind ? " " + kind : "") + " show";
    el.textContent = msg;
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.className = "vmws-toast"; }, 3400);
  }

  function apiJson(path, body, method) {
    var opts = {
      method: method || (body === undefined ? "GET" : "POST"),
      credentials: "include",
      headers: authHeaders(body === undefined ? {} : { "Content-Type": "application/json" })
    };
    if (body !== undefined) opts.body = JSON.stringify(body);
    return apiFetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          var e = new Error(d.message || ("Request failed (" + r.status + ")"));
          e.status = r.status;
          e.code = d.error_code || "";
          throw e;
        }
        return d;
      });
    });
  }

  function apiUpload(path, formData) {
    return apiFetch(path, {
      method: "POST", credentials: "include",
      headers: authHeaders({}), body: formData
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          var e = new Error(d.message || ("Upload failed (" + r.status + ")"));
          e.status = r.status; e.code = d.error_code || "";
          throw e;
        }
        return d;
      });
    });
  }

  function pollJob(jobId, onUpdate) {
    return new Promise(function (resolve, reject) {
      (function tick() {
        apiJson(API + "/jobs/" + encodeURIComponent(jobId)).then(function (d) {
          var job = d.job;
          if (onUpdate) onUpdate(job);
          if (TERMINAL[job.status]) resolve(job);
          else setTimeout(tick, 1000);
        }).catch(reject);
      })();
    });
  }

  /* ───────────────────────── state ───────────────────────── */
  function defaultState() {
    return {
      mode: "ws",
      view: "create",
      caps: null,
      loading: false,
      banner: "",
      project: null,
      projects: [],
      library: { items: [], query: "", cat: "all" },
      jobs: {},
      tracks: [],
      sections: [],
      player: {
        title: "Nothing playing", artist: "ValleyMind Music",
        url: "", artwork: "", playing: false, pos: 0, dur: 0,
        volume: 0.9, loop: false, speed: 1
      },
      create: {
        simple: true, brief: "", genre: "Afrobeats", subgenre: "", mood: "",
        energy: 0.6, bpm: "", key: "", time_signature: "4/4", duration: "",
        instrumental: false, vocal_style: "auto", language: "English",
        lyrics_mode: "ai", lyrics: "", structure: "", negative_prompt: "",
        selectedProject: ""
      },
      reference: { name: "", genre_hint: "", notes: "", file: null, fileName: "", analysis: null, result: null },
      remix: { projectId: "", instruction: "", preset: "", genre: "", mood: "", tempo: "", result: null },
      extend: { projectId: "", seconds: 30, instruction: "", result: null },
      vocal: { file: null, fileName: "", analysis: null, genre: "Afrobeats", mood: "", style: "", key: "", clean: true, result: null, projectId: "" },
      stems: { file: null, fileName: "", result: null, projectId: "", wanted: { vocals: true, drums: true, bass: true, melody: true, other: true } },
      lyrics: { text: "", action: "generate", language: "English", target: "", instruction: "", projectId: "", result: null },
      master: { projectId: "", target: "streaming", result: null, preview: false },
      exp: { projectId: "", format: "wav", result: null },
      files: [],
      parts: {
        track_type: "drums", bpm: "", key: "", bars: 8, mood: "",
        detail: "", result: null, projectId: ""
      },
      tools: {
        file: null, fileName: "", rel: "", selected: "eq",
        params: {}, result: null, files: []
      },
      vocalCleanResult: null,
      stemsQuickResult: null,
      masterAnalysis: null,
      copilot: { open: false, messages: [], busy: false, input: "" },
      ui: {
        timelineZoom: 1, snap: true, loop: false, editingTrack: "",
        newTrackType: "drums", newTrackDetail: "", recording: false,
        recordingMs: 0, selectedClip: null
      }
    };
  }

  /* ───────────────────────── audio engine (single authority) ───────────────────────── */
  var A = {
    ctx: null, master: null, sources: [], gains: {}, pans: {},
    buffers: {}, playing: false, startCtx: 0, pos: 0, dur: 0,
    raf: null, analyser: null, previewUrl: ""
  };

  function ensureCtx() {
    if (A.ctx) return A.ctx;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    A.ctx = new Ctx();
    A.master = A.ctx.createGain();
    A.master.gain.value = S.player.volume;
    A.analyser = A.ctx.createAnalyser();
    A.analyser.fftSize = 256;
    A.master.connect(A.analyser);
    A.analyser.connect(A.ctx.destination);
    return A.ctx;
  }

  function stopTransportSources() {
    A.sources.forEach(function (s) {
      try { s.stop(); } catch (e) { }
    });
    A.sources = [];
  }

  function pauseClassic() {
    try { if (window.VMMusic && VMMusic.pausePlayback) VMMusic.pausePlayback(); } catch (e) { }
  }

  function timelineDuration() {
    var max = 0;
    S.tracks.forEach(function (t) {
      (t.clips || []).forEach(function (c) {
        max = Math.max(max, (c.start || 0) + (c.duration || 0));
      });
    });
    return max || 0;
  }

  function isAudible(t) {
    var soloed = S.tracks.some(function (x) { return x.solo; });
    if (soloed) return !!t.solo;
    return !t.mute;
  }

  function getBuffer(url) {
    if (A.buffers[url]) return Promise.resolve(A.buffers[url]);
    var ctx = ensureCtx();
    if (!ctx) return Promise.reject(new Error("Web Audio not supported"));
    return fetch(url, { credentials: "include" })
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (buf) { return ctx.decodeAudioData(buf); })
      .then(function (decoded) { A.buffers[url] = decoded; return decoded; });
  }

  function scheduleTrack(t, transportPos, when) {
    if (!isAudible(t) || !t.buffer) return;
    var gain = A.gains[t.id], pan = A.pans[t.id];
    if (!gain) {
      gain = A.ctx.createGain();
      pan = A.ctx.createStereoPanner ? A.ctx.createStereoPanner() : null;
      gain.gain.value = t.vol == null ? 1 : t.vol;
      if (pan) { pan.pan.value = t.pan || 0; gain.connect(pan); pan.connect(A.master); }
      else gain.connect(A.master);
      A.gains[t.id] = gain; A.pans[t.id] = pan;
    }
    gain.gain.value = t.vol == null ? 1 : t.vol;
    if (pan) pan.pan.value = t.pan || 0;

    (t.clips || []).forEach(function (c) {
      var clipStart = c.start || 0;
      var clipDur = c.duration || 0;
      var clipEnd = clipStart + clipDur;
      if (clipEnd <= transportPos) return;
      var src = A.ctx.createBufferSource();
      src.buffer = t.buffer;
      src.connect(gain);
      var playAt = when + Math.max(0, clipStart - transportPos);
      var offset = (c.offset || 0) + Math.max(0, transportPos - clipStart);
      var remain = clipEnd - Math.max(transportPos, clipStart);
      var g = A.ctx.createGain();
      var base = t.vol == null ? 1 : t.vol;
      g.gain.value = base;
      // fade in / out applied on a per-clip gain stage
      src.disconnect(); src.connect(g); g.connect(gain);
      var fi = c.fadeIn || 0, fo = c.fadeOut || 0;
      if (fi > 0) { g.gain.setValueAtTime(0.0001, playAt); g.gain.linearRampToValueAtTime(base, playAt + fi); }
      if (fo > 0) {
        var fadeStart = playAt + Math.max(0, remain - fo);
        g.gain.setValueAtTime(base, fadeStart);
        g.gain.linearRampToValueAtTime(0.0001, playAt + remain);
      }
      try {
        if (A.playerSpeed && A.playerSpeed !== 1) src.playbackRate.value = A.playerSpeed;
        src.start(playAt, offset, remain);
        A.sources.push(src);
      } catch (e) { }
    });
  }

  function transportPlay() {
    if (!S.tracks.length || A.playing) return;
    var ctx = ensureCtx();
    if (!ctx) { toast("Web Audio is not available in this browser."); return; }
    if (ctx.state === "suspended") ctx.resume();
    pauseClassic();
    A.playerSpeed = S.player.speed;
    A.playing = true;
    A.startCtx = ctx.currentTime;
    A.transportPos = A.pos;
    A.dur = timelineDuration();
    var when = ctx.currentTime + 0.06;
    S.tracks.forEach(function (t) { scheduleTrack(t, A.transportPos, when); });
    tickTransport();
    renderPlayer();
  }

  function transportPause() {
    if (!A.playing) return;
    A.pos = clamp((A.ctx ? A.ctx.currentTime - A.startCtx : 0) * (S.player.speed || 1) + A.transportPos, 0, timelineDuration());
    A.playing = false;
    stopTransportSources();
    if (A.raf) cancelAnimationFrame(A.raf), A.raf = null;
    Object.keys(A.gains).forEach(function (k) {
      if (A.pans[k]) { try { A.pans[k].disconnect(); } catch (e) { } try { A.gains[k].disconnect(); } catch (e) { } }
    });
    A.gains = {}; A.pans = {};
    renderPlayer();
  }

  function transportStop() { transportPause(); A.pos = 0; renderPlayer(); }

  function transportSeek(pct) {
    var d = timelineDuration();
    A.pos = clamp((pct || 0) * (d || 0), 0, d);
    if (A.playing) { transportPause(); transportPlay(); }
    else renderPlayer();
  }

  function tickTransport() {
    if (!A.playing) return;
    var pos = (A.ctx.currentTime - A.startCtx) * (S.player.speed || 1) + A.transportPos;
    A.pos = pos;
    S.player.pos = pos;
    S.player.dur = A.dur || timelineDuration();
    if (pos >= S.player.dur || (S.player.loop && pos >= S.player.dur)) {
      if (S.player.loop && S.player.dur > 0) { A.pos = 0; transportPause(); transportPlay(); return; }
      transportPause();
      return;
    }
    updatePlayheadUI();
    A.raf = requestAnimationFrame(tickTransport);
  }

  function updatePlayheadUI() {
    var ph = document.getElementById("vmwsPlayhead");
    var d = S.player.dur || timelineDuration() || 1;
    if (ph) ph.style.left = clamp((A.pos / d) * 100, 0, 100) + "%";
    var bar = document.getElementById("vmwsProgress");
    if (bar) bar.style.width = clamp((A.pos / d) * 100, 0, 100) + "%";
    var t = document.getElementById("vmwsTime");
    if (t) t.textContent = fmtTime(A.pos) + " / " + fmtTime(d);
  }

  /* ───────────────────────── project helpers ───────────────────────── */
  function activeProjectOptions(selected) {
    var opts = '<option value="">— none —</option>';
    (S.projects || []).forEach(function (p) {
      opts += '<option value="' + h(p.id) + '"' + (p.id === selected ? " selected" : "") +
        ">" + h(p.name || p.title || "Untitled") + "</option>";
    });
    return opts;
  }

  function refreshLibrary() {
    return apiJson(API + "/library?q=" + encodeURIComponent(S.library.query) +
      "&category=" + encodeURIComponent(S.library.cat)).then(function (d) {
        S.library.items = d.items || [];
        S.projects = S.library.items;
        return d.items;
      }).catch(function () { return []; });
  }

  function loadIntoProject(project) {
    S.project = project || null;
    if (!project) return;
    // Build timeline tracks from stems / audio when present.
    var stems = project.stems || [];
    stems.forEach(function (s) {
      addTrack({
        name: s.name || "Stem", url: s.url || "", kind: "stem",
        sourceRel: s.rel || "", vol: 1, pan: 0
      });
    });
    S.sections = (project.sections || []).map(function (sec, i) {
      return { id: sec.id || uid("sec"), label: sec.label || sec.section || ("Section " + (i + 1)), start: sec.start || (i * 30) };
    });
  }

  function saveActiveProject(patch) {
    var data = Object.assign({}, patch || {});
    if (!S.project) {
      return apiJson(API + "/workstation/projects", data).then(function (d) {
        S.project = d.project;
        toast("Project saved", "ok");
        return d.project;
      });
    }
    return apiJson(API + "/projects/" + encodeURIComponent(S.project.id), data, "PUT")
      .then(function (d) { S.project = d.project; toast("Project saved", "ok"); return d.project; });
  }

  /* ───────────────────────── track model ───────────────────────── */
  function addTrack(opts) {
    opts = opts || {};
    var t = {
      id: uid("trk"),
      name: opts.name || ("Track " + (S.tracks.length + 1)),
      kind: opts.kind || "audio",
      url: opts.url || "",
      sourceRel: opts.sourceRel || "",
      buffer: null, peaks: null,
      color: TRACK_COLORS[S.tracks.length % TRACK_COLORS.length],
      mute: false, solo: false, lock: false,
      vol: opts.vol == null ? 0.9 : opts.vol, pan: opts.pan || 0,
      clips: opts.buffer === undefined ? [] : []
    };
    S.tracks.push(t);
    if (t.url) {
      var dur = 0;
      getBuffer(t.url).then(function (buf) {
        t.buffer = buf;
        t.duration = buf.duration;
        t.peaks = computePeaks(buf, 600);
        t.clips = [{ id: uid("clip"), start: 0, offset: 0, duration: buf.duration, fadeIn: 0, fadeOut: 0 }];
        renderWS();
      }).catch(function () { });
    }
    return t;
  }

  function computePeaks(buffer, n) {
    var ch = buffer.getChannelData(0);
    var block = Math.max(1, Math.floor(ch.length / n));
    var peaks = new Array(n);
    for (var i = 0; i < n; i++) {
      var max = 0;
      for (var j = 0; j < block; j++) {
        var v = Math.abs(ch[i * block + j] || 0);
        if (v > max) max = v;
      }
      peaks[i] = max;
    }
    return peaks;
  }

  function trackById(id) {
    return S.tracks.filter(function (t) { return t.id === id; })[0];
  }

  function splitTrackAtPlayhead(id) {
    var t = trackById(id);
    if (!t || !t.clips.length) return;
    var pos = A.pos;
    for (var i = 0; i < t.clips.length; i++) {
      var c = t.clips[i];
      if (pos > c.start + 0.05 && pos < c.start + c.duration - 0.05) {
        var leftDur = pos - c.start;
        var right = {
          id: uid("clip"), start: pos,
          offset: (c.offset || 0) + leftDur,
          duration: c.duration - leftDur, fadeIn: 0, fadeOut: c.fadeOut || 0
        };
        c.duration = leftDur; c.fadeOut = 0;
        t.clips.splice(i + 1, 0, right);
        renderWS();
        toast("Clip split", "ok");
        return;
      }
    }
    toast("Move the playhead over a clip to split.", "warn");
  }

  function removeTrack(id) {
    S.tracks = S.tracks.filter(function (t) { return t.id !== id; });
    renderWS();
  }
  function duplicateTrack(id) {
    var t = trackById(id);
    if (!t) return;
    var clone = JSON.parse(JSON.stringify(t));
    clone.id = uid("trk"); clone.name = t.name + " copy";
    clone.clips = (clone.clips || []).map(function (c) { c.id = uid("clip"); return c; });
    S.tracks.push(clone);
    renderWS();
  }

  /* ───────────────────────── server-side analysis helper ───────────────────────── */
  function analyzeFile(file) {
    var ctx = ensureCtx();
    if (!ctx || !file) return Promise.resolve(null);
    return file.arrayBuffer().then(function (ab) { return ctx.decodeAudioData(ab.slice(0)); })
      .then(function (buf) {
        var energy = 0, ch = buf.getChannelData(0), step = Math.max(1, Math.floor(ch.length / 40000));
        var sum = 0, count = 0;
        for (var i = 0; i < ch.length; i += step) { sum += ch[i] * ch[i]; count++; }
        energy = Math.sqrt(sum / Math.max(1, count));
        return {
          duration: Math.round(buf.duration * 100) / 100,
          energy: Math.round(clamp(energy * 3.2, 0, 1) * 100) / 100,
          sampleRate: buf.sampleRate,
          channels: buf.numberOfChannels
        };
      })
      .catch(function () { return null; });
  }

  /* ───────────────────────── render shell ───────────────────────── */
  function injectStyles() {
    if (document.getElementById("vmwsStyles")) return;
    var css = document.createElement("style");
    css.id = "vmwsStyles";
    css.textContent = [
      "#vmWsPanelMusic .vmws{display:flex;flex-direction:column;height:100%;background:radial-gradient(1200px 600px at 20% -10%,#0d1a33 0%,#05070d 55%);color:#dbe4f3;font-family:inherit;overflow:hidden}",
      "#vmWsPanelMusic .vmws *{box-sizing:border-box}",
      ".vmws-top{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid rgba(255,255,255,.06);background:rgba(10,16,28,.7);backdrop-filter:blur(8px)}",
      ".vmws-brand{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:.2px}",
      ".vmws-brand small{display:block;font-weight:500;font-size:10px;color:#6d7f9c;letter-spacing:.16em;text-transform:uppercase}",
      ".vmws-dot{width:26px;height:26px;border-radius:8px;background:linear-gradient(140deg,#3b82f6,#06b6d4);box-shadow:0 0 18px rgba(59,130,246,.5)}",
      ".vmws-spacer{flex:1}",
      ".vmws-pill{font-size:11px;padding:4px 9px;border-radius:999px;border:1px solid rgba(255,255,255,.12);color:#9fb0cd}",
      ".vmws-pill.warn{border-color:rgba(245,158,11,.5);color:#fbd38d;background:rgba(245,158,11,.08)}",
      ".vmws-pill.ok{border-color:rgba(16,185,129,.4);color:#6ee7b7;background:rgba(16,185,129,.08)}",
      ".vmws-btn{cursor:pointer;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);color:#dbe4f3;padding:7px 12px;border-radius:9px;font:inherit;font-size:12px;transition:.15s}",
      ".vmws-btn:hover{border-color:rgba(59,130,246,.6);background:rgba(59,130,246,.12)}",
      ".vmws-btn.primary{background:linear-gradient(140deg,#2563eb,#0891b2);border-color:transparent;font-weight:600}",
      ".vmws-btn.primary:hover{filter:brightness(1.1)}",
      ".vmws-btn.ghost{background:transparent}",
      ".vmws-btn.sm{padding:4px 8px;font-size:11px}",
      ".vmws-btn:disabled{opacity:.45;cursor:not-allowed}",
      ".vmws-body{flex:1;display:flex;min-height:0}",
      ".vmws-nav{width:190px;flex:none;padding:12px 8px;border-right:1px solid rgba(255,255,255,.05);overflow-y:auto;background:rgba(7,11,20,.55)}",
      ".vmws-nav button{display:flex;align-items:center;gap:10px;width:100%;text-align:left;padding:9px 12px;margin-bottom:2px;border-radius:9px;border:0;background:transparent;color:#93a4c1;font:inherit;font-size:13px;cursor:pointer}",
      ".vmws-nav button:hover{background:rgba(255,255,255,.05);color:#dbe4f3}",
      ".vmws-nav button.active{background:linear-gradient(90deg,rgba(59,130,246,.22),rgba(6,182,212,.1));color:#fff}",
      ".vmws-nav .ic{width:16px;display:inline-flex;justify-content:center;opacity:.9}",
      ".vmws-main{flex:1;min-width:0;overflow-y:auto;padding:18px 22px 96px}",
      ".vmws-main h2{margin:0 0 4px;font-size:19px;letter-spacing:.2px}",
      ".vmws-main .sub{color:#7c8dab;font-size:12.5px;margin-bottom:16px}",
      ".vmws-card{border:1px solid rgba(255,255,255,.07);background:rgba(17,24,39,.55);border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 10px 30px rgba(0,0,0,.25)}",
      ".vmws-grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}",
      ".vmws-field{display:flex;flex-direction:column;gap:6px}",
      ".vmws-field label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#7c8dab}",
      ".vmws-field input,.vmws-field select,.vmws-field textarea{background:rgba(5,8,15,.7);border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:9px 11px;color:#e6edf8;font:inherit;font-size:13px;outline:none;width:100%}",
      ".vmws-field textarea{min-height:110px;resize:vertical;line-height:1.5}",
      ".vmws-field input:focus,.vmws-field select:focus,.vmws-field textarea:focus{border-color:rgba(59,130,246,.65);box-shadow:0 0 0 3px rgba(59,130,246,.12)}",
      ".vmws-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}",
      ".vmws-chips{display:flex;flex-wrap:wrap;gap:7px}",
      ".vmws-chip{cursor:pointer;padding:6px 11px;border-radius:999px;border:1px solid rgba(255,255,255,.12);font-size:12px;color:#a9b8d3;background:rgba(255,255,255,.03)}",
      ".vmws-chip:hover{border-color:rgba(59,130,246,.5)}",
      ".vmws-chip.on{background:rgba(59,130,246,.2);border-color:rgba(59,130,246,.7);color:#fff}",
      ".vmws-seg{display:inline-flex;border:1px solid rgba(255,255,255,.12);border-radius:9px;overflow:hidden}",
      ".vmws-seg button{border:0;background:transparent;color:#93a4c1;padding:7px 14px;font:inherit;font-size:12px;cursor:pointer}",
      ".vmws-seg button.on{background:rgba(59,130,246,.22);color:#fff}",
      ".vmws-result{border-left:3px solid #3b82f6;padding:10px 14px;background:rgba(59,130,246,.07);border-radius:0 10px 10px 0;margin-top:12px;white-space:pre-wrap;font-size:13px;line-height:1.55}",
      ".vmws-result.warn{border-color:#f59e0b;background:rgba(245,158,11,.08)}",
      ".vmws-result.bad{border-color:#ef4444;background:rgba(239,68,68,.08)}",
      ".vmws-result.ok{border-color:#10b981;background:rgba(16,185,129,.08)}",
      ".vmws-empty{padding:48px 20px;text-align:center;color:#6d7f9c}",
      ".vmws-empty .big{font-size:15px;color:#a9b8d3;margin-bottom:6px;font-weight:600}",
      ".vmws-job{display:flex;align-items:center;gap:10px;font-size:12.5px;color:#a9b8d3;margin-top:12px}",
      ".vmws-indet{flex:1;height:4px;border-radius:4px;overflow:hidden;background:rgba(255,255,255,.08);position:relative}",
      ".vmws-indet i{position:absolute;left:-40%;top:0;bottom:0;width:40%;background:linear-gradient(90deg,transparent,#3b82f6,transparent);animation:vmwsSlide 1.1s linear infinite}",
      "@keyframes vmwsSlide{to{left:100%}}",
      ".vmws-tl{position:relative}",
      ".vmws-tl-ruler{position:relative;height:26px;border-bottom:1px solid rgba(255,255,255,.07);margin-left:150px;cursor:pointer}",
      ".vmws-tl-ruler span{position:absolute;top:4px;font-size:10px;color:#6d7f9c;transform:translateX(-50%)}",
      ".vmws-lane{display:flex;border-bottom:1px solid rgba(255,255,255,.05);min-height:64px}",
      ".vmws-lane-h{width:150px;flex:none;padding:8px;border-right:1px solid rgba(255,255,255,.07);background:rgba(9,13,22,.4)}",
      ".vmws-lane-h .nm{font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px;margin-bottom:6px;cursor:text}",
      ".vmws-lane-h .ctrls{display:flex;gap:4px;flex-wrap:wrap}",
      ".vmws-lane-h .ctrls button{width:22px;height:22px;border-radius:6px;border:1px solid rgba(255,255,255,.12);background:transparent;color:#8fa1c0;font-size:10px;cursor:pointer}",
      ".vmws-lane-h .ctrls button.on{background:rgba(59,130,246,.3);color:#fff;border-color:rgba(59,130,246,.7)}",
      ".vmws-lane-h input[type=range]{width:100%;margin-top:5px}",
      ".vmws-lane-body{position:relative;flex:1;overflow:hidden;background:rgba(5,8,14,.4)}",
      ".vmws-lane-body canvas{width:100%;height:64px;display:block;cursor:pointer}",
      ".vmws-clip{position:absolute;top:6px;bottom:6px;border-radius:6px;background:rgba(59,130,246,.22);border:1px solid rgba(59,130,246,.55);overflow:hidden;font-size:10px;color:#cfe0ff;padding:2px 5px}",
      ".vmws-playhead{position:absolute;top:0;bottom:0;width:2px;background:#f43f5e;z-index:5;pointer-events:none}",
      ".vmws-marker{position:absolute;top:0;height:26px;width:2px;background:#eab308}",
      ".vmws-marker b{position:absolute;top:2px;left:4px;font-size:9px;color:#fde68a;white-space:nowrap;font-weight:600}",
      ".vmws-player{position:absolute;left:0;right:0;bottom:0;height:76px;display:flex;align-items:center;gap:14px;padding:0 18px;border-top:1px solid rgba(255,255,255,.08);background:rgba(8,12,21,.94);backdrop-filter:blur(10px);z-index:20}",
      ".vmws-player .art{width:48px;height:48px;border-radius:10px;background:linear-gradient(140deg,#1e3a8a,#0891b2);flex:none;display:flex;align-items:center;justify-content:center;font-weight:700;color:#cfe0ff}",
      ".vmws-player .meta{min-width:150px;max-width:230px}",
      ".vmws-player .meta .t{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".vmws-player .meta .a{font-size:11px;color:#7c8dab;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".vmws-player .ctl{display:flex;align-items:center;gap:8px}",
      ".vmws-player .ctl button{border:0;background:transparent;color:#c7d3e8;cursor:pointer;padding:4px}",
      ".vmws-player .ctl button.play{width:38px;height:38px;border-radius:50%;background:linear-gradient(140deg,#2563eb,#0891b2);color:#fff;font-size:14px}",
      ".vmws-prog{flex:1;height:22px;display:flex;align-items:center;cursor:pointer}",
      ".vmws-prog .track{position:relative;flex:1;height:4px;border-radius:4px;background:rgba(255,255,255,.12)}",
      ".vmws-prog .track i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:linear-gradient(90deg,#3b82f6,#06b6d4)}",
      ".vmws-copilot{width:330px;flex:none;border-left:1px solid rgba(255,255,255,.06);display:flex;flex-direction:column;background:rgba(7,11,20,.6)}",
      ".vmws-copilot .head{padding:12px 14px;border-bottom:1px solid rgba(255,255,255,.06);display:flex;align-items:center;gap:10px}",
      ".vmws-copilot .msg{padding:10px 14px;display:flex;flex-direction:column;gap:10px;overflow-y:auto;flex:1}",
      ".vmws-bubble{max-width:88%;padding:9px 12px;border-radius:12px;font-size:12.5px;line-height:1.5;white-space:pre-wrap}",
      ".vmws-bubble.me{align-self:flex-end;background:rgba(59,130,246,.25);border:1px solid rgba(59,130,246,.4)}",
      ".vmws-bubble.ai{align-self:flex-start;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08)}",
      ".vmws-bubble .lim{margin-top:6px;font-size:11px;color:#fbbf24}",
      ".vmws-copilot .inp{display:flex;gap:8px;padding:10px 12px;border-top:1px solid rgba(255,255,255,.06)}",
      ".vmws-copilot .inp input{flex:1;background:rgba(5,8,15,.8);border:1px solid rgba(255,255,255,.12);border-radius:9px;padding:9px 11px;color:#e6edf8;font:inherit;font-size:12.5px;outline:none}",
      ".vmws-toast{position:fixed;left:50%;bottom:96px;transform:translate(-50%,20px);background:rgba(15,23,42,.96);border:1px solid rgba(255,255,255,.14);color:#e6edf8;padding:11px 16px;border-radius:10px;font-size:13px;z-index:99999;opacity:0;pointer-events:none;transition:.2s}",
      ".vmws-toast.show{opacity:1;transform:translate(-50%,0)}",
      ".vmws-toast.ok{border-color:rgba(16,185,129,.5)}",
      ".vmws-toast.warn{border-color:rgba(245,158,11,.5)}",
      ".vmws-restore{position:fixed;right:18px;bottom:18px;z-index:9000;display:none}",
      ".vmws-card-list{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}",
      ".vmws-item{border:1px solid rgba(255,255,255,.08);background:rgba(17,24,39,.5);border-radius:12px;padding:12px;cursor:pointer;transition:.15s}",
      ".vmws-item:hover{border-color:rgba(59,130,246,.55);transform:translateY(-1px)}",
      ".vmws-item .tt{font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".vmws-item .mm{font-size:11px;color:#7c8dab;margin-top:3px}",
      ".vmws-tools{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}",
      ".vmws-tool{border:1px solid rgba(255,255,255,.07);background:rgba(17,24,39,.55);border-radius:14px;padding:14px;cursor:pointer;transition:.15s;position:relative}",
      ".vmws-tool:hover{border-color:rgba(59,130,246,.45)}",
      ".vmws-tool.on{border-color:rgba(59,130,246,.75);background:rgba(59,130,246,.08);box-shadow:0 0 0 1px rgba(59,130,246,.35)}",
      ".vmws-tool .ttl{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600}",
      ".vmws-tool .hint{font-size:11.5px;color:#7c8dab;margin-top:6px;line-height:1.45}",
      ".vmws-tool .chk{position:absolute;top:12px;right:12px;width:18px;height:18px;border-radius:50%;border:1px solid rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center;font-size:10px;color:#0f172a}",
      ".vmws-tool.on .chk{background:linear-gradient(140deg,#3b82f6,#06b6d4)}",
      ".vmws-param{display:grid;grid-template-columns:110px 1fr;gap:7px 12px;align-items:center;margin-top:8px}",
      ".vmws-param label{font-size:11px;color:#7c8dab;text-align:right}",
      ".vmws-param input[type=number],.vmws-param select{background:rgba(5,8,15,.7);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:6px 9px;color:#e6edf8;font:inherit;font-size:12px;width:100%}",
      ".vmws-param input[type=range]{width:100%}",
      ".vmws-alert{display:flex;gap:10px;align-items:flex-start;padding:11px 14px;border-radius:10px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.4);color:#fcd34d;font-size:12.5px;line-height:1.5;margin:12px 0}",
      ".vmws-alert.ok{background:rgba(16,185,129,.08);border-color:rgba(16,185,129,.4);color:#6ee7b7}",
      ".vmws-alert.bad{background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.4);color:#fca5a5}",
      ".vmws-stats{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}",
      ".vmws-stat{flex:1;min-width:110px;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:10px 12px;background:rgba(9,13,22,.5)}",
      ".vmws-stat b{font-size:16px;display:block}",
      ".vmws-stat span{font-size:10px;color:#7c8dab;text-transform:uppercase;letter-spacing:.08em}",
      ".vmws-fileopt{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}",
      ".vmws-fileopt select{background:rgba(5,8,15,.7);border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:8px 10px;color:#dbe4f3;font:inherit;font-size:12px;min-width:220px}",
      "@media (max-width:900px){.vmws-nav{width:64px}.vmws-nav button span.lbl{display:none}.vmws-copilot{position:absolute;right:0;top:0;bottom:76px;z-index:30;transform:translateX(100%);transition:.2s}.vmws-copilot.open{transform:none}.vmws-player .meta{display:none}.vmws-lane-h{width:110px}}"
    ].join("\n");
    document.head.appendChild(css);
  }

  function icon(name) {
    var paths = {
      sparkles: '<path d="M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5l4.6-1.9z"/>',
      disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/>',
      shuffle: '<path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>',
      "plus-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
      mic: '<path d="M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
      layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
      "file-text": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
      sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
      gauge: '<path d="M12 14l4-4"/><path d="M3.5 18a9 9 0 1 1 17 0"/>',
      folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
      download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
      play: '<path d="M6 4l14 8L6 20z"/>',
      pause: '<path d="M7 4h4v16H7zM13 4h4v16h-4z"/>',
      stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
      prev: '<path d="M18 5v14L8 12zM6 5v14"/>',
      next: '<path d="M6 5v14l10-7zM18 5v14"/>',
      loop: '<path d="M17 2l4 4-4 4M21 6H7a4 4 0 0 0 0 8h1M7 22l-4-4 4-4M3 18h14a4 4 0 0 0 0-8h-1"/>',
      trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
      copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
      lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
      scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 16M14.5 9.5L20 20M8.1 8L12 12"/>',
      wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.3L3 17.9V21h3.1l6.3-6.3a4 4 0 0 0 5.3-5.4l-2.9 2.9-2.2-.2-.2-2.2z"/>',
      cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M8 2v3M16 2v3M8 19v3M16 19v3M2 8h3M2 16h3M19 8h3M19 16h3"/>',
      activity: '<path d="M22 12h-4l-3 8L9 4l-3 8H2"/>',
      waves: '<path d="M2 12h2M6 12h3M11 12h2M15 12h3M20 12h2"/><path d="M5 9v6M8 7v10M13 5v14M17 7v10"/>',
      music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
      upload: '<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>',
      check: '<path d="M20 6L9 17l-5-5"/>'
    };
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + (paths[name] || "") + "</svg>";
  }

  function jobCard(view) {
    var job = S.jobs[view];
    if (!job) return "";
    var st = job.status;
    if (TERMINAL[st]) return "";
    var pct = job.progress;
    return '<div class="vmws-job" data-job="' + h(job.id) + '"><span>' +
      (st === "QUEUED" ? "Queued" : st === "GENERATING" ? "Generating" : "Processing") +
      "…</span>" +
      (typeof pct === "number"
        ? '<div class="vmws-indet"><i style="left:0;width:' + Math.round(pct * 100) + '%;animation:none;background:#3b82f6"></i></div><span>' + Math.round(pct * 100) + "%</span>"
        : '<div class="vmws-indet"><i></i></div>') +
      '</div>';
  }

  function resultCard(res, kind) {
    if (!res) return "";
    if (res.error) return '<div class="vmws-result bad">' + h(res.error) + "</div>";
    if (kind === "error") return '<div class="vmws-result bad">' + h(res) + "</div>";
    return "";
  }

  function providerBanner() {
    if (!S.caps) return "";
    var audioAvailable = S.caps.server_audio_configured;
    var proc = S.caps.audio_processor && S.caps.audio_processor.available;
    var pill = audioAvailable ? "ok" : (proc ? "ok" : "warn");
    var title = audioAvailable
      ? (proc ? "Provider + bundled audio processor available" : "Server-side audio rendering is available")
      : (proc ? "Bundled audio processor available (real editing/measuring/export)" : (S.caps.notes || ""));
    var label = audioAvailable
      ? "Provider: " + ((S.caps.provider && S.caps.provider.label) || "configured")
      : (proc ? "Audio processor: on · Creative AI: on" : "Creative AI: on · Server audio: no provider");
    return '<span class="vmws-pill ' + pill + '" title="' + h(title) + '">' + label + "</span>";
  }

  function renderTop() {
    return '<div class="vmws-top">' +
      '<div class="vmws-brand"><span class="vmws-dot"></span><span>ValleyMind Music Studio<small>AI Music Workstation</small></span></div>' +
      '<span class="vmws-spacer"></span>' +
      providerBanner() +
      '<button class="vmws-btn sm ghost" onclick="VMWs.toggleCopilot()">' + icon("sparkles") + ' Elena</button>' +
      '<button class="vmws-btn sm ghost" onclick="VMWs.openClassic()">Classic Editor</button>' +
      "</div>";
  }

  function renderNav() {
    var out = '<div class="vmws-nav">';
    VIEWS.forEach(function (v) {
      out += '<button class="' + (S.view === v.id ? "active" : "") + '" onclick="VMWs.go(\'' + v.id + '\')"><span class="ic">' +
        icon(v.icon) + '</span><span class="lbl">' + h(v.label) + "</span></button>";
    });
    out += "</div>";
    return out;
  }

  function renderCopilot() {
    var msgs = S.copilot.messages.map(function (m) {
      var lim = (m.limitations && m.limitations.length)
        ? '<div class="lim">Note: ' + h(m.limitations.join(" / ")) + "</div>" : "";
      return '<div class="vmws-bubble ' + (m.role === "me" ? "me" : "ai") + '">' + h(m.text) + lim + "</div>";
    }).join("");
    return '<div class="vmws-copilot' + (S.copilot.open ? " open" : "") + '" id="vmwsCopilot">' +
      '<div class="head"><span class="vmws-dot" style="width:20px;height:20px"></span><strong style="font-size:13px">Elena · Music Co-Pilot</strong><span class="vmws-spacer"></span>' +
      '<button class="vmws-btn sm ghost" onclick="VMWs.toggleCopilot()">✕</button></div>' +
      '<div class="msg" id="vmwsCopilotMsg">' + (msgs || '<div class="vmws-empty" style="padding:20px"><div class="big">Ask Elena to operate your project</div>e.g. "make the chorus bigger", "afrobeat remix", "add Igbo lines".</div>') + "</div>" +
      '<div class="inp"><input id="vmwsCopilotInput" placeholder="Tell Elena what to do…" onkeydown="if(event.key===\'Enter\')VMWs.copilotSend()" value="' + h(S.copilot.input) + '">' +
      '<button class="vmws-btn primary sm" onclick="VMWs.copilotSend()">Send</button></div>' +
      "</div>";
  }

  function renderPlayer() {
    var p = S.player;
    return '<div class="vmws-player">' +
      '<div class="art">' + (p.artwork ? '<img src="' + h(p.artwork) + '" style="width:100%;height:100%;object-fit:cover;border-radius:10px">' : "♪") + "</div>" +
      '<div class="meta"><div class="t">' + h(p.title) + '</div><div class="a">' + h(p.artist) + "</div></div>" +
      '<div class="ctl">' +
      '<button onclick="VMWs.prev()">' + icon("prev") + "</button>" +
      '<button class="play" onclick="VMWs.togglePlay()">' + icon(p.playing ? "pause" : "play") + "</button>" +
      '<button onclick="VMWs.next()">' + icon("next") + "</button>" +
      "</div>" +
      '<div class="vmws-prog" onclick="VMWs.seekFromEvent(event)"><div class="track"><i id="vmwsProgress" style="width:' +
      clamp((p.pos / (p.dur || 1)) * 100, 0, 100) + '%"></i></div></div>' +
      '<span id="vmwsTime" style="font-size:11px;color:#7c8dab;min-width:88px;text-align:right">' + fmtTime(p.pos) + " / " + fmtTime(p.dur) + "</span>" +
      '<button class="vmws-btn sm ghost" onclick="VMWs.toggleLoop()" title="Loop" style="' + (p.loop ? "color:#3b82f6" : "") + '">' + icon("loop") + "</button>" +
      '<select onchange="VMWs.setSpeed(this.value)" style="background:transparent;border:1px solid rgba(255,255,255,.12);border-radius:8px;color:#a9b8d3;font-size:11px;padding:4px">' +
      [0.5, 0.75, 1, 1.25, 1.5].map(function (v) {
        return '<option value="' + v + '"' + (p.speed === v ? " selected" : "") + ">" + v + "x</option>";
      }).join("") + "</select>" +
      '<span title="Volume">🔊</span><input type="range" min="0" max="1" step="0.01" value="' + p.volume + '" oninput="VMWs.setVolume(this.value)" style="width:80px">' +
      "</div>";
  }

  /* ───────────────────────── panels ───────────────────────── */
  function viewCreate() {
    var c = S.create;
    var adv = !c.simple;
    var out = '<h2>Create</h2><div class="sub">Describe a song idea — ValleyMind produces real lyrics, structure and a production plan. Audio rendering depends on the configured provider.</div>';
    out += '<div class="vmws-card">';
    out += '<div class="vmws-row" style="justify-content:space-between;margin-bottom:12px"><div class="vmws-seg">' +
      '<button class="' + (c.simple ? "on" : "") + '" onclick="VMWs.setCreateMode(true)">Simple</button>' +
      '<button class="' + (adv ? "on" : "") + '" onclick="VMWs.setCreateMode(false)">Advanced</button></div>' +
      '<div class="vmws-row"><select onchange="VMWs.setCreateProject(this.value)" style="background:rgba(5,8,15,.7);border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:7px 10px;color:#dbe4f3;font-size:12px">' +
      '<option value="">New project</option>' + activeProjectOptions(c.selectedProject) + "</select></div></div>";

    out += '<div class="vmws-field" style="margin-bottom:12px"><label>Song description</label>' +
      '<textarea id="vmwsBrief" placeholder="e.g. Emotional Afro-R&B song about missing someone after a breakup">' + h(c.brief) + "</textarea></div>";

    out += '<div class="vmws-grid">' +
      field("Genre", select("vmwsGenre", c.genre, [].concat((S.caps && S.caps.genres || []).map(function (g) { return g.label; }), ["Afrobeats", "Amapiano", "Afro-R&B", "Highlife", "Gospel", "Afropop"]).filter(uniq))) +
      field("Mood", select("vmwsMood", c.mood, ["", "Romantic", "Upbeat", "Emotional", "Dark", "Happy", "Chill", "Hopeful", "Energetic", "Spiritual", "Nostalgic"])) +
      field("Language", select("vmwsLanguage", c.language, ["English", "Nigerian Pidgin", "Igbo", "Yoruba", "Hausa", "Swahili", "French", "Portuguese", "isiZulu"])) +
      "</div>";

    if (adv) {
      out += '<div class="vmws-grid" style="margin-top:12px">' +
        field("Subgenre", input("vmwsSubgenre", c.subgenre, "e.g. Igbo Highlife")) +
        field("BPM", input("vmwsBpm", c.bpm, "auto", "number")) +
        field("Key", input("vmwsKey", c.key, "e.g. A minor")) +
        field("Time signature", select("vmwsTimeSig", c.time_signature, ["4/4", "3/4", "6/8", "12/8", "2/4"])) +
        field("Duration (sec)", input("vmwsDuration", c.duration, "auto", "number")) +
        field("Energy", '<input type="range" min="0" max="1" step="0.05" value="' + c.energy + '" oninput="VMWs.setCreateField(\'energy\',this.value)">') +
        field("Vocal", select("vmwsVocalStyle", c.vocal_style, ["auto", "female", "male", "duet", "choir", "rap"])) +
        field("Structure", input("vmwsStructure", c.structure, "Intro-Verse-Chorus-…")) +
        "</div>";
      out += '<div class="vmws-grid" style="margin-top:12px">' +
        field("Lyrics mode", select("vmwsLyricsMode", c.lyrics_mode, ["ai", "custom", "instrumental"])) +
        field("Negative prompt (exclusions)", input("vmwsNegative", c.negative_prompt, "e.g. no auto-tune")) +
        "</div>";
    }

    out += '<div class="vmws-field" style="margin-top:12px"><label>Lyrics (optional — leave blank for AI lyrics)</label>' +
      '<textarea id="vmwsLyrics" style="min-height:120px" placeholder="[Verse]&#10;…">' + h(c.lyrics) + "</textarea></div>";

    out += '<div class="vmws-row" style="margin-top:14px">' +
      '<label class="vmws-row" style="gap:6px;font-size:12px;color:#a9b8d3"><input type="checkbox" id="vmwsInstrumental"' + (c.instrumental ? " checked" : "") + ' onchange="VMWs.setCreateField(\'instrumental\',this.checked)"> Instrumental only</label>' +
      '<span class="vmws-spacer"></span>' +
      '<button class="vmws-btn primary" onclick="VMWs.runCreate()">' + icon("sparkles") + " Generate</button>" +
      '<button class="vmws-btn" onclick="VMWs.attachLyricsToProject()" title="Save lyrics to the selected/new project">Save as project</button>' +
      "</div>" + jobCard("create") + "</div>";

    if (S.create.result) out += renderPackageResult(S.create.result);
    return out;
  }

  function renderPackageResult(res) {
    if (!res) return "";
    if (res.error) return '<div class="vmws-result bad">' + h(res.error) + "</div>";
    var pkg = res.package || {};
    var data = pkg.data || {};
    var audio = res.audio || {};
    var out = '<div class="vmws-card"><h2 style="font-size:16px">' + h(data.title || "Creative package") + "</h2>";
    if (data.structure) out += '<div class="sub" style="margin-bottom:8px">Structure: ' + h(data.structure) + "</div>";
    if (data.bpm || data.key) out += '<div class="vmws-row" style="margin-bottom:10px"><span class="vmws-pill">' + h(data.bpm || "?") + " BPM</span><span class=\"vmws-pill\">" + h(data.key || "?") + "</span></div>";
    if (data.lyrics) out += '<div class="vmws-field"><label>Lyrics</label><div class="vmws-result" style="margin-top:4px">' + h(data.lyrics) + "</div></div>";
    if (data.arrangement) out += '<div class="vmws-field" style="margin-top:10px"><label>Arrangement</label><div class="vmws-result">' + h(data.arrangement) + "</div></div>";
    if (data.instruments && data.instruments.length) out += '<div class="vmws-row" style="margin-top:10px">' + data.instruments.map(function (i) { return '<span class="vmws-chip">' + h(i) + "</span>"; }).join("") + "</div>";
    if (audio.rendered) out += '<div class="vmws-result ok" style="margin-top:12px">Audio rendered by provider.</div>';
    else out += '<div class="vmws-result warn" style="margin-top:12px">' + h(audio.note || "Server-side audio rendering is unavailable. The creative package is real; configure a provider to render audio.") + "</div>";
    out += '<div class="vmws-row" style="margin-top:12px"><button class="vmws-btn sm" onclick="VMWs.addPackageToTimeline()">Add to timeline</button>' +
      '<button class="vmws-btn sm" onclick="VMWs.askElena(\'Give me three different chorus arrangements for this song\')">Ask Elena for variations</button></div>';
    out += "</div>";
    return out;
  }

  function viewParts() {
    var p = S.parts;
    var types = ["drums", "bass", "keys", "guitar", "synth", "pads", "percussion", "fx", "strings", "melody", "other"];
    var out = '<h2>AI Track Generation</h2><div class="sub">Generate a single musical part instead of a whole song — drums, a bassline, a guitar layer, pads. The result is a real creative package you can drop onto the timeline.</div>';
    out += '<div class="vmws-card"><div class="vmws-chips">' + types.map(function (t) {
      return '<span class="vmws-chip' + (p.track_type === t ? " on" : "") + '" onclick="VMWs.setPartField(\'track_type\',\'' + t + '\')">' + h(t) + "</span>";
    }).join("") + "</div>";
    out += '<div class="vmws-grid" style="margin-top:12px">' +
      field("BPM", input("vmwsPartsBpm", p.bpm, "auto")) +
      field("Key", input("vmwsPartsKey", p.key, "e.g. F# minor")) +
      field("Bars", '<input type="number" id="vmwsPartsBars" value="' + p.bars + '" min="2" max="64">') +
      field("Mood", input("vmwsPartsMood", p.mood, "e.g. warm")) +
      "</div>";
    out += '<div class="vmws-field" style="margin-top:12px"><label>Detail (optional)</label>' +
      '<input id="vmwsPartsDetail" value="' + h(p.detail) + '" placeholder="e.g. punchy kick pattern with shakers, Afrobeat bounce"></div>';
    out += '<div class="vmws-row" style="margin-top:12px"><button class="vmws-btn primary" onclick="VMWs.runParts()">' + icon("cpu") + " Generate part</button></div>";
    out += jobCard("parts") + "</div>";
    if (p.result) out += renderPartResult(p.result);
    return out;
  }

  function renderPartResult(res) {
    if (!res) return "";
    if (res.error) return '<div class="vmws-result bad">' + h(res.error) + "</div>";
    var pkg = res.package || {}, data = pkg.data || {}, audio = res.audio || {};
    var out = '<div class="vmws-card"><h2 style="font-size:16px">' + h(res.track_type || "Part") + " — " + h(data.title || "part") + "</h2>";
    if (data.pattern || data.likeness) out += '<div class="vmws-result" style="margin-top:8px">' + (data.pattern ? "Pattern: " + h(data.pattern) + "\n" : "") + h(data.likeness || "") + "</div>";
    if (data.instruments && data.instruments.length) out += '<div class="vmws-row" style="margin-top:10px">' + data.instruments.map(function (i) { return '<span class="vmws-chip">' + h(i) + "</span>"; }).join("") + "</div>";
    out += '<div class="vmws-result ' + (audio.rendered ? "ok" : "warn") + '" style="margin-top:10px">' +
      (audio.rendered ? "Audio rendered by provider." : h(audio.note || (audio.error || "Server-side rendering unavailable — provider not configured. The part is a real arrangement plan you can add to the timeline."))) + "</div>";
    out += '<div class="vmws-row" style="margin-top:12px"><button class="vmws-btn sm" onclick="VMWs.addPartToTimeline()">Add to timeline</button></div>';
    out += "</div>";
    return out;
  }

  function storedFileOptions(selected) {
    var opts = '<option value="">— select a stored file —</option>';
    (S.files || []).forEach(function (f) {
      opts += '<option value="' + h(f.rel) + '"' + (f.rel === selected ? " selected" : "") +
        ">" + h(f.category + " / " + f.name) + "</option>";
    });
    return opts;
  }

  function paramInput(spec, toolId) {
    if (!spec.params.length) return "";
    var rows = spec.params.map(function (pr) {
      var id = "vmwsTool_" + toolId + "_" + pr.key;
      var val = (S.tools.params && S.tools.params[toolId] && S.tools.params[toolId][pr.key]);
      if (val === undefined || val === null) val = pr.default;
      var ctl;
      var span = (Number(pr.max) - Number(pr.min)) / (Number(pr.step) || 1);
      if (span <= 100) {
        ctl = '<input type="range" id="' + id + '" min="' + pr.min + '" max="' + pr.max + '" step="' + pr.step + '" value="' + val +
          '" oninput="var s=document.getElementById(\'' + id + "_v');if(s)s.textContent=this.value\">" +
          '<span style="font-size:10px;color:#64748b" id="' + id + '_v">' + val + "</span>";
      } else {
        ctl = '<input type="number" id="' + id + '" min="' + pr.min + '" max="' + pr.max + '" step="' + pr.step + '" value="' + val + '">';
      }
      return '<label>' + h(pr.label) + "</label>" + ctl;
    }).join("");
    return '<div class="vmws-param">' + rows + "</div>";
  }

  function viewTools() {
    var t = S.tools;
    var processor = S.caps && S.caps.audio_processor;
    var available = !!(processor && processor.available);
    var tools = (processor && processor.tools) || [];
    var out = '<h2>Audio Tools</h2><div class="sub">Real server-side processing on your stored audio — trim, EQ, compress, normalize, denoise, reverb and more. Runs on the bundled ffmpeg processor; no fake progress.</div>';
    if (!available) {
      out += '<div class="vmws-alert bad">The audio processor (bundled ffmpeg) is not available on this server. These tools stay in the architecture and will operate as soon as the server can run ffmpeg.</div>';
      return out;
    }
    out += '<div class="vmws-card"><div class="vmws-field"><label>Source audio</label>' +
      '<select onchange="VMWs.setToolRel(this.value)">' + storedFileOptions(t.rel) + "</select></div>" +
      '<div class="vmws-fileopt"><span style="font-size:12px;color:#7c8dab">or</span><input type="file" accept="audio/*" onchange="VMWs.pickToolFile(this)" style="font-size:12px">' +
      (t.fileName ? '<span class="vmws-pill ok">' + h(t.fileName) + "</span>" : "") +
      '<button class="vmws-btn sm" onclick="VMWs.refreshFileList()">' + icon("activity") + " Refresh files</button></div></div>";

    out += '<div class="vmws-tools">' + tools.map(function (spec) {
      return '<div class="vmws-tool' + (t.selected === spec.id ? " on" : "") + '" onclick="VMWs.setTool(\'' + spec.id + '\')">' +
        '<span class="chk">' + (t.selected === spec.id ? icon("check") : "") + "</span>" +
        '<div class="ttl">' + icon("waves") + " " + h(spec.label) + "</div>" +
        '<div class="hint">' + h(spec.hint) + "</div>" +
        (t.selected === spec.id ? paramInput(spec, spec.id) : "") + "</div>";
    }).join("") + "</div>";

    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.runAudioTool()">' + icon("cpu") + " Process audio</button></div>";
    out += jobCard("tools") + "</div>";
    if (t.result) {
      var r = t.result;
      if (r.status !== "success") out += '<div class="vmws-alert bad">' + h(r.error || "Processing failed") + "</div>";
      else {
        var f = r.file || {};
        out += '<div class="vmws-card"><h2 style="font-size:16px">Processed output</h2>' +
          '<div class="vmws-stats">' +
          '<div class="vmws-stat"><b>' + h(f.name || "") + "</b><span>File</span></div>" +
          (f.analysis && f.analysis.duration ? '<div class="vmws-stat"><b>' + h(Math.round(f.analysis.duration * 10) / 10) + "s</b><span>Duration</span></div>" : "") +
          '<div class="vmws-stat"><b>' + h(f.category || "") + "</b><span>Category</span></div></div>" +
          '<div class="vmws-row" style="margin-top:12px">' +
          '<button class="vmws-btn sm" onclick="VMWs.addToolFileToTimeline()">Add to timeline</button>' +
          '<a class="vmws-btn sm" href="' + h(f.url) + '" download>' + icon("download") + " Download</a></div></div>";
      }
    }
    return out;
  }

  function viewReference() {
    var r = S.reference;
    var out = '<h2>Reference / Inspire</h2><div class="sub">Analyse accessible musical characteristics of a reference and create an ORIGINAL track in that lane. No copyrighted audio is ripped or reproduced.</div>';
    out += '<div class="vmws-card"><div class="vmws-grid">' +
      field("Reference (name / artist / description)", input("vmwsRefName", r.name, "e.g. warm Lagos afro-R&B, log drums, 100 BPM")) +
      field("Genre hint", input("vmwsRefGenre", r.genre_hint, "e.g. Afro-R&B")) +
      "</div>";
    out += '<div class="vmws-field" style="margin-top:12px"><label>Reference audio (optional — analysed for tempo/energy only)</label>' +
      '<input type="file" accept="audio/*" onchange="VMWs.pickRefFile(this)"></div>';
    if (r.fileName) out += '<div class="vmws-row" style="margin-top:6px"><span class="vmws-pill">' + h(r.fileName) + "</span>" + (r.analysis ? '<span class="vmws-pill ok">' + h(Math.round(r.analysis.duration) + "s · energy " + r.analysis.energy) + "</span>" : "") + "</div>";
    out += '<div class="vmws-field" style="margin-top:12px"><label>Notes</label><textarea id="vmwsRefNotes" style="min-height:70px">' + h(r.notes) + "</textarea></div>";
    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.runReference()">Analyse reference</button></div>' + jobCard("reference") + "</div>";
    if (r.result) {
      var a = r.result.analysis || {}, b = r.result.blueprint || {};
      out += '<div class="vmws-card"><h2 style="font-size:16px">Analysis</h2>';
      out += '<div class="vmws-result">' + h(JSON.stringify(a, null, 2)) + "</div>";
      out += '<h2 style="font-size:16px;margin-top:14px">Original blueprint</h2>';
      out += '<div class="vmws-result ok">' + h((b.arrangement || "") + (b.note ? "\n\n" + b.note : "")) + "</div>";
      out += '<div class="vmws-row" style="margin-top:12px"><button class="vmws-btn primary" onclick="VMWs.createFromReference()">Create Original Track From Reference</button></div>';
      out += "</div>";
    }
    return out;
  }

  function viewRemix() {
    var r = S.remix;
    var presets = ["Afrobeat version", "Afropop version", "Amapiano version", "R&B version", "Highlife version", "Gospel version", "Cinematic version", "Acoustic version", "Club version", "Make darker", "Make happier", "Make more energetic", "Make more emotional"];
    var out = '<h2>AI Remix</h2><div class="sub">Transform an existing project or audio. The original is always preserved — a remix creates a new project or version.</div>';
    out += '<div class="vmws-card"><div class="vmws-field"><label>Source project</label><select id="vmwsRemixProject">' + activeProjectOptions(r.projectId) + "</select></div>";
    out += '<div class="vmws-field" style="margin-top:12px"><label>Transformation</label><div class="vmws-chips" style="margin-top:4px">' +
      presets.map(function (p) { return '<span class="vmws-chip' + (r.preset === p ? " on" : "") + '" onclick="VMWs.setRemixPreset(\'' + h(p) + '\')">' + h(p) + "</span>"; }).join("") + "</div></div>";
    out += '<div class="vmws-grid" style="margin-top:12px">' +
      field("Genre", input("vmwsRemixGenre", r.genre, "e.g. Amapiano")) +
      field("Mood", input("vmwsRemixMood", r.mood, "e.g. darker")) +
      field("Tempo", input("vmwsRemixTempo", r.tempo, "e.g. 112")) + "</div>";
    out += '<div class="vmws-field" style="margin-top:12px"><label>Instruction</label><textarea id="vmwsRemixInstruction" style="min-height:70px">' + h(r.instruction) + "</textarea></div>";
    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.runRemix()">Generate remix</button></div>' + jobCard("remix") + "</div>";
    if (r.result) out += renderPlanResult(r.result, "Remix");
    return out;
  }

  function viewExtend() {
    var e = S.extend;
    var out = '<h2>Continue / Extend</h2><div class="sub">Extend intro, verse, chorus, bridge, outro or an instrumental section. Audio extension depends on the provider.</div>';
    out += '<div class="vmws-card"><div class="vmws-field"><label>Source project</label><select>' + activeProjectOptions(e.projectId) + "</select></div>";
    out += '<div class="vmws-grid" style="margin-top:12px">' +
      field("Extend by (seconds)", '<input type="number" id="vmwsExtendSecs" value="' + h(e.seconds) + '">') + "</div>";
    out += '<div class="vmws-field" style="margin-top:12px"><label>Instruction</label><textarea id="vmwsExtendInstruction" style="min-height:70px" placeholder="e.g. Create a bigger final chorus and extend the outro by 20 seconds">' + h(e.instruction) + "</textarea></div>";
    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.runExtend()">Extend song</button></div>' + jobCard("extend") + "</div>";
    if (e.result) out += renderPlanResult(e.result, "Extension");
    return out;
  }

  function viewVocal() {
    var v = S.vocal;
    var out = '<h2>Vocal Lab</h2><div class="sub">Your rough vocal + AI instrumental → polished song. Upload or record your vocal; we analyse it, build a production plan and (with a provider) render the instrumental.</div>';
    out += '<div class="vmws-card"><div class="vmws-grid">' +
      field("Upload vocal", '<input type="file" accept="audio/*" onchange="VMWs.pickVocalFile(this)">') +
      field("Record", '<button class="vmws-btn" onclick="VMWs.toggleRecord()">' + (S.ui.recording ? "Stop (" + fmtTime(S.ui.recordingMs / 1000) + ")" : "Start recording") + "</button>") +
      "</div>";
    if (v.fileName) out += '<div class="vmws-row" style="margin-top:8px"><span class="vmws-pill">' + h(v.fileName) + "</span>" +
      (v.analysis ? '<span class="vmws-pill ok">' + h(Math.round(v.analysis.duration) + "s · energy " + v.analysis.energy) + "</span>" : "") +
      '<button class="vmws-btn sm" onclick="VMWs.previewVocal()">Preview</button></div>';
    out += '<div class="vmws-grid" style="margin-top:12px">' +
      field("Genre", input("vmwsVocalGenre", v.genre, "Afrobeats")) +
      field("Mood", input("vmwsVocalMood", v.mood, "infer")) +
      field("Production style", input("vmwsVocalStyle", v.style, "e.g. warm Afro-R&B")) +
      field("Key override", input("vmwsVocalKey", v.key, "optional")) +
      "</div>";
    out += '<div class="vmws-row" style="margin-top:12px"><label class="vmws-row" style="gap:6px;font-size:12px;color:#a9b8d3"><input type="checkbox"' + (v.clean ? " checked" : "") + ' onchange="VMWs.setVocalField(\'clean\',this.checked)"> Clean / enhance vocals</label></div>';
    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.runVocal()">Produce plan</button></div>' + jobCard("vocal") + "</div>";
    if (v.file) {
      out += '<div class="vmws-card"><h2 style="font-size:16px">Clean & enhance (server processor)</h2>' +
        '<div class="sub" style="margin-top:4px">Process your raw take with the bundled ffmpeg engine: noise reduction, Eq, de-ess and loudness correction. Returns a real processed audio file.</div>';
      out += '<div class="vmws-grid" style="margin-top:10px">' +
        field("Preset", '<select id="vmwsVocalPreset">' +
          ['gentle', 'shine', 'radio'].map(function (p) { return '<option value="' + p + '">' + h(p) + "</option>"; }).join("") + "</select>") +
        "</div>";
      out += '<div class="vmws-row" style="margin-top:12px"><button class="vmws-btn sm" onclick="VMWs.cleanVocal()">' + icon("cpu") + " Clean this take</button>" +
        '<button class="vmws-btn sm" onclick="VMWs.previewVocal()">Raw preview</button></div>' + jobCard("vocal_clean") + "</div>";
      if (S.vocalCleanResult) {
        var cf = S.vocalCleanResult.file || {};
        out += '<div class="vmws-card"><div class="vmws-alert ok" style="margin:0">Cleaned audio ready: <b>' + h(cf.name || "clean.wav") + "</b>.";
        if (S.vocalCleanResult.chain) out += "<div style=\"margin-top:4px;opacity:.8;font-size:11px\">" + h(S.vocalCleanResult.chain) + "</div>";
        out += "</div>" +
          '<div class="vmws-row" style="margin-top:10px">' +
          '<button class="vmws-btn sm" onclick="VMWs.addCleanVocalToTimeline()">Add to timeline (Vox)</button>' +
          '<a class="vmws-btn sm" href="' + h(cf.url) + '" download>' + icon("download") + " Download</a></div></div>";
      }
    }
    if (v.result) {
      var plan = v.result.plan || {};
      out += '<div class="vmws-card"><h2 style="font-size:16px">Production plan</h2>';
      out += '<div class="vmws-result">' + h(JSON.stringify(plan, null, 2)) + "</div>";
      out += '<div class="vmws-result ' + (v.result.polished_song ? "ok" : "warn") + '" style="margin-top:10px">' +
        (v.result.polished_song ? "Provider rendered your polished song." : h(v.result.note || "Plan ready — render the instrumental in-browser or configure a provider.")) + "</div>";
      out += '<div class="vmws-row" style="margin-top:12px"><button class="vmws-btn sm" onclick="VMWs.refineVocal()">Refine with Elena</button></div></div>';
    }
    return out;
  }

  function viewStems() {
    var st = S.stems;
    var out = '<h2>Stem Lab</h2><div class="sub">Separate a song into Vocals, Drums, Bass, Melody and Other. Each stem becomes an editable timeline track.</div>';
    out += '<div class="vmws-card"><div class="vmws-grid">' +
      field("Upload song", '<input type="file" accept="audio/*" onchange="VMWs.pickStemFile(this)">') +
      field("Add to project", '<select>' + activeProjectOptions(st.projectId) + "</select>") +
      "</div>";
    if (st.fileName) out += '<div class="vmws-row" style="margin-top:8px"><span class="vmws-pill">' + h(st.fileName) + "</span></div>";
    out += '<div class="vmws-row" style="margin-top:10px">' + STEM_ORDER.map(function (s) {
      return '<label class="vmws-chip' + (st.wanted[s] ? " on" : "") + '" onclick="VMWs.toggleStem(\'' + s + '\')">' + h(s) + "</label>";
    }).join("") + "</div>";
    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.runStems()">Separate stems</button>' +
      '<button class="vmws-btn sm" onclick="VMWs.runStemsQuick()">' + icon("cpu") + " Quick DSP split</button></div>" + jobCard("stems") + "</div>";
    if (S.stemsQuickResult) {
      var q = S.stemsQuickResult;
      out += '<div class="vmws-card"><h2 style="font-size:16px">Quick DSP split (server processor)</h2>';
      if (q.status !== "success") out += '<div class="vmws-alert bad">' + h(q.error || "Split failed") + "</div>";
      else out += '<div class="vmws-alert ok" style="margin:0">Separated Center (vocals/bass/kick) and Sides (instruments) via real mid/side processing.' +
        "<div style=\"margin-top:4px;opacity:.8;font-size:11px\">" + h(q.note || "") + "</div></div>" +
        '<div class="vmws-row" style="margin-top:10px;flex-wrap:wrap">' + (q.stems || []).map(function (sf, i) {
          return '<span class="vmws-pill">' + h(sf.name || ("part " + i)) + " <a href=\"" + h(sf.url) + '" download style="color:#93c5fd">download</a></span>';
        }).join("") + "</div>" +
        '<div class="vmws-row" style="margin-top:10px"><button class="vmws-btn sm" onclick="VMWs.stemsQuickToTimeline()">Add parts to timeline</button></div>';
      out += "</div>";
    }
    if (st.result) {
      var a = st.result.audio || {};
      if (a.rendered) {
        out += '<div class="vmws-card"><div class="vmws-result ok">Stems separated (' + h((a.stems || []).length) + " tracks).</div>" +
          '<div class="vmws-row" style="margin-top:10px"><button class="vmws-btn primary" onclick="VMWs.stemsToTimeline()">Add stems to timeline</button></div></div>';
      } else {
        out += '<div class="vmws-result warn">' + h(a.note || a.error || "Stem separation needs a configured provider.") + "</div>";
      }
    }
    return out;
  }

  function viewLyrics() {
    var l = S.lyrics;
    var actions = [["generate", "Generate"], ["rewrite", "Rewrite"], ["continue", "Continue"], ["rhyme", "Change rhyme"], ["emotional", "More emotional"], ["poetic", "More poetic"], ["simplify", "Simplify"], ["igbo", "Add Igbo"], ["pidgin", "Add Pidgin"], ["mix", "Mix En+Igbo+Pidgin"], ["translate", "Translate"]];
    var out = '<h2>AI Lyrics</h2><div class="sub">Generate, rewrite, continue, translate and add Nigerian language flavour. Attach lyrics to a project.</div>';
    out += '<div class="vmws-card"><div class="vmws-field"><label>Lyrics</label><textarea id="vmwsLyricsText" style="min-height:180px">' + h(l.text) + "</textarea></div>";
    out += '<div class="vmws-chips" style="margin-top:10px">' + actions.map(function (a) {
      return '<span class="vmws-chip' + (l.action === a[0] ? " on" : "") + '" onclick="VMWs.runLyrics(\'' + a[0] + '\')">' + h(a[1]) + "</span>";
    }).join("") + "</div>";
    out += '<div class="vmws-grid" style="margin-top:12px">' +
      field("Language", input("vmwsLyricsLang", l.language, "English")) +
      field("Target language (translate)", input("vmwsLyricsTarget", l.target, "optional")) +
      field("Attach to project", '<select>' + activeProjectOptions(l.projectId) + "</select>") + "</div>";
    out += '<div class="vmws-field" style="margin-top:12px"><label>Extra instruction</label><input id="vmwsLyricsInstr" value="' + h(l.instruction) + '"></div>';
    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.runLyrics(S.lyrics.action)">Run</button></div>' + jobCard("lyrics") + "</div>";
    if (l.result) out += '<div class="vmws-card"><div class="vmws-result ok" style="white-space:pre-wrap">' + h(l.result.lyrics || l.result.error || "") + "</div></div>";
    return out;
  }

  function viewTimeline() {
    var out = '<h2>Multitrack Timeline</h2><div class="sub">A DAW-style workspace. Controls operate on the real Web-Audio session; edits are non-destructive until exported.</div>';
    out += '<div class="vmws-card" style="padding-bottom:70px;position:relative">';
    out += '<div class="vmws-row" style="margin-bottom:10px">' +
      '<button class="vmws-btn sm" onclick="VMWs.togglePlay()">' + icon(S.player.playing ? "pause" : "play") + (S.player.playing ? " Pause" : " Play") + "</button>" +
      '<button class="vmws-btn sm" onclick="VMWs.stop()">' + icon("stop") + " Stop</button>" +
      '<button class="vmws-btn sm' + (S.ui.snap ? " primary" : "") + '" onclick="VMWs.toggleSnap()">Snap</button>' +
      '<button class="vmws-btn sm" onclick="VMWs.addTrackPrompt()">+ Track</button>' +
      '<button class="vmws-btn sm" onclick="VMWs.pickTrackFile()">+ Audio</button>' +
      '<span class="vmws-spacer"></span>' +
      '<span style="font-size:11px;color:#7c8dab">Zoom</span><input type="range" min="0.5" max="3" step="0.1" value="' + S.ui.timelineZoom + '" oninput="VMWs.setZoom(this.value)">' +
      "</div>";
    if (!S.tracks.length) {
      out += '<div class="vmws-empty"><div class="big">No tracks yet</div>Generate a song, separate stems, or add audio to begin arranging.</div>';
    } else {
      var dur = timelineDuration() || 60;
      var ticks = "";
      var interval = dur > 300 ? 60 : dur > 120 ? 30 : 10;
      for (var t = 0; t <= dur; t += interval) ticks += '<span style="left:' + (t / dur * 100) + '%">' + fmtTime(t) + "</span>";
      out += '<div class="vmws-tl"><div class="vmws-tl-ruler" style="width:' + (S.ui.timelineZoom * 100) + '%;min-width:100%" onclick="VMWs.seekFromTimeline(event)">' + ticks +
        S.sections.map(function (sec) {
          return '<div class="vmws-marker" style="left:' + (sec.start / dur * 100) + '%"><b>' + h(sec.label) + "</b></div>";
        }).join("") + '<div class="vmws-playhead" id="vmwsPlayhead" style="left:0"></div></div>';
      out += S.tracks.map(renderLane).join("");
      out += "</div></div>";
    }
    out += "</div>";
    return out;
  }

  function renderLane(t) {
    var dur = timelineDuration() || 60;
    var muted = t.mute ? " on" : "", solo = t.solo ? " on" : "", lock = t.lock ? " on" : "";
    var clips = (t.clips || []).map(function (c) {
      return '<div class="vmws-clip" id="' + h(c.id) + '" style="left:' + (c.start / dur * 100) + '%;width:' + (c.duration / dur * 100) + '%" ' +
        'onclick="VMWs.selectClip(\'' + h(t.id) + '\',\'' + h(c.id) + '\',event)">' + h(t.name) + "</div>";
    }).join("");
    return '<div class="vmws-lane"><div class="vmws-lane-h">' +
      '<div class="nm" onclick="VMWs.renameTrack(\'' + h(t.id) + '\')">' + '<span style="color:' + t.color + '">●</span> ' + h(t.name) + "</div>" +
      '<div class="ctrls">' +
      '<button class="' + mute + '" onclick="VMWs.trackProp(\'' + h(t.id) + '\',\'mute\',!getTrackProp(\'' + h(t.id) + '\',\'mute\'))" title="Mute">M</button>' +
      '<button class="' + solo + '" onclick="VMWs.trackProp(\'' + h(t.id) + '\',\'solo\',!getTrackProp(\'' + h(t.id) + '\',\'solo\'))" title="Solo">S</button>' +
      '<button class="' + lock + '" onclick="VMWs.trackProp(\'' + h(t.id) + '\',\'lock\',!getTrackProp(\'' + h(t.id) + '\',\'lock\'))" title="Lock">' + icon("lock") + "</button>" +
      '<button onclick="VMWs.splitTrack(\'' + h(t.id) + '\')" title="Split at playhead">' + icon("scissors") + "</button>" +
      '<button onclick="VMWs.duplicateTrack(\'' + h(t.id) + '\')" title="Duplicate">' + icon("copy") + "</button>" +
      '<button onclick="VMWs.removeTrack(\'' + h(t.id) + '\')" title="Delete">' + icon("trash") + "</button>" +
      "</div>" +
      '<input type="range" min="0" max="1" step="0.01" value="' + t.vol + '" oninput="VMWs.trackProp(\'' + h(t.id) + '\',\'vol\',parseFloat(this.value))" title="Volume">' +
      '<input type="range" min="-1" max="1" step="0.05" value="' + (t.pan || 0) + '" oninput="VMWs.trackProp(\'' + h(t.id) + '\',\'pan\',parseFloat(this.value))" title="Pan">' +
      "</div>" +
      '<div class="vmws-lane-body">' + clips + "</div></div>";
  }

  function viewMaster() {
    var m = S.master;
    var targets = (S.caps && S.caps.master_targets) || [];
    var out = '<h2>AI Master</h2><div class="sub">Real AI mastering recommendations for your mix. A mastered audio file requires a configured provider.</div>';
    out += '<div class="vmws-card"><div class="vmws-grid">' +
      field("Target", select2("vmwsMasterTarget", m.target, targets.map(function (t) { return { v: t.id, l: t.label }; }))) +
      field("Project", '<select>' + activeProjectOptions(m.projectId) + "</select>") +
      "</div>";
    out += '<div class="vmws-field" style="margin-top:12px"><label>Instruction</label><input id="vmwsMasterInstr" value="Radio-level loudness without squashing it"></div>';
    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.runMaster()">Analyse & master</button>' +
      '<label class="vmws-row" style="gap:6px;font-size:12px"><input type="checkbox"' + (m.preview ? " checked" : "") + ' onchange="VMWs.toggleMasterPreview(this.checked)"> Before/after browser preview</label></div>' + jobCard("master") + "</div>";
    out += '<div class="vmws-card"><h2 style="font-size:16px">Measure loudness (server processor)</h2>' +
      '<div class="sub" style="margin-top:4px">Real EBU R128 measurement on a stored file — integrated loudness, loudness range, true peak and volume stats.</div>' +
      '<div class="vmws-fileopt" style="margin-top:10px">' +
      '<select id="vmwsLoudnessRel" onchange="S.masterAnalysis=null;renderWS()">' + storedFileOptions("") + "</select>" +
      '<button class="vmws-btn sm" onclick="VMWs.measureLoudness()">' + icon("activity") + " Measure</button>" +
      '<button class="vmws-btn sm" onclick="VMWs.refreshFileList()">Refresh files</button></div>' + jobCard("loudness") + "</div>";
    if (S.masterAnalysis) {
      var L = S.masterAnalysis;
      if (L.status !== "success") out += '<div class="vmws-alert bad">' + h(L.error || "Measurement failed") + "</div>";
      else {
        var lufs = L.integrated_lufs;
        var verdict = lufs == null ? "Measurement complete."
          : (lufs < -16 ? "On the quiet side — a mastering target of ≈ -14 LUFS is typical."
            : (lufs > -9 ? "Loud mix — watch true-peak headroom and distortion."
              : "Healthy level — sits around the streaming target of ≈ -14 LUFS."));
        out += '<div class="vmws-card"><h2 style="font-size:16px">Loudness report</h2><div class="vmws-stats">' +
          '<div class="vmws-stat"><b>' + h((lufs == null ? "—" : lufs.toFixed(1))) + "</b><span>Integrated (LUFS)</span></div>" +
          '<div class="vmws-stat"><b>' + h((L.lra == null ? "—" : L.lra.toFixed(1))) + "</b><span>Range (LU)</span></div>" +
          '<div class="vmws-stat"><b>' + h((L.max_db == null ? "—" : L.max_db.toFixed(1))) + "</b><span>Sample peak (dB)</span></div>" +
          '<div class="vmws-stat"><b>' + h((L.mean_db == null ? "—" : L.mean_db.toFixed(1))) + "</b><span>Mean level (dB)</span></div>" +
          "</div>" +
          '<div class="vmws-alert ok" style="margin-top:12px">' + h(verdict) + "</div></div>";
      }
    }
    if (m.result) {
      var ms = m.result.mastering || {};
      out += '<div class="vmws-card"><h2 style="font-size:16px">Recommendations</h2><div class="vmws-result">' + h(JSON.stringify(ms, null, 2)) + "</div>";
      out += '<div class="vmws-result ' + (m.result.mastered ? "ok" : "warn") + '" style="margin-top:10px">' + h(m.result.note) + "</div></div>";
    }
    return out;
  }

  function viewLibrary() {
    var out = '<h2>Music Library</h2><div class="sub">All your projects, drafts, instrumentals, vocals, stems and exports.</div>';
    out += '<div class="vmws-card"><div class="vmws-row"><input id="vmwsLibQuery" placeholder="Search projects…" value="' + h(S.library.query) + '" style="flex:1;background:rgba(5,8,15,.7);border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:9px 11px;color:#e6edf8" onkeydown="if(event.key===\'Enter\')VMWs.searchLibrary(this.value)">' +
      '<button class="vmws-btn primary" onclick="VMWs.searchLibrary(document.getElementById(\'vmwsLibQuery\').value)">Search</button></div>';
    out += '<div class="vmws-chips" style="margin-top:10px">' +
      ["all", "projects", "drafts", "instrumentals", "vocals", "stems", "favorites", "exports"].map(function (c) {
        return '<span class="vmws-chip' + (S.library.cat === c ? " on" : "") + '" onclick="VMWs.filterLibrary(\'' + c + '\')">' + h(c) + "</span>";
      }).join("") + "</div></div>";
    if (!S.library.items.length) out += '<div class="vmws-empty"><div class="big">No projects found</div>Create a song to see it here.</div>';
    else out += '<div class="vmws-card-list">' + S.library.items.map(function (p) {
      return '<div class="vmws-item" onclick="VMWs.openProject(\'' + h(p.id) + '\')"><div class="tt">' + h(p.name || p.title || "Untitled") + "</div>" +
        '<div class="mm">' + h(p.genre || "—") + " · " + h(p.bpm ? p.bpm + " BPM" : "—") + " · " + h((p.versions || []).length) + " versions</div></div>";
    }).join("") + "</div>";
    return out;
  }

  function viewExport() {
    var e = S.exp;
    var out = '<h2>Export</h2><div class="sub">Export formats depend on what the backend can actually produce. Existing stored audio and the project package are always available.</div>';
    out += '<div class="vmws-card"><div class="vmws-grid">' +
      field("Project", '<select onchange="VMWs.setExpProject(this.value)">' + activeProjectOptions(e.projectId) + "</select>") +
      field("Format", select2("vmwsExpFormat", e.format, [
        { v: "wav", l: "WAV" }, { v: "mp3", l: "MP3" },
        { v: "stems", l: "Stems" }, { v: "instrumental", l: "Instrumental" },
        { v: "vocals", l: "Vocals" }, { v: "project_package", l: "Project package" }
      ])) + "</div>";
    out += '<div class="vmws-row" style="margin-top:14px"><button class="vmws-btn primary" onclick="VMWs.prepareExport()">Check availability</button></div>' + jobCard("export") + "</div>";
    out += '<div class="vmws-card"><h2 style="font-size:16px">Convert stored file (server processor)</h2>' +
      '<div class="sub" style="margin-top:4px">Transcode any stored audio to WAV or MP3 with the bundled ffmpeg engine.</div>' +
      '<div class="vmws-grid" style="margin-top:10px">' +
      field("Source file", '<select id="vmwsConvertRel">' + storedFileOptions("") + "</select>") +
      field("Target format", '<select id="vmwsConvertFormat"><option value="wav">WAV</option><option value="mp3">MP3</option></select>') +
      "</div>" +
      '<div class="vmws-row" style="margin-top:12px"><button class="vmws-btn sm" onclick="VMWs.convertExport()">' + icon("cpu") + " Convert</button>" +
      '<button class="vmws-btn sm" onclick="VMWs.refreshFileList()">Refresh files</button></div>' + jobCard("convert") + "</div>";
    if (S.expConverted) {
      var c = S.expConverted;
      if (c.status !== "success") out += '<div class="vmws-alert bad">' + h(c.error || "Conversion failed") + "</div>";
      else out += '<div class="vmws-card"><div class="vmws-alert ok" style="margin:0">Converted to <b>' + h(c.file.name || c.format) + "</b>." +
        '<div style="margin-top:4px;opacity:.8;font-size:11px">' + h(c.file.rel || "") + "</div></div>" +
        '<div class="vmws-row" style="margin-top:10px"><a class="vmws-btn sm" href="' + h(c.file.url) + '" download>' + icon("download") + " Download</a></div></div>";
    }
    if (e.result) {
      out += '<div class="vmws-card"><h2 style="font-size:16px">Available</h2><div class="vmws-result">' +
        h(JSON.stringify(e.result.available || {}, null, 2)) + "</div>" +
        '<div class="vmws-result warn" style="margin-top:10px">' + h(e.result.note || "") + "</div>";
      if ((e.result.files || []).length) {
        out += '<div style="margin-top:12px">' + e.result.files.map(function (f) {
          return '<div class="vmws-row" style="margin-bottom:6px"><a class="vmws-btn sm" href="' + h(f.url) + '" download>' + icon("download") + " " + h(f.name) + "</a></div>";
        }).join("") + "</div>";
      }
      out += '<div class="vmws-row" style="margin-top:12px"><button class="vmws-btn sm" onclick="VMWs.downloadProjectPackage()">Download project package (JSON)</button></div></div>';
    }
    return out;
  }

  /* ── small form helpers ── */
  function uniq(v, i, a) { return a.indexOf(v) === i; }
  function field(label, control) {
    return '<div class="vmws-field"><label>' + h(label) + "</label>" + control + "</div>";
  }
  function input(id, val, ph, type) {
    return '<input id="' + id + '" type="' + (type || "text") + '" value="' + h(val) + '" placeholder="' + h(ph || "") + '">';
  }
  function select(id, val, opts) {
    return '<select id="' + id + '">' + opts.map(function (o) {
      var v = typeof o === "object" ? o.v : o, l = typeof o === "object" ? o.l : (o || "—");
      return '<option value="' + h(v) + '"' + (String(v) === String(val) ? " selected" : "") + ">" + h(l) + "</option>";
    }).join("") + "</select>";
  }
  function select2(id, val, opts) { return select(id, val, opts); }

  function currentView() {
    switch (S.view) {
      case "create": return viewCreate();
      case "parts": return viewParts();
      case "reference": return viewReference();
      case "remix": return viewRemix();
      case "extend": return viewExtend();
      case "vocal": return viewVocal();
      case "stems": return viewStems();
      case "lyrics": return viewLyrics();
      case "timeline": return viewTimeline();
      case "tools": return viewTools();
      case "master": return viewMaster();
      case "library": return viewLibrary();
      case "export": return viewExport();
      default: return viewCreate();
    }
  }

  function renderWS() {
    var panel = document.getElementById("vmWsPanelMusic");
    if (!panel) return;
    injectStyles();
    syncFormInputs();
    var html = '<div class="vmws">' + renderTop() +
      '<div class="vmws-body">' + renderNav() +
      '<div class="vmws-main">' + currentView() + "</div>" + renderCopilot() + "</div>" +
      renderPlayer() + "</div>";
    panel.innerHTML = html;
    if (S.player.playing) updatePlayheadUI();
  }

  /* Pull values from inputs we don't bind on every keystroke. */
  function syncFormInputs() {
    function v(id) { var el = document.getElementById(id); return el ? el.value : undefined; }
    var c = S.create;
    if (v("vmwsBrief") !== undefined) c.brief = v("vmwsBrief");
    if (v("vmwsGenre") !== undefined) c.genre = v("vmwsGenre");
    if (v("vmwsMood") !== undefined) c.mood = v("vmwsMood");
    if (v("vmwsLanguage") !== undefined) c.language = v("vmwsLanguage");
    if (v("vmwsLyrics") !== undefined) c.lyrics = v("vmwsLyrics");
    if (v("vmwsSubgenre") !== undefined) c.subgenre = v("vmwsSubgenre");
    if (v("vmwsBpm") !== undefined) c.bpm = v("vmwsBpm");
    if (v("vmwsKey") !== undefined) c.key = v("vmwsKey");
    if (v("vmwsTimeSig") !== undefined) c.time_signature = v("vmwsTimeSig");
    if (v("vmwsDuration") !== undefined) c.duration = v("vmwsDuration");
    if (v("vmwsStructure") !== undefined) c.structure = v("vmwsStructure");
    if (v("vmwsVocalStyle") !== undefined) c.vocal_style = v("vmwsVocalStyle");
    if (v("vmwsLyricsMode") !== undefined) c.lyrics_mode = v("vmwsLyricsMode");
    if (v("vmwsNegative") !== undefined) c.negative_prompt = v("vmwsNegative");
    if (v("vmwsPartsBpm") !== undefined) S.parts.bpm = v("vmwsPartsBpm");
    if (v("vmwsPartsKey") !== undefined) S.parts.key = v("vmwsPartsKey");
    if (v("vmwsPartsBars") !== undefined) S.parts.bars = v("vmwsPartsBars");
    if (v("vmwsPartsMood") !== undefined) S.parts.mood = v("vmwsPartsMood");
    if (v("vmwsPartsDetail") !== undefined) S.parts.detail = v("vmwsPartsDetail");
    if (v("vmwsRefName") !== undefined) S.reference.name = v("vmwsRefName");
    if (v("vmwsRefGenre") !== undefined) S.reference.genre_hint = v("vmwsRefGenre");
    if (v("vmwsRefNotes") !== undefined) S.reference.notes = v("vmwsRefNotes");
    if (v("vmwsRemixInstruction") !== undefined) S.remix.instruction = v("vmwsRemixInstruction");
    if (v("vmwsRemixGenre") !== undefined) S.remix.genre = v("vmwsRemixGenre");
    if (v("vmwsRemixMood") !== undefined) S.remix.mood = v("vmwsRemixMood");
    if (v("vmwsRemixTempo") !== undefined) S.remix.tempo = v("vmwsRemixTempo");
    if (v("vmwsExtendInstruction") !== undefined) S.extend.instruction = v("vmwsExtendInstruction");
    if (v("vmwsExtendSecs") !== undefined) S.extend.seconds = v("vmwsExtendSecs");
    if (v("vmwsVocalGenre") !== undefined) S.vocal.genre = v("vmwsVocalGenre");
    if (v("vmwsVocalMood") !== undefined) S.vocal.mood = v("vmwsVocalMood");
    if (v("vmwsVocalStyle") !== undefined) S.vocal.style = v("vmwsVocalStyle");
    if (v("vmwsVocalKey") !== undefined) S.vocal.key = v("vmwsVocalKey");
    if (v("vmwsLyricsText") !== undefined) S.lyrics.text = v("vmwsLyricsText");
    if (v("vmwsLyricsLang") !== undefined) S.lyrics.language = v("vmwsLyricsLang");
    if (v("vmwsLyricsTarget") !== undefined) S.lyrics.target = v("vmwsLyricsTarget");
    if (v("vmwsLyricsInstr") !== undefined) S.lyrics.instruction = v("vmwsLyricsInstr");
  }

  /* ───────────────────────── job runners ───────────────────────── */
  function runJob(view, submitFn, onDone) {
    S.jobs[view] = { id: "pending", status: "QUEUED", progress: null };
    renderWS();
    submitFn().then(function (d) {
      if (!d.job) throw new Error(d.message || "Could not start job");
      S.jobs[view] = d.job;
      renderWS();
      return pollJob(d.job.id, function (job) { S.jobs[view] = job; renderWS(); });
    }).then(function (job) {
      if (job.status === "FAILED") {
        toast(job.error || "Operation failed", "warn");
        if (onDone) onDone(job, true);
      } else if (job.status === "CANCELLED") {
        toast("Cancelled");
      } else if (onDone) {
        onDone(job, false);
      }
      delete S.jobs[view];
      renderWS();
    }).catch(function (e) {
      delete S.jobs[view];
      toast(e.message || "Request failed", "warn");
      renderWS();
    });
  }

  /*
   * Fetch the stored-audio file index (used by Audio Tools, Master, Export).
   * The export endpoint doubles as an honest availability check.
   */
  function refreshStoredFiles() {
    return apiJson(API + "/export", { project_id: (S.project && S.project.id) || "" })
      .then(function (d) { S.files = d.files || []; return d; })
      .catch(function () { S.files = S.files || []; return null; });
  }

  /* ───────────────────────── API: public actions ───────────────────────── */
  window.VMWs = {
    go: function (v) {
      S.view = v;
      if (v === "library") refreshLibrary().then(renderWS);
      else if (v === "tools" || v === "master" || v === "export") refreshStoredFiles().then(renderWS);
      else renderWS();
    },
    render: renderWS,
    onShow: function () { if (S.mode === "classic") { if (origOnShow) origOnShow(); } else renderWS(); },
    toggleCopilot: function () { S.copilot.open = !S.copilot.open; renderWS(); },

    openClassic: function () {
      S.mode = "classic";
      transportPause();
      if (origOnShow) origOnShow();
      else if (window.VMMusic && VMMusic.render) VMMusic.render();
      var b = els.restore;
      if (b) b.style.display = "block";
    },
    openWorkstation: function () {
      S.mode = "ws";
      var b = els.restore;
      if (b) b.style.display = "none";
      renderWS();
    },

    /* Create */
    setCreateMode: function (simple) { syncFormInputs(); S.create.simple = !!simple; renderWS(); },
    setCreateField: function (k, v) { S.create[k] = v; },
    setCreateProject: function (id) { S.create.selectedProject = id; if (id) S.project = S.projects.filter(function (p) { return p.id === id; })[0] || S.project; },

    runCreate: function () {
      syncFormInputs();
      var c = S.create;
      if (!c.brief.trim() && !c.lyrics.trim()) { toast("Describe your song first.", "warn"); return; }
      var payload = {
        brief: c.brief, genre: c.genre, subgenre: c.subgenre, mood: c.mood,
        energy: c.energy, bpm: c.bpm || null, key: c.key, time_signature: c.time_signature,
        duration: c.duration || null, instrumental: c.instrumental,
        vocal_style: c.vocal_style, language: c.language, lyrics_mode: c.lyrics_mode,
        lyrics: c.lyrics, structure: c.structure, negative_prompt: c.negative_prompt,
        project_id: c.selectedProject || ""
      };
      runJob("create", function () { return apiJson(API + "/generate", payload); }, function (job) {
        S.create.result = job.result;
        if (job.result && job.result.project) { S.project = job.result.project; refreshLibrary().then(renderWS); }
        else if (!c.selectedProject) {
          saveActiveProject({ name: (job.result.package.data.title || "Untitled"), genre: c.genre, bpm: c.bpm || job.result.package.data.bpm, lyrics: c.lyrics || job.result.package.data.lyrics, brief: c.brief, mood: c.mood, structure: c.structure }).then(refreshLibrary);
        }
        toast("Generated creative package", "ok");
      });
    },

    attachLyricsToProject: function () {
      syncFormInputs();
      saveActiveProject({ name: S.create.brief.slice(0, 40) || "Untitled", lyrics: S.create.lyrics, brief: S.create.brief, genre: S.create.genre, mood: S.create.mood }).then(function () { refreshLibrary().then(renderWS); });
    },

    addPackageToTimeline: function () {
      var res = S.create.result;
      if (!res) return;
      var data = (res.package && res.package.data) || {};
      addTrack({ name: data.title || "AI Track", url: "", kind: "ai" });
      S.view = "timeline";
      renderWS();
      toast("Added as a timeline track (audio renders once a provider is configured)", "ok");
    },

    /* Reference */
    pickRefFile: function (el) {
      var f = el.files && el.files[0]; if (!f) return;
      S.reference.file = f; S.reference.fileName = f.name;
      var self = this;
      analyzeFile(f).then(function (a) { S.reference.analysis = a; renderWS(); });
      renderWS();
    },
    runReference: function () {
      syncFormInputs();
      var fd = new FormData();
      fd.append("reference", S.reference.name || "Uploaded reference");
      fd.append("genre_hint", S.reference.genre_hint || "");
      fd.append("user_notes", S.reference.notes || "");
      if (S.reference.analysis) fd.append("analysis", JSON.stringify(S.reference.analysis));
      if (S.reference.file) fd.append("file", S.reference.file);
      runJob("reference", function () { return apiUpload(API + "/reference/analyze", fd); }, function (job) {
        S.reference.result = job.result;
        toast("Reference analysed", "ok");
      });
    },
    createFromReference: function () {
      var b = (S.reference.result && S.reference.result.blueprint) || {};
      S.view = "create";
      S.create.simple = false;
      S.create.brief = b.arrangement || S.reference.name;
      S.create.genre = b.genre || S.reference.genre_hint || "Afrobeats";
      S.create.bpm = b.bpm || "";
      S.create.key = b.key || "";
      S.create.structure = b.structure || "";
      renderWS();
      toast("Blueprint loaded into Create — generate an original track", "ok");
    },

    /* Remix */
    setRemixPreset: function (p) { syncFormInputs(); S.remix.preset = p; S.remix.instruction = p; renderWS(); },
    runRemix: function () {
      syncFormInputs();
      runJob("remix", function () {
        return apiJson(API + "/remix", {
          project_id: S.remix.projectId || "",
          source: S.create.result && S.create.result.package && S.create.result.package.data,
          instruction: S.remix.instruction || S.remix.preset,
          genre: S.remix.genre, mood: S.remix.mood, tempo: S.remix.tempo,
          as_new_project: true
        });
      }, function (job) {
        S.remix.result = job.result;
        if (job.result.project) { S.project = job.result.project; refreshLibrary(); }
        toast("Remix plan ready — original preserved", "ok");
      });
    },

    /* Extend */
    runExtend: function () {
      syncFormInputs();
      runJob("extend", function () {
        return apiJson(API + "/extend", {
          project_id: S.extend.projectId || (S.project && S.project.id) || "",
          target_seconds: Number(S.extend.seconds) || 0,
          instruction: S.extend.instruction
        });
      }, function (job) {
        S.extend.result = job.result;
        toast("Extension plan ready", "ok");
      });
    },

    /* Vocal */
    pickVocalFile: function (el) {
      var f = el.files && el.files[0]; if (!f) return;
      S.vocal.file = f; S.vocal.fileName = f.name;
      analyzeFile(f).then(function (a) { S.vocal.analysis = a; renderWS(); });
      renderWS();
    },
    previewVocal: function () {
      if (!S.vocal.file) return;
      var url = URL.createObjectURL(S.vocal.file);
      setPlayer({ title: "Vocal take", artist: "Vocal Lab", url: url });
      playSingle(url);
    },
    setVocalField: function (k, v) { S.vocal[k] = v; },
    runVocal: function () {
      var v = S.vocal;
      if (!v.file) { toast("Upload or record a vocal first.", "warn"); return; }
      var fd = new FormData();
      fd.append("file", v.file);
      fd.append("genre", v.genre);
      fd.append("mood", v.mood);
      fd.append("production_style", v.style);
      fd.append("key_override", v.key);
      fd.append("clean", v.clean ? "1" : "0");
      if (v.analysis) fd.append("analysis", JSON.stringify(v.analysis));
      if (v.projectId) fd.append("project_id", v.projectId);
      runJob("vocal", function () { return apiUpload(API + "/vocal/process", fd); }, function (job) {
        S.vocal.result = job.result;
        toast(job.result.polished_song ? "Polished song rendered" : "Production plan ready", "ok");
      });
    },
    refineVocal: function () {
      var instr = prompt("What should change?", "make it warmer and bigger");
      if (!instr) return;
      apiJson(API + "/vocal/refine", { instruction: instr, plan: S.vocal.result && S.vocal.result.plan, genre: S.vocal.genre })
        .then(function (d) {
          if (S.vocal.result) { S.vocal.result.plan = Object.assign({}, S.vocal.result.plan, d.changes); }
          toast(d.summary || "Plan refined", "ok");
          renderWS();
        }).catch(function (e) { toast(e.message, "warn"); });
    },
    toggleRecord: function () { toggleRecord(); },

    /* Stems */
    pickStemFile: function (el) {
      var f = el.files && el.files[0]; if (!f) return;
      S.stems.file = f; S.stems.fileName = f.name; renderWS();
    },
    toggleStem: function (s) { S.stems.wanted[s] = !S.stems.wanted[s]; renderWS(); },
    runStems: function () {
      if (!S.stems.file) { toast("Upload a song first.", "warn"); return; }
      var fd = new FormData();
      fd.append("file", S.stems.file);
      var wanted = STEM_ORDER.filter(function (s) { return S.stems.wanted[s]; });
      fd.append("stems", JSON.stringify(wanted));
      if (S.stems.projectId) fd.append("project_id", S.stems.projectId);
      runJob("stems", function () { return apiUpload(API + "/stems/separate", fd); }, function (job) {
        S.stems.result = job.result;
        toast("Stems ready", "ok");
      });
    },
    stemsToTimeline: function () {
      var a = (S.stems.result && S.stems.result.audio) || {};
      (a.stems || []).forEach(function (s) {
        addTrack({ name: s.name || "Stem", url: s.url, kind: "stem", sourceRel: s.rel });
      });
      S.view = "timeline";
      renderWS();
    },

    /* Lyrics */
    runLyrics: function (action) {
      syncFormInputs();
      S.lyrics.action = action || S.lyrics.action;
      S.jobs.lyrics = { id: "pending", status: "GENERATING", progress: null };
      renderWS();
      apiJson(API + "/lyrics", {
        action: S.lyrics.action, lyrics: S.lyrics.text,
        language: S.lyrics.language, target_language: S.lyrics.target,
        instruction: S.lyrics.instruction, project_id: S.lyrics.projectId || ""
      }).then(function (d) {
        delete S.jobs.lyrics;
        S.lyrics.result = d;
        if (d.lyrics) S.lyrics.text = d.lyrics;
        renderWS();
        toast("Lyrics ready", "ok");
      }).catch(function (e) {
        delete S.jobs.lyrics;
        S.lyrics.result = { error: e.message };
        renderWS();
        toast(e.message, "warn");
      });
    },

    /* Timeline */
    togglePlay: function () {
      if (S.player.playing) transportPause();
      else if (S.tracks.length) transportPlay();
      else if (S.player.url) playSingle(S.player.url);
      else toast("Nothing to play yet.", "warn");
    },
    stop: function () { transportStop(); },
    toggleLoop: function () { S.player.loop = !S.player.loop; renderWS(); },
    toggleSnap: function () { S.ui.snap = !S.ui.snap; renderWS(); },
    toggleMasterPreview: function (on) { S.master.preview = !!on; toast(on ? "Browser preview approximation enabled (not a provider master)" : "Preview off"); },
    setZoom: function (v) { S.ui.timelineZoom = parseFloat(v); renderWS(); },
    setSpeed: function (v) { S.player.speed = parseFloat(v) || 1; if (A.playing) { transportPause(); transportPlay(); } },
    setVolume: function (v) { S.player.volume = parseFloat(v); if (A.master) A.master.gain.value = S.player.volume; },
    seekFromEvent: function (e) {
      var rect = e.currentTarget.getBoundingClientRect();
      var pct = clamp((e.clientX - rect.left) / rect.width, 0, 1);
      if (S.tracks.length) transportSeek(pct);
    },
    seekFromTimeline: function (e) {
      var rect = e.currentTarget.getBoundingClientRect();
      var pct = clamp((e.clientX - rect.left) / rect.width, 0, 1);
      transportSeek(pct);
    },
    trackProp: function (id, prop, val) {
      var t = trackById(id); if (!t) return;
      if (t.lock && prop !== "lock") { toast("Track is locked.", "warn"); return; }
      t[prop] = val;
      if (prop === "mute" || prop === "solo" || prop === "vol" || prop === "pan") {
        if (A.playing) { transportPause(); transportPlay(); }
      }
      renderWS();
    },
    renameTrack: function (id) {
      var t = trackById(id); if (!t) return;
      var n = prompt("Rename track", t.name);
      if (n) { t.name = n; renderWS(); }
    },
    removeTrack: function (id) { removeTrack(id); },
    duplicateTrack: function (id) { duplicateTrack(id); },
    splitTrack: function (id) { splitTrackAtPlayhead(id); },
    selectClip: function (trackId, clipId, e) {
      e.stopPropagation();
      if (S.ui.snap) {
        // snapping: round clip start to a half-second grid
        var t = trackById(trackId);
        var c = (t.clips || []).filter(function (x) { return x.id === clipId; })[0];
        if (c) c.start = Math.round(c.start * 2) / 2;
      }
      S.ui.selectedClip = { trackId: trackId, clipId: clipId };
      renderWS();
    },
    addTrackPrompt: function () {
      var n = prompt("New track name", "Track " + (S.tracks.length + 1));
      if (n) { addTrack({ name: n, kind: "midi" }); renderWS(); }
    },
    pickTrackFile: function () {
      var inp = document.createElement("input");
      inp.type = "file"; inp.accept = "audio/*";
      inp.onchange = function () {
        var f = inp.files && inp.files[0]; if (!f) return;
        var url = URL.createObjectURL(f);
        addTrack({ name: f.name.replace(/\.[^.]+$/, ""), url: url, kind: "audio" });
        renderWS();
      };
      inp.click();
    },
    /* Player */
    prev: function () { transportSeek(0); },
    next: function () { },
    setExpProject: function (id) { S.exp.projectId = id; },
    searchLibrary: function (q) { S.library.query = q || ""; refreshLibrary().then(renderWS); },
    filterLibrary: function (c) { S.library.cat = c; refreshLibrary().then(renderWS); },
    openProject: function (id) {
      apiJson(API + "/projects/" + encodeURIComponent(id)).then(function (d) {
        loadIntoProject(d.project);
        S.view = "timeline";
        renderWS();
        toast("Project loaded", "ok");
      }).catch(function () { toast("Could not load project", "warn"); });
    },

    /* Master / Export */
    runMaster: function () {
      syncFormInputs();
      runJob("master", function () {
        return apiJson(API + "/master", {
          project_id: S.master.projectId || (S.project && S.project.id) || "",
          target: document.getElementById("vmwsMasterTarget") ? document.getElementById("vmwsMasterTarget").value : S.master.target,
          mix: currentMixSnapshot(),
          instruction: document.getElementById("vmwsMasterInstr") ? document.getElementById("vmwsMasterInstr").value : ""
        });
      }, function (job) { S.master.result = job.result; toast("Mastering recommendations ready", "ok"); });
    },
    prepareExport: function () {
      var body = { project_id: S.exp.projectId || (S.project && S.project.id) || "", format: S.exp.format };
      S.jobs.export = { id: "pending", status: "PROCESSING", progress: null };
      renderWS();
      apiJson(API + "/export", body).then(function (d) {
        delete S.jobs.export; S.exp.result = d; renderWS();
      }).catch(function (e) { delete S.jobs.export; toast(e.message, "warn"); renderWS(); });
    },
    downloadProjectPackage: function () {
      var pkg = { project: S.project || {}, exportedAt: new Date().toISOString(), format: "valleymind-music-project" };
      var blob = new Blob([JSON.stringify(pkg, null, 2)], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = ((S.project && S.project.name) || "valleymind-project") + ".vmproj.json";
      a.click();
    },

    /* Track Parts */
    setPartField: function (k, v) { S.parts[k] = v; renderWS(); },
    runParts: function () {
      syncFormInputs();
      var p = S.parts;
      if (p.track_type !== "other" && !p.detail.trim() && !(p.bpm || p.key || p.mood)) {
        if (!confirm("Generate a " + p.track_type + " part with these (minimal) details?")) return;
      }
      runJob("parts", function () {
        return apiJson(API + "/track/generate", {
          track_type: p.track_type, bpm: p.bpm || null, key: p.key,
          bars: Number(p.bars) || 8, mood: p.mood, detail: p.detail,
          project_id: p.projectId || (S.project && S.project.id) || ""
        });
      }, function (job) {
        S.parts.result = job.result;
        toast("Part plan ready", "ok");
      });
    },
    addPartToTimeline: function () {
      var r = S.parts.result || {}, pkg = r.package || {};
      var url = (r.audio && r.audio.url) || "";
      addTrack({
        name: (pkg.data && pkg.data.title) || (r.track_type || "Part"),
        url: url, sourceRel: (r.audio && r.audio.rel) || "", kind: "ai"
      });
      S.view = "timeline";
      renderWS();
      if (!url) toast("Part added as an arrangement track (render it server-side when a provider is configured).");
    },

    /* Audio Tools */
    refreshFileList: function () { refreshStoredFiles().then(renderWS); },
    setToolRel: function (rel) { S.tools.rel = rel; S.tools.result = null; renderWS(); },
    pickToolFile: function (el) {
      var f = el.files && el.files[0]; if (!f) return;
      S.tools.file = f; S.tools.fileName = f.name; renderWS();
    },
    setTool: function (id) { S.tools.selected = id; renderWS(); },
    runAudioTool: function () {
      var t = S.tools;
      var caps_tools = (S.caps && S.caps.audio_processor && S.caps.audio_processor.tools) || [];
      if (!t.rel && !t.file) { toast("Choose a stored file or upload audio.", "warn"); return; }
      var tool = caps_tools.filter(function (s) { return s.id === t.selected; })[0];
      if (!tool) { toast("Select a tool first.", "warn"); return; }
      var tools = [];
      var params = {};
      tool.params.forEach(function (pr) {
        var el = document.getElementById("vmwsTool_" + tool.id + "_" + pr.key);
        if (el) params[pr.key] = el.value;
      });
      tools.push({ id: tool.id, params: params });
      S.jobs.tools = { id: "pending", status: "PROCESSING", progress: null };
      renderWS();
      var req = t.rel
        ? apiJson(API + "/audio/tools", { rel: t.rel, tools: tools })
        : (function () {
            var fd = new FormData();
            fd.append("file", t.file);
            fd.append("tools", JSON.stringify(tools));
            return apiUpload(API + "/audio/tools", fd);
          })();
      req.then(function (d) {
        delete S.jobs.tools;
        S.tools.result = d;
        toast(d.file ? "Processed" : (d.error || "Processing failed"), d.file ? "ok" : "warn");
        renderWS();
      }).catch(function (e) {
        delete S.jobs.tools;
        toast(e.message || "Request failed", "warn");
        renderWS();
      });
    },
    addToolFileToTimeline: function () {
      var f = S.tools.result && S.tools.result.file; if (!f) return;
      addTrack({ name: f.name || "Processed", url: f.url, sourceRel: f.rel, kind: "audio" });
      S.view = "timeline";
      renderWS();
      toast("Processed audio added to the timeline", "ok");
    },

    /* Vocal clean (real processor) */
    cleanVocal: function () {
      var v = S.vocal;
      if (!v.file) { toast("Upload or record a vocal first.", "warn"); return; }
      var presetEl = document.getElementById("vmwsVocalPreset");
      var preset = presetEl ? presetEl.value : "shine";
      var fd = new FormData();
      fd.append("file", v.file);
      fd.append("preset", preset);
      S.jobs.vocal_clean = { id: "pending", status: "PROCESSING", progress: null };
      renderWS();
      apiUpload(API + "/vocal/clean", fd).then(function (d) {
        delete S.jobs.vocal_clean;
        if (d.vocal || d.file) {
          S.vocalCleanResult = {
            status: "success",
            file: d.vocal || d.file,
            chain: "noise gate/reduction → high-pass → low-pass → compressor → limiter → loudnorm"
          };
          toast("Vocal cleaned (real processor)", "ok");
        } else {
          S.vocalCleanResult = { status: "error", error: d.message || d.error || "Processing failed" };
          toast(S.vocalCleanResult.error, "warn");
        }
        renderWS();
      }).catch(function (e) {
        delete S.jobs.vocal_clean;
        S.vocalCleanResult = { status: "error", error: e.message };
        renderWS();
      });
    },
    addCleanVocalToTimeline: function () {
      var f = S.vocalCleanResult && S.vocalCleanResult.file; if (!f) return;
      addTrack({ name: (f.name || "Clean vocal").replace(/^vocal_clean_/, ""), url: f.url, sourceRel: f.rel, kind: "audio" });
      S.view = "timeline";
      renderWS();
      toast("Cleaned vocal added to the timeline", "ok");
    },

    /* Quick DSP stems (real processor) */
    runStemsQuick: function () {
      var st = S.stems;
      if (!st.file) { toast("Upload a song first.", "warn"); return; }
      var fd = new FormData();
      fd.append("file", st.file);
      S.jobs.stems = { id: "pending", status: "PROCESSING", progress: null };
      renderWS();
      apiUpload(API + "/stems/quick", fd).then(function (d) {
        delete S.jobs.stems;
        S.stemsQuickResult = d;
        toast(d.stems ? "Quick split ready" : (d.error || "Split failed"), d.stems ? "ok" : "warn");
        renderWS();
      }).catch(function (e) {
        delete S.jobs.stems;
        S.stemsQuickResult = { status: "error", error: e.message };
        renderWS();
      });
    },
    stemsQuickToTimeline: function () {
      ((S.stemsQuickResult && S.stemsQuickResult.stems) || []).forEach(function (s) {
        addTrack({ name: s.name || "Stem", url: s.url, sourceRel: s.rel, kind: "stem" });
      });
      S.view = "timeline";
      renderWS();
      toast("Quick parts added to the timeline", "ok");
    },

    /* Loudness (real processor) */
    measureLoudness: function () {
      var relEl = document.getElementById("vmwsLoudnessRel");
      var rel = relEl ? relEl.value : "";
      if (!rel) { toast("Choose a stored file first.", "warn"); return; }
      S.jobs.loudness = { id: "pending", status: "PROCESSING", progress: null };
      renderWS();
      apiJson(API + "/loudness", { rel: rel }).then(function (d) {
        delete S.jobs.loudness;
        if (d.loudness) {
          S.masterAnalysis = Object.assign({ status: "success", rel: rel }, d.loudness);
          toast("Loudness measured", "ok");
        } else {
          S.masterAnalysis = { status: "error", error: d.message || d.error || "Measurement failed" };
          toast(S.masterAnalysis.error, "warn");
        }
        renderWS();
      }).catch(function (e) {
        delete S.jobs.loudness;
        S.masterAnalysis = { status: "error", error: e.message };
        renderWS();
      });
    },

    /* Real conversion through /api/music/export (rel + format) */
    convertExport: function () {
      var relEl = document.getElementById("vmwsConvertRel");
      var fmtEl = document.getElementById("vmwsConvertFormat");
      var rel = relEl ? relEl.value : "";
      var fmt = fmtEl ? fmtEl.value : "wav";
      if (!rel) { toast("Choose a stored file first.", "warn"); return; }
      S.jobs.convert = { id: "pending", status: "PROCESSING", progress: null };
      renderWS();
      apiJson(API + "/export", { rel: rel, format: fmt }).then(function (d) {
        delete S.jobs.convert;
        if (d.converted && d.converted.status === "success") {
          S.expConverted = { status: "success", file: d.converted.file, format: fmt };
          toast("Converted to " + fmt.toUpperCase(), "ok");
        } else {
          S.expConverted = { status: "error", error: ((d.converted || {}).error) || d.message || "Conversion failed" };
          toast(S.expConverted.error, "warn");
        }
        refreshStoredFiles();
        renderWS();
      }).catch(function (e) {
        delete S.jobs.convert;
        S.expConverted = { status: "error", error: e.message };
        renderWS();
      });
    },

    /* Elena */
    toggleCopilot: function () { S.copilot.open = !S.copilot.open; renderWS(); },
    askElena: function (msg) { S.copilot.open = true; S.copilot.input = msg; renderWS(); VMWs.copilotSend(); },
    copilotSend: function () {
      var el = document.getElementById("vmwsCopilotInput");
      var msg = (el ? el.value : S.copilot.input || "").trim();
      if (!msg || S.copilot.busy) return;
      S.copilot.input = "";
      S.copilot.messages.push({ role: "me", text: msg });
      S.copilot.busy = true;
      renderWS();
      scrollCopilot();
      apiJson(API + "/copilot", {
        message: msg,
        context: S.project ? { title: S.project.name, genre: S.project.genre, mood: S.project.mood, bpm: S.project.bpm, key: S.project.key, structure: S.project.structure } : {},
        history: S.copilot.messages.slice(-6).map(function (m) { return { role: m.role === "me" ? "user" : "assistant", content: m.text }; })
      }).then(function (d) {
        S.copilot.messages.push({ role: "ai", text: d.reply || "…", limitations: d.limitations || [] });
        S.copilot.busy = false;
        renderWS(); scrollCopilot();
        VMWs.dispatchOperation(d.operation, d.params || {});
      }).catch(function (e) {
        S.copilot.messages.push({ role: "ai", text: "I couldn't reach the producer: " + e.message });
        S.copilot.busy = false; renderWS(); scrollCopilot();
      });
    },
    dispatchOperation: function (op, params) {
      if (!op) return;
      var intent = op.intent;
      if (intent === "create" || intent === "lyrics") {
        S.view = "create";
        S.create.brief = params.brief || params.instruction || S.create.brief;
        if (params.genre) S.create.genre = params.genre;
        if (params.mood) S.create.mood = params.mood;
        renderWS();
        if (intent === "lyrics") { VMWs.runLyrics("generate"); }
        else { VMWs.runCreate(); }
      } else if (intent === "remix") {
        S.view = "remix"; S.remix.instruction = params.instruction || "";
        renderWS();
      } else if (intent === "extend") {
        S.view = "extend"; S.extend.instruction = params.instruction || "";
        if (params.target_seconds) S.extend.seconds = params.target_seconds;
        renderWS();
      } else if (intent === "master") { S.view = "master"; renderWS(); VMWs.runMaster(); }
      else if (intent === "stems") { S.view = "stems"; renderWS(); }
      else if (intent === "vocal_lab" || intent === "sing") { S.view = "vocal"; renderWS(); }
      else if (intent === "reference") { S.view = "reference"; renderWS(); }
      else if (intent === "parts" || intent === "track") { S.view = "parts"; if (params.track_type) S.parts.track_type = params.track_type; renderWS(); }
      else if (intent === "tools" || intent === "process") { S.view = "tools"; if (params.tool) S.tools.selected = params.tool; renderWS(); }
      else if (intent === "loudness" || intent === "measure") { S.view = "master"; renderWS(); }
      else if (intent === "edit") {
        if (!S.project) { toast("Open a project so Elena can edit it."); return; }
        toast("Edit operation queued (browser mix)", "ok");
      }
    }
  };

  function scrollCopilot() {
    var m = document.getElementById("vmwsCopilotMsg");
    if (m) m.scrollTop = m.scrollHeight;
  }
  function getTrackProp(id, prop) { var t = trackById(id); return t ? !!t[prop] : false; }
  function currentMixSnapshot() {
    return {
      master: S.player.volume,
      tracks: S.tracks.reduce(function (acc, t) {
        acc[t.name] = { vol: t.vol, pan: t.pan, mute: t.mute, solo: t.solo };
        return acc;
      }, {})
    };
  }

  function setPlayer(o) {
    Object.assign(S.player, o || {});
    if (S.player.url) { S.player.artist = S.player.artist || "ValleyMind Music"; }
    renderWS();
  }
  function playSingle(url) {
    transportPause();
    var audio = els.preview;
    if (!audio) {
      audio = document.createElement("audio");
      audio.id = "vmwsPreview";
      audio.style.display = "none";
      document.body.appendChild(audio);
      els.preview = audio;
    }
    audio.src = url;
    audio.volume = S.player.volume;
    audio.play().then(function () {
      S.player.playing = true; S.player.pos = 0;
      S.player.dur = audio.duration || 0;
      renderWS();
      audio.ontimeupdate = function () {
        S.player.pos = audio.currentTime; S.player.dur = audio.duration || 0;
        updatePlayheadUI();
      };
      audio.onended = function () { S.player.playing = false; renderWS(); };
    }).catch(function () { toast("Could not play audio", "warn"); });
    // stop preview when multitrack transport starts
    audio.pause = (function (orig) { return function () { S.player.playing = false; return orig.apply(this, arguments); }; })(audio.pause);
  }

  /* ───────────────────────── recording ───────────────────────── */
  var rec = { recorder: null, chunks: [], stream: null, timer: null, started: 0 };
  function toggleRecord() {
    if (rec.recorder && rec.recorder.state === "recording") { rec.recorder.stop(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast("Recording is not supported in this browser.", "warn"); return;
    }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      rec.stream = stream; rec.chunks = [];
      var mr = new (window.MediaRecorder || window.webkitMediaRecorder)(stream);
      rec.recorder = mr;
      mr.ondataavailable = function (e) { if (e.data && e.data.size) rec.chunks.push(e.data); };
      mr.onstop = function () {
        clearInterval(rec.timer); rec.timer = null;
        S.ui.recording = false; S.ui.recordingMs = 0;
        var blob = new Blob(rec.chunks, { type: mr.mimeType || "audio/webm" });
        var file = new File([blob], "recording.webm", { type: blob.type });
        S.vocal.file = file; S.vocal.fileName = file.name;
        analyzeFile(file).then(function (a) { S.vocal.analysis = a; renderWS(); });
        try { rec.stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
        renderWS();
      };
      mr.start();
      S.ui.recording = true; rec.started = Date.now();
      rec.timer = setInterval(function () {
        S.ui.recordingMs = Date.now() - rec.started;
        var el = document.getElementById("vmwsRecTime");
        if (el) el.textContent = fmtTime(S.ui.recordingMs / 1000);
      }, 400);
      renderWS();
    }).catch(function () { toast("Microphone permission denied.", "warn"); });
  }

  /* ───────────────────────── boot ───────────────────────── */
  function boot() {
    S = defaultState();
    injectStyles();

    var restore = document.createElement("button");
    restore.className = "vmws-btn primary vmws-restore";
    restore.textContent = "Open Music Workstation";
    restore.onclick = function () { VMWs.openWorkstation(); };
    document.body.appendChild(restore);
    els.restore = restore;

    origOnShow = window.vmMusicOnShow;
    window.vmMusicOnShow = VMWs.onShow;

    // Load capabilities + library (best effort; auth may not be ready yet).
    apiJson(API + "/capabilities").then(function (d) {
      S.caps = d;
      if (S.view === "create" && (d.genres || []).length) { /* genres available */ }
      renderWS();
    }).catch(function () { /* provider/status unavailable; honest banner stays */ });
    refreshLibrary().then(function () { renderWS(); }).catch(function () { });

    // Re-render if the panel was already mounted.
    if (document.getElementById("vmWsPanelMusic")) renderWS();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
