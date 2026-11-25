"""Real file generation and signed HTTP download tests."""

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from docx import Document as DocxDocument
from openpyxl import load_workbook
from pypdf import PdfReader

from app import app
from core import brain as brain_module
from core import file_gen
from core.brain import MarcusBrain, _detect_file_request

SAMPLE = """# Weekly Report

## Summary

Quarterly numbers for the ValleyMind test suite.

- Revenue grew steadily.
- Mobile downloads led the channel mix.

| Metric | Value |
|---|---|
| Revenue | 120 |
| Downloads | 45 |
"""

LLM_DRAFT = """## Executive Summary

This report sets out a practical view of quarterly revenue operations and the choices that determine whether the quarter finishes cleanly. It combines the operating picture, the main risks, and a short set of actions that can be owned and measured. The aim is to give a reader enough structure to act without waiting for a separate briefing.

The overall position is workable but uneven. Recurring revenue provided the steadiest base, while one-off activity created most of the variance. The sections below separate what is known from what still needs validation, so the recommendations can be reviewed rather than simply trusted.

## Operating context

Quarterly revenue operations cover the handoffs between pipeline, billing, recognition, and reporting. Each handoff adds a small amount of delay, and those delays compound when ownership is unclear. A clear calendar and a single source for adjustments removes most of that friction.

- Confirm the close calendar before the quarter begins.
- Name one owner for each adjustment category.
- Keep a dated log of assumptions used in the forecast.
- Review exceptions weekly instead of at the end of the quarter.

## Performance drivers

The largest driver was conversion quality rather than raw volume. Deals that arrived with a defined next step moved through the cycle with fewer interruptions. Deals without that definition tended to stall in review, which made the final week less predictable.

| Driver | Evidence | Effect | Action | Owner | Priority |
|---|---|---|---|---|---|
| Lead quality | Scored opportunities converted more often | Higher win rate | Tighten qualification | Sales lead | High |
| Discounting | Larger discounts slowed approvals | Longer cycle | Set approval bands | Finance lead | High |
| Billing timing | Late invoices shifted recognition | Revenue moved between periods | Invoice at milestone | Ops lead | High |
| Handoff clarity | Named owners reduced delays | Faster close | Publish owner map | Ops lead | Medium |
| Data quality | Missing fields caused rework | Slower reporting | Add required fields | Analyst | Medium |
| Forecast review | Weekly reviews caught drift | Fewer surprises | Keep weekly cadence | Finance lead | High |
| Customer mix | Renewals outweighed new logos | Stable base | Protect renewal motions | Account lead | Medium |

## Risks and controls

The main risk is false precision: a forecast can look exact while resting on unaudited assumptions. The control is not more commentary but a shorter, dated list of what is assumed and who checked it. A second risk is delayed feedback, which is addressed by reviewing drivers weekly.

- Mark every assumption with its source and review date.
- Escalate any adjustment that changes the quarter by a material amount.
- Keep the exception log visible to finance and sales.
- Recheck current guidance before using it externally.

## Recommendations

Start with the controls that are cheap to verify and do not depend on a system change. Once the process is stable, automate the parts that are repetitive. Keep the review focused on decisions rather than activity reports.

1. Publish the owner map before the next close.
2. Introduce a weekly drift review with a five-minute agenda.
3. Require a source and date for every manual adjustment.
4. Review discount bands after two full cycles.

## Key takeaways

- The biggest gains come from clearer handoffs, not more reporting.
- Every manual adjustment needs an owner, a source, and a date.
- Weekly reviews make the final week of the quarter predictable.
"""


def _bytes(meta):
    token = parse_qs(urlsplit(meta["download_url"]).query)["token"][0]
    path, _ = file_gen.get_file(meta["file_id"], token)
    return path.read_bytes()


def _token(meta):
    return parse_qs(urlsplit(meta["download_url"]).query)["token"][0]


def _card_meta(reply):
    match = re.search(r"\[\[FILE_CARD:[^\]]*?\|\s*(/api/files/download/[^\]\s]+)\]\]", reply)
    assert match, reply
    url = match.group(1)
    file_id = urlsplit(url).path.rsplit("/", 1)[-1]
    token = parse_qs(urlsplit(url).query)["token"][0]
    return file_gen.get_file(file_id, token)[1]


