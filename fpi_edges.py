"""Compare ESPN FPI win probabilities with Kalshi and Polymarket prices."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

_ET = ZoneInfo("America/New_York")


def format_kickoff(iso_str):
    """ESPN gives kickoff times as UTC ISO strings (e.g. '2026-09-28T17:00Z').
    Converts to Eastern time — the standard convention for NFL scheduling —
    and formats as 'Sun, Sep 28 · 1:00 PM ET'. Falls back to the raw string
    if it doesn't parse, rather than failing the whole page."""
    if not iso_str:
        return "Time TBD"
    try:
        s = iso_str.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(s)
        dt_et = dt_utc.astimezone(_ET)
        return dt_et.strftime("%a, %b %-d · %-I:%M %p ET")
    except (ValueError, TypeError):
        return iso_str

ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{lg}"
ESPN_CORE = ("https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{lg}"
             "/events/{eid}/competitions/{eid}/predictor")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2/events"
GAMMA = "https://gamma-api.polymarket.com"
NO_UA = {"User-Agent": None}  # ESPN 403s custom User-Agents from datacenter IPs

# "espn_sport" is the sport segment in ESPN's URLs (e.g. "basketball"),
# "espn" is the league segment (e.g. "nba") — they're separate because
# ESPN nests league under sport rather than using one combined slug.
# nfelo-style win-probability data ("gameProjection") is ESPN's FPI for
# football and BPI for basketball; MLB/NHL don't always have an equivalent
# published, so those may show fewer "With FPI" games — that's expected,
# not a bug, and the rest of the comparison still works off whatever ESPN
# does return.
LEAGUES = {
    "NFL": dict(espn_sport="football", espn="nfl", kalshi="KXNFLGAME",
               poly=("nfl",), extra={}),
    "College football": dict(espn_sport="football", espn="college-football",
                             kalshi="KXNCAAFGAME", poly=("cfb", "ncaaf"),
                             extra={"groups": "80"}),
    "NBA": dict(espn_sport="basketball", espn="nba", kalshi="KXNBAGAME",
               poly=("nba",), extra={}),
    "NHL": dict(espn_sport="hockey", espn="nhl", kalshi="KXNHLGAME",
               poly=("nhl",), extra={}),
    "MLB": dict(espn_sport="baseball", espn="mlb", kalshi="KXMLBGAME",
               poly=("mlb",), extra={}),
}


def _get(url, **params):
    r = requests.get(url, params=params, headers=NO_UA, timeout=25)
    r.raise_for_status()
    return r.json()


def to_prob(x):
    try:
        v = float(str(x).replace("%", ""))
    except (TypeError, ValueError):
        return None
    if v > 1:
        v /= 100
    return v if 0 <= v <= 1 else None


# ---------- ESPN ----------
# (espn_games is defined once, further down, with a day-by-day fallback —
# ESPN sometimes rejects a combined date-range request depending on sport.)


def _stat(side):
    for s in side.get("statistics") or []:
        if s.get("name") == "gameProjection":
            return to_prob(s.get("value"))
    return to_prob(side.get("gameProjection"))


def _proj(pred):
    h = _stat(pred.get("homeTeam") or {})
    if h is not None:
        return h
    a = _stat(pred.get("awayTeam") or {})
    return None if a is None else 1 - a


def fpi_home_prob(league, eid):
    cfg = LEAGUES[league]
    lg, sport = cfg["espn"], cfg["espn_sport"]
    tries = [lambda: _get(ESPN_CORE.format(sport=sport, lg=lg, eid=eid)),
             lambda: _get(ESPN_SITE.format(sport=sport, lg=lg) + "/summary", event=eid).get("predictor", {})]
    for t in tries:
        try:
            p = _proj(t())
            if p is not None:
                return p
        except Exception:
            continue
    return None


def fpi_map(league, games):
    if league == "NHL":
        # ESPN doesn't publish a BPI/FPI-style predictor for hockey
        # (verified directly), so use MoneyPuck's public win probabilities
        # instead. Their page only covers the current slate, so games on
        # later days simply won't match — same "With FPI" behavior as any
        # other data-availability gap.
        import moneypuck_edges as mpe
        team_probs = mpe.team_win_probs()
        out = {}
        for g in games:
            home_abbr = mpe._norm(g["home"].get("abbreviation", ""))
            p = team_probs.get(home_abbr)
            if p is not None:
                out[g["id"]] = p
        return out
    with ThreadPoolExecutor(8) as ex:
        probs = list(ex.map(lambda g: fpi_home_prob(league, g["id"]), games))
    return {g["id"]: p for g, p in zip(games, probs)}


# ---------- name matching ----------
def keys(team):
    ks = {str(team.get(k, "")).lower() for k in
          ("displayName", "shortDisplayName", "name", "location", "abbreviation")}
    return {k for k in ks if k}


def score(ks, text):
    t = (text or "").lower().strip()
    if not t:
        return 0
    if t in ks:
        return 2
    return int(any((len(k) > 3 and k in t) or (len(t) > 3 and t in k) for k in ks))


def assign(home, away, texts):
    """Which of two texts is the home team? Returns (home_idx, away_idx) or None."""
    a = score(home, texts[0]) * score(away, texts[1])
    b = score(home, texts[1]) * score(away, texts[0])
    a = score(home, texts[0]) + score(away, texts[1]) if a else 0
    b = score(home, texts[1]) + score(away, texts[0]) if b else 0
    if a == b:
        return None
    return (0, 1) if a > b else (1, 0)


