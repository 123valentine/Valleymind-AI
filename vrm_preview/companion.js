/**
 * companion.js -- the ONE ValleyMind body-animation engine for the preview.
 *
 * Owns: scene, camera, VRM instance, a single AnimationMixer, the .vrma clip
 * cache, transport controls, and the autonomous behaviour state machine.
 * There is deliberately no second mixer or second avatar anywhere: every test
 * button and every automatic transition goes through playClip() below.
 *
 * Correct @pixiv/three-vrm v3 pipeline (the thing that is easy to get wrong):
 *   VRMLoaderPlugin            -> gltf.userData.vrm
 *   VRMAnimationLoaderPlugin   -> gltf.userData.vrmAnimations[0]
 *   createVRMAnimationClip()   -> retargeted THREE.AnimationClip for THIS vrm
 *   mixer.update(dt)           -> advance every frame
 *   vrm.update(dt)             -> MANDATORY follow-up (see the note in _loop)
 * Binding gltf.userData.vrmAnimations[0] straight to the mixer silently produces
 * a motionless or broken avatar, because VRMA tracks use VRM humanoid bone names
 * that only exist after retargeting against the loaded model.
 *
 * Second trap, and the one that bites after retargeting looks correct:
 * createVRMAnimationClip() binds the tracks to the VRM's *normalized* proxy
 * bones, which are NOT the bones the mesh is skinned to. mixer.update() alone
 * therefore moves the proxy bones and nothing else -- the mixer clock runs and
 * every bone reports a new quaternion while the rendered mesh never budges.
 * vrm.update(dt) is what copies the normalized pose onto the real hierarchy.
 *
 * Face/expressions/lip-sync live in face.js and are never touched from here.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin } from "@pixiv/three-vrm";
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from "@pixiv/three-vrm-animation";
import { FaceChannel } from "./face.js";

const MODEL_URL = "/vrm-preview/model";
const CLIP_URL = (file) => `/vrm-preview/clips/${file}`;
const MANIFEST_URL = "/vrm-preview/assets/clips.json";

/** Autonomous behaviour weights. Idle dominates, as a real companion would. */
const BEHAVIOUR_WEIGHTS = {
  idle: 34,
  talking: 14,
  listening: 10,
  thinking: 9,
  walking: 8,
  happy: 7,
  waving: 6,
  nodding: 6,
  pointing: 5,
  sad: 4,
  angry: 0,
  headshake: 3,
  unsure: 2,
};

/** Mood expression per behaviour, applied through the separate face channel. */
const BEHAVIOUR_MOOD = {
  idle: "relaxed",
  talking: "relaxed",
  listening: "neutral",
  thinking: "neutral",
  walking: "relaxed",
  happy: "happy",
  sad: "sad",
  angry: "angry",
  waving: "happy",
  nodding: "neutral",
  pointing: "neutral",
  headshake: "neutral",
  unsure: "neutral",
};

const CROSSFADE = 0.45;

/** Skin-tone presets. Raw sRGB, 0..1 per channel; the MToon mapper converts to
 * linear and only shifts the skin materials whose name carries "_SKIN". */
const SKIN_PRESETS = [
  { name: "Very Fair", hex: "#F6D2C0", rgb: [0.9647, 0.8235, 0.7529] },
  { name: "Fair", hex: "#EBC0A8", rgb: [0.9216, 0.7529, 0.6588] },
  { name: "Light Brown", hex: "#D69A78", rgb: [0.8392, 0.6039, 0.4706] },
  { name: "Medium Brown", hex: "#B87552", rgb: [0.7216, 0.4588, 0.3216] },
  { name: "Deep Brown", hex: "#8A4F35", rgb: [0.5412, 0.3098, 0.2078] },
  { name: "Dark Chocolate", hex: "#5A3023", rgb: [0.3529, 0.1882, 0.1373] },
];

/** Average sRGB color of each skin material's base color texture (measured once
 * from valleymind.vrm; used so the tint re-centers the texture instead of
 * replacing it: color = target / textureAvg per channel). */
