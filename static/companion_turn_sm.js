/*
 * companion_turn_sm.js — the HANDS-FREE turn state machine.
 *
 *   IDLE → WAKING → LISTENING → PROCESSING → SPEAKING → LISTENING → …
 *
 * Pure reducer: send(event) -> { state, effects: [...] }. It owns NO DOM, NO
 * timers and NO network — the overlay page fires events (tap, end-of-speech,
 * transcript, chat reply, playback end, watchdogs) and executes the returned
 * effects through the EXISTING systems (native SpeechRecognizer or VMCloudVoice
 * for listening, POST /api/cloud/chat for the same brain, engine.trigger for the
 * avatar, native/system TTS for the voice).
 *
 * HANDS-FREE SESSION
 * A session is one continuous conversation: tap the avatar once and the machine
 * keeps taking turns — listen, think, speak, listen again — until the user
 * stops it. The session is what makes the loop possible, so it is modelled
 * explicitly:
 *
 *   - sessionActive is set when a tap starts the session and cleared only by an
 *     explicit STOP (the discreet stop control), a RESET, or the bounded
 *     failure budget running out.
 *   - While a session is active, SPEAK_END returns to LISTENING instead of IDLE.
 *
 * Guarantees this file encodes (each covered by tests/_companion_turn_sm_test.js):
 *   - the microphone only ever captures while LISTENING (never during SPEAKING,
 *     so the companion never transcribes its own TTS reply);
 *   - after a reply is spoken, listening resumes automatically;
 *   - a tap while SPEAKING interrupts playback and switches to LISTENING;
 *   - a tap while PROCESSING aborts the in-flight request and listens again;
 *   - no AI submission happens for empty/short/absent speech;
 *   - the same utterance is never submitted twice inside the dedupe window;
 *   - every error path lands back in a usable state — the character cannot get stuck;
 *   - a SPEAKING watchdog exists so a silent TTS failure still frees the turn;
 *   - repeated failures are BOUNDED (maxFailures), so a broken microphone or a
 *     dead network can never produce a runaway listen/retry loop;
 *   - the failure budget is reset by every successful turn.
 *
 * Loaded as a classic script (window.VMTurnSM) by the overlay page, and
 * require()-able by the Node test runner. It is NOT a second AI, state store or
 * animation controller — just the decision table for one conversation.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof globalThis !== "undefined") globalThis.VMTurnSM = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var STATES = {
    IDLE: "idle",
    WAKING: "waking",
    LISTENING: "listening",
    PROCESSING: "processing",
    SPEAKING: "speaking",
    ERROR: "error",
  };

  var EFFECTS = {
    WAKE: "wake",                 // play the wake-up animation
    START_LISTEN: "start-listen", // open the microphone (STT path chosen by the page)
    STOP_LISTEN: "stop-listen",   // close the microphone
    TRANSCRIBE: "transcribe",     // blob -> POST /api/editing/transcribe (Groq Whisper)
    CHAT: "chat",                 // text -> POST /api/cloud/chat (the ONE brain)
    SPEAK: "speak",               // reply -> VMCloudVoice.speak / system TTS
    STOP_SPEAK: "stop-speak",     // interrupt current playback
    ABORT: "abort",               // cancel the in-flight transcribe/chat request
    FEEDBACK: "feedback",         // transient cue (never a status box)
    SESSION_END: "session-end",   // the hands-free session is over
  };

  function textLen(v) {
    return typeof v === "string" ? v.trim().length : 0;
  }

  function create(opts) {
    var o = opts || {};
    var now = typeof o.now === "function" ? o.now : function () { return Date.now(); };
    var dedupeWindowMs = o.dedupeWindowMs != null ? o.dedupeWindowMs : 15000;
    // Bounded failure budget: without it a dead microphone or an offline
    // network would spin listen -> fail -> listen forever.
    var maxFailures = o.maxFailures != null ? o.maxFailures : 3;

    var state = STATES.IDLE;
    var sessionActive = false;
    var failures = 0;
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

    /** End the hands-free session: release everything and land in idle. */
    function endSession(effects, reason) {
      effects.push({ type: EFFECTS.STOP_LISTEN });
      effects.push({ type: EFFECTS.STOP_SPEAK });
      effects.push({ type: EFFECTS.ABORT });
      if (reason) effects.push({ type: EFFECTS.FEEDBACK, message: reason });
      sessionActive = false;
      failures = 0;
      effects.push({ type: EFFECTS.SESSION_END, reason: reason || "stopped" });
      return STATES.IDLE;
    }

    /**
     * A recoverable hiccup (silence, empty transcript). The session survives:
     * the mic is closed and reopened so the user can simply carry on. The
     * failure budget is charged, so repeated silence cannot loop forever.
     */
    function softFail(effects, message) {
      failures++;
      if (failures > maxFailures) {
        var end = endSession(effects, message);
        return end;
      }
      effects.push({ type: EFFECTS.STOP_LISTEN });
      effects.push({ type: EFFECTS.FEEDBACK, message: message });
      effects.push({ type: EFFECTS.START_LISTEN });
      return STATES.LISTENING;
    }

    /**
     * A hard failure (network, TTS). The state becomes ERROR so the page can
     * show it honestly, and a bounded number of retries is allowed.
     */
    function hardFail(effects, message) {
      failures++;
      effects.push({ type: EFFECTS.FEEDBACK, message: message });
      return STATES.ERROR;
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
        sessionActive = false;
        failures = 0;
        effects.push({ type: EFFECTS.SESSION_END, reason: "reset" });
        state = STATES.IDLE;
        return { state: state, effects: effects };
      }

      switch (state) {
        case STATES.IDLE:
          if (ev.type === "TAP") {
            // One tap starts the hands-free session: wake, then listen.
            sessionActive = true;
            failures = 0;
            next = STATES.WAKING;
            effects.push({ type: EFFECTS.WAKE });
          }
          break;

        case STATES.WAKING:
          if (ev.type === "WAKE_DONE") {
            next = STATES.LISTENING;
            effects.push({ type: EFFECTS.START_LISTEN });
          } else if (ev.type === "STOP" || ev.type === "TAP") {
            // A second tap during the wake-up cancels the whole session.
            next = endSession(effects, "stopped");
          }
          break;

        case STATES.LISTENING:
          if (ev.type === "WAKE_DONE") {
            // Late wake acknowledgement: the mic is already open.
          } else if (ev.type === "STOP") {
            next = endSession(effects, "stopped");
          } else if (ev.type === "SPEECH_FINAL") {
            if (textLen(ev.text) < 2) {
              next = softFail(effects, "no-speech");
            } else {
              next = STATES.PROCESSING;
              effects.push({ type: EFFECTS.STOP_LISTEN });
              var ok = chatEffects(ev.text, effects);
              // A duplicate is not a new turn: close the session cleanly.
              if (!ok) next = endSession(effects, null);
            }
          } else if (ev.type === "VAD_END") {
            if (ev.hadSpeech && ev.blob) {
              failures = 0;
              next = STATES.PROCESSING;
              effects.push({ type: EFFECTS.STOP_LISTEN });
              effects.push({ type: EFFECTS.TRANSCRIBE, blob: ev.blob });
            } else {
              next = softFail(effects, "no-speech");
            }
          } else if (ev.type === "SPEECH_TIMEOUT") {
            next = softFail(effects, "no-speech");
          } else if (ev.type === "SPEECH_ERR") {
            // The recogniser itself failed (permission, network, engine).
            next = hardFail(effects, "asr");
          } else if (ev.type === "TAP") {
            if (textLen(ev.text) >= 2) {
              failures = 0;
              next = STATES.PROCESSING;
              effects.push({ type: EFFECTS.STOP_LISTEN });
              chatEffects(ev.text, effects);
            } else {
              // A tap on the avatar while the mic is open means "stop".
              next = endSession(effects, "stopped");
            }
          }
          break;

        case STATES.PROCESSING:
          if (ev.type === "STOP") {
            next = endSession(effects, "stopped");
          } else if (ev.type === "TAP") {
            next = STATES.LISTENING;
            effects.push({ type: EFFECTS.ABORT }, { type: EFFECTS.START_LISTEN });
          } else if (ev.type === "TRANSCRIBE_OK") {
            if (textLen(ev.text) < 2) {
              next = softFail(effects, "no-speech");
            } else {
              var ok2 = chatEffects(ev.text, effects);
              if (!ok2) next = endSession(effects, null);
            }
          } else if (ev.type === "TRANSCRIBE_EMPTY") {
            next = softFail(effects, "no-speech");
          } else if (ev.type === "TRANSCRIBE_ERR") {
            next = hardFail(effects, "network");
          } else if (ev.type === "CHAT_OK") {
            // A complete, successful turn clears the failure budget.
            failures = 0;
            next = STATES.SPEAKING;
            effects.push({ type: EFFECTS.SPEAK, reply: String(ev.reply || "") });
          } else if (ev.type === "CHAT_ERR") {
            next = hardFail(effects, "network");
          } else if (ev.type === "PROCESSING_TIMEOUT") {
            effects.push({ type: EFFECTS.ABORT });
            next = hardFail(effects, "network");
          }
          // Late STT/chat events from a superseded turn are ignored on purpose.
          break;

        case STATES.SPEAKING:
          if (ev.type === "STOP") {
            next = endSession(effects, "stopped");
          } else if (ev.type === "TAP") {
            next = STATES.LISTENING;
            effects.push({ type: EFFECTS.STOP_SPEAK }, { type: EFFECTS.START_LISTEN });
          } else if (ev.type === "SPEAK_END") {
            if (sessionActive) {
              // Hands-free: the machine goes straight back to listening.
              next = STATES.LISTENING;
              effects.push({ type: EFFECTS.START_LISTEN });
            } else {
              next = STATES.IDLE;
            }
          } else if (ev.type === "SPEAK_ERR") {
            next = hardFail(effects, "tts");
          } else if (ev.type === "SPEAK_TIMEOUT") {
            effects.push({ type: EFFECTS.STOP_SPEAK });
            next = hardFail(effects, "tts");
          }
          break;

        case STATES.ERROR:
          if (ev.type === "STOP") {
            next = endSession(effects, "stopped");
          } else if (ev.type === "RETRY" || ev.type === "TAP") {
            if (failures >= maxFailures) {
              next = endSession(effects, "gave-up");
            } else {
              next = STATES.LISTENING;
              effects.push({ type: EFFECTS.START_LISTEN });
            }
          }
          break;
      }

      state = next;
      resetTimers();
      return { state: state, effects: effects };
    }

    return {
      get state() { return state; },
      get sessionActive() { return sessionActive; },
      get failures() { return failures; },
      STATES: STATES,
      EFFECTS: EFFECTS,
      send: send,
      reset: function () { return send({ type: "RESET" }); },
      dedupeWindowMs: dedupeWindowMs,
      maxFailures: maxFailures,
    };
  }

  return { STATES: STATES, EFFECTS: EFFECTS, create: create, version: 2 };
});
