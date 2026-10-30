# Alley Oop Analytics

An NBA player stats explorer. Search for a player, pick a season or date range, and see every box score color-coded against the player's season averages, along with splits (home/away, last N, vs opponent, wins/losses, back-to-backs), advanced metrics, highlights, and trend charts with an optional second-player overlay.

- **Backend:** Python 3.10+, FastAPI, uvicorn, SQLite
- **Frontend:** plain HTML/CSS/JS, with Chart.js vendored in `app/static/vendor/`. There is no build step.
- **Data:** NBA.com via the free [`nba_api`](https://github.com/swar/nba_api) package. No API keys.

It's designed for a Raspberry Pi behind a Cloudflare Tunnel: one uvicorn worker, a single SQLite file, and an ingestion script that runs separately on a timer, not per request.

---

## Quick start (local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env               # optional; defaults work locally

# 1. Try a small slice first (one season, 5 players):
DB_PATH=data/test.db .venv/bin/python -m ingest.ingest --mode full \
    --seasons 2025-26 --season-types "Regular Season" --limit-players 5

# 2. Full backfill (current + previous 2 seasons, regular season + playoffs).
#    This makes 6 HTTP calls and takes under a minute.
.venv/bin/python -m ingest.ingest --mode full

# 3. Run the app
.venv/bin/uvicorn app.main:app --port 8000
# open http://localhost:8000
```

Run the tests with `.venv/bin/pytest -q`.

## Docker (recommended for the Pi)

One image runs as two containers that share a named volume holding the SQLite file:

| Service | What it does |
|---|---|
| `web` | uvicorn serving the API and the frontend on `${BIND_ADDR:-127.0.0.1}:${HOST_PORT:-8000}` |
| `ingest` | `python -m ingest.scheduler`. On first start it runs a full backfill. After that it runs an incremental update every day at `INGEST_HOUR_UTC` (default 09:00 UTC). If a run fails, it retries an hour later. |

```bash
docker compose up -d --build      # build + start (first start backfills in ~1 minute)
open http://localhost:8000
docker compose logs -f ingest     # watch ingestion
docker compose ps                 # web should show (healthy)
docker compose down               # stop (data persists in the alleyoop-data volume)
docker compose down -v            # stop AND delete the data
```

You don't need a `.env` file. To change settings, copy `.env.example` to `.env`; compose picks it up automatically. `HOST_PORT` changes the port on the host, which helps if 8000 is already taken on the Pi.

To open the app from other devices on your home network at `http://<pi-ip>:8000`, set `BIND_ADDR=0.0.0.0` in `.env` and run `docker compose up -d`. The default, `127.0.0.1`, makes the app reachable only from the Pi itself and cloudflared.

**On the Pi** (64-bit Raspberry Pi OS with Docker and the compose plugin installed):

```bash
git clone <your repo> ~/AlleyOopAnalytics && cd ~/AlleyOopAnalytics   # or scp/rsync the folder
cp .env.example .env && nano .env      # set ALLOWED_ORIGINS=https://nba.example.com, HOST_PORT if 8000 is used
docker compose up -d --build           # builds natively on arm64; takes a few minutes the first time
```

It fits alongside other apps:
- Both containers have memory caps (256 MB for web, 384 MB for ingest); the measured usage is about 40 MB and 75 MB.
- Both have log rotation, read-only root filesystems, and run as a non-root user.
- The port is bound to localhost only, so it can't clash with or be exposed to the LAN.

With Docker you don't need the systemd units in `deploy/`.

To update after pulling new code: `docker compose up -d --build`.

## Ingestion

```
python -m ingest.ingest --mode full                    # backfill all SEASONS
python -m ingest.ingest --mode full --resume           # skip chunks fetched OK in the last 24h
python -m ingest.ingest --mode incremental             # current season, from (latest game - 3 days)
python -m ingest.ingest --mode full --seasons 2024-25  # one season
```

- It uses `LeagueGameLog` in player mode (`PlayerOrTeam=P`). One request returns every player's game log for a season and season type, so it doesn't loop over players.
- Rows are upserted on `(player_id, game_id)`, so re-running is always safe. Incremental mode re-fetches the last 3 days to pick up stat corrections.
- Each fetch has a 60s timeout and 4 retries with exponential backoff and jitter, plus a 4–5s pause between calls.
- If stats.nba.com is down or blocks you, the run is logged as `failed` in the `ingest_runs` table and the script exits non-zero. The database is left as it was, and the web app keeps serving cached data.
- If stats.nba.com hangs from your network (it blocks some cloud/VPN IP ranges), set `NBA_PROXY`.
- Player names and teams come from `nba_api`'s bundled static data, refreshed with each player's most recent team in the logs.

Check recent runs:

```bash
sqlite3 data/alleyoop.db "select * from ingest_runs order by id desc limit 10"
```

## API

| Endpoint | Description |
|---|---|
| `GET /api/players/search?q=` | Case- and accent-insensitive partial match, up to 10 results |
| `GET /api/players/{id}/games?season=&start=&end=` | Box scores in range, with per-game TS%, eFG%, Game Score, a back-to-back flag, and colors |
| `GET /api/players/{id}/summary?season=&start=&end=&last_n=5&opponent=BOS` | Season baseline, splits (overall, last N, home, away, vs opponent, W/L, B2B/rested), advanced metrics, highlights, hot/cold streak |
| `GET /api/players/{id}/opponents?season=&start=&end=` | Opponents faced in range, with game counts |
| `GET /api/meta` | Available seasons, last-updated timestamp, color thresholds |

Dates are ISO (`YYYY-MM-DD`), seasons look like `2025-26`, and opponents are 3-letter abbreviations. Invalid input returns 422 and unknown players return 404.

**Color coding.** The thresholds live in `app/stats.py` (`THRESHOLD_PCT = 0.10`, `THRESHOLD_PCT_POINTS = 0.02`). Each value is compared with the player's **regular-season** average for the selected season. If no season is selected, the season of the latest game in range is used. TOV and PF are inverted. Shooting percentages use an absolute ±2-point band and are computed from totals, not by averaging per-game percentages.

---

## Deploying on a Raspberry Pi without Docker (systemd)

Skip this section if you use Docker. These steps assume the repo is at `/home/pi/AlleyOopAnalytics` and the user is `pi`. If not, edit the paths and `User=` lines in `deploy/*.service`.

```bash
sudo apt install -y python3-venv sqlite3
cd /home/pi/AlleyOopAnalytics
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # on 32-bit Pi OS, numpy/pandas wheels may take a while
cp .env.example .env
nano .env    # set ALLOWED_ORIGINS=https://nba.example.com
.venv/bin/python -m ingest.ingest --mode full
```

### Web app service

```bash
sudo cp deploy/alleyoop.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now alleyoop
systemctl status alleyoop
curl -s localhost:8000/api/meta | head -c 200
```

### Nightly ingestion timer

```bash
sudo cp deploy/alleyoop-ingest.service deploy/alleyoop-ingest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now alleyoop-ingest.timer
systemctl list-timers alleyoop-ingest.timer
sudo systemctl start alleyoop-ingest    # run once now
journalctl -u alleyoop-ingest -n 50     # logs
```

If you'd rather use cron (`crontab -e` as `pi`, times in the Pi's local zone):