# ---------- Kalshi ----------
def kalshi_events(series):
    out, cursor = [], None
    for _ in range(10):
        p = dict(series_ticker=series, status="open", with_nested_markets="true", limit=200)
        if cursor:
            p["cursor"] = cursor
        d = _get(KALSHI, **p)
        out += d.get("events", [])
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


def kalshi_lookup(events, game):
    hk, ak = keys(game["home"]), keys(game["away"])
    pair = (game["away"].get("abbreviation", "") + game["home"].get("abbreviation", "")).upper()
    for ev in events:
        seg = re.sub(r"^\d{2}[A-Z]{3}\d{1,2}", "", ev.get("event_ticker", "").split("-")[-1].upper())
        ms = ev.get("markets") or []
        text = ev.get("title", "") + " " + " ".join(m.get("title", "") for m in ms)
        if not (seg == pair or (score(hk, text) and score(ak, text))):
            continue
        res = {}
        for side, team in (("home", game["home"]), ("away", game["away"])):
            abbr = team.get("abbreviation", "").upper()
            best = None
            for m in ms:
                if m.get("ticker", "").split("-")[-1].upper() == abbr:
                    best = m
                    break
            if best is None:
                cands = [(score(keys(team), m.get("yes_sub_title", "")), m) for m in ms]
                cands = [c for c in cands if c[0]]
                best = max(cands, key=lambda c: c[0])[1] if cands else None
            if best is not None:
                ask = _dollars(best, "yes_ask")
                res[side] = ask if ask and ask > 0 else None
        if res:
            return res
    return None


# ---------- Polymarket ----------
def poly_candidates(league, series_override=None):
    cfg = LEAGUES[league]
    series = series_override
    if not series:
        for s in _get(f"{GAMMA}/sports"):
            if str(s.get("sport", "")).lower() in cfg["poly"] and s.get("series"):
                series = str(s["series"])
                break
    if not series:
        return [], None
    evs = _get(f"{GAMMA}/events", series_id=series, tag_id=100639, active="true",
               closed="false", order="startTime", ascending="true", limit=200)
    cands = []
    for ev in evs:
        for m in ev.get("markets") or []:
            try:
                outs, prices = m["outcomes"], m["outcomePrices"]
                outs = json.loads(outs) if isinstance(outs, str) else outs
                prices = json.loads(prices) if isinstance(prices, str) else prices
                prices = [float(x) for x in prices]
            except Exception:
                continue
            if len(outs) == 2 and len(prices) == 2 and not ({o.lower() for o in outs} & {"yes", "no", "over", "under"}):
                cands.append(dict(outs=outs, prices=prices))
    return cands, series


def poly_lookup(cands, game):
    hk, ak = keys(game["home"]), keys(game["away"])
    for c in cands:
        a = assign(hk, ak, c["outs"])
        if a:
            return {"home": c["prices"][a[0]], "away": c["prices"][a[1]]}
    return None


# ---------- combine ----------
def build_rows(games, fpi, kal_events, poly):
    rows = []
    for g in games:
        ph = fpi.get(g["id"])
        if ph is None:
            continue
        kl = kalshi_lookup(kal_events, g) if kal_events else None
        pl = poly_lookup(poly, g) if poly else None
        for side, p in (("home", ph), ("away", 1 - ph)):
            ask = (kl or {}).get(side)
            pp = (pl or {}).get(side)
            fee = 0.07 * ask * (1 - ask) if ask else None  # Kalshi taker fee per contract (approx.)
            k_edge = p - ask - fee if ask else None
            p_edge = p - pp if pp else None
            edges = [e for e in (k_edge, p_edge) if e is not None]
            rows.append(dict(
                game=f'{g["away"].get("abbreviation")} @ {g["home"].get("abbreviation")}',
                kickoff=g["date"], team=g[side].get("displayName", "?"), fpi=p,
                kalshi_ask=ask, kalshi_edge=k_edge, poly_price=pp, poly_edge=p_edge,
                best=max(edges) if edges else None))
    return sorted(rows, key=lambda r: -(r["best"] if r["best"] is not None else -9))

from datetime import timedelta


def espn_games(league, start, end):
    cfg = LEAGUES[league]
    url = ESPN_SITE.format(sport=cfg["espn_sport"], lg=cfg["espn"]) + "/scoreboard"
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    errors = []

    def one_day(d):
        for extra in ({**cfg["extra"], "limit": 300}, cfg["extra"], {}):
            try:
                return _get(url, dates=f"{d:%Y%m%d}", **extra).get("events", [])
            except Exception as e:
                errors.append(str(e))
        return []

    with ThreadPoolExecutor(6) as ex:
        events = [e for chunk in ex.map(one_day, days) for e in chunk]
    if not events and errors:
        raise RuntimeError(errors[-1])
    games, seen = [], set()
    for e in events:
        if e["id"] in seen or e.get("status", {}).get("type", {}).get("state") != "pre":
            continue
        seen.add(e["id"])
        sides = {c["homeAway"]: c["team"] for c in e["competitions"][0]["competitors"]}
        if "home" in sides and "away" in sides:
            games.append(dict(id=e["id"], date=e.get("date", ""), home=sides["home"], away=sides["away"]))
    return games
