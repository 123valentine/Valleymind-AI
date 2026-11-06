import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.external_apis import clean_search_query, _wikipedia_result_relevant

# Clean query: strips intent/filler but keeps meaning/temporal terms
assert clean_search_query("List 3 AI tools with their logos") in ("AI tools logos", "3 AI tools logos", "AI tools with logos")
cq1 = clean_search_query("List 3 AI tools with their logos").lower()
assert "ai" in cq1 and "tools" in cq1
assert "list" not in cq1.split()

cq2 = clean_search_query("What is the latest iPhone model").lower()
assert "iphone" in cq2 and "latest" in cq2
assert "what" not in cq2.split()

cq3 = clean_search_query("show me bitcoin price today").lower()
assert "bitcoin" in cq3 and "price" in cq3

# Wikipedia relevance: reject disambiguation/generic unrelated definitions
assert not _wikipedia_result_relevant("List 3 AI tools with their logos", {
    "type": "standard", "title": "List (abstract data type)",
    "extract": "In computer science, a list or sequence is an abstract data type."
})
assert not _wikipedia_result_relevant("What is the latest iPhone model", {
    "type": "disambiguation", "title": "What",
    "extract": "What or WHAT may refer to: What, an interrogative pronoun."
})
assert _wikipedia_result_relevant("latest iPhone model", {
    "type": "standard", "title": "iPhone",
    "extract": "The iPhone is a line of smartphones developed and marketed by Apple."
})

print("VM_FIX20_PART2_PY_OK")
