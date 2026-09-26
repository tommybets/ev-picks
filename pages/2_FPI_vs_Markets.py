from datetime import date, timedelta

import pandas as pd
import streamlit as st

import daily_parlay as dp
import fpi_edges as fe
import parlay_grading as pg
import results_db as db

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
            st.caption(fe.format_kickoff(r["kickoff"]))

            # Log using whichever market produced the flagged edge
            use_kalshi = r["kalshi_edge"] is not None and (
                r["poly_edge"] is None or r["kalshi_edge"] >= r["poly_edge"])
            price = r["kalshi_ask"] if use_kalshi else r["poly_price"]
            book_name = "Kalshi" if use_kalshi else "Polymarket"
            if db.configured() and price:
                if st.button("📌 Log this pick", key=f"log_{r['game']}_{r['team']}"):
                    try:
                        db.log_bet(
                            game=r["game"], team=r["team"], market="prediction_market",
                            book=book_name, odds=1.0 / price, stake=50.0,
                            model_prob=r["fpi"], source="fpi_markets",
                            notes=f"gap {r['best']*100:+.1f} pts",
                        )
                        st.success("Logged to tracker.")
                    except Exception as e:
                        st.error(f"Couldn't log: {e}")

    st.subheader("🧩 Parlay these picks")
    st.caption("Combine 2+ flagged picks above into one parlay with an estimated payout, "
               "EV, and a plain-language grade of how reasonable the bet actually is.")
    if len(flagged) < 2:
        st.info("Need at least 2 flagged picks above to build a parlay.")
    else:
        options = {f"{r['team']} ({r['game']}) · edge {r['best']*100:+.1f}pts": i
                   for i, r in enumerate(flagged)}
        chosen_labels = st.multiselect("Pick legs", list(options.keys()), key="fpi_parlay_legs")
        parlay_stake = st.number_input("Stake ($)", min_value=1.0, value=50.0, step=5.0,
                                       key="fpi_parlay_stake")
        if st.button("Build & grade parlay", disabled=len(chosen_labels) < 2,
                    use_container_width=True):
            chosen_rows = [flagged[options[l]] for l in chosen_labels]
            legs = []
            for r in chosen_rows:
                price, book = dp.leg_price(r)
                legs.append(dict(prob=r["fpi"], decimal_odds=1.0 / price,
                                 game=r["game"], team=r["team"], book=book, price=price))
            result = pg.combine_parlay(legs, parlay_stake)
            letter, note = pg.grade_parlay(result["edge_pts"], result["combined_prob"])
            st.session_state["fpi_parlay_result"] = dict(
                legs=legs, stake=parlay_stake, grade=letter, grade_note=note, **result)

        pr = st.session_state.get("fpi_parlay_result")
        if pr:
            with st.container(border=True):
                st.markdown(f"### Grade: {pr['grade']}")
                st.caption(pr["grade_note"])
                for leg in pr["legs"]:
                    st.markdown(f"- **{leg['team']}** · {leg['game']} · {leg['book']} @ {leg['price']*100:.0f}¢")
                c1, c2, c3 = st.columns(3)
                c1.metric("Payout", f"${pr['payout']:.2f}")
                c2.metric("Combined win chance", f"{pr['combined_prob']*100:.1f}%")
                c3.metric("EV", f"${pr['ev']:+.2f} ({pr['ev_pct']*100:+.1f}%)")
                if pg.same_game_conflict(pr["legs"]):
                    st.warning("Two legs share the same game — their real combined probability "
                               "is likely higher than shown, since correlated outcomes aren't "
                               "independent the way this math assumes.")
                if db.configured():
                    if st.button("📌 Log this parlay", key="log_fpi_parlay", use_container_width=True):
                        try:
                            db.log_bet(
                                game=" + ".join(l["game"] for l in pr["legs"]),
                                team=" + ".join(l["team"] for l in pr["legs"]),
                                market="parlay",
                                book=" / ".join(sorted({l["book"] for l in pr["legs"]})),
                                odds=pr["combined_decimal_odds"], stake=pr["stake"],
                                model_prob=pr["combined_prob"], source="fpi_parlay",
                                notes=f"Grade {pr['grade']}, EV ${pr['ev']:+.2f}",
                            )
                            st.success("Logged to tracker.")
                        except Exception as e:
                            st.error(f"Couldn't log: {e}")

    with st.expander("All games"):
        df = pd.DataFrame(rows)
        if not df.empty:
            df["kickoff"] = df["kickoff"].apply(fe.format_kickoff)
            for c in ("fpi", "kalshi_ask", "kalshi_edge", "poly_price", "poly_edge", "best"):
                df[c] = (df[c] * 100).round(1)
            st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("Kalshi edge = FPI − ask − 7%·p·(1−p) fee. Polymarket price is the listed outcome price, "
               "not a live ask, and fees aren't included. Missing prices mean no market was matched.")
