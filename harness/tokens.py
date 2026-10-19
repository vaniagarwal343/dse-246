"""Token counts for the MCP server's input-token estimate (CONFIG.md §3; amendment 2026-10-19-05): tiktoken's o200k_base, offline.

tiktoken loads an encoding from its cache directory when the cached file's sha256 matches the pinned hash, and downloads it
otherwise. The file is kept in harness/data/tiktoken/ under tiktoken's cache name (sha1 of its URL), fetched once through
pipeline/net.py, so no request leaves this repo's clients. The count is an estimate: OpenAI does not publish gpt-6-astra's tokenizer.
"""
import hashlib
import os
from pathlib import Path

ENCODING = "o200k_base"
URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
CACHE = Path(__file__).resolve().parent / "data" / "tiktoken"
_encoding = None


def encoding():
    global _encoding
    if _encoding is None:
        cached = CACHE / hashlib.sha1(URL.encode()).hexdigest()
        if not cached.exists() or hashlib.sha256(cached.read_bytes()).hexdigest() != SHA256:
            raise SystemExit(f"{cached} is missing or not the pinned {ENCODING}; tiktoken would download it outside harness/net.py")
        os.environ["TIKTOKEN_CACHE_DIR"] = str(CACHE)
        import tiktoken
        _encoding = tiktoken.get_encoding(ENCODING)
    return _encoding


def count(text):
    return len(encoding().encode(text or "", disallowed_special=()))
