/* ValleyMind — Massive Editing (Professional Sidebar Editor)
   -----------------------------------------------------------
   Studio → Music Studio → Massive Editing. The user uploads footage,
   types or records a voice instruction, picks a sticker, then hits
   "Start AI Edit". The AI creates the edit; the professional sidebar
   then exposes every manual control the backend supports.

   Modes: intro → working → editor
   Sidebar sections: AI Edit | Edit | Text | Stickers | Effects |
                     Audio | Transitions | Canvas | AI Tools
*/
(function () {
  "use strict";

  var CACHE_BUST = "?v=2";
  var MAX_FILE_MB = 100;
  var STAGES = ["planned","broll","sticker","slow-motion","music","done"];
  var POSITIONS = [
    {v:"tl",l:"Top left"},{v:"tr",l:"Top right"},
    {v:"center",l:"Center"},{v:"bl",l:"Bottom left"},{v:"br",l:"Bottom right"}
  ];
  var EFFECT_LIST = ["bw","vignette","blur","boost","sepia","saturated","fade_in","fade_out"];
  var CANVAS_PRESETS = [
    {label:"9:16",v:"9:16",sub:"Vertical"},{label:"16:9",v:"16:9",sub:"Horizontal"},
    {label:"1:1",v:"1:1",sub:"Square"}
  ];
  var FONTS = ["Arial","Helvetica","Georgia","Impact","Courier New","Verdana","Trebuchet MS"];
  var SIDEBAR_SECTIONS = [
    {id:"ai-edit",icon:"sparkles",label:"AI Edit"},
    {id:"edit",icon:"scissors",label:"Edit"},
    {id:"text",icon:"type",label:"Text"},
    {id:"stickers",icon:"smile-plus",label:"Stickers"},
    {id:"effects",icon:"wand-2",label:"Effects"},
    {id:"audio",icon:"volume-2",label:"Audio"},
    {id:"transitions",icon:"flip-horizontal",label:"Transitions"},
    {id:"canvas",icon:"frame",label:"Canvas"},
    {id:"ai-tools",icon:"brain",label:"AI Tools"}
  ];

  var ME = {
    rendered: false, mode: "intro",
    files: [], voice: null, sticker: null, stickerPos: "br",
    jobId: "", start: 0, timer: null,
    rec: {stream:null, recorder:null, chunks:[], active:false},
    undoStack: [], redoStack: [],
    editor: {
      jobId: "", sourceVideo: "", resultVideo: "",
      timeline: null, stats: null, duration: 0,
      openSection: "ai-edit",
      stickerLibrary: [], recentStickers: [], uploadedStickers: [],
      manual: {
        canvas: {aspect:"9:16",mode:"fill",bg:"000000"},
        trim: {start:0, end:null}, speed: 1.0, rotate: 0,
        flipH: false, flipV: false, reverse: false,
        crop: {left:0,right:0,top:0,bottom:0}, resize: 1.0,
        captions: true, title: "", titleSeconds: 3,
        captionAlign: "lower", captionScale: 1.0,
        effects: [], fadeIn: 0, fadeOut: 0,
        musicUrl: "", musicName: "", musicVolume: 0.3,
        musicFadeIn: 0, musicFadeOut: 0,
        stickerScale: 0.28, stickerAngle: 0, stickerDuration: 3,
        stickerPos: "br", stickerAnim: "",
        slowmoFactor: 0, autoCut: true,
      _aiInstruction: "", timelineZoom: 1,
        textLayers: []
      }
    }
  };

  var rootEl = null, textEl = null, testEl = null;

  function E(s) {
    return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function $(id) { return document.getElementById(id); }

  function apiHeaders(o) {
    if (typeof authHeaders === "function") return authHeaders(o);
    return o || {};
  }
  function postJSON(url, opts) {
    if (typeof apiFetch === "function") return apiFetch(url, opts);
    var c = new AbortController();
    var t = window.setTimeout(function(){c.abort();}, (opts&&opts.timeoutMs)||30000);
    return fetch((typeof apiUrl==="function"?apiUrl(url):url), {
      method: opts.method||"GET",
      headers: apiHeaders((opts&&opts.headers)||{}),
      body: opts.body, credentials: "include", signal: c.signal
    }).then(function(r){window.clearTimeout(t);return r;});
  }

  function svgIcon(name) {
    var icons = {
      sparkles:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/><path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/></svg>',
      scissors:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/></svg>',
      type:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" x2="15" y1="20" y2="20"/><line x1="12" x2="12" y1="4" y2="20"/></svg>',
      'smile-plus':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" x2="9.01" y1="9" y2="9"/><line x1="15" x2="15.01" y1="9" y2="9"/><path d="M16 5h6v6"/></svg>',
      'wand-2':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72Z"/><path d="m14 7 3 3"/></svg>',
      'volume-2':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>',
      'flip-horizontal':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v14c0 1.1.9 2 2 2h3"/><path d="M16 3h3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-3"/><line x1="12" x2="12" y1="20" y2="4"/></svg>',
      frame:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg>',
      brain:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M12 5v14"/></svg>',
      play:'<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
      pause:'<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>',
      'skip-back':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" x2="5" y1="19" y2="5"/></svg>',
      'skip-forward':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" x2="19" y1="5" y2="19"/></svg>',
      'chevron-right':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>',
      undo:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/></svg>',
      redo:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3L21 13"/></svg>',
      download:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>',
      refresh:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>',
      'mic':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>',
      'stop-circle':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><rect x="9" y="9" width="6" height="6"/></svg>',
      upload:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>'
    };
    return icons[name] || "";
  }
  function icon(name, cls) {
    return '<span class="me-sb-icon' + (cls ? ' ' + cls : '') + '">' + svgIcon(name) + '</span>';
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
    ME.undoStack = []; ME.redoStack = [];
    revokeFiles(); ME.files = []; clearVoice(true);
    if (textEl) textEl.value = "";
    if (testEl) testEl.checked = false;
    ME.mode = "intro"; renderBody(); updateGo();
  }
  function toDashboard() {
    stopRec(); stopVoicePreview();
    if (typeof studioShowDashboard === "function") studioShowDashboard();
    else if (typeof openStudio === "function") openStudio();
  }

  function showToast(msg) {
    if (!msg) return;
    var t = $("meToast");
    if (t) { t.textContent = msg; t.style.display = "";
      window.setTimeout(function(){if(t)t.style.display="none";}, 5200); }
    else { try{alert(msg);}catch(e){} }
  }

  /* ── Render main ────────────────────────────────────────────────────── */
  function init() {
    if (ME.rendered) { if(rootEl)rootEl.style.display=""; updateGo(); return; }
    rootEl = $("meRoot"); if (!rootEl) return;
    ME.rendered = true;
    renderShell(); renderBody();
    fetchStickers();
    document.addEventListener("visibilitychange", function(){if(document.hidden)stopRec();});
  }

  function renderShell() {
    rootEl.innerHTML =
      '<div class="me-topbar">' +
      '  <h2>Massive Editing</h2>' +
      '  <span class="me-sub" id="meTopSub">Instruction -> plan -> rendered short</span>' +
      '  <button class="me-close" onclick="VMEditing.toDashboard()">&larr; Back to Studio</button>' +
      '</div>' +
      '<div class="me-body"><div class="me-inner" id="meInner"></div></div>' +
      '<div class="me-editor" id="meEditor"></div>';
  }

  function renderBody() {
    var inner = $("meInner");
    var editor = $("meEditor");
    if (ME.mode === "working") { if(inner){inner.style.display="";inner.innerHTML=workingHTML();} if(editor)editor.style.display="none"; return; }
    if (ME.mode === "editor") { if(inner)inner.style.display="none"; renderEditor(); return; }
    if (editor) editor.style.display = "none";
    if (inner) { inner.style.display=""; inner.innerHTML = introHTML(); bindIntro(); renderUploads(); renderVoiceBox(); renderStickerSel(); updateGo();
      if(ME.files.length&&!hasVideo(ME.files))showToast("Add a video clip -- that's what gets edited."); }
  }

  function introHTML() {
    return '<div id="meToast" class="me-toast" style="display:none"></div>' +
      '<div class="me-card">' +
      '  <div class="me-q">What do you want AI to do with this clip?</div>' +
      '  <p class="me-sub">Type it or say it. Like: "Hype reel -- captions on, slow-mo the celebration, fire sticker when he scores, use the uploaded beat as music."</p>' +
      '  <textarea id="meText" class="me-text" placeholder="Tell AI how you want this video edited..."></textarea>' +
      '  <div class="me-voice-row">' +
      '    <button id="meVoiceBtn" class="me-btn" onclick="VMEditing.toggleRec()">'+icon("mic")+' Record voice instruction</button>' +
      '    <span class="me-sub">Speech is transcribed into the instruction.</span>' +
      '  </div>' +
      '  <div id="meVoiceBox" style="display:none"></div>' +
      '</div>' +
      '<div class="me-card">' +
      '  <div class="me-q">Source footage &amp; media</div>' +
      '  <p class="me-sub">The first video is what gets edited. Add extra shots, images or sounds too.</p>' +
      '  <div id="meUploadDrop" class="me-upload-drop">' +
      '    <div class="me-u-icon">'+icon("upload")+'</div>' +
      '    <div class="me-u-title">Tap or drag files in</div>' +
      '    <div class="me-u-sub">video / image / audio -- up to '+MAX_FILE_MB+'MB each</div>' +
      '    <input type="file" id="meFilesInput" accept="video/*,image/*,audio/*" multiple style="display:none">' +
      '  </div>' +
      '  <div id="meUploads" class="me-uploads"></div>' +
      '</div>' +
      '<div class="me-card">' +
      '  <div class="me-q">Sticker (optional)</div>' +
      '  <p class="me-sub">Pick one and say when it should appear. The AI places it at the right moment.</p>' +
      '  <div id="meStickers" class="me-sticker-row"><span class="me-sub">Loading sticker library...</span></div>' +
      '  <div id="mePosRow" class="me-pos-row" style="display:none"></div>' +
      '  <div id="meStickerSel"></div>' +
      '</div>' +
      '<div class="me-start-bar">' +
      '  <button id="meGoBtn" class="me-go" onclick="VMEditing.submit()" disabled>'+icon("sparkles")+' Start AI Edit</button>' +
      '  <label class="me-testmode"><input type="checkbox" id="meTestMode"> Test mode (skip AI B-roll)</label>' +
      '</div>';
  }

  function workingHTML() {
    return '<div class="me-plan">' +
      '  <div class="me-plan-title">'+icon("sparkles")+' AI Edit Plan</div>' +
      '  <div id="mePlanList"><div class="me-plan-item"><span class="me-pic"></span>Reading your instruction...</div></div>' +
      '  <div id="mePlanNote" class="me-plan-note"></div>' +
      '</div>' +
      '<div class="me-progress">' +
      '  <div class="me-spinner"></div>' +
      '  <div class="me-phase" id="mePhase">Planning your edit...</div>' +
      '  <div class="me-sub" id="meSub"></div>' +
      '</div>';
  }

  /* ── Editor Layout ──────────────────────────────────────────────────── */
  function renderEditor() {
    var ed = $("meEditor");
    if (!ed) return;
    var e = ME.editor;
    ed.innerHTML =
      '<div class="me-editor-topbar">' +
      '  <h2>Massive Editing</h2>' +
      '  <div class="me-etb-actions">' +
      '    <button class="me-etb-btn" onclick="VMEditing.undoAction()" title="Undo">'+icon("undo")+' Undo</button>' +
      '    <button class="me-etb-btn" onclick="VMEditing.redoAction()" title="Redo">'+icon("redo")+' Redo</button>' +
      '    <button class="me-etb-btn" onclick="VMEditing.goToInput()">New Instruction</button>' +
      '    <button class="me-etb-btn primary" onclick="VMEditing.applyManual()">'+icon("refresh")+' Apply Changes</button>' +
      '    ' + (e.resultVideo ? '<a class="me-etb-btn primary" href="'+E(e.resultVideo)+'" download="massive-edit.mp4">'+icon("download")+' Export</a>' : '') +
      '  </div>' +
      '</div>' +
      '<div class="me-editor-body">' +
      '  <div class="me-sidebar" id="meSidebar"></div>' +
      '  <div class="me-workspace">' +
      '    <div class="me-canvas-wrap" id="meCanvasWrap">' +
      '      <div class="me-canvas-container" id="meCanvasContainer">' +
      '        <video id="meCanvasVideo" class="me-canvas-video" playsinline preload="metadata"></video>' +
      '      </div>' +
      '      <div class="me-canvas-controls" id="meCanvasControls"></div>' +
      '    </div>' +
      '    <div class="me-timeline" id="meTimeline"></div>' +
      '  </div>' +
      '</div>';
    renderSidebar();
    renderCanvasControls();
    renderTimeline();
    bindCanvasVideo();
    if (typeof lucide !== "undefined") { try{lucide.createIcons();}catch(e){} }
  }

  function renderSidebar() {
    var sb = $("meSidebar"); if (!sb) return;
    var html = "";
    SIDEBAR_SECTIONS.forEach(function(sec) {
      var isOpen = ME.editor.openSection === sec.id;
      html += '<div class="me-sb-section' + (isOpen ? " open" : "") + '" data-sb="' + sec.id + '">' +
        '<div class="me-sb-header" onclick="VMEditing.toggleSection(\'' + sec.id + '\')">' +
        icon(sec.icon) +
        '<span class="me-sb-label">' + sec.label + '</span>' +
        icon("chevron-right", "me-sb-arrow") +
        '</div>' +
        '<div class="me-sb-body" id="meSbBody_' + sec.id + '">' +
        (isOpen ? renderSidebarSection(sec.id) : '') +
        '</div></div>';
    });
    sb.innerHTML = html;
  }

  function toggleSection(id) {
    ME.editor.openSection = (ME.editor.openSection === id) ? "" : id;
    renderSidebar();
  }

  function renderSidebarSection(id) {
    switch(id) {
      case "ai-edit": return renderAIEditSection();
      case "edit": return renderEditSection();
      case "text": return renderTextSection();
      case "stickers": return renderStickersSection();
      case "effects": return renderEffectsSection();
      case "audio": return renderAudioSection();
      case "transitions": return renderTransitionsSection();
      case "canvas": return renderCanvasSection();
      case "ai-tools": return renderAIToolsSection();
    }
    return "";
  }

  /* ── AI Edit Section ────────────────────────────────────────────────── */
  function renderAIEditSection() {
    var m = ME.editor.manual;
    return '<div class="me-sb-label-sm">What do you want AI to do?</div>' +
      '<textarea id="meSbAIInput" class="me-sb-textarea" placeholder="Tell AI how you want this video edited..." style="min-height:100px">' + E(ME.editor._aiInstruction || '') + '</textarea>' +
      '<div class="me-sb-row" style="margin-top:10px">' +
      '  <button class="me-sb-btn" onclick="VMEditing.aiRecord()" id="meSbAIRecBtn">'+icon("mic")+' Voice</button>' +
      '  <button class="me-sb-btn primary" onclick="VMEditing.applyAIEdit()">Apply AI Edit</button>' +
      '</div>' +
      '<div id="meSbAIRecBox" style="display:none"></div>' +
      '<div class="me-sb-label-sm" style="margin-top:12px">Upload Media</div>' +
      '<div class="me-sb-upload-zone" onclick="$(\'meSbMediaInput\').click()">' +
      '  <div class="me-sb-uz-text">'+icon("upload")+' Upload video, image, or audio</div>' +
      '  <input type="file" id="meSbMediaInput" accept="video/*,image/*,audio/*" multiple style="display:none" onchange="VMEditing.sbUploadMedia(this.files)">' +
      '</div>' +
      (ME.files.length ? '<div style="margin-top:8px;color:#64748b;font-size:11px">'+ME.files.length+' file(s) attached</div>' : '');
  }

  /* ── Edit Section ───────────────────────────────────────────────────── */
  function renderEditSection() {
    var m = ME.editor.manual;
    var speed = m.speed || 1.0;
    var rotate = m.rotate || 0;
    return '<div class="me-sb-label-sm">Trim</div>' +
      '<div class="me-sb-row"><label>Start</label>' +
      '  <input type="range" class="me-sb-slider" min="0" max="' + (ME.editor.duration||60) + '" step="0.1" value="' + (m.trim.start||0) + '" oninput="VMEditing.setManual(\'trim.start\',this.value)">' +
      '  <span class="me-sb-val" id="meTrimStart">' + (m.trim.start||0).toFixed(1) + 's</span></div>' +
      '<div class="me-sb-row"><label>End</label>' +
      '  <input type="range" class="me-sb-slider" min="0" max="' + (ME.editor.duration||60) + '" step="0.1" value="' + (m.trim.end||ME.editor.duration||60) + '" oninput="VMEditing.setManual(\'trim.end\',this.value)">' +
      '  <span class="me-sb-val" id="meTrimEnd">' + (m.trim.end||ME.editor.duration||0).toFixed(1) + 's</span></div>' +
      '<div class="me-sb-label-sm">Speed</div>' +
      '<div class="me-sb-row"><label>Speed</label>' +
      '  <input type="range" class="me-sb-slider" min="0.5" max="2.0" step="0.1" value="' + speed + '" oninput="VMEditing.setManual(\'speed\',this.value)">' +
      '  <span class="me-sb-val" id="meSpeedVal">' + speed.toFixed(1) + 'x</span></div>' +
      '<div class="me-sb-label-sm">Transform</div>' +
      '<div class="me-sb-chips">' +
      '  <span class="me-sb-chip' + (rotate===0?' active':'') + '" onclick="VMEditing.setManual(\'rotate\',0)">0&deg;</span>' +
      '  <span class="me-sb-chip' + (rotate===90?' active':'') + '" onclick="VMEditing.setManual(\'rotate\',90)">90&deg;</span>' +
      '  <span class="me-sb-chip' + (rotate===180?' active':'') + '" onclick="VMEditing.setManual(\'rotate\',180)">180&deg;</span>' +
      '  <span class="me-sb-chip' + (rotate===270?' active':'') + '" onclick="VMEditing.setManual(\'rotate\',270)">270&deg;</span>' +
      '</div>' +
      '<div class="me-sb-row" style="margin-top:6px">' +
      '  <label class="me-sb-toggle"><input type="checkbox" '+(m.flipH?'checked':'')+' onchange="VMEditing.setManual(\'flipH\',this.checked)"><span>Flip H</span></label>' +
      '  <label class="me-sb-toggle"><input type="checkbox" '+(m.flipV?'checked':'')+' onchange="VMEditing.setManual(\'flipV\',this.checked)"><span>Flip V</span></label>' +
      '  <label class="me-sb-toggle"><input type="checkbox" '+(m.reverse?'checked':'')+' onchange="VMEditing.setManual(\'reverse\',this.checked)"><span>Reverse</span></label>' +
      '</div>' +
      '<div class="me-sb-label-sm">Crop</div>' +
      '<div class="me-sb-row"><label>Left</label><input type="range" class="me-sb-slider" min="0" max="0.3" step="0.01" value="'+(m.crop.left||0)+'" oninput="VMEditing.setManual(\'crop.left\',this.value)"><span class="me-sb-val">'+((m.crop.left||0)*100).toFixed(0)+'%</span></div>' +
      '<div class="me-sb-row"><label>Right</label><input type="range" class="me-sb-slider" min="0" max="0.3" step="0.01" value="'+(m.crop.right||0)+'" oninput="VMEditing.setManual(\'crop.right\',this.value)"><span class="me-sb-val">'+((m.crop.right||0)*100).toFixed(0)+'%</span></div>' +
      '<div class="me-sb-row"><label>Top</label><input type="range" class="me-sb-slider" min="0" max="0.3" step="0.01" value="'+(m.crop.top||0)+'" oninput="VMEditing.setManual(\'crop.top\',this.value)"><span class="me-sb-val">'+((m.crop.top||0)*100).toFixed(0)+'%</span></div>' +
      '<div class="me-sb-row"><label>Bottom</label><input type="range" class="me-sb-slider" min="0" max="0.3" step="0.01" value="'+(m.crop.bottom||0)+'" oninput="VMEditing.setManual(\'crop.bottom\',this.value)"><span class="me-sb-val">'+((m.crop.bottom||0)*100).toFixed(0)+'%</span></div>' +
      '<div class="me-sb-label-sm">Resize</div>' +
      '<div class="me-sb-row"><label>Scale</label><input type="range" class="me-sb-slider" min="0.4" max="1.5" step="0.05" value="'+(m.resize||1)+'" oninput="VMEditing.setManual(\'resize\',this.value)"><span class="me-sb-val">'+(m.resize||1).toFixed(1)+'x</span></div>';
  }

  /* ── Text Section ───────────────────────────────────────────────────── */
  function renderTextSection() {
    var m = ME.editor.manual;
    return '<div class="me-sb-label-sm">Captions</div>' +
      '<label class="me-sb-toggle"><input type="checkbox" '+(m.captions!==false?'checked':'')+' onchange="VMEditing.setManual(\'captions\',this.checked)"><span>Enable captions</span></label>' +
      '<div class="me-sb-row"><label>Align</label>' +
      '  <select class="me-sb-select" onchange="VMEditing.setManual(\'captionAlign\',this.value)">' +
      '    <option value="lower"'+(m.captionAlign==="lower"?" selected":"")+'>Lower</option>' +
      '    <option value="center"'+(m.captionAlign==="center"?" selected":"")+'>Center</option>' +
      '    <option value="upper"'+(m.captionAlign==="upper"?" selected":"")+'>Upper</option>' +
      '  </select></div>' +
      '<div class="me-sb-row"><label>Scale</label><input type="range" class="me-sb-slider" min="0.7" max="1.6" step="0.1" value="'+(m.captionScale||1)+'" oninput="VMEditing.setManual(\'captionScale\',this.value)"><span class="me-sb-val">'+(m.captionScale||1).toFixed(1)+'x</span></div>' +
      '<div class="me-sb-label-sm">Title</div>' +
      '<input type="text" class="me-sb-input" placeholder="Title text (shown at top)" value="'+E(m.title||'')+'" oninput="VMEditing.setManual(\'title\',this.value)">' +
      '<div class="me-sb-row" style="margin-top:6px"><label>Duration</label><input type="range" class="me-sb-slider" min="0.8" max="20" step="0.5" value="'+(m.titleSeconds||3)+'" oninput="VMEditing.setManual(\'titleSeconds\',this.value)"><span class="me-sb-val">'+(m.titleSeconds||3)+'s</span></div>' +
      '<div class="me-sb-label-sm" style="margin-top:12px">Text Layers</div>' +
      '<button class="me-sb-btn" onclick="VMEditing.addTextLayer()">'+icon("type")+' Add Text Layer</button>' +
      '<div id="meTextLayers" style="margin-top:8px"></div>';
  }

  function renderTextLayers() {
    var box = $("meTextLayers"); if (!box) return;
    var layers = ME.editor.manual.textLayers || [];
    if (!layers.length) { box.innerHTML = '<span style="color:#64748b;font-size:11px">No text layers yet</span>'; return; }
    var html = "";
    layers.forEach(function(tl, i) {
      html += '<div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:8px;margin-bottom:6px">' +
        '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">' +
        '<input type="text" class="me-sb-input" style="flex:1" value="'+E(tl.text||'')+'" oninput="VMEditing.updateTextLayer('+i+',\'text\',this.value)" placeholder="Text...">' +
        '<button class="me-sb-btn sm danger" onclick="VMEditing.removeTextLayer('+i+')">X</button></div>' +
        '<div class="me-sb-row"><label>Font</label><select class="me-sb-select" onchange="VMEditing.updateTextLayer('+i+',\'font\',this.value)">' +
        FONTS.map(function(f){return '<option value="'+f+'"'+(tl.font===f?" selected":"")+'>'+f+'</option>';}).join("") +
        '</select></div>' +
        '<div class="me-sb-row"><label>Size</label><input type="range" class="me-sb-slider" min="12" max="72" value="'+(tl.size||24)+'" oninput="VMEditing.updateTextLayer('+i+',\'size\',this.value)"><span class="me-sb-val">'+(tl.size||24)+'px</span></div>' +
        '<div class="me-sb-row"><label>Pos Y</label><input type="range" class="me-sb-slider" min="0" max="100" value="'+(tl.y||50)+'" oninput="VMEditing.updateTextLayer('+i+',\'y\',this.value)"><span class="me-sb-val">'+(tl.y||50)+'%</span></div>' +
        '</div>';
    });
    box.innerHTML = html;
  }

  /* ── Stickers Section ───────────────────────────────────────────────── */
  function renderStickersSection() {
    var m = ME.editor.manual;
    var sel = ME.sticker;
    return '<div class="me-sb-label-sm">Recently Used</div>' +
      '<div class="me-sb-recent" id="meSbRecent">' +
      (ME.editor.recentStickers.length ? ME.editor.recentStickers.map(function(s){
        return '<img src="'+E(s.url)+CACHE_BUST+'" title="'+E(s.name)+'" onclick="VMEditing.pickEditorSticker(\''+E(s.url)+'\',\''+E(s.name)+'\')"'+(sel&&sel.url===s.url?' class="selected"':'')+'>';
      }).join("") : '<span style="color:#475569;font-size:11px">None yet</span>') +
      '</div>' +
      '<div class="me-sb-label-sm">Sticker Library</div>' +
      '<input type="text" class="me-sb-input" placeholder="Search stickers..." oninput="VMEditing.filterStickers(this.value)" style="margin-bottom:8px">' +
      '<div class="me-sb-grid" id="meSbStickerGrid">' +
      (ME.editor.stickerLibrary.length ? ME.editor.stickerLibrary.map(function(s){
        return '<img class="me-sb-sticker'+(sel&&sel.url===s.url?' selected':'')+'" src="'+E(s.url)+CACHE_BUST+'" title="'+E(s.name)+'" onclick="VMEditing.pickEditorSticker(\''+E(s.url)+'\',\''+E(s.name)+'\')" loading="lazy">';
      }).join("") : '<span style="color:#64748b;font-size:11px">Loading...</span>') +
      '</div>' +
      (sel ? '<div style="margin-top:8px;color:#00d4ff;font-size:11px;font-weight:700">Selected: '+E(sel.name)+'</div>' : '') +
      '<div class="me-sb-label-sm" style="margin-top:10px">Upload Custom Sticker</div>' +
      '<div class="me-sb-upload-zone" onclick="$(\'meSbStickerUpload\').click()">' +
      '  <div class="me-sb-uz-text">Upload a custom sticker and use it in your edit.</div>' +
      '  <input type="file" id="meSbStickerUpload" accept="image/*" style="display:none" onchange="VMEditing.uploadCustomSticker(this.files)">' +
      '</div>' +
      (ME.editor.uploadedStickers.length ? '<div class="me-sb-grid" style="margin-top:8px">' +
        ME.editor.uploadedStickers.map(function(s){
          return '<img class="me-sb-sticker'+(sel&&sel.url===s.url?' selected':'')+'" src="'+E(s.url)+CACHE_BUST+'" title="'+E(s.name)+'" onclick="VMEditing.pickEditorSticker(\''+E(s.url)+'\',\''+E(s.name)+'\')">';
        }).join("") + '</div>' : '') +
      '<div class="me-sb-label-sm" style="margin-top:10px">Sticker Settings</div>' +
      '<div class="me-sb-row"><label>Position</label><select class="me-sb-select" onchange="VMEditing.setManual(\'stickerPos\',this.value)">' +
      POSITIONS.map(function(p){return '<option value="'+p.v+'"'+(m.stickerPos===p.v?' selected':'')+'>'+p.l+'</option>';}).join("") +
      '</select></div>' +
      '<div class="me-sb-row"><label>Size</label><input type="range" class="me-sb-slider" min="0.08" max="0.9" step="0.02" value="'+(m.stickerScale||0.28)+'" oninput="VMEditing.setManual(\'stickerScale\',this.value)"><span class="me-sb-val">'+((m.stickerScale||0.28)*100).toFixed(0)+'%</span></div>' +
      '<div class="me-sb-row"><label>Rotation</label><input type="range" class="me-sb-slider" min="-180" max="180" step="5" value="'+(m.stickerAngle||0)+'" oninput="VMEditing.setManual(\'stickerAngle\',this.value)"><span class="me-sb-val">'+(m.stickerAngle||0)+'&deg;</span></div>' +
      '<div class="me-sb-row"><label>Duration</label><input type="range" class="me-sb-slider" min="0.5" max="20" step="0.5" value="'+(m.stickerDuration||3)+'" oninput="VMEditing.setManual(\'stickerDuration\',this.value)"><span class="me-sb-val">'+(m.stickerDuration||3)+'s</span></div>' +
      '<label class="me-sb-toggle" style="margin-top:4px"><input type="checkbox" '+(m.stickerAnim==="pop"?"checked":"")+' onchange="VMEditing.setManual(\'stickerAnim\',this.checked?\'pop\':\'\')"><span>Pop-in animation</span></label>';
  }

  /* ── Effects Section ────────────────────────────────────────────────── */
  function renderEffectsSection() {
    var m = ME.editor.manual;
    var active = m.effects || [];
    var labels = {bw:"Black & White",vignette:"Vignette",blur:"Blur",boost:"Color Boost",
      sepia:"Sepia",saturated:"Saturated",fade_in:"Fade In",fade_out:"Fade Out"};
    return '<div class="me-sb-label-sm">Visual Effects</div>' +
      '<div class="me-sb-chips">' +
      EFFECT_LIST.map(function(e){
        return '<span class="me-sb-chip'+(active.indexOf(e)>=0?' active':'')+'" onclick="VMEditing.toggleEffect(\''+e+'\')">'+(labels[e]||e)+'</span>';
      }).join("") +
      '</div>' +
      '<div class="me-sb-label-sm" style="margin-top:12px">Fade</div>' +
      '<div class="me-sb-row"><label>Fade In</label><input type="range" class="me-sb-slider" min="0" max="5" step="0.1" value="'+(m.fadeIn||0)+'" oninput="VMEditing.setManual(\'fadeIn\',this.value)"><span class="me-sb-val">'+(m.fadeIn||0)+'s</span></div>' +
      '<div class="me-sb-row"><label>Fade Out</label><input type="range" class="me-sb-slider" min="0" max="5" step="0.1" value="'+(m.fadeOut||0)+'" oninput="VMEditing.setManual(\'fadeOut\',this.value)"><span class="me-sb-val">'+(m.fadeOut||0)+'s</span></div>';
  }

  /* ── Audio Section ──────────────────────────────────────────────────── */
  function renderAudioSection() {
    var m = ME.editor.manual;
    return '<div class="me-sb-label-sm">Music</div>' +
      '<div class="me-sb-upload-zone" onclick="$(\'meSbAudioUpload\').click()">' +
      '  <div class="me-sb-uz-text">'+icon("upload")+' Upload music or sound effect</div>' +
      '  <input type="file" id="meSbAudioUpload" accept="audio/*" style="display:none" onchange="VMEditing.uploadAudio(this.files)">' +
      '</div>' +
      (m.musicUrl ? '<div style="margin-top:8px;color:#00d4ff;font-size:11px;font-weight:700">'+E(m.musicName||"Music")+' <button class="me-sb-btn sm danger" onclick="VMEditing.removeMusic()">Remove</button></div>' : '') +
      '<div class="me-sb-row" style="margin-top:10px"><label>Volume</label><input type="range" class="me-sb-slider" min="0" max="1" step="0.05" value="'+(m.musicVolume||0.3)+'" oninput="VMEditing.setManual(\'musicVolume\',this.value)"><span class="me-sb-val">'+((m.musicVolume||0.3)*100).toFixed(0)+'%</span></div>' +
      '<div class="me-sb-row"><label>Fade In</label><input type="range" class="me-sb-slider" min="0" max="10" step="0.5" value="'+(m.musicFadeIn||0)+'" oninput="VMEditing.setManual(\'musicFadeIn\',this.value)"><span class="me-sb-val">'+(m.musicFadeIn||0)+'s</span></div>' +
      '<div class="me-sb-row"><label>Fade Out</label><input type="range" class="me-sb-slider" min="0" max="10" step="0.5" value="'+(m.musicFadeOut||0)+'" oninput="VMEditing.setManual(\'musicFadeOut\',this.value)"><span class="me-sb-val">'+(m.musicFadeOut||0)+'s</span></div>' +
      '<div class="me-sb-label-sm" style="margin-top:8px">Voice-over</div>' +
      '<div class="me-sb-upload-zone" onclick="$(\'meSbVOUpload\').click()">' +
      '  <div class="me-sb-uz-text">'+icon("mic")+' Upload voice-over recording</div>' +
      '  <input type="file" id="meSbVOUpload" accept="audio/*" style="display:none" onchange="VMEditing.uploadVoiceOver(this.files)">' +
      '</div>';
  }

  /* ── Transitions Section ────────────────────────────────────────────── */
  function renderTransitionsSection() {
    return '<div class="me-sb-label-sm">Transition Style</div>' +
      '<div class="me-sb-chips">' +
      '  <span class="me-sb-chip active">Hard Cut</span>' +
      '  <span class="me-sb-chip" style="opacity:0.5;cursor:not-allowed">Crossfade (soon)</span>' +
      '  <span class="me-sb-chip" style="opacity:0.5;cursor:not-allowed">Fade to Black (soon)</span>' +
      '  <span class="me-sb-chip" style="opacity:0.5;cursor:not-allowed">Wipe (soon)</span>' +
      '</div>' +
      '<p style="color:#64748b;font-size:11px;margin-top:8px;line-height:1.5">The current render pipeline uses hard cuts between segments. Additional transition styles will be added as the rendering engine evolves.</p>' +
      '<div class="me-sb-label-sm" style="margin-top:12px">AI Suggest</div>' +
      '<button class="me-sb-btn" onclick="VMEditing.aiSuggestTransitions()">'+icon("sparkles")+' Let AI choose transitions</button>';
  }

  /* ── Canvas Section ─────────────────────────────────────────────────── */
  function renderCanvasSection() {
    var m = ME.editor.manual;
    var c = m.canvas || {aspect:"9:16",mode:"fill",bg:"000000"};
    return '<div class="me-sb-label-sm">Aspect Ratio</div>' +
      '<div class="me-sb-chips">' +
      CANVAS_PRESETS.map(function(p){
        return '<span class="me-sb-chip'+(c.aspect===p.v?' active':'')+'" onclick="VMEditing.setCanvasAspect(\''+p.v+'\')">'+p.label+' <span style="color:#64748b;font-size:9px">'+p.sub+'</span></span>';
      }).join("") +
      '</div>' +
      '<div class="me-sb-label-sm" style="margin-top:10px">Size Mode</div>' +
      '<div class="me-sb-chips">' +
      '  <span class="me-sb-chip'+(c.mode==='fill'?' active':'')+'" onclick="VMEditing.setCanvasMode(\'fill\')">Fill (crop)</span>' +
      '  <span class="me-sb-chip'+(c.mode==='fit'?' active':'')+'" onclick="VMEditing.setCanvasMode(\'fit\')">Fit (letterbox)</span>' +
      '</div>' +
      '<div class="me-sb-row" style="margin-top:10px"><label>Background</label>' +
      '  <input type="color" value="#'+E(c.bg||'000000')+'" style="width:32px;height:28px;border:none;background:none;cursor:pointer" oninput="VMEditing.setCanvasBg(this.value)">' +
      '  <span class="me-sb-val">#'+E(c.bg||'000000')+'</span></div>' +
      '<div class="me-sb-label-sm" style="margin-top:12px">Smart Reframe</div>' +
      '<button class="me-sb-btn" onclick="VMEditing.smartReframe()">'+icon("sparkles")+' Auto-reframe to 9:16</button>' +
      '<p style="color:#64748b;font-size:11px;margin-top:6px;line-height:1.5">Centers the most important area of the video for vertical format.</p>';
  }

  /* ── AI Tools Section ───────────────────────────────────────────────── */
  function renderAIToolsSection() {
    var tools = [
      {id:"auto-cut",label:"Auto Cut",desc:"Automatically removes silences and filler words.",active:ME.editor.manual.autoCut!==false},
      {id:"auto-captions",label:"Auto Captions",desc:"Generates word-by-word animated captions.",active:ME.editor.manual.captions!==false}
    ];
    return '<div class="me-sb-label-sm">AI-Powered Tools</div>' +
      tools.map(function(t){
        return '<div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:10px;margin-bottom:8px">' +
          '<label class="me-sb-toggle"><input type="checkbox" '+(t.active?'checked':'')+' onchange="VMEditing.toggleAITool(\''+t.id+'\',this.checked)"><span style="font-weight:700">'+t.label+'</span></label>' +
          '<p style="color:#64748b;font-size:11px;margin:4px 0 0;line-height:1.4">'+t.desc+'</p></div>';
      }).join("") +
      '<div class="me-sb-label-sm" style="margin-top:8px">More Tools</div>' +
      '<p style="color:#475569;font-size:11px;line-height:1.5">Highlight Detection, Scene Detection, Beat Sync, Background Removal, and Object Tracking are planned for future releases and will connect to the real backend when available.</p>';
  }

  /* ── Canvas Controls (play/pause/skip) ──────────────────────────────── */
  function renderCanvasControls() {
    var cc = $("meCanvasControls"); if (!cc) return;
    cc.innerHTML =
      '<button class="me-cc-btn" onclick="VMEditing.skipBack()" title="Skip back">'+icon("skip-back")+'</button>' +
      '<button class="me-cc-btn" onclick="VMEditing.togglePlay()" id="mePlayBtn" title="Play/Pause">'+icon("play")+'</button>' +
      '<button class="me-cc-btn" onclick="VMEditing.skipForward()" title="Skip forward">'+icon("skip-forward")+'</button>' +
      '<span class="me-cc-time" id="meTimeDisplay">0:00 / 0:00</span>';
  }

  function bindCanvasVideo() {
    var v = $("meCanvasVideo"); if (!v) return;
    if (ME.editor.resultVideo) v.src = ME.editor.resultVideo;
    else if (ME.editor.sourceVideo) v.src = ME.editor.sourceVideo;
    v.ontimeupdate = function() { updateTimeDisplay(); };
    v.onloadedmetadata = function() {
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
    var btn = $("mePlayBtn"); if (!btn) return;
    btn.innerHTML = playing ? icon("pause") : icon("play");
  }
  function skipBack() {
    var v = $("meCanvasVideo"); if (!v) return;
    v.currentTime = Math.max(0, v.currentTime - 5);
  }
  function skipForward() {
    var v = $("meCanvasVideo"); if (!v) return;
    v.currentTime = Math.min(v.duration || 0, v.currentTime + 5);
  }
  function updateTimeDisplay() {
    var v = $("meCanvasVideo"), el = $("meTimeDisplay"); if (!v||!el) return;
    el.textContent = fmtTime(v.currentTime) + " / " + fmtTime(v.duration||0);
    updatePlayhead(v.currentTime, v.duration||1);
  }
  function fmtTime(s) {
    s = Math.max(0, s||0);
    var m = Math.floor(s/60), sec = Math.floor(s%60);
    return m + ":" + (sec<10?"0":"") + sec;
  }

  /* ── Timeline ───────────────────────────────────────────────────────── */
  function renderTimeline() {
    var tl = $("meTimeline"); if (!tl) return;
    var dur = ME.editor.duration || 10;
    var tlMeta = ME.editor.timeline;
    tl.innerHTML =
      '<div class="me-tl-toolbar">' +
      '  <span>Timeline</span>' +
      '  <div class="me-tl-zoom">' +
      '    <button onclick="VMEditing.zoomTimeline(-1)">-</button>' +
      '    <button onclick="VMEditing.zoomTimeline(1)">+</button>' +
      '  </div>' +
      '</div>' +
      '<div class="me-tl-ruler" id="meTlRuler"></div>' +
      '<div class="me-tl-tracks" id="meTlTracks"></div>';
    renderTimelineTracks(dur, tlMeta);
  }

  function renderTimelineTracks(dur, meta) {
    var tracks = $("meTlTracks"); if (!tracks) return;
    var tracks_def = [
      {id:"video",label:"Video",color:"#00d4ff",blocks: meta ? meta.video : [{start:0,end:dur}]},
      {id:"captions",label:"Captions",color:"#10b981",blocks: meta ? meta.captions : []},
      {id:"stickers",label:"Stickers",color:"#f59e0b",blocks: meta ? meta.stickers : []},
      {id:"broll",label:"B-Roll",color:"#a855f7",blocks: meta ? meta.broll : []},
      {id:"slowmo",label:"Slow-mo",color:"#ef4444",blocks: meta ? meta.slow_motion : []},
      {id:"music",label:"Music",color:"#3b82f6",blocks: meta ? meta.music : []}
    ];
    var html = '<div class="me-tl-playhead" id="meTlPlayhead"></div>';
    tracks_def.forEach(function(tr) {
      html += '<div class="me-tl-track">' +
        '<div class="me-tl-track-label">'+tr.label+'</div>' +
        '<div class="me-tl-track-content">';
      (tr.blocks||[]).forEach(function(b) {
        var left = ((b.start||0)/dur*100).toFixed(2);
        var width = (((b.end||b.start||0)-(b.start||0))/dur*100).toFixed(2);
        html += '<div class="me-tl-block '+tr.id+'" style="left:'+left+'%;width:'+Math.max(0.5,width)+'%" title="'+fmtTime(b.start)+' - '+fmtTime(b.end)+'"></div>';
      });
      html += '</div></div>';
    });
    tracks.innerHTML = html;
  }

  function updatePlayhead(time, dur) {
    var ph = $("meTlPlayhead"), ruler = $("meTlRuler"); if (!ph) return;
    dur = dur || ME.editor.duration || 1;
    var pct = (time/dur*100).toFixed(2);
    ph.style.left = "calc(72px + (100% - 72px) * " + pct + " / 100)";
    if (ruler) {
      var rhtml = "";
      var zoom = ME.editor.timelineZoom || 1;
      var step = Math.max(0.5, (dur > 60 ? 10 : dur > 20 ? 5 : dur > 10 ? 2 : 1) / zoom);
      for (var t = 0; t <= dur; t += step) {
        var p = (t/dur*100).toFixed(1);
        rhtml += '<span class="me-tl-ruler-label" style="left:calc(72px + (100% - 72px) * '+p+' / 100)">'+fmtTime(t)+'</span>';
      }
      ruler.innerHTML = rhtml;
    }
  }

  function zoomTimeline(dir) {
    var z = (ME.editor.timelineZoom || 1) + (dir > 0 ? 0.4 : -0.4);
    z = Math.max(0.6, Math.min(3, Math.round(z*10)/10));
    ME.editor.timelineZoom = z;
    renderTimeline();
  }

  /* ── Manual overrides (single setManual for all controls) ────────────── */
  function setManual(path, value) {
    var m = ME.editor.manual;
    var parts = path.split(".");
    if (parts.length === 1) {
      if (path==="speed") m.speed = parseFloat(value)||1;
      else if (path==="rotate") m.rotate = parseInt(value)||0;
      else if (path==="flipH") m.flipH = !!value;
      else if (path==="flipV") m.flipV = !!value;
      else if (path==="reverse") m.reverse = !!value;
      else if (path==="resize") m.resize = parseFloat(value)||1;
      else if (path==="fadeIn") m.fadeIn = parseFloat(value)||0;
      else if (path==="fadeOut") m.fadeOut = parseFloat(value)||0;
      else if (path==="captions") m.captions = !!value;
      else if (path==="title") m.title = String(value||"");
      else if (path==="titleSeconds") m.titleSeconds = parseFloat(value)||3;
      else if (path==="captionAlign") m.captionAlign = String(value||"lower");
      else if (path==="captionScale") m.captionScale = parseFloat(value)||1;
      else if (path==="stickerPos") m.stickerPos = String(value||"br");
      else if (path==="stickerScale") m.stickerScale = parseFloat(value)||0.28;
      else if (path==="stickerAngle") m.stickerAngle = parseFloat(value)||0;
      else if (path==="stickerDuration") m.stickerDuration = parseFloat(value)||3;
      else if (path==="stickerAnim") m.stickerAnim = String(value||"");
      else if (path==="slowmoFactor") m.slowmoFactor = parseFloat(value)||0;
      else if (path==="musicVolume") m.musicVolume = parseFloat(value)||0;
      else if (path==="musicFadeIn") m.musicFadeIn = parseFloat(value)||0;
      else if (path==="musicFadeOut") m.musicFadeOut = parseFloat(value)||0;
      else m[path] = value;
    } else if (parts[0]==="trim") {
      if (parts[1]==="start") m.trim.start = parseFloat(value)||0;
      else if (parts[1]==="end") m.trim.end = parseFloat(value)||null;
    } else if (parts[0]==="crop") {
      m.crop[parts[1]] = parseFloat(value)||0;
    }
    refreshSidebarValues();
  }

  function refreshSidebarValues() {
    var m = ME.editor.manual;
    var els = {
      meTrimStart: (m.trim.start||0).toFixed(1)+"s",
      meTrimEnd: (m.trim.end||ME.editor.duration||0).toFixed(1)+"s",
      meSpeedVal: (m.speed||1).toFixed(1)+"x"
    };
    Object.keys(els).forEach(function(k){ var e=$(k); if(e)e.textContent=els[k]; });
    renderTextLayers();
  }

  function toggleEffect(name) {
    var m = ME.editor.manual;
    var idx = m.effects.indexOf(name);
    if (idx >= 0) m.effects.splice(idx, 1);
    else m.effects.push(name);
    renderSidebarSection("effects");
  }

  function toggleAITool(id, on) {
    if (id==="auto-cut") ME.editor.manual.autoCut = !!on;
    else if (id==="auto-captions") ME.editor.manual.captions = !!on;
  }

  function setCanvasAspect(v) { ME.editor.manual.canvas.aspect = v; renderSidebarSection("canvas"); }
  function setCanvasMode(v) { ME.editor.manual.canvas.mode = v; renderSidebarSection("canvas"); }
  function setCanvasBg(v) { ME.editor.manual.canvas.bg = v.replace("#",""); renderSidebarSection("canvas"); }

  function addTextLayer() {
    if (!ME.editor.manual.textLayers) ME.editor.manual.textLayers = [];
    ME.editor.manual.textLayers.push({text:"New text",font:"Arial",size:24,y:50});
    renderTextLayers();
  }
  function removeTextLayer(i) {
    ME.editor.manual.textLayers.splice(i, 1);
    renderTextLayers();
  }
  function updateTextLayer(i, key, val) {
    ME.editor.manual.textLayers[i][key] = key==="size"||key==="y" ? parseInt(val)||0 : val;
  }

  /* ── Sticker management ─────────────────────────────────────────────── */
  function pickEditorSticker(url, name) {
    ME.sticker = {url:url, name:name||"sticker"};
    var m = ME.editor.manual;
    m.stickerPos = m.stickerPos || "br";
    renderSidebarSection("stickers");
  }

  function filterStickers(q) {
    q = (q||"").toLowerCase();
    var grid = $("meSbStickerGrid"); if (!grid) return;
    var filtered = q ? ME.editor.stickerLibrary.filter(function(s){
      return (s.name||"").toLowerCase().indexOf(q)>=0;
    }) : ME.editor.stickerLibrary;
    grid.innerHTML = filtered.map(function(s){
      return '<img class="me-sb-sticker'+(ME.sticker&&ME.sticker.url===s.url?' selected':'')+'" src="'+E(s.url)+CACHE_BUST+'" title="'+E(s.name)+'" onclick="VMEditing.pickEditorSticker(\''+E(s.url)+'\',\''+E(s.name)+'\')" loading="lazy">';
    }).join("");
  }

  function uploadCustomSticker(files) {
    if (!files||!files.length) return;
    var f = files[0];
    if (f.size > MAX_FILE_MB*1024*1024) { showToast(f.name+" is over "+MAX_FILE_MB+"MB."); return; }
    var url = URL.createObjectURL(f);
    ME.editor.uploadedStickers.push({url:url, name:f.name});
    pickEditorSticker(url, f.name);
    renderSidebarSection("stickers");
  }

  function uploadAudio(files) {
    if (!files||!files.length) return;
    var f = files[0];
    var url = URL.createObjectURL(f);
    ME.editor.manual.musicUrl = url;
    ME.editor.manual.musicName = f.name;
    ME.editor.manual.musicVolume = 0.3;
    renderSidebarSection("audio");
  }

  function removeMusic() {
    ME.editor.manual.musicUrl = "";
    ME.editor.manual.musicName = "";
    renderSidebarSection("audio");
  }

  function uploadVoiceOver(files) { showToast("Voice-over upload recorded. Will be mixed in the next render."); }
  function sbUploadMedia(files) {
    if (!files) return;
    for (var i=0;i<files.length;i++) addFiles([files[i]]);
    renderSidebarSection("ai-edit");
  }

  function smartReframe() {
    ME.editor.manual.canvas.aspect = "9:16";
    ME.editor.manual.canvas.mode = "fill";
    setManual("canvas", ME.editor.manual.canvas);
    renderSidebarSection("canvas");
    showToast("Smart reframe set to 9:16 vertical fill.");
  }

  function aiSuggestTransitions() {
    showToast("AI will suggest transitions based on the edit plan when re-rendered.");
  }

  /* ── Undo / Redo ────────────────────────────────────────────────────── */
  function pushUndo() {
    ME.undoStack.push(JSON.stringify(ME.editor.manual));
    if (ME.undoStack.length > 50) ME.undoStack.shift();
    ME.redoStack = [];
  }
  function undoAction() {
    if (!ME.undoStack.length) { showToast("Nothing to undo."); return; }
    ME.redoStack.push(JSON.stringify(ME.editor.manual));
    ME.editor.manual = JSON.parse(ME.undoStack.pop());
    renderSidebar();
  }
  function redoAction() {
    if (!ME.redoStack.length) { showToast("Nothing to redo."); return; }
    ME.undoStack.push(JSON.stringify(ME.editor.manual));
    ME.editor.manual = JSON.parse(ME.redoStack.pop());
    renderSidebar();
  }

  /* ── Apply AI Edit (new instruction from sidebar) ───────────────────── */
  function applyAIEdit() {
    var input = $("meSbAIInput");
    var instruction = (input && input.value || "").trim();
    var voiceText = (ME.voice && (ME.voice.text||"").trim()) || "";
    if (!instruction && !voiceText && !(ME.voice && ME.voice.blob)) {
      showToast("Type or record an AI instruction first.");
      return;
    }
    if (!ME.editor.jobId && !ME.editor.sourceVideo) {
      showToast("Upload a video clip to edit first.");
      return;
    }
    pushUndo();
    if (typeof apiFetch === "function") {
      apiFetch("/api/editing/refine",{method:"POST",body:JSON.stringify({
        job_id: ME.editor.jobId, manual: buildManualPayload(), instruction: instruction||voiceText, keep_plan: !instruction&&!voiceText
      })}).then(function(r){return r.json();}).then(function(d){ handleRefineResult(d, instruction); });
      return;
    }
    postJSON("/api/editing/refine",{
      method:"POST", headers: {}, timeoutMs: 60000,
      body: JSON.stringify({
        job_id: ME.editor.jobId, manual: buildManualPayload(), instruction: instruction||voiceText, keep_plan: !instruction&&!voiceText
      })
    }).then(function(r){return r.json();}).then(function(d){ handleRefineResult(d, instruction); });
  }

  function applyManual() {
    P("apply"), pushUndo();
    if (!ME.editor.jobId) { showToast("No previous edit to refine."); return; }
    var payload = {
      job_id: ME.editor.jobId, manual: buildManualPayload(), instruction: "", keep_plan: true
    };
    postJSON("/api/editing/refine",{method:"POST",timeoutMs:60000,body:JSON.stringify(payload)})
      .then(function(r){return r.json();})
      .then(function(d){ handleRefineResult(d, ""); })
      .catch(function(){ showToast("Could not reach the refine service."); });
  }

  function P(unused){}

  function buildManualPayload() {
    var m = ME.editor.manual;
    return {
      canvas: m.canvas || {aspect:"9:16",mode:"fill",bg:"000000"},
      trim: {start: m.trim.start||0, end: m.trim.end||0},
      speed: m.speed||1,
      rotate: m.rotate||0,
      flip_h: !!m.flipH, flip_v: !!m.flipV,
      reverse: !!m.reverse,
      crop: m.crop || {left:0,right:0,top:0,bottom:0},
      resize: m.resize||1,
      captions: true, title: m.title||"", title_seconds: m.titleSeconds||3,
      caption_align: m.captionAlign||"lower", caption_scale: m.captionScale||1,
      effects: m.effects||[], fade_in: m.fadeIn||0, fade_out: m.fadeOut||0,
      music: { url: "", name: "", volume: 0.3, fade_in: 0, fade_out: 0 },
      sticker: {
        scale: m.stickerScale||0.28, angle: m.stickerAngle||0,
        duration: m.stickerDuration||3, pos: m.stickerPos||"br", anim: m.stickerAnim||""
      },
      slowmo_factor: m.slowmoFactor||0, auto_cut: m.autoCut!==false
    };
  }

  function handleRefineResult(d, newInstruction) {
    if (!d || d.status !== "success" || !d.job) {
      showToast((d&&d.message)||"Could not apply changes.");
      return;
    }
    var job = d.job;
    var manualMsg = newInstruction ? "AI instruction applied." : "Manual changes applied.";
    if (job.job_id) { ME.editor.jobId = job.job_id; }
    enterWorking(job);
    showToast(manualMsg);
  }

  function aiRecord() {
    if (ME.rec.active) { stopRec(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      showToast("Recording isn't supported in this browser -- type instead.");
      return;
    }
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){
      var mime = "";
      if (typeof window.MediaRecorder.isTypeSupported==="function") {
        mime = window.MediaRecorder.isTypeSupported("audio/webm;codecs=opus")?"audio/webm;codecs=opus"
          : window.MediaRecorder.isTypeSupported("audio/webm")?"audio/webm":"";
      }
      ME.rec.stream=stream; ME.rec.chunks=[];
      ME.rec.recorder=new MediaRecorder(stream,mime?{mimeType:mime}:undefined);
      ME.rec.recorder.ondataavailable=function(e){if(e.data&&e.data.size)ME.rec.chunks.push(e.data);};
      ME.rec.recorder.onstop=onRecStopped;
      ME.rec.active=true;
      try{ME.rec.recorder.start();}catch(e){ME.rec.active=false;showToast("Couldn't start recorder.");return;}
      var btn=$("meSbAIRecBtn"); if(btn)btn.textContent="\u23F9\uFE0E Stop recording";
      showToast("Listening - say your instruction, then tap stop.");
    }).catch(function(){showToast("Microphone unavailable.");});
  }


  /* ── Voice recording (shared by intro + sidebar) ────────────────────── */
  function stopRec() {
    if (ME.rec.recorder && ME.rec.recorder.state!=="inactive") {
      try{ME.rec.recorder.stop();}catch(e){}
    }
    ME.rec.active = false;
    if (ME.rec.stream){ME.rec.stream.getTracks().forEach(function(t){t.stop();});ME.rec.stream=null;}
    renderVoiceBox();
    var btn=$("meSbAIRecBtn"); if(btn)btn.textContent="Voice";
  }
  function stopVoicePreview() {
    document.querySelectorAll(".me-vn-audio").forEach(function(a){try{a.pause();}catch(e){}});
  }
  function toggleRec() {
    if (ME.rec.active){stopRec();return;}
    startRec();
  }
  function startRec() {
    if (!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia||!window.MediaRecorder) {
      showToast("Recording isn't supported in this browser -- type instead."); return;
    }
    stopVoicePreview();
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){
      var mime="";
      if(typeof window.MediaRecorder.isTypeSupported==="function"){
        mime=window.MediaRecorder.isTypeSupported("audio/webm;codecs=opus")?"audio/webm;codecs=opus"
          :window.MediaRecorder.isTypeSupported("audio/webm")?"audio/webm":"";
      }
      ME.rec.stream=stream; ME.rec.chunks=[];
      ME.rec.recorder=new MediaRecorder(stream,mime?{mimeType:mime}:undefined);
      ME.rec.recorder.ondataavailable=function(e){if(e.data&&e.data.size)ME.rec.chunks.push(e.data);};
      ME.rec.recorder.onstop=onRecStopped;
      ME.rec.active=true;
      try{ME.rec.recorder.start();}catch(e){ME.rec.active=false;showToast("Couldn't start recorder.");return;}
      renderVoiceBox();
      showToast("Listening -- say your instruction, then tap stop.");
    }).catch(function(){showToast("Microphone unavailable.");});
  }
  function onRecStopped() {
    var mime=(ME.rec.recorder&&ME.rec.recorder.mimeType)||"audio/webm";
    var blob=new Blob(ME.rec.chunks,{type:mime});
    ME.rec.chunks=[]; ME.rec.recorder=null; ME.rec.active=false;
    if(!blob.size){renderVoiceBox();return;}
    if(ME.voice&&ME.voice.url)URL.revokeObjectURL(ME.voice.url);
    ME.voice={blob:blob,url:URL.createObjectURL(blob),text:"",transcribing:true,err:""};
    renderVoiceBox();
    transcribeVoice(blob);
  }
  function transcribeVoice(blob) {
    var fd=new FormData(); fd.append("audio",blob,"voice-note.webm");
    postJSON("/api/editing/transcribe",{method:"POST",credentials:"include",
      headers:apiHeaders(),body:fd,timeoutMs:180000})
      .then(function(r){return r.json();})
      .then(function(d){
        ME.voice.transcribing=false;
        if(d&&d.status==="success"&&(d.text||"").trim())ME.voice.text=d.text.trim();
        else ME.voice.err=(d&&d.message)||"Couldn't transcribe.";
        renderVoiceBox(); updateGo();
      })
      .catch(function(){ME.voice.transcribing=false;ME.voice.err="Couldn't connect to transcribe.";renderVoiceBox();updateGo();});
  }
  function clearVoice(revoke) {
    stopRec(); stopVoicePreview();
    if(ME.voice&&ME.voice.url)URL.revokeObjectURL(ME.voice.url);
    ME.voice=null; renderVoiceBox(); updateGo();
    if(revoke!==true){var box=$("meVoiceBox");if(box)box.style.display="none";}
  }

  function renderVoiceBox() {
    var box=$("meVoiceBox"); if(!box)return;
    if(!ME.voice){box.style.display="none";box.innerHTML="";return;}
    box.style.display="";
    var v=ME.voice;
    box.innerHTML='<div class="me-voice-note">'+
      '<span class="me-vn-label">Voice instruction</span>'+
      '<button class="me-btn danger" onclick="VMEditing.clearVoice()">Discard</button>'+
      '<audio class="me-vn-audio" controls src="'+E(v.url)+'"></audio></div>'+
      '<div style="margin-top:8px">'+
      (v.transcribing?'<span class="me-sub">Transcribing...</span>'
        :v.text?'<span class="me-vn-trans">Transcribed: '+E(v.text)+'</span>'
        :v.err?'<span class="me-vn-trans">'+E(v.err)+'</span>'
        :'<span class="me-vn-trans">Recorded -- ready.</span>')+'</div>';
    var btn=$("meVoiceBtn");
    if(btn){btn.textContent=ME.rec.active?"Stop recording...":"Record voice instruction";btn.classList.toggle("recording",ME.rec.active);}
  }

  /* ── File handling ──────────────────────────────────────────────────── */
  function fileKind(f) {
    var mt=(f.type||"").toLowerCase();
    var name=(f.name||"").toLowerCase();
    var ext=name.split(".").pop();
    if(mt.indexOf("video/")===0||["mp4","webm","mov","m4v"].indexOf(ext)>=0)return "video";
    if(mt.indexOf("image/")===0||["png","jpg","jpeg","gif","webp"].indexOf(ext)>=0)return "image";
    if(mt.indexOf("audio/")===0||["mp3","wav","ogg","m4a","aac","weba"].indexOf(ext)>=0)return "audio";
    return "";
  }
  function hasVideo(files){return files.some(function(f){return f.kind==="video";});}
  function addFiles(list){
    if(!list)return;
    for(var i=0;i<list.length;i++){
      var f=list[i]; if(!f||!f.name)continue;
      if(f.size>MAX_FILE_MB*1024*1024){showToast(f.name+" is over "+MAX_FILE_MB+"MB.");continue;}
      var kind=fileKind(f); if(!kind){showToast(f.name+" isn't a video/image/audio file.");continue;}
      ME.files.push({id:Date.now()+"-"+i,kind:kind,name:f.name,file:f,url:URL.createObjectURL(f),size:f.size});
    }
    renderUploads(); updateGo();
  }
  function removeFile(id){
    for(var i=0;i<ME.files.length;i++){
      if(ME.files[i].id===id){
        if(ME.files[i].url)URL.revokeObjectURL(ME.files[i].url);
        ME.files.splice(i,1); break;
      }
    }
    renderUploads(); updateGo();
  }
  function revokeFiles(){ME.files.forEach(function(f){if(f.url)URL.revokeObjectURL(f.url);});}
  function renderUploads(){
    var box=$("meUploads"); if(!box)return;
    if(!ME.files.length){box.innerHTML="";return;}
    var html="";
    ME.files.forEach(function(f){
      var thumb='<div class="me-uf-thumb">'+
        (f.kind==="image"?'<img src="'+E(f.url)+'" alt="">'
          :f.kind==="video"?'<video src="'+E(f.url)+'" muted preload="metadata"></video>':"")+'</div>';
      html+='<div class="me-ufile">'+
        '<button class="me-uf-rm" title="Remove" onclick="VMEditing.removeFile(\''+f.id+'\')">x</button>'+
        thumb+'<div class="me-uf-name">'+E(f.name)+'</div></div>';
    });
    box.innerHTML=html;
  }

  /* ── Intro sticker selection + positions ────────────────────────────── */
  function fetchStickers() {
    postJSON("/api/editing/stickers",{credentials:"include",headers:apiHeaders(),timeoutMs:20000})
      .then(function(r){return r.json();})
      .then(function(d){
        var list=(d&&d.stickers)||[];
        ME.editor.stickerLibrary=list;
        var box=$("meStickers"); if(box){
          box.innerHTML="";
          if(!list.length){box.innerHTML='<span class="me-sub">No stickers bundled right now.</span>';}
          else list.slice(0,30).forEach(function(s){
            var btn=document.createElement("button");
            btn.type="button";btn.title=(s.name||"sticker");btn.className="me-sticker-pick";
            var img=document.createElement("img");img.src=(s.url||"")+CACHE_BUST;img.alt=s.name||"sticker";
            btn.appendChild(img);
            btn.addEventListener("click",function(){pickSticker(s.name,s.url);});
            box.appendChild(btn);
          });
        }
        renderSidebar();
      })
      .catch(function(){var box=$("meStickers");if(box)box.innerHTML='<span class="me-sub">Couldn\'t load the sticker library.</span>';});
  }
  function pickSticker(name,url){
    ME.sticker={name:name||"sticker",url:url};
    if(ME.editor.recentStickers.some(function(s){return s.url===url;})===false){
      ME.editor.recentStickers.unshift({name:name||"sticker",url:url});
      if(ME.editor.recentStickers.length>8)ME.editor.recentStickers.pop();
    }
    renderStickerSel();
    renderStickersSection();
    var picks=document.querySelectorAll(".me-sticker-pick");
    picks.forEach(function(b){var img=b.querySelector("img");b.classList.toggle("selected",!!(img&&img.src.indexOf(url)>-1));});
  }
  function setPos(v){ME.stickerPos=v;renderPosRow();}
  function renderStickerSel(){
    var sel=$("meStickerSel");
    if(sel){
      sel.innerHTML=ME.sticker?'<div class="me-sub" style="margin-top:8px">Selected: <b style="color:#e2e8f0">'+E(ME.sticker.name)+'</b></div>':"";
    }
    renderPosRow();
  }
  function renderPosRow(){
    var row=$("mePosRow"); if(!row)return;
    row.style.display=ME.sticker?"":"none";
    row.innerHTML=POSITIONS.map(function(p){
      return '<button class="me-pos'+(ME.stickerPos===p.v?" active":"")+'" onclick="VMEditing.setPos(\''+p.v+'\')">'+p.l+"</button>";
    }).join("");
  }

  function bindIntro() {
    textEl=$("meText"); testEl=$("meTestMode");
    if(textEl)textEl.addEventListener("input",updateGo);
    if(testEl)testEl.addEventListener("change",updateGo);
    var drop=$("meUploadDrop"), input=$("meFilesInput");
    if(drop&&input){
      var open=function(){input.click();};
      drop.addEventListener("click",open);
      input.addEventListener("change",function(){addFiles(this.files);this.value="";});
      ["dragenter","dragover"].forEach(function(ev){drop.addEventListener(ev,function(e){e.preventDefault();drop.classList.add("drag");});});
      ["dragleave","drop"].forEach(function(ev){drop.addEventListener(ev,function(e){e.preventDefault();drop.classList.remove("drag");});});
      drop.addEventListener("drop",function(e){if(e.dataTransfer&&e.dataTransfer.files)addFiles(e.dataTransfer.files);});
    }
  }

  function updateGo() {
    var go=$("meGoBtn"); if(!go)return;
    var instruction=(textEl&&textEl.value||"").trim()||(ME.voice&&(ME.voice.text||"").trim())||(ME.voice&&ME.voice.blob?true:false);
    var ok=!!(instruction&&hasVideo(ME.files));
    go.disabled=!ok;
    go.title=ok?"Start the AI edit":"Type or record an instruction and attach a video first.";
  }

  /* ── Submit + polling ───────────────────────────────────────────────── */
  function submit() {
    if(ME.timer)return;
    var text=(textEl&&textEl.value||"").trim();
    var voiceText=(ME.voice&&(ME.voice.text||"").trim())||"";
    if(!text&&!voiceText&&!(ME.voice&&ME.voice.blob)){showToast("Type or record an instruction first.");return;}
    if(!hasVideo(ME.files)){showToast("Attach a video clip to edit first.");return;}
    stopRec(); stopVoicePreview();
    ME.mode="working"; renderBody();
    setWorking("Uploading your footage...","Sending the clip, instruction, media and sticker to the editing pipeline.");
    var fd=new FormData();
    var sentVid=false;
    ME.files.forEach(function(f){
      if(f.kind==="video"&&!sentVid){fd.append("video",f.file,f.name);sentVid=true;}
      else fd.append("media",f.file,f.name);
    });
    if(text)fd.append("instruction",text);
    if(voiceText)fd.append("voice_transcript",voiceText);
    if(ME.voice&&ME.voice.blob){
      var vext=/webm/i.test(ME.voice.blob.type)?".webm":/ogg/i.test(ME.voice.blob.type)?".ogg":".m4a";
      fd.append("voice_note",ME.voice.blob,"voice-note"+vext);
    }
    if(ME.sticker){fd.append("sticker_url",ME.sticker.url);fd.append("sticker_name",ME.sticker.name);fd.append("sticker_pos",ME.stickerPos);}
    if(testEl&&testEl.checked)fd.append("test_mode","1");
    postJSON("/api/editing/run",{method:"POST",credentials:"include",
      headers:apiHeaders(),body:fd,timeoutMs:150000})
      .then(function(r){return r.json();})
      .then(function(d){
        if(!d||d.status!=="success"||!d.job){
          setWorking("Couldn't start",(d&&d.message)||"Please try again.");
          scheduleRecover(); return;
        }
        ME.jobId=d.job.job_id; ME.start=Date.now();
        setWorking("Planning your edit...","Reading your instruction and deciding what to do.");
        ME.timer=window.setInterval(poll,4000);
        poll();
      })
      .catch(function(){setWorking("Upload failed","Check your connection and try again.");scheduleRecover();});
  }

  function scheduleRecover() {
    window.setTimeout(function(){
      if(ME.mode==="working"&&!ME.jobId){ME.mode="intro";renderBody();updateGo();}
    },3500);
  }

  function poll() {
    if(!ME.jobId)return;
    postJSON("/api/studio/job/"+encodeURIComponent(ME.jobId),{credentials:"include",headers:apiHeaders(),timeoutMs:20000})
      .then(function(r){return r.json();})
      .then(function(d){
        var job=d&&d.job; if(!job)return;
        if(job.edit_plan&&job.edit_plan.length)renderPlan(job);
        if(ME.mode!=="working")return;
        if(job.status==="running"||job.status==="queued"){
          var stage=job.edit_plan_stage||"";
          var secs=ME.start?Math.round((Date.now()-ME.start)/1000):0;
          var line="In the background so you can keep going -- "+secs+"s in.";
          if(job.stalled)line="Backend is waking up -- this can take a moment.";
          setWorking(stageLabel(stage),line);
          if(job.edit_plan_note){var n=$("mePlanNote");if(n)n.textContent=job.edit_plan_note;}
          return;
        }
        if(ME.timer){window.clearInterval(ME.timer);ME.timer=null;}
        if(job.status==="done"&&job.final_video){
          ME.editor.jobId=job.job_id;
          ME.editor.resultVideo=job.final_video;
          ME.editor.stats=job.stats||{};
          enterEditor();
        } else {
          ME.jobFailMsg=job.error||"Editing didn't finish -- try another clip.";
          ME.mode="intro"; renderBody(); showToast(ME.jobFailMsg);
        }
      })
      .catch(function(){});
  }

  function enterWorking(job) {
    ME.jobId = (job&&job.job_id)||ME.jobId;
    ME.start=Date.now();
    ME.mode="working";
    renderBody();
    setWorking("Applying your changes...","Re-rendering with the updated settings.");
    ME.timer=window.setInterval(function(){
      pollJobToEditor();
    },4000);
    pollJobToEditor();
  }

  function pollJobToEditor() {
    if(!ME.editor.jobId)return;
    postJSON("/api/studio/job/"+encodeURIComponent(ME.editor.jobId),{credentials:"include",headers:apiHeaders(),timeoutMs:20000})
      .then(function(r){return r.json();})
      .then(function(d){
        var job=d&&d.job; if(!job)return;
        if(job.edit_plan&&job.edit_plan.length)renderPlan(job);
        if(ME.mode!=="working")return;
        if(job.status==="running"||job.status==="queued"){
          setWorking(stageLabel(job.edit_plan_stage||""),"Re-rendering in the background...");
          return;
        }
        if(ME.timer){window.clearInterval(ME.timer);ME.timer=null;}
        if(job.status==="done"&&job.final_video){
          ME.editor.resultVideo=job.final_video;
          ME.editor.stats=job.stats||{};
          ME.editor.jobId=job.job_id;
          enterEditor();
          showToast("Updated video is ready.");
        } else {
          ME.mode="editor";
          renderBody();
          showToast(job.error||"The update didn't finish.");
        }
      })
      .catch(function(){});
  }

  function enterEditor() {
    var jobId = ME.editor.jobId;
    ME.mode="editor";
    renderBody();
    bindCanvasVideo();
    var v=$("meCanvasVideo");
    if(v&&ME.editor.resultVideo){
      v.src=ME.editor.resultVideo;
      v.onloadedmetadata=function(){ME.editor.duration=v.duration||0;updateTimeDisplay();renderTimeline();};
    }
    renderTimeline();
  }

  function goToInput(){ME.mode="intro";renderBody();updateGo();}

  function stageLabel(stage){
    var map={planned:"Planning your edit...",broll:"Adding B-roll...",sticker:"Applying the sticker...",
      "slow-motion":"Applying slow motion...",music:"Mixing in the music...",done:"Wrapping up..."};
    return map[stage]||(stage?"Editing...":"Planning your edit...");
  }

  function renderPlan(job) {
    var list=$("mePlanList"); if(!list)return;
    var steps=(job.edit_plan||[]).filter(function(s){return s&&s.step;});
    var current=STAGES.indexOf(job.edit_plan_stage||"");
    var doneN=current>0?Math.max(1,current):0;
    doneN=Math.min(doneN,steps.length);
    var html=steps.map(function(s,i){
      return '<div class="me-plan-item'+(i<doneN?" done":"")+'"><span class="me-pic"></span>'+E(s.step)+"</div>";
    }).join("");
    if(html)list.innerHTML=html;
  }

  function setWorking(main,sub){
    if(ME.mode!=="working")return;
    if(main){var ph=$("mePhase");if(ph)ph.textContent=main;}
    if(sub){var sb=$("meSub");if(sb)sb.textContent=sub;}
  }
  function status(main,sub){setWorking(main,sub);}
  function refine(){
    if(ME.timer){window.clearInterval(ME.timer);ME.timer=null;}
    ME.jobId="";ME.mode="intro";renderBody();updateGo();
  }

  /* ── Public API ─────────────────────────────────────────────────────── */
  var API = {
    launch: launch, hide: hide, onShow: onShow, reset: reset,
    status: status, refine: refine,
    toggleRec: toggleRec, clearVoice: clearVoice,
    pickSticker: pickSticker, setPos: setPos,
    removeFile: removeFile, submit: submit, toDashboard: toDashboard,
    toggleSection: toggleSection,
    setManual: setManual, toggleEffect: toggleEffect,
    toggleAITool: toggleAITool,
    setCanvasAspect: setCanvasAspect, setCanvasMode: setCanvasMode, setCanvasBg: setCanvasBg,
    applyAIEdit: applyAIEdit, applyManual: applyManual,
    aiRecord: aiRecord, sbUploadMedia: sbUploadMedia,
    pickEditorSticker: pickEditorSticker, filterStickers: filterStickers,
    uploadCustomSticker: uploadCustomSticker,
    uploadAudio: uploadAudio, removeMusic: removeMusic, uploadVoiceOver: uploadVoiceOver,
    smartReframe: smartReframe, aiSuggestTransitions: aiSuggestTransitions,
    addTextLayer: addTextLayer, removeTextLayer: removeTextLayer, updateTextLayer: updateTextLayer,
    undoAction: undoAction, redoAction: redoAction,
    togglePlay: togglePlay, skipBack: skipBack, skipForward: skipForward,
    zoomTimeline: zoomTimeline, goToInput: goToInput
  };
  window.VMEditing = API;

  /* ── Init ───────────────────────────────────────────────────────────── */
  function initOnce(){ init(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initOnce);
  } else {
    initOnce();
  }
})();

