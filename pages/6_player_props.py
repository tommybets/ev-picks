import pandas as pd
import streamlit as st

import fpi_edges as fe
import pm_props as pm
import props_edges as pe
import results_db as db
from ev_picks import kelly

st.set_page_config(page_title="Player Props", page_icon="🏀", layout="centered")
st.title("🏀 Player Props")
st.caption("Finds player props where one sportsbook's price beats the consensus fair price "
           "across all books (vig removed, sharp books weighted more). Props are priced "
           "less sharply than spreads, which is why they're often where real value shows up.")


@st.cache_data(ttl=600, show_spinner=False)
def load_events(sport_key, api_key):
    return pe.list_events(sport_key, api_key)


@st.cache_data(ttl=120, show_spinner=False)
def load_kalshi_props(sport, extra):
    return pm.kalshi_prop_markets(pm.discover_kalshi_series(sport) + list(extra))


@st.cache_data(ttl=120, show_spinner=False)
def load_poly_props(sport):
    try:
        series = fe.poly_candidates(sport)[1]
    except Exception:
        series = None
    return pm.poly_prop_markets(series)


@st.cache_data(ttl=600, show_spinner=False)
def load_props(sport_key, event_id, markets, regions, api_key):
    # Cached for 10 minutes so re-running the same scan doesn't spend credits again.
    return pe.event_props(sport_key, event_id, list(markets), regions, api_key)


try:
    default_key = st.secrets.get("ODDS_API_KEY", "")
except Exception:
    default_key = ""
api_key = st.text_input("Odds API key", value=default_key, type="password")

sport = st.selectbox("Sport", list(pe.SPORTS))
sport_key = pe.SPORTS[sport]
if not api_key:
    st.info("Add your Odds API key to load games.")
    st.stop()

try:
    events = load_events(sport_key, api_key)
except Exception as e:
    st.error(f"Couldn't load games: {e}")
    st.stop()
if not events:
    st.info(f"No upcoming {sport} games listed right now.")
    st.stop()

labels = {f"{e['away_team']} @ {e['home_team']} · {fe.format_kickoff(e['commence_time'])}": e
          for e in events}
chosen = st.multiselect("Games to scan", list(labels), default=list(labels)[:1])
market_labels = st.multiselect("Props", list(pe.MARKETS[sport]), default=pe.DEFAULT_MARKETS[sport])
include_pinnacle = st.toggle("Include Pinnacle (sharpest book, doubles the credit cost)", value=False)
regions = "us,eu" if include_pinnacle else "us"

c1, c2 = st.columns(2)
min_ev = c1.slider("Minimum EV (%)", 1.0, 10.0, 3.0, 0.5) / 100
bankroll = c2.number_input("Bankroll ($)", min_value=50.0, value=1000.0, step=50.0)

include_pm = st.toggle("Also price Kalshi & Polymarket props against the model (free)", value=True)
pm_min_edge = st.slider("Prediction market minimum edge (points)", 2.0, 15.0, 4.0, 0.5) / 100
with st.expander("Advanced"):
    extra = st.text_input("Extra Kalshi series tickers (comma separated)", "",
                          help="Kalshi's NBA prop series are built in; add others here if you "
                               "find them on kalshi.com (e.g. a new NFL yards series).")
extra_series = [t.strip().upper() for t in extra.split(",") if t.strip()]

n_regions = len(regions.split(","))
cost = len(chosen) * len(market_labels) * n_regions
st.caption(f"This scan costs about **{cost} Odds API credits** ({len(chosen)} game(s) × "
           f"{len(market_labels)} prop type(s) × {n_regions} region(s)). The free plan has 500 per "
           f"month. Re-running the same scan within 10 minutes is free.")

if st.button("Find prop value", type="primary", use_container_width=True,
             disabled=not chosen or not market_labels):
    markets = tuple(pe.MARKETS[sport][m] for m in market_labels)
    found, remaining, models = [], None, {}
    with st.spinner("Pulling props..."):
        for label in chosen:
            ev = labels[label]
            try:
                data, remaining = load_props(sport_key, ev["id"], markets, regions, api_key)
                found += pe.find_prop_edges(data, min_edge=min_ev)
                models[f"{ev['away_team']} @ {ev['home_team']}"] = dict(
                    date=pm.event_date_et(ev["commence_time"]), lines=pe.consensus(data))
            except Exception as e:
                st.warning(f"{label}: {e}")
    pm_found, pm_counts = [], {}
    if include_pm and models:
        with st.spinner("Pricing Kalshi & Polymarket props..."):
            try:
                km = load_kalshi_props(sport, tuple(extra_series))
            except Exception as e:
                km = []
                st.warning(f"Kalshi: {e}")
            try:
                polym = load_poly_props(sport)
            except Exception as e:
                polym = []
                st.warning(f"Polymarket: {e}")
            pm_found = (pm.price_kalshi(sport, km, models, pm_min_edge)
                        + pm.price_poly(sport, polym, models, pm_min_edge))
            pm_counts = dict(kalshi=len(km), poly=len(polym))
    st.session_state["pm_found"] = sorted(pm_found, key=lambda x: -x["edge"])
    st.session_state["pm_counts"] = pm_counts
    st.session_state["props_found"] = sorted(found, key=lambda x: -x["ev"])
    st.session_state["props_sport"] = sport
    if remaining is not None:
        st.session_state["props_credits"] = remaining

