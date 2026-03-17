"""
Phase 5 — Question Paper Generator

Generates complete CBSE-compliant question papers grounded in NCERT content.
Questions are stored in question_bank and reused across papers to avoid repetition.

Exam types:
  class_test   — 10–20 marks, 1 chapter, teacher-defined
  weekly_test  — 20–25 marks, 1–2 chapters, 40 min
  monthly_test — 40–50 marks, 3–5 chapters, 90 min
  mid_term     — 80 marks, 50% syllabus, 3 hr
  pre_board    — 80 marks, full syllabus, 3 hr (board-pattern)
  board        — 80 marks, full syllabus, 3 hr (strict CBSE pattern)
"""

import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types as genai_types
from config import GEMINI_MODEL_FAST, GOOGLE_API_KEY
from db import get_db, init_db
from tools.database import search_content

_client = genai.Client(api_key=GOOGLE_API_KEY)


# ── Exam type templates ───────────────────────────────────────────────────────

EXAM_TEMPLATES = {
    "class_test": {
        "label": "Class Test",
        "default_marks": 20,
        "default_duration": 40,
        "sections": [
            {"name": "Section A", "type": "MCQ",        "marks": 1, "count": 10},
            {"name": "Section B", "type": "SAQ",        "marks": 2, "count": 5},
        ],
        "difficulty_mix": {"easy": 0.5, "medium": 0.4, "hard": 0.1},
        "bloom_targets": {"remember": 0.25, "understand": 0.35, "apply": 0.30, "analyse": 0.10},
    },
    "weekly_test": {
        "label": "Weekly Test",
        "default_marks": 25,
        "default_duration": 45,
        "sections": [
            {"name": "Section A", "type": "MCQ",        "marks": 1, "count": 10},
            {"name": "Section B", "type": "SAQ",        "marks": 3, "count": 5},
        ],
        "difficulty_mix": {"easy": 0.4, "medium": 0.45, "hard": 0.15},
        "bloom_targets": {"remember": 0.20, "understand": 0.35, "apply": 0.30, "analyse": 0.15},
    },
    "monthly_test": {
        "label": "Monthly Test",
        "default_marks": 50,
        "default_duration": 90,
        "sections": [
            {"name": "Section A", "type": "MCQ",        "marks": 1, "count": 15},
            {"name": "Section B", "type": "SAQ",        "marks": 2, "count": 5},
            {"name": "Section C", "type": "SAQ",        "marks": 3, "count": 5},
            {"name": "Section D", "type": "LAQ",        "marks": 4, "count": 2},
        ],
        "difficulty_mix": {"easy": 0.30, "medium": 0.50, "hard": 0.20},
        "bloom_targets": {"remember": 0.15, "understand": 0.30, "apply": 0.30, "analyse": 0.15, "evaluate": 0.10},
    },
    "mid_term": {
        "label": "Mid-Term Examination",
        "default_marks": 80,
        "default_duration": 180,
        "sections": [
            {"name": "Section A", "type": "MCQ",        "marks": 1, "count": 20},
            {"name": "Section B", "type": "SAQ",        "marks": 2, "count": 5},
            {"name": "Section C", "type": "SAQ",        "marks": 3, "count": 8},
            {"name": "Section D", "type": "LAQ",        "marks": 5, "count": 3},
            {"name": "Section E", "type": "case_study", "marks": 4, "count": 3},
        ],
        "difficulty_mix": {"easy": 0.25, "medium": 0.50, "hard": 0.25},
        "bloom_targets": {"remember": 0.12, "understand": 0.25, "apply": 0.30, "analyse": 0.18, "evaluate": 0.08, "create": 0.07},
    },
    "pre_board": {
        "label": "Pre-Board Examination",
        "default_marks": 80,
        "default_duration": 180,
        "sections": [
            {"name": "Section A", "type": "MCQ",        "marks": 1, "count": 20},
            {"name": "Section B", "type": "SAQ",        "marks": 2, "count": 5},
            {"name": "Section C", "type": "SAQ",        "marks": 3, "count": 8},
            {"name": "Section D", "type": "LAQ",        "marks": 5, "count": 3},
            {"name": "Section E", "type": "case_study", "marks": 4, "count": 3},
        ],
        "difficulty_mix": {"easy": 0.20, "medium": 0.45, "hard": 0.35},
        "bloom_targets": {"remember": 0.10, "understand": 0.25, "apply": 0.30, "analyse": 0.18, "evaluate": 0.10, "create": 0.07},
    },
    "board": {
        "label": "Board Examination (CBSE Pattern)",
        "default_marks": 80,
        "default_duration": 180,
        "sections": [
            {"name": "Section A", "type": "MCQ",        "marks": 1, "count": 20},
            {"name": "Section B", "type": "SAQ",        "marks": 2, "count": 5},
            {"name": "Section C", "type": "SAQ",        "marks": 3, "count": 8},
            {"name": "Section D", "type": "LAQ",        "marks": 5, "count": 3},
            {"name": "Section E", "type": "case_study", "marks": 4, "count": 3},
        ],
        "difficulty_mix": {"easy": 0.20, "medium": 0.45, "hard": 0.35},
        "bloom_targets": {"remember": 0.10, "understand": 0.22, "apply": 0.30, "analyse": 0.18, "evaluate": 0.12, "create": 0.08},
    },
}

