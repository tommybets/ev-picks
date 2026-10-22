"""Auto-grader for the Results Tracker.

Two jobs, both safe to run as often as you like:
1. Closing prices: for pending bets that start within the next 45 minutes,
   record the current price of the same bet (same market, same book). That
   is the closing line used for CLV.
2. Results: for bets whose games have finished, read the final score from
   ESPN and mark each bet win / loss / push.

Runs from the Results Tracker page ("Check results now") and on a schedule
from GitHub Actions (.github/workflows/grade_bets.yml), which is what makes
closing prices get captured even when nobody has the app open.

Only bets logged with game details (league, teams, kickoff, side) can be
graded automatically. Bets typed in by hand stay manual."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import fpi_edges as fe
import results_db as db
from ev_picks import fetch as odds_api_fetch

ET = ZoneInfo("America/New_York")
CLOSING_WINDOW = timedelta(minutes=45)
ODDS_API_SPORT = {
    "NFL": "americanfootball_nfl", "College football": "americanfootball_ncaaf",
    "NBA": "basketball_nba", "NHL": "icehockey_nhl", "MLB": "baseball_mlb",
}
SPORT_TO_LEAGUE = {v: k for k, v in ODDS_API_SPORT.items()}
MONEYLINE = {"h2h", "prediction_market", "moneyline"}
SPREAD = {"spreads", "spread"}
TOTAL = {"totals", "total"}
VOID_WORDS = ("postponed", "canceled", "cancelled", "suspended", "forfeit")


def _dt(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _legs_of(bet):
    """A parlay's legs, or the bet itself as a single leg."""
    if bet.get("legs"):
        return bet["legs"]
    return [dict(league=bet.get("league"), home_team=bet.get("home_team"),
                 away_team=bet.get("away_team"), side_team=bet.get("side_team"),
                 kickoff=bet.get("kickoff"), market=bet.get("market"),
                 line=bet.get("line"), book=bet.get("book"),
                 decimal_odds=bet.get("odds"), closing_odds=bet.get("closing_odds"),
                 closing_line=bet.get("closing_line"))]


def _gradeable(leg):
    return all(leg.get(k) for k in ("league", "home_team", "away_team", "side_team", "kickoff"))


