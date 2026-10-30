/**
 * VoiceOrb - liquid-metal neon orb (WebGL, zero assets, zero dependencies).
 *
 *   import { VoiceOrb, VOICE_PRESETS } from './VoiceOrb.js';
 *   const orb = new VoiceOrb(containerElement, { preset: 'deep' });
 *   orb.setState('playing');            // 'idle' | 'playing' | 'selected'
 *   orb.connectAudio(audioElement);     // real audio drives the motion
 *   orb.setLevel(0.6);                  // or drive it manually (0..1)
 *   orb.setPreset('bright');            // change the voice look
 *   orb.destroy();                      // on unmount
 */

export const VOICE_PRESETS = {
  // colors are [r,g,b] 0..1. speed = motion speed, swirl = fold twist, gain = audio sensitivity
  deep:   { colors: [[0.10, 0.35, 1.00], [0.00, 0.90, 1.00], [0.45, 0.20, 1.00]], speed: 0.65, swirl: 0.8, gain: 1.0 },
  warm:   { colors: [[1.00, 0.45, 0.10], [1.00, 0.15, 0.45], [1.00, 0.85, 0.25]], speed: 0.85, swirl: 1.0, gain: 1.0 },
  bright: { colors: [[1.00, 0.10, 0.85], [0.55, 0.30, 1.00], [0.10, 0.95, 1.00]], speed: 1.25, swirl: 1.3, gain: 1.1 },
  fresh:  { colors: [[0.35, 1.00, 0.15], [0.00, 0.95, 0.85], [0.15, 0.45, 1.00]], speed: 1.00, swirl: 1.1, gain: 1.0 },
};

const VERT = `
attribute vec2 aPos;
varying vec2 vUv;
void main(){ vUv = aPos; gl_Position = vec4(aPos, 0.0, 1.0); }
`;

const FRAG = `
precision highp float;
varying vec2 vUv;
uniform vec2  uRes;
uniform float uTime;
uniform float uLevel;   // 0..1 smoothed audio level
uniform float uSwirl;
uniform float uPulse;   // 0..1 "selected" pulse
uniform vec3  uC0;
uniform vec3  uC1;
uniform vec3  uC2;

#define PI 3.14159265

float hash(vec2 p){ p = fract(p*vec2(123.34, 456.21)); p += dot(p, p+45.32); return fract(p.x*p.y); }
float noise(vec2 p){
  vec2 i = floor(p), f = fract(p);
  f = f*f*(3.0-2.0*f);
  return mix(mix(hash(i), hash(i+vec2(1,0)), f.x),
             mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y);
}

// Height field of the folded liquid-metal surface, in polar coordinates around the centre hole.
float height(vec2 xy, float t){
  float r  = length(xy);
  float a  = atan(xy.y, xy.x);
  float tw = a + uSwirl * (0.9 / (r + 0.25)) - t * 0.35;           // twist toward the hole
  float amp = 0.55 + uLevel * 0.9;
  float h  = 0.0;
  h += sin(tw * 4.0 + r * 5.0 - t * 1.6) * 0.60 * amp;               // big petals
  h += sin(tw * 7.0 - r * 8.0 + t * 1.1) * 0.14 * amp;              // finer folds
  h += (noise(vec2(cos(tw), sin(tw)) * 1.6 + vec2(r * 3.5, t * 0.3 + r)) - 0.5) * 1.1;  // seam-free organic irregularity
  h *= smoothstep(0.0, 0.45, r) * (1.0 - 0.25 * r);                   // flat in the hole
  return h;
}

vec3 palette(float x){
  x = fract(x) * 3.0;
  vec3 c = mix(uC0, uC1, smoothstep(0.0, 1.0, x));
  c = mix(c, uC2, smoothstep(1.0, 2.0, x));
  c = mix(c, uC0, smoothstep(2.0, 3.0, x));
  return c;
}

void main(){
  vec2 uv = vUv * vec2(uRes.x / min(uRes.x, uRes.y), uRes.y / min(uRes.x, uRes.y));
  float t = uTime;

  float ang = atan(uv.y, uv.x);
  float len = length(uv);

  // Wobbling outline: breathes at idle, bulges with audio, pops on select.
  float breathe = 0.018 * sin(t * 1.2);
  float wob = 0.012 * sin(ang * 3.0 + t * 1.3) + 0.008 * sin(ang * 5.0 - t * 1.9)
            + uLevel * (0.050 * sin(ang * 4.0 + t * 4.0) + 0.030 * sin(ang * 7.0 - t * 5.5));
  float R = 0.74 + breathe + wob + uPulse * 0.07;

  // Soft outer glow
  float glow = exp(-max(len - R, 0.0) * 7.0) * (0.10 + 0.22 * uLevel + 0.25 * uPulse);
  vec3 glowCol = palette(t * 0.04 + 0.3) * glow;

  if (len > R) {
    gl_FragColor = vec4(glowCol, glow);
    return;
  }

  // Sphere point + slow rotation of the pattern
  vec2 p = uv / R;
  float z = sqrt(max(1.0 - dot(p, p), 0.0));
  float rot = t * 0.12;
  vec2 q = vec2(cos(rot)*p.x - sin(rot)*p.y, sin(rot)*p.x + cos(rot)*p.y);

  // Bump-mapped normal from the height field
  float e = 0.012;
  float h  = height(q, t);
  float hx = height(q + vec2(e, 0.0), t);
  float hy = height(q + vec2(0.0, e), t);
  vec2 grad = vec2(hx - h, hy - h) / e;
  vec3 n = normalize(vec3(p, z) + vec3(-grad, 0.0) * (0.24 + 0.16 * uLevel));
  vec3 v = vec3(0.0, 0.0, 1.0);
  vec3 rf = reflect(-v, n);

  // Lighting: sharp neon highlights on a dark chrome body
  vec3 L1 = normalize(vec3(cos(t*0.5)*0.9, sin(t*0.4)*0.9, 0.55));
  vec3 L2 = normalize(vec3(-sin(t*0.37)*0.9, -cos(t*0.52)*0.9, 0.5));
  float s1 = pow(max(dot(rf, L1), 0.0), 28.0);
  float s2 = pow(max(dot(rf, L2), 0.0), 28.0);
  float fres = pow(1.0 - max(dot(n, v), 0.0), 3.0);

  // chrome "environment" stripes reflected in the folds
  float stripes = pow(abs(sin(rf.x * 3.0 + rf.y * 2.0 + h * 2.0 + t * 0.4)), 40.0);

  float ph = h * 0.8 + rf.y * 0.6 + rf.x * 0.4 + t * 0.05;
  vec3 irid = palette(ph);

  vec3 col = vec3(0.006, 0.008, 0.016);
  col += irid * (s1 * 3.2 + s2 * 2.4);
  col += palette(ph + 0.4) * stripes * (0.7 + 0.9 * uLevel);
  col += irid * fres * (0.12 + 0.35 * uLevel);
  col += vec3(1.0) * (pow(s1, 2.0) + pow(s2, 2.0)) * 0.55;       // hot white cores

  // sphere shading: darker toward the edge, brighter lit side
  col *= mix(0.2, 1.0, z * z);

  // Dark hole in the middle
  float rr = length(q);
  float hole = smoothstep(0.06, 0.42, rr);
  col *= mix(0.05, 1.0, hole);
  col += irid * (1.0 - hole) * 0.04 * (0.4 + uLevel);

  // Selected pulse: brief bright flash
  col += palette(t * 0.2) * uPulse * 0.35 * fres;

  // Anti-aliased edge
  float edge = smoothstep(R, R - 0.012, len);
  col = col / (1.0 + col * 0.35);                              // soft tone-map
  col = pow(col, vec3(0.92));
  gl_FragColor = vec4(mix(glowCol, col, edge), max(edge, glow));
}
`;