# Map Bloom's % targets to question bloom levels (ordered priority per section type)
SECTION_BLOOM = {
    "MCQ":        ["remember", "understand", "apply"],
    "SAQ":        ["understand", "apply", "analyse"],
    "LAQ":        ["apply", "analyse", "evaluate", "create"],
    "case_study": ["apply", "analyse", "evaluate"],
}


# ── Question bank helpers ─────────────────────────────────────────────────────

def _pull_from_bank(
    grade: int, subject: str, chapter: int | None,
    question_type: str, bloom_level: str, difficulty: str, marks: int,
    exclude_ids: set,
) -> dict | None:
    """Try to reuse an existing question from the bank."""
    with get_db() as conn:
        placeholders = ",".join("?" * len(exclude_ids)) if exclude_ids else "NULL"
        query = f"""
            SELECT * FROM question_bank
            WHERE grade=? AND subject=? AND question_type=?
              AND bloom_level=? AND difficulty=? AND marks=?
              {"AND chapter=?" if chapter else ""}
              {"AND id NOT IN (" + placeholders + ")" if exclude_ids else ""}
            ORDER BY times_used ASC, RANDOM()
            LIMIT 1
        """
        params = [grade, subject, question_type, bloom_level, difficulty, marks]
        if chapter:
            params.append(chapter)
        params.extend(exclude_ids)
        row = conn.execute(query, params).fetchone()
        if row:
            conn.execute("UPDATE question_bank SET times_used=times_used+1 WHERE id=?", (row["id"],))
            return dict(row)
    return None


def _save_to_bank(q: dict) -> int:
    """Persist a newly generated question. Returns its DB id."""
    init_db()
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO question_bank
            (grade, subject, chapter, topic, question_type, bloom_level, difficulty, marks,
             question, answer, marking_scheme, distractors, case_passage, source_chunks, times_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            q["grade"], q["subject"], q.get("chapter"), q.get("topic"),
            q["question_type"], q["bloom_level"], q["difficulty"], q["marks"],
            q["question"], q["answer"],
            json.dumps(q.get("marking_scheme", [])),
            json.dumps(q.get("distractors", [])),
            q.get("case_passage"),
            json.dumps(q.get("source_chunks", [])),
        ))
        return cur.lastrowid


# ── Question generation (single, with bank storage) ──────────────────────────

