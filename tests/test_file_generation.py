"""Real file generation and signed HTTP download tests."""

import io
import zipfile
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from docx import Document as DocxDocument
from openpyxl import load_workbook
from pypdf import PdfReader

from app import app
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


def _bytes(meta):
    token = parse_qs(urlsplit(meta["download_url"]).query)["token"][0]
    path, _ = file_gen.get_file(meta["file_id"], token)
    return path.read_bytes()


def _token(meta):
    return parse_qs(urlsplit(meta["download_url"]).query)["token"][0]


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


def test_brain_file_reply_emits_file_card():
    brain = _brain_with_fake_memory()
    request = _detect_file_request("create a PDF called Test Report")
    reply = brain._file_generation_reply("chat-1", "create a PDF called Test Report", datetime.now().isoformat(), request)
    assert reply.startswith("Done —")
    assert "[[FILE_CARD: Test Report | PDF | " in reply
    assert "/api/files/download/" in reply
    assert brain.memory.messages[-1]["role"] == "assistant"
    assert brain.last_animation["state"] == "success"


def test_stream_respond_yields_realtime_file_card():
    brain = _brain_with_fake_memory()
    events = list(brain.stream_respond("create a CSV called Q3 Metrics", chat_id="chat-2"))
    event_types = [type(event).__name__ for event in events]
    assert "dict" in event_types and "str" in event_types
    reply = next(event for event in events if isinstance(event, str) and "[[FILE_CARD:" in event)
    assert "Q3 Metrics | CSV | " in reply
    assert any(isinstance(event, dict) and "animation" in event for event in events)


def test_respond_routes_file_request_without_llm():
    brain = _brain_with_fake_memory()
    reply = brain.respond("create a PDF called Respond Report", chat_id="chat-3")
    assert "[[FILE_CARD: Respond Report | PDF | " in reply
    assert "/api/files/download/" in reply
    assert brain.memory.messages[-1]["role"] == "assistant"
