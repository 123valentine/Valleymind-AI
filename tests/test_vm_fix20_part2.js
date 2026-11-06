const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');
const brain = fs.readFileSync(path.join(root, 'core', 'brain.py'), 'utf8');
const ext = fs.readFileSync(path.join(root, 'core', 'external_apis.py'), 'utf8');
const si = fs.readFileSync(path.join(root, 'core', 'source_intelligence.py'), 'utf8');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
let ok = 0, fail = 0;
function t(name, cond) { if (cond) { ok++; } else { fail++; console.log('FAIL: ' + name); } }

// 2.1 table not truncated / continue path
t('groq stream higher limit', /max_completion_tokens"\s*:\s*4096/.test(brain));
t('finish_reason detected in groq stream', /finish_reason/.test(brain) && /__vm_control/.test(brain));
t('auto-continue on length', /finish_reason["']?\s*==\s*["']length["']/.test(brain) && /auto-continu|Continue exactly where you left off/.test(brain));
t('client handles truncated/continue', /evt\.truncated/.test(html) && /evt\.stream_continue/.test(html) && /Continue response/.test(html));
// recovery before apology when no output emitted
t('stream tries non-stream fallback before apology', /Recovered via .*non-stream fallback|_call_llm_cluster\(msgs\)/.test(brain));

// 2.2 clean search query / wikipedia
t('clean_search_query exists', /def clean_search_query/.test(ext));
t('no query.split()[0] wikipedia junk fallback', !/summary\(\{?_parse\.quote\(query\.split\(\)\[0\]\)/.test(ext) && !/wikipedia\.summary\(query\.split\(\)\[0\]/.test(ext));
t('wikipedia relevance filter', /_wikipedia_result_relevant/.test(ext) && /disambiguation/.test(ext));
t('plan_search uses cleaned query', /clean_search_query as _clean_sq/.test(si));
t('search unavailable directive', /search_note/.test(brain) && /LIVE SEARCH UNAVAILABLE|search was attempted but unavailable/.test(brain));

// 2.3 stream timeout + retry + visibility
t('45s stream read timeout', /_vmReadWithTimeout\(reader,\s*45000\)/.test(html));
t('stream auto-retry once', /_vmStreamRetryMax\s*=\s*2/.test(html) && /auto-retry/.test(html));
t('visibility stale stream abort/retry', /visibilitychange/.test(html) && /_vmStreamVisibilityAbort|idle\s*>\s*12000/.test(html));

// 2.4 menu closes on each trigger
t('messageContextMenu outside handlers', /_vmMsgCtxOutsideHandler/.test(html) && /pointerdown/.test(html) && /touchstart/.test(html));
t('messageContextMenu scroll close', /_vmMsgCtxScrollHandler/.test(html) && /scroll/.test(html));
t('messageContextMenu Escape close', /closeMessageContextMenu\(\).*catch/s.test(html) || /Escape[\s\S]{0,120}closeMessageContextMenu/.test(html));
t('messageContextMenu listener cleanup', /_vmMsgCtxRemoveListeners/.test(html) && /removeEventListener\("pointerdown"/.test(html));
t('close after action', /handleMessageContextAction[\s\S]{0,400}closeMessageContextMenu\(\)/.test(html));

console.log('VM_FIX20_PART2: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