def _generate_one(
    grade: int, subject: str, chapter: int | None, topic: str,
    question_type: str, bloom_level: str, difficulty: str, marks: int,
) -> dict:
    """Generate one question via Gemini, store in bank, return it."""
    chunks = search_content(
        query=topic, grade=grade, subject=subject,
        bloom_level=bloom_level, top_k=3,
    )
    if not chunks and chapter:
        chunks = search_content(query=topic, grade=grade, subject=subject, top_k=3)

    rag_text = "\n\n---\n\n".join(c["text"] for c in chunks) if chunks else f"Topic: {topic}"
    source_refs = [f"{c['source_file']}[{c['chunk_index']}]" for c in chunks]

    if question_type == "MCQ":
        distractor_note = "Include exactly 3 plausible but incorrect distractors in 'distractors'."
        type_note = "MCQ (multiple choice, one correct answer)"
    elif question_type == "SAQ":
        distractor_note = "Set 'distractors' to []."
        type_note = f"Short Answer Question ({marks} marks, ~{marks * 25} words expected)"
    elif question_type == "LAQ":
        distractor_note = "Set 'distractors' to []."
        type_note = f"Long Answer Question ({marks} marks, ~{marks * 40} words expected)"
    else:  # case_study
        distractor_note = "Set 'distractors' to []."
        type_note = f"Case-study based question ({marks} marks)"

    prompt = (
        f"Generate a CBSE-style {type_note}.\n"
        f"Grade: {grade}, Subject: {subject}\n"
        f"Topic: {topic}\n"
        f"Bloom's level: {bloom_level}\n"
        f"Difficulty: {difficulty}\n"
        f"Marks: {marks}\n"
        f"{distractor_note}\n\n"
        f"Base the question strictly on this NCERT source material:\n{rag_text}\n\n"
        f"Return a JSON object with these exact fields:\n"
        f"  question (string), bloom_level (string), marks (int),\n"
        f"  answer (string), marking_scheme (array of strings), distractors (array of strings).\n"
        f"For case_study type, also include a 'case_passage' field (string, 80–120 words)."
    )

    resp = _client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )

    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError:
        data = {"question": resp.text, "answer": "", "marking_scheme": [], "distractors": []}

    q = {
        "grade":         grade,
        "subject":       subject,
        "chapter":       chapter,
        "topic":         topic,
        "question_type": question_type,
        "bloom_level":   data.get("bloom_level", bloom_level),
        "difficulty":    difficulty,
        "marks":         marks,
        "question":      data.get("question", ""),
        "answer":        data.get("answer", ""),
        "marking_scheme": data.get("marking_scheme", []),
        "distractors":   data.get("distractors", []),
        "case_passage":  data.get("case_passage"),
        "source_chunks": source_refs,
    }
    q["id"] = _save_to_bank(q)
    return q


# ── Chapter → topics resolver ─────────────────────────────────────────────────

def _topics_for_chapters(grade: int, subject: str, chapters: list[int] | None) -> list[dict]:
    """Return distinct (chapter, topic) pairs from DB, optionally filtered by chapters."""
    with get_db() as conn:
        if chapters:
            placeholders = ",".join("?" * len(chapters))
            rows = conn.execute(f"""
                SELECT DISTINCT chapter, topic FROM content_chunks
                WHERE grade=? AND subject=? AND chapter IN ({placeholders})
                  AND topic IS NOT NULL AND topic != ''
                ORDER BY chapter, topic
            """, [grade, subject] + chapters).fetchall()
        else:
            rows = conn.execute("""
                SELECT DISTINCT chapter, topic FROM content_chunks
                WHERE grade=? AND subject=?
                  AND topic IS NOT NULL AND topic != ''
                ORDER BY chapter, topic
            """, (grade, subject)).fetchall()
    return [{"chapter": r["chapter"], "topic": r["topic"]} for r in rows]


# ── Core paper builder ────────────────────────────────────────────────────────