const SOURCE_SKIN_AVG = {
  "N00_000_00_Face_00_SKIN (Instance)": [239.7 / 255, 187.3 / 255, 166.3 / 255],
  "N00_000_00_Body_00_SKIN (Instance)": [195.4 / 255, 151.5 / 255, 133.7 / 255],
};

/** sRGB -> linear-srgb (MToon/three work in linear; GLTFLoader stores the same way). */
function srgbDecode(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** Per-channel tint multiplier that makes a material whose map averages `srcAvg`
 * (sRGB) render at approximately `target` (sRGB): linear k = targetLin / srcAvgLin. */
function tintMultiplier(target, srcAvg) {
  const tl = srgbDecode(target);
  const sl = srgbDecode(srcAvg);
  const k = tl / sl;
  return Number.isFinite(k) && k > 0 ? Math.min(k, 4) : 1;
}

export class Companion {
  constructor(canvas, log, opts) {
    opts = opts || {};
    // Opt-in standalone mode: no painted backdrop, so the avatar is the ONLY
    // thing composited over whatever the host page shows behind the canvas.
    // Default stays off so the /vrm-preview test page is unchanged.
    this.transparent = !!opts.transparent;
    this.canvas = canvas;
    this.log = log || (() => {});
    this.status = { stage: "boot", current: null, behaviour: null, loaded: 0, errors: [] };

    this.manifest = null;
    this.byName = new Map();
    this.clipCache = new Map(); // name -> THREE.AnimationClip
    this.inflight = new Map(); // name -> Promise
    this.manualPins = new Set(); // user-requested clips: never auto-transitioned away
    this.lastPick = new Map(); // behaviour -> clip name (avoids immediate repeats)

    this.speed = 1;
    this.loop = true;
    this.auto = true;
    this.playing = true;
    this._renderingOn = true;
    this.transitionAt = 0;
    this.transitioning = false;
    this._token = 0;
    this.elapsed = 0;

    this._skinState = new Map(); // MToonMaterial -> { color, shade } (untouched originals)
    this.skin = null; // selected preset { name, hex }, or null = original VRM colors

    this._buildScene();
  }

  _buildScene() {
    const renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: this.transparent,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    this.renderer = renderer;

    this.scene = new THREE.Scene();
    if (this.transparent) {
      // Transparent overlay: clear to alpha, no fog (fog would grey the edges
      // of the standing body against whatever UI sits behind).
      this.scene.background = null;
      this.scene.fog = null;
    } else {
      this.scene.background = new THREE.Color(0x0b0d14);
      this.scene.fog = new THREE.Fog(0x0b0d14, 6, 22);
    }

    this.camera = new THREE.PerspectiveCamera(28, 1, 0.05, 200);
    this.camera.position.set(0, 1.35, 4.2);
    this.camera.lookAt(0, 1.0, 0);

    const key = new THREE.DirectionalLight(0xffffff, 2.1);
    key.position.set(2.6, 4.4, 3.2);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.camera.near = 0.5;
    key.shadow.camera.far = 18;
    key.shadow.camera.left = key.shadow.camera.bottom = -3.5;
    key.shadow.camera.right = key.shadow.camera.top = 3.5;
    this.scene.add(key);

    const rim = new THREE.DirectionalLight(0x88aaff, 0.85);
    rim.position.set(-3, 2.4, -2.6);
    this.scene.add(rim);
    this.scene.add(new THREE.HemisphereLight(0xcfe0ff, 0x1a1c26, 0.75));

    // The dark receiving disc only exists in the boxed preview scene; a
    // transparent overlay has no ground plane behind the avatar.
    if (!this.transparent) {
      const floor = new THREE.Mesh(
        new THREE.CircleGeometry(6, 64),
        new THREE.MeshStandardMaterial({ color: 0x171a26, roughness: 0.95, metalness: 0.0 })
      );
      floor.rotation.x = -Math.PI / 2;
      floor.receiveShadow = true;
      this.scene.add(floor);
    }

    this.mixer = new THREE.AnimationMixer(new THREE.Group()); // real VRM replaces root below
    this.action = null;

    this.clock = new THREE.Clock();
    window.addEventListener("resize", () => this.resize());
    this.resize();
  }

  resize() {
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // ── boot ────────────────────────────────────────────────────────────────
  async boot() {
    this.status.stage = "manifest";
    this.manifest = await (await fetch(MANIFEST_URL)).json();
    for (const c of this.manifest.clips) this.byName.set(c.name, c);
    this.log(
      `catalogue: ${this.manifest.counts.total} clips (${this.manifest.counts.active} active + ` +
        `${this.manifest.counts.spare} spare) in ${this.manifest.categories.length} categories`
    );

    this.status.stage = "vrm";
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    const gltf = await loader.loadAsync(MODEL_URL);
    const vrm = gltf.userData.vrm;
    this.vrm = vrm;
    vrm.scene.traverse((o) => {
      if (o.isMesh) {
        o.castShadow = true;
        o.receiveShadow = true;
        o.frustumCulled = false;
      }
    });
    this.scene.add(vrm.scene);
    this._collectSkinMaterials();

    // Retarget clips against the model that actually loaded.
    this.mixer = new THREE.AnimationMixer(vrm.scene);

    this.face = new FaceChannel(vrm);
    this.face.setMood("relaxed", 1);

    this.frameFullBody();

    // Idle immediately, so something always moves even before auto picks a beat.
    this.playClip(this._pick("idle") || "idle", { behaviour: "idle" });

    this.status.stage = "ready";
    this.log(`VRM ${vrm.meta?.metaVersion ?? "?"} loaded | ${vrm.humanoid?.humanBones?.length ?? 0} humanoid bones`);
    this._loop();
    return this;
  }

  /** Frames the camera so the whole body is visible with headroom. */
  frameFullBody() {
    const box = new THREE.Box3().setFromObject(this.vrm.scene);
    const size = new THREE.Vector3();
    const center = new THREE.Vector3();
    box.getSize(size);
    box.getCenter(center);
    const height = Math.max(size.y, 0.5);
    const fov = THREE.MathUtils.degToRad(this.camera.fov);
    let dist = height / (2 * Math.tan(fov / 2));
    if (this.camera.aspect < 1) dist /= this.camera.aspect * 0.92;
    dist *= 1.18; // breathing room
    this.camera.position.set(center.x, center.y + height * 0.04, center.z + dist);
    this.camera.lookAt(center.x, center.y + height * 0.02, center.z);
    this.camera.near = 0.05;
    this.camera.far = dist * 4 + 20;
    this.camera.updateProjectionMatrix();
    this.fullBodyHeight = height;
  }

  /** Maps a world-space point to canvas pixels using the live camera/projection.
   * matrixWorldInverse is refreshed first (a stale copy silently derails the
   * projection), and the perspective divide is applied exactly as Vector3.project does. */
  projectWorld(x, y, z) {
    this.camera.updateMatrixWorld(true);
    this.camera.matrixWorldInverse.copy(this.camera.matrixWorld).invert();
    const v = new THREE.Vector3(x, y, z).project(this.camera);
    const w = this.renderer.domElement.width;
    const h = this.renderer.domElement.height;
    return [(v.x * 0.5 + 0.5) * w, (1 - (v.y * 0.5 + 0.5)) * h];
  }

  // ── skin tone (non-destructive MToon tint) ──────────────────────────────
  /** Finds the body-skin materials (name carries "_SKIN") and snapshots their
   * untouched color + shadeColorFactor so any tint stays reversible. */
  _collectSkinMaterials() {
    const seen = new Set();
    this.vrm.scene.traverse((o) => {
      if (!o.isMesh) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) {
        if (!m || !m.name || seen.has(m) || !m.name.includes("_SKIN")) continue;
        seen.add(m);
        this._skinState.set(m, {
          color: m.color ? m.color.clone() : null,
          shade: m.shadeColorFactor ? m.shadeColorFactor.clone() : null,
        });
      }
    });
    this.log(
      `skin materials: ${[...this._skinState.keys()].map((m) => m.name).join(", ") || "none found"}`
    );
  }

  /**
   * Apply a skin tone live. `preset` is a SKIN_PRESETS name or hex; any other
   * value restores the original colors. The tint is a per-channel multiplier
   * (target / source-texture-average) applied to the MToon lit + shade color,
   * so the base color texture, materials and VRM structure are never modified.
   */
  setSkinTone(preset) {
    if (!this._skinState.size) return false;
    const want =
      SKIN_PRESETS.find((p) => p.name === preset) ||
      (typeof preset === "string"
        ? SKIN_PRESETS.find((p) => p.hex.toUpperCase() === preset.toUpperCase())
        : undefined);

    for (const [m, orig] of this._skinState) {
      if (want) {
        const avg = SOURCE_SKIN_AVG[m.name] || [0.86, 0.72, 0.66];
        const kx = tintMultiplier(want.rgb[0], avg[0]);
        const ky = tintMultiplier(want.rgb[1], avg[1]);
        const kz = tintMultiplier(want.rgb[2], avg[2]);
        if (m.color && orig.color) {
          m.color.r = orig.color.r * kx;
          m.color.g = orig.color.g * ky;
          m.color.b = orig.color.b * kz;
        }
        if (m.shadeColorFactor && orig.shade) {
          m.shadeColorFactor.r = orig.shade.r * kx;
          m.shadeColorFactor.g = orig.shade.g * ky;
          m.shadeColorFactor.b = orig.shade.b * kz;
        }
      } else {
        if (m.color && orig.color) m.color.copy(orig.color);
        if (m.shadeColorFactor && orig.shade) m.shadeColorFactor.copy(orig.shade);
      }
    }

    this.skin = want ? { name: want.name, hex: want.hex } : null;
    this.log(this.skin ? `skin: ${this.skin.name} — ${this.skin.hex}` : "skin: original restored");
    this._emit();
    return true;
  }

  // ── clip loading ────────────────────────────────────────────────────────
  async clipFor(name) {
    if (this.clipCache.has(name)) return this.clipCache.get(name);
    if (this.inflight.has(name)) return this.inflight.get(name);

    const meta = this.byName.get(name);
    if (!meta) throw new Error(`unknown clip "${name}"`);

    const job = (async () => {
      const loader = new GLTFLoader();
      loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
      const gltf = await loader.loadAsync(CLIP_URL(meta.file));
      const vrma = gltf.userData.vrmAnimations?.[0];
      if (!vrma) throw new Error(`${name}: no VRM animation in file`);
      // The step that actually makes the animation apply to this VRM.
      const clip = createVRMAnimationClip(vrma, this.vrm);
      clip.name = name;
      this.clipCache.set(name, clip);
      this.status.loaded = this.clipCache.size;
      return clip;
    })();

    this.inflight.set(name, job);
    try {
      return await job;
    } finally {
      this.inflight.delete(name);
    }
  }

  /**
   * Random clip from a behaviour pool, never repeating the previous pick when
   * any alternative exists. Biases towards already-decoded clips so autonomous
   * transitions start instantly and the network is only used to widen the
   * repertoire over time.
   */
  _pick(behaviour) {
    const pool = (this.manifest.behaviours[behaviour] || []).filter((n) => this.byName.has(n));
    if (!pool.length) return null;
    const prev = this.lastPick.get(behaviour);
    const cached = pool.filter((n) => this.clipCache.has(n));
    // 70% of the time stay inside what is already decoded.
    const biased = cached.length && Math.random() < 0.7 ? cached : pool;
    let list = biased.filter((n) => n !== prev);
    if (!list.length) list = pool.filter((n) => n !== prev);
    if (!list.length) list = biased; // pool holds exactly one clip
    const choice = list[Math.floor(Math.random() * list.length)];
    this.lastPick.set(behaviour, choice);
    return choice;
  }

  _weightedBehaviour() {
    const entries = Object.entries(BEHAVIOUR_WEIGHTS).filter(([b]) => this.manifest.behaviours[b]?.length);
    const total = entries.reduce((s, [, w]) => s + w, 0);
    let r = Math.random() * total;
    for (const [b, w] of entries) {
      r -= w;
      if (r <= 0) return b;
    }
    return entries[0][0];
  }

  // ── transport ───────────────────────────────────────────────────────────
  async playClip(name, opts = {}) {
    // Loading a .vrma is a network round-trip, so a transition is not instant.
    // Without this guard the frame loop re-enters playClip() on every frame
    // while the current fetch is still in flight, which storms the network with
    // aborted requests and never settles on a clip. Each call takes a token;
    // only the newest one is allowed to install itself.
    const token = ++this._token;
    this.transitioning = true;
    this.transitionAt = Infinity;

    let clip;
    try {
      clip = await this.clipFor(name);
    } catch (err) {
      this.status.errors.push(String(err.message || err));
      this.log(`clip failed: ${name} — ${err.message || err}`);
      if (token === this._token) {
        this.transitioning = false;
        this.transitionAt = this.elapsed + 1.5; // retry shortly, don't wedge
      }
      this._emit();
      return false;
    }
    if (token !== this._token) return false; // superseded by a newer request

    const action = this.mixer.clipAction(clip);
    // LoopRepeat keeps the clip cycling; LoopOnce plays it through exactly once.
    action.setLoop(this.loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
    action.clampWhenFinished = !this.loop;
    action.enabled = true;
    action.setEffectiveTimeScale(this.speed);
    action.setEffectiveWeight(1);
    action.reset();

    if (this.action && this.action !== action) {
      // Cross-fade so autonomous transitions read as motion, not a pop.
      action.crossFadeFrom(this.action, CROSSFADE, false);
      this.action.fadeOut(CROSSFADE);
    }
    action.play();

    this.action = action;
    this.status.current = name;
    this.status.behaviour = opts.behaviour ?? this.status.behaviour;
    this.pinned = !!opts.pin;
    if (opts.behaviour) this.status.behaviour = opts.behaviour;

    const meta = this.byName.get(name);
    const mood = opts.behaviour ? BEHAVIOUR_MOOD[opts.behaviour] : null;
    if (mood && this.face) this.face.setMood(mood, 1);
    if (this.face) this.face.setSpeaking(opts.behaviour === "talking");

    // Dwell time: long enough to see a clip, short enough to feel alive.
    const dur = clip.duration || 2;
    this.transitionAt = this.elapsed + (opts.hold ?? Math.max(2.2, Math.min(6, dur * 0.85)));
    this.transitioning = false;
    if (meta) this.log(`${name} · ${meta.category} · ${dur.toFixed(2)}s · ${meta.licence}`);
    this._emit();
    return true;
  }

  async select(name) {
    this.manualPins.add(name);
    return this.playClip(name, { behaviour: this.status.behaviour, pin: true });
  }

  togglePlay() {
    this.playing = !this.playing;
    this.log(this.playing ? "play" : "pause");
    this._emit();
    return this.playing;
  }

  setLoop(on) {
    this.loop = !!on;
    if (this.action) {
      this.action.setLoop(this.loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
      this.action.clampWhenFinished = !this.loop;
    }
    this.log(`loop ${this.loop ? "on" : "off"}`);
    this._emit();
  }

  setSpeed(v) {
    this.speed = Math.max(0.1, Math.min(2, Number(v) || 1));
    if (this.action) this.action.setEffectiveTimeScale(this.speed);
    this.log(`speed ${this.speed.toFixed(2)}x`);
    this._emit();
  }

  restart() {
    if (this.action) {
      this.action.reset();
      this.action.play();
    }
    this.transitionAt = this.elapsed + 1.5;
    this.log("restart");
    this._emit();
  }

  setAuto(on) {
    this.auto = !!on;
    if (!this.auto) this.manualPins.clear();
    this.log(`auto ${this.auto ? "on" : "off"}`);
    this._emit();
  }

  /** One-shot behaviour, used by the test buttons. */
  trigger(behaviour) {
    this.manualPins.clear();
    const name = this._pick(behaviour) || this._pick("idle");
    if (!name) return false;
    return this.playClip(name, { behaviour, hold: 5.5 });
  }

  /**
   * Pause / resume the whole render loop. Used by the desktop-companion overlay
   * so a hidden character stops spending GPU time. Default stays "on" so the
   * preview page behaviour is unchanged; prod toggles it from the host page.
   */
  setRendering(on) {
    const enabled = !!on;
    if (enabled === this._renderingOn) return enabled;
    this._renderingOn = enabled;
    if (enabled) {
      this._loop();
    } else {
      this.renderer.setAnimationLoop(null);
    }
    this._emit();
    return enabled;
  }

  // ── frame loop ──────────────────────────────────────────────────────────
  _loop() {
    this.renderer.setAnimationLoop(() => {
      const dt = Math.min(this.clock.getDelta(), 0.1);
      this.elapsed += dt;
      if (this.playing) {
        // THE animation tick. Everything else in this file exists to feed it.
        this.mixer.update(dt);
        if (this.auto && !this.transitioning && this.elapsed >= this.transitionAt) {
          const behaviour = this._weightedBehaviour();
          const name = this._pick(behaviour);
          if (name) {
            this.playClip(name, { behaviour, hold: this._holdFor(behaviour) });
          } else {
            this.transitionAt = this.elapsed + 2.5;
          }
        }
      }
      // ESSENTIAL: createVRMAnimationClip() binds tracks to the VRM's
      // *normalized* proxy bones, not the bones the meshes are skinned to.
      // VRM.update() is what copies the normalized pose onto the real bone
      // hierarchy and steps the spring bones. Without it the mixer clock
      // advances and every bone "moves", but the skinned mesh stays frozen in
      // its bind pose -- i.e. a silently motionless avatar.
      //
      // Deliberately OUTSIDE the `playing` guard: spring bones are physics, not
      // transport. Pausing a clip freezes the mixer; it must not freeze the
      // hair, hood and bust simulation, or the strands stop colliding and sink
      // into the shoulders the moment the user hits pause.
      if (this.vrm) this.vrm.update(dt);
      if (this.face) this.face.update(dt, this.camera);
      this.renderer.render(this.scene, this.camera);
      this._tickStatus();
    });
  }

  _holdFor(behaviour) {
    if (behaviour === "walking") return 4.2;
    if (behaviour === "talking" || behaviour === "listening") return 5.0;
    return 3.0;
  }

  _tickStatus() {
    if (this.action && this.action.enabled) {
      const t = this.action.time;
      if (t !== this._lastActionTime) {
        this._lastActionTime = t;
        this._emit();
      }
    }
  }

  onChange(fn) {
    this._changeFn = fn;
  }

  _emit() {
    if (this._changeFn) this._changeFn(this.snapshot());
  }

  snapshot() {
    return {
      stage: this.status.stage,
      current: this.status.current,
      behaviour: this.status.behaviour,
      loaded: this.clipCache.size,
      transitioning: this.transitioning,
      speed: this.speed,
      loop: this.loop,
      auto: this.auto,
      playing: this.playing,
      time: this.action ? Number(this.action.time.toFixed(2)) : 0,
      duration: this.action && this.action.getClip() ? Number(this.action.getClip().duration.toFixed(2)) : 0,
      tracks: this.action && this.action.getClip() ? this.action.getClip().tracks.length : 0,
      skin: this.skin,
      errors: this.status.errors.slice(-4),
    };
  }
}

export { SKIN_PRESETS };