"""Cindrelo's forecast dashboard with an explicit full-cycle run action."""
import streamlit as st
import pandas as pd

from ui.charts import forecast_chart
from ui.designs import apply_theme, masthead, summary_cards
from ui.runner import displayed_directory, render_run_controls
from ui.data import ArtifactError, ROOT, TURBINES, aligned_actuals, compare_forecasts, export_filename, export_forecast, forecast_choices, load_dashboard, output_directory, previous_issuances

SYNTHETIC_BANNER = "SYNTHETIC DEMO DATA — not model results"


def utc(value) -> str:
    return value.strftime("%d %b %Y, %H:%M UTC")


def provenance_values(selected, name, *, timestamp=False):
    values = selected[name].drop_duplicates().tolist()
    return "; ".join(utc(value) if timestamp else str(value) for value in values)


def show_metrics(metrics, turbine, synthetic):
    st.subheader("Model and baseline comparison")
    if synthetic:
        st.warning("SYNTHETIC METRICS — invented examples, not measured performance")
    st.caption("Supplied evaluation results, not a new score on the displayed forecast. Lower MAE and RMSE are better; errors are in normalized-power units.")
    subset = metrics[metrics.turbine_id.eq(turbine)]
    if subset.empty:
        st.info("No evaluation metrics available for this turbine. Publish rows in metrics.csv to add a comparison.")
        return
    windows = list(subset[["period_start", "period_end"]].drop_duplicates().sort_values("period_start", ascending=False).itertuples(index=False, name=None))
    window = st.selectbox("Evaluation window (UTC)", windows, format_func=lambda w: f"{utc(w[0])} → {utc(w[1])}")
    subset = subset[subset.period_start.eq(window[0]) & subset.period_end.eq(window[1])]
    bucket = st.radio("Forecast horizon (hours)", sorted(subset.horizon_bucket.unique()), horizontal=True)
    st.caption(f"{TURBINES[turbine]} · Horizon {bucket} hours · Start inclusive, end exclusive. Sample counts are scored forecast/target pairs.")
    table = subset[subset.horizon_bucket.eq(bucket)][["model", "mae", "rmse", "n_samples"]]
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "model": st.column_config.TextColumn("Model / baseline"),
        "mae": st.column_config.NumberColumn("MAE", format="%.3f"),
        "rmse": st.column_config.NumberColumn("RMSE", format="%.3f"),
        "n_samples": st.column_config.NumberColumn("Scored pairs", format="%d"),
    })
    if table.n_samples.nunique() > 1:
        st.warning("Models have different sample counts. Confirm comparable evaluation coverage before drawing conclusions.")


def show_events(events, forecast_id, synthetic):
    st.subheader("Saved tool trace")
    st.caption("Recorded events for the selected forecast, ordered by supplied timestamp in UTC. Replay timestamps may represent simulated issuance. Viewing this trace does not run an agent.")
    if synthetic:
        st.warning("SYNTHETIC TRACE — example events; no real tool executed")
    selected_events = events[events.forecast_id.eq(forecast_id)]
    if selected_events.empty:
        st.info("No tool events recorded for this forecast in events.jsonl.")
        return
    for event in selected_events.itertuples():
        label = f"{event.timestamp.strftime('%d %b %Y, %H:%M:%S')} UTC · {event.tool} · {event.status.upper()}"
        with st.expander(label, expanded=event.status in ("warning", "failed")):
            if event.status == "failed":
                st.error("Tool reported a failure")
            elif event.status == "warning":
                st.warning("Tool reported a warning")
            # Never interpret messages as HTML or markdown.
            st.text(event.message)




def show_forecast(data, selected, turbine, forecast_id, labels, synthetic, degraded):
    previous_choices = previous_issuances(data.forecasts, forecast_id)
    overlap = pd.DataFrame()
    if not previous_choices.empty:
        previous_ids = previous_choices.forecast_id.tolist()
        previous_id = previous_ids[0]
        if len(previous_ids) > 1:
            previous_id = st.selectbox("Previous issuance version", previous_ids, format_func=labels.get)
        previous = data.forecasts[data.forecasts.forecast_id.eq(previous_id) & data.forecasts.turbine_id.eq(turbine)]
        overlap = compare_forecasts(selected, previous)
    actuals = aligned_actuals(selected, data.actuals, turbine)
    n_actuals = int(actuals.power_normalized.notna().sum())
    with st.container(key="horizon_layout"):
        chart_area, detail_area = st.columns([3, 1.2], gap="large")
    with chart_area:
        with st.container(border=True):
            st.subheader("Hourly forecast")
            st.caption(f"{TURBINES[turbine]} · {len(selected)} hourly targets · All target labels mark interval starts in UTC.")
            controls = st.columns(2)
            show_previous = controls[0].checkbox("Show previous issuance", value=True, disabled=overlap.empty)
            show_actuals = controls[1].checkbox("Show actual power", value=True, disabled=n_actuals == 0)
            st.plotly_chart(forecast_chart(selected, synthetic=synthetic,
                overlap=overlap if show_previous else None, actuals=actuals if show_actuals else None),
                width="stretch", theme=None, config={"displaylogo": False, "scrollZoom": False}, key="forecast_chart")
            if overlap.empty:
                st.info("No previous overlapping forecast")
            else:
                revision = float((overlap.power_normalized_selected - overlap.power_normalized_previous).abs().mean())
                st.metric("Forecast revision", f"{revision:.4f}")
                st.caption(f"Mean absolute change in normalized power over {len(overlap)} overlapping hours versus {previous_id}. This is a revision, not forecast error.")
            if n_actuals == 0:
                st.info("Actuals unavailable for this forecast period.")
            else:
                st.caption(f"Actual observations: {n_actuals} / {len(selected)} target hours. Missing observations remain gaps.")
            february = actuals.valid_time.ge(pd.Timestamp("2026-02-01", tz="UTC")) & actuals.valid_time.lt(pd.Timestamp("2026-03-01", tz="UTC"))
            if february.any() and not actuals.loc[february, "power_normalized"].notna().any():
                st.caption("Actuals unavailable for February 2026. February forecast accuracy cannot be measured from the supplied files.")
    with detail_area:
        with st.container(border=True):
            st.markdown("**Forecast details**")
            columns = [st.container() for _ in range(4)]
            values = [provenance_values(selected, "issued_at", timestamp=True), provenance_values(selected, "weather_run_time", timestamp=True), provenance_values(selected, "model_version"), "Degraded" if degraded else "OK"]
            for column, label, value in zip(columns, ["Forecast issue", "Weather run", "Model version", "Status"], values):
                column.caption(label)
                column.text(value)
            st.caption(f"Weather available: {provenance_values(selected, 'weather_available_at', timestamp=True)} · Forecast ID: {forecast_id}")
            with st.expander("Input identity"):
                st.code(provenance_values(selected, "input_hash"), language=None, wrap_lines=True)
        st.download_button("Download forecast CSV", data=export_forecast(data, forecast_id), file_name=export_filename(forecast_id), mime="text/csv", type="primary", on_click="ignore")
        st.caption(f"Exports all 96 rows for {forecast_id}: both turbines, all 48 hours, and the original CSV columns. The turbine selector only changes the chart.")