def generate_question_paper(
    grade: int,
    subject: str,
    exam_type: str = "monthly_test",
    chapters: list[int] | None = None,
    difficulty_mix: dict | None = None,
    include_answer_key: bool = True,
) -> dict:
    """
    Generate a complete CBSE-compliant question paper.

    Parameters
    ----------
    grade         : 7–12
    subject       : e.g. 'Mathematics', 'Science'
    exam_type     : class_test | weekly_test | monthly_test | mid_term | pre_board | board
    chapters      : list of chapter numbers to draw from; None = full syllabus
    difficulty_mix: override default {"easy": x, "medium": y, "hard": z}
    include_answer_key: include answers and marking schemes in output
    """
    init_db()

    template = EXAM_TEMPLATES.get(exam_type, EXAM_TEMPLATES["monthly_test"])
    diff_mix = difficulty_mix or template["difficulty_mix"]
    bloom_targets = template["bloom_targets"]
    diff_levels = list(diff_mix.keys())

    topic_pool = _topics_for_chapters(grade, subject, chapters)
    if not topic_pool:
        return {"error": f"No topics found for Grade {grade} {subject}. Run pipeline.py first."}

    # Assign difficulty to each question slot by section
    import random
    random.seed()

    used_ids: set[int] = set()
    sections_out = []
    bloom_tally: dict[str, int] = {}
    chapter_tally: dict[int, int] = {}
    total_marks_actual = 0

    for sec in template["sections"]:
        sec_bloom_priority = SECTION_BLOOM.get(sec["type"], ["understand", "apply"])
        questions_out = []

        for i in range(sec["count"]):
            # Pick difficulty by weighted random
            diff = random.choices(diff_levels, weights=[diff_mix[d] for d in diff_levels])[0]
            # Pick bloom level from section priority, cycling
            bloom = sec_bloom_priority[i % len(sec_bloom_priority)]

            # Pick a topic from pool (rotate through)
            t = topic_pool[(len(questions_out) + len(sections_out) * 10) % len(topic_pool)]

            # Try bank first
            q = _pull_from_bank(
                grade=grade, subject=subject, chapter=t["chapter"],
                question_type=sec["type"], bloom_level=bloom,
                difficulty=diff, marks=sec["marks"], exclude_ids=used_ids,
            )

            # Generate fresh if bank miss
            if q is None:
                q = _generate_one(
                    grade=grade, subject=subject, chapter=t["chapter"],
                    topic=t["topic"], question_type=sec["type"],
                    bloom_level=bloom, difficulty=diff, marks=sec["marks"],
                )

            used_ids.add(q["id"])
            bloom_tally[q["bloom_level"]] = bloom_tally.get(q["bloom_level"], 0) + 1
            chapter_tally[q.get("chapter") or 0] = chapter_tally.get(q.get("chapter") or 0, 0) + 1
            total_marks_actual += sec["marks"]

            entry = {
                "q_no":          f"{sec['name']} Q{i + 1}",
                "question_type": sec["type"],
                "marks":         sec["marks"],
                "bloom_level":   q["bloom_level"],
                "difficulty":    q["difficulty"],
                "topic":         q.get("topic"),
                "chapter":       q.get("chapter"),
                "question":      q["question"],
            }
            if sec["type"] == "case_study" and q.get("case_passage"):
                entry["case_passage"] = q["case_passage"]
            if sec["type"] == "MCQ":
                opts = [q["answer"]] + (json.loads(q["distractors"]) if isinstance(q["distractors"], str) else q.get("distractors", []))
                random.shuffle(opts)
                entry["options"] = opts
            if include_answer_key:
                entry["answer"] = q["answer"]
                entry["marking_scheme"] = json.loads(q["marking_scheme"]) if isinstance(q["marking_scheme"], str) else q.get("marking_scheme", [])

            questions_out.append(entry)

        sections_out.append({
            "name":         sec["name"],
            "type":         sec["type"],
            "marks_each":   sec["marks"],
            "count":        sec["count"],
            "total_marks":  sec["marks"] * sec["count"],
            "instructions": _section_instructions(sec["type"], sec["marks"]),
            "questions":    questions_out,
        })

    total_q = sum(s["count"] for s in template["sections"])
    bloom_pct = {k: round(v / total_q * 100, 1) for k, v in bloom_tally.items()}

    return {
        "exam_type":        exam_type,
        "label":            template["label"],
        "grade":            grade,
        "subject":          subject,
        "chapters_covered": sorted(chapters) if chapters else "full syllabus",
        "total_marks":      total_marks_actual,
        "duration_minutes": template["default_duration"],
        "total_questions":  total_q,
        "sections":         sections_out,
        "bloom_summary":    bloom_pct,
        "chapter_coverage": dict(sorted(chapter_tally.items())),
        "difficulty_mix_applied": diff_mix,
        "generated_at":     datetime.utcnow().isoformat() + "Z",
        "note": "Answer key included." if include_answer_key else "Answer key omitted.",
    }


def _section_instructions(qtype: str, marks: int) -> str:
    if qtype == "MCQ":
        return f"Each question carries {marks} mark. Choose the most appropriate option."
    elif qtype == "SAQ":
        return f"Each question carries {marks} marks. Answer in {marks * 25}–{marks * 35} words."
    elif qtype == "LAQ":
        return f"Each question carries {marks} marks. Answer in detail ({marks * 35}–{marks * 50} words)."
    elif qtype == "case_study":
        return f"Read the passage carefully and answer the questions that follow. Each carries {marks} marks."
    return ""
