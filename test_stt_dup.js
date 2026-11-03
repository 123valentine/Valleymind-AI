// STT correctness tests — simulating Android-style SpeechRecognition events.
// Tests the piece-based onresult logic:
//   - Loop over ev.results, keep pieces list
//   - If piece.text (trimmed, case-insensitive) startsWith previous piece.text, REPLACE previous
//   - Otherwise push
//   - Final text = baseText + pieces.join(" ")
//   - Commit pieces into baseText only when session ends, never mid-event.

let baseText = '';
let value = '';

function onresult(resultIndex, results) {
  var pieces = [];
  for (var i = resultIndex; i < results.length; i++) {
    var t = results[i][0] ? results[i][0].transcript : "";
    var trimmed = t.trim();
    var lower = trimmed.toLowerCase();
    if (pieces.length > 0) {
      var prev = pieces[pieces.length - 1];
      var prevLower = prev.trimmed.toLowerCase();
      if (lower.startsWith(prevLower) && lower.length > prev.trimmed.length) {
        pieces[pieces.length - 1] = { trimmed: trimmed, text: t };
        continue;
      }
    }
    pieces.push({ trimmed: trimmed, text: t });
  }
  var allParts = [];
  if (baseText && baseText.trim()) allParts.push(baseText.trim());
  pieces.forEach(function (p) { if (p.trimmed) allParts.push(p.trimmed); });
  value = allParts.join(" ");
}

function commitToBaseText() {
  if (value && value.trim()) baseText = value.trim();
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

// (i) Cumulative interims "the","the Shadows","the Shadows in","the Shadows in your breath" -> exactly once
value = ''; baseText = '';
onresult(0, [[{ transcript: 'the', isFinal: false }]], false);
onresult(0, [[{ transcript: 'the Shadows', isFinal: false }]], false);
onresult(0, [[{ transcript: 'the Shadows in', isFinal: false }]], false);
onresult(0, [[{ transcript: 'the Shadows in your breath', isFinal: true }]], true);
check("(i) cumulative interims -> final phrase once", value, "the Shadows in your breath");

// (ii) Second phrase after a pause (session ended, baseText committed)
commitToBaseText();
value = '';  // new session starts with empty value, baseText has previous
onresult(0, [[{ transcript: 'of the song', isFinal: true }]], true);
check("(ii) second phrase after pause", value, "the Shadows in your breath of the song");

// (iii) Recognition restart in the middle -> no repeated text
value = ''; baseText = '';
onresult(0, [[{ transcript: 'hello', isFinal: false }]], false);
onresult(0, [[{ transcript: 'hello world', isFinal: false }]], false);
// restart: pieces not committed to baseText yet, only lastTranscript shows
onresult(0, [[{ transcript: 'hello world again', isFinal: true }]], true);
check("(iii) restart in middle, no repeat", value, "hello world again");

// (iv) Existing text typed before mic -> kept once
value = 'prefix text'; baseText = 'prefix text';
onresult(0, [[{ transcript: 'suffix', isFinal: true }]], true);
check("(iv) existing text kept once", value, "prefix text suffix");

if (failed > 0) {
  console.log("STT TESTS: " + passed + " passed, " + failed + " FAILED");
  process.exit(1);
}
console.log("STT TESTS: all " + passed + " passed");
console.log("STT_DUP_OK");
