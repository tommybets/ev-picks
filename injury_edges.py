"""Situational adjustments layered on top of the base Elo power ratings:
QB/key-player injury status and rest days. These are the two factors that
move real sportsbook lines the most outside of team strength itself.

Adjustment sizes below are rule-of-thumb estimates (roughly converted from
commonly cited point-spread impacts, at ~25 Elo points per spread point),
not fitted to data — treat the *direction* and *relative size* as more
trustworthy than the exact number."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

HEADERS = {"User-Agent": None}
TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
TEAM_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{id}?enable=injuries"

# Rough Elo-point equivalents (~25 Elo ≈ 1 point of spread)
QB_STATUS_PENALTY = {
    "out": -100.0, "injured reserve": -100.0, "ir": -100.0, "pup": -100.0,
    "doubtful": -55.0,
    "questionable": -15.0,
}
SKILL_STATUS_PENALTY = {  # for non-QB "key" positions, a lighter touch
    "out": -20.0, "injured reserve": -20.0, "ir": -20.0, "pup": -20.0,
    "doubtful": -10.0,
    "questionable": -3.0,
}
KEY_SKILL_POSITIONS = {"RB", "WR", "TE", "LT", "CB", "EDGE", "DE"}
SHORT_REST_PENALTY = -15.0   # e.g. Thursday game off a Sunday
LONG_REST_BONUS = 10.0       # bye week or 10+ days
SHORT_REST_DAYS = 6
LONG_REST_DAYS = 10


def _get(url, **params):
    r = requests.get(url, params=params, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.json()


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def team_ids():
    """Returns {team_abbr: espn_team_id}."""
    data = _get(TEAMS_URL)
    out = {}
    for d in _walk(data):
        if "abbreviation" in d and "id" in d and isinstance(d.get("id"), (str, int)):
            abbr = str(d["abbreviation"]).upper()
            if len(abbr) <= 4 and abbr.isalpha():
                out[abbr] = str(d["id"])
    return out


def _status_text(d):
    for path in (("status", "name"), ("status", "type", "name"), ("status", "id"), ("status",)):
        v = d
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
            if v is None:
                break
        if isinstance(v, str) and v:
            return v.lower()
    return ""


def _player_name(d):
    for k in ("displayName", "shortName", "name", "fullName"):
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    ath = d.get("athlete")
    if isinstance(ath, dict):
        return _player_name(ath)
    return None


def _position(d):
    pos = d.get("position")
    if isinstance(pos, dict):
        return str(pos.get("abbreviation", "")).upper()
    ath = d.get("athlete")
    if isinstance(ath, dict):
        return _position(ath)
    return ""


def team_injuries(espn_id):
    """Returns [{name, position, status}, ...] for one team. Best-effort:
    returns [] if ESPN's response shape doesn't match (rather than raising),
    since this is a secondary signal, not the core of the tool."""
    try:
        data = _get(TEAM_URL.format(id=espn_id))
    except Exception:
        return []
    out, seen = [], set()
    for d in _walk(data):
        if "athlete" not in d and "displayName" not in d and "position" not in d:
            continue
        status = _status_text(d)
        if status not in ("out", "doubtful", "questionable", "injured reserve", "ir", "pup"):
            continue
        name = _player_name(d)
        pos = _position(d)
        if not name or (name, pos) in seen:
            continue
        seen.add((name, pos))
        out.append(dict(name=name, position=pos, status=status))
    return out


def all_injuries(league="NFL"):
    """Returns {team_abbr: [injury, ...]} for every team, fetched in parallel."""
    ids = team_ids()
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(team_injuries, ids.values()))
    return {abbr: injuries for abbr, injuries in zip(ids.keys(), results)}


def injury_adjustment(team_injuries_list):
    """Returns (elo_delta, [reason strings]) for one team's injury list.
    Only the single worst QB status counts (can't have two starters out),
    but each flagged skill-position player stacks."""
    delta, reasons = 0.0, []
    worst_qb, worst_qb_rank = None, -1
    rank = {"questionable": 0, "doubtful": 1, "out": 1, "injured reserve": 1, "ir": 1, "pup": 1}
    for inj in team_injuries_list:
        if inj["position"] == "QB":
            r = rank.get(inj["status"], 0)
            if r > worst_qb_rank:
                worst_qb_rank, worst_qb = r, inj
        elif inj["position"] in KEY_SKILL_POSITIONS:
            pen = SKILL_STATUS_PENALTY.get(inj["status"])
            if pen:
                delta += pen
                reasons.append(f"{inj['name']} ({inj['position']}) {inj['status']}: {pen:+.0f}")
    if worst_qb:
        pen = QB_STATUS_PENALTY.get(worst_qb["status"], 0.0)
        delta += pen
        reasons.append(f"{worst_qb['name']} (QB) {worst_qb['status']}: {pen:+.0f}")
    return delta, reasons


# ---------- rest days ----------
def _last_game_date(games_df, team, before_date):
    sub = games_df[
        ((games_df["home_team"] == team) | (games_df["away_team"] == team))
        & (games_df["gameday"] < before_date)
        & games_df["home_score"].notna()
    ]
    if sub.empty:
        return None
    return sub["gameday"].max()


def rest_adjustment(games_df, team, game_date):
    """Returns (elo_delta, reason or None) based on days since the team's
    last completed game. game_date and the CSV's gameday column are both
    'YYYY-MM-DD' strings, which sort correctly as plain strings."""
    last = _last_game_date(games_df, team, game_date)
    if last is None:
        return 0.0, None
    days = (datetime.strptime(game_date, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
    if days <= SHORT_REST_DAYS:
        return SHORT_REST_PENALTY, f"Short rest ({days}d): {SHORT_REST_PENALTY:+.0f}"
    if days >= LONG_REST_DAYS:
        return LONG_REST_BONUS, f"Extra rest ({days}d): {LONG_REST_BONUS:+.0f}"
    return 0.0, None


def adjusted_rating(base_rating, team, injuries_by_team, games_df=None, game_date=None):
    """Combines base Elo rating with injury and (optional) rest adjustments.
    Returns (adjusted_rating, [reason strings])."""
    delta, reasons = injury_adjustment(injuries_by_team.get(team, []))
    if games_df is not None and game_date is not None:
        r_delta, r_reason = rest_adjustment(games_df, team, game_date)
        delta += r_delta
        if r_reason:
            reasons.append(r_reason)
    return base_rating + delta, reasons
