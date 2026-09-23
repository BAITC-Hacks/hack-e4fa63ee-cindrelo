"""Figures in supplied normalized-power units; never infer turbine capacity."""
import pandas as pd
import plotly.graph_objects as go


def forecast_chart(selected: pd.DataFrame, *, synthetic: bool) -> go.Figure:
    selected = selected.sort_values("valid_time")
    figure = go.Figure(go.Scatter(
        x=selected.valid_time, y=selected.power_normalized,
        name="Selected forecast", mode="lines", line={"color": "#087E8B", "width": 3},
        hovertemplate="%{x|%d %b %Y, %H:%M} UTC<br>Normalized power: %{y:.3f}<extra>Selected forecast</extra>",
    ))
    figure.update_layout(
        height=350, margin={"l": 5, "r": 10, "t": 35, "b": 5},
        title={"text": "Synthetic example · not model results" if synthetic else "Published forecast", "font": {"size": 13}},
        font={"family": "Arial, sans-serif", "color": "#26384B"},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified", legend={"orientation": "h", "y": 1.12, "x": 0},
        yaxis={"title": "Normalized power", "range": [0, 1], "dtick": .2, "gridcolor": "#E8EDF2", "fixedrange": True},
        xaxis={"title": "Target interval start (UTC)", "tickformat": "%d %b\n%H:%M", "showgrid": False},
    )
    return figure
