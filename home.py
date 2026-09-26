import pandas as pd
import requests
import streamlit as st
from ev_picks import fetch, find_picks, log_picks
import daily_parlay as dp
import daily_single_pick as dsp
import results_db as db

st.set_page_config(page_title="EV Picks", page_icon="🎯", layout="centered")
st.title("🎯 Daily +EV Picks")

# ---------- posted single pick ----------
# Same 6h shared cache as the parlay below: one posted pick per app instance,
# not something each visitor regenerates for themselves.
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _posted_single_pick():
    try:
        return dsp.best_single_pick(100.0, "NFL")
    except Exception as e:
        return {"error": str(e)}

with st.container(border=True):
    st.subheader("🎯 Today's Top Pick")
    st.caption("The single side on the next NFL slate the model both favors to win and "
               "sees positive EV on — prioritizes being a real favorite over chasing the "
               "single biggest (often riskier, lower win-probability) edge.")
    pick = _posted_single_pick()
    if not pick:
        st.info("No positive-edge game found on the next available NFL slate.")
    elif "error" in pick:
        st.warning(f"Couldn't build today's pick: {pick['error']}")
    else:
        st.markdown(f"**Slate: {pick['date']}**")
        st.markdown(f"**{pick['team']}** · {pick['game']}  \n"
                    f"{pick['book']} @ {pick['price']*100:.0f}¢ (decimal {pick['decimal_odds']:.2f}) · "
                    f"model win prob **{pick['model_wp']*100:.1f}%** ({pick['confidence']}) · "
                    f"edge {pick['edge']*100:+.1f} pts")
        c1, c2, c3 = st.columns(3)
        c1.metric("$100 payout", f"${pick['payout']:.2f}")
        c2.metric("Profit if won", f"${pick['profit']:.2f}")
        c3.metric("EV", f"${pick['ev']:+.2f} ({pick['ev_pct']*100:+.1f}%)")
        st.caption("A single favored side still isn't a sure thing — even a 65%+ model "
                   "win probability means a meaningful chance of losing the full stake.")
        if db.configured():
            if st.button("📌 Log this pick", use_container_width=True, key="log_single_pick"):
                try:
                    db.log_bet(
                        game=pick["game"], team=pick["team"], market="prediction_market",
                        book=pick["book"], odds=pick["decimal_odds"], stake=pick["stake"],
                        model_prob=pick["model_wp"], source="daily_single_pick",
                        notes=f"{pick['confidence']}, edge {pick['edge']*100:+.1f} pts",
                    )
                    st.success("Logged to tracker.")
                except Exception as e:
                    st.error(f"Couldn't log: {e}")

# ---------- posted daily parlay ----------
# Cached for 6h and shared by every visitor to this app, so it acts as a
# single "posted" pick of the day rather than something each user builds.
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _posted_parlay():
    try:
        return dp.build_parlay(100.0, "NFL")
    except Exception as e:
        return {"error": str(e)}

with st.container(border=True):
    st.subheader("📌 Today's Posted Parlay")
    st.caption("Auto-built from the two best FPI-vs-market edges (Kalshi/Polymarket) on the "
               "next available NFL slate. Refreshes every 6 hours for everyone viewing this app.")
    parlay = _posted_parlay()
    if not parlay:
        st.info("No slate with two priced edges found in the next 10 days.")
    elif "error" in parlay:
        st.warning(f"Couldn't build today's parlay: {parlay['error']}")
    else:
        st.markdown(f"**Slate: {parlay['date']}**")
        for i, leg in enumerate(parlay["legs"], 1):
            st.markdown(f"**Leg {i}: {leg['team']}** · {leg['game']}  \n"
                        f"{leg['book']} @ {leg['price']*100:.0f}¢ "
                        f"(decimal {leg['decimal_odds']:.2f}) · FPI {leg['fpi']*100:.1f}% · "
                        f"edge {leg['edge']*100:+.1f} pts")
        c1, c2, c3 = st.columns(3)
        c1.metric("$100 payout", f"${parlay['payout']:.2f}")
        c2.metric("Profit if won", f"${parlay['profit']:.2f}")
        c3.metric("EV", f"${parlay['ev']:+.2f} ({parlay['ev_pct']*100:+.1f}%)")
        st.caption("EV assumes the two legs are independent and uses FPI as the 'fair' "
                   "probability for each leg. A parlay concentrates risk — a $100 stake "
                   "here can lose in full even if each leg looked like a good single bet.")
        if db.configured():
            if st.button("📌 Log this parlay", use_container_width=True):
                try:
                    db.log_bet(
                        game=" + ".join(l["game"] for l in parlay["legs"]),
                        team=" + ".join(l["team"] for l in parlay["legs"]),
                        market="parlay", book=" / ".join(sorted({l["book"] for l in parlay["legs"]})),
                        odds=parlay["combined_decimal_odds"], stake=parlay["stake"],
                        model_prob=parlay["combined_fair_prob"], source="daily_parlay",
                        notes=f"EV ${parlay['ev']:+.2f}",
                    )
                    st.success("Logged to tracker.")
                except Exception as e:
                    st.error(f"Couldn't log: {e}")

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
            if db.configured():
                errors = 0
                for p in placed:
                    try:
                        db.log_bet(
                            game=p["game"], team=f"{p['pick']} {p['line']}".strip(),
                            market=p["market"], book=p["book"], odds=p["odds"],
                            stake=p["stake"], model_prob=p["fair_prob"], source="ev_picks",
                            notes=f"EV {p['ev']*100:.1f}%",
                        )
                    except Exception:
                        errors += 1
                if errors:
                    st.warning(f"Logged {len(placed) - errors}/{len(placed)} — {errors} failed.")
                else:
                    st.success(f"Logged {len(placed)} bet(s) to the Results Tracker.")
            else:
                log_picks(placed)
                st.info("Results Tracker isn't connected yet, so this saved to picks_log.csv "
                        "instead (that file won't survive an app restart). See the Results "
                        "Tracker page to set up permanent tracking.")

        st.download_button("Download all picks (CSV)", pd.DataFrame(picks).to_csv(index=False),
                           "picks.csv", "text/csv", use_container_width=True)