def _patch_llm(monkeypatch, captured, response=LLM_DRAFT):
    def fake_llm(messages, timeout=30):
        captured.append(messages)
        return response, {"groq_used": True, "fallback_used": False, "fallback_source": ""}
    monkeypatch.setattr(brain_module, "_call_llm_cluster", fake_llm)


@pytest.fixture()
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_generate_pdf_with_styled_document_content():
    meta = file_gen.generate_file(SAMPLE, "pdf", title="Weekly Report")
    assert meta["format"] == "pdf"
    assert meta["filename"].endswith(".pdf")
    assert meta["size_kb"] > 0
    assert meta["download_url"].startswith("/api/files/download/")

    data = _bytes(meta)
    assert data.startswith(b"%PDF-")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    assert "Weekly Report" in text
    assert "Revenue grew steadily" in text
    assert "Revenue" in text


def test_pdf_uses_bright_editorial_palette_without_dark_fills():
    source = Path(file_gen.__file__).read_text(encoding="utf-8")
    assert 'HexColor(PDF_INK)' in source
    assert 'HexColor(PDF_HEADING)' in source
    assert 'HexColor(PDF_TABLE_HEADER)' in source
    assert 'HexColor(PDF_GRID)' in source
    assert 'HexColor(PDF_CALLOUT)' in source
    for forbidden in ('HexColor("#111827")', 'HexColor("#0f766e")', 'HexColor("#0f3f3c")'):
        assert forbidden not in source


def test_pdf_embeds_png_visual_for_comparison_metrics_and_hierarchy():
    payload = {
        "title": "Platform snapshot",
        "headers": ["Area", "Score"],
        "rows": [["Chat", "82"], ["Images", "67"], ["Videos", "91"]],
        "trees": [{
            "heading": "Platform map",
            "lines": ["├── Chat", "│   └── Creative tools", "└── Companion"],
        }],
    }
    meta = file_gen.generate_file(payload, "pdf", title="Platform snapshot")
    data = _bytes(meta)
    assert data.startswith(b"%PDF-")
    assert b"/Subtype /Image" in data
    assert b"/PNG" in data or b"FlateDecode" in data
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    assert "Platform snapshot" in text
    assert "Score comparison" in text or "Structure map" in text


def test_generate_docx_with_headings_bullets_and_table():
    meta = file_gen.generate_file(SAMPLE, "word", title="Weekly Report")
    assert meta["format"] == "docx"

    document = DocxDocument(io.BytesIO(_bytes(meta)))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Weekly Report" in text
    assert "Revenue grew steadily" in text
    assert len(document.tables) == 1
    assert document.tables[0].cell(0, 0).text == "Metric"
    assert document.tables[0].cell(1, 1).text == "120"


def test_generate_xlsx_with_table_rows():
    meta = file_gen.generate_file(SAMPLE, "excel", title="Weekly Report")
    assert meta["format"] == "xlsx"

    workbook = load_workbook(io.BytesIO(_bytes(meta)))
    sheet = workbook.active
    assert sheet["A1"].value == "Metric"
    assert sheet["B1"].value == "Value"
    assert str(sheet["A2"].value) == "Revenue"
    assert str(sheet["B2"].value) == "120"
    assert sheet.freeze_panes == "A2"


def test_generate_csv_txt_markdown_and_zip():
    csv_meta = file_gen.generate_file(SAMPLE, "csv", title="Weekly Report")
    assert "Metric,Value" in _bytes(csv_meta).decode("utf-8-sig")

    txt_meta = file_gen.generate_file(SAMPLE, "txt", title="Weekly Report")
    assert "Weekly Report" in _bytes(txt_meta).decode("utf-8")

    md_meta = file_gen.generate_file(SAMPLE, "md", title="Weekly Report")
    md_text = _bytes(md_meta).decode("utf-8")
    assert md_text.startswith("# Weekly Report")
    assert "| Revenue | 120 |" in md_text

    zip_meta = file_gen.generate_file({
        "title": "Bundle",
        "files": [
            {"name": "notes.txt", "content": "hello from zip"},
            {"name": "summary.md", "content": "# Summary\n\nready"},
        ],
    }, "zip", title="Bundle")
    with zipfile.ZipFile(io.BytesIO(_bytes(zip_meta))) as archive:
        names = archive.namelist()
        assert "notes.txt" in names
        assert "summary.md" in names
        assert "README.txt" in names
        assert archive.read("notes.txt") == b"hello from zip"


