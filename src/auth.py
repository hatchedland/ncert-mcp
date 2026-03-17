"""
Auth middleware — validates Supabase JWTs on protected endpoints.

Uses an in-memory token cache (5-min TTL) to avoid a Supabase roundtrip
on every request while still catching revoked tokens within a reasonable window.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

security = HTTPBearer()

# ── Supabase client singleton ─────────────────────────────────────────────────

_supabase: Client | None = None


def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env"
            )
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _supabase


# ── Token cache (avoids hitting Supabase on every request) ───────────────────

_cache: dict[str, tuple[dict, float]] = {}  # token → (user_dict, expires_at)
_CACHE_TTL = 300  # 5 minutes


def _get_cached(token: str) -> dict | None:
    entry = _cache.get(token)
    if entry and time.time() < entry[1]:
        return entry[0]
    _cache.pop(token, None)
    return None


def _set_cached(token: str, user: dict) -> None:
    _cache[token] = (user, time.time() + _CACHE_TTL)


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Validate a Supabase JWT and return the authenticated user.
    FastAPI runs this in a thread pool (sync dependency).

    Returns: {"id": str, "email": str}
    """
    token = credentials.credentials

    cached = _get_cached(token)
    if cached:
        return cached

    try:
        supabase = _get_supabase()
        response = supabase.auth.get_user(token)
        if not response.user:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = {"id": response.user.id, "email": response.user.email}
        _set_cached(token, user)
        return user

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
