# Alley Oop Analytics

NBA player stats explorer: FastAPI + SQLite backend, vanilla JS + Chart.js frontend, data from `nba_api`. It runs on a Raspberry Pi behind a Cloudflare Tunnel, so keep it lightweight.

## Commands

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m ingest.ingest --mode full          # backfill SEASONS (default 2026-27,2025-26,2024-25)
.venv/bin/python -m ingest.ingest --mode incremental   # nightly
DB_PATH=data/test.db .venv/bin/python -m ingest.ingest --mode full --seasons 2025-26 --season-types "Regular Season" --limit-players 5
.venv/bin/uvicorn app.main:app --port 8000 --reload    # dev server -> http://localhost:8000
.venv/bin/pytest -q
docker compose up -d --build                           # containerized: web + ingest (scheduler) sharing a volume
```

## Layout

- `app/config.py` holds env settings (`.env`, see `.env.example`)
- `app/db.py` holds the schema DDL and the `connect()` (read-write, for ingest) and `connect_readonly()` (requests) helpers
- `app/stats.py` contains the pure aggregation, advanced metrics, color thresholds, and splits logic. It is unit-tested.
- `app/queries.py` has the SQL. Each request loads all of one player's games (a few hundred rows at most) and filters in Python.
- `app/main.py` has the routes, validation, rate limiting (slowapi), CORS, and security headers, and serves `app/static/`
- `app/static/` contains `index.html`, `styles.css`, `app.js`, and `vendor/chart.umd.min.js` (Chart.js 4.4.7)
- `ingest/ingest.py` uses the league-wide `LeagueGameLog` (player mode). It upserts on `(player_id, game_id)` and records each run in `ingest_runs`.
- `ingest/scheduler.py` is the long-running loop for the Docker `ingest` service. It backfills if the DB is empty and otherwise runs incremental daily at `INGEST_HOUR_UTC`.
- `Dockerfile` and `docker-compose.yml` build one image used by both services, with the `alleyoop-data` volume at `/app/data`. `GET /healthz` is the container healthcheck.
- `deploy/` has the systemd units (the non-Docker alternative) and the cloudflared config example

## Conventions

- All aggregation happens server-side in `app/stats.py`. The frontend only renders what the API returns, including the `colors` dicts.
- Color thresholds live in one place, `THRESHOLD_PCT` and `THRESHOLD_PCT_POINTS` in `app/stats.py`, and `/api/meta` exposes them to the UI legend.
- The season baseline is the regular-season average for the selected season (or the latest season in range). Shooting % is computed from totals.
- There is no build step and no npm, and the frontend has no CDN dependencies. Keep the CSP strict: no inline `<script>`/`style=`. In JS, build DOM with `el()` or `textContent` and never put data into `innerHTML`.
- Validate every query param with FastAPI `Query`/`Path` constraints and parameterize all SQL.
- Ingestion must be polite: few calls, with timeouts, retries and backoff, and delays between calls. It must never be triggered by a web request. A failed fetch must leave the existing data untouched.
- Avoid pandas in app code; nba_api installs it, but the app uses `get_dict()`.
- Python 3.10+ compatible syntax (the Pi may run an older Python than dev).
