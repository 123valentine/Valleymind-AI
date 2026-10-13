/**
 * page.js -- UI wiring for the VRM companion preview.
 * Owns the DOM only. All animation decisions live in companion.js.
 */
import { Companion, SKIN_PRESETS } from "./companion.js";

const $ = (id) => document.getElementById(id);
const canvas = $("view");

function logLine(text, bad = false) {
  const box = $("log");
  if (!box) return;
  const div = document.createElement("div");
  div.textContent = text;
  if (bad) div.className = "bad";
  box.appendChild(div);
  while (box.childElementCount > 220) box.firstElementChild.remove();
  box.scrollTop = box.scrollHeight;
}

const engine = new Companion(canvas, logLine);

// ── tabs ────────────────────────────────────────────────────────────────
for (const [tabId, paneId] of [
  ["tabTest", "paneTest"],
  ["tabClips", "paneClips"],
  ["tabInfo", "paneInfo"],
]) {
  $(tabId).addEventListener("click", () => {
    for (const t of ["tabTest", "tabClips", "tabInfo"]) $(t).classList.toggle("on", t === tabId);
    for (const p of ["paneTest", "paneClips", "paneInfo"]) $(p).hidden = p !== paneId;
  });
}

// ── behaviour / transport ───────────────────────────────────────────────
const btnAuto = $("btnAuto");
btnAuto.addEventListener("click", () => {
  const on = btnAuto.textContent.includes("OFF");
  engine.setAuto(on);
  btnAuto.textContent = `Auto behaviour: ${on ? "ON" : "OFF"}`;
  btnAuto.classList.toggle("on", on);
});

const modeButtons = [...document.querySelectorAll("#modeGrid .btn")];
for (const b of modeButtons) {
  b.addEventListener("click", () => {
    modeButtons.forEach((o) => o.classList.remove("on"));
    b.classList.add("on");
    // A manual choice pins the clip, so auto stops overriding it.
    if (btnAuto.textContent.includes("ON")) btnAuto.click();
    engine.trigger(b.dataset.mode);
  });
}

const btnPlay = $("btnPlay");
btnPlay.addEventListener("click", () => {
  const playing = engine.togglePlay();
  btnPlay.textContent = playing ? "Pause" : "Play";
  btnPlay.classList.toggle("on", playing);
});

const btnLoop = $("btnLoop");
btnLoop.addEventListener("click", () => {
  engine.setLoop(!engine.loop);
  btnLoop.textContent = engine.loop ? "ON" : "OFF";
  btnLoop.classList.toggle("on", engine.loop);
});

$("btnRestart").addEventListener("click", () => engine.restart());

const speed = $("speed");
speed.addEventListener("input", () => {
  engine.setSpeed(speed.value);
  $("speedVal").textContent = `${Number(speed.value).toFixed(2)}x`;
});

// ── clip selector ───────────────────────────────────────────────────────
const clipList = $("clipList");
const search = $("search");

