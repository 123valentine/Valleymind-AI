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
 * A real speech pipeline must now provide BOTH gates before any mouth morph is
 * allowed to open: setSpeaking(true) while TTS is active AND non-zero level
 * samples from that same playback. No audio means a perfectly closed mouth —
 * there is deliberately no simulated envelope any more.
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
    this.speaking = false; // TTS playback gate
    this.audioActive = false; // a real level sample has arrived from that playback
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

  /**
   * Opens the TTS gate. This alone never moves the mouth: a level sample from
   * the active utterance must also arrive through setTalkLevel().
   */
  setSpeaking(on) {
    this.speaking = !!on;
    if (!this.speaking) this._closeMouth();
  }

  /**
   * True ONLY when TTS is active and its audio is producing level samples.
   * Every morph write in update() is gated on this exact predicate.
   */
  get isSpeaking() {
    return this.speaking === true && this.audioActive === true;
  }

  /**
   * Direct mouth control from the active TTS amplitude tap.
   * @param {number} level 0..1
   */
  setTalkLevel(level) {
    const v = Math.max(0, Math.min(1, Number(level) || 0));
    // A late level event from a finished/interrupted utterance must not reopen
    // the mouth, and setTalkLevel() must never turn the TTS gate on by itself.
    if (!this.speaking || v <= 0.01) {
      this.audioActive = false;
      this.targetLevel = 0;
      if (!this.speaking) this._closeMouth();
      return;
    }
    this.audioActive = true;
    this.targetLevel = v;
  }

  _closeMouth() {
    this.audioActive = false;
    this.targetLevel = 0;
    this.level = 0;
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

    // ---- TTS-only mouth: NEVER synthesise speech motion from silence ----
    if (this.isSpeaking) {
      // Ease toward the measured amplitude of the utterance that is playing.
      this.level += (this.targetLevel - this.level) * Math.min(1, dt * 18);
    } else {
      this.level += (0 - this.level) * Math.min(1, dt * 22);
      if (this.level < 0.002) this.level = 0;
    }

    const m = this.vrm.expressionManager;
    // Write ALL viseme morphs every frame. A hidden test can freeze the engine
    // with no audio and prove aa/ih/ou/ee/oh are exactly zero; a stale value
    // from a previous utterance is never allowed to survive.
    const open = this.isSpeaking ? this.level : 0;
    m.setValue("aa", open * 0.85);
    m.setValue("ih", open * 0.45 * (0.5 + 0.5 * Math.sin(this.t * 9.1)));
    m.setValue("ou", open * 0.5 * (0.5 + 0.5 * Math.sin(this.t * 6.3 + 2.0)));
    m.setValue("ee", open * 0.2 * (0.5 + 0.5 * Math.sin(this.t * 11.7 + 0.4)));
    m.setValue("oh", open * 0.55 * (0.5 + 0.5 * Math.sin(this.t * 7.7 + 1.9)));

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