// Studio 3D Companion: pointer/touch drag in the staged room + safe mobile
// top clearance for the Call / End / Mute / Screen / Send bar.
// Runs in Node against the real index.html and static sources.
const fs = require('fs');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 200) : '')); fail++; }
}

const html = fs.readFileSync('index.html', 'utf8');
const js = fs.readFileSync('static/vrm_companion.js', 'utf8');
const settings = fs.readFileSync('static/settings.js', 'utf8');

function extractFunction(src, name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) return '';
  let i = src.indexOf('{', start);
  if (i < 0) return '';
  let depth = 0, quote = '', line = false, block = false;
  for (; i < src.length; i++) {
    const c = src[i], n = src[i + 1];
    if (line) { if (c === '\n') line = false; continue; }
    if (block) { if (c === '*' && n === '/') { block = false; i++; } continue; }
    if (quote) { if (c === '\\') { i++; continue; } if (c === quote) quote = ''; continue; }
    if (c === '/' && n === '/') { line = true; i++; continue; }
    if (c === '/' && n === '*') { block = true; i++; continue; }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return '';
}

// ── source-level contracts ─────────────────────────────────────────────────
assert('dedicated Studio companion stage still exists',
  html.includes('id="vmCompanionStage"') && html.includes('data-ws-panel="companion"'));
assert('mount keeps one shared staged companion node',
  html.includes('stage.appendChild(avatar)') && html.includes('vm-companion-staged'));
assert('staged wrapper is pointer-transparent with touch-action none',
  html.includes('#vmVrmCompanion.vm-companion-staged {') &&
  /pointer-events:\s*none\s*!important/.test(html) &&
  /touch-action:\s*none\s*!important/.test(html));
assert('stage drag transform uses CSS custom offsets (top/left are !important auto)',
  html.includes('transform: translate3d(var(--vm-stage-x, 0px), var(--vm-stage-y, 0px), 0)'));
assert('WebGL canvas opts back in for direct drag and never scrolls the page',
  /#vmVrmCompanion\.vm-companion-staged #vmVrmCompanionCanvas\s*\{[^}]*pointer-events:\s*auto\s*!important[^}]*touch-action:\s*none\s*!important/s.test(html));
assert('stage is an isolated layer below all workspace buttons',
  /\.vm-companion-stage\s*\{[^}]*isolation:isolate[^}]*z-index:1/s.test(html) &&
  /\.vm-companion-controls\s*\{[^}]*z-index:5/s.test(html) &&
  /#studioOverlay \.gallery-header\s*\{[^}]*z-index:20/s.test(html) &&
  /#vmWsTabbar\s*\{[^}]*z-index:10/s.test(html));

const stagedCssAt = html.indexOf('#vmVrmCompanion.vm-companion-staged {');
const mobileCss = html.slice(stagedCssAt, html.indexOf('@media (max-width:700px)', stagedCssAt) + 2600);
assert('mobile workspace adds safe top clearance below the toolbar',
  /@media \(max-width:700px\)/.test(mobileCss) &&
  /\.vm-companion-workspace\s*\{[^}]*--vm-companion-safe-top:\s*64px[^}]*padding:calc\(20px \+ var\(--vm-companion-safe-top\)\)/s.test(mobileCss));
assert('active call bar reserves padded stage space and full-width outside button',
  /\.vm-companion-stage\.vm-companion-call-active\s*\{[^}]*padding-top:160px;\s*min-height:540px;/s.test(mobileCss) &&
  mobileCss.includes('#vmCompanionOutsideBtn') && mobileCss.includes('min-height:44px'));
assert('call bar JS marks the active toolbar and unmount clears it',
  js.includes('vm-companion-call-active') && js.includes('vm-companion-chat-open') &&
  html.includes('stage.classList.remove("vm-companion-call-active")'));
assert('outside-app toggle collapses the in-stage call bar on mobile',
  settings.includes('window.vmVrmCompanion.collapseChat()') &&
  settings.includes('data-vm-desktop-state') &&
  html.includes('data-vm-desktop-state="off"'));

assert('drag binds pointerdown on wrapper AND stage',
  extractFunction(js, 'wireDrag').includes('el.addEventListener("pointerdown", begin)') &&
  extractFunction(js, 'wireDrag').includes('stage.addEventListener("pointerdown", begin)'));
