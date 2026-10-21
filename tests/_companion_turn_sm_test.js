// Pure-node test runner for static/companion_turn_sm.js — the tap-to-talk turn
// state machine. No DOM: require()s the REAL module, drives it with explicit
// events and asserts the exact state + effect sequences that the overlay page
// will execute. Prints a JSON summary and exits non-zero on failure.
"use strict";

const sm = require(path_join("static", "companion_turn_sm.js"));

function path_join(...parts) {
  const path = require("path");
  return path.join(__dirname, "..", ...parts);
}

const { STATES, EFFECTS } = sm;
const failures = [];
let passes = 0;

function check(cond, name) { if (cond) passes++; else failures.push(name); }
function eq(got, want, name) {
  check(got === want, name + ` (got ${JSON.stringify(got)} expected ${JSON.stringify(want)})`);
}
function effTypes(res) { return res.effects.map((e) => e.type); }
function hasEff(res, type) { return res.effects.some((e) => e.type === type); }
function effOf(res, type) { return res.effects.find((e) => e.type === type) || null; }

let clock = 1000;
const now = () => clock;

function make(opts) { return sm.create(Object.assign({ now }, opts || {})); }

// ── 1. happy path: tap -> speech -> chat -> speak -> end ────────────────────
{
  const f = make();
  eq(f.state, STATES.IDLE, "starts idle");
  let r = f.send({ type: "TAP" });
  eq(r.state, STATES.LISTENING, "tap from idle listens");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.START_LISTEN]), "tap emits only start-listen");

  r = f.send({ type: "SPEECH_FINAL", text: "hello there" });
  eq(r.state, STATES.PROCESSING, "final transcript starts processing");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.STOP_LISTEN, EFFECTS.CHAT]),
     "transcript stops the mic and submits to the ONE brain");
  eq(effOf(r, EFFECTS.CHAT).text, "hello there", "chat carries the transcript text");

  r = f.send({ type: "CHAT_OK", reply: "Hi! How can I help?" });
  eq(r.state, STATES.SPEAKING, "a reply starts speaking");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.SPEAK]), "reply emits speak");
  eq(effOf(r, EFFECTS.SPEAK).reply, "Hi! How can I help?", "speak carries the reply");

  r = f.send({ type: "SPEAK_END" });
  eq(r.state, STATES.IDLE, "playback end returns to idle");
  eq(r.effects.length, 0, "playback end needs no cleanup effect");
}

// ── 2. a tap with no speech closes the mic and never calls the brain ───────
{
  const f = make();
  f.send({ type: "TAP" });
  const r = f.send({ type: "TAP" });
  eq(r.state, STATES.IDLE, "a tap with no transcript goes back to idle");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.STOP_LISTEN]), "it only closes the mic");
  check(!hasEff(r, EFFECTS.CHAT), "no AI submission without speech");
}

// ── 3. tap with a held transcript (manual stop) submits it ─────────────────
{
  const f = make();
  f.send({ type: "TAP" });
  const r = f.send({ type: "TAP", text: "what time is it" });
  eq(r.state, STATES.PROCESSING, "tap with text starts processing");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.STOP_LISTEN, EFFECTS.CHAT]),
     "it stops the mic and submits the text");
}

// ── 4/5/6/7/8. listening guards: short text, no speech, timeouts ───────────
{
  const f = make();
  f.send({ type: "TAP" });
  let r = f.send({ type: "SPEECH_FINAL", text: "  " });
  eq(r.state, STATES.IDLE, "an empty transcript ends the turn");
  check(!hasEff(r, EFFECTS.CHAT), "an empty transcript never reaches the brain");
  check(hasEff(r, EFFECTS.FEEDBACK) && effOf(r, EFFECTS.FEEDBACK).message === "no-speech",
        "an empty transcript cues the mic indicator");
}
{
  const f = make();
  f.send({ type: "TAP" });
  let r = f.send({ type: "VAD_END", hadSpeech: false, blob: null });
  eq(r.state, STATES.IDLE, "silence ends the turn");
  check(!hasEff(r, EFFECTS.CHAT), "silence never reaches the brain");
  check(!hasEff(r, EFFECTS.TRANSCRIBE), "silence is never transcribed");
}
{
  const f = make();
  f.send({ type: "TAP" });
  let r = f.send({ type: "VAD_END", hadSpeech: true, blob: { bytes: 7 } });
  eq(r.state, STATES.PROCESSING, "heard speech starts processing");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.STOP_LISTEN, EFFECTS.TRANSCRIBE]),
     "it stops the mic and transcribes the blob");
  eq(effOf(r, EFFECTS.TRANSCRIBE).blob.bytes, 7, "transcribe carries the blob");
}
{
  const f = make();
  f.send({ type: "TAP" });
  const r = f.send({ type: "SPEECH_TIMEOUT" });
  eq(r.state, STATES.IDLE, "the no-speech watchdog ends the turn");
  check(!hasEff(r, EFFECTS.CHAT), "the watchdog never submits");
}

