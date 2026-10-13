"""African music intelligence — the musical brain of the Music workstation.

NOT a generic "African" tag: every genre below carries the concrete musical
facts a producer needs — tempo range, feel, signature instruments, a 16-step
percussion pattern (using the same K/L/k/T/t/H/h/0/- vocabulary the existing
Sing-with-AI engine renders), candidate keys, bass roots and a culture/region
note that is handed to the LLM producer as grounding.

Percussion pattern characters (matches static/music_studio.js):
  K kick · L soft kick · k lighter kick · T snare/clap · t soft snare
  H open hat · h closed hat · 0 hat tick · - rest

Every pattern is exactly 16 characters (one bar of 16 eighth-notes). The test
suite validates length + vocabulary so a typo can never silently reach the
rendering engine.
"""

from __future__ import annotations

# Allowed single-char steps in a percussion pattern (engine vocabulary).
PATTERN_CHARS = set("KLkTtHh0-")

# Note space used for key suggestions and bass-root mapping.
KEYS = [
    "C major", "A minor", "G major", "E minor", "D major", "B minor",
    "A major", "F# minor", "E major", "C# minor", "B major", "G# minor",
    "F major", "D minor", "Eb major", "C minor", "Ab major", "F minor",
    "Bb major", "G minor", "Db major", "Bb minor", "Gb major", "Eb minor",
]

# Enharmonic spelling chosen for the bass root (the bass root of a key IS its
# tonic — spelling favours the readable form used in the African library).
_BASS_ALIASES = {
    "a": "A", "bb": "Bb", "b": "B", "cb": "B",
    "c": "C", "c#": "C#", "db": "C#", "d": "D", "eb": "Eb",
    "e": "E", "fb": "E", "e#": "F", "f": "F", "f#": "F#",
    "gb": "F#", "g": "G", "ab": "Ab",
}


def bass_root_for_key(key: str) -> str:
    """Return the note a producer should center the bass on for a song key.

    Accepts full names ("A minor", "F# minor", "Bb major") or chord shorthands
    ("Am", "F#m", "Bb"). The bass root is the tonic of the key."""
    raw = (key or "").strip().lower().replace("\u266f", "#").replace("\u266d", "b")
    if " " in raw:
        tonic = raw.split()[0].rstrip("m")
    else:
        tonic = raw.rstrip("m")
    if not tonic:
        return "A"
    tonic = tonic.replace("is", "#").replace("es", "b")
    return _BASS_ALIASES.get(tonic, (tonic[0] + ("#" if "#" in tonic else "")).upper() or "A")


