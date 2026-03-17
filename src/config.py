import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not GOOGLE_API_KEY:
    raise EnvironmentError(
        "GOOGLE_API_KEY not set. Add it to .env or export it as an environment variable.\n"
        "Get a key at: https://aistudio.google.com/apikey"
    )

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT           = Path(__file__).parent.parent
DATA           = ROOT / "data"
NCERT_PDF_DIR  = DATA / "raw" / "ncert_pdfs"
PROCESSED      = DATA / "processed"
TEXT_CACHE_DIR = PROCESSED / "text_cache"

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_MODEL      = "gemini-2.5-pro"       # pipeline tagging + curriculum graph (one-time, quality matters)
GEMINI_MODEL_FAST = "gemini-2.0-flash"     # live user requests (35x cheaper, ~same quality for generation)
GEMINI_EMBED      = "gemini-embedding-001" # 3072-dim embeddings

# ── Pipeline ──────────────────────────────────────────────────────────────────
CHUNK_SIZE_TOKENS    = 512
CHUNK_OVERLAP_TOKENS = 64

# ── Supabase (Auth) ───────────────────────────────────────────────────────────
SUPABASE_URL              = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY         = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
