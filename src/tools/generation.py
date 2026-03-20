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
            "max_words": 300,
            "depth": "hook → simple definition → one concrete example → fun fact or activity → remember box",
        }
    elif grade <= 8:
        return {
            "stage": "Middle",
            "tone": "clear and encouraging, activity-based thinking",
            "vocabulary": "introduce subject terms with simple explanations",
            "example_style": "relatable Indian contexts — markets, kitchen, cricket, seasons",
            "max_words": 500,
            "depth": "hook → definition → key idea → how it works → worked example → real-life connection → summary",
        }
    elif grade <= 10:
        return {
            "stage": "Secondary",
            "tone": "formal but accessible, exam-aware",
            "vocabulary": "precise CBSE terminology, ready for board exam answers",
            "example_style": "real-world applications and NCERT-style solved examples",
            "max_words": 700,
            "depth": "hook → definition → concept → derivation or proof if relevant → worked example → application → common mistake → exam tip",
        }
    else:
        return {
            "stage": "Higher Secondary",
            "tone": "analytical and rigorous, JEE/NEET/board aligned",
            "vocabulary": "full technical terminology with precise definitions",
            "example_style": "solved problems with step-by-step working, competitive exam style",
            "max_words": 900,
            "depth": "hook → formal definition → theory → derivation → solved numerical → case analysis → common mistakes → JEE/NEET relevance",
        }


