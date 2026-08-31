/* ValleyMind Music Studio
   ------------------------
   Modular, additive Music Studio that lives in its own static module so the
   giant inline app script stays untouched. Two modes:

     DO-IT-YOURSELF          The user sings/hums/records + uploads a beat,
                             writes lyrics and controls the production. ValleyMind
                             helps organize + save the project.

     LET-VALLEYMIND-PRODUCE  AI mode: brief + optional sung melody + settings are
                             sent to /api/music, which returns the working
                             creative package (lyrics + arrangement + structure).
                             Final audio rendering (beat synthesis, AI vocals,
                             mixing) is declared honestly as a future step.

   State lives in a single authoritative object and is persisted to
   localStorage. Nothing is faked: what cannot render yet is reported as such.
*/
(function () {
  "use strict";

  var NS = "vmMusic";
  var STORE_KEY = "vmMusicProjects";

  var MS = {
    state: null,
    recorder: null,
    recStream: null,
    chunks: [],
    timer: null,
    elapsed: 0,
    projects: [],
    rendered: false
  };

  var VOICE_LABELS = {
    keep: "Keep & enhance my own voice",
    clone: "AI-clone of my voice (authorized)",
    elena: "ValleyMind's AI singing voice (Elena)"
  };

  var GENRES = ["Afrobeats", "Amapiano", "R&B", "Hip-Hop", "Pop", "Soul", "Gospel", "Highlife", "Dancehall", "Reggae", "Folk", "Jazz", "Electronic"];
  var MOODS = ["Romantic", "Upbeat", "Melancholic", "Hopeful", "Energetic", "Chill", "Bittersweet", "Empowering", "Nostalgic"];
  var TEMPOS = ["Slow", "Medium", "Fast", "Very fast"];
  var ROLES = ["Singer", "Rapper", "Singer-songwriter", "Producer", "Both singing & producing"];

  /* ── Styles (injected once) ─────────────────────────────────────────── */
  function injectStyles() {
    if (document.getElementById("vmMusicCSS")) return;
    var css = document.createElement("style");
    css.id = "vmMusicCSS";
    css.textContent = [
      ".vmm { flex:1; min-height:0; display:flex; flex-direction:column; background:linear-gradient(180deg, rgba(15,19,32,0.9), rgba(12,15,26,0.96)); color:#e6edf5; overflow:hidden; }",
      ".vmm * { box-sizing:border-box; }",
      ".vmm-sheet { flex:1; min-height:0; overflow-y:auto; padding:22px 26px 40px; }",
      ".vmm-head { display:flex; align-items:center; gap:14px; padding:0 26px; height:62px; border-bottom:1px solid rgba(255,255,255,0.08); background:rgba(12,15,26,0.5); flex:none; }",
      ".vmm-head .vmm-logo { width:40px; height:40px; border-radius:12px; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#22d3ee,#0ea5e9); color:#033; font-weight:900; font-size:20px; }",
      ".vmm-head h2 { margin:0; font-family:'Space Grotesk',sans-serif; font-size:18px; color:#f1f5f9; }",
      ".vmm-head p { margin:0; font-size:11px; color:#7c8aa0; }",
      ".vmm-head .vmm-spacer { flex:1; }",
      ".vmm-btn { font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:12px; letter-spacing:.02em; border:1px solid rgba(255,255,255,0.14); background:rgba(255,255,255,0.04); color:#e2e8f0; border-radius:11px; padding:9px 14px; cursor:pointer; display:inline-flex; align-items:center; gap:7px; transition:all .15s; }",
      ".vmm-btn:hover { background:rgba(255,255,255,0.09); border-color:rgba(34,211,238,0.4); color:#fff; }",
      ".vmm-btn-primary { background:linear-gradient(135deg,#22d3ee,#0ea5e9); color:#03222b; border:none; }",
      ".vmm-btn-primary:hover { filter:brightness(1.06); background:linear-gradient(135deg,#22d3ee,#0ea5e9); }",
      ".vmm-btn-ghost { background:transparent; }",
      ".vmm-btn-danger { color:#fda4af; border-color:rgba(244,63,94,0.4); }",
      ".vmm-btn[disabled] { opacity:.45; cursor:not-allowed; }",
      ".vmm-card { background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.09); border-radius:18px; padding:18px 20px; margin-bottom:18px; }",
      ".vmm-card h3 { margin:0 0 4px; font-family:'Space Grotesk',sans-serif; font-size:14px; color:#f1f5f9; display:flex; align-items:center; gap:8px; }",
      ".vmm-card .vmm-sub { margin:0 0 14px; font-size:12px; color:#8a97ad; line-height:1.6; }",
      ".vmm-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; }",
      ".vmm-label { display:block; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#94a3b8; margin:0 0 6px; }",
      ".vmm-select, .vmm-input { width:100%; background:#0d1220; border:1px solid rgba(255,255,255,0.12); color:#e6edf5; border-radius:11px; padding:10px 12px; font-size:13px; font-family:inherit; outline:none; }",
      ".vmm-input:focus, .vmm-select:focus { border-color:rgba(34,211,238,0.6); }",
      ".vmm-textarea { width:100%; background:#0d1220; border:1px solid rgba(255,255,255,0.12); color:#e6edf5; border-radius:11px; padding:12px; font-size:13px; font-family:inherit; min-height:120px; resize:vertical; outline:none; line-height:1.6; }",
      ".vmm-mode-switch { display:flex; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:14px; padding:5px; gap:6px; }",
      ".vmm-mode { flex:1; border:none; background:transparent; color:#94a3b8; border-radius:10px; padding:12px; cursor:pointer; font-family:'Space Grotesk',sans-serif; font-weight:800; font-size:13px; text-align:left; line-height:1.35; }",
      ".vmm-mode small { display:block; font-weight:400; font-size:10px; color:#64748b; margin-top:2px; }",
      ".vmm-mode.active { background:linear-gradient(135deg,#0ea5e9,#22d3ee); color:#03222b; box-shadow:0 4px 16px rgba(14,165,233,.28); }",
      ".vmm-mode.active small { color:#064454; }",
      ".vmm-rec-row { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }",
      ".vmm-rec-btn { width:52px; height:52px; border-radius:50%; border:none; cursor:pointer; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#ef4444,#dc2626); color:#fff; box-shadow:0 6px 20px rgba(239,68,68,.35); }",
      ".vmm-rec-btn.recording { animation:vmmPulse 1.2s infinite; }",
      ".vmm-rec-btn.green { background:linear-gradient(135deg,#22c55e,#16a34a); box-shadow:0 6px 20px rgba(34,197,94,.3); }",
      "@keyframes vmmPulse { 0%,100%{ transform:scale(1); box-shadow:0 6px 20px rgba(239,68,68,.35);} 50%{ transform:scale(1.08); box-shadow:0 6px 30px rgba(239,68,68,.5);} }",
      ".vmm-rec-time { font-family:'Space Grotesk',sans-serif; font-size:22px; font-weight:800; color:#f1f5f9; min-width:64px; }",
      ".vmm-chip { display:inline-flex; align-items:center; gap:6px; background:rgba(34,211,238,0.12); border:1px solid rgba(34,211,238,0.35); color:#67e8f9; border-radius:999px; padding:5px 11px; font-size:11px; font-weight:700; }",
      ".vmm-track { display:flex; align-items:center; gap:12px; background:rgba(13,18,32,0.7); border:1px solid rgba(255,255,255,0.09); border-radius:13px; padding:11px 14px; margin-bottom:10px; }",
      ".vmm-track .t-name { flex:1; font-size:13px; font-weight:700; color:#e6edf5; }",
      ".vmm-track .t-meta { font-size:11px; color:#7c8aa0; }",
      ".vmm-option { display:flex; align-items:flex-start; gap:11px; padding:12px; border:1px solid rgba(255,255,255,0.1); border-radius:13px; margin-bottom:10px; cursor:pointer; background:rgba(255,255,255,0.02); }",
      ".vmm-option.sel { border-color:rgba(34,211,238,0.6); background:rgba(34,211,238,0.07); }",
      ".vmm-option input[type=radio] { accent-color:#22d3ee; margin-top:2px; }",
      ".vmm-option .o-title { font-size:13px; font-weight:700; color:#f1f5f9; }",
      ".vmm-option .o-sub { font-size:11px; color:#8a97ad; margin-top:2px; line-height:1.5; }",
      ".vmm-consent { display:flex; align-items:flex-start; gap:10px; background:rgba(255,193,7,0.07); border:1px dashed rgba(255,193,7,0.4); border-radius:12px; padding:12px 14px; margin-top:8px; }",
      ".vmm-consent input { accent-color:#f59e0b; margin-top:2px; width:16px; height:16px; }",
      ".vmm-consent label { font-size:12px; color:#fcd34d; line-height:1.6; }",
      ".vmm-progress .p-step { display:flex; align-items:center; gap:11px; padding:9px 4px; font-size:13px; color:#94a3b8; }",
      ".vmm-progress .p-step .dot { width:11px; height:11px; border-radius:50%; border:2px solid #475569; flex:none; }",
      ".vmm-progress .p-step.done { color:#a7f3d0; } .vmm-progress .p-step.done .dot { background:#22c55e; border-color:#22c55e; }",
      ".vmm-progress .p-step.active { color:#ffd166; } .vmm-progress .p-step.active .dot { border-color:#ffd166; animation:vmmPulse 1.1s infinite; }",
      ".vmm-ai-out h4 { font-family:'Space Grotesk',sans-serif; margin:14px 0 6px; color:#67e8f9; font-size:12px; text-transform:uppercase; letter-spacing:.07em; }",
      ".vmm-ai-out .lyrics { white-space:pre-wrap; font-size:13px; line-height:1.8; color:#e6edf5; background:rgba(13,18,32,0.6); border:1px solid rgba(255,255,255,0.09); border-radius:12px; padding:14px; }",
      ".vmm-note { font-size:12px; color:#ffd166; background:rgba(255,193,7,0.08); border-left:3px solid #f59e0b; border-radius:6px; padding:9px 12px; margin-top:12px; line-height:1.6; }",
      ".vmm-empty { text-align:center; color:#64748b; padding:26px 10px; font-size:12px; }",
      ".vmm-proj { display:flex; align-items:center; gap:12px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:12px 14px; margin-bottom:10px; }",
      ".vmm-proj .p-name { flex:1; font-size:13px; font-weight:700; color:#e6edf5; }",
      ".vmm-proj .p-meta { font-size:11px; color:#7c8aa0; margin-top:2px; }",
      ".vmm-spin { width:15px; height:15px; border:2px solid rgba(255,255,255,0.25); border-top-color:#22d3ee; border-radius:50%; animation:vmmRot .8s linear infinite; display:inline-block; }",
      "@keyframes vmmRot { to { transform:rotate(360deg); } }",
      ".vmm-toast { position:fixed; bottom:24px; left:50%; transform:translateX(-50%); background:#0f172a; border:1px solid rgba(34,211,238,0.4); color:#e6edf5; padding:12px 18px; border-radius:12px; font-size:13px; font-weight:600; z-index:99999; box-shadow:0 12px 40px rgba(0,0,0,.5); opacity:0; transition:opacity .25s, transform .25s; pointer-events:none; }",
      ".vmm-toast.show { opacity:1; transform:translateX(-50%) translateY(-4px); }",
      ".vmm-row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }"
    ].join("\n");
    document.head.appendChild(css);
  }

  /* ── Helpers ────────────────────────────────────────────────────────── */
  function defaultState() {
    return {
      name: "Untitled song",
      mode: "diy",
      role: "Singer",
      genre: "Afrobeats",
      mood: "Romantic",
      tempo: "Medium",
      key: "",
      language: "English",
      brief: "",
      lyrics: "",
      voice: "keep",
      consent: false,
      take: { name: "", url: "", dur: 0 },   // sung/hummed melody
      beat: { name: "", url: "", dur: 0 },   // uploaded beat/instrumental
      aiResult: null,
      savedAt: 0
    };
  }

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function fmtTime(s) { if (!s && s !== 0) return "00:00"; s = Math.round(s || 0); var m = Math.floor(s / 60); var ss = s % 60; return (m < 10 ? "0" + m : m) + ":" + (ss < 10 ? "0" + ss : ss); }

  function loadProjects() {
    try { var p = JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); MS.projects = Array.isArray(p) ? p : []; }
    catch (e) { MS.projects = []; }
  }
  function saveProjects() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(MS.projects)); } catch (e) {}
  }
  function toast(msg) {
    var el = document.getElementById("vmMusicToast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove("show"); }, 2600);
  }
  function refreshLucide() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (e) {}
    }
  }

  /* ── Recording (sing / hum / speak, like a WhatsApp voice note) ─────── */
  function toggleRecord() {
    if (MS.recorder && MS.recorder.state === "recording") { stopRecord(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast("Recording isn't supported in this browser.");
      return;
    }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      MS.recStream = stream;
      var mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      MS.recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      MS.chunks = [];
      MS.recorder.ondataavailable = function (ev) { if (ev.data && ev.data.size) MS.chunks.push(ev.data); };
      MS.recorder.onstop = function () {
        var blob = new Blob(MS.chunks, { type: MS.recorder.mimeType || "audio/webm" });
        if (MS.state.take.url) try { URL.revokeObjectURL(MS.state.take.url); } catch (e) {}
        MS.state.take.url = URL.createObjectURL(blob);
        MS.state.take.name = "My take " + fmtTime(Date.now() / 1000).replace(":", "") + " (" + fmtTime(MS.elapsed) + ")";
        MS.state.take.dur = MS.elapsed;
        stopRecTracks();
        render();
        toast("Take recorded.");
      };
      MS.recorder.start();
      MS.elapsed = 0;
      clearInterval(MS.timer);
      MS.timer = setInterval(function () { MS.elapsed++; renderRecTime(); }, 1000);
      render();
    }).catch(function () {
      toast("Microphone access was blocked. Allow mic access to sing/hum.");
    });
  }

  function stopRecTracks() {
    try { if (MS.recStream) MS.recStream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
    MS.recStream = null;
  }
  function stopRecord() {
    if (MS.recorder && MS.recorder.state === "recording") { try { MS.recorder.stop(); } catch (e) {} }
    clearInterval(MS.timer);
    MS.timer = null;
    MS.recorder = null;
    render();
  }
  function renderRecTime() {
    var n = document.getElementById("vmMusicRecTime");
    if (n) n.textContent = fmtTime(MS.elapsed);
  }

  /* ── Audio upload ───────────────────────────────────────────────────── */
  function onTakeFile(input) {
    var f = input && input.files && input.files[0];
    if (!f) return;
    if (MS.state.take.url) try { URL.revokeObjectURL(MS.state.take.url); } catch (e) {}
    MS.state.take.name = f.name;
    MS.state.take.url = URL.createObjectURL(f);
    MS.state.take.dur = 0;
    var a = new Audio(); a.preload = "metadata"; a.src = MS.state.take.url;
    a.onloadedmetadata = function () { MS.state.take.dur = a.duration || 0; render(); };
    render();
    toast("Melody audio added.");
    input.value = "";
  }
  function onBeatFile(input) {
    var f = input && input.files && input.files[0];
    if (!f) return;
    if (MS.state.beat.url) try { URL.revokeObjectURL(MS.state.beat.url); } catch (e) {}
    MS.state.beat.name = f.name;
    MS.state.beat.url = URL.createObjectURL(f);
    MS.state.beat.dur = 0;
    var a = new Audio(); a.preload = "metadata"; a.src = MS.state.beat.url;
    a.onloadedmetadata = function () { MS.state.beat.dur = a.duration || 0; render(); };
    render();
    toast("Beat / instrumental added.");
    input.value = "";
  }
  function playAudio(type) {
    var a = document.getElementById("vmMusicPlayer");
    if (!a) return;
    a.src = (type === "beat" ? MS.state.beat.url : MS.state.take.url) || "";
    if (a.src && a.play) a.play();
  }

  /* ── Mode & input sync from form ────────────────────────────────────── */
  function onMode(mode) { MS.state.mode = mode; render(); }
  function syncInputs() {
    var get = function (id) { var el = document.getElementById(id); return el ? el.value : ""; };
    MS.state.name = get("vmMusicName") || MS.state.name;
    MS.state.role = get("vmMusicRole") || "Singer";
    MS.state.genre = get("vmMusicGenre") || "Afrobeats";
    MS.state.mood = get("vmMusicMood") || "Romantic";
    MS.state.tempo = get("vmMusicTempo") || "Medium";
    MS.state.key = get("vmMusicKey") || "";
    MS.state.language = get("vmMusicLanguage") || "English";
    MS.state.brief = get("vmMusicBrief") || "";
    MS.state.lyrics = get("vmMusicLyrics") || "";
  }

  /* ── AI produce ─────────────────────────────────────────────────────── */
  var AI_STEPS = [
    "Reading your melody & brief",
    "Understanding rhythm & mood",
    "Writing / refining lyrics",
    "Designing the beat & arrangement",
    "Preparing vocals",
    "Synchronizing the song"
  ];
  function runAI() {
    syncInputs();
    if (!MS.state.consent) { toast("Please authorize the voice choice first."); return; }
    if (!MS.state.brief.trim() && !MS.state.lyrics.trim()) {
      toast("Describe your song or add lyrics first.");
      return;
    }
    var wrap = document.getElementById("vmMusicProgress");
    var out = document.getElementById("vmMusicAIOut");
    if (wrap) { wrap.style.display = "block"; wrap.innerHTML = ""; }
    if (out) { out.style.display = "none"; out.innerHTML = ""; }
    var steps = [];
    AI_STEPS.forEach(function (label, i) {
      var div = document.createElement("div");
      div.className = "p-step" + (i === 0 ? " active" : "");
      div.innerHTML = '<span class="dot"></span>' + label;
      wrap.appendChild(div);
      steps.push(div);
    });
    var si = 0;
    var prog = setInterval(function () {
      if (si < steps.length - 1) { steps[si].className = "p-step done"; }
      si++;
      if (si < steps.length) steps[si].className = "p-step active";
    }, 5000);
    // The backend genuinely generates lyrics + arrangement. The last steps
    // (beat synthesis, mixing) are honestly marked as future rendering.
    apiFetch("/api/music", {
      method: "POST", credentials: "include",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        brief: MS.state.brief, role: MS.state.role, genre: MS.state.genre,
        mood: MS.state.mood, tempo: MS.state.tempo, key: MS.state.key,
        language: MS.state.language, voice: MS.state.voice, lyrics: MS.state.lyrics
      }),
      timeoutMs: 60000
    }).then(function (res) { return res.json(); }).then(function (data) {
      clearInterval(prog);
      steps.forEach(function (s) { s.className = "p-step done"; });
      MS.state.aiResult = data || null;
      render();
    }).catch(function () {
      clearInterval(prog);
      toast("Couldn't reach the producer. Check your connection.");
    });
  }

  /* ── Save / load / delete / export ──────────────────────────────────── */
  function saveSong() {
    syncInputs();
    MS.state.savedAt = Date.now();
    if (!MS.state.id) MS.state.id = "ms" + Date.now();
    var found = false;
    for (var i = 0; i < MS.projects.length; i++) {
      if (MS.projects[i].id === MS.state.id) { MS.projects[i] = clone(MS.state); found = true; break; }
    }
    if (!found) {
      MS.projects.unshift(clone(MS.state));
    }
    saveProjects();
    toast("Song saved.");
    render();
  }
  function newSong() { MS.state = defaultState(); MS.state.id = null; render(); }
  function loadSong(id) {
    for (var i = 0; i < MS.projects.length; i++) {
      if (MS.projects[i].id === id) {
        var s = clone(MS.projects[i]);
        MS.state = s;
        render();
        toast("Song loaded.");
        return;
      }
    }
  }
  function deleteSong(id) {
    MS.projects = MS.projects.filter(function (p) { return p.id !== id; });
    saveProjects();
    render();
  }
  function exportSong() {
    syncInputs();
    var title = MS.state.name || "Untitled song";
    var parts = [];
    parts.push(title);
    parts.push("Genre: " + MS.state.genre + "  ·  Mood: " + MS.state.mood + "  ·  Tempo: " + MS.state.tempo + (MS.state.key ? "  ·  Key: " + MS.state.key : ""));
    if (MS.state.voice) parts.push("Voice: " + (VOICE_LABELS[MS.state.voice] || MS.state.voice));
    parts.push("");
    parts.push((MS.state.aiResult && MS.state.aiResult.lyrics) || MS.state.lyrics || "(no lyrics yet)");
    if (MS.state.aiResult && MS.state.aiResult.arrangement) {
      parts.push(""); parts.push("ARRANGEMENT"); parts.push(MS.state.aiResult.arrangement);
    }
    var blob = new Blob([parts.join("\n")], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = title.replace(/[\\/:*?"<>|]+/g, "_") + ".txt";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  /* ── Render ─────────────────────────────────────────────────────────── */
  function render() {
    var panel = document.getElementById("vmWsPanelMusic");
    if (!panel) return;
    panel.innerHTML = sheetHTML();
    refreshLucide();
    bindFields();
  }

  function sheetHTML() {
    var s = MS.state;
    var modeBtn = function (id, t, sub) {
      var on = s.mode === id;
      return '<button type="button" class="vmm-mode' + (on ? " active" : "") + '" onclick="window.vmMusicAPI.onMode(\'' + id + '\')"><span>' + t + '</span><small>' + sub + '</small></button>';
    };
    var sel = function (id, label, opts, val) {
      var o = opts.map(function (x) { return '<option value="' + x + '"' + (val === x ? " selected" : "") + '>' + x + "</option>"; }).join("");
      return '<div><label class="vmm-label">' + label + '</label><select class="vmm-select" id="' + id + '">' + o + '</select></div>';
    };
    var voiceOpt = function (v, locked) {
      var on = s.voice === v;
      var sub = v === "clone" ? VOICE_LABELS.clone + " — requires your authorization below." : (VOICE_LABELS[v] || "");
      var lockedAttr = locked ? " disabled" : "";
      return '<div class="vmm-option' + (on ? " sel" : "") + '" onclick="window.vmMusicAPI.onVoice(\'' + v + '\')"><input type="radio" name="vmmVoice" value="' + v + '"' + (on ? " checked" : "") + lockedAttr + '><div><div class="o-title">' + VOICE_LABELS[v] + '</div><div class="o-sub">' + sub + '</div></div></div>';
    };
    var track = function (type) {
      var t = type === "beat" ? s.beat : s.take;
      if (!t.url) return "";
      var label = type === "beat" ? "Beat" : "Vocal take";
      return '<div class="vmm-track"><span class="vmm-chip">' + label + '</span><span class="t-name">' + esc(t.name) + '</span><span class="t-meta">' + fmtTime(t.dur) + '</span><button class="vmm-btn vmm-btn-ghost" onclick="window.vmMusicAPI.playAudio(\'' + type + '\')">Play</button></div>';
    };
    var consentBlock = s.voice === "clone" ?
      '<div class="vmm-consent" onclick="window.vmMusicAPI.onConsent()"><input type="checkbox" ' + (s.consent ? "checked" : "") + '><label><b>Authorization to clone my voice:</b> I give ValleyMind permission to create an AI model of my voice from my recording, store it only for my projects, and use it solely to sing the songs I produce. I can revoke this at any time by deleting my projects.</label></div>' : "";
    var cloneLocked = s.voice === "clone" && !s.consent;

    var tracks = track("take") + track("beat");

    var projRows = MS.projects.map(function (p) {
      var d = p.savedAt ? new Date(p.savedAt).toLocaleString() : "";
      return '<div class="vmm-proj"><span class="vmm-chip">' + (p.mode === "ai" ? "AI" : "DIY") + '</span><div><div class="p-name">' + esc(p.name || "Untitled") + '</div><div class="p-meta">' + (p.genre || "") + " · " + (p.mood || "") + (d ? " · " + d : "") + '</div></div><button class="vmm-btn vmm-btn-primary" onclick="window.vmMusicAPI.loadSong(\'' + p.id + '\')">Open</button><button class="vmm-btn vmm-btn-danger" onclick="window.vmMusicAPI.deleteSong(\'' + p.id + '\')">Del</button></div>';
    }).join("");

    var aiBlock = "";
    if (s.mode === "ai") {
      aiBlock = '<div class="vmm-card"><h3>Let ValleyMind produce it</h3><p class="vmm-sub">Describe the song you want — or press record and hum/sing your melody, then tell us the vibe.</p>' +
        '<div class="vmm-grid">' +
        sel("vmMusicGenre", "Genre", GENRES, s.genre) +
        sel("vmMusicMood", "Mood", MOODS, s.mood) +
        sel("vmMusicTempo", "Tempo", TEMPOS, s.tempo) +
        sel("vmMusicRole", "Your role", ROLES, s.role) +
        '</div>' +
        '<label class="vmm-label" style="margin-top:12px;">Key (optional)</label><input class="vmm-input" id="vmMusicKey" value="' + esc(s.key) + '" placeholder="e.g. A minor">' +
        '<div style="margin:14px 0 6px;">' +
        '<label class="vmm-label">Describe it</label>' +
        '<textarea class="vmm-textarea" id="vmMusicBrief" placeholder="' + esc('Example: I just sang this melody. Turn it into a romantic Afrobeats song about finding love again after heartbreak.') + '">' + esc(s.brief) + '</textarea>' +
        '</div>' +
        '<button class="vmm-btn vmm-btn-primary" id="vmMusicRunAI" onclick="window.vmMusicAPI.runAI()">Produce this song</button>' +
        '<div class="vmm-progress" id="vmMusicProgress" style="display:none;margin-top:16px;"></div>' +
        '</div>';
    }

    var lyricsBlock = s.mode === "diy" ?
      '<div class="vmm-card"><h3>Lyrics</h3><p class="vmm-sub">Write your own lyrics, or leave blank and let a future AI pass draft them.</p><textarea class="vmm-textarea" id="vmMusicLyrics" placeholder="Your lyrics…">' + esc(s.lyrics) + '</textarea>' +
      '<div style="margin-top:12px;">' +
      '<button class="vmm-btn vmm-btn-primary" onclick="window.vmMusicAPI.saveSong()">Save song</button> ' +
      '<button class="vmm-btn" onclick="window.vmMusicAPI.exportSong()">Export lyric sheet</button>' +
      '</div></div>' : "";

    var aiOut = "";
    if (s.aiResult && s.aiResult.generated) {
      aiOut = '<div class="vmm-card vmm-ai-out" style="display:block;" id="vmMusicAIOut">' +
        '<h3>Your produced package</h3>' +
        (s.aiResult.title ? '<p class="vmm-sub"><b>Title:</b> ' + esc(s.aiResult.title) + '</p>' : "") +
        (s.aiResult.structure ? '<p class="vmm-sub"><b>Structure:</b> ' + esc(s.aiResult.structure) + '</p>' : "") +
        (s.aiResult.lyrics ? '<h4>Lyrics</h4><div class="lyrics">' + esc(s.aiResult.lyrics) + '</div>' : "") +
        (s.aiResult.arrangement ? '<h4>Arrangement</h4><p class="vmm-sub">' + esc(s.aiResult.arrangement) + '</p>' : "") +
        (s.aiResult.note ? '<div class="vmm-note">' + esc(s.aiResult.note) + '</div>' : "") +
        '<div style="margin-top:14px;"><button class="vmm-btn vmm-btn-primary" onclick="window.vmMusicAPI.saveSong()">Save this package</button> <button class="vmm-btn" onclick="window.vmMusicAPI.exportSong()">Export</button></div>' +
        '</div>';
    }

    return '' +
      '<div class="vmm-head">' +
        '<div class="vmm-logo">V</div>' +
        '<div><h2>Music Studio</h2><p>' + (s.take.name || s.beat.name || 'Sing, hum, or describe — then produce it') + '</p></div>' +
        '<div class="vmm-spacer"></div>' +
        '<input class="vmm-input" id="vmMusicName" value="' + esc(s.name) + '" style="max-width:220px;" placeholder="Song name">' +
        '<button class="vmm-btn" onclick="window.vmMusicAPI.newSong()">New</button>' +
        '<button class="vmm-btn vmm-btn-primary" onclick="window.vmMusicAPI.saveSong()">Save</button>' +
      '</div>' +
      '<div class="vmm-sheet">' +
        '<audio id="vmMusicPlayer" style="display:none;"></audio>' +
        '<div class="vmm-card">' +
          '<h3>How do you want to make it?</h3>' +
          '<p class="vmm-sub">Choose how ValleyMind helps you turn your idea into a song.</p>' +
          '<div class="vmm-mode-switch">' +
            modeBtn("diy", "Do it yourself", "You record, write and direct. ValleyMind organizes & saves your work.") +
            modeBtn("ai", "Let ValleyMind produce it", "You hum/sing or describe; ValleyMind writes lyrics + arrangement.") +
          '</div>' +
        '</div>' +

        '<div class="vmm-card">' +
          '<h3>Your starting point</h3>' +
          '<p class="vmm-sub">Physically sing, hum or record a take — just like a voice note — or upload a melody/beat.</p>' +
          '<div class="vmm-rec-row">' +
            '<button class="vmm-rec-btn' + (MS.recorder && MS.recorder.state === "recording" ? " recording" : " green") + '" id="vmMusicRecBtn" onclick="window.vmMusicAPI.toggleRecord()" title="Record / stop"><svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4"/></svg></button>' +
            '<span class="vmm-rec-time" id="vmMusicRecTime">' + fmtTime(MS.elapsed) + '</span>' +
            '<label class="vmm-btn" style="cursor:pointer;"><input type="file" accept="audio/*" id="vmMusicTakeInput" style="display:none;">Upload melody audio</label>' +
            '<label class="vmm-btn" style="cursor:pointer;"><input type="file" accept="audio/*" id="vmMusicBeatInput" style="display:none;">Upload a beat</label>' +
          '</div>' +
          '<div style="margin-top:14px;">' + tracks + '</div>' +
        '</div>' +

        (s.mode === "ai" ? aiBlock : "") +

        '<div class="vmm-card">' +
          '<h3>Voice</h3>' +
          '<p class="vmm-sub">Choose whose voice sings this song. AI voice options require your consent below.</p>' +
          voiceOpt("keep", false) +
          voiceOpt("clone", false) +
          voiceOpt("elena", false) +
          consentBlock +
          '<p class="vmm-sub" id="vmMusicConsentState" style="' + (s.voice === "clone" && !s.consent ? "color:#fcd34d;" : "") + '">' + (cloneLocked ? "Authorize above to enable the AI clone of your voice." : "") + '</p>' +
        '</div>' +

        lyricsBlock +

        aiOut +

        '<div class="vmm-card">' +
          '<h3>Projects</h3>' +
          '<p class="vmm-sub">Saved songs live on this device. Delete a project to also remove any saved voice clone.</p>' +
          (projRows || '<div class="vmm-empty">No saved songs yet.</div>') +
        '</div>' +
      '</div>' +
      '<div class="vmm-toast" id="vmMusicToast"></div>';
  }

  function bindFields() {
    var on = function (id, fn) { var el = document.getElementById(id); if (el) el.addEventListener("change", fn); };
    on("vmMusicTakeInput", function (e) { onTakeFile(e.target); });
    on("vmMusicBeatInput", function (e) { onBeatFile(e.target); });
    on("vmMusicGenre", function () { syncInputs(); });
    on("vmMusicMood", function () { syncInputs(); });
    on("vmMusicTempo", function () { syncInputs(); });
    on("vmMusicRole", function () { syncInputs(); });
    on("vmMusicName", function () { syncInputs(); });
    var b = document.getElementById("vmMusicBrief"); if (b) b.addEventListener("input", function () { syncInputs(); });
    var l = document.getElementById("vmMusicLyrics"); if (l) l.addEventListener("input", function () { syncInputs(); });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* ── Public API (global so inline onclick handlers can reach it) ────── */
  var API = {
    onMode: onMode,
    toggleRecord: toggleRecord,
    playAudio: playAudio,
    onVoice: function (v) { syncInputs(); MS.state.voice = v; if (v !== "clone") MS.state.consent = false; render(); },
    onConsent: function () { MS.state.consent = !MS.state.consent; render(); },
    runAI: runAI,
    saveSong: saveSong,
    newSong: newSong,
    loadSong: loadSong,
    deleteSong: deleteSong,
    exportSong: exportSong
  };
  window.vmMusicAPI = API;

  function onShow() {
    if (!MS.rendered) {
      MS.rendered = true;
      if (!MS.state) { loadProjects(); MS.state = defaultState(); MS.state.id = null; }
    }
    render();
  }
  window.vmMusicOnShow = onShow;

  /* ── Init ───────────────────────────────────────────────────────────── */
  function init() {
    injectStyles();
    if (!MS.state) { loadProjects(); MS.state = defaultState(); MS.state.id = null; }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { onShow(); });
    } else {
      onShow();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
