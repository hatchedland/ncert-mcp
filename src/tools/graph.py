"""
Phase 3 MCP tools — Curriculum graph queries.

Tools:
  get_prerequisites  — direct prerequisite topics for a given topic
  get_learning_path  — full ordered prerequisite chain (roots first, BFS)

Requires curriculum_graph.py to have been run first to populate curriculum_edges.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db


def get_prerequisites(topic: str, grade: int, subject: str) -> dict:
    """Return the direct prerequisite topics a student must master before this topic."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT pre_grade, pre_subject, pre_chapter, pre_topic, confidence, rationale
            FROM curriculum_edges
            WHERE post_topic = ? AND post_grade = ? AND post_subject = ?
            ORDER BY confidence DESC, pre_grade ASC
        """, (topic, grade, subject)).fetchall()

    return {
        "topic":         topic,
        "grade":         grade,
        "subject":       subject,
        "prerequisites": [
            {
                "grade":      r["pre_grade"],
                "subject":    r["pre_subject"],
                "chapter":    r["pre_chapter"],
                "topic":      r["pre_topic"],
                "confidence": round(r["confidence"], 2),
                "rationale":  r["rationale"],
            }
            for r in rows
        ],
    }


def get_learning_path(topic: str, grade: int, subject: str) -> dict:
    """
    Return the full ordered learning path to reach a target topic.

    Performs a BFS backwards through prerequisite edges to collect all
    ancestor topics, then returns them ordered from most foundational to
    most advanced (roots first).
    """
    visited: set[tuple] = set()
    # Each entry: the node info + which topic it directly unlocks
    path_nodes: list[dict] = []

    queue: list[tuple[str, int, str]] = [(topic, grade, subject)]

    while queue:
        curr_topic, curr_grade, curr_subj = queue.pop(0)
        key = (curr_topic, curr_grade, curr_subj)
        if key in visited:
            continue
        visited.add(key)

        with get_db() as conn:
            prereqs = conn.execute("""
                SELECT pre_grade, pre_subject, pre_chapter, pre_topic,
                       confidence, rationale
                FROM curriculum_edges
                WHERE post_topic = ? AND post_grade = ? AND post_subject = ?
                ORDER BY confidence DESC, pre_grade ASC
            """, (curr_topic, curr_grade, curr_subj)).fetchall()

        for r in prereqs:
            pre_key = (r["pre_topic"], r["pre_grade"], r["pre_subject"])
            if pre_key not in visited:
                queue.append((r["pre_topic"], r["pre_grade"], r["pre_subject"]))
                path_nodes.append({
                    "grade":      r["pre_grade"],
                    "subject":    r["pre_subject"],
                    "chapter":    r["pre_chapter"],
                    "topic":      r["pre_topic"],
                    "unlocks":    curr_topic,
                    "confidence": round(r["confidence"], 2),
                    "rationale":  r["rationale"],
                })

    # BFS collected nodes closest-first; reverse so roots (most foundational) come first
    path_nodes.reverse()

    return {
        "target_topic":   topic,
        "target_grade":   grade,
        "target_subject": subject,
        "path_length":    len(path_nodes),
        "learning_path":  path_nodes,
        "note": (
            "Empty path means no prerequisite edges found. "
            "Run curriculum_graph.py to build the graph."
            if not path_nodes else ""
        ),
    }
