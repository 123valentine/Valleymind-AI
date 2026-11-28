/* ValleyMind — conversational video editing bridge
   -----------------------------------------------------------
   There is no manual editing workspace any more. This file powers the small
   chat-side Studio Asset Stickers picker and owns the pending video-edit state
   for the main composer. It turns the user's chat answers into the same
   /api/editing/run + /api/studio/job/<id> requests the real FFmpeg pipeline has
   always used, then renders the finished MP4 as an icon-only card in chat.

   No sticker packs or heavy media are bundled: built-ins come from the asset
   routes, and user uploads are routed through the editing asset library.
*/
(function () {
  "use strict";

  var CACHE_BUST = "?v=7";
  var MAX_POLLS = 240;
  var POLL_MS = 1500;

  var PLACEMENTS = [
    { value: "tl", label: "Top Left" },
    { value: "tr", label: "Upper Right" },
    { value: "bl", label: "Lower Left" },
    { value: "br", label: "Lower Right" },
    { value: "center", label: "Center" }
  ];

  var state = {
    video: null,
    fileName: "",
    media: [],
    instruction: "",
    awaiting: false,
    sticker: null,
    placement: "br",
    stickers: [],
    uploaded: [],
    jobId: "",
    lastVideoUrl: "",
    pickerLoaded: false
  };

  function win() {
    return typeof window !== "undefined" ? window : {};
  }

  function doc() {
    return typeof document !== "undefined" ? document : null;
  }

  function byId(id) {
    var d = doc();
    return d && typeof d.getElementById === "function" ? d.getElementById(id) : null;
  }

  function esc(value) {
    var fn = win().VMEscapeHtmlAttr;
    if (typeof fn === "function") return fn(value);
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function downloadIcon() {
    var fn = win().VMDownloadIconSvg;
    return typeof fn === "function" ? fn() : "";
  }

  function regenerateIcon() {
    var fn = win().VMRegenerateIconSvg;
    return typeof fn === "function" ? fn() : "";
  }

  function spinnerIcon() {
    var fn = win().VMDownloadSpinnerSvg;
    return typeof fn === "function" ? fn() : "";
  }

  function sharedPlacements() {
    var list = win().VM_MASSIVE_STICKER_PLACEMENTS;
    return Array.isArray(list) && list.length ? list : PLACEMENTS;
  }

  function normalizePlacement(value) {
    var raw = String(value || "").trim().toLowerCase().replace(/[\s_\-]+/g, " ");
    var map = {
      "tl": "tl", "top left": "tl", "upper left": "tl", "top-left": "tl",
      "tr": "tr", "top right": "tr", "upper right": "tr", "top-right": "tr",
      "bl": "bl", "bottom left": "bl", "lower left": "bl", "bottom-left": "bl",
      "br": "br", "bottom right": "br", "lower right": "br", "bottom-right": "br",
      "center": "center", "centre": "center", "middle": "center"
    };
    return map[raw] || "br";
  }

  function authOptions(options) {
    var out = { credentials: "include", headers: {} };
    var make = win().authHeaders;
    var headers = typeof make === "function" ? make() : {};
    if (headers && typeof headers === "object") {
      Object.keys(headers).forEach(function (k) { out.headers[k] = headers[k]; });
    }
    if (options) {
      Object.keys(options).forEach(function (k) {
        if (k === "headers") {
          var extra = options[k] || {};
          Object.keys(extra).forEach(function (h) { out.headers[h] = extra[h]; });
        } else {
          out[k] = options[k];
        }
      });
    }
    return out;
  }

  function requestJson(path, options) {
    var w = win();
    var fn = typeof w.apiFetch === "function" ? w.apiFetch : w.fetch;
    if (typeof fn !== "function") return Promise.reject(new Error("Network unavailable."));
    return Promise.resolve(fn(path, authOptions(options || {}))).then(function (resp) {
      if (!resp || typeof resp.json !== "function") {
        throw new Error("Invalid server response.");
      }
      return resp.json().then(function (data) {
        if (resp.ok === false && (!data || data.status === "error")) {
          throw new Error((data && data.message) || "Request failed.");
        }
        return data || {};
      });
    });
  }

  function cleanName(name) {
    return String(name || "video").replace(/[\[\]\r\n]/g, " ").trim() || "video";
  }

  function marker() {
    return state.fileName ? "[VIDEO_ATTACHED: " + cleanName(state.fileName) + "]" : "";
  }

  function isConfirmation(text) {
    return /\b(?:edit|make)\s+it\s+now\b|\bmake\s+it\s+viral\s+now\b|^\s*(?:yes|yeah|yep|yup|sure|ok|okay|go\s+ahead|proceed|do\s+it|that\s+works|looks\s+good)\b/i.test(String(text || ""));
  }

  function isDetailed(text) {
    var brief = String(text || "").replace(/\[VIDEO_ATTACHED:[^\]]*\]/gi, " ").trim();
    var style = /\b(?:style|trend|viral|hype|cinematic|funny|meme|comedy|aesthetic|reel|shorts|tiktok|phonk|energetic|dramatic|sports)\b/i.test(brief);
    var stickers = /\b(?:sticker|emoji|gif|overlay|reference|top\s+left|upper\s+right|lower\s+left|lower\s+right|center)\b/i.test(brief);
    var camera = /\b(?:zoom|punch|slow[\s-]?mo|slow[\s-]?motion|angle|camera|captions?|subtitles?|text|cutaway|reaction|beat|transition|widescreen|vertical|16:9|9:16)\b/i.test(brief);
    return (style ? 1 : 0) + (stickers ? 1 : 0) + (camera ? 1 : 0) >= 2;
  }

  function mergeInstruction(previous, answer) {
    var a = String(previous || "").trim();
    var b = String(answer || "").trim();
    if (!a) return b;
    if (!b) return a;
    if (a.toLowerCase().indexOf(b.toLowerCase()) >= 0) return a;
    return a + "\nAdditional direction from the user: " + b;
  }

  /* Capture the current composer attachments before send() clears them. The
     first vague turn only stores state; later answer turns (or an explicit
     "edit it now" / "make it viral now") arm the actual render. */
  function prepare(text, attachments) {
    var atts = Array.isArray(attachments) ? attachments : [];
    var video = null;
    for (var i = 0; i < atts.length; i++) {
      var a = atts[i];
      if (a && a.kind === "video" && a.file && a.status !== "failed" && a.status !== "removed") {
        video = a;
        break;
      }
    }

    var wasAwaiting = state.awaiting;
    var said = String(text || "").trim();
    var confirmed = isConfirmation(said);
    var shouldRun = false;

    if (video) {
      state.video = video.file;
      state.fileName = cleanName(video.name);
      state.instruction = wasAwaiting ? mergeInstruction(state.instruction, said) : said;
      state.awaiting = true;
      shouldRun = confirmed || isDetailed(said);
    } else if (state.awaiting && said && !/\?$/.test(said)) {
      if (!confirmed) state.instruction = mergeInstruction(state.instruction, said);
      shouldRun = true;
    }

    if (atts.length) {
      var media = state.media.slice();
      for (var j = 0; j < atts.length; j++) {
        var m = atts[j];
        if (m && m.file && (m.kind === "image" || m.kind === "audio") &&
            m.status !== "failed" && m.status !== "removed") {
          if (!media.some(function (existing) { return existing.file === m.file; })) {
            media.push({ file: m.file, name: cleanName(m.name), kind: m.kind });
          }
        }
      }
      state.media = media;
    }

    return {
      active: !!state.video,
      fileName: state.fileName,
      marker: state.video ? marker() : "",
      shouldRun: !!state.video && shouldRun,
      instruction: state.instruction
    };
  }

  function setPlacement(value) {
    state.placement = normalizePlacement(value);
    renderPicker();
    return state.placement;
  }

  function selectSticker(index) {
    var item = state.stickers[Number(index) || 0];
    if (!item || !item.url) return null;
    state.sticker = { name: item.name || "sticker", url: item.url, kind: item.kind || "sticker" };
    renderPicker();
    return state.sticker;
  }

  function pickerItemsHtml() {
    var items = state.stickers.slice(0, 24);
    if (!items.length) {
      return '<div class="vm-sticker-picker-empty">Your uploaded stickers and the built-in set will appear here.</div>';
    }
    return items.map(function (item, index) {
      var selected = state.sticker && state.sticker.url === item.url;
      return '<button type="button" class="vm-sticker-picker-item' + (selected ? ' selected' : '') +
        '" onclick="VMChatEdit.selectSticker(' + index + ')" title="' + esc(item.name) + '">' +
        '<img src="' + esc(item.url) + '" alt="' + esc(item.name) + '">' +
        '<span>' + esc(item.name) + '</span></button>';
    }).join("");
  }

  function renderPicker() {
    var picker = byId("vmStickerPicker");
    if (!picker) return;
    var placement = sharedPlacements().map(function (p) {
      var value = normalizePlacement(p.value);
      var active = value === state.placement;
      return '<button type="button" class="vm-sticker-place' + (active ? ' active' : '') +
        '" onclick="VMChatEdit.setPlacement(\'' + value + '\')">' + esc(p.label) + '</button>';
    }).join("");
    var chosen = state.sticker ? esc(state.sticker.name) + ' · ' + esc(state.placement) : 'No sticker selected';
    picker.innerHTML =
      '<div class="vm-sticker-picker-head"><strong>Studio Asset Stickers</strong>' +
      '<button type="button" class="vm-sticker-picker-close" onclick="VMChatEdit.closeStickerPicker()" aria-label="Close">×</button></div>' +
      '<div class="vm-sticker-picker-section"><span>Placement</span><div class="vm-sticker-places">' + placement + '</div></div>' +
      '<div class="vm-sticker-picker-section"><span>Sticker</span><div class="vm-sticker-picker-grid">' + pickerItemsHtml() + '</div></div>' +
      '<div class="vm-sticker-picker-foot"><span>' + chosen + '</span>' +
      '<button type="button" onclick="document.getElementById(\'chatStickerInput\').click()">+ Custom</button></div>';
  }

  function initPicker() {
    var d = doc();
    if (!d || byId("vmStickerPicker")) return;
    var picker = d.createElement("div");
    picker.id = "vmStickerPicker";
    picker.className = "vm-sticker-picker";
    picker.style.display = "none";
    picker.innerHTML = '<div class="vm-sticker-picker-head"><strong>Studio Asset Stickers</strong></div>';
    (d.body || d.documentElement).appendChild(picker);
  }

  function toggleStickerPicker() {
    initPicker();
    var picker = byId("vmStickerPicker");
    if (!picker) return;
    var open = picker.style && picker.style.display === "block";
    picker.style.display = open ? "none" : "block";
    if (!open) {
      renderPicker();
      if (!state.pickerLoaded) refreshStickers();
    }
  }

  function closeStickerPicker() {
    var picker = byId("vmStickerPicker");
    if (picker) picker.style.display = "none";
  }

  function refreshStickers() {
    state.pickerLoaded = true;
    var builtIn = requestJson("/api/editing/stickers?type=stickers" + CACHE_BUST + "&v=stickers")
      .then(function (data) { return data.stickers || []; })
      .catch(function () { return []; });
    var gifs = requestJson("/api/editing/stickers?type=gifs" + CACHE_BUST + "&v=gifs")
      .then(function (data) { return data.stickers || []; })
      .catch(function () { return []; });
    var uploaded = requestJson("/api/editing/assets?category=sticker" + CACHE_BUST)
      .then(function (data) {
        state.uploaded = (data.assets || []).map(function (a) {
          return { name: a.name || a.title || "Uploaded sticker",
                   url: a.local_path || a.url || "", kind: "sticker" };
        }).filter(function (a) { return !!a.url; });
        return state.uploaded;
      })
      .catch(function () { return []; });
    return Promise.all([builtIn, gifs, uploaded]).then(function (lists) {
      var seen = {};
      state.stickers = [];
      [].concat(lists[0], lists[1], state.uploaded).forEach(function (item) {
        if (!item || !item.url || seen[item.url]) return;
        seen[item.url] = true;
        state.stickers.push({ name: item.name || "sticker", url: item.url,
                              kind: item.kind || "sticker" });
      });
      renderPicker();
      return state.stickers;
    });
  }

  function uploadStickers(files) {
    if (!files || !files.length) return Promise.resolve([]);
    var fd = new FormData();
    fd.append("category", "sticker");
    for (var i = 0; i < files.length; i++) fd.append("files", files[i], files[i].name);
    return requestJson("/api/editing/assets", { method: "POST", body: fd }).then(function (data) {
      var saved = (data.assets || []).map(function (a) {
        return { name: a.name || "Uploaded sticker", url: a.local_path || a.url || "",
                 kind: "sticker" };
      }).filter(function (a) { return !!a.url; });
      saved.forEach(function (item) {
        var exists = state.stickers.some(function (s) { return s.url === item.url; });
        if (!exists) state.stickers.push(item);
      });
      state.uploaded = saved.concat(state.uploaded);
      if (saved.length) state.sticker = saved[0];
      state.pickerLoaded = true;
      renderPicker();
      return saved;
    });
  }

  function buildFormData() {
    if (!state.video) throw new Error("Attach a video first.");
    var fd = new FormData();
    fd.append("video", state.video, state.fileName || "video.mp4");
    state.media.forEach(function (m) { fd.append("media", m.file, m.name || "asset"); });
    fd.append("instruction", state.instruction ||
      "Make this viral with bold word-synced captions, a zoom punch and a reaction cutaway");
    if (state.sticker && state.sticker.url) {
      fd.append("sticker_url", state.sticker.url);
      fd.append("sticker_name", state.sticker.name || "sticker");
      fd.append("sticker_pos", state.placement);
    }
    var brief = state.instruction || "";
    var aspect = /\b(?:widescreen|wide|landscape|16:9)\b/i.test(brief) ? "16:9"
      : (/\b(?:vertical|9:16)\b/i.test(brief) ? "9:16" : "");
    if (aspect) fd.append("manual", JSON.stringify({ canvas: { aspect: aspect } }));
    return fd;
  }

  function resultCardHtml(url, job) {
    var name = cleanName(state.fileName);
    var label = job && job.status === "done" ? "Edited video ready" : "Edited video";
    return '<div class="vm-edit-card-head"><span class="vm-edit-card-dot"></span>' +
      '<strong>' + label + '</strong><span class="vm-edit-card-name">' + esc(name) + '</span></div>' +
      '<video class="vm-edit-card-video" controls playsinline preload="metadata" src="' + esc(url) + '"></video>' +
      '<div class="vm-edit-card-actions">' +
      '<button type="button" class="vm-edit-icon-btn" aria-label="Download edited video" title="Download" onclick="VMChatEdit.download(this)">' + downloadIcon() + '</button>' +
      '<button type="button" class="vm-edit-icon-btn" aria-label="Regenerate edited video" title="Regenerate" onclick="VMChatEdit.regenerate(this)">' + regenerateIcon() + '</button>' +
      '</div>';
  }

  function appendCard(html, className) {
    var d = doc();
    if (!d || typeof d.createElement !== "function") return null;
    var host = byId("messages") || d.body;
    if (!host || typeof host.appendChild !== "function") return null;
    var card = d.createElement("div");
    card.className = className || "vm-edit-result-card";
    card.innerHTML = html;
    host.appendChild(card);
    return card;
  }

  function progressCard(message) {
    var card = appendCard(
      '<div class="vm-edit-progress"><span class="vm-edit-progress-spinner">' + spinnerIcon() +
      '</span><span class="vm-edit-progress-copy">' + esc(message || "Starting your edit…") + '</span></div>',
      "vm-edit-result-card vm-edit-pending");
    return {
      node: card,
      progress: function (job) {
        if (!card) return;
        var stage = job && Array.isArray(job.edit_plan)
          ? job.edit_plan.filter(function (s) { return s && s.status === "done"; }).length : 0;
        var total = job && Array.isArray(job.edit_plan) ? job.edit_plan.length : 0;
        var copy = card.querySelector ? card.querySelector(".vm-edit-progress-copy") : null;
        if (copy) {
          copy.textContent = total
            ? "Editing your video… " + stage + "/" + total + " steps"
            : "Editing your video…";
        }
      },
      done: function (job) {
        if (!card) return;
        state.lastVideoUrl = job.final_video || "";
        card.className = "vm-edit-result-card";
        card.innerHTML = resultCardHtml(state.lastVideoUrl, job);
      },
      fail: function (message) {
        if (!card) return;
        card.className = "vm-edit-result-card vm-edit-failed";
        card.innerHTML = '<div class="vm-edit-progress-copy">Edit failed: ' + esc(message) + '</div>';
      }
    };
  }

  function poll(jobId, card) {
    return new Promise(function (resolve, reject) {
      var attempts = 0;
      function tick() {
        attempts += 1;
        if (attempts > MAX_POLLS) {
          reject(new Error("The edit is taking longer than expected."));
          return;
        }
        requestJson("/api/studio/job/" + encodeURIComponent(jobId))
          .then(function (data) {
            var job = data.job || data;
            if (job.final_video || job.status === "done") {
              card.done(job);
              resolve(job);
              return;
            }
            if (job.status === "failed" || job.status === "aborted" || job.status === "budget_capped") {
              reject(new Error(job.error || job.message || "The edit could not be completed."));
              return;
            }
            card.progress(job);
            setTimeout(tick, POLL_MS);
          })
          .catch(reject);
      }
      tick();
    });
  }

  function start() {
    if (!state.video) return Promise.reject(new Error("Attach a video to edit."));
    var card = progressCard("Starting your edit…");
    var fd;
    try { fd = buildFormData(); } catch (e) { card.fail(e.message); return Promise.reject(e); }
    return requestJson("/api/editing/run", { method: "POST", body: fd }).then(function (data) {
      var job = data.job || {};
      state.jobId = job.job_id || "";
      if (!state.jobId) throw new Error(data.message || "The edit could not be started.");
      card.progress(job);
      return poll(state.jobId, card);
    }).catch(function (e) {
      card.fail(e && e.message ? e.message : String(e));
      throw e;
    });
  }

  function withSpinner(button, action) {
    if (button && typeof win().VMWithDownloadSpinner === "function") {
      return win().VMWithDownloadSpinner(button, action);
    }
    return Promise.resolve(typeof action === "function" ? action() : null);
  }

  function download(button) {
    return withSpinner(button, function () {
      if (!state.lastVideoUrl) return false;
      var d = doc();
      if (!d || typeof d.createElement !== "function") {
        if (win().open) win().open(state.lastVideoUrl, "_blank");
        return true;
      }
      var link = d.createElement("a");
      link.href = state.lastVideoUrl;
      link.download = (cleanName(state.fileName).replace(/\.[^.]+$/, "") || "valleymind-edit") + ".mp4";
      (d.body || d.documentElement).appendChild(link);
      if (typeof link.click === "function") link.click();
      if (link.parentNode) link.parentNode.removeChild(link);
      return true;
    });
  }

  function regenerate(button) {
    return withSpinner(button, function () { return start(); });
  }

  function reset() {
    state.video = null;
    state.fileName = "";
    state.media = [];
    state.instruction = "";
    state.awaiting = false;
    state.sticker = null;
    state.placement = "br";
    state.jobId = "";
    state.lastVideoUrl = "";
  }

  function init() {
    initPicker();
    var d = doc();
    if (!d || typeof d.addEventListener !== "function") return;
    d.addEventListener("click", function (event) {
      var picker = byId("vmStickerPicker");
      var plus = byId("plusBtn");
      if (!picker || !event || !event.target) return;
      if (picker.style && picker.style.display !== "block") return;
      if (picker.contains && picker.contains(event.target)) return;
      if (plus && plus.contains && plus.contains(event.target)) return;
      closeStickerPicker();
    });
    var stickerInput = byId("chatStickerInput");
    if (stickerInput && typeof stickerInput.addEventListener === "function") {
      stickerInput.addEventListener("change", function (event) {
        uploadStickers(event.target.files);
        event.target.value = "";
      });
    }
  }

  var API = {
    VERSION: "chat-edit-7",
    PLACEMENTS: PLACEMENTS,
    state: state,
    init: init,
    prepare: prepare,
    marker: marker,
    isConfirmation: isConfirmation,
    isDetailed: isDetailed,
    normalizePlacement: normalizePlacement,
    setPlacement: setPlacement,
    selectSticker: selectSticker,
    refreshStickers: refreshStickers,
    uploadStickers: uploadStickers,
    toggleStickerPicker: toggleStickerPicker,
    openStickerPicker: toggleStickerPicker,
    closeStickerPicker: closeStickerPicker,
    renderPicker: renderPicker,
    buildFormData: buildFormData,
    resultCardHtml: resultCardHtml,
    start: start,
    poll: poll,
    download: download,
    regenerate: regenerate,
    reset: reset
  };

  win().VMChatEdit = API;
  // Backwards-compatible no-op for any old inline hook that still calls in.
  win().VMEditing = {
    launch: function () { if (typeof win().openStudio === "function") win().openStudio(); },
    hide: function () {},
    reset: reset,
    status: function () {},
    onShow: function () {}
  };

  if (typeof doc() !== "undefined") {
    if (doc().readyState === "loading") {
      doc().addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }
}());
