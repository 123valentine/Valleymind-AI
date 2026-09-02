/* ValleyMind Music Studio — Professional DAW-Inspired Redesign
   -----------------------------------------------------------
   Two-panel layout: collapsible sidebar (12 sections) + main workspace.
   All existing functionality preserved: recording, upload, beats, effects,
   mix, projects, AI generation. New: AI Edit, Memory, Assets, responsive
   mobile bottom sheet.

   NO white outer frame. Dark native ValleyMind look throughout.
   Sidebar: only one section open at a time (accordion).
   Mobile: bottom tab bar opens a slide-up drawer per section.

   State → localStorage + cloud sync via /api/music/projects.
   AI generation → POST /api/music (existing backend).
   AI edit → POST /api/music/ai-edit (new incremental refinement).
*/
(function () {
  "use strict";

  var NS = "vmMusic";
  var STORE_KEY = "vmMusicProjects";
  var CACHE_BUST = "?v=4";

  /* ── Sidebar sections ─────────────────────────────────────────────── */
  var SECTIONS = [
    { id: "create",   icon: "sparkles",       label: "Create",    sub: "Generate new music" },
    { id: "voice",    icon: "mic",             label: "Voice",     sub: "Record & choose voice" },
    { id: "music",    icon: "music",           label: "Music",     sub: "Beats & backing tracks" },
    { id: "instruments", icon: "piano",        label: "Instruments", sub: "AI instrumentation" },
    { id: "lyrics",   icon: "file-text",       label: "Lyrics",    sub: "Write & refine lyrics" },
    { id: "effects",  icon: "audio-lines",     label: "Effects",   sub: "Vocal effect presets" },
    { id: "mix",      icon: "mixer",           label: "Mix",       sub: "Balance & master" },
    { id: "ai-edit",  icon: "brain",           label: "AI Edit",   sub: "Natural-language refine" },
    { id: "projects", icon: "folder",          label: "Projects",  sub: "Saved songs" },
    { id: "memory",   icon: "brain-circuit",   label: "Memory",    sub: "Music preferences" },
    { id: "assets",   icon: "library",         label: "Assets",    sub: "Generated & uploaded" },
    { id: "ai-tools", icon: "bot",             label: "AI Tools",  sub: "Production utilities" }
  ];

  /* ── Constants ────────────────────────────────────────────────────── */
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
  var GENRES = ["Afrobeats","Amapiano","R&B","Hip-Hop","Pop","Soul","Gospel","Highlife","Dancehall","Reggae","Folk","Jazz","Electronic"];
  var MOODS = ["Romantic","Upbeat","Melancholic","Hopeful","Energetic","Chill","Bittersweet","Empowering","Nostalgic"];
  var TEMPOS = ["Slow","Medium","Fast","Very fast"];
  var ROLES = ["Singer","Rapper","Singer-songwriter","Producer","Both singing & producing"];

  var EFFECT_PRESETS = [
    { name:"Clean",   color:"#22d3ee", ic:"mic",       fx:{noiseReduction:true,pitch:0,effect:"None",reverb:10,delay:0} },
    { name:"Intune",  color:"#34d399", ic:"sliders",   fx:{noiseReduction:true,pitch:0,effect:"Intune",reverb:20,delay:0} },
    { name:"Megaphone",color:"#fbbf24",ic:"volume-2",  fx:{noiseReduction:false,pitch:0,effect:"Megaphone",reverb:5,delay:0} },
    { name:"Warm",    color:"#fb923c", ic:"flame",     fx:{noiseReduction:false,pitch:0,effect:"Warm",reverb:35,delay:0} },
    { name:"Bright",  color:"#a3e635", ic:"sun",       fx:{noiseReduction:true,pitch:8,effect:"Bright",reverb:15,delay:0} },
    { name:"Hall",    color:"#60a5fa", ic:"wind",      fx:{noiseReduction:false,pitch:0,effect:"Hall reverb",reverb:70,delay:10} },
    { name:"Delay",   color:"#c084fc", ic:"repeat",    fx:{noiseReduction:false,pitch:0,effect:"Delay",reverb:30,delay:55} },
    { name:"Tape",    color:"#f472b6", ic:"tape",      fx:{noiseReduction:false,pitch:-12,effect:"Tape",reverb:25,delay:0} },
    { name:"Robot",   color:"#94a3b8", ic:"bot",       fx:{noiseReduction:true,pitch:0,effect:"Robotic",reverb:5,delay:0} },
    { name:"Choir",   color:"#818cf8", ic:"users",     fx:{noiseReduction:false,pitch:4,effect:"Choir-ish",reverb:65,delay:15} },
    { name:"Lo-Fi",   color:"#a16207", ic:"disc-3",    fx:{noiseReduction:false,pitch:-6,effect:"Lo-Fi",reverb:40,delay:0} },
    { name:"Phone",   color:"#64748b", ic:"smartphone",fx:{noiseReduction:false,pitch:0,effect:"Telephone",reverb:5,delay:0} }
  ];

  var BEAT_PRESETS = [
    { id:"bl-midnight",city:"Lagos Midnight",bpm:95,note:"C4",mood:"Romantic",color:"#e74c3c",desc:"Slow candle-lit groove.",pattern:"T00L00K0T00K0K0"},
    { id:"bl-accra",city:"Accra Breeze",bpm:100,note:"G4",mood:"Chill",color:"#1abc9c",desc:"Earthy highlife bounce.",pattern:"T0K0T0K0T0K0T0K0K0"},
    { id:"bl-abuja",city:"Abuja Sunrise",bpm:110,note:"D4",mood:"Hopeful",color:"#f39c12",desc:"Bright, uplifting groove.",pattern:"K0T0K0T0K0T0K0T0K0"},
    { id:"bl-ph",city:"Port Harcourt Groove",bpm:120,note:"A4",mood:"Upbeat",color:"#9b59b6",desc:"Party-ready log drum.",pattern:"K00T0K0KT0K0K0T0"},
    { id:"bl-enugu",city:"Enugu Nights",bpm:85,note:"E4",mood:"Melancholic",color:"#34495e",desc:"Deep, moody R&B heart.",pattern:"K00L00K0T00L00K0"},
    { id:"bl-ibadan",city:"Ibadan Vibes",bpm:130,note:"F4",mood:"Energetic",color:"#27ae60",desc:"Fast street vibration.",pattern:"K0K0K0T0K0K0K0T0K0"},
    { id:"bl-kano",city:"Kano Dust",bpm:90,note:"Bb3",mood:"Nostalgic",color:"#8B4513",desc:"Old-school desert soul.",pattern:"K0T00K0T00K0T00"},
    { id:"bl-warri",city:"Warri Energy",bpm:125,note:"Eb4",mood:"Upbeat",color:"#dc143c",desc:"High-energy bounce.",pattern:"KK0T0KK0T0KK0T0K0"},
    { id:"bl-benin",city:"Benin City Soul",bpm:95,note:"C4",mood:"Bittersweet",color:"#708090",desc:"Soulful and reflective.",pattern:"T0K00L00K0T0L0K0"},
    { id:"bl-calabar",city:"Calabar Flow",bpm:105,note:"G4",mood:"Chill",color:"#00ced1",desc:"Smooth coastline travel.",pattern:"K0T00T0K0T00T0K0"},
    { id:"bl-jos",city:"Jos Plateau",bpm:100,note:"A4",mood:"Hopeful",color:"#DAA520",desc:"Cool highland hope.",pattern:"K00K0T0K00K0T0"},
    { id:"bl-owerri",city:"Owerri Heat",bpm:135,note:"D4",mood:"Energetic",color:"#FF7F50",desc:"Scorching fast beat.",pattern:"KK0K0T0KK0K0T0K0"},
    { id:"bl-kaduna",city:"Kaduna Dawn",bpm:88,note:"F4",mood:"Romantic",color:"#C08080",desc:"Tender dawn serenade.",pattern:"K0L00K0T0L00K0"},
    { id:"bl-aba",city:"Aba Market",bpm:118,note:"Bb3",mood:"Upbeat",color:"#32CD32",desc:"Busy market bounce.",pattern:"K0T0K0T0KT0K0T0K0"},
    { id:"bl-ilorin",city:"Ilorin Breeze",bpm:98,note:"Eb4",mood:"Chill",color:"#87CEEB",desc:"Light evening air.",pattern:"T0K0T0T0K0T0K0T0"},
    { id:"bl-maiduguri",city:"Maiduguri Sun",bpm:112,note:"C4",mood:"Empowering",color:"#FFBF00",desc:"Bold and resolute.",pattern:"K0T0K0TK0T0K0T0"},
    { id:"bl-akwa",city:"Akwa Ibom Tide",bpm:92,note:"G4",mood:"Melancholic",color:"#4B0082",desc:"Watery, introspective.",pattern:"K00T00K0T0K0T00"},
    { id:"bl-osogbo",city:"Osogbo Rain",bpm:86,note:"Ab3",mood:"Bittersweet",color:"#A9A9A9",desc:"Rain on the roof.",pattern:"K0L0K0T0K0L0T0K0"},
    { id:"bl-sokoto",city:"Sokoto Stars",bpm:94,note:"B4",mood:"Nostalgic",color:"#B87333",desc:"Night sky memory.",pattern:"K0T0K00T0K0T0K0"},
    { id:"bl-bayelsa",city:"Bayelsa River",bpm:102,note:"Db4",mood:"Romantic",color:"#00A86B",desc:"Slow river romance.",pattern:"K000T0K0T0K0K0"}
  ];

  var NOTE_FREQ = (function () {
    var map = {};
    var names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
    var flatMap = {"Db":"C#","Eb":"D#","Gb":"F#","Ab":"G#","Bb":"A#"};
    var A4 = 440, A4midi = 69;
    for (var midi = 0; midi < 128; midi++) {
      var oct = Math.floor(midi / 12) - 1;
      var nc = names[midi % 12];
      map[nc + oct] = A4 * Math.pow(2, (midi - A4midi) / 12);
    }
    function noteFreq(n) {
      if (map[n]) return map[n];
      if (flatMap[n]) return map[flatMap[n]];
      return 261.63;
    }
    return noteFreq;
  })();
  function bassFreq(note) { return NOTE_FREQ(note) / 2; }

  /* ── State ────────────────────────────────────────────────────────── */
  var MS = {
    state: null,
    recorder: null, recStream: null, chunks: [], timer: null, elapsed: 0,
    projects: [], rendered: false, previewPreset: null,
    ui: { openSection: "create", dockUpload: false, mobileSheet: false, mobileSection: "" },
    memory: []   // contextual music preferences / history
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
      savedAt:0, beatPreset:null,
      // extended for new sections
      lastAiEdit:null,
      tags:[]
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
    return p;
  }

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function fmtTime(s) { if (!s && s !== 0) return "00:00"; s = Math.round(s||0); var m = Math.floor(s/60); var ss = s%60; return (m<10?"0"+m:m)+":"+(ss<10?"0"+ss:ss); }
  function esc(s) { return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

  function loadProjects() {
    try { var p = JSON.parse(localStorage.getItem(STORE_KEY)||"[]"); MS.projects = (Array.isArray(p)?p:[]).map(normalizeProject); } catch(e) { MS.projects = []; }
  }
  function saveProjects() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(MS.projects)); } catch(e) {}
  }

  /* ── Cloud sync ───────────────────────────────────────────────────── */
  function pushProjectsToServer() {
    if (typeof apiFetch !== "function") return;
    apiFetch("/api/music/projects",{method:"POST",credentials:"include",
      headers:authHeaders({"Content-Type":"application/json"}),
      body:JSON.stringify({projects:MS.projects})}).catch(function(){});
  }
  function deleteProjectOnServer(id) {
    if (typeof apiFetch !== "function") return;
    apiFetch("/api/music/projects/"+encodeURIComponent(id),{method:"DELETE",credentials:"include",headers:authHeaders({})}).catch(function(){});
  }
  function fetchProjectsFromServer() {
    if (typeof apiFetch !== "function") return Promise.resolve();
    return apiFetch("/api/music/projects",{method:"GET",credentials:"include",headers:authHeaders({})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(!d||!Array.isArray(d.projects))return;
        var server=d.projects.map(normalizeProject);
        var map={}; MS.projects.forEach(function(p){map[p.id]=p;});
        server.forEach(function(p){var mine=map[p.id];if(!mine||(p.savedAt||0)>(mine.savedAt||0))map[p.id]=p;});
        MS.projects=Object.keys(map).map(function(k){return map[k];});
        saveProjects(); render();
      }).catch(function(){});
  }

  function loadMemory() {
    try { MS.memory = JSON.parse(localStorage.getItem(STORE_KEY+"_mem")||"[]"); } catch(e) { MS.memory = []; }
  }
  function saveMemory() {
    try { localStorage.setItem(STORE_KEY+"_mem", JSON.stringify(MS.memory)); } catch(e) {}
  }
  function addMemory(entry) {
    MS.memory.unshift(entry);
    if (MS.memory.length > 50) MS.memory.pop();
    saveMemory();
  }

  /* ── Toast ────────────────────────────────────────────────────────── */
  function toast(msg) {
    var el = document.getElementById("vmMusicToast");
    if (!el) return;
    el.textContent = msg; el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(function(){ el.classList.remove("show"); }, 2600);
  }

  function refreshLucide() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch(e) {}
    }
  }

  /* ── Solo/mute ────────────────────────────────────────────────────── */
  function trackMuted(t) {
    var soloed = (MS.state.take.solo||MS.state.beat.solo)||MS.state.layers.some(function(l){return l.solo;});
    if (soloed) return !t.solo;
    return t.mute;
  }

  /* ── Recording ────────────────────────────────────────────────────── */
  function toggleRecord() {
    if (MS.recorder && MS.recorder.state === "recording") { stopRecord(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast("Recording isn't supported in this browser."); return;
    }
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){
      MS.recStream = stream;
      var mime = (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported)
        ? (MediaRecorder.isTypeSupported("audio/webm")?"audio/webm":"") : "";
      MS.recorder = new MediaRecorder(stream, mime ? {mimeType:mime} : undefined);
      MS.chunks = [];
      MS.recorder.ondataavailable = function(ev){ if(ev.data&&ev.data.size)MS.chunks.push(ev.data); };
      MS.recorder.onstop = function(){
        var blob = new Blob(MS.chunks, {type:MS.recorder.mimeType||"audio/webm"});
        var take = MS.state.take;
        if(take.url)try{URL.revokeObjectURL(take.url);}catch(e){}
        take.url = URL.createObjectURL(blob);
        take.name = "My take "+fmtTime(Date.now()/1000).replace(":","")+" ("+fmtTime(MS.elapsed)+")";
        take.dur = MS.elapsed;
        stopRecTracks(); render();
        toast("Take recorded.");
      };
      MS.recorder.start(); MS.elapsed = 0;
      clearInterval(MS.timer);
      MS.timer = setInterval(function(){ MS.elapsed++; renderRecTime(); }, 1000);
      render();
    }).catch(function(){ toast("Microphone access was blocked."); });
  }
  function stopRecTracks() { try{if(MS.recStream)MS.recStream.getTracks().forEach(function(t){t.stop();});}catch(e){} MS.recStream=null; }
  function stopRecord() {
    if(MS.recorder&&MS.recorder.state==="recording"){try{MS.recorder.stop();}catch(e){}}
    clearInterval(MS.timer); MS.timer=null; MS.recorder=null; render();
  }
  function renderRecTime() { var n=document.getElementById("vmMusicRecTime");if(n)n.textContent=fmtTime(MS.elapsed); }

  /* ── Audio upload ─────────────────────────────────────────────────── */
  function loadAudioFields(track,f) {
    track.name=f.name; track.url=URL.createObjectURL(f); track.dur=0;
    var a=new Audio();a.preload="metadata";a.src=track.url;
    a.onloadedmetadata=function(){track.dur=a.duration||0;render();};
  }
  function onTakeFile(input) { var f=input&&input.files&&input.files[0];if(!f)return;var t=MS.state.take;if(t.url)try{URL.revokeObjectURL(t.url);}catch(e){}loadAudioFields(t,f);render();toast("Vocal added.");input.value=""; }
  function onBeatFile(input) { var f=input&&input.files&&input.files[0];if(!f)return;var t=MS.state.beat;if(t.url)try{URL.revokeObjectURL(t.url);}catch(e){}loadAudioFields(t,f);render();toast("Beat added.");input.value=""; }
  function onLayerFile(input) { var f=input&&input.files&&input.files[0];if(!f)return;addLayer().then(function(){var l=MS.state.layers[MS.state.layers.length-1];if(l&&l.url)try{URL.revokeObjectURL(l.url);}catch(e){}loadAudioFields(l,f);render();toast("Layer added.");});if(input)input.value=""; }

  function playTrack(kind) {
    var a=document.getElementById("vmMusicPlayer");if(!a)return;
    var url="",vol=0;
    if(kind==="beat"){var bt=MS.state.beat;if(trackMuted(bt)){toast("Beat is muted.");return;}url=bt.url;vol=(bt.vol/100)*(MS.state.mix.master/100);}
    else{var tk=MS.state.take;if(trackMuted(tk)){toast("Vocal is muted.");return;}url=tk.url;vol=(tk.vol/100)*(MS.state.mix.master/100);}
    if(!url){toast(kind==="beat"?"Add a beat first.":"Record or add vocals first.");return;}
    a.volume=Math.min(1,Math.max(0,vol||0));a.src=url;if(a.play)a.play();
  }
  function stopPlayback() { var a=document.getElementById("vmMusicPlayer");if(a){try{a.pause();}catch(e){}} }

  function addLayer() { MS.state.layers.push({id:"ly"+Date.now(),name:"Vocal layer "+(MS.state.layers.length+1),url:"",dur:0,vol:100,mute:false,solo:false});render();return Promise.resolve(); }
  function removeLayer(id) { MS.state.layers=MS.state.layers.filter(function(l){return l.id!==id;});render(); }
  function setTrack(kind,field,val) { var t=kind==="beat"?MS.state.beat:MS.state.take;t[field]=(field==="vol")?Number(val):!!val;render(); }
  function setLayer(id,field,val) { var l=MS.state.layers.filter(function(x){return x.id===id;})[0];if(!l)return;l[field]=(field==="vol")?Number(val):!!val;render(); }

  function syncInputs() {
    var get=function(id){var el=document.getElementById(id);return el?el.value:"";};
    MS.state.name=get("vmMusicName")||MS.state.name;
    MS.state.role=get("vmMusicRole")||"Singer";
    MS.state.genre=get("vmMusicGenre")||"Afrobeats";
    MS.state.mood=get("vmMusicMood")||"Romantic";
    MS.state.tempo=get("vmMusicTempo")||"Medium";
    MS.state.key=get("vmMusicKey")||"";
    MS.state.language=get("vmMusicLanguage")||"English";
    MS.state.brief=get("vmMusicBrief")||"";
    MS.state.lyrics=get("vmMusicLyrics")||"";
  }

  /* ── Effects ──────────────────────────────────────────────────────── */
  function setFx(field,val) { if(field==="noiseReduction")MS.state.fx.noiseReduction=!!val;else if(field==="effect")MS.state.fx.effect=val;else MS.state.fx[field]=Number(val);render(); }
  function applyEffectPreset(preset) { var fx=MS.state.fx;fx.noiseReduction=!!preset.fx.noiseReduction;fx.pitch=Number(preset.fx.pitch);fx.effect=preset.fx.effect;fx.reverb=Number(preset.fx.reverb);fx.delay=Number(preset.fx.delay);render();toast("Effect: "+preset.name); }
  function setMaster(val) { MS.state.mix.master=Number(val);render(); }
  function autoMix() { var b=MS.state.beat.vol;var v=MS.state.take.vol;if(b&&v){MS.state.beat.vol=Math.round(Math.min(90,Math.max(35,b*0.72)));MS.state.take.vol=100;}MS.state.autoMix=true;toast("Auto Mix applied.");render(); }
  function autoMaster() { MS.state.mix.master=100;MS.state.autoMaster=true;toast("Master levelled.");render(); }

  /* ── AI generate ──────────────────────────────────────────────────── */
  function runAI() {
    syncInputs();
    if(!MS.state.consent){toast("Authorize the voice choice first.");return;}
    if(!MS.state.brief.trim()&&!MS.state.lyrics.trim()){toast("Describe your song or add lyrics first.");return;}
    setSection("create");
    toast("Sending to ValleyMind...");
    apiFetch("/api/music",{method:"POST",credentials:"include",
      headers:authHeaders({"Content-Type":"application/json"}),
      body:JSON.stringify({brief:MS.state.brief,role:MS.state.role,genre:MS.state.genre,mood:MS.state.mood,tempo:MS.state.tempo,key:MS.state.key,language:MS.state.language,voice:MS.state.voice,lyrics:MS.state.lyrics}),
      timeoutMs:60000
    }).then(function(r){return r.json();}).then(function(d){
      MS.state.aiResult=d||null;
      if(d&&d.generated){addMemory({type:"generated",name:MS.state.name,genre:MS.state.genre,mood:MS.state.mood,date:Date.now()});}
      render();toast("Music package generated!");
    }).catch(function(){toast("Couldn't reach the producer.");});
  }

  /* ── AI edit (incremental refinement) ─────────────────────────────── */
  function runAiEdit() {
    syncInputs();
    var input=document.getElementById("msAiEditInput");
    var instruction=(input?input.value:"").trim();
    if(!instruction){toast("Type an instruction first.");return;}
    setSection("ai-edit");
    toast("Applying your changes...");
    apiFetch("/api/music/ai-edit",{method:"POST",credentials:"include",
      headers:authHeaders({"Content-Type":"application/json"}),
      body:JSON.stringify({
        instruction:instruction,
        lyrics:MS.state.lyrics||"",
        arrangement:(MS.state.aiResult&&MS.state.aiResult.arrangement)||"",
        genre:MS.state.genre,mood:MS.state.mood,tempo:MS.state.tempo,
        key:MS.state.key,name:MS.state.name
      }),
      timeoutMs:45000
    }).then(function(r){return r.json();}).then(function(d){
      if(d&&d.status==="success"&&d.changes){
        var ch=d.changes;
        if(ch.lyrics)MS.state.lyrics=ch.lyrics;
        if(ch.title)MS.state.name=ch.title;
        if(ch.arrangement&&MS.state.aiResult)MS.state.aiResult.arrangement=ch.arrangement;
        if(ch.genre)MS.state.genre=ch.genre;
        if(ch.mood)MS.state.mood=ch.mood;
        if(ch.tempo)MS.state.tempo=ch.tempo;
        if(ch.key)MS.state.key=ch.key;
        MS.state.lastAiEdit={instruction:instruction,summary:d.summary||"",date:Date.now()};
        addMemory({type:"ai-edit",instruction:instruction,summary:d.summary||"",date:Date.now()});
        render();toast(d.summary||"Changes applied!");
      } else {
        toast((d&&d.message)||"AI edit couldn't process that.");
      }
    }).catch(function(){toast("Couldn't reach the AI editor.");});
  }

  /* ── Beat synthesis (Web Audio) ───────────────────────────────────── */
  function beatPresetById(id){for(var i=0;i<BEAT_PRESETS.length;i++)if(BEAT_PRESETS[i].id===id)return BEAT_PRESETS[i];return null;}
  function renderBeatLoop(preset) {
    var Offline=window.OfflineAudioContext||window.webkitOfflineAudioContext;
    if(!Offline)return Promise.reject(new Error("no offline ctx"));
    var rate=44100,spb=60/preset.bpm,beats=12,duration=spb*beats;
    var ctx=new Offline(2,Math.ceil(rate*duration),rate);
    var root=bassFreq(preset.note);
    var steps=preset.pattern;
    for(var i=0;i<steps.length;i++){
      var ch=steps.charAt(i);var when=i*0.75*spb;
      for(var bar=0;bar<3;bar++){
        var w=when+bar*4*spb;
        if(ch==="K"){kick(ctx,w,0.95);bassHit(ctx,root,w,spb*0.8);}
        else if(ch==="L"){kick(ctx,w,0.6);}
        else if(ch==="T"){snare(ctx,w,0.75);}
        else if(ch==="0"){hat(ctx,w,0.12);}
      }
    }
    var totalSpb=ctx.duration/spb;
    for(var h=0;h<totalSpb;h++){if(h%2===1)hat(ctx,h*spb,0.06);}
    return ctx.startRendering().then(function(buffer){return encodeWav(buffer);});
  }
  function kick(ctx,when,vel){var o=ctx.createOscillator();var g=ctx.createGain();o.type="sine";o.frequency.setValueAtTime(160,when);o.frequency.exponentialRampToValueAtTime(48,when+0.1);g.gain.setValueAtTime(0,when);g.gain.linearRampToValueAtTime(0.9*vel,when+0.005);g.gain.exponentialRampToValueAtTime(0.001,when+0.18);o.connect(g);g.connect(ctx.destination);o.start(when);o.stop(when+0.2);}
  function bassHit(ctx,root,when,dur){var o=ctx.createOscillator();var g=ctx.createGain();o.type="sine";o.frequency.value=root;g.gain.setValueAtTime(0,when);g.gain.linearRampToValueAtTime(0.4,when+0.005);g.gain.setValueAtTime(0.4,when+dur*0.5);g.gain.exponentialRampToValueAtTime(0.001,when+dur);o.connect(g);g.connect(ctx.destination);o.start(when);o.stop(when+dur+0.02);}
  function snare(ctx,when,vel){var n=ctx.createBufferSource();var b=ctx.createBuffer(1,Math.floor(ctx.sampleRate*0.25),ctx.sampleRate);var d=b.getChannelData(0);for(var i=0;i<d.length;i++)d[i]=(Math.random()*2-1)*(1-i/d.length);n.buffer=b;var f=ctx.createBiquadFilter();f.type="bandpass";f.frequency.value=3000;f.Q.value=1;var g=ctx.createGain();g.gain.setValueAtTime(0,when);g.gain.linearRampToValueAtTime(0.6*vel,when+0.002);g.gain.exponentialRampToValueAtTime(0.001,when+0.2);n.connect(f);f.connect(g);g.connect(ctx.destination);n.start(when);n.stop(when+0.25);}
  function hat(ctx,when,vel){var n=ctx.createBufferSource();var b=ctx.createBuffer(1,Math.floor(ctx.sampleRate*0.05),ctx.sampleRate);var d=b.getChannelData(0);for(var i=0;i<d.length;i++)d[i]=(Math.random()*2-1)*(1-i/d.length);n.buffer=b;var f=ctx.createBiquadFilter();f.type="highpass";f.frequency.value=7000;var g=ctx.createGain();g.gain.setValueAtTime(0,when);g.gain.linearRampToValueAtTime(0.4*vel,when+0.001);g.gain.exponentialRampToValueAtTime(0.001,when+0.04);n.connect(f);f.connect(g);g.connect(ctx.destination);n.start(when);n.stop(when+0.05);}
  function encodeWav(buffer){var numCh=buffer.numberOfChannels;var len=buffer.length*numCh*2;var out=new ArrayBuffer(44+len);var v=new DataView(out);function wStr(o,s){for(var i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i));}wStr(0,"RIFF");v.setUint32(4,36+len,true);wStr(8,"WAVE");wStr(12,"fmt ");v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,numCh,true);v.setUint32(24,buffer.sampleRate,true);v.setUint32(28,buffer.sampleRate*numCh*2,true);v.setUint16(32,numCh*2,true);v.setUint16(34,16,true);wStr(36,"data");v.setUint32(40,len,true);var chans=[];for(var i=0;i<numCh;i++)chans.push(buffer.getChannelData(i));var off=44;for(var i=0;i<buffer.length;i++){for(var c=0;c<numCh;c++){var s=Math.max(-1,Math.min(1,chans[c][i]));v.setInt16(off,s<0?s*0x8000:s*0x7FFF,true);off+=2;}}return new Blob([v],{type:"audio/wav"});}
  function urlFromBlob(blob){try{return(window.URL||window.webkitURL).createObjectURL(blob);}catch(e){return"";}}
  function previewBeat(id){var preset=beatPresetById(id);if(!preset){toast("Beat not found.");return;}stopPreview();MS.previewPreset=id;render();renderBeatLoop(preset).then(function(blob){var url=urlFromBlob(blob);if(!url){toast("Beat preview not supported.");return;}var a=document.getElementById("vmMusicPlayer");if(a){a.src=url;a.volume=0.9;if(a.play)a.play();}}).catch(function(){toast("Beat preview needs a modern browser.");MS.previewPreset=null;render();});}
  function stopPreview(){MS.previewPreset=null;var a=document.getElementById("vmMusicPlayer");if(a){try{a.pause();}catch(e){}a.removeAttribute("src");}}
  function selectBeat(id){var preset=beatPresetById(id);if(!preset){toast("Beat not found.");return;}var old=MS.state.beat;if(old.url)try{URL.revokeObjectURL(old.url);}catch(e){}MS.previewPreset=null;toast("Building "+preset.city+"...");renderBeatLoop(preset).then(function(blob){var url=urlFromBlob(blob);if(!url){toast("Beat generation not supported.");return;}MS.state.beat.url=url;MS.state.beat.name=preset.city;MS.state.beat.dur=0;MS.state.beat.vol=100;MS.state.beat.mute=false;MS.state.beat.solo=false;MS.state.beatPreset=preset.id;var a=new Audio();a.preload="metadata";a.src=url;a.onloadedmetadata=function(){MS.state.beat.dur=a.duration||0;render();};render();toast(preset.city+" loaded.");}).catch(function(){toast("Beat generation needs a modern browser.");render();});}

  /* ── Save / load / delete / export ────────────────────────────────── */
  function saveSong() {
    syncInputs();MS.state.savedAt=Date.now();
    if(!MS.state.id)MS.state.id="ms"+Date.now();
    var found=false;
    for(var i=0;i<MS.projects.length;i++){if(MS.projects[i].id===MS.state.id){MS.projects[i]=normalizeProject(clone(MS.state));found=true;break;}}
    if(!found)MS.projects.unshift(normalizeProject(clone(MS.state)));
    saveProjects();pushProjectsToServer();toast("Song saved.");render();
  }
  function newSong(){MS.state=defaultState();MS.state.id=null;render();}
  function loadSong(id){for(var i=0;i<MS.projects.length;i++){if(MS.projects[i].id===MS.state.id){MS.state=normalizeProject(clone(MS.projects[i]));render();toast("Song loaded.");return;}}}
  function deleteSong(id){MS.projects=MS.projects.filter(function(p){return p.id!==id;});saveProjects();deleteProjectOnServer(id);render();}
  function exportSong() {
    syncInputs();var title=MS.state.name||"Untitled song";var parts=[];
    parts.push(title);
    parts.push("Genre: "+MS.state.genre+"  ·  Mood: "+MS.state.mood+"  ·  Tempo: "+MS.state.tempo+(MS.state.key?"  ·  Key: "+MS.state.key:""));
    if(MS.state.voice)parts.push("Voice: "+(VOICE_LABELS[MS.state.voice]||MS.state.voice));
    parts.push("");
    parts.push((MS.state.aiResult&&MS.state.aiResult.lyrics)||MS.state.lyrics||"(no lyrics yet)");
    if(MS.state.aiResult&&MS.state.aiResult.arrangement){parts.push("");parts.push("ARRANGEMENT");parts.push(MS.state.aiResult.arrangement);}
    var blob=new Blob([parts.join("\n")],{type:"text/plain;charset=utf-8"});
    var url=URL.createObjectURL(blob);var a=document.createElement("a");a.href=url;a.download=title.replace(/[\\/:*?"<>|]+/g,"_")+".txt";
    document.body.appendChild(a);a.click();document.body.removeChild(a);setTimeout(function(){URL.revokeObjectURL(url);},4000);
  }

  /* ── Section navigation ───────────────────────────────────────────── */
  function setSection(id){MS.ui.openSection=id;MS.ui.mobileSheet=(id!=="");render();}

  /* ── CSS injection ─────────────────────────────────────────────────── */
  function injectStyles() {
    if (document.getElementById("vmMusicCSS2")) return;
    var css = document.createElement("style");
    css.id = "vmMusicCSS2";
    css.textContent = [
      /* ── Layout: sidebar + workspace ── */
      ".ms-editor{display:flex;flex:1;min-height:0;overflow:hidden;background:transparent;}",
      ".ms-sidebar{width:280px;min-width:280px;display:flex;flex-direction:column;border-right:1px solid rgba(255,255,255,0.08);background:rgba(12,15,26,0.5);overflow-y:auto;-webkit-overflow-scrolling:touch;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,0.1) transparent;}",
      ".ms-sb-head{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid rgba(255,255,255,0.08);flex:none;}",
      ".ms-sb-logo{width:34px;height:34px;min-width:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#22d3ee,#0ea5e9);color:#03222b;font-weight:900;font-size:16px;}",
      ".ms-sb-title{flex:1;min-width:0;}",
      ".ms-sb-title h3{margin:0;font-family:'Space Grotesk',sans-serif;font-size:15px;color:#f1f5f9;white-space:nowrap;}",
      ".ms-sb-title p{margin:0;font-size:10.5px;color:#7c8aa0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",
      ".ms-sb-sections{flex:1;overflow-y:auto;padding:8px 0;}",
      ".ms-sb-sec{border-bottom:1px solid rgba(255,255,255,0.04);}",
      ".ms-sb-sec-head{display:flex;align-items:center;gap:10px;padding:11px 16px;cursor:pointer;transition:background .15s;user-select:none;}",
      ".ms-sb-sec-head:hover{background:rgba(255,255,255,0.04);}",
      ".ms-sb-sec-head .ms-sb-ic{width:30px;height:30px;min-width:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;background:rgba(34,211,238,0.1);color:#67e8f9;font-size:14px;}",
      ".ms-sb-sec-head .ms-sb-label{flex:1;min-width:0;}",
      ".ms-sb-sec-head .ms-sb-label b{display:block;font-family:'Space Grotesk',sans-serif;font-size:13px;color:#e2e8f0;}",
      ".ms-sb-sec-head .ms-sb-label small{display:block;font-size:10.5px;color:#7c8aa0;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}",
      ".ms-sb-sec-head .ms-sb-chev{color:#475569;font-size:12px;transition:transform .2s;flex:none;}",
      ".ms-sb-sec.open .ms-sb-sec-head{background:rgba(34,211,238,0.06);}",
      ".ms-sb-sec.open .ms-sb-sec-head .ms-sb-ic{background:linear-gradient(135deg,#0ea5e9,#22d3ee);color:#03222b;}",
      ".ms-sb-sec.open .ms-sb-sec-head .ms-sb-chev{transform:rotate(180deg);}",
      ".ms-sb-sec-body{display:none;padding:4px 16px 14px;}",
      ".ms-sb-sec.open .ms-sb-sec-body{display:block;}",
      /* ── Workspace ── */
      ".ms-workspace{flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden;}",
      ".ms-ws-visual{flex:1;min-height:0;display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(180deg,rgba(15,19,32,0.9),rgba(12,15,26,0.96));position:relative;overflow:hidden;}",
      ".ms-ws-artwork{width:min(280px,60vw);height:min(280px,60vw);border-radius:16px;background:linear-gradient(135deg,rgba(34,211,238,0.15),rgba(14,165,233,0.08));border:1px solid rgba(255,255,255,0.08);display:flex;align-items:center;justify-content:center;position:relative;}",
      ".ms-ws-artwork .ms-ws-icon{font-size:48px;color:rgba(34,211,238,0.35);}",
      ".ms-ws-title-overlay{position:absolute;bottom:12px;left:12px;right:12px;text-align:center;}",
      ".ms-ws-title-overlay h2{margin:0;font-family:'Space Grotesk',sans-serif;font-size:16px;color:#f1f5f9;text-shadow:0 2px 8px rgba(0,0,0,0.6);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}",
      ".ms-ws-title-overlay p{margin:2px 0 0;font-size:11px;color:#94a3b8;}",
      /* ── Player ── */
      ".ms-ws-player{flex:none;padding:12px 20px;background:rgba(10,13,22,0.85);border-top:1px solid rgba(255,255,255,0.08);backdrop-filter:blur(10px);}",
      ".ms-ws-controls{display:flex;align-items:center;gap:14px;}",
      ".ms-ws-controls .ms-play{width:44px;height:44px;min-width:44px;border-radius:50%;border:none;background:linear-gradient(135deg,#22d3ee,#0ea5e9);color:#03222b;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:filter .15s;}",
      ".ms-ws-controls .ms-play:hover{filter:brightness(1.1);}",
      ".ms-ws-controls .ms-play[disabled]{opacity:.4;cursor:not-allowed;}",
      ".ms-ws-seek{flex:1;display:flex;flex-direction:column;gap:4px;min-width:0;}",
      ".ms-ws-seek input[type=range]{width:100%;-webkit-appearance:none;height:4px;border-radius:999px;background:rgba(255,255,255,0.12);outline:none;cursor:pointer;}",
      ".ms-ws-seek input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:#22d3ee;cursor:pointer;}",
      ".ms-ws-time{display:flex;justify-content:space-between;font-size:11px;color:#7c8aa0;font-family:'Space Grotesk',sans-serif;}",
      ".ms-ws-vol{display:flex;align-items:center;gap:6px;flex:none;}",
      ".ms-ws-vol input[type=range]{width:80px;-webkit-appearance:none;height:4px;border-radius:999px;background:rgba(255,255,255,0.12);outline:none;}",
      ".ms-ws-vol input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:#22d3ee;}",
      /* ── Timeline tracks ── */
      ".ms-ws-tracks{flex:none;max-height:180px;overflow-y:auto;border-top:1px solid rgba(255,255,255,0.06);padding:8px 16px;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,0.1) transparent;}",
      ".ms-ws-trk{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:8px;margin-bottom:4px;background:rgba(255,255,255,0.02);transition:background .15s;}",
      ".ms-ws-trk:hover{background:rgba(255,255,255,0.05);}",
      ".ms-ws-trk .ms-trk-ic{width:28px;height:28px;min-width:28px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:12px;}",
      ".ms-ws-trk .ms-trk-info{flex:1;min-width:0;}",
      ".ms-ws-trk .ms-trk-name{font-size:12px;font-weight:700;color:#e2e8f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}",
      ".ms-ws-trk .ms-trk-meta{font-size:10px;color:#7c8aa0;}",
      ".ms-ws-trk .ms-trk-bar{flex:1;max-width:200px;height:6px;border-radius:999px;background:rgba(255,255,255,0.08);overflow:hidden;position:relative;}",
      ".ms-ws-trk .ms-trk-bar-fill{height:100%;border-radius:999px;transition:width .3s;}",
      ".ms-ws-trk .ms-trk-ctrl{display:flex;gap:4px;}",
      ".ms-ws-trk .ms-trk-btn{width:26px;height:26px;border-radius:6px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#94a3b8;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:800;transition:all .15s;}",
      ".ms-ws-trk .ms-trk-btn:hover{background:rgba(255,255,255,0.08);color:#e2e8f0;}",
      ".ms-ws-trk .ms-trk-btn.on{color:#22d3ee;border-color:rgba(34,211,238,0.4);}",
      ".ms-ws-trk .ms-trk-btn.muted{color:#fda4af;border-color:rgba(244,63,94,0.4);}",
      /* ── Sidebar form controls ── */
      ".ms-sb-label{display:block;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#94a3b8;margin:10px 0 5px;}",
      ".ms-sb-select,.ms-sb-input{width:100%;background:#0d1220;border:1px solid rgba(255,255,255,0.1);color:#e6edf5;border-radius:10px;padding:10px 12px;font-size:13px;font-family:inherit;outline:none;min-height:40px;}",
      ".ms-sb-select:focus,.ms-sb-input:focus{border-color:rgba(34,211,238,0.5);}",
      ".ms-sb-textarea{width:100%;background:#0d1220;border:1px solid rgba(255,255,255,0.1);color:#e6edf5;border-radius:10px;padding:10px 12px;font-size:13px;font-family:inherit;min-height:100px;resize:vertical;outline:none;line-height:1.5;}",
      ".ms-sb-btn{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:12px;border:1px solid rgba(255,255,255,0.12);background:rgba(255,255,255,0.05);color:#e2e8f0;border-radius:10px;padding:9px 12px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;transition:all .15s;min-height:38px;width:100%;}",
      ".ms-sb-btn:hover{background:rgba(255,255,255,0.1);border-color:rgba(34,211,238,0.35);color:#fff;}",
      ".ms-sb-btn:active{transform:translateY(1px);}",
      ".ms-sb-btn.primary{background:linear-gradient(135deg,#22d3ee,#0ea5e9);color:#03222b;border:none;}",
      ".ms-sb-btn.primary:hover{filter:brightness(1.06);}",
      ".ms-sb-btn:disabled{opacity:.4;cursor:not-allowed;pointer-events:none;}",
      ".ms-sb-btn.danger{color:#fda4af;border-color:rgba(244,63,94,0.3);}",
      ".ms-sb-btn.sm{min-height:32px;padding:6px 10px;font-size:11px;}",
      ".ms-sb-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}",
      ".ms-sb-stack{display:flex;flex-direction:column;gap:8px;}",
      /* ── Chips (effects) ── */
      ".ms-chips{display:flex;flex-wrap:wrap;gap:7px;margin:6px 0 10px;}",
      ".ms-chip{position:relative;display:inline-flex;align-items:center;gap:6px;min-height:38px;padding:0 12px;border-radius:10px;border:1px solid rgba(255,255,255,0.12);background:rgba(255,255,255,0.04);color:#cbd5e1;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:11.5px;cursor:pointer;transition:all .15s;}",
      ".ms-chip:hover{transform:translateY(-1px);}",
      ".ms-chip.active{color:#fff;border-color:var(--cc);box-shadow:0 0 0 1px var(--cc),0 4px 12px -4px var(--cc);}",
      /* ── Slider row ── */
      ".ms-slider-row{display:flex;align-items:center;gap:10px;padding:7px 0;}",
      ".ms-slider-row label{flex:1;min-width:0;font-size:12px;color:#cbd5e1;}",
      ".ms-slider-row output{font-size:11px;color:#67e8f9;min-width:30px;text-align:right;font-weight:700;}",
      ".ms-range{-webkit-appearance:none;appearance:none;width:140px;height:5px;border-radius:999px;background:rgba(255,255,255,0.12);outline:none;cursor:pointer;}",
      ".ms-range::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:#22d3ee;border:2px solid #0b2730;box-shadow:0 2px 6px rgba(34,211,238,.35);}",
      ".ms-range::-moz-range-thumb{width:18px;height:18px;border-radius:50%;background:#22d3ee;border:2px solid #0b2730;}",
      /* ── Switch ── */
      ".ms-switch{display:inline-flex;align-items:center;cursor:pointer;}",
      ".ms-switch input{display:none;}",
      ".ms-switch .sw{width:40px;height:22px;border-radius:999px;background:rgba(255,255,255,0.14);position:relative;transition:background .2s;}",
      ".ms-switch .sw::after{content:'';position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:transform .2s;}",
      ".ms-switch input:checked+.sw{background:#22d3ee;}",
      ".ms-switch input:checked+.sw::after{transform:translateX(18px);}",
      /* ── Option cards ── */
      ".ms-option{display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border:1px solid rgba(255,255,255,0.08);border-radius:10px;margin-bottom:6px;cursor:pointer;background:rgba(255,255,255,0.02);transition:all .15s;}",
      ".ms-option.sel{border-color:rgba(34,211,238,0.5);background:rgba(34,211,238,0.06);}",
      ".ms-option input[type=radio]{accent-color:#22d3ee;margin-top:3px;width:16px;height:16px;flex:none;}",
      ".ms-option .o-title{font-size:12.5px;font-weight:700;color:#f1f5f9;}",
      ".ms-option .o-sub{font-size:10.5px;color:#8a97ad;margin-top:1px;line-height:1.4;}",
      ".ms-consent{display:flex;align-items:flex-start;gap:10px;background:rgba(255,193,7,0.06);border:1px dashed rgba(255,193,7,0.35);border-radius:10px;padding:10px 12px;margin-top:6px;}",
      ".ms-consent input{accent-color:#f59e0b;margin-top:3px;width:16px;height:16px;flex:none;}",
      ".ms-consent label{font-size:11.5px;color:#fcd34d;line-height:1.5;}",
      /* ── Recording ── */
      ".ms-rec{display:flex;align-items:center;gap:12px;padding:4px 0;}",
      ".ms-rec-btn{width:52px;height:52px;min-width:52px;border-radius:50%;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff;box-shadow:0 6px 18px rgba(239,68,68,.35);transition:all .15s;}",
      ".ms-rec-btn.recording{animation:msPulse 1.2s infinite;}",
      ".ms-rec-btn.green{background:linear-gradient(135deg,#22c55e,#16a34a);box-shadow:0 6px 18px rgba(34,197,94,.3);}",
      "@keyframes msPulse{0%,100%{transform:scale(1);box-shadow:0 4px 14px rgba(239,68,68,.35);}50%{transform:scale(1.05);box-shadow:0 6px 22px rgba(239,68,68,.5);}}",
      ".ms-rec-time{font-family:'Space Grotesk',sans-serif;font-size:22px;font-weight:800;color:#f1f5f9;min-width:56px;}",
      ".ms-rec-meta{flex:1;min-width:0;}",
      ".ms-rec-meta b{display:block;font-size:12px;color:#e6edf5;}",
      ".ms-rec-meta small{font-size:10.5px;color:#8a97ad;}",
      /* ── Beat cards ── */
      ".ms-beatlib{display:grid;grid-template-columns:1fr;gap:8px;margin-top:4px;max-height:300px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,0.1) transparent;}",
      ".ms-beat{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:10px;overflow:hidden;transition:all .15s;}",
      ".ms-beat .ms-beat-top{height:4px;width:100%;}",
      ".ms-beat .ms-beat-body{padding:10px 11px 11px;}",
      ".ms-beat.selected{border-color:rgba(34,211,238,0.4);box-shadow:0 0 0 1px rgba(34,211,238,0.3);}",
      ".ms-beat-name{font-family:'Space Grotesk',sans-serif;font-size:12.5px;font-weight:800;color:#f1f5f9;}",
      ".ms-beat-meta{font-size:10px;color:#8a97ad;margin-top:1px;}",
      ".ms-beat-desc{font-size:10.5px;color:#9fb0c4;line-height:1.4;margin:4px 0 7px;}",
      ".ms-beat-actions{display:flex;gap:6px;}",
      ".ms-beat-actions .ms-sb-btn{flex:1;}",
      /* ── Project cards ── */
      ".ms-proj{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:10px 12px;margin-bottom:6px;}",
      ".ms-proj .p-icon{width:30px;height:30px;min-width:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;background:rgba(34,211,238,0.1);color:#67e8f9;font-size:12px;}",
      ".ms-proj .p-main{flex:1;min-width:0;}",
      ".ms-proj .p-name{font-size:12.5px;font-weight:700;color:#e6edf5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}",
      ".ms-proj .p-meta{font-size:10px;color:#7c8aa0;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}",
      /* ── Memory card ── */
      ".ms-mem{padding:8px 10px;border-radius:8px;border:1px solid rgba(255,255,255,0.06);margin-bottom:6px;background:rgba(255,255,255,0.02);}",
      ".ms-mem .mem-type{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;color:#67e8f9;}",
      ".ms-mem .mem-text{font-size:11.5px;color:#cbd5e1;margin-top:3px;line-height:1.4;}",
      ".ms-mem .mem-date{font-size:10px;color:#64748b;margin-top:3px;}",
      /* ── Info / note ── */
      ".ms-info{font-size:11.5px;color:#94a3b8;background:rgba(255,255,255,0.03);border-left:3px solid #475569;border-radius:6px;padding:8px 10px;margin-top:8px;line-height:1.5;}",
      ".ms-note{font-size:11.5px;color:#ffd166;background:rgba(255,193,7,0.06);border-left:3px solid #f59e0b;border-radius:6px;padding:8px 10px;margin-top:8px;line-height:1.5;}",
      ".ms-empty{text-align:center;color:#64748b;padding:16px 8px;font-size:12px;}",
      ".ms-divider{height:1px;background:rgba(255,255,255,0.06);margin:10px 0;}",
      /* ── Toast ── */
      ".ms-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#0f172a;border:1px solid rgba(34,211,238,0.35);color:#e6edf5;padding:10px 16px;border-radius:10px;font-size:12.5px;font-weight:600;z-index:99999;box-shadow:0 10px 30px rgba(0,0,0,.5);opacity:0;transition:opacity .25s,transform .25s;pointer-events:none;max-width:min(90vw,380px);text-align:center;}",
      ".ms-toast.show{opacity:1;transform:translateX(-50%) translateY(-3px);}",
      /* ── AI output ── */
      ".ms-ai-out{border:1px solid rgba(34,211,238,0.3);border-radius:12px;padding:14px;margin-top:10px;background:rgba(34,211,238,0.04);}",
      ".ms-ai-out h4{font-family:'Space Grotesk',sans-serif;margin:10px 0 4px;color:#67e8f9;font-size:11px;text-transform:uppercase;letter-spacing:.06em;}",
      ".ms-ai-out .lyrics{white-space:pre-wrap;font-size:12.5px;line-height:1.7;color:#e6edf5;background:rgba(13,18,32,0.5);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:12px;}",
      /* ── Mobile bottom sheet ── */
      ".ms-mobile-tabs{display:none;flex:none;gap:2px;padding:6px 8px calc(6px + env(safe-area-inset-bottom,0px));background:rgba(10,13,22,0.92);border-top:1px solid rgba(255,255,255,0.08);backdrop-filter:blur(14px);overflow-x:auto;scrollbar-width:none;}",
      ".ms-mobile-tabs::-webkit-scrollbar{display:none;}",
      ".ms-mobile-tabs .ms-mtab{flex:none;display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 10px;border:none;background:transparent;color:#94a3b8;font-size:9.5px;font-weight:700;border-radius:8px;cursor:pointer;white-space:nowrap;min-width:56px;transition:all .15s;}",
      ".ms-mobile-tabs .ms-mtab.active{color:#22d3ee;background:rgba(34,211,238,0.1);}",
      ".ms-mobile-tabs .ms-mtab i{width:18px;height:18px;}",
      ".ms-sheet-backdrop{position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:40;display:none;}",
      ".ms-sheet-backdrop.show{display:block;}",
      ".ms-sheet{position:fixed;left:0;right:0;bottom:0;max-height:70vh;background:#0c0f1a;border-top:1px solid rgba(255,255,255,0.1);border-radius:16px 16px 0 0;z-index:41;overflow-y:auto;transform:translateY(100%);transition:transform .3s ease;padding-bottom:env(safe-area-inset-bottom,0px);}",
      ".ms-sheet.show{transform:translateY(0);}",
      ".ms-sheet-head{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid rgba(255,255,255,0.08);position:sticky;top:0;background:#0c0f1a;z-index:1;}",
      ".ms-sheet-head h4{margin:0;font-family:'Space Grotesk',sans-serif;font-size:14px;color:#f1f5f9;flex:1;}",
      ".ms-sheet-close{width:32px;height:32px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#94a3b8;cursor:pointer;display:flex;align-items:center;justify-content:center;}",
      ".ms-sheet-body{padding:12px 16px 16px;}",
      /* ── Responsive ── */
      "@media (max-width:900px){",
      "  .ms-sidebar{display:none;}",
      "  .ms-mobile-tabs{display:flex;}",
      "}",
      "@media (min-width:901px){",
      "  .ms-mobile-tabs{display:none!important;}",
      "  .ms-sheet,.ms-sheet-backdrop{display:none!important;}",
      "}",
      "@media (max-width:600px){",
      "  .ms-ws-artwork{width:min(200px,70vw);height:min(200px,70vw);}",
      "  .ms-ws-controls{gap:10px;}",
      "  .ms-ws-vol input[type=range]{width:60px;}",
      "  .ms-ws-tracks{max-height:120px;}",
      "}"
    ].join("\n");
    document.head.appendChild(css);
  }

  /* ── Sidebar section renderers ─────────────────────────────────────── */
  function sbSection(id, icon, label, sub, body) {
    var open = MS.ui.openSection === id;
    return '<div class="ms-sb-sec'+(open?" open":"")+'" data-sid="'+id+'">'+
      '<div class="ms-sb-sec-head" onclick="window.vmMusicAPI.setSection(\''+id+'\')">'+
        '<span class="ms-sb-ic"><i data-lucide="'+icon+'"></i></span>'+
        '<span class="ms-sb-label"><b>'+label+'</b><small>'+sub+'</small></span>'+
        '<span class="ms-sb-chev"><i data-lucide="chevron-down"></i></span>'+
      '</div>'+
      '<div class="ms-sb-sec-body">'+body+'</div>'+
    '</div>';
  }

  function sel(id,label,opts,val) {
    var o=opts.map(function(x){return '<option value="'+x+'"'+(val===x?" selected":"")+">"+x+"</option>";}).join("");
    return '<div><label class="ms-sb-label">'+label+'</label><select class="ms-sb-select" id="'+id+'">'+o+'</select></div>';
  }

  function sliderRow(label,id,val,min,max,unit,oninput) {
    return '<div class="ms-slider-row"><label>'+label+'</label>'+
      '<input type="range" class="ms-range" id="'+id+'" min="'+min+'" max="'+max+'" value="'+val+'" oninput="window.vmMusicAPI.'+oninput+'">'+
      '<output>'+val+unit+'</output></div>';
  }

  function renderCreateSection() {
    var s=MS.state;
    var genreOpts=GENRES.map(function(g){return '<option value="'+g+'"'+(s.genre===g?" selected":"")+">"+g+"</option>";}).join("");
    var moodOpts=MOODS.map(function(m){return '<option value="'+m+'"'+(s.mood===m?" selected":"")+">"+m+"</option>";}).join("");
    var tempoOpts=TEMPOS.map(function(t){return '<option value="'+t+'"'+(s.tempo===t?" selected":"")+">"+t+"</option>";}).join("");
    var roleOpts=ROLES.map(function(r){return '<option value="'+r+'"'+(s.role===r?" selected":"")+">"+r+"</option>";}).join("");
    return '<label class="ms-sb-label">Describe your song</label>'+
      '<textarea class="ms-sb-textarea" id="vmMusicBrief" placeholder="e.g. Create a smooth Nigerian Afrobeats love song about falling in love.">'+esc(s.brief)+'</textarea>'+
      '<div class="ms-sb-stack" style="margin-top:8px;">'+
        sel("vmMusicGenre","Genre",GENRES,s.genre)+
        sel("vmMusicMood","Mood",MOODS,s.mood)+
        sel("vmMusicTempo","Tempo",TEMPOS,s.tempo)+
        sel("vmMusicRole","Your role",ROLES,s.role)+
        '<div><label class="ms-sb-label">Key (optional)</label><input class="ms-sb-input" id="vmMusicKey" value="'+esc(s.key)+'" placeholder="e.g. A minor"></div>'+
        '<div><label class="ms-sb-label">Language</label><input class="ms-sb-input" id="vmMusicLanguage" value="'+esc(s.language)+'" placeholder="English"></div>'+
      '</div>'+
      '<button class="ms-sb-btn primary" onclick="window.vmMusicAPI.runAI()" style="margin-top:10px;">Generate Music</button>'+
      (s.aiResult?'<div class="ms-ai-out" style="margin-top:10px;">'+
        (s.aiResult.title?'<div style="font-size:12px;color:#67e8f9;font-weight:700;">'+esc(s.aiResult.title)+'</div>':'')+
        (s.aiResult.structure?'<div style="font-size:11px;color:#94a3b8;margin-top:2px;">'+esc(s.aiResult.structure)+'</div>':'')+
        (s.aiResult.lyrics?'<h4>Lyrics</h4><div class="lyrics">'+esc(s.aiResult.lyrics)+'</div>':'')+
        (s.aiResult.arrangement?'<h4>Arrangement</h4><p style="font-size:11.5px;color:#cbd5e1;line-height:1.5;">'+esc(s.aiResult.arrangement)+'</p>':'')+
        (s.aiResult.note?'<div class="ms-note">'+esc(s.aiResult.note)+'</div>':'')+
      '</div>':'');
  }

  function renderVoiceSection() {
    var s=MS.state;
    var rec=MS.recorder&&MS.recorder.state==="recording";
    var voiceOpt=function(v){
      var on=s.voice===v;
      var locked=(v==="clone"&&!s.consent)?" disabled":"";
      return '<div class="ms-option'+(on?" sel":"")+'" onclick="window.vmMusicAPI.onVoice(\''+v+'\')">'+
        '<input type="radio" name="msVoice" value="'+v+'"'+(on?" checked":"")+locked+'>',
        '<div><div class="o-title">'+VOICE_LABELS[v]+'</div><div class="o-sub">'+(VOICE_SUBS[v]||"")+'</div></div></div>';
    };
    var consentBlock=s.voice==="clone"?'<div class="ms-consent" onclick="window.vmMusicAPI.onConsent()"><input type="checkbox"'+(s.consent?" checked":"")+"><label><b>Authorize voice cloning:</b> I give ValleyMind permission to create an AI model of my voice, stored only for my projects.</label></div>":"";
    return '<div class="ms-rec">'+
      '<button class="ms-rec-btn'+(rec?" recording":" green")+'" onclick="window.vmMusicAPI.toggleRecord()"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4"/></svg></button>'+
      '<div class="ms-rec-meta"><b>'+(rec?"Recording... tap to stop":"Record your voice")+'</b><small>'+(rec?"Like a voice note":"Sing, hum, or speak")+'</small></div>'+
      '<span class="ms-rec-time" id="vmMusicRecTime">'+fmtTime(MS.elapsed)+'</span>'+
    '</div>'+
    '<div class="ms-sb-row" style="margin-top:8px;">'+
      '<label class="ms-sb-btn sm" style="cursor:pointer;flex:1;"><input type="file" accept="audio/*" id="vmMusicTakeInput" style="display:none;">Upload vocal</label>'+
      '<label class="ms-sb-btn sm" style="cursor:pointer;flex:1;"><input type="file" accept="audio/*" id="vmMusicBeatInput" style="display:none;">Upload beat</label>'+
    '</div>'+
    '<div class="ms-divider"></div>'+
    '<label class="ms-sb-label">Voice</label>'+
    voiceOpt("keep")+voiceOpt("clone")+voiceOpt("elena")+consentBlock;
  }

  function renderMusicSection() {
    var s=MS.state;
    var beatCards=BEAT_PRESETS.map(function(p){
      var selected=s.beatPreset===p.id;
      var previewing=MS.previewPreset===p.id;
      return '<div class="ms-beat'+(selected?" selected":"")+'">'+
        '<div class="ms-beat-top" style="background:'+p.color+';"></div>'+
        '<div class="ms-beat-body">'+
          '<div class="ms-beat-name">'+esc(p.city)+'</div>'+
          '<div class="ms-beat-meta">'+p.bpm+' BPM · '+esc(p.note)+' · '+esc(p.mood)+'</div>'+
          '<div class="ms-beat-desc">'+esc(p.desc)+'</div>'+
          '<div class="ms-beat-actions">'+
            '<button class="ms-sb-btn sm" onclick="window.vmMusicAPI.previewBeat(\''+p.id+'\')">'+(previewing?"Playing":"Preview")+'</button>'+
            '<button class="ms-sb-btn sm'+(selected?" primary":"")+'" onclick="window.vmMusicAPI.selectBeat(\''+p.id+'\')">'+(selected?"Loaded":"Use")+'</button>'+
          '</div>'+
        '</div></div>';
    }).join("");
    return '<div class="ms-info">Tap a beat to preview, then Use to load it. Beats are synthesised in your browser.</div>'+
      '<div class="ms-beatlib">'+beatCards+'</div>'+
      '<div class="ms-divider"></div>'+
      '<div class="ms-sb-row">'+
        '<label class="ms-sb-btn sm" style="cursor:pointer;flex:1;"><input type="file" accept="audio/*" id="vmMusicBeatInput2" style="display:none;">Upload beat</label>'+
        '<label class="ms-sb-btn sm" style="cursor:pointer;flex:1;"><input type="file" accept="audio/*" id="vmMusicLayerInput" style="display:none;">Add layer</label>'+
      '</div>';
  }

  function renderInstrumentsSection() {
    return '<div class="ms-info">Tell the AI what instruments to use in your arrangement. This feeds into the next generation or edit.</div>'+
      '<label class="ms-sb-label">Instrumentation instruction</label>'+
      '<textarea class="ms-sb-textarea" id="msInstrumentInput" placeholder="e.g. Add soft guitar and deeper bass to the backing track. Include light percussion." style="min-height:80px;"></textarea>'+
      '<button class="ms-sb-btn primary" onclick="window.vmMusicAPI.aiInstrument()" style="margin-top:8px;">Apply to arrangement</button>'+
      '<div class="ms-info" style="margin-top:8px;">The AI interprets natural-language requests about instrumentation and updates the arrangement spec.</div>';
  }

  function renderLyricsSection() {
    var s=MS.state;
    return '<label class="ms-sb-label">Lyrics</label>'+
      '<textarea class="ms-sb-textarea" id="vmMusicLyrics" placeholder="Write your lyrics here..." style="min-height:150px;">'+esc(s.lyrics)+'</textarea>'+
      '<div class="ms-sb-stack" style="margin-top:8px;">'+
        '<button class="ms-sb-btn primary" onclick="window.vmMusicAPI.aiLyrics()">AI Generate Lyrics</button>'+
        '<button class="ms-sb-btn" onclick="window.vmMusicAPI.aiRewriteLyrics()">AI Rewrite</button>'+
        '<button class="ms-sb-btn" onclick="window.vmMusicAPI.exportSong()">Export lyric sheet</button>'+
      '</div>';
  }

  function renderEffectsSection() {
    var s=MS.state;
    var chips=EFFECT_PRESETS.map(function(p){
      var active=s.fx.effect===p.fx.effect&&s.fx.pitch===p.fx.pitch&&s.fx.reverb===p.fx.reverb&&s.fx.delay===p.fx.delay&&s.fx.noiseReduction===!!p.fx.noiseReduction;
      return '<button type="button" class="ms-chip'+(active?" active":"")+'" style="--cc:'+p.color+'" onclick="window.vmMusicAPI.applyEffect(\''+p.name+'\')"><span>'+p.name+'</span></button>';
    }).join("");
    return '<div class="ms-chips">'+chips+'</div>'+
      '<div class="ms-slider-row"><label>Noise reduction</label>'+
        '<label class="ms-switch"><input type="checkbox"'+(s.fx.noiseReduction?" checked":"")+' onchange="window.vmMusicAPI.setFx(\'noiseReduction\',this.checked)"><span class="sw"></span></label></div>'+
      sliderRow("Pitch (cents)","msPitch",s.fx.pitch,-50,50,"","setFx('pitch',document.getElementById('msPitch').value)")+
      sliderRow("Reverb","msReverb",s.fx.reverb,0,100,"","setFx('reverb',document.getElementById('msReverb').value)")+
      sliderRow("Delay","msDelay",s.fx.delay,0,100,"","setFx('delay',document.getElementById('msDelay').value)")+
      '<div class="ms-note">These controls set vocal effect intent and persist with the project. DSP rendering is a future engine step.</div>';
  }

  function renderMixSection() {
    var s=MS.state;
    var rows="";
    if(s.take.url)rows+=sliderRow("Vocal","msTakeVol",s.take.vol,0,100,"","setTrack('take','vol',document.getElementById('msTakeVol').value)");
    if(s.beat.url)rows+=sliderRow("Beat","msBeatVol",s.beat.vol,0,100,"","setTrack('beat','vol',document.getElementById('msBeatVol').value)");
    return '<div class="ms-info">Mix balances your tracks with real gain. Auto Master normalises levels.</div>'+
      sliderRow("Master","msMaster",s.mix.master,0,100,"","setMaster(document.getElementById('msMaster').value)")+
      rows+
      '<div class="ms-sb-stack" style="margin-top:8px;">'+
        '<button class="ms-sb-btn" onclick="window.vmMusicAPI.autoMix()">Auto Mix</button>'+
        '<button class="ms-sb-btn" onclick="window.vmMusicAPI.autoMaster()">Auto Master</button>'+
        '<button class="ms-sb-btn" onclick="window.vmMusicAPI.exportSong()">Export</button>'+
      '</div>';
  }

  function renderAiEditSection() {
    var s=MS.state;
    var lastEdit=s.lastAiEdit;
    return '<label class="ms-sb-label">What do you want AI to change?</label>'+
      '<textarea class="ms-sb-textarea" id="msAiEditInput" placeholder="e.g. Make the chorus more energetic. Add drums. Give the ending a cinematic fade." style="min-height:100px;"></textarea>'+
      '<button class="ms-sb-btn primary" onclick="window.vmMusicAPI.runAiEdit()" style="margin-top:8px;">Apply AI Changes</button>'+
      (lastEdit?'<div class="ms-info" style="margin-top:8px;"><b style="color:#67e8f9;">Last edit:</b> '+esc(lastEdit.instruction)+(lastEdit.summary?' — '+esc(lastEdit.summary):"")+'</div>':'')+
      '<div class="ms-info" style="margin-top:8px;">AI edits are incremental — they modify the existing project without regenerating everything.</div>';
  }

  function renderProjectsSection() {
    var projRows=MS.projects.map(function(p){
      var d=p.savedAt?new Date(p.savedAt).toLocaleString():"";
      return '<div class="ms-proj">'+
        '<span class="p-icon"><i data-lucide="'+(p.mode==="ai"?"sparkles":"music")+'"></i></span>'+
        '<div class="p-main"><div class="p-name">'+esc(p.name||"Untitled")+'</div><div class="p-meta">'+esc(p.genre||"")+" · "+esc(p.mood||"")+(d?" · "+d:"")+'</div></div>'+
        '<button class="ms-sb-btn sm primary" onclick="window.vmMusicAPI.loadSong(\''+p.id+'\')">Open</button>'+
        '<button class="ms-sb-btn sm danger" onclick="window.vmMusicAPI.deleteSong(\''+p.id+'\')">&#215;</button>'+
      '</div>';
    }).join("");
    return '<button class="ms-sb-btn" onclick="window.vmMusicAPI.newSong()" style="margin-bottom:10px;">+ New Project</button>'+
      (projRows||'<div class="ms-empty">No saved songs yet.</div>')+
      '<button class="ms-sb-btn" onclick="window.vmMusicAPI.saveSong()" style="margin-top:8px;">Save Current</button>';
  }

  function renderMemorySection() {
    var memHtml=MS.memory.map(function(m){
      var date=m.date?new Date(m.date).toLocaleString():"";
      var typeLabel=m.type==="generated"?"Music Generated":m.type==="ai-edit"?"AI Edit":"Preference";
      return '<div class="ms-mem">'+
        '<div class="mem-type">'+typeLabel+'</div>'+
        '<div class="mem-text">'+esc(m.name||m.instruction||m.summary||"")+'</div>'+
        '<div class="mem-date">'+date+'</div>'+
      '</div>';
    }).join("");
    return '<div class="ms-info">Music memory stores context about your creations — not the projects themselves.</div>'+
      (memHtml||'<div class="ms-empty">No memory entries yet. Generate or edit music to build context.</div>');
  }

  function renderAssetsSection() {
    var assets=[];
    if(MS.state.take.url)assets.push({name:MS.state.take.name||"Vocal take",type:"Vocal",dur:MS.state.take.dur});
    if(MS.state.beat.url)assets.push({name:MS.state.beat.name||"Beat",type:"Beat",dur:MS.state.beat.dur});
    MS.state.layers.forEach(function(l){if(l.url)assets.push({name:l.name||"Layer",type:"Layer",dur:l.dur});});
    var html=assets.map(function(a){
      return '<div class="ms-proj"><span class="p-icon"><i data-lucide="audio-lines"></i></span>'+
        '<div class="p-main"><div class="p-name">'+esc(a.name)+'</div><div class="p-meta">'+a.type+' · '+fmtTime(a.dur)+'</div></div></div>';
    }).join("");
    return '<div class="ms-info">Assets include your uploaded/recorded audio in this project.</div>'+
      (html||'<div class="ms-empty">No audio assets yet.</div>')+
      '<div class="ms-divider"></div>'+
      '<label class="ms-sb-label">Upload audio</label>'+
      '<div class="ms-sb-row">'+
        '<label class="ms-sb-btn sm" style="cursor:pointer;flex:1;"><input type="file" accept="audio/*" id="vmMusicTakeInput3" style="display:none;">Vocal</label>'+
        '<label class="ms-sb-btn sm" style="cursor:pointer;flex:1;"><input type="file" accept="audio/*" id="vmMusicBeatInput3" style="display:none;">Beat</label>'+
      '</div>';
  }

  function renderAiToolsSection() {
    return '<div class="ms-info">AI tools that are genuinely available or coming soon.</div>'+
      '<div class="ms-sb-stack" style="margin-top:8px;">'+
        '<button class="ms-sb-btn" onclick="window.vmMusicAPI.runAI()">Generate Music</button>'+
        '<button class="ms-sb-btn" onclick="window.vmMusicAPI.runAiEdit()">AI Edit</button>'+
        '<button class="ms-sb-btn" disabled>Extend Song <span style="font-size:10px;color:#64748b;margin-left:auto;">Soon</span></button>'+
        '<button class="ms-sb-btn" disabled>Remix <span style="font-size:10px;color:#64748b;margin-left:auto;">Soon</span></button>'+
        '<button class="ms-sb-btn" disabled>Stem Separation <span style="font-size:10px;color:#64748b;margin-left:auto;">Soon</span></button>'+
        '<button class="ms-sb-btn" disabled>Vocal Isolation <span style="font-size:10px;color:#64748b;margin-left:auto;">Soon</span></button>'+
        '<button class="ms-sb-btn" disabled>Noise Removal <span style="font-size:10px;color:#64748b;margin-left:auto;">Soon</span></button>'+
        '<button class="ms-sb-btn" disabled>Auto Master <span style="font-size:10px;color:#64748b;margin-left:auto;">Soon</span></button>'+
      '</div>';
  }

  /* ── Workspace renderers ───────────────────────────────────────────── */
  function renderWorkspace() {
    var s=MS.state;
    var playing=false;
    var player=document.getElementById("vmMusicPlayer");
    if(player&&!player.paused)playing=true;

    /* Visual area */
    var visual='<div class="ms-ws-visual">'+
      '<div class="ms-ws-artwork"><span class="ms-ws-icon"><i data-lucide="music"></i></span>'+
        '<div class="ms-ws-title-overlay"><h2>'+esc(s.name||"Untitled song")+'</h2>'+
        '<p>'+esc(s.genre)+" · "+esc(s.mood)+(s.key?" · "+esc(s.key):"")+'</p></div>'+
      '</div></div>';

    /* Player controls */
    var hasAudio=s.take.url||s.beat.url;
    var player_html='<div class="ms-ws-player"><div class="ms-ws-controls">'+
      '<button class="ms-play" onclick="window.vmMusicAPI.togglePlay()"'+(hasAudio?"":' disabled')+'><i data-lucide="'+(playing?"pause":"play")+'"></i></button>'+
      '<div class="ms-ws-seek">'+
        '<input type="range" min="0" max="100" value="0" id="msSeek" oninput="window.vmMusicAPI.seek(this.value)">'+
        '<div class="ms-ws-time"><span id="msTimeNow">00:00</span><span id="msTimeDur">'+fmtTime(s.take.dur||s.beat.dur||0)+'</span></div>'+
      '</div>'+
      '<div class="ms-ws-vol"><i data-lucide="volume-2" style="width:16px;height:16px;color:#94a3b8;"></i>'+
        '<input type="range" min="0" max="100" value="'+s.mix.master+'" oninput="window.vmMusicAPI.setMaster(this.value)"></div>'+
    '</div></div>';

    /* Tracks */
    var trackRows="";
    if(s.take.url){
      var muted=trackMuted(s.take);
      trackRows+='<div class="ms-ws-trk">'+
        '<div class="ms-trk-ic" style="background:rgba(34,211,238,0.12);color:#67e8f9;"><i data-lucide="mic"></i></div>'+
        '<div class="ms-trk-info"><div class="ms-trk-name">'+esc(s.take.name||"Vocal")+'</div><div class="ms-trk-meta">Vocal · '+fmtTime(s.take.dur)+(muted?" · muted":"")+'</div></div>'+
        '<div class="ms-trk-bar"><div class="ms-trk-bar-fill" style="width:'+s.take.vol+'%;background:#22d3ee;"></div></div>'+
        '<div class="ms-trk-ctrl">'+
          '<button class="ms-trk-btn'+(s.take.solo?" on":"")+'" title="Solo" onclick="window.vmMusicAPI.setTrack(\'take\',\'solo\','+(!s.take.solo)+')">S</button>'+
          '<button class="ms-trk-btn'+(muted?" muted":"")+'" title="Mute" onclick="window.vmMusicAPI.setTrack(\'take\',\'mute\','+(!s.take.mute)+')">M</button>'+
        '</div></div>';
    }
    if(s.beat.url){
      var bm=trackMuted(s.beat);
      trackRows+='<div class="ms-ws-trk">'+
        '<div class="ms-trk-ic" style="background:rgba(168,85,247,0.12);color:#c084fc;"><i data-lucide="disc-3"></i></div>'+
        '<div class="ms-trk-info"><div class="ms-trk-name">'+esc(s.beat.name||"Beat")+'</div><div class="ms-trk-meta">Beat · '+fmtTime(s.beat.dur)+(bm?" · muted":"")+'</div></div>'+
        '<div class="ms-trk-bar"><div class="ms-trk-bar-fill" style="width:'+s.beat.vol+'%;background:#a855f7;"></div></div>'+
        '<div class="ms-trk-ctrl">'+
          '<button class="ms-trk-btn'+(s.beat.solo?" on":"")+'" title="Solo" onclick="window.vmMusicAPI.setTrack(\'beat\',\'solo\','+(!s.beat.solo)+')">S</button>'+
          '<button class="ms-trk-btn'+(bm?" muted":"")+'" title="Mute" onclick="window.vmMusicAPI.setTrack(\'beat\',\'mute\','+(!s.beat.mute)+')">M</button>'+
        '</div></div>';
    }
    s.layers.forEach(function(l){
      var lm=trackMuted(l);
      trackRows+='<div class="ms-ws-trk">'+
        '<div class="ms-trk-ic" style="background:rgba(16,185,129,0.12);color:#34d399;"><i data-lucide="layers"></i></div>'+
        '<div class="ms-trk-info"><div class="ms-trk-name">'+esc(l.name||"Layer")+'</div><div class="ms-trk-meta">Layer · '+fmtTime(l.dur)+(lm?" · muted":"")+'</div></div>'+
        '<div class="ms-trk-bar"><div class="ms-trk-bar-fill" style="width:'+l.vol+'%;background:#10b981;"></div></div>'+
        '<div class="ms-trk-ctrl">'+
          '<button class="ms-trk-btn'+(l.solo?" on":"")+'" onclick="window.vmMusicAPI.setLayer(\''+l.id+'\',\'solo\','+(!l.solo)+')">S</button>'+
          '<button class="ms-trk-btn'+(lm?" muted":"")+'" onclick="window.vmMusicAPI.setLayer(\''+l.id+'\',\'mute\','+(!l.mute)+')">M</button>'+
          '<button class="ms-trk-btn" style="color:#fda4af;" onclick="window.vmMusicAPI.removeLayer(\''+l.id+'\')">&#215;</button>'+
        '</div></div>';
    });
    if(!trackRows)trackRows='<div class="ms-empty">No tracks yet — record or upload to start.</div>';
    var tracks='<div class="ms-ws-tracks">'+trackRows+'</div>';

    return visual+player_html+tracks;
  }

  /* ── Main render ───────────────────────────────────────────────────── */
  function render() {
    var panel=document.getElementById("vmWsPanelMusic");
    if(!panel)return;

    /* Build sidebar sections */
    var sections=[
      sbSection("create","sparkles","Create","Generate new music",renderCreateSection()),
      sbSection("voice","mic","Voice","Record & choose voice",renderVoiceSection()),
      sbSection("music","music","Music","Beats & backing tracks",renderMusicSection()),
      sbSection("instruments","piano","Instruments","AI instrumentation",renderInstrumentsSection()),
      sbSection("lyrics","file-text","Lyrics","Write & refine lyrics",renderLyricsSection()),
      sbSection("effects","audio-lines","Effects","Vocal effect presets",renderEffectsSection()),
      sbSection("mix","mixer","Mix","Balance & master",renderMixSection()),
      sbSection("ai-edit","brain","AI Edit","Natural-language refine",renderAiEditSection()),
      sbSection("projects","folder","Projects","Saved songs",renderProjectsSection()),
      sbSection("memory","brain-circuit","Memory","Music preferences",renderMemorySection()),
      sbSection("assets","library","Assets","Generated & uploaded",renderAssetsSection()),
      sbSection("ai-tools","bot","AI Tools","Production utilities",renderAiToolsSection())
    ].join("");

    /* Sidebar */
    var sidebar='<div class="ms-sidebar">'+
      '<div class="ms-sb-head">'+
        '<div class="ms-sb-logo">V</div>'+
        '<div class="ms-sb-title"><h3>Music Studio</h3><p>'+esc(MS.state.name||"Untitled")+'</p></div>'+
        '<button class="ms-sb-btn sm" onclick="window.vmMusicAPI.newSong()" title="New song"><i data-lucide="file-plus"></i></button>'+
      '</div>'+
      '<div class="ms-sb-sections">'+sections+'</div>'+
    '</div>';

    /* Workspace */
    var workspace='<div class="ms-workspace">'+renderWorkspace()+'</div>';

    /* Mobile tabs */
    var mobileTabs='<div class="ms-mobile-tabs">'+
      SECTIONS.slice(0,6).map(function(s){
        return '<button class="ms-mtab'+(MS.ui.openSection===s.id?" active":"")+'" onclick="window.vmMusicAPI.setSection(\''+s.id+'\')"><i data-lucide="'+s.icon+'"></i><span>'+s.label+'</span></button>';
      }).join("")+
      '<button class="ms-mtab" onclick="window.vmMusicAPI.setSection(\'projects\')"><i data-lucide="folder"></i><span>More</span></button>'+
    '</div>';

    /* Sheet (mobile drawer) */
    var sheetSection=SECTIONS.find(function(s){return s.id===MS.ui.openSection;})||SECTIONS[0];
    var sheetBody="";
    switch(MS.ui.openSection){
      case "create":sheetBody=renderCreateSection();break;
      case "voice":sheetBody=renderVoiceSection();break;
      case "music":sheetBody=renderMusicSection();break;
      case "instruments":sheetBody=renderInstrumentsSection();break;
      case "lyrics":sheetBody=renderLyricsSection();break;
      case "effects":sheetBody=renderEffectsSection();break;
      case "mix":sheetBody=renderMixSection();break;
      case "ai-edit":sheetBody=renderAiEditSection();break;
      case "projects":sheetBody=renderProjectsSection();break;
      case "memory":sheetBody=renderMemorySection();break;
      case "assets":sheetBody=renderAssetsSection();break;
      case "ai-tools":sheetBody=renderAiToolsSection();break;
    }
    var sheet='<div class="ms-sheet-backdrop'+(MS.ui.mobileSheet?" show":"")+'" onclick="window.vmMusicAPI.closeSheet()"></div>'+
      '<div class="ms-sheet'+(MS.ui.mobileSheet?" show":"")+'">'+
        '<div class="ms-sheet-head"><h4>'+esc(sheetSection.label)+'</h4>'+
          '<button class="ms-sheet-close" onclick="window.vmMusicAPI.closeSheet()"><i data-lucide="x"></i></button></div>'+
        '<div class="ms-sheet-body">'+sheetBody+'</div>'+
      '</div>';

    /* Header (mobile top bar) */
    var head='<div class="vmm-head">'+
      '<div class="vmm-logo">V</div>'+
      '<div class="vmm-title"><h2>Music Studio</h2><p class="vmm-sub">'+esc(MS.state.name||"Untitled")+'</p></div>'+
      '<div style="flex:1;"></div>'+
      '<input class="vmm-input vmm-projectname" id="vmMusicName" value="'+esc(MS.state.name)+'" placeholder="Song name" oninput="window.vmMusicAPI.syncName()" style="max-width:200px;">'+
    '</div>';

    panel.innerHTML=head+'<div class="vmm" style="flex:1;display:flex;flex-direction:column;overflow:hidden;">'+
      '<div class="ms-editor">'+sidebar+workspace+'</div>'+
      mobileTabs+sheet+
      '<audio id="vmMusicPlayer" preload="none" style="display:none;"></audio>'+
      '<div class="ms-toast" id="vmMusicToast"></div>'+
    '</div>';

    refreshLucide();
    bindFields();
    bindPlayerEvents();
  }

  function bindFields() {
    var on=function(id,fn){var el=document.getElementById(id);if(el)el.addEventListener("change",fn);};
    on("vmMusicTakeInput",function(e){onTakeFile(e.target);});
    on("vmMusicBeatInput",function(e){onBeatFile(e.target);});
    on("vmMusicBeatInput2",function(e){onBeatFile(e.target);});
    on("vmMusicLayerInput",function(e){onLayerFile(e.target);});
    on("vmMusicTakeInput3",function(e){onTakeFile(e.target);});
    on("vmMusicBeatInput3",function(e){onBeatFile(e.target);});
    on("vmMusicGenre",function(){syncInputs();});
    on("vmMusicMood",function(){syncInputs();});
    on("vmMusicTempo",function(){syncInputs();});
    on("vmMusicRole",function(){syncInputs();});
    var b=document.getElementById("vmMusicBrief");if(b)b.addEventListener("input",function(){syncInputs();});
    var l=document.getElementById("vmMusicLyrics");if(l)l.addEventListener("input",function(){syncInputs();});
  }

  function bindPlayerEvents() {
    var player=document.getElementById("vmMusicPlayer");
    if(!player)return;
    player.ontimeupdate=function(){
      var now=document.getElementById("msTimeNow");
      var seek=document.getElementById("msSeek");
      if(now)now.textContent=fmtTime(player.currentTime);
      if(seek&&player.duration)seek.value=Math.round(player.currentTime/player.duration*100);
    };
    player.onloadedmetadata=function(){
      var dur=document.getElementById("msTimeDur");
      if(dur)dur.textContent=fmtTime(player.duration);
      MS.state.take.dur=MS.state.take.dur||player.duration||0;
    };
    player.onended=function(){
      var playBtn=document.querySelector(".ms-play");
      if(playBtn)playBtn.innerHTML='<i data-lucide="play"></i>';
      refreshLucide();
    };
  }

  function syncName(){var el=document.getElementById("vmMusicName");if(el)MS.state.name=el.value||MS.state.name;}

  function togglePlay(){
    var player=document.getElementById("vmMusicPlayer");
    if(!player)return;
    if(player.paused){
      if(!player.src&&MS.state.take.url){player.src=MS.state.take.url;player.volume=MS.state.take.vol/100*(MS.state.mix.master/100);}
      if(player.src)player.play().catch(function(){});
    } else {player.pause();}
    render();
  }
  function seek(pct){
    var player=document.getElementById("vmMusicPlayer");
    if(player&&player.duration)player.currentTime=pct/100*player.duration;
  }

  /* ── AI helpers for instruments / lyrics ───────────────────────────── */
  function aiInstrument() {
    var input=document.getElementById("msInstrumentInput");
    var instruction=(input?input.value:"").trim();
    if(!instruction){toast("Type an instrumentation instruction.");return;}
    MS.state.brief=(MS.state.brief?MS.state.brief+"\n\n":"")+instruction;
    toast("Added to next generation brief.");
    if(input)input.value="";
  }
  function aiLyrics() {
    syncInputs();
    MS.state.brief=MS.state.brief||"Generate lyrics for a "+MS.state.genre+" song in "+MS.state.mood+" mood.";
    toast("Generating lyrics...");
    runAI();
  }
  function aiRewriteLyrics() {
    syncInputs();
    if(!MS.state.lyrics){toast("Write some lyrics first to rewrite.");return;}
    document.getElementById("msAiEditInput")&&(document.getElementById("msAiEditInput").value="Rewrite these lyrics to be more polished and singable, keeping the same theme.");
    setSection("ai-edit");
    runAiEdit();
  }

  /* ── Public API ────────────────────────────────────────────────────── */
  var API = {
    setSection: setSection,
    closeSheet: function(){MS.ui.mobileSheet=false;render();},
    onMode: function(m){MS.state.mode=m;render();},
    toggleRecord: toggleRecord, stopRecord: stopRecord,
    playTrack: playTrack, stopPlayback: stopPlayback,
    togglePlay: togglePlay, seek: seek,
    onVoice: function(v){syncInputs();MS.state.voice=v;if(v!=="clone")MS.state.consent=false;render();},
    onConsent: function(){MS.state.consent=!MS.state.consent;render();},
    setFx: setFx,
    applyEffect: function(name){for(var i=0;i<EFFECT_PRESETS.length;i++){if(EFFECT_PRESETS[i].name===name){applyEffectPreset(EFFECT_PRESETS[i]);return;}}},
    setMaster: setMaster, autoMix: autoMix, autoMaster: autoMaster,
    addLayer: addLayer, removeLayer: removeLayer,
    setTrack: setTrack, setLayer: setLayer,
    runAI: runAI, runAiEdit: runAiEdit,
    aiInstrument: aiInstrument, aiLyrics: aiLyrics, aiRewriteLyrics: aiRewriteLyrics,
    saveSong: saveSong, newSong: newSong, loadSong: loadSong, deleteSong: deleteSong,
    exportSong: exportSong, syncName: syncName,
    previewBeat: previewBeat, selectBeat: selectBeat, stopPreview: stopPreview
  };
  window.vmMusicAPI = API;

  function onShow() {
    if(!MS.rendered){
      MS.rendered=true;
      if(!MS.state){loadProjects();loadMemory();MS.state=defaultState();MS.state.id=null;}
      fetchProjectsFromServer();
    }
    render();
  }
  window.vmMusicOnShow = onShow;

  /* ── Init ──────────────────────────────────────────────────────────── */
  function init() {
    injectStyles();
    if(!MS.state){loadProjects();loadMemory();MS.state=defaultState();MS.state.id=null;}
    if(document.readyState==="loading"){
      document.addEventListener("DOMContentLoaded",function(){onShow();});
    } else {onShow();}
  }
  if(document.readyState==="loading"){
    document.addEventListener("DOMContentLoaded",init);
  } else {init();}
})();