GENRES: list[dict] = [
    {
        "id": "afrobeats",
        "label": "Afrobeats",
        "region": "Nigeria / Ghana / West Africa",
        "bpm": 108,
        "bpm_range": (100, 120),
        "feel": "Bold, rhythmic, log-drum drive with melodic brass and bass",
        "instruments": ["Log drums", "Shakers", "Shekere", "Talking drum",
                        "Electric bass", "Key pads", "Brass stabs", "Guitar stabs"],
        "pattern": "K-h-Th-hK-h-Kh-h",
        "vocal_style": "Melismatic, playful delivery; call-and-response hooks; Pidgin/English",
        "keys": ["A minor", "F minor", "E minor", "C major"],
        "culture_note": "Built on the log-drum pulse pioneered by Lagos pioneers; grooves sit "
                        "between 100-120 BPM and the shaker/talking-drum texture is what makes it Afrobeats.",
    },
    {
        "id": "afropop",
        "label": "Afropop",
        "region": "Nigeria / pan-African",
        "bpm": 104,
        "bpm_range": (92, 112),
        "feel": "Pop-forward, melody-first with African rhythmic colour",
        "instruments": ["Programmed drums", "Synth plucks", "Keys", "Bass", "Shakers", "Background vocals"],
        "pattern": "K-h-Th-hK-h-T-hh",
        "vocal_style": "Hook-heavy, catchy melodic choruses; English with West-African flavour",
        "keys": ["C major", "F major", "G major", "A minor"],
        "culture_note": "The commercial crossover sound of Lagos; tempo and structure hug pop "
                        "while the percussion keeps the West-African pocket.",
    },
    {
        "id": "highlife",
        "label": "Highlife",
        "region": "Ghana / Nigeria",
        "bpm": 116,
        "bpm_range": (104, 136),
        "feel": "Sunny, flowing guitar-plucked melodies with brass and congas",
        "instruments": ["Clean electric guitar", "Brass", "Congas", "Bass", "Maracas",
                        "Piano", "Hammond organ"],
        "pattern": "K-h-Th-hK-h-Th-h",
        "vocal_style": "Warm, storytelling tenor; rich harmonies, call-and-response",
        "keys": ["G major", "C major", "F major", "E minor"],
        "culture_note": "The 'ka-ching-ka-ching' guitar rhythm is the signature; brass answers "
                        "the vocal lines and the groove stays relaxed even at brisk tempos.",
    },
    {
        "id": "igbo_highlife",
        "label": "Igbo Highlife",
        "region": "Eastern Nigeria",
        "bpm": 116,
        "bpm_range": (104, 136),
        "feel": "Highlife groove with Igbo melodic phrasing and keyboards",
        "instruments": ["Ogene gong", "Clean guitar", "Keys", "Bass", "Congas",
                        "Talking drum", "Hammond organ"],
        "pattern": "K-h-Kh-Th-Kh-T-h",
        "vocal_style": "Igbo-language storytelling; danceable call-and-response; ngwa-ngwa feel",
        "keys": ["G major", "C major", "F major", "D minor"],
        "culture_note": "Igbo Highlife layers the highlife guitar pulse under Igbo-language "
                        "vocals and the bell-like ogene — pride and dance in one.",
    },
    {
        "id": "amapiano",
        "label": "Amapiano",
        "region": "South Africa",
        "bpm": 110,
        "bpm_range": (106, 114),
        "feel": "Deep log-drum bass, shaker shuffles and melodic piano (yini log drums)",
        "instruments": ["Log drums", "Shakers", "Piano/keys", "Bass", "Pad stabs", "Percussion"],
        "pattern": "Kh-hK-hh-Th-hh-h",
        "vocal_style": "Laid-back, chanting hooks; isiZulu/English lines; low-key delivery",
        "keys": ["F# minor", "C# minor", "B minor", "A major"],
        "culture_note": "Born in the townships of Gauteng; the log drums (kwaito-style "
                        "percussive bass) and piano-led melodies define the pocket around 110 BPM.",
    },
    {
        "id": "afro_rnb",
        "label": "Afro-R&B",
        "region": "Nigeria / alté scene",
        "bpm": 84,
        "bpm_range": (70, 95),
        "feel": "Smooth, breathy, alté-inflected — RnB with African percussion",
        "instruments": ["R&B drums", "808 bass", "Keys", "Guitar plucks", "Shakers", "Synth pads"],
        "pattern": "K-Lh-K-L-Kh-T-hh",
        "vocal_style": "soft, intimate, run-heavy; alté melodies; candid lyrics",
        "keys": ["C minor", "F minor", "Eb major", "A minor"],
        "culture_note": "Chilled alté and Afro-RnB ride 70-95 BPM with 808 sub-bass and "
                        "African shaker texture underneath smooth vocal runs.",
    },
    {
        "id": "afro_fusion",
        "label": "Afro-fusion",
        "region": "pan-African / diaspora",
        "bpm": 106,
        "bpm_range": (92, 122),
        "feel": "Genre-blending engine-room — any African groove fused with pop/rock/soul",
        "instruments": ["Talking drum", "Bass", "Keys", "Guitar", "Shakers", "Drum machine"],
        "pattern": "K-h-Th-K-h-Th-hh",
        "vocal_style": "Playful multilingual delivery; blends any of the West/East African styles",
        "keys": ["F# minor", "E minor", "C major", "B minor"],
        "culture_note": "Afro-fusion borrows the middle-eighth of any African groove and fuses "
                        "it with global sounds — the arrangement should declare its blend.",
    },
    {
        "id": "gospel",
        "label": "Gospel",
        "region": "Nigeria / Ghana / USA",
        "bpm": 96,
        "bpm_range": (72, 140),
        "feel": "Uplifting, choir-backed, organ-and-drums worship and praise",
        "instruments": ["Organ", "Piano", "Drums", "Bass", "Choir vocals", "Shakers"],
        "pattern": "Kh-T-hKh-T-hKh-h",
        "vocal_style": "Powerful, emotive leads with layered choir; adlibs and runs",
        "keys": ["G major", "C major", "Eb major", "F major"],
        "culture_note": "West-African gospel fuses highlife organ, talking-drum accents and "
                        "a massive choir — structure should build verse worship into praise chorus.",
    },
    {
        "id": "nigerian_pop",
        "label": "Nigerian Pop",
        "region": "Nigeria",
        "bpm": 108,
        "bpm_range": (95, 115),
        "feel": "Radio-friendly Lagos pop built on Afrobeat percussion and trap snares",
        "instruments": ["Log drums", "Trap hi-hats", "Bass", "Synth leads", "Shakers", "Brass"],
        "pattern": "K-h-Th-hK-h-T-hh",
        "vocal_style": "Charismatic topline; playful adlibs; Pidgin/English hooks",
        "keys": ["C major", "A minor", "F# minor", "G major"],
        "culture_note": "The commercial fusion of Afrobeats drum language with trap hats and "
                        "global pop songwriting — current Lagos charts.",
    },
    {
        "id": "juju",
        "label": "Jùjú",
        "region": "Yoruba, South-West Nigeria",
        "bpm": 122,
        "bpm_range": (110, 140),
        "feel": "Upbeat Yoruba praise music with talking-drum conversations and pedal steel",
        "instruments": ["Talking drum", "Pedal steel guitar", "Congas", "Rhythm guitar", "Bass"],
        "pattern": "Kh-Th-Kh-Th-Kh-T",
        "vocal_style": "Praise-singing in Yoruba with guitar-vocal call-and-response",
        "keys": ["G major", "E minor", "D major", "A major"],
        "culture_note": "Jùjú's talking drum literally 'talks' between vocal phrases; pedal "
                        "steel colours the melody and the tempo pushes the dance.",
    },
    {
        "id": "fuji",
        "label": "Fuji",
        "region": "Yoruba, South-West Nigeria",
        "bpm": 130,
        "bpm_range": (118, 148),
        "feel": "Fast, percussion-driven Yoruba vocal music",
        "instruments": ["Talking drum", "Congas", "Dundun", "Shakers", "Lead vocal"],
        "pattern": "K-T-KhT-K-T-Kh-0",
        "vocal_style": "Virtuosic, rapid Yoruba vocals; rhythmic elongation of syllables",
        "keys": ["D major", "A major", "G major"],
        "culture_note": "Noise of percussion and vocal agility — the arrangement is drums "
                        "under a single, powerful lead voice.",
    },
    {
        "id": "apala",
        "label": "Apala",
        "region": "Yoruba, South-West Nigeria",
        "bpm": 120,
        "bpm_range": (100, 140),
        "feel": "Hypnotic Yoruba percussion with thumb-piano and talking drum",
        "instruments": ["Talking drum", "Sakara drum", "Shekere", "Tumbi (thumb piano)"],
        "pattern": "Th-hTh-T-Thh-Thh",
        "vocal_style": "Conversational Yoruba chant with percussive mouth music",
        "keys": ["A minor", "D minor", "E minor"],
        "culture_note": "Apala rides layered hand percussion; voice and talking drum answer "
                        "one another in a call-and-response conversation.",
    },
    {
        "id": "makossa",
        "label": "Makossa",
        "region": "Cameroon / Central Africa",
        "bpm": 122,
        "bpm_range": (110, 132),
        "feel": "Bass-heavy dance groove with tight rhythm-guitar chops",
        "instruments": ["Electric bass", "Rhythm guitar", "Horns", "Drums", "Percussion"],
        "pattern": "K-h-Th-K-h-Th-hh",
        "vocal_style": "Douala-rooted melodic chants; tight harmonies",
        "keys": ["G minor", "C minor", "F minor", "Bb major"],
        "culture_note": "Makossa's bass line and staccato rhythm-guitar are the engine; "
                        "horns punctuate the chorus hits.",
    },
    {
        "id": "soukous",
        "label": "Soukous",
        "region": "DRC / East-Central Africa",
        "bpm": 126,
        "bpm_range": (116, 140),
        "feel": "Rumba-at-speed; seben crescendos with Latin-flavoured guitar solos",
        "instruments": ["Lead guitar", "Rhythm guitar", "Congas", "Bass", "Horns"],
        "pattern": "K-h-Th-Kh-Th-hTh",
        "vocal_style": "French/Lingala lyrics; long seben instrumental climaxes",
        "keys": ["G major", "D major", "C major", "A minor"],
        "culture_note": "Soukous builds from rhythm to a 'seben' — a fast, guitar-duelling "
                        "climax where the crowd dances hardest.",
    },
    {
        "id": "mbalax",
        "label": "Mbalax",
        "region": "Senegal / West Africa",
        "bpm": 120,
        "bpm_range": (110, 132),
        "feel": "Talking-drum polyrhythm with Wolof vocals and pop sheen",
        "instruments": ["Tama (talking drum)", "Sabar", "Bass", "Keys", "Guitar", "Horns"],
        "pattern": "Kh-Th-Kh-Th-K-Th",
        "vocal_style": "Wolof vocals; passionate, percussive phrasing",
        "keys": ["C major", "G major", "F major"],
        "culture_note": "Mbalax is powered by tama/sabar polyrhythm; the vocals lock to the "
                        "talking drum's accents.",
    },
    {
        "id": "gqom",
        "label": "Gqom",
        "region": "South Africa (Durban)",
        "bpm": 124,
        "bpm_range": (120, 128),
        "feel": "Minimal, hard drum-machine techno-groove",
        "instruments": ["Drum machine", "Synthesised bass", "Sirens", "Vocoded vocals", "Percussion"],
        "pattern": "K-h-KhKh-Kh-Th-h",
        "vocal_style": "Sparse, hard chants; deep male delivery over the pulse",
        "keys": ["E minor", "G minor", "B minor"],
        "culture_note": "Gqom strips the groove to a drum-machine engine — repetition IS the "
                        "arrangement, building tension by subtracting, not adding.",
    },
    {
        "id": "kwaito",
        "label": "Kwaito",
        "region": "South Africa (Gauteng)",
        "bpm": 96,
        "bpm_range": (88, 104),
        "feel": "Laid-back South-African hip-house with sing-along chants",
        "instruments": ["Kwaito drum machine", "Bass", "Keys", "Chant vocals"],
        "pattern": "Kh-hKh-h-KT-Kh-h",
        "vocal_style": "Slack, sampled speech and choral chanting; township slang",
        "keys": ["C major", "F major", "A minor"],
        "culture_note": "Kwaito's relaxed tempo and chanted toplines are the direct ancestor of "
                        "Amapiano's log-drum sensibility.",
    },
    {
        "id": "kuduro",
        "label": "Kuduro",
        "region": "Angola / Lusophone Africa",
        "bpm": 128,
        "bpm_range": (124, 138),
        "feel": "Festive, percussive techno-afro dance",
        "instruments": ["Percussion", "Drum machine", "Bass", "Synth riff", "Crowd chants"],
        "pattern": "K-T-K-hK-T-K-hh-",
        "vocal_style": "Portuguese chant hooks; festival energy",
        "keys": ["D minor", "A minor", "F major"],
        "culture_note": "Kuduro fuses batida percussion with hard techno energy — relentless, "
                        "minimal and made for the dance floor.",
    },
]

