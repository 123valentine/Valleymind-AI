/* ValleyMind Music Studio — Professional Workspace-First Redesign
   ─────────────────────────────────────────────────────────────
   Architecture: BandLab-inspired. Workspace ALWAYS takes priority.
   Tools open as panels/drawers — they never replace the workspace.

   Structure:
     Header (compact) → Workspace (waveform/tracks) → Transport → Bottom Nav
     Tapping a bottom nav item opens a tool panel overlay.
     Mobile: panels slide up from bottom.
     Desktop: panels appear as right-side inspector.

   All existing functionality preserved: recording, upload, beats,
   effects, mix, projects, AI generation, AI edit, cloud sync.
*/
(function () {
  "use strict";

  var STORE_KEY = "vmMusicProjects";

  /* ── Bottom nav items (the primary navigation) ────────────────────── */
  var NAV = [
    { id: "record",  icon: "mic",        label: "Record" },
    { id: "tracks",  icon: "layers",      label: "Tracks" },
    { id: "generate",icon: "sparkles",    label: "Create" },
    { id: "tools",   icon: "sliders-horizontal", label: "Tools" },
    { id: "projects",icon: "folder",      label: "Projects" }
  ];

  /* ── Tool sub-panels (opened inside the Tools overlay) ────────────── */
  var TOOLS = [
    { id: "voice",    icon: "mic",         label: "Voice",       sub: "Record & choose voice" },
    { id: "music",    icon: "music",       label: "Beats",       sub: "Beat library & upload" },
    { id: "instruments",icon:"piano",      label: "Instruments", sub: "AI instrumentation" },
    { id: "lyrics",   icon: "file-text",   label: "Lyrics",      sub: "Write & refine" },
    { id: "effects",  icon: "audio-lines", label: "Effects",     sub: "Vocal presets" },
    { id: "mix",      icon: "mixer",       label: "Mix",         sub: "Balance & master" },
    { id: "ai-edit",  icon: "brain",       label: "AI Edit",     sub: "Refine with AI" },
    { id: "memory",   icon: "brain-circuit",label: "Memory",     sub: "Preferences" },
    { id: "assets",   icon: "library",     label: "Assets",      sub: "Audio files" },
    { id: "ai-tools", icon: "bot",         label: "AI Tools",    sub: "Utilities" }
  ];

  /* ── Constants (preserved from original) ──────────────────────────── */
  var VOICE_LABELS = { keep:"Keep & enhance my own voice", clone:"AI-clone of my voice (authorized)", elena:"ValleyMind's AI singing voice (Elena)" };
  var VOICE_SUBS = { keep:"Cleans, tunes and enhances your recording.", clone:"An AI model of your voice — requires authorization.", elena:"ValleyMind's approved AI singing voice." };
  var GENRES = ["Afrobeats","Amapiano","R&B","Hip-Hop","Pop","Soul","Gospel","Highlife","Dancehall","Reggae","Folk","Jazz","Electronic"];
  var MOODS = ["Romantic","Upbeat","Melancholic","Hopeful","Energetic","Chill","Bittersweet","Empowering","Nostalgic"];
  var TEMPOS = ["Slow","Medium","Fast","Very fast"];
  var ROLES = ["Singer","Rapper","Singer-songwriter","Producer","Both singing & producing"];

  var EFFECT_PRESETS = [
    {name:"Clean",color:"#22d3ee",fx:{noiseReduction:true,pitch:0,effect:"None",reverb:10,delay:0}},
    {name:"Intune",color:"#34d399",fx:{noiseReduction:true,pitch:0,effect:"Intune",reverb:20,delay:0}},
    {name:"Megaphone",color:"#fbbf24",fx:{noiseReduction:false,pitch:0,effect:"Megaphone",reverb:5,delay:0}},
    {name:"Warm",color:"#fb923c",fx:{noiseReduction:false,pitch:0,effect:"Warm",reverb:35,delay:0}},
    {name:"Bright",color:"#a3e635",fx:{noiseReduction:true,pitch:8,effect:"Bright",reverb:15,delay:0}},
    {name:"Hall",color:"#60a5fa",fx:{noiseReduction:false,pitch:0,effect:"Hall reverb",reverb:70,delay:10}},
    {name:"Delay",color:"#c084fc",fx:{noiseReduction:false,pitch:0,effect:"Delay",reverb:30,delay:55}},
    {name:"Tape",color:"#f472b6",fx:{noiseReduction:false,pitch:-12,effect:"Tape",reverb:25,delay:0}},
    {name:"Robot",color:"#94a3b8",fx:{noiseReduction:true,pitch:0,effect:"Robotic",reverb:5,delay:0}},
    {name:"Choir",color:"#818cf8",fx:{noiseReduction:false,pitch:4,effect:"Choir-ish",reverb:65,delay:15}},
    {name:"Lo-Fi",color:"#a16207",fx:{noiseReduction:false,pitch:-6,effect:"Lo-Fi",reverb:40,delay:0}},
    {name:"Phone",color:"#64748b",fx:{noiseReduction:false,pitch:0,effect:"Telephone",reverb:5,delay:0}}
  ];

  var BEAT_PRESETS = [
    {id:"bl-midnight",city:"Lagos Midnight",bpm:95,note:"C4",mood:"Romantic",color:"#e74c3c",desc:"Slow candle-lit groove.",pattern:"T00L00K0T00K0K0"},
    {id:"bl-accra",city:"Accra Breeze",bpm:100,note:"G4",mood:"Chill",color:"#1abc9c",desc:"Earthy highlife bounce.",pattern:"T0K0T0K0T0K0T0K0K0"},
    {id:"bl-abuja",city:"Abuja Sunrise",bpm:110,note:"D4",mood:"Hopeful",color:"#f39c12",desc:"Bright, uplifting groove.",pattern:"K0T0K0T0K0T0K0T0K0"},
    {id:"bl-ph",city:"Port Harcourt Groove",bpm:120,note:"A4",mood:"Upbeat",color:"#9b59b6",desc:"Party-ready log drum.",pattern:"K00T0K0KT0K0K0T0"},
    {id:"bl-enugu",city:"Enugu Nights",bpm:85,note:"E4",mood:"Melancholic",color:"#34495e",desc:"Deep, moody R&B.",pattern:"K00L00K0T00L00K0"},
    {id:"bl-ibadan",city:"Ibadan Vibes",bpm:130,note:"F4",mood:"Energetic",color:"#27ae60",desc:"Fast street vibration.",pattern:"K0K0K0T0K0K0K0T0K0"},
    {id:"bl-kano",city:"Kano Dust",bpm:90,note:"Bb3",mood:"Nostalgic",color:"#8B4513",desc:"Old-school desert soul.",pattern:"K0T00K0T00K0T00"},
    {id:"bl-warri",city:"Warri Energy",bpm:125,note:"Eb4",mood:"Upbeat",color:"#dc143c",desc:"High-energy bounce.",pattern:"KK0T0KK0T0KK0T0K0"},
    {id:"bl-benin",city:"Benin City Soul",bpm:95,note:"C4",mood:"Bittersweet",color:"#708090",desc:"Soulful and reflective.",pattern:"T0K00L00K0T0L0K0"},
    {id:"bl-calabar",city:"Calabar Flow",bpm:105,note:"G4",mood:"Chill",color:"#00ced1",desc:"Smooth coastline travel.",pattern:"K0T00T0K0T00T0K0"},
    {id:"bl-jos",city:"Jos Plateau",bpm:100,note:"A4",mood:"Hopeful",color:"#DAA520",desc:"Cool highland hope.",pattern:"K00K0T0K00K0T0"},
    {id:"bl-owerri",city:"Owerri Heat",bpm:135,note:"D4",mood:"Energetic",color:"#FF7F50",desc:"Scorching fast beat.",pattern:"KK0K0T0KK0K0T0K0"},
    {id:"bl-kaduna",city:"Kaduna Dawn",bpm:88,note:"F4",mood:"Romantic",color:"#C08080",desc:"Tender dawn serenade.",pattern:"K0L00K0T0L00K0"},
    {id:"bl-aba",city:"Aba Market",bpm:118,note:"Bb3",mood:"Upbeat",color:"#32CD32",desc:"Busy market bounce.",pattern:"K0T0K0T0KT0K0T0K0"},
    {id:"bl-ilorin",city:"Ilorin Breeze",bpm:98,note:"Eb4",mood:"Chill",color:"#87CEEB",desc:"Light evening air.",pattern:"T0K0T0T0K0T0K0T0"},
    {id:"bl-maiduguri",city:"Maiduguri Sun",bpm:112,note:"C4",mood:"Empowering",color:"#FFBF00",desc:"Bold and resolute.",pattern:"K0T0K0TK0T0K0T0"},
    {id:"bl-akwa",city:"Akwa Ibom Tide",bpm:92,note:"G4",mood:"Melancholic",color:"#4B0082",desc:"Watery, introspective.",pattern:"K00T00K0T0K0T00"},
    {id:"bl-osogbo",city:"Osogbo Rain",bpm:86,note:"Ab3",mood:"Bittersweet",color:"#A9A9A9",desc:"Rain on the roof.",pattern:"K0L0K0T0K0L0T0K0"},
    {id:"bl-sokoto",city:"Sokoto Stars",bpm:94,note:"B4",mood:"Nostalgic",color:"#B87333",desc:"Night sky memory.",pattern:"K0T0K00T0K0T0K0"},
    {id:"bl-bayelsa",city:"Bayelsa River",bpm:102,note:"Db4",mood:"Romantic",color:"#00A86B",desc:"Slow river romance.",pattern:"K000T0K0T0K0K0"}
  ];

  var NOTE_FREQ=(function(){var map={};var names=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];var flatMap={"Db":"C#","Eb":"D#","Gb":"F#","Ab":"G#","Bb":"A#"};var A4=440,A4midi=69;for(var midi=0;midi<128;midi++){var oct=Math.floor(midi/12)-1;var nc=names[midi%12];map[nc+oct]=A4*Math.pow(2,(midi-A4midi)/12);}function noteFreq(n){if(map[n])return map[n];if(flatMap[n])return map[flatMap[n]];return 261.63;}return noteFreq;})();
  function bassFreq(note){return NOTE_FREQ(note)/2;}

  /* ── State ────────────────────────────────────────────────────────── */
  var MS = {
    state: null,
    recorder: null, recStream: null, chunks: [], timer: null, elapsed: 0,
    projects: [], rendered: false, previewPreset: null,
    ui: { activeNav: "", openTool: "", recording: false, saveState: "" },
    memory: []
  };

  function defaultState() {
    return {
      name:"Untitled song", mode:"diy", role:"Singer", genre:"Afrobeats",
      mood:"Romantic", tempo:"Medium", key:"", language:"English",
      brief:"", lyrics:"", voice:"keep", consent:false,
      take:{name:"",url:"",dur:0,vol:100,mute:false,solo:false},
      beat:{name:"",url:"",dur:0,vol:100,mute:false,solo:false},
      layers:[],
      fx:{noiseReduction:false,pitch:0,effect:"None",reverb:30,delay:0},
      mix:{master:80},
      autoMix:false, autoMaster:false, aiResult:null,
      savedAt:0, beatPreset:null, lastAiEdit:null, tags:[]
    };
  }

  function normalizeProject(p) {
    var d=defaultState();for(var k in d){if(typeof p[k]==="undefined")p[k]=clone(d[k]);}
    if(!p.take||typeof p.take!=="object")p.take=d.take;
    if(!p.beat||typeof p.beat!=="object")p.beat=d.beat;
    if(!p.fx)p.fx=d.fx;if(!p.mix)p.mix=d.mix;
    p.take.vol=(typeof p.take.vol==="number")?p.take.vol:100;p.take.mute=!!p.take.mute;p.take.solo=!!p.take.solo;
    p.beat.vol=(typeof p.beat.vol==="number")?p.beat.vol:100;p.beat.mute=!!p.beat.mute;p.beat.solo=!!p.beat.solo;
    p.layers=Array.isArray(p.layers)?p.layers:[];return p;
  }
  function clone(o){return JSON.parse(JSON.stringify(o));}
  function fmtTime(s){if(!s&&s!==0)return"00:00";s=Math.round(s||0);var m=Math.floor(s/60);var ss=s%60;return(m<10?"0"+m:m)+":"+(ss<10?"0"+ss:ss);}
  function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
  function loadProjects(){try{var p=JSON.parse(localStorage.getItem(STORE_KEY)||"[]");MS.projects=(Array.isArray(p)?p:[]).map(normalizeProject);}catch(e){MS.projects=[];}}
  function saveProjects(){try{localStorage.setItem(STORE_KEY,JSON.stringify(MS.projects));}catch(e){}}
  function pushProjectsToServer(){if(typeof apiFetch!=="function")return;apiFetch("/api/music/projects",{method:"POST",credentials:"include",headers:authHeaders({"Content-Type":"application/json"}),body:JSON.stringify({projects:MS.projects})}).catch(function(){});}
  function deleteProjectOnServer(id){if(typeof apiFetch!=="function")return;apiFetch("/api/music/projects/"+encodeURIComponent(id),{method:"DELETE",credentials:"include",headers:authHeaders({})}).catch(function(){});}
  function fetchProjectsFromServer(){if(typeof apiFetch!=="function")return Promise.resolve();return apiFetch("/api/music/projects",{method:"GET",credentials:"include",headers:authHeaders({})}).then(function(r){return r.json();}).then(function(d){if(!d||!Array.isArray(d.projects))return;var server=d.projects.map(normalizeProject);var map={};MS.projects.forEach(function(p){map[p.id]=p;});server.forEach(function(p){var mine=map[p.id];if(!mine||(p.savedAt||0)>(mine.savedAt||0))map[p.id]=p;});MS.projects=Object.keys(map).map(function(k){return map[k];});saveProjects();render();}).catch(function(){});}
  function loadMemory(){try{MS.memory=JSON.parse(localStorage.getItem(STORE_KEY+"_mem")||"[]");}catch(e){MS.memory=[];}}
  function saveMemory(){try{localStorage.setItem(STORE_KEY+"_mem",JSON.stringify(MS.memory));}catch(e){}}
  function addMemory(entry){MS.memory.unshift(entry);if(MS.memory.length>50)MS.memory.pop();saveMemory();}
  function toast(msg){var el=document.getElementById("vmMusicToast");if(!el)return;el.textContent=msg;el.classList.add("show");clearTimeout(el._t);el._t=setTimeout(function(){el.classList.remove("show");},2600);}
  function refreshLucide(){if(window.lucide&&typeof window.lucide.createIcons==="function"){try{window.lucide.createIcons();}catch(e){}}}
  function trackMuted(t){var soloed=(MS.state.take.solo||MS.state.beat.solo)||MS.state.layers.some(function(l){return l.solo;});if(soloed)return!t.solo;return t.mute;}

  function showSaveState(state) {
    MS.ui.saveState = state;
    var el = document.getElementById("msSaveStatus");
    if (el) el.textContent = state;
  }
  function autoSaveDebounced() {
    if (MS.ui._saveTimer) clearTimeout(MS.ui._saveTimer);
    showSaveState("Saving...");
    MS.ui._saveTimer = setTimeout(function() {
      syncInputs();
      MS.state.savedAt = Date.now();
      if (!MS.state.id) MS.state.id = "ms" + Date.now();
      var found = false;
      for (var i = 0; i < MS.projects.length; i++) {
        if (MS.projects[i].id === MS.state.id) { MS.projects[i] = normalizeProject(clone(MS.state)); found = true; break; }
      }
      if (!found) MS.projects.unshift(normalizeProject(clone(MS.state)));
      saveProjects(); pushProjectsToServer();
      showSaveState("Saved");
      setTimeout(function(){ if(MS.ui.saveState==="Saved") showSaveState(""); }, 2000);
    }, 1500);
  }

  /* ── Recording ────────────────────────────────────────────────────── */
  function toggleRecord() {
    if (MS.recorder && MS.recorder.state === "recording") { stopRecord(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { toast("Recording not supported."); return; }
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream) {
      MS.recStream = stream;
      var mime = (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported) ? (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "") : "";
      MS.recorder = new MediaRecorder(stream, mime ? {mimeType:mime} : undefined);
      MS.chunks = [];
      MS.recorder.ondataavailable = function(ev) { if (ev.data && ev.data.size) MS.chunks.push(ev.data); };
      MS.recorder.onstop = function() {
        var blob = new Blob(MS.chunks, {type: MS.recorder.mimeType || "audio/webm"});
        var take = MS.state.take;
        if (take.url) try { URL.revokeObjectURL(take.url); } catch(e) {}
        take.url = URL.createObjectURL(blob);
        take.name = "Take " + fmtTime(Date.now()/1000).replace(":","");
        take.dur = MS.elapsed;
        stopRecTracks(); MS.ui.recording = false; render();
        autoSaveDebounced();
        toast("Take recorded.");
      };
      MS.recorder.start(); MS.elapsed = 0; MS.ui.recording = true;
      clearInterval(MS.timer);
      MS.timer = setInterval(function() { MS.elapsed++; renderRecTime(); }, 1000);
      render();
    }).catch(function() { toast("Microphone blocked. Allow mic access."); });
  }
  function stopRecTracks() { try { if (MS.recStream) MS.recStream.getTracks().forEach(function(t) { t.stop(); }); } catch(e) {} MS.recStream = null; }
  function stopRecord() { if (MS.recorder && MS.recorder.state === "recording") { try { MS.recorder.stop(); } catch(e) {} } clearInterval(MS.timer); MS.timer = null; MS.recorder = null; MS.ui.recording = false; render(); }
  function renderRecTime() { var n = document.getElementById("msRecTime"); if (n) n.textContent = fmtTime(MS.elapsed); }

  /* ── Audio upload ─────────────────────────────────────────────────── */
  function loadAudioFields(track, f) { track.name = f.name; track.url = URL.createObjectURL(f); track.dur = 0; var a = new Audio(); a.preload = "metadata"; a.src = track.url; a.onloadedmetadata = function() { track.dur = a.duration || 0; render(); }; }
  function onTakeFile(input) { var f = input && input.files && input.files[0]; if (!f) return; var t = MS.state.take; if (t.url) try { URL.revokeObjectURL(t.url); } catch(e) {} loadAudioFields(t, f); render(); autoSaveDebounced(); toast("Vocal added."); input.value = ""; }
  function onBeatFile(input) { var f = input && input.files && input.files[0]; if (!f) return; var t = MS.state.beat; if (t.url) try { URL.revokeObjectURL(t.url); } catch(e) {} loadAudioFields(t, f); render(); autoSaveDebounced(); toast("Beat added."); input.value = ""; }
  function onLayerFile(input) { var f = input && input.files && input.files[0]; if (!f) return; addLayer().then(function() { var l = MS.state.layers[MS.state.layers.length - 1]; if (l && l.url) try { URL.revokeObjectURL(l.url); } catch(e) {} loadAudioFields(l, f); render(); autoSaveDebounced(); toast("Layer added."); }); if (input) input.value = ""; }
  function playTrack(kind) {
    var a = document.getElementById("vmMusicPlayer"); if (!a) return;
    var url = "", vol = 0;
    if (kind === "beat") { var bt = MS.state.beat; if (trackMuted(bt)) { toast("Beat is muted."); return; } url = bt.url; vol = (bt.vol / 100) * (MS.state.mix.master / 100); }
    else { var tk = MS.state.take; if (trackMuted(tk)) { toast("Vocal is muted."); return; } url = tk.url; vol = (tk.vol / 100) * (MS.state.mix.master / 100); }
    if (!url) { toast(kind === "beat" ? "Add a beat first." : "Record or add vocals first."); return; }
    a.volume = Math.min(1, Math.max(0, vol || 0)); a.src = url; if (a.play) a.play();
  }
  function stopPlayback() { var a = document.getElementById("vmMusicPlayer"); if (a) { try { a.pause(); } catch(e) {} } }
  function addLayer() { MS.state.layers.push({id:"ly"+Date.now(),name:"Layer "+(MS.state.layers.length+1),url:"",dur:0,vol:100,mute:false,solo:false}); render(); return Promise.resolve(); }
  function removeLayer(id) { MS.state.layers = MS.state.layers.filter(function(l) { return l.id !== id; }); render(); autoSaveDebounced(); }
  function setTrack(kind, field, val) { var t = kind === "beat" ? MS.state.beat : MS.state.take; t[field] = (field === "vol") ? Number(val) : !!val; render(); autoSaveDebounced(); }
  function setLayer(id, field, val) { var l = MS.state.layers.filter(function(x) { return x.id === id; })[0]; if (!l) return; l[field] = (field === "vol") ? Number(val) : !!val; render(); autoSaveDebounced(); }
  function syncInputs() { var get = function(id) { var el = document.getElementById(id); return el ? el.value : ""; }; MS.state.name = get("vmMusicName") || MS.state.name; MS.state.role = get("vmMusicRole") || "Singer"; MS.state.genre = get("vmMusicGenre") || "Afrobeats"; MS.state.mood = get("vmMusicMood") || "Romantic"; MS.state.tempo = get("vmMusicTempo") || "Medium"; MS.state.key = get("vmMusicKey") || ""; MS.state.language = get("vmMusicLanguage") || "English"; MS.state.brief = get("vmMusicBrief") || ""; MS.state.lyrics = get("vmMusicLyrics") || ""; }
  function syncName() { var el = document.getElementById("vmMusicName"); if (el) MS.state.name = el.value || MS.state.name; }

  /* ── Effects / Mix ────────────────────────────────────────────────── */
  function setFx(field, val) { if (field === "noiseReduction") MS.state.fx.noiseReduction = !!val; else if (field === "effect") MS.state.fx.effect = val; else MS.state.fx[field] = Number(val); render(); autoSaveDebounced(); }
  function applyEffectPreset(preset) { var fx = MS.state.fx; fx.noiseReduction = !!preset.fx.noiseReduction; fx.pitch = Number(preset.fx.pitch); fx.effect = preset.fx.effect; fx.reverb = Number(preset.fx.reverb); fx.delay = Number(preset.fx.delay); render(); autoSaveDebounced(); toast("Effect: " + preset.name); }
  function setMaster(val) { MS.state.mix.master = Number(val); render(); }
  function autoMix() { var b = MS.state.beat.vol; var v = MS.state.take.vol; if (b && v) { MS.state.beat.vol = Math.round(Math.min(90, Math.max(35, b * 0.72))); MS.state.take.vol = 100; } MS.state.autoMix = true; toast("Auto Mix applied."); render(); autoSaveDebounced(); }
  function autoMaster() { MS.state.mix.master = 100; MS.state.autoMaster = true; toast("Master levelled."); render(); autoSaveDebounced(); }

  /* ── AI generate + edit ───────────────────────────────────────────── */
  function runAI() {
    syncInputs(); if (!MS.state.consent) { toast("Authorize the voice choice first."); return; }
    if (!MS.state.brief.trim() && !MS.state.lyrics.trim()) { toast("Describe your song or add lyrics first."); return; }
    toast("Generating...");
    apiFetch("/api/music", {method:"POST",credentials:"include",headers:authHeaders({"Content-Type":"application/json"}),
      body:JSON.stringify({brief:MS.state.brief,role:MS.state.role,genre:MS.state.genre,mood:MS.state.mood,tempo:MS.state.tempo,key:MS.state.key,language:MS.state.language,voice:MS.state.voice,lyrics:MS.state.lyrics}),
      timeoutMs:60000
    }).then(function(r){return r.json();}).then(function(d){
      MS.state.aiResult = d || null;
      if (d && d.generated) addMemory({type:"generated",name:MS.state.name,genre:MS.state.genre,mood:MS.state.mood,date:Date.now()});
      render(); toast("Music package generated!"); autoSaveDebounced();
    }).catch(function() { toast("Couldn't reach the producer."); });
  }
  function runAiEdit() {
    syncInputs(); var input = document.getElementById("msAiEditInput"); var instruction = (input ? input.value : "").trim();
    if (!instruction) { toast("Type an instruction first."); return; }
    toast("Applying changes...");
    apiFetch("/api/music/ai-edit", {method:"POST",credentials:"include",headers:authHeaders({"Content-Type":"application/json"}),
      body:JSON.stringify({instruction:instruction,lyrics:MS.state.lyrics||"",arrangement:(MS.state.aiResult&&MS.state.aiResult.arrangement)||"",genre:MS.state.genre,mood:MS.state.mood,tempo:MS.state.tempo,key:MS.state.key,name:MS.state.name}),
      timeoutMs:45000
    }).then(function(r){return r.json();}).then(function(d){
      if (d && d.status === "success" && d.changes) {
        var ch = d.changes;
        if (ch.lyrics) MS.state.lyrics = ch.lyrics;
        if (ch.title) MS.state.name = ch.title;
        if (ch.arrangement && MS.state.aiResult) MS.state.aiResult.arrangement = ch.arrangement;
        if (ch.genre) MS.state.genre = ch.genre; if (ch.mood) MS.state.mood = ch.mood;
        if (ch.tempo) MS.state.tempo = ch.tempo; if (ch.key) MS.state.key = ch.key;
        MS.state.lastAiEdit = {instruction:instruction,summary:d.summary||"",date:Date.now()};
        addMemory({type:"ai-edit",instruction:instruction,summary:d.summary||"",date:Date.now()});
        render(); toast(d.summary || "Changes applied!"); autoSaveDebounced();
      } else { toast((d && d.message) || "AI edit couldn't process that."); }
    }).catch(function() { toast("Couldn't reach the AI editor."); });
  }

  /* ── Beat synthesis (Web Audio — preserved) ───────────────────────── */
  function beatPresetById(id){for(var i=0;i<BEAT_PRESETS.length;i++)if(BEAT_PRESETS[i].id===id)return BEAT_PRESETS[i];return null;}
  function renderBeatLoop(preset){var Offline=window.OfflineAudioContext||window.webkitOfflineAudioContext;if(!Offline)return Promise.reject(new Error("no offline ctx"));var rate=44100,spb=60/preset.bpm,beats=12,duration=spb*beats;var ctx=new Offline(2,Math.ceil(rate*duration),rate);var root=bassFreq(preset.note);var steps=preset.pattern;for(var i=0;i<steps.length;i++){var ch=steps.charAt(i);var when=i*0.75*spb;for(var bar=0;bar<3;bar++){var w=when+bar*4*spb;if(ch==="K"){kick(ctx,w,0.95);bassHit(ctx,root,w,spb*0.8);}else if(ch==="L"){kick(ctx,w,0.6);}else if(ch==="T"){snare(ctx,w,0.75);}else if(ch==="0"){hat(ctx,w,0.12);}}}var totalSpb=ctx.duration/spb;for(var h=0;h<totalSpb;h++){if(h%2===1)hat(ctx,h*spb,0.06);}return ctx.startRendering().then(function(buffer){return encodeWav(buffer);});}
  function kick(ctx,when,vel){var o=ctx.createOscillator();var g=ctx.createGain();o.type="sine";o.frequency.setValueAtTime(160,when);o.frequency.exponentialRampToValueAtTime(48,when+0.1);g.gain.setValueAtTime(0,when);g.gain.linearRampToValueAtTime(0.9*vel,when+0.005);g.gain.exponentialRampToValueAtTime(0.001,when+0.18);o.connect(g);g.connect(ctx.destination);o.start(when);o.stop(when+0.2);}
  function bassHit(ctx,root,when,dur){var o=ctx.createOscillator();var g=ctx.createGain();o.type="sine";o.frequency.value=root;g.gain.setValueAtTime(0,when);g.gain.linearRampToValueAtTime(0.4,when+0.005);g.gain.setValueAtTime(0.4,when+dur*0.5);g.gain.exponentialRampToValueAtTime(0.001,when+dur);o.connect(g);g.connect(ctx.destination);o.start(when);o.stop(when+dur+0.02);}
  function snare(ctx,when,vel){var n=ctx.createBufferSource();var b=ctx.createBuffer(1,Math.floor(ctx.sampleRate*0.25),ctx.sampleRate);var d=b.getChannelData(0);for(var i=0;i<d.length;i++)d[i]=(Math.random()*2-1)*(1-i/d.length);n.buffer=b;var f=ctx.createBiquadFilter();f.type="bandpass";f.frequency.value=3000;f.Q.value=1;var g=ctx.createGain();g.gain.setValueAtTime(0,when);g.gain.linearRampToValueAtTime(0.6*vel,when+0.002);g.gain.exponentialRampToValueAtTime(0.001,when+0.2);n.connect(f);f.connect(g);g.connect(ctx.destination);n.start(when);n.stop(when+0.25);}
  function hat(ctx,when,vel){var n=ctx.createBufferSource();var b=ctx.createBuffer(1,Math.floor(ctx.sampleRate*0.05),ctx.sampleRate);var d=b.getChannelData(0);for(var i=0;i<d.length;i++)d[i]=(Math.random()*2-1)*(1-i/d.length);n.buffer=b;var f=ctx.createBiquadFilter();f.type="highpass";f.frequency.value=7000;var g=ctx.createGain();g.gain.setValueAtTime(0,when);g.gain.linearRampToValueAtTime(0.4*vel,when+0.001);g.gain.exponentialRampToValueAtTime(0.001,when+0.04);n.connect(f);f.connect(g);g.connect(ctx.destination);n.start(when);n.stop(when+0.05);}
  function encodeWav(buffer){var numCh=buffer.numberOfChannels;var len=buffer.length*numCh*2;var out=new ArrayBuffer(44+len);var v=new DataView(out);function wStr(o,s){for(var i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i));}wStr(0,"RIFF");v.setUint32(4,36+len,true);wStr(8,"WAVE");wStr(12,"fmt ");v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,numCh,true);v.setUint32(24,buffer.sampleRate,true);v.setUint32(28,buffer.sampleRate*numCh*2,true);v.setUint16(32,numCh*2,true);v.setUint16(34,16,true);wStr(36,"data");v.setUint32(40,len,true);var chans=[];for(var i=0;i<numCh;i++)chans.push(buffer.getChannelData(i));var off=44;for(var i=0;i<buffer.length;i++){for(var c=0;c<numCh;c++){var s=Math.max(-1,Math.min(1,chans[c][i]));v.setInt16(off,s<0?s*0x8000:s*0x7FFF,true);off+=2;}}return new Blob([v],{type:"audio/wav"});}
  function urlFromBlob(blob){try{return(window.URL||window.webkitURL).createObjectURL(blob);}catch(e){return"";}}
  function previewBeat(id){var preset=beatPresetById(id);if(!preset){toast("Beat not found.");return;}stopPreview();MS.previewPreset=id;render();renderBeatLoop(preset).then(function(blob){var url=urlFromBlob(blob);if(!url){return;}var a=document.getElementById("vmMusicPlayer");if(a){a.src=url;a.volume=0.9;if(a.play)a.play();}}).catch(function(){MS.previewPreset=null;render();});}
  function stopPreview(){MS.previewPreset=null;var a=document.getElementById("vmMusicPlayer");if(a){try{a.pause();}catch(e){}a.removeAttribute("src");}}
  function selectBeat(id){var preset=beatPresetById(id);if(!preset){return;}var old=MS.state.beat;if(old.url)try{URL.revokeObjectURL(old.url);}catch(e){}MS.previewPreset=null;renderBeatLoop(preset).then(function(blob){var url=urlFromBlob(blob);if(!url){return;}MS.state.beat.url=url;MS.state.beat.name=preset.city;MS.state.beat.dur=0;MS.state.beat.vol=100;MS.state.beat.mute=false;MS.state.beat.solo=false;MS.state.beatPreset=preset.id;var a=new Audio();a.preload="metadata";a.src=url;a.onloadedmetadata=function(){MS.state.beat.dur=a.duration||0;render();};render();toast(preset.city+" loaded.");autoSaveDebounced();}).catch(function(){render();});}

  /* ── Save / load / delete / export ────────────────────────────────── */
  function saveSong(){syncInputs();MS.state.savedAt=Date.now();if(!MS.state.id)MS.state.id="ms"+Date.now();var found=false;for(var i=0;i<MS.projects.length;i++){if(MS.projects[i].id===MS.state.id){MS.projects[i]=normalizeProject(clone(MS.state));found=true;break;}}if(!found)MS.projects.unshift(normalizeProject(clone(MS.state)));saveProjects();pushProjectsToServer();showSaveState("Saved");toast("Song saved.");render();}
  function newSong(){MS.state=defaultState();MS.state.id=null;MS.ui.openTool="";MS.ui.activeNav="";render();}
  function loadSong(id){for(var i=0;i<MS.projects.length;i++){if(MS.projects[i].id===id){MS.state=normalizeProject(clone(MS.projects[i]));MS.ui.openTool="";MS.ui.activeNav="";render();toast("Song loaded.");return;}}}
  function deleteSong(id){MS.projects=MS.projects.filter(function(p){return p.id!==id;});saveProjects();deleteProjectOnServer(id);render();}
  function exportSong(){syncInputs();var title=MS.state.name||"Untitled";var parts=[title,"Genre: "+MS.state.genre+" · Mood: "+MS.state.mood+" · Tempo: "+MS.state.tempo+(MS.state.key?" · Key: "+MS.state.key:""),"",""];if(MS.state.voice)parts[2]="Voice: "+(VOICE_LABELS[MS.state.voice]||MS.state.voice);parts.push((MS.state.aiResult&&MS.state.aiResult.lyrics)||MS.state.lyrics||"(no lyrics yet)");if(MS.state.aiResult&&MS.state.aiResult.arrangement){parts.push("");parts.push("ARRANGEMENT");parts.push(MS.state.aiResult.arrangement);}var blob=new Blob([parts.join("\n")],{type:"text/plain;charset=utf-8"});var url=URL.createObjectURL(blob);var a=document.createElement("a");a.href=url;a.download=title.replace(/[\\/:*?"<>|]+/g,"_")+".txt";document.body.appendChild(a);a.click();document.body.removeChild(a);setTimeout(function(){URL.revokeObjectURL(url);},4000);}

  /* ── Navigation ───────────────────────────────────────────────────── */
  function openNav(id) {
    if (MS.ui.activeNav === id) { MS.ui.activeNav = ""; MS.ui.openTool = ""; }
    else { MS.ui.activeNav = id; MS.ui.openTool = ""; }
    render();
  }
  function openTool(id) {
    MS.ui.openTool = (MS.ui.openTool === id) ? "" : id;
    render();
  }
  function setVoice(v) { MS.state.voice = v; render(); autoSaveDebounced(); }
  function setConsent(v) { MS.state.consent = !!v; render(); autoSaveDebounced(); }
  function applyEffectPresetByIdx(idx) { if (EFFECT_PRESETS[idx]) applyEffectPreset(EFFECT_PRESETS[idx]); }

  /* ── CSS Injection ─────────────────────────────────────────────────── */
  function injectStyles() {
    if (document.getElementById("ms-css")) return;
    var css = [
      "#vmWsPanelMusic{position:relative;overflow:hidden;}",
      "#vmWsPanelMusic .ms-studio{display:flex;flex-direction:column;height:100%;background:#0a0a0f;color:#e2e8f0;font-family:inherit;overflow:hidden;}",
      ".ms-hdr{display:flex;align-items:center;gap:10px;padding:8px 16px;background:#111118;border-bottom:1px solid #1e1e2e;min-height:48px;flex-shrink:0;}",
      ".ms-logo{width:28px;height:28px;background:#f97316;color:#000;font-weight:700;font-size:13px;border-radius:6px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}",
      ".ms-inp{flex:1;background:transparent;border:1px solid transparent;color:#e2e8f0;font-size:15px;font-weight:600;padding:4px 8px;border-radius:4px;outline:none;}",
      ".ms-inp:focus{border-color:#f97316;background:#16161e;}",
      ".ms-sv{font-size:11px;color:#64748b;flex-shrink:0;min-width:50px;text-align:right;}",
      ".ms-sv.saving{color:#f59e0b;}.ms-sv.saved{color:#22c55e;}",
      ".ms-ws{flex:1;overflow-y:auto;padding:16px;position:relative;}",
      ".ms-empty{text-align:center;padding:48px 16px;color:#475569;}",
      ".ms-empty svg{width:48px;height:48px;margin-bottom:12px;opacity:.4;}",
      ".ms-empty p{margin:4px 0;font-size:13px;}",
      ".ms-card{background:#14141f;border:1px solid #1e1e2e;border-radius:10px;padding:12px;margin-bottom:10px;display:flex;align-items:center;gap:12px;}",
      ".ms-card-ico{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}",
      ".ms-card-info{flex:1;min-width:0;}",
      ".ms-card-nm{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",
      ".ms-card-dur{font-size:11px;color:#64748b;}",
      ".ms-card-vol{display:flex;align-items:center;gap:6px;}",
      ".ms-card-vol input[type=range]{width:60px;accent-color:#f97316;}",
      ".ms-card-btns{display:flex;gap:4px;}",
      ".ms-card-btns button{width:28px;height:28px;border-radius:6px;border:1px solid #2a2a3a;background:#0f0f18;color:#94a3b8;font-size:10px;cursor:pointer;display:flex;align-items:center;justify-content:center;}",
      ".ms-card-btns button.on{background:#f97316;color:#000;border-color:#f97316;}",
      ".ms-tr{display:flex;align-items:center;gap:8px;padding:8px 16px;background:#111118;border-top:1px solid #1e1e2e;flex-shrink:0;}",
      ".ms-tr button{width:36px;height:36px;border-radius:50%;border:none;background:#1e1e2e;color:#e2e8f0;cursor:pointer;display:flex;align-items:center;justify-content:center;}",
      ".ms-tr button:hover{background:#2a2a3a;}",
      ".ms-tr button.rec{background:#ef4444;color:#fff;animation:ms-pulse 1s infinite;}",
      "@keyframes ms-pulse{0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.4)}50%{box-shadow:0 0 0 10px rgba(239,68,68,0)}}",
      ".ms-seek{flex:1;height:4px;background:#1e1e2e;border-radius:2px;position:relative;cursor:pointer;}",
      ".ms-seek-fill{height:100%;background:#f97316;border-radius:2px;width:0%;}",
      ".ms-tm{font-size:12px;color:#64748b;font-variant-numeric:tabular-nums;min-width:42px;text-align:center;}",
      ".ms-vol{display:flex;align-items:center;gap:4px;}",
      ".ms-vol input[type=range]{width:50px;accent-color:#f97316;}",
      ".ms-bn{display:flex;background:#111118;border-top:1px solid #1e1e2e;flex-shrink:0;}",
      ".ms-bn button{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:8px 0;border:none;background:transparent;color:#64748b;font-size:10px;cursor:pointer;transition:color .15s;}",
      ".ms-bn button.on{color:#f97316;}",
      ".ms-bn button svg{width:20px;height:20px;}",
      ".ms-pnl{position:absolute;bottom:56px;left:0;right:0;background:#111118;border-top:1px solid #1e1e2e;transform:translateY(110%);transition:transform .25s ease;z-index:10;max-height:60vh;display:flex;flex-direction:column;}",
      ".ms-pnl.open{transform:translateY(0);}",
      ".ms-pnl-h{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-bottom:1px solid #1e1e2e;flex-shrink:0;}",
      ".ms-pnl-h h3{font-size:14px;font-weight:600;margin:0;}",
      ".ms-pnl-x{background:none;border:none;color:#64748b;cursor:pointer;padding:4px;}",
      ".ms-pnl-b{flex:1;overflow-y:auto;padding:12px 16px;}",
      ".ms-sn{display:flex;gap:4px;padding:8px 12px;overflow-x:auto;flex-shrink:0;border-bottom:1px solid #1e1e2e;}",
      ".ms-sn button{flex-shrink:0;padding:6px 10px;border-radius:6px;border:1px solid #2a2a3a;background:transparent;color:#94a3b8;font-size:11px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:4px;}",
      ".ms-sn button.on{background:#f97316;color:#000;border-color:#f97316;}",
      ".ms-sn button svg{width:14px;height:14px;}",
      ".ms-lb{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin:12px 0 6px;font-weight:600;}",
      ".ms-fi{width:100%;background:#0f0f18;border:1px solid #1e1e2e;color:#e2e8f0;padding:8px 10px;border-radius:6px;font-size:13px;outline:none;box-sizing:border-box;}",
      ".ms-fi:focus{border-color:#f97316;}",
      "textarea.ms-fi{resize:vertical;min-height:60px;font-family:inherit;}",
      "select.ms-fi{appearance:none;background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\");background-repeat:no-repeat;background-position:right 8px center;padding-right:28px;}",
      ".ms-rw{display:flex;gap:8px;margin-bottom:10px;}.ms-rw>*{flex:1;}",
      ".ms-btn{padding:8px 14px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;}",
      ".ms-btn.pri{background:#f97316;color:#000;}.ms-btn.pri:hover{background:#ea580c;}",
      ".ms-btn.sec{background:#1e1e2e;color:#e2e8f0;}.ms-btn.sec:hover{background:#2a2a3a;}",
      ".ms-btn.dng{background:#dc2626;color:#fff;}",
      ".ms-btn.sm{padding:5px 10px;font-size:11px;}",
      ".ms-tog{display:flex;align-items:center;gap:8px;cursor:pointer;}",
      ".ms-tog input[type=checkbox]{accent-color:#f97316;width:16px;height:16px;}",
      ".ms-tog span{font-size:12px;}",
      ".ms-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;}",
      ".ms-beat{background:#14141f;border:1px solid #1e1e2e;border-radius:8px;padding:10px;cursor:pointer;transition:all .15s;}",
      ".ms-beat:hover{border-color:#f97316;}.ms-beat.on{border-color:#f97316;background:#1a1a28;}",
      ".ms-beat-ct{font-size:13px;font-weight:600;margin-bottom:4px;}",
      ".ms-beat-mt{font-size:11px;color:#64748b;}",
      ".ms-chip{display:inline-flex;padding:6px 10px;border-radius:6px;border:1px solid #2a2a3a;background:#0f0f18;color:#94a3b8;font-size:11px;cursor:pointer;margin:0 4px 6px 0;transition:all .12s;}",
      ".ms-chip.on{background:#f97316;color:#000;border-color:#f97316;}",
      ".ms-slr{display:flex;align-items:center;gap:8px;margin-bottom:8px;}",
      ".ms-slr label{font-size:12px;color:#94a3b8;min-width:70px;}",
      ".ms-slr input[type=range]{flex:1;accent-color:#f97316;}",
      ".ms-slr span{font-size:11px;color:#64748b;min-width:30px;text-align:right;}",
      ".ms-pc{background:#14141f;border:1px solid #1e1e2e;border-radius:8px;padding:10px;margin-bottom:8px;display:flex;align-items:center;gap:10px;}",
      ".ms-pc:hover{border-color:#2a2a3a;}",
      ".ms-pc-info{flex:1;min-width:0;}",
      ".ms-pc-nm{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",
      ".ms-pc-mt{font-size:11px;color:#64748b;}",
      ".ms-pc-btns{display:flex;gap:4px;}",
      ".ms-rec-big{width:80px;height:80px;border-radius:50%;border:3px solid #ef4444;background:#1a0000;color:#ef4444;display:flex;align-items:center;justify-content:center;cursor:pointer;margin:0 auto 12px;transition:all .2s;}",
      ".ms-rec-big.rec{background:#ef4444;color:#fff;animation:ms-pulse 1s infinite;}",
      ".ms-consent{background:#0f0f18;border:1px solid #1e1e2e;border-radius:8px;padding:10px;margin-top:8px;}",
      ".ms-consent label{font-size:11px;color:#94a3b8;display:flex;align-items:flex-start;gap:6px;cursor:pointer;line-height:1.4;}",
      ".ms-consent input{margin-top:2px;accent-color:#f97316;}",
      ".ms-vc{background:#14141f;border:1px solid #1e1e2e;border-radius:8px;padding:10px;margin-bottom:8px;cursor:pointer;transition:all .12s;}",
      ".ms-vc.on{border-color:#f97316;background:#1a1a28;}",
      ".ms-vc .vn{font-size:13px;font-weight:600;}.ms-vc .vs{font-size:11px;color:#64748b;margin-top:2px;}",
      ".ms-mi{background:#0f0f18;border-radius:6px;padding:8px;margin-bottom:6px;font-size:12px;}",
      ".ms-mi .mt{color:#64748b;font-size:10px;margin-top:2px;}",
      ".ms-res{background:#0f0f18;border-radius:8px;padding:12px;font-size:12px;white-space:pre-wrap;max-height:200px;overflow-y:auto;line-height:1.5;}",
      "@media(min-width:769px){",
      ".ms-pnl{left:auto;width:380px;max-height:100%;border-top:none;border-left:1px solid #1e1e2e;bottom:0;top:48px;transform:translateX(100%);}",
      ".ms-pnl.open{transform:translateX(0);}",
      ".ms-grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr));}",
      "}"
    ].join("\n");
    var s = document.createElement("style");
    s.id = "ms-css";
    s.textContent = css;
    document.head.appendChild(s);
  }

  /* ── Workspace Renderer ────────────────────────────────────────────── */
  function renderTrackCard(name, icon, color, track, kind) {
    var muted = trackMuted(track);
    var solo = track.solo;
    var mute = track.mute;
    return '<div class="ms-card">' +
      '<div class="ms-card-ico" style="background:' + color + ';color:#fff;font-size:16px;">' + icon + '</div>' +
      '<div class="ms-card-info"><div class="ms-card-nm">' + esc(name) + '</div>' +
      '<div class="ms-card-dur">' + fmtTime(track.dur) + (muted ? ' (muted)' : '') + '</div></div>' +
      '<div class="ms-card-vol"><input type="range" min="0" max="100" value="' + track.vol + '" oninput="VMMusic.setTrackVol(\'' + kind + '\',this.value)"></div>' +
      '<div class="ms-card-btns">' +
      '<button' + (solo ? ' class="on"' : '') + ' onclick="VMMusic.setTrackProp(\'' + kind + '\',\'solo\',' + (!solo) + ')" title="Solo">S</button>' +
      '<button' + (mute ? ' class="on"' : '') + ' onclick="VMMusic.setTrackProp(\'' + kind + '\',\'mute\',' + (!mute) + ')" title="Mute">M</button>' +
      '</div></div>';
  }
  function renderWorkspace() {
    var s = MS.state;
    var hasTake = s.take && s.take.url;
    var hasBeat = s.beat && s.beat.url;
    var hasLayers = s.layers && s.layers.length;
    var hasAi = s.aiResult;
    if (!hasTake && !hasBeat && !hasLayers && !hasAi) {
      return '<div class="ms-empty"><svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>' +
        '<p style="font-size:15px;font-weight:600;margin-top:8px;">Start your song</p>' +
        '<p>Record vocals, add a beat, or use Create to generate with AI.</p></div>';
    }
    var h = '';
    if (hasTake) h += renderTrackCard(s.take.name || 'Vocal', '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg>', '#f97316', s.take, 'take');
    if (hasBeat) h += renderTrackCard(s.beat.name || 'Beat', '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>', '#8b5cf6', s.beat, 'beat');
    if (hasLayers) {
      s.layers.forEach(function(l) {
        h += renderTrackCard(l.name, '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>', '#06b6d4', l, 'layer_' + l.id);
      });
    }
    if (hasAi) {
      h += '<div class="ms-card" style="border-color:#f9731630;">' +
        '<div class="ms-card-ico" style="background:#f97316;color:#000;font-size:16px;">&#10024;</div>' +
        '<div class="ms-card-info"><div class="ms-card-nm">AI Generated Package</div>' +
        '<div class="ms-card-dur">' + esc(s.genre) + ' &middot; ' + esc(s.mood) + ' &middot; ' + esc(s.tempo) + '</div></div></div>';
    }
    return h;
  }

  /* ── Panel Content Renderers ───────────────────────────────────────── */
  function panelLabel(id) {
    var m = {record:'Record',tracks:'Tracks',generate:'Create',tools:'Tools',projects:'Projects'};
    return m[id] || '';
  }
  function renderRecordPanel() {
    var s = MS.state;
    var isRec = MS.recorder && MS.recorder.state === "recording";
    var h = '<div class="ms-rec-big' + (isRec ? ' rec' : '') + '" onclick="VMMusic.toggleRecord()">';
    h += isRec ? '<svg width="32" height="32" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>'
      : '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>';
    h += '</div>';
    h += '<div style="text-align:center;font-size:20px;font-variant-numeric:tabular-nums;margin-bottom:16px;" id="msRecTime">' + fmtTime(MS.elapsed) + '</div>';
    h += '<div class="ms-lb">Upload vocals</div>';
    h += '<input type="file" accept="audio/*" style="display:none" id="msTakeFile" onchange="VMMusic.onTakeFile(this)">';
    h += '<button class="ms-btn sec" onclick="document.getElementById(\'msTakeFile\').click()">&#128228; Upload vocal</button>';
    h += '<div class="ms-lb">Voice</div>';
    ["keep","clone","elena"].forEach(function(v) {
      h += '<div class="ms-vc' + (s.voice === v ? ' on' : '') + '" onclick="VMMusic.setVoice(\'' + v + '\')">' +
        '<div class="vn">' + esc(VOICE_LABELS[v]) + '</div>' +
        '<div class="vs">' + esc(VOICE_SUBS[v]) + '</div></div>';
    });
    h += '<div class="ms-consent"><label><input type="checkbox" ' + (s.consent ? 'checked' : '') + ' onchange="VMMusic.setConsent(this.checked)">I authorize ValleyMind to process my voice for this song.</label></div>';
    return h;
  }
  function renderTracksPanel() {
    var s = MS.state;
    var h = '';
    if (s.take && s.take.url) {
      h += '<div class="ms-lb">Vocals</div>';
      h += '<div class="ms-card"><div class="ms-card-ico" style="background:#f97316;color:#fff;font-size:16px;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg></div>';
      h += '<div class="ms-card-info"><div class="ms-card-nm">' + esc(s.take.name || 'Vocal') + '</div><div class="ms-card-dur">' + fmtTime(s.take.dur) + '</div></div>';
      h += '<div class="ms-card-vol"><input type="range" min="0" max="100" value="' + s.take.vol + '" oninput="VMMusic.setTrackVol(\'take\',this.value)"></div>';
      h += '<div class="ms-card-btns"><button' + (s.take.solo ? ' class="on"' : '') + ' onclick="VMMusic.setTrackProp(\'take\',\'solo\',' + (!s.take.solo) + ')">S</button><button' + (s.take.mute ? ' class="on"' : '') + ' onclick="VMMusic.setTrackProp(\'take\',\'mute\',' + (!s.take.mute) + ')">M</button></div></div>';
    } else {
      h += '<div class="ms-lb">Vocals</div><p style="font-size:12px;color:#475569;">No vocal yet. Use Record tab.</p>';
    }
    if (s.beat && s.beat.url) {
      h += '<div class="ms-lb">Beat</div>';
      h += '<div class="ms-card"><div class="ms-card-ico" style="background:#8b5cf6;color:#fff;font-size:16px;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg></div>';
      h += '<div class="ms-card-info"><div class="ms-card-nm">' + esc(s.beat.name || 'Beat') + '</div><div class="ms-card-dur">' + fmtTime(s.beat.dur) + '</div></div>';
      h += '<div class="ms-card-vol"><input type="range" min="0" max="100" value="' + s.beat.vol + '" oninput="VMMusic.setTrackVol(\'beat\',this.value)"></div>';
      h += '<div class="ms-card-btns"><button' + (s.beat.solo ? ' class="on"' : '') + ' onclick="VMMusic.setTrackProp(\'beat\',\'solo\',' + (!s.beat.solo) + ')">S</button><button' + (s.beat.mute ? ' class="on"' : '') + ' onclick="VMMusic.setTrackProp(\'beat\',\'mute\',' + (!s.beat.mute) + ')">M</button></div></div>';
    }
    if (s.layers && s.layers.length) {
      h += '<div class="ms-lb">Layers</div>';
      s.layers.forEach(function(l) {
        h += '<div class="ms-card"><div class="ms-card-ico" style="background:#06b6d4;color:#fff;font-size:16px;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div>';
        h += '<div class="ms-card-info"><div class="ms-card-nm">' + esc(l.name) + '</div><div class="ms-card-dur">' + fmtTime(l.dur) + '</div></div>';
        h += '<div class="ms-card-vol"><input type="range" min="0" max="100" value="' + l.vol + '" oninput="VMMusic.setLayerVol(\'' + l.id + '\',this.value)"></div>';
        h += '<div class="ms-card-btns"><button' + (l.solo ? ' class="on"' : '') + ' onclick="VMMusic.setLayerProp(\'' + l.id + '\',\'solo\',' + (!l.solo) + ')">S</button><button' + (l.mute ? ' class="on"' : '') + ' onclick="VMMusic.setLayerProp(\'' + l.id + '\',\'mute\',' + (!l.mute) + ')">M</button><button onclick="VMMusic.removeLayer(\'' + l.id + '\')" title="Remove">&#10005;</button></div></div>';
      });
    }
    h += '<div style="margin-top:12px"><input type="file" accept="audio/*" style="display:none" id="msLayerFile" onchange="VMMusic.onLayerFile(this)"><button class="ms-btn sec" onclick="document.getElementById(\'msLayerFile\').click()">+ Add layer</button></div>';
    return h;
  }
  function renderCreatePanel() {
    var s = MS.state;
    var h = '<div class="ms-lb">Song description</div>';
    h += '<textarea class="ms-fi" id="vmMusicBrief" rows="3" placeholder="Describe your song...">' + esc(s.brief) + '</textarea>';
    h += '<div class="ms-rw"><div><div class="ms-lb">Genre</div><select class="ms-fi" id="vmMusicGenre">';
    GENRES.forEach(function(g) { h += '<option' + (s.genre === g ? ' selected' : '') + '>' + g + '</option>'; });
    h += '</select></div><div><div class="ms-lb">Mood</div><select class="ms-fi" id="vmMusicMood">';
    MOODS.forEach(function(m) { h += '<option' + (s.mood === m ? ' selected' : '') + '>' + m + '</option>'; });
    h += '</select></div></div>';
    h += '<div class="ms-rw"><div><div class="ms-lb">Tempo</div><select class="ms-fi" id="vmMusicTempo">';
    TEMPOS.forEach(function(t) { h += '<option' + (s.tempo === t ? ' selected' : '') + '>' + t + '</option>'; });
    h += '</select></div><div><div class="ms-lb">Key</div><input class="ms-fi" id="vmMusicKey" value="' + esc(s.key) + '" placeholder="e.g. C minor"></div></div>';
    h += '<div class="ms-rw"><div><div class="ms-lb">Role</div><select class="ms-fi" id="vmMusicRole">';
    ROLES.forEach(function(r) { h += '<option' + (s.role === r ? ' selected' : '') + '>' + r + '</option>'; });
    h += '</select></div><div><div class="ms-lb">Language</div><input class="ms-fi" id="vmMusicLanguage" value="' + esc(s.language) + '"></div></div>';
    h += '<button class="ms-btn pri" style="width:100%;margin-top:8px" onclick="VMMusic.runAI()">&#10024; Generate with AI</button>';
    if (s.aiResult) {
      h += '<div class="ms-lb" style="margin-top:16px">AI Result</div>';
      h += '<div class="ms-card" style="border-color:#f9731640"><div class="ms-card-info"><div class="ms-card-nm">Generated Package</div>';
      if (s.aiResult.lyrics) h += '<div class="ms-card-dur" style="white-space:pre-wrap;margin-top:4px;max-height:120px;overflow-y:auto">' + esc(s.aiResult.lyrics.substring(0, 300)) + (s.aiResult.lyrics.length > 300 ? '...' : '') + '</div>';
      h += '</div></div>';
    }
    return h;
  }
  function renderProjectsPanel() {
    var h = '<div style="display:flex;gap:8px;margin-bottom:12px"><button class="ms-btn pri" onclick="VMMusic.newSong()">+ New song</button></div>';
    if (!MS.projects.length) h += '<p style="color:#475569;font-size:13px;">No saved projects yet.</p>';
    MS.projects.forEach(function(p) {
      h += '<div class="ms-pc"><div class="ms-pc-info"><div class="ms-pc-nm">' + esc(p.name || 'Untitled') + '</div>';
      h += '<div class="ms-pc-mt">' + esc(p.genre || '') + ' &middot; ' + esc(p.mood || '') + (p.savedAt ? ' &middot; ' + new Date(p.savedAt).toLocaleDateString() : '') + '</div></div>';
      h += '<div class="ms-pc-btns"><button class="ms-btn sm sec" onclick="VMMusic.loadSong(\'' + p.id + '\')">Load</button><button class="ms-btn sm dng" onclick="VMMusic.deleteSong(\'' + p.id + '\')">&#10005;</button></div></div>';
    });
    return h;
  }

  /* ── Tool Sub-Panel Renderers ──────────────────────────────────────── */
  function renderToolsSubnav(active) {
    var h = '<div class="ms-sn">';
    TOOLS.forEach(function(t) {
      h += '<button' + (active === t.id ? ' class="on"' : '') + ' onclick="VMMusic.openTool(\'' + t.id + '\')"><i data-lucide="' + t.icon + '"></i>' + t.label + '</button>';
    });
    return h + '</div>';
  }
  function renderToolContent(id) {
    switch (id) {
      case "voice": return renderVoiceSub();
      case "music": return renderBeatsSub();
      case "instruments": return renderInstrSub();
      case "lyrics": return renderLyricsSub();
      case "effects": return renderFxSub();
      case "mix": return renderMixSub();
      case "ai-edit": return renderAiEditSub();
      case "memory": return renderMemSub();
      case "assets": return renderAssetsSub();
      case "ai-tools": return renderAiToolsSub();
      default: return '<p style="color:#475569;font-size:13px">Select a tool above.</p>';
    }
  }
  function renderVoiceSub() {
    var s = MS.state;
    var h = '<div class="ms-lb">Voice Type</div>';
    ["keep","clone","elena"].forEach(function(v) {
      h += '<div class="ms-vc' + (s.voice === v ? ' on' : '') + '" onclick="VMMusic.setVoice(\'' + v + '\')">';
      h += '<div class="vn">' + esc(VOICE_LABELS[v]) + '</div><div class="vs">' + esc(VOICE_SUBS[v]) + '</div></div>';
    });
    h += '<div class="ms-consent"><label><input type="checkbox" ' + (s.consent ? 'checked' : '') + ' onchange="VMMusic.setConsent(this.checked)">I authorize ValleyMind to process my voice.</label></div>';
    return h;
  }
  function renderBeatsSub() {
    var h = '<div class="ms-lb">Beat Library</div><div class="ms-grid">';
    BEAT_PRESETS.forEach(function(b) {
      var active = MS.state.beatPreset === b.id;
      h += '<div class="ms-beat' + (active ? ' on' : '') + '" onclick="VMMusic.selectBeat(\'' + b.id + '\')">';
      h += '<div class="ms-beat-ct" style="color:' + b.color + '">' + esc(b.city) + '</div>';
      h += '<div class="ms-beat-mt">' + b.bpm + ' BPM &middot; ' + b.mood + '</div>';
      h += '<div style="margin-top:6px"><button class="ms-btn sm sec" onclick="event.stopPropagation();VMMusic.previewBeat(\'' + b.id + '\')">&#9654; Preview</button></div></div>';
    });
    h += '</div><div class="ms-lb">Upload beat</div>';
    h += '<input type="file" accept="audio/*" style="display:none" id="msBeatFile" onchange="VMMusic.onBeatFile(this)">';
    h += '<button class="ms-btn sec" onclick="document.getElementById(\'msBeatFile\').click()">&#128228; Upload your own beat</button>';
    return h;
  }
  function renderInstrSub() {
    var s = MS.state;
    if (!s.aiResult || !s.aiResult.arrangement) return '<p style="color:#475569;font-size:13px">Generate music first to see AI instrumentation.</p>';
    return '<div class="ms-lb">AI Arrangement</div><div class="ms-res">' + esc(s.aiResult.arrangement) + '</div>';
  }
  function renderLyricsSub() {
    var h = '<div class="ms-lb">Lyrics</div>';
    h += '<textarea class="ms-fi" id="vmMusicLyrics" rows="10" placeholder="Write your lyrics here...">' + esc(MS.state.lyrics) + '</textarea>';
    h += '<div style="margin-top:8px;display:flex;gap:8px"><button class="ms-btn sec" onclick="VMMusic.exportSong()">Export</button></div>';
    return h;
  }
  function renderFxSub() {
    var s = MS.state;
    var h = '<div class="ms-lb">Presets</div>';
    EFFECT_PRESETS.forEach(function(p, i) {
      var active = s.fx.effect === p.fx.effect && s.fx.reverb === p.fx.reverb && s.fx.delay === p.fx.delay;
      h += '<span class="ms-chip' + (active ? ' on' : '') + '" onclick="VMMusic.applyFx(' + i + ')">' + esc(p.name) + '</span>';
    });
    h += '<div class="ms-lb">Custom</div>';
    h += '<div class="ms-tog"><input type="checkbox" ' + (s.fx.noiseReduction ? 'checked' : '') + ' onchange="VMMusic.setFx(\'noiseReduction\',this.checked)"><span>Noise reduction</span></div>';
    h += '<div class="ms-slr"><label>Pitch</label><input type="range" min="-12" max="12" value="' + s.fx.pitch + '" oninput="VMMusic.setFx(\'pitch\',this.value)"><span>' + s.fx.pitch + '</span></div>';
    h += '<div class="ms-slr"><label>Reverb</label><input type="range" min="0" max="100" value="' + s.fx.reverb + '" oninput="VMMusic.setFx(\'reverb\',this.value)"><span>' + s.fx.reverb + '</span></div>';
    h += '<div class="ms-slr"><label>Delay</label><input type="range" min="0" max="100" value="' + s.fx.delay + '" oninput="VMMusic.setFx(\'delay\',this.value)"><span>' + s.fx.delay + '</span></div>';
    return h;
  }
  function renderMixSub() {
    var s = MS.state;
    var h = '<div class="ms-lb">Master Volume</div>';
    h += '<div class="ms-slr"><label>Master</label><input type="range" min="0" max="100" value="' + s.mix.master + '" oninput="VMMusic.setMaster(this.value)"><span>' + s.mix.master + '</span></div>';
    h += '<div style="margin-top:12px;display:flex;gap:8px"><button class="ms-btn sec" onclick="VMMusic.autoMix()">Auto Mix</button><button class="ms-btn sec" onclick="VMMusic.autoMaster()">Auto Master</button></div>';
    h += '<div class="ms-lb" style="margin-top:16px">Track Levels</div>';
    if (s.take && s.take.url) h += '<div class="ms-slr"><label>Vocal</label><input type="range" min="0" max="100" value="' + s.take.vol + '" oninput="VMMusic.setTrackVol(\'take\',this.value)"><span>' + s.take.vol + '</span></div>';
    if (s.beat && s.beat.url) h += '<div class="ms-slr"><label>Beat</label><input type="range" min="0" max="100" value="' + s.beat.vol + '" oninput="VMMusic.setTrackVol(\'beat\',this.value)"><span>' + s.beat.vol + '</span></div>';
    s.layers.forEach(function(l) {
      h += '<div class="ms-slr"><label>' + esc(l.name) + '</label><input type="range" min="0" max="100" value="' + l.vol + '" oninput="VMMusic.setLayerVol(\'' + l.id + '\',this.value)"><span>' + l.vol + '</span></div>';
    });
    return h;
  }
  function renderAiEditSub() {
    var h = '<div class="ms-lb">AI Edit Instruction</div>';
    h += '<textarea class="ms-fi" id="msAiEditInput" rows="3" placeholder="e.g. Make the chorus more upbeat, add a bridge..."></textarea>';
    h += '<button class="ms-btn pri" style="width:100%;margin-top:8px" onclick="VMMusic.runAiEdit()">Apply changes</button>';
    if (MS.state.lastAiEdit) {
      h += '<div class="ms-lb" style="margin-top:16px">Last edit</div>';
      h += '<div class="ms-mi">' + esc(MS.state.lastAiEdit.instruction) + '<div class="mt">' + esc(MS.state.lastAiEdit.summary || '') + '</div></div>';
    }
    return h;
  }
  function renderMemSub() {
    var h = '<div class="ms-lb">Session Memory</div>';
    if (!MS.memory.length) h += '<p style="color:#475569;font-size:13px">No memory entries yet.</p>';
    MS.memory.forEach(function(m) {
      h += '<div class="ms-mi">' + esc(m.type || '') + ': ' + esc(m.instruction || m.name || m.summary || '');
      h += '<div class="mt">' + new Date(m.date || 0).toLocaleString() + '</div></div>';
    });
    return h;
  }
  function renderAssetsSub() {
    var s = MS.state;
    var h = '<div class="ms-lb">Audio Assets</div>';
    var assets = [];
    if (s.take && s.take.url) assets.push({name: s.take.name || "Vocal", dur: s.take.dur});
    if (s.beat && s.beat.url) assets.push({name: s.beat.name || "Beat", dur: s.beat.dur});
    s.layers.forEach(function(l) { if (l.url) assets.push({name: l.name, dur: l.dur}); });
    if (!assets.length) h += '<p style="color:#475569;font-size:13px">No audio files yet.</p>';
    assets.forEach(function(a) {
      h += '<div class="ms-card"><div class="ms-card-info"><div class="ms-card-nm">' + esc(a.name) + '</div><div class="ms-card-dur">' + fmtTime(a.dur) + '</div></div></div>';
    });
    return h;
  }
  function renderAiToolsSub() {
    var s = MS.state;
    if (!s.aiResult) return '<p style="color:#475569;font-size:13px">Generate music first to access AI tools.</p>';
    var h = '<div class="ms-lb">AI Generated Content</div>';
    if (s.aiResult.lyrics) h += '<div class="ms-mi"><div class="mt">LYRICS</div><div style="white-space:pre-wrap;margin-top:4px">' + esc(s.aiResult.lyrics.substring(0, 500)) + (s.aiResult.lyrics.length > 500 ? '...' : '') + '</div></div>';
    if (s.aiResult.arrangement) h += '<div class="ms-mi"><div class="mt">ARRANGEMENT</div><div style="white-space:pre-wrap;margin-top:4px">' + esc(s.aiResult.arrangement.substring(0, 500)) + (s.aiResult.arrangement.length > 500 ? '...' : '') + '</div></div>';
    if (s.aiResult.suggestions) h += '<div class="ms-mi"><div class="mt">SUGGESTIONS</div><div style="white-space:pre-wrap;margin-top:4px">' + esc(String(s.aiResult.suggestions).substring(0, 500)) + '</div></div>';
    return h;
  }

  /* ── Main Render ───────────────────────────────────────────────────── */
  function render() {
    var panel = document.getElementById("vmWsPanelMusic");
    if (!panel) return;
    injectStyles();
    syncInputs();
    var s = MS.state;
    var nav = MS.ui.activeNav;
    var tool = MS.ui.openTool;
    var isRec = MS.recorder && MS.recorder.state === "recording";
    var svCls = MS.ui.saveState === "Saving..." ? " saving" : (MS.ui.saveState === "Saved" ? " saved" : "");
    var wsHtml = renderWorkspace();
    var pnlHtml = "";
    if (nav) {
      pnlHtml = '<div class="ms-pnl open">';
      pnlHtml += '<div class="ms-pnl-h"><h3>' + esc(panelLabel(nav)) + '</h3>';
      pnlHtml += '<button class="ms-pnl-x" onclick="VMMusic.openNav(\'\')"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" x2="6" y1="6" y2="18"/><line x1="6" x2="18" y1="6" y2="18"/></svg></button></div>';
      if (nav === "tools") {
        pnlHtml += renderToolsSubnav(tool);
        pnlHtml += '<div class="ms-pnl-b">' + renderToolContent(tool) + '</div>';
      } else {
        var body = "";
        if (nav === "record") body = renderRecordPanel();
        else if (nav === "tracks") body = renderTracksPanel();
        else if (nav === "generate") body = renderCreatePanel();
        else if (nav === "projects") body = renderProjectsPanel();
        pnlHtml += '<div class="ms-pnl-b">' + body + '</div>';
      }
      pnlHtml += '</div>';
    }
    var micSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg>';
    var playSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    var stopSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>';
    var discSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>';
    panel.innerHTML = '<div class="ms-studio">' +
      '<div class="ms-hdr"><div class="ms-logo">V</div>' +
      '<input class="ms-inp" id="vmMusicName" value="' + esc(s.name) + '">' +
      '<span class="ms-sv' + svCls + '" id="msSaveStatus">' + esc(MS.ui.saveState) + '</span></div>' +
      '<div class="ms-ws">' + wsHtml + '</div>' +
      '<div class="ms-tr">' +
      '<button class="' + (isRec ? 'rec' : '') + '" onclick="VMMusic.toggleRecord()" title="Record">' + micSvg + '</button>' +
      '<button onclick="VMMusic.playTrack(\'vocal\')" title="Play vocal">' + playSvg + '</button>' +
      '<button onclick="VMMusic.playTrack(\'beat\')" title="Play beat">' + discSvg + '</button>' +
      '<button onclick="VMMusic.stopPlayback()" title="Stop">' + stopSvg + '</button>' +
      '<div class="ms-seek"><div class="ms-seek-fill"></div></div>' +
      '<span class="ms-tm" id="msRecTime">' + fmtTime(MS.elapsed) + '</span>' +
      '<div class="ms-vol"><input type="range" min="0" max="100" value="' + s.mix.master + '" oninput="VMMusic.setMaster(this.value)"></div>' +
      '</div>' +
      '<div class="ms-bn">' +
      NAV.map(function(n) {
        return '<button' + (nav === n.id ? ' class="on"' : '') + ' onclick="VMMusic.openNav(\'' + n.id + '\')"><i data-lucide="' + n.icon + '"></i>' + n.label + '</button>';
      }).join("") +
      '</div>' + pnlHtml + '</div>';
    refreshLucide();
  }

  /* ── API Surface ───────────────────────────────────────────────────── */
  window.VMMusic = {
    render: render, openNav: openNav, openTool: openTool,
    toggleRecord: toggleRecord, stopRecord: stopRecord,
    playTrack: playTrack, stopPlayback: stopPlayback,
    setTrackVol: function(kind, val) { setTrack(kind === "take" ? "take" : "beat", "vol", val); },
    setTrackProp: function(kind, prop, val) { setTrack(kind === "take" ? "take" : "beat", prop, val); },
    setLayerVol: function(id, val) { setLayer(id, "vol", val); },
    setLayerProp: function(id, prop, val) { setLayer(id, prop, val); },
    removeLayer: removeLayer,
    onTakeFile: onTakeFile, onBeatFile: onBeatFile, onLayerFile: onLayerFile,
    setVoice: setVoice, setConsent: setConsent,
    setFx: setFx, applyFx: applyEffectPresetByIdx,
    setMaster: setMaster, autoMix: autoMix, autoMaster: autoMaster,
    runAI: runAI, runAiEdit: runAiEdit,
    previewBeat: previewBeat, selectBeat: selectBeat,
    newSong: newSong, loadSong: loadSong, deleteSong: deleteSong, saveSong: saveSong, exportSong: exportSong
  };

  /* ── Init ──────────────────────────────────────────────────────────── */
  function init() {
    injectStyles();
    loadProjects();
    loadMemory();
    MS.state = defaultState();
    fetchProjectsFromServer();
    render();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
