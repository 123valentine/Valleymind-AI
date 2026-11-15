"""Regression tests for Markdown table rendering in chat replies.

These pin the two defects that made comparison tables show up as raw pipe
text / overflow the mobile chat bubble:

1. marked v9's browser build exposes an OBJECT on ``window.marked`` with the
   parser at ``.parse``. The renderer used to require a callable, so every
   reply silently fell back to the plain-paragraph path (raw ``|`` text).
2. Tables were either left unwrapped or forced to ``display:block`` with a
   fixed ``min-width``, which widened the whole message on a phone.

Run: python tests/test_table_rendering.py
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ok = 0
failed = []


def t(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        failed.append(name)
        print("FAIL:", name)


def read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


settings = read("static/settings.js")
index = read("index.html")


def block(src, start, end):
    i = src.find(start)
    if i < 0:
        return ""
    j = src.find(end, i + len(start))
    return src[i:j] if j >= 0 else src[i:]


render_fn = block(settings, "function render(text)", "return { render: render };")

# --- 1. marked v9 object shape is accepted ---------------------------------
t("renderer accepts marked.parse object", "window.marked.parse" in render_fn)
t("renderer keeps legacy callable support", 'typeof window.marked === "function"' in render_fn)
t("renderer calls marked with gfm", "gfm: true" in render_fn)
t("plain-paragraph fallback still present", "paras.map" in render_fn)

# --- 2. table scroll wrapper is created for every render path --------------
t("table wrapper helper exposed", "window.VMWrapReplyTables" in settings)
t("wrapper helper marks the bubble", 'classList.add("has-table")' in settings)
t("stream enrich calls wrapper helper", "window.VMWrapReplyTables(bub)" in settings)
t("history addMessage calls wrapper helper", "window.VMWrapReplyTables(bubble)" in index)

# --- 3. mobile containment CSS --------------------------------------------
wrap_css = block(index, ".bubble .vm-table-wrap{", "\n")
t("wrapper is a horizontal scroll container", "overflow-x:auto" in wrap_css)
t("wrapper is capped to the message", "max-width:100%" in wrap_css)

table_css = block(index, ".bubble .vm-table-wrap table{", "\n")
t("wrapped table keeps real table layout", "display:table" in table_css)
t("no fixed 480px table min-width", "min-width:480px" not in index)
t("no fixed 420px mobile min-width", "min-width:420px" not in index)

rich_table = block(index, ".bubble.ai-rich table{", "}")
t("table is not turned into a block box", "display:block" not in rich_table)

t("table-bearing bubble gets a definite width",
  ".assistant-message .bubble.ai-rich.has-table" in index)

cell_css = block(index, ".bubble.ai-rich table th,", "}")
t("cells wrap long text", "white-space:normal" in cell_css)
t("cells break overlong words", "overflow-wrap:break-word" in cell_css)

# --- 4. GFM column alignment survives the sanitizer -----------------------
t("sanitizer keeps align attribute",
  'name === "align"' in settings and 'tag === "th"' in settings)
t("center alignment styled", 'th[align="center"]' in index)
t("right alignment styled", 'td[align="right"]' in index)

print("TABLE_RENDER_TESTS: %d passed, %d failed" % (ok, len(failed)))
if failed:
    sys.exit(1)
