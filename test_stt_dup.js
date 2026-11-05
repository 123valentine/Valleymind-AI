// STT buffer tests (vm-fix15): committed (finals only) + single latest interim (replaced).
// Logic mirrors index.html onresult rebuild.

function makeBuffer() {
  return { committed: "", interim: "", committedBase: "" };
}

function startSession(buf) {
  buf.committedBase = (buf.committed || "").trim();
  buf.interim = "";
}

function onresult(buf, ev) {
  // Rebuild from FULL results each time (current session).
  var finals = [];
  var latestInterim = "";
  for (var i = 0; i < ev.results.length; i++) {
    var res = ev.results[i];
    var t = (res && res[0]) ? (res[0].transcript || "") : "";
    var trimmed = t.trim();
    if (!trimmed) continue;
    if (res.isFinal) {
      finals.push(trimmed);
    } else {
      if (!latestInterim || trimmed.toLowerCase().indexOf(latestInterim.toLowerCase()) === 0 || latestInterim.toLowerCase().indexOf(trimmed.toLowerCase()) === 0 || trimmed.length >= latestInterim.length) {
        latestInterim = trimmed;
      } else {
        latestInterim = trimmed;
      }
    }
  }
  var deduped = [];
  for (var f = 0; f < finals.length; f++) {
    if (deduped.length === 0 || deduped[deduped.length - 1].toLowerCase() !== finals[f].toLowerCase()) {
      deduped.push(finals[f]);
    }
  }
  var sessionFinals = deduped.join(" ");
  buf.committed = (buf.committedBase ? buf.committedBase + (sessionFinals ? " " : "") : "") + sessionFinals;
  buf.interim = latestInterim;
  return buf;
}

function textOf(buf) {
  return ((buf.committed || "") + (buf.committed && buf.interim ? " " : "") + (buf.interim || "")).trim();
}

function onRestart(buf) {
  if (buf.interim && buf.interim.trim()) {
    buf.committed = (buf.committed && buf.committed.trim() ? buf.committed.trim() + " " : "") + buf.interim.trim();
    buf.interim = "";
  }
}

function R(transcript, isFinal) { return [{ transcript: transcript }, , isFinal]; }
// helper builds SpeechRecognition-like result
function res(transcript, isFinal) { return { 0: { transcript: transcript }, isFinal: !!isFinal, length: 1 }; }
function ev(results) { return { resultIndex: 0, results: results }; }

var ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log("OK: " + name); ok++; }
  else { console.log("FAIL: " + name + (detail ? " :: " + detail : "")); fail++; }
}

// Case 1: cumulative interims ["I","I heard","I heard you","I heard you told"] -> exactly "I heard you told"
(function () {
  var b = makeBuffer();
  var seq = ["I", "I heard", "I heard you", "I heard you told"];
  for (var i = 0; i < seq.length; i++) {
    // each event shows the growing interim as the sole non-final result
    onresult(b, ev([res(seq[i], false)]));
  }
  var t = textOf(b);
  assert("cumulative interims -> exactly final phrase", t === "I heard you told", JSON.stringify(t));
})();

// Case 2: two sentences with a restart in between give each sentence once
(function () {
  var b = makeBuffer();
  startSession(b);
  // first sentence finalizes in session 1
  onresult(b, ev([res("Hello there", true)]));
  onresult(b, ev([res("Hello there", true), res("how are", false)])); // interim
  onRestart(b); // restart moves interim to committed once
  // session 2 (new recognition, fresh ev.results) produces second sentence
  startSession(b);
  onresult(b, ev([res("you today", true)]));
  var t = textOf(b);
  var hasHello = t.toLowerCase().indexOf("hello there") >= 0;
  var hasHow = t.toLowerCase().indexOf("how are") >= 0;
  var hasYou = t.toLowerCase().indexOf("you today") >= 0;
  // "hello there" and "how are" and "you today" should each appear once
  var count = function (s, sub) { var n=0,i=0,l=sub.toLowerCase(),S=s.toLowerCase(); while((i=S.indexOf(l,i))>=0){n++;i+=l.length;} return n; };
  assert("two sentences across restart each once",
    hasHello && hasHow && hasYou && count(t,"hello there")===1 && count(t,"how are")===1 && count(t,"you today")===1,
    JSON.stringify(t));
})();

// Case 3: the bad concatenated sample must never appear
(function () {
  var b = makeBuffer();
  var fragments = ["I", "I heard", "I heard I", "I heard I heard", "I heard I heard you", "I heard you told", "I heard you told your"];
  for (var i = 0; i < fragments.length; i++) {
    // simulate messy engine where it may re-emit; rebuild from full results as growing interims
    var results = [];
    for (var j = 0; j <= i; j++) results.push(res(fragments[j], j < i ? false : false));
    // model as: previous non-finals superseded, current latest is fragments[i]
    onresult(b, ev([res(fragments[i], false)]));
  }
  var t = textOf(b);
  var bad = "I I heard I heard I heard you";
  var hasDoubleIHeard = /I\s+I\s+heard\s+I\s+heard/i.test(t);
  assert("no concatenated interim duplication sample", !hasDoubleIHeard && t.indexOf(bad) < 0, JSON.stringify(t));
  // and it should be a single coherent phrase (not exploded)
  assert("buffer is not exploded", t.split(/\s+/).length < 15, "words=" + t.split(/\s+/).length + " " + JSON.stringify(t));
})();

// Case 4: finals are committed once by index (rebuild idempotent)
(function () {
  var b = makeBuffer();
  startSession(b);
  var ev1 = ev([res("one", true), res("two", false)]);
  onresult(b, ev1);
  onresult(b, ev1); // same event again (idempotent rebuild)
  var t = textOf(b);
  assert("rebuild idempotent finals once", t === "one two", JSON.stringify(t));
})();

console.log("STT_BUFFER: " + ok + " passed, " + fail + " failed");
if (fail) process.exit(1);
