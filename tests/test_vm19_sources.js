// vm-fix19 item 12: Sources show/hide and citation linking (reliable checks)
const fs = require('fs');
const vm = require('vm');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0,160) : '')); fail++; }
}

const html = fs.readFileSync('index.html', 'utf8');

function extractBetween(startMarker, endMarker) {
  var i = html.indexOf(startMarker);
  if (i < 0) return null;
  var j = html.indexOf(endMarker, i);
  if (j < 0) return null;
  return html.slice(i, j);
}

var panelSrc = extractBetween('function renderSourcesPanel(bubbleEl, sourceMetadata, confidence) {',
  '/* A'); // followed by Reply Copy Bar comment block
if (!panelSrc) panelSrc = extractBetween('function renderSourcesPanel(bubbleEl, sourceMetadata, confidence) {', '\nfunction renderReplyCopyBar');
var citeSrc = extractBetween('function renderInlineCitations(bubbleEl, sourceMetadata) {', '\n/* ');
// try to find end of renderInlineCitations more reliably
if (!citeSrc) {
  var ci = html.indexOf('function renderInlineCitations(bubbleEl, sourceMetadata) {');
  var cj = html.indexOf('\nfunction renderSourcesPanel(bubbleEl, sourceMetadata, confidence) {');
  if (ci >= 0 && cj > ci) citeSrc = html.slice(ci, cj);
}

assert('renderSourcesPanel found in index.html', !!panelSrc && panelSrc.length > 100);
assert('renderInlineCitations found in index.html', !!citeSrc && citeSrc.length > 100);

// Structural/behavioral checks (items 5,11,12)
// 1) No sources → panel not shown (early return)
assert('renderSourcesPanel returns early when no valid sources', panelSrc.indexOf('if (!valid.length) return') !== -1);
assert('renderSourcesPanel checks for existing panel (no duplicate)', panelSrc.indexOf('sources-section') !== -1 && panelSrc.indexOf('querySelector') !== -1);

// 2) Sources label present, links open in new tab with noopener
assert('Sources label rendered', panelSrc.indexOf('Sources') !== -1);
assert('source chips/links use target _blank and rel noopener', panelSrc.indexOf('target') !== -1 && panelSrc.indexOf('_blank') !== -1 && panelSrc.indexOf('noopener') !== -1);

// 3) Collapsible when >3 (vm-fix19)
assert('collapsed by default when > 3 sources', panelSrc.indexOf('valid.length > 3') !== -1 && panelSrc.indexOf('sources-collapsed') !== -1);

// 4) Date/snippet shown when available (real data only)
assert('shows published date when available', panelSrc.indexOf('s.published') !== -1);
assert('shows snippet/content when available', panelSrc.indexOf('s.snippet') !== -1 || panelSrc.indexOf('s.content') !== -1);

// 5) Favicon with fallback
assert('favicon with onerror fallback', panelSrc.indexOf('onerror') !== -1 || panelSrc.indexOf('favicons') !== -1);

// 6) Inline citations: expand panel and scroll to matching row
assert('inline citations expand panel on click', citeSrc.indexOf('sources-collapsed') !== -1 && citeSrc.indexOf('classList.remove') !== -1);
assert('inline citations scroll to matching source row', citeSrc.indexOf('source-row') !== -1 && citeSrc.indexOf('scrollIntoView') !== -1);
assert('inline citations handle numeric [1] and source names', citeSrc.indexOf('\\d+') !== -1 || citeSrc.indexOf('numeric') !== -1 || citeSrc.indexOf('parseInt') !== -1);

// 7) Enrichment safety (item 7): stream hook runs in requestAnimationFrame + try/catch
assert('stream enrichment deferred via requestAnimationFrame', html.indexOf('requestAnimationFrame(function () {') !== -1);
assert('stream enrichment wrapped in try/catch', html.indexOf('VMEnrichAssistantReply] stream enrichment failed') !== -1);
assert('addMessage enrichment wrapped in try/catch', html.indexOf('VMRichRender] addMessage enrichment failed') !== -1);

// 8) settings.js?v=17 bumped
assert('settings.js cache-bust bumped to v17', html.indexOf('settings.js?v=17') !== -1);

console.log('VM19_SOURCES_TESTS: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
