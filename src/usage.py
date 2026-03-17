"""
Per-user daily rate limiting — tracked in SQLite user_usage table.

Free tier limits (resets at midnight UTC):
  explain         20 / day
  question        30 / day
  question_paper   5 / day
  search         100 / day
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import HTTPException, status

from db import get_db

DAILY_LIMITS: dict[str, int] = {
    "explain":         20,
    "question":        30,
    "question_paper":   5,
    "search":         100,
}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_and_increment(user_id: str, operation: str) -> None:
    """
    Atomically check the daily limit and increment the counter.
    Raises HTTP 429 if the user has hit their limit for the day.
    """
    limit = DAILY_LIMITS.get(operation, 100)
    today = _today()

    with get_db() as conn:
        row = conn.execute(
            "SELECT count FROM user_usage WHERE user_id = ? AND date = ? AND operation = ?",
            (user_id, today, operation),
        ).fetchone()

        current = row["count"] if row else 0

        if current >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error":     "Daily limit reached",
                    "operation": operation,
                    "limit":     limit,
                    "used":      current,
                    "resets":    f"{today}T23:59:59Z",
                },
            )

        conn.execute(
            """
            INSERT INTO user_usage (user_id, date, operation, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, date, operation) DO UPDATE SET count = count + 1
            """,
            (user_id, today, operation),
        )


def get_usage_summary(user_id: str) -> dict:
    """Return today's usage counts and limits for a user."""
    today = _today()

    with get_db() as conn:
        rows = conn.execute(
            "SELECT operation, count FROM user_usage WHERE user_id = ? AND date = ?",
            (user_id, today),
        ).fetchall()

    used = {row["operation"]: row["count"] for row in rows}

    return {
        "date": today,
        "usage": {
            op: {
                "used":      used.get(op, 0),
                "limit":     limit,
                "remaining": max(0, limit - used.get(op, 0)),
            }
            for op, limit in DAILY_LIMITS.items()
        },
    }
