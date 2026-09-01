/* ValleyMind — Massive Editing
   ------------------------------
   The instruction-driven AI edit tool, now a workspace inside the Studio
   (Studio → Massive Editing, right after Music Studio). The user types or
   records a voice instruction, drops in the footage + extra media, picks a
   sticker, then hits "Start AI Edit". Everything they attach actually travels
   to the real editing pipeline:

     POST /api/editing/transcribe  — a recorded voice note becomes text
     POST /api/editing/run         — instruction + voice + media + sticker
     GET  /api/studio/job/:id      — poll: AI Edit Plan → stages → final video

   Nothing auto-starts on upload; the edit launches only on the explicit
   "Start AI Edit" tap. Mirrors static/music_studio.js (single IIFE, small
   public window.VMEditing surface, idempotent render). Workspace markup + CSS
   live in index.html under `.me-*`.
*/
(function () {
  "use strict";

  var CACHE_BUST = "?v=1";
  var MAX_FILE_MB = 100;
  var STAGES = ["planned", "broll", "sticker", "slow-motion", "music", "done"];
  var POSITIONS = [
    { v: "tl", l: "Top left" },
    { v: "tr", l: "Top right" },
    { v: "center", l: "Center" },
    { v: "bl", l: "Bottom left" },
    { v: "br", l: "Bottom right" }
  ];

  var ME = {
    rendered: false,
    mode: "intro",            // intro | working | result
    files: [],                // {id, kind, name, file, url, size}
    voice: null,              // {blob, url, text, transcribing, err}
    sticker: null,            // {name, url}
    stickerPos: "br",
    jobId: "",
    start: 0,
    timer: null,
    rec: { stream: null, recorder: null, chunks: [], active: false }
  };

  var rootEl = null;
  var textEl = null;
  var testEl = null;
  var toasts = [];
  var lastPlanJSON = "";

  function E(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function $(id) { return document.getElementById(id); }

  function apiHeaders(o) {
    if (typeof authHeaders === "function") return authHeaders(o);
    return o || {};
  }

  function postJSON(url, opts) {
    if (typeof apiFetch === "function") return apiFetch(url, opts);
    var controller = new AbortController();
    var to = window.setTimeout(function () { controller.abort(); }, (opts && opts.timeoutMs) || 30000);
    return fetch((typeof apiUrl === "function" ? apiUrl(url) : url), {
      method: opts.method || "GET",
      headers: apiHeaders((opts && opts.headers) || {}),
      body: opts.body,
      credentials: "include",
      signal: controller.signal
    }).then(function (r) { window.clearTimeout(to); return r; });
  }

  /* ── Entry points used by index.html (legacy wrappers + workspace hook) ── */
  function launch() {
    if (typeof closeSidebar === "function") closeSidebar();
    if (typeof openStudio === "function") openStudio();
    if (typeof vmWsGo === "function") vmWsGo("editing");
    else if (rootEl) rootEl.style.display = "flex";
  }

  function hide() {
    stopRec();
    stopVoicePreview();
    ME._paused = true;
  }

  function onShow() {
    ME._paused = false;
    init();
  }

  function reset() {
    if (ME.timer) { window.clearInterval(ME.timer); ME.timer = null; }
    ME.jobId = "";
    ME.start = 0;
    ME.sticker = null;
    ME.stickerPos = "br";
    revokeFiles();
    ME.files = [];
    clearVoice(true);
    if (textEl) textEl.value = "";
    if (testEl) testEl.checked = false;
    ME.mode = "intro";
    renderBody();
    updateGo();
  }

  function status(main, sub) {
    if (ME.mode !== "working") return;
    if (main) { var ph = $("mePhase"); if (ph) ph.textContent = main; }
    if (sub) { var sb = $("meSub"); if (sb) sb.textContent = sub; }
  }

  function refine() {
    if (ME.timer) { window.clearInterval(ME.timer); ME.timer = null; }
    ME.jobId = "";
    ME.mode = "intro";
    renderBody();
    updateGo();
  }

  /* ── Render ─────────────────────────────────────────────────────────── */
  function init() {
    if (ME.rendered) { if (rootEl) rootEl.style.display = ""; updateGo(); return; }
    rootEl = $("meRoot");
    if (!rootEl) return;
    ME.rendered = true;
    renderShell();
    renderBody();
    fetchStickers();
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { stopRec(); }
    });
  }

  function renderShell() {
    rootEl.innerHTML =
      '<div class="me-topbar">' +
      '  <h2>Massive Editing</h2>' +
      '  <span class="me-sub" id="meTopSub">Instruction → plan → rendered short</span>' +
      '  <button class="me-close" onclick="VMEditing.toDashboard()">&larr; Back to Studio</button>' +
      '</div>' +
      '<div class="me-body"><div class="me-inner" id="meInner"></div></div>';
  }

  function renderBody() {
    var inner = $("meInner");
    if (!inner) return;
    if (ME.mode === "working") { inner.innerHTML = workingHTML(); return; }
    if (ME.mode === "result") { renderResultBody(); return; }
    inner.innerHTML = introHTML();
    bindIntro();
    renderUploads();
    renderVoiceBox();
    renderStickerSel();
    updateGo();
    if (ME.files.length && !hasVideo(ME.files)) showToast("Add a video clip — that's what gets edited.");
  }

  function introHTML() {
    return '<div id="meToast" class="me-toast" style="display:none"></div>' +
      '<div class="me-card">' +
      '  <div class="me-q">What do you want me to do with this clip?</div>' +
      '  <p class="me-sub">Type it or say it — same weight. Like: “Hype reel — captions on, slow-mo the celebration, fire sticker when he scores, use the uploaded beat as music.”</p>' +
      '  <textarea id="meText" class="me-text" placeholder="e.g. Turn this goal celebration into a hype reel: word captions, slow the celebration down, fire sticker at the goal, and mix the uploaded beat under it."></textarea>' +
      '  <div class="me-voice-row">' +
      '    <button id="meVoiceBtn" class="me-btn" onclick="VMEditing.toggleRec()">🎙️ Record a voice instruction</button>' +
      '    <span class="me-sub">Speech is transcribed into the instruction (the audio is kept on the job too).</span>' +
      '  </div>' +
      '  <div id="meVoiceBox" style="display:none"></div>' +
      '</div>' +
      '<div class="me-card">' +
      '  <div class="me-q">Source footage &amp; media</div>' +
      '  <p class="me-sub">The first video you attach is what gets edited. Add extra shots, images or sounds too — the plan decides how to use them (B-roll, beats, insets).</p>' +
      '  <div id="meUploadDrop" class="me-upload-drop">' +
      '    <div class="me-u-icon">🎞️</div>' +
      '    <div class="me-u-title">Tap or drag files in</div>' +
      '    <div class="me-u-sub">video · image · audio — up to ' + MAX_FILE_MB + 'MB each</div>' +
      '    <input type="file" id="meFilesInput" accept="video/*,image/*,audio/*" multiple style="display:none">' +
      '  </div>' +
      '  <div id="meUploads" class="me-uploads"></div>' +
      '</div>' +
      '<div class="me-card">' +
      '  <div class="me-q">Sticker (optional)</div>' +
      '  <p class="me-sub">Pick one and say when it should appear — “put the 🔥 on the goal call”. The AI places it on its own moment unless you say otherwise.</p>' +
      '  <div id="meStickers" class="me-sticker-row"><span class="me-sub">Loading sticker library…</span></div>' +
      '  <div id="mePosRow" class="me-pos-row" style="display:none"></div>' +
      '  <div id="meStickerSel"></div>' +
      '</div>' +
      '<div class="me-start-bar">' +
      '  <button id="meGoBtn" class="me-go" onclick="VMEditing.submit()" disabled>🚀 Start AI Edit</button>' +
      '  <label class="me-testmode"><input type="checkbox" id="meTestMode"> Test mode (skip AI B-roll)</label>' +
      '</div>';
  }

  function workingHTML() {
    return '<div class="me-plan">' +
      '  <div class="me-plan-title">🧠 AI Edit Plan</div>' +
      '  <div id="mePlanList"><div class="me-plan-item"><span class="me-pic"></span>Reading your instruction…</div></div>' +
      '  <div id="mePlanNote" class="me-plan-note"></div>' +
      '</div>' +
      '<div class="me-progress">' +
      '  <div class="me-spinner"></div>' +
      '  <div class="me-phase" id="mePhase">Planning your edit…</div>' +
      '  <div class="me-sub" id="meSub"></div>' +
      '</div>';
  }

  function renderResultBody() {
    var inner = $("meInner");
    if (!inner) return;
    inner.innerHTML = '<div class="me-result">' +
      '  <div class="me-sheet-label">Your rendered short</div>' +
      '  <video id="meVideo" controls playsinline preload="metadata"></video>' +
      '  <div class="me-stats" id="meStats"></div>' +
      '  <div class="me-actions">' +
      '    <a id="meDownload" class="me-btn primary" download>Download</a>' +
      '    <button class="me-btn" onclick="VMEditing.refine()">Refine</button>' +
      '    <button class="me-btn" onclick="VMEditing.reset()">Edit another</button>' +
      '  </div>' +
      '</div>';
    var v = $("meVideo");
    if (v && ME.resultVideo) v.src = ME.resultVideo;
    var dl = $("meDownload");
    if (dl && ME.resultVideo) { dl.href = ME.resultVideo; dl.setAttribute("download", "massive-edit.mp4"); }
    var st = ME.resultStats || {};
    var parts = [];
    if (st.source_seconds && st.output_seconds) parts.push("Trimmed " + st.source_seconds + "s → " + st.output_seconds + "s");
    if (st.removed_words) parts.push(st.removed_words + " filler/pause words cut");
    if (st.brolls) parts.push(st.brolls + " B-roll moment" + (st.brolls > 1 ? "s" : ""));
    if (st.caption_words) parts.push(st.caption_words + " captioned words");
    if (st.sticker_applied) parts.push("Sticker overlaid");
    if (st.slow_motion) parts.push("Slow-motion moment");
    if (st.music) parts.push("Uploaded music mixed in");
    var el = $("meStats");
    if (el) el.textContent = parts.length ? parts.join("  ·  ") : "Rendered from your instruction.";
  }

  function bindIntro() {
    textEl = $("meText");
    testEl = $("meTestMode");
    if (textEl) textEl.addEventListener("input", updateGo);
    if (testEl) testEl.addEventListener("change", updateGo);
    var drop = $("meUploadDrop");
    var input = $("meFilesInput");
    if (drop && input) {
      var open = function () { input.click(); };
      drop.addEventListener("click", open);
      input.addEventListener("change", function () { addFiles(this.files); this.value = ""; });
      ["dragenter", "dragover"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("drag"); });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("drag"); });
      });
      drop.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
      });
    }
  }

  function renderUploads() {
    var box = $("meUploads");
    if (!box) return;
    if (!ME.files.length) { box.innerHTML = ""; return; }
    var html = "";
    var firstVideo = true;
    ME.files.forEach(function (f) {
      var isVid = f.kind === "video";
      var thumb = '<div class="me-uf-thumb">' +
        (f.kind === "image" ? '<img src="' + E(f.url) + '" alt="">'
          : f.kind === "video" ? '<video src="' + E(f.url) + '" muted preload="metadata"></video>'
          : "🎵") + '</div>';
      var badge = isVid && firstVideo ? '<span style="position:absolute;top:6px;left:6px;font-size:9px;font-weight:800;letter-spacing:0.06em;color:#04222b;background:linear-gradient(135deg,#00d4ff,#0ea5e9);border-radius:999px;padding:2px 7px;">EDIT SOURCE</span>' : "";
      var mb = f.size ? (f.size / 1048576).toFixed(1) + "MB" : "";
      html += '<div class="me-ufile">' + badge +
        '  <button class="me-uf-rm" title="Remove" onclick="VMEditing.removeFile(\'' + f.id + '\')">×</button>' +
        thumb +
        '  <div class="me-uf-name">' + E(f.name) + (mb ? " · " + mb : "") + '</div>' +
        '</div>';
      if (isVid) firstVideo = false;
    });
    box.innerHTML = html;
  }

  function renderVoiceBox() {
    var box = $("meVoiceBox");
    if (!box) return;
    if (!ME.voice) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "";
    var v = ME.voice;
    var inner = '<div class="me-voice-note">' +
      '  <span class="me-vn-label">🎙️ Voice instruction</span>' +
      '  <button class="me-btn danger" onclick="VMEditing.clearVoice()">Discard</button>' +
      '  <audio class="me-vn-audio" controls src="' + E(v.url) + '"></audio>' +
      '</div>' +
      '<div style="margin-top:8px">' +
      (v.transcribing ? '<span class="me-sub">Transcribing…</span>'
        : v.text ? '<span class="me-vn-trans">✍️ ' + E(v.text) + '</span>'
        : v.err ? '<span class="me-vn-trans">' + E(v.err) + '</span>'
        : '<span class="me-vn-trans">Recorded — tap start when you’re ready. (Keep the audio or type; it travels with the edit.)</span>') +
      '</div>';
    box.innerHTML = inner;
    var btn = $("meVoiceBtn");
    if (btn) {
      btn.textContent = ME.rec.active ? "⏹️ Stop recording…" : "🎙️ Record a voice instruction";
      btn.classList.toggle("recording", ME.rec.active);
    }
  }

  function renderStickerSel() {
    var sel = $("meStickerSel");
    if (sel) {
      sel.innerHTML = ME.sticker
        ? '<div class="me-sub" style="margin-top:8px">Selected: <b style="color:#e2e8f0">' + E(ME.sticker.name) + '</b> — ta right ' +
          E((POSITIONS.filter(function (p) { return p.v === ME.stickerPos; })[0] || POSITIONS[4]).l.toLowerCase()) +
          '.</div>'
        : "";
    }
    renderPosRow();
  }

  function renderPosRow() {
    var row = $("mePosRow");
    if (!row) return;
    row.style.display = ME.sticker ? "" : "none";
    row.innerHTML = POSITIONS.map(function (p) {
      return '<button class="me-pos' + (ME.stickerPos === p.v ? " active" : "") +
        '" onclick="VMEditing.setPos(\'' + p.v + '\')">' + p.l + "</button>";
    }).join("");
  }

  function fetchStickers() {
    postJSON("/api/editing/stickers", { credentials: "include", headers: apiHeaders(), timeoutMs: 20000 })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var box = $("meStickers");
        if (!box) return;
        var list = (d && d.stickers) || [];
        box.innerHTML = "";
        if (!list.length) {
          box.innerHTML = '<span class="me-sub">No stickers bundled right now — you can still describe one in the instruction.</span>';
          return;
        }
        list.forEach(function (s) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.title = (s.name || "sticker");
          btn.className = "me-sticker-pick" + (ME.sticker && ME.sticker.name === s.name && ME.sticker.url === s.url ? " selected" : "");
          var img = document.createElement("img");
          img.src = (s.url || "") + CACHE_BUST;
          img.alt = s.name || "sticker";
          btn.appendChild(img);
          btn.addEventListener("click", function () { pickSticker(s.name, s.url); });
          box.appendChild(btn);
        });
      })
      .catch(function () {
        var box = $("meStickers");
        if (box) box.innerHTML = '<span class="me-sub">Couldn’t load the sticker library.</span>';
      });
  }

  function pickSticker(name, url) {
    ME.sticker = { name: name || "sticker", url: url };
    renderStickerSel();
    document.querySelectorAll(".me-sticker-pick").forEach(function (b) {
      var img = b.querySelector("img");
      b.classList.toggle("selected", !!(img && img.src.indexOf(url) > -1));
    });
  }

  function setPos(v) {
    ME.stickerPos = v;
    renderPosRow();
  }

  /* ── Voice recording ────────────────────────────────────────────────── */
  function toggleRec() {
    if (ME.rec.active) { stopRec(); return; }
    startRec();
  }

  function startRec() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      showToast("Recording isn’t supported in this browser — type your instruction instead.");
      return;
    }
    stopVoicePreview();
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var mime = "";
      if (typeof window.MediaRecorder.isTypeSupported === "function") {
        mime = window.MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus"
          : window.MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      }
      ME.rec.stream = stream;
      ME.rec.chunks = [];
      ME.rec.recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      ME.rec.recorder.ondataavailable = function (e) { if (e.data && e.data.size) ME.rec.chunks.push(e.data); };
      ME.rec.recorder.onstop = onRecStopped;
      ME.rec.active = true;
      try { ME.rec.recorder.start(); } catch (e) {
        ME.rec.active = false;
        showToast("Couldn’t start the recorder.");
        return;
      }
      renderVoiceBox();
      showToast("Listening — say your instruction, then tap stop.");
    }).catch(function () {
      showToast("Microphone unavailable — type your instruction instead.");
    });
  }

  function stopRec() {
    if (!ME.rec.active && ME.rec.recorder) {
      try { ME.rec.recorder.stop(); } catch (e) {}
    } else if (ME.rec.active && ME.rec.recorder && ME.rec.recorder.state !== "inactive") {
      try { ME.rec.recorder.stop(); } catch (e) {}
    }
    ME.rec.active = false;
    if (ME.rec.stream) { ME.rec.stream.getTracks().forEach(function (t) { t.stop(); }); ME.rec.stream = null; }
    renderVoiceBox();
  }

  function onRecStopped() {
    var mime = (ME.rec.recorder && ME.rec.recorder.mimeType) || "audio/webm";
    var blob = new Blob(ME.rec.chunks, { type: mime });
    ME.rec.chunks = [];
    ME.rec.recorder = null;
    ME.rec.active = false;
    if (!blob.size) { renderVoiceBox(); return; }
    if (ME.voice && ME.voice.url) URL.revokeObjectURL(ME.voice.url);
    ME.voice = { blob: blob, url: URL.createObjectURL(blob), text: "", transcribing: true, err: "" };
    renderVoiceBox();
    transcribeVoice(blob);
  }

  function transcribeVoice(blob) {
    var fd = new FormData();
    fd.append("audio", blob, "voice-note.webm");
    postJSON("/api/editing/transcribe", {
      method: "POST", credentials: "include",
      headers: apiHeaders(), body: fd, timeoutMs: 180000
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        ME.voice.transcribing = false;
        if (d && d.status === "success" && (d.text || "").trim()) {
          ME.voice.text = d.text.trim();
        } else {
          ME.voice.err = (d && d.message) || "Couldn’t transcribe.";
        }
        renderVoiceBox();
        updateGo();
      })
      .catch(function (err) {
        ME.voice.transcribing = false;
        ME.voice.err = "Couldn’t connect to transcribe — the audio is still kept on the job.";
        renderVoiceBox();
        updateGo();
      });
  }

  function clearVoice(revoke) {
    stopRec();
    stopVoicePreview();
    if (ME.voice && ME.voice.url) URL.revokeObjectURL(ME.voice.url);
    ME.voice = null;
    renderVoiceBox();
    updateGo();
    if (revoke !== true) { var box = $("meVoiceBox"); if (box) box.style.display = "none"; }
  }

  function stopVoicePreview() {
    document.querySelectorAll(".me-vn-audio").forEach(function (a) { try { a.pause(); } catch (e) {} });
  }

  /* ── File handling ──────────────────────────────────────────────────── */
  function fileKind(f) {
    var mt = (f.type || "").toLowerCase();
    var name = (f.name || "").toLowerCase();
    var ext = name.split(".").pop();
    if (mt.indexOf("video/") === 0 || ["mp4", "webm", "mov", "m4v"].indexOf(ext) >= 0) return "video";
    if (mt.indexOf("image/") === 0 || ["png", "jpg", "jpeg", "gif", "webp"].indexOf(ext) >= 0) return "image";
    if (mt.indexOf("audio/") === 0 || ["mp3", "wav", "ogg", "m4a", "aac", "weba"].indexOf(ext) >= 0) return "audio";
    return "";
  }

  function hasVideo(files) {
    return files.some(function (f) { return f.kind === "video"; });
  }

  function addFiles(list) {
    if (!list) return;
    for (var i = 0; i < list.length; i++) {
      var f = list[i];
      if (!f || !f.name) continue;
      if (f.size > MAX_FILE_MB * 1024 * 1024) {
        showToast(f.name + " is over " + MAX_FILE_MB + "MB.");
        continue;
      }
      var kind = fileKind(f);
      if (!kind) { showToast(f.name + " isn’t a video/image/audio file."); continue; }
      ME.files.push({ id: Date.now() + "-" + i, kind: kind, name: f.name, file: f, url: URL.createObjectURL(f), size: f.size });
    }
    renderUploads();
    updateGo();
  }

  function removeFile(id) {
    for (var i = 0; i < ME.files.length; i++) {
      if (ME.files[i].id === id) {
        if (ME.files[i].url) URL.revokeObjectURL(ME.files[i].url);
        ME.files.splice(i, 1);
        break;
      }
    }
    renderUploads();
    updateGo();
  }

  function revokeFiles() {
    ME.files.forEach(function (f) { if (f.url) URL.revokeObjectURL(f.url); });
  }

  function updateGo() {
    var go = $("meGoBtn");
    if (!go) return;
    var instruction = (textEl && textEl.value || "").trim() || (ME.voice && (ME.voice.text || "").trim()) ||
      (ME.voice && ME.voice.blob ? true : false);
    var ok = !!(instruction && hasVideo(ME.files));
    go.disabled = !ok;
    go.title = ok ? "Start the AI edit" : "Type or record an instruction and attach a video first.";
  }

  /* ── Submit + polling ───────────────────────────────────────────────── */
  function submit() {
    if (ME.timer) return;
    var text = (textEl && textEl.value || "").trim();
    var voiceText = (ME.voice && (ME.voice.text || "").trim()) || "";
    if (!text && !voiceText && !(ME.voice && ME.voice.blob)) { showToast("Type or record an instruction first."); return; }
    if (!hasVideo(ME.files)) { showToast("Attach a video clip to edit first."); return; }
    stopRec();
    stopVoicePreview();

    ME.mode = "working";
    renderBody();
    setWorking("Uploading your footage…", "Holding on — sending the clip, instruction, media and sticker to the editing pipeline.");

    var fd = new FormData();
    var sentVid = false;
    ME.files.forEach(function (f) {
      if (f.kind === "video" && !sentVid) { fd.append("video", f.file, f.name); sentVid = true; }
      else { fd.append("media", f.file, f.name); }
    });
    if (text) fd.append("instruction", text);
    if (voiceText) fd.append("voice_transcript", voiceText);
    if (ME.voice && ME.voice.blob) {
      var vext = /webm/i.test(ME.voice.blob.type) ? ".webm" : /ogg/i.test(ME.voice.blob.type) ? ".ogg" : ".m4a";
      fd.append("voice_note", ME.voice.blob, "voice-note" + vext);
    }
    if (ME.sticker) {
      fd.append("sticker_url", ME.sticker.url);
      fd.append("sticker_name", ME.sticker.name);
      fd.append("sticker_pos", ME.stickerPos);
    }
    if (testEl && testEl.checked) fd.append("test_mode", "1");

    postJSON("/api/editing/run", {
      method: "POST", credentials: "include",
      headers: apiHeaders(), body: fd, timeoutMs: 150000
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.status !== "success" || !d.job) {
          setWorking("Couldn’t start", (d && d.message) || "Please try again.");
          scheduleRecover();
          return;
        }
        ME.jobId = d.job.job_id;
        ME.start = Date.now();
        setWorking("Planning your edit…", "Reading your instruction, transcribing the clip and deciding what to do with it.");
        ME.timer = window.setInterval(poll, 4000);
        poll();
      })
      .catch(function (err) {
        setWorking("Upload failed", (err && err.message) || "Check your connection and try again.");
        scheduleRecover();
      });
  }

  function scheduleRecover() {
    window.setTimeout(function () {
      if (ME.mode === "working" && !ME.jobId) { ME.mode = "intro"; renderBody(); updateGo(); }
    }, 3500);
  }

  function poll() {
    if (!ME.jobId) return;
    postJSON("/api/studio/job/" + encodeURIComponent(ME.jobId), {
      credentials: "include", headers: apiHeaders(), timeoutMs: 20000
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var job = d && d.job;
        if (!job) return;
        if (job.edit_plan && job.edit_plan.length) renderPlan(job);
        if (ME.mode !== "working") return;
        if (job.status === "running" || job.status === "queued") {
          var stage = job.edit_plan_stage || "";
          var secs = ME.start ? Math.round((Date.now() - ME.start) / 1000) : 0;
          var line = "In the background so you can keep going — " + secs + "s in.";
          if (job.stalled) line = "Backend is waking up — this can take a moment on the free plan.";
          setWorking(stageLabel(stage), line);
          if (job.edit_plan_note) { var n = $("mePlanNote"); if (n) n.textContent = job.edit_plan_note; }
          return;
        }
        if (ME.timer) { window.clearInterval(ME.timer); ME.timer = null; }
        if (job.status === "done" && job.final_video) {
          ME.resultVideo = job.final_video;
          ME.resultStats = job.stats || {};
          ME.mode = "result";
          renderBody();
        } else {
          ME.jobFailMsg = job.error || "Editing didn’t finish — try another clip.";
          ME.mode = "intro";
          renderBody();
          showToast(ME.jobFailMsg);
        }
      })
      .catch(function () {});
  }

  function stageLabel(stage) {
    var map = {
      "planned": "Planning your edit…",
      "broll": "Adding B-roll…",
      "sticker": "Applying the sticker…",
      "slow-motion": "Applying slow motion…",
      "music": "Mixing in the music…",
      "done": "Wrapping up…"
    };
    return map[stage] || (stage ? "Editing…" : "Planning your edit…");
  }

  function renderPlan(job) {
    var list = $("mePlanList");
    if (!list) return;
    var steps = (job.edit_plan || []).filter(function (s) { return s && s.step; });
    var current = STAGES.indexOf(job.edit_plan_stage || "");
    var doneN = current > 0 ? Math.max(1, current) : 0;
    doneN = Math.min(doneN, steps.length);
    var html = steps.map(function (s, i) {
      return '<div class="me-plan-item' + (i < doneN ? " done" : "") + '"><span class="me-pic"></span>' + E(s.step) + "</div>";
    }).join("");
    if (html) list.innerHTML = html;
  }

  function setWorking(main, sub) {
    status(main, sub);
  }

  /* ── Toast / misc ───────────────────────────────────────────────────── */
  function showToast(msg) {
    if (!msg) return;
    var t = $("meToast");
    if (t) {
      t.textContent = msg;
      t.style.display = "";
      window.setTimeout(function () { if (t) t.style.display = "none"; }, 5200);
    } else {
      try { alert(msg); } catch (e) {}
    }
  }

  function toDashboard() {
    stopRec();
    stopVoicePreview();
    if (typeof studioShowDashboard === "function") studioShowDashboard();
    else if (typeof openStudio === "function") openStudio();
  }

  var API = {
    launch: launch,
    hide: hide,
    onShow: onShow,
    reset: reset,
    status: status,
    refine: refine,
    toggleRec: toggleRec,
    clearVoice: clearVoice,
    pickSticker: pickSticker,
    setPos: setPos,
    removeFile: removeFile,
    submit: submit,
    toDashboard: toDashboard
  };
  window.VMEditing = API;

  /* ── Init ───────────────────────────────────────────────────────────── */
  function initOnce() {
    init();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initOnce);
  } else {
    initOnce();
  }
})();