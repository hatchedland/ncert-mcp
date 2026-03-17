"""
REST API — CBSE Ed-Tech Content Platform

Exposes all MCP tools as HTTP endpoints with Supabase auth and per-user rate limiting.

Run:
    cd /Users/rajanyadav/Documents/ed-stuff
    .venv313/bin/uvicorn src.api:app --reload --port 8000

Docs: http://localhost:8000/docs
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from auth import get_current_user
from config import SUPABASE_ANON_KEY, SUPABASE_URL
from tools.filesystem import (
    get_chapter,
    get_chapter_metadata,
    list_books,
    list_topics,
    search_chapters,
)
from tools.database import get_curriculum_map, search_content
from tools.generation import generate_explanation, generate_question, stream_explanation, stream_question
from tools.graph import get_learning_path, get_prerequisites
from tools.question_paper import EXAM_TEMPLATES, generate_question_paper
from usage import check_and_increment, get_usage_summary

app = FastAPI(
    title="CBSE Ed-Tech Content API",
    description="NCERT-grounded content platform: search, explain, question generation, curriculum graph.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_CHAT_HTML = Path(__file__).parent.parent / "examples" / "chat.html"


# ── UI + meta ─────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return FileResponse(str(_CHAT_HTML))


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.get("/config", tags=["meta"])
def public_config():
    """Return Supabase public config needed by the frontend."""
    return {
        "supabase_url":      SUPABASE_URL,
        "supabase_anon_key": SUPABASE_ANON_KEY,
    }


# ── Usage ─────────────────────────────────────────────────────────────────────

@app.get("/usage/me", tags=["usage"])
def my_usage(user: dict = Depends(get_current_user)):
    """Return today's usage counts and remaining quota for the authenticated user."""
    return get_usage_summary(user["id"])


# ── Books (public) ────────────────────────────────────────────────────────────

@app.get("/books", tags=["books"])
def api_list_books(
    grade:   int | None = Query(None, description="Filter by grade (7–12)"),
    subject: str | None = Query(None, description="Filter by subject name"),
):
    """List all available NCERT textbooks, optionally filtered by grade and/or subject."""
    return list_books(grade=grade, subject=subject)


@app.get("/books/{grade}/{subject}/topics", tags=["books"])
def api_list_topics(grade: int, subject: str):
    """Return chapter numbers and titles for a textbook."""
    try:
        return list_topics(grade=grade, subject=subject)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/books/{grade}/{subject}/chapters/{chapter}", tags=["books"])
def api_get_chapter(grade: int, subject: str, chapter: int):
    """Return full extracted text and metadata for one NCERT chapter."""
    try:
        return get_chapter(grade=grade, subject=subject, chapter=chapter)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/books/{grade}/{subject}/chapters/{chapter}/metadata", tags=["books"])
def api_get_chapter_metadata(grade: int, subject: str, chapter: int):
    """Return only the metadata sidecar for a chapter (fast, no PDF parsing)."""
    try:
        return get_chapter_metadata(grade=grade, subject=subject, chapter=chapter)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/search/chapters", tags=["search"])
def api_search_chapters(
    query:   str         = Query(..., description="Keyword search query"),
    grade:   int | None  = Query(None, description="Scope to a grade"),
    subject: str | None  = Query(None, description="Scope to a subject"),
    top_k:   int         = Query(5, ge=1, le=20, description="Max results"),
):
    """BM25 keyword search across all downloaded NCERT chapter PDFs."""
    return search_chapters(query=query, grade=grade, subject=subject, top_k=top_k)


@app.get("/search/content", tags=["search"])
def api_search_content(
    query:       str        = Query(..., description="Semantic search query"),
    grade:       int | None = Query(None, description="Filter by grade"),
    subject:     str | None = Query(None, description="Filter by subject"),
    bloom_level: str | None = Query(None, description="remember|understand|apply|analyse|evaluate|create"),
    top_k:       int        = Query(8, ge=1, le=20, description="Max results"),
    user: dict = Depends(get_current_user),
):
    """Semantic vector search over embedded NCERT chunks."""
    check_and_increment(user["id"], "search")
    return search_content(
        query=query, grade=grade, subject=subject,
        bloom_level=bloom_level, top_k=top_k,
    )


@app.get("/curriculum/{grade}/{subject}", tags=["curriculum"])
def api_get_curriculum_map(grade: int, subject: str):
    """Return topics and Bloom's level distribution across all chapters for a grade/subject."""
    return get_curriculum_map(grade=grade, subject=subject)