def main():
    st.set_page_config(page_title="Cindrelo — Wind power forecast", page_icon="🌬️", layout="wide")
    apply_theme()
    try:
        directory = displayed_directory(output_directory())
    except ArtifactError as exc:
        st.error(str(exc))
        st.stop()
    with st.sidebar:
        st.subheader("Cindrelo")
        st.caption("Forecast review")
        st.divider()
        directory_display = st.container()
        st.caption("Refresh reloads saved artifacts. It does not run the forecasting pipeline.")
    masthead()
    intro, refresh = st.columns([5, 1])
    intro.caption("Cindrelo — Wind power forecast · Hourly normalized power for two wind turbines")
    refresh.button("Refresh", width="stretch", help="Reload saved files only. No weather requests or model training.")
    directory = render_run_controls(directory)
    with directory_display:
        st.markdown("**Loaded directory**")
        st.code(str(directory.relative_to(ROOT)) if directory.is_relative_to(ROOT) else str(directory), language=None, wrap_lines=True)
        with st.expander("Resolved path"):
            st.code(str(directory), language=None, wrap_lines=True)
    try:
        data = load_dashboard(directory)
    except ArtifactError as exc:
        st.error(str(exc))
        st.info("Check CINDRELO_OUTPUT_DIR and the contract in docs/TEAMMATE_SPEC.md. Relative paths resolve from the app directory. No fallback data has been loaded.")
        st.stop()
    synthetic = data.manifest["data_kind"] == "synthetic"
    if synthetic:
        st.warning(SYNTHETIC_BANNER)
        st.sidebar.warning(SYNTHETIC_BANNER)
    else:
        st.sidebar.info("Published model output · UTC")
    with st.sidebar.expander("About this dataset"):
        st.text(data.manifest["description"])
    choices = forecast_choices(data.forecasts)
    ids = choices.forecast_id.tolist()
    labels = {row.forecast_id: f"{utc(row.issued_at)} · {row.forecast_id}" for row in choices.itertuples()}
    # Retain valid selections across Refresh; a removed ID must not leave stale UI.
    if st.session_state.get("forecast_id") not in ids:
        st.session_state["forecast_id"] = ids[0]
    columns = st.columns([1, 2])
    turbine = columns[0].selectbox("Turbine", list(TURBINES), format_func=TURBINES.get, key="turbine")
    forecast_id = columns[1].selectbox("Forecast issuance (UTC)", ids, format_func=labels.get, key="forecast_id")
    selected = data.forecasts[data.forecasts.forecast_id.eq(forecast_id) & data.forecasts.turbine_id.eq(turbine)].sort_values("valid_time")
    degraded = selected.status.eq("degraded").any()
    if degraded:
        st.warning("Degraded forecast — review the source warnings before using this forecast.")
    for warning in data.manifest["warnings"]:
        if warning != SYNTHETIC_BANNER:
            st.sidebar.warning(warning)
    failures = data.events[data.events.forecast_id.eq(forecast_id) & data.events.status.eq("failed")]
    if not failures.empty:
        st.error(f"{len(failures)} failed tool event(s) recorded for this forecast. Inspect Tool trace.")
    unpublished = data.events[data.events.status.eq("failed") & ~data.events.forecast_id.isin(ids)]
    if not unpublished.empty:
        with st.sidebar.expander(f"Unpublished run failures ({len(unpublished)})", expanded=True):
            for event in unpublished.itertuples():
                st.text(f"{event.forecast_id} · {utc(event.timestamp)} · {event.tool}: {event.message}")

    summary_cards(selected)
    def forecast():
        show_forecast(data, selected, turbine, forecast_id, labels, synthetic, degraded)

    forecast_tab, metrics_tab, trace_tab = st.tabs(["Forecast", "Model comparison", "Tool trace"])
    with forecast_tab:
        forecast()
    with metrics_tab:
        show_metrics(data.metrics, turbine, synthetic)
    with trace_tab:
        show_events(data.events, forecast_id, synthetic)
