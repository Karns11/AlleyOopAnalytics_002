"""Alley Oop Analytics web app: JSON API + static single-page frontend."""
import sqlite3
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app import config, queries, stats
from app.db import connect_readonly

STATIC_DIR = config.BASE_DIR / "app" / "static"


def client_ip(request: Request) -> str:
    # Behind Cloudflare Tunnel every request arrives from cloudflared on localhost;
    # Cloudflare supplies the real visitor IP in CF-Connecting-IP.
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")


limiter = Limiter(key_func=client_ip)

app = FastAPI(
    title="Alley Oop Analytics",
    debug=False,
    docs_url="/api/docs" if config.ENABLE_DOCS else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if config.ENABLE_DOCS else None,
)
app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=[],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limited(request: Request, exc: RateLimitExceeded):
    return JSONResponse({"detail": "Too many requests, slow down."}, status_code=429)


CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "public, max-age=60"
    return response


def get_db():
    if not config.DB_PATH.exists():
        raise HTTPException(503, "Database not initialized yet. Run the ingestion script.")
    conn = connect_readonly()
    try:
        yield conn
    finally:
        conn.close()


DB = Annotated[sqlite3.Connection, Depends(get_db)]
PlayerId = Annotated[int, Path(gt=0, le=2_000_000_000)]
Season = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")]
Start = Annotated[date | None, Query()]
End = Annotated[date | None, Query()]
Opponent = Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")]
LastN = Annotated[int, Query(ge=1, le=82)]


def check_range(start: date | None, end: date | None) -> None:
    if start and end and start > end:
        raise HTTPException(422, "start must be on or before end")


def load_player(conn, player_id: int) -> dict:
    player = queries.get_player(conn, player_id)
    if not player:
        raise HTTPException(404, "player not found")
    return player


def baseline_season(games_in_range: list[dict], all_games: list[dict], season: str | None) -> str | None:
    if season:
        return season
    if games_in_range:
        return games_in_range[-1]["season"]
    return all_games[-1]["season"] if all_games else None


def season_baseline_games(all_games: list[dict], season: str | None) -> list[dict]:
    """Regular-season games of the baseline season (falls back to all games if none)."""
    in_season = queries.filter_games(all_games, season)
    regular = [g for g in in_season if g["season_type"] == "Regular Season"]
    return regular or in_season


def with_colors(avg: dict | None, baseline: dict | None) -> dict | None:
    if avg is None:
        return None
    return {**avg, "colors": stats.color_row(avg, baseline or {})}


# ---------------------------------------------------------------- routes

@app.get("/api/meta")
@limiter.limit(config.RATE_LIMIT)
def meta(request: Request, conn: DB):
    return {
        "seasons": queries.available_seasons(conn),
        "last_updated": queries.last_updated(conn),
        "default_last_n": config.DEFAULT_LAST_N,
        "thresholds": {
            "pct": stats.THRESHOLD_PCT,
            "pct_points": stats.THRESHOLD_PCT_POINTS,
            "lower_is_better": sorted(stats.LOWER_IS_BETTER),
        },
        "teams": queries.team_names(conn),
    }


@app.get("/api/players/search")
@limiter.limit("120/minute")
def search(request: Request, conn: DB,
           q: Annotated[str, Query(min_length=2, max_length=40, pattern=r"^[\w .'\-]+$")]):
    return {"results": queries.search_players(conn, q)}


@app.get("/api/players/{player_id}/games")
@limiter.limit(config.RATE_LIMIT)
def player_games(request: Request, conn: DB, player_id: PlayerId,
                 season: Season = None, start: Start = None, end: End = None):
    check_range(start, end)
    player = load_player(conn, player_id)
    every = queries.all_games(conn, player_id)
    games = queries.filter_games(every, season, start, end)
    base_season = baseline_season(games, every, season)
    baseline = stats.averages(season_baseline_games(every, base_season)) or {}
    rows = [{**g, **stats.game_advanced(g), "colors": stats.color_row(g, baseline)} for g in games]
    return {"player": player, "baseline_season": base_season, "games": rows}


@app.get("/api/players/{player_id}/summary")
@limiter.limit(config.RATE_LIMIT)
def player_summary(request: Request, conn: DB, player_id: PlayerId,
                   season: Season = None, start: Start = None, end: End = None,
                   last_n: LastN = config.DEFAULT_LAST_N, opponent: Opponent = None):
    check_range(start, end)
    player = load_player(conn, player_id)
    every = queries.all_games(conn, player_id)
    games = queries.filter_games(every, season, start, end)
    base_season = baseline_season(games, every, season)
    season_games = season_baseline_games(every, base_season)
    season_avg = stats.averages(season_games)

    def split(subset):
        return with_colors(stats.averages(subset), season_avg)

    return {
        "player": player,
        "baseline_season": base_season,
        "season_avg": with_colors(season_avg, season_avg),
        "season_advanced": stats.advanced_summary(season_games),
        "splits": {
            "overall": split(games),
            "last_n": split(games[-last_n:]),
            "home": split([g for g in games if g["is_home"]]),
            "away": split([g for g in games if not g["is_home"]]),
            "vs_opponent": split([g for g in games if g["opponent_abbr"] == opponent]) if opponent else None,
            "wins": split([g for g in games if g["wl"] == "W"]),
            "losses": split([g for g in games if g["wl"] == "L"]),
            "b2b": split([g for g in games if g["b2b"]]),
            "rested": split([g for g in games if not g["b2b"]]),
        },
        "last_n": last_n,
        "opponent": opponent,
        "advanced": stats.advanced_summary(games),
        "highlights": stats.highlights(games, queries.filter_games(every, base_season), every),
        "streak": stats.streak(season_games, season_avg),
    }


@app.get("/api/players/{player_id}/opponents")
@limiter.limit(config.RATE_LIMIT)
def player_opponents(request: Request, conn: DB, player_id: PlayerId,
                     season: Season = None, start: Start = None, end: End = None):
    check_range(start, end)
    load_player(conn, player_id)
    games = queries.filter_games(queries.all_games(conn, player_id), season, start, end)
    names = queries.team_names(conn)
    counts: dict[str, int] = {}
    for g in games:
        counts[g["opponent_abbr"]] = counts.get(g["opponent_abbr"], 0) + 1
    return {"opponents": [{"abbr": a, "name": names.get(a, a), "games": n} for a, n in sorted(counts.items())]}


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness probe for Docker; not rate limited and does not touch the DB."""
    return {"ok": True}


# ---------------------------------------------------------------- frontend

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
