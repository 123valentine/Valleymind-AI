// Pure-node test runner for static/companion_turn_sm.js — the HANDS-FREE turn
// state machine. No DOM: require()s the REAL module, drives it with explicit
// events and asserts the exact state + effect sequences that the overlay page
// will execute. Prints a JSON summary and exits non-zero on failure.
//
// The contract under test:
//   IDLE --TAP--> WAKING --WAKE_DONE--> LISTENING --speech--> PROCESSING
//        --reply--> SPEAKING --SPEAK_END--> LISTENING (the hands-free loop)
// Every failure path must land in a usable state, and the failure budget must
// be BOUNDED so a dead microphone can never spin the loop forever.
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

// A full hands-free turn: tap -> wake -> listen -> brain -> speak -> listen.
function awake(f) {
  f.send({ type: "TAP" });
  return f.send({ type: "WAKE_DONE" });
}

// ── 1. happy path, and the loop that makes it hands-free ──────────────────
{
  const f = make();
  eq(f.state, STATES.IDLE, "starts idle");
  let r = f.send({ type: "TAP" });
  eq(r.state, STATES.WAKING, "a tap wakes the companion");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.WAKE]), "the tap only wakes");
  check(!f.sessionActive === false, "the tap opens a hands-free session");

  r = f.send({ type: "WAKE_DONE" });
  eq(r.state, STATES.LISTENING, "the wake opens the microphone");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.START_LISTEN]), "it only opens the mic");

  r = f.send({ type: "SPEECH_FINAL", text: "hello there" });
  eq(r.state, STATES.PROCESSING, "a final transcript starts processing");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.STOP_LISTEN, EFFECTS.CHAT]),
     "the transcript stops the mic and submits to the ONE brain");
  eq(effOf(r, EFFECTS.CHAT).text, "hello there", "chat carries the transcript text");

  r = f.send({ type: "CHAT_OK", reply: "Hi! How can I help?" });
  eq(r.state, STATES.SPEAKING, "a reply starts speaking");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.SPEAK]), "the reply emits speak");
  eq(effOf(r, EFFECTS.SPEAK).reply, "Hi! How can I help?", "speak carries the reply");

  r = f.send({ type: "SPEAK_END" });
  eq(r.state, STATES.LISTENING, "HANDS-FREE: after speaking it listens again");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.START_LISTEN]),
     "the loop reopens the microphone, with nothing else");
  check(f.sessionActive, "the session is still running after the first reply");
}

// ── 2. the loop keeps going for several turns ─────────────────────────────
{
  const f = make();
  awake(f);
  for (let i = 0; i < 3; i++) {
    f.send({ type: "SPEECH_FINAL", text: `question ${i}` });
    f.send({ type: "CHAT_OK", reply: `answer ${i}` });
    const r = f.send({ type: "SPEAK_END" });
    eq(r.state, STATES.LISTENING, `turn ${i + 1} goes straight back to listening`);
  }
  check(f.sessionActive, "the session survives many turns");
}

// ── 3. STOP ends the session from every state ─────────────────────────────
{
  for (const setup of [
    (f) => f.send({ type: "TAP" }),
    (f) => awake(f),
    (f) => { awake(f); f.send({ type: "SPEECH_FINAL", text: "q" }); },
    (f) => { awake(f); f.send({ type: "SPEECH_FINAL", text: "q" }); f.send({ type: "CHAT_OK", reply: "a" }); },
  ]) {
    const f = make();
    setup(f);
    const r = f.send({ type: "STOP" });
    eq(r.state, STATES.IDLE, "stop always lands in idle");
    check(hasEff(r, EFFECTS.STOP_LISTEN), "stop closes the microphone");
    check(hasEff(r, EFFECTS.STOP_SPEAK), "stop silences playback");
    check(hasEff(r, EFFECTS.ABORT), "stop aborts in-flight work");
    check(hasEff(r, EFFECTS.SESSION_END), "stop ends the session");
    check(!f.sessionActive, "the session is closed after stop");
  }
}

// ── 4. a tap with no speech stops the session ─────────────────────────────
{
  const f = make();
  awake(f);
  const r = f.send({ type: "TAP" });
  eq(r.state, STATES.IDLE, "a tap with no transcript ends the conversation");
  check(hasEff(r, EFFECTS.SESSION_END), "the session ends");
  check(!hasEff(r, EFFECTS.CHAT), "no AI submission without speech");
}

