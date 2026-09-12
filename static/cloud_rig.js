/* ValleyMind Cloud — faithful layered vector rig.
   ────────────────────────────────────────────────────
   The canonical character is static/cloud.png (a single flattened 433×577
   image). Because that asset cannot move its eyes, brows, mouth, arms or legs
   independently, this module builds a LAYERED SVG rig that faithfully redraws
   the exact Cloud character — same palette, proportions, silhouette and facial
   design, measured directly from cloud.png — as separately addressable parts:

       body | head | leftEyebrow | rightEyebrow | leftEye | rightEye |
       mouth | leftArm | rightArm | leftLeg | rightLeg | feet/shadow

   Each part is an <g> that the centralized animation engine
   (static/cloud_anim.js) can translate/rotate/scale independently via CSS
   transforms. The flattened PNG remains the source of truth and the static
   fallback for the non-animated companion avatar.

   This rig is a faithful vector recreation, NOT a redesign: it preserves the
   character's proportions, colors, outlines, facial design and recognizable
   silhouette so it is clearly still the SAME Cloud. To be honest: a flattened
   PNG cannot be independently animated; only this layered redraw can.

   Usage (internal):
       var rig = window.VMCloudRig.build(containerEl);
       rig.parts.leftEye  -> <g> DOM node
       rig.parts.mouth    -> <g> DOM node
       rig.root           -> container <div>
*/

