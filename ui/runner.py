"""Explicit dashboard action for a full forecasting cycle.

Backend dependencies are imported only after the user presses Run. Reading
published artifacts therefore still works with just requirements-ui.txt.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import streamlit as st

from ui.data import ArtifactError, ROOT, load_dashboard

DEFAULT_ISSUED_AT = "2026-01-09T19:00:00Z"
LIVE_MODE = "Live OpenAI agent + weather"
OFFLINE_MODE = "Offline deterministic rehearsal (cached weather)"


class CycleDisplayError(Exception):
    """A backend failure whose message is safe to display."""


def invoke_cycle(**kwargs):
    """Keep optional backend imports and their safe-error contract together."""
    from src.service import ForecastCycleError, run_forecast_cycle

    try:
        return run_forecast_cycle(**kwargs)
    except ForecastCycleError as exc:
        raise CycleDisplayError(str(exc)) from None


def displayed_directory(base_directory: Path) -> Path:
    """A successful run remains selected through normal Streamlit reruns."""
    return Path(st.session_state.get("cycle_display_directory", base_directory))


def render_run_controls(base_directory: Path) -> Path:
    """Run on a button press, then return the last valid artifact directory."""
    directory = displayed_directory(base_directory)
    with st.container(border=True, key="run_panel"):
        st.markdown("**Run the forecasting agent**")
        st.caption(
            "One click retrieves weather, prepares data, runs the model, analyzes and publishes "
            "the forecast, then advances the simulated UTC clock by 12 hours to recalculate "
            "with a newer weather run. This is a historical replay, not today's weather."
        )
        with st.expander("Run options"):
            issued_at = st.text_input(
                "First forecast issuance (UTC)", value=DEFAULT_ISSUED_AT,
                key="cycle_issued_at", help="ISO 8601 UTC timestamp, for example 2026-01-09T19:00:00Z.",
            )
            include_update = st.checkbox(
                "Automatically run the revision 12 hours later", value=True,
                key="cycle_include_update",
            )
            mode = st.radio("Execution mode", [LIVE_MODE, OFFLINE_MODE], key="cycle_mode")
            st.caption(
                "Live mode uses OPENAI_API_KEY from your local environment or .env and may incur API "
                "costs. The offline rehearsal runs deterministic orchestration with cached weather; "
                "it does not call an LLM. Both modes publish real model predictions."
            )
        clicked = st.button("Run forecast cycle", type="primary", key="run_forecast_cycle")
        if not clicked:
            return directory

        if "cycle_output_directory" not in st.session_state:
            st.session_state["cycle_output_directory"] = str(ROOT / "outputs/dashboard-runs" / uuid4().hex)
        output = Path(st.session_state["cycle_output_directory"])
        with st.status("Starting forecast cycle…", expanded=True) as progress:
            def on_event(event):
                tool = str(event.get("tool", "forecast"))
                state = str(event.get("status", "running"))
                issuance = str(event.get("issued_at", ""))
                message = str(event.get("message", ""))
                progress.update(label=f"{tool}: {state}")
                st.text(f"{issuance} · {tool} · {state}: {message}")

            try:
                result = invoke_cycle(
                    issued_at=issued_at.strip(), output=output,
                    controller="agent" if mode == LIVE_MODE else "deterministic",
                    offline=mode == OFFLINE_MODE, include_update=include_update,
                    on_event=on_event,
                )
                published_directory = Path(result["output_dir"]).resolve()
                if (published_directory.parent != output.resolve()
                        or not published_directory.name.startswith("snapshot-")):
                    raise CycleDisplayError("The run returned an unexpected output directory. Please retry.")
                published = load_dashboard(published_directory)
                forecast_id = result["forecast_id"]
                if forecast_id not in set(published.forecasts.forecast_id):
                    raise CycleDisplayError("The latest forecast was not found in the published files. Please retry.")
            except ImportError:
                progress.update(label="Backend dependencies unavailable", state="error", expanded=True)
                st.error(
                    "Install the backend in the same Python environment as Streamlit: "
                    "python -m pip install -r requirements-backend.txt. Then restart the app and retry."
                )
                return directory
            except (CycleDisplayError, ArtifactError) as exc:
                progress.update(label="Forecast cycle failed", state="error", expanded=True)
                st.error(str(exc))
                st.caption("Your current forecast remains selected. Correct the issue and press Run forecast cycle to retry.")
                return directory
            except Exception:
                # Unexpected exception strings can contain request details or secrets.
                progress.update(label="Forecast cycle failed", state="error", expanded=True)
                st.error("The forecast cycle could not finish. Check the backend configuration and retry.")
                st.caption("Your current forecast remains selected.")
                return directory

            st.session_state["cycle_display_directory"] = str(published_directory)
            st.session_state["forecast_id"] = forecast_id
            progress.update(label="Forecast cycle complete", state="complete", expanded=False)
        st.success("Displaying the latest forecast from this run. Repeating unchanged inputs reuses existing forecasts.")
        return published_directory