found = st.session_state.get("props_found")
if found is not None:
    psport = st.session_state.get("props_sport", sport)
    if st.session_state.get("props_credits"):
        st.caption(f"Odds API credits left this month: {st.session_state['props_credits']}")
    if not found:
        st.info("No props beat the consensus by that much right now. Try a lower minimum EV, "
                "more prop types, or turning on Pinnacle (more books makes the fair price sharper).")
    for i, p in enumerate(found):
        stake = min(bankroll * kelly(p["fair_prob"], p["odds"], 0.25), bankroll * 0.02)
        with st.container(border=True):
            st.markdown(f"**{p['player']}** · {pe.market_label(psport, p['market'])} "
                        f"**{p['side']} {p['point']:g}**")
            st.markdown(f"{p['book']} @ **{p['odds']:.2f}** ({pe.to_american(p['odds']):+.0f}) · "
                        f"fair {p['fair_prob']*100:.1f}% · EV **{p['ev']*100:+.1f}%** · "
                        f"stake ${stake:.2f}")
            st.caption(f"{p['game']} · {fe.format_kickoff(p['kickoff'])} · "
                       f"priced at {p['books']} book(s)")
            if db.configured() and st.button("📌 Log this prop", key=f"log_prop_{i}"):
                try:
                    db.log_bet(
                        game=p["game"],
                        team=f"{p['player']} {p['side']} {p['point']:g} {pe.market_label(psport, p['market'])}",
                        market="player_prop", book=p["book"], odds=p["odds"], stake=round(stake, 2),
                        model_prob=p["fair_prob"], source="player_props",
                        notes=f"EV {p['ev']*100:.1f}%, {p['books']} books",
                    )
                    st.success("Logged to tracker (props are graded by hand for now).")
                except Exception as e:
                    st.error(f"Couldn't log: {e}")
    if found:
        with st.expander("All results as a table"):
            df = pd.DataFrame(found)
            df["market"] = df["market"].apply(lambda m: pe.market_label(psport, m))
            df["fair_prob"] = (df["fair_prob"] * 100).round(1)
            df["ev"] = (df["ev"] * 100).round(1)
            st.dataframe(df[["player", "market", "side", "point", "book", "odds", "fair_prob",
                             "ev", "books", "game"]], use_container_width=True, hide_index=True)
    st.caption("A prop priced better than consensus is a value signal, not a lock. Watch "
               "for late scratches: if a player is ruled out, books usually void the bet, "
               "but a limited role can quietly sink an Over.")

pm_found = st.session_state.get("pm_found")
if found is not None and pm_found is not None and st.session_state.get("pm_counts"):
    psport = st.session_state.get("props_sport", sport)
    counts = st.session_state["pm_counts"]
    st.subheader("📈 Kalshi & Polymarket vs the model")
    st.caption(f"Model = the sportsbook consensus above. Checked {counts.get('kalshi', 0)} Kalshi "
               f"and {counts.get('poly', 0)} Polymarket markets; only props for the games and "
               f"stats you scanned can be priced. Kalshi edges are after its trading fee.")
    if not pm_found:
        st.info("No prediction-market props beat the model by that much. If the counts above "
                "are 0, that sport may not have prop markets listed right now.")
    for i, p in enumerate(pm_found):
        with st.container(border=True):
            st.markdown(f"**{p['player']}** · {pe.market_label(psport, p['market'])} · "
                        f"{p['venue']} **{p['label']}**")
            st.markdown(f"Price **{p['price']*100:.0f}¢** · model **{p['model_prob']*100:.1f}%** · "
                        f"edge **{p['edge']*100:+.1f} pts**")
            note = ("books post this exact line" if p["method"] == "matched"
                    else "estimated from the books' nearby line, so treat it as rougher")
            st.caption(f"{p['game']} · model {note}")
            if db.configured() and st.button("📌 Log this prop", key=f"log_pm_{i}"):
                try:
                    db.log_bet(
                        game=p["game"],
                        team=f"{p['player']} {p['side']} {p['point']:g} {pe.market_label(psport, p['market'])}",
                        market="player_prop", book=p["venue"], odds=1 / p["price"], stake=25.0,
                        model_prob=p["model_prob"], source="pm_props",
                        notes=f"edge {p['edge']*100:+.1f} pts ({p['method']})",
                    )
                    st.success("Logged to tracker (props are graded by hand for now).")
                except Exception as e:
                    st.error(f"Couldn't log: {e}")
