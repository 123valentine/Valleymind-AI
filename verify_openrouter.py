import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()


def verify_embedding_dimensions() -> int:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("[ERROR] OPENROUTER_API_KEY not found in .env", file=sys.stderr)
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    _model = os.getenv("EMBEDDING_MODEL", "baai/bge-base-en-v1.5").strip()
    payload = {
        "model": _model,
        "input": "Valleymind AI embedding dimension verification",
    }

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ERROR] API call failed: {exc}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    try:
        embedding = data["data"][0]["embedding"]
    except (KeyError, IndexError) as exc:
        print(f"[ERROR] Unexpected response structure: {exc}", file=sys.stderr)
        sys.exit(1)

    return len(embedding)


def main():
    dims = verify_embedding_dimensions()
    _model = os.getenv("EMBEDDING_MODEL", "baai/bge-base-en-v1.5").strip()
    expected = int(os.getenv("EMBEDDING_DIMS", "768").strip())
    if dims == expected:
        print(f"Valleymind-AI now using {_model} ({dims} dimensions) aligned with Pinecone index.")
    else:
        print(f"Dimension mismatch: expected {expected}, got {dims}")


if __name__ == "__main__":
    main()
