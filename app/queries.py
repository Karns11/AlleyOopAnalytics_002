"""SQL access layer. All queries are parameterized."""
import sqlite3
from datetime import date

from app.stats import mark_back_to_backs
from app.text import fold

GAME_FIELDS = """game_id, game_date, season, season_type, team_abbr, opponent_abbr, is_home, wl,
    min, pts, reb, oreb, dreb, ast, stl, blk, tov, pf, fgm, fga, fg_pct, fg3m, fg3a, fg3_pct,
    ftm, fta, ft_pct, plus_minus"""


def search_players(conn: sqlite3.Connection, q: str, limit: int = 10) -> list[dict]:
    needle = fold(q.strip())
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = conn.execute(
        """SELECT p.id, p.full_name, p.team_abbr, p.is_active,
                  EXISTS(SELECT 1 FROM player_game_logs g WHERE g.player_id = p.id) AS has_games
           FROM players p
           WHERE p.full_name_lower LIKE ? ESCAPE '\\'
           ORDER BY has_games DESC,
                    (p.full_name_lower LIKE ? ESCAPE '\\') DESC,   -- prefix matches first
                    p.is_active DESC, p.full_name
           LIMIT ?""",
        (f"%{escaped}%", f"{escaped}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_player(conn: sqlite3.Connection, player_id: int) -> dict | None:
    row = conn.execute(
        """SELECT p.id, p.full_name, p.team_abbr, p.is_active, t.full_name AS team_name
           FROM players p LEFT JOIN teams t ON t.id = p.team_id WHERE p.id = ?""",
        (player_id,),
    ).fetchone()
    return dict(row) if row else None


def all_games(conn: sqlite3.Connection, player_id: int) -> list[dict]:
    """Every ingested game for the player, oldest first, annotated with b2b flags.

    A player has at most a few hundred rows across the ingested seasons, so filtering
    in Python after this single indexed query is cheap and keeps b2b detection correct
    at range boundaries.
    """
    rows = conn.execute(
        f"SELECT {GAME_FIELDS} FROM player_game_logs WHERE player_id = ? ORDER BY game_date, game_id",
        (player_id,),
    ).fetchall()
    games = [dict(r) for r in rows]
    mark_back_to_backs(games)
    return games


def filter_games(games: list[dict], season: str | None = None, start: date | None = None,
                 end: date | None = None, opponent: str | None = None) -> list[dict]:
    s, e = (start.isoformat() if start else None), (end.isoformat() if end else None)
    return [
        g for g in games
        if (season is None or g["season"] == season)
        and (s is None or g["game_date"] >= s)
        and (e is None or g["game_date"] <= e)
        and (opponent is None or g["opponent_abbr"] == opponent)
    ]


def available_seasons(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT season FROM player_game_logs ORDER BY season DESC")]


def last_updated(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'last_successful_ingest'").fetchone()
    return row[0] if row else None


def team_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT abbreviation, full_name FROM teams")}
