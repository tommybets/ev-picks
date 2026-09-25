import pandas as pd
import streamlit as st

import injury_edges as ie
import nfelo_edges as ne
from ev_picks import fetch

st.set_page_config(page_title="nfelo Sharpen", page_icon="🧠", layout="centered")
st.title("🧠 Sharpen with Elo")
st.caption("nfeloapp.com doesn't publish a public API and its pages are rendered by "
           "JavaScript, so this instead computes its own Elo-style power ratings directly "
           "from official game results — the same public data nfelo itself is built on, "
           "using Elo + margin-of-victory + season regression, then layers on QB/skill-"
           "position injury status and rest days, the two biggest situational line-movers. "
           "These are independent, rule-of-thumb estimates, not nfelo's exact numbers.")


@st.cache_data(ttl=1800, show_spinner=False)
def load_power():
    return ne.power_ratings()


@st.cache_data(ttl=1800, show_spinner=False)
def load_games():
    return ne.fetch_games()


@st.cache_data(ttl=900, show_spinner=False)
def load_injuries():
    return ie.all_injuries()


@st.cache_data(ttl=600, show_spinner=False)
def load_book_odds(key):
    return fetch("americanfootball_nfl", ["spreads"], "us", key)


key = st.text_input("Odds API key (for sportsbook spreads)", type="password")

if st.button("Run comparison", type="primary", use_container_width=True):
    with st.spinner("Computing Elo power ratings from game results..."):
        try:
            power = load_power()
        except Exception as e:
            power = {}
            st.error(f"Power ratings failed: {e}")
    with st.spinner("Pulling injury reports..."):
        try:
            injuries = load_injuries()
        except Exception as e:
            injuries = {}
            st.warning(f"Injury reports failed: {e}")
    with st.spinner("Loading game schedule for rest-day calc..."):
        try:
            games_df = load_games()
        except Exception as e:
            games_df = None
            st.warning(f"Schedule load failed (rest-day adjustment skipped): {e}")

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
            home_abbr = next((k for k in power if k in home.upper() or home.upper().endswith(k)), None)
            away_abbr = next((k for k in power if k in away.upper() or away.upper().endswith(k)), None)
            if home_abbr is None or away_abbr is None:
                continue
            game_date = ev.get("commence_time", "")[:10]

            base_home_wp = ne.elo_win_prob(power[home_abbr], power[away_abbr])
            if games_df is not None:
                adj_home, home_reasons = ie.adjusted_rating(
                    power[home_abbr], home_abbr, injuries, games_df, game_date)
                adj_away, away_reasons = ie.adjusted_rating(
                    power[away_abbr], away_abbr, injuries, games_df, game_date)
            else:
                adj_home, home_reasons = ie.adjusted_rating(power[home_abbr], home_abbr, injuries)
                adj_away, away_reasons = ie.adjusted_rating(power[away_abbr], away_abbr, injuries)
            adj_home_wp = ne.elo_win_prob(adj_home, adj_away)
            all_reasons = home_reasons + away_reasons

            for bk in ev.get("bookmakers", [])[:1]:
                for m in bk.get("markets", []):
                    if m["key"] != "spreads":
                        continue
                    for o in m["outcomes"]:
                        is_home = o["name"] == home
                        base_wp = base_home_wp if is_home else 1 - base_home_wp
                        adj_wp = adj_home_wp if is_home else 1 - adj_home_wp
                        rows.append(dict(
                            game=f"{away} @ {home}", team=o["name"],
                            book_line=o.get("point"),
                            base_wp=round(base_wp, 3), adjusted_wp=round(adj_wp, 3),
                            shift=round(adj_wp - base_wp, 3),
                            adjustments="; ".join(all_reasons) if all_reasons else "—",
                        ))
    st.session_state["nfelo_rows"] = rows
    st.session_state["nfelo_meta"] = (len(power), sum(len(v) for v in injuries.values()) if injuries else 0)

if "nfelo_rows" in st.session_state:
    n_power, n_injuries = st.session_state["nfelo_meta"]
    c1, c2 = st.columns(2)
    c1.metric("Teams rated", n_power)
    c2.metric("Injuries tracked", n_injuries)

    rows = st.session_state["nfelo_rows"]
    if not rows:
        st.info("No comparable games yet, or add your Odds API key above.")
    else:
        moved = sorted(rows, key=lambda r: -abs(r["shift"]))
        st.subheader("Biggest injury/rest swings")
        for r in moved[:6]:
            if abs(r["shift"]) < 0.005:
                continue
            with st.container(border=True):
                st.markdown(f"**{r['team']}** · {r['game']}")
                st.markdown(f"Base win prob **{r['base_wp']*100:.1f}%** → "
                            f"adjusted **{r['adjusted_wp']*100:.1f}%** "
                            f"({r['shift']*100:+.1f} pts)")
                if r["adjustments"] != "—":
                    st.caption(r["adjustments"])

        with st.expander("All games"):
            df = pd.DataFrame(rows)
            for c in ("base_wp", "adjusted_wp", "shift"):
                df[c] = (df[c] * 100).round(1)
            st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption("Injury and rest adjustments are rough, rule-of-thumb Elo-point estimates "
               "(~25 Elo ≈ 1 spread point) — useful for spotting which side of a line a "
               "report might swing, not a precise line prediction.")
