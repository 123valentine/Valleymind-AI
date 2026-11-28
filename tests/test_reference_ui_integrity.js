// Reference-photo UI integrity: icon-only media controls, correct generation
// frames, locked mobile header, recent-chat sidebar and transparent Studio cutouts.
const fs = require('fs');
const vm = require('vm');

let ok = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 240) : '')); fail++; }
}

const html = fs.readFileSync('index.html', 'utf8');
const settingsSrc = fs.readFileSync('static/settings.js', 'utf8');
const richSrc = fs.readFileSync('static/vm_richrender.js', 'utf8');

function makeElement(tag) {
  return {
    tagName: String(tag || '').toUpperCase(),
    children: [],
    className: '',
    textContent: '',
    innerHTML: '',
    value: '',
    dataset: {},
    style: { cssText: '' },
    attrs: {},
    parentNode: null,
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
    focus() {}, click() {}
  };
}

const messages = makeElement('div');
const documentStub = {
  readyState: 'complete',
  createElement: makeElement,
  body: makeElement('body'),
  getElementById(id) { return id === 'messages' ? messages : null; },
  querySelector() { return null; },
  addEventListener() {}
};

const win = {
  DOMParser: function () {},
  marked: function () {},
  document: documentStub,
  navigator: {},
  URL: { createObjectURL() { return 'blob:vm-test'; }, revokeObjectURL() {} },
  console,
  Promise,
  Object,
  Date,
  setTimeout,
  clearTimeout,
  NodeFilter: { SHOW_ELEMENT: 1, SHOW_COMMENT: 128 },
  fetch() { return Promise.resolve({ ok: true, blob() { return Promise.resolve({ size: 8 }); } }); },
  open() {}
};
const ctx = {
  window: win,
  document: documentStub,
  DOMParser: win.DOMParser,
  marked: win.marked,
  navigator: win.navigator,
  URL: win.URL,
  console,
  Promise,
  Object,
  Date,
  setTimeout,
  clearTimeout,
  NodeFilter: win.NodeFilter,
  fetch: win.fetch
};
vm.createContext(ctx);
vm.runInContext(settingsSrc, ctx);
vm.runInContext(richSrc, ctx);

function findByClass(node, name) {
  if (!node) return null;
  if (String(node.className || '').split(/\s+/).indexOf(name) !== -1) return node;
  for (const child of node.children || []) {
    const found = findByClass(child, name);
    if (found) return found;
  }
  return null;
}

function functionSource(source, signature) {
  const match = source.match(new RegExp('function ' + signature + '[\\s\\S]*?\\n\\}'));
  return match ? match[0] : '';
}

// ── Shared circular SVG icons ───────────────────────────────────────────────
check('settings exports a regenerate icon helper',
  /window\.VMRegenerateIconSvg = VMRegenerateIconSvg;/.test(settingsSrc));
check('settings exports a shared status spinner',
  /function VMStatusSpinner\(\)/.test(settingsSrc) &&
  /window\.VMStatusSpinner = VMStatusSpinner;/.test(settingsSrc));
check('renderer exposes a regenerate icon helper',
  typeof ctx.window.VMRichRenderAddons.regenerateIconSvg === 'function' &&
  /<svg[\s\S]*vm-regenerate-icon/.test(ctx.window.VMRichRenderAddons.regenerateIconSvg()));
check('renderer exposes a download icon helper',
  typeof ctx.window.VMRichRenderAddons.downloadIconSvg === 'function' &&
  /<svg[\s\S]*vm-download-icon/.test(ctx.window.VMRichRenderAddons.downloadIconSvg()));

// ── Generation skeleton frame and label ────────────────────────────────────
for (const signature of [
  'showGenerationSkeleton\\(kind\\) \\{',
  'showImageGenSkeleton\\(reference\\) \\{',
  'showVideoGenSkeleton\\(\\) \\{',
  'toggleSessionListScope\\(\\) \\{'
]) {
  const name = signature.split('\\')[0];
  check(name + ' source extracted', !!functionSource(html, signature));
  vm.runInContext(functionSource(html, signature), ctx);
}
ctx.removeThinkingIndicator = function () {};
ctx._imgGenStartTime = 0;

function badge(wrapper) {
  const el = findByClass(wrapper, 'skel-badge');
  return el ? el.textContent : '';
}
function skeletonCard(wrapper) { return findByClass(wrapper, 'img-gen-skeleton'); }

