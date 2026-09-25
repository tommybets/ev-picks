"""NHL win probabilities from MoneyPuck's public predictions page, used in
place of ESPN's predictor (ESPN doesn't publish a BPI/FPI-style win
probability for hockey — verified via direct check). MoneyPuck's site is a
simple, mostly-static page rather than a JS-rendered app, so a plain HTTP
fetch should see the real content, unlike nfeloapp.com."""
import io
import re

import pandas as pd
import requests

PRED_URL = "https://moneypuck.com/predictions.htm"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TailwindBets/1.0)"}

# MoneyPuck occasionally uses a different 2-3 letter code than ESPN for a
# handful of teams; map theirs -> ESPN's so lookups line up.
ALIASES = {"SJS": "SJ", "TBL": "TB", "LAK": "LA", "NJD": "NJ"}


def _fetch():
    r = requests.get(PRED_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def _visible_text(html):
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def _norm(abbr):
    a = re.sub(r"[^A-Za-z]", "", str(abbr)).upper()
    return ALIASES.get(a, a)


def _to_prob(x):
    try:
        v = float(str(x).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    if v > 1:
        v /= 100
    return v if 0 <= v <= 1 else None


def team_win_probs():
    """Returns {team_abbr: win_prob} for today's NHL slate. Tries an HTML
    table first, then a plain-text pattern match, so a moderate layout
    change on MoneyPuck's end still has a chance of working. Returns {} —
    never raises — if neither strategy finds anything, since this is a
    secondary data source."""
    try:
        html = _fetch()
    except Exception:
        return {}

    # Strategy 1: a real <table> with team + win% columns
    try:
        for df in pd.read_html(io.StringIO(html)):
            cols = {str(c).lower(): c for c in df.columns}
            team_col = next((cols[c] for c in cols if "team" in c), None)
            win_col = next((cols[c] for c in cols if "win" in c), None)
            if team_col is None or win_col is None:
                continue
            out = {}
            for _, row in df.iterrows():
                team = _norm(row[team_col])
                p = _to_prob(row[win_col])
                if team and p is not None:
                    out[team] = p
            if len(out) >= 2:
                return out
    except Exception:
        pass

    # Strategy 2: plain text fallback. Bound each team code's search window
    # at the START of the NEXT team code (not a fixed character count) —
    # a fixed-width window can steal a neighboring team's percentage when
    # codes sit close together in text (e.g. "TOR ... SJS win prob 62%"
    # would wrongly match TOR to SJS's number with a naive fixed window).
    text = _visible_text(html)
    code_matches = list(re.finditer(r"\b[A-Z]{2,3}\b", text))
    out = {}
    for i, cm in enumerate(code_matches):
        window_end = code_matches[i + 1].start() if i + 1 < len(code_matches) else len(text)
        segment = text[cm.end():window_end]
        if len(segment) > 60:  # too far from any number to plausibly belong to this code
            continue
        pm = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", segment)
        if not pm:
            continue
        team, pct = _norm(cm.group(0)), _to_prob(pm.group(1))
        if team and pct is not None and 0.01 < pct < 0.99:
            out.setdefault(team, pct)
    return out