function fillSelector(filter = "") {
  const q = filter.trim().toLowerCase();
  const matched = engine.manifest.clips.filter((c) => {
    if (!q) return true;
    return (
      c.name.toLowerCase().includes(q) ||
      c.category.toLowerCase().includes(q) ||
      (c.sourceClip || "").toLowerCase().includes(q) ||
      (c.note || "").toLowerCase().includes(q)
    );
  });

  const frag = document.createDocumentFragment();
  const groups = new Map();
  for (const c of matched) {
    if (!groups.has(c.category)) groups.set(c.category, []);
    groups.get(c.category).push(c);
  }
  for (const [cat, list] of [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const og = document.createElement("optgroup");
    og.label = `${cat} (${list.length})`;
    for (const c of list) {
      const o = document.createElement("option");
      o.value = c.name;
      o.dataset.file = c.file;
      const dur = c.duration != null ? ` · ${c.duration.toFixed(1)}s` : "";
      o.textContent = `${c.name}${dur}`;
      og.appendChild(o);
    }
    frag.appendChild(og);
  }
  clipList.replaceChildren(frag);
  $("clipCount").textContent =
    `Showing ${matched.length} of ${engine.manifest.counts.total} clips` +
    (q ? ` matching "${filter.trim()}"` : "") +
    ` · ${engine.manifest.counts.active} active + ${engine.manifest.counts.spare} spare (extra/)`;

  // If the current clip is visible, keep it selected.
  if (engine.status.current) {
    for (const o of clipList.options) if (o.value === engine.status.current) o.selected = true;
  }
}

search.addEventListener("input", () => fillSelector(search.value));

clipList.addEventListener("change", () => {
  const c = engine.byName.get(clipList.value);
  if (!c) return;
  if (btnAuto.textContent.includes("ON")) btnAuto.click(); // pin it
  modeButtons.forEach((o) => o.classList.remove("on"));
  engine.select(c.name);
  $("clipMeta").textContent =
    `${c.category} · ${c.licence} · ${c.source}` + (c.sourceClip ? ` · from ${c.sourceClip}` : "");
});
clipList.addEventListener("click", () => {
  const c = engine.byName.get(clipList.value);
  if (c) $("clipMeta").textContent = `${c.category} · ${c.licence} · ${c.source}`;
});

// ── skin tone picker ────────────────────────────────────────────────────
const swatchGrid = $("skinSwatches");

function buildSkinPicker() {
  const frag = document.createDocumentFragment();
  for (const p of SKIN_PRESETS) {
    const wrap = document.createElement("div");
    wrap.className = "swatchRow";

    const b = document.createElement("button");
    b.className = "swatch";
    b.style.background = p.hex;
    b.dataset.skin = p.name;
    b.title = `${p.name} — ${p.hex}`;
    b.setAttribute("aria-label", p.name);

    const n = document.createElement("span");
    n.className = "swatchName";
    n.textContent = p.name;

    wrap.appendChild(b);
    wrap.appendChild(n);
    frag.appendChild(wrap);
  }
  swatchGrid.replaceChildren(frag);

  for (const b of swatchGrid.querySelectorAll(".swatch")) {
    b.addEventListener("click", () => engine.setSkinTone(b.dataset.skin));
  }
}

function updateSkinReadout(s) {
  const skin = s && s.skin;
  $("skinNameLbl").textContent = skin ? `${skin.name} — ${skin.hex}` : "Preserve original appearance";
  $("skinHexLbl").textContent = skin ? skin.hex : "";
  for (const b of swatchGrid.querySelectorAll(".swatch")) {
    b.classList.toggle("on", !!skin && b.dataset.skin === skin.name);
  }
}

$("btnSkinReset").addEventListener("click", () => engine.setSkinTone(null));
buildSkinPicker();
updateSkinReadout(engine.snapshot());

// ── status mirror ───────────────────────────────────────────────────────
let lastFired = null;
engine.onChange((s) => {
  $("nowClip").textContent = s.current ?? "—";
  $("nowBehaviour").textContent = s.behaviour ?? "idle";
  const pct = s.duration ? Math.min(100, (s.time / s.duration) * 100) : 0;
  $("nowBar").style.width = `${pct}%`;

  $("iStage").textContent = s.stage;
  $("iLoaded").textContent = s.loaded;
  $("iClip").textContent = s.current ?? "—";
  $("iTracks").textContent = s.tracks || "—";
  $("iTime").textContent = `${s.time.toFixed(2)}s / ${s.duration.toFixed(2)}s`;
  $("iErrors").textContent = s.errors.length ? s.errors.join(" | ") : "";
  updateSkinReadout(s);

  // Announce clip changes in the log only when they actually change.
  if (s.current !== lastFired) {
    lastFired = s.current;
    if (s.stage === "ready") fillSelector(search.value);
  }
});

// ── boot ────────────────────────────────────────────────────────────────
(async () => {
  try {
    await engine.boot();
    $("iVrm").textContent = engine.vrm.meta?.metaVersion ?? "?";
    $("iBones").textContent = engine.vrm.humanoid?.humanBones?.length ?? "?";
    $("iHeight").textContent = `${(engine.fullBodyHeight ?? 0).toFixed(2)}m`;
    $("iTotal").textContent = engine.manifest.counts.total;
    const lic = engine.manifest.counts.byLicence;
    $("iLicence").innerHTML = Object.entries(lic)
      .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
      .join("") + `<tr><td>Total</td><td>${engine.manifest.counts.total}</td></tr>`;
    fillSelector();
    $("boot").classList.add("done");
    logLine("preview ready — full body framed, idle running");
  } catch (err) {
    $("boot").classList.add("failed");
    $("bootTitle").textContent = "Failed to start preview";
    $("bootMsg").textContent = String(err?.message || err);
    logLine(`FATAL: ${err?.message || err}`, true);
    console.error(err);
  }
})();

// Test hooks for the automated browser check.
window.__preview = {
  engine,
  face: () => engine.face,
  snapshot: () => engine.snapshot(),
  state: () => ({
    vrmVersion: engine.vrm?.meta?.metaVersion ?? null,
    bones: engine.vrm?.humanoid?.humanBones?.length ?? 0,
    total: engine.manifest?.counts?.total ?? 0,
    active: engine.manifest?.counts?.active ?? 0,
    categories: engine.manifest?.categories ?? [],
    clipCount: engine.manifest?.clips?.length ?? 0,
  }),
  select: (n) => engine.select(n),
  trigger: (b) => engine.trigger(b),
  auto: (on) => engine.setAuto(on),
  setSkinTone: (n) => engine.setSkinTone(n),
  skin: () => engine.skin,
  projectWorld: (x, y, z) => engine.projectWorld(x, y, z),
  skinMaterials: () =>
    [...engine._skinState.entries()].map(([m, o]) => ({
      name: m.name,
      color: m.color ? m.color.toArray() : null,
      shade: m.shadeColorFactor ? m.shadeColorFactor.toArray() : null,
      origColor: o.color ? o.color.toArray() : null,
      origShade: o.shade ? o.shade.toArray() : null,
    })),
  materials: () => {
    const out = {};
    engine.vrm.scene.traverse((o) => {
      if (!o.isMesh) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) {
        if (!m || !m.name || out[m.name]) continue;
        out[m.name] = {
          color: m.color ? m.color.toArray() : null,
          shade: m.shadeColorFactor ? m.shadeColorFactor.toArray() : null,
        };
      }
    });
    return out;
  },
};