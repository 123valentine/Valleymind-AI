// Phase 1 Massive Editing workspace integrity.
// Executes settings.js + massive_editing.js in a stub DOM, then verifies the
// rendered shell: top bar/Projects, preview/timeline, unified composer, bottom
// tabs and the four slide-up drawers. No network or disk is touched.
const fs = require('fs');
const vm = require('vm');

let ok = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 240) : '')); fail++; }
}
function decode(s) {
  return String(s || '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#039;/g, "'");
}

const html = fs.readFileSync('index.html', 'utf8');
const settingsSrc = fs.readFileSync('static/settings.js', 'utf8');
const meSrc = fs.readFileSync('static/massive_editing.js', 'utf8');

function makeElement(tag) {
  return {
    tagName: String(tag || '').toUpperCase(),
    children: [], className: '', textContent: '', innerHTML: '', value: '',
    checked: false, disabled: false, title: '', download: '', href: '',
    dataset: {}, style: {}, attrs: {}, parentNode: null,
    classList: {
      flags: {},
      add(n) { this.flags[n] = true; },
      remove(n) { delete this.flags[n]; },
      toggle(n, on) { if (on) this.flags[n] = true; else delete this.flags[n]; },
      contains(n) { return !!this.flags[n]; }
    },
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : ''; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {}, focus() {}, click() {}, scrollIntoView() {}, play() {}, pause() {}
  };
}

const cache = {};
function byId(id) {
  if (!cache[id]) cache[id] = makeElement('div');
  return cache[id];
}

// settings.js runs with a null-returning DOM first; that keeps its unrelated
// persona hydration out of the way. Then the lookup is enabled for the editor.
let domReady = false;
const documentStub = {
  readyState: 'complete',
  hidden: false,
  createElement: makeElement,
  body: makeElement('body'),
  getElementById(id) { return domReady ? byId(id) : null; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {}
};

function fakeFetch(url) {
  const u = String(url);
  const data = { status: 'success' };
  if (u.indexOf('/api/editing/assets') >= 0) data.assets = [];
  if (u.indexOf('/api/editing/storage') >= 0) { data.storage = 'r2'; data.ready = true; }
  return Promise.resolve({ ok: true, json() { return Promise.resolve(data); } });
}

const store = {};
const win = {
  document: documentStub,
  navigator: {},
  AbortController: function () { this.signal = {}; this.abort = function () {}; },
  URL: { createObjectURL() { return 'blob:vm-test'; }, revokeObjectURL() {} },
  localStorage: {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; }
  },
  matchMedia() { return { matches: false, addListener() {}, removeListener() {} }; },
  console, Promise, Object, Date, Blob: function () {}, FormData: function () {},
  setTimeout, clearTimeout, setInterval, clearInterval,
  fetch: fakeFetch,
  open() {}
};
const ctx = {
  window: win,
  document: documentStub,
  navigator: win.navigator,
  URL: win.URL,
  console, Promise, Object, Date, AbortController: win.AbortController,
  setTimeout, clearTimeout, setInterval, clearInterval,
  fetch: fakeFetch
};
vm.createContext(ctx);

let settingsError = null, moduleError = null;
try { vm.runInContext(settingsSrc, ctx); } catch (e) { settingsError = e; }
domReady = true;
try { vm.runInContext(meSrc, ctx); } catch (e) { moduleError = e; }

check('settings.js evaluates cleanly', !settingsError, settingsError && settingsError.message);
check('massive_editing.js initializes cleanly', !moduleError, moduleError && moduleError.message);
if (settingsError || moduleError) {
  console.log('MASSIVE_EDITING_WORKSPACE_TESTS: ' + ok + ' passed, ' + fail + ' failed');
  process.exitCode = 1;
  return;
}

const API = win.VMEditing;
check('workspace exposes the VMEditing API',
  !!API && typeof API.openDrawer === 'function' &&
  typeof API.submit === 'function' && typeof API.pickFiles === 'function');

// ── Shared vocabularies ──────────────────────────────────────────────────────
const modes = win.VM_MASSIVE_MODES || [];
const effectGroups = win.VM_MASSIVE_EFFECT_GROUPS || [];
const stickerGroups = win.VM_MASSIVE_STICKER_GROUPS || [];
const captionStyles = win.VM_MASSIVE_CAPTION_STYLES || [];
const audioModes = win.VM_MASSIVE_AUDIO_MODES || [];
check('settings shares the five compact modes',
  modes.length === 5 && modes[0] === 'Auto AI Director' &&
  modes[4] === 'Character Fusion', modes);
check('settings shares the three effect categories',
  effectGroups.map(function (g) { return g.name; }).join('|') ===
  'Velocity & Beat|Color & Vibe|Framing', effectGroups);
check('settings shares all seven effect presets',
  effectGroups.reduce(function (n, g) { return n + g.items.length; }, 0) === 7 &&
  effectGroups[0].items.join('|') === 'Beat Flash|Zoom Punch|Velocity Slow-Mo|Camera Shake', effectGroups);
check('settings shares the four sticker categories',
  stickerGroups.join('|') === 'My Stickers|Reactions|Sports|Tutorial Pop-Ups', stickerGroups);
check('settings shares caption + audio controls',
  captionStyles.join('|') === 'Word-by-Word Pop|Clean Subtitle|Off' &&
  audioModes.join('|') === 'Keep Original Audio|Add Background Track');

// ── Rendered shell ───────────────────────────────────────────────────────────
const topbar = decode(byId('meTopbar').innerHTML);
check('top bar has a Projects drawer trigger',
  /meProjectsBtn/.test(topbar) && /Projects/.test(topbar), topbar);
check('top bar has a compact Mode pill with every mode',
  /me-mode-pill/.test(topbar) && modes.every(function (m) { return topbar.indexOf(m) >= 0; }), topbar);
check('top bar has Export',
  /meExportBtn/.test(topbar) && /Export/.test(topbar), topbar);

const workspace = decode(byId('meWorkspace').innerHTML);
const timeline = decode(byId('meTimeline').innerHTML);
check('preview offers 16:9 and 9:16 frames',
  /data-aspect="16:9"/.test(workspace) && /data-aspect="9:16"/.test(workspace));
check('compact timeline has a base video track and overlay markers',
  /me-timeline-strip/.test(workspace) && /me-tl-base-track/.test(timeline) &&
  /me-tl-marker/.test(timeline), timeline);
check('one composer uses the Describe your edit placeholder',
  /placeholder="Describe your edit\.\.\."/.test(workspace));
check('composer + accepts videos, audio files and pictures',
  /id="meFilesInput"[^>]*accept="video\/\*,image\/\*,audio\/\*"[^>]*multiple/.test(workspace));
check('composer Apply is the explicit submit control',
  /me-composer-apply/.test(workspace) && />Apply<\/button>/.test(workspace) &&
  /title="Start AI Edit"/.test(workspace));

const tabs = decode(byId('meBottomTabs').innerHTML);
check('bottom tabs expose Effects, Stickers, Captions and Audio',
  ['Effects', 'Stickers', 'Captions', 'Audio'].every(function (label) {
    return tabs.indexOf(label) >= 0;
  }), tabs);

// ── Attachments ──────────────────────────────────────────────────────────────
API.sbUploadMedia([
  { name: 'clip.mp4', type: 'video/mp4', size: 1024 },
  { name: 'beat.mp3', type: 'audio/mpeg', size: 1024 },
  { name: 'still.png', type: 'image/png', size: 1024 }
]);
const chips = decode(byId('meUploads').innerHTML);
check('one composer shows removable chips for every attachment type',
  ['clip.mp4', 'beat.mp3', 'still.png'].every(function (name) { return chips.indexOf(name) >= 0; }) &&
  (chips.match(/VMEditing\.removeFile/g) || []).length === 3, chips);
check('preview picks the attached video',
  byId('meCanvasVideo').getAttribute('src') === 'blob:vm-test');

// ── Drawers ──────────────────────────────────────────────────────────────────
API.openDrawer('effects');
let effects = '';
effectGroups.forEach(function (group) {
  API.setDrawerCategory('effects', group.name);
  effects += decode(byId('meDrawer').innerHTML);
});
check('Effects drawer shows all three categories',
  effectGroups.every(function (g) { return effects.indexOf(g.name) >= 0; }), effects);
check('Effects drawer shows all seven presets across its categories',
  effectGroups.every(function (g) {
    return g.items.every(function (item) { return effects.indexOf(item) >= 0; });
  }), effects);

API.openDrawer('stickers');
const stickers = decode(byId('meDrawer').innerHTML);
check('Stickers drawer shows all four categories',
  stickerGroups.every(function (g) { return stickers.indexOf(g) >= 0; }), stickers);
check('Stickers drawer has an Add Sticker slot plus empty-state slots',
  /Add Sticker/.test(stickers) && /Ready for sync/.test(stickers), stickers);
check('Stickers drawer bundles no sticker packs or reaction clips',
  !/sticker-pack/i.test(stickers) && !/reaction-clips/i.test(stickers));

API.openDrawer('captions');
const captions = decode(byId('meDrawer').innerHTML);
check('Captions drawer offers all three caption styles',
  captionStyles.every(function (s) { return captions.indexOf(s) >= 0; }), captions);

API.openDrawer('audio');
const audio = decode(byId('meDrawer').innerHTML);
check('Audio drawer offers keep-original and background-track modes',
  audioModes.every(function (m) { return audio.indexOf(m) >= 0; }), audio);

API.openDrawer('projects');
const projects = decode(byId('meDrawer').innerHTML);
check('Projects drawer has a clean empty state', /No saved edits yet/.test(projects), projects);
API.closeDrawer();
check('drawer closes cleanly', !byId('meDrawer').classList.contains('open'));

// ── Compact controls + source wiring ────────────────────────────────────────
API.setAspect('16:9');
check('aspect control switches the preview frame',
  byId('mePreview').getAttribute('data-aspect') === '16:9');
API.setMode('Sports / Beat-Sync');
check('mode pill reflects the selected mode',
  byId('meModeSelect').value === 'Sports / Beat-Sync');

check('submit still posts to the real editing pipeline',
  /postJSON\("\/api\/editing\/run"/.test(meSrc));
check('workspace CSS is loaded from index.html',
  /\.me-bottom-tabs/.test(html) && /\.me-drawer\.open/.test(html) &&
  /\.me-timeline-strip/.test(html) && /\.me-preview\[data-aspect="9:16"\]/.test(html));
check('workspace script versions are bumped',
  /settings\.js\?v=20/.test(html) && /massive_editing\.js\?v=4/.test(html));

const visibleCopy = [topbar, workspace, tabs, effects, stickers, captions,
  audio, projects, chips].join(' ');
check('no storage provider names leak into user-facing copy',
  !/cloudflare|\br2\b|\bs3\b|boto/i.test(visibleCopy), visibleCopy);

console.log('MASSIVE_EDITING_WORKSPACE_TESTS: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exitCode = 1;
