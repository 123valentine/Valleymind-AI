/* ValleyMind Cloud companion — layered vector rig for the ROBOT visual.
   ────────────────────────────────────────────────────────────────────
   The companion is a small, cute, futuristic floating 3D-cartoon AI ROBOT:
   an original robot companion (NOT the legacy cloud.png). Because a single
   flattened image cannot move its eyes, brows, mouth, arms or hands
   independently, this module builds a LAYERED SVG rig that draws the robot
   as separately addressable 3D-toy-like parts:

       body | head | face | leftEyebrow | rightEyebrow | leftEye | rightEye |
       mouth | leftEar | rightEar | leftArm | rightArm | leftHand | rightHand |
       leftLeg | rightLeg | lowerBody | halo | shadow

   Each part is an SVG <g> that the single centralized animation engine
   (static/cloud_anim.js) can translate/rotate/scale independently via CSS
   transforms (eyes blink, pupils gaze, mouth smiles/talks, arms wave & point,
   hands waggle, ears pulse, halo floats, base hovers). The character itself
   floats with a gentle hover idle — no legs, no walking figure; the lower
   body is a rounded floating base with small blue hover pods.

   static/cloud.png is NOT the robot visual. It remains in the DOM ONLY as the
   no-JS / loading fallback before this rig mounts; the moment the rig takes
   over the fallback is hidden, so there is exactly ONE visible companion and
   the old cloud never shows behind the robot.

   Palette (robot): white/off-white glossy body, saturated clean blue accents,
   deep black glossy face screen, bright cyan glowing eyes/mouth, and a subtle
   light-blue halo ring.

   Usage (internal):
       var rig = window.VMCloudRig.build(containerEl);
       rig.parts.leftEye  -> <g> DOM node
       rig.parts.mouth    -> <g> DOM node
       rig.root           -> container <div>
*/