GENRES_BY_ID = {g["id"]: g for g in GENRES}

# Genres test authors type without the underscores (display labels).
_LABEL_TO_ID = {g["label"].lower().replace(" ", "_"): g["id"] for g in GENRES}
_LABEL_TO_ID["rnb"] = "afro_rnb"
_LABEL_TO_ID["afro rnb"] = "afro_rnb"
_LABEL_TO_ID["afro-rnb"] = "afro_rnb"


def resolve_genre(genre: str) -> str:
    """Normalise a user-typed genre to a registry id; '' when unknown."""
    raw = (genre or "").strip().lower()
    if not raw:
        return ""
    if raw in GENRES_BY_ID:
        return raw
    if raw in _LABEL_TO_ID:
        return _LABEL_TO_ID[raw]
    # Tolerant normalisation: "Igbo Highlife" -> "igbo_highlife"
    folded = raw.replace("-", " ").replace("&", " ").replace("+", " ").split()
    for g in GENRES:
        gid = g["id"].replace("_", " ")
        if all(part in gid for part in folded):
            return g["id"]
    return ""


def genre_list() -> list[dict]:
    """Public genre registry (values for dropdowns + advanced create)."""
    return [
        {"id": g["id"], "label": g["label"], "bpm": g["bpm"],
         "bpm_range": list(g["bpm_range"]), "region": g["region"],
         "instruments": g["instruments"]}
        for g in GENRES
    ]


