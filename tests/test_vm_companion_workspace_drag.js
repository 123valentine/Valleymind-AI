// Studio 3D Companion: six real stage controls, no embedded chat/call box,
// pointer + touch dragging inside the staged room, and unchanged outside-app
// fallback. Runs in Node against the real index.html and static sources.
const fs = require('fs');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 300) : '')); fail++; }
}

const html = fs.readFileSync('index.html', 'utf8');
const js = fs.readFileSync('static/vrm_companion.js', 'utf8');
const settings = fs.readFileSync('static/settings.js', 'utf8');
const faceJs = fs.readFileSync('vrm_preview/face.js', 'utf8');
const cloudVoiceJs = fs.readFileSync('static/cloud_voice.js', 'utf8');
const clips = fs.readFileSync('vrm_preview/clips.json', 'utf8');

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

function cssBlock(source, selector) {
  const at = source.indexOf(selector);
  if (at < 0) return '';
  const open = source.indexOf('{', at);
  if (open < 0) return '';
  let depth = 0, i = open;
  for (; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (!depth) return source.slice(open + 1, i);
    }
  }
  return '';
}

// ── source-level contracts ─────────────────────────────────────────────────
assert('dedicated Studio companion stage still exists',
  html.includes('id="vmCompanionStage"') && html.includes('data-ws-panel="companion"'));
assert('mount keeps one shared staged companion node',
  html.includes('stage.appendChild(avatar)') && html.includes('vm-companion-staged'));

const removedOverlayMarkers = [
  'id="vmVrmCompanionChat"', 'id="vmVrmCompanionLog"', 'id="vmVrmCompanionInput"',
  'id="vmVrmCompanionMicBtn"', 'id="vmVrmCompanionCallBtn"', 'id="vmVrmCompanionEndBtn"',
  'id="vmVrmCompanionMuteBtn"', 'id="vmVrmCompanionScreenBtn"', 'id="vmVrmCompanionSend"',
  'vmvrm-chat', 'vm-companion-chat-open', 'vm-companion-call-active',
];
for (const marker of removedOverlayMarkers) {
  assert('embedded chat/call box removed: ' + marker,
    !html.includes(marker) && !js.includes(marker) && !settings.includes(marker));
}
assert('no chat toggle surface survives anywhere',
  !js.includes('toggleChat') && !js.includes('collapseChat') &&
  !js.includes('syncChatClearance') && !settings.includes('vmCompanionOpenChat') &&
  !settings.includes('Open chat') && !html.includes('vmVrmCompanionChat'));

assert('six real stage controls are present',
  html.includes('vmCompanionStageVoice()') && html.includes('vmCompanionStageMute()') &&
  html.includes("vmCompanionStageAnimation('waving')") &&
  html.includes("vmCompanionStageAnimation('nodding')") &&
  html.includes("vmCompanionStageAnimation('pointing')") &&
  html.includes("vmCompanionStageAnimation('thinking')") &&
  html.includes('id="vmCompanionStageVoiceBtn"') && html.includes('id="vmCompanionStageMuteBtn"'));
assert('stage voice handler calls the real voice edge',
  extractFunction(html.slice(html.indexOf('function vmCompanionStageVoice()')), 'vmCompanionStageVoice').includes('startStageVoice') &&
  extractFunction(html.slice(html.indexOf('function vmCompanionStageMute()')), 'vmCompanionStageMute').includes('toggleMute') &&
  extractFunction(html.slice(html.indexOf('function vmCompanionStageAnimation(')), 'vmCompanionStageAnimation').includes('.gesture('));

const stagedCss = cssBlock(html, '#vmVrmCompanion.vm-companion-staged {');
const baseCss = cssBlock(html, '#vmVrmCompanion {');
assert('staged transform is plain translate3d with no !important position/transform conflict',
  stagedCss.includes('transform: translate3d(var(--vm-stage-x, 0px), var(--vm-stage-y, 0px), 0)') &&
  !/transform\s*:[^;]*!important/.test(stagedCss) &&
  !/position\s*:\s*[^;]*!important/.test(stagedCss) &&
  !/top\s*:\s*[^;]*!important/.test(stagedCss) &&
  !/left\s*:\s*[^;]*!important/.test(stagedCss) &&
  /position:\s*fixed\s*;/.test(baseCss) && !/position:\s*fixed\s*!important/.test(baseCss));