def test_signed_download_endpoint_returns_real_attachment(client):
    meta = file_gen.generate_file(SAMPLE, "csv", title="Weekly Report")
    expected = _bytes(meta)

    response = client.get(meta["download_url"])
    assert response.status_code == 200
    assert response.data == expected
    disposition = response.headers.get("Content-Disposition", "")
    assert "attachment" in disposition.lower()
    assert meta["filename"] in disposition
    assert response.headers.get("Content-Type", "").startswith("text/csv")
    cache_control = response.headers.get("Cache-Control", "").lower()
    assert "no-cache" in cache_control or "max-age=0" in cache_control


def test_download_rejects_missing_or_wrong_token(client):
    meta = file_gen.generate_file("Private export", "txt", title="Private")
    assert client.get(f"/api/files/download/{meta['file_id']}").status_code == 403
    assert client.get(f"/api/files/download/{meta['file_id']}?token=wrong").status_code == 403
    assert client.get("/api/files/download/not-a-real-id?token=x").status_code == 404


def test_generate_endpoint_and_file_card():
    response = app.test_client().post("/api/files/generate", json={
        "content": SAMPLE,
        "format": "pdf",
        "title": "Weekly Report",
    })
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "success"
    assert data["format"] == "pdf"
    assert data["download_url"].startswith("/api/files/download/")
    assert data["file_card"] == file_gen.file_card(data)
    assert data["file_card"].startswith("[[FILE_CARD: Weekly Report | PDF | ")
    assert data["download_url"] in data["file_card"]


def test_brain_detects_file_intents():
    cases = [
        ("create a PDF", "pdf"),
        ("make a downloadable Word file", "docx"),
        ("export this as an Excel file", "xlsx"),
        ("give me a download link", "txt"),
        ("create a CSV called Q3 Metrics", "csv"),
    ]
    for message, fmt in cases:
        detected = _detect_file_request(message)
        assert detected is not None, message
        assert detected["format"] == fmt, (message, detected)
    assert _detect_file_request("What is the weather today?") is None


class _FakeMemory:
    def __init__(self):
        self.messages = []

    def reload(self):
        return None

    def get_chat(self, chat_id):
        return list(self.messages)

    def get_message_count(self, chat_id):
        return 1

    def add_message(self, chat_id, role, content, timestamp, **kwargs):
        self.messages.append({"role": role, "content": content})

    def save_memory(self):
        return None


def _brain_with_fake_memory():
    brain = object.__new__(MarcusBrain)
    brain.memory = _FakeMemory()
    brain.profile = type("Profile", (), {"key": "marcus"})()
    return brain


def test_brain_file_reply_emits_file_card(monkeypatch):
    _patch_llm(monkeypatch, [])
    brain = _brain_with_fake_memory()
    request = _detect_file_request("create a PDF called Test Report")
    reply = brain._file_generation_reply("chat-1", "create a PDF called Test Report", datetime.now().isoformat(), request)
    assert reply.startswith("Done —")
    assert "[[FILE_CARD: Test Report | PDF | " in reply
    assert "/api/files/download/" in reply
    assert brain.memory.messages[-1]["role"] == "assistant"
    assert brain.last_animation["state"] == "success"


def test_stream_respond_yields_realtime_file_card(monkeypatch):
    _patch_llm(monkeypatch, [])
    brain = _brain_with_fake_memory()
    events = list(brain.stream_respond(
        "create a CSV called Q3 Metrics, create it now",
        chat_id="chat-2",
    ))
    event_types = [type(event).__name__ for event in events]
    assert "dict" in event_types and "str" in event_types
    reply = next(event for event in events if isinstance(event, str) and "[[FILE_CARD:" in event)
    assert "Q3 Metrics | CSV | " in reply
    assert any(isinstance(event, dict) and "animation" in event for event in events)