def resolve_pattern(genre: str) -> str:
    """16-step percussion pattern for a genre id/label (validated by tests)."""
    gid = resolve_genre(genre)
    g = GENRES_BY_ID.get(gid) if gid else None
    return (g["pattern"] if g else GENRES_BY_ID["afrobeats"]["pattern"])


def resolve_bpm(genre: str) -> int:
    gid = resolve_genre(genre)
    g = GENRES_BY_ID.get(gid) if gid else None
    return g["bpm"] if g else 108


def resolve_keys(genre: str) -> list[str]:
    gid = resolve_genre(genre)
    g = GENRES_BY_ID.get(gid) if gid else None
    return list(g["keys"]) if g else ["A minor", "C major", "F minor"]


def resolve_instruments(genre: str) -> list[str]:
    gid = resolve_genre(genre)
    g = GENRES_BY_ID.get(gid) if gid else None
    return list(g["instruments"]) if g else GENRES_BY_ID["afrobeats"]["instruments"]


def genre_context(genre: str) -> str:
    """Producer grounding snippet for the LLM — one paragraph of real musical
    fact for the resolved genre (or an honest 'unknown genre' guidance)."""
    gid = resolve_genre(genre)
    g = GENRES_BY_ID.get(gid) if gid else None
    if not g:
        return (
            f"'{genre}' is not a preset genre in the ValleyMind African library. "
            "Treat it as a custom style: read the user's description, pick a sensible "
            "tempo/key and note clearly in the arrangement which African foundations "
            "(percussion, talking drums, bass pocket) you are borrowing."
        )
    return (
        f"Genre: {g['label']} ({g['region']}). "
        f"Tempo {g['bpm_range'][0]}-{g['bpm_range'][1]} BPM (default {g['bpm']}). "
        f"Feel: {g['feel']}. Instruments: {', '.join(g['instruments'])}. "
        f"Vocal style: {g['vocal_style']}. "
        f"Cultural note: {g['culture_note']}."
    )


