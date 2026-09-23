# Horizon dashboard

Horizon is the selected dashboard design. Start it using the [demo guide](DEMO.md).

Bright panels, violet accents and summary cards lead into three tabs: Forecast, Model comparison and Tool trace. The forecast chart has a separate provenance and export rail on wide screens; the rail stacks on narrow screens.

## Review script

1. Select a turbine and forecast issuance. Mean normalized power describes the selected predictions; it is not accuracy.
2. Toggle previous-issuance and actual-power overlays, inspect revisions and provenance, then export the original 96 forecast rows for both turbines.
3. Open Model comparison to inspect the supplied evaluation window and horizon. Open Tool trace for saved execution events.
4. Retain source warnings, degraded status and synthetic disclosures during presentations.

No backend contract or calculation changes are introduced. Horizon interaction tests cover forecast selection, tabs, revision availability and synthetic disclosures. Existing contract tests cover export contents, time alignment, missing actuals and malformed input errors.
