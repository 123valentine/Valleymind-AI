// vm-fix15 item 3 regenerate checks: regenerates only that AI reply, no duplicate user message.
const fs = require('fs');
const h = fs.readFileSync('index.html', 'utf8');
let ok = 0, fail = 0;
function assert(name, cond, detail) { if (cond) { console.log('OK: ' + name); ok++; } else { console.log('FAIL: ' + name + (detail ? ' :: ' + detail : '')); fail++; } }

var m = h.match(/function regenerateResponse\s*\([^)]*\)\s*\{([\s\S]*?)\n\}/);
var body = m ? m[1] : '';
assert('regenerateResponse exists', !!m);

// Must NOT add the user message again / must not stuff input and send as a normal new user turn
assert('regenerate does not add user message', !/addMessage\([^)]*["']user["']/.test(body));
assert('regenerate does not set chatInput value as new send path', !/chatInput[\s\S]*\.value\s*=/.test(body));
// Must target a specific AI bubble / use regen context that replaces in place
assert('regenerate targets assistant bubble for in-place replacement',
  /closest\(|assistant-message|_regenCtx|regenCtx|wrapper/.test(body) && (/\.bubble|replace|_regenCtx|regenCtx|userText/.test(body)),
  body.slice(0,260));

// send() must honor regen context by NOT adding a user message
assert('send() skips user message add in regen mode',
  /do NOT add a user message/.test(h) || (/_regenCtx/.test(h) && /}\s*else\s*\{[\s\S]{0,200}addMessage\(text,\s*"user"/.test(h)));
// send() must stream into/regenerate target bubble (not always add new AI message blindly in regen path)
assert('send() reuses target bubble in regen mode',
  /_regenCtx\s*&&\s*_regenCtx\.wrapper[\s\S]{0,180}bubbleEl\s*=\s*_regenCtx\.wrapper/.test(h));

assert('regenerate uses original user message', /_lastUserText|dataset\.userText|userText|user-message/.test(body));

console.log('REGENERATE: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
