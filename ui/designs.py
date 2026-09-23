"""Three presentation systems over the same artifact contract."""
import streamlit as st

from ui.data import ROOT

DESIGNS = ("Horizon", "Control room", "Field report")
PALETTES = {
    "Horizon": ("#6046d7", "#919bb0", "#13796d", "#25304a", "#e5e8f1"),
    "Control room": ("#75edd2", "#aaa6ed", "#ffc673", "#d8e6ed", "#2b3e50"),
    "Field report": ("#23614b", "#979082", "#bc592d", "#363b31", "#dedacf"),
}


def choose_design():
    design = st.radio("Design version", DESIGNS, key="design", horizontal=True)
    slug = {"Horizon": "horizon", "Control room": "control", "Field report": "report"}[design]
    css = (ROOT / "assets" / f"{slug}.css").read_text(encoding="utf-8")
    st.html("<style>" + (ROOT / "assets/dashboard.css").read_text(encoding="utf-8") + css + "</style>")
    return design


def masthead(design):
    headers = {
        "Horizon": ("01 / HORIZON", "The next 48 hours, in focus.", "Explore the forecast. Understand what changed."),
        "Control room": ("02 / CONTROL ROOM", "Forecast operations", "SAVED ARTIFACTS / UTC / TWO TURBINES"),
        "Field report": ("03 / FIELD REPORT", "Reading the wind.", "A field guide to the forecast, its revisions and the evidence behind it."),
    }
    eyebrow, title, subtitle = headers[design]
    st.html(f'<div class="design-masthead"><div class="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>')


def summary_cards(selected, design):
    with st.container(key="summary_cards"):
        columns = st.columns(3)
        columns[0].metric("Forecast window", f"{len(selected)} hours")
        columns[1].metric("Mean normalized power", f"{selected.power_normalized.mean():.3f}")
        columns[2].metric("Publication status", "Degraded" if selected.status.eq("degraded").any() else "OK")
    if design == "Field report":
        st.caption("Forecast summary · descriptive values from the selected saved forecast, not measured accuracy.")


def chapter(number, title, text):
    st.html(f'<div class="chapter"><span>{number}</span><div><h2>{title}</h2><p>{text}</p></div></div>')
