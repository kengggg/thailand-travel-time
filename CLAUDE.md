# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

Static, single-page polar travel-time chart for Thailand. Inspired by the Tokyo→prefectural-capitals "fastest arrival time map." `PRD.md` is the source of truth for design decisions and version scope; treat it as authoritative when refactoring.

## Common commands

```bash
# Smoke-test the Overpass query in isolation (no API key needed)
./overpass_check.py

# Build data for one tour. Writes data/<tour>.js + outputs/<UTC-iso>__<tour>.json.
export GOOGLE_MAPS_API_KEY=...
./prepare_data.py --tour cnx-district          # 24 อำเภอ from Chiang Mai (DRIVE)
./prepare_data.py --tour cnx-nationwide        # 76 ศาลากลางจังหวัด from Chiang Mai
./prepare_data.py --tour bkk-district          # 50 เขต from Bangkok (DRIVE)
./prepare_data.py --tour bkk-district-transit  # 50 เขต from Bangkok (TRANSIT — needs Routes Advanced enabled)
./prepare_data.py --tour bkk-nationwide        # 76 ศาลากลางจังหวัด from Bangkok

# Plan-only mode (no Google calls — exercises Overpass + address construction)
./prepare_data.py --tour <tour-key> --dry-run

# Open the chart locally
open index.html
```

The Python scripts use uv inline script metadata (`# /// script ... ///`), so `./script.py` runs without venv setup if `uv` is installed.

## Architecture

### The "tour" abstraction

A *tour* is a complete `(origin × destination-scope)` configuration. v1 ships five, defined as `Tour` instances in the `TOURS` dict in `prepare_data.py`. Each tour has its own destination Overpass query, address template, departure-time sampling strategy, travel mode, and display strings. Adding a new tour is purely a config addition (no code changes).

Two `dest_kind` values dispatch the destination logic:
- `"district"` — admin_level=6 within one province (uses `iso` filter + `office_template` directly)
- `"province"` — all admin_level=4 in Thailand. Hardcoded special case: Bangkok-as-destination resolves to `ศาลาว่าการกรุงเทพมหานคร` instead of the templated `ศาลากลางจังหวัด{name}` form.

### Data flow

```
Overpass API ─┐
              ├─→ prepare_data.py ─→ data/<tour>.js  ─→ index.html (loads all)
Google Geocoding ─┤                  outputs/<UTC-iso>__<tour>.json
Google Routes ────┘                  (full request/response archive)
```

The handoff between Python and the browser is `data/<tour>.js`. Each file calls `window.CITY_REGISTRY[<key>] = {...}`. `index.html` loads all five files via `<script src>` tags and merges them into one registry. The picker shows whichever tours have populated `rows`. Selection persists across reloads via `localStorage` (key: `thailand-travel-time:tour`); URL never changes.

### Non-obvious invariants

**Address-based routing, no centroids.** Both origin and destination go to the Routes API as `{address: "..."}` waypoints, not `latLng`. Reason: Overpass polygon centroids land in jungle for mountain districts (อำเภออมก๋อย etc.), giving wrong drive times. The bearing for the chart's polar layout comes from a **separate Geocoding API call per destination** — also why every row costs 2 API calls (Geocoding + Routes), not 1.

**No fallback to centroid on failure.** If Geocoding or Routes fails, the row is recorded as a failure and skipped from the chart. This is intentional: silent fallback would fabricate a different number than the address represents. Failed rows still appear in `outputs/<...>.json` with full request + raw response — `record["geocode"]` and `record["routes"]` are populated **before** any raise, so REQUEST_DENIED bodies are captured for diagnosis.

