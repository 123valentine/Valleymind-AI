"""Real downloadable file generation for ValleyMind chat replies.

The chat brain calls :func:`generate_file` when a user asks for a PDF, Word
file, spreadsheet, CSV, text/markdown export, or ZIP bundle.  Files are written
to persistent local storage and served by the signed download endpoint in
``app.py``.  Nothing here depends on the browser, so the same generated file
works in the PWA, mobile browsers and Android WebView.
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FORMAT_ALIASES = {
    "pdf": "pdf",
    "doc": "docx",
    "docx": "docx",
    "word": "docx",
    "msword": "docx",
    "xls": "xlsx",
    "xlsx": "xlsx",
    "excel": "xlsx",
    "spreadsheet": "xlsx",
    "sheet": "xlsx",
    "csv": "csv",
    "txt": "txt",
    "text": "txt",
    "md": "md",
    "markdown": "md",
    "zip": "zip",
}

EXTENSIONS = {
    "pdf": ".pdf",
    "docx": ".docx",
    "xlsx": ".xlsx",
    "csv": ".csv",
    "txt": ".txt",
    "md": ".md",
    "zip": ".zip",
}

MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "zip": "application/zip",
}

_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")


def _storage_dir() -> Path:
    configured = os.getenv("VM_FILE_DOWNLOAD_DIR", "").strip()
    base = Path(configured) if configured else Path(tempfile.gettempdir()) / "valleymind_files"
    base.mkdir(parents=True, exist_ok=True)
    return base


_FILE_DIR = _storage_dir()
_registry_lock = threading.RLock()
_registry: dict[str, dict[str, Any]] = {}


def normalize_format(value: str, default: str = "txt") -> str:
    """Map natural format names (Word, Excel, markdown, ...) to a file format."""
    key = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    return FORMAT_ALIASES.get(key, default)


def _safe_filename(value: str, fmt: str) -> str:
    name = _INVALID_NAME_RE.sub("-", str(value or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .-")
    if not name or name in {".", ".."}:
        name = "valleymind-file"
    if len(name) > 90:
        name = name[:90].rstrip()
    ext = EXTENSIONS.get(fmt, ".txt")
    if name.lower().endswith(ext):
        return name
    return name + ext


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_clean(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_clean(v)}" for k, v in value.items())
    return str(value).strip()


def _table_lines_to_rows(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    rows: list[list[str]] = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells):
            continue
        if any(cells):
            rows.append(cells)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def normalize_content(content: Any, title: str = "") -> dict[str, Any]:
    """Turn strings, lists or dict payloads into one document model."""
    doc: dict[str, Any] = {
        "title": _clean(title) or "ValleyMind Export",
        "subtitle": "",
        "paragraphs": [],
        "bullets": [],
        "sections": [],
        "headers": [],
        "rows": [],
        "raw": "",
        "files": [],
    }

    if isinstance(content, dict):
        doc["title"] = _clean(content.get("title")) or doc["title"]
        doc["subtitle"] = _clean(content.get("subtitle") or content.get("summary"))
        raw = _clean(content.get("content") or content.get("text"))
        doc["raw"] = raw
        for para in content.get("paragraphs") or []:
            if _clean(para):
                doc["paragraphs"].append(_clean(para))
        for bullet in content.get("bullets") or content.get("items") or []:
            if _clean(bullet):
                doc["bullets"].append(_clean(bullet))
        for section in content.get("sections") or []:
            if isinstance(section, dict):
                doc["sections"].append({
                    "heading": _clean(section.get("heading") or section.get("title")),
                    "paragraphs": [_clean(p) for p in (section.get("paragraphs") or []) if _clean(p)],
                    "bullets": [_clean(b) for b in (section.get("bullets") or section.get("items") or []) if _clean(b)],
                })
            elif _clean(section):
                doc["sections"].append({"heading": "", "paragraphs": [_clean(section)], "bullets": []})
        rows = content.get("rows") or []
        if rows:
            if isinstance(rows[0], dict):
                headers = list(rows[0].keys())
                doc["headers"] = [_clean(h) for h in headers]
                doc["rows"] = [[_clean(row.get(h, "")) for h in headers] for row in rows]
            else:
                parsed = [[_clean(cell) for cell in row] for row in rows if isinstance(row, (list, tuple))]
                if parsed:
                    doc["headers"] = _clean(content.get("headers")) and [_clean(h) for h in (content.get("headers") or [])] or parsed[0]
                    doc["rows"] = parsed[1:] if not content.get("headers") else parsed
        files = content.get("files")
        if isinstance(files, dict):
            doc["files"] = [{"name": _clean(k), "content": _clean(v)} for k, v in files.items()]
        elif isinstance(files, (list, tuple)):
            for item in files:
                if isinstance(item, dict):
                    doc["files"].append({
                        "name": _clean(item.get("name") or item.get("filename")) or f"file-{len(doc['files']) + 1}.txt",
                        "content": _clean(item.get("content") or item.get("text")),
                    })
                else:
                    doc["files"].append({"name": f"file-{len(doc['files']) + 1}.txt", "content": _clean(item)})
        if not doc["paragraphs"] and not doc["bullets"] and not doc["sections"] and not doc["rows"] and raw:
            doc["paragraphs"] = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
        return doc

    if isinstance(content, (list, tuple)):
        if content and all(isinstance(row, dict) for row in content):
            headers = list(content[0].keys())
            doc["headers"] = [_clean(h) for h in headers]
            doc["rows"] = [[_clean(row.get(h, "")) for h in headers] for row in content]
        elif content and all(isinstance(row, (list, tuple)) for row in content):
            doc["headers"] = [_clean(cell) for cell in content[0]]
            doc["rows"] = [[_clean(cell) for cell in row] for row in content[1:]]
        else:
            doc["bullets"] = [_clean(item) for item in content if _clean(item)]
        doc["raw"] = "\n".join(doc["bullets"] or [" | ".join(doc["headers"])] + [" | ".join(r) for r in doc["rows"]])
        return doc

    text = _clean(content).replace("\r\n", "\n")
    doc["raw"] = text
    if not text:
        return doc

    table_lines: list[str] = []
    para_buffer: list[str] = []
    current_section: dict[str, Any] | None = None

    def flush_para() -> None:
        if para_buffer:
            paragraph = " ".join(p.strip() for p in para_buffer if p.strip()).strip()
            if paragraph:
                if current_section is not None:
                    current_section["paragraphs"].append(paragraph)
                else:
                    doc["paragraphs"].append(paragraph)
            para_buffer.clear()

    def flush_table() -> None:
        if table_lines:
            headers, rows = _table_lines_to_rows(table_lines)
            if headers:
                doc["headers"] = headers
                doc["rows"] = rows
            else:
                para_buffer.extend(table_lines)
            table_lines.clear()

    for line in text.split("\n"):
        if not line.strip():
            flush_para()
            flush_table()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_para()
            flush_table()
            heading_text = _clean(heading.group(2))
            if not doc["paragraphs"] and not doc["bullets"] and not doc["sections"] and doc["title"] == "ValleyMind Export":
                doc["title"] = heading_text
            else:
                current_section = {"heading": heading_text, "paragraphs": [], "bullets": []}
                doc["sections"].append(current_section)
            continue
        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            flush_para()
            table_lines.append(line)
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            flush_para()
            flush_table()
            item = _clean(line[bullet.end():])
            if current_section is not None:
                current_section["bullets"].append(item)
            else:
                doc["bullets"].append(item)
            continue
        para_buffer.append(line)

    flush_para()
    flush_table()
    if not doc["paragraphs"] and not doc["bullets"] and not doc["sections"] and not doc["rows"]:
        doc["paragraphs"] = [text]
    return doc


def _plain_text(doc: dict[str, Any]) -> str:
    lines = [doc["title"]]
    if doc["subtitle"]:
        lines += ["", doc["subtitle"]]
    for paragraph in doc["paragraphs"]:
        lines += ["", paragraph]
    for section in doc["sections"]:
        lines += ["", section["heading"] or "Section"]
        for paragraph in section.get("paragraphs", []):
            lines += ["", paragraph]
        for bullet in section.get("bullets", []):
            lines.append(f"- {bullet}")
    if doc["bullets"]:
        lines += ["", "Key points"]
        lines += [f"- {bullet}" for bullet in doc["bullets"]]
    if doc["headers"]:
        lines += ["", " | ".join(doc["headers"])]
        lines += [" | ".join(row) for row in doc["rows"]]
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def _markdown_text(doc: dict[str, Any]) -> str:
    lines = [f"# {doc['title']}"]
    if doc["subtitle"]:
        lines += ["", f"*{doc['subtitle']}*"]
    for paragraph in doc["paragraphs"]:
        lines += ["", paragraph]
    for section in doc["sections"]:
        if section["heading"]:
            lines += ["", f"## {section['heading']}"]
        for paragraph in section.get("paragraphs", []):
            lines += ["", paragraph]
        for bullet in section.get("bullets", []):
            lines.append(f"- {bullet}")
    if doc["bullets"]:
        lines += ["", "## Key points"]
        lines += [f"- {bullet}" for bullet in doc["bullets"]]
    if doc["headers"]:
        lines += ["", "| " + " | ".join(doc["headers"]) + " |", "|" + "|".join(["---"] * len(doc["headers"])) + "|"]
        lines += ["| " + " | ".join(row) + " |" for row in doc["rows"]]
    return "\n".join(lines).strip() + "\n"


def _build_pdf(doc: dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        title=doc["title"],
        author="ValleyMind-AI",
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "VMTitle", parent=styles["Title"], fontSize=20, leading=25,
        textColor=colors.HexColor("#111827"), alignment=TA_LEFT, spaceAfter=10,
    )
    heading_style = ParagraphStyle(
        "VMHeading", parent=styles["Heading2"], fontSize=14, leading=18,
        textColor=colors.HexColor("#0f766e"), spaceBefore=10, spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "VMBody", parent=styles["BodyText"], fontSize=11, leading=16,
        textColor=colors.HexColor("#1f2937"), spaceAfter=6,
    )
    table_header_style = ParagraphStyle(
        "VMTableHeader", parent=body_style, fontSize=10, leading=13,
        textColor=colors.white, fontName="Helvetica-Bold", spaceAfter=0,
    )

    def para(text: str, style=body_style) -> Paragraph:
        return Paragraph(str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"), style)

    story: list[Any] = [para(doc["title"], title_style)]
    if doc["subtitle"]:
        story.append(para(doc["subtitle"]))
        story.append(Spacer(1, 4))
    for paragraph in doc["paragraphs"]:
        story.append(para(paragraph))
    for section in doc["sections"]:
        if section["heading"]:
            story.append(para(section["heading"], heading_style))
        for paragraph in section.get("paragraphs", []):
            story.append(para(paragraph))
        if section.get("bullets"):
            story.append(ListFlowable(
                [ListItem(para(bullet), leftIndent=12) for bullet in section["bullets"]],
                bulletType="bullet", start="•", leftIndent=18,
            ))
    if doc["bullets"]:
        story.append(ListFlowable(
            [ListItem(para(bullet), leftIndent=12) for bullet in doc["bullets"]],
            bulletType="bullet", start="•", leftIndent=18,
        ))
    if doc["headers"]:
        table_data = [[para(cell, table_header_style) for cell in doc["headers"]]]
        table_data += [[para(cell) for cell in row] for row in doc["rows"]]
        table = Table(table_data, hAlign="LEFT", repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story += [Spacer(1, 8), table]
    if len(story) == 1:
        story.append(para("Generated by ValleyMind-AI."))
    pdf.build(story)
    return buffer.getvalue()


def _build_docx(doc: dict[str, Any]) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading(doc["title"], level=0)
    if doc["subtitle"]:
        document.add_paragraph(doc["subtitle"])
    for paragraph in doc["paragraphs"]:
        document.add_paragraph(paragraph)
    for section in doc["sections"]:
        if section["heading"]:
            document.add_heading(section["heading"], level=2)
        for paragraph in section.get("paragraphs", []):
            document.add_paragraph(paragraph)
        for bullet in section.get("bullets", []):
            document.add_paragraph(bullet, style="List Bullet")
    for bullet in doc["bullets"]:
        document.add_paragraph(bullet, style="List Bullet")
    if doc["headers"]:
        table = document.add_table(rows=1, cols=len(doc["headers"]))
        table.style = "Table Grid"
        for idx, header in enumerate(doc["headers"]):
            table.rows[0].cells[idx].text = header
        for row in doc["rows"]:
            cells = table.add_row().cells
            for idx, cell in enumerate(row[:len(cells)]):
                cells[idx].text = cell
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _build_xlsx(doc: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = re.sub(r"[\[\]:*?/\\]", "-", doc["title"])[:31] or "ValleyMind"

    if doc["headers"]:
        sheet.append(doc["headers"])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="0F766E")
            cell.alignment = Alignment(vertical="center")
        for row in doc["rows"]:
            sheet.append([cell for cell in row])
        sheet.freeze_panes = "A2"
        widths: dict[int, int] = {}
        for row in sheet.iter_rows():
            for idx, cell in enumerate(row, start=1):
                widths[idx] = min(42, max(widths.get(idx, 10), len(str(cell.value or "")) + 2))
        for idx, width in widths.items():
            sheet.column_dimensions[get_column_letter(idx)].width = width
    else:
        sheet.append([doc["title"]])
        sheet["A1"].font = Font(bold=True, size=14)
        row_num = 2
        if doc["subtitle"]:
            sheet.cell(row=row_num, column=1, value=doc["subtitle"])
            row_num += 1
        for paragraph in doc["paragraphs"]:
            row_num += 1
            cell = sheet.cell(row=row_num, column=1, value=paragraph)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        for section in doc["sections"]:
            row_num += 1
            sheet.cell(row=row_num, column=1, value=section["heading"] or "Section").font = Font(bold=True)
            for paragraph in section.get("paragraphs", []):
                row_num += 1
                sheet.cell(row=row_num, column=1, value=paragraph).alignment = Alignment(wrap_text=True, vertical="top")
            for bullet in section.get("bullets", []):
                row_num += 1
                sheet.cell(row=row_num, column=1, value=f"- {bullet}")
        for bullet in doc["bullets"]:
            row_num += 1
            sheet.cell(row=row_num, column=1, value=f"- {bullet}")
        sheet.column_dimensions["A"].width = 80

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _build_csv(doc: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    if doc["headers"]:
        writer.writerow(doc["headers"])
        writer.writerows(doc["rows"])
    else:
        for line in _plain_text(doc).splitlines():
            writer.writerow([line])
    return output.getvalue().encode("utf-8-sig")


def _safe_member_name(value: str) -> str:
    name = str(value or "").replace("\\", "/").strip()
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._()\- ]+", "-", name).strip(" .-") or "file.txt"
    if not Path(name).suffix:
        name += ".txt"
    return name[:120]


def _build_zip(content: Any, title: str) -> tuple[bytes, str]:
    doc = normalize_content(content, title)
    files = list(doc["files"])
    if not files:
        base_name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "valleymind-export"
        files = [{"name": base_name + ".md", "content": _markdown_text(doc)}]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        seen: set[str] = set()
        for item in files:
            name = _safe_member_name(item.get("name") or "file.txt")
            while name in seen:
                stem, ext = os.path.splitext(name)
                name = f"{stem}-{len(seen) + 1}{ext}"
            seen.add(name)
            archive.writestr(name, str(item.get("content") or ""))
        archive.writestr("README.txt", f"{doc['title']}\n\nGenerated by ValleyMind-AI.\n")
    return buffer.getvalue(), _safe_filename(title, "zip")


def _meta_path(file_id: str) -> Path:
    return _FILE_DIR / f"{file_id}.json"


def _public_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": meta["file_id"],
        "filename": meta["filename"],
        "format": meta["format"],
        "size_kb": meta["size_kb"],
        "download_url": meta["download_url"],
        "title": meta.get("title", meta["filename"]),
    }


def generate_file(content: Any = "", format: str = "txt", title: str = "ValleyMind Export", filename: str = "") -> dict[str, Any]:
    """Write a real file to disk and return its signed download metadata."""
    fmt = normalize_format(format)
    file_id = secrets.token_urlsafe(12).replace("/", "_")
    token = secrets.token_urlsafe(18).replace("/", "_")
    path = _FILE_DIR / f"{file_id}{EXTENSIONS[fmt]}"

    if fmt == "zip":
        payload, default_name = _build_zip(content, title)
    else:
        doc = normalize_content(content, title)
        builders = {
            "pdf": _build_pdf,
            "docx": _build_docx,
            "xlsx": _build_xlsx,
            "csv": _build_csv,
            "txt": lambda d: _plain_text(d).encode("utf-8"),
            "md": lambda d: _markdown_text(d).encode("utf-8"),
        }
        payload = builders[fmt](doc)
        default_name = _safe_filename(filename or doc["title"], fmt)

    path.write_bytes(payload)
    size_bytes = len(payload)
    meta = {
        "file_id": file_id,
        "token": token,
        "title": _clean(title) or default_name,
        "filename": _safe_filename(filename or default_name, fmt),
        "format": fmt,
        "mimetype": MIME_TYPES.get(fmt) or mimetypes.guess_type(str(path))[0] or "application/octet-stream",
        "size_bytes": size_bytes,
        "size_kb": max(0.1, round(size_bytes / 1024, 1)),
        "download_url": f"/api/files/download/{file_id}?token={token}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stored_name": path.name,
    }
    with _registry_lock:
        _registry[file_id] = meta
    try:
        _meta_path(file_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return _public_metadata(meta)


def get_file(file_id: str, token: str = "") -> tuple[Path, dict[str, Any]]:
    """Resolve a signed file id to its on-disk path and full metadata."""
    file_id = str(file_id or "").strip()
    if not _FILE_ID_RE.fullmatch(file_id):
        raise FileNotFoundError("invalid file id")
    with _registry_lock:
        meta = _registry.get(file_id)
    if meta is None:
        meta_path = _meta_path(file_id)
        if not meta_path.is_file():
            raise FileNotFoundError("unknown file id")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FileNotFoundError("unreadable file metadata") from exc
        with _registry_lock:
            _registry[file_id] = meta
    expected = str(meta.get("token") or "")
    if expected and not hmac.compare_digest(expected, str(token or "")):
        raise PermissionError("invalid download token")
    stored_name = str(meta.get("stored_name") or "")
    if not stored_name or Path(stored_name).name != stored_name:
        raise FileNotFoundError("invalid stored file name")
    path = _FILE_DIR / stored_name
    if not path.is_file():
        raise FileNotFoundError("file is no longer available")
    return path, meta


def file_card(meta: dict[str, Any], title: str = "") -> str:
    """Build the structured wire block understood by vm_richrender.js."""
    label = re.sub(r"\s*\|\s*", " / ", _clean(title or meta.get("title") or meta.get("filename")))
    fmt = str(meta.get("format") or "txt").upper()
    size = f"{meta.get('size_kb', 0.1)} KB"
    url = str(meta.get("download_url") or "")
    return f"[[FILE_CARD: {label} | {fmt} | {size} | {url}]]"


__all__ = [
    "generate_file",
    "get_file",
    "file_card",
    "normalize_format",
    "normalize_content",
    "FORMAT_ALIASES",
]
