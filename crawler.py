"""
Web crawler + Pinecone vector integration for Valleymind-AI.

Scrapes readable text from a target URL using requests + BeautifulSoup,
chunks it, generates random embedding vectors, and upserts them into the
Serverless 'valleymind-knowledge' Pinecone index on AWS us-east-1.

Usage:
    python crawler.py https://example.com/page
"""

import os
import re
import sys
import hashlib
import argparse
import numpy as np
import requests
from bs4 import BeautifulSoup


_dotenv_loaded = False
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip().strip("\"'")
            if _key and not os.environ.get(_key):
                os.environ[_key] = _val

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "").strip()
PINECONE_INDEX_NAME = "valleymind-knowledge"
PINECONE_CLOUD = "aws"
PINECONE_REGION = "us-east-1"
PINECONE_NAMESPACE = "web-crawler"
VECTOR_DIMENSION = 384


def _validate_env():
    if not PINECONE_API_KEY:
        print("[ERROR] PINECONE_API_KEY environment variable is not set.")
        print("        Add it to your .env file or set it in the shell.")
        sys.exit(1)


def _fetch_html(url: str) -> str:
    print(f"[FETCH] Requesting {url} ...")
    resp = requests.get(url, timeout=30, headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
    })
    resp.raise_for_status()
    print(f"[FETCH] Status {resp.status_code}, {len(resp.text)} bytes")
    return resp.text


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "footer", "nav", "header",
                     "aside", "noscript", "iframe", "form", "button"]):
        tag.decompose()

    parts = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
        text = tag.get_text(strip=True)
        if text:
            parts.append(text)

    return "\n".join(parts)


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _chunk_text(text: str, max_chars: int = 800, overlap: int = 80) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    buffer = ""

    for sentence in sentences:
        if len(buffer) + len(sentence) + 1 <= max_chars:
            if buffer:
                buffer += " "
            buffer += sentence
        else:
            if buffer:
                chunks.append(buffer)
            overlap_words = buffer.split()[-3:] if buffer else []
            buffer = " ".join(overlap_words) + " " + sentence if overlap_words else sentence

    if buffer:
        chunks.append(buffer)

    return chunks if chunks else [text]


def _make_vector(text: str, dim: int = VECTOR_DIMENSION) -> list[float]:
    seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    vec = rng.normal(0, 0.1, dim).astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


def _init_index():
    from pinecone import Pinecone, ServerlessSpec

    pc = Pinecone(api_key=PINECONE_API_KEY)

    existing = [idx["name"] for idx in pc.list_indexes()]
    if PINECONE_INDEX_NAME not in existing:
        print(f"[PINECONE] Creating index '{PINECONE_INDEX_NAME}' ...")
        pc.create_index(
            name=PINECONE_INDEX_NAME,
            dimension=VECTOR_DIMENSION,
            metric="cosine",
            spec=ServerlessSpec(cloud=PINECONE_CLOUD, region=PINECONE_REGION),
        )
        print(f"[PINECONE] Index '{PINECONE_INDEX_NAME}' created.")
    else:
        print(f"[PINECONE] Index '{PINECONE_INDEX_NAME}' already exists.")

    return pc.Index(PINECONE_INDEX_NAME)


def _upsert(index, chunks: list[str], source_url: str, namespace: str = PINECONE_NAMESPACE):
    vectors = []
    for i, chunk in enumerate(chunks):
        vector = _make_vector(chunk)
        doc_id = hashlib.sha256(
            f"{source_url}::chunk-{i}".encode("utf-8")
        ).hexdigest()[:24]
        vectors.append({
            "id": doc_id,
            "values": vector,
            "metadata": {
                "source_url": source_url,
                "raw_text": chunk,
                "chunk_index": i,
                "total_chunks": len(chunks),
            },
        })

    print(f"[UPSERT] Sending {len(vectors)} vectors to namespace '{namespace}' ...")
    index.upsert(vectors=vectors, namespace=namespace)
    print(f"[UPSERT] Done. Inserted {len(vectors)} chunks from: {source_url}")


def crawl_and_index(url: str, namespace: str = PINECONE_NAMESPACE):
    _validate_env()

    html = _fetch_html(url)
    raw_text = _extract_text(html)
    cleaned = _clean_text(raw_text)

    if not cleaned:
        print("[WARN] No readable text extracted from the page.")
        return

    print(f"[EXTRACT] {len(cleaned)} characters of clean text.")

    chunks = _chunk_text(cleaned)
    print(f"[CHUNK] {len(chunks)} chunks generated.")

    index = _init_index()
    _upsert(index, chunks, url, namespace)

    print(f"\n[DONE] URL indexed successfully: {url}")


def main():
    parser = argparse.ArgumentParser(
        description="Crawl a URL and index its content into Pinecone."
    )
    parser.add_argument("url", nargs="?", help="Target URL to crawl")
    parser.add_argument(
        "--namespace",
        default=PINECONE_NAMESPACE,
        help=f"Pinecone namespace (default: {PINECONE_NAMESPACE})",
    )
    args = parser.parse_args()

    url = args.url or os.environ.get("CRAWLER_TARGET_URL", "").strip()

    if not url:
        parser.print_help()
        print("\n[ERROR] No URL provided. Pass a URL or set CRAWLER_TARGET_URL.")
        sys.exit(1)

    crawl_and_index(url, namespace=args.namespace)


if __name__ == "__main__":
    main()
