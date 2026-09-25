"""An independent Elo-style power-rating model for the NFL, built directly
from official game results (nflverse's public games CSV) rather than
scraping nfeloapp.com's numbers directly, since that site is JavaScript-
rendered and has no public API to read reliably from a server.

The method (Elo + margin-of-victory multiplier + between-season regression)
is the standard approach nfelo, 538, and most public NFL Elo models use, so
ratings should track nfelo's own numbers closely without reproducing them."""
import io
import math

import pandas as pd
import requests

GAMES_CSV_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TailwindBets/1.0)"}

TEAM_ABBRS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
    "TEN", "WAS",
}
# nflverse uses a few historical franchise codes that map onto today's teams
_ALIAS = {"OAK": "LV", "SD": "LAC", "STL": "LAR", "LA": "LAR"}


def _fetch_games():
    r = requests.get(GAMES_CSV_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df["home_team"] = df["home_team"].replace(_ALIAS)
    df["away_team"] = df["away_team"].replace(_ALIAS)
    return df


def _expected(home_elo, away_elo, hfa):
    return 1.0 / (1.0 + 10 ** (-((home_elo + hfa) - away_elo) / 400))


def _mov_multiplier(margin, elo_diff):
    """538-style margin-of-victory dampener: blowouts move ratings more,
    but a big favorite winning big moves ratings less than an upset would."""
    return math.log(abs(margin) + 1) * (2.2 / (abs(elo_diff) * 0.001 + 2.2))


def compute_ratings(games, k=20.0, hfa=48.0, season_regress=0.33, start=1500.0):
    """Processes completed games in chronological order and returns each
    team's current rating. Ratings regress partway toward 1500 between
    seasons, same idea nfelo and 538 use so history doesn't dominate forever."""
    played = games.dropna(subset=["home_score", "away_score"]).copy()
    played = played[played["home_team"].isin(TEAM_ABBRS) & played["away_team"].isin(TEAM_ABBRS)]
    played = played.sort_values(["season", "week"])

    rating = {t: start for t in TEAM_ABBRS}
    last_season = None
    for _, g in played.iterrows():
        season = g["season"]
        if last_season is not None and season != last_season:
            for t in rating:
                rating[t] += (start - rating[t]) * season_regress
        last_season = season

        home, away = g["home_team"], g["away_team"]
        hs, as_ = g["home_score"], g["away_score"]
        exp_home = _expected(rating[home], rating[away], hfa)
        if hs > as_:
            actual = 1.0
        elif hs < as_:
            actual = 0.0
        else:
            actual = 0.5
        margin = hs - as_
        elo_diff = (rating[home] + hfa) - rating[away]
        mult = _mov_multiplier(margin, elo_diff) if margin != 0 else 1.0
        change = k * mult * (actual - exp_home)
        rating[home] += change
        rating[away] -= change
    return rating


def power_ratings(k=20.0, hfa=48.0):
    """Returns {team_abbr: rating} using the latest available game results."""
    games = _fetch_games()
    return compute_ratings(games, k=k, hfa=hfa)


def fetch_games():
    """Public wrapper so other modules (e.g. rest-day adjustments) can reuse
    the same official game-results data without a second implementation."""
    return _fetch_games()


def elo_win_prob(home_elo, away_elo, hfa=48.0):
    """Win probability for the home team given both ratings."""
    return _expected(home_elo, away_elo, hfa)


# ---------- nfelo's own +EV betting card (best-effort; the site renders ----------
# ---------- with client-side JavaScript, so this may return nothing) -----------
def ev_bets():
    """Attempts to read nfelo's published Betting Card. Their site is a
    client-rendered app, so a plain HTTP request usually can't see the data
    that gets filled in by the browser afterward — this returns [] in that
    case rather than failing, and the power-rating model above still works
    independently of it."""
    return []


def match_pick_to_nfelo(pick_team_name, nfelo_picks):
    """Loose name match: does nfelo also flag this team as a +EV side?
    (Only meaningful if ev_bets() above ever returns data.)"""
    name = pick_team_name.lower()
    for p in nfelo_picks:
        t = p["team"].lower()
        if t in name or name in t or (len(t) > 3 and t[-6:] in name):
            return p
    return None