```cron
0 5 * * * cd /home/pi/AlleyOopAnalytics && .venv/bin/python -m ingest.ingest --mode incremental >> data/ingest.log 2>&1
```

A weekly `--mode full --resume` is optional. Old seasons don't change, so the nightly incremental run is enough.

### Exposing it with Cloudflare Tunnel

You need a domain on Cloudflare. The app stays bound to `127.0.0.1`, so no ports are opened on your router. This works the same way with Docker or systemd.

**If the Pi already runs a tunnel for your other apps**, don't create a new one. Add a hostname rule above the final catch-all in your existing `config.yml`, then route DNS and restart:

```yaml
ingress:
  - hostname: nba.example.com
    service: http://localhost:8000      # HOST_PORT from .env
  # ...your existing rules...
  - service: http_status:404
```

```bash
cloudflared tunnel route dns <your-existing-tunnel-name> nba.example.com
sudo systemctl restart cloudflared
```

If cloudflared itself runs in a Docker container rather than on the host, `localhost` refers to that container, not the Pi. In that case, either put both on a shared Docker network and use `service: http://web:8000`, or run cloudflared with `network_mode: host`.

**Setting up a tunnel from scratch:**

```bash
# 1. Install cloudflared (64-bit Pi OS: arm64; 32-bit: arm)
curl -L -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb

# 2. Authenticate (opens a browser link; choose your domain)
cloudflared tunnel login

# 3. Create a named tunnel (prints a UUID and writes ~/.cloudflared/<UUID>.json)
cloudflared tunnel create alleyoop

# 4. Config: copy the example and fill in the UUID + hostname
cp deploy/cloudflared-config.example.yml ~/.cloudflared/config.yml
nano ~/.cloudflared/config.yml

# 5. Route DNS: creates a CNAME nba.example.com -> <UUID>.cfargotunnel.com
cloudflared tunnel route dns alleyoop nba.example.com

# 6. Test in the foreground
cloudflared tunnel run alleyoop

# 7. Install as a service (copies the config to /etc/cloudflared/)
sudo cloudflared --config /home/pi/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
```

In `/etc/cloudflared/config.yml`, make sure `credentials-file` points to a path the service can read.

### Public-site protections

- **Rate limiting:** per visitor IP (Cloudflare's `CF-Connecting-IP`). Data endpoints default to 60/min (`RATE_LIMIT`) and search to 120/min. Because uvicorn only listens on localhost, only cloudflared can set that header.
- **Input validation:** every parameter is checked by type and pattern, SQL is fully parameterized, and the SQLite connections used by requests are read-only.
- **CORS:** restricted to `ALLOWED_ORIGINS`, GET only.
- **Debug and docs:** debug is off, and `/api/docs` is disabled unless `ENABLE_DOCS=true`.
- **Security headers:** a strict CSP (no inline scripts or styles, no third-party origins), `nosniff`, `X-Frame-Options: DENY`, and a referrer policy.
- **Extra Cloudflare options:** consider enabling Bot Fight Mode and a cache rule for `/static/*` in the Cloudflare dashboard.

## Notes and limitations

- Early in a season the baselines are based on only a few games, so the colors are noisy until about 10 games have been played. Switch to the previous season for a steadier baseline.
- "High since 2024-25" highlights count only the ingested seasons, not the player's full career.
- Back-to-back means the player also played the previous day. A game the player sat out on the first night isn't counted.
- The data belongs to NBA.com. This project is unaffiliated with the NBA and intended for personal, non-commercial use.
