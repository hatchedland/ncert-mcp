"""
Phase 2 pipeline: text_cache → chunk → tag (Gemini Flash) → embed → SQLite + Qdrant.

Usage:
  python pipeline.py                     # process all cached chapters
  python pipeline.py --grades 7 8        # limit to specific grades
  python pipeline.py --subject Science   # limit to subject
  python pipeline.py --limit 3           # max chapters (for testing)

Cost: stays within Gemini free tier for local dev.
  text-embedding-004 : 1500 req/day, 100 RPM  (free)
  gemini-2.0-flash   : 1500 req/day, 1M tok/day (free)
"""

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

from google import genai
from google.genai import types as genai_types
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

sys.path.insert(0, str(Path(__file__).parent))

from config import GEMINI_EMBED, GEMINI_MODEL, GOOGLE_API_KEY, PROCESSED
from db import COLLECTION_NAME, get_db, get_qdrant, init_db

_client = genai.Client(api_key=GOOGLE_API_KEY)
console = Console()

TEXT_CACHE_DIR = PROCESSED / "text_cache"

# Reverse map: book_code → (grade, subject)
CODE_TO_BOOK: dict[str, tuple[int, str]] = {
    "gemh1": (7,  "Mathematics"),   "gesc1": (7,  "Science"),
    "gess1": (7,  "Geography"),     "gess2": (7,  "History"),
    "gess3": (7,  "Civics"),
    "hemh1": (8,  "Mathematics"),   "hesc1": (8,  "Science"),
    "hess2": (8,  "Geography"),     "hess3": (8,  "History"),
    "hess4": (8,  "Civics"),
    "iemh1": (9,  "Mathematics"),   "iesc1": (9,  "Science"),
    "iess1": (9,  "Geography"),     "iess2": (9,  "History"),
    "iess3": (9,  "Civics"),        "iess4": (9,  "Economics"),
    "jemh1": (10, "Mathematics"),   "jesc1": (10, "Science"),
    "jess1": (10, "Geography"),     "jess2": (10, "History"),
    "jess3": (10, "Civics"),        "jess4": (10, "Economics"),
    "kemh1": (11, "Mathematics"),   "keph1": (11, "Physics_1"),
    "keph2": (11, "Physics_2"),     "kech1": (11, "Chemistry_1"),
    "kech2": (11, "Chemistry_2"),   "kebo1": (11, "Biology"),
    "kehs1": (11, "History"),       "kehp1": (11, "Geography_1"),
    "kecs1": (11, "Civics"),
    "lemh1": (12, "Mathematics_1"), "lemh2": (12, "Mathematics_2"),
    "leph1": (12, "Physics_1"),     "leph2": (12, "Physics_2"),
    "lech1": (12, "Chemistry_1"),   "lech2": (12, "Chemistry_2"),
    "lebo1": (12, "Biology"),       "lehs1": (12, "History"),
    "lecs1": (12, "Civics"),
}


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, max_tokens: int = 512, overlap: int = 64) -> list[str]:
    """Split text into overlapping chunks, respecting paragraph boundaries."""
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: list[str] = []
    current: list[int] = []

    for para in paragraphs:
        para_tokens = enc.encode(para)
        if current and len(current) + len(para_tokens) > max_tokens:
            chunks.append(enc.decode(current))
            current = current[-overlap:]
        current.extend(para_tokens)
        while len(current) > max_tokens:
            chunks.append(enc.decode(current[:max_tokens]))
            current = current[max_tokens - overlap:]

    if current:
        chunks.append(enc.decode(current))

    return [c for c in chunks if len(c.strip()) > 50]


# ── Tagging ───────────────────────────────────────────────────────────────────

_DEFAULT_TAG = {"bloom_level": "understand", "topic": "General content", "difficulty": "medium"}


