import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect, init_db
from app.main import app
from ingest.ingest import upsert_logs


def raw(game_id, date, matchup, pts, wl="W", player_id=1, name="Test Pläyer"):
    return {"PLAYER_ID": player_id, "PLAYER_NAME": name, "TEAM_ID": 10, "TEAM_ABBREVIATION": "AAA",
            "GAME_ID": game_id, "GAME_DATE": date, "MATCHUP": matchup, "WL": wl, "MIN": 30,
            "FGM": 8, "FGA": 16, "FG_PCT": 0.5, "FG3M": 2, "FG3A": 5, "FG3_PCT": 0.4, "FTM": 2, "FTA": 2,
            "FT_PCT": 1.0, "OREB": 1, "DREB": 4, "REB": 5, "AST": 5, "STL": 1, "BLK": 1, "TOV": 2, "PF": 2,
            "PTS": pts, "PLUS_MINUS": 3}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    conn = connect(db)
    init_db(conn)
    conn.execute("INSERT INTO teams VALUES (10, 'AAA', 'Alpha', 'A', 'Alphas'), (20, 'BBB', 'Beta', 'B', 'Betas'),"
                 " (30, 'CCC', 'Gamma', 'C', 'Gammas')")
    rows = [
        raw("1", "2025-11-01", "AAA vs. BBB", 20),
        raw("2", "2025-11-02", "AAA @ CCC", 30, "L"),
        raw("3", "2025-11-05", "AAA vs. CCC", 10),
        raw("4", "2025-11-08", "AAA @ BBB", 40),
    ]
    upsert_logs(conn, rows, "2025-26", "Regular Season")
    upsert_logs(conn, rows[:2], "2025-26", "Regular Season")  # idempotent re-run
    conn.close()
    app.state.limiter.enabled = False
    yield TestClient(app)
    app.state.limiter.enabled = True


def test_search_is_case_and_accent_insensitive(client):
    r = client.get("/api/players/search", params={"q": "PLAYER"})
    assert r.status_code == 200
    assert [p["id"] for p in r.json()["results"]] == [1]
    assert client.get("/api/players/search", params={"q": "pläy"}).json()["results"]


def test_games_range_and_flags(client):
    games = client.get("/api/players/1/games", params={"start": "2025-11-02", "end": "2025-11-05"}).json()["games"]
    assert [g["game_id"] for g in games] == ["2", "3"]
    assert games[0]["b2b"] is True and games[0]["is_home"] == 0 and games[0]["opponent_abbr"] == "CCC"
    assert games[0]["colors"]["pts"] == "green"  # 30 vs season avg 25
    assert games[1]["colors"]["pts"] == "red"


def test_summary_splits(client):
    s = client.get("/api/players/1/summary", params={"season": "2025-26", "last_n": 2, "opponent": "CCC"}).json()
    sp = s["splits"]
    assert s["season_avg"]["games"] == 4 and s["season_avg"]["pts"] == 25.0
    assert sp["last_n"]["games"] == 2 and sp["last_n"]["pts"] == 25.0
    assert sp["home"]["pts"] == 15.0 and sp["away"]["pts"] == 35.0
    assert sp["vs_opponent"]["games"] == 2 and sp["vs_opponent"]["pts"] == 20.0
    assert sp["b2b"]["games"] == 1 and sp["rested"]["games"] == 3
    assert sp["wins"]["games"] == 3 and sp["losses"]["games"] == 1


def test_opponents(client):
    opps = client.get("/api/players/1/opponents").json()["opponents"]
    assert opps == [{"abbr": "BBB", "name": "Beta", "games": 2}, {"abbr": "CCC", "name": "Gamma", "games": 2}]


@pytest.mark.parametrize("url", [
    "/api/players/search?q=a",
    "/api/players/search?q=<script>",
    "/api/players/0/games",
    "/api/players/1/games?start=2025-12-01&end=2025-11-01",
    "/api/players/1/games?start=yesterday",
    "/api/players/1/summary?season=2025",
    "/api/players/1/summary?opponent=bbb",
    "/api/players/1/summary?last_n=0",
    "/api/players/1/summary?last_n=1000",
])
def test_validation_errors(client, url):
    assert client.get(url).status_code == 422


def test_unknown_player(client):
    assert client.get("/api/players/999/summary").status_code == 404


def test_security_headers(client):
    r = client.get("/api/meta")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/docs").status_code == 404
