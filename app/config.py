"""Environment-driven settings. Loaded once at import time."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _csv(name: str, default: str) -> list[str]:
    return [s.strip() for s in os.getenv(name, default).split(",") if s.strip()]


DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "alleyoop.db"))
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# Seasons to ingest, newest first. First entry is treated as the "current" season.
SEASONS = _csv("SEASONS", "2026-27,2025-26,2024-25")
SEASON_TYPES = _csv("SEASON_TYPES", "Regular Season,Playoffs")

ALLOWED_ORIGINS = _csv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
RATE_LIMIT = os.getenv("RATE_LIMIT", "60/minute")
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "false").lower() == "true"

DEFAULT_LAST_N = int(os.getenv("DEFAULT_LAST_N", "5"))

# nba_api ingestion politeness
NBA_TIMEOUT = int(os.getenv("NBA_TIMEOUT", "60"))
NBA_RETRIES = int(os.getenv("NBA_RETRIES", "4"))
NBA_DELAY_SECONDS = float(os.getenv("NBA_DELAY_SECONDS", "4"))
NBA_PROXY = os.getenv("NBA_PROXY") or None
