"""Ingest NBA player game logs from stats.nba.com (via nba_api) into SQLite.

Usage:
    python -m ingest.ingest --mode full                 # backfill all configured seasons
    python -m ingest.ingest --mode full --resume        # skip chunks fetched OK in the last 24h
    python -m ingest.ingest --mode incremental          # recent games of the current season
    python -m ingest.ingest --mode full --seasons 2025-26 --season-types "Regular Season" --limit-players 5

Uses the league-wide LeagueGameLog endpoint in player mode, so each
(season, season type) pair is a single HTTP call for every player.
"""
import argparse
import logging
import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

from app import config
from app.db import connect, init_db
from app.text import fold

log = logging.getLogger("ingest")

INCREMENTAL_LOOKBACK_DAYS = 3  # re-fetch a few days to pick up stat corrections
RESUME_WINDOW = timedelta(hours=24)

STAT_COLUMNS = {
    # db column: API column
    "min": "MIN", "pts": "PTS", "reb": "REB", "oreb": "OREB", "dreb": "DREB",
    "ast": "AST", "stl": "STL", "blk": "BLK", "tov": "TOV", "pf": "PF",
    "fgm": "FGM", "fga": "FGA", "fg_pct": "FG_PCT",
    "fg3m": "FG3M", "fg3a": "FG3A", "fg3_pct": "FG3_PCT",
    "ftm": "FTM", "fta": "FTA", "ft_pct": "FT_PCT",
    "plus_minus": "PLUS_MINUS",
}
LOG_COLUMNS = [
    "player_id", "game_id", "game_date", "season", "season_type", "team_id", "team_abbr",
    "opponent_abbr", "is_home", "wl", *STAT_COLUMNS,
]


class FetchError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- parsing

def parse_matchup(matchup: str) -> tuple[bool, str]:
    """'LAL vs. BOS' -> (True, 'BOS'); 'LAL @ BOS' -> (False, 'BOS')."""
    if " vs. " in matchup:
        return True, matchup.split(" vs. ", 1)[1].strip()
    if " @ " in matchup:
        return False, matchup.split(" @ ", 1)[1].strip()
    raise ValueError(f"Unrecognized MATCHUP: {matchup!r}")


def parse_minutes(value) -> float | None:
    """MIN can be numeric (e.g. 34 or 34.5) or 'MM:SS' depending on endpoint."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    if ":" in text:
        mins, secs = text.split(":", 1)
        return int(mins) + int(secs) / 60
    return float(text)


def parse_row(raw: dict, season: str, season_type: str) -> dict:
    is_home, opponent = parse_matchup(raw["MATCHUP"])
    game_date = str(raw["GAME_DATE"])[:10]
    date.fromisoformat(game_date)  # validate
    row = {
        "player_id": int(raw["PLAYER_ID"]),
        "game_id": str(raw["GAME_ID"]),
        "game_date": game_date,
        "season": season,
        "season_type": season_type,
        "team_id": raw.get("TEAM_ID"),
        "team_abbr": raw.get("TEAM_ABBREVIATION"),
        "opponent_abbr": opponent,
        "is_home": int(is_home),
        "wl": raw.get("WL"),
    }
    for col, api_col in STAT_COLUMNS.items():
        row[col] = raw.get(api_col)
    row["min"] = parse_minutes(row["min"])
    return row


# ---------------------------------------------------------------- fetching

def fetch_game_log(season: str, season_type: str, date_from: date | None = None) -> list[dict]:
    """Fetch league-wide player game logs with retries + exponential backoff."""
    from nba_api.stats.endpoints import LeagueGameLog  # imported lazily: heavy (pulls pandas)

    params = dict(
        player_or_team_abbreviation="P",
        season=season,
        season_type_all_star=season_type,
        date_from_nullable=date_from.strftime("%m/%d/%Y") if date_from else "",
        date_to_nullable="",
        timeout=config.NBA_TIMEOUT,
        proxy=config.NBA_PROXY,
    )
    last_error = None
    for attempt in range(1, config.NBA_RETRIES + 1):
        try:
            log.info("Fetching %s %s%s (attempt %d/%d)", season, season_type,
                     f" from {date_from}" if date_from else "", attempt, config.NBA_RETRIES)
            endpoint = LeagueGameLog(**params)
            result = endpoint.get_dict()["resultSets"][0]
            headers = result["headers"]
            return [dict(zip(headers, r)) for r in result["rowSet"]]
        except Exception as e:  # network errors, timeouts, bad JSON from throttling
            last_error = e
            if attempt < config.NBA_RETRIES:
                wait = 5 * 2 ** (attempt - 1) + random.uniform(0, 2)
                log.warning("Fetch failed (%s: %s); retrying in %.0fs", type(e).__name__, e, wait)
                time.sleep(wait)
    raise FetchError(f"{season} {season_type}: {type(last_error).__name__}: {last_error}")


# ---------------------------------------------------------------- storage

def seed_static(conn: sqlite3.Connection) -> None:
    """Teams and players from nba_api's bundled static data (no network)."""
    from nba_api.stats.static import players, teams

    conn.executemany(
        """INSERT INTO teams (id, abbreviation, full_name, city, nickname)
           VALUES (:id, :abbreviation, :full_name, :city, :nickname)
           ON CONFLICT(id) DO UPDATE SET abbreviation=excluded.abbreviation,
             full_name=excluded.full_name, city=excluded.city, nickname=excluded.nickname""",
        teams.get_teams(),
    )
    conn.executemany(
        """INSERT INTO players (id, full_name, full_name_lower, is_active)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name,
             full_name_lower=excluded.full_name_lower""",
        [(p["id"], p["full_name"], fold(p["full_name"]), int(p["is_active"])) for p in players.get_players()],
    )
    conn.commit()


