/* ValleyMind — Massive Studio (AI Video Studio)
   -----------------------------------------------------------
   Studio -> Massive Editing. The user uploads footage, types or
   records an AI instruction, picks a sticker, then hits
   "Start AI Edit". The AI creates the edit; the studio then exposes
   every manual control the backend supports.

   Layout: left tools rail (Media/Stickers/Effects/Text/Audio/
   Filters/Adjustments) + center stage (preview + timeline) + right
   "Tell Massive what you want" AI panel. Mobile: compact top, large
   preview, bottom AI bar + tool sheets. Tablet: intermediate.

   All engine/API behaviour is unchanged -- this is presentation only.
   Modes: intro -> working -> editor
*/
(function () {
  "use strict";

  var CACHE_BUST = "?v=3";
  var MAX_FILE_MB = 100;
  var STAGES = ["analysis","planned","broll","sticker","slow-motion","camera","transitions","sfx","music","done"];
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
  var TOOLS = [
    {id:"media",icon:"film",label:"Media"},
    {id:"stickers",icon:"smile-plus",label:"Stickers"},
    {id:"effects",icon:"wand-2",label:"Effects"},
    {id:"text",icon:"type",label:"Text"},
    {id:"audio",icon:"volume-2",label:"Audio"},
    {id:"filters",icon:"palette",label:"Filters"},
    {id:"adjustments",icon:"sliders-h",label:"Adjustments"}
  ];
  var FILTER_LOOK = {
    bw: "linear-gradient(135deg,#3f3f46,#1b1e28)",
    vignette: "radial-gradient(circle at 50% 35%, #334155 0%, #080d17 74%)",
    blur: "radial-gradient(circle at 50% 50%, #475569 0%, #0f172a 62%)",
    boost: "linear-gradient(135deg,#ff5d5d,#ffb84d,#7dd3fc)",
    sepia: "linear-gradient(135deg,#7c5a3a,#c99a4a,#ead9b0)",
    saturated: "linear-gradient(135deg,#ff2e63,#ff8f3f,#35d0ba)",
    fade_in: "linear-gradient(180deg,#0b1222,#9fb4cc)",
    fade_out: "linear-gradient(180deg,#9fb4cc,#0b1222)"
  };
  var EFFECT_LABELS = {bw:"Black & White",vignette:"Vignette",blur:"Blur",boost:"Color Boost",
    sepia:"Sepia",saturated:"Saturated",fade_in:"Fade In",fade_out:"Fade Out"};

  var ME = {
    rendered: false, mode: "intro",
    files: [], voice: null, sticker: null, stickerPos: "br",
    jobId: "", start: 0, timer: null,
    rec: {stream:null, recorder:null, chunks:[], active:false},
    undoStack: [], redoStack: [],
    editor: {
      jobId: "", sourceVideo: "", resultVideo: "",
      timeline: null, stats: null, duration: 0,
      openTool: "media",
      stickerLibrary: [], recentStickers: [], uploadedStickers: [],
      manual: {
        canvas: {aspect:"9:16",mode:"fill",bg:"000000",reframe:"smart"},
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
        intensity: "medium", transitionsMode: "auto",
        camera: true, sfx: true,
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
  function isSmall() {
    return window.matchMedia && window.matchMedia("(max-width:700px)").matches;
  }

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
      film:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" x2="7" y1="2" y2="22"/><line x1="17" x2="17" y1="2" y2="22"/><line x1="2" x2="22" y1="12" y2="12"/><line x1="2" x2="7" y1="7" y2="7"/><line x1="2" x2="7" y1="17" y2="17"/><line x1="17" x2="22" y1="17" y2="17"/><line x1="17" x2="22" y1="7" y2="7"/></svg>',
      image:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>',
      palette:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/></svg>',
      'sliders-h':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/><line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/><line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/><line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/><line x1="16" x2="16" y1="18" y2="22"/></svg>',
      music:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
      x:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
      layers:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/></svg>',
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
      mic:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>',
      'stop-circle':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><rect x="9" y="9" width="6" height="6"/></svg>',
      upload:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>'
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
    window.addEventListener("resize", function(){
      if (ME.mode === "editor" && !isSmall()) {
        var f = $("meToolPanel"); if (f) f.classList.remove("open");
        var p = $("meAiPanel"); if (p) p.classList.remove("open");
        var sc = $("meScrim"); if (sc) sc.classList.remove("show");
      }
    });
  }

  function renderShell() {
    rootEl.innerHTML =
      '<div class="me-topbar" id="meTopbar"></div>' +
      '<div id="meToast" class="me-toast" style="display:none"></div>' +
      '<div id="meInner" class="me-studio" style="display:none"></div>' +
      '<div id="meEditor" class="me-studio" style="display:none"></div>' +
      '<div class="me-scrim" id="meScrim"></div>';
    renderTopbar();
  }

  function renderTopbar() {
    var tb = $("meTopbar"); if (!tb) return;
    var edit = ME.mode === "editor";
    tb.innerHTML =
      '<h2>Massive</h2>' +
      '<span class="me-top-sub">AI Video Studio</span>' +
      '<div class="me-top-actions">' +
        '<button class="me-etb-btn me-only-small" onclick="VMEditing.toggleAIPanel()">'+icon("sparkles")+' AI</button>' +
        (edit ? '<button class="me-etb-btn me-only-small" onclick="VMEditing.goToInput()">New</button>' : '') +
        '<button class="me-close" onclick="VMEditing.toDashboard()">&larr; <span>Back to Studio</span></button>' +
      '</div>';
  }

  function renderBody() {
    var inner = $("meInner");
    var editor = $("meEditor");
    renderTopbar();
    if (ME.mode === "working") { if(inner){inner.style.display="";inner.innerHTML=workingHTML();} if(editor)editor.style.display="none"; return; }
    if (ME.mode === "editor") { if(inner)inner.style.display="none"; renderEditor(); return; }
    if (editor) editor.style.display = "none";
    if (inner) { inner.style.display=""; inner.innerHTML = introHTML(); bindIntro(); renderUploads(); renderVoiceBox(); renderStickerSel(); updateGo();
      if(ME.files.length&&!hasVideo(ME.files))showToast("Add a video clip -- that's what gets edited."); }
  }

  /* ── Shared studio frame: rail + stage + AI panel ───────────────────── */
  function railHTML() {
    var html = '<div class="me-rail" id="meRail">' +
      '<div class="me-rail-brand">'+icon("sparkles")+'</div>';
    TOOLS.forEach(function(t){
      var active = (ME.mode==="editor") && (ME.editor.openTool===t.id);
      html += '<button class="me-rail-item'+(active?' active':'')+'" data-tool="'+t.id+'" title="'+t.label+'" onClick="VMEditing.railClick(\''+t.id+'\')">' +
        icon(t.icon) + '<span>'+t.label+'</span></button>';
    });
    return html + '</div>';
  }

  function aiFloatbar(text) {
    return '<div class="me-ai-floatbar" onClick="VMEditing.toggleAIPanel(true)"><span>'+icon("sparkles") + "</span><b>" + text + "</b></div>";
  }

  function aiPanelHead(title, sub) {
    return '<div class="me-ai-head"><span class="me-ai-head-ic">'+icon("sparkles")+'</span>' +
      '<div><div class="me-ai-title">'+title+'</div>' +
      (sub ? '<div class="me-ai-sub">'+sub+'</div>' : '') + '</div></div>';
  }

  /* ── Intro (creation) mode ──────────────────────────────────────────── */
  function introHTML() {
    return railHTML() +
      '<div class="me-stage me-stage-create" id="meCreateStage">' +
      '  <div class="me-create-inner">' +
      '    <div class="me-create-title">Start creating</div>' +
      '    <p class="me-create-sub">Upload your clip, describe the video you want, and let Massive build it. Then tweak anything in the studio and export.</p>' +
      '    <div class="me-upload-actions">' +
      '      <button class="me-upload-action primary" onclick="VMEditing.pickFiles(\'video/*\')">'+icon("film")+' <span>Upload Video</span></button>' +
      '      <button class="me-upload-action" onclick="VMEditing.pickFiles(\'image/*\')">'+icon("image")+' <span>+ Add Photos</span></button>' +
      '      <button class="me-upload-action" onclick="VMEditing.pickFiles(\'audio/*\')">'+icon("music")+' <span>+ Add Assets</span></button>' +
      '    </div>' +
      '    <div id="meUploadDrop" class="me-upload-drop">' +
      '      <div class="me-u-icon">'+icon("upload")+'</div>' +
      '      <div class="me-u-title">or drop files anywhere in this box</div>' +
      '      <div class="me-u-sub">video / image / audio &mdash; up to '+MAX_FILE_MB+'MB each</div>' +
      '      <input type="file" id="meFilesInput" accept="video/*,image/*,audio/*" multiple style="display:none">' +
      '    </div>' +
      '    <div id="meUploads" class="me-uploads"></div>' +
      '    <div class="me-sticker-card">' +
      '      <div class="me-sticker-card-head"><span class="me-scc-ic">'+icon("smile-plus")+'</span>' +
      '        <div><div class="me-scc-title">Sticker</div><div class="me-scc-sub">Pick one and Massive pops it in at the right moment.</div></div>' +
      '      </div>' +
      '      <div id="meStickers" class="me-sticker-row"><span class="me-sub">Loading sticker library...</span></div>' +
      '      <div id="mePosRow" class="me-pos-row" style="display:none"></div>' +
      '      <div id="meStickerSel"></div>' +
      '    </div>' +
      '  </div>' +
      '</div>' +
      '<div class="me-ai-panel" id="meAiPanel">' +
        aiPanelHead("Tell Massive what you want", "Upload &rarr; describe &rarr; AI creates &rarr; review &rarr; adjust &rarr; export") +
        '<textarea id="meText" class="me-ai-input" placeholder="Describe the video you want&hellip;"></textarea>' +
        '<div class="me-ai-row">' +
        '  <button id="meVoiceBtn" class="me-sb-btn" onclick="VMEditing.toggleRec()">'+icon("mic")+' Record voice instruction</button>' +
        '</div>' +
        '<div id="meVoiceBox" style="display:none"></div>' +
        '<button id="meGoBtn" class="me-ai-go" onclick="VMEditing.submit()" disabled>'+icon("sparkles")+' <span>Start AI Edit</span></button>' +
        '<label class="me-testmode me-ai-extras"><input type="checkbox" id="meTestMode"> Test mode (skip AI B-roll)</label>' +
      '</div>' +
      aiFloatbar("Tell Massive what you want&hellip;");
  }

  /* ── Working mode ───────────────────────────────────────────────────── */
  function workingHTML() {
    return railHTML() +
      '<div class="me-stage me-stage-working">' +
      '  <div class="me-working-card">' +
      '    <div class="me-plan">' +
      '      <div class="me-plan-title">'+icon("sparkles")+' AI Edit Plan</div>' +
      '      <div id="mePlanList"><div class="me-plan-item"><span class="me-pic"></span>Reading your instruction...</div></div>' +
      '      <div id="mePlanNote" class="me-plan-note"></div>' +
      '    </div>' +
      '    <div class="me-progress">' +
      '      <div class="me-spinner"></div>' +
      '      <div class="me-phase" id="mePhase">Planning your edit...</div>' +
      '      <div class="me-sub" id="meSub"></div>' +
      '    </div>' +
      '  </div>' +
      '</div>' +
      '<div class="me-ai-panel" id="meAiPanel">' +
        aiPanelHead("Massive is creating", "Your edit runs safely in the background.") +
        '<div class="me-working-note"><span class="me-wn-ic">'+icon("sparkles")+'</span>' +
        '  <span>Following your instruction &mdash; trimming, captions, stickers, camera moves, transitions and sound.</span></div>' +
      '</div>' +
      aiFloatbar("Massive is working on your edit&hellip;");
  }

  /* ── Editor (studio) mode ───────────────────────────────────────────── */
  function renderEditor() {
    var ed = $("meEditor");
    if (!ed) return;
    var e = ME.editor;
    ed.innerHTML =
      '<div class="me-studio-body">' +
        railHTML() +
        '<div class="me-flyout" id="meToolPanel"></div>' +
        '<div class="me-stage">' +
        '  <div class="me-canvas-wrap" id="meCanvasWrap">' +
        '    <div class="me-canvas-container" id="meCanvasContainer">' +
        '      <video id="meCanvasVideo" class="me-canvas-video" playsinline preload="metadata"></video>' +
        '    </div>' +
        '    <div class="me-canvas-controls" id="meCanvasControls"></div>' +
        '  </div>' +
        '  <div class="me-timeline" id="meTimeline"></div>' +
        '</div>' +
        aiPanelEditor() +
        aiFloatbar("Ask Massive to change anything&hellip;") +
      '</div>';
    renderToolPanel();
    renderCanvasControls();
    renderTimeline();
    bindCanvasVideo();
    if (typeof lucide !== "undefined") { try{lucide.createIcons();}catch(e){} }
  }

  function aiPanelEditor() {
    var m = ME.editor.manual;
    var e = ME.editor;
    var iv = m.intensity || "medium";
    var chips = ["low","medium","high"].map(function(v){
      return '<span class="me-sb-chip'+(iv===v?' active':'')+'" onclick="VMEditing.setManual(\'intensity\',\''+v+'\')">' +
        (v==="low"?"Low":v==="medium"?"Medium":"High") + '</span>';
    }).join("");
    return '<div class="me-ai-panel" id="meAiPanel">' +
      aiPanelHead("Tell Massive what you want", "Type or record it &mdash; Massive creates the edit.") +
      '<textarea id="meSbAIInput" class="me-ai-input" placeholder="Describe the video you want&hellip;">'+E(ME.editor._aiInstruction||'')+'</textarea>' +
      '<div class="me-ai-row">' +
      '  <button class="me-sb-btn" id="meSbAIRecBtn" onclick="VMEditing.aiRecord()">'+icon("mic")+' Voice</button>' +
      '  <button class="me-sb-btn primary" style="flex:1" onclick="VMEditing.applyAIEdit()">'+icon("sparkles")+' Apply AI Edit</button>' +
      '</div>' +
      '<div id="meVoiceBox" style="display:none"></div>' +
      '<div class="me-ai-group me-ai-extras"><div class="me-sb-label-sm">Intensity</div>' +
      '  <div class="me-sb-chips">'+chips+'</div>' +
      '  <p class="me-muted" style="margin-top:6px">Low = clean &amp; minimal &middot; Medium = modern social &middot; High = energetic viral.</p>' +
      '</div>' +
      '<div class="me-ai-group me-ai-extras"><div class="me-sb-label-sm">AI Set</div>' +
      '  <label class="me-sb-toggle"><input type="checkbox" '+(m.autoCut!==false?'checked':'')+' onclick="VMEditing.toggleAITool(\'auto-cut\',this.checked)"><span>Auto-cut silences</span></label>' +
      '  <label class="me-sb-toggle"><input type="checkbox" '+(m.captions!==false?'checked':'')+' onclick="VMEditing.toggleAITool(\'auto-captions\',this.checked)"><span>Auto animated captions</span></label>' +
      '</div>' +
      '<div class="me-ai-group me-ai-extras"><div class="me-sb-label-sm">Quick AI Actions</div>' +
      '  <div class="me-ai-actions">' +
      '    <button class="me-sb-btn" onclick="VMEditing.smartReframe()">'+icon("frame")+' Reframe to 9:16</button>' +
      '    <button class="me-sb-btn" onclick="VMEditing.aiSuggestTransitions()">'+icon("sparkles")+' AI transitions</button>' +
      '  </div>' +
      '</div>' +
      '<div class="me-ai-cta">' +
        (e.resultVideo ?
          '<a class="me-ai-go" href="'+E(e.resultVideo)+'" download="massive-edit.mp4">'+icon("download")+' <span>Export MP4</span></a>' +
          '<button class="me-ai-apply" onclick="VMEditing.applyManual()">'+icon("refresh")+' <span>Apply manual changes</span></button>'
         : '<button class="me-ai-apply" onclick="VMEditing.applyManual()">'+icon("refresh")+' <span>Apply changes</span></button>') +
      '</div>' +
      '</div>';
  }

  /* ── Tools rail + flyout panels ─────────────────────────────────────── */
  function toolById(id) {
    for (var i=0;i<TOOLS.length;i++) if (TOOLS[i].id===id) return TOOLS[i];
    return TOOLS[0];
  }

  function railClick(id) {
    if (ME.mode==="working") { showToast("Massive is still working on your edit..."); return; }
    if (ME.mode==="intro") {
      if (id==="media") { var d=$("meUploadDrop"); if(d)try{d.scrollIntoView({behavior:"smooth",block:"center"});}catch(e){d.scrollIntoView();} }
      else if (id==="stickers") { var s=$("meStickers"); if(s)try{s.scrollIntoView({behavior:"smooth",block:"center"});}catch(e){s.scrollIntoView();} }
      else showToast("Upload a video, describe it, and start your AI edit to unlock the studio tools.");
      return;
    }
    openTool(id);
  }

  function openTool(id) {
    ME.editor.openTool = (id&&toolById(id))? id : "media";
    renderToolPanel();
    if (isSmall()) openScrim();
  }

  function closeTool() {
    var b = $("meToolPanel"); if (!b) return;
    if (isSmall()) { ME.editor.openTool = ""; b.classList.remove("open"); b.innerHTML=""; syncScrim(); }
  }

  function toggleSection(id) { openTool(id); }

  function renderToolPanel() {
    var box = $("meToolPanel"); if (!box) return;
    var id = ME.editor.openTool || "media";
    var tool = toolById(id);
    box.innerHTML =
      '<div class="me-tool-head">' +
      '  <div class="me-tool-title">'+icon(tool.icon)+' <span>'+tool.label+'</span></div>' +
      (isSmall() ? '<button class="me-tool-close" onclick="VMEditing.closeTool()">'+icon("x")+'</button>' : '') +
      '</div>' +
      '<div class="me-tool-body">'+renderToolBody(id)+'</div>';
    box.classList.add("open");
  }

  function renderToolBody(id) {
    switch(id) {
      case "media": return renderMediaPanel();
      case "stickers": return renderStickersSection();
      case "effects": return renderEffectsPanel();
      case "text": return renderTextSection();
      case "audio": return renderAudioSection();
      case "filters": return renderFiltersPanel();
      case "adjustments": return renderAdjustmentsPanel();
    }
    return "";
  }

  function renderMediaPanel() {
    return '<div class="me-sb-upload-zone" onclick="$(\'meSbMediaInput\').click()">' +
      '  <div class="me-sb-uz-text">'+icon("upload")+' Upload video, image, or audio</div>' +
      '  <input type="file" id="meSbMediaInput" accept="video/*,image/*,audio/*" multiple style="display:none" onchange="VMEditing.sbUploadMedia(this.files)">' +
      '</div>' +
      '<div class="me-sb-label-sm" style="margin-top:12px">Project Files</div>' +
      '<div id="meUploads" class="me-uploads"></div>' +
      (ME.files.length
        ? '<p class="me-muted">'+ME.files.length+' file(s) attached. Remove any you don\'t need, then apply changes to re-render.</p>'
        : '<p class="me-muted">The source clip drives the edit. Add b-roll, stills or music here for the AI to weave in.</p>');
  }

  function renderEffectsPanel() {
    var m = ME.editor.manual;
    var slow = m.slowmoFactor || 0;
    return '<div class="me-sb-label-sm">Motion</div>' +
      '<div class="me-motion-cards">' +
      '  <div class="me-motion-card'+(m.camera!==false?' on':'')+'">' +
      '    <label class="me-sb-toggle"><input type="checkbox" '+(m.camera!==false?'checked':'')+' onchange="VMEditing.setManual(\'camera\',this.checked)"><span>Camera effects</span></label>' +
      '    <p class="me-muted">Punch-in, shake and freeze at the best moments.</p></div>' +
      '  <div class="me-motion-card'+(m.sfx!==false?' on':'')+'">' +
      '    <label class="me-sb-toggle"><input type="checkbox" '+(m.sfx!==false?'checked':'')+' onchange="VMEditing.setManual(\'sfx\',this.checked)"><span>Sound effects</span></label>' +
      '    <p class="me-muted">Whooshes, pops, dings and laughs timed to the action.</p></div>' +
      '</div>' +
      '<div class="me-sb-label-sm" style="margin-top:12px">Slow Motion</div>' +
      '<div class="me-sb-chips">' +
      '  '+[["Slow-mo off",0],["Half speed",0.5],["Light slow-mo",0.75]].map(function(o){
        return '<span class="me-sb-chip'+(slow===o[1]?' active':'')+'" onclick="VMEditing.setManual(\'slowmoFactor\','+o[1]+')">'+o[0]+'</span>';
      }).join("") +
      '</div>' +
      '<div class="me-sb-label-sm" style="margin-top:14px">Transitions</div>' +
      '<button class="me-sb-btn" onclick="VMEditing.aiSuggestTransitions()">'+icon("sparkles")+' Let AI choose transitions</button>' +
      '<p class="me-muted">The engine cuts between segments and AI picks the change points. More styles arrive with the rendering engine.</p>';
  }

  function renderFiltersPanel() {
    var m = ME.editor.manual;
    var active = m.effects || [];
    var visual = ["bw","vignette","blur","boost","sepia","saturated"];
    var fades = ["fade_in","fade_out"];
    return '<div class="me-sb-label-sm">Filters</div>' +
      '<div class="me-filter-grid">' +
      visual.map(function(e){
        return '<div class="me-filter-card'+(active.indexOf(e)>=0?' active':'')+'" onclick="VMEditing.toggleEffect(\''+e+'\')">' +
          '<div class="me-filter-swatch" style="background:'+(FILTER_LOOK[e]||"#1f2937")+'">'+(active.indexOf(e)>=0?'<span class="me-filter-on">'+icon("check")+'</span>':'')+'</div>' +
          '<span class="me-filter-name">'+(EFFECT_LABELS[e]||e)+'</span></div>';
      }).join("") +
      '</div>' +
      '<div class="me-sb-label-sm" style="margin-top:14px">Fades</div>' +
      '<div class="me-sb-chips">' +
      fades.map(function(e){
        return '<span class="me-sb-chip'+(active.indexOf(e)>=0?' active':'')+'" onclick="VMEditing.toggleEffect(\''+e+'\')">'+(EFFECT_LABELS[e]||e)+'</span>';
      }).join("") +
      '</div>' +
      '<div class="me-sb-row" style="margin-top:10px"><label>Fade In</label><input type="range" class="me-sb-slider" min="0" max="5" step="0.1" value="'+(m.fadeIn||0)+'" oninput="VMEditing.setManual(\'fadeIn\',this.value)"><span class="me-sb-val">'+(m.fadeIn||0)+'s</span></div>' +
      '<div class="me-sb-row"><label>Fade Out</label><input type="range" class="me-sb-slider" min="0" max="5" step="0.1" value="'+(m.fadeOut||0)+'" oninput="VMEditing.setManual(\'fadeOut\',this.value)"><span class="me-sb-val">'+(m.fadeOut||0)+'s</span></div>';
  }

  function renderAdjustmentsPanel() {
    var m = ME.editor.manual;
    var c = m.canvas || {aspect:"9:16",mode:"fill",bg:"000000"};
    return renderEditSection() +
      '<div class="me-sb-label-sm" style="margin-top:14px">Canvas Format</div>' +
      '<div class="me-sb-chips">' +
      CANVAS_PRESETS.map(function(p){
        return '<span class="me-sb-chip'+(c.aspect===p.v?' active':'')+'" onclick="VMEditing.setCanvasAspect(\''+p.v+'\')">'+p.label+' <em>'+p.sub+'</em></span>';
      }).join("") +
      '</div>' +
      '<div class="me-sb-chips" style="margin-top:6px">' +
      '  <span class="me-sb-chip'+(c.mode==='fill'?' active':'')+'" onclick="VMEditing.setCanvasMode(\'fill\')">Fill (crop)</span>' +
      '  <span class="me-sb-chip'+(c.mode==='fit'?' active':'')+'" onclick="VMEditing.setCanvasMode(\'fit\')">Fit (letterbox)</span>' +
      '</div>' +
      '<div class="me-sb-row" style="margin-top:10px"><label>Background</label>' +
      '  <input type="color" value="#'+E(c.bg||'000000')+'" style="width:32px;height:28px;border:none;background:none;cursor:pointer" oninput="VMEditing.setCanvasBg(this.value)">' +
      '  <span class="me-sb-val">#'+E(c.bg||'000000')+'</span></div>' +
      '<button class="me-sb-btn" style="margin-top:10px" onclick="VMEditing.smartReframe()">'+icon("sparkles")+' Smart reframe to 9:16</button>';
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
        return '<img src="'+E(s.url)+CACHE_BUST+'" title="'+E(s.name)+'" onclick="VMEditing.pickEditorSticker(\''+E(s.url).replace(/'/g,"\\'")+'\',\''+E(s.name).replace(/'/g,"\\'")+'\')"'+(sel&&sel.url===s.url?' class="selected"':'')+'>';
      }).join("") : '<span style="color:#475569;font-size:11px">None yet</span>') +
      '</div>' +
      '<div class="me-sb-label-sm">Sticker Library</div>' +
      '<input type="text" class="me-sb-input" placeholder="Search stickers..." oninput="VMEditing.filterStickers(this.value)" style="margin-bottom:8px">' +
      '<div class="me-sb-grid" id="meSbStickerGrid">' +
      (ME.editor.stickerLibrary.length ? ME.editor.stickerLibrary.map(function(s){
        return '<img class="me-sb-sticker'+(sel&&sel.url===s.url?' selected':'')+'" src="'+E(s.url)+CACHE_BUST+'" title="'+E(s.name)+'" onclick="VMEditing.pickEditorSticker(\''+E(s.url).replace(/'/g,"\\'")+'\',\''+E(s.name).replace(/'/g,"\\'")+'\')" loading="lazy">';
      }).join("") : '<span style="color:#64748b;font-size:11px">Loading...</span>') +
      '</div>' +
      (sel ? '<div style="margin-top:8px;color:#00d4ff;font-size:11px;font-weight:700">Selected: '+E(sel.name)+'</div>' : '') +
      '<div class="me-sb-label-sm" style="margin-top:10px">Upload Your Own Sticker</div>' +
      '<div class="me-sb-upload-zone" onclick="$(\'meSbStickerUpload\').click()">' +
      '  <div class="me-sb-uz-text">Upload a custom sticker to use in your edit.</div>' +
      '  <input type="file" id="meSbStickerUpload" accept="image/*" style="display:none" onchange="VMEditing.uploadCustomSticker(this.files)">' +
      '</div>' +
      (ME.editor.uploadedStickers.length ? '<div class="me-sb-grid" style="margin-top:8px">' +
        ME.editor.uploadedStickers.map(function(s){
          return '<img class="me-sb-sticker'+(sel&&sel.url===s.url?' selected':'')+'" src="'+E(s.url)+CACHE_BUST+'" title="'+E(s.name)+'" onclick="VMEditing.pickEditorSticker(\''+E(s.url).replace(/'/g,"\\'")+'\',\''+E(s.name).replace(/'/g,"\\'")+'\')">';
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

  /* ── Edit / Adjustments core ────────────────────────────────────────── */
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
      {id:"camera",label:"Camera",color:"#f97316",blocks: meta ? meta.camera : []},
      {id:"transitions",label:"Transitions",color:"#eab308",blocks: meta ? meta.transitions : []},
      {id:"sfx",label:"SFX",color:"#ec4899",blocks: meta ? meta.sfx : []},
      {id:"emphasis",label:"Text",color:"#a855f7",blocks: meta ? meta.emphasis : []},
      {id:"broll",label:"B-Roll",color:"#8b5cf6",blocks: meta ? meta.broll : []},
      {id:"slowmo",label:"Slow-mo",color:"#ef4444",blocks: meta ? meta.slow_motion : []},
      {id:"music",label:"Music",color:"#3b82f6",blocks: meta ? meta.music : []}
    ];
    var html = '<div class="me-tl-playhead" id="meTlPlayhead"></div>';
    tracks_def.forEach(function(tr) {
      html += '<div class="me-tl-track">' +
        '<div class="me-tl-track-label">'+tr.label+'</div>' +
        '<div class="me-tl-track-content">';
      (tr.blocks||[]).forEach(function(b) {
        var left, width, title;
        if (b.start != null && b.end != null) {
          left = ((b.start||0)/dur*100).toFixed(2);
          width = (((b.end||b.start||0)-(b.start||0))/dur*100).toFixed(2);
          title = fmtTime(b.start)+" - "+fmtTime(b.end);
        } else {
          var at = (b.at||0), w = Math.max(0.3, 0.2);
          left = (at/dur*100).toFixed(2);
          width = (w/dur*100).toFixed(2);
          title = fmtTime(at);
        }
        var effect = b.effect || b.kind || "";
        if (effect) title += " ("+effect+")";
        html += '<div class="me-tl-block '+tr.id+'" style="left:'+left+'%;width:'+Math.max(0.5,width)+'%" title="'+E(title)+'"></div>';
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
    var ai = $("meSbAIInput");
    if (ai) ME.editor._aiInstruction = ai.value;
  }

  function toggleEffect(name) {
    var m = ME.editor.manual;
    var idx = m.effects.indexOf(name);
    if (idx >= 0) m.effects.splice(idx, 1);
    else m.effects.push(name);
    renderToolPanel();
  }

  function toggleAITool(id, on) {
    if (id==="auto-cut") ME.editor.manual.autoCut = !!on;
    else if (id==="auto-captions") ME.editor.manual.captions = !!on;
    renderToolPanel();
  }

  function setCanvasAspect(v) { ME.editor.manual.canvas.aspect = v; renderToolPanel(); }
  function setCanvasMode(v) { ME.editor.manual.canvas.mode = v; renderToolPanel(); }
  function setCanvasBg(v) { ME.editor.manual.canvas.bg = v.replace("#",""); renderToolPanel(); }

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
    renderToolPanel();
  }

  function filterStickers(q) {
    q = (q||"").toLowerCase();
    var grid = $("meSbStickerGrid"); if (!grid) return;
    var filtered = q ? ME.editor.stickerLibrary.filter(function(s){
      return (s.name||"").toLowerCase().indexOf(q)>=0;
    }) : ME.editor.stickerLibrary;
    grid.innerHTML = filtered.map(function(s){
      return '<img class="me-sb-sticker'+(ME.sticker&&ME.sticker.url===s.url?' selected':'')+'" src="'+E(s.url)+CACHE_BUST+'" title="'+E(s.name)+'" onclick="VMEditing.pickEditorSticker(\''+E(s.url).replace(/'/g,"\\'")+'\',\''+E(s.name).replace(/'/g,"\\'")+'\')" loading="lazy">';
    }).join("");
  }

  function uploadCustomSticker(files) {
    if (!files||!files.length) return;
    var f = files[0];
    if (f.size > MAX_FILE_MB*1024*1024) { showToast(f.name+" is over "+MAX_FILE_MB+"MB."); return; }
    var url = URL.createObjectURL(f);
    ME.editor.uploadedStickers.push({url:url, name:f.name});
    pickEditorSticker(url, f.name);
    renderToolPanel();
  }

  function uploadAudio(files) {
    if (!files||!files.length) return;
    var f = files[0];
    var url = URL.createObjectURL(f);
    ME.editor.manual.musicUrl = url;
    ME.editor.manual.musicName = f.name;
    ME.editor.manual.musicVolume = 0.3;
    renderToolPanel();
  }

  function removeMusic() {
    ME.editor.manual.musicUrl = "";
    ME.editor.manual.musicName = "";
    renderToolPanel();
  }

  function uploadVoiceOver(files) { showToast("Voice-over upload recorded. Will be mixed in the next render."); }
  function sbUploadMedia(files) {
    if (!files) return;
    for (var i=0;i<files.length;i++) addFiles([files[i]]);
    renderToolPanel();
  }

  function smartReframe() {
    var m = ME.editor.manual;
    m.canvas.aspect = "9:16";
    m.canvas.mode = "fill";
    m.canvas.reframe = "smart";
    m.camera = true;
    renderToolPanel();
    refineManualOnly("Auto-reframed to 9:16 with subject-tracking (re-rendering).");
  }

  function aiSuggestTransitions() {
    var m = ME.editor.manual;
    m.transitionsMode = "auto";
    m.camera = true;
    renderToolPanel();
    refineManualOnly("AI transitions enabled at the best change points (re-rendering).");
  }

  function refineManualOnly(toastMsg) {
    if (!ME.editor.jobId) { showToast("Edit a clip first, then re-render."); return; }
    pushUndo();
    var body = { job_id: ME.editor.jobId, manual: buildManualPayload(), instruction: "", keep_plan: true };
    var cb = function(r){return r.json();};
    var done = function(d){ handleRefineResult(d, ""); };
    if (typeof apiFetch === "function") {
      apiFetch("/api/editing/refine",{method:"POST",body:JSON.stringify(body)}).then(cb).then(done);
      return;
    }
    postJSON("/api/editing/refine",{method:"POST", headers:{}, timeoutMs: 60000, body: JSON.stringify(body)})
      .then(cb).then(function(d){ done(d); if (toastMsg) showToast(toastMsg); });
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
    renderToolPanel();
  }
  function redoAction() {
    if (!ME.redoStack.length) { showToast("Nothing to redo."); return; }
    ME.undoStack.push(JSON.stringify(ME.editor.manual));
    ME.editor.manual = JSON.parse(ME.redoStack.pop());
    renderToolPanel();
  }

  /* ── Apply AI Edit (new instruction from the AI panel) ──────────────── */
  function applyAIEdit() {
    var input = $("meSbAIInput");
    var instruction = (input && input.value || "").trim();
    var voiceText = (ME.voice && (ME.voice.text||"").trim()) || "";
    if (input) ME.editor._aiInstruction = input.value;
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
      canvas: {aspect: m.canvas.aspect||"9:16", mode: m.canvas.mode||"fill",
               bg: m.canvas.bg||"000000", reframe: m.canvas.reframe||"smart"},
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
      slowmo_factor: m.slowmoFactor||0, auto_cut: m.autoCut!==false,
      intensity: m.intensity||"medium", transitions: m.transitionsMode||"auto",
      camera: !!m.camera, sfx: !!m.sfx
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

  /* ── Voice recording (shared by intro + AI panel) ───────────────────── */
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
  function pickFiles(accept) {
    var input=$("meFilesInput"); if(!input)return;
    try{input.accept = accept||"video/*,image/*,audio/*";}catch(e){}
    input.click();
  }
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
        if (ME.mode==="editor") renderToolPanel();
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
          ME.editor.timeline=job.edit_timeline||null;
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
          ME.editor.timeline=job.edit_timeline||null;
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
    var map={analysis:"Analyzing your video...",planned:"Planning your edit...",broll:"Adding B-roll...",sticker:"Applying the sticker...",
      "slow-motion":"Applying slow motion...",camera:"Applying camera effects...",transitions:"Adding transitions...",sfx:"Adding sound effects...",
      music:"Mixing in the music...",done:"Wrapping up..."};
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

  /* ── Sheeting (mobile) ──────────────────────────────────────────────── */
  function openScrim() { var sc=$("meScrim"); if(sc&&isSmall()) sc.classList.add("show"); }
  function syncScrim() {
    var sc=$("meScrim"); if(!sc)return;
    var f=$("meToolPanel"), p=$("meAiPanel");
    var any = !!(f&&f.classList.contains("open")) || !!(p&&p.classList.contains("open"));
    sc.classList.toggle("show", isSmall() && any);
  }
  function toggleAIPanel(force) {
    var p=$("meAiPanel"); if(!p)return;
    var show = (typeof force==="boolean")? force : !p.classList.contains("open");
    p.classList.toggle("open", show);
    syncScrim();
    if (ME.mode==="intro") updateGo();
  }
  function closeSheets(){
    var f=$("meToolPanel"); if(f)f.classList.remove("open");
    var p=$("meAiPanel"); if(p)p.classList.remove("open");
    if(isSmall()){ var tb=$("meToolPanel"); if(tb) tb.innerHTML=""; }
    syncScrim();
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
    zoomTimeline: zoomTimeline, goToInput: goToInput,
    pickFiles: pickFiles, railClick: railClick, openTool: openTool, closeTool: closeTool,
    toggleAIPanel: toggleAIPanel, closeSheets: closeSheets
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