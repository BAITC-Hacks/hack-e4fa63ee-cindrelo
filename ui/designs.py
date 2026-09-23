"""Horizon presentation for the dashboard artifact contract."""
import streamlit as st

from ui.data import ROOT

PALETTE = ("#6554ad", "#8590a3", "#287e77", "#354052", "#e3e5ed")


def apply_theme():
    css = (ROOT / "assets/horizon.css").read_text(encoding="utf-8")
    st.html("<style>" + (ROOT / "assets/dashboard.css").read_text(encoding="utf-8") + css + "</style>")


def masthead():
    st.html('<div class="design-masthead"><div class="eyebrow">HORIZON</div><h1>The next 48 hours, in focus.</h1><p>Explore the forecast. Understand what changed.</p></div>')


def summary_cards(selected):
    with st.container(key="summary_cards"):
        columns = st.columns(3)
        columns[0].metric("Forecast window", f"{len(selected)} hours")
        columns[1].metric("Mean normalized power", f"{selected.power_normalized.mean():.3f}")
        columns[2].metric("Publication status", "Degraded" if selected.status.eq("degraded").any() else "OK")
