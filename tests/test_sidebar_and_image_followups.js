// Sidebar gear polish, completed Thinking collapse and image follow-up chips.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync('index.html', 'utf8');
const settingsSrc = fs.readFileSync('static/settings.js', 'utf8');
const richSrc = fs.readFileSync('static/vm_richrender.js', 'utf8');

let ok = 0;
function check(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { throw new Error('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 240) : '')); }
}

// ── Sidebar footer ──────────────────────────────────────────────────────────
const footerStart = html.indexOf('<div id="sidebarUserFooter"');
const footer = html.slice(footerStart, html.indexOf('</aside>', footerStart));
check('sidebar footer source found', footerStart !== -1 && footer.length > 200);
check('clipped help icon removed from footer', !/help_outline|vmHelpCenter\.open/.test(footer));
check('settings gear button kept', /id="settingsGearBtn"/.test(footer));
check('settings gear is a crisp vector SVG', /<svg[^>]*viewBox="0 0 24 24"/.test(footer) && /<circle[^>]*cx="12"[^>]*cy="12"/.test(footer));
check('gear button is a 36px flex-none dark tile',
  /width:36px;height:36px/.test(footer) && /flex-shrink:0/.test(footer) && /border-radius:11px/.test(footer),
  footer.slice(0, 320));
check('gear has subtle border and hover glow',
  /border:1px solid rgba\(148,163,184,0\.22\)/.test(footer) &&
  /onmouseenter=/.test(footer) && /boxShadow=/.test(footer));
check('gear stays accessible', /aria-label="Settings"/.test(footer) && /title="Settings"/.test(footer));

// ── Completed Thinking status collapse ──────────────────────────────────────
const collapseStart = html.indexOf('function VMCollapseThinkingIndicator(');
const collapseSrc = html.slice(collapseStart, html.indexOf('function VMEnsureAssistantToolbar', collapseStart));
check('collapse helper source found', collapseSrc.indexOf('function VMCollapseThinkingIndicator(') === 0);
check('removeThinkingIndicator collapses before detach',
  html.indexOf('VMCollapseThinkingIndicator(indicator);') < html.indexOf('function VMCollapseThinkingIndicator(') &&
  html.indexOf('VMCollapseThinkingIndicator(indicator);') > html.indexOf('function removeThinkingIndicator('));

function makeCollapseNode() {
  return {
    dataset: {},
    style: {},
    attrs: {},
    classList: { add(name) { (this.names || (this.names = [])).push(name); } },
    setAttribute(key, value) { this.attrs[key] = String(value); },
    getAttribute(key) { return this.attrs[key] || ''; }
  };
}
const collapseCtx = { console };
vm.createContext(collapseCtx);
vm.runInContext(collapseSrc + '\nthis.VMCollapseThinkingIndicator = VMCollapseThinkingIndicator;', collapseCtx);

const statusRoot = makeCollapseNode();
const indicator = makeCollapseNode();
indicator._vmActivity = {
  rootEl: statusRoot,
  getSnapshot() { return { steps: [] }; },
  getSummaryLabel() { return 'Thinking...'; }
};
collapseCtx.VMCollapseThinkingIndicator(indicator);
check('wrapper collapsed to zero height', indicator.style.maxHeight === '0px' && indicator.style.overflow === 'hidden');
check('wrapper stays hidden from Read-Aloud', indicator.dataset.noTts === 'true' && indicator.attrs['aria-hidden'] === 'true');
check('generic completed status line is hidden',
  statusRoot.dataset.noTts === 'true' && statusRoot.style.display === 'none' &&
  statusRoot.classList.names.includes('vm-activity-hidden'));

const stepRoot = makeCollapseNode();
const withSteps = makeCollapseNode();
withSteps._vmActivity = {
  rootEl: stepRoot,
  getSnapshot() { return { steps: [{ label: 'Searching the web...' }] }; },
  getSummaryLabel() { return 'Searching the web... ›'; }
};
collapseCtx.VMCollapseThinkingIndicator(withSteps);
check('meaningful step summary remains available', stepRoot.style.display !== 'none' && stepRoot.attrs['aria-hidden'] === 'true');

// ── Image-specific follow-up chips ──────────────────────────────────────────
const match = /var VM_IMAGE_FOLLOWUP_SUGGESTIONS = (\[[\s\S]*?\]);/.exec(settingsSrc);
check('shared image follow-up list exported by settings.js', !!match);
const followups = vm.runInNewContext(match[1]);
check('four image edit shortcuts shared', followups.length === 4 && /outfit|background|lighting|aspect ratio/i.test(followups.map((f) => f.label).join(' ')));
check('rich renderer still owns the image card', /function imageCardHtml\(body\)/.test(richSrc) && /vm-image-card/.test(richSrc));

function makeNode(tag) {
  const node = {
    tagName: String(tag || '').toUpperCase(),
    className: '', textContent: '', value: '',
    style: { cssText: '' }, dataset: {}, children: [],
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    querySelector(sel) {
      const cls = String(sel || '').replace(/^\./, '');
      const walk = (n) => {
        if (n.className && n.className.split(/\s+/).indexOf(cls) !== -1) return n;
        for (const child of n.children || []) { const found = walk(child); if (found) return found; }
        return null;
      };
      return walk(this);
    }
  };
  return node;
}

const chipStart = html.indexOf('function VMAddProactiveSuggestions(');
const chipSrc = html.slice(chipStart, html.indexOf('/* Append the small, muted AI disclaimer', chipStart));
check('suggestion source found', chipSrc.indexOf('function VMAddProactiveSuggestions(') === 0);
const chipCtx = {
  console,
  document: { createElement(tag) { return makeNode(tag); }, getElementById() { return null; } },
  Event: function Event() {},
  window: { VM_IMAGE_FOLLOWUP_SUGGESTIONS: followups }
};
vm.createContext(chipCtx);
vm.runInContext(chipSrc + '\nthis.VMAddProactiveSuggestions = VMAddProactiveSuggestions;', chipCtx);

const wrapper = makeNode('div');
wrapper.className = 'assistant-message';
const bubble = makeNode('div');
bubble.className = 'bubble';
wrapper.appendChild(bubble);
chipCtx.VMAddProactiveSuggestions(
  wrapper,
  "Here is the image I created.\n\n[[IMAGE_CARD: /static/generated/result.png | A red dress | valleymind_image.png]]\n\nWant me to adjust the lighting?",
  ''
);
const chips = bubble.children.find((child) => child.className === 'vm-suggestion-chips');
check('generated image reply receives four chips', !!chips && chips.children.length === 4, chips && chips.children.length);
const labels = chips ? chips.children.map((child) => child.textContent.toLowerCase()) : [];
check('chips cover outfit, background, lighting and aspect ratio',
  labels.some((l) => l.includes('outfit')) && labels.some((l) => l.includes('background')) &&
  labels.some((l) => l.includes('lighting')) && labels.some((l) => l.includes('aspect ratio')),
  labels);

const plainWrapper = makeNode('div');
const plainBubble = makeNode('div');
plainBubble.className = 'bubble';
plainWrapper.appendChild(plainBubble);
chipCtx.VMAddProactiveSuggestions(plainWrapper, 'A short plain answer about Paris.', '');
const plainChips = plainBubble.children.find((child) => child.className === 'vm-suggestion-chips');
check('ordinary replies keep the two-chip cap', !!plainChips && plainChips.children.length === 1, plainChips && plainChips.children.length);

console.log('Sidebar gear, Thinking collapse and image follow-up tests passed (' + ok + ' checks).');
