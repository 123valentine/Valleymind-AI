// Conversational video editing in the main chat.
// Executes settings.js + massive_editing.js in a stub DOM and verifies the
// chat-side asset picker, pending-edit state, real /api/editing/run submission,
// job polling and MP4 result card. Also locks removal of the old manual timeline.
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
    removeChild(child) {
      const i = this.children.indexOf(child);
      if (i >= 0) this.children.splice(i, 1);
      return child;
    },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : ''; },
    contains() { return false; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {}, focus() {}, click() {}, scrollIntoView() {}
  };
}

const cache = {};
function byId(id) {
  if (!cache[id]) cache[id] = makeElement('div');
  return cache[id];
}
byId('vmStickerPicker').style.display = 'none';
byId('plusBtn');
byId('chatStickerInput');
byId('messages');

let domReady = false;
const documentListeners = {};
const documentStub = {
  readyState: 'complete',
  hidden: false,
  createElement: makeElement,
  body: makeElement('body'),
  documentElement: makeElement('html'),
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

let jobPolls = 0;
function fakeApiFetch(url, opts) {
  captured.api.push({ url: String(url), method: opts && opts.method, body: opts && opts.body });
  const u = String(url);
  let data = { status: 'success' };
  if (u.indexOf('/api/editing/stickers') >= 0) {
    data.stickers = u.indexOf('type=gifs') >= 0
      ? [{ name: 'Dance GIF', kind: 'gif', url: '/static/assets/gifs/dance.gif' }]
      : [{ name: 'Fire Sticker', kind: 'sticker', url: '/static/assets/stickers/fire.png' }];
  }
  if (u.indexOf('/api/editing/assets') >= 0) {
    data.assets = [{
      name: 'My Upload.png', kind: 'image', category: 'sticker',
      local_path: '/static/media/users/u/images/mine.png'
    }];
  }
  if (u.indexOf('/api/editing/run') >= 0) {
    data = { status: 'success', job: { job_id: 'job-chat-1', status: 'queued' } };
  }
  if (u.indexOf('/api/studio/job/') >= 0) {
    jobPolls += 1;
    data = {
      status: 'success',
      job: {
        job_id: 'job-chat-1',
        status: jobPolls === 1 ? 'done' : 'queued',
        final_video: '/static/media/users/u/edits/finished.mp4',
        edit_plan: [{ status: 'done' }]
      }
    };
  }
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
  console, Promise, Object, Date, Blob: function () {}, FormData: FakeFormData,
  // Immediate timers keep the poll test synchronous without changing the module.
  setTimeout(fn) { if (typeof fn === 'function') fn(); return 0; },
  clearTimeout() {}, setInterval() { return 0; }, clearInterval() {},
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
  setTimeout: win.setTimeout, clearTimeout() {}, setInterval() { return 0; }, clearInterval() {},
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
  console.log('VM_CHAT_EDIT_TESTS: ' + ok + ' passed, ' + fail + ' failed');
  process.exitCode = 1;
  return;
}

async function runTests() {
  const API = win.VMChatEdit;
  check('chat edit bridge exposes the expected API',
    !!API && ['VERSION', 'PLACEMENTS', 'init', 'prepare', 'marker', 'isConfirmation',
      'isDetailed', 'normalizePlacement', 'setPlacement', 'selectSticker',
      'refreshStickers', 'uploadStickers', 'openStickerPicker', 'buildFormData',
      'resultCardHtml', 'start', 'poll', 'download', 'regenerate', 'reset']
      .every(function (k) { return typeof API[k] !== 'undefined'; }));

  // ── Removed manual workspace ────────────────────────────────────────────────
  const forbidden = [
    'data-ws="editing"', 'vmWsPanelEditing', 'id="meRoot"', '.me-root',
    'me-timeline', 'me-playhead', 'Properties - Velocity', 'Beat Sync',
    'VIDEO/AUDIO/FX/STICKERS', 'me-sidebar', 'me-composer-dock',
    'massive_editing.js?v=6', 'settings.js?v=22'
  ];
  forbidden.forEach(function (marker) {
    check('manual workspace marker removed: ' + marker, html.indexOf(marker) < 0);
  });
  check('script versions are bumped',
    html.indexOf('settings.js?v=23') >= 0 && html.indexOf('massive_editing.js?v=7') >= 0);
  check('openEditing returns the user to chat',
    /function openEditing\(\)\s*\{[^}]*closeStudio\(\);[^}]*focusChatInput\(\);/s.test(html));
  check('legacy VMEditing shim is a no-op',
    /win\(\)\.VMEditing\s*=\s*\{/.test(meSrc) && /hide:\s*function\s*\(\)\s*\{\}/.test(meSrc));

  // ── Composer contract ───────────────────────────────────────────────────────
  const avatarBlock = /#personaBtnAvatar\s*\{([\s\S]*?)\}/.exec(html);
  const avatarCss = avatarBlock ? avatarBlock[1].replace(/\s+/g, ' ') : '';
  check('composer persona avatar is locked to a 24px circle',
    ['width: 24px', 'height: 24px', 'max-width: 24px', 'max-height: 24px',
      'border-radius: 9999px', 'object-fit: cover', 'flex-shrink: 0']
      .every(function (decl) { return avatarCss.indexOf(decl) >= 0; }), avatarCss);
  ['chatVideoInput', 'chatImageInput', 'chatAudioInput', 'chatStickerInput'].forEach(function (id) {
    check('composer has hidden ' + id,
      new RegExp('id="' + id + '"[^>]*class="vm-file-input"').test(html));
  });
  check('+ menu attaches video, reference pictures and funny audio',
    html.indexOf("vmPlusMenuPick('chatVideoInput')") >= 0 &&
    html.indexOf("vmPlusMenuPick('chatImageInput')") >= 0 &&
    html.indexOf("vmPlusMenuPick('chatAudioInput')") >= 0);
  check('+ menu opens the Studio Asset Stickers picker',
    html.indexOf('VMChatEdit.openStickerPicker()') >= 0 &&
    html.indexOf('Studio Asset Stickers') >= 0);
  check('picker DOM and placement controls are styled',
    html.indexOf('#vmStickerPicker{') >= 0 && html.indexOf('.vm-sticker-places{') >= 0 &&
    html.indexOf('.vm-sticker-place.active') >= 0);
  check('result card buttons are circular and icon-only',
    html.indexOf('.vm-edit-icon-btn{') >= 0 &&
    /border-radius:9999px/.test((html.match(/\.vm-edit-icon-btn\{[\s\S]*?\}/) || [''])[0]));
  check('send flow prepares the edit before cleanup',
    html.indexOf('_vmEditPlan = window.VMChatEdit.prepare(text, vmChatAttachments)') >= 0 &&
    html.indexOf('textToSend += "\\n" + _vmEditPlan.marker') >= 0);
  check('send flow shows the marker only in the outbound text',
    html.indexOf('addMessage(text, "user", sentImage);') >= 0 &&
    html.indexOf('text = textToSend;') >= 0);
  check('confirmed edit launches after the stream finishes',
    html.indexOf('window.VMChatEdit.start().catch(') >= 0 &&
    html.indexOf('_vmEditPlan.shouldRun') >= 0);
  check('audio attachments are mirrored as File objects',
    html.indexOf('var pendingAudioFiles = []') >= 0 &&
    html.indexOf("a.kind==='audio'") >= 0 && html.indexOf("kind: 'audio'") >= 0);

  // ── Shared placement vocabulary ────────────────────────────────────────────
  const placements = win.VM_MASSIVE_STICKER_PLACEMENTS || [];
  check('settings shares the five requested placement labels',
    placements.map(function (p) { return p.label; }).join('|') ===
    'Top Left|Upper Right|Lower Left|Lower Right|Center', placements);
  check('placement values are render-engine codes',
    placements.map(function (p) { return p.value; }).join('|') === 'tl|tr|bl|br|center', placements);
  check('picker normalises human placement labels',
    ['Top Left', 'upper right', 'Lower-Left', 'bottom right', 'centre']
      .map(API.normalizePlacement).join('|') === 'tl|tr|bl|br|center');

  // ── Pending edit state ──────────────────────────────────────────────────────
  API.reset();
  const video = { name: 'clip 2026.mp4' };
  const image = { name: 'ref.png' };
  const audio = { name: 'funny.mp3' };
  const atts = [
    { kind: 'video', name: 'clip 2026.mp4', file: video, status: 'ready' },
    { kind: 'image', name: 'ref.png', file: image, status: 'ready' },
    { kind: 'audio', name: 'funny.mp3', file: audio, status: 'ready' }
  ];
  let plan = API.prepare('make this viral', atts);
  check('first vague turn stores the video without rendering',
    plan.active && !plan.shouldRun && plan.marker === '[VIDEO_ATTACHED: clip 2026.mp4]', plan);
  check('pending state keeps all companion media',
    API.state.video === video && API.state.media.length === 2, API.state.media);
  check('first turn instruction is preserved verbatim',
    API.state.instruction === 'make this viral', API.state.instruction);

  plan = API.prepare('hype reel, sticker Upper Right, zoom punches', []);
  check('a detailed answer arms the real render', plan.shouldRun && !plan.active === false, plan);
  check('answers extend the brief instead of replacing it',
    /Additional direction from the user: hype reel/.test(API.state.instruction), API.state.instruction);

  API.reset();
  plan = API.prepare('edit it now', [{ kind: 'video', name: 'team.mp4', file: { name: 'team.mp4' }, status: 'ready' }]);
  check('explicit confirmation arms the render immediately', plan.shouldRun, plan);
  check('confirmation text is recognised', API.isConfirmation('make it viral now'));
  check('detailed brief is recognised', API.isDetailed('cinematic reel with lower left sticker and slow motion captions'));

  API.reset();
  plan = API.prepare('just this', [{ kind: 'video', name: 'clip [1]\n.mp4', file: { name: 'x' }, status: 'ready' }]);
  check('attachment marker is safe for the log and line protocol',
    !/\[[^\]]*\]/.test(plan.marker.slice(13, -1)) && !/[\r\n]/.test(plan.marker), plan.marker);

  // ── Sticker picker ──────────────────────────────────────────────────────────
  API.reset();
  await API.refreshStickers();
  check('picker merges built-ins, GIFs and uploads',
    API.state.stickers.length === 3 &&
    API.state.stickers.map(function (s) { return s.name; }).join('|') ===
    'Fire Sticker|Dance GIF|My Upload.png', API.state.stickers);
  API.setPlacement('Upper Right');
  API.selectSticker(0);
  const pickerHtml = decode(byId('vmStickerPicker').innerHTML);
  check('picker renders all placement labels and current selection',
    ['Top Left', 'Upper Right', 'Lower Left', 'Lower Right', 'Center']
      .every(function (label) { return pickerHtml.indexOf(label) >= 0; }) &&
    /vm-sticker-place active[^>]*>Upper Right</.test(pickerHtml), pickerHtml);
  check('picker exposes custom upload control',
    pickerHtml.indexOf('chatStickerInput') >= 0 && pickerHtml.indexOf('+ Custom') >= 0, pickerHtml);

  // ── Real pipeline payload ───────────────────────────────────────────────────
  API.reset();
  API.prepare('make a widescreen hype reel', [
    { kind: 'video', name: 'game.mp4', file: { name: 'game.mp4' }, status: 'ready' },
    { kind: 'image', name: 'ref.png', file: image, status: 'ready' },
    { kind: 'audio', name: 'funny.mp3', file: audio, status: 'ready' }
  ]);
  API.selectSticker(0);
  API.setPlacement('Lower Left');
  const fd = API.buildFormData();
  const entries = fd.entries;
  const values = function (key) {
    return entries.filter(function (e) { return e.key === key; }).map(function (e) { return e.value; });
  };
  check('payload posts the real source video and companion media',
    values('video').length === 1 && values('media').length === 2, entries);
  check('payload carries the user brief with no voiceover request',
    values('instruction')[0] === 'make a widescreen hype reel' &&
    !entries.some(function (e) { return /tts|voiceover|voice_over/i.test(e.key); }), entries);
  check('payload carries the chosen sticker and mapped placement',
    values('sticker_url')[0] === '/static/assets/stickers/fire.png' &&
    values('sticker_name')[0] === 'Fire Sticker' && values('sticker_pos')[0] === 'bl', entries);
  check('widescreen brief maps to the real canvas override',
    (function () {
      try { return JSON.parse(values('manual')[0] || '{}').canvas.aspect === '16:9'; }
      catch (e) { return false; }
    })(), values('manual')[0]);

  // ── Render start, poll and MP4 card ─────────────────────────────────────────
  const beforeApi = captured.api.length;
  const job = await API.start();
  const newCalls = captured.api.slice(beforeApi);
  check('start submits to /api/editing/run',
    newCalls.some(function (c) { return c.url.indexOf('/api/editing/run') >= 0 && c.method === 'POST'; }) ||
    newCalls.some(function (c) { return c.url.indexOf('/api/editing/run') >= 0; }), newCalls.map(function (c) { return c.url; }));
  check('start polls the real studio job endpoint',
    newCalls.some(function (c) { return c.url.indexOf('/api/studio/job/job-chat-1') >= 0; }),
    newCalls.map(function (c) { return c.url; }));
  check('finished job resolves with the MP4 URL',
    !!job && job.final_video === '/static/media/users/u/edits/finished.mp4', job);
  const cards = byId('messages').children;
  const finalCard = cards[cards.length - 1];
  const cardHtml = decode(finalCard ? finalCard.innerHTML : '');
  check('finished edit renders an MP4 card in chat',
    cardHtml.indexOf('/static/media/users/u/edits/finished.mp4') >= 0 &&
    /<video[^>]*controls/.test(cardHtml), cardHtml);
  check('MP4 card has icon-only Download and Regenerate controls',
    (cardHtml.match(/vm-edit-icon-btn/g) || []).length === 2 &&
    cardHtml.indexOf('VMChatEdit.download(this)') >= 0 &&
    cardHtml.indexOf('VMChatEdit.regenerate(this)') >= 0 &&
    cardHtml.indexOf('>Download<') < 0 && cardHtml.indexOf('>Regenerate<') < 0, cardHtml);
  check('no provider names leak into user-facing copy',
    !/cloudflare|\br2\b|\bs3\b|boto/i.test(cardHtml + pickerHtml), cardHtml + pickerHtml);

  console.log('VM_CHAT_EDIT_TESTS: ' + ok + ' passed, ' + fail + ' failed');
  if (fail) process.exitCode = 1;
}
runTests().catch(function (e) {
  console.log('FAIL: chat edit test runner :: ' + (e && e.stack ? e.stack : e));
  process.exitCode = 1;
});
