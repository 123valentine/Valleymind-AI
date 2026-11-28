/* ValleyMind — Massive Editing workspace
   -----------------------------------------------------------
   Phase 1: one lightweight, mobile-first workspace — top bar, preview,
   compact timeline, unified prompt composer and four slide-up drawers.

   No sticker packs, reaction clips or heavy media are bundled here. User
   uploads are staged briefly on disk by the server and routed through the
   media manager, which stores them in object storage when it is configured
   and uses temporary local storage only when object storage is unset.

   Engine/API behaviour is unchanged. Modes: intro -> working -> editor.
*/
(function () {
  "use strict";

  var CACHE_BUST = "?v=4";
  var MAX_FILE_MB = 100;
  var STAGES = ["analysis", "planned", "broll", "sticker", "slow-motion",
    "camera", "transitions", "sfx", "music", "done"];
  var POSITIONS = [
    {v: "tl", l: "Top left"}, {v: "tr", l: "Top right"},
    {v: "center", l: "Center"}, {v: "bl", l: "Bottom left"},
    {v: "br", l: "Bottom right"}
  ];

  // Shared vocabularies live in settings.js; these are fallback copies so the
  // module still renders if that script is unavailable in a test harness.
  var MODES = (typeof window !== "undefined" && window.VM_MASSIVE_MODES &&
    window.VM_MASSIVE_MODES.length) ? window.VM_MASSIVE_MODES.slice() : [
    "Auto AI Director", "Skit & Meme Cutaways", "Sports / Beat-Sync",
    "Tutorial Pop-Ups", "Character Fusion"
  ];
  var EFFECT_GROUPS = (typeof window !== "undefined" && window.VM_MASSIVE_EFFECT_GROUPS &&
    window.VM_MASSIVE_EFFECT_GROUPS.length) ? window.VM_MASSIVE_EFFECT_GROUPS : [
    {name: "Velocity & Beat", items: ["Beat Flash", "Zoom Punch", "Velocity Slow-Mo", "Camera Shake"]},
    {name: "Color & Vibe", items: ["Phonk Contrast", "Cinematic Warm"]},
    {name: "Framing", items: ["Vignette"]}
  ];
  var STICKER_GROUPS = (typeof window !== "undefined" && window.VM_MASSIVE_STICKER_GROUPS &&
    window.VM_MASSIVE_STICKER_GROUPS.length) ? window.VM_MASSIVE_STICKER_GROUPS.slice() : [
    "My Stickers", "Reactions", "Sports", "Tutorial Pop-Ups"
  ];
  var CAPTION_STYLES = (typeof window !== "undefined" && window.VM_MASSIVE_CAPTION_STYLES &&
    window.VM_MASSIVE_CAPTION_STYLES.length) ? window.VM_MASSIVE_CAPTION_STYLES.slice() : [
    "Word-by-Word Pop", "Clean Subtitle", "Off"
  ];
  var AUDIO_MODES = (typeof window !== "undefined" && window.VM_MASSIVE_AUDIO_MODES &&
    window.VM_MASSIVE_AUDIO_MODES.length) ? window.VM_MASSIVE_AUDIO_MODES.slice() : [
    "Keep Original Audio", "Add Background Track"
  ];

  // Preset cards are presentation-only. They map onto the engine's existing
  // effect/flag vocabulary so no new rendering backend is required in Phase 1.
  var EFFECT_MAP = {
    "Beat Flash": {effects: ["boost"], camera: true},
    "Zoom Punch": {effects: ["boost"], camera: true},
    "Velocity Slow-Mo": {effects: [], slowmoFactor: 0.5},
    "Camera Shake": {effects: [], camera: true},
    "Phonk Contrast": {effects: ["boost", "saturated"]},
    "Cinematic Warm": {effects: ["sepia"]},
    "Vignette": {effects: ["vignette"]}
  };
  var DRAWER_TITLES = {
    effects: "Effects", stickers: "Stickers", captions: "Captions",
    audio: "Audio", projects: "Projects"
  };

  var ME = {
    rendered: false, mode: "intro", modeName: MODES[0], aspect: "9:16",
    files: [], voice: null, sticker: null, stickerPos: "br",
    jobId: "", start: 0, timer: null,
    drawer: "", assetCategory: "sticker", storageReady: false,
    drawerCats: {effects: "Velocity & Beat", stickers: "My Stickers"},
    assetLibrary: [],
    rec: {stream: null, recorder: null, chunks: [], active: false},
    undoStack: [], redoStack: [],
    projects: [],
    editor: {
      jobId: "", sourceVideo: "", resultVideo: "", timeline: null, stats: null,
      duration: 0, timelineZoom: 1,
      stickerLibrary: [], recentStickers: [], uploadedStickers: [],
      manual: {
        canvas: {aspect: "9:16", mode: "fill", bg: "000000", reframe: "smart"},
        trim: {start: 0, end: null}, speed: 1, rotate: 0,
        flipH: false, flipV: false, reverse: false,
        crop: {left: 0, right: 0, top: 0, bottom: 0}, resize: 1,
        captions: true, captionStyle: CAPTION_STYLES[0],
        captionAlign: "lower", captionScale: 1,
        title: "", titleSeconds: 3,
        effects: [], selectedEffects: [], fadeIn: 0, fadeOut: 0,
        musicUrl: "", musicName: "", musicVolume: 0.3,
        musicFadeIn: 0, musicFadeOut: 0,
        audioMode: AUDIO_MODES[0],
        stickerScale: 0.28, stickerAngle: 0, stickerDuration: 3,
        stickerPos: "br", stickerAnim: "",
        slowmoFactor: 0, autoCut: true, intensity: "medium",
        transitionsMode: "auto", camera: true, sfx: true,
        _aiInstruction: "", textLayers: []
      }
    }
  };

  var rootEl = null, textEl = null, testEl = null;

  function E(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }
  function $(id) { return document.getElementById(id); }
  function isSmall() {
    return !!(window.matchMedia && window.matchMedia("(max-width:700px)").matches);
  }
  function apiHeaders(o) {
    if (typeof authHeaders === "function") return authHeaders(o);
    return o || {};
  }
  function postJSON(url, opts) {
    if (typeof apiFetch === "function") return apiFetch(url, opts);
    var c = new AbortController();
    var t = window.setTimeout(function () { c.abort(); }, (opts && opts.timeoutMs) || 30000);
    return fetch((typeof apiUrl === "function" ? apiUrl(url) : url), {
      method: (opts && opts.method) || "GET",
      headers: apiHeaders((opts && opts.headers) || {}),
      body: opts && opts.body, credentials: "include", signal: c.signal
    }).then(function (r) { window.clearTimeout(t); return r; });
  }

  function svgIcon(name) {
    var icons = {
      sparkles: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/><path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/></svg>',
      film: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" x2="7" y1="2" y2="22"/><line x1="17" x2="17" y1="2" y2="22"/><line x1="2" x2="22" y1="12" y2="12"/></svg>',
      image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>',
      music: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
      x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
      type: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" x2="15" y1="20" y2="20"/><line x1="12" x2="12" y1="4" y2="20"/></svg>',
      'smile-plus': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" x2="9.01" y1="9" y2="9"/><line x1="15" x2="15.01" y1="9" y2="9"/><path d="M16 5h6v6"/></svg>',
      'wand-2': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72Z"/><path d="m14 7 3 3"/></svg>',
      'volume-2': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>',
      play: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
      pause: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>',
      'skip-back': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" x2="5" y1="19" y2="5"/></svg>',
      'skip-forward': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" x2="19" y1="5" y2="19"/></svg>',
      download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>',
      refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>',
      mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>',
      upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>',
      folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>',
      plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="12" x2="12" y1="5" y2="19"/><line x1="5" x2="19" y1="12" y2="12"/></svg>',
      check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
      trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>'
    };
    return icons[name] || "";
  }
  function icon(name, cls) {
    return '<span class="me-ic' + (cls ? ' ' + cls : '') + '">' + svgIcon(name) + '</span>';
  }

  /* ── Entry points ───────────────────────────────────────────────────── */
  function launch() {
    if (typeof closeSidebar === "function") closeSidebar();
    if (typeof openStudio === "function") openStudio();
    if (typeof vmWsGo === "function") vmWsGo("editing");
    else if (rootEl) rootEl.style.display = "flex";
  }
  function hide() { stopRec(); stopVoicePreview(); ME._paused = true; }
  function onShow() { ME._paused = false; init(); }
  function reset() {
    if (ME.timer) { window.clearInterval(ME.timer); ME.timer = null; }
    ME.jobId = ""; ME.start = 0; ME.sticker = null; ME.stickerPos = "br";
    ME.mode = "intro"; ME.drawer = "";
    ME.undoStack = []; ME.redoStack = [];
    revokeFiles(); ME.files = []; clearVoice(true);
    if (textEl) textEl.value = "";
    if (testEl) testEl.checked = false;
    closeSheets(); renderBody(); updateGo();
  }
  function toDashboard() {
    stopRec(); stopVoicePreview(); closeSheets();
    if (typeof studioShowDashboard === "function") studioShowDashboard();
    else if (typeof openStudio === "function") openStudio();
  }

  function showToast(msg) {
    if (!msg) return;
    var t = $("meToast");
    if (t) {
      t.textContent = msg; t.style.display = "";
      window.setTimeout(function () { if (t) t.style.display = "none"; }, 5200);
    } else { try { alert(msg); } catch (e) {} }
  }

  /* ── Shell + render ─────────────────────────────────────────────────── */
  function init() {
    if (ME.rendered) {
      if (rootEl) rootEl.style.display = "";
      renderBody(); return;
    }
    rootEl = $("meRoot"); if (!rootEl) return;
    ME.rendered = true;
    ME.projects = loadProjects();
    rootEl.innerHTML =
      '<div class="me-topbar" id="meTopbar"></div>' +
      '<div class="me-workspace" id="meWorkspace"></div>' +
      '<nav class="me-bottom-tabs" id="meBottomTabs"></nav>' +
      '<div class="me-drawer" id="meDrawer"></div>' +
      '<div class="me-scrim" id="meScrim" onclick="VMEditing.closeSheets()"></div>' +
      '<div id="meToast" class="me-toast" style="display:none"></div>';
    renderBody();
    // Stickers are empty-state only in Phase 1; custom additions come from
    // the user's own library via fetchAssets().
    fetchAssets(); fetchStorageStatus();
    document.addEventListener("visibilitychange", function () { if (document.hidden) stopRec(); });
  }

  function renderTopbar() {
    var tb = $("meTopbar"); if (!tb) return;
    var options = MODES.map(function (m) {
      return '<option value="' + E(m) + '">' + E(m) + '</option>';
    }).join("");
    tb.innerHTML =
      '<button class="me-top-projects" id="meProjectsBtn" aria-label="Projects" ' +
        'onclick="VMEditing.openDrawer(\'projects\')">' +
        icon("folder") + '<span>Projects</span></button>' +
      '<label class="me-mode-pill"><span>Mode</span>' +
        '<select id="meModeSelect" onchange="VMEditing.setMode(this.value)">' + options + '</select></label>' +
      '<span class="me-storage-dot' + (ME.storageReady ? ' ready' : '') + '" id="meStorageDot" ' +
        'title="' + (ME.storageReady ? 'Media storage ready' : 'Temporary media storage') + '"></span>' +
      '<button class="me-top-export" id="meExportBtn" aria-label="Export" ' +
        'onclick="VMEditing.exportVideo()">' +
        icon("download") + '<span>Export</span></button>' +
      '<button class="me-top-back" onclick="VMEditing.toDashboard()" title="Back to Studio">&larr;</button>';
    var sel = $("meModeSelect"); if (sel) sel.value = ME.modeName;
  }

  function renderBottomTabs() {
    var nav = $("meBottomTabs"); if (!nav) return;
    var tabs = [
      {id: "effects", icon: "wand-2", label: "Effects"},
      {id: "stickers", icon: "smile-plus", label: "Stickers"},
      {id: "captions", icon: "type", label: "Captions"},
      {id: "audio", icon: "volume-2", label: "Audio"}
    ];
    nav.innerHTML = tabs.map(function (t) {
      return '<button class="me-tab' + (ME.drawer === t.id ? ' active' : '') +
        '" data-tab="' + t.id + '" onclick="VMEditing.openDrawer(\'' + t.id + '\')">' +
        icon(t.icon) + '<span>' + t.label + '</span></button>';
    }).join("");
  }

  function renderBody() {
    renderTopbar(); renderBottomTabs(); renderDrawer();
    var ws = $("meWorkspace"); if (!ws) return;
    ws.innerHTML = workspaceHTML();
    bindWorkspace();
    renderPreview(); renderTimeline(); renderVoiceBox(); renderUploads(); updateGo();
    if (ME.mode === "working") {
      var overlay = $("meWorkingOverlay"); if (overlay) overlay.style.display = "";
    }
    if (ME.mode === "editor") renderEditor();
  }

  function workspaceHTML() {
    return '<div class="me-workspace-inner">' +
      '<div class="me-preview" id="mePreview" data-aspect="' + E(ME.aspect) + '">' +
        '<video id="meCanvasVideo" class="me-canvas-video" playsinline preload="metadata"></video>' +
        '<div class="me-preview-empty" id="mePreviewEmpty">' + icon("film") +
          '<span>Your preview appears here</span></div>' +
        '<div class="me-preview-tools">' +
          '<button data-aspect="16:9" onclick="VMEditing.setAspect(\'16:9\')">16:9</button>' +
          '<button data-aspect="9:16" onclick="VMEditing.setAspect(\'9:16\')">9:16</button>' +
        '</div>' +
        '<div class="me-canvas-controls" id="meCanvasControls"></div>' +
        '<div class="me-working-overlay" id="meWorkingOverlay" style="display:none">' +
          '<div class="me-spinner"></div>' +
          '<div class="me-phase" id="mePhase">Planning your edit...</div>' +
          '<div class="me-sub" id="meSub"></div>' +
          '<div class="me-plan" id="mePlanList"></div>' +
          '<div class="me-plan-note" id="mePlanNote"></div>' +
        '</div>' +
      '</div>' +
      '<div class="me-timeline-strip" id="meTimeline"></div>' +
      '<div class="me-composer" id="meComposer">' +
        '<div id="meVoiceBox" class="me-voice-slot" style="display:none"></div>' +
        '<div id="meUploads" class="me-attachment-chips"></div>' +
        '<div class="me-composer-row">' +
          '<button class="me-composer-plus" id="mePlusBtn" aria-label="Attach videos, audio files, or pictures" ' +
            'title="Attach videos, audio files, or pictures" onclick="VMEditing.pickFiles()">' + icon("plus") + '</button>' +
          '<input type="file" id="meFilesInput" accept="video/*,image/*,audio/*" multiple style="display:none">' +
          '<textarea id="meText" class="me-composer-input" rows="1" placeholder="Describe your edit..."></textarea>' +
          '<button class="me-composer-voice" id="meVoiceBtn" aria-label="Record a voice instruction" ' +
            'title="Record a voice instruction" onclick="VMEditing.toggleRec()">' + icon("mic") + '</button>' +
          // Apply is the explicit "Start AI Edit" submission. The label is
          // intentionally short; the old wording is preserved for API parity.
          '<button class="me-composer-apply" id="meGoBtn" title="Start AI Edit" ' +
            'onclick="VMEditing.submit()">Apply</button>' +
        '</div>' +
        '<label class="me-testmode me-composer-option"><input type="checkbox" id="meTestMode"> Quick preview</label>' +
      '</div>' +
    '</div>';
  }

  function bindWorkspace() {
    textEl = $("meText"); testEl = $("meTestMode");
    if (textEl) {
      if (textEl.value && ME.editor._aiInstruction && !textEl.value) textEl.value = ME.editor._aiInstruction;
      textEl.addEventListener("input", updateGo);
      if (ME.editor._aiInstruction) textEl.value = ME.editor._aiInstruction;
    }
    var input = $("meFilesInput");
    if (input) input.addEventListener("change", function () { addFiles(this.files); this.value = ""; });
    var composer = $("meComposer");
    if (composer && input) {
      ["dragenter", "dragover"].forEach(function (ev) {
        composer.addEventListener(ev, function (e) { e.preventDefault(); composer.classList.add("drag"); });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        composer.addEventListener(ev, function (e) { e.preventDefault(); composer.classList.remove("drag"); });
      });
      composer.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
      });
    }
  }

  function renderPreview() {
    var p = $("mePreview"); if (!p) return;
    p.setAttribute("data-aspect", ME.aspect);
    var btns = p.querySelectorAll("[data-aspect]");
    if (btns && btns.forEach) btns.forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-aspect") === ME.aspect);
    });
    var v = $("meCanvasVideo");
    var src = "";
    if (ME.mode === "editor") src = ME.editor.resultVideo || ME.editor.sourceVideo || "";
    else {
      for (var i = 0; i < ME.files.length; i++) {
        if (ME.files[i].kind === "video") { src = ME.files[i].url; break; }
      }
    }
    if (v && v.getAttribute("src") !== src) v.setAttribute("src", src);
    var empty = $("mePreviewEmpty"); if (empty) empty.style.display = src ? "none" : "";
    renderCanvasControls();
    bindCanvasVideo();
  }

  function renderEditor() {
    var v = $("meCanvasVideo");
    if (v && ME.editor.resultVideo) {
      v.onloadedmetadata = function () {
        ME.editor.duration = v.duration || 0;
        updateTimeDisplay(); renderTimeline();
      };
    }
    renderTimeline();
  }

  /* ── Timeline strip ─────────────────────────────────────────────────── */
  function collectMarkers(dur) {
    var meta = ME.editor.timeline;
    var out = [];
    if (meta) {
      ["captions", "stickers", "camera", "transitions", "sfx", "broll",
        "slow_motion", "music"].forEach(function (key) {
        (meta[key] || []).forEach(function (b) {
          out.push({at: b.at != null ? b.at : (b.start || 0),
            label: key.replace("_", " ")});
        });
      });
    }
    if (!out.length) {
      out = [
        {at: dur * 0.22, label: "Beat"},
        {at: dur * 0.55, label: "Overlay"},
        {at: dur * 0.82, label: "End"}
      ];
    }
    return out;
  }

  function renderTimeline() {
    var tl = $("meTimeline"); if (!tl) return;
    var dur = ME.editor.duration || (ME.files.length ? 8 : 12);
    var markers = collectMarkers(dur);
    var markerHTML = markers.map(function (m) {
      return '<span class="me-tl-marker" style="left:' + ((m.at / dur) * 100).toFixed(2) +
        '%" title="' + E(m.label) + '"></span>';
    }).join("");
    tl.innerHTML =
      '<div class="me-tl-head"><span>Timeline</span>' +
        '<div class="me-tl-zoom"><button onclick="VMEditing.zoomTimeline(-1)">-</button>' +
        '<button onclick="VMEditing.zoomTimeline(1)">+</button></div></div>' +
      '<div class="me-tl-ruler" id="meTlRuler"></div>' +
      '<div class="me-tl-base"><div class="me-tl-base-track"></div>' + markerHTML + '</div>' +
      '<div class="me-tl-playhead" id="meTlPlayhead"></div>';
    updatePlayhead(0, dur);
  }

  function updatePlayhead(time, dur) {
    var ph = $("meTlPlayhead"), ruler = $("meTlRuler"); if (!ph) return;
    dur = dur || ME.editor.duration || 1;
    var pct = Math.max(0, Math.min(100, (time / dur) * 100));
    ph.style.left = pct.toFixed(2) + "%";
    if (ruler) {
      var zoom = ME.editor.timelineZoom || 1;
      var step = Math.max(1, (dur > 60 ? 10 : dur > 20 ? 5 : 2) / zoom);
      var html = "";
      for (var t = 0; t <= dur; t += step) {
        html += '<span style="left:' + ((t / dur) * 100).toFixed(2) + '%">' + fmtTime(t) + '</span>';
      }
      ruler.innerHTML = html;
    }
  }
  function zoomTimeline(dir) {
    var z = (ME.editor.timelineZoom || 1) + (dir > 0 ? 0.4 : -0.4);
    ME.editor.timelineZoom = Math.max(0.6, Math.min(3, Math.round(z * 10) / 10));
    renderTimeline();
  }

  function renderCanvasControls() {
    var cc = $("meCanvasControls"); if (!cc) return;
    cc.innerHTML =
      '<button onclick="VMEditing.skipBack()" title="Skip back">' + icon("skip-back") + '</button>' +
      '<button id="mePlayBtn" onclick="VMEditing.togglePlay()" title="Play/Pause">' + icon("play") + '</button>' +
      '<button onclick="VMEditing.skipForward()" title="Skip forward">' + icon("skip-forward") + '</button>' +
      '<span class="me-cc-time" id="meTimeDisplay">0:00 / 0:00</span>';
  }
  function bindCanvasVideo() {
    var v = $("meCanvasVideo"); if (!v) return;
    v.ontimeupdate = function () { updateTimeDisplay(); };
    v.onloadedmetadata = function () {
      ME.editor.duration = v.duration || 0;
      updateTimeDisplay();
    };
  }
  function togglePlay() {
    var v = $("meCanvasVideo"); if (!v) return;
    if (v.paused) { v.play(); updatePlayBtn(true); }
    else { v.pause(); updatePlayBtn(false); }
  }
  function updatePlayBtn(playing) {
    var btn = $("mePlayBtn"); if (btn) btn.innerHTML = playing ? icon("pause") : icon("play");
  }
  function skipBack() {
    var v = $("meCanvasVideo"); if (v) v.currentTime = Math.max(0, v.currentTime - 5);
  }
  function skipForward() {
    var v = $("meCanvasVideo"); if (v) v.currentTime = Math.min(v.duration || 0, v.currentTime + 5);
  }
  function updateTimeDisplay() {
    var v = $("meCanvasVideo"), el = $("meTimeDisplay"); if (!v || !el) return;
    el.textContent = fmtTime(v.currentTime) + " / " + fmtTime(v.duration || 0);
    updatePlayhead(v.currentTime, v.duration || 1);
  }
  function fmtTime(s) {
    s = Math.max(0, s || 0);
    var m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return m + ":" + (sec < 10 ? "0" : "") + sec;
  }

  /* ── Drawers ────────────────────────────────────────────────────────── */
  function openDrawer(name) {
    if (name !== "projects" && !DRAWER_TITLES[name]) return;
    ME.drawer = name;
    if (name === "projects") ME.projects = loadProjects();
    renderDrawer(); renderBottomTabs(); syncScrim();
  }
  function closeDrawer() {
    ME.drawer = "";
    var d = $("meDrawer"); if (d) { d.classList.remove("open"); d.innerHTML = ""; }
    renderBottomTabs(); syncScrim();
  }
  function renderDrawer() {
    var d = $("meDrawer"); if (!d) return;
    if (!ME.drawer) { d.classList.remove("open"); d.innerHTML = ""; return; }
    var body = "";
    if (ME.drawer === "effects") body = effectsDrawer();
    else if (ME.drawer === "stickers") body = stickersDrawer();
    else if (ME.drawer === "captions") body = captionsDrawer();
    else if (ME.drawer === "audio") body = audioDrawer();
    else body = projectsDrawer();
    d.innerHTML =
      '<div class="me-drawer-head"><span class="me-drawer-grip"></span>' +
        '<strong>' + E(DRAWER_TITLES[ME.drawer] || "Drawer") + '</strong>' +
        '<button class="me-drawer-close" aria-label="Close" onclick="VMEditing.closeDrawer()">' + icon("x") + '</button></div>' +
      '<div class="me-drawer-body">' + body + '</div>';
    d.classList.add("open");
  }
  function categoryTabs(kind, names, active) {
    return '<div class="me-drawer-cats">' + names.map(function (n) {
      return '<button class="' + (n === active ? 'active' : '') +
        '" onclick="VMEditing.setDrawerCategory(\'' + kind + '\',\'' + E(n).replace(/'/g, "\\'") + '\')">' +
        E(n) + '</button>';
    }).join("") + '</div>';
  }
  function effectsDrawer() {
    var active = ME.drawerCats.effects || EFFECT_GROUPS[0].name;
    var group = EFFECT_GROUPS.filter(function (g) { return g.name === active; })[0] || EFFECT_GROUPS[0];
    return categoryTabs("effects", EFFECT_GROUPS.map(function (g) { return g.name; }), active) +
      '<div class="me-card-grid">' + group.items.map(function (name) {
        var on = (ME.editor.manual.selectedEffects || []).indexOf(name) >= 0;
        return '<button class="me-preset-card' + (on ? ' active' : '') +
          '" onclick="VMEditing.selectEffect(\'' + E(name).replace(/'/g, "\\'") + '\')">' +
          icon("wand-2") + '<span>' + E(name) + '</span>' +
          (on ? '<em class="me-card-check">' + icon("check") + '</em>' : '') + '</button>';
      }).join("") + '</div>';
  }
  function stickersDrawer() {
    var active = ME.drawerCats.stickers || STICKER_GROUPS[0];
    var assets = ME.assetLibrary.filter(function (a) {
      return (a.category || "") === active.toLowerCase() ||
        (active === "My Stickers" && (a.category === "sticker" || a.kind === "image"));
    });
    var cards = '<button class="me-preset-card add" onclick="VMEditing.openAssetPicker(\'' +
      E(active).replace(/'/g, "\\'") + '\')">' + icon("plus") +
      '<span>Add Sticker</span></button>';
    if (assets.length) {
      cards += assets.map(function (a) {
        var url = a.local_path || a.url || "";
        var thumb = a.kind === "video"
          ? '<video src="' + E(url) + CACHE_BUST + '" muted preload="metadata"></video>'
          : '<img src="' + E(url) + CACHE_BUST + '" alt="">';
        return '<button class="me-preset-card asset" onclick="VMEditing.pickSticker(\'' +
          E(a.name || "Sticker").replace(/'/g, "\\'") + '\',\'' + E(url).replace(/'/g, "\\'") + '\')">' +
          thumb + '<span>' + E(a.name || "Sticker") + '</span></button>';
      }).join("");
    } else {
      cards += '<div class="me-empty-slot">Ready for sync</div><div class="me-empty-slot">Ready for sync</div>';
    }
    return categoryTabs("stickers", STICKER_GROUPS, active) +
      '<input type="file" id="meDrawerAssetInput" accept="image/*,video/*" style="display:none" ' +
      'onchange="VMEditing.uploadAssetFiles(this.files)">' +
      '<div class="me-card-grid">' + cards + '</div>';
  }
  function captionsDrawer() {
    var active = ME.editor.manual.captionStyle || CAPTION_STYLES[0];
    return '<p class="me-drawer-hint">Choose how words appear on the video.</p>' +
      '<div class="me-option-list">' + CAPTION_STYLES.map(function (s) {
        return '<button class="me-option' + (s === active ? ' active' : '') +
          '" onclick="VMEditing.setCaptionStyle(\'' + E(s).replace(/'/g, "\\'") + '\')">' +
          icon(s === "Off" ? "x" : "type") + '<span>' + E(s) + '</span>' +
          (s === active ? icon("check") : '') + '</button>';
      }).join("") + '</div>';
  }
  function audioDrawer() {
    var active = ME.editor.manual.audioMode || AUDIO_MODES[0];
    return '<p class="me-drawer-hint">Decide what happens to sound in this edit.</p>' +
      '<div class="me-option-list">' + AUDIO_MODES.map(function (s) {
        return '<button class="me-option' + (s === active ? ' active' : '') +
          '" onclick="VMEditing.setAudioMode(\'' + E(s).replace(/'/g, "\\'") + '\')">' +
          icon("music") + '<span>' + E(s) + '</span>' +
          (s === active ? icon("check") : '') + '</button>';
      }).join("") + '</div>' +
      (active === AUDIO_MODES[1]
        ? '<button class="me-drawer-action" onclick="VMEditing.uploadAudio()">' + icon("upload") +
          ' Add a background track</button>'
        : '');
  }
  function projectsDrawer() {
    if (!ME.projects.length) {
      return '<div class="me-empty-projects">' + icon("folder") +
        '<span>No saved edits yet. Your next Apply creates one.</span></div>';
    }
    return '<div class="me-project-list">' + ME.projects.map(function (p) {
      return '<div class="me-project"><div class="me-project-meta">' +
        '<strong>' + E(p.title || "Untitled edit") + '</strong>' +
        '<span>' + E(p.updated_at || "") + '</span></div>' +
        '<button onclick="VMEditing.openProject(\'' + E(p.job_id) + '\')">Open</button>' +
        '<button class="danger" aria-label="Remove project" onclick="VMEditing.deleteProject(\'' +
        E(p.job_id) + '\')">' + icon("trash") + '</button></div>';
    }).join("") + '</div>';
  }
  function setDrawerCategory(kind, name) {
    ME.drawerCats[kind] = name; renderDrawer();
  }
  function openAssetPicker(category) {
    var normalized = String(category || "sticker").toLowerCase();
    if (normalized === "reactions") normalized = "reaction";
    if (normalized === "sports") normalized = "sticker";
    if (normalized === "tutorial pop-ups") normalized = "sticker";
    ME.assetCategory = normalized;
    var input = $("meDrawerAssetInput"); if (input) input.click();
  }
  function uploadAssetFiles(files) {
    if (!files || !files.length) return;
    var fd = new FormData();
    fd.append("category", ME.assetCategory || "sticker");
    for (var i = 0; i < files.length; i++) {
      if (files[i].size > MAX_FILE_MB * 1024 * 1024) {
        showToast(files[i].name + " is over " + MAX_FILE_MB + "MB."); return;
      }
      fd.append("files", files[i], files[i].name);
    }
    postJSON("/api/editing/assets", {method: "POST", body: fd, timeoutMs: 120000})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.status !== "success") {
          showToast((d && d.errors && d.errors[0]) || "Could not save that item."); return;
        }
        fetchAssets();
        showToast("Saved to your editing library.");
      })
      .catch(function () { showToast("Could not save that item."); });
  }
  function fetchAssets() {
    postJSON("/api/editing/assets", {timeoutMs: 20000})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        ME.assetLibrary = (d && d.assets) || [];
        if (ME.drawer === "stickers") renderDrawer();
      })
      .catch(function () {});
  }
  function fetchStorageStatus() {
    postJSON("/api/editing/storage", {timeoutMs: 10000})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        ME.storageReady = !!(d && d.storage === "r2");
        renderTopbar();
      })
      .catch(function () {});
  }
  function setCaptionStyle(style) {
    ME.editor.manual.captionStyle = style;
    ME.editor.manual.captions = style !== "Off";
    ME.editor.manual.captionScale = style === "Word-by-Word Pop" ? 1.1 : 1;
    pushUndo(); renderDrawer();
  }
  function setAudioMode(mode) {
    ME.editor.manual.audioMode = mode;
    pushUndo(); renderDrawer();
  }
  function selectEffect(name) {
    var m = ME.editor.manual;
    var list = m.selectedEffects || [];
    var pos = list.indexOf(name);
    if (pos >= 0) list.splice(pos, 1); else list.push(name);
    m.selectedEffects = list;
    m.effects = [];
    m.slowmoFactor = 0;
    list.forEach(function (n) {
      var mapping = EFFECT_MAP[n] || {effects: []};
      (mapping.effects || []).forEach(function (e) {
        if (m.effects.indexOf(e) < 0) m.effects.push(e);
      });
      if (mapping.slowmoFactor) m.slowmoFactor = mapping.slowmoFactor;
      if (mapping.camera) m.camera = true;
    });
    pushUndo(); renderDrawer();
  }
  function toggleEffect(name) { selectEffect(name); }

  function syncScrim() {
    var sc = $("meScrim"), d = $("meDrawer"); if (!sc || !d) return;
    sc.classList.toggle("show", isSmall() && d.classList.contains("open"));
  }

  /* ── Projects (local, lightweight) ──────────────────────────────────── */
  function loadProjects() {
    try {
      var raw = window.localStorage.getItem("vmMassiveProjects");
      var list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function saveProjects(list) {
    try { window.localStorage.setItem("vmMassiveProjects", JSON.stringify(list)); } catch (e) {}
  }
  function rememberProject(job, instruction) {
    if (!job || !job.job_id) return;
    var p = {
      job_id: job.job_id,
      title: (instruction || ME.modeName || "Untitled edit").slice(0, 72),
      mode: ME.modeName, aspect: ME.aspect,
      updated_at: new Date().toISOString().slice(0, 16).replace("T", " ")
    };
    var list = loadProjects().filter(function (x) { return x.job_id !== p.job_id; });
    list.unshift(p); saveProjects(list.slice(0, 20)); ME.projects = list.slice(0, 20);
    renderTopbar();
  }
  function deleteProject(jobId) {
    ME.projects = loadProjects().filter(function (p) { return p.job_id !== jobId; });
    saveProjects(ME.projects); renderDrawer();
  }
  function openProject(jobId) {
    if (!jobId) return;
    ME.editor.jobId = jobId; ME.jobId = jobId; ME.mode = "working";
    closeDrawer(); renderBody();
    setWorking("Opening saved edit...", "Checking the latest render.");
    ME.timer = window.setInterval(pollJobToEditor, 4000);
    pollJobToEditor();
  }
  function exportVideo() {
    var url = ME.editor.resultVideo;
    if (!url) { showToast("Finish an edit before exporting."); return; }
    var a = document.createElement("a");
    a.href = url; a.download = "massive-edit.mp4";
    try { a.click(); } catch (e) { window.open(url, "_blank"); }
  }

  /* ── Unified composer: files, voice, submit ─────────────────────────── */
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
  function pickFiles(accept) {
    var input = $("meFilesInput"); if (!input) return;
    try { input.accept = accept || "video/*,image/*,audio/*"; } catch (e) {}
    input.click();
  }
  function addFiles(list) {
    if (!list) return;
    for (var i = 0; i < list.length; i++) {
      var f = list[i]; if (!f || !f.name) continue;
      if (f.size > MAX_FILE_MB * 1024 * 1024) { showToast(f.name + " is over " + MAX_FILE_MB + "MB."); continue; }
      var kind = fileKind(f);
      if (!kind) { showToast(f.name + " isn't a video, audio file, or picture."); continue; }
      ME.files.push({id: Date.now() + "-" + i, kind: kind, name: f.name,
        file: f, url: URL.createObjectURL(f), size: f.size});
    }
    renderUploads(); renderPreview(); renderTimeline(); updateGo();
  }
  function removeFile(id) {
    for (var i = 0; i < ME.files.length; i++) {
      if (ME.files[i].id === id) {
        if (ME.files[i].url) URL.revokeObjectURL(ME.files[i].url);
        ME.files.splice(i, 1); break;
      }
    }
    renderUploads(); renderPreview(); renderTimeline(); updateGo();
  }
  function revokeFiles() {
    ME.files.forEach(function (f) { if (f.url) URL.revokeObjectURL(f.url); });
  }
  function renderUploads() {
    var box = $("meUploads"); if (!box) return;
    box.innerHTML = ME.files.map(function (f) {
      var label = f.kind === "image" ? "Photo" : f.kind === "audio" ? "Audio" : "Video";
      return '<span class="me-attach-chip" title="' + E(f.name) + '">' +
        '<span class="me-chip-kind">' + icon(f.kind === "audio" ? "music" : f.kind === "image" ? "image" : "film") + '</span>' +
        '<span class="me-chip-name">' + E(f.name) + '</span>' +
        '<button aria-label="Remove ' + E(label) + '" onclick="VMEditing.removeFile(\'' + f.id + '\')">' + icon("x") + '</button></span>';
    }).join("");
  }
  function sbUploadMedia(files) { addFiles(files); }

  function renderVoiceBox() {
    var box = $("meVoiceBox"); if (!box) return;
    if (!ME.voice) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "";
    var v = ME.voice;
    box.innerHTML = '<span class="me-vn-label">Voice note</span>' +
      '<audio class="me-vn-audio" controls src="' + E(v.url) + '"></audio>' +
      '<button class="me-vn-clear" onclick="VMEditing.clearVoice()">' +
      (v.transcribing ? "Transcribing..." : v.text ? E(v.text.slice(0, 42)) : "Discard") + '</button>';
    var btn = $("meVoiceBtn");
    if (btn) btn.classList.toggle("recording", ME.rec.active);
  }
  function toggleRec() { if (ME.rec.active) stopRec(); else startRec(); }
  function startRec() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      showToast("Recording isn't supported here -- type instead."); return;
    }
    stopVoicePreview();
    navigator.mediaDevices.getUserMedia({audio: true}).then(function (stream) {
      var mime = "";
      if (typeof window.MediaRecorder.isTypeSupported === "function") {
        mime = window.MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus"
          : window.MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      }
      ME.rec.stream = stream; ME.rec.chunks = [];
      ME.rec.recorder = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined);
      ME.rec.recorder.ondataavailable = function (e) { if (e.data && e.data.size) ME.rec.chunks.push(e.data); };
      ME.rec.recorder.onstop = onRecStopped;
      ME.rec.active = true;
      try { ME.rec.recorder.start(); } catch (e) { ME.rec.active = false; showToast("Couldn't start recording."); return; }
      renderVoiceBox(); showToast("Listening -- tap the mic again when you're done.");
    }).catch(function () { showToast("Microphone unavailable."); });
  }
  function aiRecord() { toggleRec(); }
  function stopRec() {
    if (ME.rec.recorder && ME.rec.recorder.state !== "inactive") {
      try { ME.rec.recorder.stop(); } catch (e) {}
    }
    ME.rec.active = false;
    if (ME.rec.stream) { ME.rec.stream.getTracks().forEach(function (t) { t.stop(); }); ME.rec.stream = null; }
    renderVoiceBox();
  }
  function stopVoicePreview() {
    document.querySelectorAll(".me-vn-audio").forEach(function (a) { try { a.pause(); } catch (e) {} });
  }
  function onRecStopped() {
    var mime = (ME.rec.recorder && ME.rec.recorder.mimeType) || "audio/webm";
    var blob = new Blob(ME.rec.chunks, {type: mime});
    ME.rec.chunks = []; ME.rec.recorder = null; ME.rec.active = false;
    if (!blob.size) { renderVoiceBox(); return; }
    if (ME.voice && ME.voice.url) URL.revokeObjectURL(ME.voice.url);
    ME.voice = {blob: blob, url: URL.createObjectURL(blob), text: "", transcribing: true, err: ""};
    renderVoiceBox(); transcribeVoice(blob);
  }
  function transcribeVoice(blob) {
    var fd = new FormData(); fd.append("audio", blob, "voice-note.webm");
    postJSON("/api/editing/transcribe", {method: "POST", body: fd, timeoutMs: 180000})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        ME.voice.transcribing = false;
        if (d && d.status === "success" && (d.text || "").trim()) ME.voice.text = d.text.trim();
        else ME.voice.err = (d && d.message) || "Couldn't transcribe.";
        renderVoiceBox(); updateGo();
      })
      .catch(function () { ME.voice.transcribing = false; ME.voice.err = "Couldn't connect."; renderVoiceBox(); updateGo(); });
  }
  function clearVoice(revoke) {
    stopRec(); stopVoicePreview();
    if (ME.voice && ME.voice.url) URL.revokeObjectURL(ME.voice.url);
    ME.voice = null; renderVoiceBox(); updateGo();
    if (revoke !== true) { var box = $("meVoiceBox"); if (box) box.style.display = "none"; }
  }
  function updateGo() {
    var go = $("meGoBtn"); if (!go) return;
    var instruction = (textEl && textEl.value || "").trim() ||
      (ME.voice && (ME.voice.text || "").trim()) || (ME.voice && ME.voice.blob ? true : false);
    var ok = !!(instruction && (hasVideo(ME.files) || ME.editor.jobId));
    go.disabled = !ok;
    go.title = ok ? "Start AI Edit" : "Describe your edit and attach a video first.";
  }

  function instructionText() {
    return (textEl && textEl.value || "").trim() || (ME.voice && (ME.voice.text || "").trim()) || "";
  }
  function submit() {
    if (ME.timer) return;
    var text = instructionText();
    if (!text && !(ME.voice && ME.voice.blob)) { showToast("Describe the edit you want first."); return; }
    if (ME.mode === "editor" && ME.editor.jobId && !hasVideo(ME.files)) {
      refineWithInstruction(text); return;
    }
    if (!hasVideo(ME.files)) { showToast("Attach a video clip to edit first."); return; }
    stopRec(); stopVoicePreview();
    ME.mode = "working"; renderBody();
    setWorking("Uploading your media...", "Sending the clip, instruction and attachments.");
    var fd = new FormData();
    var sentVid = false;
    ME.files.forEach(function (f) {
      if (f.kind === "video" && !sentVid) { fd.append("video", f.file, f.name); sentVid = true; }
      else fd.append("media", f.file, f.name);
    });
    if (text) fd.append("instruction", text);
    if (ME.voice && (ME.voice.text || "").trim()) fd.append("voice_transcript", ME.voice.text.trim());
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
    postJSON("/api/editing/run", {method: "POST", body: fd, timeoutMs: 150000})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.status !== "success" || !d.job) {
          setWorking("Couldn't start", (d && d.message) || "Please try again.");
          scheduleRecover(); return;
        }
        ME.jobId = d.job.job_id; ME.start = Date.now();
        rememberProject(d.job, text);
        setWorking("Planning your edit...", "Reading your instruction and deciding what to do.");
        ME.timer = window.setInterval(poll, 4000);
        poll();
      })
      .catch(function () { setWorking("Upload failed", "Check your connection and try again."); scheduleRecover(); });
  }
  function scheduleRecover() {
    window.setTimeout(function () {
      if (ME.mode === "working" && !ME.jobId) { ME.mode = "intro"; renderBody(); updateGo(); }
    }, 3500);
  }
  function poll() {
    if (!ME.jobId) return;
    postJSON("/api/studio/job/" + encodeURIComponent(ME.jobId), {timeoutMs: 20000})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var job = d && d.job; if (!job) return;
        if (job.edit_plan && job.edit_plan.length) renderPlan(job);
        if (ME.mode !== "working") return;
        if (job.status === "running" || job.status === "queued") {
          var secs = ME.start ? Math.round((Date.now() - ME.start) / 1000) : 0;
          var line = job.stalled ? "Still getting ready -- this can take a moment."
            : "Running in the background -- " + secs + "s in.";
          setWorking(stageLabel(job.edit_plan_stage || ""), line);
          if (job.edit_plan_note) { var n = $("mePlanNote"); if (n) n.textContent = job.edit_plan_note; }
          return;
        }
        if (ME.timer) { window.clearInterval(ME.timer); ME.timer = null; }
        if (job.status === "done" && job.final_video) {
          ME.editor.jobId = job.job_id;
          ME.editor.resultVideo = job.final_video;
          ME.editor.stats = job.stats || {};
          ME.editor.timeline = job.edit_timeline || null;
          rememberProject(job, job.instruction || instructionText());
          enterEditor();
        } else {
          ME.jobFailMsg = job.error || "Editing didn't finish -- try another clip.";
          ME.mode = "intro"; renderBody(); showToast(ME.jobFailMsg);
        }
      })
      .catch(function () {});
  }
  function pollJobToEditor() {
    if (!ME.editor.jobId) return;
    postJSON("/api/studio/job/" + encodeURIComponent(ME.editor.jobId), {timeoutMs: 20000})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var job = d && d.job; if (!job) return;
        if (job.edit_plan && job.edit_plan.length) renderPlan(job);
        if (ME.mode !== "working") return;
        if (job.status === "running" || job.status === "queued") {
          setWorking(stageLabel(job.edit_plan_stage || ""), "Re-rendering in the background...");
          return;
        }
        if (ME.timer) { window.clearInterval(ME.timer); ME.timer = null; }
        if (job.status === "done" && job.final_video) {
          ME.editor.resultVideo = job.final_video;
          ME.editor.stats = job.stats || {};
          ME.editor.jobId = job.job_id;
          ME.editor.timeline = job.edit_timeline || null;
          enterEditor(); showToast("Updated video is ready.");
        } else { ME.mode = "editor"; renderBody(); showToast(job.error || "The update didn't finish."); }
      })
      .catch(function () {});
  }
  function enterEditor() {
    ME.mode = "editor"; renderBody();
    var v = $("meCanvasVideo");
    if (v && ME.editor.resultVideo) {
      v.onloadedmetadata = function () {
        ME.editor.duration = v.duration || 0; updateTimeDisplay(); renderTimeline();
      };
    }
    renderPreview(); renderTimeline();
  }
  function renderPlan(job) {
    var list = $("mePlanList"); if (!list) return;
    var steps = (job.edit_plan || []).filter(function (s) { return s && s.step; });
    var current = STAGES.indexOf(job.edit_plan_stage || "");
    var doneN = Math.min(current > 0 ? current : 0, steps.length);
    list.innerHTML = steps.map(function (s, i) {
      return '<div class="me-plan-item' + (i < doneN ? ' done' : '') + '">' +
        '<span class="me-pic"></span>' + E(s.step) + '</div>';
    }).join("");
  }
  function stageLabel(stage) {
    var map = {analysis: "Analyzing your video...", planned: "Planning your edit...",
      broll: "Adding B-roll...", sticker: "Applying the sticker...",
      "slow-motion": "Applying slow motion...", camera: "Applying camera effects...",
      transitions: "Adding transitions...", sfx: "Adding sound effects...",
      music: "Mixing in the music...", done: "Wrapping up..."};
    return map[stage] || (stage ? "Editing..." : "Planning your edit...");
  }
  function setWorking(main, sub) {
    if (ME.mode !== "working") return;
    if (main) { var ph = $("mePhase"); if (ph) ph.textContent = main; }
    if (sub) { var sb = $("meSub"); if (sb) sb.textContent = sub; }
  }
  function goToInput() { closeDrawer(); ME.mode = "intro"; renderBody(); updateGo(); }
  function refine() {
    if (ME.timer) { window.clearInterval(ME.timer); ME.timer = null; }
    ME.jobId = ""; ME.mode = "intro"; renderBody(); updateGo();
  }

  /* ── Refinement ─────────────────────────────────────────────────────── */
  function buildManualPayload() {
    var m = ME.editor.manual;
    var music = null;
    if (m.audioMode === AUDIO_MODES[1] && m.musicUrl) {
      music = {url: m.musicUrl, name: m.musicName || "Background track",
        volume: m.musicVolume || 0.3, fade_in: m.musicFadeIn || 0, fade_out: m.musicFadeOut || 0};
    }
    return {
      canvas: {aspect: ME.aspect || "9:16", mode: m.canvas.mode || "fill",
        bg: m.canvas.bg || "000000", reframe: m.canvas.reframe || "smart"},
      trim: {start: m.trim.start || 0, end: m.trim.end || 0},
      speed: m.speed || 1, rotate: m.rotate || 0,
      flip_h: !!m.flipH, flip_v: !!m.flipV, reverse: !!m.reverse,
      crop: m.crop || {}, resize: m.resize || 1,
      captions: m.captions !== false,
      title: m.title || "", title_seconds: m.titleSeconds || 3,
      caption_align: m.captionAlign || "lower", caption_scale: m.captionScale || 1,
      effects: m.effects || [], fade_in: m.fadeIn || 0, fade_out: m.fadeOut || 0,
      music: music,
      sticker: {scale: m.stickerScale || 0.28, angle: m.stickerAngle || 0,
        duration: m.stickerDuration || 3, pos: ME.stickerPos || "br",
        sticker_position: ME.stickerPos || "br", anim: m.stickerAnim || ""},
      slowmo_factor: m.slowmoFactor || 0, auto_cut: m.autoCut !== false,
      intensity: m.intensity || "medium", transitions_mode: m.transitionsMode || "auto",
      camera: m.camera !== false, sfx: m.sfx !== false
    };
  }
  function refineWithInstruction(instruction) {
    if (!ME.editor.jobId) { showToast("Edit a clip first, then refine it."); return; }
    pushUndo();
    var body = {job_id: ME.editor.jobId, manual: buildManualPayload(),
      instruction: instruction || "", keep_plan: !instruction};
    postJSON("/api/editing/refine", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body), timeoutMs: 60000
    }).then(function (r) { return r.json(); })
      .then(function (d) { handleRefineResult(d, instruction); })
      .catch(function () { showToast("Could not reach the editing service."); });
  }
  function applyAIEdit() {
    if (textEl) ME.editor._aiInstruction = textEl.value;
    var instruction = instructionText();
    if (!instruction && !(ME.voice && ME.voice.blob)) { showToast("Describe the change you want first."); return; }
    refineWithInstruction(instruction);
  }
  function applyManual() { refineWithInstruction(""); }
  function handleRefineResult(d, instruction) {
    if (!d || d.status !== "success" || !d.job) {
      showToast((d && d.message) || "Could not apply changes."); return;
    }
    if (d.job.job_id) ME.editor.jobId = d.job.job_id;
    ME.jobId = ME.editor.jobId; ME.start = Date.now(); ME.mode = "working";
    renderBody(); setWorking("Applying your changes...", "Re-rendering with the updated settings.");
    ME.timer = window.setInterval(pollJobToEditor, 4000); pollJobToEditor();
    showToast(instruction ? "Instruction applied." : "Changes applied.");
  }
  function pushUndo() {
    ME.undoStack.push(JSON.stringify(ME.editor.manual));
    if (ME.undoStack.length > 50) ME.undoStack.shift();
    ME.redoStack = [];
  }
  function undoAction() {
    if (!ME.undoStack.length) { showToast("Nothing to undo."); return; }
    ME.redoStack.push(JSON.stringify(ME.editor.manual));
    ME.editor.manual = JSON.parse(ME.undoStack.pop()); renderDrawer();
  }
  function redoAction() {
    if (!ME.redoStack.length) { showToast("Nothing to redo."); return; }
    ME.undoStack.push(JSON.stringify(ME.editor.manual));
    ME.editor.manual = JSON.parse(ME.redoStack.pop()); renderDrawer();
  }

  /* ── Small state helpers / legacy-compatible API ───────────────────── */
  function setMode(v) {
    if (MODES.indexOf(v) < 0) return;
    ME.modeName = v; renderTopbar(); showToast("Mode set to " + v + ".");
  }
  function setAspect(v) {
    if (v !== "16:9" && v !== "9:16") return;
    ME.aspect = v; ME.editor.manual.canvas.aspect = v; renderPreview();
  }
  function setManual(path, value) {
    var m = ME.editor.manual, parts = String(path || "").split(".");
    if (parts.length === 1) {
      if (["speed", "rotate", "fadeIn", "fadeOut", "stickerScale", "stickerAngle",
        "stickerDuration", "musicVolume", "musicFadeIn", "musicFadeOut", "slowmoFactor"].indexOf(parts[0]) >= 0) {
        m[parts[0]] = parseFloat(value) || 0;
      } else if (["flipH", "flipV", "reverse", "captions", "camera", "sfx"].indexOf(parts[0]) >= 0) {
        m[parts[0]] = !!value;
      } else m[parts[0]] = value;
    } else if (parts[0] === "trim") m.trim[parts[1]] = parseFloat(value) || 0;
    else if (parts[0] === "crop") m.crop[parts[1]] = parseFloat(value) || 0;
    renderDrawer();
  }
  function toggleAITool(id, on) {
    if (id === "auto-cut") ME.editor.manual.autoCut = !!on;
    else if (id === "auto-captions") ME.editor.manual.captions = !!on;
  }
  function setCanvasAspect(v) { setAspect(v); }
  function setCanvasMode(v) { ME.editor.manual.canvas.mode = v === "fit" ? "fit" : "fill"; }
  function setCanvasBg(v) { ME.editor.manual.canvas.bg = String(v || "").replace("#", ""); }
  function smartReframe() { setAspect("9:16"); showToast("Reframed to 9:16."); }
  function aiSuggestTransitions() { ME.editor.manual.transitionsMode = "auto"; showToast("Transitions set to auto."); }
  function addTextLayer() { showToast("Text layers arrive with the next drawer phase."); }
  function removeTextLayer() {}
  function updateTextLayer() {}
  function uploadVoiceOver(files) { if (files && files.length) addFiles(files); }
  function uploadAudio() { ME.assetCategory = "music"; var i = $("meDrawerAssetInput"); if (i) { i.accept = "audio/*"; i.click(); } }
  function removeMusic() { ME.editor.manual.musicUrl = ""; ME.editor.manual.musicName = ""; renderDrawer(); }
  function uploadCustomSticker(files) {
    ME.assetCategory = "sticker";
    if (files && files.length) uploadAssetFiles(files);
    else openAssetPicker("My Stickers");
  }
  function pickEditorSticker(url, name) { pickSticker(name, url); }
  function filterStickers(q) {
    var needle = String(q || "").toLowerCase();
    ME.assetLibrary = ME.assetLibrary.filter(function (a) {
      return !needle || String(a.name || "").toLowerCase().indexOf(needle) >= 0;
    });
    if (ME.drawer === "stickers") renderDrawer();
  }
  function pickSticker(name, url) {
    if (!url) return;
    ME.sticker = {name: name || "Sticker", url: url};
    ME.editor.recentStickers.unshift({name: ME.sticker.name, url: url});
    ME.editor.recentStickers = ME.editor.recentStickers.slice(0, 8);
    showToast("Sticker selected: " + ME.sticker.name);
    closeDrawer();
  }
  function setPos(v) { ME.stickerPos = v; }
  function railClick(id) {
    if (id === "media") { goToInput(); var c = $("meComposer"); if (c) c.scrollIntoView && c.scrollIntoView({behavior: "smooth", block: "center"}); return; }
    if (id === "stickers" || id === "effects" || id === "audio") openDrawer(id);
    else if (id === "text" || id === "filters" || id === "adjustments") openDrawer("captions");
  }
  function openTool(id) { railClick(id); }
  function closeTool() { closeDrawer(); }
  function toggleSection(id) { railClick(id); }
  function toggleAIPanel(force) { if (force === true) openDrawer("captions"); else closeDrawer(); }
  function status(main, sub) { setWorking(main, sub); }

  var API = {
    launch: launch, hide: hide, onShow: onShow, reset: reset,
    status: status, refine: refine,
    toggleRec: toggleRec, clearVoice: clearVoice,
    pickSticker: pickSticker, setPos: setPos,
    removeFile: removeFile, submit: submit, toDashboard: toDashboard,
    toggleSection: toggleSection, setManual: setManual,
    toggleEffect: toggleEffect, toggleAITool: toggleAITool,
    setCanvasAspect: setCanvasAspect, setCanvasMode: setCanvasMode, setCanvasBg: setCanvasBg,
    applyAIEdit: applyAIEdit, applyManual: applyManual, aiRecord: aiRecord,
    sbUploadMedia: sbUploadMedia, pickEditorSticker: pickEditorSticker,
    filterStickers: filterStickers, uploadCustomSticker: uploadCustomSticker,
    uploadAudio: uploadAudio, removeMusic: removeMusic, uploadVoiceOver: uploadVoiceOver,
    smartReframe: smartReframe, aiSuggestTransitions: aiSuggestTransitions,
    addTextLayer: addTextLayer, removeTextLayer: removeTextLayer, updateTextLayer: updateTextLayer,
    undoAction: undoAction, redoAction: redoAction,
    togglePlay: togglePlay, skipBack: skipBack, skipForward: skipForward,
    zoomTimeline: zoomTimeline, goToInput: goToInput,
    pickFiles: pickFiles, railClick: railClick, openTool: openTool, closeTool: closeTool,
    toggleAIPanel: toggleAIPanel, closeSheets: closeDrawer,
    openDrawer: openDrawer, closeDrawer: closeDrawer,
    setDrawerCategory: setDrawerCategory, setMode: setMode, setAspect: setAspect,
    selectEffect: selectEffect, setCaptionStyle: setCaptionStyle, setAudioMode: setAudioMode,
    openAssetPicker: openAssetPicker, uploadAssetFiles: uploadAssetFiles,
    exportVideo: exportVideo, openProject: openProject, deleteProject: deleteProject
  };
  window.VMEditing = API;

  function initOnce() { init(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initOnce);
  else initOnce();
})();
