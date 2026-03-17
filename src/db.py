"""
SQLite + Qdrant initialisation for Phase 2 pipeline.

Local dev:
  - SQLite at data/content.db  (same schema as target PostgreSQL)
  - Qdrant at data/qdrant/     (embedded local mode, no Docker)
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import DATA

DB_PATH = DATA / "content.db"
QDRANT_PATH = DATA / "qdrant"
COLLECTION_NAME = "content_chunks"
VECTOR_DIM = 3072  # gemini-embedding-001


def init_db() -> None:
    """Create SQLite tables and Qdrant collection if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS content_chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            grade       INTEGER NOT NULL,
            subject     TEXT NOT NULL,
            chapter     INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text        TEXT NOT NULL,
            bloom_level TEXT,
            topic       TEXT,
            difficulty  TEXT DEFAULT 'medium',
            qdrant_id   TEXT,
            UNIQUE(source_file, chunk_index)
        );

        CREATE TABLE IF NOT EXISTS content_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            book_code   TEXT NOT NULL,
            grade       INTEGER NOT NULL,
            subject     TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            title       TEXT,
            UNIQUE(book_code, chapter_num)
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_grade_subject
            ON content_chunks(grade, subject);
        CREATE INDEX IF NOT EXISTS idx_chunks_bloom
            ON content_chunks(bloom_level);
        CREATE INDEX IF NOT EXISTS idx_chunks_topic
            ON content_chunks(topic);

        CREATE TABLE IF NOT EXISTS curriculum_edges (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            pre_grade    INTEGER NOT NULL,
            pre_subject  TEXT NOT NULL,
            pre_chapter  INTEGER NOT NULL,
            pre_topic    TEXT NOT NULL,
            post_grade   INTEGER NOT NULL,
            post_subject TEXT NOT NULL,
            post_chapter INTEGER NOT NULL,
            post_topic   TEXT NOT NULL,
            confidence   REAL NOT NULL DEFAULT 1.0,
            rationale    TEXT,
            UNIQUE(pre_grade, pre_subject, pre_topic, post_grade, post_subject, post_topic)
        );

        CREATE INDEX IF NOT EXISTS idx_edges_post
            ON curriculum_edges(post_topic, post_grade, post_subject);
        CREATE INDEX IF NOT EXISTS idx_edges_pre
            ON curriculum_edges(pre_topic, pre_grade, pre_subject);

        CREATE TABLE IF NOT EXISTS question_bank (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            grade          INTEGER NOT NULL,
            subject        TEXT NOT NULL,
            chapter        INTEGER,
            topic          TEXT,
            question_type  TEXT NOT NULL,   -- MCQ | SAQ | LAQ | case_study
            bloom_level    TEXT NOT NULL,
            difficulty     TEXT NOT NULL,   -- easy | medium | hard
            marks          INTEGER NOT NULL,
            question       TEXT NOT NULL,
            answer         TEXT NOT NULL,
            marking_scheme TEXT,            -- JSON array of strings
            distractors    TEXT,            -- JSON array of strings (MCQ only)
            case_passage   TEXT,            -- for case_study questions
            source_chunks  TEXT,            -- JSON array
            created_at     TEXT DEFAULT (datetime('now')),
            times_used     INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_qb_grade_subject
            ON question_bank(grade, subject);
        CREATE INDEX IF NOT EXISTS idx_qb_bloom_diff
            ON question_bank(bloom_level, difficulty);
        CREATE INDEX IF NOT EXISTS idx_qb_chapter
            ON question_bank(grade, subject, chapter);

        CREATE TABLE IF NOT EXISTS user_usage (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            date      TEXT NOT NULL,   -- YYYY-MM-DD
            operation TEXT NOT NULL,   -- explain | question | question_paper | search
            count     INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, date, operation)
        );

        CREATE INDEX IF NOT EXISTS idx_usage_user_date
            ON user_usage(user_id, date);
        """)

    # Qdrant collection
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams

    QDRANT_PATH.mkdir(parents=True, exist_ok=True)
    qclient = QdrantClient(path=str(QDRANT_PATH))
    existing = {c.name for c in qclient.get_collections().collections}
    if COLLECTION_NAME not in existing:
        qclient.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
    qclient.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_qdrant():
    from qdrant_client import QdrantClient
    return QdrantClient(path=str(QDRANT_PATH))
