import streamlit as st

import parlay_grading as pg
import results_db as db

st.set_page_config(page_title="Parlay Grader", page_icon="🧮", layout="centered")
st.title("🧮 Parlay Grader")
st.caption("Enter any parlay by hand — any sport, any bet type — and get an estimated "
           "payout, EV, and a plain grade of how reasonable it is. You supply the "
           "probability for each leg (your own read, or a number from wherever you got "
           "the pick); this tool doesn't look that up for you.")

odds_format = st.radio("Odds format", ["American", "Decimal"], horizontal=True)
n_legs = st.number_input("Number of legs", min_value=2, max_value=8, value=2, step=1)
stake = st.number_input("Stake ($)", min_value=1.0, value=50.0, step=5.0)

st.divider()
legs_input = []
for i in range(int(n_legs)):
    st.markdown(f"**Leg {i + 1}**")
    c1, c2, c3 = st.columns([2, 1, 1])
    desc = c1.text_input("Bet", key=f"leg_desc_{i}", placeholder="e.g. Lakers -4.5",
                         label_visibility="collapsed")
    game = c1.text_input("Game (optional, for same-game detection)", key=f"leg_game_{i}",
                         placeholder="e.g. LAL @ BOS")
    if odds_format == "American":
        odds_val = c2.number_input("Odds", key=f"leg_odds_{i}", value=-110, step=5,
                                   label_visibility="collapsed")
    else:
        odds_val = c2.number_input("Odds", key=f"leg_odds_{i}", value=1.91, step=0.01,
                                   min_value=1.01, label_visibility="collapsed")
    prob_pct = c3.number_input("Win %", key=f"leg_prob_{i}", value=55.0, min_value=0.1,
                               max_value=99.9, step=0.5, label_visibility="collapsed")
    legs_input.append(dict(desc=desc or f"Leg {i + 1}", game=game, odds_val=odds_val,
                           prob_pct=prob_pct))
st.caption("Bet / Game / Odds / Win % — fill each leg's row above.")

if st.button("Grade this parlay", type="primary", use_container_width=True):
    try:
        legs = []
        for li in legs_input:
            decimal_odds = (pg.american_to_decimal(li["odds_val"]) if odds_format == "American"
                            else float(li["odds_val"]))
            legs.append(dict(prob=li["prob_pct"] / 100.0, decimal_odds=decimal_odds,
                             desc=li["desc"], game=li["game"]))
        result = pg.combine_parlay(legs, stake)
        letter, note = pg.grade_parlay(result["edge_pts"], result["combined_prob"])
        st.session_state["manual_parlay_result"] = dict(
            legs=legs, stake=stake, grade=letter, grade_note=note, **result)
    except Exception as e:
        st.error(f"Couldn't grade that: {e}")

pr = st.session_state.get("manual_parlay_result")
if pr:
    grade_colors = {"A": "🟢", "B": "🟢", "C": "🟡", "C-": "🟡", "D": "🟠", "F": "🔴"}
    with st.container(border=True):
        st.markdown(f"## {grade_colors.get(pr['grade'], '')} Grade: {pr['grade']}")
        st.markdown(pr["grade_note"])
        for leg in pr["legs"]:
            american = pg.decimal_to_american(leg["decimal_odds"])
            st.markdown(f"- **{leg['desc']}**" + (f" · {leg['game']}" if leg["game"] else "")
                        + f" · {leg['decimal_odds']:.2f} ({american:+.0f}) · you: {leg['prob']*100:.1f}%")
        st.divider()
        c1, c2 = st.columns(2)
        c1.metric("Combined odds", f"{pr['combined_decimal_odds']:.2f}")
        c2.metric("Payout", f"${pr['payout']:.2f}")
        c3, c4 = st.columns(2)
        c3.metric("Your combined win chance", f"{pr['combined_prob']*100:.1f}%")
        c4.metric("Book's implied chance", f"{pr['implied_prob']*100:.1f}%")
        st.metric("EV at your numbers", f"${pr['ev']:+.2f} ({pr['ev_pct']*100:+.1f}%)")

        if pg.same_game_conflict(pr["legs"]):
            st.warning("Two or more legs share the same game — their real combined "
                       "probability is likely higher than shown, since correlated "
                       "outcomes (e.g. a team covering the spread and the game going "
                       "over) aren't independent the way this math assumes.")
        st.caption("The grade only reflects how your own win-probability estimates compare "
                   "to the price — if you're systematically too confident on every leg, "
                   "a high grade here won't save you. Garbage in, garbage out.")

        if db.configured():
            if st.button("📌 Log this parlay", use_container_width=True):
                try:
                    db.log_bet(
                        game=" + ".join(l["game"] or l["desc"] for l in pr["legs"]),
                        team=" + ".join(l["desc"] for l in pr["legs"]),
                        market="parlay", book="manual",
                        odds=pr["combined_decimal_odds"], stake=pr["stake"],
                        model_prob=pr["combined_prob"], source="manual_parlay",
                        notes=f"Grade {pr['grade']}, EV ${pr['ev']:+.2f}",
                    )
                    st.success("Logged to tracker.")
                except Exception as e:
                    st.error(f"Couldn't log: {e}")