// ── 5. a tap with a held transcript submits it ─────────────────────────────
{
  const f = make();
  awake(f);
  const r = f.send({ type: "TAP", text: "what time is it" });
  eq(r.state, STATES.PROCESSING, "a tap with text starts processing");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.STOP_LISTEN, EFFECTS.CHAT]),
     "it stops the mic and submits the text");
}

// ── 6/7/8/9. silence is survivable, but only for so long ──────────────────
{
  const f = make();
  awake(f);
  let r = f.send({ type: "SPEECH_FINAL", text: "  " });
  eq(r.state, STATES.LISTENING, "an empty transcript keeps the conversation alive");
  check(!hasEff(r, EFFECTS.CHAT), "an empty transcript never reaches the brain");
  check(hasEff(r, EFFECTS.FEEDBACK) && effOf(r, EFFECTS.FEEDBACK).message === "no-speech",
        "an empty transcript is cued");
  check(hasEff(r, EFFECTS.START_LISTEN), "the microphone reopens for the next try");
}
{
  const f = make();
  awake(f);
  const r = f.send({ type: "VAD_END", hadSpeech: false, blob: null });
  eq(r.state, STATES.LISTENING, "silence keeps the conversation alive");
  check(!hasEff(r, EFFECTS.CHAT), "silence never reaches the brain");
  check(!hasEff(r, EFFECTS.TRANSCRIBE), "silence is never transcribed");
}
{
  const f = make();
  awake(f);
  const r = f.send({ type: "VAD_END", hadSpeech: true, blob: { bytes: 7 } });
  eq(r.state, STATES.PROCESSING, "heard speech starts processing");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.STOP_LISTEN, EFFECTS.TRANSCRIBE]),
     "it stops the mic and transcribes the blob");
  eq(effOf(r, EFFECTS.TRANSCRIBE).blob.bytes, 7, "transcribe carries the blob");
}
{
  const f = make();
  awake(f);
  const r = f.send({ type: "SPEECH_TIMEOUT" });
  eq(r.state, STATES.LISTENING, "the no-speech watchdog keeps the conversation alive");
  check(!hasEff(r, EFFECTS.CHAT), "the watchdog never submits");
}

// ── 10. the failure budget is BOUNDED (no runaway listen/retry loop) ──────
{
  const f = make({ maxFailures: 3 });
  awake(f);
  for (let i = 0; i < 3; i++) {
    const r = f.send({ type: "SPEECH_FINAL", text: "" });
    eq(r.state, STATES.LISTENING, `silence ${i + 1} is absorbed`);
  }
  const r = f.send({ type: "SPEECH_FINAL", text: "" });
  eq(r.state, STATES.IDLE, "the fourth silence gives up instead of looping forever");
  check(hasEff(r, EFFECTS.SESSION_END), "giving up ends the session");
  check(!f.sessionActive, "the session is closed when the budget runs out");
}

// ── 11. a successful turn resets the failure budget ───────────────────────
{
  const f = make({ maxFailures: 2 });
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "" });
  f.send({ type: "SPEECH_FINAL", text: "" });
  eq(f.failures, 2, "two silences are charged to the budget");
  f.send({ type: "SPEECH_FINAL", text: "a real question" });
  f.send({ type: "CHAT_OK", reply: "sure" });
  eq(f.failures, 0, "a completed turn clears the budget");
}