(function () {
  "use strict";

  // Palette sampled directly from static/cloud.png.
  var COLORS = {
    headTop: "#E5EAE8",   // fluffy white cloud top (png ~229,234,230)
    headMid: "#D7DDDB",   // soft mid shadow on the fluff
    face:    "#788286",   // gray-teal face (png 120,130,131)
    faceLight:"#8B9597",  // lighter face highlight
    body:    "#9FA5A3",   // gray-teal torso (png 159,165,163)
    bodyDark:"#838B89",   // torso shading
    limb:    "#D4DADA",   // arms/hands (png 212,218,216)
    limbDark:"#A9B1B0",   // arm shading
    leg:     "#40464A",   // dark legs (png 65,70,74)
    legDark: "#2E3336",   // leg shading
    ink:     "#010409",   // eyes/brows (png ~1,4,9)
    mouthOpen:"#0B1012",  // dark mouth interior
    shadow:  "rgba(0,0,0,0.22)"
  };

  // Anchor / geometry constants (content space, x 0..328, y 0..261).
  // Derived from pixel measurements of cloud.png (content bbox x78..405, y185..446).
  var GEO = {
    // Face feature anchors (content coords).
    leftEye:  { x: 135, y: 90 },
    rightEye: { x: 162, y: 90 },
    eyeW: 13, eyeH: 13,
    browY: 57, browGap: 2,
    mouth: { x: 172, y: 106 },
    // Limb pivot anchors.
    leftShoulder:  { x: 96, y: 118 },
    rightShoulder: { x: 232, y: 118 },
    leftHip:       { x: 108, y: 176 },
    rightHip:      { x: 190, y: 176 }
  };

  function attr(el, name, val) { el.setAttribute(name, val); }
  function el(name) { return document.createElementNS("http://www.w3.org/2000/svg", name); }

  // Cloud-head silhouette represented as a soft lumpy dome.
  function headPath() {
    return "M34 30 " +
      "C34 12 46 0 66 0 " +
      "C66 0 84 -4 102 0 " +
      "C114 -10 140 -10 150 0 " +
      "C168 -8 196 -4 200 6 " +
      "C218 2 236 12 236 28 " +
      "C236 28 238 60 238 96 " +
      "C122 101 60 96 44 96 " +
      "C42 70 34 52 34 30 Z";
  }

  // Left arm pointing down at rest (pivot near shoulder).
  function armPath(side) {
    // side: "left" arm is on image-left (character's right), "right" arm on image-right.
    var base = side === "left"
      ? "M96 120 C78 124 64 138 62 156 C60 172 68 184 82 186 C96 188 106 178 104 164 C102 152 104 150 104 150 L112 150 L112 146 C112 146 104 120 96 120 Z"
      : "M232 120 C250 124 264 138 266 156 C268 172 260 184 246 186 C232 188 222 178 224 164 C226 152 224 150 224 150 L216 150 L216 146 C216 146 224 120 232 120 Z";
    return base;
  }

  function legPath(side) {
    // Pivot near the hip; the leg hangs down and the foot flares.
    if (side === "left") {
      return "M98 176 C88 200 80 224 78 244 C77 256 88 258 96 258 C108 258 120 254 122 244 " +
        "C124 232 120 220 118 208 C116 200 118 196 118 196 L104 196 C104 196 106 182 98 176 Z";
    }
    return "M180 176 C190 200 198 224 200 244 C201 256 190 258 182 258 C170 258 158 254 156 244 " +
      "C154 232 158 220 160 208 C162 200 160 196 160 196 L174 196 C174 196 172 182 180 176 Z";
  }

  // Helper: set the CSS pivot so cloud_anim.js can rotate/scale each part
  // around the correct joint (shoulder/hip/eye-centre) as a pure transform.
  function rigPart(g, xPct, yPct) {
    g.style.transformBox = "fill-box";
    g.style.transformOrigin = xPct + "% " + yPct + "%";
    g.style.willChange = "transform";
    return g;
  }

  function build() {
    var svg = el("svg");
    // The rig must overlay static/cloud.png exactly: the PNG canvas is
    // 433x577 and its character content lives in the bbox x78..405, y185..446
    // (measured from cloud.png). So the SVG claims the same canvas and all
    // content is wrapped in translate(78,185) — giving one visible Cloud where
    // the rig paints over (and can safely replace) the flattened image.
    attr(svg, "viewBox", "0 0 433 577");
    attr(svg, "preserveAspectRatio", "xMidYMid meet");
    attr(svg, "width", "100%");
    attr(svg, "height", "100%");
    attr(svg, "aria-hidden", "true");

    var content = el("g");
    attr(content, "transform", "translate(78 185)");

    // Shared linear gradients to softly shade the fluff and body.
    var defs = el("defs");

    var headG = el("linearGradient"); headG.id = "cloudHeadGr";
    attr(headG, "x1", "0"); attr(headG, "y1", "0"); attr(headG, "x2", "0"); attr(headG, "y2", "1");
    var hs1 = el("stop"); attr(hs1, "offset", "0%"); attr(hs1, "stop-color", COLORS.headTop);
    var hs2 = el("stop"); attr(hs2, "offset", "62%"); attr(hs2, "stop-color", COLORS.headMid);
    var hs3 = el("stop"); attr(hs3, "offset", "100%"); attr(hs3, "stop-color", COLORS.face);
    headG.appendChild(hs1); headG.appendChild(hs2); headG.appendChild(hs3);
    defs.appendChild(headG);

    var bodyG = el("linearGradient"); bodyG.id = "cloudBodyGr";
    attr(bodyG, "x1", "0"); attr(bodyG, "y1", "0"); attr(bodyG, "x2", "0"); attr(bodyG, "y2", "1");
    var bs1 = el("stop"); attr(bs1, "offset", "0%"); attr(bs1, "stop-color", COLORS.bodyDark);
    var bs2 = el("stop"); attr(bs2, "offset", "55%"); attr(bs2, "stop-color", COLORS.body);
    var bs3 = el("stop"); attr(bs3, "offset", "100%"); attr(bs3, "stop-color", COLORS.bodyDark);
    bodyG.appendChild(bs1); bodyG.appendChild(bs2); bodyG.appendChild(bs3);
    defs.appendChild(bodyG);

    var legG = el("linearGradient"); legG.id = "cloudLegGr";
    attr(legG, "x1", "0"); attr(legG, "y1", "0"); attr(legG, "x2", "0"); attr(legG, "y2", "1");
    var ls1 = el("stop"); attr(ls1, "offset", "0%"); attr(ls1, "stop-color", COLORS.legDark);
    var ls2 = el("stop"); attr(ls2, "offset", "100%"); attr(ls2, "stop-color", COLORS.leg);
    legG.appendChild(ls1); legG.appendChild(ls2);
    defs.appendChild(legG);
    svg.appendChild(defs);

    // ── Ground shadow (under feet) ────────────────────────────────────────
    var shadow = el("ellipse");
    attr(shadow, "cx", "164"); attr(shadow, "cy", "258"); attr(shadow, "rx", "110"); attr(shadow, "ry", "7");
    attr(shadow, "fill", COLORS.shadow);
    var shadowG = el("g"); attr(shadowG, "data-part", "shadow");
    shadowG.appendChild(shadow);
    content.appendChild(shadowG);

    // ── Body (torso) ───────────────────────────────────────────────────────
    var bodyPath = el("path");
    attr(bodyPath, "d",
      "M118 104 C118 104 96 168 96 176 " +
      "C86 192 84 200 92 206 L236 206 " +
      "C244 200 242 192 232 176 " +
      "C232 168 210 104 210 104 " +
      "C196 116 150 116 118 104 Z");
    attr(bodyPath, "fill", "url(#cloudBodyGr)");
    var bodyG = rigPart(el("g"), 50, 30); attr(bodyG, "data-part", "body");
    bodyG.appendChild(bodyPath);
    content.appendChild(bodyG);

    // ── Legs (boots) ───────────────────────────────────────────────────────
    var leftLegG = rigPart(el("g"), 50, 68); attr(leftLegG, "data-part", "leftLeg");
    var leftLegPath = el("path");
    attr(leftLegPath, "d", legPath("left"));
    attr(leftLegPath, "fill", "url(#cloudLegGr)");
    leftLegG.appendChild(leftLegPath);
    content.appendChild(leftLegG);

    var rightLegG = rigPart(el("g"), 50, 68); attr(rightLegG, "data-part", "rightLeg");
    var rightLegPath = el("path");
    attr(rightLegPath, "d", legPath("right"));
    attr(rightLegPath, "fill", "url(#cloudLegGr)");
    rightLegG.appendChild(rightLegPath);
    content.appendChild(rightLegG);

    // ── Arms (blob arms with hands) ───────────────────────────────────────
    var leftArmG = rigPart(el("g"), 50, 72); attr(leftArmG, "data-part", "leftArm");
    var leftArm = el("path");
    attr(leftArm, "d", armPath("left"));
    attr(leftArm, "fill", "url(#cloudLegGr)");
    attr(leftArm, "stroke", COLORS.limbDark); attr(leftArm, "stroke-width", "2");
    leftArmG.appendChild(leftArm);
    content.appendChild(leftArmG);

    var rightArmG = rigPart(el("g"), 50, 72); attr(rightArmG, "data-part", "rightArm");
    var rightArm = el("path");
    attr(rightArm, "d", armPath("right"));
    attr(rightArm, "fill", "url(#cloudLegGr)");
    attr(rightArm, "stroke", COLORS.limbDark); attr(rightArm, "stroke-width", "2");
    rightArmG.appendChild(rightArm);
    content.appendChild(rightArmG);

    // ── Head (fluff) ──────────────────────────────────────────────────────
    var head = el("path");
    attr(head, "d", headPath());
    attr(head, "fill", "url(#cloudHeadGr)");
    var headG = rigPart(el("g"), 50, 50); attr(headG, "data-part", "head");
    headG.appendChild(head);
    content.appendChild(headG);

    // ── Eyebrows ──────────────────────────────────────────────────────────
    // A soft dark arc sitting just above each eye.
    function browPart(id, cx, spanY) {
      var g = rigPart(el("g"), 50, 50); attr(g, "data-part", id);
      var p = el("path");
      attr(p, "d", "M" + (cx - 14) + " " + spanY + " C" + (cx - 6) + " " + (spanY - 4) + " " +
        (cx + 6) + " " + (spanY - 4) + " " + (cx + 14) + " " + spanY);
      attr(p, "fill", "none"); attr(p, "stroke", COLORS.ink);
      attr(p, "stroke-width", "3.5"); attr(p, "stroke-linecap", "round");
      g.appendChild(p);
      return g;
    }
    var leftBrow = browPart("leftEyebrow", GEO.leftEye.x, GEO.browY);
    var rightBrow = browPart("rightEyebrow", GEO.rightEye.x, GEO.browY);
    content.appendChild(leftBrow);
    content.appendChild(rightBrow);

    // ── Eyes ──────────────────────────────────────────────────────────────
    // Each eye is a group holding a dark pupil so "looking" can translate the
    // pupil within a fixed socket without sliding the whole eye around.
    function eyePart(id, cx) {
      var g = rigPart(el("g"), 50, 50); attr(g, "data-part", id);
      var socket = el("ellipse");
      attr(socket, "cx", cx); attr(socket, "cy", GEO.leftEye.y + 4);
      attr(socket, "rx", GEO.eyeW); attr(socket, "ry", GEO.eyeH);
      attr(socket, "fill", "#ECEFF0"); attr(socket, "stroke", "#9AA3A5"); attr(socket, "stroke-width", "1.5");
      var pupil = el("circle");
      attr(pupil, "class", "cloud-pupil");
      attr(pupil, "cx", cx); attr(pupil, "cy", GEO.leftEye.y + 4);
      attr(pupil, "r", 4.6); attr(pupil, "fill", COLORS.ink);
      var hl = el("circle");
      attr(hl, "cx", cx - 1.6); attr(hl, "cy", GEO.leftEye.y + 2); attr(hl, "r", 1.5); attr(hl, "fill", "#ffffff");
      g.appendChild(socket);
      g.appendChild(pupil);
      g.appendChild(hl);
      return g;
    }
    var leftEye = eyePart("leftEye", GEO.leftEye.x);
    var rightEye = eyePart("rightEye", GEO.rightEye.x);
    content.appendChild(leftEye);
    content.appendChild(rightEye);

    // ── Mouth ─────────────────────────────────────────────────────────────
    // By default a small neutral smile; cloud_anim.js swaps expressions.
    var mouthG = rigPart(el("g"), 50, 50); attr(mouthG, "data-part", "mouth");
    var mouthPath = el("path");
    attr(mouthPath, "d",
      "M" + (GEO.mouth.x - 5) + " " + GEO.mouth.y +
      " C" + (GEO.mouth.x - 2) + " " + (GEO.mouth.y + 3) +
      " " + (GEO.mouth.x + 2) + " " + (GEO.mouth.y + 3) +
      " " + (GEO.mouth.x + 5) + " " + GEO.mouth.y);
    attr(mouthPath, "fill", "none"); attr(mouthPath, "stroke", COLORS.ink);
    attr(mouthPath, "stroke-width", "2.5"); attr(mouthPath, "stroke-linecap", "round");
    attr(mouthPath, "data-expression", "neutral");
    mouthG.appendChild(mouthPath);
    content.appendChild(mouthG);
    svg.appendChild(content); // content lives in PNG-canvas space (after #defs)

    var parts = {
      root: svg,
      body: bodyG,
      head: headG,
      leftEye: leftEye,
      rightEye: rightEye,
      leftEyebrow: leftBrow,
      rightEyebrow: rightBrow,
      mouth: mouthG,
      leftArm: leftArmG,
      rightArm: rightArmG,
      leftLeg: leftLegG,
      rightLeg: rightLegG,
      shadow: shadowG,
      nightMouth: mouthPath,
      leftPupil: leftEye.querySelector(".cloud-pupil"),
      rightPupil: rightEye.querySelector(".cloud-pupil")
    };

    return { parts: parts, GEO: GEO };
  }

  // ── Mounting (used by index.html static markup and cloud.js) ────────────
  // The character container (#vmCloudCharacter) is a small fixed <div>
  // holding the flattened PNG as a static fallback plus this layered rig in a
  // pointer-transparent overlay. `mount` builds the SVG into an existing
  // container once and returns the runnable rig (parts + noted pivots) so the
  // animation engine can drive it.
  function findRigMount(container) {
    if (!container) return null;
    var m = container.querySelector && container.querySelector(".vmcloud-rig-mount");
    return m || container;
  }

  function alreadyRooted(host) {
    var roots = host.querySelectorAll && host.querySelectorAll("svg.vmcloud-rig-svg");
    return !!(roots && roots.length);
  }

  // The rig overlay is the single visible Cloud once it is live. The flattened
  // PNG stays in the DOM only as the no-JS/loading fallback — hide it the
  // moment the rig takes over so there is never a frozen PNG over the moving
  // Cloud.
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
    hideFallback(container);
    if (alreadyRooted(host)) {
      // Already mounted (auto-boot or cloud.js raced ahead): return it.
      return collectRig(host.querySelector("svg.vmcloud-rig-svg"));
    }
    host.setAttribute("data-vm-rig", "1");
    var built = build();
    attr(built.parts.root, "class", "vmcloud-rig-svg");
    host.appendChild(built.parts.root);
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
    var pupil = function (eye) {
      return eye && eye.querySelector ? (eye.querySelector(".cloud-pupil") || null) : null;
    };
    return {
      parts: {
        root: svg,
        body: part("body"),
        head: part("head"),
        leftEye: leftEye,
        rightEye: rightEye,
        leftEyebrow: part("leftEyebrow"),
        rightEyebrow: part("rightEyebrow"),
        mouth: part("mouth"),
        leftArm: part("leftArm"),
        rightArm: part("rightArm"),
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
    d.style.width = "96px";
    d.style.height = "auto";
    d.style.aspectRatio = "433 / 577";
    d.style.pointerEvents = "auto";
    d.style.touchAction = "none";
    d.style.cursor = "grab";
    d.style.userSelect = "none";
    d.style.webkitUserDrag = "none";
    d.style.filter = "drop-shadow(0 8px 14px rgba(0,0,0,0.45))";

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
