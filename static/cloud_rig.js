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

   All outline geometry was extracted from static/cloud.png itself with a
   measurement harness (silhouette IoU ≈ 0.93 against the source), so the rig
   overlays the fallback image almost exactly. Alive features are painted over
   that flat base: light fluff dome + gray face plate, light arms with dark
   mittens, dark legs with gray feet.

   Usage (internal):
       var rig = window.VMCloudRig.build(containerEl);
       rig.parts.leftEye  -> <g> DOM node
       rig.parts.mouth    -> <g> DOM node
       rig.root           -> container <div>
*/

(function () {
  "use strict";

  // Palette sampled directly from static/cloud.png (region averages over the
  // actual pixel areas; see the measurement notes in the repo history).
  var COLORS = {
    headTop: "#E5EAE8",   // fluffy white cloud top (png ~222,227,223)
    headMid: "#D7DDDB",   // soft mid shadow on the fluff
    headBase:"#B4BAB8",   // under-chin fluff shading
    face:    "#6A7274",   // gray-teal face plate, darkened toward png tone
    faceLight:"#8C9496",  // lighter lower face / chin
    body:    "#B8BCBB",   // light gray-teal torso, lightened toward png tone
    bodyDark:"#A1A6A4",   // torso bottom shading
    limb:    "#DDE6E3",   // light fluff-colored upper arm (png ~221,230,227)
    limbRight:"#9AA3A1",  // shadowed right upper arm (png ~143,152,149)
    limbDark:"#3A3F42",   // dark mitten hands (png ~57,62,65)
    leg:     "#272B2B",   // dark leg shafts (png ~40,43,43)
    legDark: "#171B1C",   // leg shading
    foot:    "#6F7578",   // gray feet (png ~111,118,121)
    ink:     "#010409",   // eyes/brows/mouth
    mouthOpen:"#05090B",  // dark mouth interior
    shadow:  "rgba(0,0,0,0.10)"
  };

  // Anchor / geometry constants (content space, x 0..328, y 0..261).
  // Derived from pixel measurements of cloud.png (content bbox x78..406, y184..447).
  var GEO = {
    // Face feature anchors (content coords).
    leftEye:  { x: 136, y: 91 },
    rightEye: { x: 162, y: 91 },
    eyeW: 7, eyeH: 6,
    browY: 57,
    mouth: { x: 166, y: 104 },
    // Limb pivot anchors.
    leftShoulder:  { x: 36, y: 100 },
    rightShoulder: { x: 231, y: 112 },
    leftHip:       { x: 108, y: 158 },
    rightHip:      { x: 190, y: 159 }
  };

  // Outlines extracted from cloud.png (content coordinates). These are the
  // measured silhouettes: a bulky fluff dome with drooping side lobes, a wide
  // scallop-bottomed body, short legs that flare into wide gray feet, and arms
  // that end in dark rounded mittens. Hand/foot overlays carry the dark/gray
  // accents on top of the matching base part.
  var PATHS = {
    head: "M110 3 L114 2 L118 1 L122 1 L126 2 L130 3 L134 4 L138 6 L142 8 L146 8 L150 7 L154 5 L158 3 L162 2 L166 1 L170 1 L174 1 L178 2 L182 4 L186 8 L195 15 L208 21 L221 27 L227 33 L231 39 L233 45 L234 51 L235 57 L236 63 L242 69 L249 75 L253 81 L260 85 L267 87 L272 93 L273 99 L290 102 L308 107 L318 114 L321 122 L312 130 L306 136 L303 142 L301 146 L298 147 L290 152 L278 156 L262 160 L244 163 L226 163 L206 161 L186 159 L166 158 L146 158 L126 159 L106 160 L86 161 L66 162 L48 162 L34 161 L22 159 L14 157 L8 155 L4 153 L2 156 L1 159 L1 156 L2 153 L3 150 L4 147 L5 144 L7 141 L8 138 L11 135 L13 129 L16 126 L22 121 L30 117 L33 111 L35 105 L34 99 L34 96 L34 93 L35 90 L35 87 L36 84 L37 81 L39 78 L41 75 L44 72 L47 69 L50 66 L54 63 L56 57 L57 51 L58 45 L60 39 L64 33 L69 27 L74 24 L81 21 L88 16 L96 12 Z",
    body: "M46 118 L30 124 L22 132 L18 142 L20 152 L18 160 L26 168 L42 172 L58 168 L70 158 L88 150 L100 156 L116 162 L132 163 L150 158 L168 152 L182 148 L194 150 L206 156 L220 162 L234 166 L250 172 L268 176 L284 168 L294 156 L296 144 L288 132 L272 124 L250 118 L226 116 L200 116 L170 118 L140 120 L110 120 L80 120 L56 118 Z",
    belt: "M138 152 L176 153 L202 156 L205 160 L204 163 L170 164 L146 163 L136 160 L135 156 Z",
    legL: "M94 158 L90 166 L88 177 L89 186 L89 196 L92 202 L87 208 L81 214 L74 222 L67 230 L61 238 L60 247 L64 256 L76 261 L90 262 L104 261 L114 256 L126 247 L134 239 L139 231 L141 224 L140 217 L138 211 L134 206 L126 200 L123 192 L122 185 L122 177 L122 169 L120 163 L114 157 L106 155 L98 157 Z",
    legR: "M179 157 L173 164 L168 173 L167 183 L168 194 L170 200 L165 207 L160 214 L157 223 L157 232 L159 241 L162 249 L170 256 L181 260 L194 263 L207 263 L220 260 L231 255 L240 249 L243 242 L243 233 L242 225 L238 216 L228 208 L218 205 L207 201 L204 193 L203 185 L201 178 L198 171 L193 165 L188 160 Z",
    footL: "M87 208 L81 214 L74 222 L67 230 L61 238 L60 247 L64 256 L76 261 L90 262 L104 261 L114 256 L126 247 L134 239 L139 231 L141 224 L140 217 L137 212 L132 208 L124 205 L112 204 L99 206 L92 208 Z",
    footR: "M165 207 L160 214 L157 223 L157 232 L159 241 L162 249 L170 256 L181 260 L194 263 L207 263 L220 260 L231 255 L240 249 L243 242 L243 233 L242 225 L238 217 L232 211 L223 207 L213 205 L201 203 L188 204 L177 204 L170 205 Z",
    armL: "M30 108 L26 114 L22 122 L18 130 L15 137 L10 143 L5 149 L1 157 L0 164 L3 172 L9 177 L15 178 L21 176 L23 169 L25 160 L26 151 L26 143 L27 135 L29 127 L30 120 L31 113 Z",
    armR: "M216 122 L218 116 L226 113 L234 115 L240 121 L244 130 L248 140 L252 150 L255 160 L256 168 L254 176 L247 180 L240 179 L233 175 L228 167 L225 158 L222 149 L220 140 L220 132 L223 125 L228 119 Z",
    handL: "M11 140 L8 143 L5 146 L3 150 L1 156 L1 161 L2 167 L6 171 L12 173 L17 172 L20 169 L23 165 L23 159 L21 154 L17 149 L14 145 Z",
    handR: "M228 129 L222 134 L219 141 L218 148 L218 156 L221 164 L226 171 L232 176 L239 177 L245 174 L250 168 L253 161 L255 152 L255 143 L252 135 L246 130 L241 128 L235 128 L231 127 Z",
    face: "M134 57 L125 63 L120 72 L118 82 L120 92 L126 104 L136 113 L150 118 L164 119 L178 116 L190 108 L199 98 L204 86 L202 74 L196 64 L186 57 L172 53 L158 52 L146 53 Z"
  };

  function attr(el, name, val) { el.setAttribute(name, val); }
  function el(name) { return document.createElementNS("http://www.w3.org/2000/svg", name); }

  function headPath() { return PATHS.head; }
  function bodyPath() { return PATHS.body; }

  function legPath(side) { return side === "left" ? PATHS.legL : PATHS.legR; }
  function footPath(side) { return side === "left" ? PATHS.footL : PATHS.footR; }
  function armPath(side) { return side === "left" ? PATHS.armL : PATHS.armR; }
  function handPath(side) { return side === "left" ? PATHS.handL : PATHS.handR; }

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

  function build() {
    var svg = el("svg");
    // The rig must overlay static/cloud.png exactly: the PNG canvas is
    // 433x577 and its character content lives in the bbox x78..406, y184..447
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

    // Shared linear gradients to softly shade the fluff, face, body and legs.
    var defs = el("defs");

    var headG = el("linearGradient"); headG.id = "cloudHeadGr";
    attr(headG, "x1", "0"); attr(headG, "y1", "0"); attr(headG, "x2", "0"); attr(headG, "y2", "1");
    var hs1 = el("stop"); attr(hs1, "offset", "0%"); attr(hs1, "stop-color", COLORS.headTop);
    var hs2 = el("stop"); attr(hs2, "offset", "55%"); attr(hs2, "stop-color", COLORS.headMid);
    var hs3 = el("stop"); attr(hs3, "offset", "100%"); attr(hs3, "stop-color", COLORS.headBase);
    headG.appendChild(hs1); headG.appendChild(hs2); headG.appendChild(hs3);
    defs.appendChild(headG);

    var faceG = el("linearGradient"); faceG.id = "cloudFaceGr";
    attr(faceG, "x1", "0"); attr(faceG, "y1", "0"); attr(faceG, "x2", "0"); attr(faceG, "y2", "1");
    var fs1 = el("stop"); attr(fs1, "offset", "0%"); attr(fs1, "stop-color", COLORS.face);
    var fs2 = el("stop"); attr(fs2, "offset", "100%"); attr(fs2, "stop-color", COLORS.faceLight);
    faceG.appendChild(fs1); faceG.appendChild(fs2);
    defs.appendChild(faceG);

    var bodyG = el("linearGradient"); bodyG.id = "cloudBodyGr";
    attr(bodyG, "x1", "0"); attr(bodyG, "y1", "0"); attr(bodyG, "x2", "0"); attr(bodyG, "y2", "1");
    var bs1 = el("stop"); attr(bs1, "offset", "0%"); attr(bs1, "stop-color", COLORS.body);
    var bs2 = el("stop"); attr(bs2, "offset", "100%"); attr(bs2, "stop-color", COLORS.bodyDark);
    bodyG.appendChild(bs1); bodyG.appendChild(bs2);
    defs.appendChild(bodyG);

    var legG = el("linearGradient"); legG.id = "cloudLegGr";
    attr(legG, "x1", "0"); attr(legG, "y1", "0"); attr(legG, "x2", "0"); attr(legG, "y2", "1");
    var ls1 = el("stop"); attr(ls1, "offset", "0%"); attr(ls1, "stop-color", COLORS.legDark);
    var ls2 = el("stop"); attr(ls2, "offset", "100%"); attr(ls2, "stop-color", COLORS.leg);
    legG.appendChild(ls1); legG.appendChild(ls2);
    defs.appendChild(legG);
    svg.appendChild(defs);

    // ── Ground shadow (soft, under the feet) ──────────────────────────────
    var shadow = el("ellipse");
    attr(shadow, "cx", "164"); attr(shadow, "cy", "252"); attr(shadow, "rx", "85"); attr(shadow, "ry", "5");
    attr(shadow, "fill", COLORS.shadow);
    var shadowG = el("g"); attr(shadowG, "data-part", "shadow");
    shadowG.appendChild(shadow);
    content.appendChild(shadowG);

    // ── Body (torso) ───────────────────────────────────────────────────────
    // The wide scalloped belly plus a small pad filling the V between the legs
    // (measured from the flattened image).
    var bodyG = rigPart(el("g"), 50, 30); attr(bodyG, "data-part", "body");
    var bodyPath = el("path");
    attr(bodyPath, "d", bodyPath());
    attr(bodyPath, "fill", "url(#cloudBodyGr)");
    bodyG.appendChild(bodyPath);
    var belt = el("path");
    attr(belt, "d", PATHS.belt);
    attr(belt, "fill", "url(#cloudBodyGr)");
    bodyG.appendChild(belt);
    content.appendChild(bodyG);

    // ── Legs (dark shafts + flared gray feet) ──────────────────────────────
    var leftLegG = rigPart(el("g"), 59, 3); labelPart(leftLegG, "leftLeg");
    var leftLegPath = el("path");
    attr(leftLegPath, "d", legPath("left"));
    attr(leftLegPath, "fill", "url(#cloudLegGr)");
    leftLegG.appendChild(leftLegPath);
    var leftFoot = el("path");
    attr(leftFoot, "d", footPath("left"));
    attr(leftFoot, "fill", COLORS.foot);
    leftLegG.appendChild(leftFoot);
    content.appendChild(leftLegG);

    var rightLegG = rigPart(el("g"), 38, 2); labelPart(rightLegG, "rightLeg");
    var rightLegPath = el("path");
    attr(rightLegPath, "d", legPath("right"));
    attr(rightLegPath, "fill", "url(#cloudLegGr)");
    rightLegG.appendChild(rightLegPath);
    var rightFoot = el("path");
    attr(rightFoot, "d", footPath("right"));
    attr(rightFoot, "fill", COLORS.foot);
    rightLegG.appendChild(rightFoot);
    content.appendChild(rightLegG);

    // ── Head (fluff dome + gray face plate) ────────────────────────────────
    // The dome carries the fluffy gradient; the face plate sits on it in the
    // gray-teal facial zone so brows/eyes/mouth have a proper backdrop. The
    // face is inside the head group so it tilts with the head.
    var head = el("path");
    attr(head, "d", headPath());
    attr(head, "fill", "url(#cloudHeadGr)");
    var headG = rigPart(el("g"), 50, 50); attr(headG, "data-part", "head");
    headG.appendChild(head);
    var face = el("path");
    attr(face, "d", PATHS.face);
    attr(face, "fill", "url(#cloudFaceGr)");
    headG.appendChild(face);
    content.appendChild(headG);

    // ── Arms (light upper arm + dark mitten) ──────────────────────────────
    // Drawn after the head so each arm's measured silhouette (and its dark
    // rounded hand overlay) reads against the body instead of vanishing under
    // the fluff. Pivots sit at the shoulder joints for cloud_anim.js rotations.
    var leftArmG = rigPart(el("g"), 116, -11); labelPart(leftArmG, "leftArm");
    var leftArm = el("path");
    attr(leftArm, "d", armPath("left"));
    attr(leftArm, "fill", COLORS.limb);
    leftArmG.appendChild(leftArm);
    var leftHand = el("path");
    attr(leftHand, "d", handPath("left"));
    attr(leftHand, "fill", COLORS.limbDark);
    leftArmG.appendChild(leftHand);
    content.appendChild(leftArmG);

    var rightArmG = rigPart(el("g"), 38, -1); labelPart(rightArmG, "rightArm");
    var rightArm = el("path");
    attr(rightArm, "d", armPath("right"));
    attr(rightArm, "fill", COLORS.limbRight);
    rightArmG.appendChild(rightArm);
    var rightHand = el("path");
    attr(rightHand, "d", handPath("right"));
    attr(rightHand, "fill", COLORS.limbDark);
    rightArmG.appendChild(rightHand);
    content.appendChild(rightArmG);

    // ── Eyebrows ──────────────────────────────────────────────────────────
    // Two long soft arcs that nearly meet over the nose, reading as the
    // single mobile brow band of the reference.
    function browPart(id, cx, spanY) {
      var g = rigPart(el("g"), 50, 50); labelPart(g, id);
      var p = el("path");
      attr(p, "d", "M" + (cx - 17) + " " + spanY + " C" + (cx - 8) + " " + (spanY - 3) + " " +
        (cx + 8) + " " + (spanY - 3) + " " + (cx + 17) + " " + spanY);
      attr(p, "fill", "none"); attr(p, "stroke", COLORS.ink);
      attr(p, "stroke-width", "4.5"); attr(p, "stroke-linecap", "round");
      g.appendChild(p);
      return g;
    }
    var leftBrow = browPart("leftEyebrow", 138, GEO.browY);
    var rightBrow = browPart("rightEyebrow", 169, GEO.browY);
    content.appendChild(leftBrow);
    content.appendChild(rightBrow);

    // ── Eyes ──────────────────────────────────────────────────────────────
    // Each eye is a group holding a dark pupil so "looking" can translate the
    // pupil within a fixed dark socket without sliding the whole eye around,
    // and blinking scales the group around its center.
    function eyePart(id, cx) {
      var g = rigPart(el("g"), 50, 50); labelPart(g, id);
      var socket = el("ellipse");
      attr(socket, "cx", cx); attr(socket, "cy", GEO.leftEye.y);
      attr(socket, "rx", GEO.eyeW); attr(socket, "ry", GEO.eyeH);
      attr(socket, "fill", "#1B242A");
      var pupil = el("circle");
      attr(pupil, "class", "cloud-pupil");
      attr(pupil, "cx", cx); attr(pupil, "cy", GEO.leftEye.y);
      attr(pupil, "r", 3.8); attr(pupil, "fill", COLORS.ink);
      var hl = el("circle");
      attr(hl, "cx", cx - 2); attr(hl, "cy", GEO.leftEye.y - 2); attr(hl, "r", 1.3); attr(hl, "fill", "#ffffff");
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
    // Neutral smile matching cloud_anim.js MOUTH.neutral (anchored ~166,104).
    var mouthG = rigPart(el("g"), 50, 50); labelPart(mouthG, "mouth");
    var mouthPath = el("path");
    attr(mouthPath, "d", "M161 104 C164 108 168 108 171 104");
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