import pytest

from app import stats
from app.stats import GREEN, RED, YELLOW, color_for
from ingest.ingest import parse_matchup, parse_minutes


def game(**kw):
    base = dict(game_date="2025-11-01", opponent_abbr="BOS", is_home=1, wl="W", min=30, pts=20, reb=5,
                oreb=1, dreb=4, ast=5, stl=1, blk=1, tov=2, pf=2, fgm=8, fga=16, fg3m=2, fg3a=5,
                ftm=2, fta=2, plus_minus=5)
    base.update(kw)
    for pct, (m, a) in stats.PCT_STATS.items():
        base.setdefault(pct, base[m] / base[a] if base[a] else None)
    return base


# ---------------------------------------------------------------- color thresholds

@pytest.mark.parametrize("value,expected", [
    (22.1, GREEN), (22.0, YELLOW), (20.0, YELLOW), (18.0, YELLOW), (17.9, RED),
])
def test_counting_stat_band_is_ten_percent(value, expected):
    assert color_for("pts", value, 20.0) == expected


@pytest.mark.parametrize("stat", ["tov", "pf"])
def test_lower_is_better_is_inverted(stat):
    assert color_for(stat, 4, 2.0) == RED
    assert color_for(stat, 1, 2.0) == GREEN
    assert color_for(stat, 2.1, 2.0) == YELLOW


def test_percentages_use_absolute_points():
    assert color_for("fg_pct", 0.48, 0.45) == GREEN    # +3 pts
    assert color_for("fg_pct", 0.465, 0.45) == YELLOW  # +1.5 pts
    assert color_for("fg_pct", 0.42, 0.45) == RED      # -3 pts
    # a relative 10% band would call 0.40 vs 0.45 "near"; points-based does not
    assert color_for("fg3_pct", 0.40, 0.45) == RED


def test_zero_baseline():
    assert color_for("blk", 0, 0.0) == YELLOW
    assert color_for("blk", 1, 0.0) == GREEN
    assert color_for("tov", 1, 0.0) == RED


def test_plus_minus_handles_negative_and_zero_average():
    assert color_for("plus_minus", 5, -2.0) == GREEN
    assert color_for("plus_minus", -5, -2.0) == RED
    assert color_for("plus_minus", 0, 0.0) == YELLOW


def test_missing_values_have_no_color():
    assert color_for("pts", None, 20) is None
    assert color_for("pts", 20, None) is None


def test_color_row_skips_pct_for_zero_attempts():
    baseline = stats.averages([game(), game()])
    colors = stats.color_row(game(fg3m=0, fg3a=0, fg3_pct=None), baseline)
    assert colors["fg3_pct"] is None
    assert colors["pts"] == YELLOW


# ---------------------------------------------------------------- aggregation

def test_averages_and_pct_from_totals():
    games = [game(pts=10, fgm=1, fga=1), game(pts=30, fgm=9, fga=19)]
    avg = stats.averages(games)
    assert avg["games"] == 2
    assert avg["pts"] == 20.0
    # from totals: 10/20 = .500 (averaging per-game pct would give (1.0 + .474)/2 = .737)
    assert avg["fg_pct"] == 0.5
    assert avg["wins"] == 2 and avg["losses"] == 0


def test_averages_empty():
    assert stats.averages([]) is None


def test_averages_zero_attempts_pct_is_none():
    assert stats.averages([game(fta=0, ftm=0)])["ft_pct"] is None


def test_advanced_metrics():
    g = game(pts=30, fgm=10, fga=20, fg3m=4, fta=10, ftm=6)
    adv = stats.game_advanced(g)
    assert adv["efg_pct"] == 0.6                                # (10 + 2) / 20
    assert adv["ts_pct"] == round(30 / (2 * (20 + 4.4)), 3)
    summary = stats.advanced_summary([game(min=18, pts=10, ast=4, tov=2)])
    assert summary["per36"]["pts"] == 20.0
    assert summary["ast_to"] == 2.0


def test_game_score():
    g = game(pts=20, fgm=8, fga=16, fta=2, ftm=2, oreb=1, dreb=4, stl=1, ast=5, blk=1, pf=2, tov=2)
    # 20 + 3.2 - 11.2 - 0 + 0.7 + 1.2 + 1 + 3.5 + 0.7 - 0.8 - 2
    assert stats.game_score(g) == 16.3


def test_back_to_back_detection():
    games = [game(game_date="2025-11-01"), game(game_date="2025-11-02"), game(game_date="2025-11-04")]
    stats.mark_back_to_backs(games)
    assert [g["b2b"] for g in games] == [False, True, False]


def test_streak():
    season = [game(pts=20, reb=5, ast=5)] * 10 + [game(pts=30, reb=8, ast=8)] * 5
    s = stats.streak(season, stats.averages(season))
    assert s["status"] == "hot"
    cold = [game(pts=20, reb=5, ast=5)] * 10 + [game(pts=10, reb=2, ast=2)] * 5
    assert stats.streak(cold, stats.averages(cold))["status"] == "cold"


def test_highlights():
    season = [game(game_date="2025-11-01", pts=20), game(game_date="2025-11-03", pts=40)]
    career = season + [game(game_date="2024-11-01", pts=35)]
    hs = stats.highlights(season, season, career)
    pts = [h for h in hs if h["stat"] == "pts"]
    assert pts == [{"stat": "pts", "value": 40, "kind": "career", "game_date": "2025-11-03",
                    "opponent_abbr": "BOS", "is_home": 1}]


# ---------------------------------------------------------------- ingestion parsing

def test_parse_matchup():
    assert parse_matchup("LAL vs. BOS") == (True, "BOS")
    assert parse_matchup("LAL @ BOS") == (False, "BOS")
    with pytest.raises(ValueError):
        parse_matchup("LAL BOS")


def test_parse_minutes():
    assert parse_minutes(34) == 34.0
    assert parse_minutes("34:30") == 34.5
    assert parse_minutes(None) is None