def upsert_logs(conn: sqlite3.Connection, raw_rows: list[dict], season: str, season_type: str) -> int:
    rows, players = [], {}
    for raw in raw_rows:
        try:
            rows.append(parse_row(raw, season, season_type))
        except (ValueError, KeyError, TypeError) as e:
            log.warning("Skipping bad row %s: %s", raw.get("GAME_ID"), e)
            continue
        players[int(raw["PLAYER_ID"])] = raw.get("PLAYER_NAME") or ""

    placeholders = ", ".join(f":{c}" for c in LOG_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in LOG_COLUMNS if c not in ("player_id", "game_id"))
    with conn:  # single transaction per chunk
        conn.executemany(
            f"INSERT INTO player_game_logs ({', '.join(LOG_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(player_id, game_id) DO UPDATE SET {updates}",
            rows,
        )
        # Players missing from static data (e.g. new rookies)
        conn.executemany(
            "INSERT INTO players (id, full_name, full_name_lower, is_active) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(id) DO NOTHING",
            [(pid, name, fold(name)) for pid, name in players.items() if name],
        )
    return len(rows)


def refresh_player_teams(conn: sqlite3.Connection, current_season: str) -> None:
    """Set each player's team to the one from their most recent game; mark current-season players active."""
    with conn:
        conn.execute(
            """UPDATE players SET
                 team_id   = (SELECT team_id   FROM player_game_logs g WHERE g.player_id = players.id
                              ORDER BY game_date DESC LIMIT 1),
                 team_abbr = (SELECT team_abbr FROM player_game_logs g WHERE g.player_id = players.id
                              ORDER BY game_date DESC LIMIT 1)
               WHERE id IN (SELECT DISTINCT player_id FROM player_game_logs)"""
        )
        conn.execute(
            "UPDATE players SET is_active = 1 WHERE id IN "
            "(SELECT DISTINCT player_id FROM player_game_logs WHERE season = ?)",
            (current_season,),
        )


def recently_ok(conn: sqlite3.Connection, season: str, season_type: str) -> bool:
    row = conn.execute(
        "SELECT finished_at FROM ingest_runs WHERE season=? AND season_type=? AND status='ok' "
        "AND mode='full' ORDER BY id DESC LIMIT 1",
        (season, season_type),
    ).fetchone()
    if not row or not row["finished_at"]:
        return False
    return datetime.now(timezone.utc) - datetime.fromisoformat(row["finished_at"]) < RESUME_WINDOW


def run_chunk(conn, mode, season, season_type, date_from=None, limit_players=None) -> bool:
    run_id = conn.execute(
        "INSERT INTO ingest_runs (started_at, mode, season, season_type, status) VALUES (?, ?, ?, ?, 'running')",
        (now_iso(), mode, season, season_type),
    ).lastrowid
    conn.commit()
    try:
        raw = fetch_game_log(season, season_type, date_from)
        if limit_players:
            keep = sorted({r["PLAYER_ID"] for r in raw})[:limit_players]
            raw = [r for r in raw if r["PLAYER_ID"] in keep]
        n = upsert_logs(conn, raw, season, season_type)
        finished = now_iso()
        with conn:
            conn.execute("UPDATE ingest_runs SET status='ok', finished_at=?, rows=? WHERE id=?",
                         (finished, n, run_id))
            conn.execute("INSERT INTO meta (key, value) VALUES ('last_successful_ingest', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (finished,))
        log.info("OK %s %s: upserted %d rows", season, season_type, n)
        return True
    except Exception as e:
        log.error("FAILED %s %s: %s", season, season_type, e)
        with conn:
            conn.execute("UPDATE ingest_runs SET status='failed', finished_at=?, error=? WHERE id=?",
                         (now_iso(), str(e)[:500], run_id))
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    ap.add_argument("--seasons", help="Comma-separated, e.g. 2026-27,2025-26 (default from SEASONS env)")
    ap.add_argument("--season-types", help='Comma-separated (default "Regular Season,Playoffs")')
    ap.add_argument("--resume", action="store_true", help="full mode: skip chunks fetched OK in the last 24h")
    ap.add_argument("--limit-players", type=int, help="testing: keep only the first N players per chunk")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    seasons = [s.strip() for s in args.seasons.split(",")] if args.seasons else config.SEASONS
    season_types = [s.strip() for s in args.season_types.split(",")] if args.season_types else config.SEASON_TYPES

    conn = connect()
    init_db(conn)
    seed_static(conn)

    if args.mode == "incremental":
        seasons = seasons[:1]  # current season only

    results = []
    first = True
    for season in seasons:
        for season_type in season_types:
            date_from = None
            if args.mode == "full" and args.resume and recently_ok(conn, season, season_type):
                log.info("Skipping %s %s (fetched OK within %s)", season, season_type, RESUME_WINDOW)
                continue
            if args.mode == "incremental":
                row = conn.execute(
                    "SELECT MAX(game_date) AS d FROM player_game_logs WHERE season=? AND season_type=?",
                    (season, season_type),
                ).fetchone()
                if row["d"]:
                    date_from = date.fromisoformat(row["d"]) - timedelta(days=INCREMENTAL_LOOKBACK_DAYS)
            if not first:
                time.sleep(config.NBA_DELAY_SECONDS + random.uniform(0, 1))  # be polite
            first = False
            results.append(run_chunk(conn, args.mode, season, season_type, date_from, args.limit_players))

    refresh_player_teams(conn, config.SEASONS[0])
    total = conn.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0]
    conn.close()

    failed = results.count(False)
    log.info("Done: %d chunk(s) ok, %d failed; %d total game-log rows in DB",
             results.count(True), failed, total)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
