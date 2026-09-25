import pandas as pd
import streamlit as st

import results_db as db

st.set_page_config(page_title="Results Tracker", page_icon="📊", layout="centered")
st.title("📊 Results Tracker")
st.caption("Logs every bet you mark as placed, then tracks win rate, ROI, and closing "
           "line value (CLV) — whether your price beat what the market settled on. "
           "CLV is the standard way to tell if picks are well-timed, independent of "
           "whether any single bet happened to win.")

if not db.configured():
    st.warning(
        "Not connected to a database yet, so nothing will be saved. This page needs a "
        "free Supabase project — Streamlit's own storage resets every time the app "
        "restarts, so a real database is the only way history survives long-term."
    )
    with st.expander("One-time setup (about 5 minutes)"):
        st.markdown(
            "1. Go to **supabase.com**, sign up free, and create a new project.\n"
            "2. In the project, open **SQL Editor** and run:\n"
        )
        st.code(
            "create table bets (\n"
            "  id bigint generated always as identity primary key,\n"
            "  created_at timestamptz default now(),\n"
            "  game text, team text, market text, book text,\n"
            "  odds float8, stake float8, model_prob float8, source text, notes text,\n"
            "  closing_odds float8, result text default 'pending'\n"
            ");\n"
            "alter table bets disable row level security;",
            language="sql",
        )
        st.markdown(
            "3. Go to **Project Settings → API**. Copy the **Project URL** and the "
            "**anon public** key.\n"
            "4. In this app: **Manage app → ⋮ → Settings → Secrets**, and add:\n"
        )
        st.code('SUPABASE_URL = "https://your-project.supabase.co"\nSUPABASE_KEY = "your-anon-key"')
        st.markdown(
            "5. Reboot the app.\n\n"
            "Row-level security is disabled above for simplicity since this is a "
            "single-user personal tool — don't share the anon key or the app link "
            "publicly with this setup, since anyone with it could write to the table."
        )
    st.stop()

st.subheader("Log a bet")
with st.form("log_bet", clear_on_submit=True):
    c1, c2 = st.columns(2)
    game = c1.text_input("Game", placeholder="TB @ CIN")
    team = c2.text_input("Team / side", placeholder="Cincinnati Bengals -3.5")
    c3, c4 = st.columns(2)
    market = c3.selectbox("Market", ["spread", "h2h", "totals", "parlay", "other"])
    book = c4.text_input("Book", placeholder="DraftKings")
    c5, c6, c7 = st.columns(3)
    odds = c5.number_input("Decimal odds", min_value=1.01, value=1.91, step=0.01)
    stake = c6.number_input("Stake ($)", min_value=1.0, value=50.0, step=5.0)
    model_prob = c7.number_input("Model probability", min_value=0.0, max_value=1.0, value=0.55, step=0.01)
    source = st.selectbox("Found via", ["ev_picks", "fpi_markets", "nfelo_elo", "daily_parlay", "other"])
    notes = st.text_input("Notes (optional)")
    if st.form_submit_button("Log bet", type="primary", use_container_width=True):
        try:
            db.log_bet(game, team, market, book, odds, stake, model_prob, source, notes)
            st.success("Logged.")
            st.cache_data.clear()
        except Exception as e:
            st.error(f"Couldn't save: {e}")

try:
    bets = db.list_bets()
except Exception as e:
    st.error(f"Couldn't load bets: {e}")
    bets = []

if bets:
    stats = db.summary_stats(bets)
    st.subheader("Performance")
    c1, c2, c3 = st.columns(3)
    c1.metric("Record", f"{stats['wins']}-{stats['losses']}" if stats["n_settled"] else "—")
    c2.metric("ROI", f"{stats['roi']*100:+.1f}%" if stats["roi"] is not None else "—")
    c3.metric("Profit", f"${stats['profit']:+.2f}" if stats["n_settled"] else "—")
    c4, c5 = st.columns(2)
    c4.metric("Avg CLV", f"{stats['avg_clv']*100:+.2f}%" if stats["avg_clv"] is not None else "—",
               help="Positive = you beat the closing line on average — the strongest signal "
                    "of real edge, separate from whether bets happened to win.")
    c5.metric("Bets with CLV logged", stats["n_with_clv"])

    st.subheader("Bets")
    for b in bets:
        with st.container(border=True):
            st.markdown(f"**{b['team']}** · {b['game']} · {b['market']} @ {b['book']}")
            st.caption(f"Odds {b['odds']:.2f} · Stake ${b['stake']:.2f} · "
                       f"Model {b['model_prob']*100:.0f}% · via {b['source']}"
                       + (f" · {b['notes']}" if b.get("notes") else ""))
            cols = st.columns([2, 2, 1])
            new_result = cols[0].selectbox(
                "Result", db.RESULT_OPTIONS,
                index=db.RESULT_OPTIONS.index(b["result"]) if b["result"] in db.RESULT_OPTIONS else 0,
                key=f"result_{b['id']}", label_visibility="collapsed")
            new_closing = cols[1].number_input(
                "Closing odds", min_value=0.0, value=float(b["closing_odds"] or 0.0),
                step=0.01, key=f"closing_{b['id']}", label_visibility="collapsed")
            if cols[2].button("Save", key=f"save_{b['id']}"):
                try:
                    fields = {"result": new_result}
                    if new_closing > 0:
                        fields["closing_odds"] = new_closing
                    db.update_bet(b["id"], **fields)
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't update: {e}")
else:
    st.info("No bets logged yet — use the form above.")
