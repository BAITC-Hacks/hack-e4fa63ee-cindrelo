"""Cindrelo's offline, read-only dashboard for contract-v1 output files."""
import streamlit as st

from ui.charts import forecast_chart
from ui.data import ArtifactError, ROOT, TURBINES, export_filename, export_forecast, forecast_choices, load_dashboard, output_directory

SYNTHETIC_BANNER = "SYNTHETIC DEMO DATA — not model results"


def utc(value) -> str:
    return value.strftime("%d %b %Y, %H:%M UTC")


def main():
    st.set_page_config(page_title="Cindrelo — Wind power forecast", page_icon="🌬️", layout="wide")
    st.html("<style>" + (ROOT / "assets/dashboard.css").read_text(encoding="utf-8") + "</style>")
    directory = output_directory()
    with st.sidebar:
        st.subheader("Cindrelo")
        st.caption("Forecast review")
        st.divider()
        st.markdown("**Loaded directory**")
        st.code(str(directory.relative_to(ROOT)) if directory.is_relative_to(ROOT) else str(directory), language=None, wrap_lines=True)
        with st.expander("Resolved path"):
            st.code(str(directory), language=None, wrap_lines=True)
        st.button("Refresh", width="stretch", help="Reload saved files only. No weather requests or model training.")
        st.caption("Refresh reloads saved artifacts. It does not run the forecasting pipeline.")
    st.title("Cindrelo — Wind power forecast")
    st.caption("Hourly normalized power for two wind turbines")
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
    first = selected.iloc[0]
    degraded = selected.status.eq("degraded").any()
    if degraded:
        st.warning("Degraded forecast — review the source warnings before using this forecast.")
    for warning in data.manifest["warnings"]:
        if warning != SYNTHETIC_BANNER:
            st.sidebar.warning(warning)
    with st.container(border=True):
        st.subheader("Hourly forecast")
        st.caption(f"{TURBINES[turbine]} · {len(selected)} hourly targets · {utc(selected.valid_time.min())} to {utc(selected.valid_time.max())}")
        st.plotly_chart(forecast_chart(selected, synthetic=synthetic), width="stretch", config={"displaylogo": False, "scrollZoom": False}, key="forecast_chart")
    with st.container(border=True):
        st.markdown("**Forecast details**")
        columns = st.columns(4)
        for column, label, value in zip(columns, ["Forecast issue", "Weather run", "Model version", "Status"], [utc(first.issued_at), utc(first.weather_run_time), first.model_version, "Degraded" if degraded else "OK"]):
            column.caption(label)
            column.text(value)
        st.caption(f"Weather available: {utc(first.weather_available_at)} · Forecast ID: {forecast_id}")
        with st.expander("Input identity"):
            st.code(first.input_hash, language=None, wrap_lines=True)
    st.download_button("Download forecast CSV", data=export_forecast(data, forecast_id), file_name=export_filename(forecast_id), mime="text/csv", type="primary", on_click="ignore")
    st.caption(f"Exports all 96 rows for {forecast_id}: both turbines, all 48 hours, and the original CSV columns. The turbine selector only changes the chart.")