assert('drag binds pointermove / pointerup / pointercancel on element and window',
  extractFunction(js, 'wireDrag').includes('el.addEventListener("pointermove", move)') &&
  extractFunction(js, 'wireDrag').includes('el.addEventListener("pointerup", end)') &&
  extractFunction(js, 'wireDrag').includes('el.addEventListener("pointercancel", end)') &&
  extractFunction(js, 'wireDrag').includes('window.addEventListener("pointermove", move)') &&
  extractFunction(js, 'wireDrag').includes('window.addEventListener("pointerup", end)') &&
  extractFunction(js, 'wireDrag').includes('window.addEventListener("pointercancel", end)'));
assert('wireDrag keeps touch-action none on the shared wrapper',
  extractFunction(js, 'wireDrag').includes('el.style.touchAction = "none"'));
assert('stage clamping reads real padding (safe top) from computed style',
  extractFunction(js, 'stageDragBounds').includes('getComputedStyle(stage)') &&
  extractFunction(js, 'stageDragBounds').includes('padding("paddingTop")'));
assert('stage offsets persist and are reset by Home',
  js.includes('prefs.stageX') && js.includes('prefs.stageY') &&
  extractFunction(js, 'home').includes('prefs.stageX = null'));
assert('module exposes chat collapse/clearance to settings + workspace',
  js.includes('syncChatClearance: syncChatClearance') && js.includes('collapseChat: collapseChat'));

// ── behavioral clamp test ──────────────────────────────────────────────────
const helperNames = [
  'stagedWorkspace', 'axisClamp', 'readStageOffset', 'writeStageOffset',
  'stageBaseRect', 'stageDragBounds', 'clampStageOffset',
];
const helperSrc = helperNames.map(function (name) {
  const src = extractFunction(js, name);
  assert('found helper ' + name, !!src);
  return src;
}).join('\n');

function makeClassList(initial) {
  const set = new Set(initial || []);
  return {
    contains: function (name) { return set.has(name); },
    add: function (name) { set.add(name); },
    remove: function (name) { set.delete(name); },
    toggle: function (name, force) {
      const on = force === undefined ? !set.has(name) : !!force;
      if (on) set.add(name); else set.delete(name);
      return on;
    },
  };
}

function makeStyle(vars) {
  return {
    touchAction: '', cursor: '', display: '', left: '', top: '', right: '', bottom: '',
    _vars: Object.assign({}, vars || {}),
    setProperty: function (key, value) { this._vars[key] = String(value); },
    getPropertyValue: function (key) { return this._vars[key] || ''; },
  };
}

