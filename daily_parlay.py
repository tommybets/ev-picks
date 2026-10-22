"""Build the day's optimal 2-leg parlay from FPI-vs-market edges
(Kalshi/Polymarket) across every sport the app tracks. If no games are on
today's slate, rolls forward to the next day that has one.

"Optimal" here means: among every pair of positive-edge picks from
different games, the pair with the highest expected value — but only
among pairs with a realistic chance to cash. Without that floor, the
highest-EV parlay is almost always two longshot underdogs that will
usually lose, which isn't a useful daily post."""
from datetime import date, timedelta
from itertools import combinations

import fpi_edges as fe
import parlay_grading as pg

# Every league the app knows about. Remove names here to exclude a sport.
DAILY_LEAGUES = tuple(fe.LEAGUES.keys())

# Combined win-probability floors, tried highest first.
PROB_TIERS = (0.30, 0.20, 0.0)

# Only the strongest edges are paired, which keeps the search fast on
# busy days (a big college football Saturday can have 100+ priced sides).
MAX_CANDIDATES = 25


def _as_tuple(leagues):
    return (leagues,) if isinstance(leagues, str) else tuple(leagues)


def find_daily_slate(leagues=DAILY_LEAGUES, max_days=10, min_priced=2):
    """Returns (date, priced_rows) for the earliest upcoming day where the
    selected leagues combined have at least `min_priced` games with a matched
    Kalshi or Polymarket price. Each row is tagged with its league. A league
    that errors (off-season, a data source down) is skipped, not fatal."""
    leagues = _as_tuple(leagues)

    markets = {}
    for lg in leagues:
        try:
            kal = fe.kalshi_events(fe.LEAGUES[lg]["kalshi"])
        except Exception:
            kal = []
        try:
            poly, _ = fe.poly_candidates(lg)
        except Exception:
            poly = []
        markets[lg] = (kal, poly)

    for offset in range(max_days):
        d = date.today() + timedelta(days=offset)
        priced = []
        for lg in leagues:
            kal, poly = markets[lg]
            if not kal and not poly:
                continue
            try:
                games = fe.espn_games(lg, d, d)
                if not games:
                    continue
                fpi = fe.fpi_map(lg, games)
                if not any(v is not None for v in fpi.values()):
                    continue
                for r in fe.build_rows(games, fpi, kal, poly, league=lg):
                    if r["kalshi_ask"] or r["poly_price"]:
                        r["league"] = lg
                        priced.append(r)
            except Exception:
                continue
        if len(priced) >= min_priced:
            return d, priced
    return None, []


def leg_price(row):
    """The market price that produced this row's best edge."""
    k, p = row["kalshi_edge"], row["poly_edge"]
    if k is not None and (p is None or k >= p):
        return row["kalshi_ask"], "Kalshi"
    return row["poly_price"], "Polymarket"


def _leg(row):
    price, book = leg_price(row)
    return dict(league=row.get("league", ""), game=row["game"], team=row["team"],
                kickoff=row["kickoff"], book=book, price=price,
                decimal_odds=1.0 / price, prob=row["fpi"], fpi=row["fpi"],
                edge=row["best"], home_name=row.get("home_name", ""),
                away_name=row.get("away_name", ""))


def grading_leg(leg):
    """The fields the auto-grader needs to settle one leg later."""
    return dict(league=leg.get("league"), home_team=leg.get("home_name"),
                away_team=leg.get("away_name"), side_team=leg.get("team"),
                kickoff=leg.get("kickoff"), market="h2h", book=leg.get("book"),
                decimal_odds=leg.get("decimal_odds"))


def pick_optimal_pair(priced_rows):
    """Returns (leg_a, leg_b, combined_result) for the best pair, or None."""
    cands = sorted((r for r in priced_rows if r["best"] is not None and r["best"] > 0),
                   key=lambda r: -r["best"])[:MAX_CANDIDATES]
    legs = []
    for r in cands:
        price, _ = leg_price(r)
        if price and 0 < price < 1:
            legs.append(_leg(r))

    pairs = []
    for a, b in combinations(legs, 2):
        if (a["league"], a["game"]) == (b["league"], b["game"]):
            continue  # two sides of one game are correlated, not a real parlay
        pairs.append((a, b, pg.combine_parlay([a, b], 1.0)))

    for floor in PROB_TIERS:
        pool = [p for p in pairs if p[2]["combined_prob"] >= floor]
        if pool:
            return max(pool, key=lambda p: p[2]["ev"])
    return None


def build_parlay(stake=100.0, leagues=DAILY_LEAGUES):
    """Returns None if no slate with 2+ priced positive edges is found,
    else a dict with both legs, combined odds, payout, profit, EV and grade.
    EV assumes the legs are independent and uses each leg's FPI (or
    MoneyPuck, for NHL) probability as the 'fair' probability."""
    slate_date, priced = find_daily_slate(leagues)
    if not priced:
        return None
    best = pick_optimal_pair(priced)
    if best is None:
        return None
    a, b, _ = best
    res = pg.combine_parlay([a, b], stake)
    grade, grade_note = pg.grade_parlay(res["edge_pts"], res["combined_prob"])
    return dict(
        leagues=sorted({a["league"], b["league"]}), date=slate_date.isoformat(),
        legs=[a, b], stake=stake,
        combined_decimal_odds=res["combined_decimal_odds"], payout=res["payout"],
        profit=res["profit"], combined_fair_prob=res["combined_prob"],
        ev=res["ev"], ev_pct=res["ev_pct"], grade=grade, grade_note=grade_note)
