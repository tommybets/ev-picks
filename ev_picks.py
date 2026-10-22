#!/usr/bin/env python3
"""
Daily +EV picks from major sportsbooks.

Method: build a "fair" probability for every outcome from the market itself
(de-vigged odds, sharp book weighted heavily), then flag any book whose
price beats that fair probability by a margin. Sized with fractional Kelly.

Setup:
    pip install requests pandas
    export ODDS_API_KEY=your_key      # free tier at https://the-odds-api.com

Run:
    python ev_picks.py --sports basketball_nba americanfootball_nfl \
        --markets h2h spreads totals --min-edge 0.03 --bankroll 1000

Bets are appended to picks_log.csv so you can later compute closing line
value (CLV), the honest test of whether the model has an edge.
"""
import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests

API = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
SHARP_BOOKS = {"pinnacle": 3.0}          # weight vs 1.0 for everyone else
MAX_EDGE = 0.15                          # bigger "edges" are usually stale/erroneous lines


# ---------- odds math ----------
def devig(prices):
    """Power-method de-vig: find k so sum((1/o)^k) == 1. Better than
    proportional scaling on longshots. Returns fair probabilities."""
    imp = [1.0 / p for p in prices]
    if abs(sum(imp) - 1) < 1e-9:
        return imp
    lo, hi = 0.5, 5.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(i ** k for i in imp) > 1:
            lo = k
        else:
            hi = k
    return [i ** k for i in imp]


def kelly(p, odds, frac):
    b = odds - 1
    f = (b * p - (1 - p)) / b
    return max(0.0, f * frac)


# ---------- data ----------
def fetch(sport, markets, regions, key):
    r = requests.get(
        API.format(sport=sport),
        params=dict(apiKey=key, regions=regions, markets=",".join(markets),
                    oddsFormat="decimal", dateFormat="iso"),
        timeout=30,
    )
    r.raise_for_status()
    print(f"[{sport}] credits remaining: {r.headers.get('x-requests-remaining')}",
          file=sys.stderr)
    return r.json()


# ---------- model ----------
def fair_probs(event):
    """Weighted consensus fair probability per (market, outcome, point)."""
    acc = defaultdict(lambda: [0.0, 0.0])   # key -> [weighted_sum, weight]
    for bk in event["bookmakers"]:
        w = SHARP_BOOKS.get(bk["key"], 1.0)
        for m in bk["markets"]:
            outs = m["outcomes"]
            if len(outs) < 2:
                continue
            probs = devig([o["price"] for o in outs])
            for o, p in zip(outs, probs):
                k = (m["key"], o["name"], o.get("point"))
                acc[k][0] += w * p
                acc[k][1] += w
    # require at least 3 books' worth of weight so one bad line can't set "truth"
    return {k: s / w for k, (s, w) in acc.items() if w >= 3}


def find_picks(events, sport, min_edge, kelly_frac, bankroll, allowed_books):
    picks = []
    for ev in events:
        fair = fair_probs(ev)
        for bk in ev["bookmakers"]:
            if allowed_books and bk["key"] not in allowed_books:
                continue
            if bk["key"] in SHARP_BOOKS and not allowed_books:
                continue                      # don't bet into the reference book
            for m in bk["markets"]:
                for o in m["outcomes"]:
                    k = (m["key"], o["name"], o.get("point"))
                    p = fair.get(k)
                    if p is None:
                        continue
                    ev_pct = p * o["price"] - 1
                    if not (min_edge <= ev_pct <= MAX_EDGE):
                        continue
                    stake = bankroll * kelly(p, o["price"], kelly_frac)
                    if stake <= 0:
                        continue
                    picks.append(dict(
                        time=ev["commence_time"], sport=sport,
                        game=f'{ev["away_team"]} @ {ev["home_team"]}',
                        market=m["key"], pick=o["name"], line=o.get("point", ""),
                        book=bk["title"], odds=o["price"], fair_prob=round(p, 4),
                        ev=round(ev_pct, 4), stake=round(stake, 2),
                        event_id=ev["id"],
                        home_team=ev["home_team"], away_team=ev["away_team"],
                    ))
    return sorted(picks, key=lambda x: -x["ev"])


def log_picks(picks, path="picks_log.csv"):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(picks[0].keys()) + ["logged_at", "closing_odds"])
        if new:
            w.writeheader()
        now = datetime.now(timezone.utc).isoformat()
        for p in picks:
            w.writerow({**p, "logged_at": now, "closing_odds": ""})


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", nargs="+", default=["basketball_nba"])
    ap.add_argument("--markets", nargs="+", default=["h2h", "spreads", "totals"])
    ap.add_argument("--regions", default="us,eu")   # eu needed for Pinnacle
    ap.add_argument("--books", nargs="*", default=["draftkings", "fanduel", "betmgm", "caesars"],
                    help="books you can actually bet at")
    ap.add_argument("--min-edge", type=float, default=0.03)
    ap.add_argument("--kelly", type=float, default=0.25)
    ap.add_argument("--bankroll", type=float, default=1000)
    ap.add_argument("--max-stake-pct", type=float, default=0.03)
    args = ap.parse_args()

    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("Set ODDS_API_KEY first.")

    all_picks = []
    for sport in args.sports:
        events = fetch(sport, args.markets, args.regions, key)
        all_picks += find_picks(events, sport, args.min_edge, args.kelly,
                                args.bankroll, set(args.books))

    cap = args.bankroll * args.max_stake_pct
    for p in all_picks:
        p["stake"] = round(min(p["stake"], cap), 2)

    if not all_picks:
        print("No +EV picks meet the threshold today.")
        return
    print(f"{'GAME':38} {'PICK':22} {'BOOK':11} {'ODDS':>6} {'EV':>6} {'STAKE':>7}")
    for p in all_picks:
        label = f'{p["pick"]} {p["line"]}'.strip()
        print(f'{p["game"][:37]:38} {label[:21]:22} {p["book"][:10]:11} '
              f'{p["odds"]:6.2f} {p["ev"]*100:5.1f}% {p["stake"]:7.2f}')
    log_picks(all_picks)
    print(f"\nLogged {len(all_picks)} picks to picks_log.csv")


if __name__ == "__main__":
    main()
