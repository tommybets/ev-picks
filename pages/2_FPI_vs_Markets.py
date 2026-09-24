from datetime import date, timedelta

import pandas as pd
import streamlit as st

import fpi_edges as fe

st.set_page_config(page_title="FPI vs Markets", page_icon="📊", layout="centered")
st.title("📊 FPI vs Kalshi / Polymarket")
st.caption("Finds games where ESPN's FPI win probability differs from prediction-market prices. "
           "A gap is a lead to research, not a signal: markets often already price in news FPI hasn't.")


@st.cache_data(ttl=900, show_spinner=False)
def load_espn(league, days):
    games = fe.espn_games(league, date.today(), date.today() + timedelta(days=days))
    return games, fe.fpi_map(league, games)


@st.cache_data(ttl=120, show_spinner=False)
def load_kalshi(series):
    return fe.kalshi_events(series)


@st.cache_data(ttl=120, show_spinner=False)
def load_poly(league, series):
    return fe.poly_candidates(league, series or None)


league = st.selectbox("League", list(fe.LEAGUES))
days = st.slider("Games in next N days", 1, 14, 7)
min_gap = st.slider("Flag gaps of at least (points)", 2, 15, 5)
with st.expander("Advanced"):
    k_series = st.text_input("Kalshi series ticker", fe.LEAGUES[league]["kalshi"])
    p_series = st.text_input("Polymarket series id (blank = auto-detect)", "")

if st.button("Compare", type="primary", use_container_width=True):
    status = {}
    with st.spinner("Loading ESPN FPI..."):
        try:
            games, fpi = load_espn(league, days)
        except Exception as e:
            st.error(f"ESPN failed: {e}")
            st.stop()
    with st.spinner("Loading Kalshi + Polymarket..."):
        try:
            kal = load_kalshi(k_series)
        except Exception as e:
            kal = []
            st.warning(f"Kalshi failed: {e}")
        try:
            poly, used = load_poly(league, p_series)
            if used is None:
                st.warning("Couldn't auto-detect the Polymarket series id. Enter it under Advanced.")
        except Exception as e:
            poly = []
            st.warning(f"Polymarket failed: {e}")
    st.session_state["fpi_rows"] = fe.build_rows(games, fpi, kal, poly)
    st.session_state["fpi_meta"] = (len(games), sum(v is not None for v in fpi.values()), len(kal), len(poly))

rows = st.session_state.get("fpi_rows")
if rows is not None:
    n_games, n_fpi, n_kal, n_poly = st.session_state["fpi_meta"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Games", n_games)
    c2.metric("With FPI", n_fpi)
    c3.metric("Kalshi evts", n_kal)
    c4.metric("Poly mkts", n_poly)

    flagged = [r for r in rows if r["best"] is not None and r["best"] * 100 >= min_gap]
    if not flagged:
        st.info(f"No gaps of {min_gap}+ points right now.")
    for r in flagged:
        with st.container(border=True):
            st.markdown(f"**{r['team']}** · {r['game']}")
            st.markdown(f"FPI **{r['fpi']*100:.1f}%**")
            if r["kalshi_ask"]:
                st.markdown(f"Kalshi ask **{r['kalshi_ask']*100:.0f}¢** → net edge **{r['kalshi_edge']*100:+.1f} pts** (after est. fee)")
            if r["poly_price"]:
                st.markdown(f"Polymarket **{r['poly_price']*100:.0f}¢** → gap **{r['poly_edge']*100:+.1f} pts**")
            st.caption(r["kickoff"])

    with st.expander("All games"):
        df = pd.DataFrame(rows)
        if not df.empty:
            for c in ("fpi", "kalshi_ask", "kalshi_edge", "poly_price", "poly_edge", "best"):
                df[c] = (df[c] * 100).round(1)
            st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("Kalshi edge = FPI − ask − 7%·p·(1−p) fee. Polymarket price is the listed outcome price, "
               "not a live ask, and fees aren't included. Missing prices mean no market was matched.")
