// vm-b STEP 3 tests (TTS/voices/menus)
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0,180) : '')); fail++; }
}
const html = fs.readFileSync('index.html', 'utf8');
const settings = fs.readFileSync(path.join('static','settings.js'), 'utf8');

// --- 1) voice id -> profile mapping for all six voices ---
(function(){
  const m = html.match(/function _savedVoiceProfile\(persona\)[\s\S]*?var map = \{([^}]+)\}/);
  const mapBody = m ? m[1] : '';
  const has4 = /guy.*marcus/.test(mapBody) && /aria.*elena/.test(mapBody) && /jenny.*angelina/.test(mapBody) && /christopher.*companion/.test(mapBody);
  assert('savedVoiceProfile maps guy/aria/jenny/christopher to marcus/elena/angelina/companion', has4, mapBody);
  assert('_vmTtsPayload sends explicit voice id (supports Piper ids)', /function _vmTtsPayload[\s\S]*?body\.voice\s*=\s*vid/.test(html));
  assert('Piper ids kristin_piper/ljspeech_piper present in catalogue', /kristin_piper/.test(settings) && /ljspeech_piper/.test(settings));
  try {
    const ls = {_d:{},getItem:function(k){return Object.prototype.hasOwnProperty.call(this._d,k)?this._d[k]:null;},setItem:function(k,v){this._d[k]=String(v);}};
    const ctx = { console, localStorage:ls, currentPersona:'marcus' };
    vm.createContext(ctx);
    vm.runInContext('var localStorage = globalThis.localStorage; function _vmDefaultCompanionVoiceId(){ return "jenny"; } ' + html.match(/function _vmSavedPersonaVoiceId\(persona\) \{[\s\S]*?\n\}/)[0] + '\n' + html.match(/function _vmTtsPayload\(text, persona\) \{[\s\S]*?\n\}/)[0] + '\nfunction _savedVoiceProfile(p){var map={guy:"marcus",aria:"elena",jenny:"angelina",christopher:"companion"};return map[_vmSavedPersonaVoiceId(p)]||"";}', ctx);
    const cases = [['marcus','guy'],['elena','aria'],['angelina','jenny'],['companion','christopher'],['marcus','kristin_piper'],['marcus','ljspeech_piper']];
    let all = true, bad='';
    cases.forEach(c => { ls._d.vmPersonaVoices = JSON.stringify({[c[0]]: c[1]}); const body = ctx._vmTtsPayload('hi', c[0]); if (body.voice !== c[1] || body.persona !== c[0]) { all=false; bad=JSON.stringify(c)+'=>'+JSON.stringify(body); } });
    assert('voice id -> /api/tts payload.voice mapping for all 6 voices', all, bad);
  } catch(e){ assert('voice id -> /api/tts payload.voice mapping for all 6 voices', false, e.message); }
})();

// --- 2) changing saved voice changes profile/voice sent to /api/tts ---
(function(){
  try {
    const ls = {_d:{},getItem:function(k){return this._d[k]||null;},setItem:function(k,v){this._d[k]=String(v);}};
    const ctx = { console, localStorage:ls, currentPersona:'marcus' };
    vm.createContext(ctx);
    vm.runInContext('var localStorage=globalThis.localStorage; function _vmDefaultCompanionVoiceId(){return "jenny";} ' + html.match(/function _vmSavedPersonaVoiceId\(persona\) \{[\s\S]*?\n\}/)[0] + '\n' + html.match(/function _vmTtsPayload\(text, persona\) \{[\s\S]*?\n\}/)[0], ctx);
    ls._d.vmPersonaVoices = JSON.stringify({marcus:'guy'}); const a = ctx._vmTtsPayload('hi','marcus').voice;
    ls._d.vmPersonaVoices = JSON.stringify({marcus:'aria'}); const b = ctx._vmTtsPayload('hi','marcus').voice;
    assert('changing saved voice changes voice sent to /api/tts', a==='guy' && b==='aria', a+','+b);
  } catch(e){ assert('changing saved voice changes voice sent to /api/tts', false, e.message); }
})();

