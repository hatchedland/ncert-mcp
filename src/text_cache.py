"""
PDF → plain-text cache.

Extracted text is stored in data/processed/text_cache/ as
  {code}{ch:02d}.txt

so we never re-parse the same PDF twice.
"""

from pathlib import Path
import pdfplumber

from config import PROCESSED

TEXT_CACHE_DIR = PROCESSED / "text_cache"


def _cache_path(pdf_path: Path) -> Path:
    TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return TEXT_CACHE_DIR / (pdf_path.stem + ".txt")


def extract_text(pdf_path: Path) -> str:
    cache = _cache_path(pdf_path)
    if cache.exists():
        return cache.read_text(encoding="utf-8")

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
    text = "\n\n".join(pages)
    cache.write_text(text, encoding="utf-8")
    return text


def extract_page_count(pdf_path: Path) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)