(function () {
  "use strict";

  // Robot palette (white/off-white glossy body, blue accents, black screen,
  // cyan glow). Values are the canonical companion palette.
  var COLORS = {
    headTop: "#F4F9FB",   // white glossy head highlight
    headMid: "#E8F0F4",   // soft mid tone on the head/arms
    headBase:"#D3DEE4",   // shaded underside of the head
    face:    "#05090D",   // deep black glossy face screen
    faceGlow:"#0B1622",   // faint screen reflection/lower glow
    body:    "#F4F9FB",   // white glossy torso
    bodyDark:"#DAE6EA",   // torso shading
    limb:    "#F4F9FB",   // white arms
    limbDark:"#C2D2D9",   // arm underside shading
    blue:    "#2E7CF6",   // saturated blue accent (panels, joints, pods)
    blueDark:"#1E5FD0",   // blue shading
    blueLight:"#6FA9FF",  // blue highlight
    cyan:    "#00E5FF",   // glowing eyes / mouth / halo
    cyanDim: "#00B8D6",   // cyan shadow tone
    ink:     "#00E5FF",   // eyes/brows/mouth glow ink
    leg:     "#2E7CF6",   // hover pods (blue)
    legDark: "#1E5FD0",   // pod shading / nozzles
    shadow:  "rgba(0,0,0,0.16)"
  };

  // Anchor / geometry constants in full canvas space (0..433 x 0..577).
  var GEO = {
    // Face feature anchors.
    leftEye:  { x: 186, y: 208 },
    rightEye: { x: 247, y: 208 },
    eyeW: 13, eyeH: 17,
    browY: 180,
    mouth: { x: 216, y: 248 },
    // Limb pivot anchors (shoulder / hover-pod hip).
    leftShoulder:  { x: 150, y: 352 },
    rightShoulder: { x: 283, y: 352 },
    leftHip:       { x: 172, y: 496 },
    rightHip:      { x: 261, y: 496 }
  };

  function attr(el, name, val) { el.setAttribute(name, val); }
  function el(name) { return document.createElementNS("http://www.w3.org/2000/svg", name); }

  // Helper: set the CSS pivot so cloud_anim.js can rotate/scale each part
  // around the correct joint (shoulder/pod/eye-centre) as a pure transform.
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

  // Linear gradient helper (defs stop pairs, top→bottom).
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

  function build() {
    var svg = el("svg");
    // Same canvas as the companion container (aspect-ratio 433/577); content
    // lives in full canvas space directly under <body>.
    attr(svg, "viewBox", "0 0 433 577");
    attr(svg, "preserveAspectRatio", "xMidYMid meet");
    attr(svg, "width", "100%");
    attr(svg, "height", "100%");
    attr(svg, "aria-hidden", "true");

    var content = el("g");

    // Shared linear gradients for the glossy toy shading.
    var defs = el("defs");
    makeGrad(defs, "vmRobotHeadGr", COLORS.headTop, COLORS.headBase, "0%", "100%");
    makeGrad(defs, "vmRobotScreenGr", COLORS.face, COLORS.faceGlow, "0%", "100%");
    makeGrad(defs, "vmRobotBodyGr", COLORS.body, COLORS.bodyDark, "0%", "100%");
    makeGrad(defs, "vmRobotBlueGr", COLORS.blueLight, COLORS.blueDark, "0%", "100%");
    makeGrad(defs, "vmRobotCyanGr", COLORS.cyan, COLORS.cyanDim, "0%", "100%");
    makeGrad(defs, "vmRobotBaseGr", "#EFF6F9", "#D5E1E6", "0%", "100%");
    svg.appendChild(defs);

    // ── Ground shadow (soft contact shadow under the hovering robot) ──────
    var shadowG = el("g"); attr(shadowG, "data-part", "shadow");
    var shadowOuter = el("ellipse");
    attr(shadowOuter, "cx", "216"); attr(shadowOuter, "cy", "552");
    attr(shadowOuter, "rx", "118"); attr(shadowOuter, "ry", "15");
    attr(shadowOuter, "fill", COLORS.shadow);
    var shadowCore = el("ellipse");
    attr(shadowCore, "cx", "216"); attr(shadowCore, "cy", "550");
    attr(shadowCore, "rx", "62"); attr(shadowCore, "ry", "9");
    attr(shadowCore, "fill", "rgba(0,0,0,0.24)");
    shadowG.appendChild(shadowOuter);
    shadowG.appendChild(shadowCore);
    content.appendChild(shadowG);

    // ── Halo (thin glowing light-blue ring floating above the head) ───────
    var haloG = rigPart(el("g"), 50, 50); labelPart(haloG, "halo");
    var haloGlow = el("ellipse");
    attr(haloGlow, "cx", "216"); attr(haloGlow, "cy", "48");
    attr(haloGlow, "rx", "112"); attr(haloGlow, "ry", "22");
    attr(haloGlow, "fill", "none"); attr(haloGlow, "stroke", COLORS.cyan);
    attr(haloGlow, "stroke-width", "15"); attr(haloGlow, "stroke-opacity", "0.22");
    var haloRing = el("ellipse");
    attr(haloRing, "cx", "216"); attr(haloRing, "cy", "48");
    attr(haloRing, "rx", "112"); attr(haloRing, "ry", "22");
    attr(haloRing, "fill", "none"); attr(haloRing, "stroke", COLORS.cyan);
    attr(haloRing, "stroke-width", "6"); attr(haloRing, "stroke-opacity", "0.75");
    haloG.appendChild(haloGlow);
    haloG.appendChild(haloRing);
    content.appendChild(haloG);

    // ── Ears (blue circular modules peeking out behind the head) ───────────
    function earPart(id, cx) {
      var g = rigPart(el("g"), 50, 50); labelPart(g, id);
      var shell = el("circle");
      attr(shell, "cx", cx); attr(shell, "cy", "190"); attr(shell, "r", "24");
      attr(shell, "fill", "url(#vmRobotBlueGr)");
      var inner = el("circle");
      attr(inner, "cx", cx); attr(inner, "cy", "190"); attr(inner, "r", "13");
      attr(inner, "fill", COLORS.blueDark);
      var dot = el("circle");
      attr(dot, "cx", cx); attr(dot, "cy", "190"); attr(dot, "r", "4.5");
      attr(dot, "fill", COLORS.cyan); attr(dot, "stroke-opacity", "0.85");
      g.appendChild(shell); g.appendChild(inner); g.appendChild(dot);
      return g;
    }
    var leftEar = earPart("leftEar", 92);
    var rightEar = earPart("rightEar", 341);
    content.appendChild(leftEar);
    content.appendChild(rightEar);

    // ── Hover pods ("legs"): small blue rounded pods with nozzles ──────────
    function podPart(id, cx) {
      var g = rigPart(el("g"), 50, 0); labelPart(g, id);
      var pod = el("circle");
      attr(pod, "cx", cx); attr(pod, "cy", "500"); attr(pod, "r", "23");
      attr(pod, "fill", "url(#vmRobotBlueGr)");
      var cap = el("ellipse");
      attr(cap, "cx", cx); attr(cap, "cy", "488"); attr(cap, "rx", "16"); attr(cap, "ry", "7");
      attr(cap, "fill", "rgba(255,255,255,0.35)");
      var nozzle = el("rect");
      attr(nozzle, "x", String(cx - 10)); attr(nozzle, "y", "520");
      attr(nozzle, "width", "20"); attr(nozzle, "height", "14"); attr(nozzle, "rx", "6");
      attr(nozzle, "fill", COLORS.legDark);
      g.appendChild(pod); g.appendChild(cap); g.appendChild(nozzle);
      return g;
    }
    var leftLeg = podPart("leftLeg", 172);
    var rightLeg = podPart("rightLeg", 261);
    content.appendChild(leftLeg);
    content.appendChild(rightLeg);

    // ── Lower body (rounded floating base the torso sits on) ───────────────
    var lowerBodyG = rigPart(el("g"), 50, 30); labelPart(lowerBodyG, "lowerBody");
    var base = el("ellipse");
    attr(base, "cx", "216"); attr(base, "cy", "460"); attr(base, "rx", "104"); attr(base, "ry", "36");
    attr(base, "fill", "url(#vmRobotBaseGr)");
    var baseRim = el("ellipse");
    attr(baseRim, "cx", "216"); attr(baseRim, "cy", "474"); attr(baseRim, "rx", "82"); attr(baseRim, "ry", "15");
    attr(baseRim, "fill", COLORS.bodyDark); attr(baseRim, "opacity", "0.7");
    var baseRing = el("ellipse");
    attr(baseRing, "cx", "216"); attr(baseRing, "cy", "463"); attr(baseRing, "rx", "96"); attr(baseRing, "ry", "26");
    attr(baseRing, "fill", "none"); attr(baseRing, "stroke", COLORS.blue);
    attr(baseRing, "stroke-width", "4"); attr(baseRing, "stroke-opacity", "0.5");
    lowerBodyG.appendChild(base); lowerBodyG.appendChild(baseRim); lowerBodyG.appendChild(baseRing);
    content.appendChild(lowerBodyG);

    // ── Body (white glossy torso + blue shoulder joints, chest mark, waist) ─
    var bodyG = rigPart(el("g"), 50, 30); attr(bodyG, "data-part", "body");
    // Neck (draw first so the head covers its top edge).
    var neck = el("rect");
    attr(neck, "x", "198"); attr(neck, "y", "300"); attr(neck, "width", "37"); attr(neck, "height", "26"); attr(neck, "rx", "10");
    attr(neck, "fill", COLORS.headMid);
    bodyG.appendChild(neck);
    // Torso.
    var torso = el("rect");
    attr(torso, "x", "136"); attr(torso, "y", "314"); attr(torso, "width", "161"); attr(torso, "height", "130"); attr(torso, "rx", "48");
    attr(torso, "fill", "url(#vmRobotBodyGr)");
    bodyG.appendChild(torso);
    // Chest gloss sheen.
    var gloss = el("ellipse");
    attr(gloss, "cx", "216"); attr(gloss, "cy", "336"); attr(gloss, "rx", "54"); attr(gloss, "ry", "13");
    attr(gloss, "fill", "#FFFFFF"); attr(gloss, "opacity", "0.45");
    bodyG.appendChild(gloss);
    // Chest badge: blue round mark with a white valley chevron.
    var badge = el("circle");
    attr(badge, "cx", "216"); attr(badge, "cy", "374"); attr(badge, "r", "20");
    attr(badge, "fill", "url(#vmRobotBlueGr)");
    var chevron = el("path");
    attr(chevron, "d", "M204 366 L216 379 L228 366");
    attr(chevron, "fill", "none"); attr(chevron, "stroke", "#FFFFFF");
    attr(chevron, "stroke-width", "7"); attr(chevron, "stroke-linecap", "round"); attr(chevron, "stroke-linejoin", "round");
    bodyG.appendChild(badge);
    bodyG.appendChild(chevron);
    // Blue shoulder joints (arm pivots).
    var leftJoint = el("circle");
    attr(leftJoint, "cx", "150"); attr(leftJoint, "cy", "352"); attr(leftJoint, "r", "13");
    attr(leftJoint, "fill", "url(#vmRobotBlueGr)");
    var rightJoint = el("circle");
    attr(rightJoint, "cx", "283"); attr(rightJoint, "cy", "352"); attr(rightJoint, "r", "13");
    attr(rightJoint, "fill", "url(#vmRobotBlueGr)");
    bodyG.appendChild(leftJoint);
    bodyG.appendChild(rightJoint);
    // Blue waist band.
    var waist = el("rect");
    attr(waist, "x", "146"); attr(waist, "y", "414"); attr(waist, "width", "141"); attr(waist, "height", "30"); attr(waist, "rx", "15");
    attr(waist, "fill", "url(#vmRobotBlueGr)");
    bodyG.appendChild(waist);
    content.appendChild(bodyG);

    // ── Head (big white glossy rounded shell + blue top panel + face screen) ─
    var headG = rigPart(el("g"), 50, 50); attr(headG, "data-part", "head");
    var shell = el("rect");
    attr(shell, "x", "102"); attr(shell, "y", "84"); attr(shell, "width", "229"); attr(shell, "height", "224"); attr(shell, "rx", "74");
    attr(shell, "fill", "url(#vmRobotHeadGr)");
    var shellSheen = el("ellipse");
    attr(shellSheen, "cx", "184"); attr(shellSheen, "cy", "122"); attr(shellSheen, "rx", "72"); attr(shellSheen, "ry", "18");
    attr(shellSheen, "fill", "#FFFFFF"); attr(shellSheen, "opacity", "0.5");
    headG.appendChild(shell);
    headG.appendChild(shellSheen);
    // Blue top panel.
    var topPanel = el("rect");
    attr(topPanel, "x", "130"); attr(topPanel, "y", "98"); attr(topPanel, "width", "173"); attr(topPanel, "height", "32"); attr(topPanel, "rx", "16");
    attr(topPanel, "fill", "url(#vmRobotBlueGr)");
    var panelGloss = el("rect");
    attr(panelGloss, "x", "144"); attr(panelGloss, "y", "104"); attr(panelGloss, "width", "96"); attr(panelGloss, "height", "9"); attr(panelGloss, "rx", "4.5");
    attr(panelGloss, "fill", "#FFFFFF"); attr(panelGloss, "opacity", "0.45");
    headG.appendChild(topPanel);
    headG.appendChild(panelGloss);

    // Face (dark glossy screen) — a part of the head so it tilts with it.
    var faceG = rigPart(el("g"), 50, 50); labelPart(faceG, "face");
    var screen = el("rect");
    attr(screen, "x", "147"); attr(screen, "y", "156"); attr(screen, "width", "139"); attr(screen, "height", "120"); attr(screen, "rx", "38");
    attr(screen, "fill", "url(#vmRobotScreenGr)");
    var rim = el("rect");
    attr(rim, "x", "147"); attr(rim, "y", "156"); attr(rim, "width", "139"); attr(rim, "height", "120"); attr(rim, "rx", "38");
    attr(rim, "fill", "none"); attr(rim, "stroke", COLORS.cyan); attr(rim, "stroke-width", "2"); attr(rim, "stroke-opacity", "0.18");
    var screenSheen = el("ellipse");
    attr(screenSheen, "cx", "216"); attr(screenSheen, "cy", "250"); attr(screenSheen, "rx", "52"); attr(screenSheen, "ry", "15");
    attr(screenSheen, "fill", COLORS.faceGlow); attr(screenSheen, "opacity", "0.8");
    faceG.appendChild(screen);
    faceG.appendChild(screenSheen);
    faceG.appendChild(rim);
    headG.appendChild(faceG);

    // Eyebrows — short glowing cyan arcs above the eyes on the screen.
    function browPart(id, cx, cy) {
      var g = rigPart(el("g"), 50, 50); labelPart(g, id);
      var p = el("path");
      attr(p, "d", "M" + (cx - 15) + " " + cy + " C" + (cx - 7) + " " + (cy - 5) + " " +
        (cx + 7) + " " + (cy - 5) + " " + (cx + 15) + " " + cy);
      attr(p, "fill", "none"); attr(p, "stroke", COLORS.ink);
      attr(p, "stroke-width", "5"); attr(p, "stroke-linecap", "round"); attr(p, "stroke-opacity", "0.9");
      g.appendChild(p);
      return g;
    }
    var leftBrow = browPart("leftEyebrow", 186, GEO.browY);
    var rightBrow = browPart("rightEyebrow", 247, GEO.browY);

    // Eyes — glowing cyan ovals on the screen; blink scales the group around
    // its center, pupils translate for gaze.
    function eyePart(id, cx) {
      var g = rigPart(el("g"), 50, 50); labelPart(g, id);
      var bloom = el("ellipse");
      attr(bloom, "cx", cx); attr(bloom, "cy", GEO.leftEye.y);
      attr(bloom, "rx", String(GEO.eyeW + 3)); attr(bloom, "ry", String(GEO.eyeH + 3));
      attr(bloom, "fill", COLORS.cyan); attr(bloom, "opacity", "0.18");
      var eye = el("ellipse");
      attr(eye, "cx", cx); attr(eye, "cy", GEO.leftEye.y);
      attr(eye, "rx", String(GEO.eyeW)); attr(eye, "ry", String(GEO.eyeH));
      attr(eye, "fill", "url(#vmRobotCyanGr)");
      var pupil = el("circle");
      attr(pupil, "class", "cloud-pupil");
      attr(pupil, "cx", cx); attr(pupil, "cy", GEO.leftEye.y);
      attr(pupil, "r", "4.6"); attr(pupil, "fill", "#032430");
      var hl = el("circle");
      attr(hl, "cx", String(cx - 4)); attr(hl, "cy", String(GEO.leftEye.y - 4.5)); attr(hl, "r", "2.6");
      attr(hl, "fill", "#FFFFFF"); attr(hl, "opacity", "0.95");
      g.appendChild(bloom);
      g.appendChild(eye);
      g.appendChild(pupil);
      g.appendChild(hl);
      return g;
    }
    var leftEye = eyePart("leftEye", GEO.leftEye.x);
    var rightEye = eyePart("rightEye", GEO.rightEye.x);

    // Mouth — glowing cyan smile on the screen. cloud_anim.js swaps this
    // path's `d` and data-expression for every facial expression.
    var mouthG = rigPart(el("g"), 50, 50); labelPart(mouthG, "mouth");
    var mouthPath = el("path");
    attr(mouthPath, "d", "M203 247 C209 253 223 253 229 247");
    attr(mouthPath, "fill", "none"); attr(mouthPath, "stroke", COLORS.ink);
    attr(mouthPath, "stroke-width", "4"); attr(mouthPath, "stroke-linecap", "round");
    attr(mouthPath, "data-expression", "neutral");
    mouthG.appendChild(mouthPath);

    headG.appendChild(leftBrow);
    headG.appendChild(rightBrow);
    headG.appendChild(leftEye);
    headG.appendChild(rightEye);
    headG.appendChild(mouthG);
    content.appendChild(headG);

    // ── Arms (short white capsules with blue round cartoon hands) ──────────
    // Pivots sit at the shoulder joints; hands are nested groups so they can
    // waggle independently on top of arm rotation.
    function armPart(id, armX, handCx, shoulderY) {
      var g = rigPart(el("g"), 50, 0); labelPart(g, id);
      var arm = el("rect");
      attr(arm, "x", String(armX)); attr(arm, "y", String(shoulderY));
      attr(arm, "width", "30"); attr(arm, "height", "82"); attr(arm, "rx", "15");
      attr(arm, "fill", "url(#vmRobotBodyGr)");
      attr(arm, "stroke", COLORS.limbDark); attr(arm, "stroke-width", "2");
      g.appendChild(arm);

      var handG = rigPart(el("g"), 50, 22); labelPart(handG, id === "leftArm" ? "leftHand" : "rightHand");
      var ball = el("circle");
      attr(ball, "cx", handCx); attr(ball, "cy", "442"); attr(ball, "r", "17");
      attr(ball, "fill", "url(#vmRobotBlueGr)");
      // Two little cartoon finger nubs.
      var fl = el("rect");
      attr(fl, "x", String(handCx - 15)); attr(fl, "y", "454"); attr(fl, "width", "10"); attr(fl, "height", "12"); attr(fl, "rx", "5");
      attr(fl, "fill", "#EAF1F5");
      var fr = el("rect");
      attr(fr, "x", String(handCx - 1)); attr(fr, "y", "454"); attr(fr, "width", "10"); attr(fr, "height", "12"); attr(fr, "rx", "5");
      attr(fr, "fill", "#EAF1F5");
      var knuckle = el("ellipse");
      attr(knuckle, "cx", handCx); attr(knuckle, "cy", "440"); attr(knuckle, "rx", "12"); attr(knuckle, "ry", "6");
      attr(knuckle, "fill", "#FFFFFF"); attr(knuckle, "opacity", "0.5");
      handG.appendChild(ball);
      handG.appendChild(fl);
      handG.appendChild(fr);
      handG.appendChild(knuckle);
      // Hands drift visually downward so the whole arm reads as one limb.
      g.appendChild(handG);
      return g;
    }
    var leftArm = armPart("leftArm", 135, 146, 352);
    var rightArm = armPart("rightArm", 268, 287, 352);
    content.appendChild(leftArm);
    content.appendChild(rightArm);
    svg.appendChild(content); // content lives in full companion-canvas space (after #defs)

    var parts = {
      root: svg,
      body: bodyG,
      head: headG,
      face: faceG,
      halo: haloG,
      lowerBody: lowerBodyG,
      leftEar: leftEar,
      rightEar: rightEar,
      leftEye: leftEye,
      rightEye: rightEye,
      leftEyebrow: leftBrow,
      rightEyebrow: rightBrow,
      mouth: mouthG,
      leftArm: leftArm,
      rightArm: rightArm,
      leftHand: leftArm.querySelector('[data-part="leftHand"]'),
      rightHand: rightArm.querySelector('[data-part="rightHand"]'),
      leftLeg: leftLeg,
      rightLeg: rightLeg,
      shadow: shadowG,
      nightMouth: mouthPath,
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
  // hide it the moment the rig takes over so the old cloud is never visible
  // behind the robot and there is exactly one visible companion.
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
    // Build + attach FIRST so the fallback image is only hidden once the robot
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
        head: part("head"),
        face: part("face"),
        halo: part("halo"),
        lowerBody: part("lowerBody"),
        leftEar: part("leftEar"),
        rightEar: part("rightEar"),
        leftEye: leftEye,
        rightEye: rightEye,
        leftEyebrow: part("leftEyebrow"),
        rightEyebrow: part("rightEyebrow"),
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
    d.style.width = "128px";
    d.style.height = "auto";
    d.style.aspectRatio = "433 / 577";
    d.style.pointerEvents = "auto";
    d.style.touchAction = "none";
    d.style.cursor = "grab";
    d.style.userSelect = "none";
    d.style.webkitUserDrag = "none";
    d.style.filter = "drop-shadow(0 10px 18px rgba(0,10,20,0.5))";

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
      try { mount(node); } catch (e) { }
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoBoot);
  } else {
    autoBoot();
  }
})();