def test_respond_routes_file_request_through_drafting(monkeypatch):
    _patch_llm(monkeypatch, [])
    brain = _brain_with_fake_memory()
    reply = brain.respond("create a PDF called Respond Report, create it now", chat_id="chat-3")
    assert "[[FILE_CARD: Respond Report | PDF | " in reply
    assert "/api/files/download/" in reply
    assert brain.memory.messages[-1]["role"] == "assistant"
    assert brain.last_response_meta["detected_route"] == "file_generation"


def test_pdf_request_generates_llm_sections_not_prompt_echo(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)
    brain = _brain_with_fake_memory()
    brain.memory.messages.append({
        "role": "assistant",
        "content": "Earlier context: margin pressure led the forecast review.",
    })
    message = "create a PDF about quarterly revenue operations"
    reply = brain._file_generation_reply(
        "llm-pdf", message, datetime.now().isoformat(), _detect_file_request(message)
    )

    assert captured and len(captured) == 1
    prompt = captured[0][-1]["content"]
    assert "quarterly revenue operations" in prompt
    assert "Earlier context: margin pressure" in prompt
    assert "ready-to-use document" in captured[0][0]["content"].lower()

    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(io.BytesIO(_bytes(_card_meta(reply)))).pages
    )
    assert len(text) > 800
    assert "Executive Summary" in text
    assert "Performance drivers" in text
    assert "Key takeaways" in text
    assert "Lead quality" in text
    assert "create a pdf" not in text.lower()
    assert message.lower() not in text.lower()
    assert brain.last_response_meta["file_content_provider"] == "Groq"


def test_xlsx_request_generates_llm_rows_and_overview(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)
    brain = _brain_with_fake_memory()
    message = "create an XLSX about quarterly revenue operations"
    reply = brain._file_generation_reply(
        "llm-xlsx", message, datetime.now().isoformat(), _detect_file_request(message)
    )

    workbook = load_workbook(io.BytesIO(_bytes(_card_meta(reply))))
    data_sheet = workbook.active
    assert data_sheet.max_row >= 8
    assert [cell.value for cell in data_sheet[1]][:3] == ["Driver", "Evidence", "Effect"]
    rows = [[cell.value for cell in row] for row in data_sheet.iter_rows(min_row=2)]
    assert len(rows) >= 6
    assert all(any(str(cell or "").strip() for cell in row) for row in rows)
    assert {row[0] for row in rows} >= {"Lead quality", "Discounting", "Billing timing"}

    assert "Overview" in workbook.sheetnames
    overview_text = " ".join(
        str(cell.value or "") for row in workbook["Overview"].iter_rows() for cell in row
    )
    assert "Executive Summary" in overview_text
    assert "Recommendations" in overview_text
    assert "create an xlsx" not in overview_text.lower()


def test_respond_and_stream_use_llm_drafting(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)
    brain = _brain_with_fake_memory()

    reply = brain.respond(
        "create a PDF about quarterly revenue operations with sections and a table, create it now",
        chat_id="llm-respond",
    )
    assert "[[FILE_CARD:" in reply
    assert len(captured) == 1

    events = list(brain.stream_respond(
        "create a CSV about quarterly revenue operations with six data rows, create it now",
        chat_id="llm-stream",
    ))
    assert any(isinstance(event, str) and "[[FILE_CARD:" in event for event in events)
    assert len(captured) == 2
    assert all("quarterly revenue operations" in messages[-1]["content"] for messages in captured)


def test_llm_failure_still_produces_structured_non_echo_draft(monkeypatch):
    def broken_llm(messages, timeout=30):
        raise RuntimeError("all providers failed")

    monkeypatch.setattr(brain_module, "_call_llm_cluster", broken_llm)
    brain = _brain_with_fake_memory()
    message = "create a TXT about logistics planning"
    reply = brain._file_generation_reply(
        "fallback-txt", message, datetime.now().isoformat(), _detect_file_request(message)
    )
    text = _bytes(_card_meta(reply)).decode("utf-8")
    assert "Executive summary" in text
    assert "Analysis" in text
    assert "Recommendations" in text
    assert "Early signal" in text
    assert "create a txt" not in text.lower()
    assert brain.last_response_meta["file_content_provider"] == "local-fallback"