const editSkeleton = ctx.showImageGenSkeleton(true);
check('reference edit skeleton shows honest progress timing',
  badge(editSkeleton) === 'Editing your photo with AI (~15–30s)...', badge(editSkeleton));
check('reference edit skeleton uses 4/5 frame', skeletonCard(editSkeleton).classList.contains('vm-gen-edit') && editSkeleton.dataset.generationKind === 'edit');
check('reference flag from SSE selects edit skeleton', badge(ctx.showImageGenSkeleton('reference')) === 'Editing your photo with AI (~15–30s)...');

const videoSkeleton = ctx.showVideoGenSkeleton();
check('video skeleton shows honest progress timing',
  badge(videoSkeleton) === 'Generating video (~30–60s)...', badge(videoSkeleton));
check('video skeleton uses 16/9 frame', skeletonCard(videoSkeleton).classList.contains('vm-gen-video') && videoSkeleton.dataset.generationKind === 'video');

const imageSkeleton = ctx.showImageGenSkeleton(false);
check('plain image skeleton keeps its own label', badge(imageSkeleton) === 'Generating image...', badge(imageSkeleton));
check('SSE passes the reference flag to the skeleton', /showImageGenSkeleton\(evt\.reference\)/.test(html));
check('generating_video intent mounts the video skeleton', /evt\.intent === "generating_video"[\s\S]{0,160}showVideoGenSkeleton\(\)/.test(html));
check('video progress updates the skeleton badge', /statusIndicator\.querySelector\("\.skel-badge"\)/.test(html));

// ── Media card geometry and icon-only controls ─────────────────────────────
const geo = ctx.window.VMRichRenderAddons.mediaGeometry;
check('research portrait keeps the 2:3 poster frame',
  geo('portrait').width === 124 && geo('portrait').height === 186 && geo('portrait').aspect === '2/3');
check('portrait edit card uses 160x200 4/5',
  geo('edit').width === 160 && geo('edit').height === 200 && geo('edit').aspect === '4/5');
check('explicit 4:5 hint uses the same edit frame', geo('4/5').aspect === '4/5');
check('video card uses 208x117 16/9',
  geo('video').width === 208 && geo('video').height === 117 && geo('video').aspect === '16/9');

const editCard = ctx.window.VMRichRenderAddons.imageCardHtml(
  '/api/images/edit-1.png | Edited photo | valleymind_image.png | edit | Change my outfit'
);
check('edit card renders at 4/5', /width:160px/.test(editCard) && /height:200px/.test(editCard) && /aspect-ratio:4\/5/.test(editCard));
check('edit card is one full-width card with a separate action bar',
  /display:block;width:100%;max-width:100%/.test(editCard) &&
  /vm-media-card-actions/.test(editCard) &&
  !/position:absolute;bottom:10px/.test(editCard));
check('edit card has circular regenerate and download controls',
  /aria-label="Regenerate"/.test(editCard) && /aria-label="Download"/.test(editCard) &&
  /vm-regenerate-icon/.test(editCard) && /vm-download-icon/.test(editCard) &&
  /border-radius:50%/.test(editCard));
check('edit card controls carry their prompts and targets',
  /data-regenerate-prompt="Change my outfit"/.test(editCard) && /data-download-url=/.test(editCard));

const videoCard = ctx.window.VMRichRenderAddons.imageCardHtml(
  '/api/videos/clip.mp4 | Generated video | valleymind_video.mp4 | video'
);
check('video card renders at 16/9', /width:208px/.test(videoCard) && /height:117px/.test(videoCard) && /aspect-ratio:16\/9/.test(videoCard));

// ── Video result card buttons are icons, not text links ────────────────────
const videoButtons = html.match(/\/\* Action buttons row: circular icon-only controls\. \*\/[\s\S]*?vidBubble\.appendChild\(btnRow\);/);
check('video action row source extracted', !!videoButtons);
if (videoButtons) {
  const vidBubble = makeElement('div');
  ctx.vidBubble = vidBubble;
  ctx.evt = { video_url: '/static/generated/clip.mp4', query: 'a city at night' };
  vm.runInContext(videoButtons[0], ctx);
  const download = findByClass(vidBubble, 'vm-media-download');
  const regenerate = findByClass(vidBubble, 'vm-media-regenerate');
  check('video download is a circular icon-only button',
    !!download && download.tagName === 'BUTTON' && download.title === 'Download' &&
    download.getAttribute('aria-label') === 'Download' && /<svg/.test(download.innerHTML));
  check('video regenerate is a circular icon-only button',
    !!regenerate && regenerate.tagName === 'BUTTON' && regenerate.title === 'Regenerate' &&
    regenerate.getAttribute('aria-label') === 'Regenerate' && /<svg/.test(regenerate.innerHTML));
  check('video action row no longer renders text buttons',
    !/textContent = "Download"/.test(videoButtons[0]) && !/textContent = "Regenerate"/.test(videoButtons[0]));
}
check('generated image regenerate button is icon-only',
  /editBtn\.setAttribute\("aria-label", "Regenerate"\)/.test(html) &&
  /editBtn\.innerHTML = typeof window\.VMRegenerateIconSvg/.test(html));
