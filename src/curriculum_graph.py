"""
Phase 3 — Curriculum Graph Builder

Reads all distinct topics from content_chunks, calls Gemini per subject to
identify prerequisite edges, and stores them in curriculum_edges.

Run once after pipeline.py has populated the DB:
    cd /Users/rajanyadav/Documents/ed-stuff/scripts
    ../.venv313/bin/python curriculum_graph.py

Safe to re-run — duplicate edges are silently skipped (INSERT OR IGNORE).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from google import genai
from google.genai import types as genai_types
from config import GEMINI_MODEL, GOOGLE_API_KEY
from db import get_db, init_db

_client = genai.Client(api_key=GOOGLE_API_KEY)


def _get_topics_by_subject() -> dict[str, list[dict]]:
    """Load all distinct (grade, subject, chapter, topic) from DB, grouped by subject."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT grade, subject, chapter, topic
            FROM content_chunks
            WHERE topic IS NOT NULL AND topic != ''
            ORDER BY subject, grade, chapter, topic
        """).fetchall()

    by_subject: dict[str, list[dict]] = {}
    for row in rows:
        subj = row["subject"]
        if subj not in by_subject:
            by_subject[subj] = []
        by_subject[subj].append({
            "grade":   row["grade"],
            "subject": row["subject"],
            "chapter": row["chapter"],
            "topic":   row["topic"],
        })
    return by_subject


def _build_edges_for_subject(subject: str, topics: list[dict]) -> list[dict]:
    """Call Gemini to identify prerequisite edges for all topics in a subject."""
    if len(topics) < 2:
        return []

    topics_text = "\n".join(
        f"  Grade {t['grade']}, Chapter {t['chapter']}: {t['topic']}"
        for t in topics
    )

    prompt = (
        f"You are a CBSE curriculum expert analysing the NCERT topic sequence for {subject}.\n\n"
        f"Below are all distinct topics from {subject} textbooks (Grades 7–12):\n"
        f"{topics_text}\n\n"
        f"Identify prerequisite relationships: where a student MUST understand Topic A "
        f"before they can properly learn Topic B.\n\n"
        f"Rules:\n"
        f"- Only add edges where the prerequisite is strong and pedagogically clear.\n"
        f"- Prerequisite topics should generally come from equal or lower grade levels.\n"
        f"- Do NOT add edges just because two topics appear in the same chapter.\n"
        f"- Confidence 1.0 = essential prerequisite. 0.7 = helpful but not strictly required.\n"
        f"- Only include topics that appear exactly in the list above.\n\n"
        f"Return a JSON object with an 'edges' array. Each edge has these exact fields:\n"
        f"  pre_grade (int), pre_subject (str), pre_chapter (int), pre_topic (str),\n"
        f"  post_grade (int), post_subject (str), post_chapter (int), post_topic (str),\n"
        f"  confidence (float 0.0–1.0), rationale (str, one sentence)."
    )

    resp = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )

    try:
        data = json.loads(resp.text)
        return data.get("edges", [])
    except (json.JSONDecodeError, AttributeError):
        print(f"  Warning: could not parse Gemini response for {subject}")
        return []


def _store_edges(edges: list[dict]) -> int:
    """Insert edges into curriculum_edges, skipping duplicates. Returns insert count."""
    inserted = 0
    with get_db() as conn:
        for e in edges:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO curriculum_edges
                    (pre_grade, pre_subject, pre_chapter, pre_topic,
                     post_grade, post_subject, post_chapter, post_topic,
                     confidence, rationale)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    e.get("pre_grade"),   e.get("pre_subject"),
                    e.get("pre_chapter"), e.get("pre_topic"),
                    e.get("post_grade"),  e.get("post_subject"),
                    e.get("post_chapter"), e.get("post_topic"),
                    e.get("confidence", 1.0),
                    e.get("rationale", ""),
                ))
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as ex:
                print(f"  Skipped edge ({e.get('pre_topic')} → {e.get('post_topic')}): {ex}")
    return inserted


def build_graph() -> None:
    """Build the curriculum prerequisite graph for all subjects in the DB."""
    init_db()

    print("Loading topics from DB...")
    by_subject = _get_topics_by_subject()

    if not by_subject:
        print("No topics found. Run pipeline.py first to populate the database.")
        return

    total_inserted = 0
    for subject, topics in sorted(by_subject.items()):
        print(f"\n{subject} ({len(topics)} topics) → calling Gemini...")
        edges = _build_edges_for_subject(subject, topics)
        inserted = _store_edges(edges)
        total_inserted += inserted
        print(f"  {len(edges)} edges proposed, {inserted} new stored")

    print(f"\nDone. Total new edges inserted: {total_inserted}")

    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM curriculum_edges").fetchone()[0]
        print(f"Total edges in graph: {total}")


if __name__ == "__main__":
    build_graph()
