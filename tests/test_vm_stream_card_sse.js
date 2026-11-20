// Live-stream rendering regressions: SSE token spacing, JSON-bleed filtering,
// and buffering of streamed [[CARD:]] / [[ENTITY:]] blocks.
// Runs in Node against the REAL inline source in index.html.
const fs = require('fs');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 200) : '')); fail++; }
}

const html = fs.readFileSync('index.html', 'utf8');
const win = {};

// ── VMStreamCardSafeText: extracted from the real page source ─────────────
const fnStart = html.indexOf('function VMStreamCardSafeText(text) {');
let fnEnd = -1;
if (fnStart >= 0) {
  const closeMatch = /\r?\n\}\r?\n/.exec(html.slice(fnStart));
  fnEnd = closeMatch ? fnStart + closeMatch.index + closeMatch[0].length - 1 : -1;
}
assert('VMStreamCardSafeText exists in index.html', fnStart >= 0 && fnEnd > fnStart);
if (fnStart < 0 || fnEnd < 0) {
  console.log('VM_STREAM_CARD_SSE_TESTS: ' + ok + ' passed, ' + fail + ' failed');
  process.exit(1);
}
const fnSrc = html.slice(fnStart, fnEnd);
const VMStreamCardSafeText = new Function(fnSrc + '\nreturn VMStreamCardSafeText;')();
// The legacy name must remain available for cached pages.
assert('legacy VMStreamEntitySafeText alias is assigned',
  html.indexOf('window.VMStreamEntitySafeText = VMStreamCardSafeText;') !== -1);

assert('plain text is unchanged',
  VMStreamCardSafeText('Hello  world ') === 'Hello  world ');
assert('whitespace-only delta is preserved verbatim',
  VMStreamCardSafeText(' ') === ' ' && VMStreamCardSafeText('\t') === '\t');
assert('null/undefined become empty text',
  VMStreamCardSafeText(null) === '' && VMStreamCardSafeText(undefined) === '');

const card = '[[CARD: Prime Video | Streaming service | primevideo.com | https://www.primevideo.com | Legal streaming]]';
const entity = '[[ENTITY: Liverpool FC | Club | liverpoolfc.com | https://www.liverpoolfc.com | Historic club]]';

assert('complete CARD block is withheld from live text',
  VMStreamCardSafeText('Watch here: ' + card + ' Done.') === 'Watch here:  Done.' &&
  VMStreamCardSafeText('Watch here: ' + card + ' Done.').indexOf('|') === -1);
assert('complete ENTITY block is withheld from live text',
  VMStreamCardSafeText('Teams: ' + entity + ' End.') === 'Teams:  End.');

const partial = 'Prime Video | Streaming and rental service | primevideo.com | https://www.primevideo.com ||';
assert('incomplete CARD row never leaks raw pipe syntax',
  VMStreamCardSafeText('Watch here: [[CARD: ' + partial) === 'Watch here: ');
assert('incomplete ENTITY row never leaks raw pipe syntax',
  VMStreamCardSafeText('Teams: [[ENTITY: ' + partial) === 'Teams: ');
assert('marker without colon is not truncated',
  VMStreamCardSafeText('[[CARD this is prose') === '[[CARD this is prose');

const multi = 'A ' + card + ' B ' + entity + ' C';
assert('multiple complete blocks are all withheld',
  VMStreamCardSafeText(multi) === 'A  B  C');
assert('complete block followed by an incomplete block keeps prior text only',
  VMStreamCardSafeText('A ' + card + ' B [[CARD: Truncated | x | y') === 'A  B ');

// ── SSE token filter: extracted from the real stream loop ─────────────────
const filterStart = html.indexOf('                    // A whitespace-only delta is a real token.');
const filterEndMarker = /\r?\n\s*if \(statusIndicator\)/.exec(html.slice(Math.max(0, filterStart)));
const filterEnd = filterStart >= 0 && filterEndMarker
  ? filterStart + filterEndMarker.index : -1;
assert('SSE token filter block found', filterStart >= 0 && filterEnd > filterStart);
if (filterStart >= 0 && filterEnd > filterStart) {
  const filterSrc = html.slice(filterStart, filterEnd).replace(/\r\n/g, '\n');
  const acceptsToken = new Function('tok', `
    for (var __once = 0; __once < 1; __once++) {
${filterSrc}
      return true; // accepted: fell through to the append path
    }
    return false; // a continue fired: filtered out
  `);

  assert('lone space token is accepted', acceptsToken(' ') === true);
  assert('lone newline token is accepted', acceptsToken('\n') === true);
  assert('normal word token is accepted', acceptsToken('to') === true);
  assert('citation token starting with [ is accepted', acceptsToken('[citation]') === true);
  assert('CARD marker token starting with [ is accepted', acceptsToken('[[CARD: Prime') === true);
  assert('ENTITY marker token starting with [ is accepted', acceptsToken('[[ENTITY: Prime') === true);
  assert('JSON object bleed is rejected', acceptsToken('{"token":"x"}') === false);
  assert('JSON object bleed after whitespace is rejected', acceptsToken('  {"intent":"search"}') === false);
  assert('JSON array bleed is rejected', acceptsToken('[{"a":1}]') === false);
  assert('brackets in ordinary prose are accepted', acceptsToken('See [ refined text') === true);
}

console.log('\nVM_STREAM_CARD_SSE_TESTS: ' + ok + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