// ── 9/10/11/12/13/15. processing outcomes ──────────────────────────────────
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "VAD_END", hadSpeech: true, blob: {} });
  let r = f.send({ type: "TRANSCRIBE_OK", text: "set a timer" });
  eq(r.state, STATES.PROCESSING, "a transcript keeps the turn in processing");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.CHAT]), "the transcript is submitted");
}
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "VAD_END", hadSpeech: true, blob: {} });
  let r = f.send({ type: "TRANSCRIBE_EMPTY" });
  eq(r.state, STATES.IDLE, "an empty transcription ends the turn");
  check(!hasEff(r, EFFECTS.CHAT), "an empty transcription never reaches the brain");
}
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "VAD_END", hadSpeech: true, blob: {} });
  let r = f.send({ type: "TRANSCRIBE_ERR" });
  eq(r.state, STATES.IDLE, "a transcription failure lands in idle");
  eq(effOf(r, EFFECTS.FEEDBACK).message, "network", "it cues network feedback");
}
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "tell me a story" });
  const r = f.send({ type: "CHAT_ERR" });
  eq(r.state, STATES.IDLE, "a chat failure lands in idle (never stuck)");
  eq(effOf(r, EFFECTS.FEEDBACK).message, "network", "it cues network feedback");
}
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "slow request" });
  const r = f.send({ type: "PROCESSING_TIMEOUT" });
  eq(r.state, STATES.IDLE, "the processing watchdog aborts back to idle");
  check(hasEff(r, EFFECTS.ABORT), "the watchdog aborts the in-flight request");
}

// ── 14. a tap DURING processing aborts and listens again ───────────────────
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "long question" });
  const r = f.send({ type: "TAP" });
  eq(r.state, STATES.LISTENING, "a tap during processing listens again");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.ABORT, EFFECTS.START_LISTEN]),
     "it aborts the in-flight request first, then opens the mic");
}

// ── 16/17/18/19. speaking: interrupt, end, errors, watchdog ────────────────
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "long story" });
  f.send({ type: "CHAT_OK", reply: "Once upon a time..." });
  eq(f.state, STATES.SPEAKING, "setup: speaking");
  const r = f.send({ type: "TAP" });
  eq(r.state, STATES.LISTENING, "a tap during speech interrupts into listening");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.STOP_SPEAK, EFFECTS.START_LISTEN]),
     "playback stops BEFORE the mic opens (never both)");
}
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "x y" });
  f.send({ type: "CHAT_OK", reply: "done" });
  let r = f.send({ type: "SPEAK_ERR" });
  eq(r.state, STATES.IDLE, "a TTS failure lands in idle");
  eq(effOf(r, EFFECTS.FEEDBACK).message, "tts", "it cues tts feedback");
}
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "x y" });
  f.send({ type: "CHAT_OK", reply: "done" });
  const r = f.send({ type: "SPEAK_TIMEOUT" });
  eq(r.state, STATES.IDLE, "the speaking watchdog frees the turn");
  check(hasEff(r, EFFECTS.STOP_SPEAK), "the watchdog stops playback");
}

