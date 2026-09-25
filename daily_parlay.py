"""Build a 2-leg parlay from the day's best FPI-vs-market edges (Kalshi/Polymarket).
If no games are on today's slate, rolls forward to the next day that has one."""
from datetime import date, timedelta

import fpi_edges as fe


def find_daily_slate(league="NFL", max_days=10, min_priced=2):
    """Returns (date, priced_rows) for the earliest upcoming day with at least
    `min_priced` games that have a matched Kalshi or Polymarket price."""
    for offset in range(max_days):
        d = date.today() + timedelta(days=offset)
        try:
            games = fe.espn_games(league, d, d)
        except Exception:
            games = []
        if not games:
            continue
        fpi = fe.fpi_map(league, games)
        if not any(v is not None for v in fpi.values()):
            continue
        try:
            kal = fe.kalshi_events(fe.LEAGUES[league]["kalshi"])
        except Exception:
            kal = []
        try:
            poly, _ = fe.poly_candidates(league)
        except Exception:
            poly = []
        rows = fe.build_rows(games, fpi, kal, poly)
        priced = [r for r in rows if r["kalshi_ask"] or r["poly_price"]]
        if len(priced) >= min_priced:
            return d, priced
    return None, []


def pick_best_two(priced_rows):
    """Top 2 edges, each from a different game (two sides of one game are
    correlated, so that wouldn't be a real parlay)."""
    ranked = sorted((r for r in priced_rows if r["best"] is not None), key=lambda r: -r["best"])
    chosen, seen_games = [], set()
    for r in ranked:
        if r["game"] in seen_games:
            continue
        chosen.append(r)
        seen_games.add(r["game"])
        if len(chosen) == 2:
            break
    return chosen


def leg_price(row):
    """The market price that produced this row's best edge."""
    k, p = row["kalshi_edge"], row["poly_edge"]
    if k is not None and (p is None or k >= p):
        return row["kalshi_ask"], "Kalshi"
    return row["poly_price"], "Polymarket"


def build_parlay(stake=100.0, league="NFL"):
    """Returns None if no slate with 2+ priced edges is found within max_days,
    else a dict with both legs, combined odds, payout, profit, and EV.
    EV assumes leg outcomes are independent (different games) and uses each
    leg's FPI probability as the 'fair' probability."""
    slate_date, priced = find_daily_slate(league)
    if not priced:
        return None
    legs = pick_best_two(priced)
    if len(legs) < 2:
        return None

    combined_decimal, combined_fair_prob, leg_info = 1.0, 1.0, []
    for r in legs:
        price, book = leg_price(r)
        decimal_odds = 1.0 / price
        combined_decimal *= decimal_odds
        combined_fair_prob *= r["fpi"]
        leg_info.append(dict(
            game=r["game"], team=r["team"], kickoff=r["kickoff"], book=book,
            price=price, decimal_odds=decimal_odds, fpi=r["fpi"], edge=r["best"]))

    payout = stake * combined_decimal
    profit = payout - stake
    ev = combined_fair_prob * payout - stake
    return dict(
        league=league, date=slate_date.isoformat(), legs=leg_info, stake=stake,
        combined_decimal_odds=combined_decimal, payout=payout, profit=profit,
        combined_fair_prob=combined_fair_prob, ev=ev, ev_pct=ev / stake)