assert('stage member wins position specificity without !important',
  /position:\s*relative\s*;/.test(stagedCss) &&
  !/position\s*:[^;]*!important/.test(stagedCss) &&
  !/top\s*:[^;]*!important/.test(stagedCss) &&
  !/left\s*:[^;]*!important/.test(stagedCss));
assert('stage member layout does not override transform geometry with !important',
  !/position\s*:\s*relative\s*!important/.test(cssBlock(html, '.vm-companion-staged {')) &&
  !/top\s*:\s*auto\s*!important/.test(cssBlock(html, '.vm-companion-staged {')) &&
  !/left\s*:\s*auto\s*!important/.test(cssBlock(html, '.vm-companion-staged {')));
assert('staged wrapper stays pointer-transparent with touch-action none',
  /pointer-events:\s*none\s*!important/.test(stagedCss) &&
  /touch-action:\s*none\s*!important/.test(stagedCss));
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
assert('mobile stage keeps full-width outside button tap target',
  mobileCss.includes('#vmCompanionOutsideBtn') && mobileCss.includes('min-height:44px'));

// ── outside-app contract stays byte-for-byte intact ───────────────────────
assert('outside-app button markup is unchanged',
  html.includes('id="vmCompanionOutsideBtn"') &&
  html.includes('data-vm-desktop-toggle="true"') &&
  html.includes('data-vm-desktop-state="off"') &&
  html.includes('aria-pressed="false"') &&
  html.includes('onclick="vmCompanionToggleOutsideFromWorkspace()"') &&
  html.includes('>Companion Outside App</button>'));
const outsideToggle = settings.split('function vmCompanionToggleOutsideFromWorkspace()', 2)[1]
  ? settings.split('function vmCompanionToggleOutsideFromWorkspace()', 2)[1].split('\n}', 1)[0] : '';
assert('outside-app toggle still reads/writes the one platform state',
  outsideToggle.includes('vmCompanionSetDesktopVisible(on)') &&
  !outsideToggle.includes('collapseChat'));
assert('outside-app fallback messages are unchanged',
  settings.includes('This browser or installed PWA can\\u2019t float the character over other apps. That needs the ValleyMind Android app (system overlay).') &&
  settings.includes('iOS doesn\\u2019t allow a floating overlay from a browser or PWA. This needs the ValleyMind iOS app.') &&
  settings.includes('"Not running \\u2014 " + VM_DESKTOP_SETUP_HINT'));

// ── real VRM voice/gesture wiring ─────────────────────────────────────────
const startStageVoiceSrc = extractFunction(js, 'startStageVoice');
const syncControlsSrc = extractFunction(js, 'syncControls');
const syncStageControlsSrc = extractFunction(js, 'syncStageControls');
const dragSrc = extractFunction(js, 'wireDrag');
assert('stage voice reuses the one recognised voice/brain pipeline',
  startStageVoiceSrc.includes('wireVoice()') && startStageVoiceSrc.includes('wireNativeSpeech()') &&
  startStageVoiceSrc.includes('maybePromptAccess()') &&
  startStageVoiceSrc.includes('nativeAsrAvailable()') &&
  startStageVoiceSrc.includes('startCall()') && startStageVoiceSrc.includes('startVoiceSession()') &&
  !js.includes('new SpeechRecognition') && !js.includes('speechSynthesis.speak'));
