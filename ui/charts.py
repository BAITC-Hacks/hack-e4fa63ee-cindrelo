"""Figures in supplied normalized-power units; never infer turbine capacity."""
import pandas as pd
import plotly.graph_objects as go


def forecast_chart(selected: pd.DataFrame, *, synthetic: bool, overlap: pd.DataFrame | None = None, actuals: pd.DataFrame | None = None) -> go.Figure:
    selected = selected.sort_values("valid_time")
    figure = go.Figure(go.Scatter(
        x=selected.valid_time, y=selected.power_normalized,
        name="Selected forecast", mode="lines", line={"color": "#087E8B", "width": 3},
        hovertemplate="%{x|%d %b %Y, %H:%M} UTC<br>Normalized power: %{y:.3f}<extra>Selected forecast</extra>",
    ))
    if overlap is not None and not overlap.empty:
        figure.add_trace(go.Scatter(
            x=overlap.valid_time, y=overlap.power_normalized_previous,
            name="Previous issuance", mode="lines", line={"color": "#65758B", "width": 2, "dash": "dash"},
            hovertemplate="%{x|%d %b %Y, %H:%M} UTC<br>Normalized power: %{y:.3f}<extra>Previous issuance</extra>",
        ))
    if actuals is not None and actuals.power_normalized.notna().any():
        figure.add_trace(go.Scatter(
            x=actuals.valid_time,
            y=[None if pd.isna(value) else float(value) for value in actuals.power_normalized],
            name="Actual power" if not synthetic else "Synthetic actuals", mode="lines", connectgaps=False,
            line={"color": "#BF6C18", "width": 2},
            hovertemplate="%{x|%d %b %Y, %H:%M} UTC<br>Normalized power: %{y:.3f}<extra>Actual power</extra>",
        ))
    figure.update_layout(
        height=350, margin={"l": 5, "r": 10, "t": 35, "b": 5},
        title={"text": "Synthetic example · not model results" if synthetic else "Published forecast", "font": {"size": 13}},
        font={"family": "Arial, sans-serif", "color": "#26384B"},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified", legend={"orientation": "h", "y": -.25, "x": 0},
        yaxis={"title": "Normalized power", "range": [0, 1], "dtick": .2, "gridcolor": "#E8EDF2", "fixedrange": True},
        xaxis={"title": "Target interval start (UTC)", "tickformat": "%d %b\n%H:%M", "showgrid": False},
    )
    return figure