// --- 3) cloud-over-local on load ---
(function(){
  assert('hydrate function merges cloud over local (cloud wins)', /function _vmHydratePersonaVoices/.test(settings) && /cloudRaw/.test(settings));
  assert('cloud persona_voices keys overwrite local keys on hydrate', /for \(k in cloudRaw\)[\s\S]*merged\[k\] = cloudRaw\[k\]/.test(settings));
  assert('merged persona_voices written back to localStorage', /_writePersonaVoices\(merged\)/.test(settings));
})();

// --- 4) saving Main Chat does not change Companion and the reverse ---
(function(){
  const mainSave = settings.match(/function _vmPreviewSaveVoice[\s\S]*?if \(destination === "main"\) \{([\s\S]*?)\} else if \(destination === "companion"\)/);
  const onlyMain = mainSave && /marcus/.test(mainSave[1]) && /elena/.test(mainSave[1]) && /angelina/.test(mainSave[1]) && !/companion\s*=/.test(mainSave[1]);
  assert('saving Main Chat writes only marcus/elena/angelina', !!onlyMain);
  assert('saving Companion writes only companion key', /destination === "companion"\) \{\s*saved\.companion = voiceId;/.test(settings));
  // vmAssignVoiceDestination also isolates
  assert('assignment "Use in Main Chat" targets main personas only', /destination === "main"[\s\S]{0,120}\["marcus", "elena", "angelina"\]/.test(settings));
  assert('assignment "Use in Companion" targets only companion', /destination === "companion"[\s\S]{0,80}saved\.companion = vmSelectedVoice/.test(settings));
})();