// ── 12/13/14/15/16. processing outcomes ───────────────────────────────────
{
  const f = make();
  awake(f);
  f.send({ type: "VAD_END", hadSpeech: true, blob: {} });
  let r = f.send({ type: "TRANSCRIBE_OK", text: "set a timer" });
  eq(r.state, STATES.PROCESSING, "a transcript keeps the turn in processing");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.CHAT]), "the transcript is submitted");
}
{
  const f = make();
  awake(f);
  f.send({ type: "VAD_END", hadSpeech: true, blob: {} });
  let r = f.send({ type: "TRANSCRIBE_EMPTY" });
  eq(r.state, STATES.LISTENING, "an empty transcription keeps the conversation alive");
  check(!hasEff(r, EFFECTS.CHAT), "an empty transcription never reaches the brain");
}
{
  const f = make();
  awake(f);
  f.send({ type: "VAD_END", hadSpeech: true, blob: {} });
  let r = f.send({ type: "TRANSCRIBE_ERR" });
  eq(r.state, STATES.ERROR, "a transcription failure is an error state");
  eq(effOf(r, EFFECTS.FEEDBACK).message, "network", "it cues network feedback");
}
{
  const f = make();
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "tell me a story" });
  const r = f.send({ type: "CHAT_ERR" });
  eq(r.state, STATES.ERROR, "a chat failure is an error state (never stuck)");
  eq(effOf(r, EFFECTS.FEEDBACK).message, "network", "it cues network feedback");
}
{
  const f = make();
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "slow request" });
  const r = f.send({ type: "PROCESSING_TIMEOUT" });
  eq(r.state, STATES.ERROR, "the processing watchdog ends in an error state");
  check(hasEff(r, EFFECTS.ABORT), "the watchdog aborts the in-flight request");
}
{
  const f = make();
  awake(f);
  const r = f.send({ type: "SPEECH_ERR", reason: "permission" });
  eq(r.state, STATES.ERROR, "a recogniser failure is an error state");
  eq(effOf(r, EFFECTS.FEEDBACK).message, "asr", "it cues the recogniser failure");
}

//// ── 17. an error retries, and the retries are bounded ─────────────────────
{
  const f = make({ maxFailures: 2 });
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "hello" });
  f.send({ type: "CHAT_ERR" });
  eq(f.state, STATES.ERROR, "setup: error");
  let r = f.send({ type: "RETRY" });
  eq(r.state, STATES.LISTENING, "a retry goes back to listening");
  check(hasEff(r, EFFECTS.START_LISTEN), "the retry reopens the microphone");
  f.send({ type: "SPEECH_FINAL", text: "hello again" });
  f.send({ type: "CHAT_ERR" });
  eq(f.state, STATES.ERROR, "setup: a second failure");
  r = f.send({ type: "RETRY" });
  eq(r.state, STATES.IDLE, "the retries are bounded: the session gives up");
  check(hasEff(r, EFFECTS.SESSION_END), "giving up ends the session");
}

// ── 18. a tap during processing aborts and listens again ──────────────────
{
  const f = make();
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "long question" });
  const r = f.send({ type: "TAP" });
  eq(r.state, STATES.LISTENING, "a tap during processing listens again");
  eq(JSON.stringify(effTypes(r)), JSON.stringify([EFFECTS.ABORT, EFFECTS.START_LISTEN]),
     "it aborts the in-flight request first, then opens the mic");
}

// ── 19/20/21/22. speaking: interrupt, end, errors, watchdog ───────────────
{
  const f = make();
  awake(f);
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
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "x y" });
  f.send({ type: "CHAT_OK", reply: "done" });
  let r = f.send({ type: "SPEAK_ERR" });
  eq(r.state, STATES.ERROR, "a TTS failure is an error state");
  eq(effOf(r, EFFECTS.FEEDBACK).message, "tts", "it cues tts feedback");
}
{
  const f = make();
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "x y" });
  f.send({ type: "CHAT_OK", reply: "done" });
  const r = f.send({ type: "SPEAK_TIMEOUT" });
  eq(r.state, STATES.ERROR, "the speaking watchdog frees the turn");
  check(hasEff(r, EFFECTS.STOP_SPEAK), "the watchdog stops playback");
}