check('media regenerate control restores its prompt in the composer',
  /\[data-copy-id\],\[data-download-url\],\[data-regenerate-prompt\]/.test(richSrc) &&
  /input\.value = attr\(this, "data-regenerate-prompt"\)/.test(richSrc));

// ── Mobile shell stays fixed while streams and videos replay ───────────────
check('mobile viewport cannot widen', /html, body \{ width:100%!important; max-width:100%!important; overflow-x:hidden!important; \}/.test(html));
check('mobile header is pinned and clipped',
  /#topHeader \{[\s\S]{0,400}position:fixed!important[\s\S]{0,200}overflow:hidden!important/.test(html));
check('header children shrink instead of scattering', /#topHeader > \* \{ min-width:0!important; flex-shrink:1!important; \}/.test(html));
check('messages and video stay inside the viewport',
  /#messages, #messages \.bubble, #messages video \{ max-width:100%!important/.test(html));

// ── Sidebar recent-five toggle and mobile navigation ───────────────────────
check('sidebar lists five newest chats by default',
  /var displayedSessions = sessionListExpanded[\s\S]{0,120}visibleSessions\.slice\(0, 5\)/.test(html));
check('scope toggle switches its accessible label',
  /scopeLabel = sessionListExpanded \? "Show recent" : "Show all chats"/.test(html) &&
  /scopeBtn\.setAttribute\("aria-expanded"/.test(html));
check('scope toggle button exists and is labelled',
  /id="sessionListToggle"[\s\S]{0,300}aria-label="Show all chats"/.test(html));
let renderCalls = 0;
ctx.renderSessionList = function () { renderCalls++; };
ctx.sessionListExpanded = false;
ctx.toggleSessionListScope();
check('toggle expands and re-renders', ctx.sessionListExpanded === true && renderCalls === 1);
ctx.toggleSessionListScope();
check('toggle returns to recent view', ctx.sessionListExpanded === false && renderCalls === 2);
check('mobile drawer keeps Studio, Help and Settings reachable',
  /\.sidebar #mainNav\{display:block!important\}/.test(html) &&
  /\.sidebar #nav-studio, \.sidebar #nav-help\{display:flex!important\}/.test(html) &&
  /\.sidebar #settingsGearBtn\{display:flex!important/.test(html));

// ── Studio Background Remover produces a transparent preview ───────────────
check('checkerboard preview style exists',
  /\.vm-photo-compare\.vm-transparent \{[\s\S]{0,600}linear-gradient\(45deg/.test(html));
check('Remove Background maps to the cutout mode',
  html.includes('/remove.?background|remove.?bg|cutout|transparent/i.test(toolTitle)') &&
  html.includes('vmPhotoToolMode = "remove-bg"'));
check('Studio calls the dedicated remove-bg endpoint', html.includes('"/api/studio/remove-bg"'));
check('Studio toggles the checkerboard for the transparent result',
  html.includes('compare.classList.toggle("vm-transparent", transparent)') &&
  html.includes('compare.classList.toggle("vm-transparent", vmPhotoToolMode === "remove-bg")'));
check('Studio downloads a transparent PNG cutout', html.includes('valleymind_cutout.png'));
check('Remove Background auto-processes on file selection',
  /if \(vmPhotoToolMode === "remove-bg"\) \{[\s\S]{0,260}vmPhotoToolApply\(\);/.test(html));
check('Remove Background shows a spinner while processing',
  /status\.innerHTML = spinner \+ \(vmPhotoToolMode === "remove-bg"[\s\S]{0,120}"Removing background\.\.\."/.test(html));
check('finished media cards scroll smoothly into view',
  /function vmScrollToFinishedCard/.test(html) &&
  /scrollIntoView\(\{ behavior: "smooth", block: "nearest" \}\)/.test(html) &&
  /vmScrollToFinishedCard\(wrapper\)/.test(html));

console.log('REFERENCE_UI_INTEGRITY_TESTS: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exitCode = 1;