export class VoiceOrb {
  constructor(container, opts = {}) {
    this.container = container;
    this.state = 'idle';
    this.level = 0;          // smoothed
    this.targetLevel = 0;    // raw
    this.pulse = 0;
    this.time = Math.random() * 10;
    this.running = false;
    this.destroyed = false;
    this.maxDpr = opts.maxDpr || 2;
    this.reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText = 'width:100%;height:100%;display:block;';
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', opts.ariaLabel || 'Voice animation');
    container.appendChild(this.canvas);

    this.gl = this.canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false, antialias: false });
    if (!this.gl) { this._fallback(); return; }

    this._initGL();
    this.setPreset(opts.preset || 'deep');

    this._onResize = () => this._resize();
    this._ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(this._onResize) : null;
    if (this._ro) this._ro.observe(container); else window.addEventListener('resize', this._onResize);
    this._resize();

    this._onVis = () => (document.hidden ? this._stop() : this._start());
    document.addEventListener('visibilitychange', this._onVis);

    this._io = typeof IntersectionObserver !== 'undefined'
      ? new IntersectionObserver(es => es.forEach(e => (e.isIntersecting ? this._start() : this._stop())))
      : null;
    if (this._io) this._io.observe(container); else this._start();
  }

  // ---------- public API ----------
  setPreset(nameOrObj) {
    const p = typeof nameOrObj === 'string' ? VOICE_PRESETS[nameOrObj] : nameOrObj;
    if (!p) return;
    this.preset = p;
    if (this.reduceMotion) this._draw();
  }

  setState(state) {
    if (state === 'selected' && this.state !== 'selected') this.pulse = 1;
    this.state = state;
    if (state === 'playing') this._start();          // ensure the animation loop is running
    if (state !== 'playing' && !this._analyser) this.targetLevel = 0;
  }

  setLevel(v) { this.targetLevel = Math.max(0, Math.min(1, v)); }

  /** Drive the orb from a real <audio>/<video> element. Call from a user gesture (tap) at least once. */
  connectAudio(mediaEl) {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      this._ctx = this._ctx || new AC();
      if (this._ctx.state === 'suspended') this._ctx.resume();
      if (!mediaEl.__orbSource) {                        // createMediaElementSource may only be called once per element
        mediaEl.__orbSource = this._ctx.createMediaElementSource(mediaEl);
        mediaEl.__orbSource.connect(this._ctx.destination);
      }
      this._analyser = this._ctx.createAnalyser();
      this._analyser.fftSize = 256;
      this._analyser.smoothingTimeConstant = 0.7;
      mediaEl.__orbSource.connect(this._analyser);
      this._buf = new Uint8Array(this._analyser.frequencyBinCount);
      return true;
    } catch (e) { this._analyser = null; return false; }  // cross-origin audio etc: fall back to simulated pulse
  }

  disconnectAudio() { this._analyser = null; this.targetLevel = 0; }

  destroy() {
    this.destroyed = true;
    this._stop();
    document.removeEventListener('visibilitychange', this._onVis);
    if (this._ro) this._ro.disconnect(); else window.removeEventListener('resize', this._onResize);
    if (this._io) this._io.disconnect();
    if (this._ctx) { try { this._ctx.close(); } catch (e) {} }
    const lose = this.gl && this.gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    this.canvas.remove();
  }

  // ---------- internals ----------
  _initGL() {
    const gl = this.gl;
    const sh = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    gl.useProgram(prog);
    this.prog = prog;

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'aPos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    this.u = {};
    ['uRes', 'uTime', 'uLevel', 'uSwirl', 'uPulse', 'uC0', 'uC1', 'uC2']
      .forEach(n => (this.u[n] = gl.getUniformLocation(prog, n)));
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0, 0, 0, 0);
  }

  _resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, this.maxDpr);
    const w = Math.max(1, Math.round(this.container.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.container.clientHeight * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w; this.canvas.height = h;
      this.gl.viewport(0, 0, w, h);
    }
    if (this.reduceMotion) this._draw();
  }

  _start() {
    if (this.running || this.destroyed || this.reduceMotion) return;
    this.running = true;
    this._last = performance.now();
    const loop = (now) => {
      if (!this.running) return;
      const dt = Math.min((now - this._last) / 1000, 0.05);
      this._last = now;
      this._update(dt);
      this._draw();
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  _stop() { this.running = false; if (this._raf) cancelAnimationFrame(this._raf); }

  _update(dt) {
    const p = this.preset;
    this.time += dt * p.speed * (1 + this.level * 1.2);

    // Pick the target level
    let target = this.targetLevel;
    if (this._analyser) {
      this._analyser.getByteFrequencyData(this._buf);
      let sum = 0; const n = Math.min(this._buf.length, 48);   // voice lives in the low bins
      for (let i = 0; i < n; i++) sum += this._buf[i];
      target = Math.min(1, (sum / n / 255) * 1.8 * p.gain);
    } else if (this.state === 'playing') {
      // simulated speech-like pulse when audio can't be analysed
      const s = this.time * 3.0;
      target = 0.35 + 0.25 * Math.sin(s) * Math.sin(s * 0.37 + 1.0) + 0.15 * Math.sin(s * 2.3);
      target = Math.max(0, target) * p.gain;
    }
    // fast attack, slow release = organic feel
    const k = target > this.level ? 14 : 4;
    this.level += (target - this.level) * Math.min(1, dt * k);

    // selected pulse decays
    if (this.pulse > 0) this.pulse = Math.max(0, this.pulse - dt * 1.6);
  }

  _draw() {
    const gl = this.gl, u = this.u, p = this.preset;
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(u.uRes, this.canvas.width, this.canvas.height);
    gl.uniform1f(u.uTime, this.time);
    gl.uniform1f(u.uLevel, this.level);
    gl.uniform1f(u.uSwirl, p.swirl);
    gl.uniform1f(u.uPulse, this.pulse);
    gl.uniform3fv(u.uC0, p.colors[0]);
    gl.uniform3fv(u.uC1, p.colors[1]);
    gl.uniform3fv(u.uC2, p.colors[2]);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  _fallback() {
    // No WebGL: show a simple animated gradient orb instead.
    this.canvas.remove();
    const d = document.createElement('div');
    d.style.cssText = 'width:100%;height:100%;border-radius:50%;background:radial-gradient(circle at 35% 35%,#00e5ff,#2a1bff 45%,#05050a 75%);box-shadow:0 0 40px #4a3cff88;';
    this.container.appendChild(d);
    this.setPreset = this.setState = this.setLevel = this.connectAudio = this.disconnectAudio = () => false;
    this.destroy = () => d.remove();
  }
}
