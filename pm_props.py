"""Prices Kalshi and Polymarket player prop markets against a model.

The model is the sportsbook consensus: for each player and stat, the vig is
removed from every book's Over/Under and the fair probabilities are averaged
(see props_edges.consensus). That is the sharpest public estimate of the
true chance, so a prediction-market price far from it is a real gap.

Prediction markets usually ask "will the player reach N or more?" (Kalshi's
"25+ points"). That equals an Over of N - 0.5. When books post that exact
line, the model uses it directly ("matched"). Otherwise it fits a normal
curve to the player's posted lines and reads the chance at N ("estimated"),
which is rougher and only done for stats with lines of 4.5 or more.

Both sites' market formats are parsed defensively: a market is only used
when its player can be tied to a player the sportsbooks also price, so a
misread market is skipped rather than turned into a fake edge."""
import json
import re
from datetime import datetime
from statistics import NormalDist
from zoneinfo import ZoneInfo

import requests

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
ET = ZoneInfo("America/New_York")

# Known Kalshi player prop series. Others are discovered from Kalshi's series
# list by title, and you can add more by hand on the Player Props page.
KALSHI_SERIES = {
    "NBA": ["KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT"],
    "NFL": [], "College football": [], "MLB": [], "NHL": [],
}
SERIES_KEYWORD = {"NBA": "nba", "NFL": "nfl", "College football": "college football",
                  "MLB": "mlb", "NHL": "nhl"}

# Phrase in a market's title -> Odds API market key. Checked in order.
STAT_WORDS = {
    "NBA": [("three", "player_threes"), ("3-point", "player_threes"), ("3pt", "player_threes"),
            ("rebound", "player_rebounds"), ("assist", "player_assists"), ("point", "player_points")],
    "NFL": [("passing yard", "player_pass_yds"), ("pass yard", "player_pass_yds"),
            ("rushing yard", "player_rush_yds"), ("rush yard", "player_rush_yds"),
            ("receiving yard", "player_reception_yds"), ("reception", "player_receptions"),
            ("passing touchdown", "player_pass_tds"), ("passing td", "player_pass_tds")],
    "MLB": [("strikeout", "pitcher_strikeouts"), ("total base", "batter_total_bases"),
            ("hit", "batter_hits")],
    "NHL": [("shots on goal", "player_shots_on_goal"), ("shot", "player_shots_on_goal"),
            ("save", "player_total_saves"), ("assist", "player_assists"), ("point", "player_points")],
}
STAT_WORDS["College football"] = STAT_WORDS["NFL"]
COMBO_MARKERS = ("+ reb", "+reb", "+ ast", "+ast", "pts+", "pra", "&", " and ")

# Rough game-to-game spread of each stat, as a fraction of the typical line.
STAT_CV = {
    "player_points": 0.30, "player_rebounds": 0.38, "player_assists": 0.45,
    "player_threes": 0.55, "player_pass_yds": 0.25, "player_rush_yds": 0.45,
    "player_reception_yds": 0.55, "player_receptions": 0.40, "player_pass_tds": 0.60,
    "pitcher_strikeouts": 0.33, "batter_total_bases": 0.90, "batter_hits": 0.80,
    "player_shots_on_goal": 0.45, "player_total_saves": 0.25,
}
MIN_LINE_TO_ESTIMATE = 4.5
KALSHI_FEE = 0.07   # Kalshi taker fee is about 7% x price x (1 - price) per contract
_N = NormalDist()


# ---------- names ----------
def norm_name(s):
    s = re.sub(r"[^a-z ]", "", str(s).lower().replace("-", " "))
    return " ".join(w for w in s.split() if w not in ("jr", "sr", "ii", "iii", "iv"))


def name_key(full):
    """'Jalen Brunson' -> 'JBRUNSON' (first initial + last name)."""
    parts = norm_name(full).split()
    return (parts[0][0] + parts[-1]).upper() if len(parts) >= 2 else "".join(parts).upper()


def match_player(text, players):
    """The sportsbook player this text refers to, or None if unclear."""
    t = norm_name(text)
    hits = [p for p in players if norm_name(p) and norm_name(p) in t]
    if len(hits) == 1:
        return hits[0]
    letters = re.sub(r"[^A-Z]", "", str(text).upper())
    hits = [p for p in players if letters and letters.endswith(name_key(p)) and len(name_key(p)) > 3]
    return hits[0] if len(hits) == 1 else None


