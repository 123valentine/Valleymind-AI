// Tests for static/vm_richrender.js add-ons: dark code/copy cards, file cards,
// badges, thumbnail grids, syntax highlighting, clipboard and download fallbacks.
const fs = require('fs');
const vm = require('vm');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 200) : '')); fail++; }
}
function escapeHtml(text) {
  return String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function marked(src) {
  const text = String(src || '');
  if (/```[\s\S]*?```/.test(text)) {
    return text
      .replace(/```([a-zA-Z0-9_+#-]*)\n?([\s\S]*?)```/g,
        (m, lang, code) => '<pre><code class="language-' + lang + '">' + escapeHtml(code.replace(/\n$/, '')) + '</code></pre>')
      .split(/\n\s*\n/).map((part) => part.trim().indexOf('<pre>') === 0 ? part : '<p>' + escapeHtml(part).replace(/\n/g, '<br>') + '</p>')
      .join('\n');
  }
  if (/^\|.*\|/m.test(text) && /\|[-:| ]+\|/m.test(text)) {
    return '<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>';
  }
  return text.split(/\n\s*\n/).map((p) => '<p>' + escapeHtml(p).replace(/\n/g, '<br>') + '</p>').join('\n');
}
marked.parse = marked;

function makeWalker() { return { nextNode: function () { return null; } }; }
function StubDOMParser() {}
StubDOMParser.prototype.parseFromString = function (html) {
  return { body: { innerHTML: html, createTreeWalker: makeWalker }, createTreeWalker: makeWalker };
};

const calls = { execCommand: 0, fetch: [], objectUrls: [], revoked: [], anchors: [], opens: [], hljs: [], native: [] };

const documentStub = {
  readyState: 'complete',
  createTreeWalker: makeWalker,
  addEventListener: function () {},
  createElement: function (tag) {
    return {
      tagName: String(tag || '').toUpperCase(),
      value: '',
      style: {},
      href: '',
      download: '',
      rel: '',
      parentNode: null,
      setAttribute: function (name, value) { this[name] = value; },
      select: function () {},
      setSelectionRange: function () {},
      click: function () { if (tag === 'a') calls.anchors.push(this); }
    };
  },
  body: {
    appendChild: function (el) { el.parentNode = this; },
    removeChild: function (el) { el.parentNode = null; }
  },
  documentElement: { appendChild: function () {}, removeChild: function () {} }
};
documentStub.execCommand = function () { calls.execCommand++; return true; };

const urlStub = {
  createObjectURL: function (blob) { const id = 'blob:vm-' + calls.objectUrls.length; calls.objectUrls.push({ id, blob }); return id; },
  revokeObjectURL: function (id) { calls.revoked.push(id); }
};

const win = {
  DOMParser: StubDOMParser,
  marked: marked,
  document: documentStub,
  navigator: {},
  URL: urlStub,
  console: console,
  Promise: Promise,
  Object: Object,
  Date: Date,
  // Skip only the 30s blob-URL cleanup timer so the test process exits; the
  // browser still schedules it normally via window.setTimeout.
  setTimeout: function (fn, ms) { return Number(ms) >= 30000 ? 0 : setTimeout(fn, ms); },
  clearTimeout: clearTimeout,
  NodeFilter: { SHOW_ELEMENT: 1, SHOW_COMMENT: 128 },
  hljs: {
    highlightElement: function (el) { calls.hljs.push(el); if (el.classList) el.classList.add('hljs'); }
  },
  fetch: function (url, opts) {
    calls.fetch.push({ url, opts });
    return Promise.resolve({ ok: true, blob: function () { return Promise.resolve({ size: 12 }); } });
  },
  open: function (url) { calls.opens.push(url); }
};
const ctx = {
  window: win,
  document: documentStub,
  DOMParser: StubDOMParser,
  marked: marked,
  navigator: win.navigator,
  URL: urlStub,
  console: console,
  Promise: Promise,
  Object: Object,
  Date: Date,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  NodeFilter: win.NodeFilter
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('static/settings.js', 'utf8'), ctx);
vm.runInContext(fs.readFileSync('static/vm_richrender.js', 'utf8'), ctx);

const R = ctx.window.VMRichRender;
const Addons = ctx.window.VMRichRenderAddons;

(async function () {
  assert('addons installed on renderer', !!Addons && typeof R.render === 'function');

  const proseHtml = R.render('Here is the image I created. Want me to try a different style or lighting?');
  assert('ordinary prose has no bordered copy card',
    proseHtml.indexOf('vm-code-card') === -1 &&
    proseHtml.indexOf('vm-copy-card') === -1 &&
    proseHtml.indexOf('⧉ Copy') === -1,
    proseHtml.slice(0, 260));
  assert('automatic copy-box wrapping is disabled', ctx.window.VMAutoCopyReady({}, proseHtml) === false);

  const imageHtml = R.render('[[IMAGE_CARD: /api/images/gen-1.png | Generated image | valleymind_image.png]]');
  assert('image card renders a real image element',
    /<img\s[^>]*src="\/api\/images\/gen-1\.png"/.test(imageHtml) &&
    imageHtml.indexOf('vm-image-card') !== -1,
    imageHtml.slice(0, 320));
  assert('image card uses icon-only circular download button with spinner support',
    imageHtml.indexOf('data-download-url="/api/images/gen-1.png"') !== -1 &&
    imageHtml.indexOf('vm-icon-download') !== -1 &&
    imageHtml.indexOf('title="Download"') !== -1 &&
    imageHtml.indexOf('aria-label="Download"') !== -1 &&
    imageHtml.indexOf('vm-download-icon') !== -1 &&
    imageHtml.indexOf('>Download<') === -1,
    imageHtml.slice(0, 420));

  const codeHtml = R.render('```python\nprint("hello")\n```');
  assert('fenced code becomes dark code card', codeHtml.indexOf('vm-code-card') !== -1 && codeHtml.indexOf('vm-fenced-code') !== -1, codeHtml.slice(0, 240));
  assert('fenced code card shows language label', codeHtml.indexOf('>Python<') !== -1, codeHtml.slice(0, 240));
  assert('fenced code card has syntax class', codeHtml.indexOf('class="hljs language-python"') !== -1, codeHtml.slice(0, 240));
  assert('fenced code card has copy button', codeHtml.indexOf('⧉ Copy') !== -1 && /data-copy-id="vmcopy\d+"/.test(codeHtml), codeHtml.slice(0, 300));
  assert('fenced code card keeps code text', codeHtml.indexOf('print(&quot;hello&quot;)') !== -1 || codeHtml.indexOf('print("hello")') !== -1, codeHtml.slice(0, 300));
  const codeCopyId = (/data-copy-id="(vmcopy\d+)"/.exec(codeHtml) || [])[1];
  assert('fenced code copy text registered', codeCopyId && ctx.window.__VM_RICH_COPY_TEXT[codeCopyId].indexOf('print("hello")') !== -1,
    codeCopyId && ctx.window.__VM_RICH_COPY_TEXT[codeCopyId]);

  const copyHtml = R.render('[[COPY_BOX: Prompt | Write a short haiku about Lagos]]');
  assert('COPY_BOX renders titled copy card', copyHtml.indexOf('vm-copy-card') !== -1 && copyHtml.indexOf('>Prompt<') !== -1, copyHtml.slice(0, 260));
  assert('COPY_BOX keeps content and copy button', copyHtml.indexOf('Write a short haiku about Lagos') !== -1 && copyHtml.indexOf('⧉ Copy') !== -1, copyHtml.slice(0, 260));
  assert('COPY_BOX does not leak wire syntax', copyHtml.indexOf('[[COPY_BOX:') === -1, copyHtml);

  const fileHtml = R.render('[[FILE_CARD: Weekly Report | PDF | 18.4 KB | /api/files/download/abc?token=xyz]]');
  assert('FILE_CARD renders dark download card', fileHtml.indexOf('vm-file-card') !== -1 && fileHtml.indexOf('Weekly Report') !== -1, fileHtml.slice(0, 260));
  assert('FILE_CARD has working icon-only download target',
    fileHtml.indexOf('data-download-url="/api/files/download/abc?token=xyz"') !== -1 &&
    fileHtml.indexOf('title="Download"') !== -1 &&
    fileHtml.indexOf('aria-label="Download"') !== -1 &&
    fileHtml.indexOf('vm-download-icon') !== -1 &&
    fileHtml.indexOf('>Download<') === -1, fileHtml.slice(0, 340));
  assert('FILE_CARD shows metadata', fileHtml.indexOf('PDF') !== -1 && fileHtml.indexOf('18.4 KB') !== -1, fileHtml.slice(0, 260));

  const iconHtml = ctx.window.VMDownloadIconSvg();
  const fileButton = {
    innerHTML: iconHtml,
    style: { pointerEvents: '' },
    attrs: { 'data-download-url': '/api/files/download/abc?token=xyz', 'data-download-name': 'weekly report.pdf' },
    classList: {
      flags: {},
      add(name) { this.flags[name] = true; },
      remove(name) { delete this.flags[name]; },
      contains(name) { return !!this.flags[name]; }
    },
    getAttribute(name) { return this.attrs[name] || ''; },
    setAttribute(name, value) { this.attrs[name] = String(value); }
  };
  const fileRoot = { querySelectorAll(sel) { return sel.indexOf('data-download-url') !== -1 ? [fileButton] : []; } };
  ctx.window.VMFormatRichBubble(fileRoot);
  calls.fetch.length = 0;
  fileButton.onclick({ stopPropagation() {} });
  assert('FILE_CARD click replaces icon with circular spinner',
    fileButton.innerHTML.indexOf('vm-download-spinner') !== -1 &&
    fileButton.classList.contains('vm-downloading') &&
    fileButton.style.pointerEvents === 'none' &&
    fileButton.getAttribute('aria-busy') === 'true',
    fileButton.innerHTML.slice(0, 200));
  for (let i = 0; i < 100 && fileButton.__vmDownloadActive; i++) {
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert('FILE_CARD restores icon after download settles',
    fileButton.innerHTML === iconHtml &&
    !fileButton.classList.contains('vm-downloading') &&
    fileButton.style.pointerEvents === '' &&
    fileButton.getAttribute('aria-busy') === 'false',
    fileButton.innerHTML.slice(0, 200));
  assert('FILE_CARD spinner still fetches signed download',
    calls.fetch.length === 1 && calls.fetch[0].url === '/api/files/download/abc?token=xyz', JSON.stringify(calls.fetch));
  calls.fetch.length = 0;
  calls.anchors.length = 0;
  calls.objectUrls.length = 0;

  const badgeHtml = R.render('[[BADGE: Cards | blue]] [[BADGE: Copy boxes | green]] [[BADGE: Example | amber]]');
  assert('BADGE renders soft tinted pills', badgeHtml.indexOf('vm-badge-blue') !== -1 && badgeHtml.indexOf('vm-badge-green') !== -1 && badgeHtml.indexOf('vm-badge-amber') !== -1, badgeHtml.slice(0, 300));
  assert('BADGE hides wire syntax', badgeHtml.indexOf('[[BADGE:') === -1, badgeHtml);

  const thumbHtml = R.render('[[THUMB_GRID: Cards | Rounded UI cards | 🧩; Thumbnails | Multi-column previews | ▦]]');
  assert('THUMB_GRID renders multi-column cards', (thumbHtml.match(/vm-thumb-card/g) || []).length === 2 && thumbHtml.indexOf('vm-thumb-grid') !== -1, thumbHtml.slice(0, 340));
  assert('THUMB_GRID keeps titles/subtitles', thumbHtml.indexOf('Rounded UI cards') !== -1 && thumbHtml.indexOf('Multi-column previews') !== -1, thumbHtml.slice(0, 340));

  assert('selector detects new rich blocks', ctx.window.VMPresentationSelector.select('[[FILE_CARD: A | PDF | 1 KB | /api/files/download/x]]') === 'rich');
  assert('selector still detects plain text', ctx.window.VMPresentationSelector.select('Hello there') === 'plain');

  const codeEl = { classList: { add: function (name) { this[name] = true; }, contains: function () { return false; } } };
  const root = { querySelectorAll: function (sel) { return sel === 'pre code' ? [codeEl] : []; } };
  ctx.window.VMFormatRichBubble(root);
  assert('format bubble runs highlight.js on card code', calls.hljs.indexOf(codeEl) !== -1, JSON.stringify(calls.hljs.length));

  const copied = await ctx.window.VMCopyText('Android clipboard fallback');
  assert('clipboard falls back to execCommand in WebView', copied === true && calls.execCommand > 0, 'copied=' + copied + ' exec=' + calls.execCommand);

  const downloaded = await ctx.window.VMDownloadFile('/api/files/download/abc?token=xyz', 'weekly report.pdf');
  assert('download fetches signed URL with same-origin credentials', downloaded === true && calls.fetch.length === 1 && calls.fetch[0].opts.credentials === 'same-origin', JSON.stringify(calls.fetch));
  assert('download uses blob anchor with safe filename', calls.objectUrls.length === 1 && calls.anchors.length === 1 && calls.anchors[0].download === 'weekly report.pdf', JSON.stringify(calls.anchors.map((a) => a.download)));
  assert('download schedules blob URL cleanup', calls.revoked.length === 0, JSON.stringify(calls.revoked));

  calls.fetch.length = 0;
  ctx.window.vmCompanionAndroid = { downloadFile: function (url, filename) { calls.native.push({ url, filename }); return true; } };
  const nativeDownloaded = await ctx.window.VMDownloadFile('/api/files/download/def?token=uvw', 'native.pdf');
  assert('download uses native bridge when available', nativeDownloaded === true && calls.native.length === 1 && calls.fetch.length === 0, JSON.stringify(calls.native));
  delete ctx.window.vmCompanionAndroid;

  console.log('RENDER_ADDON_TESTS: ' + ok + ' passed, ' + fail + ' failed');
  if (fail) process.exit(1);
})().catch((err) => {
  console.log('FAIL: addon test harness threw :: ' + (err && err.stack ? err.stack : err));
  process.exit(1);
});
