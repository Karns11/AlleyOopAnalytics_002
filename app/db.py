"""SQLite connection helpers and schema."""
import sqlite3
from pathlib import Path

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id            INTEGER PRIMARY KEY,
    abbreviation  TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL,
    city          TEXT,
    nickname      TEXT
);

CREATE TABLE IF NOT EXISTS players (
    id               INTEGER PRIMARY KEY,
    full_name        TEXT NOT NULL,
    full_name_lower  TEXT NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 0,
    team_id          INTEGER,
    team_abbr        TEXT
);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(full_name_lower);

CREATE TABLE IF NOT EXISTS player_game_logs (
    player_id     INTEGER NOT NULL,
    game_id       TEXT    NOT NULL,
    game_date     TEXT    NOT NULL,   -- ISO YYYY-MM-DD
    season        TEXT    NOT NULL,   -- e.g. 2025-26
    season_type   TEXT    NOT NULL,   -- Regular Season | Playoffs
    team_id       INTEGER,
    team_abbr     TEXT,
    opponent_abbr TEXT,
    is_home       INTEGER NOT NULL,
    wl            TEXT,
    min REAL, pts INTEGER, reb INTEGER, oreb INTEGER, dreb INTEGER, ast INTEGER,
    stl INTEGER, blk INTEGER, tov INTEGER, pf INTEGER,
    fgm INTEGER, fga INTEGER, fg_pct REAL,
    fg3m INTEGER, fg3a INTEGER, fg3_pct REAL,
    ftm INTEGER, fta INTEGER, ft_pct REAL,
    plus_minus INTEGER,
    PRIMARY KEY (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_logs_player_date ON player_game_logs(player_id, game_date);
CREATE INDEX IF NOT EXISTS idx_logs_player_season ON player_game_logs(player_id, season);
CREATE INDEX IF NOT EXISTS idx_logs_date ON player_game_logs(game_date);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    mode         TEXT NOT NULL,
    season       TEXT NOT NULL,
    season_type  TEXT NOT NULL,
    status       TEXT NOT NULL,   -- running | ok | failed
    rows         INTEGER DEFAULT 0,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Read-write connection (used by ingestion and schema setup)."""
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect_readonly(path: Path | str | None = None) -> sqlite3.Connection:
    """Read-only connection for request handlers."""
    path = Path(path or config.DB_PATH)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
