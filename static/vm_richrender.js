/*
 * VMRichRender add-ons: dark copy/code cards, syntax colouring, file download
 * cards, badges and thumbnail grids.
 *
 * This file loads AFTER static/settings.js. It wraps the existing renderer and
 * formatter rather than replacing them, so every older Stage 3 convention keeps
 * working while the new structured blocks are rendered as real UI.
 */
(function () {
  "use strict";

  var renderer = window.VMRichRender;
  if (!renderer || renderer.__vmRichAddonsInstalled) return;
  renderer.__vmRichAddonsInstalled = true;

  var TOKEN_START = "\uFFF9";
  var TOKEN_END = "\uFFFA";
  var copySeq = 0;

  var FALLBACK_DOWNLOAD_ICON = '<svg class="vm-download-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  var FALLBACK_DOWNLOAD_SPINNER = '<svg class="vm-download-spinner" width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle class="vm-spinner-track" cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5"/><circle class="vm-spinner-arc" cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5" stroke-dasharray="28 28" stroke-linecap="round"/></svg>';

  function downloadIconSvg() {
    return typeof window.VMDownloadIconSvg === "function"
      ? window.VMDownloadIconSvg() : FALLBACK_DOWNLOAD_ICON;
  }

  function downloadSpinnerSvg() {
    return typeof window.VMDownloadSpinnerSvg === "function"
      ? window.VMDownloadSpinnerSvg() : FALLBACK_DOWNLOAD_SPINNER;
  }

  window.__VM_RICH_COPY_TEXT = window.__VM_RICH_COPY_TEXT || {};

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function decodeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#0*39;|&#x0*27;/gi, "'")
      .replace(/&nbsp;/g, " ")
      .replace(/&amp;/g, "&");
  }

  function tokenFor(index) {
    return TOKEN_START + "VM" + index + TOKEN_END;
  }

  function safeUrl(value) {
    var url = String(value || "").trim();
    return /^(https?:\/\/|\/[^\/])/i.test(url) ? url : "";
  }

  function attr(el, name) {
    if (!el) return "";
    if (typeof el.getAttribute === "function") return String(el.getAttribute(name) || "");
    return String(el[name] || "");
  }

  var LANGUAGE_LABELS = {
    js: "JavaScript", javascript: "JavaScript", jsx: "JavaScript JSX",
    ts: "TypeScript", typescript: "TypeScript", tsx: "TypeScript TSX",
    py: "Python", python: "Python", rb: "Ruby", php: "PHP",
    java: "Java", c: "C", cpp: "C++", cs: "C#", go: "Go",
    rs: "Rust", swift: "Swift", kt: "Kotlin", scala: "Scala",
    sql: "SQL", json: "JSON", yaml: "YAML", yml: "YAML",
    xml: "XML", html: "HTML", css: "CSS", scss: "SCSS",
    bash: "Bash", sh: "Shell", shell: "Shell", powershell: "PowerShell",
    md: "Markdown", markdown: "Markdown", txt: "Text", text: "Text",
    dockerfile: "Dockerfile", env: "Configuration", ini: "Configuration",
    diff: "Diff", graphql: "GraphQL"
  };

  function languageLabel(lang) {
    var key = String(lang || "").toLowerCase();
    return LANGUAGE_LABELS[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "Code");
  }

  function registerCopy(text) {
    var id = "vmcopy" + (++copySeq);
    window.__VM_RICH_COPY_TEXT[id] = String(text == null ? "" : text);
    return id;
  }

  function copyCardHtml(title, codeHtml, copyText, lang, extraClass) {
    var id = registerCopy(copyText);
    var label = escapeHtml(title || languageLabel(lang) || "Code");
    var languageClass = lang ? " language-" + escapeHtml(String(lang).toLowerCase()) : "";
    return '<div class="vm-code-card ' + (extraClass || "") + '" data-vm-card="code">' +
      '<div class="vm-code-card-head">' +
        '<span class="vm-code-card-label">' + label + '</span>' +
        '<button type="button" class="vm-code-card-copy" data-copy-id="' + escapeHtml(id) + '" aria-label="Copy ' + label + '">⧉ Copy</button>' +
      '</div>' +
      '<pre class="vm-code-card-body"><code class="hljs' + languageClass + '">' + codeHtml + '</code></pre>' +
    '</div>';
  }

  function copyBoxHtml(body) {
    var raw = String(body || "");
    var split = raw.indexOf("|");
    var title = split >= 0 ? raw.slice(0, split).trim() : "Copy box";
    var content = split >= 0 ? raw.slice(split + 1) : raw;
    content = content.replace(/^\s*\n|\n\s*$/g, "");
    return copyCardHtml(title || "Copy box", escapeHtml(content), content, "", "vm-copy-card");
  }

  var BADGE_COLORS = {
    blue: "blue", cyan: "blue",
    green: "green", emerald: "green", success: "green",
    purple: "purple", violet: "purple",
    amber: "amber", yellow: "amber", warning: "amber",
    gray: "gray", grey: "gray", neutral: "gray"
  };

  function badgeHtml(body) {
    var parts = String(body || "").split("|");
    var label = escapeHtml((parts[0] || "").trim());
    var color = BADGE_COLORS[String(parts[1] || "blue").trim().toLowerCase()] || "gray";
    return '<span class="vm-badge vm-badge-' + color + '">' + label + '</span>';
  }

  function fileCardHtml(body) {
    var parts = String(body || "").split("|").map(function (p) { return String(p || "").trim(); });
    var title = parts[0] || "Download";
    var format = parts[1] || "FILE";
    var size = parts[2] || "";
    var url = safeUrl(parts.slice(3).join("|"));
    var meta = escapeHtml(format.toUpperCase() + (size ? " • " + size : ""));
    var icon = escapeHtml(format.slice(0, 4).toUpperCase() || "FILE");
    var button = url
      ? '<button type="button" class="vm-file-download vm-icon-download" title="Download" aria-label="Download" data-download-url="' + escapeHtml(url) + '" data-download-name="' + escapeHtml(title + "." + format.toLowerCase()) + '">' + downloadIconSvg() + '</button>'
      : '<button type="button" class="vm-file-download vm-icon-download" title="Download unavailable" aria-label="Download unavailable" disabled>' + downloadIconSvg() + '</button>';
    return '<div class="vm-file-card" data-vm-card="file">' +
      '<div class="vm-file-icon">' + icon + '</div>' +
      '<div class="vm-file-body">' +
        '<div class="vm-file-title">' + escapeHtml(title) + '</div>' +
        '<div class="vm-file-meta">' + meta + '</div>' +
      '</div>' + button +
    '</div>';
  }

  function imageCardHtml(body) {
    var parts = String(body || "").split("|").map(function (part) { return String(part || "").trim(); });
    var url = safeUrl(parts[0]);
    if (!url) return "";
    var alt = parts[1] || "Generated image";
    var fileName = parts[2] || "valleymind_image.png";
    return '<div class="vm-image-card vm-chat-media-card" data-vm-card="image" style="position:relative;display:inline-block;max-width:100%;margin:8px 0;border-radius:14px;overflow:hidden;border:1px solid rgba(148,163,184,0.28);background:#f8fafc;">' +
      '<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(alt) + '" loading="lazy" style="display:block;max-width:100%;width:100%;height:auto;">' +
      '<button type="button" class="vm-file-download vm-icon-download vm-media-download" title="Download" aria-label="Download" ' +
        'data-download-url="' + escapeHtml(url) + '" data-download-name="' + escapeHtml(fileName) + '" ' +
        'style="position:absolute;right:10px;bottom:10px;width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:50%;border:1px solid rgba(255,255,255,0.45);background:rgba(15,23,42,0.72);color:#fff;cursor:pointer;">' +
        downloadIconSvg() +
      '</button>' +
    '</div>';
  }

  function thumbGridHtml(body) {
    var items = String(body || "").split(/;|\r?\n/).map(function (item) { return item.trim(); }).filter(Boolean);
    if (!items.length) return "";
    var cards = items.map(function (item) {
      var parts = item.split("|").map(function (part) { return String(part || "").trim(); });
      var title = parts[0] || "Item";
      var subtitle = parts[1] || "";
      var icon = parts[2] || title.charAt(0).toUpperCase();
      var iconUrl = safeUrl(icon);
      var iconHtml = iconUrl
        ? '<img src="' + escapeHtml(iconUrl) + '" alt="">'
        : '<span>' + escapeHtml(icon) + '</span>';
      return '<div class="vm-thumb-card">' +
        '<div class="vm-thumb-icon">' + iconHtml + '</div>' +
        '<div class="vm-thumb-title">' + escapeHtml(title) + '</div>' +
        (subtitle ? '<div class="vm-thumb-subtitle">' + escapeHtml(subtitle) + '</div>' : '') +
      '</div>';
    }).join("");
    return '<div class="vm-thumb-grid">' + cards + '</div>';
  }

  function decorateFencedCode(html) {
    return String(html || "").replace(
      /<pre\b([^>]*)>\s*<code\b([^>]*)>([\s\S]*?)<\/code>\s*<\/pre>/gi,
      function (match, preAttrs, codeAttrs, codeHtml) {
        if (/vm-code-card-body/i.test(preAttrs)) return match;
        var langMatch = /language-([A-Za-z0-9_+#-]+)/i.exec(codeAttrs || "");
        var lang = langMatch ? langMatch[1] : "";
        return copyCardHtml(languageLabel(lang), codeHtml, decodeHtml(codeHtml), lang, "vm-fenced-code");
      }
    );
  }

  var originalRender = renderer.render;
  renderer.render = function (text, sourceMetadata) {
    if (text == null) return "";
    var store = [];
    var prepared = String(text);

    prepared = prepared.replace(/\[\[IMAGE_CARD:\s*([\s\S]*?)\]\]/gi, function (match, body) {
      var card = imageCardHtml(body);
      if (!card) return "";
      store.push(card);
      return tokenFor(store.length - 1);
    });
    prepared = prepared.replace(/\[\[FILE_CARD:\s*([\s\S]*?)\]\]/gi, function (match, body) {
      store.push(fileCardHtml(body));
      return tokenFor(store.length - 1);
    });
    prepared = prepared.replace(/\[\[COPY_BOX:\s*([\s\S]*?)\]\]/gi, function (match, body) {
      store.push(copyBoxHtml(body));
      return tokenFor(store.length - 1);
    });
    prepared = prepared.replace(/\[\[THUMB_GRID:\s*([\s\S]*?)\]\]/gi, function (match, body) {
      var grid = thumbGridHtml(body);
      if (!grid) return "";
      store.push(grid);
      return tokenFor(store.length - 1);
    });
    prepared = prepared.replace(/\[\[BADGE:\s*([\s\S]*?)\]\]/gi, function (match, body) {
      store.push(badgeHtml(body));
      return tokenFor(store.length - 1);
    });

    var html = originalRender ? originalRender.call(renderer, prepared, sourceMetadata) : escapeHtml(prepared);
    html = html.replace(
      new RegExp("<p>\\s*(" + TOKEN_START + "VM\\d+" + TOKEN_END + ")\\s*</p>", "g"),
      function (match, token) { return token; }
    );
    for (var i = 0; i < store.length; i++) {
      html = html.split(tokenFor(i)).join(store[i]);
    }
    return decorateFencedCode(html);
  };

  // ── Clipboard with Android WebView fallback ────────────────────────────
  function legacyCopy(text) {
    try {
      if (typeof document === "undefined" || !document.createElement || !document.body) return false;
      var area = document.createElement("textarea");
      area.value = String(text || "");
      area.setAttribute("readonly", "");
      if (area.style) {
        area.style.position = "fixed";
        area.style.top = "0";
        area.style.left = "0";
        area.style.opacity = "0";
      }
      document.body.appendChild(area);
      if (typeof area.select === "function") area.select();
      if (area.setSelectionRange) area.setSelectionRange(0, area.value.length);
      var ok = !!(document.execCommand && document.execCommand("copy"));
      if (area.parentNode && area.parentNode.removeChild) area.parentNode.removeChild(area);
      return ok;
    } catch (e) {
      return false;
    }
  }

  function copyText(text) {
    var value = String(text == null ? "" : text);
    var bridge = window.vmCompanionAndroid || window.Android || null;
    if (bridge && typeof bridge.copyText === "function") {
      try { bridge.copyText(value); return Promise.resolve(true); } catch (e) {}
    }
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return Promise.resolve()
        .then(function () { return navigator.clipboard.writeText(value); })
        .then(function () { return true; })
        .catch(function () { return legacyCopy(value); });
    }
    return Promise.resolve(legacyCopy(value));
  }

  // ── Download: native bridge if present, blob anchor otherwise ─────────
  function nativeDownload(url, filename) {
    var bridges = [window.vmCompanionAndroid, window.Android, window.VmAndroidBridge];
    for (var i = 0; i < bridges.length; i++) {
      var bridge = bridges[i];
      if (!bridge) continue;
      var methods = ["downloadFile", "downloadUrl", "download"];
      for (var j = 0; j < methods.length; j++) {
        if (typeof bridge[methods[j]] !== "function") continue;
        try {
          var result = methods[j] === "downloadUrl"
            ? bridge[methods[j]](url)
            : bridge[methods[j]](url, filename);
          if (result !== false) return true;
        } catch (e) {}
      }
    }
    return false;
  }

  function downloadFile(url, filename) {
    var safe = safeUrl(url);
    if (!safe) return Promise.resolve(false);
    var name = String(filename || "valleymind-download").replace(/[\\\/:*?"<>|]/g, "-");
    if (nativeDownload(safe, name)) return Promise.resolve(true);

    if (typeof window.fetch === "function") {
      return window.fetch(safe, { credentials: "same-origin" })
        .then(function (response) {
          if (!response || !response.ok) throw new Error("download failed");
          if (typeof response.blob !== "function") throw new Error("blob unsupported");
          return response.blob();
        })
        .then(function (blob) {
          if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
            window.open(safe, "_blank");
            return true;
          }
          var objectUrl = URL.createObjectURL(blob);
          var anchor = document.createElement("a");
          anchor.href = objectUrl;
          anchor.download = name;
          anchor.rel = "noopener";
          if (anchor.style) anchor.style.display = "none";
          var host = document.body || document.documentElement;
          if (host && host.appendChild) host.appendChild(anchor);
          if (typeof anchor.click === "function") anchor.click();
          if (host && host.removeChild && anchor.parentNode === host) host.removeChild(anchor);
          if (typeof URL.revokeObjectURL === "function") window.setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 30000);
          return true;
        })
        .catch(function () {
          try { window.open(safe, "_blank"); } catch (e) {}
          return false;
        });
    }
    try { window.open(safe, "_blank"); } catch (e) {}
    return Promise.resolve(true);
  }

  function setAttr(el, name, value) {
    if (!el) return;
    if (typeof el.setAttribute === "function") el.setAttribute(name, value);
    else el[name] = value;
  }

  // Replace the icon with a circular spinner for the full download handoff plus
  // a short settle, then restore the original icon. This covers FILE_CARD
  // buttons and any generated image/media button carrying data-download-url.
  function downloadWithSpinner(button, url, name) {
    if (!button || button.__vmDownloadActive) return;
    var originalHtml = button.innerHTML;
    var originalPointerEvents = button.style ? button.style.pointerEvents : "";
    button.__vmDownloadActive = true;
    button.innerHTML = downloadSpinnerSvg();
    if (button.classList && typeof button.classList.add === "function") button.classList.add("vm-downloading");
    if (button.style) button.style.pointerEvents = "none";
    setAttr(button, "aria-busy", "true");

    var settle = new Promise(function (resolve) { setTimeout(resolve, 450); });
    function restore(result) {
      button.innerHTML = originalHtml;
      if (button.style) button.style.pointerEvents = originalPointerEvents;
      if (button.classList && typeof button.classList.remove === "function") button.classList.remove("vm-downloading");
      setAttr(button, "aria-busy", "false");
      button.__vmDownloadActive = false;
      return result;
    }
    Promise.resolve()
      .then(function () { return downloadFile(url, name); })
      .catch(function () { return false; })
      .then(function (result) { return settle.then(function () { return restore(result); }); },
            function () { return restore(false); });
  }

  function buttonFeedback(button, copied) {
    if (!button) return;
    button.textContent = copied ? "✓ Copied!" : "Copy failed";
    if (button.classList) button.classList.toggle("vm-copied", !!copied);
    window.setTimeout(function () {
      button.textContent = "⧉ Copy";
      if (button.classList) button.classList.remove("vm-copied");
    }, copied ? 1600 : 1000);
  }

  function bindActions(root) {
    if (!root || typeof root.querySelectorAll !== "function") return;
    var nodes = root.querySelectorAll("[data-copy-id],[data-download-url]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (!node || node.__vmRichActionBound) continue;
      var copyId = attr(node, "data-copy-id");
      var downloadUrl = attr(node, "data-download-url");
      if (copyId) {
        node.__vmRichActionBound = true;
        node.onclick = function (event) {
          if (event && typeof event.stopPropagation === "function") event.stopPropagation();
          var id = attr(this, "data-copy-id");
          var text = (window.__VM_RICH_COPY_TEXT || {})[id] || this.textContent || "";
          var button = this;
          copyText(text).then(function (ok) { buttonFeedback(button, ok); });
        };
      } else if (downloadUrl) {
        node.__vmRichActionBound = true;
        node.onclick = function (event) {
          if (event && typeof event.stopPropagation === "function") event.stopPropagation();
          if (this.__vmDownloadActive) return;
          downloadWithSpinner(this, attr(this, "data-download-url"), attr(this, "data-download-name"));
        };
      }
    }
  }

  function highlightCode(root) {
    if (!window.hljs || !root || typeof root.querySelectorAll !== "function") return;
    var blocks = root.querySelectorAll("pre code");
    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];
      if (!block || (block.classList && block.classList.contains("vm-hljs-done"))) continue;
      try {
        window.hljs.highlightElement(block);
        if (block.classList) block.classList.add("vm-hljs-done");
      } catch (e) {}
    }
  }

  function formatBubble(bub) {
    if (!bub || typeof bub.querySelectorAll !== "function") return;
    try { highlightCode(bub); } catch (e) {}
    try { bindActions(bub); } catch (e) {}
  }

  var originalFormat = window.VMFormatRichBubble;
  window.VMFormatRichBubble = function (bub) {
    if (originalFormat) {
      try { originalFormat(bub); } catch (e) {}
    }
    formatBubble(bub);
  };

  // Delegated clicks cover bubbles hydrated later (history replay, streaming).
  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("click", function (event) {
      var node = event && event.target;
      while (node && node !== document) {
        var copyId = attr(node, "data-copy-id");
        var downloadUrl = attr(node, "data-download-url");
        if (copyId || downloadUrl) {
          if (!node.__vmRichActionBound) {
            bindActions(node.parentNode || document);
            // The document listener runs after the button's own onclick in the
            // bubble path, so invoke the freshly bound handler on first click.
            if (typeof node.onclick === "function") node.onclick(event);
          }
          return;
        }
        node = node.parentNode;
      }
    }, false);
  }

  var selector = window.VMPresentationSelector;
  if (selector && typeof selector.select === "function") {
    var originalSelect = selector.select;
    selector.select = function (text) {
      if (text && /\[\[(?:IMAGE_CARD|FILE_CARD|COPY_BOX|BADGE|THUMB_GRID):/i.test(String(text))) return "rich";
      return originalSelect.call(selector, text);
    };
  }

  // Ordinary prose must never become a bordered monospace copy box. The
  // legacy VMAutoCopyReady heuristic wraps any short question/reply, so it is
  // disabled here (after index.html has declared it). Copy UI remains limited
  // to explicit fenced code and [[COPY_BOX: ...]] blocks above.
  function disableAutomaticCopyBoxes() {
    try {
      window.VMLooksLikeCopyableMessage = function () { return false; };
      window.VMAutoCopyReady = function () { return false; };
    } catch (e) {}
  }
  if (typeof document !== "undefined") {
    if (document.readyState === "loading" && typeof document.addEventListener === "function") {
      document.addEventListener("DOMContentLoaded", disableAutomaticCopyBoxes, false);
    } else {
      disableAutomaticCopyBoxes();
    }
    if (typeof window.addEventListener === "function") {
      window.addEventListener("load", disableAutomaticCopyBoxes, false);
    }
  } else {
    disableAutomaticCopyBoxes();
  }

  window.VMCopyText = copyText;
  window.VMDownloadFile = downloadFile;
  window.VMRichRenderAddons = {
    render: renderer.render,
    copyText: copyText,
    downloadFile: downloadFile,
    formatBubble: formatBubble,
    downloadWithSpinner: downloadWithSpinner,
    downloadIconSvg: downloadIconSvg,
    downloadSpinnerSvg: downloadSpinnerSvg,
    languageLabel: languageLabel,
    badgeHtml: badgeHtml,
    copyBoxHtml: copyBoxHtml,
    imageCardHtml: imageCardHtml,
    fileCardHtml: fileCardHtml,
    thumbGridHtml: thumbGridHtml
  };
})();
