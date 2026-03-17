"""
Phase 2 MCP tools — SQLite + Qdrant-backed semantic search.

Requires pipeline.py to have been run first to populate the database.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types as genai_types
from config import GEMINI_EMBED, GOOGLE_API_KEY
from db import COLLECTION_NAME, get_db, get_qdrant

_client = genai.Client(api_key=GOOGLE_API_KEY)


def _embed_query(text: str) -> list[float]:
    result = _client.models.embed_content(
        model=GEMINI_EMBED,
        contents=text,
        config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return result.embeddings[0].values


def search_content(
    query: str,
    grade: "int | None" = None,
    subject: "str | None" = None,
    bloom_level: "str | None" = None,
    top_k: int = 8,
) -> list[dict]:
    """Semantic vector search over embedded chunks with optional metadata filters."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    embedding = _embed_query(query)

    conditions = []
    if grade is not None:
        conditions.append(FieldCondition(key="grade", match=MatchValue(value=grade)))
    if subject:
        conditions.append(FieldCondition(key="subject", match=MatchValue(value=subject)))
    if bloom_level:
        conditions.append(FieldCondition(key="bloom_level", match=MatchValue(value=bloom_level)))

    qfilter = Filter(must=conditions) if conditions else None

    qclient = get_qdrant()
    try:
        result = qclient.query_points(
            collection_name=COLLECTION_NAME,
            query=embedding,
            query_filter=qfilter,
            limit=top_k,
            with_payload=True,
        )
        hits = result.points
    finally:
        qclient.close()

    results = []
    for hit in hits:
        p = hit.payload
        with get_db() as conn:
            row = conn.execute(
                "SELECT text FROM content_chunks WHERE source_file=? AND chunk_index=?",
                (p["source_file"], p["chunk_index"]),
            ).fetchone()
        full_text = row["text"] if row else p.get("text", "")
        results.append({
            "score":       round(hit.score, 4),
            "grade":       p["grade"],
            "subject":     p["subject"],
            "chapter":     p["chapter"],
            "chunk_index": p["chunk_index"],
            "bloom_level": p["bloom_level"],
            "topic":       p["topic"],
            "difficulty":  p["difficulty"],
            "text":        full_text,
            "source_file": p["source_file"],
        })

    return results


def get_curriculum_map(grade: int, subject: str) -> dict:
    """Return topics and Bloom's distribution across chapters from the DB."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT chapter, topic, bloom_level, COUNT(*) as chunk_count
               FROM content_chunks
               WHERE grade=? AND subject=?
               GROUP BY chapter, topic, bloom_level
               ORDER BY chapter, topic""",
            (grade, subject),
        ).fetchall()

    if not rows:
        return {
            "grade": grade,
            "subject": subject,
            "chapters": [],
            "note": "No data found. Run pipeline.py first to populate the database.",
        }

    chapters: dict[int, dict] = {}
    for row in rows:
        ch = row["chapter"]
        if ch not in chapters:
            chapters[ch] = {"chapter": ch, "topics": {}}
        topic = row["topic"] or "General"
        if topic not in chapters[ch]["topics"]:
            chapters[ch]["topics"][topic] = {"bloom_levels": {}}
        chapters[ch]["topics"][topic]["bloom_levels"][row["bloom_level"]] = row["chunk_count"]

    chapter_list = [
        {
            "chapter": ch_num,
            "topics": [
                {"name": t, "bloom_distribution": data["bloom_levels"]}
                for t, data in chapters[ch_num]["topics"].items()
            ],
        }
        for ch_num in sorted(chapters)
    ]

    return {"grade": grade, "subject": subject, "chapters": chapter_list}
