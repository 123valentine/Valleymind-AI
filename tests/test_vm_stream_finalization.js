// SSE completion regressions: the final assistant reply must REPLACE the live
// stream bubble exactly once, never append a second wrapper or clip characters.
// Runs in Node against the real inline source in index.html.
const fs = require('fs');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 200) : '')); fail++; }
}

const html = fs.readFileSync('index.html', 'utf8');

function extractFunction(name) {
  const start = html.indexOf('function ' + name + '(');
  if (start < 0) return null;
  const close = /\r?\n\}\r?\n/.exec(html.slice(start));
  return close ? html.slice(start, start + close.index + close[0].length) : null;
}

const helperSrc = extractFunction('VMFinalizeStreamedAssistant');
assert('VMFinalizeStreamedAssistant exists in index.html', !!helperSrc);

// Minimal DOM: only the operations the finalizer is allowed to perform.
const created = [];
const fakeDocument = {
  createElement(tag) {
    const node = {
      tagName: String(tag).toUpperCase(),
      className: '',
      textContent: '',
      dataset: {},
      children: [],
      appendChild(child) {
        this.children = [child];
        child.parentNode = this;
      },
      insertBefore(child) {
        this.children = [child];
        child.parentNode = this;
      },
    };
    created.push(node);
    return node;
  }
};

if (helperSrc) {
  const VMFinalizeStreamedAssistant = new Function(
    'document', helperSrc + '\nreturn VMFinalizeStreamedAssistant;'
  )(fakeDocument);

  const bubble = {
    className: 'bubble',
    _text: 'develop',
    children: [],
    get textContent() { return this._text; },
    // Real DOM clears child nodes when textContent is assigned; mirror that so
    // repeated finalization is checked faithfully.
    set textContent(value) { this._text = String(value); this.children = []; },
    appendChild(child) { this.children.push(child); child.parentNode = this; },
    insertBefore(child) { this.children.unshift(child); child.parentNode = this; },
  };
  let wrapperAppendCalls = 0;
  const wrapper = {
    dataset: {},
    children: [bubble],
    querySelector(sel) { return sel === '.bubble' ? bubble : null; },
    insertBefore(child) { wrapperAppendCalls++; this.children.unshift(child); },
    appendChild() { wrapperAppendCalls++; },
  };

  const finalText = 'developers build the companion.';
  const first = VMFinalizeStreamedAssistant(wrapper, finalText);
  const second = VMFinalizeStreamedAssistant(wrapper, finalText);

  assert('finalization reuses the existing wrapper', first.wrapper === wrapper && second.wrapper === wrapper);
  assert('finalization never appends another assistant wrapper', wrapperAppendCalls === 0);
  assert('completion replaces partial text instead of appending', bubble.children.length === 1);
  assert('final text is complete (no leading-character clipping)', bubble.children[0].textContent === finalText);
  assert('final text span keeps its stream class', bubble.children[0].className === 'vm-stream-text');
  assert('finalized wrapper is stamped for idempotent enrichment', wrapper.dataset.vmFinalized === '1');
}

// Source-level guarantees for the live send() path.
assert('completion branch finalizes through the shared helper',
  html.includes('VMFinalizeStreamedAssistant(bubbleEl, fullReply);'));
assert('no-chunk final reply does not call addMessage again',
  !/addMessage\(fullReply,\s*"ai"\)/.test(html));
assert('tokens after completion are ignored',
  html.includes('if (streamDone) continue; // completion already owns the final text'));
assert('toolbar and enrichment are each guarded once',
  html.includes('dataset.vmToolbarDone') && html.includes('dataset.vmEnriched'));
assert('non-stream fallback reuses a live bubble',
  html.includes('var _fbWrapper = bubbleEl || (_regenCtx && _regenCtx.wrapper);'));

console.log('\nVM_STREAM_FINALIZATION_TESTS: ' + ok + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
