// Sidebar-led Massive Editing workspace integrity.
// Executes settings.js + massive_editing.js in a stub DOM, then verifies the
// rendered shell: permanent asset rail, adjacent slide-over drawers, stacked
// multi-track timeline, docked speed properties and the bottom multimedia
// composer (including the real Apply FormData). No network or disk is touched.
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

// Width-aware matchMedia lets the runtime checks exercise mobile and laptop
// behaviour without a real browser.
let viewportWidth = 1280;
function setViewportWidth(px) { viewportWidth = Number(px) || 1280; }

// settings.js runs with a null-returning DOM first; that keeps its unrelated
// persona hydration out of the way. Then the lookup is enabled for the editor.
let domReady = false;
const documentListeners = {};
const documentStub = {
  readyState: 'complete',
  hidden: false,
  createElement: makeElement,
  body: makeElement('body'),
  getElementById(id) { return domReady ? byId(id) : null; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener(name, fn) {
    if (!documentListeners[name]) documentListeners[name] = [];
    documentListeners[name].push(fn);
  }
};

const captured = { api: [] };
function FakeFormData() { this.entries = []; }
FakeFormData.prototype.append = function (key, value, name) {
  this.entries.push({ key: String(key), value: value, name: name });
};

function fakeApiFetch(url, opts) {
  captured.api.push({ url: String(url), method: opts && opts.method, body: opts && opts.body });
  const u = String(url);
  let data = { status: 'success' };
  if (u.indexOf('/api/editing/assets') >= 0) {
    data.assets = [{
      name: 'My Upload.png', kind: 'image', category: 'sticker',
      local_path: '/static/media/users/u/images/mine.png'
    }];
  }
  if (u.indexOf('/api/editing/stickers') >= 0) {
    data.stickers = u.indexOf('type=gifs') >= 0
      ? [{ name: 'Dance GIF', kind: 'gif', url: '/static/assets/gifs/dance.gif' }]
      : [{ name: 'Fire Sticker', kind: 'sticker', url: '/static/assets/stickers/fire.png' }];
  }
  if (u.indexOf('/api/editing/storage') >= 0) { data.storage = 'r2'; data.ready = true; }
  if (u.indexOf('/api/studio/job/') >= 0) {
    data = { status: 'success', job: { job_id: 'job-sidebar', status: 'queued' } };
  }
  if (u.indexOf('/api/editing/run') >= 0) {
    data = { status: 'success', job: { job_id: 'job-sidebar' } };
  }
  return Promise.resolve({ ok: true, json() { return Promise.resolve(data); } });
}

const store = {};
const noopInterval = function () { return 0; };
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
  matchMedia(query) {
    const m = /max-width:\s*(\d+)px/.exec(String(query));
    return { matches: !!m && viewportWidth <= Number(m[1]), addListener() {}, removeListener() {} };
  },
  console, Promise, Object, Date, Blob: function () {}, FormData: FakeFormData,
  setTimeout, clearTimeout, setInterval: noopInterval, clearInterval() {},
  fetch: fakeApiFetch, apiFetch: fakeApiFetch,
  open() {}
};
const ctx = {
  window: win,
  document: documentStub,
  navigator: win.navigator,
  URL: win.URL,
  console, Promise, Object, Date, AbortController: win.AbortController,
  FormData: FakeFormData,
  setTimeout, clearTimeout, setInterval: noopInterval, clearInterval() {},
  fetch: fakeApiFetch, apiFetch: fakeApiFetch
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

async function runTests() {
// Let the built-in sticker and asset fetches settle before inspecting drawers.
await new Promise(function (resolve) { setTimeout(resolve, 0); });
const API = win.VMEditing;
check('workspace exposes the VMEditing API',
  !!API && typeof API.openDrawer === 'function' &&
  typeof API.submit === 'function' && typeof API.pickFiles === 'function' &&
  typeof API.sliceAtPlayhead === 'function' && typeof API.alignToBeat === 'function' &&
  typeof API.toggleProperties === 'function' &&
  typeof API.setStickerFilter === 'function' && typeof API.isMobile === 'function');

// ── Shared vocabularies ──────────────────────────────────────────────────────
const modes = win.VM_MASSIVE_MODES || [];
const effectGroups = win.VM_MASSIVE_EFFECT_GROUPS || [];
const stickerGroups = win.VM_MASSIVE_STICKER_GROUPS || [];
const captionStyles = win.VM_MASSIVE_CAPTION_STYLES || [];
const audioModes = win.VM_MASSIVE_AUDIO_MODES || [];
const tools = win.VM_MASSIVE_TOOLS || [];
const speedCurves = win.VM_MASSIVE_SPEED_CURVES || [];
check('settings shares the five compact modes',
  modes.length === 5 && modes[0] === 'Auto AI Director' &&
  modes[4] === 'Character Fusion', modes);
check('settings shares the three effect categories',
  effectGroups.map(function (g) { return g.name; }).join('|') ===
  'Velocity & Beat|Color & Vibe|Framing', effectGroups);
check('settings shares all seven effect presets',
  effectGroups.reduce(function (n, g) { return n + g.items.length; }, 0) === 7 &&
  effectGroups[0].items.join('|') === 'Beat Flash|Zoom Punch|Velocity Slow-Mo|Camera Shake', effectGroups);
check('settings shares asset-first sticker categories and list options',
  stickerGroups.join('|') === 'Asset Stickers|Emojis' &&
  (win.VM_MASSIVE_STICKER_LIST_OPTIONS || []).join('|') === 'All|Uploaded|Animated',
  [stickerGroups, win.VM_MASSIVE_STICKER_LIST_OPTIONS]);
check('settings shares caption + audio controls',
  captionStyles.join('|') === 'Word-by-Word Pop|Clean Subtitle|Off' &&
  audioModes.join('|') === 'Keep Original Audio|Add Background Track');
check('settings shares the sidebar tool ids',
  tools.map(function (t) { return t.id; }).join('|') === 'stickers|overlays|transitions|fx', tools);
check('settings shares the slow-mo speed curves',
  speedCurves.join('|') === 'Linear|Ease In|Ease Out|S-Curve', speedCurves);

// ── Rendered shell ───────────────────────────────────────────────────────────
const topbar = decode(byId('meTopbar').innerHTML);
check('top bar has a Projects drawer trigger',
  /meProjectsBtn/.test(topbar) && /Projects/.test(topbar), topbar);
check('top bar has a compact Mode pill with every mode',
  /me-mode-pill/.test(topbar) && modes.every(function (m) { return topbar.indexOf(m) >= 0; }), topbar);
check('top bar has Export',
  /meExportBtn/.test(topbar) && /Export/.test(topbar), topbar);

const sidebar = decode(byId('meSidebar').innerHTML);
check('permanent sidebar exposes Stickers, Overlays, Transitions and FX',
  tools.every(function (t) { return sidebar.indexOf(t.label) >= 0; }) &&
  (sidebar.match(/me-sidebar-item/g) || []).length >= 4 && /toggleTool/.test(sidebar), sidebar);
check('drawer is mounted immediately beside the sidebar',
  /'<nav class="me-sidebar" id="meSidebar"[^>]*><\/nav>'\s*\+\s*'<aside class="me-flyout" id="meDrawer"><\/aside>'/.test(meSrc));

const workspace = decode(byId('meWorkspace').innerHTML);
const composer = decode(byId('meComposerDock').innerHTML);
check('preview offers 16:9 and 9:16 frames',
  /data-aspect="16:9"/.test(workspace) && /data-aspect="9:16"/.test(workspace));
check('preview leads with a highlighted viral caption',
  /me-preview-caption/.test(meSrc) && /YES!/.test(meSrc));
check('one bottom composer uses the Describe your AI edit placeholder',
  /placeholder="Describe your AI edit\.\.\."/.test(composer));
check('composer + accepts videos, audio files and pictures',
  /id="meFilesInput"[^>]*accept="video\/\*,image\/\*,audio\/\*"[^>]*multiple/.test(composer) &&
  /me-plus-mark">\+/.test(composer) && /Add Asset/.test(composer));
check('composer Apply is the explicit submit control',
  /me-composer-apply/.test(composer) && />Apply<\/button>/.test(composer) &&
  /title="Start AI Edit"/.test(composer));
check('composer has a microphone button',
  /me-composer-voice/.test(composer) && /VMEditing\.toggleRec/.test(composer));

const props = decode(byId('meProperties').innerHTML);
check('docked properties include velocity curve and speed ramps',
  ['Properties', 'Speed Control', 'Velocity', 'Slow-mo curve', 'Ramp in', 'Ramp out',
    'S-Curve'].every(function (label) { return props.indexOf(label) >= 0; }), props);
check('properties collapse to a compact velocity toggle',
  /mePropsToggle/.test(meSrc) &&
  /Properties - Velocity \(/.test(byId('mePropsToggle').textContent),
  byId('mePropsToggle').textContent);
API.toggleProperties(true);
check('properties toggle expands the overlay on demand',
  byId('mePropsToggle').getAttribute('aria-expanded') === 'true' &&
  byId('meProperties').classList.contains('open'));
API.toggleProperties(false);

// ── Attachments ──────────────────────────────────────────────────────────────
API.sbUploadMedia([
  { name: 'clip.mp4', type: 'video/mp4', size: 1024 },
  { name: 'beat.mp3', type: 'audio/mpeg', size: 1024 },
  { name: 'sticker.png', type: 'image/png', size: 1024 }
]);
const chips = decode(byId('meUploads').innerHTML);
check('one composer shows removable chips for every attachment type',
  ['clip.mp4', 'beat.mp3', 'sticker.png'].every(function (name) { return chips.indexOf(name) >= 0; }) &&
  (chips.match(/VMEditing\.removeFile/g) || []).length === 3, chips);
check('composer chip labels include Video and Pic prefixes',
  /Video: clip\.mp4/.test(decode(chips)) && /Pic: sticker\.png/.test(decode(chips)), chips);
check('preview picks the attached video',
  byId('meCanvasVideo').getAttribute('src') === 'blob:vm-test');

// ── Drawers ──────────────────────────────────────────────────────────────────
API.openDrawer('effects');
check('opening a tool slides the adjacent drawer in',
  byId('meDrawer').classList.contains('open'));
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
let stickers = decode(byId('meDrawer').innerHTML);
check('Stickers drawer offers Asset Stickers and Emojis categories',
  stickerGroups.every(function (g) { return stickers.indexOf(g) >= 0; }) &&
  stickers.indexOf('Asset Stickers') < stickers.indexOf('Emoji shelf'), stickers);
check('Asset Sticker row merges built-in and uploaded assets',
  ['Fire Sticker', '/static/assets/stickers/fire.png',
    'My Upload.png', '/static/media/users/u/images/mine.png'].every(function (label) {
    return stickers.indexOf(label) >= 0;
  }), stickers);
check('Sticker drawer exposes customizable list options',
  ['All', 'Uploaded', 'Animated'].every(function (label) {
    return stickers.indexOf('>' + label + '</button>') >= 0 || stickers.indexOf(label) >= 0;
  }), stickers);
check('Stickers drawer has a prominent blue Add button',
  /me-add-tile/.test(stickers) && /me-add-mark">\+/.test(stickers) &&
  /<strong>Add<\/strong>/.test(stickers), stickers);
check('Emoji reactions are a secondary scrolling shelf',
  /me-sticker-shelf/.test(stickers) &&
  ['YES!', 'NO WAY', 'TOO REAL', 'GOAT', '🔥', '😂', '⚽'].every(function (label) {
    return stickers.indexOf(label) >= 0;
  }), stickers);
API.setDrawerCategory('stickers', 'Emojis');
const emojiTab = decode(byId('meDrawer').innerHTML);
check('Emojis category keeps the lightweight reaction set',
  /Emoji reactions/.test(emojiTab) && emojiTab.indexOf('Fire Sticker') < 0, emojiTab);
API.setDrawerCategory('stickers', 'Asset Stickers');
API.setStickerFilter('Uploaded');
const uploadedOnly = decode(byId('meDrawer').innerHTML);
check('Uploaded filter narrows the asset list',
  uploadedOnly.indexOf('My Upload.png') >= 0 && uploadedOnly.indexOf('Fire Sticker') < 0,
  uploadedOnly);
API.setStickerFilter('All');
check('Stickers drawer bundles no sticker packs or reaction clips',
  !/sticker-pack/i.test(stickers) && !/reaction-clips/i.test(stickers));

// ── Responsive runtime ───────────────────────────────────────────────────────
setViewportWidth(360);
check('runtime detects a mobile viewport', API.isMobile() && API.isOverlayLayout());
API.openDrawer('stickers');
check('below 768px the drawer is a modal overlay',
  byId('meDrawer').getAttribute('data-overlay') === 'true' &&
  byId('meScrim').classList.contains('show'));
const outsideTarget = makeElement('div');
(documentListeners.pointerdown || []).forEach(function (fn) {
  fn({ target: outsideTarget });
});
check('an outside pointer closes the overlay drawer',
  !byId('meDrawer').classList.contains('open'));
setViewportWidth(1024);
check('laptop viewport returns to the inline drawer',
  !API.isMobile() && !API.isOverlayLayout());
API.openDrawer('stickers');
check('above 768px the drawer does not use the modal scrim',
  byId('meDrawer').getAttribute('data-overlay') === 'false' &&
  !byId('meScrim').classList.contains('show'));
API.closeDrawer();

API.openDrawer('overlays');
let overlays = '';
['Borders', 'Pictures', 'PIP'].forEach(function (cat) {
  API.setDrawerCategory('overlays', cat);
  overlays += decode(byId('meDrawer').innerHTML);
});
check('Overlays drawer has border, picture and PIP presets',
  ['Clean Border', 'Neon Frame', 'Picture Card', 'PIP Window'].every(function (label) {
    return overlays.indexOf(label) >= 0;
  }), overlays);

API.openDrawer('transitions');
const transitions = decode(byId('meDrawer').innerHTML);
check('Transitions drawer has graphical preset cards',
  /me-preset-card/.test(transitions) &&
  ['Cut', 'Zoom Punch', 'Whip Pan', 'Fade', 'Glitch'].every(function (label) {
    return transitions.indexOf(label) >= 0;
  }), transitions);

API.openDrawer('fx');
let fx = '';
effectGroups.forEach(function (group) {
  API.setDrawerCategory('effects', group.name);
  fx += decode(byId('meDrawer').innerHTML);
});
check('FX drawer reuses the shared effect groups',
  /me-preset-card/.test(fx) &&
  effectGroups.every(function (g) {
    return g.items.every(function (item) { return fx.indexOf(item) >= 0; });
  }), fx);

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

API.toggleTool('stickers');
check('tapping a sidebar tool reopens its drawer', byId('meDrawer').classList.contains('open'));
API.toggleTool('stickers');
check('tapping the active tool collapses the drawer', !byId('meDrawer').classList.contains('open'));

// ── Timeline interactions + Apply payload ───────────────────────────────────
API.setAspect('16:9');
check('aspect control switches the preview frame',
  byId('mePreview').getAttribute('data-aspect') === '16:9');
API.setMode('Sports / Beat-Sync');
check('mode pill reflects the selected mode',
  byId('meModeSelect').value === 'Sports / Beat-Sync');

API.selectOverlay('Clean Border');
API.selectTransition('Zoom Punch');
API.setSpeedCurve('S-Curve');
API.setManual('rampIn', 0.5);
API.setManual('rampOut', 1.2);
API.alignToBeat();
const timeline = decode(byId('meTimeline').innerHTML);
check('stacked timeline has four labeled tracks',
  ['VIDEO', 'AUDIO', 'FX', 'STICKERS'].every(function (label) {
    return timeline.indexOf(label) >= 0;
  }) && (timeline.match(/me-tl-track"/g) || []).length === 4, timeline);
check('timeline exposes compact slice, trim, beat-sync and playhead controls',
  ['sliceAtPlayhead', 'trimAtPlayhead', 'alignToBeat', 'Beat Sync',
    '>Trim</button>', 'me-tl-trim-handle', 'me-tl-marker', 'me-tl-playhead',
    'Clean Border'].every(function (marker) {
    return timeline.indexOf(marker) >= 0;
  }), timeline);

byId('meText').value = 'slow-mo the goal and add YES!';
API.submit();
const runCall = captured.api.filter(function (c) { return c.url === '/api/editing/run'; })[0];
check('Apply posts to the real editing pipeline', !!runCall && runCall.method === 'POST');
const entries = (runCall && runCall.body && runCall.body.entries) || [];
const values = function (key) {
  return entries.filter(function (e) { return e.key === key; }).map(function (e) { return e.value; });
};
check('Apply FormData carries instruction, mode and attachments',
  values('instruction')[0] === 'slow-mo the goal and add YES!' &&
  values('mode')[0] === 'Sports / Beat-Sync' &&
  values('video').length === 1 && values('media').length === 2, entries);
check('Apply manual payload carries speed curves, ramps, trim and beat sync',
  (function () {
    let manual = {};
    try { manual = JSON.parse(values('manual')[0] || '{}'); } catch (e) { manual = {}; }
    return manual.speed_curve === 'S-Curve' && manual.ramp_in === 0.5 &&
      manual.ramp_out === 1.2 && manual.trim_start === 0 && manual.trim_end === 0 &&
      manual.beat_sync === true && manual.sticker_anim === 'beat';
  })(), values('manual')[0]);
check('Apply payload carries sidebar overlay and transition selections',
  values('overlay')[0] === 'Clean Border' && values('transition')[0] === 'Zoom Punch', entries);

check('workspace CSS is loaded from index.html',
  /\.me-sidebar/.test(html) && /\.me-flyout\.open/.test(html) &&
  /\.me-tl-multitrack/.test(html) && /\.me-properties/.test(html) &&
  /\.me-scrim/.test(html) && /\.me-composer-dock/.test(html) &&
  /\.me-preview\[data-aspect="9:16"\]/.test(html));
check('workspace script versions are bumped',
  /settings\.js\?v=22/.test(html) && /massive_editing\.js\?v=6/.test(html));
check('mobile rail collapses to 40px icons',
  html.indexOf('@media (max-width: 768px)') >= 0 &&
  html.indexOf('.me-sidebar { width:40px; min-width:40px;') >= 0 &&
  html.indexOf('.me-sidebar-label { display:none; }') >= 0);
check('mobile drawer matches the requested modal overlay geometry',
  html.indexOf('position: absolute; left: 40px; top: 0; height: 100%; z-index: 999;') >= 0 &&
  html.indexOf('width: min(280px, 80vw); box-shadow: 4px 0 15px rgba(0,0,0,0.5);') >= 0 &&
  html.indexOf('.me-scrim { z-index:998; }') >= 0);
check('timeline lanes stay 24-28px with compact actions',
  html.indexOf('height:clamp(24px,3.2vw,28px)') >= 0 &&
  html.indexOf('.me-tl-track { height:24px;') >= 0 &&
  html.indexOf('.me-tl-track-content { height:20px; }') >= 0);
check('laptop keeps the stage/properties side-by-side split',
  html.indexOf('@media (min-width: 993px)') >= 0 &&
  html.indexOf('.me-stage-grid { grid-template-columns:minmax(0,1fr) 258px; }') >= 0);
check('the text composer is prominently centered',
  html.indexOf('.me-composer-dock { flex-shrink:0; display:flex; justify-content:center;') >= 0 &&
  html.indexOf('.me-composer { width:min(100%,1120px); margin:0 auto;') >= 0);
check('old bottom-tab layout is gone', !/\.me-bottom-tabs/.test(html));

const visibleCopy = [topbar, sidebar, workspace, composer, timeline, props,
  effects, stickers, overlays, transitions, fx, captions, audio, projects, chips].join(' ');
check('no storage provider names leak into user-facing copy',
  !/cloudflare|\br2\b|\bs3\b|boto/i.test(visibleCopy), visibleCopy);

console.log('MASSIVE_EDITING_WORKSPACE_TESTS: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exitCode = 1;
}
runTests().catch(function (e) {
  console.log('FAIL: workspace test runner :: ' + (e && e.stack ? e.stack : e));
  process.exitCode = 1;
});