# ── Generation (auth + rate-limited) ─────────────────────────────────────────

class ExplainRequest(BaseModel):
    grade:    int
    subject:  str
    topic:    str
    language: str = "en"


@app.post("/explain", tags=["generation"])
def api_generate_explanation(
    body: ExplainRequest,
    user: dict = Depends(get_current_user),
):
    """
    Stream a RAG-grounded explanation of a CBSE topic using Gemini (SSE).
    Each SSE event is one of:
      data: {"type": "chunk", "text": "..."}
      data: {"type": "done",  "source_chunks": [...], "model_used": "..."}
    language: 'en' (default) or 'hi' for Hindi.
    """
    check_and_increment(user["id"], "explain")

    def event_stream():
        for text, meta in stream_explanation(
            grade=body.grade, subject=body.subject,
            topic=body.topic, language=body.language,
        ):
            if meta is None:
                yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'done', **meta})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class QuestionRequest(BaseModel):
    grade:         int
    subject:       str
    topic:         str
    bloom_level:   str = "understand"
    difficulty:    str = "medium"
    question_type: str = "MCQ"
    marks:         int = 1


@app.post("/question", tags=["generation"])
def api_generate_question(
    body: QuestionRequest,
    user: dict = Depends(get_current_user),
):
    """
    Stream a CBSE-style question grounded in NCERT content (SSE).
    Each SSE event is one of:
      data: {"type": "chunk", "text": "..."}   ← raw JSON being built
      data: {"type": "done",  ...question_fields}  ← final parsed question
    question_type: MCQ | SAQ | LAQ
    bloom_level:   remember | understand | apply | analyse | evaluate | create
    difficulty:    easy | medium | hard
    """
    check_and_increment(user["id"], "question")

    def event_stream():
        for text, meta in stream_question(
            grade=body.grade, subject=body.subject, topic=body.topic,
            bloom_level=body.bloom_level, difficulty=body.difficulty,
            question_type=body.question_type, marks=body.marks,
        ):
            if meta is None:
                yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'done', **meta})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Question paper (auth + rate-limited) ──────────────────────────────────────

class QuestionPaperRequest(BaseModel):
    grade:              int
    subject:            str
    exam_type:          str = "monthly_test"
    chapters:           list[int] | None = None
    difficulty_mix:     dict | None = None
    include_answer_key: bool = True


@app.get("/exam-types", tags=["question_paper"])
def api_exam_types():
    """List all supported exam types with their default structure."""
    return {
        k: {
            "label":            v["label"],
            "default_marks":    v["default_marks"],
            "default_duration": v["default_duration"],
            "sections":         v["sections"],
        }
        for k, v in EXAM_TEMPLATES.items()
    }


@app.post("/question-paper", tags=["question_paper"])
def api_generate_question_paper(
    body: QuestionPaperRequest,
    user: dict = Depends(get_current_user),
):
    """
    Generate a complete CBSE-compliant question paper.

    exam_type: class_test | weekly_test | monthly_test | mid_term | pre_board | board
    chapters: list of chapter numbers; omit for full syllabus
    difficulty_mix: optional override e.g. {"easy": 0.3, "medium": 0.5, "hard": 0.2}
    """
    check_and_increment(user["id"], "question_paper")
    result = generate_question_paper(
        grade=body.grade,
        subject=body.subject,
        exam_type=body.exam_type,
        chapters=body.chapters,
        difficulty_mix=body.difficulty_mix,
        include_answer_key=body.include_answer_key,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Curriculum graph (public) ─────────────────────────────────────────────────

@app.get("/graph/prerequisites", tags=["graph"])
def api_get_prerequisites(
    topic:   str = Query(..., description="Target topic name"),
    grade:   int = Query(..., description="Grade of the target topic"),
    subject: str = Query(..., description="Subject of the target topic"),
):
    """Return direct prerequisite topics a student must master before this topic."""
    return get_prerequisites(topic=topic, grade=grade, subject=subject)


@app.get("/graph/learning-path", tags=["graph"])
def api_get_learning_path(
    topic:   str = Query(..., description="Target topic name"),
    grade:   int = Query(..., description="Grade of the target topic"),
    subject: str = Query(..., description="Subject of the target topic"),
):
    """
    Return the full ordered learning path to reach a topic.
    Topics are ordered from most foundational to the target (roots first).
    """
    return get_learning_path(topic=topic, grade=grade, subject=subject)
