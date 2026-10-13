/**
 * face.js -- ValleyMind face channel.
 *
 * ARCHITECTURAL BOUNDARY (do not merge this into companion.js):
 * This module owns ONLY facial concerns: VRM expressions, blinking, gaze and
 * mouth shape. It never touches the AnimationMixer, never plays a .vrma body
 * clip, and knows nothing about the behaviour state machine.
 *
 * The point of the split is future TTS lip-sync. A real speech pipeline will
 * hand us a per-frame loudness envelope; that plugs into setTalkLevel() alone,
 * with zero changes to the body engine:
 *
 *     const rms = analyser.getByteTimeDomainData(buf);  // Web Audio
 *     face.setTalkLevel(rms01(rms));                   // 0..1 mouth openness
 *
 * Until then setSpeaking(true) drives a simulated envelope so the wiring is
 * visibly correct before any audio exists.
 */

import * as THREE from "three";

const EXPRESSIONS = ["happy", "angry", "sad", "relaxed", "surprised", "blink"];

// VRMLookAt.lookAt() takes a single THREE.Vector3 (it does `vec.copy(position)`),
// so it must be a Vector3 -- passing loose numbers silently yields NaN yaw/pitch.
const _gazeTarget = new THREE.Vector3();

export class FaceChannel {
  constructor(vrm) {
    this.vrm = vrm;
    this.level = 0; // 0..1 current mouth openness
    this.targetLevel = 0;
    this.speaking = false;
    this.t = 0;
    this.blinkAt = 2.5;
    // Steady gaze so the head is not dead even while the body idles.
    this.gaze = { x: 0, y: 0 };
    this.enabled = true;
  }

  /** Mood expression, e.g. "happy" / "sad" / "angry" / "relaxed". */
  setMood(name, weight = 1) {
    if (!this.vrm.expressionManager) return;
    for (const e of EXPRESSIONS) {
      if (e === "blink") continue;
      this.vrm.expressionManager.setValue(e, e === name ? weight : 0);
    }
  }

  /** True while the companion should appear to be speaking. */
  setSpeaking(on) {
    this.speaking = !!on;
  }

  /**
   * Direct mouth control. This is the hook a real TTS/audio lip-sync will use.
   * @param {number} level 0..1
   */
  setTalkLevel(level) {
    this.targetLevel = Math.max(0, Math.min(1, Number(level) || 0));
    this.speaking = this.targetLevel > 0.01;
  }

  /** -1..1 horizontal, -1..1 vertical gaze bias. */
  setGaze(x, y) {
    this.gaze.x = Math.max(-1, Math.min(1, x));
    this.gaze.y = Math.max(-1, Math.min(1, y));
  }

  /**
   * Aim the eyes at the viewer.
   *
   * VRMLookAt.lookAt() takes a WORLD-space point (three-vrm: "A target position,
   * in world space"). The old call fed it a couple of tenths of a unit around the
   * origin, which is a metre and a half below and behind the head, so the gaze
   * solver dutifully rolled the irises into the bottom of every eye socket and
   * the model's authored eyes read as wrong. The camera sits at the viewer's own
   * eyes, so that is the target, and the idle wander is applied as a world-space
   * offset scaled by the distance to the camera -- that keeps the angular
   * amplitude constant whether the shot is full body or a head close-up.
   */
  _gaze(dt, camera) {
    if (!this.vrm.lookAt) return;

    const head = this.vrm.humanoid ? this.vrm.humanoid.getRawBoneNode("head") : null;
    const hm = head ? head.matrixWorld.elements : null;
    const hx = hm ? hm[12] : 0;
    const hy = hm ? hm[13] : 1.4;
    const hz = hm ? hm[14] : 0;

    // Where the viewer is, in world space.
    let ax = hx;
    let ay = hy;
    let az = hz + 4;
    if (camera) {
      camera.updateMatrixWorld();
      const e = camera.matrixWorld.elements;
      ax = e[12];
      ay = e[13];
      az = e[14];
    }

    const dist = Math.hypot(ax - hx, ay - hy, az - hz);
    const d = dist > 0.05 ? dist : 4; // never divide by a camera inside the skull
    const t = this.t;
    const wx = (this.gaze.x * 0.05 + 0.02 * Math.sin(t * 0.37)) * d;
    const wy = (this.gaze.y * 0.04 + 0.014 * Math.sin(t * 0.29 + 1.1)) * d;

    // Ease toward the live target so a camera cut never snaps the eyes.
    if (this._lookX === undefined) {
      this._lookX = ax;
      this._lookY = ay;
      this._lookZ = az;
    }
    const k = Math.min(1, dt * 6);
    this._lookX += (ax + wx - this._lookX) * k;
    this._lookY += (ay + wy - this._lookY) * k;
    this._lookZ += (az - this._lookZ) * k;

    this.vrm.lookAt.lookAt(_gazeTarget.set(this._lookX, this._lookY, this._lookZ));
  }

  update(dt, camera) {
    if (!this.enabled || !this.vrm.expressionManager) return;
    this.t += dt;

    // ---- simulated speech envelope (stands in for a TTS amplitude track) ----
    if (this.speaking && this.targetLevel <= 0.01) {
      const wobble =
        0.45 +
        0.3 * Math.sin(this.t * 13.7) +
        0.2 * Math.sin(this.t * 27.1 + 1.3) +
        0.12 * Math.sin(this.t * 41.3 + 0.7);
      this.level = Math.max(0, Math.min(1, wobble));
    } else {
      // ease toward whatever setTalkLevel asked for (or closed when idle)
      this.level += (this.targetLevel - this.level) * Math.min(1, dt * 18);
    }

    const m = this.vrm.expressionManager;
    // Spread the level across the three jaw shapes so the mouth reads as speech.
    m.setValue("aa", this.level * 0.85);
    m.setValue("ih", this.level * 0.45 * (0.5 + 0.5 * Math.sin(this.t * 9.1)));
    m.setValue("ou", this.level * 0.5 * (0.5 + 0.5 * Math.sin(this.t * 6.3 + 2.0)));
    m.setValue("ee", this.level * 0.2 * (0.5 + 0.5 * Math.sin(this.t * 11.7 + 0.4)));

    // ---- blinking ----
    if (this.t > this.blinkAt) {
      const phase = this.t - this.blinkAt;
      const closed = phase < 0.12 ? Math.sin((phase / 0.12) * Math.PI) : 0;
      m.setValue("blink", closed);
      if (phase > 0.12) this.blinkAt = this.t + 2.2 + Math.random() * 3.4;
    } else {
      m.setValue("blink", 0);
    }

    // ---- gaze ----
    this._gaze(dt, camera);
  }
}