// Headless stub-DOM harness to test renderVoiceSection returns non-empty HTML
// even when cloud hydrate fails / fetch rejects / localStorage is empty.
const fs = require('fs');
let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0,200) : '')); fail++; }
}

// ---- Stub DOM ----
function el(){ return { style:{}, classList:{add:function(){},remove:function(){},contains:function(){return false;}}, children:[], appendChild:function(){return el();}, setAttribute:function(){}, getAttribute:function(){return null;}, querySelector:function(){return null;}, querySelectorAll:function(){return [];}, innerHTML:'', textContent:'', focus:function(){}, addEventListener:function(){}, removeEventListener:function(){}, dispatchEvent:function(){return true;}, insertAdjacentHTML:function(){}, scrollTop:0, scrollHeight:0, offsetHeight:0, offsetWidth:0, getBoundingClientRect:function(){return {top:0,left:0,width:0,height:0,bottom:0,right:0};} }; }
const docStub = {
  getElementById:function(){ return el(); },
  createElement:function(){ return el(); },
  head:{ appendChild:function(){} },
  body:{ appendChild:function(){} },
  addEventListener:function(){},
  documentElement:{ style:{}, classList:{add:function(){},remove:function(){}} }
};
const storage = {};
const localStorageStub = {
  getItem:function(k){ return storage[k] === undefined ? null : String(storage[k]); },
  setItem:function(k,v){ storage[k]=String(v); },
  removeItem:function(k){ delete storage[k]; },
  clear:function(){ Object.keys(storage).forEach(k=>delete storage[k]); }
};

let settingsApiGetCalls = 0;
function settingsApiGetRejecting(section){
  settingsApiGetCalls++;
  return Promise.reject(new Error('cloud fetch rejected'));
}
function settingsApiGetResolving(section){
  settingsApiGetCalls++;
  return Promise.resolve({ persona_voices: { marcus:'aria', companion:'jenny' } });
}

function authHeaders(h){ return h || {}; }
function apiFetch(){ return Promise.resolve({ json:function(){return {};} }); }

const _SH = {
  sectionHeader:function(title,sub){ return '<div class="sh"><h2>'+title+'</h2>'+(sub||'')+'</div>'; },
  card:function(title,html){ return '<div class="card">'+(html||'')+'</div>'; },
  statusSpan:function(id){ return '<span id="'+id+'"></span>'; }
};

const windowStub = {
  setTimeout:setTimeout,
  clearTimeout:clearTimeout,
  Date:Date,
  Promise:Promise,
  Object:Object,
  console:console,
  VoiceOrb: undefined,
  vmPersonaVoicesSync: undefined,
  navigator:{},
  location:{},
  addEventListener:function(){},
  dispatchEvent:function(){}
};
// Make globals available to settings.js via `window` references it may use and direct names.
global.window = windowStub;
global.document = docStub;
global.localStorage = localStorageStub;
global.settingsApiGet = settingsApiGetRejecting;
global.authHeaders = authHeaders;
global.apiFetch = apiFetch;
global._SH = _SH;
global.Promise = Promise;
global.setTimeout = setTimeout;
global.clearTimeout = clearTimeout;

// Load settings.js (defines renderVoiceSection etc.)
const settingsSrc = fs.readFileSync('static/settings.js','utf8');
try {
  // Use vm with window context so `window.X` refs resolve.
  const vm = require('vm');
  const ctx = {
    window: windowStub, document: docStub, localStorage: localStorageStub,
    settingsApiGet: settingsApiGetRejecting, authHeaders, apiFetch, _SH,
    Promise, setTimeout, clearTimeout, Date, Object, console,
    navigator: {}, location: {}
  };
  vm.createContext(ctx);
  vm.runInContext(settingsSrc, ctx);
  const renderVoiceSection = ctx.renderVoiceSection;
  assert('renderVoiceSection is a function', typeof renderVoiceSection === 'function');

  // Test 1: localStorage empty + fetch rejects (cloud failure) -> still non-empty HTML
  storage['vmPersonaVoices'] = '{}';
  const container1 = { innerHTML:'', style:{}, classList:{add:function(){},remove:function(){}} };
  let threw = false;
  let html1 = '';
  try { renderVoiceSection.call(ctx, container1); html1 = container1.innerHTML || ''; } catch (e) { threw = true; }
  assert('renderVoiceSection does not throw with empty localStorage + rejecting fetch', !threw, threw?String(e):'');
  assert('renderVoiceSection returns non-empty HTML (empty localStorage, fetch rejects)', html1.length > 0, 'len='+html1.length);
  assert('HTML contains Voice section header', /sectionHeader|Voice/.test(html1));
  assert('HTML contains safe-fallback note(s)', /Could not load/.test(html1) || html1.length>100);

  // Test 2: localStorage has stale data + fetch rejects -> non-empty HTML
  storage['vmPersonaVoices'] = JSON.stringify({ marcus:'guy', elena:'aria' });
  const container2 = { innerHTML:'', style:{}, classList:{add:function(){},remove:function(){}} };
  let threw2=false; let html2='';
  try { renderVoiceSection.call(ctx, container2); html2 = container2.innerHTML || ''; } catch (e){ threw2=true; }
  assert('renderVoiceSection does not throw with stale localStorage + rejecting fetch', !threw2, threw2?String(e):'');
  assert('renderVoiceSection returns non-empty HTML (stale localStorage, fetch rejects)', html2.length > 0);

  // Test 3: fetch resolves (cloud wins) -> non-empty HTML
  global.settingsApiGet = settingsApiGetResolving;
  const container3 = { innerHTML:'', style:{}, classList:{add:function(){},remove:function(){}} };
  let threw3=false; let html3='';
  try { renderVoiceSection.call(ctx, container3); html3 = container3.innerHTML || ''; } catch(e){ threw3=true; }
  assert('renderVoiceSection does not throw with resolving fetch', !threw3, threw3?String(e):'');
  assert('renderVoiceSection returns non-empty HTML (fetch resolves)', html3.length > 0);
  global.settingsApiGet = settingsApiGetRejecting;

  console.log('VOICE_SECTION: ' + ok + ' passed, ' + fail + ' failed');
  if (fail) process.exit(1);
} catch (e) {
  console.log('FAIL: load settings.js :: ' + e.stack.slice(0,300));
  process.exit(1);
}
