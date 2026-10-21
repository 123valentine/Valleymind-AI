/*
 * companion_turn_sm.js — the tap-to-talk turn state machine.
 *
 *   IDLE → LISTENING → PROCESSING → SPEAKING → IDLE
 *
 * Pure reducer: send(event) -> { state, effects: [...] }. It owns NO DOM, NO
 * timers and NO network — the overlay page fires events (tap, end-of-speech,
 * transcript, chat reply, playback end, watchdogs) and executes the returned
 * effects through the EXISTING systems (VMCloudVoice for listening/speaking,
 * POST /api/cloud/chat for the same brain, engine.trigger for the avatar).
 *
 * Guarantees this file encodes (each one is covered by
 * tests/_companion_turn_sm_test.js):
 *   - the microphone only ever captures while LISTENING (never during SPEAKING,
 *     so the companion never transcribes its own TTS reply);
 *   - a tap while SPEAKING interrupts playback and switches to LISTENING;
 *   - a tap while PROCESSING aborts the in-flight request and listens again;
 *   - no AI submission happens for empty/short/absent speech;
 *   - the same utterance is never submitted twice inside the dedupe window;
 *   - every error path lands back in IDLE — the character cannot get stuck;
 *   - a SPEAKING watchdog exists so a silent TTS failure still frees the turn.
 *
 * Loaded as a classic script (window.VMTurnSM) by the overlay page, and
 * require()-able by the Node test runner. It is NOT a second AI, state store or
 * animation controller — just the decision table for one conversation turn.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof globalThis !== "undefined") globalThis.VMTurnSM = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var STATES = {
    IDLE: "idle",
    LISTENING: "listening",
    PROCESSING: "processing",
    SPEAKING: "speaking",
  };

  var EFFECTS = {
    START_LISTEN: "start-listen",   // open the microphone (STT path chosen by the page)
    STOP_LISTEN: "stop-listen",     // close the microphone
    TRANSCRIBE: "transcribe",       // blob -> POST /api/editing/transcribe (Groq Whisper)
    CHAT: "chat",                   // text -> POST /api/cloud/chat (the ONE brain)
    SPEAK: "speak",                 // reply -> VMCloudVoice.speak / system TTS
    STOP_SPEAK: "stop-speak",       // interrupt current playback
    ABORT: "abort",                 // cancel the in-flight transcribe/chat request
    FEEDBACK: "feedback",           // transient mic-indicator cue (never a status box)
  };

  function textLen(v) {
    return typeof v === "string" ? v.trim().length : 0;
  }

  function create(opts) {
    var o = opts || {};
    var now = typeof o.now === "function" ? o.now : function () { return Date.now(); };
    var dedupeWindowMs = o.dedupeWindowMs != null ? o.dedupeWindowMs : 15000;

    var state = STATES.IDLE;
    var lastChat = { text: "", at: -Infinity };

    function resetTimers() { /* timers are owned by the page; reset() emits stops */ }

    /** Dedupe gate: identical utterance inside the window -> no second CHAT. */
    function chatEffects(text, effects) {
      var clean = String(text || "").trim();
      var t = now();
      if (clean && clean === lastChat.text && t - lastChat.at < dedupeWindowMs) {
        effects.push({ type: EFFECTS.FEEDBACK, message: "duplicate" });
        return false;
      }
      lastChat = { text: clean, at: t };
      effects.push({ type: EFFECTS.CHAT, text: clean });
      return true;
    }

    function send(event) {
      var ev = event || {};
      var effects = [];
      var next = state;

      if (ev.type === "RESET") {
        // Recovery from any state: release everything, land in idle.
        effects = [];
        if (state === STATES.LISTENING) effects.push({ type: EFFECTS.STOP_LISTEN });
        effects.push({ type: EFFECTS.STOP_SPEAK }, { type: EFFECTS.ABORT });
        state = STATES.IDLE;
        return { state: state, effects: effects };
      }

      switch (state) {
        case STATES.IDLE:
          if (ev.type === "TAP") {
            next = STATES.LISTENING;
            effects.push({ type: EFFECTS.START_LISTEN });
          }
          break;

        case STATES.LISTENING:
          if (ev.type === "TAP") {
            if (textLen(ev.text) >= 2) {
              next = STATES.PROCESSING;
              effects.push({ type: EFFECTS.STOP_LISTEN });
              chatEffects(ev.text, effects);
            } else {
              next = STATES.IDLE;
              effects.push({ type: EFFECTS.STOP_LISTEN });
            }
          } else if (ev.type === "SPEECH_FINAL") {
            if (textLen(ev.text) < 2) {
              next = STATES.IDLE;
              effects.push({ type: EFFECTS.STOP_LISTEN });
              effects.push({ type: EFFECTS.FEEDBACK, message: "no-speech" });
            } else {
              next = STATES.PROCESSING;
              effects.push({ type: EFFECTS.STOP_LISTEN });
              var ok = chatEffects(ev.text, effects);
              if (!ok) next = STATES.IDLE; // duplicate: do not submit, do not wait
            }
          } else if (ev.type === "VAD_END") {
            if (ev.hadSpeech && ev.blob) {
              next = STATES.PROCESSING;
              effects.push({ type: EFFECTS.STOP_LISTEN });
              effects.push({ type: EFFECTS.TRANSCRIBE, blob: ev.blob });
            } else {
              next = STATES.IDLE;
              effects.push({ type: EFFECTS.STOP_LISTEN });
              effects.push({ type: EFFECTS.FEEDBACK, message: "no-speech" });
            }
          } else if (ev.type === "SPEECH_TIMEOUT") {
            next = STATES.IDLE;
            effects.push({ type: EFFECTS.STOP_LISTEN });
            effects.push({ type: EFFECTS.FEEDBACK, message: "no-speech" });
          }
          break;

        case STATES.PROCESSING:
          if (ev.type === "TAP") {
            next = STATES.LISTENING;
            effects.push({ type: EFFECTS.ABORT }, { type: EFFECTS.START_LISTEN });
          } else if (ev.type === "TRANSCRIBE_OK") {
            if (textLen(ev.text) < 2) {
              next = STATES.IDLE;
              effects.push({ type: EFFECTS.FEEDBACK, message: "no-speech" });
            } else {
              var ok2 = chatEffects(ev.text, effects);
              if (!ok2) next = STATES.IDLE;
            }
          } else if (ev.type === "TRANSCRIBE_EMPTY") {
            next = STATES.IDLE;
            effects.push({ type: EFFECTS.FEEDBACK, message: "no-speech" });
          } else if (ev.type === "TRANSCRIBE_ERR") {
            next = STATES.IDLE;
            effects.push({ type: EFFECTS.FEEDBACK, message: "network" });
          } else if (ev.type === "CHAT_OK") {
            next = STATES.SPEAKING;
            effects.push({ type: EFFECTS.SPEAK, reply: String(ev.reply || "") });
          } else if (ev.type === "CHAT_ERR") {
            next = STATES.IDLE;
            effects.push({ type: EFFECTS.FEEDBACK, message: "network" });
          } else if (ev.type === "PROCESSING_TIMEOUT") {
            next = STATES.IDLE;
            effects.push({ type: EFFECTS.ABORT });
            effects.push({ type: EFFECTS.FEEDBACK, message: "network" });
          }
          // Late STT/chat events from a superseded turn are ignored on purpose.
          break;

        case STATES.SPEAKING:
          if (ev.type === "TAP") {
            next = STATES.LISTENING;
            effects.push({ type: EFFECTS.STOP_SPEAK }, { type: EFFECTS.START_LISTEN });
          } else if (ev.type === "SPEAK_END") {
            next = STATES.IDLE;
          } else if (ev.type === "SPEAK_ERR") {
            next = STATES.IDLE;
            effects.push({ type: EFFECTS.FEEDBACK, message: "tts" });
          } else if (ev.type === "SPEAK_TIMEOUT") {
            next = STATES.IDLE;
            effects.push({ type: EFFECTS.STOP_SPEAK });
            effects.push({ type: EFFECTS.FEEDBACK, message: "tts" });
          }
          break;
      }

      state = next;
      resetTimers();
      return { state: state, effects: effects };
    }

    return {
      get state() { return state; },
      STATES: STATES,
      EFFECTS: EFFECTS,
      send: send,
      reset: function () { return send({ type: "RESET" }); },
      dedupeWindowMs: dedupeWindowMs,
    };
  }

  return { STATES: STATES, EFFECTS: EFFECTS, create: create, version: 1 };
});
