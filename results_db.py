"""Persistent bet tracking via Supabase (a free hosted Postgres database),
using its REST API directly with `requests` — no extra DB driver needed.
Streamlit Cloud's local disk resets on every restart/redeploy, so this is
what actually gives you history that survives across days and app reboots."""
import requests
import streamlit as st

RESULT_OPTIONS = ["pending", "win", "loss", "push"]


def _creds():
    try:
        url = st.secrets["SUPABASE_URL"].rstrip("/")
        key = st.secrets["SUPABASE_KEY"]
        return url, key
    except (KeyError, FileNotFoundError):
        return None, None


def configured():
    url, key = _creds()
    return bool(url and key)


def _headers(key, prefer=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def implied_prob(decimal_odds):
    return 1.0 / decimal_odds if decimal_odds else None


def clv(bet_odds, closing_odds):
    """Positive CLV = you got a better price than the closing line, which
    is the standard sign that a bet was well-timed/well-found, independent
    of whether it ultimately won."""
    p_bet, p_close = implied_prob(bet_odds), implied_prob(closing_odds)
    if p_bet is None or p_close is None:
        return None
    return p_close - p_bet  # book's fair-price view moved away from your price


def log_bet(game, team, market, book, odds, stake, model_prob, source, notes=""):
    """Inserts one bet. Call this whenever a pick from any page gets placed."""
    url, key = _creds()
    if not url:
        return None
    row = dict(game=game, team=team, market=market, book=book, odds=odds,
               stake=stake, model_prob=model_prob, source=source, notes=notes,
               result="pending")
    r = requests.post(f"{url}/rest/v1/bets", json=[row],
                       headers=_headers(key, prefer="return=representation"), timeout=20)
    r.raise_for_status()
    return r.json()[0]


def list_bets(limit=200):
    url, key = _creds()
    if not url:
        return []
    r = requests.get(f"{url}/rest/v1/bets?order=created_at.desc&limit={limit}",
                      headers=_headers(key), timeout=20)
    r.raise_for_status()
    return r.json()


def update_bet(bet_id, **fields):
    """Patches one bet — used to record closing odds or a final result."""
    url, key = _creds()
    if not url:
        return None
    r = requests.patch(f"{url}/rest/v1/bets?id=eq.{bet_id}", json=fields,
                       headers=_headers(key, prefer="return=representation"), timeout=20)
    r.raise_for_status()
    out = r.json()
    return out[0] if out else None


def settle_profit(odds, stake, result):
    if result == "win":
        return stake * (odds - 1)
    if result == "loss":
        return -stake
    return 0.0  # push or pending


def summary_stats(bets):
    settled = [b for b in bets if b.get("result") in ("win", "loss", "push")]
    wins = sum(1 for b in settled if b["result"] == "win")
    losses = sum(1 for b in settled if b["result"] == "loss")
    staked = sum(b["stake"] for b in settled)
    profit = sum(settle_profit(b["odds"], b["stake"], b["result"]) for b in settled)
    clv_vals = [clv(b["odds"], b["closing_odds"]) for b in bets if b.get("closing_odds")]
    clv_vals = [c for c in clv_vals if c is not None]
    return dict(
        n_settled=len(settled), wins=wins, losses=losses,
        win_rate=wins / len(settled) if settled else None,
        staked=staked, profit=profit,
        roi=profit / staked if staked else None,
        avg_clv=sum(clv_vals) / len(clv_vals) if clv_vals else None,
        n_with_clv=len(clv_vals),
    )
