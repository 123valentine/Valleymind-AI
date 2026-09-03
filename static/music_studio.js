/* ValleyMind Music Studio � Professional DAW Workspace
   ----------------------------------------------------
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
  var CYAN = "#00e5ff";
  var CYAN_TEAL = "#00f0c8";

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
  var VOICE_SUBS = { keep: "Cleans, tunes and enhances your recording.", clone: "An AI model of your voice � requires authorization.", elena: "ValleyMind's approved AI singing voice." };
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

  /* -- 110+ Beat Presets � real synthesis configs ---------------------- */
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
    return { id: "b" + i, no: i + 1, city: r[0], bpm: r[1], note: r[2], mood: r[3], genre: r[4], desc: r[5], pattern: r[6], type: beatCategory(r[4]) };
  });

  /* -- Beat type categories ("lock" groups) --------------------------- */
  var BEAT_TYPES = ["Afrobeats", "Pop", "Amapiano", "Dancehall", "R&B", "Hip-Hop", "Soul", "Gospel", "Highlife", "Reggae", "Electronic", "Folk", "Jazz"];
  function beatCategory(genre) {
    var g = String(genre).toLowerCase();
    if (g.indexOf("afro") !== -1) return "Afrobeats";
    if (g === "pop") return "Pop";
    if (g === "amapiano") return "Amapiano";
    if (g === "dancehall") return "Dancehall";
    if (g === "r&b" || g === "rnb") return "R&B";
    if (g === "hip-hop" || g === "hip hop") return "Hip-Hop";
    if (g === "soul") return "Soul";
    if (g === "gospel") return "Gospel";
    if (g === "highlife") return "Highlife";
    if (g === "reggae") return "Reggae";
    if (g === "electronic") return "Electronic";
    if (g === "jazz") return "Jazz";
    return "Folk";
  }
  var BEAT_MOODS = ["Romantic", "Upbeat", "Hopeful", "Energetic", "Chill", "Melancholic", "Bittersweet", "Empowering", "Nostalgic"];

  /* -- Genre sound kits � give every type a unique, punchy voicing ---- */
  var GENRE_KIT = {
    Afrobeats: { kit: "afro", swing: 0.03, hat16: 0.5, perc: true, bassStyle: "log", fillEvery: 4 },
    Pop:       { kit: "pop",  swing: 0.0,  hat16: 0.75, perc: true,  bassStyle: "pop", fillEvery: 4 },
    Amapiano:  { kit: "ama",  swing: 0.08, hat16: 0.4,  perc: false, bassStyle: "log", fillEvery: 8 },
    Dancehall: { kit: "dh",   swing: 0.05, hat16: 0.5,  perc: true,  bassStyle: "dh",  fillEvery: 4 },
    "R&B":     { kit: "rnb",  swing: 0.06, hat16: 0.35, perc: false, bassStyle: "rnb", fillEvery: 8 },
    "Hip-Hop": { kit: "hip",  swing: 0.03, hat16: 0.4,  perc: false, bassStyle: "hip", fillEvery: 8 },
    Soul:      { kit: "soul", swing: 0.05, hat16: 0.3,  perc: false, bassStyle: "rnb", fillEvery: 8 },
    Gospel:    { kit: "pop",  swing: 0.0,  hat16: 0.6,  perc: true,  bassStyle: "pop", fillEvery: 4 },
    Highlife:  { kit: "hl",   swing: 0.04, hat16: 0.4,  perc: true,  bassStyle: "afro", fillEvery: 4 },
    Reggae:    { kit: "reggae", swing: 0.06, hat16: 0.35, perc: false, bassStyle: "rnb", fillEvery: 8 },
    Electronic:{ kit: "elec", swing: 0.0,  hat16: 0.8,  perc: true,  bassStyle: "pop", fillEvery: 4 },
    Folk:      { kit: "folk", swing: 0.02, hat16: 0.25, perc: false, bassStyle: "acoustic", fillEvery: 8 },
    Jazz:      { kit: "jazz", swing: 0.1,  hat16: 0.3,  perc: false, bassStyle: "rnb", fillEvery: 8 }
  };
  function kitFor(preset) { return GENRE_KIT[preset.type] || GENRE_KIT["Pop"]; }

  /* -- Chord-friendly bass note pool (root + fifth, one octave) ------- */
  var NOTE_FREQ = (function () { var map = {}; var names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]; var flatMap = { "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#" }; var A4 = 440, A4midi = 69; for (var midi = 0; midi < 128; midi++) { var oct = Math.floor(midi / 12) - 1; var nc = names[midi % 12]; map[nc + oct] = A4 * Math.pow(2, (midi - A4midi) / 12); } function noteFreq(n) { if (map[n]) return map[n]; if (flatMap[n]) return map[flatMap[n]]; return 261.63; } return noteFreq; })();
  function bassFreq(note) { return NOTE_FREQ(note) / 2; }
  function bassRootNth(note, semis) {
    var m = mapNoteToMidi(note); var nm = m + semis; return midiToFreq(nm) / 2;
  }
  function mapNoteToMidi(n) { return Math.round(69 + 12 * Math.log(Math.max(1, NOTE_FREQ(n)) / 440) / Math.log(2)); }
  function midiToFreq(m) { return 440 * Math.pow(2, (m - 69) / 12); }

  /* -- State ---------------------------------------------------------- */
  var MS = {
    state: null,
    recorder: null, recStream: null, chunks: [], timer: null, elapsed: 0,
    projects: [], rendered: false, previewPreset: null,
    ui: { activeNav: "", openTool: "", recording: false, saveState: "", searchBeat: "", beatType: "", beatMood: "" },
    memory: [],
    live: null,
    audio: { el: null, playing: null, position: 0, dur: 0, loopDur: 0, loopBase: 0, lastTime: 0, peaks: {}, raf: null }
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
      eq: [0, 0, 0, 0],
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

  /* -- Storage -------------------------------------------------------- */
  function loadProjects() { try { var p = JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); MS.projects = (Array.isArray(p) ? p : []).map(normalizeProject); } catch (e) { MS.projects = []; } }
  function saveProjects() { try { localStorage.setItem(STORE_KEY, JSON.stringify(MS.projects)); } catch (e) { } }
  function pushProjectsToServer() { if (typeof apiFetch !== "function") return; apiFetch("/api/music/projects", { method: "POST", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ projects: MS.projects }) }).catch(function () { }); }
  function deleteProjectOnServer(id) { if (typeof apiFetch !== "function") return; apiFetch("/api/music/projects/" + encodeURIComponent(id), { method: "DELETE", credentials: "include", headers: authHeaders({}) }).catch(function () { }); }
  function fetchProjectsFromServer() { if (typeof apiFetch !== "function") return Promise.resolve(); return apiFetch("/api/music/projects", { method: "GET", credentials: "include", headers: authHeaders({}) }).then(function (r) { return r.json(); }).then(function (d) { if (!d || !Array.isArray(d.projects)) return; var server = d.projects.map(normalizeProject); var map = {}; MS.projects.forEach(function (p) { map[p.id] = p; }); server.forEach(function (p) { var mine = map[p.id]; if (!mine || (p.savedAt || 0) > (mine.savedAt || 0)) map[p.id] = p; }); MS.projects = Object.keys(map).map(function (k) { return map[k]; }); saveProjects(); render(); }).catch(function () { }); }

  /* -- Memory --------------------------------------------------------- */
  function loadMemory() { try { MS.memory = JSON.parse(localStorage.getItem(STORE_KEY + "_mem") || "[]"); } catch (e) { MS.memory = []; } }
  function saveMemory() { try { localStorage.setItem(STORE_KEY + "_mem", JSON.stringify(MS.memory)); } catch (e) { } }
  function addMemory(entry) { MS.memory.unshift(entry); if (MS.memory.length > 50) MS.memory.pop(); saveMemory(); }

  /* -- UI Helpers ----------------------------------------------------- */
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

  /* -- Recording ------------------------------------------------------ */
  function toggleRecord() {
    if (MS.recorder && MS.recorder.state === "recording") { stopRecord(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { toast("Recording not supported."); return; }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      MS.recStream = stream;
      startLiveViz(stream);
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
  function stopRecTracks() { try { if (MS.recStream) MS.recStream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { } MS.recStream = null; stopLiveViz(); }
  function stopRecord() { if (MS.recorder && MS.recorder.state === "recording") { try { MS.recorder.stop(); } catch (e) { } } clearInterval(MS.timer); MS.timer = null; MS.recorder = null; MS.ui.recording = false; stopLiveViz(); render(); }
  function renderRecTime() { var n = document.getElementById("msRecTime"); if (n) n.textContent = fmtTime(MS.elapsed); }

  /* -- Audio Engine --------------------------------------------------- */
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
    if (kind === "beat") return MS.state.beatPreset ? (MS.state.beat.dur || BEAT_SECONDS) : (MS.state.beat.dur || 0);
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
    MS.audio.dur = getEffectiveDur(kind);
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
    MS.audio.dur = dur;
    var pct = dur > 0 ? Math.min(100, (totalPos / dur) * 100) : 0;
    var sf = document.getElementById("msSeekFill"); if (sf) sf.style.width = pct + "%";
    var pt = document.getElementById("msPlayTime"); if (pt) pt.textContent = fmtTime(totalPos) + " / " + fmtTime(dur);
    var ph = document.getElementById("msPlayhead"); if (ph) ph.style.left = pct + "%";
    var pb = document.getElementById("msPlayBtn"); if (pb) pb.innerHTML = a.paused ? svgPlay() : svgPause();
    requestAnimationFrame(function () {
      var m = document.getElementById("mseWaveMain"); var o = document.getElementById("mseWaveOv");
      if (m && !(MS.live && MS.live.active)) { var p = getActivePeaks(); if (p) drawPeaksTo(m, p, pct, true); }
      if (o) { var po = getActivePeaks(); if (po) drawPeaksTo(o, po, pct, false); }
    });
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

  /* -- Editor Waveform (center canvas + overview + EQ) --------------- */
  function hexA(hex, a) { var r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16); return "rgba(" + r + "," + g + "," + b + "," + a + ")"; }
  function getActivePeaks() {
    var kinds = ["take", "beat"];
    MS.state.layers.forEach(function (l) { if (l.url) kinds.push("layer_" + l.id); });
    for (var i = 0; i < kinds.length; i++) { var k = kinds[i]; var p = MS.audio.peaks[k]; if (p && p.data) return { data: p.data, dur: p.dur || getEffectiveDur(k), kind: k }; }
    return null;
  }
  function drawPeaksTo(canvas, peaksInfo, playheadPct, glow) {
    if (!canvas || !peaksInfo || !peaksInfo.data) return;
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.width = canvas.offsetWidth * dpr;
    var h = canvas.height = canvas.offsetHeight * dpr;
    if (!w || !h) return;
    var ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    var peaks = peaksInfo.data;
    var numPeaks = peaks.length / 2;
    var barW = w / numPeaks;
    var mid = h / 2;
    ctx.fillStyle = hexA(CYAN, 0.85);
    ctx.shadowColor = hexA(CYAN, glow ? 0.6 : 0.35);
    ctx.shadowBlur = (glow ? 18 : 8) * dpr;
    for (var i = 0; i < numPeaks; i++) {
      var min = peaks[i * 2], max = peaks[i * 2 + 1];
      var yMax = (1 - max) * mid, yMin = (1 - min) * mid;
      var yTop = yMin, barH = Math.max(1.5 * dpr, yMax - yMin);
      ctx.fillRect(i * barW, yTop, Math.max(1.5 * dpr, barW - 0.6), barH);
    }
    ctx.shadowBlur = 0;
    if (typeof playheadPct === "number") {
      var px = w * (playheadPct / 100);
      ctx.fillStyle = hexA(CYAN, 0.9);
      ctx.shadowColor = CYAN;
      ctx.shadowBlur = 12 * dpr;
      ctx.fillRect(px - 1.5 * dpr, 0, 3 * dpr, h);
      ctx.shadowBlur = 0;
    }
  }
  function drawGridLines(canvas) {
    if (!canvas) return;
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.offsetWidth * dpr, h = canvas.offsetHeight * dpr;
    if (!w || !h) return;
    var ctx = canvas.getContext("2d");
    ctx.lineWidth = 1 * dpr;
    for (var x = 0; x < w; x += w / 8) {
      ctx.strokeStyle = hexA(CYAN, 0.05);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    for (var y = 0; y < h; y += h / 6) {
      ctx.strokeStyle = hexA(CYAN, 0.04);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    ctx.strokeStyle = hexA(CYAN, 0.12);
    ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
  }
  function drawEditorWave() {
    var main = document.getElementById("mseWaveMain");
    var ov = document.getElementById("mseWaveOv");
    var eq = document.getElementById("mseEq");
    var pct = MS.audio.dur > 0 ? Math.min(100, (MS.audio.position / (MS.audio.dur || 1)) * 100) : 0;
    if (MS.live && MS.live.raf && !MS.live.analyser) {
      /* live mode handled by its own loop; draw the static grid base */
    }
    if (main) {
      drawGridLines(main);
      if (!(MS.live && MS.live.active)) {
        var p = getActivePeaks();
        if (p) drawPeaksTo(main, p, pct, true);
        else {
          var dpr = window.devicePixelRatio || 1;
          var w = main.width = main.offsetWidth * dpr, h = main.height = main.offsetHeight * dpr;
          if (w && h) { var c = main.getContext("2d"); c.clearRect(0, 0, w, h); drawAmbient(c, w, h); }
        }
      }
    }
    if (ov) {
      drawGridLines(ov);
      var po = getActivePeaks();
      if (po) drawPeaksTo(ov, po, pct, false);
      else {
        var dpr2 = window.devicePixelRatio || 1;
        var w2 = ov.width = ov.offsetWidth * dpr2, h2 = ov.height = ov.offsetHeight * dpr2;
        if (w2 && h2) { var c2 = ov.getContext("2d"); c2.clearRect(0, 0, w2, h2); drawAmbient(c2, w2, h2, 0.5); }
      }
    }
    if (eq) drawEqCurve(eq);
  }
  function drawAmbient(ctx, w, h, alpha) {
    var a = alpha || 0.5;
    var mid = h / 2;
    ctx.strokeStyle = hexA(CYAN, a * 0.5);
    ctx.fillStyle = hexA(CYAN, a * 0.08);
    ctx.lineWidth = 2;
    ctx.beginPath();
    var n = 90;
    for (var i = 0; i <= n; i++) {
      var x = (i / n) * w;
      var t = i * 0.35 + performance.now() * 0.0008;
      var y = mid + Math.sin(t) * (h * 0.22) + Math.sin(t * 0.6 + 1) * (h * 0.1);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    ctx.fill();
  }
  function drawEqCurve(canvas) {
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.width = canvas.offsetWidth * dpr, h = canvas.height = canvas.offsetHeight * dpr;
    if (!w || !h) return;
    var ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    var g = MS.state.eq || [0, 0, 0, 0];
    ctx.strokeStyle = hexA(CYAN, 0.9);
    ctx.shadowColor = CYAN; ctx.shadowBlur = 10 * dpr;
    ctx.lineWidth = 2 * dpr;
    ctx.beginPath();
    var n = 60;
    for (var i = 0; i <= n; i++) {
      var t = i / n;
      var x = t * w;
      var off = 0;
      for (var b = 0; b < g.length; b++) { off += (Math.sin(t * Math.PI * (b + 1)) * 0.5) * (g[b] / 100) * (h * 0.4); }
      var y = h / 2 - off;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  /* -- Live Microphone Visualizer ------------------------------------ */
  function startLiveViz(stream) {
    try {
      var Actx = window.AudioContext || window.webkitAudioContext;
      if (!Actx) return;
      if (MS.live && MS.live.ctx) { try { MS.live.ctx.close(); } catch (e) { } }
      var ctx = new Actx();
      var src = ctx.createMediaStreamSource(stream);
      var analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      src.connect(analyser);
      MS.live = { ctx: ctx, src: src, analyser: analyser, active: true, raf: 0, buf: new Uint8Array(analyser.frequencyBinCount), trail: [] };
      liveLoop();
    } catch (e) { MS.live = null; }
  }
  function liveLoop() {
    if (!MS.live || !MS.live.analyser) return;
    var l = MS.live;
    var analyser = l.analyser;
    var canvas = document.getElementById("mseWaveMain");
    if (canvas && canvas.offsetWidth) {
      var dpr = window.devicePixelRatio || 1;
      var w = canvas.width = canvas.offsetWidth * dpr;
      var h = canvas.height = canvas.offsetHeight * dpr;
      var ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, w, h);
      drawGridLines(canvas);
      analyser.getByteFrequencyData(l.buf);
      var bars = 96;
      var bw = w / bars;
      var mid = h / 2;
      var level = 0;
      for (var i = 0; i < bars; i++) {
        var idx = Math.floor(i * (l.buf.length / bars) * 0.5);
        var v = l.buf[idx] / 255;
        level = Math.max(level, v);
        var bh = Math.max(1.5 * dpr, v * h * 0.86);
        var grad = ctx.createLinearGradient(0, mid - bh / 2, 0, mid + bh / 2);
        grad.addColorStop(0, CYAN); grad.addColorStop(1, hexA(CYAN, 0.35));
        ctx.fillStyle = grad;
        ctx.shadowColor = CYAN; ctx.shadowBlur = 12 * dpr;
        ctx.fillRect(i * bw + 1, mid - bh / 2, Math.max(1, bw - 2), bh);
        ctx.fillRect(i * bw + 1, mid, Math.max(1, bw - 2), bh);
      }
      ctx.shadowBlur = 0;
      if (canvas.offsetWidth > 1 && h > 1) {
        ctx.strokeStyle = hexA(CYAN, 0.25);
        ctx.lineWidth = 1 * dpr;
        ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
      }
    }
    l.raf = requestAnimationFrame(liveLoop);
  }
  function stopLiveViz() {
    if (MS.live) {
      if (MS.live.raf) cancelAnimationFrame(MS.live.raf);
      try { if (MS.live.ctx) MS.live.ctx.close(); } catch (e) { }
      MS.live = null;
    }
    if (document.getElementById("mseWaveMain")) {
      requestAnimationFrame(function () { drawEditorWave(); });
    }
  }

  /* -- Audio Upload --------------------------------------------------- */
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

  /* -- Beat Synthesis ------------------------------------------------- */
  function beatPresetById(id) { for (var i = 0; i < BEAT_PRESETS.length; i++) if (BEAT_PRESETS[i].id === id) return BEAT_PRESETS[i]; return null; }
  var BEAT_SECONDS = 180; /* approx 3-minute arrangement */
  function renderBeatLoop(preset) {
    var Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Offline) return Promise.reject(new Error("no offline ctx"));
    var rate = 44100, bpm = preset.bpm, spb = 60 / bpm, steps = 16, barDur = spb * steps;
    var totalBars = Math.max(8, Math.round((BEAT_SECONDS / barDur)));
    var samples = Math.ceil(rate * (totalBars * barDur + 1.5));
    var ctx = new Offline(2, samples, rate);
    var kit = kitFor(preset);
    var root = bassFreq(preset.note);
    var fifth = bassRootNth(preset.note, 7);
    var sub4th = bassRootNth(preset.note, 5);
    var pat = preset.pattern;

    /* Sparse intro (bars 0-1), full groove (bars 2+), fills on chorus (every fillEvery bars) */
    for (var bar = 0; bar < totalBars; bar++) {
      var barStart = bar * barDur;
      var section = bar < 2 ? "intro" : (bar >= totalBars - 4 ? "outro" : "main");
      var isChorus = section !== "intro" && section !== "outro" && (bar - 2) % kit.fillEvery === (kit.fillEvery - 1);
      for (var i = 0; i < steps; i++) {
        var ch = pat.charAt(i);
        var stepDur = barDur / steps;
        var swing = (i % 2 === 1) ? kit.swing : 0;
        if ((section === "intro" || section === "outro") && ch !== "K" && ch !== "L" && ch !== "H" && ch !== "h" && ch !== "0") ch = "-";
        var when = barStart + i * stepDur + swing * stepDur;
        var vel = (section === "intro" || section === "outro") ? 0.7 : 1;
        if (ch === "K") { kick(ctx, when, 1.0 * vel); bassFor(ctx, kit, root, sub4th, when, spb * 0.8, vel); }
        else if (ch === "k") { kick(ctx, when, 0.72 * vel); }
        else if (ch === "L") { kick(ctx, when, 0.8 * vel); bassFor(ctx, kit, root, fifth, when, spb * 0.9, vel); }
        else if (ch === "T") { snareFor(ctx, kit, when, 0.92 * vel); }
        else if (ch === "t") { snareFor(ctx, kit, when, 0.5 * vel); }
        else if (ch === "H") { hatFor(ctx, kit, when, 0.42); }
        else if (ch === "h") { hatFor(ctx, kit, when, 0.2); }
        else if (ch === "0") { hatFor(ctx, kit, when, 0.16); }
        /* Rhythm/perc stems set by the kit for an authentic feel */
        if (kit.perc && (i === 4 || i === 12) && (section === "main" || isChorus)) shakerHit(ctx, when, 0.22);
      }
      /* Clean offbeat 16th hat layer adds drive & keeps it simple/full */
      var hk = kit.hat16;
      if (hk > 0 && (section === "main" || isChorus)) {
        for (var h16 = 0; h16 < 8; h16++) {
          if (h16 % 2 === 1 || h16 % 4 === 1) hatFor(ctx, kit, barStart + (h16 * 2 + 1) * (stepDur / 2), 0.1 * hk);
        }
      }
      /* Drum fills add energy into the chorus */
      if (isChorus) { for (var f = 0; f < 8; f++) { var fw = barStart + (f / 8) * barDur; if (f % 2 === 0) kick(ctx, fw, 0.5); else snareFor(ctx, kit, fw, 0.4); } }
    }
    /* Master bus: punchy, loud, full */
    punchMaster(ctx);
    return ctx.startRendering().then(function (buffer) { return encodeWav(buffer); });
  }
  function punchMaster(ctx) {
    var c = ctx.createDynamicsCompressor();
    c.threshold.value = -14; c.knee.value = 22; c.ratio.value = 5; c.attack.value = 0.003; c.release.value = 0.28;
    var g = ctx.createGain(); g.gain.value = 1.15;
    c.connect(g); g.connect(ctx.destination);
    ctx._out = c;
  }
  function bassFor(ctx, kit, root, alt, when, dur, vel) {
    var o = ctx.createOscillator(); var g = ctx.createGain();
    var base = root;
    if (kit.bassStyle === "dh") base = alt;
    if (kit.bassStyle === "hip" && (Math.floor(when / 1) % 2 === 0)) base = alt;
    o.type = "sine";
    o.frequency.value = base;
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(0.62 * vel, when + 0.006);
    g.gain.setValueAtTime(0.62 * vel, when + dur * 0.6);
    g.gain.exponentialRampToValueAtTime(0.002, when + dur);
    o.connect(g); g.connect(ctx._out || ctx.destination); o.start(when); o.stop(when + dur + 0.03);
  }
  function kick(ctx, when, vel) {
    var o = ctx.createOscillator(); var g = ctx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(170, when);
    o.frequency.exponentialRampToValueAtTime(46, when + 0.09);
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(1.02 * vel, when + 0.004);
    g.gain.exponentialRampToValueAtTime(0.001, when + 0.2);
    var click = ctx.createOscillator(); var cg = ctx.createGain();
    click.type = "square"; click.frequency.value = 900;
    cg.gain.setValueAtTime(0.12 * vel, when);
    cg.gain.exponentialRampToValueAtTime(0.001, when + 0.012);
    o.connect(g); click.connect(cg); g.connect(ctx._out || ctx.destination); cg.connect(ctx._out || ctx.destination);
    o.start(when); o.stop(when + 0.22); click.start(when); click.stop(when + 0.015);
  }
  function snareFor(ctx, kit, when, vel) {
    var n = ctx.createBufferSource(); var b = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.28), ctx.sampleRate); var d = b.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
    n.buffer = b;
    var f = ctx.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = kit.kit === "elec" ? 4200 : 2400; f.Q.value = 0.8;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(0.85 * vel, when + 0.002);
    g.gain.exponentialRampToValueAtTime(0.001, when + 0.22);
    var tone = ctx.createOscillator(); tone.type = "triangle"; tone.frequency.value = 190;
    var tg = ctx.createGain(); tg.gain.setValueAtTime(0.3 * vel, when); tg.gain.exponentialRampToValueAtTime(0.001, when + 0.1);
    n.connect(f); f.connect(g); g.connect(ctx._out || ctx.destination); tone.connect(tg); tg.connect(ctx._out || ctx.destination);
    n.start(when); n.stop(when + 0.3); tone.start(when); tone.stop(when + 0.12);
  }
  function hatFor(ctx, kit, when, vel) {
    var n = ctx.createBufferSource(); var b = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.06), ctx.sampleRate); var d = b.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
    n.buffer = b;
    var f = ctx.createBiquadFilter(); f.type = "highpass"; f.frequency.value = kit.kit === "elec" ? 8500 : 7000;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(0.5 * vel, when + 0.001);
    g.gain.exponentialRampToValueAtTime(0.001, when + 0.045);
    n.connect(f); f.connect(g); g.connect(ctx._out || ctx.destination);
    n.start(when); n.stop(when + 0.06);
  }
  function shakerHit(ctx, when, vel) {
    var n = ctx.createBufferSource(); var b = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.14), ctx.sampleRate); var d = b.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length) * 0.8;
    n.buffer = b;
    var f = ctx.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = 5200; f.Q.value = 2;
    var g = ctx.createGain(); g.gain.value = vel;
    n.connect(f); f.connect(g); g.connect(ctx._out || ctx.destination); n.start(when); n.stop(when + 0.15);
  }
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
      MS.state.beat.dur = BEAT_SECONDS; MS.state.beat.vol = 100;
      MS.state.beat.mute = false; MS.state.beat.solo = false;
      MS.state.beatPreset = preset.id;
      var a = new Audio(); a.preload = "metadata"; a.src = url;
      a.onloadedmetadata = function () { MS.audio.loopDur = 0; render(); };
      storePeaks("beat", url, function () { render(); });
      toast(preset.city + " loaded."); autoSaveDebounced();
    }).catch(function () { render(); });
  }

  /* -- Effects / Mix -------------------------------------------------- */
  function setFx(field, val) { if (field === "noiseReduction") MS.state.fx.noiseReduction = !!val; else if (field === "effect") MS.state.fx.effect = val; else MS.state.fx[field] = Number(val); render(); autoSaveDebounced(); }
  function applyEffectPreset(preset) { var fx = MS.state.fx; fx.noiseReduction = !!preset.fx.noiseReduction; fx.pitch = Number(preset.fx.pitch); fx.effect = preset.fx.effect; fx.reverb = Number(preset.fx.reverb); fx.delay = Number(preset.fx.delay); render(); autoSaveDebounced(); toast("Effect: " + preset.name); }
  function setMaster(val) { MS.state.mix.master = Number(val); render(); }
  function autoMix() { var b = MS.state.beat.vol; var v = MS.state.take.vol; if (b && v) { MS.state.beat.vol = Math.round(Math.min(90, Math.max(35, b * 0.72))); MS.state.take.vol = 100; } MS.state.autoMix = true; toast("Auto Mix applied."); render(); autoSaveDebounced(); }
  function autoMaster() { MS.state.mix.master = 100; MS.state.autoMaster = true; toast("Master levelled."); render(); autoSaveDebounced(); }

  /* -- AI generate + edit --------------------------------------------- */
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

  /* -- Save / load / delete / export ---------------------------------- */
  function saveSong() { syncInputs(); MS.state.savedAt = Date.now(); if (!MS.state.id) MS.state.id = "ms" + Date.now(); var found = false; for (var i = 0; i < MS.projects.length; i++) { if (MS.projects[i].id === MS.state.id) { MS.projects[i] = normalizeProject(clone(MS.state)); found = true; break; } } if (!found) MS.projects.unshift(normalizeProject(clone(MS.state))); saveProjects(); pushProjectsToServer(); showSaveState("Saved"); toast("Song saved."); render(); }
  function newSong() { stopPlayback(); MS.state = defaultState(); MS.state.id = null; MS.ui.openTool = ""; MS.ui.activeNav = ""; MS.audio.peaks = {}; render(); }
  function loadSong(id) { for (var i = 0; i < MS.projects.length; i++) { if (MS.projects[i].id === id) { stopPlayback(); MS.state = normalizeProject(clone(MS.projects[i])); MS.ui.openTool = ""; MS.ui.activeNav = ""; MS.audio.peaks = {}; render(); toast("Song loaded."); return; } } }
  function deleteSong(id) { MS.projects = MS.projects.filter(function (p) { return p.id !== id; }); saveProjects(); deleteProjectOnServer(id); render(); }
  function exportSong() { syncInputs(); var title = MS.state.name || "Untitled"; var parts = [title, "Genre: " + MS.state.genre + " \u00b7 Mood: " + MS.state.mood + " \u00b7 Tempo: " + MS.state.tempo + (MS.state.key ? " \u00b7 Key: " + MS.state.key : ""), "", ""]; if (MS.state.voice) parts[2] = "Voice: " + (VOICE_LABELS[MS.state.voice] || MS.state.voice); parts.push((MS.state.aiResult && MS.state.aiResult.lyrics) || MS.state.lyrics || "(no lyrics yet)"); if (MS.state.aiResult && MS.state.aiResult.arrangement) { parts.push(""); parts.push("ARRANGEMENT"); parts.push(MS.state.aiResult.arrangement); } var blob = new Blob([parts.join("\n")], { type: "text/plain;charset=utf-8" }); var url = URL.createObjectURL(blob); var a = document.createElement("a"); a.href = url; a.download = title.replace(/[\\/:*?"<>|]+/g, "_") + ".txt"; document.body.appendChild(a); a.click(); document.body.removeChild(a); setTimeout(function () { URL.revokeObjectURL(url); }, 4000); }

  /* -- Navigation ----------------------------------------------------- */
  function openNav(id) { if (MS.ui.activeNav === id) { MS.ui.activeNav = ""; MS.ui.openTool = ""; } else { MS.ui.activeNav = id; MS.ui.openTool = ""; } render(); }
  function openTool(id) { MS.ui.openTool = (MS.ui.openTool === id) ? "" : id; render(); }
  function setVoice(v) { MS.state.voice = v; render(); autoSaveDebounced(); }
  function setConsent(v) { MS.state.consent = !!v; render(); autoSaveDebounced(); }
  function applyEffectPresetByIdx(idx) { if (EFFECT_PRESETS[idx]) applyEffectPreset(EFFECT_PRESETS[idx]); }
  function setBeatSearch(v) { MS.ui.searchBeat = v; render(); }
  function setBeatType(v) { MS.ui.beatType = v || ""; if (MS.ui.beatType) MS.ui.beatMood = ""; render(); }
  function setBeatMood(v) { MS.ui.beatMood = v || ""; if (MS.ui.beatMood) MS.ui.beatType = ""; render(); }

  /* -- SVG Icons ------------------------------------------------------ */
  function svgPlay() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>'; }
  function svgPause() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>'; }
  function svgStop() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>'; }
  function svgMic() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg>'; }
  function svgX() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" x2="6" y1="6" y2="18"/><line x1="6" x2="18" y1="6" y2="18"/></svg>'; }
  function svgWaveLogo() { return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#04121f" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12h2M6 12h1M10.5 12H13M17 12h1M20 12h2"/><path d="M8.8 7.5c-.5 3-1.3 6-1.3 9M14.2 6.5c.4 3.6 1.3 7.7 1.3 11.5"/></svg>'; }
  function svgPrev() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h2v14H6z"/><polygon points="8 12 20 5 20 19 8 12" transform="translate(-6 0)"/></svg>'; }
  function svgNext() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M16 5h2v14h-2z"/><polygon points="8 12 20 5 20 19 8 12" transform="translate(-2 0)"/></svg>'; }
  function svgStop2() { return svgStop(); }
  function svgVol() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19" fill="currentColor"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/></svg>'; }
  function svgMenu() { return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg>'; }
  var MS_TOOL_ICONS = {
    cut: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.12 15.88M14.47 14.48 20 20M8.12 8.12 12 12"/></svg>',
    copy: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    trim: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>',
    normalize: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3v3M12 10v4M12 17v4"/></svg>',
    fade: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3a9 9 0 0 1 0 18"/></svg>',
    reverse: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',
    eq: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 7h6M13 7h8M3 17h4M11 17h10M15 12h6"/></svg>',
    comp: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 8h12M18 8h3M3 16h3M12 16h9M3 12h18"/></svg>',
    reverb: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12c3 0 3-4 6-4s3 8 6 8 3-8 6-8"/></svg>',
    delay: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    ai: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/></svg>',
    projects: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/></svg>'
  };
  function svgTool(n) { return MS_TOOL_ICONS[n] || svgMenu(); }

  /* -- CSS Injection (Professional Waveform Editor � dark + cyan) ----- */
  function injectStyles() {
    if (document.getElementById("ms-css")) return;
    var css = [
      "#vmWsPanelMusic{position:relative;overflow:hidden;background:#05070d;}",
      "#vmWsPanelMusic *{box-sizing:border-box;}",
      "#vmWsPanelMusic .ms-studio{display:flex;flex-direction:column;height:100%;background:linear-gradient(180deg,#070b14 0%,#05070d 100%);color:#d7e0ee;font-family:inherit;overflow:hidden;}",
      /* -- Top bar -- */
      ".mse-top{display:flex;align-items:center;gap:16px;padding:10px 18px;background:rgba(10,16,28,.85);border-bottom:1px solid rgba(0,229,255,.12);flex-shrink:0;backdrop-filter:blur(8px);}",
      ".mse-top .mse-brand{display:flex;align-items:center;gap:10px;min-width:200px;}",
      ".mse-brand .mse-logo{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#00e5ff,#00f0c8);display:flex;align-items:center;justify-content:center;box-shadow:0 0 18px rgba(0,229,255,.45);}",
      ".mse-brand .mse-bt{font-size:13px;font-weight:800;letter-spacing:.04em;color:#eaf6ff;line-height:1;}",
      ".mse-brand .mse-bs{font-size:9px;letter-spacing:.22em;color:#2a6f9e;text-transform:uppercase;font-weight:700;}",
      /* -- Transport -- */
      ".mse-tp{display:flex;align-items:center;gap:8px;margin:0 auto;}",
      ".mse-tp button{width:40px;height:40px;border-radius:50%;border:1px solid rgba(0,229,255,.18);background:rgba(0,229,255,.05);color:#bfeaff;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;}",
      ".mse-tp button:hover{border-color:rgba(0,229,255,.5);background:rgba(0,229,255,.12);box-shadow:0 0 14px rgba(0,229,255,.25);}",
      ".mse-tp button.mse-rec{background:linear-gradient(135deg,#ff3b5c,#e11d48);border-color:rgba(255,59,92,.5);}",
      ".mse-tp button.mse-rec:hover{box-shadow:0 0 16px rgba(255,59,92,.5);}",
      ".mse-tp button.mse-play{width:52px;height:52px;background:linear-gradient(135deg,#00e5ff,#00c8f0);border:none;color:#04121f;}",
      ".mse-tp button.mse-play:hover{box-shadow:0 0 24px rgba(0,229,255,.6);}",
      ".mse-tp button svg{width:18px;height:18px;}",
      ".mse-tc{font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:18px;font-weight:700;color:#eaf6ff;letter-spacing:.06em;min-width:96px;text-align:center;text-shadow:0 0 14px rgba(0,229,255,.35);}",
      ".mse-vol{display:flex;align-items:center;gap:6px;color:#5b7f9e;}",
      ".mse-vol input[type=range]{width:80px;height:3px;accent-color:#00e5ff;cursor:pointer;}",
      ".mse-menu{width:38px;height:38px;border-radius:10px;border:1px solid rgba(0,229,255,.16);background:rgba(0,229,255,.05);color:#bfeaff;cursor:pointer;display:flex;align-items:center;justify-content:center;}",
      ".mse-menu:hover{background:rgba(0,229,255,.14);}",
      /* -- Body (sidebar + center + right) -- */
      ".mse-body{flex:1;min-height:0;display:flex;}",
      /* -- Left sidebar -- */
      ".mse-side{width:216px;flex-shrink:0;background:rgba(8,13,24,.7);border-right:1px solid rgba(0,229,255,.1);overflow-y:auto;padding:14px 12px;display:none;}",
      ".mse-side.open{display:block;}",
      ".mse-side-lb{font-size:9px;letter-spacing:.2em;color:#2a6f9e;text-transform:uppercase;font-weight:800;margin:14px 2px 8px;padding-bottom:6px;border-bottom:1px solid rgba(0,229,255,.08);}",
      ".mse-side-lb:first-child{margin-top:2px;}",
      ".mse-file{background:linear-gradient(180deg, rgba(0,229,255,.07),transparent);border:1px solid rgba(0,229,255,.22);border-radius:10px;padding:10px;box-shadow:0 0 18px rgba(0,229,255,.06) inset;}",
      ".mse-file .mse-fn{font-size:12px;font-weight:700;color:#eaf6ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",
      ".mse-file .mse-fm{font-size:10px;color:#3f74a0;margin-top:4px;font-variant-numeric:tabular-nums;}",
      ".mse-tbtn{display:flex;align-items:center;gap:9px;width:100%;padding:8px 10px;margin:2px 0;border:1px solid transparent;background:transparent;color:#8fb0cc;font-size:12px;cursor:pointer;border-radius:8px;text-align:left;transition:all .12s;}",
      ".mse-tbtn:hover{background:rgba(0,229,255,.08);color:#dff3ff;}",
      ".mse-tbtn .ic{opacity:.85;}",
      ".mse-tbtn.active{background:rgba(0,229,255,.1);color:#00e5ff;border-color:rgba(0,229,255,.22);box-shadow:0 0 14px rgba(0,229,255,.12);}",
      /* -- Center (waveform editor) -- */
      ".mse-center{flex:1;min-width:0;display:flex;flex-direction:column;padding:16px 18px;gap:12px;}",
      ".mse-wave-wrap{flex:1;min-height:0;background:#02040a;border:1px solid rgba(0,229,255,.14);border-radius:14px;position:relative;overflow:hidden;box-shadow:0 0 40px rgba(0,229,255,.06), inset 0 0 60px rgba(0,229,255,.03);}",
      ".mse-wave-wrap::after{content:'';position:absolute;inset:0;pointer-events:none;background:radial-gradient(60% 50% at 50% 45%,rgba(0,229,255,.06),transparent 70%);}",
      "#mseWaveMain{position:absolute;inset:0;width:100%;height:100%;display:block;}",
      ".mse-hint{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);text-align:center;color:#24506f;z-index:2;pointer-events:none;font-size:13px;}",
      ".mse-hint .ic{display:block;margin:0 auto 10px;opacity:.5;}",
      ".mse-hint b{color:#5b9dcc;font-weight:700;}",
      ".mse-ovwrap{margin-top:14px;height:70px;background:#02040a;border:1px solid rgba(0,229,255,.12);border-radius:10px;position:relative;overflow:hidden;}",
      "#mseWaveOv{position:absolute;inset:0;width:100%;height:100%;display:block;}",
      /* -- Right panels -- */
      ".mse-right{width:250px;flex-shrink:0;padding:14px;display:flex;flex-direction:column;gap:10px;overflow-y:auto;display:none;}",
      ".mse-right.open{display:flex;border-left:1px solid rgba(0,229,255,.1);background:rgba(8,13,24,.5);}",
      ".mse-card{background:linear-gradient(180deg, rgba(13,20,34,.7),rgba(8,13,24,.7));border:1px solid rgba(0,229,255,.14);border-radius:12px;padding:12px;}",
      ".mse-card .mse-card-t{font-size:10px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#3f9ccd;margin-bottom:10px;}",
      ".mse-prop{display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:11px;}",
      ".mse-prop .k{color:#3f74a0;}",
      ".mse-prop .v{color:#dff3ff;font-variant-numeric:tabular-nums;}",
      ".mse-eq{height:110px;position:relative;background:linear-gradient(180deg,rgba(0,229,255,.04),transparent);border-radius:8px;}",
      "#mseEq{width:100%;height:100%;display:block;}",
      ".mse-eqctl{display:flex;gap:8px;margin-top:10px;}",
      ".mse-eqctl input[type=range]{flex:1;height:3px;accent-color:#00e5ff;cursor:pointer;}",
      /* -- Status bar -- */
      ".mse-status{display:flex;align-items:center;gap:18px;padding:7px 18px;background:rgba(8,13,24,.9);border-top:1px solid rgba(0,229,255,.1);font-size:10px;color:#3f74a0;flex-shrink:0;letter-spacing:.04em;}",
      ".mse-status .mse-stop{color:#7fd3f5;font-weight:600;}",
      ".mse-status .mse-saves.ok{color:#22c55e;}",
      /* -- Ambient decorative wave -- */
      ".mse-ambient{position:relative;height:64px;overflow:hidden;flex-shrink:0;}",
      ".mse-ambient svg{position:absolute;left:0;right:0;bottom:0;width:100%;height:100%;opacity:.5;}",
      /* -- Sheets (reused panels) -- */
      ".ms-pnl{position:absolute;top:0;right:0;bottom:0;width:min(440px,92vw);background:#0a1120;border-left:1px solid rgba(0,229,255,.18);transform:translateX(102%);transition:transform .28s cubic-bezier(.4,0,.2,1);z-index:30;display:flex;flex-direction:column;box-shadow:-20px 0 60px rgba(0,0,0,.5);}",
      ".ms-pnl.open{transform:translateX(0);}",
      ".ms-pnl-h{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid rgba(0,229,255,.12);flex-shrink:0;}",
      ".ms-pnl-h h3{font-size:13px;font-weight:800;letter-spacing:.04em;margin:0;color:#eaf6ff;text-transform:uppercase;}",
      ".ms-pnl-x{background:rgba(0,229,255,.06);border:1px solid rgba(0,229,255,.2);color:#bfeaff;cursor:pointer;width:30px;height:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;}",
      ".ms-pnl-x:hover{background:rgba(0,229,255,.16);}",
      ".ms-pnl-b{flex:1;overflow-y:auto;padding:14px 18px;}",
      ".ms-sn{display:flex;gap:4px;padding:10px 12px;overflow-x:auto;flex-shrink:0;border-bottom:1px solid rgba(0,229,255,.1);flex-wrap:wrap;}",
      ".ms-sn button{flex-shrink:0;padding:6px 11px;border-radius:8px;border:1px solid rgba(0,229,255,.18);background:transparent;color:#7fb3d6;font-size:11px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:5px;transition:all .12s;}",
      ".ms-sn button.on{background:#00e5ff;color:#04121f;border-color:#00e5ff;font-weight:700;}",
      ".ms-sn button svg{width:14px;height:14px;}",
      /* -- Form & controls -- */
      ".ms-lb{font-size:9px;color:#2a6f9e;text-transform:uppercase;letter-spacing:.18em;margin:14px 0 7px;font-weight:800;}",
      ".ms-fi{width:100%;background:#050a14;border:1px solid rgba(0,229,255,.14);color:#dff3ff;padding:9px 12px;border-radius:9px;font-size:13px;outline:none;}",
      ".ms-fi:focus{border-color:#00e5ff;box-shadow:0 0 0 2px rgba(0,229,255,.12);}",
      "textarea.ms-fi{resize:vertical;min-height:60px;font-family:inherit;}",
      "select.ms-fi{appearance:none;background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23408cc0' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\");background-repeat:no-repeat;background-position:right 10px center;padding-right:30px;}",
      ".ms-rw{display:flex;gap:8px;margin-bottom:10px;}.ms-rw>*{flex:1;}",
      ".ms-btn{padding:9px 15px;border-radius:9px;border:none;font-size:13px;font-weight:700;cursor:pointer;transition:all .15s;}",
      ".ms-btn.pri{background:linear-gradient(135deg,#00e5ff,#00c8f0);color:#04121f;box-shadow:0 0 16px rgba(0,229,255,.25);}.ms-btn.pri:hover{box-shadow:0 0 22px rgba(0,229,255,.45);}",
      ".ms-btn.sec{background:rgba(0,229,255,.07);color:#bfeaff;border:1px solid rgba(0,229,255,.16);}.ms-btn.sec:hover{background:rgba(0,229,255,.14);}",
      ".ms-btn.dng{background:#e11d48;color:#fff;}",
      ".ms-btn.sm{padding:5px 10px;font-size:11px;}",
      ".ms-chip{padding:4px 10px;border-radius:999px;border:1px solid rgba(0,229,255,.16);background:transparent;color:#8fb0cc;font-size:11px;font-weight:600;cursor:pointer;transition:all .15s;margin:2px 3px 2px 0;}",
      ".ms-chip:hover{border-color:rgba(0,229,255,.4);color:#bfeaff;}",
      ".ms-chip.on{background:rgba(0,229,255,.18);border-color:#00e5ff;color:#d8faff;box-shadow:0 0 10px rgba(0,229,255,.25);}",
      ".ms-tog{display:flex;align-items:center;gap:8px;cursor:pointer;}", 
      ".ms-tog input[type=checkbox]{accent-color:#00e5ff;width:16px;height:16px;}",
      ".ms-tog span{font-size:12px;color:#8fb0cc;}",
      ".ms-chip{display:inline-flex;padding:6px 11px;border-radius:8px;border:1px solid rgba(0,229,255,.16);background:#050a14;color:#8fb0cc;font-size:11px;cursor:pointer;margin:0 4px 7px 0;transition:all .12s;}",
      ".ms-chip.on{background:#00e5ff;color:#04121f;border-color:#00e5ff;font-weight:700;}",
      ".ms-slr{display:flex;align-items:center;gap:8px;margin-bottom:9px;}",
      ".ms-slr label{font-size:12px;color:#7b9cc0;min-width:74px;}",
      ".ms-slr input[type=range]{flex:1;accent-color:#00e5ff;}",
      ".ms-slr span{font-size:11px;color:#5b9dcc;min-width:30px;text-align:right;font-variant-numeric:tabular-nums;}",
      /* -- Reused cards / elements -- */
      ".ms-pc{background:linear-gradient(180deg,rgba(13,20,34,.7),rgba(8,13,24,.7));border:1px solid rgba(0,229,255,.14);border-radius:10px;padding:10px;margin-bottom:8px;display:flex;align-items:center;gap:10px;}",
      ".ms-pc:hover{border-color:rgba(0,229,255,.34);}",
      ".ms-pc-info{flex:1;min-width:0;}",
      ".ms-pc-nm{font-size:13px;font-weight:700;color:#dff3ff;}",
      ".ms-pc-mt{font-size:11px;color:#3f74a0;}",
      ".ms-pc-btns{display:flex;gap:4px;}",
      ".ms-rec-big{width:84px;height:84px;border-radius:50%;border:3px solid #ff3b5c;background:rgba(255,59,92,.08);color:#ff3b5c;display:flex;align-items:center;justify-content:center;cursor:pointer;margin:0 auto 14px;transition:all .2s;box-shadow:0 0 24px rgba(255,59,92,.15);}",
      ".ms-rec-big.rec{background:#ff3b5c;color:#fff;animation:ms-pulse 1s infinite;}",
      "@keyframes ms-pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,59,92,.45)}50%{box-shadow:0 0 0 14px rgba(255,59,92,0)}}",
      ".ms-consent{background:#050a14;border:1px solid rgba(0,229,255,.14);border-radius:9px;padding:10px;margin-top:8px;}",
      ".ms-consent label{font-size:11px;color:#8fb0cc;display:flex;align-items:flex-start;gap:6px;cursor:pointer;line-height:1.4;}",
      ".ms-consent input{margin-top:2px;accent-color:#00e5ff;}",
      ".ms-vc{background:linear-gradient(180deg,rgba(13,20,34,.7),rgba(8,13,24,.7));border:1px solid rgba(0,229,255,.14);border-radius:10px;padding:10px;margin-bottom:8px;cursor:pointer;transition:all .12s;}",
      ".ms-vc.on{border-color:#00e5ff;background:rgba(0,229,255,.08);box-shadow:0 0 14px rgba(0,229,255,.12);}",
      ".ms-vc .vn{font-size:13px;font-weight:700;color:#dff3ff;}.ms-vc .vs{font-size:11px;color:#3f74a0;margin-top:2px;}",
      ".ms-mi{background:#050a14;border-radius:8px;padding:9px;margin-bottom:6px;font-size:12px;color:#cfe6f7;}",
      ".ms-mi .mt{color:#3f74a0;font-size:10px;margin-top:2px;}",
      ".ms-res{background:#050a14;border-radius:9px;padding:12px;font-size:12px;white-space:pre-wrap;max-height:200px;overflow-y:auto;line-height:1.5;color:#cfe6f7;}",
      ".ms-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;}",
      ".ms-beat{background:linear-gradient(180deg,rgba(13,20,34,.7),rgba(8,13,24,.7));border:1px solid rgba(0,229,255,.14);border-radius:10px;padding:10px;cursor:pointer;transition:all .15s;}",
      ".ms-beat:hover{border-color:rgba(0,229,255,.4);}.ms-beat.on{border-color:#00e5ff;background:rgba(0,229,255,.08);box-shadow:0 0 16px rgba(0,229,255,.14);}",
      ".ms-beat-ct{font-size:12px;font-weight:700;color:#dff3ff;margin-bottom:2px;}",
      ".ms-beat-mt{font-size:10px;color:#5b9dcc;}",
      ".ms-beat-g{font-size:9px;color:#3f74a0;margin-top:1px;}",
      ".ms-beat-act{display:flex;gap:4px;margin-top:6px;align-items:center;}",
      ".ms-beat-dur{font-size:9px;color:#00e5ff;margin-left:auto;font-weight:700;}",
      ".ms-se{width:100%;background:#050a14;border:1px solid rgba(0,229,255,.14);color:#dff3ff;padding:7px 11px;border-radius:9px;font-size:12px;outline:none;margin-bottom:10px;}",
      ".ms-se:focus{border-color:#00e5ff;}",
      ".ms-lyr{width:100%;min-height:200px;background:#050a14;border:1px solid rgba(0,229,255,.14);color:#dff3ff;padding:12px;border-radius:10px;font-size:14px;font-family:inherit;line-height:1.8;outline:none;resize:vertical;}",
      ".ms-lyr:focus{border-color:#00e5ff;}",
      /* -- Responsive -- */
      "@media(min-width:1025px){.mse-side{display:block;}.mse-right{display:flex;}}",
      "@media(max-width:1024px){.mse-side{display:none !important;}.mse-right{display:none !important;}.mse-tp .mse-vol{display:none;}}",
      "@media(max-width:640px){.mse-top{padding:8px 10px;gap:8px;}.mse-tc{font-size:14px;min-width:72px;}.mse-tp button{width:36px;height:36px;}.mse-tp button.mse-play{width:44px;height:44px;}.mse-brand .mse-bs{display:none;}.mse-center{padding:10px;}.mse-ovwrap{height:54px;}.mse-hint{font-size:11px;}}"
    ].join("\n");
    var s = document.createElement("style");
    s.id = "ms-css";
    s.textContent = css;
    document.head.appendChild(s);
  }

  /* -- Timeline / Workspace Renderer ---------------------------------- */
  function fallbackWorkspace() {
    return '<div class="ms-empty"><svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>' +
      '<p style="font-size:15px;font-weight:600;margin-top:8px;">Start your song</p>' +
      '<p>Record, upload, or use Create to generate with AI.</p></div>';
  }
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
    if (s.beatPreset) dur = Math.max(dur, s.beat.dur || BEAT_SECONDS);
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

  /* -- Panel Content Renderers ----------------------------------------- */
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

  /* -- Tool Sub-Panel Renderers ---------------------------------------- */
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
    var typeF = MS.ui.beatType || "";
    var moodF = MS.ui.beatMood || "";
    var filtered = BEAT_PRESETS.filter(function (b) {
      if (typeF && b.type !== typeF) return false;
      if (moodF && b.mood !== moodF) return false;
      if (search && (b.city + " " + b.genre + " " + b.type + " " + b.mood + " " + b.desc).toLowerCase().indexOf(search) === -1) return false;
      return true;
    });
    var typeChips = '<button class="ms-chip' + (!typeF ? ' on' : '') + '" onclick="VMMusic.setBeatType(\'\')">All Types</button>';
    BEAT_TYPES.forEach(function (t) { typeChips += '<button class="ms-chip' + (typeF === t ? ' on' : '') + '" onclick="VMMusic.setBeatType(\'' + t + '\')">' + esc(t) + '</button>'; });
    var moodChips = '<button class="ms-chip' + (!moodF ? ' on' : '') + '" onclick="VMMusic.setBeatMood(\'\')">All Moods</button>';
    BEAT_MOODS.forEach(function (m) { moodChips += '<button class="ms-chip' + (moodF === m ? ' on' : '') + '" onclick="VMMusic.setBeatMood(\'' + m + '\')">' + esc(m) + '</button>'; });
    var h = '<input class="ms-se" placeholder="Search beats by name, genre, mood..." value="' + esc(MS.ui.searchBeat || "") + '" oninput="VMMusic.setBeatSearch(this.value)">';
    h += '<div class="ms-lb">Lock by Type</div><div style="margin-bottom:6px">' + typeChips + '</div>';
    h += '<div class="ms-lb">Lock by Emotion</div><div style="margin-bottom:6px">' + moodChips + '</div>';
    h += '<div style="font-size:11px;color:#475569;margin-bottom:8px">' + filtered.length + ' beats' + ((search || typeF || moodF) ? ' found' : ' total') + ' \u00b7 Strong, full 3-minute arrangements built for chords & singing</div>';
    h += '<div class="ms-grid">';
    filtered.forEach(function (b) {
      var active = MS.state.beatPreset === b.id;
      var previewing = MS.previewPreset === b.id;
      h += '<div class="ms-beat' + (active ? ' on' : '') + '" onclick="VMMusic.selectBeat(\'' + b.id + '\')">';
      h += '<div class="ms-beat-ct">' + esc(b.city) + '</div>';
      h += '<div class="ms-beat-mt">' + b.bpm + ' BPM \u00b7 ' + b.mood + '</div>';
      h += '<div class="ms-beat-g">' + esc(b.type) + ' \u00b7 ' + esc(b.note) + '</div>';
      h += '<div class="ms-beat-act"><button class="ms-btn sm sec" onclick="event.stopPropagation();VMMusic.previewBeat(\'' + b.id + '\')">' + (previewing ? 'Stop' : 'Preview') + '</button>';
      h += '<span class="ms-beat-dur">3:00</span></div></div>';
    });
    if (!filtered.length) h += '<p style="color:#475569;font-size:13px">No beats match this filter.</p>';
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

  /* -- Main Render ----------------------------------------------------- */
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
    var wsHtml;
    try { wsHtml = renderWorkspace(); } catch (e) { wsHtml = fallbackWorkspace(); }
    var pnlHtml = "";
    if (nav || tool) {
      var dispNav = nav || "tools";
      pnlHtml = '<div class="ms-pnl open">';
      pnlHtml += '<div class="ms-pnl-h"><h3>' + esc(panelLabel(dispNav)) + '</h3>';
      pnlHtml += '<button class="ms-pnl-x" onclick="VMMusic.openNav(\'\')">' + svgX() + '</button></div>';
      if (dispNav === "tools") { pnlHtml += renderToolsSubnav(tool); pnlHtml += '<div class="ms-pnl-b">' + renderToolContent(tool) + '</div>'; }
      else {
        var body = "";
        if (dispNav === "record") body = renderRecordPanel();
        else if (dispNav === "tracks") body = renderTracksPanel();
        else if (dispNav === "generate") body = renderCreatePanel();
        else if (dispNav === "projects") body = renderProjectsPanel();
        pnlHtml += '<div class="ms-pnl-b">' + body + '</div>';
      }
      pnlHtml += '</div>';
    }
    var dur = getEffectiveDur(MS.audio.playing) || 0;
    var pos = MS.audio.position || 0;
    var hasAudio = !!(s.take && s.take.url) || !!(s.beat && s.beat.url) || (s.layers && s.layers.length);
    var playIcon = isPlaying ? svgPause() : svgPlay();
    var baseName = hasAudio ? (s.beat ? s.beat.name || "Recording" : "Recording") : "New Session";
    panel.innerHTML = '<div class="ms-studio">' +
      /* -- Top bar -- */
      '<div class="mse-top">' +
        '<div class="mse-brand">' +
          '<div class="mse-logo">' + svgWaveLogo() + '</div>' +
          '<div><div class="mse-bt">ValleyMind Studio</div><div class="mse-bs">Music Editor</div></div>' +
        '</div>' +
        '<div class="mse-tp">' +
          '<button onclick="VMMusic.seekN(' + (pos > 0.5 ? -0.05 : -0.05) + ')" title="Previous">' + svgPrev() + '</button>' +
          '<button onclick="VMMusic.stopPlayback()" title="Stop">' + svgStop() + '</button>' +
          '<button class="mse-rec' + (isRec ? ' mse-rec' : '') + '" onclick="VMMusic.toggleRecord()" title="Record">' + svgMic() + '</button>' +
          '<button class="mse-play" id="msPlayBtn" onclick="VMMusic.togglePlay(' + (MS.audio.playing ? "'" + MS.audio.playing + "'" : "'take'") + ')" title="Play/Pause">' + playIcon + '</button>' +
          '<button onclick="VMMusic.seekN(0.05)" title="Next">' + svgNext() + '</button>' +
        '</div>' +
        '<div class="mse-tc" id="msPlayTime">' + fmtTime(pos) + ' / ' + fmtTime(dur) + '</div>' +
        '<div class="mse-vol">' + svgVol() + '<input type="range" min="0" max="100" value="' + s.mix.master + '" oninput="VMMusic.setMaster(this.value)"></div>' +
        '<button class="mse-menu" onclick="VMMusic.openNav(\'tracks\')" title="Menu">' + svgMenu() + '</button>' +
      '</div>' +
      '<div class="mse-body">' +
        /* -- Left sidebar: FILES / TOOLS / EFFECTS -- */
        '<div class="mse-side" id="mseSide">' +
          '<div class="mse-side-lb">Files</div>' +
          '<div class="mse-file">' +
            '<div class="mse-fn">' + esc(baseName) + '</div>' +
            '<div class="mse-fm">' + fmtTime(dur) + ' &middot; ' + Math.max(1, Math.round((s.take && s.take.sampleRate) || 44100) / 1000) + ' kHz</div>' +
          '</div>' +
          '<div class="mse-side-lb">Tools</div>' +
          '<button class="mse-tbtn' + (tool === "cut" ? ' active' : '') + '" onclick="VMMusic.openTool(\'cut\')"><span class="ic">' + svgTool("cut") + '</span>Cut</button>' +
          '<button class="mse-tbtn' + (tool === "copy" ? ' active' : '') + '" onclick="VMMusic.openTool(\'copy\')"><span class="ic">' + svgTool("copy") + '</span>Copy</button>' +
          '<button class="mse-tbtn' + (tool === "trim" ? ' active' : '') + '" onclick="VMMusic.openTool(\'trim\')"><span class="ic">' + svgTool("trim") + '</span>Trim</button>' +
          '<button class="mse-tbtn' + (tool === "normalize" ? ' active' : '') + '" onclick="VMMusic.openTool(\'normalize\')"><span class="ic">' + svgTool("normalize") + '</span>Normalize</button>' +
          '<button class="mse-tbtn' + (tool === "fade" ? ' active' : '') + '" onclick="VMMusic.openTool(\'fade\')"><span class="ic">' + svgTool("fade") + '</span>Fade In / Out</button>' +
          '<button class="mse-tbtn' + (tool === "reverse" ? ' active' : '') + '" onclick="VMMusic.openTool(\'reverse\')"><span class="ic">' + svgTool("reverse") + '</span>Reverse</button>' +
          '<div class="mse-side-lb">Effects</div>' +
          '<button class="mse-tbtn' + (tool === "eq" ? ' active' : '') + '" onclick="VMMusic.openTool(\'eq\')"><span class="ic">' + svgTool("eq") + '</span>EQ</button>' +
          '<button class="mse-tbtn' + (tool === "comp" ? ' active' : '') + '" onclick="VMMusic.openTool(\'comp\')"><span class="ic">' + svgTool("comp") + '</span>Compressor</button>' +
          '<button class="mse-tbtn' + (tool === "reverb" ? ' active' : '') + '" onclick="VMMusic.openTool(\'reverb\')"><span class="ic">' + svgTool("reverb") + '</span>Reverb</button>' +
          '<button class="mse-tbtn' + (tool === "delay" ? ' active' : '') + '" onclick="VMMusic.openTool(\'delay\')"><span class="ic">' + svgTool("delay") + '</span>Delay</button>' +
          '<button class="mse-tbtn' + (nav === "generate" ? ' active' : '') + '" onclick="VMMusic.openNav(\'generate\')"><span class="ic">' + svgTool("ai") + '</span>AI Create</button>' +
          '<button class="mse-tbtn' + (nav === "projects" ? ' active' : '') + '" onclick="VMMusic.openNav(\'projects\')"><span class="ic">' + svgTool("projects") + '</span>Projects</button>' +
        '</div>' +
        /* -- Center: waveform editor -- */
        '<div class="mse-center">' +
          '<div class="mse-wave-wrap">' +
            '<canvas id="mseWaveMain"></canvas>' +
            (hasAudio ? '' : '<div class="mse-hint"><svg class="ic" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#00e5ff" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12h3M7 12h1M11 12h2M16 12h1M19 12h3"/><path d="M4.5 7.2c6-8 6 15.6 0 9.6"/><path d="M8.5 15c1.5-9 1.5 6 0-1.4"/></svg><b>Start your song</b><br>Record your voice, upload a file,<br>or use AI Create to begin</div>') +
          '</div>' +
          '<div class="mse-ovwrap"><canvas id="mseWaveOv"></canvas></div>' +
        '</div>' +
        /* -- Right: PROPERTIES + EQUALIZER -- */
        '<div class="mse-right" id="mseRight">' +
          '<div class="mse-card">' +
            '<div class="mse-card-t">Properties</div>' +
            '<div class="mse-prop"><span class="k">Start</span><span class="v">0:00.000</span></div>' +
            '<div class="mse-prop"><span class="k">Length</span><span class="v">' + fmtTime(dur) + '</span></div>' +
            '<div class="mse-prop"><span class="k">End</span><span class="v">' + fmtTime(dur) + '</span></div>' +
            '<div class="mse-prop"><span class="k">Sample Rate</span><span class="v">' + Math.round(((s.take && s.take.sampleRate) || 44100) / 1000) + ' kHz</span></div>' +
            '<div class="mse-prop"><span class="k">Channels</span><span class="v">Mono</span></div>' +
            '<div class="mse-prop"><span class="k">Bit Depth</span><span class="v">32-bit float</span></div>' +
          '</div>' +
          '<div class="mse-card">' +
            '<div class="mse-card-t">Equalizer</div>' +
            '<div class="mse-eq"><canvas id="mseEq"></canvas></div>' +
            '<div class="mse-eqctl">' +
              '<input type="range" min="0" max="100" value="50" oninput="VMMusic.touchEq(event)">' +
              '<input type="range" min="0" max="100" value="50" oninput="VMMusic.touchEq(event)">' +
              '<input type="range" min="0" max="100" value="50" oninput="VMMusic.touchEq(event)">' +
            '</div>' +
          '</div>' +
          '<div class="mse-card"><div class="mse-card-t">Mix</div>' +
            sliceButtons(NAV) +
          '</div>' +
        '</div>' +
      '</div>' +
      /* -- Status bar -- */
      '<div class="mse-status">' +
        '<span>Format: ' + (hasAudio ? 'WAV' : '�') + '</span>' +
        '<span>Sample Rate: ' + Math.round(((s.take && s.take.sampleRate) || 44100) / 1000) + ' kHz</span>' +
        '<span>Bit Depth: 32-bit float</span>' +
        '<span>Channels: Mono</span>' +
        '<span class="mse-stop">' + (isRec ? 'REC' : (isPlaying ? 'PLAYING' : 'STOPPED')) + '</span>' +
        '<span class="mse-saves' + (svCls ? ' ok' : '') + '">' + esc(MS.ui.saveState) + '</span>' +
      '</div>' +
      pnlHtml + '</div>';
    refreshLucide();
    requestAnimationFrame(function () { drawEditorWave(); });
  }
  function sliceButtons(NAVlist) {
    var out = "";
    for (var i = 0; i < NAVlist.length; i++) {
      var n = NAVlist[i];
      out += '<button class="ms-btn sec sm" style="margin:0 4px 6px 0" onclick="VMMusic.openNav(\'' + n.id + '\')">' + esc(n.label) + '</button>';
    }
    return out;
  }
  function seekFromBar(e) {
    var bar = document.getElementById("msSeekBar"); if (!bar) return;
    var rect = bar.getBoundingClientRect();
    var pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    if (!MS.audio.playing) { togglePlay("take"); }
    seekTo(pct);
  }

  /* -- API Surface ----------------------------------------------------- */
  window.VMMusic = {
    render: render, openNav: openNav, openTool: openTool,
    toggleRecord: toggleRecord, stopRecord: stopRecord,
    togglePlay: togglePlay, pausePlayback: pausePlayback, stopPlayback: stopPlayback, seekTo: seekTo,
    seekN: function (dt) { var a = MS.audio.el; if (!a || !MS.audio.playing) return; var cur = MS.audio.position || 0; var d = getEffectiveDur(MS.audio.playing) || 1; var t = Math.max(0, Math.min(d, cur + dt)); var pct = d > 0 ? t / d : 0; a.currentTime = Math.min(t, a.duration || t); MS.audio.position = t; MS.audio.loopBase = 0; MS.audio.lastTime = a.currentTime; },
    touchEq: function (ev) {
      var inputs = document.querySelectorAll(".mse-eqctl input");
      var vals = [];
      for (var i = 0; i < inputs.length; i++) vals.push(Number(inputs[i].value));
      MS.state.eq = vals; var eq = document.getElementById("mseEq"); if (eq) drawEqCurve(eq);
      if (MS.audio.el && MS.audio.playing && window.AudioContext) {
        try {
          if (!MS.audio.eqCtx) { MS.audio.eqCtx = new (window.AudioContext || window.webkitAudioContext)(); MS.audio.eqSrc = MS.audio.eqCtx.createMediaElementSource(MS.audio.el); MS.audio.eqFilters = []; for (var b = 0; b < 4; b++) { var f = MS.audio.eqCtx.createBiquadFilter(); f.type = "peaking"; f.frequency.value = [80, 400, 2000, 8000][b]; f.Q.value = 0.9; MS.audio.eqFilters.push(f); } MS.audio.eqFilters.reduce(function (p, c) { p.connect(c); return c; }); MS.audio.eqSrc.connect(MS.audio.eqFilters[0]); MS.audio.eqFilters[MS.audio.eqFilters.length - 1].connect(MS.audio.eqCtx.destination); }
        } catch (e) { }
      }
    },
    setFocusNav: function (n) { MS.ui.activeNav = n; render(); },
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
    setBeatType: setBeatType,
    setBeatMood: setBeatMood,
    newSong: newSong, loadSong: loadSong, deleteSong: deleteSong, saveSong: saveSong, exportSong: exportSong
  };

  /* -- Show hook (wired to index.html vmWsGo("music")) ----------------- */
  function onShow() {
    render();
  }
  window.vmMusicOnShow = onShow;
  if (window.VMMusic) window.VMMusic.onShow = onShow;

  /* -- Init ------------------------------------------------------------ */
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
