// vm-fix15 item 2 mic flow structural checks
const fs = require('fs');
const h = fs.readFileSync('index.html', 'utf8');
let ok = 0, fail = 0;
function assert(name, cond, detail) { if (cond) { console.log('OK: ' + name); ok++; } else { console.log('FAIL: ' + name + (detail ? ' :: ' + detail : '')); fail++; } }

// tap start: clears committed/interim, sets wants mic, starts listening
assert('tap start clears buffer and starts (no textarea write on start)',
  /_sttCommitted\s*=\s*""/.test(h) && /_sttInterim\s*=\s*""/.test(h) && /_userWantsMic\s*=\s*true/.test(h) && /startListening\(\)/.test(h));
assert('onresult does not write to textarea while recording',
  /NO textarea write while recording/.test(h) || (/updateMicCounter\(buf\)/.test(h) && !/onresult[\s\S]{0,1600}input\.value\s*=(?!=)/.test(h)));

// tap again stop/convert: flushBufferToTextarea appends after existing, scrolls to end, no send
assert('flush appends after existing text and scrolls to end',
  /function flushBufferToTextarea[\s\S]*?var sep\s*=\s*\(existing\s*&&\s*existing\.trim\(\)\)\s*\?\s*" "\s*:\s*"";[\s\S]*?input\.value\s*=[\s\S]*?\+\s*buf;[\s\S]*?input\.scrollTop\s*=\s*input\.scrollHeight/.test(h));
// extract flush body to ensure no send
var fm = h.match(/function flushBufferToTextarea\s*\(\)\s*\{([\s\S]*?)\nfunction /);
var fb = fm ? fm[1] : '';
assert('flush does not send', fb && !/window\.send\s*\(|\bsend\s*\(\s*\)/.test(fb), fb ? fb.slice(0,120) : 'no body');

// send while recording: flushes buffer before sending (checks _userWantsMic or _listening)
assert('send while recording flushes buffer before sending',
  (/(_userWantsMic|_listening)[\s\S]{0,700}flushBufferToTextarea\(\)/.test(h)) && /\/chat\/stream/.test(h));

// send with mic off sends box content
assert('send uses textarea box content', /(var|const|let)\s+text\s*=\s*input\.value\.trim\(\)/.test(h) && /addMessage\(text,\s*"user"/.test(h));

console.log('MIC_FLOW: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
