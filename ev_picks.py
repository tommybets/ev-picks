import argparse, csv, os, sys
from collections import defaultdict
from datetime import datetime, timezone
import requests

API = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
SHARP_BOOKS = {"pinnacle": 3.0}
MAX_EDGE = 0.15

def devig(prices):
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
    return max(0.0, ((b * p - (1 - p)) / b) * frac)

def fetch(sport, markets, regions, key):
    r = requests.get(API.format(sport=sport),
        params=dict(apiKey=key, regions=regions, markets=",".join(markets),
                    oddsFormat="decimal", dateFormat="iso"), timeout=30)
    r.raise_for_status()
    print(f"[{sport}] credits left: {r.headers.get('x-requests-remaining')}", file=sys.stderr)
    return r.json()

def fair_probs(event):
    acc = defaultdict(lambda: [0.0, 0.0])
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
    return {k: s / w for k, (s, w) in acc.items() if w >= 3}

def find_picks(events, sport, min_edge, kelly_frac, bankroll, allowed):
    picks = []
    for ev in events:
        fair = fair_probs(ev)
        for bk in ev["bookmakers"]:
            if allowed and bk["key"] not in allowed:
                continue
            if bk["key"] in SHARP_BOOKS and not allowed:
                continue
            for m in bk["markets"]:
                for o in m["outcomes"]:
                    p = fair.get((m["key"], o["name"], o.get("point")))
                    if p is None:
                        continue
                    e = p * o["price"] - 1
                    if not (min_edge <= e <= MAX_EDGE):
                        continue
                    stake = bankroll * kelly(p, o["price"], kelly_frac)
                    if stake <= 0:
                        continue
                    picks.append(dict(
                        time=ev["commence_time"], sport=sport,
                        game=f'{ev["away_team"]} @ {ev["home_team"]}',
                        market=m["key"], pick=o["name"], line=o.get("point", ""),
                        book=bk["title"], odds=o["price"], fair_prob=round(p, 4),
                        ev=round(e, 4), stake=round(stake, 2), event_id=ev["id"]))
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
