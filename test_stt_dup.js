// STT correctness tests — simulating Android-style SpeechRecognition events.
// Tests the onresult logic in startListening(): build text from LAST result only,
// replace never concatenate interim, append final to baseText once.

let baseText = '';
let sttLastFinal = '';
let value = '';
let startCount = 0;

function onresult(resultIndex, results) {
  // Build text from the LAST result only.
  const lastResult = results[results.length - 1];
  const lastTranscript = lastResult ? (lastResult[0] ? lastResult[0].transcript : "") : "";
  const isFinal = lastResult ? lastResult.isFinal : false;
  if (isFinal) {
    if (lastTranscript && lastTranscript !== sttLastFinal) {
      sttLastFinal = lastTranscript;
      baseText = (baseText || "") + lastTranscript;
    }
  }
  var parts = [];
  if (baseText && baseText.trim()) parts.push(baseText.trim());
  if (lastTranscript && lastTranscript.trim()) parts.push(lastTranscript.trim());
  value = parts.join(" ");
}

function startSession() {
  baseText = value; // existing text kept as base
  sttLastFinal = '';
  startCount++;
}

let passed = 0;
let failed = 0;

function check(name, actual, expected) {
  if (actual === expected) {
    console.log("PASS: " + name + " -> [" + actual + "]");
    passed++;
  } else {
    console.log("FAIL: " + name + " -> expected [" + expected + "] got [" + actual + "]");
    failed++;
  }
}

// (i) Cumulative interims then final -> exactly "tell me the name"
value = ''; baseText = ''; sttLastFinal = '';
onresult(0, [[{ transcript: 'tell', isFinal: false }]], false);
onresult(0, [[{ transcript: 'tell me', isFinal: false }]], false);
onresult(0, [[{ transcript: 'tell me the name', isFinal: true }]], true);
check("(i) cumulative interims then final", value, "tell me the name");

// (ii) Second phrase after final -> "tell me the name of the song"
value = 'tell me the name'; baseText = 'tell me the name'; sttLastFinal = 'tell me the name';
onresult(0, [[{ transcript: 'tell me the name', isFinal: true }], [{ transcript: 'of the song', isFinal: true }]], true);
check("(ii) second phrase after final", value, "tell me the name of the song");

// (iii) Recognition restart in the middle -> no repeated text
value = ''; baseText = ''; sttLastFinal = '';
onresult(0, [[{ transcript: 'hello', isFinal: false }]], false);
onresult(0, [[{ transcript: 'hello world', isFinal: false }]], false);
// restart: baseText stays '' (no committed finals), onend->start() doesn't reset it
onresult(0, [[{ transcript: 'hello world again', isFinal: true }]], true);
check("(iii) restart in middle, no repeat", value, "hello world again");

// (iv) Existing text typed before mic -> kept once
value = 'prefix text'; baseText = 'prefix text'; sttLastFinal = '';
onresult(0, [[{ transcript: 'suffix', isFinal: true }]], true);
check("(iv) existing text kept once", value, "prefix text suffix");

if (failed > 0) {
  console.log("STT TESTS: " + passed + " passed, " + failed + " FAILED");
  process.exit(1);
}
console.log("STT TESTS: all " + passed + " passed");
console.log("STT_DUP_OK");
