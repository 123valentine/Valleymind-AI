/* ValleyMind Cloud companion — layered vector rig for the CLOUD character.
   ────────────────────────────────────────────────────────────────────
   Cloud is a small, friendly, floating AI companion. This module builds a
   LAYERED SVG rig that draws the original cloud as separately addressable
   parts:

       body | leftEye | rightEye | leftEyebrow | rightEyebrow | nose | mouth |
       leftArm | rightArm | leftHand | rightHand | leftLeg | rightLeg | shadow

   Each part is an SVG <g> that the single centralized animation engine
   (static/cloud_anim.js) can translate/rotate/scale independently via CSS
   transforms: the body breathes and leans with gaze, eyes blink, pupils gaze,
   brows express, the nose twitches, the mouth smiles/talks, arms wave and
   point, hands waggle, and the little legs dangle as the cloud drifts.

   Design notes
   ────────────
   • The silhouette is ONE closed path (SILHOUETTE_D) shared by the rim pass,
     the gradient fill pass and the clip path, so those three passes can never
     disagree. It rolls gently across the top and scallops softly underneath —
     it reads as a single cloud mass, never as detached circles or "ears".
   • The outer rim is a thick round-joined stroke on a duplicate of that same
     path, so the silhouette stays legible on both light and dark UI without
     needing to grow circles.
   • Arms are long round-capped stroke wisps that taper naturally, with mitten
     hands grouped at the wrist so gestures read clearly.
   • There is no robot anywhere in this rig: no head panel, no face screen,
     no halo, no ears, no hover base, no emblem.

   static/cloud.png is the legacy flattened cloud image. It stays in the DOM
   ONLY as the no-JS / loading fallback before this rig mounts; the moment the
   rig takes over the fallback is hidden, so there is exactly ONE visible
   companion and no duplicate ever appears.

   Usage (internal):
       var rig = window.VMCloudRig.build(containerEl);
       rig.parts.leftEye  -> <g> DOM node
       rig.parts.mouth    -> <g> DOM node
       rig.root           -> container <div>
*/