def _call_with_retry(fn, max_retries: int = 4):
    """Call fn(), retrying on rate-limit / server errors with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            is_retryable = any(code in msg for code in ("429", "500", "503", "RESOURCE_EXHAUSTED"))
            if is_retryable and attempt < max_retries - 1:
                wait = 2 ** (attempt + 2)  # 4s, 8s, 16s, 32s
                console.print(f"[yellow]  Rate limit / server error, retrying in {wait}s...[/yellow]")
                time.sleep(wait)
            else:
                raise


def tag_chunks(chunks: list[str], grade: int, subject: str) -> list[dict]:
    """Tag a batch of chunks using Gemini 2.5 Pro (JSON output, thinking disabled for speed)."""
    numbered = "\n\n".join(f"[{i+1}] {c[:600]}" for i, c in enumerate(chunks))
    prompt = (
        f"Grade: {grade}, Subject: {subject}\n\n"
        f"For each numbered chunk of NCERT text, return a JSON array with one object per chunk.\n"
        f"Each object must have:\n"
        f"  bloom_level: one of remember|understand|apply|analyse|evaluate|create\n"
        f"  topic: the concept covered (5-10 words)\n"
        f"  difficulty: one of easy|medium|hard\n\n"
        f"Chunks:\n{numbered}\n\n"
        f"Return ONLY a JSON array, no explanation."
    )
    try:
        resp = _call_with_retry(lambda: _client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=1,  # required when thinking is active
                response_mime_type="application/json",
            ),
        ))
        tags = json.loads(resp.text)
        if isinstance(tags, dict):
            tags = next(iter(tags.values())) if tags else []
        if not isinstance(tags, list):
            raise ValueError("Expected JSON array")
        while len(tags) < len(chunks):
            tags.append(_DEFAULT_TAG.copy())
        return tags[: len(chunks)]
    except Exception as e:
        console.print(f"[yellow]  Tagging failed: {e} — using defaults[/yellow]")
        return [_DEFAULT_TAG.copy() for _ in chunks]


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_text(text: str) -> list[float]:
    result = _call_with_retry(lambda: _client.models.embed_content(
        model=GEMINI_EMBED,
        contents=text,
        config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    ))
    return result.embeddings[0].values


# ── Per-file processing ───────────────────────────────────────────────────────

def parse_cache_filename(stem: str) -> "tuple[str, int] | None":
    """'gesc101' → ('gesc1', 1). Returns None if unrecognised."""
    if len(stem) < 7:
        return None
    code = stem[:5]
    try:
        chapter = int(stem[5:])
    except ValueError:
        return None
    return (code, chapter) if code in CODE_TO_BOOK else None


def already_processed(source_file: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM content_chunks WHERE source_file = ?",
            (source_file,),
        ).fetchone()
        return row[0] > 0


def process_file(txt_path: Path, qclient) -> int:
    """Chunk, tag, embed and store one chapter. Returns chunk count."""
    parsed = parse_cache_filename(txt_path.stem)
    if not parsed:
        return 0
    code, chapter = parsed
    grade, subject = CODE_TO_BOOK[code]
    source_file = txt_path.name

    if already_processed(source_file):
        return 0

    text = txt_path.read_text(encoding="utf-8")
    chunks = chunk_text(text)
    if not chunks:
        return 0

    # Tag in batches of 5 to reduce API calls
    tags: list[dict] = []
    for i in range(0, len(chunks), 5):
        batch = chunks[i : i + 5]
        tags.extend(tag_chunks(batch, grade, subject))
        time.sleep(1.5)  # stay inside 60 RPM free-tier limit

    # Embed + collect rows
    from qdrant_client.models import PointStruct

    rows = []
    points = []
    for idx, (chunk_str, tag) in enumerate(zip(chunks, tags)):
        try:
            embedding = embed_text(chunk_str)
            time.sleep(0.7)  # ~85 RPM, inside 100 RPM limit
        except Exception as e:
            console.print(f"[yellow]  Embed failed chunk {idx}: {e}[/yellow]")
            continue

        qdrant_id = str(uuid.uuid4())
        rows.append((
            source_file, grade, subject, chapter, idx,
            chunk_str,
            tag.get("bloom_level", "understand"),
            tag.get("topic", ""),
            tag.get("difficulty", "medium"),
            qdrant_id,
        ))
        points.append(PointStruct(
            id=qdrant_id,
            vector=embedding,
            payload={
                "source_file": source_file,
                "grade":        grade,
                "subject":      subject,
                "chapter":      chapter,
                "chunk_index":  idx,
                "bloom_level":  tag.get("bloom_level", "understand"),
                "topic":        tag.get("topic", ""),
                "difficulty":   tag.get("difficulty", "medium"),
                "text":         chunk_str[:500],
            },
        ))

    if not rows:
        return 0

    with get_db() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO content_chunks
               (source_file, grade, subject, chapter, chunk_index,
                text, bloom_level, topic, difficulty, qdrant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    qclient.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def run_pipeline(
    grades: "list[int] | None" = None,
    subjects: "list[str] | None" = None,
    limit: "int | None" = None,
) -> None:
    init_db()
    qclient = get_qdrant()

    txt_files = sorted(TEXT_CACHE_DIR.glob("*.txt"))
    if not txt_files:
        console.print("[red]No text cache files found. Run ingest.py first.[/red]")
        qclient.close()
        return

    if grades or subjects:
        filtered = []
        for f in txt_files:
            parsed = parse_cache_filename(f.stem)
            if not parsed:
                continue
            code, _ = parsed
            g, s = CODE_TO_BOOK[code]
            if grades and g not in grades:
                continue
            if subjects and s.lower() not in [x.lower() for x in subjects]:
                continue
            filtered.append(f)
        txt_files = filtered

    if limit:
        txt_files = txt_files[:limit]

    total_chunks = 0
    skipped = 0

    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"),
        BarColumn(), TaskProgressColumn(), console=console
    ) as progress:
        task = progress.add_task("Processing...", total=len(txt_files))
        for txt_path in txt_files:
            parsed = parse_cache_filename(txt_path.stem)
            if parsed:
                code, ch = parsed
                g, s = CODE_TO_BOOK[code]
                desc = f"[cyan]Grade {g} {s} ch{ch:02d}[/cyan]"
            else:
                desc = f"[dim]{txt_path.name}[/dim]"
            progress.update(task, description=desc)

            if already_processed(txt_path.name):
                skipped += 1
                progress.advance(task)
                continue

            n = process_file(txt_path, qclient)
            total_chunks += n
            progress.advance(task)

    qclient.close()
    console.print(
        f"\n[green]Done.[/green] "
        f"{total_chunks} chunks stored, {skipped} files skipped (already processed)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 2: chunk → tag → embed → store")
    parser.add_argument("--grades", nargs="+", type=int, help="Limit to these grade numbers")
    parser.add_argument("--subject", nargs="+", help="Limit to these subjects")
    parser.add_argument("--limit", type=int, help="Max chapters to process (useful for testing)")
    args = parser.parse_args()
    run_pipeline(grades=args.grades, subjects=args.subject, limit=args.limit)
