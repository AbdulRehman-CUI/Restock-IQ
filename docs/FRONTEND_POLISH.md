# MarginShift frontend polish

Implemented in the existing Streamlit 1.45.1 / Plotly 6.1.2 application. Existing local refinements were retained. No backend calculations, stored data, dependencies, or API contracts were changed by this task. Nothing was deployed or pushed.

## Visual and interaction changes

- Top navigation uses a compact bordered, translucent surface with a solid fallback, local Unicode icons, native selection state, visible destination names, and hover/focus feedback. Analysis selection remains in the sidebar.
- MarginShift branding enters over 600 ms with an 8 px upward fade and a small teal/cyan accent. Session state removes its entrance class on subsequent reruns.
- Overview has a compact gradient-and-dot introduction. Two slow decorative layers drift by a few pixels; headings and controls remain stable. Small screens disable drift.
- Main summary cards use native metrics, rounded surfaces, tabular figures, restrained shadows, and a 340 ms entrance staggered by 55 ms (505 ms total for four cards). Each page's cards enter only on their first meaningful render; values update immediately.
- Page headings enter over 260 ms when the destination changes. Routine reruns have no heading entrance. Native product subsections remain immediate and stable.
- Priority rows have hover/focus-within emphasis without moving the row or table. Buttons have 160 ms transitions, a 1 px press response, visible focus, and native loading/disabled behavior.
- Scenario results use the shared summary cards and a short result-ready emphasis when the displayed result ID changes. Calculated assumptions, unapplied-change warnings, explicit submission, failure recovery, and successful-submission reuse remain intact. A pending message identifies previous results as saved results.
- Data & Settings displays numbered Ready/Pending step indicators alongside the existing prerequisites, actual job status, and validation feedback.
- All charts use the shared Plotly helper with navy typography, understated grids, horizontal legends, consistent hover styling, and zero transition duration. Observed sales are blue, demand forecasts teal/dashed, receipts cyan bars, and unmet demand red.
- Reduced-motion CSS removes decorative animation, delay effects, and transitions. No remote fonts, assets, JavaScript, polling, or animation dependencies were introduced.

## Streamlit adaptations

Navigation labels remain visible at all times. Streamlit's native radio widget does not provide a supported independent icon/label slot for an accessible icon-only reveal; hiding parts of its internal markup would be brittle. Native keyboard navigation and checked state are preserved. Navigation is intentionally nonsticky so it cannot overlap controls or notifications.

Layered CSS drift replaces scroll-driven parallax. It requires no scripts reaching into Streamlit's parent page. Page changes animate the heading only, keeping tables, charts, and controls stable. There is no animated financial-number interpolation. Native form validation and widget loading retain Streamlit's behavior; this task does not promise reserved-height validation or animated subsection switching.

CSS targets authored classes, documented `st.container(key=...)` classes, and semantic elements within those scopes. It does not use generated classes, element-order selectors, internal test IDs, or invisible control overlays. Theme colors originate in `common.COLORS`; spacing, radii, shadows, and timing tokens are in `styles.css`. The existing light `.streamlit/config.toml` theme is retained.

## Files changed by this task

- `app/ui/common.py`: shared theme loading, session motion tracking, native heading/card wrappers, Plotly refinements.
- `app/ui/styles.css` (new): scoped design tokens, surfaces, responsive styles, motion and accessibility fallbacks.
- `app/ui/streamlit_app.py`: functional top navigation and branding; shared empty Overview header.
- `app/ui/overview.py`: keyed priority cards.
- `app/ui/product.py`: distinct receipt color.
- `app/ui/scenario.py`: shared cards, pending clarification, result-ready emphasis.
- `app/ui/data_settings.py`: numbered setup indicators.
- `tests/test_ui_motion.py` (new): AppTest navigation, session entrances, immediate metric updates, and local-style checks.
- `docs/FRONTEND_POLISH.md` (this report).

Model Performance benefits from the shared heading, metric, and chart helpers without page-specific changes. Other modified files shown by Git predate this task.

## Verification

- Full suite: 129 passed, one existing AnyIO deprecation warning.
- Final affected UI/API suite after the last card/setup edits: 20 passed, one existing AnyIO deprecation warning (22.70 seconds).
- Python compilation of `app/ui`: passed.
- `git diff --check`: passed (Git emitted line-ending notices).
- AppTest exercises all five destinations, ordinary reruns, native navigation, branding entrance suppression, and immediate metric updates without backend writes.
- Existing regression tests cover saved-context retention, selected-run downloads, scenario duplicate submission reuse and retry, forecast reuse, error recovery, caches, inventory, pricing, forecasting, and model evaluation.
- FastAPI TestClient initially stalled in the restricted Windows sandbox while creating asyncio's internal socket pair. The full test suite passed outside that sandbox using isolated test data under `runtime`. AppTest itself needed no browser.
- No browser, browser automation, screenshots, or visual verification was performed. CSS appearance, focus appearance, and responsive layout require the manual review below.

## Startup

From the project directory, use separate PowerShell terminals:

```powershell
.\.venv\Scripts\python.exe -m app.cli serve --port 8000
```

```powershell
.\.venv\Scripts\python.exe -m app.cli ui --port 8501 --api-url http://127.0.0.1:8000
```

Open http://127.0.0.1:8501. Existing README setup and optional `prepare-demo` instructions still apply. No dependency installation is needed for this redesign.

## Manual visual review

1. Open a fresh session: branding should enter once. Change a filter and page, then return: branding and previously viewed summary cards should stay steady.
2. Review all five destinations on desktop and a narrow/mobile viewport. Check navigation wrapping, card stacking, financial-value readability, and absence of page-shell horizontal overflow.
3. Use Tab and arrow keys through native navigation, selectors, forms, and buttons. Check visible focus, selected destination, and touch-friendly labels.
4. Enable the operating system's reduced-motion setting. Branding, card entrances, page headings, and background drift should become static. Narrow screens should also stop the background drift.
5. On Overview, check subtle background movement, readable text, priority actions near the top, stable table scrolling, and retained sorting/filtering.
6. Inspect Products charts: blue observed sales, dashed teal forecasts, cyan receipts, red unmet demand. Check legends and hover information at narrow widths.
7. Run a scenario; check pending wording, result-ready emphasis, base/scenario comparisons, hypothetical labels, and unchanged saved results when editing unapplied assumptions.
8. Check setup steps, missing-file feedback, failed-job recovery, and a selected-run download. Verify synthetic-data and uncertainty labels remain prominent.
