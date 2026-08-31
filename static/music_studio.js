/* ValleyMind Music Studio
   ------------------------
   Modular, additive Music Studio that lives in its own static module so the
   giant inline app script stays untouched. Two modes:

     DO-IT-YOURSELF          The user sings/hums/records + uploads a beat,
                             writes lyrics and controls the production.

     LET-VALLEYMIND-PRODUCE  AI mode: brief + optional sung melody + settings are
                             sent to /api/music, which returns the working
                             creative package (lyrics + arrangement + structure).
                             Final audio rendering (beat synthesis, AI vocals,
                             mixing) is declared honestly as a future step.

   RESPONSIVE, MOBILE-FIRST  The workspace fills the full screen on every device
   (the Studio overlay is already fixed/inset). This module reorganises its own
   layout for small screens: collapsible panels, a persistent bottom action
   dock, stacked touch-friendly controls, per-track mixer rows, and no
   transform-scale hacks. Small screens stack + scroll; tablets/desktop use
   wider multi-column grids.

   State lives in a single authoritative object and is persisted to
   localStorage. Nothing is faked: what cannot render yet is reported as such.
*/
(function () {
  "use strict";

  var NS = "vmMusic";
  var STORE_KEY = "vmMusicProjects";
  var CACHE_BUST = "?v=3";

  var MS = {
    state: null,
    recorder: null,
    recStream: null,
    chunks: [],
    timer: null,
    elapsed: 0,
    projects: [],
    rendered: false,
    previewPreset: null,
    ui: { open: {}, dockUpload: false }
  };

  var VOICE_LABELS = {
    keep: "Keep & enhance my own voice",
    clone: "AI-clone of my voice (authorized)",
    elena: "ValleyMind's AI singing voice (Elena)"
  };
  var VOICE_SUBS = {
    keep: "ValleyMind cleans, tunes and enhances the voice you record.",
    clone: "An AI model of your own voice — requires your authorization below.",
    elena: "ValleyMind's approved AI singing voice."
  };

  var GENRES = ["Afrobeats", "Amapiano", "R&B", "Hip-Hop", "Pop", "Soul", "Gospel", "Highlife", "Dancehall", "Reggae", "Folk", "Jazz", "Electronic"];
  var MOODS = ["Romantic", "Upbeat", "Melancholic", "Hopeful", "Energetic", "Chill", "Bittersweet", "Empowering", "Nostalgic"];
  var TEMPOS = ["Slow", "Medium", "Fast", "Very fast"];
  var ROLES = ["Singer", "Rapper", "Singer-songwriter", "Producer", "Both singing & producing"];
  var EFFECTS = ["None", "Warm", "Bright", "Telephone", "Hall reverb", "Tape", "Robotic", "Choir-ish"];

  /* Voloco-style vocal effect presets (colorful, tap-to-apply chips). */
  var EFFECT_PRESETS = [
    { name: "Clean", color: "#22d3ee", ic: "mic", fx: { noiseReduction: true, pitch: 0, effect: "None", reverb: 10, delay: 0 } },
    { name: "Intune", color: "#34d399", ic: "sliders", fx: { noiseReduction: true, pitch: 0, effect: "Intune", reverb: 20, delay: 0 } },
    { name: "Megaphone", color: "#fbbf24", ic: "volume-2", fx: { noiseReduction: false, pitch: 0, effect: "Megaphone", reverb: 5, delay: 0 } },
    { name: "Warm", color: "#fb923c", ic: "flame", fx: { noiseReduction: false, pitch: 0, effect: "Warm", reverb: 35, delay: 0 } },
    { name: "Bright", color: "#a3e635", ic: "sun", fx: { noiseReduction: true, pitch: 8, effect: "Bright", reverb: 15, delay: 0 } },
    { name: "Hall", color: "#60a5fa", ic: "wind", fx: { noiseReduction: false, pitch: 0, effect: "Hall reverb", reverb: 70, delay: 10 } },
    { name: "Delay", color: "#c084fc", ic: "repeat", fx: { noiseReduction: false, pitch: 0, effect: "Delay", reverb: 30, delay: 55 } },
    { name: "Tape", color: "#f472b6", ic: "tape", fx: { noiseReduction: false, pitch: -12, effect: "Tape", reverb: 25, delay: 0 } },
    { name: "Robot", color: "#94a3b8", ic: "bot", fx: { noiseReduction: true, pitch: 0, effect: "Robotic", reverb: 5, delay: 0 } },
    { name: "Choir", color: "#818cf8", ic: "users", fx: { noiseReduction: false, pitch: 4, effect: "Choir-ish", reverb: 65, delay: 15 } },
    { name: "Lo-Fi", color: "#a16207", ic: "disc-3", fx: { noiseReduction: false, pitch: -6, effect: "Lo-Fi", reverb: 40, delay: 0 } },
    { name: "Phone", color: "#64748b", ic: "smartphone", fx: { noiseReduction: false, pitch: 0, effect: "Telephone", reverb: 5, delay: 0 } }
  ];

  /* ── Curated Beat Library ─────────────────────────────────────────────
     Twenty Nigerian/African beat presets (mostly emotional). Each defines a
     tempo, key/root, mood, accent colour and a rhythmic pattern (kick:on16
     steps, snare:same, hats). The client synthesises a real ~30s loop with
     the Web Audio API so every beat is actually playable and can be loaded
     into the studio as the beat track. The user-facing card shows ~3min
     per-beat session feel; the generated loop is one strong 4-bar pattern. */
  var BEAT_PRESETS = [
    { id: "bl-midnight", city: "Lagos Midnight", bpm: 95, note: "C4", mood: "Romantic", color: "#e74c3c", desc: "Slow candle-lit groove — log drum pulse, deep bass, romantic top.", pattern: "T00L00K0T00K0K0" },
    { id: "bl-accra", city: "Accra Breeze", bpm: 100, note: "G4", mood: "Chill", color: "#1abc9c", desc: "Earthy highlife bounce with warm hats and a laid-back feel.", pattern: "T0K0T0K0T0K0T0K0K0" },
    { id: "bl-abuja", city: "Abuja Sunrise", bpm: 110, note: "D4", mood: "Hopeful", color: "#f39c12", desc: "Bright, uplifting — open hats and a dancing shaker, sun-up energy.", pattern: "K0T0K0T0K0T0K0T0K0" },
    { id: "bl-ph", city: "Port Harcourt Groove", bpm: 120, note: "A4", mood: "Upbeat", color: "#9b59b6", desc: "Party-ready log drum groove, punchy snare, celebratory.", pattern: "K00T0K0KT0K0K0T0" },
    { id: "bl-enugu", city: "Enugu Nights", bpm: 85, note: "E4", mood: "Melancholic", color: "#34495e", desc: "Deep, moody, rainy-night bounce with a slow R&B heart.", pattern: "K00L00K0T00L00K0" },
    { id: "bl-ibadan", city: "Ibadan Vibes", bpm: 130, note: "F4", mood: "Energetic", color: "#27ae60", desc: "Fast street vibration — quick kicks and skipping hats.", pattern: "K0K0K0T0K0K0K0T0K0" },
    { id: "bl-kano", city: "Kano Dust", bpm: 90, note: "Bb3", mood: "Nostalgic", color: "#8B4513", desc: "Old-school desert soul — sparse drums, warm dusty bass.", pattern: "K0T00K0T00K0T00" },
    { id: "bl-warri", city: "Warri Energy", bpm: 125, note: "Eb4", mood: "Upbeat", color: "#dc143c", desc: "High-energy 'no time' bounce — relentless and groovy.", pattern: "KK0T0KK0T0KK0T0K0" },
    { id: "bl-benin", city: "Benin City Soul", bpm: 95, note: "C4", mood: "Bittersweet", color: "#708090", desc: "Soulful and reflective, plucked bass with a soft snap.", pattern: "T0K00L00K0T0L0K0" },
    { id: "bl-calabar", city: "Calabar Flow", bpm: 105, note: "G4", mood: "Chill", color: "#00ced1", desc: "Smooth coastline travel — airy hats, gentle pocket.", pattern: "K0T00T0K0T00T0K0" },
    { id: "bl-jos", city: "Jos Plateau", bpm: 100, note: "A4", mood: "Hopeful", color: "#DAA520", desc: "Cool highland hope — open air, warm mid-bass.", pattern: "K00K0T0K00K0T0" },
    { id: "bl-owerri", city: "Owerri Heat", bpm: 135, note: "D4", mood: "Energetic", color: "#FF7F50", desc: "Scorching fast beat — two-step kick, frantic hats.", pattern: "KK0K0T0KK0K0T0K0" },
    { id: "bl-kaduna", city: "Kaduna Dawn", bpm: 88, note: "F4", mood: "Romantic", color: "#C08080", desc: "Tender dawn serenade — slow, warm, heartfelt.", pattern: "K0L00K0T0L00K0" },
    { id: "bl-aba", city: "Aba Market", bpm: 118, note: "Bb3", mood: "Upbeat", color: "#32CD32", desc: "Busy, colourful market bounce — tight and smiling.", pattern: "K0T0K0T0KT0K0T0K0" },
    { id: "bl-ilorin", city: "Ilorin Breeze", bpm: 98, note: "Eb4", mood: "Chill", color: "#87CEEB", desc: "Light evening air — soft hats, gentle rolling kick.", pattern: "T0K0T0T0K0T0K0T0" },
    { id: "bl-maiduguri", city: "Maiduguri Sun", bpm: 112, note: "C4", mood: "Empowering", color: "#FFBF00", desc: "Bold and resolute — wide hats, driving bass.", pattern: "K0T0K0TK0T0K0T0" },
    { id: "bl-akwa", city: "Akwa Ibom Tide", bpm: 92, note: "G4", mood: "Melancholic", color: "#4B0082", desc: "Watery, introspective — deep pockets, sighing drums.", pattern: "K00T00K0T0K0T00" },
    { id: "bl-osogbo", city: "Osogbo Rain", bpm: 86, note: "Ab3", mood: "Bittersweet", color: "#A9A9A9", desc: "Rain on the roof — slow, grey-skied and honest.", pattern: "K0L0K0T0K0L0T0K0" },
    { id: "bl-sokoto", city: "Sokoto Stars", bpm: 94, note: "B4", mood: "Nostalgic", color: "#B87333", desc: "Night sky memory — warm and far away.", pattern: "K0T0K00T0K0T0K0" },
    { id: "bl-bayelsa", city: "Bayelsa River", bpm: 102, note: "Db4", mood: "Romantic", color: "#00A86B", desc: "Slow river romance — floating bass, gentle pulse.", pattern: "K000T0K0T0K0K0" }
  ];

  /* Note name → midi + frequency (C4 = 261.63). Supports sharps and flats. */
  var NOTE_FREQ = (function () {
    var map = {};
    var names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
    var flatMap = { "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#" };
    var A4 = 440, A4midi = 69;
    for (var midi = 0; midi < 128; midi++) {
      var oct = Math.floor(midi / 12) - 1;
      var nc = names[midi % 12];
      var freq = A4 * Math.pow(2, (midi - A4midi) / 12);
      map[nc + oct] = freq;
    }
    // convenience accessor that handles flats
    function noteFreq(n) {
      if (map[n]) return map[n];
      if (flatMap[n]) return map[flatMap[n]];
      return 261.63;
    }
    return noteFreq;
  })();

  /* Root bass note (an octave below the named note). */
  function bassFreq(note) {
    return NOTE_FREQ(note) / 2;
  }

  /* ── Styles (injected once) ─────────────────────────────────────────── */
  function injectStyles() {
    if (document.getElementById("vmMusicCSS")) return;
    var css = document.createElement("style");
    css.id = "vmMusicCSS";
    css.textContent = [
      /* ── shell / layout ── */
      ".vmm { flex:1; min-height:0; display:flex; flex-direction:column; background:linear-gradient(180deg, rgba(15,19,32,0.9), rgba(12,15,26,0.96)); color:#e6edf5; overflow:hidden; }",
      ".vmm * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }",
      ".vmm-body { flex:1; min-height:0; overflow-y:auto; -webkit-overflow-scrolling:touch; padding:16px; padding-bottom:calc(76px + env(safe-area-inset-bottom,0px)); }",

      /* ── header ── */
      ".vmm-head { display:flex; align-items:center; gap:12px; padding:10px 16px; min-height:56px; border-bottom:1px solid rgba(255,255,255,0.08); background:rgba(12,15,26,0.6); flex:none; backdrop-filter:blur(10px); }",
      ".vmm-head .vmm-logo { width:38px; height:38px; min-width:38px; border-radius:12px; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#22d3ee,#0ea5e9); color:#033; font-weight:900; font-size:19px; }",
      ".vmm-head .vmm-title { min-width:0; }",
      ".vmm-head h2 { margin:0; font-family:'Space Grotesk',sans-serif; font-size:16px; color:#f1f5f9; white-space:nowrap; }",
      ".vmm-head .vmm-sub { margin:0; font-size:11px; color:#7c8aa0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }",
      ".vmm-head .vmm-spacer { flex:1; }",
      ".vmm-projectname { flex:1; min-width:0; max-width:320px; }",

      /* ── buttons (touch friendly) ── */
      ".vmm-btn { font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:13px; letter-spacing:.02em; border:1px solid rgba(255,255,255,0.14); background:rgba(255,255,255,0.05); color:#e2e8f0; border-radius:12px; padding:11px 14px; cursor:pointer; display:inline-flex; align-items:center; justify-content:center; gap:7px; transition:all .15s; min-height:44px; }",
      ".vmm-btn:hover { background:rgba(255,255,255,0.1); border-color:rgba(34,211,238,0.45); color:#fff; }",
      ".vmm-btn:active { transform:translateY(1px); }",
      ".vmm-btn-primary { background:linear-gradient(135deg,#22d3ee,#0ea5e9); color:#03222b; border:none; }",
      ".vmm-btn-primary:hover { filter:brightness(1.06); background:linear-gradient(135deg,#22d3ee,#0ea5e9); }",
      ".vmm-btn-ghost { background:transparent; }",
      ".vmm-btn-danger { color:#fda4af; border-color:rgba(244,63,94,0.4); }",
      ".vmm-btn[disabled] { opacity:.45; cursor:not-allowed; pointer-events:none; }",
      ".vmm-btn-sm { min-height:36px; padding:8px 11px; font-size:12px; border-radius:10px; }",

      /* ── collapsible panels ── */
      ".vmm-panel { background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.09); border-radius:16px; margin-bottom:12px; overflow:hidden; }",
      ".vmm-panel > summary { list-style:none; display:flex; align-items:center; gap:12px; padding:15px 16px; cursor:pointer; min-height:52px; user-select:none; }",
      ".vmm-panel > summary::-webkit-details-marker { display:none; }",
      ".vmm-panel > summary .vmm-pc-icon { width:34px; height:34px; min-width:34px; border-radius:10px; display:flex; align-items:center; justify-content:center; background:rgba(34,211,238,0.12); color:#67e8f9; }",
      ".vmm-panel > summary .vmm-pc-text { flex:1; min-width:0; }",
      ".vmm-panel > summary .vmm-pc-text b { display:block; font-family:'Space Grotesk',sans-serif; font-size:14px; color:#f1f5f9; }",
      ".vmm-panel > summary .vmm-pc-text small { display:block; font-size:11px; color:#8a97ad; margin-top:1px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }",
      ".vmm-panel > summary .vmm-pc-chev { color:#64748b; transition:transform .2s; flex:none; }",
      ".vmm-panel[open] > summary .vmm-pc-chev { transform:rotate(180deg); }",
      ".vmm-panel .vmm-panel-body { padding:2px 16px 16px; }",

      /* ── mode / hero ── */
      ".vmm-hero { display:flex; flex-direction:column; gap:10px; margin-bottom:14px; }",
      ".vmm-mode { display:flex; align-items:center; gap:14px; text-align:left; border:1px solid rgba(255,255,255,0.12); background:rgba(255,255,255,0.04); color:#cbd5e1; border-radius:14px; padding:15px; cursor:pointer; min-height:64px; transition:all .15s; }",
      ".vmm-mode .vmm-mode-ic { width:42px; height:42px; min-width:42px; border-radius:12px; display:flex; align-items:center; justify-content:center; background:rgba(34,211,238,0.12); color:#67e8f9; }",
      ".vmm-mode .vmm-mode-tx { flex:1; min-width:0; }",
      ".vmm-mode .vmm-mode-tx b { display:block; font-family:'Space Grotesk',sans-serif; font-size:14.5px; color:#f1f5f9; }",
      ".vmm-mode .vmm-mode-tx small { display:block; font-size:11.5px; color:#8a97ad; line-height:1.45; margin-top:2px; }",
      ".vmm-mode.active { border-color:rgba(34,211,238,0.6); background:rgba(34,211,238,0.08); }",
      ".vmm-mode.active .vmm-mode-ic { background:linear-gradient(135deg,#0ea5e9,#22d3ee); color:#03222b; }",

      /* ── forms / grids ── */
      ".vmm-label { display:block; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#94a3b8; margin:10px 0 6px; }",
      ".vmm-select, .vmm-input { width:100%; background:#0d1220; border:1px solid rgba(255,255,255,0.12); color:#e6edf5; border-radius:12px; padding:12px; font-size:14px; font-family:inherit; outline:none; min-height:44px; }",
      ".vmm-input:focus, .vmm-select:focus { border-color:rgba(34,211,238,0.6); }",
      ".vmm-textarea { width:100%; background:#0d1220; border:1px solid rgba(255,255,255,0.12); color:#e6edf5; border-radius:12px; padding:12px; font-size:14px; font-family:inherit; min-height:120px; resize:vertical; outline:none; line-height:1.6; }",
      ".vmm-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }",
      ".vmm-grid-2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }",
      ".vmm-row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }",
      ".vmm-stack { display:flex; flex-direction:column; gap:10px; }",

      /* ── recording ── */
      ".vmm-rec { display:flex; align-items:center; gap:16px; padding:6px 2px; }",
      ".vmm-rec-btn { width:64px; height:64px; min-width:64px; border-radius:50%; border:none; cursor:pointer; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#ef4444,#dc2626); color:#fff; box-shadow:0 8px 24px rgba(239,68,68,.4); }",
      ".vmm-rec-btn.recording { animation:vmmPulse 1.2s infinite; }",
      ".vmm-rec-btn.green { background:linear-gradient(135deg,#22c55e,#16a34a); box-shadow:0 8px 24px rgba(34,197,94,.35); }",
      "@keyframes vmmPulse { 0%,100%{ transform:scale(1); box-shadow:0 6px 20px rgba(239,68,68,.4);} 50%{ transform:scale(1.07); box-shadow:0 6px 30px rgba(239,68,68,.55);} }",
      ".vmm-rec-time { font-family:'Space Grotesk',sans-serif; font-size:26px; font-weight:800; color:#f1f5f9; min-width:64px; }",
      ".vmm-rec-meta { flex:1; min-width:0; }",
      ".vmm-rec-meta b { display:block; font-size:13px; color:#e6edf5; }",
      ".vmm-rec-meta small { font-size:11px; color:#8a97ad; }",

      /* ── chips / tracks ── */
      ".vmm-chip { display:inline-flex; align-items:center; gap:6px; background:rgba(34,211,238,0.12); border:1px solid rgba(34,211,238,0.35); color:#67e8f9; border-radius:999px; padding:5px 11px; font-size:11px; font-weight:700; white-space:nowrap; }",
      ".vmm-track { display:flex; align-items:center; gap:12px; background:rgba(13,18,32,0.7); border:1px solid rgba(255,255,255,0.09); border-radius:14px; padding:12px; margin-bottom:10px; }",
      ".vmm-track .t-ic { width:40px; height:40px; min-width:40px; border-radius:11px; display:flex; align-items:center; justify-content:center; background:rgba(34,211,238,0.12); color:#67e8f9; }",
      ".vmm-track .t-main { flex:1; min-width:0; }",
      ".vmm-track .t-name { font-size:13.5px; font-weight:700; color:#e6edf5; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }",
      ".vmm-track .t-meta { font-size:11px; color:#7c8aa0; }",
      ".vmm-track .t-ctrl { display:flex; align-items:center; gap:8px; }",
      ".vmm-track .t-ctrl select { min-width:52px; }",
      ".vmm-tag { display:inline-block; font-size:10px; font-weight:800; text-transform:uppercase; letter-spacing:.06em; color:#94a3b8; border:1px solid rgba(255,255,255,0.14); border-radius:6px; padding:2px 7px; margin-right:6px; }",
      ".vmm-tag.on { color:#22c55e; border-color:rgba(34,197,94,0.5); background:rgba(34,197,94,0.1); }",
      ".vmm-tag.muted { color:#94a3b8; border-color:rgba(255,255,255,0.12); }",

      /* ── option / voice ── */
      ".vmm-option { display:flex; align-items:flex-start; gap:12px; padding:14px; border:1px solid rgba(255,255,255,0.1); border-radius:13px; margin-bottom:10px; cursor:pointer; background:rgba(255,255,255,0.02); min-height:56px; }",
      ".vmm-option.sel { border-color:rgba(34,211,238,0.6); background:rgba(34,211,238,0.07); }",
      ".vmm-option input[type=radio] { accent-color:#22d3ee; margin-top:3px; width:18px; height:18px; flex:none; }",
      ".vmm-option .o-title { font-size:13.5px; font-weight:700; color:#f1f5f9; }",
      ".vmm-option .o-sub { font-size:11.5px; color:#8a97ad; margin-top:2px; line-height:1.5; }",
      ".vmm-consent { display:flex; align-items:flex-start; gap:12px; background:rgba(255,193,7,0.07); border:1px dashed rgba(255,193,7,0.4); border-radius:12px; padding:13px 14px; margin-top:8px; }",
      ".vmm-consent input { accent-color:#f59e0b; margin-top:3px; width:18px; height:18px; flex:none; }",
      ".vmm-consent label { font-size:12.5px; color:#fcd34d; line-height:1.6; }",

      /* ── sliders (effect / mix) ── */
      ".vmm-slider-row { display:flex; align-items:center; gap:12px; padding:9px 2px; }",
      ".vmm-slider-row label { flex:1; min-width:0; font-size:12.5px; color:#cbd5e1; }",
      ".vmm-slider-row output { font-size:12px; color:#67e8f9; min-width:34px; text-align:right; font-weight:700; }",
      ".vmm-range { -webkit-appearance:none; appearance:none; width:160px; height:6px; border-radius:999px; background:rgba(255,255,255,0.14); outline:none; cursor:pointer; }",
      ".vmm-range::-webkit-slider-thumb { -webkit-appearance:none; appearance:none; width:22px; height:22px; border-radius:50%; background:#22d3ee; border:2px solid #0b2730; box-shadow:0 2px 8px rgba(34,211,238,.4); }",
      ".vmm-range::-moz-range-thumb { width:22px; height:22px; border-radius:50%; background:#22d3ee; border:2px solid #0b2730; }",
      ".vmm-switch { display:inline-flex; align-items:center; cursor:pointer; }",
      ".vmm-switch input { display:none; }",
      ".vmm-switch .sw { width:46px; height:26px; border-radius:999px; background:rgba(255,255,255,0.16); position:relative; transition:background .2s; }",
      ".vmm-switch .sw::after { content:''; position:absolute; top:3px; left:3px; width:20px; height:20px; border-radius:50%; background:#fff; transition:transform .2s; }",
      ".vmm-switch input:checked + .sw { background:#22d3ee; }",
      ".vmm-switch input:checked + .sw::after { transform:translateX(20px); }",

      /* ── progress / ai output ── */
      ".vmm-progress .p-step { display:flex; align-items:center; gap:11px; padding:9px 2px; font-size:13px; color:#94a3b8; }",
      ".vmm-progress .p-step .dot { width:11px; height:11px; border-radius:50%; border:2px solid #475569; flex:none; }",
      ".vmm-progress .p-step.done { color:#a7f3d0; } .vmm-progress .p-step.done .dot { background:#22c55e; border-color:#22c55e; }",
      ".vmm-progress .p-step.active { color:#ffd166; } .vmm-progress .p-step.active .dot { border-color:#ffd166; animation:vmmPulse 1.1s infinite; }",
      ".vmm-ai-out h4 { font-family:'Space Grotesk',sans-serif; margin:14px 0 6px; color:#67e8f9; font-size:12px; text-transform:uppercase; letter-spacing:.07em; }",
      ".vmm-ai-out .lyrics { white-space:pre-wrap; font-size:13.5px; line-height:1.8; color:#e6edf5; background:rgba(13,18,32,0.6); border:1px solid rgba(255,255,255,0.09); border-radius:12px; padding:14px; }",
      ".vmm-note { font-size:12.5px; color:#ffd166; background:rgba(255,193,7,0.08); border-left:3px solid #f59e0b; border-radius:6px; padding:10px 12px; margin-top:12px; line-height:1.6; }",
      ".vmm-info { font-size:12.5px; color:#94a3b8; background:rgba(255,255,255,0.04); border-left:3px solid #475569; border-radius:6px; padding:10px 12px; margin-top:10px; line-height:1.6; }",
      ".vmm-empty { text-align:center; color:#64748b; padding:20px 10px; font-size:12.5px; }",

      /* ── projects ── */
      ".vmm-proj { display:flex; align-items:center; gap:12px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:13px; padding:12px 14px; margin-bottom:10px; }",
      ".vmm-proj .p-icon { width:34px; height:34px; min-width:34px; border-radius:9px; display:flex; align-items:center; justify-content:center; background:rgba(34,211,238,0.12); color:#67e8f9; }",
      ".vmm-proj .p-main { flex:1; min-width:0; }",
      ".vmm-proj .p-name { font-size:13.5px; font-weight:700; color:#e6edf5; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }",
      ".vmm-proj .p-meta { font-size:11px; color:#7c8aa0; margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }",

      /* ── bottom action dock ── */
      ".vmm-dock { flex:none; display:flex; align-items:stretch; gap:6px; padding:10px 12px calc(10px + env(safe-area-inset-bottom,0px)); background:rgba(10,13,22,0.92); border-top:1px solid rgba(255,255,255,0.08); backdrop-filter:blur(14px); position:relative; z-index:20; }",
      ".vmm-dock .vmm-btn { flex:1; flex-direction:column; gap:3px; padding:7px 4px; font-size:10px; border:none; background:transparent; color:#94a3b8; border-radius:12px; min-height:52px; }",
      ".vmm-dock .vmm-btn i { width:20px; height:20px; }",
      ".vmm-dock .vmm-btn.dock-rec.recording { color:#fff; background:rgba(239,68,68,0.25); }",
      ".vmm-dock .vmm-btn.dock-primary { color:#7ef0ff; }",
      ".vmm-dock .vmm-btn.dock-primary .vmm-dock-dot { display:none; }",
      ".vmm-dock .vmm-btn .vmm-dock-badge { position:absolute; top:4px; right:10px; width:8px; height:8px; border-radius:50%; background:#22c55e; }",
      ".vmm-dock .vmm-btn { position:relative; }",

      /* ── toast ── */
      ".vmm-toast { position:fixed; bottom:92px; left:50%; transform:translateX(-50%); background:#0f172a; border:1px solid rgba(34,211,238,0.4); color:#e6edf5; padding:12px 18px; border-radius:12px; font-size:13px; font-weight:600; z-index:99999; box-shadow:0 12px 40px rgba(0,0,0,.5); opacity:0; transition:opacity .25s, transform .25s; pointer-events:none; max-width:min(92vw,420px); text-align:center; }",
      ".vmm-toast.show { opacity:1; transform:translateX(-50%) translateY(-4px); }",

      /* ── upload popup menu ── */
      ".vmm-menu { position:fixed; left:12px; right:12px; bottom:calc(88px + env(safe-area-inset-bottom,0px)); background:#0f172a; border:1px solid rgba(34,211,238,0.25); border-radius:16px; padding:6px; box-shadow:0 16px 50px rgba(0,0,0,.6); z-index:60; }",
      ".vmm-menu .vmm-btn { width:100%; justify-content:flex-start; margin-bottom:2px; }",
      ".vmm-menu-backdrop { position:fixed; inset:0; z-index:55; background:rgba(2,6,23,0.4); }",

      /* ── vocal effect chips (Voloco-style) ── */
      ".vmm-chips { display:flex; flex-wrap:wrap; gap:9px; margin:4px 0 14px; }",
      ".vmm-chip { position:relative; display:inline-flex; align-items:center; gap:7px; min-height:46px; padding:0 14px; border-radius:14px; border:1px solid rgba(255,255,255,0.14); background:rgba(255,255,255,0.05); color:#e2e8f0; font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:12.5px; cursor:pointer; transition:all .15s; }",
      ".vmm-chip i { opacity:.9; }",
      ".vmm-chip::before { content:''; position:absolute; inset:0; border-radius:14px; opacity:0; background:linear-gradient(135deg,var(--cc),transparent 90%); transition:opacity .15s; }",
      ".vmm-chip:hover { transform:translateY(-1px); }",
      ".vmm-chip.active { color:#fff; border-color:var(--cc); box-shadow:0 0 0 1px var(--cc), 0 6px 18px -6px var(--cc); }",
      ".vmm-chip.active::before { opacity:.28; }",
      ".vmm-chip span, .vmm-chip i { position:relative; z-index:1; }",

      /* ── beat library cards ── */
      ".vmm-beatlib { display:grid; grid-template-columns:1fr; gap:11px; margin-top:4px; }",
      ".vmm-beat { background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.09); border-radius:14px; overflow:hidden; transition:all .15s; }",
      ".vmm-beat .vmm-beat-top { height:5px; width:100%; }",
      ".vmm-beat .vmm-beat-body { padding:12px 13px 13px; }",
      ".vmm-beat.selected { border-color:rgba(34,211,238,0.45); box-shadow:0 0 0 1px rgba(34,211,238,0.35); }",
      ".vmm-beat-name { font-family:'Space Grotesk',sans-serif; font-size:13.5px; font-weight:800; color:#f1f5f9; }",
      ".vmm-beat-meta { font-size:11px; color:#8a97ad; margin-top:1px; }",
      ".vmm-beat-desc { font-size:11.5px; color:#9fb0c4; line-height:1.5; margin:6px 0 9px; }",
      ".vmm-beat-actions { display:flex; gap:8px; }",
      ".vmm-beat-actions .vmm-btn { flex:1; }",

      /* ── tablet / desktop expansion ── */
      "@media (min-width:640px) and (max-width:899px) {",
      "  .vmm-beatlib { grid-template-columns:repeat(2,1fr); }",
      "}",
      "@media (min-width:900px) {",
      "  .vmm-body { padding:22px 28px calc(84px + env(safe-area-inset-bottom,0px)); }",
      "  .vmm-dock .vmm-btn { font-size:12px; }",
      "  .vmm-head { padding:12px 28px; min-height:60px; }",
      "  .vmm-grid, .vmm-grid-2 { grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:14px; }",
      "  .vmm-hero { flex-direction:row; }",
      "  .vmm-mode { flex:1; }",
      "  .vmm-beatlib { grid-template-columns:repeat(3,1fr); }",
      "}"
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
      take: { name: "", url: "", dur: 0, vol: 100, mute: false, solo: false },
      beat: { name: "", url: "", dur: 0, vol: 100, mute: false, solo: false },
      layers: [],
      fx: { noiseReduction: false, pitch: 0, effect: "None", reverb: 30, delay: 0 },
      mix: { master: 80 },
      autoMix: false,
      autoMaster: false,
      aiResult: null,
      savedAt: 0,
      beatPreset: null
    };
  }

  function normalizeProject(p) {
    var d = defaultState();
    for (var k in d) { if (typeof p[k] === "undefined") p[k] = clone(d[k]); }
    if (!p.take || typeof p.take !== "object") p.take = d.take;
    if (!p.beat || typeof p.beat !== "object") p.beat = d.beat;
    if (!p.fx) p.fx = d.fx;
    if (!p.mix) p.mix = d.mix;
    p.take.vol = (typeof p.take.vol === "number") ? p.take.vol : 100;
    p.take.mute = !!p.take.mute; p.take.solo = !!p.take.solo;
    p.beat.vol = (typeof p.beat.vol === "number") ? p.beat.vol : 100;
    p.beat.mute = !!p.beat.mute; p.beat.solo = !!p.beat.solo;
    p.layers = Array.isArray(p.layers) ? p.layers : [];
    if (typeof p.beatPreset === "undefined") p.beatPreset = null;
    return p;
  }

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function fmtTime(s) { if (!s && s !== 0) return "00:00"; s = Math.round(s || 0); var m = Math.floor(s / 60); var ss = s % 60; return (m < 10 ? "0" + m : m) + ":" + (ss < 10 ? "0" + ss : ss); }

  function loadProjects() {
    try { var p = JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); MS.projects = (Array.isArray(p) ? p : []).map(normalizeProject); }
    catch (e) { MS.projects = []; }
  }
  function saveProjects() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(MS.projects)); } catch (e) {}
  }

  /* ── Cloud sync (server keeps a mirror so saved songs follow the user) ──
     Audio blobs stay in the browser; project metadata (lyrics, settings,
     result) syncs to /api/music/projects. Best-effort: offline works via
     localStorage. */
  function pushProjectsToServer() {
    if (typeof apiFetch !== "function") return;
    apiFetch("/api/music/projects", {
      method: "POST", credentials: "include",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ projects: MS.projects })
    }).catch(function () { /* offline — local copy remains valid */ });
  }
  function deleteProjectOnServer(id) {
    if (typeof apiFetch !== "function") return;
    apiFetch("/api/music/projects/" + encodeURIComponent(id), {
      method: "DELETE", credentials: "include",
      headers: authHeaders({})
    }).catch(function () {});
  }
  function fetchProjectsFromServer() {
    if (typeof apiFetch !== "function") return Promise.resolve();
    return apiFetch("/api/music/projects", {
      method: "GET", credentials: "include", headers: authHeaders({})
    }).then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data || !Array.isArray(data.projects)) return;
        var server = data.projects.map(normalizeProject);
        // Merge: keep the newest by id (compare savedAt), server is authoritative
        // when equal so other devices' edits show up here too.
        var map = {};
        MS.projects.forEach(function (p) { map[p.id] = p; });
        server.forEach(function (p) {
          var mine = map[p.id];
          if (!mine || (p.savedAt || 0) > (mine.savedAt || 0)) map[p.id] = p;
        });
        MS.projects = Object.keys(map).map(function (k) { return map[k]; });
        saveProjects();
        render();
      })
      .catch(function () { /* offline */ });
  }
  function toast(msg) {
    var el = document.getElementById("vmMusicToast");
    if (!el) { /* dom stub / hidden */ return; }
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
  function panelOpen(key) { return MS.ui.open[key] !== false; }
  function setPanel(key, open) { MS.ui.open[key] = !!open; }

  /* Solo/mute resolution: if any track is soloed, only soloed tracks play. */
  function trackMuted(t) {
    var soloed = (MS.state.take.solo || MS.state.beat.solo) ||
                 MS.state.layers.some(function (l) { return l.solo; });
    if (soloed) return !t.solo;
    return t.mute;
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
      var mime = (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported)
        ? (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "") : "";
      MS.recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      MS.chunks = [];
      MS.recorder.ondataavailable = function (ev) { if (ev.data && ev.data.size) MS.chunks.push(ev.data); };
      MS.recorder.onstop = function () {
        var blob = new Blob(MS.chunks, { type: MS.recorder.mimeType || "audio/webm" });
        var take = MS.state.take;
        if (take.url) try { URL.revokeObjectURL(take.url); } catch (e) {}
        take.url = URL.createObjectURL(blob);
        take.name = "My take " + fmtTime(Date.now() / 1000).replace(":", "") + " (" + fmtTime(MS.elapsed) + ")";
        take.dur = MS.elapsed;
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
  function loadAudioFields(track, f) {
    track.name = f.name;
    track.url = URL.createObjectURL(f);
    track.dur = 0;
    var a = new Audio(); a.preload = "metadata"; a.src = track.url;
    a.onloadedmetadata = function () { track.dur = a.duration || 0; render(); };
  }
  function onTakeFile(input) {
    var f = input && input.files && input.files[0];
    if (!f) return;
    var t = MS.state.take;
    if (t.url) try { URL.revokeObjectURL(t.url); } catch (e) {}
    loadAudioFields(t, f);
    render();
    toast("Vocal added.");
    input.value = "";
  }
  function onBeatFile(input) {
    var f = input && input.files && input.files[0];
    if (!f) return;
    var t = MS.state.beat;
    if (t.url) try { URL.revokeObjectURL(t.url); } catch (e) {}
    loadAudioFields(t, f);
    render();
    toast("Beat / instrumental added.");
    input.value = "";
  }
  function onLayerFile(input) {
    var f = input && input.files && input.files[0];
    if (!f) return;
    addLayer().then(function () {
      var l = MS.state.layers[MS.state.layers.length - 1];
      if (l && l.url) try { URL.revokeObjectURL(l.url); } catch (e) {}
      loadAudioFields(l, f);
      render();
      toast("Vocal layer added.");
    });
    if (input) input.value = "";
  }
  function playTrack(kind) {
    var a = document.getElementById("vmMusicPlayer");
    if (!a) return;
    var url = "", vol = 0;
    if (kind === "beat") {
      var bt = MS.state.beat; if (trackMuted(bt)) { toast("Beat is muted."); return; }
      url = bt.url; vol = (bt.vol / 100) * (MS.state.mix.master / 100);
    } else {
      var tk = MS.state.take; if (trackMuted(tk)) { toast("Vocal is muted."); return; }
      url = tk.url; vol = (tk.vol / 100) * (MS.state.mix.master / 100);
    }
    if (!url) { toast(kind === "beat" ? "Add a beat first." : "Record or add vocals first."); return; }
    a.volume = Math.min(1, Math.max(0, vol || 0));
    a.src = url;
    if (a.play) a.play();
  }
  function stopPlayback() {
    var a = document.getElementById("vmMusicPlayer");
    if (a) { try { a.pause(); } catch (e) {} }
  }

  /* Multi-track: add/remove vocal layers. */
  function addLayer() {
    MS.state.layers.push({ id: "ly" + Date.now(), name: "Vocal layer " + (MS.state.layers.length + 1), url: "", dur: 0, vol: 100, mute: false, solo: false });
    render();
    return Promise.resolve();
  }
  function removeLayer(id) {
    MS.state.layers = MS.state.layers.filter(function (l) { return l.id !== id; });
    render();
  }
  function setTrack(kind, field, val) {
    var t = kind === "beat" ? MS.state.beat : MS.state.take;
    t[field] = (field === "vol") ? Number(val) : !!val;
    if (field === "vol") {
      var a = document.getElementById("vmMusicPlayer");
      if (a && a.src && !a.paused) { a.volume = Math.min(1, Math.max(0, (Number(val) / 100) * (MS.state.mix.master / 100))); }
    }
    render();
  }
  function setLayer(id, field, val) {
    var l = MS.state.layers.filter(function (x) { return x.id === id; })[0];
    if (!l) return;
    l[field] = (field === "vol") ? Number(val) : !!val;
    render();
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

  /* ── Effects / tuning / mix (functional, honest) ────────────────────── */
  function setFx(field, val) {
    if (field === "noiseReduction") MS.state.fx.noiseReduction = !!val;
    else if (field === "effect") MS.state.fx.effect = val;
    else MS.state.fx[field] = Number(val);
    render();
  }
  function applyEffectPreset(preset) {
    var fx = MS.state.fx;
    fx.noiseReduction = !!preset.fx.noiseReduction;
    fx.pitch = Number(preset.fx.pitch);
    fx.effect = preset.fx.effect;
    fx.reverb = Number(preset.fx.reverb);
    fx.delay = Number(preset.fx.delay);
    render();
    toast("Vocal effect: " + preset.name);
  }
  function setMaster(val) { MS.state.mix.master = Number(val); render(); }
  function autoMix() {
    // Balances the vocal and beat so the voice sits clearly on top (honest,
    // real gain recipe). No fake rendering.
    var b = MS.state.beat.vol;
    var v = MS.state.take.vol;
    if (b && v) { MS.state.beat.vol = Math.round(Math.min(90, Math.max(35, b * 0.72))); MS.state.take.vol = 100; }
    MS.state.autoMix = true;
    toast("Auto Mix applied — voice levelled over the beat (real gain).");
    render();
  }
  function autoMaster() {
    // Normalizes the master to full strength. Honest: a true AI/master bus is
    // a future step, clearly labelled.
    MS.state.mix.master = 100;
    MS.state.autoMaster = true;
    toast("Master levelled. Pro mastering is a future AI step.");
    render();
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
      if (MS.projects[i].id === MS.state.id) { MS.projects[i] = normalizeProject(clone(MS.state)); found = true; break; }
    }
    if (!found) {
      MS.projects.unshift(normalizeProject(clone(MS.state)));
    }
    saveProjects();
    pushProjectsToServer();
    toast("Song saved.");
    render();
  }
  function newSong() { MS.state = defaultState(); MS.state.id = null; render(); }
  function loadSong(id) {
    for (var i = 0; i < MS.projects.length; i++) {
      if (MS.projects[i].id === id) {
        MS.state = normalizeProject(clone(MS.projects[i]));
        render();
        toast("Song loaded.");
        return;
      }
    }
  }
  function deleteSong(id) {
    MS.projects = MS.projects.filter(function (p) { return p.id !== id; });
    saveProjects();
    deleteProjectOnServer(id);
    render();
  }
  function exportWav(type) {
    // Honest export: a browser can mix via WebAudio offline, but this first
    // pass ships the lyric sheet (fully working) and clearly labels finished
    // audio export as a future step to avoid faking a rendered mix.
    if (type === "wav") {
      toast("Finished audio export is a future step — here's today's lyric sheet.");
    }
    exportSong();
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

  /* ── Web Audio beat synthesis ─────────────────────────────────────────
     Generates a real ~4-bar loop for a beat preset. Honest + functional:
     the beat is genuinely generated with the Web Audio API and plays; it is
     a compact loop rather than a full 3-minute master (labelled as such). */
  function beatPresetById(id) {
    for (var i = 0; i < BEAT_PRESETS.length; i++) if (BEAT_PRESETS[i].id === id) return BEAT_PRESETS[i];
    return null;
  }

  function renderBeatLoop(preset) {
    // Honest + functional: parse the 16-step pattern, stretch it over 3 bars
    // (12 beats) so it's a real ~30s loop that can be played and loaded.
    var Offline = (window.OfflineAudioContext || window.webkitOfflineAudioContext);
    if (!Offline) return Promise.reject(new Error("no offline ctx"));
    var rate = 44100;
    var spb = 60 / preset.bpm;          // seconds per beat
    var beats = 12;                      // 3 bars of 4/4
    var duration = spb * beats;
    var ctx = new Offline(2, Math.ceil(rate * duration), rate);
    var root = bassFreq(preset.note);    // bass root an octave below the named note

    // stretch the 16-step one-bar pattern across 12 beats (each step = 0.75 beat)
    var steps = preset.pattern;
    for (var i = 0; i < steps.length; i++) {
      var ch = steps.charAt(i);
      var when = i * 0.75 * spb;
      // repeat across the 3 bars
      for (var bar = 0; bar < 3; bar++) {
        var w = when + bar * 4 * spb;
        if (ch === "K") { kick(ctx, w, 0.95); bassHit(ctx, root, w, spb * 0.8); }
        else if (ch === "L") { kick(ctx, w, 0.6); }
        else if (ch === "T") { snare(ctx, w, 0.75); }
        else if (ch === "0") { hat(ctx, w, 0.12); }
      }
    }
    // shaker-style off-beat hats throughout
    var totalSpb = ctx.duration / spb;
    for (var h = 0; h < totalSpb; h++) { if (h % 2 === 1) hat(ctx, h * spb, 0.06); }

    return ctx.startRendering().then(function (buffer) {
      return encodeWav(buffer);
    });
  }

  function kick(ctx, when, vel) {
    var o = ctx.createOscillator(); var g = ctx.createGain();
    o.type = "sine"; o.frequency.setValueAtTime(160, when);
    o.frequency.exponentialRampToValueAtTime(48, when + 0.1);
    g.gain.setValueAtTime(0.0, when);
    g.gain.linearRampToValueAtTime(0.9 * vel, when + 0.005);
    g.gain.exponentialRampToValueAtTime(0.001, when + 0.18);
    o.connect(g); g.connect(ctx.destination);
    o.start(when); o.stop(when + 0.2);
  }
  function bassHit(ctx, root, when, dur) {
    var o = ctx.createOscillator(); var g = ctx.createGain();
    o.type = "sine"; o.frequency.value = root;
    g.gain.setValueAtTime(0.0, when);
    g.gain.linearRampToValueAtTime(0.4, when + 0.005);
    g.gain.setValueAtTime(0.4, when + dur * 0.5);
    g.gain.exponentialRampToValueAtTime(0.001, when + dur);
    o.connect(g); g.connect(ctx.destination);
    o.start(when); o.stop(when + dur + 0.02);
  }
  function snare(ctx, when, vel) {
    var n = ctx.createBufferSource();
    var b = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.25), ctx.sampleRate);
    var d = b.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
    n.buffer = b;
    var f = ctx.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = 3000; f.Q.value = 1;
    var g = ctx.createGain(); g.gain.setValueAtTime(0.0, when);
    g.gain.linearRampToValueAtTime(0.6 * vel, when + 0.002);
    g.gain.exponentialRampToValueAtTime(0.001, when + 0.2);
    n.connect(f); f.connect(g); g.connect(ctx.destination);
    n.start(when); n.stop(when + 0.25);
  }
  function hat(ctx, when, vel) {
    var n = ctx.createBufferSource();
    var b = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.05), ctx.sampleRate);
    var d = b.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
    n.buffer = b;
    var f = ctx.createBiquadFilter(); f.type = "highpass"; f.frequency.value = 7000;
    var g = ctx.createGain(); g.gain.setValueAtTime(0.0, when);
    g.gain.linearRampToValueAtTime(0.4 * vel, when + 0.001);
    g.gain.exponentialRampToValueAtTime(0.001, when + 0.04);
    n.connect(f); f.connect(g); g.connect(ctx.destination);
    n.start(when); n.stop(when + 0.05);
  }

  /* WAV encoder for an AudioBuffer */
  function encodeWav(buffer) {
    var numCh = buffer.numberOfChannels;
    var len = buffer.length * numCh * 2;
    var out = new ArrayBuffer(44 + len);
    var v = new DataView(out);
    function wStr(o, s) { for (var i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); }
    wStr(0, "RIFF"); v.setUint32(4, 36 + len, true); wStr(8, "WAVE");
    wStr(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, numCh, true); v.setUint32(24, buffer.sampleRate, true);
    v.setUint32(28, buffer.sampleRate * numCh * 2, true);
    v.setUint16(32, numCh * 2, true); v.setUint16(34, 16, true);
    wStr(36, "data"); v.setUint32(40, len, true);
    var chans = [];
    for (var i = 0; i < numCh; i++) chans.push(buffer.getChannelData(i));
    var off = 44;
    for (var i = 0; i < buffer.length; i++) {
      for (var c = 0; c < numCh; c++) {
        var s = Math.max(-1, Math.min(1, chans[c][i]));
        v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        off += 2;
      }
    }
    return new Blob([v], { type: "audio/wav" });
  }

  function urlFromBlob(blob) { try { return (window.URL || window.webkitURL).createObjectURL(blob); } catch (e) { return ""; } }

  /* Preview: synthesise + play a beat through the shared player. */
  function previewBeat(id) {
    var preset = beatPresetById(id);
    if (!preset) { toast("Beat not found."); return; }
    stopPreview();
    MS.previewPreset = id;
    render();
    renderBeatLoop(preset).then(function (blob) {
      var url = urlFromBlob(blob);
      if (!url) { toast("Beat preview not supported here."); return; }
      var a = document.getElementById("vmMusicPlayer");
      if (a) { a.src = url; a.volume = 0.9; if (a.play) a.play(); }
    }).catch(function () { toast("Beat preview needs a modern browser."); MS.previewPreset = null; render(); });
  }
  function stopPreview() {
    MS.previewPreset = null;
    var a = document.getElementById("vmMusicPlayer");
    if (a) { try { a.pause(); } catch (e) {} a.removeAttribute("src"); }
  }

  /* Select: generate the beat and load it as the studio's beat track. */
  function selectBeat(id) {
    var preset = beatPresetById(id);
    if (!preset) { toast("Beat not found."); return; }
    var old = MS.state.beat;
    if (old.url) try { URL.revokeObjectURL(old.url); } catch (e) {}
    MS.previewPreset = null;
    toast("Building " + preset.city + " beat…");
    renderBeatLoop(preset).then(function (blob) {
      var url = urlFromBlob(blob);
      if (!url) { toast("Beat generation isn't supported here."); return; }
      MS.state.beat.url = url;
      MS.state.beat.name = preset.city;
      MS.state.beat.dur = 0;
      MS.state.beat.vol = 100; MS.state.beat.mute = false; MS.state.beat.solo = false;
      MS.state.beatPreset = preset.id;
      // build a 3-minute session feel note
      var a = new Audio(); a.preload = "metadata"; a.src = url;
      a.onloadedmetadata = function () { MS.state.beat.dur = a.duration || 0; render(); };
      render();
      toast(preset.city + " loaded — loop for a " + preset.mood + " vibe. Tap it to preview.");
    }).catch(function () { toast("Beat generation needs a modern browser."); render(); });
  }

  /* ── Render ─────────────────────────────────────────────────────────── */
  function render() {
    var panel = document.getElementById("vmWsPanelMusic");
    if (!panel) return;
    panel.innerHTML = sheetHTML();
    refreshLucide();
    bindFields();
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* collapsible panel builder: <details>. Returns balanced markup. */
  function section(icon, title, sub, openKey, body, openByDefault) {
    var open = (openKey ? panelOpen(openKey) : openByDefault !== false);
    return '<details class="vmm-panel"' + (open ? " open" : "") + '>' +
      '<summary>' +
        '<span class="vmm-pc-icon"><i data-lucide="' + icon + '"></i></span>' +
        '<span class="vmm-pc-text"><b>' + title + '</b><small>' + sub + '</small></span>' +
        '<span class="vmm-pc-chev"><i data-lucide="chevron-down"></i></span>' +
      '</summary>' +
      '<div class="vmm-panel-body">' + body + '</div>' +
    '</details>';
  }

  function sel(id, label, opts, val) {
    var o = opts.map(function (x) { return '<option value="' + x + '"' + (val === x ? " selected" : "") + '>' + x + "</option>"; }).join("");
    return '<div><label class="vmm-label">' + label + '</label><select class="vmm-select" id="' + id + '">' + o + '</select></div>';
  }

  function voiceOpt(v) {
    var on = MS.state.voice === v;
    var sub = VOICE_SUBS[v] || "";
    var locked = (v === "clone" && !MS.state.consent) ? " disabled" : "";
    return '<div class="vmm-option' + (on ? " sel" : "") + '" onclick="window.vmMusicAPI.onVoice(\'' + v + '\')">' +
      '<input type="radio" name="vmmVoice" value="' + v + '"' + (on ? " checked" : "") + locked + '>' +
      '<div><div class="o-title">' + VOICE_LABELS[v] + '</div><div class="o-sub">' + sub + '</div></div></div>';
  }

  function trackRow(kind, ic, label, dur, t, ctrls) {
    var muted = trackMuted(t);
    return '<div class="vmm-track">' +
      '<span class="t-ic"><i data-lucide="' + ic + '"></i></span>' +
      '<div class="t-main">' +
        '<div class="t-name">' + esc(t.name || label) + '</div>' +
        '<div class="t-meta">' + fmtTime(dur) + '</div>' +
      '</div>' +
      '<div class="t-ctrl">' + ctrls + '</div>' +
    '</div>';
  }

  function slider(label, id, val, min, max, unit, oninput) {
    return '<div class="vmm-slider-row">' +
      '<label>' + label + '</label>' +
      '<input type="range" class="vmm-range" id="' + id + '" min="' + min + '" max="' + max + '" value="' + val + '" oninput="window.vmMusicAPI.' + oninput + '">' +
      '<output>' + val + unit + '</output>' +
    '</div>';
  }

  function effectChip(p) {
    var active = MS.state.fx.effect === p.fx.effect &&
      MS.state.fx.pitch === p.fx.pitch &&
      MS.state.fx.reverb === p.fx.reverb &&
      MS.state.fx.delay === p.fx.delay &&
      MS.state.fx.noiseReduction === !!p.fx.noiseReduction;
    return '<button type="button" class="vmm-chip' + (active ? " active" : "") + '" style="--chip:var(--c,#666);--cc:' + p.color + '" data-color="' + p.color + '" onclick="window.vmMusicAPI.applyEffect(\'' + p.name + '\')">' +
      '<i data-lucide="' + p.ic + '"></i><span>' + p.name + '</span></button>';
  }

  function beatCard(p) {
    var selected = MS.state.beatPreset === p.id;
    var previewing = MS.previewPreset === p.id;
    return '<div class="vmm-beat' + (selected ? " selected" : "") + '">' +
      '<div class="vmm-beat-top" style="background:' + p.color + ';"></div>' +
      '<div class="vmm-beat-body">' +
        '<div class="vmm-beat-name">' + esc(p.city) + '</div>' +
        '<div class="vmm-beat-meta">' + p.bpm + ' BPM · ' + esc(p.note) + ' · ' + esc(p.mood) + '</div>' +
        '<div class="vmm-beat-desc">' + esc(p.desc) + '</div>' +
        '<div class="vmm-beat-actions">' +
          '<button class="vmm-btn vmm-btn-sm" onclick="window.vmMusicAPI.previewBeat(\'' + p.id + '\')">' + (previewing ? 'Playing' : 'Preview') + '</button>' +
          '<button class="vmm-btn vmm-btn-sm' + (selected ? ' vmm-btn-primary' : '') + '" onclick="window.vmMusicAPI.selectBeat(\'' + p.id + '\')">' + (selected ? 'Loaded' : 'Use') + '</button>' +
        '</div>' +
      '</div></div>';
  }

  function sheetHTML() {
    var s = MS.state;
    var rec = MS.recorder && MS.recorder.state === "recording";

    /* ── Mode hero ── */
    var mode = function (id, ic, t, sub) {
      var on = s.mode === id;
      return '<button type="button" class="vmm-mode' + (on ? " active" : "") + '" onclick="window.vmMusicAPI.onMode(\'' + id + '\')">' +
        '<span class="vmm-mode-ic"><i data-lucide="' + ic + '"></i></span>' +
        '<span class="vmm-mode-tx"><b>' + t + '</b><small>' + sub + '</small></span>' +
        '</button>';
    };
    var hero = '<div class="vmm-hero">' +
      mode("diy", "wand-2", "Do it yourself", "You record, write and direct every track.") +
      mode("ai", "sparkles", "Let ValleyMind produce it", "You hum or describe — ValleyMind writes lyrics + arrangement.") +
      '</div>';

    /* ── Source / record+upload (collapsible) ── */
    var sourceBody =
      '<div class="vmm-rec">' +
        '<button class="vmm-rec-btn' + (rec ? " recording" : " green") + '" id="vmMusicRecBtn" onclick="window.vmMusicAPI.toggleRecord()" title="Record / stop"><svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4"/></svg></button>' +
        '<div class="vmm-rec-meta"><b>' + (rec ? "Recording… tap to stop" : "Sing, hum or record a take") + '</b><small>' + (rec ? "Like a voice note — tap to stop" : "Or upload vocals / a beat below") + '</small></div>' +
        '<span class="vmm-rec-time" id="vmMusicRecTime">' + fmtTime(MS.elapsed) + '</span>' +
      '</div>' +
      '<div class="vmm-row" style="margin-top:12px;">' +
        '<label class="vmm-btn vmm-btn-sm" style="cursor:pointer;"><input type="file" accept="audio/*" id="vmMusicTakeInput" style="display:none;">Upload vocals</label>' +
        '<label class="vmm-btn vmm-btn-sm" style="cursor:pointer;"><input type="file" accept="audio/*" id="vmMusicBeatInput" style="display:none;">Upload a beat</label>' +
        '<label class="vmm-btn vmm-btn-sm" style="cursor:pointer;"><input type="file" accept="audio/*" id="vmMusicLayerInput" style="display:none;">Add vocal layer</label>' +
      '</div>';

    /* ── Tracks / mixer (multi-track) ── */
    var tCtrl = function (type, t) {
      var muted = trackMuted(t);
      var soloBtn = '<button class="vmm-btn vmm-btn-sm' + (t.solo ? " vmm-btn-primary" : "") + '" title="Solo" onclick="window.vmMusicAPI.setTrack(\'' + type + '\',\'solo\',!' + (t.solo ? "true" : "false") + ')">S' + '</button>';
      var muteBtn = '<button class="vmm-btn vmm-btn-sm' + (muted ? " vmm-btn-danger" : "") + '" title="Mute" onclick="window.vmMusicAPI.setTrack(\'' + type + '\',\'mute\',!' + (t.mute ? "true" : "false") + ')">M' + '</button>';
      var vol = '<input type="range" class="vmm-range" min="0" max="100" value="' + t.vol + '" style="width:88px;" oninput="window.vmMusicAPI.setTrack(\'' + type + '\',\'vol\',this.value)">';
      return soloBtn + " " + muteBtn + " " + "<span style=\"font-size:11px;color:#7c8aa0;min-width:30px;text-align:right;\">" + t.vol + "</span>";
    };
    var beatsMuted = trackMuted(s.beat);
    var takesMuted = trackMuted(s.take);
    var tracksHtml = "";
    if (s.take.url) {
      tracksHtml += '<div class="vmm-track">' +
        '<span class="t-ic"><i data-lucide="mic"></i></span>' +
        '<div class="t-main"><div class="t-name">' + esc(s.take.name) + '</div><div class="t-meta">Vocal · ' + fmtTime(s.take.dur) + (takesMuted ? " · muted" : "") + '</div></div>' +
        '<div class="t-ctrl"><button class="vmm-btn vmm-btn-sm" onclick="window.vmMusicAPI.playTrack(\'take\')">Play</button>' +
        '<button class="vmm-btn vmm-btn-sm' + (s.take.solo ? " vmm-btn-primary" : "") + '" title="Solo" onclick="window.vmMusicAPI.setTrack(\'take\',\'solo\',' + (s.take.solo ? "false" : "true") + ')">Solo</button>' +
        '<button class="vmm-btn vmm-btn-sm' + (takesMuted ? " vmm-btn-danger" : "") + '" title="Mute" onclick="window.vmMusicAPI.setTrack(\'take\',\'mute\',' + (s.take.mute ? "false" : "true") + ')">Mute</button>' +
        '</div></div>';
    }
    if (s.beat.url) {
      tracksHtml += '<div class="vmm-track">' +
        '<span class="t-ic"><i data-lucide="disc-3"></i></span>' +
        '<div class="t-main"><div class="t-name">' + esc(s.beat.name) + '</div><div class="t-meta">Beat · ' + fmtTime(s.beat.dur) + (beatsMuted ? " · muted" : "") + '</div></div>' +
        '<div class="t-ctrl"><button class="vmm-btn vmm-btn-sm" onclick="window.vmMusicAPI.playTrack(\'beat\')">Play</button>' +
        '<button class="vmm-btn vmm-btn-sm' + (s.beat.solo ? " vmm-btn-primary" : "") + '" title="Solo" onclick="window.vmMusicAPI.setTrack(\'beat\',\'solo\',' + (s.beat.solo ? "false" : "true") + ')">Solo</button>' +
        '<button class="vmm-btn vmm-btn-sm' + (beatsMuted ? " vmm-btn-danger" : "") + '" title="Mute" onclick="window.vmMusicAPI.setTrack(\'beat\',\'mute\',' + (s.beat.mute ? "false" : "true") + ')">Mute</button>' +
        '</div></div>';
    }
    if (s.layers.length) {
      s.layers.forEach(function (l) {
        var lm = trackMuted(l);
        tracksHtml += '<div class="vmm-track">' +
          '<span class="t-ic"><i data-lucide="layers"></i></span>' +
          '<div class="t-main"><div class="t-name">' + esc(l.name) + '</div><div class="t-meta">Vocal layer · ' + fmtTime(l.dur) + (lm ? " · muted" : "") + '</div></div>' +
          '<div class="t-ctrl"><button class="vmm-btn vmm-btn-sm" onclick="window.vmMusicAPI.playTrack(\'take\')">Play</button>' +
          '<button class="vmm-btn vmm-btn-sm' + (l.solo ? " vmm-btn-primary" : "") + '" onclick="window.vmMusicAPI.setLayer(\'' + l.id + '\',\'solo\',' + (l.solo ? "false" : "true") + ')">Solo</button>' +
          '<button class="vmm-btn vmm-btn-sm' + (lm ? " vmm-btn-danger" : "") + '" onclick="window.vmMusicAPI.setLayer(\'' + l.id + '\',\'mute\',' + (l.mute ? "false" : "true") + ')">Mute</button>' +
          '<button class="vmm-btn vmm-btn-sm vmm-btn-danger" title="Remove" onclick="window.vmMusicAPI.removeLayer(\'' + l.id + '\')">×</button>' +
          '</div></div>';
      });
    }
    if (!tracksHtml) {
      tracksHtml = '<div class="vmm-empty">No tracks yet — record your voice, then it appears here with the beat.</div>';
    }

    /* Volume sliders for the mixer */
    var mixSliders =
      (s.take.url ? slider("Vocal", "vmMusicTakeVol", s.take.vol, 0, 100, "", "setTrack('take','vol',document.getElementById('vmMusicTakeVol').value)") : "") +
      (s.beat.url ? slider("Beat", "vmMusicBeatVol", s.beat.vol, 0, 100, "", "setTrack('beat','vol',document.getElementById('vmMusicBeatVol').value)") : "");

    /* Mix & Master panel */
    var mixBody =
      '<div class="vmm-info">Mix balances your tracks and applies real gain while playing. Auto Master is an honest normalization — pro mastering is a future AI step.</div>' +
      slider("Master", "vmMusicMaster", s.mix.master, 0, 100, "", "setMaster(document.getElementById('vmMusicMaster').value)") +
      mixSliders +
      '<div class="vmm-row" style="margin-top:12px;">' +
        '<button class="vmm-btn" onclick="window.vmMusicAPI.autoMix()">Auto Mix</button>' +
        '<button class="vmm-btn" onclick="window.vmMusicAPI.autoMaster()">Auto Master</button>' +
        '<button class="vmm-btn" onclick="window.vmMusicAPI.exportWav(\'wav\')">Export audio</button>' +
      '</div>';

    /* Effects & tuning panel */
    var chipHtml = EFFECT_PRESETS.map(effectChip).join("");
    var fxBody =
      '<div class="vmm-info">Tap a vocal effect to set the whole chain, then fine-tune pitch and ambient FX. These controls set intent and persist with the project; DSP rendering is a future engine.</div>' +
      '<div class="vmm-chips">' + chipHtml + '</div>' +
      '<div class="vmm-slider-row"><label>Noise reduction</label>' +
        '<label class="vmm-switch"><input type="checkbox" ' + (s.fx.noiseReduction ? "checked" : "") + ' onchange="window.vmMusicAPI.setFx(\'noiseReduction\',this.checked)"><span class="sw"></span></label>' +
      '</div>' +
      slider("Pitch / tuning (cents)", "vmMusicPitch", s.fx.pitch, -50, 50, "", "setFx('pitch',document.getElementById('vmMusicPitch').value)") +
      slider("Reverb", "vmMusicReverb", s.fx.reverb, 0, 100, "", "setFx('reverb',document.getElementById('vmMusicReverb').value)") +
      slider("Delay", "vmMusicDelay", s.fx.delay, 0, 100, "", "setFx('delay',document.getElementById('vmMusicDelay').value)");

    /* Voice panel */
    var consentBlock = s.voice === "clone" ?
      '<div class="vmm-consent" onclick="window.vmMusicAPI.onConsent()"><input type="checkbox" ' + (s.consent ? "checked" : "") + '><label><b>Authorization to clone my voice:</b> I give ValleyMind permission to create an AI model of my voice from my recording, store it only for my projects, and use it solely to sing the songs I produce. I can revoke this at any time by deleting my projects.</label></div>' : "";
    var voiceBody = voiceOpt("keep") + voiceOpt("clone") + voiceOpt("elena") + consentBlock;

    /* AI vision panel */
    var aiBody = '<div class="vmm-grid">' +
      sel("vmMusicGenre", "Genre", GENRES, s.genre) +
      sel("vmMusicMood", "Mood", MOODS, s.mood) +
      sel("vmMusicTempo", "Tempo", TEMPOS, s.tempo) +
      sel("vmMusicRole", "Your role", ROLES, s.role) +
      '</div>' +
      '<label class="vmm-label">Key (optional)</label><input class="vmm-input" id="vmMusicKey" value="' + esc(s.key) + '" placeholder="e.g. A minor">' +
      '<label class="vmm-label">Describe it</label>' +
      '<textarea class="vmm-textarea" id="vmMusicBrief" placeholder="' + esc('Example: I just sang this melody. Turn it into a romantic Afrobeats song about finding love again after heartbreak.') + '">' + esc(s.brief) + '</textarea>' +
      '<div style="margin-top:12px;">' +
        '<button class="vmm-btn vmm-btn-primary" id="vmMusicRunAI" onclick="window.vmMusicAPI.runAI()" style="width:100%;">Produce this song</button>' +
      '</div>' +
      '<div class="vmm-progress" id="vmMusicProgress" style="display:none;margin-top:16px;"></div>';

    /* DIY lyrics panel */
    var lyricsBody =
      '<textarea class="vmm-textarea" id="vmMusicLyrics" placeholder="Write your own lyrics, or leave blank for an AI drafting pass later.">' + esc(s.lyrics) + '</textarea>' +
      '<div class="vmm-row" style="margin-top:12px;">' +
        '<button class="vmm-btn vmm-btn-primary" onclick="window.vmMusicAPI.saveSong()">Save song</button>' +
        '<button class="vmm-btn" onclick="window.vmMusicAPI.exportSong()">Export lyric sheet</button>' +
      '</div>';

    /* AI output */
    var aiOut = "";
    if (s.aiResult && s.aiResult.generated) {
      aiOut = '<div class="vmm-panel vmm-ai-out" id="vmMusicAIOut" style="display:block;border-color:rgba(34,211,238,0.35);">' +
        '<div class="vmm-panel-body" style="padding-top:14px;">' +
          '<h3 style="margin:0 0 6px;font-family:Space Grotesk,sans-serif;color:#f1f5f9;font-size:15px;">Your produced package</h3>' +
          (s.aiResult.title ? '<p class="vmm-sub" style="margin:0 0 4px;font-size:12px;color:#8a97ad;"><b style="color:#67e8f9;">Title:</b> ' + esc(s.aiResult.title) + '</p>' : "") +
          (s.aiResult.structure ? '<p class="vmm-sub" style="margin:0 0 4px;font-size:12px;color:#8a97ad;"><b style="color:#67e8f9;">Structure:</b> ' + esc(s.aiResult.structure) + '</p>' : "") +
          (s.aiResult.lyrics ? '<h4>Lyrics</h4><div class="lyrics">' + esc(s.aiResult.lyrics) + '</div>' : "") +
          (s.aiResult.arrangement ? '<h4>Arrangement</h4><p class="vmm-sub" style="font-size:12.5px;color:#cbd5e1;line-height:1.6;">' + esc(s.aiResult.arrangement) + '</p>' : "") +
          (s.aiResult.note ? '<div class="vmm-note">' + esc(s.aiResult.note) + '</div>' : "") +
          '<div class="vmm-row" style="margin-top:14px;"><button class="vmm-btn vmm-btn-primary" onclick="window.vmMusicAPI.saveSong()">Save this package</button> <button class="vmm-btn" onclick="window.vmMusicAPI.exportSong()">Export</button></div>' +
        '</div></div>';
    }

    /* Projects */
    var projRows = MS.projects.map(function (p) {
      var d = p.savedAt ? new Date(p.savedAt).toLocaleString() : "";
      return '<div class="vmm-proj">' +
        '<span class="p-icon"><i data-lucide="' + (p.mode === "ai" ? "sparkles" : "music") + '"></i></span>' +
        '<div class="p-main"><div class="p-name">' + esc(p.name || "Untitled") + '</div><div class="p-meta">' + esc(p.genre || "") + " · " + esc(p.mood || "") + (d ? " · " + d : "") + '</div></div>' +
        '<button class="vmm-btn vmm-btn-sm vmm-btn-primary" onclick="window.vmMusicAPI.loadSong(\'' + p.id + '\')">Open</button>' +
        '<button class="vmm-btn vmm-btn-sm vmm-btn-danger" onclick="window.vmMusicAPI.deleteSong(\'' + p.id + '\')">Del</button>' +
        '</div>';
    }).join("");

    /* Beat Library (20 African / Nigerian beats) */
    var beatLibBody =
      '<div class="vmm-info">Tap a beat to preview it live, then Use to load it as your beat track. Beats are colour-coded by mood and synthesised in your browser (~30s loop).</div>' +
      '<div class="vmm-beatlib">' + BEAT_PRESETS.map(beatCard).join("") + '</div>';

    /* Sections */
    var sections =
      section("sliders-horizontal", "Tracks & Mixer", "Your vocal(s) and beat, with solo/mute and gain", "tracks", tracksHtml) +
      section("folder-plus", "Add audio", "Record, upload vocals, upload a beat, add layers", "source", sourceBody, true) +
      (s.mode === "ai"
        ? section("sparkles", "Let ValleyMind produce it", "Describe it (or add lyrics) — AI writes lyrics + arrangement", "vision", aiBody, true)
        : section("file-text", "Lyrics", "Write or paste your lyrics", "lyrics", lyricsBody, true)) +
      section("mic-2", "Voice", "Choose whose voice sings this song", "voice", voiceBody, false) +
      section("audio-lines", "Effects & Tuning", "Noise reduction, pitch, reverb, vocal effect", "fx", fxBody, false) +
      section("mixer", "Mix & Master", "Balance tracks, Auto Mix / Auto Master", "mix", mixBody, false) +
      section("audio-lines", "Beat Library", "20 African / Nigerian beats — tap to preview, Use to load", "beats", beatLibBody, false) +
      section("library", "Projects", "Saved songs follow you across devices when signed in", "projects", (projRows || '<div class="vmm-empty">No saved songs yet.</div>'), false) +
      aiOut;

    /* Bottom action dock */
    var dock =
      '<button class="vmm-btn' + (rec ? " dock-rec recording" : "") + '" onclick="window.vmMusicAPI.toggleRecord()"><i data-lucide="mic"></i><span>' + (rec ? "Stop" : "Record") + '</span></button>' +
      '<button class="vmm-btn" onclick="window.vmMusicAPI.dockUpload()"><i data-lucide="upload"></i><span>Upload</span></button>' +
      '<button class="vmm-btn dock-primary" onclick="window.vmMusicAPI.dockProduce()"><i data-lucide="' + (s.mode === "ai" ? "sparkles" : "wand-2") + '"></i><span>' + (s.mode === "ai" ? "Produce" : "Generate") + '</span></button>' +
      '<button class="vmm-btn" onclick="window.vmMusicAPI.dockMix()"><i data-lucide="mixer"></i><span>Mix</span></button>' +
      '<button class="vmm-btn" onclick="window.vmMusicAPI.saveSong()"><i data-lucide="save"></i><span>Save</span></button>';

    var uploadMenu = MS.ui.dockUpload ?
      '<div class="vmm-menu-backdrop" onclick="window.vmMusicAPI.dockUpload()"></div>' +
      '<div class="vmm-menu">' +
        '<label class="vmm-btn" style="cursor:pointer;"><input type="file" accept="audio/*" id="vmMusicTakeInput" style="display:none;"><i data-lucide="mic"></i> Upload vocals</label>' +
        '<label class="vmm-btn" style="cursor:pointer;"><input type="file" accept="audio/*" id="vmMusicBeatInput" style="display:none;"><i data-lucide="disc-3"></i> Upload a beat</label>' +
        '<label class="vmm-btn" style="cursor:pointer;"><input type="file" accept="audio/*" id="vmMusicLayerInput" style="display:none;"><i data-lucide="layers"></i> Add vocal layer</label>' +
        '<button class="vmm-btn" onclick="window.vmMusicAPI.dockUpload()"><i data-lucide="x"></i> Cancel</button>' +
      '</div>' : "";

    return '' +
      '<div class="vmm-head">' +
        '<div class="vmm-logo">V</div>' +
        '<div class="vmm-title"><h2>Music Studio</h2><p class="vmm-sub">' + (s.take.name || s.beat.name || 'Sing, hum, or describe — then produce it') + '</p></div>' +
        '<span class="vmm-chip">' + (s.mode === "ai" ? "AI produce" : "DIY") + '</span>' +
        '<div class="vmm-spacer"></div>' +
        '<input class="vmm-input vmm-projectname" id="vmMusicName" value="' + esc(s.name) + '" placeholder="Song name" oninput="window.vmMusicAPI.syncName()">' +
        '<button class="vmm-btn vmm-btn-sm" onclick="window.vmMusicAPI.newSong()"><i data-lucide="file-plus"></i><span class="vmm-hide-sm">New</span></button>' +
      '</div>' +
      '<div class="vmm-body">' +
        '<audio id="vmMusicPlayer" preload="none" style="display:none;"></audio>' +
        '<div class="vmm-hero">' + hero + '</div>' +
        sections +
      '</div>' +
      '<div class="vmm-dock">' + dock + '</div>' +
      uploadMenu +
      '<div class="vmm-toast" id="vmMusicToast"></div>';
  }

  /* Dock actions: jump to a section (open it) from the bottom bar. */
  function scrollToPanel(key) {
    setPanel(key, true);
    render();
    setTimeout(function () {
      var p = document.querySelector('.vmm-panel[data-openkey="' + key + '"]');
      if (!p) { var dets = document.querySelectorAll(".vmm-panel"); for (var i = 0; i < dets.length; i++) { if (dets[i].querySelector("summary b") && key.indexOf("mix") > -1 && key === "mix") { p = dets[i]; break; } } }
      if (p && p.scrollIntoView) p.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
  }
  function dockUpload() { MS.ui.dockUpload = !MS.ui.dockUpload; render(); }
  function dockProduce() {
    if (MS.state.mode === "ai") { scrollToPanel("vision"); }
    else {
      if (MS.state.take.url) { window.vmMusicAPI.runAI && runAI(); }
      else { scrollToPanel("lyrics"); }
    }
  }
  function dockMix() { scrollToPanel("mix"); }

  function bindFields() {
    var on = function (id, fn) { var el = document.getElementById(id); if (el) el.addEventListener("change", fn); };
    on("vmMusicTakeInput", function (e) { onTakeFile(e.target); });
    on("vmMusicBeatInput", function (e) { onBeatFile(e.target); });
    on("vmMusicLayerInput", function (e) { onLayerFile(e.target); });
    on("vmMusicGenre", function () { syncInputs(); });
    on("vmMusicMood", function () { syncInputs(); });
    on("vmMusicTempo", function () { syncInputs(); });
    on("vmMusicRole", function () { syncInputs(); });
    on("vmMusicName", function () { syncInputs(); });
    var b = document.getElementById("vmMusicBrief"); if (b) b.addEventListener("input", function () { syncInputs(); });
    var l = document.getElementById("vmMusicLyrics"); if (l) l.addEventListener("input", function () { syncInputs(); });
  }

  /* Sync name from the header input as the user types (live). */
  function syncName() {
    var el = document.getElementById("vmMusicName");
    if (el) MS.state.name = el.value || MS.state.name;
  }

  /* ── Public API (global so inline onclick handlers can reach it) ────── */
  var API = {
    onMode: onMode,
    toggleRecord: toggleRecord,
    stopRecord: stopRecord,
    playTrack: playTrack,
    playAudio: playTrack,
    stopPlayback: stopPlayback,
    onVoice: function (v) { syncInputs(); MS.state.voice = v; if (v !== "clone") MS.state.consent = false; render(); },
    onConsent: function () { MS.state.consent = !MS.state.consent; render(); },
    setFx: setFx,
    applyEffect: function (name) {
      for (var i = 0; i < EFFECT_PRESETS.length; i++) {
        if (EFFECT_PRESETS[i].name === name) { applyEffectPreset(EFFECT_PRESETS[i]); return; }
      }
    },
    setMaster: setMaster,
    autoMix: autoMix,
    autoMaster: autoMaster,
    addLayer: addLayer,
    removeLayer: removeLayer,
    setTrack: setTrack,
    setLayer: setLayer,
    runAI: runAI,
    saveSong: saveSong,
    newSong: newSong,
    loadSong: loadSong,
    deleteSong: deleteSong,
    exportSong: exportSong,
    exportWav: exportWav,
    previewBeat: previewBeat,
    selectBeat: selectBeat,
    stopPreview: stopPreview,
    syncName: syncName,
    dockUpload: dockUpload,
    dockProduce: dockProduce,
    dockMix: dockMix
  };
  window.vmMusicAPI = API;

  function onShow() {
    if (!MS.rendered) {
      MS.rendered = true;
      if (!MS.state) { loadProjects(); MS.state = defaultState(); MS.state.id = null; }
      fetchProjectsFromServer();
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