function makeStage() {
  return {
    classList: makeClassList(['vm-companion-stage', 'vm-companion-call-active']),
    listeners: {},
    addEventListener: function (type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    getBoundingClientRect: function () {
      return { left: 0, top: 0, right: 500, bottom: 500, width: 500, height: 500 };
    },
    contains: function (node) { return node === el || node === canvas || node === chat; },
  };
}

function makeWindow() {
  return {
    innerWidth: 1000,
    innerHeight: 800,
    listeners: {},
    addEventListener: function (type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    getComputedStyle: function () {
      return { paddingLeft: '10px', paddingTop: '80px', paddingRight: '10px', paddingBottom: '10px' };
    },
  };
}

let stage, el, canvas, chat, win;
function makeFixture() {
  stage = makeStage();
  canvas = { id: 'vmVrmCompanionCanvas' };
  chat = {
    classList: makeClassList(['vmvrm-chat', 'open']),
    contains: function (node) { return node === this; },
  };
  el = {
    classList: makeClassList(['vm-companion-staged', 'vm-companion-chat-open']),
    style: makeStyle({ '--vm-stage-x': '40px', '--vm-stage-y': '20px' }),
    offsetWidth: 200, offsetHeight: 300,
    listeners: {},
    addEventListener: function (type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    setPointerCapture: function () { this.captured = true; },
    contains: function (node) { return node === this || node === canvas || node === chat; },
    getBoundingClientRect: function () {
      return { left: 130, top: 120, right: 330, bottom: 420, width: 200, height: 300 };
    },
  };
  win = makeWindow();
  const ids = {
    vmVrmCompanion: el, vmCompanionStage: stage,
    vmVrmCompanionChat: chat, vmVrmCompanionCanvas: canvas,
  };
  return function $id(id) { return ids[id] || null; };
}

if (helperSrc) {
  const $idA = makeFixture();
  const helpers = new Function('$id', 'window',
    helperSrc + '\nreturn { stagedWorkspace, stageBaseRect, stageDragBounds, clampStageOffset, readStageOffset };'
  )($idA, win);

  assert('stagedWorkspace recognises the mounted stage descendant',
    !!helpers.stagedWorkspace() && helpers.stagedWorkspace().stage === stage);
  const base = helpers.stageBaseRect(el);
  assert('base rect subtracts the current drag transform',
    base.left === 90 && base.top === 100 && base.width === 200 && base.height === 300,
    JSON.stringify(base));
  const bounds = helpers.stageDragBounds(stage);
  assert('stage bounds honour padding (including the active call-bar top band)',
    bounds.left === 10 && bounds.top === 80 && bounds.right === 490 && bounds.bottom === 490,
    JSON.stringify(bounds));

  const high = helpers.clampStageOffset(400, -100);
  assert('drag clamps to right edge and safe top', high.x === 200 && high.y === -20, JSON.stringify(high));
  const low = helpers.clampStageOffset(-500, 500);
  assert('drag clamps to left edge and bottom edge', low.x === -80 && low.y === 90, JSON.stringify(low));
  assert('clamped visual rect stays inside the stage padding',
    90 + high.x >= bounds.left && 100 + high.y >= bounds.top &&
    90 + high.x + 200 <= bounds.right && 100 + high.y + 300 <= bounds.bottom);
}

// ── behavioral pointer-drag test ───────────────────────────────────────────
const dragFns = ['runDragFrame', 'stopDragFrame', 'wireDrag'].map(extractFunction.bind(null, js)).join('\n');
if (helperSrc && dragFns) {
  const $idB = makeFixture();
  const prefs = { stageX: null, stageY: null, x: null, y: null, userPlaced: false };
  let saves = 0, cancels = 0, toggles = 0;
  const rafQueue = [];
  const raf = function (fn) { rafQueue.push(fn); return rafQueue.length; };
  const cancelRaf = function () { cancels++; };
  const drag = new Function(
    '$id', 'window', 'prefs', 'savePrefs', 'cancelWalk', 'toggleChat',
    'requestAnimationFrame', 'cancelAnimationFrame',
    'let dragState = null; let dragRaf = 0; let dragTargetX = 0; let dragTargetY = 0;\n' +
    helperSrc + '\n' + dragFns +
    '\nreturn { wireDrag, runDragFrame, state: function () { return dragState; },' +
    ' target: function () { return { x: dragTargetX, y: dragTargetY }; } };'
  )($idB, win, prefs, function () { saves++; }, function () { }, function () { toggles++; }, raf, cancelRaf);

  drag.wireDrag();
  const downCount = el.listeners.pointerdown.length;
  drag.wireDrag();
  assert('wireDrag is idempotent and installs stage/window pointer handlers',
    el.listeners.pointerdown.length === 1 && downCount === 1 &&
    stage.listeners.pointerdown.length === 1 &&
    (win.listeners.pointermove || []).length === 1 &&
    (win.listeners.pointerup || []).length === 1 &&
    (win.listeners.pointercancel || []).length === 1);

  const down = el.listeners.pointerdown[0];
  let prevented = false;
  down({
    pointerId: 7, button: 0, clientX: 150, clientY: 200, target: canvas,
    currentTarget: el, cancelable: true, preventDefault: function () { prevented = true; },
  });
  assert('pointerdown starts a stage drag from the WebGL canvas',
    !!drag.state() && drag.state().staged === true && prevented && !!el.captured);

  const move = win.listeners.pointermove[0];
  move({ pointerId: 7, clientX: 800, clientY: 900 });
  const target = drag.target();
  assert('window pointermove clamps the stage-relative target',
    target.x === 200 && target.y === 90, JSON.stringify(target));

  drag.runDragFrame();
  assert('drag frame writes the transform custom properties',
    el.style.getPropertyValue('--vm-stage-x') !== '' &&
    el.style.getPropertyValue('--vm-stage-y') !== '');

  el.listeners.pointerup[0]({ pointerId: 7 });
  assert('pointerup settles exactly on the clamped target and persists it',
    prefs.stageX === 200 && prefs.stageY === 90 && prefs.userPlaced === true &&
    el.style.getPropertyValue('--vm-stage-x') === '200px' &&
    el.style.getPropertyValue('--vm-stage-y') === '90px' && saves === 1);
  assert('pointerup releases drag state and dragging cursor',
    drag.state() === null && !el.classList.contains('vm-companion-dragging') &&
    el.style.cursor === 'grab' && cancels === 1);

  // A press inside the call bar must never become a drag/tap-to-toggle.
  el.listeners.pointerdown[0]({
    pointerId: 9, button: 0, clientX: 200, clientY: 200, target: chat,
    currentTarget: el, cancelable: true, preventDefault: function () { },
  });
  assert('call-bar buttons do not start a drag', drag.state() === null);
  assert('no tap-to-toggle was fired during the drag test', toggles === 0);
}

console.log('\nVM_COMPANION_WORKSPACE_DRAG_TESTS: ' + ok + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
