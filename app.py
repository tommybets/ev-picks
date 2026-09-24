import pandas as pd
import requests
import streamlit as st
from ev_picks import fetch, find_picks, log_picks

st.set_page_config(page_title="EV Picks", page_icon="🎯", layout="centered")
st.title("🎯 Daily +EV Picks")

try:
    default_key = st.secrets.get("ODDS_API_KEY", "")
except Exception:
    default_key = ""

SPORTS = {"NBA": "basketball_nba", "NFL": "americanfootball_nfl", "MLB": "baseball_mlb",
          "NHL": "icehockey_nhl", "NCAAF": "americanfootball_ncaaf",
          "NCAAB": "basketball_ncaab", "EPL": "soccer_epl", "MLS": "soccer_usa_mls"}
BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "espnbet", "fanatics"]

with st.sidebar:
    st.header("Settings")
    key = st.text_input("Odds API key", value=default_key, type="password")
    sports = st.multiselect("Sports", list(SPORTS), default=["NBA"])
    markets = st.multiselect("Markets", ["h2h", "spreads", "totals"], default=["h2h", "spreads", "totals"])
    books = st.multiselect("My books", BOOKS, default=BOOKS[:4])
    min_edge = st.slider("Min edge (EV)", 0.01, 0.10, 0.03, 0.005, format="%.3f")
    kelly_frac = st.slider("Kelly fraction", 0.05, 0.50, 0.25, 0.05)
    bankroll = st.number_input("Bankroll ($)", 50.0, 1_000_000.0, 1000.0, 50.0)
    max_pct = st.slider("Max stake % of bankroll", 0.5, 10.0, 3.0, 0.5)

if st.button("Find picks", type="primary", use_container_width=True):
    if not key or not sports or not books:
        st.warning("Add your API key, at least one sport, and at least one book.")
    else:
        picks = []
        with st.spinner("Pulling odds..."):
            for s in sports:
                try:
                    events = fetch(SPORTS[s], markets, "us,eu", key)
                    picks += find_picks(events, SPORTS[s], min_edge, kelly_frac, bankroll, set(books))
                except requests.HTTPError as e:
                    st.error(f"{s}: {e}")
        cap = bankroll * max_pct / 100
        for p in picks:
            p["stake"] = round(min(p["stake"], cap), 2)
        st.session_state["picks"] = sorted(picks, key=lambda x: -x["ev"])

picks = st.session_state.get("picks")
if picks is not None:
    if not picks:
        st.info("No +EV picks meet your threshold right now.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Picks", len(picks))
        c2.metric("Avg EV", f"{sum(p['ev'] for p in picks) / len(picks) * 100:.1f}%")
        c3.metric("Total stake", f"${sum(p['stake'] for p in picks):,.0f}")

        placed = []
        for i, p in enumerate(picks):
            with st.container(border=True):
                label = f"{p['pick']} {p['line']}".strip()
                st.markdown(f"**{p['game']}**  \n{label} · {p['market']}")
                st.markdown(f"**{p['book']}** @ **{p['odds']:.2f}** · EV **{p['ev']*100:.1f}%** · stake **${p['stake']:.2f}**")
                st.caption(f"Fair prob {p['fair_prob']*100:.1f}% · {p['time']}")
                if st.checkbox("I placed this bet", key=f"placed_{i}"):
                    placed.append(p)

        if st.button(f"Log {len(placed)} placed bets", disabled=not placed, use_container_width=True):
            log_picks(placed)
            st.success("Saved to picks_log.csv")

        st.download_button("Download all picks (CSV)", pd.DataFrame(picks).to_csv(index=False),
                           "picks.csv", "text/csv", use_container_width=True)