assert('one engine and one voice player remain',
  (js.match(/new Companion\(/g) || []).length === 1 &&
  (js.match(/VMCloudVoice\.speak\(/g) || []).length === 1);
assert('real amplitude still drives jaw/mouth visemes while TTS plays',
  js.includes('setLevelListener(function (level)') &&
  js.includes('engine.face.setTalkLevel(level)') &&
  js.includes('engine.face.setSpeaking(true)') &&
  cloudVoiceJs.includes('isListening: function ()') &&
  cloudVoiceJs.includes('isSpeaking: function ()') &&
  faceJs.includes('m.setValue("aa"') && faceJs.includes('m.setValue("ou"') &&
  faceJs.includes('m.setValue("oh"') &&
  faceJs.includes('if (!this.speaking || v <= 0.01)'));
assert('mute silences both voice owners and reflects the stage button state',
  extractFunction(js, 'toggleMute').includes('stopNativeSpeech()') &&
  extractFunction(js, 'toggleMute').includes('window.VMCloudVoice.stop()') &&
  extractFunction(js, 'toggleMute').includes('syncControls()') &&
  syncStageControlsSrc.includes('vmCompanionStageMuteBtn') &&
  syncStageControlsSrc.includes('aria-pressed') &&
  syncStageControlsSrc.includes('"active"') &&
  syncControlsSrc.includes('syncStageControls()') &&
  !syncControlsSrc.includes('vmVrmCompanionMuteBtn'));
assert('stage voice button active state follows real mic/TTS state',
  syncStageControlsSrc.includes('vmCompanionStageVoiceBtn') &&
  syncStageControlsSrc.includes('nativeCallActive') &&
  syncStageControlsSrc.includes('window.VMCloudVoice.isListening') &&
  syncStageControlsSrc.includes('stateKey === "speaking"'));
assert('gesture aliases route to real engine behaviours',
  js.includes('const GESTURE_STATUS = {') &&
  extractFunction(js, 'gesture').includes('GESTURE_STATUS[raw]') &&
  extractFunction(js, 'setStatus').includes('fireBodyClip(behaviour)') &&
  js.includes('engine.trigger(behaviour)'));
assert('four gestures have immediate bone fallback and release to the real clip',
  js.includes('const GESTURE_FALLBACKS = {') &&
  ['waving', 'nodding', 'pointing', 'thinking'].every((b) => js.includes(b + ': {')) &&
  ['waving', 'nodding', 'pointing', 'thinking'].every((b) => clips.includes('"' + b + '"')) &&
  extractFunction(js, 'humanBoneNode').includes('getRawBoneNode') &&
  extractFunction(js, 'applyGestureFrame').includes('rotation.set') &&
  extractFunction(js, 'playVrmGesture').includes('releaseGestureFallback()') &&
  extractFunction(js, 'stop').includes('cancelGestureFallback(true)') &&
  extractFunction(js, 'hide').includes('cancelGestureFallback(true)') &&
  extractFunction(js, 'cleanup').includes('cancelGestureFallback(true)'));

// ── pointer + touch drag wiring ───────────────────────────────────────────
assert('drag binds pointer AND touch handlers on wrapper + stage',
  dragSrc.includes('el.addEventListener("pointerdown", beginPointer)') &&
  dragSrc.includes('el.addEventListener("touchstart", beginTouch, passiveFalse)') &&
  dragSrc.includes('stage.addEventListener("pointerdown", beginPointer)') &&
  dragSrc.includes('stage.addEventListener("touchstart", beginTouch, passiveFalse)'));
assert('drag binds pointer/touch move + end on element AND window',
  ['pointermove', 'pointerup', 'pointercancel', 'touchmove', 'touchend', 'touchcancel'].every((type) =>
    dragSrc.includes('el.addEventListener("' + type + '"') &&
    dragSrc.includes('window.addEventListener("' + type + '"')));
assert('touch listeners are non-passive and preventDefault on begin/move/end',
  dragSrc.includes('const passiveFalse = { passive: false }') &&
  dragSrc.includes('if (e.cancelable) e.preventDefault();') &&
  dragSrc.includes('function touchPoint(') && dragSrc.includes('touchIdentifier('));
assert('stage clamp reads real padding, persists offsets, and has no tap action',
  extractFunction(js, 'stageDragBounds').includes('getComputedStyle(stage)') &&
  extractFunction(js, 'stageDragBounds').includes('padding("paddingTop")') &&
  js.includes('prefs.stageX') && js.includes('prefs.stageY') &&
  extractFunction(js, 'home').includes('prefs.stageX = null') &&
  !dragSrc.includes('toggleChat'));

// ── fixtures shared by the behavioral tests ───────────────────────────────
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

let stage, el, canvas, win;
function makeStage() {
  return {
    classList: makeClassList(['vm-companion-stage']),
    listeners: {}, listenerOptions: {},
    addEventListener: function (type, fn, opts) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
      (this.listenerOptions[type] = this.listenerOptions[type] || []).push(opts);
    },
    getBoundingClientRect: function () {
      return { left: 0, top: 0, right: 500, bottom: 500, width: 500, height: 500 };
    },
    contains: function (node) { return node === el || node === canvas; },
  };
}

function makeWindow() {
  return {
    innerWidth: 1000, innerHeight: 800,
    listeners: {}, listenerOptions: {},
    addEventListener: function (type, fn, opts) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
      (this.listenerOptions[type] = this.listenerOptions[type] || []).push(opts);
    },
    getComputedStyle: function () {
      return { paddingLeft: '10px', paddingTop: '80px', paddingRight: '10px', paddingBottom: '10px' };
    },
  };
}

