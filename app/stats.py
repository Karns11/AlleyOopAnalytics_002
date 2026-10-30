"""Pure aggregation, advanced metrics and color-threshold logic.

Everything here operates on plain dicts (one per game) so it is easy to unit-test
without a database.
"""
from datetime import date

# ---- Color-coding thresholds: the single source of truth (exposed to the UI via /api/meta)
THRESHOLD_PCT = 0.10          # counting stats: +/-10% of the season average is "near"
THRESHOLD_PCT_POINTS = 0.02   # shooting percentages: +/-2 percentage points is "near"

COUNT_STATS = ["pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "min",
               "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "plus_minus"]
PCT_STATS = {"fg_pct": ("fgm", "fga"), "fg3_pct": ("fg3m", "fg3a"), "ft_pct": ("ftm", "fta")}
LOWER_IS_BETTER = {"tov", "pf"}

GREEN, YELLOW, RED = "green", "yellow", "red"


# ---------------------------------------------------------------- color coding

def color_for(stat: str, value, baseline):
    """Classify `value` relative to the season `baseline` for `stat`.

    Returns 'green' | 'yellow' | 'red', or None when there is nothing to compare.
    """
    if value is None or baseline is None:
        return None
    if stat in PCT_STATS:
        diff = value - baseline
        if diff > THRESHOLD_PCT_POINTS:
            return GREEN
        if diff < -THRESHOLD_PCT_POINTS:
            return RED
        return YELLOW

    if stat == "plus_minus":
        # Average can be negative or ~0, so a relative band is meaningless; use +/-10% of
        # max(|avg|, 1) as an absolute band.
        band = max(abs(baseline), 1.0) * THRESHOLD_PCT
    elif baseline == 0:
        band = 0.0
    else:
        band = abs(baseline) * THRESHOLD_PCT

    if value > baseline + band:
        result = GREEN
    elif value < baseline - band:
        result = RED
    else:
        result = YELLOW
    if stat in LOWER_IS_BETTER and result != YELLOW:
        result = RED if result == GREEN else GREEN
    return result


def color_row(row: dict, baseline: dict) -> dict:
    """Colors for every stat present in both row and baseline. Percentage colors are
    omitted for zero-attempt rows (e.g. 0-0 from three)."""
    colors = {}
    for stat in COUNT_STATS:
        colors[stat] = color_for(stat, row.get(stat), baseline.get(stat))
    for stat, (_, att) in PCT_STATS.items():
        colors[stat] = None if not row.get(att) else color_for(stat, row.get(stat), baseline.get(stat))
    return colors


# ---------------------------------------------------------------- aggregation

def _pct(made, att):
    return round(made / att, 3) if att else None


def averages(games: list[dict]) -> dict | None:
    """Per-game averages for counting stats; shooting % from totals (sum made / sum attempted)."""
    if not games:
        return None
    n = len(games)
    out = {"games": n}
    totals = {}
    for stat in COUNT_STATS:
        totals[stat] = sum((g.get(stat) or 0) for g in games)
        out[stat] = round(totals[stat] / n, 1)
    for stat, (made, att) in PCT_STATS.items():
        out[stat] = _pct(totals[made], totals[att])
    out["wins"] = sum(1 for g in games if g.get("wl") == "W")
    out["losses"] = sum(1 for g in games if g.get("wl") == "L")
    return out


# ---------------------------------------------------------------- advanced metrics

def efg_pct(fgm, fg3m, fga):
    return round((fgm + 0.5 * fg3m) / fga, 3) if fga else None


def ts_pct(pts, fga, fta):
    denom = 2 * (fga + 0.44 * fta)
    return round(pts / denom, 3) if denom else None


def game_score(g: dict) -> float:
    """John Hollinger's Game Score."""
    v = lambda k: g.get(k) or 0  # noqa: E731
    return round(
        v("pts") + 0.4 * v("fgm") - 0.7 * v("fga") - 0.4 * (v("fta") - v("ftm"))
        + 0.7 * v("oreb") + 0.3 * v("dreb") + v("stl") + 0.7 * v("ast") + 0.7 * v("blk")
        - 0.4 * v("pf") - v("tov"),
        1,
    )


def game_advanced(g: dict) -> dict:
    v = lambda k: g.get(k) or 0  # noqa: E731
    return {
        "efg_pct": efg_pct(v("fgm"), v("fg3m"), v("fga")),
        "ts_pct": ts_pct(v("pts"), v("fga"), v("fta")),
        "game_score": game_score(g),
    }


def advanced_summary(games: list[dict]) -> dict | None:
    if not games:
        return None
    tot = {k: sum((g.get(k) or 0) for g in games)
           for k in ("pts", "reb", "ast", "stl", "blk", "tov", "min", "fgm", "fga", "fg3m", "fta")}
    per36 = None
    if tot["min"]:
        f = 36 / tot["min"]
        per36 = {k: round(tot[k] * f, 1) for k in ("pts", "reb", "ast", "stl", "blk", "tov")}
    return {
        "efg_pct": efg_pct(tot["fgm"], tot["fg3m"], tot["fga"]),
        "ts_pct": ts_pct(tot["pts"], tot["fga"], tot["fta"]),
        "game_score": round(sum(game_score(g) for g in games) / len(games), 1),
        "ast_to": round(tot["ast"] / tot["tov"], 2) if tot["tov"] else None,
        "per36": per36,
    }


# ---------------------------------------------------------------- splits & highlights

def mark_back_to_backs(games: list[dict]) -> None:
    """Annotate each game (sorted by date) with b2b=True if the player also played the day before."""
    prev = None
    for g in games:
        d = date.fromisoformat(g["game_date"])
        g["b2b"] = prev is not None and (d - prev).days == 1
        prev = d


HIGHLIGHT_STATS = ["pts", "reb", "ast", "fg3m", "stl", "blk"]


def highlights(range_games: list[dict], season_games: list[dict], career_games: list[dict]) -> list[dict]:
    """Games in the range that matched the season high / career high (within ingested data)."""
    out = []
    for stat in HIGHLIGHT_STATS:
        season_high = max((g.get(stat) or 0 for g in season_games), default=0)
        career_high = max((g.get(stat) or 0 for g in career_games), default=0)
        if not season_high:
            continue
        for g in range_games:
            val = g.get(stat) or 0
            if val and val == career_high:
                kind = "career"
            elif val and val == season_high:
                kind = "season"
            else:
                continue
            out.append({"stat": stat, "value": val, "kind": kind, "game_date": g["game_date"],
                        "opponent_abbr": g["opponent_abbr"], "is_home": g["is_home"]})
    out.sort(key=lambda h: (h["kind"] != "career", h["game_date"]), reverse=False)
    return out


STREAK_STATS = ["pts", "reb", "ast"]


def streak(recent: list[dict], season_avg: dict | None, window: int = 5) -> dict | None:
    """Hot/cold indicator: last `window` games vs season average, using Game Score and PTS/REB/AST."""
    if not season_avg or len(recent) < min(window, 3):
        return None
    last = averages(recent[-window:])
    deltas = {}
    for stat in STREAK_STATS:
        base = season_avg.get(stat) or 0
        deltas[stat] = round((last[stat] - base) / base, 3) if base else 0.0
    score = sum(deltas.values()) / len(deltas)
    status = "hot" if score > THRESHOLD_PCT else "cold" if score < -THRESHOLD_PCT else "steady"
    return {"status": status, "window": len(recent[-window:]), "delta_pct": round(score, 3),
            "deltas": deltas, "last": {s: last[s] for s in STREAK_STATS}}
