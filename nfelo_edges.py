"""Pull nfelo's own power ratings and +EV betting card from nfeloapp.com,
and cross-check them against your sportsbook-derived picks."""
import io
import json
import re

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TailwindBets/1.0)"}
POWER_URL = "https://www.nfeloapp.com/nfl-power-ratings/"
EV_URL = "https://www.nfeloapp.com/games/nfl-ev-bets/"

TEAM_ABBRS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
    "TEN", "WAS",
}


def _fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def _next_data(html):
    """Pull the Next.js __NEXT_DATA__ JSON blob embedded in the page, if present."""
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _walk(obj):
    """Yield every dict found anywhere inside a nested JSON structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------- power ratings ----------
def power_ratings():
    """Returns {team_abbr: nfelo_rating}. Tries an HTML table first, then
    falls back to scanning the page's embedded JSON."""
    html = _fetch(POWER_URL)

    for df in pd.read_html(io.StringIO(html)):
        team_col = next((c for c in df.columns if str(c).lower() in ("team", "unnamed: 1")), None)
        elo_col = next((c for c in df.columns if "nfelo" in str(c).lower()), None)
        if team_col is not None and elo_col is not None:
            out = {}
            for _, row in df.iterrows():
                team = re.sub(r"[^A-Z]", "", str(row[team_col]).upper())[-3:]
                rating = _num(row[elo_col])
                if team in TEAM_ABBRS and rating and rating > 800:
                    out[team] = rating
            if len(out) >= 20:
                return out

    data = _next_data(html)
    out = {}
    if data:
        for d in _walk(data):
            team = str(d.get("team", d.get("abbr", ""))).upper()
            rating = _num(d.get("nfelo", d.get("elo", d.get("rating"))))
            if team in TEAM_ABBRS and rating and rating > 800:
                out[team] = rating
    return out


def elo_win_prob(home_elo, away_elo, hfa=55.0):
    """Standard Elo win-probability formula, nfelo's published HFA (~55 pts)."""
    diff = (home_elo + hfa) - away_elo
    return 1.0 / (1.0 + 10 ** (-diff / 400))


# ---------- nfelo's own EV bets page ----------
def ev_bets():
    """Returns a list of nfelo's own flagged +EV sides:
    [{team, opponent, spread, ev}, ...]. Best-effort parse; may return []
    if nfelo changes their page layout."""
    html = _fetch(EV_URL)
    picks = []

    for df in pd.read_html(io.StringIO(html)):
        cols = {str(c).lower(): c for c in df.columns}
        team_col = next((cols[c] for c in cols if "team" in c or c == "pick"), None)
        ev_col = next((cols[c] for c in cols if c in ("ev", "expected value") or "ev" in c), None)
        spread_col = next((cols[c] for c in cols if "spread" in c or "line" in c), None)
        if team_col is not None and ev_col is not None:
            for _, row in df.iterrows():
                ev = _num(str(row[ev_col]).replace("%", ""))
                team = re.sub(r"[^A-Za-z ]", "", str(row[team_col])).strip()
                if ev is not None and team:
                    picks.append(dict(
                        team=team,
                        spread=row[spread_col] if spread_col is not None else None,
                        ev=ev / 100 if abs(ev) > 1 else ev,
                    ))
            if picks:
                return picks

    data = _next_data(html)
    if data:
        for d in _walk(data):
            ev = _num(d.get("ev", d.get("expected_value")))
            team = d.get("team") or d.get("pick_team")
            if ev is not None and team:
                picks.append(dict(team=str(team), spread=d.get("spread"),
                                  ev=ev / 100 if abs(ev) > 1 else ev))
    return picks


def match_pick_to_nfelo(pick_team_name, nfelo_picks):
    """Loose name match: does nfelo also flag this team as a +EV side?"""
    name = pick_team_name.lower()
    for p in nfelo_picks:
        t = p["team"].lower()
        if t in name or name in t or (len(t) > 3 and t[-6:] in name):
            return p
    return None
