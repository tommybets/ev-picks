"""Player prop value finder.

Same method as the main EV picks, applied to player props: for every
player / stat / line, remove the vig from each book's Over-Under pair,
average those fair probabilities across books (sharp books weighted more)
into a consensus, then flag any book whose price beats that consensus.

Props are fetched one game at a time from The Odds API's event endpoint,
which costs (markets x regions) credits per game. Listing games is free."""
from collections import defaultdict

import requests

from ev_picks import devig

API = "https://api.the-odds-api.com/v4"
SHARP_BOOKS = {"pinnacle": 3.0, "betonlineag": 1.5}
MIN_BOOK_WEIGHT = 3.0   # need roughly 3 books' agreement before trusting a "fair" price
MAX_EDGE = 0.15         # bigger "edges" are usually a stale or mistaken line

SPORTS = {
    "NBA": "basketball_nba",
    "NFL": "americanfootball_nfl",
    "College football": "americanfootball_ncaaf",
    "MLB": "baseball_mlb",
    "NHL": "icehockey_nhl",
}

# Common Over/Under prop markets per sport (label -> Odds API key).
MARKETS = {
    "NBA": {
        "Points": "player_points", "Rebounds": "player_rebounds", "Assists": "player_assists",
        "Threes": "player_threes", "Pts+Reb+Ast": "player_points_rebounds_assists",
        "Pts+Reb": "player_points_rebounds", "Pts+Ast": "player_points_assists",
        "Blocks": "player_blocks", "Steals": "player_steals", "Turnovers": "player_turnovers",
    },
    "NFL": {
        "Passing yards": "player_pass_yds", "Rushing yards": "player_rush_yds",
        "Receiving yards": "player_reception_yds", "Receptions": "player_receptions",
        "Passing TDs": "player_pass_tds", "Pass attempts": "player_pass_attempts",
        "Completions": "player_pass_completions", "Interceptions": "player_pass_interceptions",
        "Rush attempts": "player_rush_attempts", "Rush+Rec yards": "player_rush_reception_yds",
    },
    "MLB": {
        "Pitcher strikeouts": "pitcher_strikeouts", "Hits": "batter_hits",
        "Total bases": "batter_total_bases", "Hits+Runs+RBIs": "batter_hits_runs_rbis",
        "RBIs": "batter_rbis", "Runs": "batter_runs_scored", "Pitcher outs": "pitcher_outs",
        "Hits allowed": "pitcher_hits_allowed", "Earned runs": "pitcher_earned_runs",
    },
    "NHL": {
        "Shots on goal": "player_shots_on_goal", "Points": "player_points",
        "Assists": "player_assists", "Goalie saves": "player_total_saves",
        "Blocked shots": "player_blocked_shots",
    },
}
MARKETS["College football"] = {k: v for k, v in MARKETS["NFL"].items()
                               if k in ("Passing yards", "Rushing yards", "Receiving yards",
                                        "Receptions", "Passing TDs")}
DEFAULT_MARKETS = {
    "NBA": ["Points", "Rebounds", "Assists"],
    "NFL": ["Passing yards", "Rushing yards", "Receiving yards"],
    "College football": ["Passing yards", "Rushing yards", "Receiving yards"],
    "MLB": ["Pitcher strikeouts", "Total bases", "Hits"],
    "NHL": ["Shots on goal", "Points"],
}


def list_events(sport_key, api_key):
    """Upcoming games for a sport. This endpoint costs no credits."""
    r = requests.get(f"{API}/sports/{sport_key}/events",
                     params=dict(apiKey=api_key, dateFormat="iso"), timeout=30)
    r.raise_for_status()
    return r.json()


def event_props(sport_key, event_id, markets, regions, api_key):
    """Returns (event_json, credits_remaining). Costs markets x regions credits."""
    r = requests.get(f"{API}/sports/{sport_key}/events/{event_id}/odds",
                     params=dict(apiKey=api_key, regions=regions, markets=",".join(markets),
                                 oddsFormat="decimal", dateFormat="iso"), timeout=30)
    r.raise_for_status()
    return r.json(), r.headers.get("x-requests-remaining")


def _pairs(event):
    """Yields (book_key, book_title, market_key, player, point, {side: price})
    for every book's complete Over/Under pair."""
    for bk in event.get("bookmakers", []):
        for m in bk.get("markets", []):
            grouped = defaultdict(dict)
            for o in m.get("outcomes", []):
                side = str(o.get("name", "")).title()
                player = o.get("description")
                if side not in ("Over", "Under") or not player or o.get("point") is None:
                    continue
                grouped[(player, float(o["point"]))][side] = float(o["price"])
            for (player, point), sides in grouped.items():
                if len(sides) == 2:
                    yield bk["key"], bk.get("title", bk["key"]), m["key"], player, point, sides


def consensus(event):
    """The model: {(market_key, player): [(line, fair P(over)), ...]} from the
    de-vigged, weighted average of every book's Over/Under at each line."""
    acc = defaultdict(lambda: [0.0, 0.0])
    for bkey, _, mkey, player, point, sides in _pairs(event):
        w = SHARP_BOOKS.get(bkey, 1.0)
        over_fair, _ = devig([sides["Over"], sides["Under"]])
        acc[(mkey, player, point)][0] += w * over_fair
        acc[(mkey, player, point)][1] += w
    out = defaultdict(list)
    for (mkey, player, point), (s, w) in acc.items():
        if w >= MIN_BOOK_WEIGHT:
            out[(mkey, player)].append((point, s / w))
    return {k: sorted(v) for k, v in out.items()}


def find_prop_edges(event, min_edge=0.03, max_edge=MAX_EDGE, allowed_books=None):
    """Returns +EV props for one game, best first."""
    acc = defaultdict(lambda: [0.0, 0.0])   # (market, player, point, side) -> [weighted fair sum, weight]
    pairs = list(_pairs(event))
    for bkey, _, mkey, player, point, sides in pairs:
        w = SHARP_BOOKS.get(bkey, 1.0)
        over_fair, under_fair = devig([sides["Over"], sides["Under"]])
        for side, p in (("Over", over_fair), ("Under", under_fair)):
            acc[(mkey, player, point, side)][0] += w * p
            acc[(mkey, player, point, side)][1] += w
    fair = {k: s / w for k, (s, w) in acc.items() if w >= MIN_BOOK_WEIGHT}
    n_books = defaultdict(int)
    for bkey, _, mkey, player, point, _ in pairs:
        n_books[(mkey, player, point)] += 1

    game = f'{event.get("away_team", "")} @ {event.get("home_team", "")}'
    out = []
    for bkey, title, mkey, player, point, sides in pairs:
        if allowed_books and bkey not in allowed_books:
            continue
        for side, price in sides.items():
            p = fair.get((mkey, player, point, side))
            if p is None:
                continue
            ev = p * price - 1
            if min_edge <= ev <= max_edge:
                out.append(dict(
                    game=game, home_team=event.get("home_team"), away_team=event.get("away_team"),
                    kickoff=event.get("commence_time"), market=mkey, player=player,
                    side=side, point=point, book=title, book_key=bkey, odds=price,
                    fair_prob=p, ev=ev, books=n_books[(mkey, player, point)]))
    return sorted(out, key=lambda x: -x["ev"])


def market_label(sport, mkey):
    for label, key in MARKETS.get(sport, {}).items():
        if key == mkey:
            return label
    return mkey.replace("player_", "").replace("batter_", "").replace("pitcher_", "pitcher ").replace("_", " ").title()


def to_american(decimal_odds):
    return (decimal_odds - 1) * 100 if decimal_odds >= 2 else -100 / (decimal_odds - 1)
