/**
 * Read-Aloud must speak the assistant's answer only: status pills, thinking
 * indicators, step counters and copy buttons are never spoken aloud.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

let checks = 0;
function check(name, condition, detail) {
  if (condition) { console.log('OK: ' + name); checks++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 240) : '')); process.exitCode = 1; }
}

/* ---- Minimal DOM stub: only what _vmSpeakableText needs. ---- */
function camel(name) {
  return name.replace(/-([a-z])/g, (m, c) => c.toUpperCase());
}
class FakeNode {
  constructor(opts) {
    opts = opts || {};
    this.tag = (opts.tag || 'div').toLowerCase();
    this.className = opts.className || '';
    this.dataset = opts.dataset || {};
    this.attrs = opts.attrs || {};
    this.text = opts.text || '';
    this.children = [];
    this.parentNode = null;
  }
  add(node) { node.parentNode = this; this.children.push(node); return node; }
  matchesSelector(sel) {
    sel = sel.trim();
    if (!sel) return false;
    if (sel.charAt(0) === '.') {
      return this.className.split(/\s+/).indexOf(sel.slice(1)) !== -1;
    }
    if (sel.charAt(0) === '[') {
      const m = /^\[([^\]=]+)(?:=["']?([^\]"']*)["']?)?\]$/.exec(sel);
      if (!m) return false;
      const name = m[1];
      const actual = name.indexOf('data-') === 0
        ? this.dataset[camel(name.slice(5))]
        : this.attrs[name];
      return m[2] === undefined
        ? actual !== undefined && actual !== false
        : String(actual) === m[2];
    }
    return this.tag === sel;
  }
  collect(out, selectors) {
    for (const child of this.children) {
      if (selectors.some((sel) => child.matchesSelector(sel))) out.push(child);
      child.collect(out, selectors);
    }
  }
  querySelectorAll(selector) {
    const out = [];
    this.collect(out, String(selector).split(','));
    return out;
  }
  removeChild(node) {
    const i = this.children.indexOf(node);
    if (i !== -1) { this.children.splice(i, 1); node.parentNode = null; }
  }
  cloneNode() {
    const clone = new FakeNode({
      tag: this.tag, className: this.className,
      dataset: Object.assign({}, this.dataset), attrs: Object.assign({}, this.attrs),
      text: this.text,
    });
    this.children.forEach((child) => clone.add(child.cloneNode(true)));
    return clone;
  }
  get textContent() {
    return this.text + this.children.map((c) => c.textContent).join('');
  }
  set textContent(value) {
    this.text = String(value || '');
    this.children = [];
  }
}

/* ---- Extract _vmSpeakableText from the live page source. ---- */
const start = html.indexOf('function _vmSpeakableText(root) {');
check('_vmSpeakableText is defined in index.html', start !== -1);
if (start !== -1) {
  let depth = 0, end = -1;
  for (let i = html.indexOf('{', start); i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
  }
  check('_vmSpeakableText has a complete body', end > start, end);
  const source = html.slice(start, end);
  const ctx = vm.createContext({ console });
  vm.runInContext(source + '\nthis._vmSpeakableText = _vmSpeakableText;', ctx);

  const bubble = new FakeNode({ className: 'bubble' });
  bubble.add(new FakeNode({
    className: 'vm-activity-inline',
    dataset: { noTts: 'true', vmActivity: 'true' },
    attrs: { 'aria-hidden': 'true' },
    text: 'Thinking... Step 2 of 4',
  }));
  bubble.add(new FakeNode({
    className: 'vm-code-card-head',
    text: 'Copy',
  }));
  bubble.add(new FakeNode({
    className: 'vm-stream-text',
    text: 'Here is the answer you asked for.',
  }));

  const spoken = ctx._vmSpeakableText(bubble);
  check('TTS starts on the actual reply', spoken === 'Here is the answer you asked for.', spoken);
  check('TTS never says Thinking', spoken.toLowerCase().indexOf('thinking') === -1, spoken);
  check('TTS never says step counters', !/\bstep\b|\b2 of 4\b/i.test(spoken), spoken);
  check('TTS never says Copy', spoken.indexOf('Copy') === -1, spoken);

  const plain = ctx._vmSpeakableText(new FakeNode({ text: 'Thinking...\n\nReal reply starts here.' }));
  check('fallback strips bare Thinking line', plain === 'Real reply starts here.', plain);

  const stepped = ctx._vmSpeakableText(new FakeNode({ text: 'Step 3 / 5\nActual answer.' }));
  check('fallback strips step counter line', stepped === 'Actual answer.', stepped);
}

/* ---- Lifecycle and mobile layout source guards. ---- */
const thinkingBlock = html.slice(
  html.indexOf('function addThinkingIndicator()'),
  html.indexOf('function removeThinkingIndicator(')
);
check('thinking indicator is marked no-tts', thinkingBlock.indexOf('dataset.noTts = "true"') !== -1);
check('thinking indicator is aria-hidden', thinkingBlock.indexOf('setAttribute("aria-hidden", "true")') !== -1);

const intentBlock = html.slice(html.indexOf('if (evt.intent) {'), html.indexOf('/* ---- THINKING PROCESS'));
check('intent status does not remove the thinking indicator',
  intentBlock.indexOf('removeThinkingIndicator(thinking)') === -1, intentBlock.slice(0, 300));
check('intent status is marked no-tts',
  intentBlock.indexOf('statusDiv.dataset.noTts = "true"') !== -1);

check('mobile user bubble width is capped', html.indexOf('max-width:86%') !== -1);
check('mobile copy cards are border-box', /box-sizing:border-box/.test(html));
check('mobile side margins are 14-16px', /#messages\{ padding:22px 16px !important/.test(html));