class Grader:
    def __init__(self, now=None):
        self.now = now or datetime.now(timezone.utc)
        self._events = {}
        self._kalshi = {}
        self._poly = {}
        self._odds = {}
        self.errors = []

    # ---------- finding the game ----------
    def _events_on(self, league, day):
        k = (league, day)
        if k not in self._events:
            try:
                self._events[k] = fe.espn_events(league, day, day)
            except Exception as e:
                self.errors.append(f"ESPN {league} {day}: {e}")
                self._events[k] = []
        return self._events[k]

    def find_game(self, leg):
        """The ESPN game matching both team names, nearest the logged kickoff
        (which keeps the two games of a doubleheader apart)."""
        league, ko = leg["league"], _dt(leg["kickoff"])
        if league not in fe.LEAGUES or ko is None:
            return None
        days = {ko.date(), ko.astimezone(ET).date()}
        best, best_gap = None, None
        for day in days:
            for g in self._events_on(league, day):
                if not (fe.score(fe.keys(g["home"]), leg["home_team"])
                        and fe.score(fe.keys(g["away"]), leg["away_team"])):
                    continue
                start = _dt(g["date"])
                gap = abs((start - ko).total_seconds()) if start else 0
                if gap > 8 * 3600:
                    continue
                if best is None or gap < best_gap:
                    best, best_gap = g, gap
        return best

    @staticmethod
    def side_of(leg, game):
        """'home', 'away', or None if the picked team can't be told apart."""
        h = fe.score(fe.keys(game["home"]), leg["side_team"])
        a = fe.score(fe.keys(game["away"]), leg["side_team"])
        if h == a:
            return None
        return "home" if h > a else "away"

    # ---------- results ----------
    def grade_leg(self, leg):
        """'win' / 'loss' / 'push', or None if not finished or not gradeable."""
        if not _gradeable(leg):
            return None
        ko = _dt(leg["kickoff"])
        if ko is None or ko > self.now:
            return None
        game = self.find_game(leg)
        if game is None:
            return None
        status = (game.get("status") or "").lower()
        if any(w in status for w in VOID_WORDS):
            return "push"
        if game.get("state") != "post" or not game.get("completed"):
            return None
        hs, as_ = game.get("home_score"), game.get("away_score")
        if hs is None or as_ is None:
            return None

        market = (leg.get("market") or "").lower()
        if market in TOTAL:
            line = leg.get("line")
            if line is None:
                return None
            total, pick = hs + as_, leg["side_team"].lower()
            if total == line:
                return "push"
            over_hit = total > line
            if pick.startswith("over"):
                return "win" if over_hit else "loss"
            if pick.startswith("under"):
                return "loss" if over_hit else "win"
            return None

        side = self.side_of(leg, game)
        if side is None:
            return None
        mine, theirs = (hs, as_) if side == "home" else (as_, hs)
        if market in SPREAD:
            line = leg.get("line")
            if line is None:
                return None
            margin = mine + float(line) - theirs
        elif market in MONEYLINE:
            margin = mine - theirs
        else:
            return None
        if margin > 0:
            return "win"
        if margin < 0:
            return "loss"
        return "push"

    # ---------- closing prices ----------
    def _market_price(self, leg, game):
        league = leg["league"]
        side = self.side_of(leg, game)
        if side is None:
            return None
        book = (leg.get("book") or "").lower()
        if book == "kalshi":
            if league not in self._kalshi:
                try:
                    self._kalshi[league] = fe.kalshi_events(fe.LEAGUES[league]["kalshi"])
                except Exception as e:
                    self.errors.append(f"Kalshi {league}: {e}")
                    self._kalshi[league] = []
            price = (fe.kalshi_lookup(self._kalshi[league], game) or {}).get(side)
        else:
            if league not in self._poly:
                try:
                    self._poly[league] = fe.poly_candidates(league)[0]
                except Exception as e:
                    self.errors.append(f"Polymarket {league}: {e}")
                    self._poly[league] = []
            price = (fe.poly_lookup(self._poly[league], game) or {}).get(side)
        return 1.0 / price if price and 0 < price < 1 else None

    def _sportsbook_price(self, leg):
        """Returns (decimal_odds or None, current_line or None)."""
        api_key = db.secret("ODDS_API_KEY")
        sport = ODDS_API_SPORT.get(leg["league"])
        market = (leg.get("market") or "").lower()
        mkey = "h2h" if market in MONEYLINE else "spreads" if market in SPREAD else "totals" if market in TOTAL else None
        if not api_key or not sport or not mkey:
            return None, None
        k = (sport, mkey)
        if k not in self._odds:
            try:
                self._odds[k] = odds_api_fetch(sport, [mkey], "us,eu", api_key)
            except Exception as e:
                self.errors.append(f"Odds API {sport}: {e}")
                self._odds[k] = []
        want_home, want_away = leg["home_team"].lower(), leg["away_team"].lower()
        book = (leg.get("book") or "").lower()
        for ev in self._odds[k]:
            if ev.get("home_team", "").lower() != want_home or ev.get("away_team", "").lower() != want_away:
                continue
            for bk in ev.get("bookmakers", []):
                if book not in (bk.get("title", "").lower(), bk.get("key", "").lower()):
                    continue
                for m in bk.get("markets", []):
                    if m.get("key") != mkey:
                        continue
                    for o in m.get("outcomes", []):
                        if o.get("name", "").lower() == leg["side_team"].lower():
                            return o.get("price"), o.get("point")
        return None, None

    def closing_for_leg(self, leg):
        """Returns dict(closing_odds=..., closing_line=...) or None if this leg
        isn't inside the pre-game window or no current price was found."""
        if not _gradeable(leg) or leg.get("closing_odds") or leg.get("closing_line") is not None:
            return None
        ko = _dt(leg["kickoff"])
        if ko is None or not (self.now <= ko <= self.now + CLOSING_WINDOW):
            return None
        book = (leg.get("book") or "").lower()
        if book in ("kalshi", "polymarket"):
            game = self.find_game(leg)
            if game is None or game.get("state") != "pre":
                return None
            odds = self._market_price(leg, game)
            return dict(closing_odds=odds) if odds else None
        price, point = self._sportsbook_price(leg)
        if price is None:
            return None
        line = leg.get("line")
        if line is None or point is None or float(point) == float(line):
            return dict(closing_odds=price)
        # The line moved, so the price isn't comparable: keep only the new line.
        return dict(closing_line=point)

    # ---------- per bet ----------
    def process(self, bet):
        """Returns the fields to update on this bet ({} if nothing changed)."""
        legs = [dict(l) for l in _legs_of(bet)]
        is_parlay = bool(bet.get("legs"))
        update = {}

        # closing prices
        changed_legs = False
        for leg in legs:
            c = self.closing_for_leg(leg)
            if c:
                leg.update(c)
                changed_legs = True
        if changed_legs:
            if is_parlay:
                update["legs"] = legs
                if all(l.get("closing_odds") for l in legs):
                    prod = 1.0
                    for l in legs:
                        prod *= float(l["closing_odds"])
                    update["closing_odds"] = prod
            else:
                for f in ("closing_odds", "closing_line"):
                    if legs[0].get(f) is not None:
                        update[f] = legs[0][f]

        # results
        results = []
        for leg in legs:
            r = leg.get("result") if leg.get("result") in ("win", "loss", "push") else self.grade_leg(leg)
            if r and is_parlay and leg.get("result") != r:
                leg["result"] = r
                update["legs"] = legs
            results.append(r)

        if "loss" in results:
            final = "loss"
        elif None in results:
            final = None
        elif all(r == "push" for r in results):
            final = "push"
        else:
            final = "win"
            if "push" in results and is_parlay:
                # A pushed leg drops out of the parlay, so it pays less.
                new_odds = 1.0
                for leg, r in zip(legs, results):
                    if r == "win":
                        new_odds *= float(leg.get("decimal_odds") or 1)
                update["odds"] = round(new_odds, 4)
                update["notes"] = ((bet.get("notes") or "") +
                                   f" | leg pushed: odds reduced from {bet['odds']:.2f}").strip(" |")
        if final:
            update["result"] = final
            update["graded_at"] = self.now.isoformat()
        return update


def run(now=None):
    """Processes every pending bet once. Returns a summary dict."""
    g = Grader(now)
    summary = dict(checked=0, closings=0, graded=0, manual=0, errors=g.errors)
    for bet in db.pending_bets():
        summary["checked"] += 1
        if not any(_gradeable(l) for l in _legs_of(bet)):
            summary["manual"] += 1
            continue
        try:
            upd = g.process(bet)
        except Exception as e:
            g.errors.append(f"bet {bet.get('id')}: {e}")
            continue
        if not upd:
            continue
        try:
            db.update_bet(bet["id"], **upd)
        except Exception as e:
            g.errors.append(f"saving bet {bet.get('id')}: {e}")
            continue
        if "closing_odds" in upd or "closing_line" in upd:
            summary["closings"] += 1
        if "result" in upd:
            summary["graded"] += 1
    return summary


if __name__ == "__main__":
    if not db.configured():
        raise SystemExit("SUPABASE_URL / SUPABASE_KEY are not set.")
    s = run()
    print(f"checked {s['checked']} pending bets: {s['graded']} graded, "
          f"{s['closings']} closing prices saved, {s['manual']} need manual grading")
    for e in s["errors"]:
        print("warning:", e)