def detect_stat(sport, text):
    t = str(text).lower()
    if any(c in t for c in COMBO_MARKERS):
        return None
    for word, key in STAT_WORDS.get(sport, []):
        if word in t:
            return key
    return None


# ---------- the model ----------
def model_over_prob(lines, target):
    """Chance the player goes Over `target`, given consensus [(line, p_over)].
    Returns (prob, method) or (None, None)."""
    if not lines:
        return None, None
    for line, p in lines:
        if abs(line - target) < 1e-9:
            return p, "matched"
    return None, None


def estimate_over_prob(lines, target, market_key):
    exact, how = model_over_prob(lines, target)
    if exact is not None:
        return exact, how
    cv = STAT_CV.get(market_key)
    usable = [(l, p) for l, p in lines if l >= MIN_LINE_TO_ESTIMATE and 0.03 < p < 0.97]
    if not cv or not usable:
        return None, None
    sd = max(cv * sum(l for l, _ in usable) / len(usable), 1.0)
    means = [l - sd * _N.inv_cdf(1 - p) for l, p in usable]
    mean = sum(means) / len(means)
    nearest = min(abs(target - l) for l, _ in usable)
    if nearest > 1.25 * sd:
        return None, None   # too far from any posted line to trust
    return 1 - _N.cdf((target - mean) / sd), "estimated"


# ---------- Kalshi ----------
def _get(url, **params):
    r = requests.get(url, params=params, headers={"User-Agent": None}, timeout=25)
    r.raise_for_status()
    return r.json()


def discover_kalshi_series(sport):
    """Known series plus any Sports series whose title looks like a player
    prop for this league. Never raises."""
    found = list(KALSHI_SERIES.get(sport, []))
    try:
        data = _get(f"{KALSHI}/series", category="Sports")
    except Exception:
        return found
    kw = SERIES_KEYWORD.get(sport, "")
    for s in data.get("series", []) or []:
        title = str(s.get("title", "")).lower()
        if kw and kw in title and ("player" in title or "prop" in title) and detect_stat(sport, title):
            if s.get("ticker") and s["ticker"] not in found:
                found.append(s["ticker"])
    return found


def kalshi_prop_markets(series_list):
    out = []
    for series in series_list:
        cursor = None
        for _ in range(5):
            params = dict(series_ticker=series, status="open", limit=1000)
            if cursor:
                params["cursor"] = cursor
            try:
                d = _get(f"{KALSHI}/markets", **params)
            except Exception:
                break
            for m in d.get("markets", []) or []:
                m["_series"] = series
                out.append(m)
            cursor = d.get("cursor")
            if not cursor:
                break
    return out


def _dollars(m, key):
    v = m.get(key + "_dollars")
    if v not in (None, ""):
        try:
            return float(v)
        except ValueError:
            return None
    v = m.get(key)
    return v / 100 if isinstance(v, (int, float)) else None


def _kalshi_date(ticker):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", str(ticker))
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(3)}{m.group(2)}20{m.group(1)}", "%d%b%Y").date()
    except ValueError:
        return None


def kalshi_line(m):
    """The equivalent sportsbook Over line for a Kalshi 'Yes', or None."""
    text = " ".join(str(m.get(k, "")) for k in ("yes_sub_title", "title", "subtitle"))
    n = re.search(r"(\d+(?:\.\d+)?)\s*\+", text)   # "25+" in the title is the clearest signal
    if n:
        return float(n.group(1)) - 0.5
    strike, kind = m.get("floor_strike"), str(m.get("strike_type", "")).lower()
    if isinstance(strike, (int, float)):
        return float(strike) - 0.5 if kind in ("greater_or_equal", "at_least") else float(strike)
    n = re.search(r"over\s+(\d+(?:\.\d+)?)", text.lower())
    if n:
        return float(n.group(1))
    n = re.search(r"-(\d+)$", str(m.get("ticker", "")))
    return float(n.group(1)) - 0.5 if n else None


def _series_stat(sport, m):
    return (detect_stat(sport, m.get("title", "")) or detect_stat(sport, m.get("yes_sub_title", ""))
            or detect_stat(sport, {"KXNBAPTS": "points", "KXNBAREB": "rebounds",
                                   "KXNBAAST": "assists", "KXNBA3PT": "threes"}.get(m.get("_series"), "")))