(function () {
  "use strict";

  // Cloud palette: luminous cool-white crown, cool shaded underside, a soft
  // silhouette line, deep navy face ink and warm blush — not robot chrome.
  var COLORS = {
    cloudTop:  "#F4FBFF",
    cloudBase: "#BCE2F0",
    edge:      "#8CC6DC",
    ink:       "#16324F",
    inkSoft:   "#3A5F80",
    blush:     "#FFA9C4",
    limbHigh:  "#EAF8FF",
    limbLow:   "#A6D2E6",
    glint:     "#FFFFFF",
    shadow:    "rgba(22, 50, 79, 0.13)"
  };

  // Anchor / geometry constants in full canvas space (0..433 x 0..577).
  // Every value below is inside that viewBox; the silhouette is written with
  // absolute coordinates so the shape can be verified by inspection.
  var GEO = {
    canvasW: 433, canvasH: 577,

    // Face feature anchors. The mouth stays at the y that static/cloud_anim.js
    // hardcodes in its MOUTH table (~216,346) so expression swaps never jump.
    faceX: 216, faceY: 300,
    leftEye:  { x: 176, y: 268 },
    rightEye: { x: 256, y: 268 },
    eyeW: 23, eyeH: 16,
    pupilR: 9,
    browY: 230,
    nose:  { x: 216, y: 306 },
    mouth: { x: 216, y: 346 },
    cheekY: 306,
    leftCheekX: 148, rightCheekX: 284,

    // Body envelope the silhouette is drawn around (for reference/clamping).
    body: { cx: 216, top: 158, bottom: 438 },

    // Limb pivot anchors (shoulder / hip).
    leftShoulder:  { x: 66,  y: 296 },
    rightShoulder: { x: 366, y: 296 },
    leftHip:       { x: 162, y: 396 },
    rightHip:      { x: 270, y: 396 },

    // Where the hanging hands and the planted feet land. The arms are deliberately
    // long: the wrists reach well past the body underside (438) so the wisps
    // read as expressive limbs rather than stubs.
    wristY: 436,
    handY: 450,
    footY: 482,
    shadowY: 518,

    armWidth: 21,
    legTop: 388
  };

  // The one cloud silhouette: a single closed path.
  // Extents: x 40..392 (centred on 216), y ~152..438.
  // Top edge rolls through three broad humps; underside scallops softly.
  var SILHOUETTE_D = [
    "M 44 396",
    "C 78 434 122 440 166 416",
    "C 208 438 248 438 290 416",
    "C 332 440 376 434 384 396",
    "C 392 372 390 340 380 312",
    "C 368 268 338 214 310 196",
    "C 286 182 262 196 244 224",
    "C 230 176 210 158 190 158",
    "C 160 158 130 178 114 214",
    "C 94 244 70 262 58 300",
    "C 44 336 40 370 44 396",
    "Z"
  ].join(" ");

  function attr(el, name, val) { el.setAttribute(name, val); }
  function el(name) { return document.createElementNS("http://www.w3.org/2000/svg", name); }

  // Helper: set the CSS pivot so cloud_anim.js can rotate/scale each part
  // around the correct joint (shoulder/hip/eye-centre) as a pure transform.
  function rigPart(g, xPct, yPct) {
    g.style.transformBox = "fill-box";
    g.style.transformOrigin = xPct + "% " + yPct + "%";
    g.style.willChange = "transform";
    return g;
  }

  // Generic marker used for every addressable part (read back by collectRig).
  function labelPart(g, id) {
    attr(g, "data-part", id);
    return g;
  }

  // Linear gradient helper (defs stop pairs, top->bottom).
  function makeGrad(defs, id, top, bottom, topOffset, bottomOffset) {
    var g = el("linearGradient");
    attr(g, "id", id);
    attr(g, "x1", "0"); attr(g, "y1", "0"); attr(g, "x2", "0"); attr(g, "y2", "1");
    var s1 = el("stop"); attr(s1, "offset", topOffset || "0%"); attr(s1, "stop-color", top);
    var s2 = el("stop"); attr(s2, "offset", bottomOffset || "100%"); attr(s2, "stop-color", bottom);
    g.appendChild(s1); g.appendChild(s2);
    defs.appendChild(g);
    return g;
  }

  // Radial gradient helper (for the crown sheen).
  function makeRadial(defs, id, color) {
    var g = el("radialGradient");
    attr(g, "id", id);
    attr(g, "cx", "0.5"); attr(g, "cy", "0.5"); attr(g, "r", "0.5");
    var s1 = el("stop"); attr(s1, "offset", "0%");
    attr(s1, "stop-color", color); attr(s1, "stop-opacity", "0.85");
    var s2 = el("stop"); attr(s2, "offset", "100%");
    attr(s2, "stop-color", color); attr(s2, "stop-opacity", "0");
    g.appendChild(s1); g.appendChild(s2);
    defs.appendChild(g);
    return g;
  }

  // Blush gradient. The stops deliberately hold a plateau before falling to
  // zero: a straight 0.85 -> 0 fade leaves only a faint centre dot, and at
  // production size the cheek ellipse is only a few pixels tall, so the flush
  // would be invisible. The plateau keeps the colour readable all the way out
  // to a soft edge.
  function makeBlushGrad(defs, id, color) {
    var g = el("radialGradient");
    attr(g, "id", id);
    attr(g, "cx", "0.5"); attr(g, "cy", "0.5"); attr(g, "r", "0.5");
    var s1 = el("stop"); attr(s1, "offset", "0%");
    attr(s1, "stop-color", color); attr(s1, "stop-opacity", "0.78");
    var s2 = el("stop"); attr(s2, "offset", "55%");
    attr(s2, "stop-color", color); attr(s2, "stop-opacity", "0.62");
    var s3 = el("stop"); attr(s3, "offset", "100%");
    attr(s3, "stop-color", color); attr(s3, "stop-opacity", "0");
    g.appendChild(s1); g.appendChild(s2); g.appendChild(s3);
    defs.appendChild(g);
    return g;
  }

  function build() {
    var svg = el("svg");
    attr(svg, "viewBox", "0 0 " + GEO.canvasW + " " + GEO.canvasH);
    attr(svg, "preserveAspectRatio", "xMidYMid meet");
    attr(svg, "width", "100%");
    attr(svg, "height", "100%");
    attr(svg, "aria-hidden", "true");

    var content = el("g");

    // ── Soft ground shadow (the cloud floats just above it) ───────────────
    var shadowG = rigPart(el("g"), 50, 50); labelPart(shadowG, "shadow");
    var shadowOuter = el("ellipse");
    attr(shadowOuter, "cx", "216"); attr(shadowOuter, "cy", String(GEO.shadowY));
    attr(shadowOuter, "rx", "116"); attr(shadowOuter, "ry", "15");
    attr(shadowOuter, "fill", COLORS.shadow);
    var shadowCore = el("ellipse");
    attr(shadowCore, "cx", "216"); attr(shadowCore, "cy", String(GEO.shadowY - 1));
    attr(shadowCore, "rx", "60"); attr(shadowCore, "ry", "8");
    attr(shadowCore, "fill", COLORS.shadow);
    shadowG.appendChild(shadowOuter);
    shadowG.appendChild(shadowCore);
    content.appendChild(shadowG);

    // ── Legs — stubby tapered limbs with soft planted feet ──────────────
    // Drawn before the body so the hips are tucked behind the silhouette and
    // the legs read as attached rather than floating.
    function legPart(name, cx) {
      var g = rigPart(el("g"), 50, 0); labelPart(g, name);
      var top = GEO.legTop;
      var stub = el("path");
      attr(stub, "d",
        "M " + (cx - 18) + " " + top +
        " C " + (cx - 23) + " 424 " + (cx - 21) + " 452 " + (cx - 15) + " 468 " +
        " L " + (cx + 15) + " 468" +
        " C " + (cx + 21) + " 452 " + (cx + 23) + " 424 " + (cx + 18) + " " + top + " Z");
      attr(stub, "fill", COLORS.limbLow);
      var foot = el("ellipse");
      attr(foot, "cx", String(cx)); attr(foot, "cy", String(GEO.footY));
      attr(foot, "rx", "26"); attr(foot, "ry", "10");
      attr(foot, "fill", COLORS.limbHigh);
      attr(foot, "stroke", COLORS.edge); attr(foot, "stroke-width", "2.5");
      g.appendChild(stub);
      g.appendChild(foot);
      return g;
    }
    var leftLeg = legPart("leftLeg", GEO.leftHip.x);
    var rightLeg = legPart("rightLeg", GEO.rightHip.x);
    content.appendChild(leftLeg);
    content.appendChild(rightLeg);

    // ── Shared gradients + the silhouette clip ──────────────────────────
    var defs = el("defs");
    makeGrad(defs, "vmCloudBodyGr", COLORS.cloudTop, COLORS.cloudBase, "0%", "100%");
    makeGrad(defs, "vmCloudLimbGr", COLORS.limbHigh, COLORS.limbLow, "0%", "100%");
    makeBlushGrad(defs, "vmCloudBlushGr", COLORS.blush);
    makeRadial(defs, "vmCloudSheenGr", COLORS.glint);

    // The clip reuses SILHOUETTE_D so inner shading can never spill outside
    // the cloud, whatever happens to the fills later.
    var clip = el("clipPath");
    attr(clip, "id", "vmCloudSilhouette");
    var clipShape = el("path");
    attr(clipShape, "d", SILHOUETTE_D);
    clip.appendChild(clipShape);
    defs.appendChild(clip);
    svg.appendChild(defs);

    // ── Body ────────────────────────────────────────────────────────────
    var bodyG = rigPart(el("g"), 50, 58); labelPart(bodyG, "body");

    // Rim pass: the same path, grown outward by a thick round-joined stroke so
    // the silhouette stays readable on light and dark surfaces alike.
    var rim = el("path");
    attr(rim, "d", SILHOUETTE_D);
    attr(rim, "fill", COLORS.edge);
    attr(rim, "stroke", COLORS.edge);
    attr(rim, "stroke-width", "14");
    attr(rim, "stroke-linejoin", "round");
    bodyG.appendChild(rim);

    // Fill pass: the gradient body, drawn over the rim.
    var mainBody = el("path");
    attr(mainBody, "d", SILHOUETTE_D);
    attr(mainBody, "fill", "url(#vmCloudBodyGr)");
    bodyG.appendChild(mainBody);

    // Everything below is clipped to the silhouette.
    var shaded = el("g");
    attr(shaded, "clip-path", "url(#vmCloudSilhouette)");

    // Crown sheen for volume.
    var sheen = el("ellipse");
    attr(sheen, "cx", "196"); attr(sheen, "cy", "206");
    attr(sheen, "rx", "118"); attr(sheen, "ry", "62");
    attr(sheen, "fill", "url(#vmCloudSheenGr)");
    shaded.appendChild(sheen);

    // Soft underside shading so the lower mass sits back.
    var softShade = el("path");
    attr(softShade, "d",
      "M 56 360 C 120 414 200 430 250 428 C 306 426 352 402 376 360 " +
      "C 300 452 140 452 56 360 Z");
    attr(softShade, "fill", COLORS.cloudBase);
    attr(softShade, "opacity", "0.55");
    shaded.appendChild(softShade);

    // ── Blush cheeks ─────────────────────────────────────────────────────
    function cheekPart(name, cx) {
      var g = rigPart(el("g"), 50, 50); labelPart(g, name);
      var c = el("ellipse");
      attr(c, "cx", String(cx)); attr(c, "cy", String(GEO.cheekY));
      attr(c, "rx", "30"); attr(c, "ry", "16");
      attr(c, "fill", "url(#vmCloudBlushGr)");
      g.appendChild(c);
      return g;
    }
    var leftCheek = cheekPart("leftCheek", GEO.leftCheekX);
    var rightCheek = cheekPart("rightCheek", GEO.rightCheekX);
    shaded.appendChild(leftCheek);
    shaded.appendChild(rightCheek);

    // ── Eyebrows — soft arcs that the expression layer tilts ────────────
    function browPart(name, cx) {
      var g = rigPart(el("g"), 50, 50); labelPart(g, name);
      var p = el("path");
      attr(p, "d", "M " + (cx - 22) + " " + (GEO.browY + 6) +
                  " Q " + cx + " " + (GEO.browY - 8) +
                  " " + (cx + 22) + " " + (GEO.browY + 6));
      attr(p, "fill", "none");
      attr(p, "stroke", COLORS.ink);
      attr(p, "stroke-width", "7");
      attr(p, "stroke-linecap", "round");
      g.appendChild(p);
      return g;
    }
    var leftBrow = browPart("leftEyebrow", GEO.leftEye.x);
    var rightBrow = browPart("rightEyebrow", GEO.rightEye.x);
    shaded.appendChild(leftBrow);
    shaded.appendChild(rightBrow);

    // ── Eyes — navy almond with a light pupil the gaze engine can move ──
    function eyePart(name, cx, cy) {
      var g = rigPart(el("g"), 50, 50); labelPart(g, name);

      // Almond (lens) eye: two mirrored quadratics.
      var almond = el("path");
      attr(almond, "d",
        "M " + (cx - GEO.eyeW) + " " + cy +
        " Q " + cx + " " + (cy - GEO.eyeH * 2) + " " + (cx + GEO.eyeW) + " " + cy +
        " Q " + cx + " " + (cy + GEO.eyeH * 2) + " " + (cx - GEO.eyeW) + " " + cy + " Z");
      attr(almond, "fill", COLORS.ink);
      g.appendChild(almond);

      // The pupil is its own group so gaze translates it as one unit and the
      // glint travels with it instead of sliding off.
      var pupil = el("g");
      attr(pupil, "class", "cloud-pupil");
      var iris = el("circle");
      attr(iris, "cx", String(cx)); attr(iris, "cy", String(cy));
      attr(iris, "r", String(GEO.pupilR));
      attr(iris, "fill", COLORS.limbHigh);
      var glint = el("circle");
      attr(glint, "cx", String(cx - 3.5)); attr(glint, "cy", String(cy - 4));
      attr(glint, "r", "3.2");
      attr(glint, "fill", COLORS.glint);
      pupil.appendChild(iris);
      pupil.appendChild(glint);
      g.appendChild(pupil);
      return g;
    }
    var leftEye = eyePart("leftEye", GEO.leftEye.x, GEO.leftEye.y);
    var rightEye = eyePart("rightEye", GEO.rightEye.x, GEO.rightEye.y);
    shaded.appendChild(leftEye);
    shaded.appendChild(rightEye);

    // ── Nose — a small soft wedge, just enough to read ───────────────────
    var noseG = rigPart(el("g"), 50, 50); labelPart(noseG, "nose");
    var nose = el("path");
    attr(nose, "d",
      "M " + (GEO.nose.x - 8) + " " + (GEO.nose.y - 7) +
      " Q " + GEO.nose.x + " " + (GEO.nose.y - 9) + " " + (GEO.nose.x + 8) + " " + (GEO.nose.y - 7) +
      " Q " + (GEO.nose.x + 4) + " " + (GEO.nose.y + 8) + " " + GEO.nose.x + " " + (GEO.nose.y + 9) +
      " Q " + (GEO.nose.x - 4) + " " + (GEO.nose.y + 8) + " " + (GEO.nose.x - 8) + " " + (GEO.nose.y - 7) + " Z");
    attr(nose, "fill", COLORS.inkSoft);
    var noseGloss = el("circle");
    attr(noseGloss, "cx", String(GEO.nose.x - 2.5)); attr(noseGloss, "cy", String(GEO.nose.y - 4));
    attr(noseGloss, "r", "2.4");
    attr(noseGloss, "fill", COLORS.glint);
    attr(noseGloss, "opacity", "0.75");
    noseG.appendChild(nose);
    noseG.appendChild(noseGloss);
    shaded.appendChild(noseG);

    // ── Mouth ────────────────────────────────────────────────────────────
    // static/cloud_anim.js swaps this path's `d` per expression, so the
    // neutral value below MUST match MOUTH.neutral in that file (~216,346).
    var mouthG = rigPart(el("g"), 50, 50); labelPart(mouthG, "mouth");
    var mouthPath = el("path");
    attr(mouthPath, "d", "M198 344 C205 351 227 351 234 344");
    attr(mouthPath, "fill", "none");
    attr(mouthPath, "stroke", COLORS.ink);
    attr(mouthPath, "stroke-width", "6");
    attr(mouthPath, "stroke-linecap", "round");
    attr(mouthPath, "data-expression", "neutral");
    mouthG.appendChild(mouthPath);
    shaded.appendChild(mouthG);

    bodyG.appendChild(shaded);

    // ── Arms — long tapered wisps with mitten hands at the wrist ─────────
    function armPart(name, handName, sx, sy, hx, hy, dir) {
      var g = rigPart(el("g"), 50, 0); labelPart(g, name);

      // Round-capped stroke so the limb tapers smoothly into the wrist. Control
      // points are derived from the wrist so the whole limb scales with GEO.
      var armPath = el("path");
      attr(armPath, "d",
        "M " + sx + " " + sy +
        " C " + (sx + dir * 20) + " " + (sy + 56) +
        " " + (hx - dir * 8) + " " + (hy - 38) + " " + hx + " " + hy);
      attr(armPath, "fill", "none");
      attr(armPath, "stroke", "url(#vmCloudLimbGr)");
      attr(armPath, "stroke-width", String(GEO.armWidth));
      attr(armPath, "stroke-linecap", "round");
      g.appendChild(armPath);

      // Hand group: palm + thumb, pivoting from the wrist.
      var handG = rigPart(el("g"), 50, 0); labelPart(handG, handName);
      var palm = el("ellipse");
      attr(palm, "cx", String(hx)); attr(palm, "cy", String(GEO.handY));
      attr(palm, "rx", "17"); attr(palm, "ry", "14");
      attr(palm, "fill", COLORS.limbHigh);
      attr(palm, "stroke", COLORS.edge); attr(palm, "stroke-width", "2.5");
      var thumb = el("ellipse");
      attr(thumb, "cx", String(hx - dir * 14)); attr(thumb, "cy", String(GEO.handY - 8));
      attr(thumb, "rx", "8"); attr(thumb, "ry", "6");
      attr(thumb, "fill", COLORS.limbHigh);
      attr(thumb, "stroke", COLORS.edge); attr(thumb, "stroke-width", "2");
      handG.appendChild(palm);
      handG.appendChild(thumb);
      g.appendChild(handG);
      return g;
    }
    var leftArm = armPart("leftArm", "leftHand",
      GEO.leftShoulder.x + 8, GEO.leftShoulder.y - 4, 46, GEO.wristY, -1);
    var rightArm = armPart("rightArm", "rightHand",
      GEO.rightShoulder.x - 8, GEO.rightShoulder.y - 4, 386, GEO.wristY, 1);
    bodyG.appendChild(leftArm);
    bodyG.appendChild(rightArm);

    content.appendChild(bodyG);
    svg.appendChild(content);

    var parts = {
      root: svg,
      body: bodyG,
      leftEye: leftEye,
      rightEye: rightEye,
      leftEyebrow: leftBrow,
      rightEyebrow: rightBrow,
      nose: noseG,
      leftCheek: leftCheek,
      rightCheek: rightCheek,
      mouth: mouthG,
      leftArm: leftArm,
      rightArm: rightArm,
      leftHand: leftArm.querySelector('[data-part="leftHand"]'),
      rightHand: rightArm.querySelector('[data-part="rightHand"]'),
      leftLeg: leftLeg,
      rightLeg: rightLeg,
      shadow: shadowG,
      leftPupil: leftEye.querySelector(".cloud-pupil"),
      rightPupil: rightEye.querySelector(".cloud-pupil")
    };

    return { parts: parts, GEO: GEO };
  }

  // ── Mounting (used by index.html static markup and cloud.js) ────────────
  // The character container (#vmCloudCharacter) is a small fixed <div>
  // holding the fallback <img> plus this layered rig in a pointer-transparent
  // overlay. `mount` builds the SVG into an existing container once and
  // returns the runnable rig (parts + noted pivots) so the animation engine
  // can drive it.
  function findRigMount(container) {
    if (!container) return null;
    var m = container.querySelector && container.querySelector(".vmcloud-rig-mount");
    return m || container;
  }

  function alreadyRooted(host) {
    var roots = host.querySelectorAll && host.querySelectorAll("svg.vmcloud-rig-svg");
    return !!(roots && roots.length);
  }

  // The rig overlay is the single visible companion once it is live. The
  // flattened cloud.png stays in the DOM only as the no-JS/loading fallback —
  // hide it the moment the rig takes over so the old asset is never visible
  // behind the cloud and there is exactly one visible companion.
  function hideFallback(container) {
    if (!container || !container.querySelectorAll) return;
    var imgs = container.querySelectorAll("img.vmcloud-fallback");
    for (var i = 0; imgs && i < imgs.length; i++) {
      imgs[i].style.visibility = "hidden";
    }
  }

  function mount(container) {
    var host = findRigMount(container);
    if (!host || host.nodeType !== 1) return null;
    if (alreadyRooted(host)) {
      // Already mounted (auto-boot or cloud.js raced ahead): return it.
      hideFallback(container);
      return collectRig(host.querySelector("svg.vmcloud-rig-svg"));
    }
    host.setAttribute("data-vm-rig", "1");
    // Build + attach FIRST so the fallback image is only hidden once the cloud
    // is actually on screen — a failure here must never leave a blank space.
    var built;
    try { built = build(); } catch (e) { built = null; }
    if (!built) return null;
    attr(built.parts.root, "class", "vmcloud-rig-svg");
    host.appendChild(built.parts.root);
    hideFallback(container);
    built.holder = host;
    return built;
  }

  // Re-read parts out of an already-built rig SVG (used to hand the anim
  // engine the same object shape whether we built it or an auto-boot did).
  function collectRig(svg) {
    if (!svg) return null;
    function part(name) {
      var g = svg.querySelector('[data-part="' + name + '"]');
      return g || null;
    }
    var leftEye = part("leftEye");
    var rightEye = part("rightEye");
    var leftArm = part("leftArm");
    var rightArm = part("rightArm");
    var pupil = function (eye) {
      return eye && eye.querySelector ? (eye.querySelector(".cloud-pupil") || null) : null;
    };
    return {
      parts: {
        root: svg,
        body: part("body"),
        leftEye: leftEye,
        rightEye: rightEye,
        leftEyebrow: part("leftEyebrow"),
        rightEyebrow: part("rightEyebrow"),
        nose: part("nose"),
        leftCheek: part("leftCheek"),
        rightCheek: part("rightCheek"),
        mouth: part("mouth"),
        leftArm: leftArm,
        rightArm: rightArm,
        leftHand: leftArm ? leftArm.querySelector('[data-part="leftHand"]') : null,
        rightHand: rightArm ? rightArm.querySelector('[data-part="rightHand"]') : null,
        leftLeg: part("leftLeg"),
        rightLeg: part("rightLeg"),
        shadow: part("shadow"),
        leftPupil: pupil(leftEye),
        rightPupil: pupil(rightEye)
      },
      GEO: GEO
    };
  }

  // Build the canonical #vmCloudCharacter container programmatically
  // (fallback PNG + rig mount). Used by cloud.js ensureCloudCharacter when no
  // static markup exists. Returns the created element.
  function mountRoot() {
    var existing = document.getElementById("vmCloudCharacter");
    if (existing) return existing;
    if (!document.body) return null;
    var d = document.createElement("div");
    d.id = "vmCloudCharacter";
    d.className = "vmcloud-character";
    d.setAttribute("role", "button");
    d.setAttribute("aria-label", "Cloud companion");
    d.style.position = "fixed";
    d.style.right = "18px";
    d.style.bottom = "calc(18px + env(safe-area-inset-bottom))";
    d.style.zIndex = "8000";
    d.style.display = "block";
    d.style.visibility = "visible";
    d.style.opacity = "1";
    d.style.width = "132px";
    d.style.height = "auto";
    d.style.aspectRatio = GEO.canvasW + " / " + GEO.canvasH;
    d.style.pointerEvents = "auto";
    d.style.touchAction = "none";
    d.style.cursor = "grab";
    d.style.userSelect = "none";
    d.style.webkitUserDrag = "none";
    d.style.filter = "drop-shadow(0 10px 18px rgba(0, 10, 20, 0.35))";

    var img = document.createElement("img");
    img.className = "vmcloud-fallback";
    img.src = "/static/cloud.png";
    img.alt = "Cloud";
    img.draggable = false;
    img.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;object-fit:contain;" +
      "pointer-events:none;user-select:none;-webkit-user-drag:none;";

    var rigMount = document.createElement("div");
    rigMount.className = "vmcloud-rig-mount";
    rigMount.style.cssText =
      "position:absolute;inset:0;pointer-events:none;overflow:visible;";

    d.appendChild(img);
    d.appendChild(rigMount);
    document.body.appendChild(d);
    mount(d);
    return d;
  }

  window.VMCloudRig = {
    COLORS: COLORS,
    GEO: GEO,
    build: build,
    mount: mount,
    mountRoot: mountRoot,
    collectRig: collectRig
  };

  // Auto-boot: if static index.html markup already carries a
  // .vmcloud-rig-mount inside #vmCloudCharacter (the visual-isolation path),
  // mount the rig into it without waiting for the app-shell lifecycle.
  function autoBoot() {
    if (!document.body) return;
    var node = document.getElementById("vmCloudCharacter");
    if (node && !alreadyRooted(findRigMount(node))) {
      try { mount(node); } catch (e) { /* index.html path stays fallback-only */ }
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoBoot);
  } else {
    autoBoot();
  }
})();