**Multi-sample worst-case for urban drive tours.** `cnx-district` and `bkk-district` set `departure_samples=(0, 15, 30)`. The script geocodes once, then routes three times at 08:00 / 08:15 / 08:30, and keeps the **max-duration** sample as the result (approximating Google Maps' "30–50 min" UI range). Other tours use `(0,)`. The archive's `routes` field is therefore a **list** of samples, not a single object.

**BMA city-hall address needs the `กรุงเทพมหานคร` qualifier.** Without it, Google's geocoder mis-matches `สำนักงานเขตบางเขน` to a sub-district named บางเขน in Nonthaburi (`administrative_area_level_3`, wrong province). The `bkk-district` and `bkk-district-transit` `office_template` ends in `กรุงเทพมหานคร` for this reason. Other BMA district names happen to be unique enough that Google guesses right, but the qualifier is defensive.

**Origin coord is bearing-only.** It's not used for routing (the address is). Verify the hardcoded coord lands on the right building before each tour's first run, since the chart's compass directions depend on it. Bangkok's `(13.7549, 100.5024)` points at เสาชิงช้า; if Google routes from the newer ดินแดง building, add `"ดินแดง"` to that tour's `skip_short_names` to avoid a 0-min spoke.

**TRANSIT routing is on a different SKU.** `bkk-district-transit` uses `travel_mode="TRANSIT"` and the script omits `routingPreference` in that body (DRIVE-only). Per Google's pricing, TRANSIT bills against **Compute Routes Advanced**, which is enabled separately from Compute Routes Basic in Cloud Console. Some outer Bangkok districts will fail with `ZERO_RESULTS` (no transit coverage); expected.

### `index.html` rendering pipeline

Pure inline JS, no dependencies beyond Google Fonts. The render flow:
1. **Picker:** built from `ORDER` (a fixed display order) filtered by what's in `CITY_REGISTRY`. Default is `bkk-district` (overridden by `localStorage` if present).
2. **`computeItems`:** per row, computes marker `(x, y)` from `(time, bearing)` via `radius(time) = CHART_R_MIN + time × polarK`. `polarK` is per-city: `(chartRMax − CHART_R_MIN) / maxMin`, so each tour's data fills the chart regardless of time scale (CNX→Narathiwat ≈ 25 h vs BKK→Pathum ≈ 41 min).
3. **Density adaptation:** tours with ≥50 rows trigger a `dense` mode (CSS class on `<svg>`). Smaller fonts (11 px), tighter `MIN_GAP` (13), shrunk `CHART_R_MAX` (360) to leave horizontal room for long Thai labels, and lighter leader strokes.
4. **`balanceSides`** (dense only): when one side has ≥5 more labels than the other (CNX-nationwide is ~70 right / 6 left), flips the markers with smallest `|markerX|` from the heavy side to the empty one. Their leaders cross the chart center but use otherwise-empty space.
5. **`nudgeColumn`** per side: cascade-resolves vertical label collisions, with progressive **horizontal fan-out** (`fanStep`/`fanCap`) so deeper-cascade labels splay diagonally instead of stacking parallel. Y-clamps to `[yMin, yMax]` so the cascade can't push labels off the SVG.
6. **Interaction:** hover preview + click-to-lock + Escape/outside-click to clear. Hit-test is JS-driven (`mousemove` + geometric distance to marker) so it works regardless of `pointer-events: none` on dimmed elements. Selection sets `[data-amphoe]` classes on every related SVG element (spoke, leader, marker, label) and the SVG root toggles `data-active` to drive the dim CSS.

### Archive (`outputs/`) shape

Every run writes `outputs/<UTC-iso>__<tour-key>.json` with full provenance:

```jsonc
{
  "run_at": "2026-05-06T...",
  "tour_key": "bkk-district",
  "dest_kind": "district",
  "travel_mode": "DRIVE",
  "field_mask": "routes.duration,routes.distanceMeters",
  "summary": { "total": 49, "succeeded": 49, "failed": 0 },
  "calls": [
    {
      "place": "บางรัก",
      "address": "สำนักงานเขตบางรัก กรุงเทพมหานคร",
      "geocode": { "url": ..., "params": { /* api key stripped */ }, "response": {...}, "coord": [...] },
      "routes": [   // list (one per departure_samples offset)
        { "departure": "...", "body": {...}, "response": {...}, "minutes": ..., "km": ..., "error": null }
      ],
      "result": { "minutes": 17, "km": 4.2, "bearing_deg": 140, "samples_total": 3, "samples_minutes": [15, 17, 17], "worst_offset_minutes": 15 }
    }
  ],
  "data_js_rows": [...]
}
```

API keys are stripped from saved request `params` and `headers` before writing — files are safe to share for debugging.

## Diagnostic patterns

When a tour fails or produces wrong-looking numbers, the archive almost always shows what Google returned:

```bash
python3 -c "import json; a=json.load(open('outputs/<latest>.json'));
[print(c['place'], c['error']) for c in a['calls'] if c['error']]"
```

For a specific failing row, inspect `c['geocode']['response']['results'][0]['formatted_address']` and `['types']` — that tells you whether the geocoder fuzzy-matched to a wrong administrative area (the บางเขน-in-Nonthaburi pattern). Routes-side failures (REQUEST_DENIED, ZERO_RESULTS) appear in `c['routes'][i]['response']` with structured error details.
