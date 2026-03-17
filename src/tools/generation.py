"""
Phase 2 MCP tools — Gemini-backed content generation.

Pipeline A: generate_explanation — RAG + Gemini Flash explanation
Pipeline B: generate_question   — structured question generation
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types as genai_types
from config import GEMINI_MODEL_FAST, GOOGLE_API_KEY
from tools.database import search_content

_client = genai.Client(api_key=GOOGLE_API_KEY)


# ── Pedagogical stage config (NCF 2023 / NEP 2020 5+3+3+4) ───────────────────

def _stage_config(grade: int) -> dict:
    """Return pedagogical stage parameters for a grade."""
    if grade <= 5:
        return {
            "stage": "Foundational/Preparatory",
            "tone": "very simple, friendly, story-like",
            "vocabulary": "everyday words only, avoid jargon",
            "example_style": "from home, playground, or familiar Indian daily life",
            "max_words": 150,
            "depth": "basic definition and one concrete example only",
        }
    elif grade <= 8:
        return {
            "stage": "Middle",
            "tone": "clear and encouraging, activity-based thinking",
            "vocabulary": "introduce subject terms with simple explanations",
            "example_style": "relatable Indian contexts — markets, kitchen, cricket, seasons",
            "max_words": 250,
            "depth": "definition → key idea → one worked example → summary",
        }
    elif grade <= 10:
        return {
            "stage": "Secondary",
            "tone": "formal but accessible, exam-aware",
            "vocabulary": "precise CBSE terminology, ready for board exam answers",
            "example_style": "real-world applications and NCERT-style solved examples",
            "max_words": 350,
            "depth": "definition → concept → derivation or proof if relevant → application → exam tip",
        }
    else:
        return {
            "stage": "Higher Secondary",
            "tone": "analytical and rigorous, JEE/NEET/board aligned",
            "vocabulary": "full technical terminology with precise definitions",
            "example_style": "solved problems with step-by-step working, competitive exam style",
            "max_words": 450,
            "depth": "formal definition → theory → derivation → solved numericals or case analysis → common mistakes",
        }


def _explanation_prompt(grade: int, subject: str, topic: str, rag_text: str, language: str) -> str:
    stage = _stage_config(grade)
    lang_instruction = (
        "Respond in Hindi, using English for subject-specific terms where "
        "the Hindi equivalent is rarely used in Indian classrooms."
        if language == "hi"
        else "Respond in English."
    )
    return (
        f"You are an expert CBSE {subject} teacher for Grade {grade} "
        f"({stage['stage']} stage, NCF 2023).\n"
        f"Tone: {stage['tone']}.\n"
        f"Vocabulary: {stage['vocabulary']}.\n"
        f"Examples should be: {stage['example_style']}.\n"
        f"Only state facts present in the source material below. Never hallucinate.\n\n"
        f"Source material (NCERT):\n{rag_text}\n\n"
        f"Task: Explain '{topic}' for a Grade {grade} student.\n"
        f"Format: {stage['depth']}. Max {stage['max_words']} words.\n"
        f"Formatting rules:\n"
        f"- Use ## for section headings, **term** to bold key vocabulary on first use.\n"
        f"- Use numbered lists for steps/sequences, bullet lists for types or properties.\n"
        f"- Use > **Note:** for important reminders or NCERT callouts.\n"
        f"- Use > **Example:** for worked examples with step-by-step working.\n"
        f"- Use Markdown tables (| col | col |) for comparisons (e.g. plant vs animal cell).\n"
        f"- For any mathematical or chemical formula use LaTeX: $...$ inline, $$...$$ for display equations.\n"
        f"{lang_instruction}"
    )


def generate_explanation(
    grade: int,
    subject: str,
    topic: str,
    language: str = "en",
) -> dict:
    """Pipeline A: RAG-grounded explanation, grade-stage aware."""
    chunks = search_content(query=topic, grade=grade, subject=subject, top_k=4)

    if not chunks:
        return {
            "topic": topic, "grade": grade, "subject": subject, "language": language,
            "explanation": "No relevant content found. Run pipeline.py to populate the database.",
            "source_chunks": [], "model_used": GEMINI_MODEL_FAST,
            "stage": _stage_config(grade)["stage"],
        }

    rag_text = "\n\n---\n\n".join(c["text"] for c in chunks)
    prompt = _explanation_prompt(grade, subject, topic, rag_text, language)

    resp = _client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=genai_types.GenerateContentConfig(temperature=0.4),
    )

    mermaid = (
        _generate_mermaid(topic, subject, grade, resp.text)
        if _should_generate_diagram(topic, resp.text)
        else {"diagram": None, "caption": None}
    )

    return {
        "topic":           topic,
        "grade":           grade,
        "subject":         subject,
        "language":        language,
        "stage":           _stage_config(grade)["stage"],
        "explanation":     resp.text,
        "source_chunks":   [f"{c['source_file']}[{c['chunk_index']}]" for c in chunks],
        "model_used":      GEMINI_MODEL_FAST,
        "mermaid_diagram": mermaid["diagram"],
        "mermaid_caption": mermaid["caption"],
    }


_QUESTION_FORMATTING = (
    "Formatting rules:\n"
    "- Use LaTeX for all formulas: $...$ inline, $$...$$ for display equations.\n"
    "- Use **bold** for key terms in the question text.\n"
    "- Use numbered list entries in marking_scheme for multi-step answers.\n"
)


def _question_prompt(
    grade: int, subject: str, topic: str,
    bloom_level: str, difficulty: str, question_type: str, marks: int,
    distractor_note: str, rag_text: str,
) -> str:
    return (
        f"Generate a CBSE-style {question_type} question.\n"
        f"Grade: {grade}, Subject: {subject}\n"
        f"Topic: {topic}\n"
        f"Bloom's level: {bloom_level}\n"
        f"Difficulty: {difficulty}\n"
        f"Marks: {marks}\n"
        f"{distractor_note}\n\n"
        f"{_QUESTION_FORMATTING}\n"
        f"Base the question strictly on this NCERT source material:\n{rag_text}\n\n"
        f"Return a JSON object with these exact fields:\n"
        f"  question (string), bloom_level (string), marks (integer),\n"
        f"  answer (string), marking_scheme (array of strings), distractors (array of strings)."
    )


def generate_question(
    grade: int,
    subject: str,
    topic: str,
    bloom_level: str = "understand",
    difficulty: str = "medium",
    question_type: str = "MCQ",
    marks: int = 1,
) -> dict:
    """Pipeline B: Structured CBSE-style question grounded in NCERT content."""
    chunks = search_content(
        query=topic, grade=grade, subject=subject, bloom_level=bloom_level, top_k=3
    )

    if not chunks:
        return {"error": "No relevant content found. Run pipeline.py to populate the database."}

    rag_text = "\n\n---\n\n".join(c["text"] for c in chunks)
    distractor_note = (
        "Include exactly 3 plausible but incorrect distractors in the 'distractors' field."
        if question_type == "MCQ"
        else "Set 'distractors' to an empty array []."
    )

    prompt = _question_prompt(
        grade, subject, topic, bloom_level, difficulty, question_type, marks,
        distractor_note, rag_text,
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
        return json.loads(resp.text)
    except json.JSONDecodeError:
        return {"error": "Failed to parse structured output", "raw": resp.text}


# ── Mermaid diagram generation ────────────────────────────────────────────────

# Topics where a flow/process diagram genuinely helps comprehension.
# This heuristic avoids a Gemini call for definitions, formulas, and static facts.
_DIAGRAM_KEYWORDS = {
    "cycle", "process", "system", "chain", "flow", "stages", "steps",
    "pathway", "mechanism", "hierarchy", "classification", "circuit",
    "digestion", "photosynthesis", "respiration", "circulation", "ecosystem",
    "water cycle", "carbon cycle", "nitrogen cycle", "rock cycle",
    "food web", "food chain", "life cycle", "reproduction", "osmosis",
    "diffusion", "refraction", "reflection", "conduction", "convection",
    "mitosis", "meiosis", "excretion", "transpiration",
}


def _should_generate_diagram(topic: str, explanation: str) -> bool:
    """Return True only when the topic is likely to benefit from a visual diagram."""
    text = (topic + " " + explanation[:400]).lower()
    return any(kw in text for kw in _DIAGRAM_KEYWORDS)


def _generate_mermaid(topic: str, subject: str, grade: int, explanation: str) -> dict:
    """
    Generate a Mermaid flowchart for a topic already determined to be diagram-worthy.
    Call _should_generate_diagram() before this — don't call unconditionally.
    Returns {"diagram": str, "caption": str}.
    """
    prompt = (
        f"Generate a Mermaid flowchart to visually explain '{topic}' "
        f"for a Grade {grade} CBSE {subject} student.\n\n"
        f"Context (from explanation):\n{explanation[:400]}\n\n"
        f"Rules:\n"
        f"- Use flowchart TD or LR. Max 10 nodes. Labels: 3-5 words max.\n"
        f"- Node IDs: plain alphanumeric only (e.g. stepA, node1).\n"
        f"- Forbidden node IDs: graph, end, style, subgraph, classDef, linkStyle.\n"
        f"- Wrap every node label in double quotes.\n\n"
        f'Return JSON: {{"diagram": "complete mermaid code", "caption": "one-line caption"}}'
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
        if data.get("diagram"):
            return {"diagram": data["diagram"], "caption": data.get("caption", "")}
    except (json.JSONDecodeError, KeyError):
        pass
    return {"diagram": None, "caption": None}


# ── Streaming variants (used by REST API, not MCP) ────────────────────────────

def stream_explanation(
    grade: int,
    subject: str,
    topic: str,
    language: str = "en",
):
    """Generator: yields (text_chunk, meta) where meta is sent once at the end."""
    chunks = search_content(query=topic, grade=grade, subject=subject, top_k=4)

    if not chunks:
        yield ("No relevant content found. Run pipeline.py to populate the database.", None)
        return

    rag_text = "\n\n---\n\n".join(c["text"] for c in chunks)
    prompt = _explanation_prompt(grade, subject, topic, rag_text, language)
    source_chunks = [f"{c['source_file']}[{c['chunk_index']}]" for c in chunks]

    full_text = ""
    for chunk in _client.models.generate_content_stream(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=genai_types.GenerateContentConfig(temperature=0.4),
    ):
        if chunk.text:
            full_text += chunk.text
            yield (chunk.text, None)

    mermaid = (
        _generate_mermaid(topic, subject, grade, full_text)
        if _should_generate_diagram(topic, full_text)
        else {"diagram": None, "caption": None}
    )

    yield ("", {
        "source_chunks":   source_chunks,
        "model_used":      GEMINI_MODEL_FAST,
        "stage":           _stage_config(grade)["stage"],
        "mermaid_diagram": mermaid["diagram"],
        "mermaid_caption": mermaid["caption"],
    })


def stream_question(
    grade: int,
    subject: str,
    topic: str,
    bloom_level: str = "understand",
    difficulty: str = "medium",
    question_type: str = "MCQ",
    marks: int = 1,
):
    """Generator: yields raw JSON text chunks; final event carries parsed dict."""
    chunks = search_content(
        query=topic, grade=grade, subject=subject, bloom_level=bloom_level, top_k=3
    )

    if not chunks:
        yield ("", {"error": "No relevant content found. Run pipeline.py to populate the database."})
        return

    rag_text = "\n\n---\n\n".join(c["text"] for c in chunks)
    distractor_note = (
        "Include exactly 3 plausible but incorrect distractors in the 'distractors' field."
        if question_type == "MCQ"
        else "Set 'distractors' to an empty array []."
    )

    prompt = _question_prompt(
        grade, subject, topic, bloom_level, difficulty, question_type, marks,
        distractor_note, rag_text,
    )

    raw = ""
    for chunk in _client.models.generate_content_stream(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    ):
        if chunk.text:
            raw += chunk.text
            yield (chunk.text, None)

    try:
        yield ("", json.loads(raw))
    except json.JSONDecodeError:
        yield ("", {"error": "Failed to parse structured output", "raw": raw})
