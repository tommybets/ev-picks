"""Shared, network-free parlay math and a plain-language grade: given each
leg's probability and price, compute the combined payout/EV and rate how
reasonable the bet actually is — not just whether EV is positive, but
whether the parlay has a realistic chance of ever paying out at all.
Used by both the FPI-picks parlay builder and the manual Parlay Grader page."""


def american_to_decimal(american):
    a = float(american)
    if a == 0:
        raise ValueError("American odds can't be 0")
    return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)


def decimal_to_american(decimal_odds):
    d = float(decimal_odds)
    if d <= 1:
        raise ValueError("Decimal odds must be > 1")
    return (d - 1) * 100 if d >= 2 else -100 / (d - 1)


def same_game_conflict(legs):
    """True if two or more legs share the same 'game' label — their real
    combined probability isn't just the product of the two (usually higher,
    since correlated outcomes tend to move together), so the parlay math
    below is an underestimate of true win probability for same-game legs."""
    games = [leg.get("game") for leg in legs if leg.get("game")]
    return len(games) != len(set(games))


def combine_parlay(legs, stake):
    """legs: [{'prob': 0-1, 'decimal_odds': >1, ...}, ...]. Assumes
    independent legs (see same_game_conflict for when that's questionable).
    Returns combined odds/probability, payout, profit, EV, and the edge
    (your probability vs. the book's implied probability, in probability
    points) that grade_parlay below turns into a letter grade."""
    combined_decimal, combined_prob, implied_prob = 1.0, 1.0, 1.0
    for leg in legs:
        combined_decimal *= leg["decimal_odds"]
        combined_prob *= leg["prob"]
        implied_prob *= 1.0 / leg["decimal_odds"]
    payout = stake * combined_decimal
    profit = payout - stake
    ev = combined_prob * payout - stake
    return dict(
        combined_decimal_odds=combined_decimal, combined_prob=combined_prob,
        implied_prob=implied_prob, payout=payout, profit=profit, ev=ev,
        ev_pct=(ev / stake) if stake else None,
        edge_pts=combined_prob - implied_prob,
    )


# (grade, headline, explanation) — checked top to bottom, first match wins.
_GRADE_RULES = [
    (lambda e, p: e >= 0.05 and p >= 0.15, "A",
     "Positive edge on a bet with a realistic chance to actually cash."),
    (lambda e, p: e >= 0.05, "B",
     "A real positive edge, but a long shot — expect this to lose more often "
     "than not even though the math favors it over many repeated bets."),
    (lambda e, p: e > -0.03 and p >= 0.15, "C",
     "Roughly fair value at this price — not a mistake, but not a real edge either."),
    (lambda e, p: e > -0.03, "C-",
     "Roughly fair price, but a low-probability parlay — more lottery ticket than value bet."),
    (lambda e, p: e > -0.10, "D",
     "The price is worse than your own probability estimate — a modest negative edge."),
]


def grade_parlay(edge_pts, combined_prob):
    """Returns (letter_grade, explanation)."""
    for test, letter, explanation in _GRADE_RULES:
        if test(edge_pts, combined_prob):
            return letter, explanation
    return "F", "Your own numbers say this parlay is priced well against you."