// --- 5) default companion is female only when unset ---
(function(){
  assert('default companion voice is jenny (female Edge, else aria)', /_vmDefaultCompanionVoiceId\(\) \{ return "jenny"/.test(html));
  assert('normalizer sets companion only when unset', /if \(!out\.companion\) out\.companion = _vmDefaultCompanionPersonaVoice\(\)/.test(settings));
  try {
    const ctx = { console, VM_PERSONA_VOICES:{voices:[{id:'jenny'},{id:'aria'}]} };
    vm.createContext(ctx);
    vm.runInContext('function _personaVoiceById(id){return (VM_PERSONA_VOICES.voices||[]).find(function(v){return v.id===id})||null;} ' + settings.match(/function _vmDefaultCompanionPersonaVoice\(\) \{[\s\S]*?\n\}/)[0] + '\n' + settings.match(/function _vmNormalizePersonaVoices\(map\) \{[\s\S]*?\n\}/)[0], ctx);
    assert('companion defaults to jenny when unset', ctx._vmNormalizePersonaVoices({ marcus:'guy' }).companion==='jenny');
    assert('saved companion choice never overridden', ctx._vmNormalizePersonaVoices({ marcus:'guy', companion:'aria' }).companion==='aria');
  } catch(e){ assert('default companion logic runtime', false, e.message); }
})();

// --- 6) unknown profile / playback failure falls back to saved Edge voice (never silence) ---
(function(){
  const tvStart = html.indexOf('window.toggleVoicePlayback');
  const tvEnd = html.indexOf('function quickSend', tvStart);
  const tvBody = tvStart >= 0 && tvEnd > tvStart ? html.slice(tvStart, tvEnd) : '';
  assert('Speak Aloud plays returned url without piper_local-only engine filter', tvBody && /_vmPlayTtsAudio\(data\.url\)/.test(tvBody) && !/data\.engine\s*===\s*['"]piper_local['"]\s*&&/.test(tvBody));
  assert('Speak Aloud sends profile/voice for reply persona', tvBody && /_vmTtsPayload\(text, persona\)/.test(tvBody) && /wrapper\.dataset\.persona/.test(tvBody));
  assert('_vmEdgeFallbackPlay exists and maps to safe Edge voice', /function _vmEdgeFallbackPlay/.test(html) && /emap\[p\]/.test(html));
  assert('Speak Aloud shows "voice not available" and falls back to Edge', tvBody && /voice not available/.test(tvBody) && /_vmEdgeFallbackPlay\(text, persona\)/.test(tvBody));
  assert('Speak Aloud uses reply persona (data-persona) at press time', tvBody && /wrapper\.dataset\.persona/.test(tvBody) && /_vmTtsPayload\(text, persona\)/.test(tvBody));
  assert('AI reply stores data-persona', /wrapper\.dataset\.persona\s*=\s*\(typeof currentPersona/.test(html));
})();

// --- 7) long-press opens the menu and Edit fills the composer without sending ---
(function(){
  assert('user message menu has Copy/Select text/Edit', /id="userMessageMenu"/.test(html) && /_vmUserMsgAction\('edit'\)/.test(html) && /Select text/.test(html));
  assert('long-press ~500ms opens user message menu', /setTimeout\(function\(\)\{\s*_vmOpenUserMsgMenu\(startTarget,\s*startTarget,\s*startX,\s*startY\);\s*\},\s*500\)/.test(html));
  assert('right-click (contextmenu) opens user message menu', /addEventListener\("contextmenu"[\s\S]*?\.user-message[\s\S]*?_vmOpenUserMsgMenu/.test(html));
  const actStart = html.indexOf('function _vmUserMsgAction(action)');
  const actEnd = html.indexOf('\nfunction _vmCloseUserMsgMenu', actStart) > 0 ? html.indexOf('\nfunction _vmCloseUserMsgMenu', actStart) : (html.indexOf('\n(function _vmInitUserMsgMenuHandlers', actStart));
  const actBody = actStart >= 0 ? html.slice(actStart, actEnd > actStart ? actEnd : actStart + 800) : '';
  assert('Edit fills chatInput without sending', /action === "edit"/.test(actBody) && /ci\.value\s*=\s*text/.test(actBody) && /ci\.focus\(\)/.test(actBody) && !/\bsend\s*\(/.test(actBody));
  assert('AI menu has Read aloud and Report a bug and Report an issue', /Read aloud/.test(html) && /Report a bug/.test(html) && /Report an issue/.test(html));
  assert('menus positioned via positionMenuAbove above button', /positionMenuAbove\("messageContextMenu"/.test(html) && /positionMenuAbove\("userMessageMenu"/.test(html));
  // DOM simulation for Edit
  try {
    const input = { value:'', focus:function(){this._f=true;}, dispatchEvent:function(){}, style:{} };
    const menu = {classList:{remove:function(){},contains:function(){return false;}},style:{}};
    const doc = { getElementById:function(id){ if(id==='chatInput') return input; if(id==='userMessageMenu') return menu; return null; }, addEventListener:function(){}, createRange:function(){return {selectNodeContents:function(){}}} };
    const ctx = { console, document:doc, window:{getSelection:function(){return {removeAllRanges:function(){},addRange:function(){}};}}, navigator:{}, Event:function(t,o){this.type=t;}, _vmToast:function(){}, closeMessageContextMenu:function(){}, positionMenuAbove:function(){}, lucide:null, send:function(){ throw new Error('SEND_CALLED'); } };
    ctx._vmUserMsgMenuTarget = { querySelector:function(){ return { innerText:'hello from user', textContent:'hello from user', style:{} }; } };
    vm.createContext(ctx);
    const fn = html.match(/function _vmUserMsgAction\(action\) \{[\s\S]*?\r?\}\r?\n\(function _vmInitUserMsgMenuHandlers/);
    if (!fn) throw new Error('no _vmUserMsgAction');
    vm.runInContext(fn[0].replace(/\r?\n\(function _vmInitUserMsgMenuHandlers/,'') + '\nfunction _vmCloseUserMsgMenu(){try{var m=document.getElementById("userMessageMenu"); if(m){m.classList.remove("open");m.style.display="none";}}catch(e){} _vmUserMsgMenuTarget=null;}', ctx);
    ctx._vmUserMsgAction('edit');
    assert('Edit DOM: fills composer without sending', input.value==='hello from user' && input._f===true);
  } catch(e){ assert('Edit DOM: fills composer without sending', false, e.message); }
})();

console.log('VM_B: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
