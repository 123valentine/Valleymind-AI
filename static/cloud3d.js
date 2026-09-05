(function () {
  "use strict";

  var THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";
  var STAGE_ID = "vmCloudStage";
  var STATUS_ID = "vmCloud3DStatus";
  var FALLBACK_ORB_ID = "vmCloudOrb";

  var EMOTIONS = [
    "neutral", "happy", "excited", "thinking", "curious", "concerned", "sad",
    "frustrated", "angry", "surprised", "confused", "focused", "listening", "speaking"
  ];
  var STATUSES = [
    "idle", "listening", "thinking", "speaking", "helping", "learning", "observing", "guiding"
  ];

  var _threeCache = null;
  var _threePromise = null;

  function loadThree() {
    if (_threeCache) return Promise.resolve(_threeCache);
    if (_threePromise) return _threePromise;
    _threePromise = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = THREE_URL;
      s.async = true;
      s.onload = function () {
        if (window.THREE) {
          _threeCache = window.THREE;
          resolve(window.THREE);
        } else {
          reject(new Error("THREE global missing"));
        }
      };
      s.onerror = function () { reject(new Error("three.js failed to load")); };
      (document.head || document.documentElement).appendChild(s);
    });
    _threePromise.catch(function () { _threePromise = null; });
    return _threePromise;
  }

  function hexToRgb(hex) {
    var h = String(hex || "").replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return { r: 234, g: 247, b: 255 };
    var n = parseInt(h, 16);
    if (isNaN(n)) return { r: 234, g: 247, b: 255 };
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }

  function mixColor(a, b, t) {
    var ca = hexToRgb(a), cb = hexToRgb(b);
    var r = Math.round(ca.r + (cb.r - ca.r) * t);
    var g = Math.round(ca.g + (cb.g - ca.g) * t);
    var bl = Math.round(ca.b + (cb.b - ca.b) * t);
    return (r << 16) | (g << 8) | bl;
  }

  function intColor(c) { return typeof c === "number" ? c : parseInt(String(c).replace("#", ""), 16); }

  var BASE_BODY = 0xeaf7ff;
  var BASE_ACCENT = 0x00d4ff;
  var DARK_EDGE = 0x0a1520;

  var EMOTION_RIG = {
    neutral:    { body: 0xeaf7ff, accent: 0x00d4ff, glow: 0.42, bright: 1.0,  float: 1.0, breath: 1.0, eyeFocus: 0.4, lid: 0.0, squint: 0.0, browH: 0.0,  browTilt: 0.0,  pupScale: 1.0, mouth: "neutral",   mouthOpen: 0.0, arm: 0 },
    happy:      { body: 0xf4ffff, accent: 0x2ee6b0, glow: 0.62, bright: 1.12, float: 1.25, breath: 1.3, eyeFocus: 0.5, lid: 0.0, squint: 0.75, browH: 0.04, browTilt: 0.0,  pupScale: 0.9, mouth: "smile",     mouthOpen: 0.05, arm: 1 },
    excited:    { body: 0xf6ffff, accent: 0xffcf5c, glow: 0.78, bright: 1.25, float: 1.5, breath: 1.5, eyeFocus: 0.6, lid: 0.0, squint: 0.55, browH: 0.06, browTilt: 0.0,  pupScale: 0.85, mouth: "smile",     mouthOpen: 0.35, arm: 4 },
    thinking:   { body: 0xdfeef9, accent: 0x2fd0ff, glow: 0.5,  bright: 0.96, float: 0.7, breath: 0.8, eyeFocus: 0.85, lid: 0.0, squint: 0.2, browH: 0.05, browTilt: 0.14, pupScale: 0.9, mouth: "thin",      mouthOpen: 0.0, arm: 2 },
    curious:    { body: 0xe5f1fa, accent: 0x38b6ff, glow: 0.55, bright: 1.05, float: 0.95, breath: 0.9, eyeFocus: 0.9, lid: 0.0, squint: 0.0, browH: 0.05, browTilt: -0.1,  pupScale: 0.92, mouth: "open",      mouthOpen: 0.28, arm: 4 },
    concerned:  { body: 0xddeaf2, accent: 0x5f9fc9, glow: 0.34, bright: 0.9,  float: 0.7, breath: 0.85, eyeFocus: 0.9, lid: 0.35, squint: 0.3, browH: 0.06, browTilt: 0.12, pupScale: 0.95, mouth: "concern",   mouthOpen: 0.0, arm: 2 },
    sad:        { body: 0xcdd9e2, accent: 0x5d8fa8, glow: 0.24, bright: 0.8,  float: 0.5, breath: 0.65, eyeFocus: 0.5, lid: 0.55, squint: 0.1, browH: 0.06, browTilt: 0.16, pupScale: 0.95, mouth: "sad",       mouthOpen: 0.0, arm: 6 },
    frustrated: { body: 0xf2e9e2, accent: 0xff8a5b, glow: 0.5,  bright: 1.05, float: 0.75, breath: 0.9, eyeFocus: 1.0, lid: 0.4, squint: 0.5, browH: -0.08, browTilt: -0.16, pupScale: 0.88, mouth: "frustrated", mouthOpen: 0.0, arm: 5 },
    angry:      { body: 0xf4e4dc, accent: 0xff6b4a, glow: 0.62, bright: 1.12, float: 0.85, breath: 1.1, eyeFocus: 1.0, lid: 0.45, squint: 0.65, browH: -0.1, browTilt: -0.2, pupScale: 0.85, mouth: "frustrated", mouthOpen: 0.12, arm: 5 },
    surprised:  { body: 0xf6fdf3, accent: 0x7fdcff, glow: 0.72, bright: 1.25, float: 1.2, breath: 1.4, eyeFocus: 1.0, lid: 0.0, squint: 0.0, browH: 0.09, browTilt: 0.0,  pupScale: 0.5, mouth: "surprised", mouthOpen: 0.95, arm: 3 },
    confused:   { body: 0xe2ecf4, accent: 0x8aa8ff, glow: 0.4,  bright: 0.92, float: 0.8, breath: 0.85, eyeFocus: 0.8, lid: 0.15, squint: 0.2, browH: 0.07, browTilt: -0.18, pupScale: 0.9, mouth: "confused", mouthOpen: 0.15, arm: 7 },
    focused:    { body: 0xddf1fb, accent: 0x2fb8e8, glow: 0.48, bright: 1.0,  float: 0.55, breath: 0.8, eyeFocus: 0.95, lid: 0.2, squint: 0.3, browH: 0.0,  browTilt: 0.0,  pupScale: 0.94, mouth: "neutral",   mouthOpen: 0.0, arm: 0 },
    listening:  { body: 0xe6f6fa, accent: 0x2fd4c8, glow: 0.5,  bright: 1.05, float: 0.8, breath: 0.9, eyeFocus: 1.0, lid: 0.0, squint: 0.1, browH: 0.03, browTilt: 0.0,  pupScale: 0.92, mouth: "neutral",   mouthOpen: 0.0, arm: 8 },
    speaking:   { body: 0xeaf7ff, accent: 0x00d4ff, glow: 0.5,  bright: 1.08, float: 0.95, breath: 1.25, eyeFocus: 0.9, lid: 0.0, squint: 0.15, browH: 0.03, browTilt: 0.0,  pupScale: 0.92, mouth: "speak",     mouthOpen: 0.4, arm: 9 }
  };

  var STATUS_OVERRIDE = {
    listening: { eyeFocus: 1.0, arm: 8, mouth: "neutral", mouthOpen: 0.0 },
    thinking: { eyeFocus: 0.9, arm: 2 },
    speaking: { mouth: "speak", arm: 9, eyeFocus: 0.92, float: 1.0, breath: 1.2 },
    helping: { eyeFocus: 0.95, arm: 0 },
    learning: { eyeFocus: 0.9, arm: 8 },
    observing: { eyeFocus: 1.0, arm: 8 },
    guiding: { eyeFocus: 0.9, arm: 0 }
  };

  var PRESENTATION_ADJ = {
    feminine: { eyeR: 0.168, pupR: 0.078, bodyBase: 0xf4fbff, arm: 0.94, spacing: 0.01, warmth: 0.05 },
    masculine: { eyeR: 0.148, pupR: 0.072, bodyBase: 0xdfeef7, arm: 1.08, spacing: -0.008, warmth: -0.04 },
    neutral: { eyeR: 0.158, pupR: 0.075, bodyBase: 0xeaf7ff, arm: 1.0, spacing: 0, warmth: 0 }
  };

  var GESTURES = {
    0: { l: 0.16, r: -0.16, el: 0.05, er: 0.05, hl: 1.0, hr: 1.0 },
    1: { l: -1.35, r: 1.35, el: 0.2, er: 0.2, hl: 1.15, hr: 1.15 },
    2: { l: 0.15, r: -0.9, el: 0.3, er: 1.1, hl: 1.0, hr: 1.3 },
    3: { l: -0.55, r: 0.55, el: 0.35, er: 0.35, hl: 1.45, hr: 1.45 },
    4: { l: -0.7, r: 0.7, el: 0.25, er: 0.25, hl: 1.25, hr: 1.25 },
    5: { l: 0.25, r: -0.25, el: 0.15, er: 0.15, hl: 0.8, hr: 0.8 },
    6: { l: 0.28, r: -0.28, el: 0.15, er: 0.15, hl: 0.9, hr: 0.9 },
    7: { l: 0.15, r: -1.0, el: 0.2, er: 1.25, hl: 1.0, hr: 1.35 },
    8: { l: 0.2, r: -0.2, el: 0.1, er: 0.1, hl: 1.0, hr: 1.0 },
    9: { l: 0.16, r: -0.16, el: 0.08, er: 0.08, hl: 1.05, hr: 1.05 }
  };

  function mouthShapeBase(shape) {
    switch (shape) {
      case "smile":      return { w: 1.35, tilt: 0.0,  corner: 0.05, open: 0.06 };
      case "concern":    return { w: 1.1,  tilt: 0.1,  corner: -0.03, open: 0.0 };
      case "sad":        return { w: 1.25, tilt: 0.14, corner: -0.05, open: 0.0 };
      case "frustrated": return { w: 1.3,  tilt: 0.06, corner: 0.0,  open: 0.0 };
      case "thin":       return { w: 0.85, tilt: 0.03, corner: 0.01, open: 0.0 };
      case "surprised":  return { w: 1.05, tilt: 0.0,  corner: 0.0,  open: 0.95 };
      case "confused":   return { w: 1.0,  tilt: -0.08, corner: -0.08, open: 0.15 };
      case "speak":      return { w: 0.9,  tilt: 0.0,  corner: 0.0,  open: 0.5 };
      case "open":       return { w: 1.2,  tilt: 0.0,  corner: 0.0,  open: 0.28 };
      default:           return { w: 1.15, tilt: 0.0,  corner: 0.0,  open: 0.0 };
    }
  }

  function armTarget(k) {
    return GESTURES[k] || GESTURES[0];
  }

  function mergeWeld(geos, THREE) {
    var pos = [], nor = [], uvs = [], ind = [], off = 0;
    var i, k;
    for (i = 0; i < geos.length; i++) {
      var g = geos[i];
      var p = g.attributes.position.array;
      var nn = g.attributes.normal.array;
      var uv = g.attributes.uv ? g.attributes.uv.array : null;
      var ix = g.index ? g.index.array : null;
      var cnt = p.length / 3;
      for (k = 0; k < p.length; k++) pos.push(p[k]);
      for (k = 0; k < nn.length; k++) nor.push(nn[k]);
      if (uv) for (k = 0; k < uv.length; k++) uvs.push(uv[k]);
      if (ix) { for (k = 0; k < ix.length; k++) ind.push(ix[k] + off); }
      else { for (k = 0; k < cnt; k++) ind.push(k + off); }
      off += cnt;
    }
    var merged = new THREE.BufferGeometry();
    merged.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    merged.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
    if (uvs.length) merged.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
    merged.setIndex(ind);

    var np = merged.attributes.position.array;
    var round = 1000;
    var map = {};
    var remap = new Array(np.length / 3);
    var weldCount = 0;
    for (i = 0; i < np.length; i += 3) {
      var key = Math.round(np[i] * round) + "_" + Math.round(np[i + 1] * round) + "_" + Math.round(np[i + 2] * round);
      if (map[key] === undefined) {
        map[key] = weldCount;
        remap[i / 3] = weldCount;
        if (weldCount !== i / 3) {
          np[weldCount * 3] = np[i];
          np[weldCount * 3 + 1] = np[i + 1];
          np[weldCount * 3 + 2] = np[i + 2];
        }
        weldCount++;
      } else {
        remap[i / 3] = map[key];
      }
    }
    if (weldCount < np.length / 3) {
      var newPos = new Float32Array(weldCount * 3);
      for (i = 0; i < weldCount; i++) {
        newPos[i * 3] = np[i * 3];
        newPos[i * 3 + 1] = np[i * 3 + 1];
        newPos[i * 3 + 2] = np[i * 3 + 2];
      }
      merged.setAttribute("position", new THREE.BufferAttribute(newPos, 3));
      var newInd = [];
      var oldInd = merged.index.array;
      for (i = 0; i < oldInd.length; i++) newInd.push(remap[oldInd[i]]);
      merged.setIndex(newInd);
    }
    merged.computeVertexNormals();
    merged.computeBoundingSphere();
    return merged;
  }

  function buildBody(THREE) {
    var lobes = [
      { r: 1.0, x: 0.0, y: 0.0, z: 0.0 },
      { r: 0.62, x: 0.0, y: 0.72, z: 0.06 },
      { r: 0.72, x: -0.82, y: -0.08, z: 0.24 },
      { r: 0.72, x: 0.82, y: -0.08, z: 0.24 },
      { r: 0.55, x: -0.56, y: 0.34, z: -0.48 },
      { r: 0.55, x: 0.56, y: 0.34, z: -0.48 },
      { r: 0.6, x: 0.0, y: -0.68, z: 0.16 }
    ];
    var geos = [];
    for (var i = 0; i < lobes.length; i++) {
      var s = new THREE.SphereGeometry(lobes[i].r, 28, 22);
      s.translate(lobes[i].x, lobes[i].y, lobes[i].z);
      geos.push(s);
    }
    var geom = mergeWeld(geos, THREE);
    geos.forEach(function (g) { g.dispose(); });
    var mat = new THREE.MeshPhysicalMaterial({
      color: BASE_BODY,
      roughness: 0.58,
      metalness: 0.04,
      clearcoat: 0.4,
      clearcoatRoughness: 0.3,
      emissive: DARK_EDGE,
      emissiveIntensity: 0.35,
      transparent: true,
      opacity: 0.99
    });
    var mesh = new THREE.Mesh(geom, mat);
    mesh.name = "cloudBody";
    return mesh;
  }

  function buildFace(THREE, adj) {
    var face = new THREE.Group();
    face.name = "faceGroup";

    var eyeMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.25, metalness: 0.02 });
    var pupilMat = new THREE.MeshStandardMaterial({ color: 0x12283a, roughness: 0.35, metalness: 0 });
    var glintMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.1, metalness: 0, transparent: true, opacity: 0.92 });
    var lidMat = new THREE.MeshStandardMaterial({ color: BASE_BODY, roughness: 0.6, metalness: 0, transparent: true, opacity: 0.92 });
    var browMat = new THREE.MeshStandardMaterial({ color: 0x0e2433, roughness: 0.5, metalness: 0 });

    var parts = { eyes: [], pupils: [], glints: [], lids: [], brows: [] };

    for (var s = -1; s <= 1; s += 2) {
      var ex = s * (0.30 + (adj.spacing || 0));
      var eyeG = new THREE.Group();
      eyeG.position.set(ex, 0.14, 0);

      var sclera = new THREE.Mesh(new THREE.SphereGeometry(adj.eyeR, 24, 20), eyeMat);
      eyeG.add(sclera);

      var pupil = new THREE.Mesh(new THREE.SphereGeometry(adj.pupR, 20, 16), pupilMat);
      pupil.position.z = adj.eyeR * 0.9;
      pupil.name = "pupil";
      eyeG.add(pupil);

      var glint = new THREE.Mesh(new THREE.SphereGeometry(adj.pupR * 0.32, 10, 8), glintMat);
      glint.position.set(adj.pupR * 0.45, adj.pupR * 0.45, adj.eyeR * 1.05);
      eyeG.add(glint);

      var lidPiv = new THREE.Group();
      lidPiv.position.copy(pupil.position);
      var lid = new THREE.Mesh(new THREE.SphereGeometry(adj.eyeR * 1.12, 24, 14, 0, Math.PI * 2, 0, Math.PI * 0.5), lidMat);
      lid.scale.z = 0.4;
      lidPiv.add(lid);
      lidPiv.name = "lidPiv";
      eyeG.add(lidPiv);

      var brow = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.045, 0.05), browMat);
      brow.position.set(ex * 0.5, 0.42, 0.02);
      brow.name = "brow";
      face.add(brow);

      face.add(eyeG);
      parts.eyes.push(eyeG);
      parts.pupils.push(pupil);
      parts.glints.push(glint);
      parts.lids.push(lidPiv);
      parts.brows.push(brow);
    }
    face.userData.parts = parts;

    var mouthGroup = new THREE.Group();
    mouthGroup.position.set(0, -0.24, 0.12);
    var mouthMat = new THREE.MeshStandardMaterial({ color: 0x0a1a26, roughness: 0.55, metalness: 0, transparent: true, opacity: 0.92 });
    var base = new THREE.Mesh(new THREE.SphereGeometry(0.07, 20, 14), mouthMat);
    base.scale.set(1.3, 0.85, 0.55);
    base.name = "mouthBase";
    mouthGroup.add(base);
    var cl = new THREE.Mesh(new THREE.SphereGeometry(0.034, 12, 10), mouthMat);
    cl.position.set(-0.085, -0.01, 0.01);
    var cr = cl.clone();
    cr.position.x = 0.085;
    mouthGroup.add(cl);
    mouthGroup.add(cr);
    mouthGroup.userData = { base: base, cornerL: cl, cornerR: cr };
    face.add(mouthGroup);
    face.userData.mouth = mouthGroup;

    return face;
  }

  function buildArm(THREE, side, scale) {
    var arm = new THREE.Group();
    arm.name = "arm" + (side > 0 ? "R" : "L");
    arm.position.set(side * 0.86, -0.2, 0.18);

    var skinMat = new THREE.MeshStandardMaterial({ color: BASE_BODY, roughness: 0.6, metalness: 0.04 });
    var glintTipMat = new THREE.MeshStandardMaterial({ color: BASE_ACCENT, emissive: BASE_ACCENT, emissiveIntensity: 0.8, transparent: true, opacity: 0.9 });

    var shoulder = new THREE.Group();
    shoulder.name = "shoulder";

    var upper = new THREE.Mesh(new THREE.CylinderGeometry(0.05 * scale, 0.05 * scale, 0.34, 12), skinMat);
    upper.position.y = -0.17;
    shoulder.add(upper);

    var elbow = new THREE.Group();
    elbow.position.set(0, -0.34, 0);
    elbow.name = "elbow";
    shoulder.add(elbow);

    var fore = new THREE.Mesh(new THREE.CylinderGeometry(0.04 * scale, 0.04 * scale, 0.3, 12), skinMat);
    fore.position.y = -0.15;
    elbow.add(fore);

    var hand = new THREE.Mesh(new THREE.SphereGeometry(0.066 * scale, 14, 12), skinMat);
    hand.position.y = -0.34;
    hand.name = "hand";
    elbow.add(hand);

    var tip = new THREE.Mesh(new THREE.SphereGeometry(0.03, 10, 8), glintTipMat);
    tip.position.set(0, -0.375, 0.02);
    elbow.add(tip);

    shoulder.rotation.z = side > 0 ? -0.15 : 0.15;
    arm.add(shoulder);
    arm.userData = { shoulder: shoulder, elbow: elbow, hand: hand };
    return arm;
  }

  function buildScene(T, stage, config) {
    var renderer;
    try {
      renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance", stencil: false });
    } catch (e) {
      return { error: e };
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputEncoding = T.sRGBEncoding;
    var scene = new T.Scene();

    scene.add(new T.HemisphereLight(0xdff6ff, 0x0e1417, 0.75));
    var key = new T.DirectionalLight(0xffffff, 1.55);
    key.position.set(2.4, 3.2, 4.5);
    scene.add(key);
    var rim = new T.DirectionalLight(0x8fd0ff, 0.55);
    rim.position.set(-3.5, 1.5, -2.5);
    scene.add(rim);
    scene.add(new T.AmbientLight(0x2a4a5e, 0.25));

    var stageGroup = new T.Group();
    stageGroup.name = "cloudStageRef";

    var body = buildBody(T);
    stageGroup.add(body);

    var presentation = (config && (config.presentation_pref || config.presentation)) || "neutral";
    var adj = PRESENTATION_ADJ[presentation] || PRESENTATION_ADJ.neutral;

    var face = buildFace(T, adj);
    face.position.set(0, 0.32, 0.92);
    stageGroup.add(face);

    stageGroup.add(buildArm(T, -1, adj.arm));
    stageGroup.add(buildArm(T, 1, adj.arm));

    var coreLight = new T.PointLight(BASE_ACCENT, 1.2, 4.2, 1.7);
    coreLight.position.set(0, -0.05, 0.3);
    stageGroup.add(coreLight);

    scene.add(stageGroup);

    var camera = new T.PerspectiveCamera(40, 1, 0.1, 50);
    camera.position.set(0, 0.05, 8.6);
    camera.lookAt(0, 0.05, 0);

    var arms = [];
    stageGroup.children.forEach(function (c) { if (c.userData && c.userData.shoulder) arms.push(c); });

    return {
      renderer: renderer,
      scene: scene,
      camera: camera,
      group: stageGroup,
      body: body,
      bodyMat: body.material,
      face: face,
      mouth: face.userData.mouth,
      eyes: face.userData.parts.eyes,
      pupils: face.userData.parts.pupils,
      glints: face.userData.parts.glints,
      lids: face.userData.parts.lids,
      brows: face.userData.parts.brows,
      arms: arms,
      coreLight: coreLight
    };
  }

  var engine = {
    attached: false,
    paused: false,
    container: null,
    api: null,
    raf: 0,
    lastT: 0,
    ro: null,
    nextBlinkAt: 0,
    statusEl: null,
    fallbackEl: null,
    cfg: null,
    cur: null,
    tgt: null,
    speakPhase: 0,
    popSpeed: 0,
    waveT: 0,
    prevEmotion: "",
    speechActive: false
  };

  function freshCur() {
    return {
      float: 1, breath: 1, tilt: 0, faceTilt: 0,
      eyeX: 0, eyeY: 0, lid: 0, squint: 0, browH: 0, browTilt: 0, pup: 1,
      mouthOpen: 0, mouthW: 1, mouthTilt: 0, cornerY: 0,
      armL: 0, armR: 0, elbowL: 0, elbowR: 0, handL: 1, handR: 1,
      glow: 0.42, bright: 1, body: BASE_BODY, accent: BASE_ACCENT
    };
  }

  function getSafe(obj, key) { return obj ? obj[key] : undefined; }

  function computeTargets() {
    var cfg = engine.cfg || {};
    var emotion = EMOTIONS.indexOf(cfg.emotion) !== -1 ? cfg.emotion : "neutral";
    var status = STATUSES.indexOf(cfg.status) !== -1 ? cfg.status : "idle";
    var rig = EMOTION_RIG[emotion] || EMOTION_RIG.neutral;
    var ov = STATUS_OVERRIDE[status];
    var t = engine.tgt;

    var intensity = Number(getSafe(cfg, "intensity"));
    if (!isFinite(intensity)) intensity = 0.5;
    intensity = Math.max(0, Math.min(1, intensity));
    var iMul = 0.35 + 0.65 * intensity;
    var glowMul = 0.5 + 0.5 * intensity;

    var presentation = cfg.presentation_pref || cfg.presentation || "neutral";
    var adj = PRESENTATION_ADJ[presentation] || PRESENTATION_ADJ.neutral;

    t.body = mixColor(adj.bodyBase, rig.body, 0.75);
    t.accent = rig.accent;
    t.glow = Math.max(0.15, rig.glow * glowMul);
    t.bright = rig.bright;
    t.float = (rig.float || 1) * (0.45 + 0.55 * iMul);
    t.breath = (rig.breath || 1) * (0.4 + 0.6 * iMul);

    t.lid = rig.lid || 0;
    t.squint = rig.squint || 0;
    t.browH = rig.browH || 0;
    t.browTilt = rig.browTilt || 0;
    t.pup = rig.pupScale || 1;
    t.faceTilt = emotion === "confused" ? 0.1 : emotion === "curious" ? 0.06 : 0;
    t.tilt = Math.sin(engine.waveT * 0.5) * 0.02 * iMul;

    var effArm = rig.arm || 0;
    var effMouth = rig.mouth || "neutral";
    var effOpen = typeof rig.mouthOpen === "number" ? rig.mouthOpen : 0;
    var effFocus = rig.eyeFocus || 0.4;
    if (ov) {
      if (ov.arm !== undefined) effArm = ov.arm;
      if (ov.mouth) effMouth = ov.mouth;
      if (ov.mouthOpen !== undefined) effOpen = ov.mouthOpen;
      if (ov.eyeFocus !== undefined) effFocus = ov.eyeFocus;
    }

    var g = armTarget(effArm);
    t.armL = g.l; t.armR = g.r; t.elbowL = g.el; t.elbowR = g.er;
    t.handL = g.hl; t.handR = g.hr;

    var attention = cfg.attention_target;
    if (attention && typeof attention === "object" && isFinite(attention.x) && isFinite(attention.y)) {
      t.eyeX = Math.max(-1, Math.min(1, Number(attention.x))) * 0.14;
      t.eyeY = Math.max(-1, Math.min(1, Number(attention.y))) * 0.1;
    } else {
      var focus = effFocus;
      t.eyeX = (1 - focus) * (0.06 * Math.sin(engine.waveT * 0.45) + 0.04 * Math.sin(engine.waveT * 0.23));
      t.eyeY = 0.02 * Math.sin(engine.waveT * 0.8) * (1 - focus);
      if (emotion === "thinking") { t.eyeY = -0.06; t.eyeX = 0.035; }
    }

    var md = mouthShapeBase(effMouth);
    if (status === "speaking") {
      t.mouthW = 0.95; t.mouthTilt = 0; t.cornerY = 0; t.mouthOpen = 0.5;
    } else if (status === "listening") {
      t.mouthW = 0.8; t.mouthTilt = 0; t.cornerY = 0; t.mouthOpen = 0.04;
    } else {
      t.mouthW = md.w; t.mouthTilt = md.tilt; t.cornerY = md.corner; t.mouthOpen = effOpen;
    }
  }

  function blendNum(key, k) {
    engine.cur[key] += (engine.tgt[key] - engine.cur[key]) * k;
  }

  function blendColor(dstKey, k) {
    var c = hexToRgb(engine.tgt[dstKey]);
    var cur = hexToRgb(engine.cur[dstKey]);
    engine.cur[dstKey] = (Math.round(cur.r + (c.r - cur.r) * k) << 16) |
      (Math.round(cur.g + (c.g - cur.g) * k) << 8) |
      Math.round(cur.b + (c.b - cur.b) * k);
  }

  function resizeCompute() {
    var api = engine.api;
    if (!api || !engine.container) return;
    var w = engine.container.clientWidth || 320;
    var h = engine.container.clientHeight || 260;
    if (w < 10 || h < 10) return;
    api.renderer.setSize(w, h, false);
    api.camera.aspect = w / h;
    var aspect = w / h;
    if (aspect < 0.8) api.camera.position.z = 7.2;
    else if (aspect < 1.2) api.camera.position.z = 7.9;
    else api.camera.position.z = 8.6;
    api.camera.lookAt(0, 0.05, 0);
    api.camera.updateProjectionMatrix();
  }

  function onWinResize() { resizeCompute(); }

  function setupResize() {
    teardownResize();
    if (window.ResizeObserver) {
      try {
        engine.ro = new ResizeObserver(function () { resizeCompute(); });
        engine.ro.observe(engine.container);
      } catch (e) { engine.ro = null; }
    }
    window.addEventListener("resize", onWinResize);
  }

  function teardownResize() {
    if (engine.ro) { engine.ro.disconnect(); engine.ro = null; }
    window.removeEventListener("resize", onWinResize);
  }

  function disposeObject(obj) {
    if (!obj) return;
    obj.traverse(function (node) {
      if (node.isMesh) {
        if (node.geometry) node.geometry.dispose();
        if (node.material) {
          if (Array.isArray(node.material)) node.material.forEach(function (m) { m.dispose(); });
          else node.material.dispose();
        }
      }
    });
  }

  function onVis() { engine.paused = document.hidden; }

  function ensureVisListener() {
    document.removeEventListener("visibilitychange", onVis);
    engine.onVisBound = true;
    document.addEventListener("visibilitychange", onVis);
  }

  function renderOnce() {
    if (!engine.container || !engine.container.isConnected) {
      engine.raf = 0;
      return;
    }
    var now = performance.now();
    var dt = engine.lastT ? Math.min(0.05, (now - engine.lastT) / 1000) : 0.016;
    engine.lastT = now;
    engine.waveT += dt;
    engine.speakPhase += dt * 7;
    engine.popSpeed = Math.max(0, engine.popSpeed - dt * 1.6);

    if (now > engine.nextBlinkAt) engine.nextBlinkAt = now + 2200 + Math.random() * 3600;

    computeTargets();
    if (!engine.paused) applyChannels(dt);

    if (!engine.paused) {
      engine.api.renderer.render(engine.api.scene, engine.api.camera);
    }
    engine.raf = requestAnimationFrame(renderOnce);
  }

  function applyChannels(dt) {
    var k = Math.min(1, dt * 6);
    var kFace = Math.min(1, dt * 8.5);
    var api = engine.api;
    var T = _threeCache;
    if (!api || !T || !engine.cfg) return;

    blendColor("body", k);
    blendColor("accent", k);
    blendNum("glow", k);
    blendNum("bright", k);
    blendNum("float", k);
    blendNum("breath", k);
    blendNum("tilt", k);
    blendNum("faceTilt", k * 1.4);
    blendNum("eyeX", kFace);
    blendNum("eyeY", kFace);
    blendNum("lid", kFace);
    blendNum("squint", kFace);
    blendNum("browH", kFace);
    blendNum("browTilt", kFace);
    blendNum("pup", kFace);
    blendNum("mouthOpen", kFace);
    blendNum("mouthW", kFace);
    blendNum("mouthTilt", kFace);
    blendNum("cornerY", kFace);
    blendNum("armL", k);
    blendNum("armR", k);
    blendNum("elbowL", k);
    blendNum("elbowR", k);
    blendNum("handL", kFace);
    blendNum("handR", kFace);

    var intensity = Number(getSafe(engine.cfg, "intensity"));
    if (!isFinite(intensity)) intensity = 0.5;
    intensity = Math.max(0, Math.min(1, intensity));
    var iMul = 0.35 + 0.65 * intensity;
    var t = engine.waveT;
    var pop = Math.max(0, 1 - engine.popSpeed * 3);
    var popAmt = pop * pop * 0.09 * intensity;

    var g = api.group;
    g.position.y = Math.sin(t * 0.9) * 0.13 * engine.cur.float * iMul;
    g.rotation.z = engine.cur.tilt * engine.cur.float;
    g.rotation.y = Math.sin(t * 0.3) * 0.06 * engine.cur.float;

    var breath = 1 + Math.sin(t * 1.3) * 0.035 * engine.cur.breath * iMul + engine.cur.breath * popAmt * 0.4;
    api.body.scale.x = breath * (1 + popAmt * 0.6);
    api.body.scale.y = breath;
    api.body.scale.z = breath * (1 + popAmt * 0.5);

    api.bodyMat.color.setHex(intColor(engine.cur.body));
    var emissive = mixColor(engine.cur.body, engine.cur.accent, 0.06 + engine.cur.glow * 0.16);
    api.bodyMat.emissive = new T.Color(intColor(emissive));
    api.bodyMat.emissiveIntensity = 0.5 + engine.cur.glow * engine.cur.bright * 0.6;

    api.face.rotation.z = engine.cur.faceTilt;
    api.face.rotation.y = -engine.cur.eyeX * 0.4;

    var presentation = engine.cfg.presentation_pref || engine.cfg.presentation || "neutral";
    var spacing = presentation === "feminine" ? 0.01 : presentation === "masculine" ? -0.008 : 0;

    for (var i = 0; i < 2; i++) {
      var side = i === 0 ? -1 : 1;
      var e = api.eyes[i];
      var px = side * (0.30 + spacing) + engine.cur.eyeX * 0.18;
      var py = 0.14 + engine.cur.eyeY * 0.18;
      e.position.x += (px - e.position.x) * kFace;
      e.position.y += (py - e.position.y) * kFace;

      var pup = api.pupils[i];
      pup.position.x += ((engine.cur.eyeX * 0.12) - pup.position.x) * kFace;
      pup.position.y += ((engine.cur.eyeY * 0.12) - pup.position.y) * kFace;
      pup.scale.setScalar(engine.cur.pup);

      var gl = api.glints[i];
      gl.position.x += ((engine.cur.eyeX * 0.12 + 0.045) - gl.position.x) * kFace;
      gl.position.y += ((engine.cur.eyeY * 0.12 + 0.045) - gl.position.y) * kFace;

      var lidRot = 0.35 - 0.8 * engine.cur.lid + 0.25 * engine.cur.squint;
      var piv = api.lids[i];
      piv.rotation.x += (lidRot - piv.rotation.x) * kFace;

      var brow = api.brows[i];
      var browY = 0.42 + engine.cur.browH + side * engine.cur.browTilt * 0.4;
      brow.position.y += (browY - brow.position.y) * kFace;
      brow.rotation.z += ((side * engine.cur.browTilt) - brow.rotation.z) * kFace;
    }

    var mouth = api.mouth;
    var mb = mouth.userData.base;
    var mOpen = engine.cur.mouthOpen;
    if (engine.cfg.status === "speaking" && engine.speechActive) {
      mOpen = 0.45 + 0.5 * Math.abs(Math.sin(engine.speakPhase));
    }
    mb.scale.x += (engine.cur.mouthW - mb.scale.x) * kFace;
    mb.scale.y += ((0.55 + mOpen * 1.5) - mb.scale.y) * kFace;
    mb.scale.z = 0.55;
    mb.rotation.z += (engine.cur.mouthTilt - mb.rotation.z) * kFace;

    var cL = mouth.userData.cornerL, cR = mouth.userData.cornerR;
    cL.position.y += (engine.cur.cornerY - cL.position.y) * kFace;
    cR.position.y += (engine.cur.cornerY - cR.position.y) * kFace;

    var a0 = api.arms[0], a1 = api.arms[1];
    var wave = engine.cfg.status === "speaking" && engine.speechActive
      ? Math.abs(Math.sin(engine.speakPhase)) * 0.08
      : Math.sin(t * 2.2) * 0.25 * intensity * (iMul) * 0.4;
    a0.userData.shoulder.rotation.z += ((engine.cur.armL + wave) - a0.userData.shoulder.rotation.z) * k;
    a1.userData.shoulder.rotation.z += ((engine.cur.armR - wave) - a1.userData.shoulder.rotation.z) * k;
    a0.userData.elbow.rotation.z += (engine.cur.elbowL - a0.userData.elbow.rotation.z) * k;
    a1.userData.elbow.rotation.z += (engine.cur.elbowR - a1.userData.elbow.rotation.z) * k;
    a0.userData.hand.scale.setScalar(engine.cur.handL);
    a1.userData.hand.scale.setScalar(engine.cur.handR);

    api.coreLight.color = new T.Color(intColor(engine.cur.accent));
    api.coreLight.intensity = (0.6 + engine.cur.glow * 2.2 * engine.cur.bright) * (1 + pop * 1.5);
  }

  function setStatusText(text, color) {
    if (engine.statusEl) engine.statusEl.textContent = text;
    if (engine.statusEl && color) engine.statusEl.style.color = color;
  }

  function showFallback() {
    if (engine.fallbackEl) engine.fallbackEl.style.display = "";
  }

  function freshConfig() {
    if (window.VMCloud && typeof window.VMCloud.renderConfig === "function") {
      try { return window.VMCloud.renderConfig(); } catch (e) { }
    }
    return {};
  }

  function resolveEl(idOrEl) {
    if (!idOrEl) return null;
    if (typeof idOrEl === "string") return document.getElementById(idOrEl) || null;
    return (idOrEl && idOrEl.nodeType === 1) ? idOrEl : null;
  }

  function relocate(target, opts) {
    if (!target || !engine.api || !engine.container) return;
    engine.container = target;
    engine.statusEl = resolveEl((opts && opts.statusId) || STATUS_ID) || null;
    engine.fallbackEl = resolveEl((opts && opts.fallbackId) || FALLBACK_ORB_ID) || null;
    var canvas = engine.api.renderer.domElement;
    if (canvas && canvas.parentNode !== target) {
      target.appendChild(canvas);
    }
    var cfg = (opts && opts.config) || null;
    if (cfg && cfg.emotion) engine.cfg = cfg;
    if (engine.cfg) { engine.cur = engine.cur || freshCur(); engine.tgt = engine.tgt || freshCur(); }
    resizeCompute();
    setupResize();
    ensureVisListener();
    engine.paused = document.hidden;
    engine.suspended = false;
    engine.lastT = 0;
    if (!engine.raf) engine.raf = requestAnimationFrame(renderOnce);
    if (engine.fallbackEl) engine.fallbackEl.style.display = "none";
    setStatusText("Cloud active", "#2ee6b0");
    notifyState(engine.cfg);
  }

  function attach(container, opts) {
    var target = resolveEl(container);
    if (!target || !target.isConnected) return;
    if (engine.api) {
      relocate(target, opts);
      return;
    }
    if (engine.attached) return;
    engine.container = target;
    engine.statusEl = resolveEl((opts && opts.statusId) || STATUS_ID) || null;
    engine.fallbackEl = resolveEl((opts && opts.fallbackId) || FALLBACK_ORB_ID) || null;
    engine.cfg = (opts && opts.config) || freshConfig();
    engine.cur = freshCur();
    engine.tgt = freshCur();
    engine.speakPhase = 0;
    engine.popSpeed = 0;
    engine.waveT = 0;
    engine.nextBlinkAt = performance.now() + 1500 + Math.random() * 2000;

    setStatusText("Activating Cloud…", "#8fd0ff");

    loadThree().then(function () {
      if (!engine.container || !engine.container.isConnected) { engine.container = null; return; }
      var built = buildScene(_threeCache, engine.container, engine.cfg);
      if (built.error || !built.renderer) {
        setStatusText("WebGL unavailable — placeholder active", "#f59e0b");
        showFallback();
        engine.attached = false;
        return;
      }
      engine.api = built;
      engine.attached = true;
      if (engine.fallbackEl) engine.fallbackEl.style.display = "none";
      var canvas = built.renderer.domElement;
      canvas.style.position = "absolute";
      canvas.style.top = "0";
      canvas.style.left = "0";
      canvas.style.width = "100%";
      canvas.style.height = "100%";
      canvas.style.display = "block";
      engine.container.appendChild(canvas);
      resizeCompute();
      setupResize();
      ensureVisListener();
      engine.paused = document.hidden;
      engine.lastT = 0;
      setStatusText("Cloud active", "#2ee6b0");
      engine.raf = requestAnimationFrame(renderOnce);
      notifyState(engine.cfg);
    }).catch(function () {
      setStatusText("3D engine unavailable — placeholder active", "#f59e0b");
      showFallback();
      engine.attached = false;
    });
  }

  function notifyState(cfg) {
    var next = cfg || freshConfig();
    engine.cfg = next && next.emotion ? next : engine.cfg;
    if (!engine.cfg) engine.cfg = next || {};
    if (engine.cfg.emotion && engine.cfg.emotion === "surprised" && engine.cfg.emotion !== engine.prevEmotion) {
      engine.popSpeed = 1;
    }
    if (engine.cfg.emotion) engine.prevEmotion = engine.cfg.emotion;
    if (engine.attached && engine.container && engine.container.isConnected) {
      resizeCompute();
    }
  }

  function suspend() {
    engine.suspended = true;
    engine.paused = true;
    if (engine.raf) { cancelAnimationFrame(engine.raf); engine.raf = 0; }
    document.removeEventListener("visibilitychange", onVis);
    teardownResize();
  }

  function resume(cfg) {
    if (!engine.api) { return; }
    engine.suspended = false;
    if (cfg && cfg.emotion) engine.cfg = cfg;
    if (engine.container && engine.container.isConnected) {
      setupResize();
      ensureVisListener();
      engine.paused = document.hidden;
      engine.lastT = 0;
      if (!engine.raf) engine.raf = requestAnimationFrame(renderOnce);
    }
    notifyState(engine.cfg);
  }

  function detach() {
    engine.attached = false;
    engine.suspended = false;
    if (engine.raf) { cancelAnimationFrame(engine.raf); engine.raf = 0; }
    document.removeEventListener("visibilitychange", onVis);
    teardownResize();
    engine.paused = false;
    var api = engine.api;
    if (api) {
      disposeObject(api.scene);
      try { api.renderer.dispose(); } catch (e) { }
      var cv = api.renderer.domElement;
      if (cv && cv.parentNode) cv.parentNode.removeChild(cv);
      engine.api = null;
    }
    engine.container = null;
    engine.statusEl = null;
    engine.fallbackEl = null;
    engine.cfg = null;
  }

  window.VMCloud3D = {
    attach: attach,
    detach: detach,
    suspend: suspend,
    resume: resume,
    notifyState: notifyState,
    notifySpeech: function (active) {
      engine.speechActive = !!active;
      if (!engine.speechActive) engine.speakPhase = 0;
    },
    setAttentionTarget: function (x, y) {
      if (window.VMCloud && typeof window.VMCloud.setState === "function") {
        window.VMCloud.setState({ attention_target: { x: x, y: y } });
      } else {
        engine.cfg = engine.cfg || {};
        engine.cfg.attention_target = { x: x, y: y };
      }
    },
    clearAttentionTarget: function () {
      var clear = { attention_target: null };
      if (window.VMCloud && typeof window.VMCloud.setState === "function") {
        window.VMCloud.setState(clear);
      } else if (engine.cfg) {
        engine.cfg.attention_target = null;
      } else {
        engine.cfg = { attention_target: null };
      }
    },
    isActive: function () { return engine.attached; },
    getConfig: function () { return engine.cfg || {}; },
    getEngineInfo: function () {
      var el = engine.statusEl || document.getElementById(STATUS_ID);
      return {
        three: !!_threeCache,
        mode: engine.attached ? "webgl" : "css",
        suspended: !!engine.suspended,
        status: el ? el.textContent : "idle"
      };
    }
  };
})();