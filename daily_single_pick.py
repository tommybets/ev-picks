"""Picks a single 'best bet of the day': among games with a positive FPI-
vs-market edge, prefers the side the model actually favors to win (not just
whatever has the biggest edge, which is often a risky underdog), then takes
the best EV among those favorites. Falls back to a lower confidence bar if
nothing clears the higher ones, and reuses the same slate-finding logic as
the daily parlay so both features look at the same day's games."""
import daily_parlay as dp

# Win-probability bars to try, highest first — "likely to win" first,
# only relaxed if nothing on the slate clears it.
CONFIDENCE_TIERS = [
    (0.65, "high confidence"),
    (0.60, "good confidence"),
    (0.55, "moderate confidence"),
    (0.52, "slight favorite"),
]


def best_single_pick(stake=100.0, league="NFL"):
    """Returns None if no slate with any positive-edge game is found, else
    a dict describing the single recommended pick."""
    slate_date, priced = dp.find_daily_slate(league, min_priced=1)
    if not priced:
        return None

    candidates = [r for r in priced if r["best"] is not None and r["best"] > 0]
    if not candidates:
        return None

    pick, tier_label = None, None
    for threshold, label in CONFIDENCE_TIERS:
        pool = [r for r in candidates if r["fpi"] >= threshold]
        if pool:
            pick = max(pool, key=lambda r: r["best"])
            tier_label = label
            break
    if pick is None:
        # Nothing clears even a bare-favorite bar — fall back to whichever
        # positive-edge side the model thinks is likeliest to win anyway.
        pick = max(candidates, key=lambda r: r["fpi"])
        tier_label = "best available"

    price, book = dp.leg_price(pick)
    decimal_odds = 1.0 / price
    payout = stake * decimal_odds
    profit = payout - stake
    ev = pick["fpi"] * payout - stake

    return dict(
        league=league, date=slate_date.isoformat(), game=pick["game"], team=pick["team"],
        kickoff=pick.get("kickoff"), book=book, price=price, decimal_odds=decimal_odds,
        model_wp=pick["fpi"], edge=pick["best"], confidence=tier_label,
        stake=stake, payout=payout, profit=profit, ev=ev, ev_pct=ev / stake,
    )