function makeFixture() {
  stage = makeStage();
  canvas = { id: 'vmVrmCompanionCanvas' };
  el = {
    classList: makeClassList(['vm-companion-staged']),
    style: makeStyle({ '--vm-stage-x': '40px', '--vm-stage-y': '20px' }),
    offsetWidth: 200, offsetHeight: 300,
    listeners: {}, listenerOptions: {},
    addEventListener: function (type, fn, opts) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
      (this.listenerOptions[type] = this.listenerOptions[type] || []).push(opts);
    },
    setPointerCapture: function () { this.captured = true; },
    contains: function (node) { return node === this || node === canvas; },
    // The visual rect includes the current transform; the untransformed layout
    // origin is 90/100, so stageBaseRect() always recovers that same base.
    getBoundingClientRect: function () {
      const x = parseFloat(this.style.getPropertyValue('--vm-stage-x')) || 0;
      const y = parseFloat(this.style.getPropertyValue('--vm-stage-y')) || 0;
      return {
        left: 90 + x, top: 100 + y, right: 290 + x, bottom: 400 + y,
        width: 200, height: 300,
      };
    },
  };
  win = makeWindow();
  const ids = { vmVrmCompanion: el, vmCompanionStage: stage, vmVrmCompanionCanvas: canvas };
  return function $id(id) { return ids[id] || null; };
}

// ── behavioral clamp test ──────────────────────────────────────────────────
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
  assert('stage bounds honour the real stage padding/safe-top',
    bounds.left === 10 && bounds.top === 80 && bounds.right === 490 && bounds.bottom === 490,
    JSON.stringify(bounds));

  const high = helpers.clampStageOffset(400, -100);
  assert('drag clamps to right edge and safe top', high.x === 200 && high.y === -20, JSON.stringify(high));
  const low = helpers.clampStageOffset(-500, 500);
  assert('drag clamps to left edge and bottom edge', low.x === -80 && low.y === 90, JSON.stringify(low));
  assert('clamped visual rect stays inside stage padding',
    90 + high.x >= bounds.left && 100 + high.y >= bounds.top &&
    90 + high.x + 200 <= bounds.right && 100 + high.y + 300 <= bounds.bottom);
}

// ── behavioral pointer + touch drag test ───────────────────────────────────
const dragFns = ['runDragFrame', 'stopDragFrame', 'wireDrag']
  .map(extractFunction.bind(null, js)).join('\n');
