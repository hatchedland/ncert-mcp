"""
ncert-mcp — NCERT/CBSE Content MCP Server

13 tools across 4 categories:
  Textbook tools  : list_books, list_topics, get_chapter, get_chapter_metadata, search_chapters
  RAG + generation: search_content, get_curriculum_map, generate_explanation,
                    generate_question, generate_question_paper
  Graph tools     : get_prerequisites, get_learning_path

Run:
  python src/mcp_server.py

Configure in Claude Desktop (~/.config/claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "ncert-mcp": {
        "command": "/path/to/ncert-mcp/.venv/bin/python",
        "args": ["/path/to/ncert-mcp/src/mcp_server.py"],
        "env": {
          "GOOGLE_API_KEY": "<your Gemini API key>"
        }
      }
    }
  }

Each user supplies their own GOOGLE_API_KEY.
The key is never stored in this repo — only passed at runtime via env.
See README.md for full setup and data pipeline instructions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from tools.filesystem import (
    get_chapter,
    get_chapter_metadata,
    list_books,
    list_topics,
    search_chapters,
)
from tools.database import get_curriculum_map, search_content
from tools.generation import generate_explanation, generate_question
from tools.graph import get_learning_path, get_prerequisites
from tools.question_paper import EXAM_TEMPLATES, generate_question_paper

mcp = FastMCP("ncert-cbse-content")


# ── Phase 1: file-system tools ────────────────────────────────────────────────

@mcp.tool()
def tool_list_books(grade: int, subject: str) -> str:
    """List all NCERT textbooks available in the corpus. Optionally filter by grade and/or subject."""
    import json
    return json.dumps(list_books(grade=grade, subject=subject), indent=2)


@mcp.tool()
def tool_get_chapter(grade: int, subject: str, chapter: int) -> str:
    """Return the full extracted text and metadata of one NCERT chapter."""
    import json
    try:
        return json.dumps(get_chapter(grade=grade, subject=subject, chapter=chapter), indent=2)
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


@mcp.tool()
def tool_get_chapter_metadata(grade: int, subject: str, chapter: int) -> str:
    """Return only the metadata (source URL, download date, book code) for a chapter — no PDF parsing."""
    import json
    try:
        return json.dumps(get_chapter_metadata(grade=grade, subject=subject, chapter=chapter), indent=2)
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


@mcp.tool()
def tool_list_topics(grade: int, subject: str) -> str:
    """Return chapter numbers and titles for a textbook."""
    import json
    try:
        return json.dumps(list_topics(grade=grade, subject=subject), indent=2)
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def tool_search_chapters(query: str, grade: int, subject: str, top_k: int = 5) -> str:
    """BM25 keyword search across all downloaded NCERT chapter PDFs. Optionally scope to a grade/subject."""
    import json
    return json.dumps(search_chapters(query=query, grade=grade, subject=subject, top_k=top_k), indent=2)


# ── Phase 2: RAG + generation tools ──────────────────────────────────────────

@mcp.tool()
def tool_search_content(
    query: str,
    grade: int,
    subject: str,
    bloom_level: str = "understand",
    top_k: int = 8,
) -> str:
    """
    Semantic vector search over embedded NCERT chunks.
    Supports filtering by grade, subject, and Bloom's level (remember|understand|apply|analyse|evaluate|create).
    Requires pipeline.py to have been run first.
    """
    import json
    return json.dumps(search_content(query=query, grade=grade, subject=subject, bloom_level=bloom_level, top_k=top_k), indent=2)


@mcp.tool()
def tool_get_curriculum_map(grade: int, subject: str) -> str:
    """
    Return the topic and Bloom's level distribution across all chapters for a grade/subject.
    Requires pipeline.py to have been run first.
    """
    import json
    return json.dumps(get_curriculum_map(grade=grade, subject=subject), indent=2)


@mcp.tool()
def tool_generate_explanation(grade: int, subject: str, topic: str, language: str = "en") -> str:
    """
    Generate a RAG-grounded explanation of a CBSE topic using Gemini 2.5 Pro.
    Retrieves relevant NCERT chunks then generates: definition → key idea → example → summary (max 300 words).
    language: 'en' (default) or 'hi' for Hindi.
    Requires pipeline.py to have been run first.
    """
    import json
    return json.dumps(generate_explanation(grade=grade, subject=subject, topic=topic, language=language), indent=2)


@mcp.tool()
def tool_generate_question(
    grade: int,
    subject: str,
    topic: str,
    bloom_level: str = "understand",
    difficulty: str = "medium",
    question_type: str = "MCQ",
    marks: int = 1,
) -> str:
    """
    Generate a structured CBSE-style question grounded in NCERT content.
    question_type: MCQ | SAQ | LAQ
    bloom_level: remember | understand | apply | analyse | evaluate | create
    difficulty: easy | medium | hard
    Requires pipeline.py to have been run first.
    """
    import json
    return json.dumps(generate_question(
        grade=grade, subject=subject, topic=topic,
        bloom_level=bloom_level, difficulty=difficulty,
        question_type=question_type, marks=marks,
    ), indent=2)


# ── Phase 3: curriculum graph tools ──────────────────────────────────────────

@mcp.tool()
def tool_get_prerequisites(topic: str, grade: int, subject: str) -> str:
    """
    Return the direct prerequisite topics a student must master before learning the given topic.
    Requires curriculum_graph.py to have been run first.
    """
    import json
    return json.dumps(get_prerequisites(topic=topic, grade=grade, subject=subject), indent=2)


@mcp.tool()
def tool_get_learning_path(topic: str, grade: int, subject: str) -> str:
    """
    Return the full ordered learning path (all prerequisite topics, most foundational first)
    that a student should follow to reach the given topic.
    Requires curriculum_graph.py to have been run first.
    """
    import json
    return json.dumps(get_learning_path(topic=topic, grade=grade, subject=subject), indent=2)


# ── Question paper generator ──────────────────────────────────────────────────

@mcp.tool()
def tool_generate_question_paper(
    grade: int,
    subject: str,
    exam_type: str,
    chapters: str,
    include_answer_key: bool,
) -> str:
    """
    Generate a complete CBSE-compliant question paper grounded in NCERT content.

    exam_type: class_test | weekly_test | monthly_test | mid_term | pre_board | board
    chapters: comma-separated chapter numbers e.g. "1,2,3" or "all" for full syllabus
    include_answer_key: true to include answers and marking schemes

    Each exam type has a preset structure (sections, marks, duration) and Bloom's distribution
    aligned with CBSE norms. Questions are drawn from the persistent question bank or freshly
    generated if not available.
    """
    import json
    chapter_list = None
    if chapters.lower() not in ("all", ""):
        try:
            chapter_list = [int(c.strip()) for c in chapters.split(",") if c.strip()]
        except ValueError:
            return json.dumps({"error": "chapters must be comma-separated integers or 'all'"})

    result = generate_question_paper(
        grade=grade,
        subject=subject,
        exam_type=exam_type,
        chapters=chapter_list,
        include_answer_key=include_answer_key,
    )
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