// ── 20/21. dedupe: the same utterance is never submitted twice ─────────────
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "thanks a lot" });
  f.send({ type: "CHAT_OK", reply: "anytime" });
  f.send({ type: "SPEAK_END" });
  f.send({ type: "TAP" });
  const r = f.send({ type: "SPEECH_FINAL", text: "thanks a lot" });
  eq(r.state, STATES.IDLE, "an immediate duplicate utterance ends the turn");
  check(!hasEff(r, EFFECTS.CHAT), "the duplicate is NOT submitted again");
  check(hasEff(r, EFFECTS.FEEDBACK) && effOf(r, EFFECTS.FEEDBACK).message === "duplicate",
        "the duplicate is flagged for the mic indicator");
}
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "thanks a lot" });
  f.send({ type: "CHAT_OK", reply: "anytime" });
  f.send({ type: "SPEAK_END" });
  clock += f.dedupeWindowMs + 1;              // leave the dedupe window
  f.send({ type: "TAP" });
  const r = f.send({ type: "SPEECH_FINAL", text: "thanks a lot" });
  eq(r.state, STATES.PROCESSING, "after the window the same phrase is a new turn");
  check(hasEff(r, EFFECTS.CHAT), "after the window it IS submitted");
}
{
  // dedupe also guards the transcribe path (recorder fallback)
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "repeat me" });
  f.send({ type: "CHAT_OK", reply: "ok" });
  f.send({ type: "SPEAK_END" });
  f.send({ type: "TAP" });
  f.send({ type: "VAD_END", hadSpeech: true, blob: {} });
  const r = f.send({ type: "TRANSCRIBE_OK", text: "repeat me" });
  eq(r.state, STATES.IDLE, "a duplicate from the recorder path is dropped too");
  check(!hasEff(r, EFFECTS.CHAT), "no second chat for the same text");
}

// ── 22. late/superseded events are ignored ─────────────────────────────────
{
  const f = make();
  f.send({ type: "TAP" });
  f.send({ type: "SPEECH_FINAL", text: "first" });
  const r1 = f.send({ type: "SPEECH_FINAL", text: "late stt final" });
  eq(r1.state, STATES.PROCESSING, "a late STT final does not change processing");
  eq(r1.effects.length, 0, "a late STT final emits nothing");
  f.send({ type: "CHAT_OK", reply: "answer" });
  const r2 = f.send({ type: "CHAT_OK", reply: "stale answer" });
  eq(r2.state, STATES.SPEAKING, "a stale chat reply does not disturb speaking");
  eq(r2.effects.length, 0, "a stale chat reply emits nothing");
}

// ── 23. RESET recovers from any state ──────────────────────────────────────
{
  for (const setup of [
    (f) => f.send({ type: "TAP" }),
    (f) => { f.send({ type: "TAP" }); f.send({ type: "SPEECH_FINAL", text: "q" }); },
    (f) => { f.send({ type: "TAP" }); f.send({ type: "SPEECH_FINAL", text: "q" }); f.send({ type: "CHAT_OK", reply: "a" }); },
  ]) {
    const f = make();
    setup(f);
    const r = f.reset();
    eq(r.state, STATES.IDLE, "reset lands in idle from " + f.state);
    check(hasEff(r, EFFECTS.STOP_SPEAK) && hasEff(r, EFFECTS.ABORT),
          "reset stops playback and aborts requests");
  }
}

// ── 24. invariant sweep: the mic is never open outside LISTENING ───────────
{
  const f = make();
  const trace = [];
  const steps = [
    { type: "TAP" },
    { type: "SPEECH_FINAL", text: "one two" },
    { type: "CHAT_OK", reply: "spoken" },
    { type: "TAP" },                              // interrupt speaking
    { type: "VAD_END", hadSpeech: true, blob: {} },
    { type: "TRANSCRIBE_OK", text: "three four" },
    { type: "CHAT_OK", reply: "spoken two" },
    { type: "SPEAK_END" },
    { type: "TAP" },
    { type: "SPEECH_TIMEOUT" },
  ];
  for (const ev of steps) {
    const r = f.send(ev);
    trace.push({ ev: ev.type, state: r.state, eff: effTypes(r) });
    if (r.state !== STATES.LISTENING) {
      check(!r.effects.some((e) => e.type === EFFECTS.START_LISTEN),
            "start-listen only fires when entering LISTENING (" + ev.type + ")");
    }
    if (r.state === STATES.LISTENING) {
      check(!r.effects.some((e) => e.type === EFFECTS.CHAT || e.type === EFFECTS.SPEAK),
            "chat/speak never overlap the open microphone (" + ev.type + ")");
    }
  }
  eq(f.state, STATES.IDLE, "the whole sweep ends idle");
  const listenSegments = trace.filter((t) => t.state === STATES.LISTENING);
  eq(listenSegments.length, 3, "exactly three listening turns in the sweep");
}

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);