# Lyric languages reused from the cultural registry (subset that makes musical
# sense for lyrics). Values match core.african_culture LANGUAGES codes.
LYRIC_LANGUAGES = [
    {"code": "en", "label": "English", "note": "Default — works with any genre"},
    {"code": "pcm", "label": "Nigerian Pidgin", "note": "Warm, relatable Naija flavour"},
    {"code": "ig", "label": "Igbo", "note": "Igbo Highlife, Gospel, Amapiano crossover"},
    {"code": "yo", "label": "Yoruba", "note": "Jùjú, Fuji, Apala, Afrobeats praise"},
    {"code": "ha", "label": "Hausa", "note": "Northern Nigerian ballads, Gospel"},
    {"code": "sw", "label": "Swahili", "note": "Bongo-parts, East-African pop"},
    {"code": "fr", "label": "French", "note": "Soukous, Makossa, chanson-afro"},
    {"code": "pt", "label": "Portuguese", "note": "Kuduro, Angolan pop"},
    {"code": "zu", "label": "isiZulu", "note": "Amapiano, Maskandi crossover"},
]


def lyric_language_context(language: str) -> str:
    """Prompt snippet steering lyric language. Honest when unknown."""
    code = (language or "").strip().lower()
    for lng in LYRIC_LANGUAGES:
        if code in (lng["code"], lng["label"].lower()):
            return (f"Write the lyrics mostly in {lng['label']} "
                    f"({lng['note']}). Label the language on the track.")
    return ("Use the language the user requested, matching genre norms "
            "(default English with a light local hook where natural).")