// ── 23/24/25. dedupe: the same utterance is never submitted twice ─────────
{
  const f = make();
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "thanks a lot" });
  f.send({ type: "CHAT_OK", reply: "anytime" });
  f.send({ type: "SPEAK_END" });
  const r = f.send({ type: "SPEECH_FINAL", text: "thanks a lot" });
  eq(r.state, STATES.IDLE, "an immediate duplicate utterance ends the turn");
  check(!hasEff(r, EFFECTS.CHAT), "the duplicate is NOT submitted again");
  check(hasEff(r, EFFECTS.FEEDBACK) && effOf(r, EFFECTS.FEEDBACK).message === "duplicate",
        "the duplicate is flagged");
  check(!f.sessionActive, "a duplicate closes the session");
}
{
  const f = make();
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "thanks a lot" });
  f.send({ type: "CHAT_OK", reply: "anytime" });
  f.send({ type: "SPEAK_END" });
  clock += f.dedupeWindowMs + 1;              // leave the dedupe window
  const r = f.send({ type: "SPEECH_FINAL", text: "thanks a lot" });
  eq(r.state, STATES.PROCESSING, "after the window the same phrase is a new turn");
  check(hasEff(r, EFFECTS.CHAT), "after the window it IS submitted");
}
{
  // dedupe also guards the transcribe path (recorder fallback)
  const f = make();
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "repeat me" });
  f.send({ type: "CHAT_OK", reply: "ok" });
  f.send({ type: "SPEAK_END" });
  f.send({ type: "VAD_END", hadSpeech: true, blob: {} });
  const r = f.send({ type: "TRANSCRIBE_OK", text: "repeat me" });
  eq(r.state, STATES.IDLE, "a duplicate from the recorder path is dropped too");
  check(!hasEff(r, EFFECTS.CHAT), "no second chat for the same text");
}

// ── 26. late/superseded events are ignored ────────────────────────────────
{
  const f = make();
  awake(f);
  f.send({ type: "SPEECH_FINAL", text: "first" });
  const r1 = f.send({ type: "SPEECH_FINAL", text: "late stt final" });
  eq(r1.state, STATES.PROCESSING, "a late STT final does not change processing");
  eq(r1.effects.length, 0, "a late STT final emits nothing");
  f.send({ type: "CHAT_OK", reply: "answer" });
  const r2 = f.send({ type: "CHAT_OK", reply: "stale answer" });
  eq(r2.state, STATES.SPEAKING, "a stale chat reply does not disturb speaking");
  eq(r2.effects.length, 0, "a stale chat reply emits nothing");
}

// ── 27. RESET recovers from any state ─────────────────────────────────────
{
  for (const setup of [
    (f) => f.send({ type: "TAP" }),
    (f) => awake(f),
    (f) => { awake(f); f.send({ type: "SPEECH_FINAL", text: "q" }); },
    (f) => { awake(f); f.send({ type: "SPEECH_FINAL", text: "q" }); f.send({ type: "CHAT_OK", reply: "a" }); },
  ]) {
    const f = make();
    setup(f);
    const r = f.reset();
    eq(r.state, STATES.IDLE, "reset lands in idle");
    check(hasEff(r, EFFECTS.STOP_SPEAK) && hasEff(r, EFFECTS.ABORT),
          "reset stops playback and aborts requests");
    check(!f.sessionActive, "reset closes the session");
  }
}

// ── 28. invariant sweep: the mic is never open outside LISTENING ──────────
{
  const f = make();
  const trace = [];
  const steps = [
    { type: "TAP" },
    { type: "WAKE_DONE" },
    { type: "SPEECH_FINAL", text: "one two" },
    { type: "CHAT_OK", reply: "spoken" },
    { type: "SPEAK_END" },
    { type: "SPEECH_FINAL", text: "three four" },
    { type: "CHAT_OK", reply: "spoken two" },
    { type: "SPEAK_END" },
    { type: "SPEECH_FINAL", text: "" },        // silence
    { type: "SPEECH_TIMEOUT" },
    { type: "STOP" },
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
    if (r.state === STATES.SPEAKING) {
      check(!r.effects.some((e) => e.type === EFFECTS.START_LISTEN),
            "the mic is closed while the companion speaks (" + ev.type + ")");
    }
  }
  eq(f.state, STATES.IDLE, "the whole sweep ends idle");
  const listenSegments = trace.filter((t) => t.state === STATES.LISTENING);
  eq(listenSegments.length, 5, "the loop opened the microphone five times in the sweep");
}

// ── 29. the state and the session flag never disagree ─────────────────────
{
  const f = make();
  const steps = [
    { type: "TAP" }, { type: "WAKE_DONE" }, { type: "SPEECH_FINAL", text: "hi" },
    { type: "CHAT_OK", reply: "yo" }, { type: "SPEAK_END" }, { type: "STOP" },
  ];
  for (const ev of steps) {
    const r = f.send(ev);
    if (r.state === STATES.IDLE || r.state === STATES.ERROR) {
      check(!f.sessionActive || r.state === STATES.ERROR,
            "idle means the session is closed (" + ev.type + ")");
    }
  }
}

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);