if (helperSrc && dragFns) {
  const $idB = makeFixture();
  const prefs = { stageX: null, stageY: null, x: null, y: null, userPlaced: false };
  let saves = 0, cancels = 0;
  const rafQueue = [];
  const raf = function (fn) { rafQueue.push(fn); return rafQueue.length; };
  const cancelRaf = function () { cancels++; };
  const drag = new Function(
    '$id', 'window', 'prefs', 'savePrefs', 'cancelWalk',
    'requestAnimationFrame', 'cancelAnimationFrame',
    'let dragState = null; let dragRaf = 0; let dragTargetX = 0; let dragTargetY = 0;\n' +
    helperSrc + '\n' + dragFns +
    '\nreturn { wireDrag, runDragFrame, state: function () { return dragState; },' +
    ' target: function () { return { x: dragTargetX, y: dragTargetY }; } };'
  )($idB, win, prefs, function () { saves++; }, function () { }, raf, cancelRaf);

  drag.wireDrag();
  const pointerDownCount = el.listeners.pointerdown.length;
  drag.wireDrag();
  assert('wireDrag is idempotent and installs pointer + touch handlers',
    el.listeners.pointerdown.length === 1 && pointerDownCount === 1 &&
    el.listeners.touchstart.length === 1 &&
    stage.listeners.pointerdown.length === 1 && stage.listeners.touchstart.length === 1 &&
    (win.listeners.pointermove || []).length === 1 &&
    (win.listeners.pointerup || []).length === 1 &&
    (win.listeners.pointercancel || []).length === 1 &&
    (win.listeners.touchmove || []).length === 1 &&
    (win.listeners.touchend || []).length === 1 &&
    (win.listeners.touchcancel || []).length === 1);
  assert('touch listeners use { passive: false } at element and window',
    el.listenerOptions.touchstart[0] &&
    el.listenerOptions.touchstart[0].passive === false &&
    el.listenerOptions.touchmove[0].passive === false &&
    el.listenerOptions.touchend[0].passive === false &&
    win.listenerOptions.touchmove[0].passive === false &&
    win.listenerOptions.touchend[0].passive === false);

  let pointerPrevented = false;
  el.listeners.pointerdown[0]({
    pointerId: 7, button: 0, clientX: 150, clientY: 200, target: canvas,
    currentTarget: el, cancelable: true, preventDefault: function () { pointerPrevented = true; },
  });
  assert('pointerdown starts a stage drag from the WebGL canvas',
    !!drag.state() && drag.state().staged === true && pointerPrevented && !!el.captured);

  win.listeners.pointermove[0]({ pointerId: 7, clientX: 800, clientY: 900 });
  const target = drag.target();
  assert('window pointermove clamps the stage-relative target',
    target.x === 200 && target.y === 90, JSON.stringify(target));
  drag.runDragFrame();
  assert('drag frame writes transform custom properties',
    el.style.getPropertyValue('--vm-stage-x') !== '' &&
    el.style.getPropertyValue('--vm-stage-y') !== '');
  el.listeners.pointerup[0]({ pointerId: 7 });
  assert('pointerup settles/persists the clamped transform and releases drag state',
    prefs.stageX === 200 && prefs.stageY === 90 && prefs.userPlaced === true &&
    el.style.getPropertyValue('--vm-stage-x') === '200px' &&
    el.style.getPropertyValue('--vm-stage-y') === '90px' &&
    saves === 1 && drag.state() === null &&
    !el.classList.contains('vm-companion-dragging') &&
    el.style.cursor === 'grab' && cancels === 1);

  let touchStartPrevented = false, touchMovePrevented = false, touchEndPrevented = false;
  const touchPoint = { identifier: 3, clientX: 150, clientY: 200 };
  el.listeners.touchstart[0]({
    changedTouches: [touchPoint], target: canvas, currentTarget: el,
    cancelable: true, preventDefault: function () { touchStartPrevented = true; },
  });
  assert('touchstart starts a non-passive stage drag',
    !!drag.state() && drag.state().touch === true && drag.state().id === 'touch:3' && touchStartPrevented);
  win.listeners.touchmove[0]({
    changedTouches: [{ identifier: 3, clientX: 800, clientY: 900 }],
    cancelable: true, preventDefault: function () { touchMovePrevented = true; },
  });
  assert('touchmove clamps the target and cancels browser panning',
    touchMovePrevented && drag.target().x === 200 && drag.target().y === 90,
    JSON.stringify(drag.target()));
  drag.runDragFrame();
  win.listeners.touchend[0]({
    changedTouches: [{ identifier: 3, clientX: 800, clientY: 900 }],
    cancelable: true, preventDefault: function () { touchEndPrevented = true; },
  });
  assert('touchend persists the transform and releases touch state',
    touchEndPrevented && prefs.stageX === 200 && prefs.stageY === 90 &&
    saves === 2 && drag.state() === null);

  // A tap (no movement) must never re-open the removed inner box.
  el.listeners.touchstart[0]({
    changedTouches: [{ identifier: 4, clientX: 150, clientY: 200 }],
    target: canvas, currentTarget: el, cancelable: true, preventDefault: function () { },
  });
  el.listeners.touchend[0]({
    changedTouches: [{ identifier: 4, clientX: 150, clientY: 200 }],
    cancelable: true, preventDefault: function () { },
  });
  assert('tapping/stage-tapping the character does nothing and saves nothing',
    drag.state() === null && saves === 2 && !el.classList.contains('vm-companion-dragging'));
}