def _explanation_prompt(grade: int, subject: str, topic: str, rag_text: str, language: str) -> str:
    stage = _stage_config(grade)
    lang_instruction = (
        "Respond in Hindi, using English for subject-specific terms where "
        "the Hindi equivalent is rarely used in Indian classrooms."
        if language == "hi"
        else "Respond in English."
    )

    # Grade-specific closing instruction
    if grade <= 8:
        closing = (
            "End with a **Remember** box: 2–3 bullet points the student must memorise."
        )
    else:
        closing = (
            "End with two sections:\n"
            "  - **Key Takeaway**: 1–2 sentences capturing the single most important idea.\n"
            "  - **Exam Tip**: one concrete tip on how this topic is tested in CBSE boards "
            "(common question types, marks weightage, or words examiners look for)."
        )

    return (
        f"You are an expert CBSE {subject} teacher for Grade {grade} "
        f"({stage['stage']} stage, NCF 2023).\n"
        f"Tone: {stage['tone']}.\n"
        f"Vocabulary: {stage['vocabulary']}.\n"
        f"Examples should be: {stage['example_style']}.\n"
        f"Strictly ground every fact in the source material below — never add facts not present in it.\n\n"
        f"Source material (NCERT):\n{rag_text}\n\n"
        f"Task: Explain '{topic}' for a Grade {grade} student.\n\n"
        f"Structure your response exactly as follows ({stage['depth']}). Max {stage['max_words']} words.\n\n"
        f"1. OPENING HOOK (1–2 sentences): Start with a surprising fact, a relatable scenario from "
        f"Indian daily life, or a question that makes the student curious. Do NOT start with the definition.\n\n"
        f"2. MAIN EXPLANATION: Follow this order — {stage['depth']}.\n"
        f"   - For any formula: name it, write it in LaTeX, then explain what each variable means "
        f"before substituting numbers.\n"
        f"   - For any process or sequence: use numbered steps.\n"
        f"   - For comparisons (e.g. plant vs animal cell, acids vs bases): use a Markdown table.\n\n"
        f"3. COMMON MISTAKE: In a > **Watch Out:** blockquote, flag the single most common "
        f"misconception or error students make about this topic. Be specific.\n\n"
        f"4. CLOSING: {closing}\n\n"
        f"Visual formatting rules (follow strictly — good formatting is part of teaching):\n"
        f"- Use ## for major section headings, ### for sub-sections.\n"
        f"- **Bold** every key term the first time it appears.\n"
        f"- Use numbered lists for steps/sequences/proofs; bullet lists for properties/types/features.\n"
        f"- Use Markdown tables for ALL comparisons (at least 2 rows + header). Examples: "
        f"plant cell vs animal cell, mitosis vs meiosis, acid vs base, series vs parallel circuit.\n"
        f"- Use > **Note:** blockquote for important NCERT callouts or definitions worth memorising.\n"
        f"- Use > **Example:** blockquote for every worked example. Show every arithmetic step — "
        f"never skip steps even if they seem obvious.\n"
        f"- Use > **Watch Out:** blockquote for the common misconception (section 3).\n"
        f"- Use horizontal rules (---) to visually separate major sections.\n"
        f"- Use LaTeX: $...$ for inline math, $$...$$ for display equations. "
        f"Never write formulas in plain text.\n"
        f"- For multi-step derivations, number each step and align equals signs using LaTeX align.\n"
        f"- For ALL chemical formulas and equations use mhchem notation inside LaTeX: "
        f"$\\ce{{H2O}}$ for compounds, $\\ce{{2H2 + O2 -> 2H2O}}$ for reactions, "
        f"$\\ce{{H2SO4(aq) -> 2H+(aq) + SO4^{{2-}}(aq)}}$ for ionic equations, "
        f"$\\ce{{A <=> B}}$ for equilibrium. Never write chemical formulas as plain text.\n"
        f"- State symbols are mandatory in all chemical equations: (s), (l), (g), (aq).\n"
        f"- End every worked example with a clearly marked final answer line: **∴ Answer: ...**\n"
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

    visuals = _build_visuals(topic, subject, grade, resp.text)

    return {
        "topic":         topic,
        "grade":         grade,
        "subject":       subject,
        "language":      language,
        "stage":         _stage_config(grade)["stage"],
        "explanation":   resp.text,
        "source_chunks": [f"{c['source_file']}[{c['chunk_index']}]" for c in chunks],
        "model_used":    GEMINI_MODEL_FAST,
        "visuals":       visuals,
    }


_QUESTION_FORMATTING = (
    "Formatting rules:\n"
    "- Use LaTeX for all math: $...$ inline, $$...$$ for display equations.\n"
    "- For ALL chemical formulas and equations use mhchem: $\\ce{H2O}$, "
    "$\\ce{2H2 + O2 -> 2H2O}$, $\\ce{A <=> B}$ for equilibrium. "
    "Include state symbols: (s), (l), (g), (aq).\n"
    "- Use **bold** for key terms in the question text.\n"
    "- Use numbered list entries in marking_scheme for multi-step answers.\n"
)


_BLOOM_GUIDANCE = {
    "remember":  "Test direct recall — definition, name, list, or state. The answer should be found verbatim or near-verbatim in the source.",
    "understand": "Test comprehension — explain in own words, give an example, or distinguish between two related concepts.",
    "apply":     "Give a novel scenario or numerical problem; ask the student to apply the concept to solve it.",
    "analyse":   "Ask the student to break down a process, compare two things, or identify cause and effect.",
    "evaluate":  "Ask the student to justify, argue for/against, or assess the validity of a claim.",
    "create":    "Ask the student to design, propose, or construct something using the concept.",
}

_DIFFICULTY_GUIDANCE = {
    "easy":   "Straightforward — one idea, no multi-step reasoning. Any attentive student should get this right.",
    "medium": "Requires understanding, not just recall. May involve one inferential step or application.",
    "hard":   "Multi-step, application-heavy, or tests a commonly confused distinction. Discriminates top students.",
}


def _question_prompt(
    grade: int, subject: str, topic: str,
    bloom_level: str, difficulty: str, question_type: str, marks: int,
    distractor_note: str, rag_text: str,
) -> str:
    bloom_note = _BLOOM_GUIDANCE.get(bloom_level.lower(), "")
    diff_note = _DIFFICULTY_GUIDANCE.get(difficulty.lower(), "")
    marks_guidance = (
        f"The answer should be answerable in {marks} × 1-mark point(s). "
        f"The marking_scheme must have exactly {marks} distinct award-worthy point(s)."
    )
    distractor_guidance = (
        "Each distractor must represent a real student misconception or a plausible but wrong "
        "interpretation — not an obviously absurd option. A student who half-understands the topic "
        "should be tempted by at least one distractor."
        if question_type == "MCQ"
        else ""
    )

    return (
        f"Generate a CBSE-style {question_type} question for Grade {grade} {subject}.\n\n"
        f"Topic: {topic}\n"
        f"Bloom's level: {bloom_level} — {bloom_note}\n"
        f"Difficulty: {difficulty} — {diff_note}\n"
        f"Marks: {marks}. {marks_guidance}\n\n"
        f"{distractor_note}\n"
        f"{distractor_guidance}\n\n"
        f"Marking scheme guidance: Each entry in marking_scheme must be a keyword or key phrase "
        f"an examiner would look for in the student's answer (e.g. 'names mitochondria', "
        f"'states ATP is produced', 'gives correct unit: Joules'). Not full sentences.\n\n"
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


# ── Visual explanation engine ─────────────────────────────────────────────────
#
# Two complementary visual types are generated independently and combined:
#
#   concept_map  — knowledge graph: concepts as nodes, named relationships as
#                  edges (requires / produces / is_a / has_part / causes / etc.)
#                  Rendered as Mermaid `graph LR` + structured nodes/edges JSON
#                  so frontends can use richer renderers (Cytoscape, React Flow).
#
#   process_flow — step-by-step or cyclic sequence with parallel stages, decision
#                  points, and subgraph groupings. Rendered as Mermaid `flowchart TD`.
#
# A topic may warrant both (photosynthesis: concept map of inputs/outputs AND
# process flow of light reactions → Calvin cycle).

# Topics where concept relationships matter — concept map is useful
_CONCEPT_MAP_TOPICS: set[str] = {
    # Biology
    "cell", "organelle", "mitochondria", "chloroplast", "nucleus", "ribosome",
    "tissue", "organ", "organ system", "ecosystem", "food web", "food chain",
    "biodiversity", "evolution", "natural selection", "genetics", "heredity",
    "hormone", "enzyme", "protein", "dna", "rna", "gene",
    "plant kingdom", "animal kingdom", "five kingdom", "taxonomy", "classification",
    # Chemistry
    "periodic table", "chemical bonding", "ionic bond", "covalent bond",
    "acid", "base", "salt", "oxidation", "reduction", "redox",
    "polymer", "monomer", "functional group", "organic chemistry",
    "element", "compound", "mixture", "solution",
    # Physics
    "force", "motion", "newton", "energy", "work", "power",
    "wave", "sound", "light", "electricity", "magnetism",
    "atom", "nucleus", "electron", "proton", "neutron",
    # SST / Civics
    "democracy", "constitution", "federalism", "government", "parliament",
    "fundamental rights", "directive principles", "judiciary", "legislature",
    "nationalism", "colonialism", "globalisation", "development",
    # Economics
    "money", "market", "demand", "supply", "gdp", "inflation",
    "sector", "poverty", "employment", "credit",
    # Geography
    "climate", "weather", "soil", "agriculture", "industry", "resource",
    "landform", "river", "mountain", "plateau", "plain",
}

# Topics where sequence / process matters — process flow is useful
_PROCESS_FLOW_TOPICS: set[str] = {
    # Biology
    "photosynthesis", "respiration", "digestion", "excretion", "transpiration",
    "circulation", "blood clotting", "urine formation", "nerve impulse", "reflex arc",
    "mitosis", "meiosis", "fertilisation", "germination", "reproduction",
    "nitrogen cycle", "carbon cycle", "water cycle", "krebs cycle", "calvin cycle",
    "nitrogen fixation", "carbon fixation", "immune response", "vaccination",
    # Chemistry
    "electrolysis", "galvanic cell", "electrochemical", "reaction mechanism",
    "manufacture of", "industrial process", "extraction of",
    # Physics
    "refraction", "reflection", "total internal reflection",
    "conduction", "convection", "radiation", "nuclear fission", "nuclear fusion",
    # Earth science
    "rock cycle", "rock formation", "weathering", "soil formation",
    "seed dispersal", "pollination",
    # History (sequence of events)
    "revolt", "revolution", "independence", "partition",
    "freedom movement", "industrial revolution",
    # Processes described by "cycle", "stages", "steps", "pathway", "mechanism"
    "cycle", "stages", "steps", "pathway", "mechanism", "process", "flow",
}


def _classify_visuals(topic: str, explanation: str) -> set[str]:
    """
    Return the set of visual types to generate for this topic.
    Possible values: "concept_map", "process_flow" — can be both or neither.
    """
    text = (topic + " " + explanation[:500]).lower()
    result: set[str] = set()
    if any(kw in text for kw in _CONCEPT_MAP_TOPICS):
        result.add("concept_map")
    if any(kw in text for kw in _PROCESS_FLOW_TOPICS):
        result.add("process_flow")
    return result


# ── Concept map ───────────────────────────────────────────────────────────────

# Named relationship types used as edge labels in concept maps.
# Keeping these consistent helps frontends colour-code or filter edges.
_EDGE_TYPES = (
    "requires", "produces", "is_a", "has_part", "causes", "inhibits",
    "occurs_in", "is_type_of", "converts_to", "regulates", "interacts_with",
)


def _generate_concept_map(topic: str, subject: str, grade: int, explanation: str) -> dict:
    """
    Generate a concept map: Mermaid `graph LR` with labelled edges + structured
    nodes/edges JSON for frontend renderers.

    Returns:
        {
            "mermaid":  str,          # Mermaid graph LR source
            "nodes":    list[dict],   # [{id, label, type}]
            "edges":    list[dict],   # [{from, to, label, type}]
            "caption":  str,
        }
    """
    edge_types_hint = ", ".join(_EDGE_TYPES)
    prompt = (
        f"Generate a concept map for '{topic}' (Grade {grade} CBSE {subject}).\n\n"
        f"Context:\n{explanation[:900]}\n\n"
        f"A concept map shows KEY CONCEPTS as nodes and NAMED RELATIONSHIPS as labelled edges.\n"
        f"It answers: how are these ideas connected?\n\n"
        f"Rules:\n"
        f"- 8–16 nodes. Each node = one concept, organelle, force, term, or idea.\n"
        f"- Every edge must have a relationship label chosen from: {edge_types_hint}.\n"
        f"- Node types: 'primary' (the main topic), 'concept', 'input', 'output', 'example'.\n"
        f"- The primary topic node must be present and connected to at least 4 others.\n"
        f"- Only include concepts grounded in the context above — no hallucination.\n\n"
        f"Return JSON with:\n"
        f"  nodes: array of {{id (alphanumeric), label (2–4 words), type}}\n"
        f"  edges: array of {{from (node id), to (node id), label (relationship), type (edge type)}}\n"
        f"  caption: one sentence describing what this map shows\n\n"
        f"Also generate the Mermaid source in the 'mermaid' field using this format:\n"
        f"  graph LR\n"
        f"    nodeId[\"Label\"] -->|\"relationship\"| nodeId2[\"Label2\"]\n"
        f"Node IDs: alphanumeric only. Wrap all labels in double quotes. Max 16 nodes.\n"
        f"Forbidden IDs: graph, end, style, subgraph, classDef, linkStyle."
    )

    resp = _client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )

    try:
        data = json.loads(resp.text)
        if data.get("mermaid") and data.get("nodes"):
            return {
                "mermaid": data["mermaid"],
                "nodes":   data.get("nodes", []),
                "edges":   data.get("edges", []),
                "caption": data.get("caption", ""),
            }
    except (json.JSONDecodeError, KeyError):
        pass
    return {"mermaid": None, "nodes": [], "edges": [], "caption": None}


# ── Process flow ──────────────────────────────────────────────────────────────

def _generate_process_flow(topic: str, subject: str, grade: int, explanation: str) -> dict:
    """
    Generate an enriched process flow: Mermaid `flowchart TD` with subgraphs for
    parallel stages, decision diamonds, and styled nodes + structured steps JSON.

    Returns:
        {
            "mermaid":  str,          # Mermaid flowchart TD source
            "steps":    list[dict],   # [{id, label, type, substeps, parallel_group}]
            "caption":  str,
        }
    """
    prompt = (
        f"Generate a detailed process flow diagram for '{topic}' (Grade {grade} CBSE {subject}).\n\n"
        f"Context:\n{explanation[:900]}\n\n"
        f"A process flow shows HOW something happens step by step, including:\n"
        f"- Parallel stages that happen simultaneously (use subgraph blocks)\n"
        f"- Decision points (use diamond nodes: {{\"condition?\"}})\n"
        f"- Inputs and outputs (distinguish them visually)\n"
        f"- Cyclic flows (loop arrows back to an earlier step if it's a cycle)\n\n"
        f"Rules:\n"
        f"- Use `flowchart TD` (top-down).\n"
        f"- Use subgraph blocks to group parallel or related stages "
        f"(e.g. subgraph LightReactions[\"Light Reactions\"]).\n"
        f"- Node shapes: rectangles for steps, diamonds {{...}} for decisions, "
        f"stadium shapes ([...]) for start/end, parallelograms [/input/] for inputs.\n"
        f"- Node IDs: alphanumeric only. Wrap labels in double quotes.\n"
        f"- Forbidden IDs: graph, end, style, classDef, linkStyle.\n"
        f"- Max 18 nodes. Labels: 3–7 words. Be specific to the NCERT content.\n"
        f"- Only include steps grounded in the context above — no hallucination.\n\n"
        f"Return JSON with:\n"
        f"  mermaid: complete Mermaid flowchart TD source\n"
        f"  steps: array of {{id, label, type ('step'|'decision'|'input'|'output'|'start'|'end'), "
        f"parallel_group (string or null)}}\n"
        f"  caption: one sentence describing what this flow shows"
    )

    resp = _client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )

    try:
        data = json.loads(resp.text)
        if data.get("mermaid") and data.get("steps"):
            return {
                "mermaid": data["mermaid"],
                "steps":   data.get("steps", []),
                "caption": data.get("caption", ""),
            }
    except (json.JSONDecodeError, KeyError):
        pass
    return {"mermaid": None, "steps": [], "caption": None}


def _build_visuals(topic: str, subject: str, grade: int, explanation: str) -> dict:
    """
    Classify the topic and run whichever visual generators apply.
    Returns a `visuals` dict ready to embed in the API response.
    """
    needed = _classify_visuals(topic, explanation)

    concept_map  = _generate_concept_map(topic, subject, grade, explanation)  if "concept_map"  in needed else None
    process_flow = _generate_process_flow(topic, subject, grade, explanation) if "process_flow" in needed else None

    return {
        "concept_map":  concept_map,
        "process_flow": process_flow,
    }


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

    visuals = _build_visuals(topic, subject, grade, full_text)

    yield ("", {
        "source_chunks": source_chunks,
        "model_used":    GEMINI_MODEL_FAST,
        "stage":         _stage_config(grade)["stage"],
        "visuals":       visuals,
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
