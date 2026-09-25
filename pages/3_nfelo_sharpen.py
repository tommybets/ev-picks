import pandas as pd
import streamlit as st

import nfelo_edges as ne
from ev_picks import fetch

st.set_page_config(page_title="nfelo Sharpen", page_icon="🧠", layout="centered")
st.title("🧠 Sharpen with nfelo")
st.caption("Pulls nfelo's power ratings and its own +EV betting card from nfeloapp.com, "
           "then checks whether nfelo agrees with today's sportsbook-derived picks. "
           "Please cite nfeloapp.com if you share this analysis (their request, not ours).")


@st.cache_data(ttl=1800, show_spinner=False)
def load_power():
    return ne.power_ratings()


@st.cache_data(ttl=1800, show_spinner=False)
def load_nfelo_picks():
    return ne.ev_bets()


@st.cache_data(ttl=600, show_spinner=False)
def load_book_odds(key):
    return fetch("americanfootball_nfl", ["spreads"], "us", key)


key = st.text_input("Odds API key (for sportsbook spreads)", type="password")

if st.button("Run comparison", type="primary", use_container_width=True):
    with st.spinner("Pulling nfelo power ratings..."):
        try:
            power = load_power()
        except Exception as e:
            power = {}
            st.error(f"nfelo power ratings failed: {e}")
    with st.spinner("Pulling nfelo's own +EV betting card..."):
        try:
            nfelo_picks = load_nfelo_picks()
        except Exception as e:
            nfelo_picks = []
            st.warning(f"nfelo betting card failed: {e}")
    rows = []
    if key:
        with st.spinner("Pulling sportsbook spreads..."):
            try:
                events = load_book_odds(key)
            except Exception as e:
                events = []
                st.error(f"Odds API failed: {e}")
        for ev in events:
            home, away = ev["home_team"], ev["away_team"]
            he = next((v for k, v in power.items() if k in home.upper() or home.upper().endswith(k)), None)
            ae = next((v for k, v in power.items() if k in away.upper() or away.upper().endswith(k)), None)
            if he is None or ae is None:
                continue
            model_home_wp = ne.elo_win_prob(he, ae)
            for bk in ev.get("bookmakers", [])[:1]:
                for m in bk.get("markets", []):
                    if m["key"] != "spreads":
                        continue
                    for o in m["outcomes"]:
                        is_home = o["name"] == home
                        book_wp_side = model_home_wp if is_home else 1 - model_home_wp
                        nfelo_flag = ne.match_pick_to_nfelo(o["name"], nfelo_picks)
                        rows.append(dict(
                            game=f"{away} @ {home}", team=o["name"],
                            book_line=o.get("point"), model_wp=round(book_wp_side, 3),
                            nfelo_ev_bets_agrees="✅" if nfelo_flag else "—",
                            nfelo_flagged_spread=nfelo_flag["spread"] if nfelo_flag else None,
                        ))
    st.session_state["nfelo_rows"] = rows
    st.session_state["nfelo_meta"] = (len(power), len(nfelo_picks))

if "nfelo_rows" in st.session_state:
    n_power, n_picks = st.session_state["nfelo_meta"]
    c1, c2 = st.columns(2)
    c1.metric("Teams rated", n_power)
    c2.metric("nfelo +EV picks found", n_picks)

    rows = st.session_state["nfelo_rows"]
    if not rows:
        st.info("No comparable games yet, or add your Odds API key above.")
    else:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        agree = df[df["nfelo_ev_bets_agrees"] == "✅"]
        if not agree.empty:
            st.success(f"{len(agree)} side(s) where nfelo's own betting card agrees with the matchup.")

    if st.session_state["nfelo_meta"][1] == 0:
        st.caption("nfelo's betting card returned nothing parseable — their page layout may have changed. "
                   "The power-rating win probabilities above still work independently.")
