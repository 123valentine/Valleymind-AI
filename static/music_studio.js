/* ValleyMind Music Studio — Professional DAW Workspace
   ────────────────────────────────────────────────────
   Architecture: BandLab-style. Workspace ALWAYS takes priority.
   Blue (#3b82f6) as the interaction color. Timeline-first layout.
   Real audio playback with waveforms, playhead, and scrubbing.
   110+ beat presets with Web Audio synthesis and seamless looping.
   All existing functionality preserved.
*/
(function () {
  "use strict";

  var STORE_KEY = "vmMusicProjects";
  var BLUE = "#3b82f6";
  var BLUE_HV = "#2563eb";
  var BLUE_LT = "#60a5fa";
  var BLUE_DIM = "rgba(59,130,246,.25)";

  var NAV = [
    { id: "record", icon: "mic", label: "Record" },
    { id: "tracks", icon: "layers", label: "Tracks" },
    { id: "generate", icon: "sparkles", label: "Create" },
    { id: "tools", icon: "sliders-horizontal", label: "Tools" },
    { id: "projects", icon: "folder", label: "Projects" }
  ];

  var TOOLS = [
    { id: "voice", icon: "mic", label: "Voice" },
    { id: "music", icon: "music", label: "Beats" },
    { id: "instruments", icon: "piano", label: "Instruments" },
    { id: "lyrics", icon: "file-text", label: "Lyrics" },
    { id: "effects", icon: "audio-lines", label: "Effects" },
    { id: "mix", icon: "mixer", label: "Mix" },
    { id: "ai-edit", icon: "brain", label: "AI Edit" },
    { id: "memory", icon: "brain-circuit", label: "Memory" },
    { id: "assets", icon: "library", label: "Assets" },
    { id: "ai-tools", icon: "bot", label: "AI Tools" }
  ];

  var VOICE_LABELS = { keep: "Keep & enhance my own voice", clone: "AI-clone of my voice (authorized)", elena: "ValleyMind's AI singing voice (Elena)" };
  var VOICE_SUBS = { keep: "Cleans, tunes and enhances your recording.", clone: "An AI model of your voice — requires authorization.", elena: "ValleyMind's approved AI singing voice." };
  var GENRES = ["Afrobeats", "Amapiano", "R&B", "Hip-Hop", "Pop", "Soul", "Gospel", "Highlife", "Dancehall", "Reggae", "Folk", "Jazz", "Electronic"];
  var MOODS = ["Romantic", "Upbeat", "Melancholic", "Hopeful", "Energetic", "Chill", "Bittersweet", "Empowering", "Nostalgic"];
  var TEMPOS = ["Slow", "Medium", "Fast", "Very fast"];
  var ROLES = ["Singer", "Rapper", "Singer-songwriter", "Producer", "Both singing & producing"];

  var EFFECT_PRESETS = [
    { name: "Clean", fx: { noiseReduction: true, pitch: 0, effect: "None", reverb: 10, delay: 0 } },
    { name: "Intune", fx: { noiseReduction: true, pitch: 0, effect: "Intune", reverb: 20, delay: 0 } },
    { name: "Megaphone", fx: { noiseReduction: false, pitch: 0, effect: "Megaphone", reverb: 5, delay: 0 } },
    { name: "Warm", fx: { noiseReduction: false, pitch: 0, effect: "Warm", reverb: 35, delay: 0 } },
    { name: "Bright", fx: { noiseReduction: true, pitch: 8, effect: "Bright", reverb: 15, delay: 0 } },
    { name: "Hall", fx: { noiseReduction: false, pitch: 0, effect: "Hall reverb", reverb: 70, delay: 10 } },
    { name: "Delay", fx: { noiseReduction: false, pitch: 0, effect: "Delay", reverb: 30, delay: 55 } },
    { name: "Tape", fx: { noiseReduction: false, pitch: -12, effect: "Tape", reverb: 25, delay: 0 } },
    { name: "Robot", fx: { noiseReduction: true, pitch: 0, effect: "Robotic", reverb: 5, delay: 0 } },
    { name: "Choir", fx: { noiseReduction: false, pitch: 4, effect: "Choir-ish", reverb: 65, delay: 15 } },
    { name: "Lo-Fi", fx: { noiseReduction: false, pitch: -6, effect: "Lo-Fi", reverb: 40, delay: 0 } },
    { name: "Phone", fx: { noiseReduction: false, pitch: 0, effect: "Telephone", reverb: 5, delay: 0 } }
  ];

  /* ── 110+ Beat Presets — real synthesis configs ────────────────────── */
  var _BS = [
    ["Lagos Midnight", 95, "C4", "Romantic", "Afrobeats", "Slow candle-lit groove", "T00L00K0T00K0K0"],
    ["Accra Breeze", 100, "G4", "Chill", "Highlife", "Earthy highlife bounce", "T0K0T0K0T0K0T0K0K0"],
    ["Abuja Sunrise", 110, "D4", "Hopeful", "Afrobeats", "Bright uplifting groove", "K0T0K0T0K0T0K0T0K0"],
    ["Port Harcourt Groove", 120, "A4", "Upbeat", "Afrobeats", "Party-ready log drum", "K00T0K0KT0K0K0T0"],
    ["Enugu Nights", 85, "E4", "Melancholic", "R&B", "Deep moody R&B", "K00L00K0T00L00K0"],
    ["Ibadan Vibes", 130, "F4", "Energetic", "Afrobeats", "Fast street vibration", "K0K0K0T0K0K0K0T0K0"],
    ["Kano Dust", 90, "Bb3", "Nostalgic", "Folk", "Old-school desert soul", "K0T00K0T00K0T00"],
    ["Warri Energy", 125, "Eb4", "Upbeat", "Afrobeats", "High-energy bounce", "KK0T0KK0T0KK0T0K0"],
    ["Benin City Soul", 95, "C4", "Bittersweet", "Soul", "Soulful and reflective", "T0K00L00K0T0L0K0"],
    ["Calabar Flow", 105, "G4", "Chill", "Highlife", "Smooth coastline travel", "K0T00T0K0T00T0K0"],
    ["Jos Plateau", 100, "A4", "Hopeful", "Pop", "Cool highland hope", "K00K0T0K00K0T0"],
    ["Owerri Heat", 135, "D4", "Energetic", "Dancehall", "Scorching fast beat", "KK0K0T0KK0K0T0K0"],
    ["Kaduna Dawn", 88, "F4", "Romantic", "R&B", "Tender dawn serenade", "K0L00K0T0L00K0"],
    ["Aba Market", 118, "Bb3", "Upbeat", "Afrobeats", "Busy market bounce", "K0T0K0T0KT0K0T0K0"],
    ["Ilorin Breeze", 98, "Eb4", "Chill", "Highlife", "Light evening air", "T0K0T0T0K0T0K0T0"],
    ["Maiduguri Sun", 112, "C4", "Empowering", "Hip-Hop", "Bold and resolute", "K0T0K0TK0T0K0T0"],
    ["Akwa Ibom Tide", 92, "G4", "Melancholic", "R&B", "Watery introspective", "K00T00K0T0K0T00"],
    ["Osogbo Rain", 86, "Ab3", "Bittersweet", "Jazz", "Rain on the roof", "K0L0K0T0K0L0T0K0"],
    ["Sokoto Stars", 94, "B4", "Nostalgic", "Folk", "Night sky memory", "K0T0K00T0K0T0K0"],
    ["Bayelsa River", 102, "Db4", "Romantic", "Soul", "Slow river romance", "K000T0K0T0K0K0"],
    ["Kumasi Gold", 96, "E4", "Romantic", "Highlife", "Golden sunset groove", "T0K0L0K0T0K0L0K0"],
    ["Tamale Fire", 115, "F4", "Energetic", "Dancehall", "Northern fire dance", "KK0T0K0KK0T0KK0"],
    ["Cape Coast Wind", 103, "G4", "Chill", "Reggae", "Coastal breeze rhythm", "K0T0T0K0T0T0K0T0"],
    ["Tema Harbor", 108, "A4", "Upbeat", "Afrobeats", "Harbor night energy", "K0K0T0K0K0T0K0T0"],
    ["Takoradi Sunset", 97, "Bb3", "Romantic", "Soul", "Seaside evening sway", "L0K0T00L0K0T0K0"],
    ["Sekondi Drums", 113, "C4", "Energetic", "Afrobeats", "Deep drum circle", "KK0KK0T0KK0KK0T0"],
    ["Ho Highlands", 91, "D4", "Hopeful", "Gospel", "Mountain top melodies", "K00T0K00T0K00T0K0"],
    ["Bolgatanga North", 89, "E4", "Nostalgic", "Folk", "Sahel wind rhythms", "K0T000K0T000K0T0"],
    ["Wa Sunrise", 106, "F4", "Hopeful", "Highlife", "Eastern light bounce", "K0K0T0T0K0K0T0T0K0"],
    ["Johannesburg Deep", 118, "Ab3", "Energetic", "Electronic", "Deep house foundation", "K0T0K0K0T0K0T0K0K0"],
    ["Pretoria Nights", 105, "Bb3", "Chill", "R&B", "Cool capital evening", "T0K0T0K0T0K0T0K0"],
    ["Soweto Stories", 122, "C4", "Upbeat", "Hip-Hop", "Township energy", "KK0T0K0KK0T0KK0"],
    ["Durban Waves", 98, "Db4", "Romantic", "Amapiano", "Indian Ocean sway", "K00L00K0T0L00K0"],
    ["Cape Town Lights", 112, "Eb4", "Hopeful", "Pop", "Mother city groove", "K0T0K0T0K0T0K0T0K0"],
    ["Bloemfontein Plains", 95, "F4", "Chill", "Folk", "Free State calm", "T00K00T0K00K0T0"],
    ["Pietermaritzburg", 100, "G4", "Hopeful", "Gospel", "Garden city rhythm", "K0K0T0K0K0T0K0T0"],
    ["Port Elizabeth Surf", 108, "A4", "Upbeat", "Reggae", "Coastal surf beats", "K0T0K0T0KT0K0T0K0"],
    ["East London Drift", 103, "Bb3", "Bittersweet", "R&B", "Eastern cape drift", "T0K00L0K0T0L0K0"],
    ["Kimberley Mines", 92, "C4", "Nostalgic", "Soul", "Diamond dust groove", "K00T0K0T00K0T0K0"],
    ["Polokwane Beats", 116, "D4", "Energetic", "Dancehall", "Limpopo heat", "KK0K0T0KK0K0T0K0"],
    ["Nelspruit Sunrise", 101, "Eb4", "Hopeful", "Highlife", "Mpumalanga dawn", "K0T0T0K0T0T0K0T0"],
    ["Rustenburg Mining", 125, "F4", "Upbeat", "Hip-Hop", "North West bounce", "K0K0K0T0K0K0K0T0K0"],
    ["Witbank Coal", 110, "G4", "Energetic", "Electronic", "Power station jam", "KK0T0KK0T0KK0T0K0"],
    ["George Garden", 88, "A4", "Chill", "Jazz", "Garden route calm", "T0K0T0T0K0T0K0T0"],
    ["Nairobi Pulse", 120, "C4", "Upbeat", "Afrobeats", "East African energy", "K0T0K0T0K0T0K0T0K0"],
    ["Mombasa Dhow", 95, "Db4", "Romantic", "Soul", "Dhow sailing rhythm", "K00L00K0T0L00K0"],
    ["Kampala Night", 108, "D4", "Energetic", "Dancehall", "Ugandan nightlife", "KK0T0K0KK0T0KK0"],
    ["Dar es Salaam", 102, "Eb4", "Chill", "Highlife", "Coastal city groove", "T0K0T0K0T0K0T0K0K0"],
    ["Addis Ababa", 90, "F4", "Melancholic", "Jazz", "Ethiopian soul", "K00T00K0T0K0T00"],
    ["Kigali Heights", 115, "G4", "Hopeful", "Pop", "Rwanda rising", "K0T0K0TK0T0K0T0"],
    ["Bujumbura Lake", 86, "Ab3", "Bittersweet", "Folk", "Lakeside contemplation", "K0L0K0T0K0L0T0K0"],
    ["Lusaka Sun", 100, "A4", "Upbeat", "Afrobeats", "Zambian sunshine", "K0K0T0K0K0T0K0T0"],
    ["Harare Gold", 97, "Bb3", "Nostalgic", "Soul", "Zimbabwe memory", "K0T0K00T0K0T0K0"],
    ["Maputo Bay", 105, "C4", "Romantic", "R&B", "Maputo bay romance", "T0K0L0K0T0K0L0K0"],
    ["Lilongwe Heart", 91, "Db4", "Bittersweet", "Folk", "Malawi heart song", "K00T0K0T00K0T0K0"],
    ["Blantyre Rise", 109, "D4", "Empowering", "Gospel", "Southern Malawi fire", "KK0K0T0KK0K0T0K0"],
    ["Windhoek Desert", 88, "Eb4", "Nostalgic", "Folk", "Desert night stillness", "K0T000K0T000K0T0"],
    ["Gaborone Diamond", 103, "F4", "Hopeful", "Pop", "Botswana sparkle", "K0K0T0T0K0K0T0T0K0"],
    ["Antananarivo", 94, "G4", "Melancholic", "Reggae", "Island melancholy", "T00K00T0K00K0T0"],
    ["Dakar Teranga", 118, "C4", "Upbeat", "Afrobeats", "Senegalese energy", "K0T0K0T0KT0K0T0K0"],
    ["Bamako Niger", 100, "D4", "Chill", "Folk", "River Mali groove", "T0K0T0K0T0K0T0K0K0"],
    ["Conakry Bridge", 107, "Eb4", "Energetic", "Dancehall", "Guinea bridge bounce", "KK0T0K0KK0T0KK0"],
    ["Freetown Harbor", 96, "F4", "Romantic", "Soul", "Sierra Leone harbor", "K00L00K0T0L00K0"],
    ["Monrovia Coast", 102, "G4", "Bittersweet", "R&B", "Liberian coast vibe", "K0T00L0K0T0L0K0"],
    ["Abidjan Plateau", 122, "A4", "Upbeat", "Afrobeats", "Ivory Coast energy", "K0K0K0T0K0K0K0T0K0"],
    ["Ouagadougou", 95, "Bb3", "Hopeful", "Highlife", "Burkina Faso light", "K00T0K00T0K00T0K0"],
    ["Niamey Desert", 88, "C4", "Nostalgic", "Folk", "Niger desert wind", "K0T000K0T000K0T0"],
    ["N'Djamena Sun", 112, "D4", "Empowering", "Hip-Hop", "Chad sun power", "KK0K0T0KK0K0T0K0"],
    ["Brazzaville River", 104, "Eb4", "Chill", "R&B", "Congo river flow", "T0K0T0K0T0K0T0K0K0"],
    ["Libreville Forest", 93, "F4", "Melancholic", "Jazz", "Gabon forest echo", "K00T00K0T0K0T00"],
    ["Douala Heat", 128, "G4", "Energetic", "Dancehall", "Cameroon fire", "KK0T0KK0T0KK0T0K0"],
    ["Yaounde Hills", 101, "A4", "Hopeful", "Pop", "Cameroon highlands", "K0K0T0K0K0T0K0T0"],
    ["Kinshasa Rumba", 115, "Bb3", "Upbeat", "Afrobeats", "DRC rumba energy", "K0T0K0T0K0T0K0T0K0"],
    ["Lubumbashi Copper", 98, "C4", "Bittersweet", "Soul", "Copperbelt blues", "T0K00L0K0T0L0K0"],
    ["Marrakech Medina", 105, "Db4", "Romantic", "Jazz", "Moroccan night", "K00L00K0T0L00K0"],
    ["Casablanca Port", 110, "D4", "Chill", "Electronic", "Atlantic breeze", "T0K0T0K0T0K0T0K0K0"],
    ["Tunis Medina", 96, "Eb4", "Nostalgic", "Folk", "Carthage memory", "K0T0K00T0K0T0K0"],
    ["Algiers Casbah", 108, "F4", "Hopeful", "Jazz", "Algerian dawn", "K00T0K00T0K00T0K0"],
    ["Cairo Nights", 120, "G4", "Energetic", "Electronic", "Nile night energy", "KK0T0K0KK0T0KK0"],
    ["Alexandria Sea", 102, "A4", "Romantic", "Soul", "Med Sea romance", "K00T0K0T00K0T0K0"],
    ["Kingston Dancehall", 132, "C4", "Energetic", "Dancehall", "Yard dancehall fire", "KK0K0T0KK0K0T0K0"],
    ["Montego Bay", 118, "D4", "Upbeat", "Reggae", "MBJ beach vibes", "K0K0T0T0K0K0T0T0K0"],
    ["Nassau Islands", 105, "Eb4", "Chill", "Reggae", "Caribbean islands", "T0K0T0K0T0K0T0K0K0"],
    ["Havana Nights", 95, "F4", "Romantic", "Soul", "Cuban night sway", "T00L00K0T00K0K0"],
    ["San Juan Beat", 112, "G4", "Upbeat", "Pop", "Boricua bounce", "K0T0K0T0KT0K0T0K0"],
    ["Santo Domingo", 122, "A4", "Energetic", "Dancehall", "Dominican fire", "KK0T0KK0T0KK0T0K0"],
    ["Port-au-Prince", 100, "Bb3", "Bittersweet", "R&B", "Haitian heart", "K00T00K0T0K0T00"],
    ["Bridgetown Breeze", 98, "C4", "Chill", "Reggae", "Barbados calm", "T0K0T0T0K0T0K0T0"],
    ["St George's", 108, "D4", "Hopeful", "Pop", "Grenada sunrise", "K0K0T0K0K0T0K0T0"],
    ["Castries Light", 103, "Eb4", "Romantic", "R&B", "St Lucia sunset", "K00L00K0T0L00K0"],
    ["Roseau Valley", 91, "F4", "Melancholic", "Folk", "Dominica valley", "K0T00L0K0T0L0K0"],
    ["St John's Harbour", 96, "G4", "Nostalgic", "Reggae", "Antigua harbour", "K0T0K00T0K0T0K0"],
    ["Kingstown Hill", 106, "A4", "Hopeful", "Gospel", "St Vincent green", "K00T0K00T0K00T0K0"],
    ["Basseterre Night", 99, "Bb3", "Chill", "Reggae", "St Kitts night", "T0K0T0K0T0K0T0K0K0"],
    ["Addis Groove", 110, "C4", "Energetic", "Afrobeats", "Ethiopian grooves", "KK0T0K0KK0T0KK0"],
    ["Mekelle Highland", 87, "D4", "Nostalgic", "Folk", "Tigray highlands", "K0T000K0T000K0T0"],
    ["Harar Ancient", 93, "Eb4", "Bittersweet", "Jazz", "Ancient city beat", "K0L0K0T0K0L0T0K0"],
    ["Jimma Coffee", 101, "F4", "Chill", "Soul", "Coffee ceremony", "T0K0T0K0T0K0T0K0K0"],
    ["Lalibela Sacred", 85, "G4", "Melancholic", "Gospel", "Rock church echo", "K00T00K0T0K0T00"],
    ["Gondar Castle", 99, "A4", "Nostalgic", "Folk", "Castle drums", "K0T0K00T0K0T0K0"],
    ["Axum Obelisk", 92, "Bb3", "Hopeful", "Gospel", "Ancient obelisk", "K00K0T0K00K0T0"],
    ["Bahir Dar Lake", 104, "C4", "Romantic", "Soul", "Lake Tana sway", "T0K0L0K0T0K0L0K0"],
    ["Dire Dawa Rail", 116, "D4", "Upbeat", "Afrobeats", "Railway junction", "K0K0T0T0K0K0T0T0K0"],
    ["Assela Plains", 97, "Eb4", "Chill", "Highlife", "Oromia plains", "T0K0T0T0K0T0K0T0"],
    ["Lagos Electric", 128, "F4", "Energetic", "Electronic", "Electronic Lagos", "KK0KK0T0KK0KK0T0"],
    ["Accra Electronic", 125, "G4", "Upbeat", "Electronic", "Ghana electronic", "KK0T0K0KK0T0KK0"],
    ["Nairobi Electronic", 130, "A4", "Energetic", "Electronic", "Kenya electro", "K0K0K0T0K0K0K0T0K0"],
    ["Jozi Bass", 135, "Bb3", "Energetic", "Electronic", "SA bass house", "KK0T0KK0T0KK0T0K0"],
    ["Dakar Pulse", 122, "C4", "Upbeat", "Electronic", "West African electro", "K0T0K0T0KT0K0T0K0"],
    ["Addis Electronic", 115, "Db4", "Hopeful", "Electronic", "Ethiopian electronic", "K0K0T0K0K0T0K0T0"],
    ["Kigali Electronic", 118, "D4", "Upbeat", "Electronic", "Rwanda beats", "K0T0K0T0K0T0K0T0K0"],
    ["Accra Afropop", 108, "Eb4", "Upbeat", "Pop", "Ghanaian pop heat", "K0K0T0T0K0K0T0T0K0"],
    ["Lagos Highlife", 102, "F4", "Chill", "Highlife", "Classic highlife charm", "T0K0T0K0T0K0T0K0K0"],
    ["Kinshasa Funk", 114, "G4", "Energetic", "Soul", "Congolese funk drive", "KK0T0K0KK0T0KK0"],
    ["Maputo Groove", 100, "A4", "Chill", "Amapiano", "Mozambique groove", "K00L00K0T0L00K0"],
    ["Nairobi Reggae", 96, "Bb3", "Chill", "Reggae", "Kenyan roots reggae", "K0T00L0K0T0L0K0"]
  ];
  var BEAT_PRESETS = _BS.map(function (r, i) {
    return { id: "b" + i, city: r[0], bpm: r[1], note: r[2], mood: r[3], genre: r[4], desc: r[5], pattern: r[6] };
  });

  var NOTE_FREQ = (function () { var map = {}; var names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]; var flatMap = { "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#" }; var A4 = 440, A4midi = 69; for (var midi = 0; midi < 128; midi++) { var oct = Math.floor(midi / 12) - 1; var nc = names[midi % 12]; map[nc + oct] = A4 * Math.pow(2, (midi - A4midi) / 12); } function noteFreq(n) { if (map[n]) return map[n]; if (flatMap[n]) return map[flatMap[n]]; return 261.63; } return noteFreq; })();
  function bassFreq(note) { return NOTE_FREQ(note) / 2; }

  /* ── State ────────────────────────────────────────────────────────── */
  var MS = {
    state: null,
    recorder: null, recStream: null, chunks: [], timer: null, elapsed: 0,
    projects: [], rendered: false, previewPreset: null,
    ui: { activeNav: "", openTool: "", recording: false, saveState: "", searchBeat: "" },
    memory: [],
    audio: { el: null, playing: null, position: 0, loopDur: 0, loopBase: 0, lastTime: 0, peaks: {}, raf: null }
  };

  function defaultState() {
    return {
      name: "Untitled song", mode: "diy", role: "Singer", genre: "Afrobeats",
      mood: "Romantic", tempo: "Medium", key: "", language: "English",
      brief: "", lyrics: "", voice: "keep", consent: false,
      take: { name: "", url: "", dur: 0, vol: 100, mute: false, solo: false },
      beat: { name: "", url: "", dur: 0, vol: 100, mute: false, solo: false },
      layers: [],
      fx: { noiseReduction: false, pitch: 0, effect: "None", reverb: 30, delay: 0 },
      mix: { master: 80 },
      autoMix: false, autoMaster: false, aiResult: null,
      savedAt: 0, beatPreset: null, lastAiEdit: null, tags: []
    };
  }

  function normalizeProject(p) {
    var d = defaultState(); for (var k in d) { if (typeof p[k] === "undefined") p[k] = clone(d[k]); }
    if (!p.take || typeof p.take !== "object") p.take = d.take;
    if (!p.beat || typeof p.beat !== "object") p.beat = d.beat;
    if (!p.fx) p.fx = d.fx; if (!p.mix) p.mix = d.mix;
    p.take.vol = (typeof p.take.vol === "number") ? p.take.vol : 100; p.take.mute = !!p.take.mute; p.take.solo = !!p.take.solo;
    p.beat.vol = (typeof p.beat.vol === "number") ? p.beat.vol : 100; p.beat.mute = !!p.beat.mute; p.beat.solo = !!p.beat.solo;
    p.layers = Array.isArray(p.layers) ? p.layers : []; return p;
  }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function fmtTime(s) { if (!s && s !== 0) return "00:00"; s = Math.round(s || 0); var m = Math.floor(s / 60); var ss = s % 60; return (m < 10 ? "0" + m : m) + ":" + (ss < 10 ? "0" + ss : ss); }
  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }

  /* ── Storage ──────────────────────────────────────────────────────── */
  function loadProjects() { try { var p = JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); MS.projects = (Array.isArray(p) ? p : []).map(normalizeProject); } catch (e) { MS.projects = []; } }
  function saveProjects() { try { localStorage.setItem(STORE_KEY, JSON.stringify(MS.projects)); } catch (e) { } }
  function pushProjectsToServer() { if (typeof apiFetch !== "function") return; apiFetch("/api/music/projects", { method: "POST", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ projects: MS.projects }) }).catch(function () { }); }
  function deleteProjectOnServer(id) { if (typeof apiFetch !== "function") return; apiFetch("/api/music/projects/" + encodeURIComponent(id), { method: "DELETE", credentials: "include", headers: authHeaders({}) }).catch(function () { }); }
  function fetchProjectsFromServer() { if (typeof apiFetch !== "function") return Promise.resolve(); return apiFetch("/api/music/projects", { method: "GET", credentials: "include", headers: authHeaders({}) }).then(function (r) { return r.json(); }).then(function (d) { if (!d || !Array.isArray(d.projects)) return; var server = d.projects.map(normalizeProject); var map = {}; MS.projects.forEach(function (p) { map[p.id] = p; }); server.forEach(function (p) { var mine = map[p.id]; if (!mine || (p.savedAt || 0) > (mine.savedAt || 0)) map[p.id] = p; }); MS.projects = Object.keys(map).map(function (k) { return map[k]; }); saveProjects(); render(); }).catch(function () { }); }

  /* ── Memory ───────────────────────────────────────────────────────── */
  function loadMemory() { try { MS.memory = JSON.parse(localStorage.getItem(STORE_KEY + "_mem") || "[]"); } catch (e) { MS.memory = []; } }
  function saveMemory() { try { localStorage.setItem(STORE_KEY + "_mem", JSON.stringify(MS.memory)); } catch (e) { } }
  function addMemory(entry) { MS.memory.unshift(entry); if (MS.memory.length > 50) MS.memory.pop(); saveMemory(); }

  /* ── UI Helpers ───────────────────────────────────────────────────── */
  function toast(msg) { var el = document.getElementById("vmMusicToast"); if (!el) return; el.textContent = msg; el.classList.add("show"); clearTimeout(el._t); el._t = setTimeout(function () { el.classList.remove("show"); }, 2600); }
  function refreshLucide() { if (window.lucide && typeof window.lucide.createIcons === "function") { try { window.lucide.createIcons(); } catch (e) { } } }
  function trackMuted(t) { var soloed = (MS.state.take.solo || MS.state.beat.solo) || MS.state.layers.some(function (l) { return l.solo; }); if (soloed) return !t.solo; return t.mute; }
  function showSaveState(state) { MS.ui.saveState = state; var el = document.getElementById("msSaveStatus"); if (el) el.textContent = state; }
  function autoSaveDebounced() {
    if (MS.ui._saveTimer) clearTimeout(MS.ui._saveTimer);
    showSaveState("Saving...");
    MS.ui._saveTimer = setTimeout(function () {
      syncInputs(); MS.state.savedAt = Date.now();
      if (!MS.state.id) MS.state.id = "ms" + Date.now();
      var found = false;
      for (var i = 0; i < MS.projects.length; i++) { if (MS.projects[i].id === MS.state.id) { MS.projects[i] = normalizeProject(clone(MS.state)); found = true; break; } }
      if (!found) MS.projects.unshift(normalizeProject(clone(MS.state)));
      saveProjects(); pushProjectsToServer(); showSaveState("Saved");
      setTimeout(function () { if (MS.ui.saveState === "Saved") showSaveState(""); }, 2000);
    }, 1500);
  }

  /* ── Recording ────────────────────────────────────────────────────── */
  function toggleRecord() {
    if (MS.recorder && MS.recorder.state === "recording") { stopRecord(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { toast("Recording not supported."); return; }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      MS.recStream = stream;
      var mime = (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported) ? (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "") : "";
      MS.recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      MS.chunks = [];
      MS.recorder.ondataavailable = function (ev) { if (ev.data && ev.data.size) MS.chunks.push(ev.data); };
      MS.recorder.onstop = function () {
        var blob = new Blob(MS.chunks, { type: MS.recorder.mimeType || "audio/webm" });
        var take = MS.state.take;
        if (take.url) try { URL.revokeObjectURL(take.url); } catch (e) { }
        take.url = URL.createObjectURL(blob);
        take.name = "Take " + fmtTime(Date.now() / 1000).replace(":", "");
        take.dur = MS.elapsed;
        stopRecTracks(); MS.ui.recording = false;
        storePeaks("take", take.url, function () { render(); });
        autoSaveDebounced(); toast("Take recorded.");
      };
      MS.recorder.start(); MS.elapsed = 0; MS.ui.recording = true;
      clearInterval(MS.timer);
      MS.timer = setInterval(function () { MS.elapsed++; renderRecTime(); }, 1000);
      render();
    }).catch(function () { toast("Microphone blocked. Allow mic access."); });
  }
  function stopRecTracks() { try { if (MS.recStream) MS.recStream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { } MS.recStream = null; }
  function stopRecord() { if (MS.recorder && MS.recorder.state === "recording") { try { MS.recorder.stop(); } catch (e) { } } clearInterval(MS.timer); MS.timer = null; MS.recorder = null; MS.ui.recording = false; render(); }
  function renderRecTime() { var n = document.getElementById("msRecTime"); if (n) n.textContent = fmtTime(MS.elapsed); }

  /* ── Audio Engine ─────────────────────────────────────────────────── */
  function initAudioEl() {
    if (MS.audio.el) return;
    var a = new Audio();
    a.preload = "auto";
    a.addEventListener("timeupdate", onTimeUpdate);
    a.addEventListener("ended", onPlayEnd);
    MS.audio.el = a;
  }
  function getTrackUrl(kind) {
    if (kind === "take") return MS.state.take.url;
    if (kind === "beat") return MS.state.beat.url;
    var id = kind.replace("layer_", "");
    var l = MS.state.layers.filter(function (x) { return x.id === id; })[0];
    return l ? l.url : "";
  }
  function getTrackVol(kind) {
    var m = MS.state.mix.master / 100;
    if (kind === "take") return (MS.state.take.vol / 100) * m;
    if (kind === "beat") return (MS.state.beat.vol / 100) * m;
    var id = kind.replace("layer_", "");
    var l = MS.state.layers.filter(function (x) { return x.id === id; })[0];
    return l ? (l.vol / 100) * m : 0;
  }
  function getEffectiveDur(kind) {
    if (!kind) return 0;
    if (kind === "take") return MS.state.take.dur || 0;
    if (kind === "beat") return MS.state.beatPreset ? 45 * 60 : (MS.state.beat.dur || 0);
    var id = kind.replace("layer_", "");
    var l = MS.state.layers.filter(function (x) { return x.id === id; })[0];
    return l ? (l.dur || 0) : 0;
  }
  function togglePlay(kind) {
    initAudioEl();
    var a = MS.audio.el;
    var url = getTrackUrl(kind);
    if (!url) { toast("No audio loaded for this track."); return; }
    if (MS.audio.playing === kind && !a.paused) { a.pause(); return; }
    if (MS.audio.playing !== kind) {
      MS.audio.loopBase = 0; MS.audio.lastTime = 0;
      MS.audio.playing = kind; a.src = url;
    }
    a.volume = Math.min(1, Math.max(0, getTrackVol(kind)));
    a.play().catch(function () { });
    startRAF();
  }
  function pausePlayback() { var a = MS.audio.el; if (a) { try { a.pause(); } catch (e) { } } }
  function stopPlayback() { pausePlayback(); if (MS.audio.el) MS.audio.el.currentTime = 0; MS.audio.playing = null; MS.audio.position = 0; MS.audio.loopBase = 0; stopRAF(); render(); }
  function seekTo(pct) {
    var a = MS.audio.el; if (!a || !MS.audio.playing) return;
    var dur = getEffectiveDur(MS.audio.playing);
    var target = pct * dur;
    var ld = MS.audio.loopDur;
    if (ld > 0 && MS.state.beatPreset) {
      MS.audio.loopBase = Math.floor(target / ld) * ld;
      a.currentTime = target % ld;
    } else {
      a.currentTime = Math.min(target, a.duration || target);
      MS.audio.loopBase = 0;
    }
    MS.audio.position = target; MS.audio.lastTime = a.currentTime;
  }
  function onTimeUpdate() {
    var a = MS.audio.el; if (!a || !MS.audio.playing) return;
    var ld = MS.audio.loopDur;
    var ct = a.currentTime;
    if (ld > 0 && ct < MS.audio.lastTime - 0.5) { MS.audio.loopBase += ld; }
    MS.audio.lastTime = ct;
    var totalPos = MS.audio.loopBase + ct;
    MS.audio.position = totalPos;
    var dur = getEffectiveDur(MS.audio.playing);
    var pct = dur > 0 ? Math.min(100, (totalPos / dur) * 100) : 0;
    var sf = document.getElementById("msSeekFill"); if (sf) sf.style.width = pct + "%";
    var pt = document.getElementById("msPlayTime"); if (pt) pt.textContent = fmtTime(totalPos) + " / " + fmtTime(dur);
    var ph = document.getElementById("msPlayhead"); if (ph) ph.style.left = pct + "%";
    var pb = document.getElementById("msPlayBtn"); if (pb) pb.innerHTML = a.paused ? svgPlay() : svgPause();
    if (totalPos >= dur && dur > 0) { a.pause(); onPlayEnd(); }
  }
  function onPlayEnd() { stopRAF(); var pb = document.getElementById("msPlayBtn"); if (pb) pb.innerHTML = svgPlay(); }
  function startRAF() { if (MS.audio.raf) return; }
  function stopRAF() { if (MS.audio.raf) { cancelAnimationFrame(MS.audio.raf); MS.audio.raf = null; } }
  function decodeForPeaks(url, cb) {
    if (!url || (!window.AudioContext && !window.webkitAudioContext)) { if (cb) cb(null); return; }
    fetch(url).then(function (r) { return r.arrayBuffer(); }).then(function (buf) {
      var Actx = window.AudioContext || window.webkitAudioContext;
      var ctx = new Actx();
      ctx.decodeAudioData(buf).then(function (ab) { var p = extractPeaks(ab, 800); ctx.close(); if (cb) cb({ data: p, dur: ab.duration }); }).catch(function () { if (cb) cb(null); });
    }).catch(function () { if (cb) cb(null); });
  }
  function extractPeaks(audioBuf, numPeaks) {
    var data = audioBuf.getChannelData(0);
    var step = Math.ceil(data.length / numPeaks);
    var peaks = new Float32Array(numPeaks * 2);
    for (var i = 0; i < numPeaks; i++) {
      var min = 1, max = -1;
      for (var j = 0; j < step; j++) {
        var idx = i * step + j;
        if (idx < data.length) { if (data[idx] < min) min = data[idx]; if (data[idx] > max) max = data[idx]; }
      }
      peaks[i * 2] = min; peaks[i * 2 + 1] = max;
    }
    return peaks;
  }
  function storePeaks(kind, url, cb) {
    if (!url) { if (cb) cb(); return; }
    decodeForPeaks(url, function (info) {
      if (info) MS.audio.peaks[kind] = info;
      if (cb) cb();
    });
  }
  function drawWaveform(canvas, peaksInfo) {
    if (!canvas || !peaksInfo || !peaksInfo.data) return;
    var w = canvas.width = canvas.offsetWidth * 2;
    var h = canvas.height = canvas.offsetHeight * 2;
    if (w === 0 || h === 0) return;
    var ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    var peaks = peaksInfo.data;
    var numPeaks = peaks.length / 2;
    var barW = w / numPeaks;
    ctx.fillStyle = BLUE;
    for (var i = 0; i < numPeaks; i++) {
      var min = peaks[i * 2], max = peaks[i * 2 + 1];
      var yMax = (1 + max) * h / 2, yMin = (1 + min) * h / 2;
      ctx.fillRect(i * barW, yMax, Math.max(1, barW - 0.5), yMin - yMax);
    }
  }
  function drawAllWaveforms() {
    var canvases = document.querySelectorAll(".ms-tl-wave");
    for (var i = 0; i < canvases.length; i++) {
      var c = canvases[i];
      var kind = c.getAttribute("data-kind");
      drawWaveform(c, MS.audio.peaks[kind]);
    }
  }

  /* ── Audio Upload ─────────────────────────────────────────────────── */
  function loadAudioFields(track, f, kind) {
    track.name = f.name; track.url = URL.createObjectURL(f); track.dur = 0;
    var a = new Audio(); a.preload = "metadata"; a.src = track.url;
    a.onloadedmetadata = function () { track.dur = a.duration || 0; storePeaks(kind, track.url, function () { render(); }); };
  }
  function onTakeFile(input) { var f = input && input.files && input.files[0]; if (!f) return; var t = MS.state.take; if (t.url) try { URL.revokeObjectURL(t.url); } catch (e) { } loadAudioFields(t, f, "take"); autoSaveDebounced(); toast("Vocal added."); input.value = ""; }
  function onBeatFile(input) { var f = input && input.files && input.files[0]; if (!f) return; var t = MS.state.beat; if (t.url) try { URL.revokeObjectURL(t.url); } catch (e) { } MS.state.beatPreset = null; loadAudioFields(t, f, "beat"); autoSaveDebounced(); toast("Beat added."); input.value = ""; }
  function onLayerFile(input) { var f = input && input.files && input.files[0]; if (!f) return; addLayer().then(function () { var l = MS.state.layers[MS.state.layers.length - 1]; if (l && l.url) try { URL.revokeObjectURL(l.url); } catch (e) { } loadAudioFields(l, f, "layer_" + l.id); autoSaveDebounced(); toast("Layer added."); }); if (input) input.value = ""; }
  function addLayer() { MS.state.layers.push({ id: "ly" + Date.now(), name: "Layer " + (MS.state.layers.length + 1), url: "", dur: 0, vol: 100, mute: false, solo: false }); render(); return Promise.resolve(); }
  function removeLayer(id) { MS.state.layers = MS.state.layers.filter(function (l) { return l.id !== id; }); render(); autoSaveDebounced(); }
  function setTrack(kind, field, val) { var t = kind === "beat" ? MS.state.beat : MS.state.take; t[field] = (field === "vol") ? Number(val) : !!val; render(); autoSaveDebounced(); }
  function setLayer(id, field, val) { var l = MS.state.layers.filter(function (x) { return x.id === id; })[0]; if (!l) return; l[field] = (field === "vol") ? Number(val) : !!val; render(); autoSaveDebounced(); }
  function syncInputs() { var get = function (id) { var el = document.getElementById(id); return el ? el.value : ""; }; MS.state.name = get("vmMusicName") || MS.state.name; MS.state.role = get("vmMusicRole") || "Singer"; MS.state.genre = get("vmMusicGenre") || "Afrobeats"; MS.state.mood = get("vmMusicMood") || "Romantic"; MS.state.tempo = get("vmMusicTempo") || "Medium"; MS.state.key = get("vmMusicKey") || ""; MS.state.language = get("vmMusicLanguage") || "English"; MS.state.brief = get("vmMusicBrief") || ""; MS.state.lyrics = get("vmMusicLyrics") || ""; }
  function syncName() { var el = document.getElementById("vmMusicName"); if (el) MS.state.name = el.value || MS.state.name; }

  /* ── Beat Synthesis ───────────────────────────────────────────────── */
  function beatPresetById(id) { for (var i = 0; i < BEAT_PRESETS.length; i++) if (BEAT_PRESETS[i].id === id) return BEAT_PRESETS[i]; return null; }
  function renderBeatLoop(preset) {
    var Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Offline) return Promise.reject(new Error("no offline ctx"));
    var rate = 44100, spb = 60 / preset.bpm, steps = 16, barDur = spb * steps;
    var ctx = new Offline(2, Math.ceil(rate * barDur), rate);
    var root = bassFreq(preset.note);
    var pat = preset.pattern;
    for (var i = 0; i < pat.length; i++) {
      var ch = pat.charAt(i);
      var when = i * (barDur / pat.length);
      if (ch === "K") { kick(ctx, when, 0.95); bassHit(ctx, root, when, spb * 0.8); }
      else if (ch === "k") { kick(ctx, when, 0.5); }
      else if (ch === "L") { kick(ctx, when, 0.6); }
      else if (ch === "T") { snare(ctx, when, 0.75); }
      else if (ch === "t") { snare(ctx, when, 0.3); }
      else if (ch === "H") { hat(ctx, when, 0.3); }
      else if (ch === "h") { hat(ctx, when, 0.12); }
      else if (ch === "0") { hat(ctx, when, 0.12); }
    }
    for (var h = 0; h < Math.ceil(ctx.duration / spb); h++) { if (h % 2 === 1) hat(ctx, h * spb, 0.06); }
    return ctx.startRendering().then(function (buffer) { return encodeWav(buffer); });
  }
  function kick(ctx, when, vel) { var o = ctx.createOscillator(); var g = ctx.createGain(); o.type = "sine"; o.frequency.setValueAtTime(160, when); o.frequency.exponentialRampToValueAtTime(48, when + 0.1); g.gain.setValueAtTime(0, when); g.gain.linearRampToValueAtTime(0.9 * vel, when + 0.005); g.gain.exponentialRampToValueAtTime(0.001, when + 0.18); o.connect(g); g.connect(ctx.destination); o.start(when); o.stop(when + 0.2); }
  function bassHit(ctx, root, when, dur) { var o = ctx.createOscillator(); var g = ctx.createGain(); o.type = "sine"; o.frequency.value = root; g.gain.setValueAtTime(0, when); g.gain.linearRampToValueAtTime(0.4, when + 0.005); g.gain.setValueAtTime(0.4, when + dur * 0.5); g.gain.exponentialRampToValueAtTime(0.001, when + dur); o.connect(g); g.connect(ctx.destination); o.start(when); o.stop(when + dur + 0.02); }
  function snare(ctx, when, vel) { var n = ctx.createBufferSource(); var b = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.25), ctx.sampleRate); var d = b.getChannelData(0); for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length); n.buffer = b; var f = ctx.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = 3000; f.Q.value = 1; var g = ctx.createGain(); g.gain.setValueAtTime(0, when); g.gain.linearRampToValueAtTime(0.6 * vel, when + 0.002); g.gain.exponentialRampToValueAtTime(0.001, when + 0.2); n.connect(f); f.connect(g); g.connect(ctx.destination); n.start(when); n.stop(when + 0.25); }
  function hat(ctx, when, vel) { var n = ctx.createBufferSource(); var b = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.05), ctx.sampleRate); var d = b.getChannelData(0); for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length); n.buffer = b; var f = ctx.createBiquadFilter(); f.type = "highpass"; f.frequency.value = 7000; var g = ctx.createGain(); g.gain.setValueAtTime(0, when); g.gain.linearRampToValueAtTime(0.4 * vel, when + 0.001); g.gain.exponentialRampToValueAtTime(0.001, when + 0.04); n.connect(f); f.connect(g); g.connect(ctx.destination); n.start(when); n.stop(when + 0.05); }
  function encodeWav(buffer) { var numCh = buffer.numberOfChannels; var len = buffer.length * numCh * 2; var out = new ArrayBuffer(44 + len); var v = new DataView(out); function wStr(o, s) { for (var i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); } wStr(0, "RIFF"); v.setUint32(4, 36 + len, true); wStr(8, "WAVE"); wStr(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, numCh, true); v.setUint32(24, buffer.sampleRate, true); v.setUint32(28, buffer.sampleRate * numCh * 2, true); v.setUint16(32, numCh * 2, true); v.setUint16(34, 16, true); wStr(36, "data"); v.setUint32(40, len, true); var chans = []; for (var i = 0; i < numCh; i++) chans.push(buffer.getChannelData(i)); var off = 44; for (var i = 0; i < buffer.length; i++) { for (var c = 0; c < numCh; c++) { var s = Math.max(-1, Math.min(1, chans[c][i])); v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true); off += 2; } } return new Blob([v], { type: "audio/wav" }); }
  function urlFromBlob(blob) { try { return (window.URL || window.webkitURL).createObjectURL(blob); } catch (e) { return ""; } }
  function previewBeat(id) {
    var preset = beatPresetById(id); if (!preset) { toast("Beat not found."); return; }
    stopPreview(); MS.previewPreset = id; render();
    renderBeatLoop(preset).then(function (blob) {
      var url = urlFromBlob(blob); if (!url) return;
      var a = document.getElementById("vmMusicPlayer"); if (!a) { a = document.createElement("audio"); a.id = "vmMusicPlayer"; document.body.appendChild(a); }
      a.src = url; a.volume = 0.9; if (a.play) a.play();
    }).catch(function () { MS.previewPreset = null; render(); });
  }
  function stopPreview() { MS.previewPreset = null; var a = document.getElementById("vmMusicPlayer"); if (a) { try { a.pause(); } catch (e) { } a.removeAttribute("src"); } }
  function selectBeat(id) {
    var preset = beatPresetById(id); if (!preset) return;
    var old = MS.state.beat; if (old.url) try { URL.revokeObjectURL(old.url); } catch (e) { }
    stopPlayback(); MS.previewPreset = null;
    renderBeatLoop(preset).then(function (blob) {
      var url = urlFromBlob(blob); if (!url) return;
      MS.state.beat.url = url; MS.state.beat.name = preset.city;
      MS.state.beat.dur = 45 * 60; MS.state.beat.vol = 100;
      MS.state.beat.mute = false; MS.state.beat.solo = false;
      MS.state.beatPreset = preset.id;
      var a = new Audio(); a.preload = "metadata"; a.src = url;
      a.onloadedmetadata = function () { MS.audio.loopDur = a.duration || 0; render(); };
      storePeaks("beat", url, function () { render(); });
      toast(preset.city + " loaded."); autoSaveDebounced();
    }).catch(function () { render(); });
  }

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
    apiFetch("/api/music", { method: "POST", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ brief: MS.state.brief, role: MS.state.role, genre: MS.state.genre, mood: MS.state.mood, tempo: MS.state.tempo, key: MS.state.key, language: MS.state.language, voice: MS.state.voice, lyrics: MS.state.lyrics }),
      timeoutMs: 60000
    }).then(function (r) { return r.json(); }).then(function (d) {
      MS.state.aiResult = d || null;
      if (d && d.generated) addMemory({ type: "generated", name: MS.state.name, genre: MS.state.genre, mood: MS.state.mood, date: Date.now() });
      render(); toast("Music package generated!"); autoSaveDebounced();
    }).catch(function () { toast("Couldn't reach the producer."); });
  }
  function runAiEdit() {
    syncInputs(); var input = document.getElementById("msAiEditInput"); var instruction = (input ? input.value : "").trim();
    if (!instruction) { toast("Type an instruction first."); return; }
    toast("Applying changes...");
    apiFetch("/api/music/ai-edit", { method: "POST", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ instruction: instruction, lyrics: MS.state.lyrics || "", arrangement: (MS.state.aiResult && MS.state.aiResult.arrangement) || "", genre: MS.state.genre, mood: MS.state.mood, tempo: MS.state.tempo, key: MS.state.key, name: MS.state.name }),
      timeoutMs: 45000
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.status === "success" && d.changes) {
        var ch = d.changes;
        if (ch.lyrics) MS.state.lyrics = ch.lyrics;
        if (ch.title) MS.state.name = ch.title;
        if (ch.arrangement && MS.state.aiResult) MS.state.aiResult.arrangement = ch.arrangement;
        if (ch.genre) MS.state.genre = ch.genre; if (ch.mood) MS.state.mood = ch.mood;
        if (ch.tempo) MS.state.tempo = ch.tempo; if (ch.key) MS.state.key = ch.key;
        MS.state.lastAiEdit = { instruction: instruction, summary: d.summary || "", date: Date.now() };
        addMemory({ type: "ai-edit", instruction: instruction, summary: d.summary || "", date: Date.now() });
        render(); toast(d.summary || "Changes applied!"); autoSaveDebounced();
      } else { toast((d && d.message) || "AI edit couldn't process that."); }
    }).catch(function () { toast("Couldn't reach the AI editor."); });
  }

  /* ── Save / load / delete / export ────────────────────────────────── */
  function saveSong() { syncInputs(); MS.state.savedAt = Date.now(); if (!MS.state.id) MS.state.id = "ms" + Date.now(); var found = false; for (var i = 0; i < MS.projects.length; i++) { if (MS.projects[i].id === MS.state.id) { MS.projects[i] = normalizeProject(clone(MS.state)); found = true; break; } } if (!found) MS.projects.unshift(normalizeProject(clone(MS.state))); saveProjects(); pushProjectsToServer(); showSaveState("Saved"); toast("Song saved."); render(); }
  function newSong() { stopPlayback(); MS.state = defaultState(); MS.state.id = null; MS.ui.openTool = ""; MS.ui.activeNav = ""; MS.audio.peaks = {}; render(); }
  function loadSong(id) { for (var i = 0; i < MS.projects.length; i++) { if (MS.projects[i].id === id) { stopPlayback(); MS.state = normalizeProject(clone(MS.projects[i])); MS.ui.openTool = ""; MS.ui.activeNav = ""; MS.audio.peaks = {}; render(); toast("Song loaded."); return; } } }
  function deleteSong(id) { MS.projects = MS.projects.filter(function (p) { return p.id !== id; }); saveProjects(); deleteProjectOnServer(id); render(); }
  function exportSong() { syncInputs(); var title = MS.state.name || "Untitled"; var parts = [title, "Genre: " + MS.state.genre + " \u00b7 Mood: " + MS.state.mood + " \u00b7 Tempo: " + MS.state.tempo + (MS.state.key ? " \u00b7 Key: " + MS.state.key : ""), "", ""]; if (MS.state.voice) parts[2] = "Voice: " + (VOICE_LABELS[MS.state.voice] || MS.state.voice); parts.push((MS.state.aiResult && MS.state.aiResult.lyrics) || MS.state.lyrics || "(no lyrics yet)"); if (MS.state.aiResult && MS.state.aiResult.arrangement) { parts.push(""); parts.push("ARRANGEMENT"); parts.push(MS.state.aiResult.arrangement); } var blob = new Blob([parts.join("\n")], { type: "text/plain;charset=utf-8" }); var url = URL.createObjectURL(blob); var a = document.createElement("a"); a.href = url; a.download = title.replace(/[\\/:*?"<>|]+/g, "_") + ".txt"; document.body.appendChild(a); a.click(); document.body.removeChild(a); setTimeout(function () { URL.revokeObjectURL(url); }, 4000); }

  /* ── Navigation ───────────────────────────────────────────────────── */
  function openNav(id) { if (MS.ui.activeNav === id) { MS.ui.activeNav = ""; MS.ui.openTool = ""; } else { MS.ui.activeNav = id; MS.ui.openTool = ""; } render(); }
  function openTool(id) { MS.ui.openTool = (MS.ui.openTool === id) ? "" : id; render(); }
  function setVoice(v) { MS.state.voice = v; render(); autoSaveDebounced(); }
  function setConsent(v) { MS.state.consent = !!v; render(); autoSaveDebounced(); }
  function applyEffectPresetByIdx(idx) { if (EFFECT_PRESETS[idx]) applyEffectPreset(EFFECT_PRESETS[idx]); }
  function setBeatSearch(v) { MS.ui.searchBeat = v; render(); }

  /* ── SVG Icons ────────────────────────────────────────────────────── */
  function svgPlay() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>'; }
  function svgPause() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>'; }
  function svgStop() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>'; }
  function svgMic() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg>'; }
  function svgX() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" x2="6" y1="6" y2="18"/><line x1="6" x2="18" y1="6" y2="18"/></svg>'; }

  /* ── CSS Injection (Blue Theme + Timeline) ────────────────────────── */
  function injectStyles() {
    if (document.getElementById("ms-css")) return;
    var css = [
      "#vmWsPanelMusic{position:relative;overflow:hidden;}",
      "#vmWsPanelMusic .ms-studio{display:flex;flex-direction:column;height:100%;background:#0a0a0f;color:#e2e8f0;font-family:inherit;overflow:hidden;}",
      ".ms-hdr{display:flex;align-items:center;gap:10px;padding:8px 16px;background:#111118;border-bottom:1px solid #1e1e2e;min-height:48px;flex-shrink:0;}",
      ".ms-logo{width:28px;height:28px;background:" + BLUE + ";color:#fff;font-weight:700;font-size:13px;border-radius:6px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}",
      ".ms-inp{flex:1;background:transparent;border:1px solid transparent;color:#e2e8f0;font-size:15px;font-weight:600;padding:4px 8px;border-radius:4px;outline:none;}",
      ".ms-inp:focus{border-color:" + BLUE + ";background:#16161e;}",
      ".ms-sv{font-size:11px;color:#64748b;flex-shrink:0;min-width:50px;text-align:right;}",
      ".ms-sv.saving{color:#f59e0b;}.ms-sv.saved{color:#22c55e;}",
      ".ms-ws{flex:1;overflow-y:auto;overflow-x:hidden;position:relative;background:#0d0d14;}",
      ".ms-empty{text-align:center;padding:48px 16px;color:#475569;}",
      ".ms-empty svg{width:48px;height:48px;margin-bottom:12px;opacity:.4;}",
      ".ms-empty p{margin:4px 0;font-size:13px;}",
      ".ms-timeline{position:relative;width:100%;min-height:100%;}",
      ".ms-tl-ruler{height:28px;position:relative;border-bottom:1px solid #1e1e2e;background:#111118;display:flex;align-items:flex-end;overflow:hidden;}",
      ".ms-tl-ruler-mark{position:absolute;font-size:10px;color:#475569;bottom:4px;transform:translateX(-50%);white-space:nowrap;}",
      ".ms-tl-lane{display:flex;border-bottom:1px solid #1a1a28;min-height:56px;}",
      ".ms-tl-lane:hover{background:rgba(59,130,246,.03);}",
      ".ms-tl-lane-hdr{width:110px;flex-shrink:0;display:flex;flex-direction:column;justify-content:center;padding:4px 8px;border-right:1px solid #1e1e2e;background:#111118;gap:3px;}",
      ".ms-tl-lane-nm{font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",
      ".ms-tl-lane-meta{font-size:9px;color:#475569;}",
      ".ms-tl-lane-ctrl{display:flex;align-items:center;gap:3px;}",
      ".ms-tl-lane-ctrl button{width:20px;height:20px;border-radius:4px;border:1px solid #2a2a3a;background:#0f0f18;color:#64748b;font-size:8px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;line-height:1;}",
      ".ms-tl-lane-ctrl button.on{background:" + BLUE + ";color:#fff;border-color:" + BLUE + ";}",
      ".ms-tl-lane-ctrl input[type=range]{width:50px;height:3px;accent-color:" + BLUE + ";}",
      ".ms-tl-lane-body{flex:1;position:relative;overflow:hidden;cursor:pointer;}",
      ".ms-tl-wave{width:100%;height:100%;display:block;}",
      ".ms-tl-playhead{position:absolute;top:0;bottom:0;width:2px;background:" + BLUE + ";z-index:5;pointer-events:none;left:0%;box-shadow:0 0 6px " + BLUE_DIM + ";}",
      ".ms-tl-noaudio{display:flex;align-items:center;justify-content:center;height:100%;color:#2a2a3a;font-size:11px;}",
      ".ms-tr{display:flex;align-items:center;gap:6px;padding:6px 16px;background:#111118;border-top:1px solid #1e1e2e;flex-shrink:0;}",
      ".ms-tr button{width:36px;height:36px;border-radius:50%;border:none;background:#1e1e2e;color:#e2e8f0;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s;}",
      ".ms-tr button:hover{background:#2a2a3a;}",
      ".ms-tr button.rec{background:#ef4444;color:#fff;animation:ms-pulse 1s infinite;}",
      "@keyframes ms-pulse{0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.4)}50%{box-shadow:0 0 0 10px rgba(239,68,68,0)}}",
      ".ms-seek{flex:1;height:6px;background:#1e1e2e;border-radius:3px;position:relative;cursor:pointer;}",
      ".ms-seek-fill{height:100%;background:" + BLUE + ";border-radius:3px;width:0%;transition:width .1s linear;pointer-events:none;}",
      ".ms-tm{font-size:11px;color:#64748b;font-variant-numeric:tabular-nums;min-width:80px;text-align:center;white-space:nowrap;}",
      ".ms-vol{display:flex;align-items:center;gap:4px;}",
      ".ms-vol input[type=range]{width:50px;accent-color:" + BLUE + ";}",
      ".ms-bn{display:flex;background:#111118;border-top:1px solid #1e1e2e;flex-shrink:0;}",
      ".ms-bn button{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:8px 0;border:none;background:transparent;color:#64748b;font-size:10px;cursor:pointer;transition:color .15s;}",
      ".ms-bn button.on{color:" + BLUE + ";}",
      ".ms-bn button svg{width:20px;height:20px;}",
      ".ms-pnl{position:absolute;bottom:56px;left:0;right:0;background:#111118;border-top:1px solid #1e1e2e;transform:translateY(110%);transition:transform .25s ease;z-index:10;max-height:60vh;display:flex;flex-direction:column;}",
      ".ms-pnl.open{transform:translateY(0);}",
      ".ms-pnl-h{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-bottom:1px solid #1e1e2e;flex-shrink:0;}",
      ".ms-pnl-h h3{font-size:14px;font-weight:600;margin:0;}",
      ".ms-pnl-x{background:none;border:none;color:#64748b;cursor:pointer;padding:4px;}",
      ".ms-pnl-b{flex:1;overflow-y:auto;padding:12px 16px;}",
      ".ms-sn{display:flex;gap:4px;padding:8px 12px;overflow-x:auto;flex-shrink:0;border-bottom:1px solid #1e1e2e;}",
      ".ms-sn button{flex-shrink:0;padding:6px 10px;border-radius:6px;border:1px solid #2a2a3a;background:transparent;color:#94a3b8;font-size:11px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:4px;transition:all .12s;}",
      ".ms-sn button.on{background:" + BLUE + ";color:#fff;border-color:" + BLUE + ";}",
      ".ms-sn button svg{width:14px;height:14px;}",
      ".ms-lb{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin:12px 0 6px;font-weight:600;}",
      ".ms-fi{width:100%;background:#0f0f18;border:1px solid #1e1e2e;color:#e2e8f0;padding:8px 10px;border-radius:6px;font-size:13px;outline:none;box-sizing:border-box;}",
      ".ms-fi:focus{border-color:" + BLUE + ";}",
      "textarea.ms-fi{resize:vertical;min-height:60px;font-family:inherit;}",
      "select.ms-fi{appearance:none;background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\");background-repeat:no-repeat;background-position:right 8px center;padding-right:28px;}",
      ".ms-rw{display:flex;gap:8px;margin-bottom:10px;}.ms-rw>*{flex:1;}",
      ".ms-btn{padding:8px 14px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;}",
      ".ms-btn.pri{background:" + BLUE + ";color:#fff;}.ms-btn.pri:hover{background:" + BLUE_HV + ";}",
      ".ms-btn.sec{background:#1e1e2e;color:#e2e8f0;}.ms-btn.sec:hover{background:#2a2a3a;}",
      ".ms-btn.dng{background:#dc2626;color:#fff;}",
      ".ms-btn.sm{padding:5px 10px;font-size:11px;}",
      ".ms-tog{display:flex;align-items:center;gap:8px;cursor:pointer;}",
      ".ms-tog input[type=checkbox]{accent-color:" + BLUE + ";width:16px;height:16px;}",
      ".ms-tog span{font-size:12px;}",
      ".ms-chip{display:inline-flex;padding:6px 10px;border-radius:6px;border:1px solid #2a2a3a;background:#0f0f18;color:#94a3b8;font-size:11px;cursor:pointer;margin:0 4px 6px 0;transition:all .12s;}",
      ".ms-chip.on{background:" + BLUE + ";color:#fff;border-color:" + BLUE + ";}",
      ".ms-slr{display:flex;align-items:center;gap:8px;margin-bottom:8px;}",
      ".ms-slr label{font-size:12px;color:#94a3b8;min-width:70px;}",
      ".ms-slr input[type=range]{flex:1;accent-color:" + BLUE + ";}",
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
      ".ms-consent input{margin-top:2px;accent-color:" + BLUE + ";}",
      ".ms-vc{background:#14141f;border:1px solid #1e1e2e;border-radius:8px;padding:10px;margin-bottom:8px;cursor:pointer;transition:all .12s;}",
      ".ms-vc.on{border-color:" + BLUE + ";background:rgba(59,130,246,.08);}",
      ".ms-vc .vn{font-size:13px;font-weight:600;}.ms-vc .vs{font-size:11px;color:#64748b;margin-top:2px;}",
      ".ms-mi{background:#0f0f18;border-radius:6px;padding:8px;margin-bottom:6px;font-size:12px;}",
      ".ms-mi .mt{color:#64748b;font-size:10px;margin-top:2px;}",
      ".ms-res{background:#0f0f18;border-radius:8px;padding:12px;font-size:12px;white-space:pre-wrap;max-height:200px;overflow-y:auto;line-height:1.5;}",
      ".ms-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;}",
      ".ms-beat{background:#14141f;border:1px solid #1e1e2e;border-radius:8px;padding:10px;cursor:pointer;transition:all .15s;}",
      ".ms-beat:hover{border-color:" + BLUE_LT + ";}.ms-beat.on{border-color:" + BLUE + ";background:rgba(59,130,246,.06);}",
      ".ms-beat-ct{font-size:12px;font-weight:600;margin-bottom:2px;}",
      ".ms-beat-mt{font-size:10px;color:#64748b;}",
      ".ms-beat-g{font-size:9px;color:#475569;margin-top:1px;}",
      ".ms-beat-act{display:flex;gap:4px;margin-top:6px;align-items:center;}",
      ".ms-beat-dur{font-size:9px;color:" + BLUE_LT + ";margin-left:auto;}",
      ".ms-se{width:100%;background:#0f0f18;border:1px solid #1e1e2e;color:#e2e8f0;padding:6px 10px;border-radius:6px;font-size:12px;outline:none;margin-bottom:10px;box-sizing:border-box;}",
      ".ms-se:focus{border-color:" + BLUE + ";}",
      ".ms-lyr{width:100%;min-height:200px;background:#0f0f18;border:1px solid #1e1e2e;color:#e2e8f0;padding:12px;border-radius:8px;font-size:14px;font-family:inherit;line-height:1.8;outline:none;resize:vertical;box-sizing:border-box;}",
      ".ms-lyr:focus{border-color:" + BLUE + ";}",
      "@media(min-width:769px){",
      ".ms-pnl{left:auto;width:380px;max-height:100%;border-top:none;border-left:1px solid #1e1e2e;bottom:0;top:48px;transform:translateX(100%);}",
      ".ms-pnl.open{transform:translateX(0);}",
      ".ms-grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr));}",
      ".ms-tl-lane-hdr{width:130px;}",
      "}"
    ].join("\n");
    var s = document.createElement("style");
    s.id = "ms-css";
    s.textContent = css;
    document.head.appendChild(s);
  }

  /* ── Timeline / Workspace Renderer ────────────────────────────────── */
  function renderWorkspace() {
    var s = MS.state;
    var hasTake = s.take && s.take.url;
    var hasBeat = s.beat && s.beat.url;
    var hasLayers = s.layers && s.layers.length;
    if (!hasTake && !hasBeat && !hasLayers) {
      return '<div class="ms-empty"><svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>' +
        '<p style="font-size:15px;font-weight:600;margin-top:8px;">Start your song</p>' +
        '<p>Record, upload, or use Create to generate with AI.</p></div>';
    }
    var dur = 0;
    if (hasTake) dur = Math.max(dur, s.take.dur || 0);
    if (hasBeat) dur = Math.max(dur, s.beat.dur || 0);
    s.layers.forEach(function (l) { if (l.url) dur = Math.max(dur, l.dur || 0); });
    if (s.beatPreset) dur = Math.max(dur, 45 * 60);
    if (dur === 0) dur = 60;

    var rulerMarks = "";
    var interval = dur > 600 ? 60 : (dur > 120 ? 30 : 10);
    for (var t = 0; t <= dur; t += interval) {
      var pct = (t / dur) * 100;
      rulerMarks += '<div class="ms-tl-ruler-mark" style="left:' + pct + '%">' + fmtTime(t) + '</div>';
    }

    var lanes = "";
    if (hasTake) lanes += renderLane("Vocal", "take", s.take, BLUE);
    if (hasBeat) lanes += renderLane(s.beat.name || "Beat", "beat", s.beat, "#8b5cf6");
    s.layers.forEach(function (l) { if (l.url) lanes += renderLane(l.name, "layer_" + l.id, l, "#06b6d4"); });

    var playheadPct = 0;
    if (MS.audio.playing && dur > 0) playheadPct = Math.min(100, (MS.audio.position / dur) * 100);

    return '<div class="ms-timeline">' +
      '<div class="ms-tl-ruler" onclick="VMMusic.seekFromTimeline(event)">' + rulerMarks + '</div>' +
      '<div class="ms-tl-tracks">' + lanes + '</div>' +
      '<div class="ms-tl-playhead" id="msPlayhead" style="left:' + playheadPct + '%"></div>' +
      '</div>';
  }
  function renderLane(name, kind, track, color) {
    var muted = trackMuted(track);
    var solo = track.solo, mute = track.mute;
    return '<div class="ms-tl-lane">' +
      '<div class="ms-tl-lane-hdr">' +
      '<div class="ms-tl-lane-nm" style="color:' + color + '">' + esc(name) + '</div>' +
      '<div class="ms-tl-lane-meta">' + fmtTime(track.dur) + (s.beatPreset && kind === "beat" ? " (loop)" : "") + '</div>' +
      '<div class="ms-tl-lane-ctrl">' +
      '<button' + (solo ? ' class="on"' : '') + ' onclick="VMMusic.setTrackProp(\'' + kind.replace("layer_", "layer:") + '\',\'solo\',' + (!solo) + ')" title="Solo">S</button>' +
      '<button' + (mute ? ' class="on"' : '') + ' onclick="VMMusic.setTrackProp(\'' + kind.replace("layer_", "layer:") + '\',\'mute\',' + (!mute) + ')" title="Mute">M</button>' +
      '<input type="range" min="0" max="100" value="' + track.vol + '" oninput="VMMusic.setTrackVol(\'' + kind.replace("layer_", "layer:") + '\',this.value)">' +
      '</div></div>' +
      '<div class="ms-tl-lane-body">' +
      (MS.audio.peaks[kind] ? '<canvas class="ms-tl-wave" data-kind="' + kind + '"></canvas>' : '<div class="ms-tl-noaudio">Loading waveform...</div>') +
      '</div></div>';
  }
  function seekFromTimeline(e) {
    var rect = e.currentTarget.getBoundingClientRect();
    var pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    if (!MS.audio.playing) {
      var kinds = [];
      if (MS.state.take.url) kinds.push("take");
      if (MS.state.beat.url) kinds.push("beat");
      if (kinds.length) togglePlay(kinds[0]);
    }
    seekTo(pct);
  }

  /* ── Panel Content Renderers ───────────────────────────────────────── */
  function panelLabel(id) { var m = { record: "Record", tracks: "Tracks", generate: "Create", tools: "Tools", projects: "Projects" }; return m[id] || ""; }
  function renderRecordPanel() {
    var s = MS.state;
    var isRec = MS.recorder && MS.recorder.state === "recording";
    var h = '<div class="ms-rec-big' + (isRec ? ' rec' : '') + '" onclick="VMMusic.toggleRecord()">';
    h += isRec ? '<svg width="32" height="32" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>' : svgMic();
    h += '</div>';
    h += '<div style="text-align:center;font-size:20px;font-variant-numeric:tabular-nums;margin-bottom:16px;" id="msRecTime">' + fmtTime(MS.elapsed) + '</div>';
    h += '<div class="ms-lb">Upload vocals</div>';
    h += '<input type="file" accept="audio/*" style="display:none" id="msTakeFile" onchange="VMMusic.onTakeFile(this)">';
    h += '<button class="ms-btn sec" onclick="document.getElementById(\'msTakeFile\').click()">Upload vocal</button>';
    h += '<div class="ms-lb">Voice</div>';
    ["keep", "clone", "elena"].forEach(function (v) {
      h += '<div class="ms-vc' + (s.voice === v ? ' on' : '') + '" onclick="VMMusic.setVoice(\'' + v + '\')">' +
        '<div class="vn">' + esc(VOICE_LABELS[v]) + '</div><div class="vs">' + esc(VOICE_SUBS[v]) + '</div></div>';
    });
    h += '<div class="ms-consent"><label><input type="checkbox" ' + (s.consent ? 'checked' : '') + ' onchange="VMMusic.setConsent(this.checked)">I authorize ValleyMind to process my voice for this song.</label></div>';
    return h;
  }
  function renderTracksPanel() {
    var s = MS.state; var h = '';
    var tracks = [];
    if (s.take && s.take.url) tracks.push({ label: "Vocals", kind: "take", t: s.take, color: BLUE });
    if (s.beat && s.beat.url) tracks.push({ label: s.beat.name || "Beat", kind: "beat", t: s.beat, color: "#8b5cf6" });
    s.layers.forEach(function (l) { if (l.url) tracks.push({ label: l.name, kind: "layer_" + l.id, t: l, color: "#06b6d4" }); });
    if (!tracks.length) { h += '<p style="color:#475569;font-size:13px;">No tracks loaded. Record or upload audio.</p>'; }
    tracks.forEach(function (tr) {
      h += '<div class="ms-pc"><div class="ms-pc-info"><div class="ms-pc-nm" style="color:' + tr.color + '">' + esc(tr.label) + '</div><div class="ms-pc-mt">' + fmtTime(tr.t.dur) + '</div></div>';
      h += '<div style="display:flex;align-items:center;gap:6px;"><input type="range" min="0" max="100" value="' + tr.t.vol + '" style="width:60px;accent-color:' + BLUE + '" oninput="VMMusic.setTrackVol(\'' + tr.kind.replace("layer_", "layer:") + '\',this.value)">';
      h += '<button style="width:22px;height:22px;border-radius:4px;border:1px solid #2a2a3a;background:' + (tr.t.solo ? BLUE : '#0f0f18') + ';color:' + (tr.t.solo ? '#fff' : '#64748b') + ';font-size:9px;font-weight:700;cursor:pointer" onclick="VMMusic.setTrackProp(\'' + tr.kind.replace("layer_", "layer:") + '\',\'solo\',' + (!tr.t.solo) + ')">S</button>';
      h += '<button style="width:22px;height:22px;border-radius:4px;border:1px solid #2a2a3a;background:' + (tr.t.mute ? BLUE : '#0f0f18') + ';color:' + (tr.t.mute ? '#fff' : '#64748b') + ';font-size:9px;font-weight:700;cursor:pointer" onclick="VMMusic.setTrackProp(\'' + tr.kind.replace("layer_", "layer:") + '\',\'mute\',' + (!tr.t.mute) + ')">M</button>';
      h += '</div></div>';
    });
    h += '<div style="margin-top:12px;display:flex;gap:8px"><input type="file" accept="audio/*" style="display:none" id="msLayerFile" onchange="VMMusic.onLayerFile(this)"><button class="ms-btn sec" onclick="document.getElementById(\'msLayerFile\').click()">+ Add layer</button></div>';
    return h;
  }
  function renderCreatePanel() {
    var s = MS.state;
    var h = '<div class="ms-lb">Song description</div>';
    h += '<textarea class="ms-fi" id="vmMusicBrief" rows="3" placeholder="Describe your song...">' + esc(s.brief) + '</textarea>';
    h += '<div class="ms-lb" style="margin-top:8px">Lyrics</div>';
    h += '<textarea class="ms-lyr" id="vmMusicLyrics" placeholder="Type your lyrics...">' + esc(s.lyrics) + '</textarea>';
    h += '<div class="ms-rw" style="margin-top:10px"><div><div class="ms-lb">Genre</div><select class="ms-fi" id="vmMusicGenre">';
    GENRES.forEach(function (g) { h += '<option' + (s.genre === g ? ' selected' : '') + '>' + g + '</option>'; });
    h += '</select></div><div><div class="ms-lb">Mood</div><select class="ms-fi" id="vmMusicMood">';
    MOODS.forEach(function (m) { h += '<option' + (s.mood === m ? ' selected' : '') + '>' + m + '</option>'; });
    h += '</select></div></div>';
    h += '<div class="ms-rw"><div><div class="ms-lb">Tempo</div><select class="ms-fi" id="vmMusicTempo">';
    TEMPOS.forEach(function (t) { h += '<option' + (s.tempo === t ? ' selected' : '') + '>' + t + '</option>'; });
    h += '</select></div><div><div class="ms-lb">Key</div><input class="ms-fi" id="vmMusicKey" value="' + esc(s.key) + '" placeholder="e.g. C minor"></div></div>';
    h += '<div class="ms-rw"><div><div class="ms-lb">Role</div><select class="ms-fi" id="vmMusicRole">';
    ROLES.forEach(function (r) { h += '<option' + (s.role === r ? ' selected' : '') + '>' + r + '</option>'; });
    h += '</select></div><div><div class="ms-lb">Language</div><input class="ms-fi" id="vmMusicLanguage" value="' + esc(s.language) + '"></div></div>';
    h += '<button class="ms-btn pri" style="width:100%;margin-top:8px" onclick="VMMusic.runAI()">Generate with AI</button>';
    if (s.aiResult) {
      h += '<div class="ms-lb" style="margin-top:16px">AI Result</div>';
      h += '<div class="ms-mi" style="border:1px solid ' + BLUE_DIM + '"><div class="mt" style="color:' + BLUE_LT + '">GENERATED PACKAGE</div>';
      if (s.aiResult.lyrics) h += '<div style="white-space:pre-wrap;margin-top:4px;max-height:120px;overflow-y:auto">' + esc(s.aiResult.lyrics.substring(0, 400)) + (s.aiResult.lyrics.length > 400 ? '...' : '') + '</div>';
      h += '</div>';
    }
    return h;
  }
  function renderProjectsPanel() {
    var h = '<div style="display:flex;gap:8px;margin-bottom:12px"><button class="ms-btn pri" onclick="VMMusic.newSong()">+ New song</button></div>';
    if (!MS.projects.length) h += '<p style="color:#475569;font-size:13px;">No saved projects yet.</p>';
    MS.projects.forEach(function (p) {
      h += '<div class="ms-pc"><div class="ms-pc-info"><div class="ms-pc-nm">' + esc(p.name || 'Untitled') + '</div>';
      h += '<div class="ms-pc-mt">' + esc(p.genre || '') + ' \u00b7 ' + esc(p.mood || '') + (p.savedAt ? ' \u00b7 ' + new Date(p.savedAt).toLocaleDateString() : '') + '</div></div>';
      h += '<div class="ms-pc-btns"><button class="ms-btn sm sec" onclick="VMMusic.loadSong(\'' + p.id + '\')">Load</button><button class="ms-btn sm dng" onclick="VMMusic.deleteSong(\'' + p.id + '\')">&#10005;</button></div></div>';
    });
    return h;
  }

  /* ── Tool Sub-Panel Renderers ──────────────────────────────────────── */
  function renderToolsSubnav(active) {
    var h = '<div class="ms-sn">';
    TOOLS.forEach(function (t) { h += '<button' + (active === t.id ? ' class="on"' : '') + ' onclick="VMMusic.openTool(\'' + t.id + '\')"><i data-lucide="' + t.icon + '"></i>' + t.label + '</button>'; });
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
    var s = MS.state; var h = '<div class="ms-lb">Voice Type</div>';
    ["keep", "clone", "elena"].forEach(function (v) {
      h += '<div class="ms-vc' + (s.voice === v ? ' on' : '') + '" onclick="VMMusic.setVoice(\'' + v + '\')">';
      h += '<div class="vn">' + esc(VOICE_LABELS[v]) + '</div><div class="vs">' + esc(VOICE_SUBS[v]) + '</div></div>';
    });
    h += '<div class="ms-consent"><label><input type="checkbox" ' + (s.consent ? 'checked' : '') + ' onchange="VMMusic.setConsent(this.checked)">I authorize ValleyMind to process my voice.</label></div>';
    return h;
  }
  function renderBeatsSub() {
    var search = (MS.ui.searchBeat || "").toLowerCase();
    var filtered = BEAT_PRESETS;
    if (search) filtered = BEAT_PRESETS.filter(function (b) { return (b.city + " " + b.genre + " " + b.mood + " " + b.desc).toLowerCase().indexOf(search) !== -1; });
    var h = '<input class="ms-se" placeholder="Search beats by name, genre, mood..." value="' + esc(MS.ui.searchBeat || "") + '" oninput="VMMusic.setBeatSearch(this.value)">';
    h += '<div style="font-size:11px;color:#475569;margin-bottom:8px">' + filtered.length + ' beats' + (search ? ' found' : ' total') + ' \u00b7 Each generates real audio via Web Audio synthesis \u00b7 Loops to 45 min</div>';
    h += '<div class="ms-grid">';
    filtered.forEach(function (b) {
      var active = MS.state.beatPreset === b.id;
      var previewing = MS.previewPreset === b.id;
      h += '<div class="ms-beat' + (active ? ' on' : '') + '" onclick="VMMusic.selectBeat(\'' + b.id + '\')">';
      h += '<div class="ms-beat-ct">' + esc(b.city) + '</div>';
      h += '<div class="ms-beat-mt">' + b.bpm + ' BPM \u00b7 ' + b.mood + '</div>';
      h += '<div class="ms-beat-g">' + esc(b.genre) + ' \u00b7 ' + esc(b.note) + '</div>';
      h += '<div class="ms-beat-act"><button class="ms-btn sm sec" onclick="event.stopPropagation();VMMusic.previewBeat(\'' + b.id + '\')">' + (previewing ? 'Stop' : 'Preview') + '</button>';
      h += '<span class="ms-beat-dur">45:00</span></div></div>';
    });
    h += '</div><div class="ms-lb">Upload beat</div>';
    h += '<input type="file" accept="audio/*" style="display:none" id="msBeatFile" onchange="VMMusic.onBeatFile(this)">';
    h += '<button class="ms-btn sec" onclick="document.getElementById(\'msBeatFile\').click()">Upload your own beat</button>';
    return h;
  }
  function renderInstrSub() {
    var s = MS.state;
    if (!s.aiResult || !s.aiResult.arrangement) return '<p style="color:#475569;font-size:13px">Generate music first to see AI instrumentation.</p>';
    return '<div class="ms-lb">AI Arrangement</div><div class="ms-res">' + esc(s.aiResult.arrangement) + '</div>';
  }
  function renderLyricsSub() {
    var h = '<div class="ms-lb">Type your lyrics</div>';
    h += '<textarea class="ms-lyr" id="vmMusicLyrics" placeholder="Type your lyrics...">' + esc(MS.state.lyrics) + '</textarea>';
    h += '<div style="margin-top:8px;display:flex;gap:8px"><button class="ms-btn sec" onclick="VMMusic.exportSong()">Export</button></div>';
    return h;
  }
  function renderFxSub() {
    var s = MS.state; var h = '<div class="ms-lb">Presets</div>';
    EFFECT_PRESETS.forEach(function (p, i) {
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
    var s = MS.state; var h = '<div class="ms-lb">Master Volume</div>';
    h += '<div class="ms-slr"><label>Master</label><input type="range" min="0" max="100" value="' + s.mix.master + '" oninput="VMMusic.setMaster(this.value)"><span>' + s.mix.master + '</span></div>';
    h += '<div style="margin-top:12px;display:flex;gap:8px"><button class="ms-btn sec" onclick="VMMusic.autoMix()">Auto Mix</button><button class="ms-btn sec" onclick="VMMusic.autoMaster()">Auto Master</button></div>';
    h += '<div class="ms-lb" style="margin-top:16px">Track Levels</div>';
    if (s.take && s.take.url) h += '<div class="ms-slr"><label>Vocal</label><input type="range" min="0" max="100" value="' + s.take.vol + '" oninput="VMMusic.setTrackVol(\'take\',this.value)"><span>' + s.take.vol + '</span></div>';
    if (s.beat && s.beat.url) h += '<div class="ms-slr"><label>Beat</label><input type="range" min="0" max="100" value="' + s.beat.vol + '" oninput="VMMusic.setTrackVol(\'beat\',this.value)"><span>' + s.beat.vol + '</span></div>';
    s.layers.forEach(function (l) { h += '<div class="ms-slr"><label>' + esc(l.name) + '</label><input type="range" min="0" max="100" value="' + l.vol + '" oninput="VMMusic.setLayerVol(\'' + l.id + '\',this.value)"><span>' + l.vol + '</span></div>'; });
    return h;
  }
  function renderAiEditSub() {
    var h = '<div class="ms-lb">AI Edit Instruction</div>';
    h += '<textarea class="ms-fi" id="msAiEditInput" rows="3" placeholder="e.g. Make the chorus more upbeat, add a bridge..."></textarea>';
    h += '<button class="ms-btn pri" style="width:100%;margin-top:8px" onclick="VMMusic.runAiEdit()">Apply changes</button>';
    if (MS.state.lastAiEdit) { h += '<div class="ms-lb" style="margin-top:16px">Last edit</div>'; h += '<div class="ms-mi">' + esc(MS.state.lastAiEdit.instruction) + '<div class="mt">' + esc(MS.state.lastAiEdit.summary || '') + '</div></div>'; }
    return h;
  }
  function renderMemSub() {
    var h = '<div class="ms-lb">Session Memory</div>';
    if (!MS.memory.length) h += '<p style="color:#475569;font-size:13px">No memory entries yet.</p>';
    MS.memory.forEach(function (m) { h += '<div class="ms-mi">' + esc(m.type || '') + ': ' + esc(m.instruction || m.name || m.summary || ''); h += '<div class="mt">' + new Date(m.date || 0).toLocaleString() + '</div></div>'; });
    return h;
  }
  function renderAssetsSub() {
    var s = MS.state; var h = '<div class="ms-lb">Audio Assets</div>';
    var assets = [];
    if (s.take && s.take.url) assets.push({ name: s.take.name || "Vocal", dur: s.take.dur });
    if (s.beat && s.beat.url) assets.push({ name: s.beat.name || "Beat", dur: s.beat.dur });
    s.layers.forEach(function (l) { if (l.url) assets.push({ name: l.name, dur: l.dur }); });
    if (!assets.length) h += '<p style="color:#475569;font-size:13px">No audio files yet.</p>';
    assets.forEach(function (a) { h += '<div class="ms-pc"><div class="ms-pc-info"><div class="ms-pc-nm">' + esc(a.name) + '</div><div class="ms-pc-mt">' + fmtTime(a.dur) + '</div></div></div>'; });
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
    var isPlaying = MS.audio.el && !MS.audio.el.paused && MS.audio.playing;
    var svCls = MS.ui.saveState === "Saving..." ? " saving" : (MS.ui.saveState === "Saved" ? " saved" : "");
    var wsHtml = renderWorkspace();
    var pnlHtml = "";
    if (nav) {
      pnlHtml = '<div class="ms-pnl open">';
      pnlHtml += '<div class="ms-pnl-h"><h3>' + esc(panelLabel(nav)) + '</h3>';
      pnlHtml += '<button class="ms-pnl-x" onclick="VMMusic.openNav(\'\')">' + svgX() + '</button></div>';
      if (nav === "tools") { pnlHtml += renderToolsSubnav(tool); pnlHtml += '<div class="ms-pnl-b">' + renderToolContent(tool) + '</div>'; }
      else {
        var body = "";
        if (nav === "record") body = renderRecordPanel();
        else if (nav === "tracks") body = renderTracksPanel();
        else if (nav === "generate") body = renderCreatePanel();
        else if (nav === "projects") body = renderProjectsPanel();
        pnlHtml += '<div class="ms-pnl-b">' + body + '</div>';
      }
      pnlHtml += '</div>';
    }
    var dur = getEffectiveDur(MS.audio.playing) || 0;
    var pos = MS.audio.position || 0;
    panel.innerHTML = '<div class="ms-studio">' +
      '<div class="ms-hdr"><div class="ms-logo">V</div>' +
      '<input class="ms-inp" id="vmMusicName" value="' + esc(s.name) + '">' +
      '<span class="ms-sv' + svCls + '" id="msSaveStatus">' + esc(MS.ui.saveState) + '</span></div>' +
      '<div class="ms-ws" id="msWorkspace">' + wsHtml + '</div>' +
      '<div class="ms-tr">' +
      '<button class="' + (isRec ? 'rec' : '') + '" onclick="VMMusic.toggleRecord()" title="Record">' + svgMic() + '</button>' +
      '<button id="msPlayBtn" onclick="VMMusic.togglePlay(' + (MS.audio.playing ? "'" + MS.audio.playing + "'" : "'take'") + ')" title="Play/Pause">' + (isPlaying ? svgPause() : svgPlay()) + '</button>' +
      '<button onclick="VMMusic.stopPlayback()" title="Stop">' + svgStop() + '</button>' +
      '<div class="ms-seek" onclick="VMMusic.seekFromBar(event)" id="msSeekBar"><div class="ms-seek-fill" id="msSeekFill" style="width:' + (dur > 0 ? (pos / dur * 100) : 0) + '%"></div></div>' +
      '<span class="ms-tm" id="msPlayTime">' + fmtTime(pos) + ' / ' + fmtTime(dur) + '</span>' +
      '<div class="ms-vol"><input type="range" min="0" max="100" value="' + s.mix.master + '" oninput="VMMusic.setMaster(this.value)"></div>' +
      '</div>' +
      '<div class="ms-bn">' +
      NAV.map(function (n) { return '<button' + (nav === n.id ? ' class="on"' : '') + ' onclick="VMMusic.openNav(\'' + n.id + '\')"><i data-lucide="' + n.icon + '"></i>' + n.label + '</button>'; }).join("") +
      '</div>' + pnlHtml + '</div>';
    refreshLucide();
    requestAnimationFrame(function () { drawAllWaveforms(); });
  }
  function seekFromBar(e) {
    var bar = document.getElementById("msSeekBar"); if (!bar) return;
    var rect = bar.getBoundingClientRect();
    var pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    if (!MS.audio.playing) { togglePlay("take"); }
    seekTo(pct);
  }

  /* ── API Surface ───────────────────────────────────────────────────── */
  window.VMMusic = {
    render: render, openNav: openNav, openTool: openTool,
    toggleRecord: toggleRecord, stopRecord: stopRecord,
    togglePlay: togglePlay, pausePlayback: pausePlayback, stopPlayback: stopPlayback, seekTo: seekTo,
    seekFromBar: seekFromBar, seekFromTimeline: seekFromTimeline,
    setTrackVol: function (kind, val) { var k = kind.indexOf("layer:") === 0 ? "layer_" + kind.substring(6) : kind; setTrack(k === "take" ? "take" : "beat", "vol", val); },
    setTrackProp: function (kind, prop, val) { var k = kind.indexOf("layer:") === 0 ? "layer_" + kind.substring(6) : kind; if (k.indexOf("layer_") === 0) setLayer(k.substring(6), prop, val); else setTrack(k, prop, val); },
    setLayerVol: function (id, val) { setLayer(id, "vol", val); },
    removeLayer: removeLayer,
    onTakeFile: onTakeFile, onBeatFile: onBeatFile, onLayerFile: onLayerFile,
    setVoice: setVoice, setConsent: setConsent,
    setFx: setFx, applyFx: applyEffectPresetByIdx,
    setMaster: setMaster, autoMix: autoMix, autoMaster: autoMaster,
    runAI: runAI, runAiEdit: runAiEdit,
    previewBeat: previewBeat, stopPreview: stopPreview, selectBeat: selectBeat,
    setBeatSearch: setBeatSearch,
    newSong: newSong, loadSong: loadSong, deleteSong: deleteSong, saveSong: saveSong, exportSong: exportSong
  };

  /* ── Show hook (wired to index.html vmWsGo("music")) ───────────────── */
  function onShow() {
    render();
  }
  window.vmMusicOnShow = onShow;
  if (window.VMMusic) window.VMMusic.onShow = onShow;

  /* ── Init ──────────────────────────────────────────────────────────── */
  function init() {
    injectStyles();
    loadProjects(); loadMemory();
    MS.state = defaultState();
    fetchProjectsFromServer();
    initAudioEl();
    render();
  }
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); } else { init(); }
})();