def price_kalshi(sport, markets, consensus_by_game, min_edge):
    """consensus_by_game: {game_label: {"date": date, "lines": {(mkey, player): [(line, p)]}}}"""
    out = []
    for m in markets:
        mkey = _series_stat(sport, m)
        line = kalshi_line(m)
        if not mkey or line is None:
            continue
        mdate = _kalshi_date(m.get("event_ticker") or m.get("ticker"))
        text = " ".join(str(m.get(k, "")) for k in ("title", "yes_sub_title"))
        for game, info in consensus_by_game.items():
            if mdate and info.get("date") and mdate != info["date"]:
                continue
            players = {p for (k, p) in info["lines"] if k == mkey}
            parts = str(m.get("ticker", "")).split("-")
            player = match_player(text, players) or (match_player(parts[-2], players) if len(parts) >= 3 else None)
            if not player:
                continue
            p_over, how = estimate_over_prob(info["lines"][(mkey, player)], line, mkey)
            if p_over is None:
                continue
            for side, price, p in (("Yes", _dollars(m, "yes_ask"), p_over),
                                   ("No", _dollars(m, "no_ask"), 1 - p_over)):
                if not price or not 0 < price < 1:
                    continue
                edge = p - price - KALSHI_FEE * price * (1 - price)
                if edge >= min_edge:
                    out.append(dict(venue="Kalshi", game=game, player=player, market=mkey,
                                    side="Over" if side == "Yes" else "Under", point=line,
                                    label=f"{'Yes' if side == 'Yes' else 'No'} on {line + 0.5:g}+",
                                    price=price, model_prob=p, edge=edge, method=how,
                                    ticker=m.get("ticker", "")))
            break
    return out


# ---------- Polymarket ----------
def poly_prop_markets(series_id):
    """All open markets in a league series (games and props). Never raises."""
    if not series_id:
        return []
    try:
        evs = _get(f"{GAMMA}/events", series_id=series_id, active="true", closed="false", limit=300)
    except Exception:
        return []
    out = []
    for ev in evs or []:
        for m in ev.get("markets") or []:
            try:
                outs = m["outcomes"]; prices = m["outcomePrices"]
                outs = json.loads(outs) if isinstance(outs, str) else outs
                prices = [float(x) for x in (json.loads(prices) if isinstance(prices, str) else prices)]
            except Exception:
                continue
            if len(outs) == 2 and len(prices) == 2:
                out.append(dict(question=m.get("question") or m.get("groupItemTitle") or "",
                                event_title=ev.get("title", ""), outcomes=outs, prices=prices,
                                end=m.get("endDate") or ev.get("endDate")))
    return out


def poly_line(question):
    """(over_line, yes_means_over) parsed from a question, or (None, None)."""
    q = question.lower()
    n = re.search(r"(\d+(?:\.\d+)?)\s*\+", q)
    if n:
        return float(n.group(1)) - 0.5, True
    n = re.search(r"(?:o/u|over/under|over|under)\s*(\d+(?:\.\d+)?)", q) or re.search(r"(\d+\.5)", q)
    if n:
        return float(n.group(1)), "under" not in q.split(n.group(1))[0][-12:]
    return None, None


def price_poly(sport, markets, consensus_by_game, min_edge):
    out = []
    for m in markets:
        q = m["question"]
        mkey = detect_stat(sport, q)
        line, yes_over = poly_line(q)
        if not mkey or line is None:
            continue
        outs = [str(o).lower() for o in m["outcomes"]]
        if set(outs) == {"over", "under"}:
            over_price, under_price = m["prices"][outs.index("over")], m["prices"][outs.index("under")]
        elif set(outs) == {"yes", "no"}:
            y, n = m["prices"][outs.index("yes")], m["prices"][outs.index("no")]
            over_price, under_price = (y, n) if yes_over else (n, y)
        else:
            continue
        for game, info in consensus_by_game.items():
            players = {p for (k, p) in info["lines"] if k == mkey}
            player = match_player(q, players)
            if not player:
                continue
            p_over, how = estimate_over_prob(info["lines"][(mkey, player)], line, mkey)
            if p_over is None:
                continue
            for side, price, p in (("Over", over_price, p_over), ("Under", under_price, 1 - p_over)):
                if not 0 < price < 1:
                    continue
                edge = p - price
                if edge >= min_edge:
                    out.append(dict(venue="Polymarket", game=game, player=player, market=mkey,
                                    side=side, point=line, label=f"{side} {line:g}", price=price,
                                    model_prob=p, edge=edge, method=how, ticker=""))
            break
    return out


def event_date_et(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(ET).date()
    except ValueError:
        return None
