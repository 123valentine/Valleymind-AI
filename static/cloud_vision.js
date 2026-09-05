/**
 * VMCloudVision — explicit screen/context capture for the VMware Cloud companion.
 *
 * Privacy contract:
 *   - Capture is NEVER always-on or silent. It only starts through an explicit
 *     user action ("Let Cloud see your screen") that triggers a browser
 *     permission prompt.
 *   - Frames are extracted on a throttled timer (never full-resolution video,
 *     never continuous full-res frames) as downscaled JPEG data URIs.
 *   - No capture happens before activation. A clear "sharing" indicator is
 *     exposed via onStateChange and the companion UI.
 *   - Frames are NEVER stored in long-term memory or sent anywhere other than
 *     the existing ValleyMind brain (the cloud chat route passes them
 *     transiently with persist_image_data=False).
 *   - No computer control: no clicks, typing, scrolling, browser automation
 *     or desktop control is performed here or exposed to callers.
 *
 * States: off -> requesting -> active | stopped | denied | unsupported
 *         active -> stopped (manual or stream end)
 */
(function () {
  "use strict";

  var VISION_STATES = ["off", "requesting", "active", "stopped", "denied", "unsupported"];

  var state = "off";
  var stream = null;
  var videoEl = null;
  var canvasEl = null;
  var lastFrame = "";
  var rafHandle = 0;
  var lastCaptureAt = 0;
  var onStateChange = null;

  var MIN_FRAME_GAP_MS = 2500;
  var DEFAULT_FRAME_WIDTH = 640;
  var DEFAULT_FRAME_HEIGHT = 480;
  var MAX_ATTACH_BYTES = 4 * 1024 * 1024;

  function getState() {
    return state;
  }

  function setState(next) {
    if (VISION_STATES.indexOf(next) === -1) return;
    state = next;
    if (typeof onStateChange === "function") {
      try { onStateChange(state); } catch (err) { /* non-fatal */ }
    }
  }

  function supported() {
    return !!(
      typeof navigator !== "undefined" &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getDisplayMedia === "function"
    );
  }

  function ensureEls() {
    if (!videoEl) {
      videoEl = document.createElement("video");
      videoEl.setAttribute("autoplay", "");
      videoEl.setAttribute("playsinline", "");
      videoEl.style.display = "none";
    }
    if (!canvasEl) {
      canvasEl = document.createElement("canvas");
      canvasEl.width = DEFAULT_FRAME_WIDTH;
      canvasEl.height = DEFAULT_FRAME_HEIGHT;
    }
  }

  function stopTracks() {
    if (stream && stream.getTracks) {
      stream.getTracks().forEach(function (track) {
        try { track.stop(); } catch (err) { /* noop */ }
      });
    }
  }

  function clearVideo() {
    if (videoEl) {
      try { videoEl.srcObject = null; } catch (err) { /* noop */ }
    }
  }

  function onStreamEnded() {
    stopTracks();
    clearVideo();
    setState("stopped");
  }

  function start() {
    if (state === "requesting" || state === "active") return true;
    if (!supported()) {
      setState("unsupported");
      return false;
    }
    stopTracks();
    lastFrame = "";
    setState("requesting");
    try {
      var request = navigator.mediaDevices.getDisplayMedia({
        video: { cursor: "motion" },
        audio: false,
      });
      if (request && typeof request.then === "function") {
        request.then(function (media) {
          stream = media;
          ensureEls();
          videoEl.srcObject = media;
          stream.getVideoTracks()[0] &&
            stream.getVideoTracks()[0].addEventListener("ended", onStreamEnded);
          setState("active");
        }, function () {
          setState("denied");
        });
        return true;
      }
      stream = request;
      ensureEls();
      videoEl.srcObject = request;
      setState(request ? "active" : "unsupported");
      return true;
    } catch (err) {
      setState("unsupported");
      return false;
    }
  }

  function stop() {
    stopTracks();
    clearVideo();
    lastFrame = "";
    stream = null;
    setState("stopped");
  }

  function capture(maxWidth) {
    if (state !== "active" || !videoEl) return "";
    var now = Date.now();
    if (now - lastCaptureAt < MIN_FRAME_GAP_MS) return lastFrame;
    lastCaptureAt = now;
    try {
      var vw = videoEl.videoWidth || 0;
      var vh = videoEl.videoHeight || 0;
      if (!vw || !vh) return lastFrame;
      var targetW = maxWidth && maxWidth > 0 ? Math.min(maxWidth, vw) : Math.min(DEFAULT_FRAME_WIDTH, vw);
      var targetH = Math.round((vh / vw) * targetW);
      canvasEl.width = Math.round(targetW);
      canvasEl.height = Math.round(targetH);
      var ctx = canvasEl.getContext("2d");
      if (!ctx) return lastFrame;
      ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
      var dataUrl = canvasEl.toDataURL("image/jpeg", 0.5);
      if (dataUrl && dataUrl.length > 16 && dataUrl.length <= MAX_ATTACH_BYTES) {
        lastFrame = dataUrl;
      }
    } catch (err) {
      return lastFrame;
    }
    return lastFrame;
  }

  function getLastFrame() {
    return lastFrame;
  }

  function init(opts) {
    opts = opts || {};
    if (typeof opts.onStateChange === "function") {
      onStateChange = opts.onStateChange;
    }
  }

  function destroy() {
    stop();
    if (videoEl && videoEl.parentNode) videoEl.parentNode.removeChild(videoEl);
    if (canvasEl && canvasEl.parentNode) canvasEl.parentNode.removeChild(canvasEl);
    videoEl = null;
    canvasEl = null;
    onStateChange = null;
  }

  window.VMCloudVision = {
    STATES: VISION_STATES.slice(),
    getState: getState,
    supported: supported,
    start: start,
    stop: stop,
    capture: capture,
    getLastFrame: getLastFrame,
    onStateChange: init,
    init: init,
    destroy: destroy,
  };
})();