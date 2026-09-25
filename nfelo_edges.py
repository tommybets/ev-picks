"""Pull nfelo's own power ratings and +EV betting card from nfeloapp.com,
and cross-check them against your sportsbook-derived picks."""
import re
from concurrent.futures import ThreadPoolExecutor

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TailwindBets/1.0)"}
TEAM_URL = "https://www.nfeloapp.com/teams/{abbr}"
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


def _visible_text(html):
    """Strip tags/scripts down to plain, whitespace-collapsed page text."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------- power ratings ----------
def _one_team_rating(abbr):
    try:
        text = _visible_text(_fetch(TEAM_URL.format(abbr=abbr.lower())))
    except Exception:
        return None
    m = re.search(r"nfelo Rating\s*(\d{3,4})", text)
    return _num(m.group(1)) if m else None


def power_ratings():
    """Returns {team_abbr: nfelo_rating}, one request per team page."""
    with ThreadPoolExecutor(8) as ex:
        ratings = list(ex.map(_one_team_rating, sorted(TEAM_ABBRS)))
    return {t: r for t, r in zip(sorted(TEAM_ABBRS), ratings) if r and r > 800}


def elo_win_prob(home_elo, away_elo, hfa=55.0):
    """Standard Elo win-probability formula, nfelo's published HFA (~55 pts)."""
    diff = (home_elo + hfa) - away_elo
    return 1.0 / (1.0 + 10 ** (-diff / 400))


# ---------- nfelo's own EV bets page ----------
_EV_PATTERN = re.compile(
    r"Model Recommendation\s+"
    r"([A-Za-z0-9 .'\-]+?)\s+"          # team full name, e.g. "Houston Texans"
    r"[A-Za-z.]+[+-]\d+(?:\.\d+)?\s+"    # short pick label, e.g. "Texans-5.5" (discarded)
    r"Market Line\s*([+-]?\d+(?:\.\d+)?)\s+"
    r"Model Line\s*([+-]?\d+(?:\.\d+)?|N/A)\s+"
    r"Cover Probability\s*([\d.]+)%\s+"
    r"Expected Value\s*([+-]?[\d.]+)%"
)


def ev_bets():
    """Returns a list of nfelo's own flagged +EV sides:
    [{team, spread, model_line, cover_prob, ev}, ...]. Best-effort text
    parse of nfelo's Betting Card page; returns [] if their layout changed."""
    text = _visible_text(_fetch(EV_URL))
    picks = []
    for m in _EV_PATTERN.finditer(text):
        team, market_line, model_line, cover_prob, ev = m.groups()
        picks.append(dict(
            team=team.strip(),
            spread=_num(market_line),
            model_line=None if model_line == "N/A" else _num(model_line),
            cover_prob=_num(cover_prob) / 100 if _num(cover_prob) is not None else None,
            ev=_num(ev) / 100 if _num(ev) is not None else None,
        ))
    return picks


def match_pick_to_nfelo(pick_team_name, nfelo_picks):
    """Loose name match: does nfelo also flag this team as a +EV side?"""
    name = pick_team_name.lower()
    for p in nfelo_picks:
        t = p["team"].lower()
        if t in name or name in t or (len(t) > 3 and t[-6:] in name):
            return p
    return None