// ── behavioral procedural gesture fallback test ───────────────────────────
const gestureConstSrc = js.slice(js.indexOf('const GESTURE_STATUS = {'), js.indexOf('const EMOTION_STATUS = {'));
const gestureFnNames = [
  'humanBoneNode', 'gestureClipReady', 'restoreGestureBones', 'applyGestureFrame',
  'runGestureFallbackFrame', 'cancelGestureFallback', 'releaseGestureFallback',
  'startGestureFallback', 'playVrmGesture',
];
const gestureFns = gestureFnNames.map(function (name) {
  const src = extractFunction(js, name);
  assert('found gesture helper ' + name, !!src);
  return src;
}).join('\n');

async function gestureFallbackBehavior() {
  if (!gestureConstSrc || !gestureFns) return;
  function makeBone() {
    return {
      rotation: {
        x: 0, y: 0, z: 0, order: 'XYZ',
        set: function (x, y, z, order) {
          this.x = x; this.y = y; this.z = z; this.order = order;
        },
      },
    };
  }
  const bones = {
    rightUpperArm: makeBone(), rightLowerArm: makeBone(), rightHand: makeBone(),
    head: makeBone(),
  };
  const vrm = {
    humanoid: {
      getRawBoneNode: function (name) { return bones[name] || null; },
      getNormalizedBoneNode: function () { return null; },
    },
  };
  let triggered = 0, resolveClip = null;
  const engine = {
    vrm: vrm,
    clipCache: new Map(),
    manifest: { behaviours: { waving: ['wave_a.vrma'] } },
    trigger: function (behaviour) {
      triggered++;
      return new Promise(function (resolve) { resolveClip = resolve; });
    },
  };
  const rafQueue = [];
  const api = new Function(
    'engine', 'requestAnimationFrame', 'cancelAnimationFrame',
    'let gestureFallback = null;\n' + gestureConstSrc + '\n' + gestureFns +
    '\nreturn { playVrmGesture, applyGestureFrame, releaseGestureFallback,' +
    ' fallback: function () { return gestureFallback; } };'
  )(engine, function (fn) { rafQueue.push(fn); return rafQueue.length; }, function () { });

  const played = api.playVrmGesture('waving');
  assert('first gesture tap starts the real clip and an immediate bone fallback',
    triggered === 1 && !!api.fallback() && api.fallback().behaviour === 'waving' &&
    bones.rightUpperArm.rotation.z < -0.1 && bones.rightHand.rotation.z !== 0 &&
    rafQueue.length === 1);
  resolveClip(true);
  const ok = await played;
  assert('resolved real clip releases the procedural pose',
    ok === true && api.fallback() && api.fallback().releasing === true);
  api.applyGestureFrame(api.fallback(), Date.now() + 500);
  assert('released bones return to their authored rotation',
    bones.rightUpperArm.rotation.x === 0 &&
    bones.rightUpperArm.rotation.y === 0 &&
    bones.rightUpperArm.rotation.z === 0 &&
    bones.rightHand.rotation.z === 0 &&
    api.fallback() === null);
}

(async function () {
  await gestureFallbackBehavior();
  console.log('\nVM_COMPANION_WORKSPACE_DRAG_TESTS: ' + ok + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})().catch(function (err) {
  console.error('VM_COMPANION_WORKSPACE_DRAG_TESTS error: ' + (err && err.stack || err));
  process.exit(1);
});
