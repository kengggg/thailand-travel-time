# Chiang Mai Amphoe Travel-Time Map — PRD v1

*Status: Phase 0 in progress — Overpass smoke test pending.*
*Last updated: 2026-05-05*

---

## 1. Background

Inspired by the Japanese **"Tokyo → Prefectural Capitals fastest arrival time map"** — a radial polar chart with concentric rings labeled as clock times (8:00, 9:00, 10:00...), spokes drawn at true geographic bearing from Tokyo, and each destination plotted at the ring corresponding to its earliest possible arrival time. The Japanese version uses two colors to distinguish "flight + ground" vs "ground only" routes. The chart is striking because it reads simultaneously as a map (geographic direction) and as a schedule (arrival time).

This PRD covers a **Thailand-localized PoC** of that concept, scoped down to a single province for the first version.

### Vision (multi-version roadmap)

This PRD only covers v1. Future versions are noted here for context but are explicitly out of scope:

- **v1 (this PRD)** — Single SPA (`index.html`) with a tour picker; five tours ship as selectable: Chiang Mai → 24 อำเภอ (drive), Chiang Mai → 76 ศาลากลางจังหวัด (drive), Bangkok → 50 เขต (drive), Bangkok → 50 เขต (transit), Bangkok → 76 ศาลากลางจังหวัด (drive). Single-color spokes (no second-dimension encoding — see §4); per-tour travel mode replaces what would otherwise be a colour encoding.
- **v2** — Add traffic-impact-ratio encoding (compare 08:00 vs free-flow times) and side-by-side drive-vs-transit comparison view; reuses v1's pipeline + picker.
- **v3** — 50×50 Bangkok inter-district matrix as a heatmap with origin-picker overlay
- **v4** — Bangkok → 76 provincial capitals, multimodal (road / train / flight), color = winning mode

Each version stands alone; later versions reuse v1's data pipeline and visualization patterns.

---

## 2. Goal (v1)

A single-page static HTML chart showing **arrival clock times** by car from ศาลากลางจังหวัดเชียงใหม่ to each of the 24 other amphoe offices in Chiang Mai province, assuming a fixed 08:00 Monday departure (a normal working-day morning, including rush-hour traffic) and Google's traffic-aware historical model.

The deliverable is one HTML file plus a one-shot Python pipeline that regenerates the data.

---

## 3. Architecture

```
┌──────────────────┐    ┌──────────────────────────────┐    ┌────────────────────┐
│  Overpass API    │ →  │  prepare_data.py --tour X    │ →  │  data/<tour>.js    │
│  (admin_level=6  │    │  Google Geocoding API   ─┐   │    │  CITY_REGISTRY[X]  │
│   for district;  │    │  (per-destination coord) │   │    │  = { ..., rows }   │
│   admin_level=4  │    │  Google Routes API     ──┤   │    └─────────┬──────────┘
│   for nationwide)│    │  (TRAFFIC_AWARE drive)   │   │              │
│                  │    │                          ▼   │              ▼
│                  │    │  outputs/<iso>__<tour>.json  │    ┌────────────────────┐
└──────────────────┘    └──────────────────────────────┘    │ index.html         │
                                                            │ (tour picker +     │
                                                            │  dynamic SVG)      │
                                                            └────────────────────┘
```

A "tour" is one (origin × destination-scope) configuration. v1 ships four:
`cnx-district`, `cnx-nationwide`, `bkk-district`, `bkk-nationwide`.

Pipeline files:

| File | Purpose | Run time |
|---|---|---|
| `overpass_check.py` | Smoke-test an Overpass query in isolation | ~5 sec |
| `prepare_data.py` | Per-tour pipeline: Overpass → Geocoding + Routes → `data/<tour>.js` + archive. Selected via `--tour {cnx-district, cnx-nationwide, bkk-district, bkk-nationwide}` | ~15–60 sec |
| `index.html` | Single SPA with tour picker; renders the selected tour's data dynamically | static |
| `data/<tour>.js` | Per-tour payload: each file calls `window.CITY_REGISTRY[<key>] = {...}` with all the metadata + rows index.html needs | static |

The handoff is `data/<tour>.js`: each pipeline run overwrites one tour's file. `index.html` loads all of them via `<script src>` tags and merges them into `window.CITY_REGISTRY`, then the picker exposes whichever tours have been generated. Selection is in-page only (no URL change), persisted across reloads via `localStorage`. Default: `bkk-district` if it has rows, otherwise the first tour in the registry that does.

---

## 4. Decisions & Rationale

| Decision | Choice | Rationale |
|---|---|---|
| Routing engine | **Google Routes API** | Best traffic-aware data for Thailand (Android fleet density); measured speed data is more accurate than OSM speed tags on Thai mountain roads. Cost stays at $0 for one-shot or daily renders (24 calls × any frequency ≪ 5,000/mo Pro free tier). |
| Routes endpoint | **Compute Routes** (per-route), not Compute Route Matrix | 24 calls is small enough that per-route is simpler and easier to debug. Matrix would save no money and gain no features. |
| Routing preference | **`TRAFFIC_AWARE`** (not `TRAFFIC_AWARE_OPTIMAL`) | Similar accuracy for our use case, higher rate limits, larger element caps. `OPTIMAL` is for live navigation. |
| Duration aggregation | **Worst-case across a 30-min departure window** for short urban DRIVE tours (`cnx-district`, `bkk-district`); single 08:00 sample for nationwide DRIVE and TRANSIT. | Google Maps' web UI shows ranges like "30–50 min" for rush-hour drives; the Routes API returns only a single (median) number. To approximate the upper bound of that range, the script samples the same destination at 08:00, 08:15, and 08:30 and keeps the maximum. Tripling Routes calls per row is well within free tier (49 × 3 = 147 calls per render). Single-sample retained for tours where the window-variance is small relative to total trip time (long-distance highways) or schedule-driven (transit). The archive captures all samples for transparency; per-tour `departure_samples` controls the offsets list. |
| Travel mode | **Per-tour.** Default `DRIVE` (Routes Pro, TRAFFIC_AWARE). `bkk-district-transit` uses `TRANSIT` (Routes Advanced; BTS / MRT / city bus per Google's transit data). | v1 is single-mode-per-tour, but mode is now a Tour field rather than a global. `routingPreference` is sent only with `DRIVE` — TRANSIT errors out if it's included. Bangkok is the only place in Thailand where Google's transit data has meaningful coverage; transit tours for other cities are deferred. `TWO_WHEELER` (motorcycle), `WALK`, `BICYCLE` still deferred. Flight isn't in Routes API at all (separate flights endpoint). |
| Routes SKU | **DRIVE → Compute Routes Basic** (5,000/mo free); **TRANSIT → Compute Routes Advanced** (smaller free tier; verify in Cloud Console after first run) | The minimal field mask `routes.duration,routes.distanceMeters` keeps DRIVE on Basic. TRANSIT bumps the SKU regardless of field mask — Google charges Advanced for the underlying transit-graph access. Both APIs are still under their respective free tiers at v1's call volume. |
| Departure time | **Fixed real future timestamp**: next Monday 08:00 ICT | A normal working-day morning — captures realistic rush-hour traffic on the close-in amphoes (Saraphi, Sansai, Sankamphaeng) rather than the artificially-clean numbers an early-morning departure would give. Google uses `departureTime` to pull historical traffic for that day-of-week + time-of-day, so a fixed forward-dated timestamp gives reproducible numbers across runs. |
| Field mask | **`routes.duration,routes.distanceMeters`** only | Minimal mask keeps the call billed at Pro SKU. Adding `routes.legs` or `routes.polyline` bumps to Enterprise SKU. |
| Routing endpoints | **Address waypoints**: origin = `ศาลากลางจังหวัดเชียงใหม่`, destinations = `ที่ว่าการอำเภอ{name} จังหวัดเชียงใหม่` | Overpass district centroids are polygon centers — for mountain อำเภอ they sit in jungle, miles from any road. Routing to the named landmark is what readers interpret as "drive to อำเภอ X". Routes API geocodes the address inline (no extra billing event, still Pro SKU). For v2 Bangkok: origin = `ศาลาว่าการกรุงเทพมหานคร`, destinations = `ที่ทำการเขต{name}`. |
| Origin coordinate | `(18.85283, 98.96739)` — ศาลากลาง at ศูนย์ราชการ on Hwy 11 | Hardcoded bearing-reference origin. Routing uses the address (above). User should verify the coord lands on the right building since bearings depend on it. |
| Destination bearing reference | **Geocoding API call per office** → use returned coord for the chart's bearing math | Centroid is removed from the script entirely. The chart's spoke direction now points at the same place Google routes to (the geocoded ที่ว่าการอำเภอ), not at a polygon centroid that may sit miles away on a ridge. Two API calls per district (Geocoding + Routes), both within free tier. |
| Failure policy | **No centroid fallback.** If Geocoding *or* Routes fails for a row, the row is recorded as a failure and skipped. | The address *is* the answer the chart reports — falling back to a centroid would silently fabricate a different number. Better to surface the failure, fix the address (or hardcode a known-good place id), and re-run. Full archive makes diagnosis cheap. |
| Per-run archive | Every Geocoding + Routes call (request, raw response, parsed result) saved to `outputs/<UTC-iso>__<province-iso>.json` | We pay for the data; capturing it lets us re-derive `data.js`, debug a specific row, or compare runs without re-billing Google. API key stripped from saved params/headers; safe to share. |
| Origin label in chart | "ศาลากลางเชียงใหม่" | Matches everyday Thai usage. |
| Skip list | `{"เมืองเชียงใหม่", "Mueang Chiang Mai"}` | Origin and destination are the same place; chart would show a 0-min spoke. |
| Chart layout | **Polar, true-bearing spokes, ring per hour** | Matches Japanese reference. Geographic and temporal info are both readable. |
| Color encoding | **Single-color spokes (#555).** No second-dimension encoding in v1. | The Japanese reference uses two colors for *mode of transport* (flight vs ground) — a fact about the trip, cleanly binary. On a single-mode chart the equivalent doesn't exist: a speed-derived terrain heuristic conflates traffic with elevation (rush-hour suburbs read "mountain", highways across passes read "lowland"); a manual mountain list is subjective; an origin↔destination elevation diff misses the route's actual climb-and-descent profile (Fang's endpoint diff is +170m but the route crests at ~700m). All three are wrong-or-misleading more often than they're informative. v1 ships with rings + spokes only — three honest dimensions (direction, time, arrival clock) instead of four with one fabricated. v2's traffic-impact ratio reintroduces a clean second axis when it becomes the chart's primary story. |
| Departure time framing | "ออกเดินทาง 08:00 น." → arrival clock times | Matches Japanese reference (which uses arrival time, not duration). Single departure moment makes the chart unambiguous. Rings are labeled with arrival times (09:00, 10:00, 11:00, 12:00, 13:00). |
| Typography | **Google Sans** (Latin + numerals) with **Noto Sans Thai** as the Thai-script fallback | Updated 2026-05-05 from IBM Plex Sans Thai/IBM Plex Sans. Cleaner, more modern feel; Google Sans handles Latin glyphs and time numerals while Noto Sans Thai picks up the Thai block via the font-family fallback chain. Both are on Google Fonts so the chart still has zero non-Google-Fonts asset dependencies. |

---

## 5. Phases

### Phase 0 — Overpass smoke test ⚙️ *in progress*

**Goal:** Verify the Overpass query returns the expected amphoes before paying (any amount of) Google API calls.

**Inputs:** None (no API key, no config).

**Run:**
```bash
./overpass_check.py
```

**Acceptance criteria:**
- [x] Script runs without error
- [ ] Returns **25 relations** (24 amphoes we want + Mueang Chiang Mai which the build script will filter)
- [ ] All 25 have `name:th` populated
- [ ] All 25 have a `center` coordinate
- [ ] Names match expected list (Saraphi, San Sai, Hang Dong, San Kamphaeng, Mae Rim, Doi Saket, Mae On, San Pa Tong, Mae Taeng, Doi Lo, Mae Wang, Samoeng, Phrao, Chom Thong, Chiang Dao, Hot, Chai Prakan, Doi Tao, Fang, Mae Ai, Mae Chaem, Wiang Haeng, Galyani Vadhana, Omkoi, Mueang Chiang Mai)

**If acceptance fails:**
- Wrong count → adjust the area filter (try `name="Chiang Mai Province"` or `name:en="Chiang Mai"` instead of `ISO3166-2="TH-50"`)
- Missing `name:th` → fall back to `name` (build script already does this)
- Missing center → re-run with `out geom;` and compute centroid client-side

**Deliverable:** Console output proving 25 named amphoes with coordinates.

---

### Phase 1 — Google Routes pipeline 🚧

**Goal:** Replace the placeholder data in `cnx_amphoe_map.html` with real Google traffic-aware travel times.

**Inputs:**
- `GOOGLE_MAPS_API_KEY` env var
- **Both** Routes API **and** Geocoding API enabled on the project
- API key restricted to those two APIs (and only those two)
- Daily quota cap of $5 set on the key (defense in depth — actual cost will be $0)

**Run (one tour at a time):**
```bash
export GOOGLE_MAPS_API_KEY=...
./prepare_data.py --tour cnx-district           # writes data/cnx-district.js
./prepare_data.py --tour cnx-nationwide         # writes data/cnx-nationwide.js
./prepare_data.py --tour bkk-district           # writes data/bkk-district.js (DRIVE)
./prepare_data.py --tour bkk-district-transit   # writes data/bkk-district-transit.js (TRANSIT)
./prepare_data.py --tour bkk-nationwide         # writes data/bkk-nationwide.js
# each also writes outputs/<UTC-iso>__<tour>.json
```

**Output format** (`data/<city>.js`):
```js
window.CITY_REGISTRY = window.CITY_REGISTRY || {};
window.CITY_REGISTRY["cnx"] = {
  key: "cnx",
  displayName: "เชียงใหม่",
  // ... origin, destLabel, departure, snapshotDate, etc.
  rows: [
    ["สารภี",     25, 155],
    ["สันทราย",   19,  93],
    ...
  ],
};
// Each row: [name (str, prefix-stripped), duration_minutes (int), bearing_degrees (int 0-359)]
// Sorted ascending by duration_minutes.
```

**Acceptance criteria (per tour):**
- [ ] Script completes with no errors. *Some skipped rows are expected for `bkk-district-transit`* — outer districts where Google has no transit route return ZERO_RESULTS. Expected count: ≥ 30/49 succeed; far districts (Nong Chok, far Lat Krabang, etc.) may fail.
- [ ] Row counts: cnx-district 24, cnx-nationwide 76, bkk-district ~49, bkk-district-transit ~30–49, bkk-nationwide 76
- [ ] All durations sane: districts (drive) 10–360 min; districts (transit) 15–120 min; nationwide 10–1000 min
- [ ] All bearings in 0–359
- [ ] cnx-district Saraphi ≈ 20–30 min at 08:00; Omkoi ≈ 180–240 min
- [ ] cnx-nationwide includes กรุงเทพมหานคร with destination resolved as `ศาลาว่าการกรุงเทพมหานคร` (special case, not the templated form)
- [ ] bkk-district-transit inner districts (Bang Rak, Sathon, Pathum Wan, Phaya Thai) ≈ 15–35 min; same destinations under bkk-district may be slower in 08:00 traffic — that's the comparison the chart pair tells.
- [ ] Total Google billable events: exactly 2× the row count (Geocoding + Routes; no fallback). For `bkk-district-transit`, Routes calls are billed under the **Advanced** SKU rather than Basic — verify in Cloud Console.
- [ ] `outputs/<UTC-iso>__<tour-key>.json` written with one entry per destination containing both the Geocoding and Routes request/response/result. Archive's `travel_mode` field reflects the tour's mode (`DRIVE` or `TRANSIT`).

**Deliverable:** `data/<city>.js` written; `index.html` picks it up via `<script src="data/<city>.js"></script>` on the next browser refresh — no copy-paste step. The city becomes selectable in the picker.

---

### Phase 2 — HTML chart with real data 🚧

**Goal:** The chart renders correctly with the real data from Phase 1.

**Run:**
```bash
open index.html  # or just double-click
```

**Acceptance criteria:**
- [ ] City picker visible at the top of the frame, listing all generated cities
- [ ] Default city is `bkk` if its data file has rows; otherwise the first city with rows; selection persists across reloads via localStorage
- [ ] Selecting a city re-renders the title bar, note box, center circle, rings + ring labels (with arrival times derived from departure + step), spokes, markers, labels — without changing the URL
- [ ] Empty state for a city with no rows: hide rings/markers/labels; show "🏗 ข้อมูลยังไม่พร้อม" message; note box explains how to populate
- [ ] All amphoes/districts for the selected city appear as yellow markers on the chart
- [ ] Center circle text matches the city's origin (e.g., "ศาลากลาง / เชียงใหม่" for cnx, "ศาลาว่าการ / กทม." for bkk)
- [ ] Ring labels read arrival times relative to the city's departure (CNX 08:00 → 09:00, 10:00, ...)
- [ ] Each label includes the city's prefix and arrival clock time (e.g., "อ.สารภี 8:25" for cnx, "เขตบางรัก 8:30" for bkk)
- [ ] All spokes single neutral color (no terrain encoding in v1)
- [ ] Compass-direction sanity check (cnx): Fang/Mae Ai point north, Hot/Omkoi south, Samoeng west, Doi Saket/Mae On east
- [ ] No JavaScript console errors

**Interaction acceptance:**
- [ ] Hovering a marker (within ~14 px) or its label highlights that amphoe — others fade to opacity ~0.18
- [ ] Cursor leaving the chart (or hitting empty space) restores all amphoes to full brightness (unless one is locked)
- [ ] Clicking an amphoe locks the highlight; preview-on-hover still works while locked, snapping back to the locked amphoe when the cursor leaves
- [ ] Clicking the same locked amphoe again unlocks it
- [ ] Clicking outside any amphoe (chart background) clears the locked selection
- [ ] Pressing Escape clears the locked selection
- [ ] Locked amphoe shows enlarged marker (r=8.5) and bolder label (font-weight 700); the emphasis persists faintly (at dimmed opacity) when previewing another amphoe — the user can see what they had locked
- [ ] No flicker on hover transitions between adjacent amphoes

**Deliverable:** Working static HTML — opens in any browser, no server, no dependencies beyond Google Fonts.

---

### Phase 3 — Visual polish 🔮

**Goal:** Production-ready chart suitable for sharing or printing.

**Sub-tasks:**

1. ~~**Label collision in central cluster**~~ Addressed in v1 with per-side, single-pass label nudging plus thin leader lines on the nudged labels only. Algorithm sorts left-side and right-side labels separately by y, walks each list maintaining a "most-recent-in-column" predecessor (skipping outliers whose markerX is > 80 px from the running column), and pushes any label whose y is within 17 px of its predecessor down by 17 px while shifting its x outward by 30 px. Nudged labels get a 0.6-px gray leader line from the marker to the right edge (left side) or left edge (right side) of the text. Non-clustered labels render exactly as before — leader machinery only appears where the cluster forces it. The simple algorithm handles the SW chain and the close-in north cluster cleanly. v2 (Bangkok 50 districts) may exceed it and need d3-force; revisit then.

2. **Time spot-checks for remote amphoes**
   Cross-check Google's traffic-aware times for the deepest-mountain destinations against a known recent drive. Omkoi 186 min and Galyani Vadhana 202 min at 08:00 look plausible but haven't been ground-truthed; Google's data quality is known to drop in low-fleet-density mountain areas.

3. **Print stylesheet** *(optional)*
   `@media print` rules: white background, no frame shadow, ensure SVG scales to A3.

4. **Description box accuracy** *(optional)*
   Update the notes box to credit Google as the data source, mention the departure timestamp used.

**Acceptance criteria:**
- [ ] No two labels overlap
- [ ] Mountain amphoes look right to someone who knows the roads
- [ ] (Optional) Chart prints cleanly to A3 with no clipping

---

## 6. Cost & safety

| Item | Value |
|---|---|
| Per-render calls | 24 Geocoding + 24 Routes = 48 total |
| Per-render cost | $0 (Geocoding free tier 10,000/mo; Routes Pro free tier 5,000/mo) |
| Daily render cost (48 × 30 = 1,440/mo) | $0 |
| Hourly render cost (48 × 24 × 30 = 34,560/mo) | ~$240/mo (don't do this) |
| Required APIs enabled | Routes API **and** Geocoding API |
| Required key restrictions | Restricted to those two APIs only; IP-restricted if possible |
| Required quota cap | $5/day (in Cloud Console) |
| Required Routes field mask | `routes.duration,routes.distanceMeters` |

---

## 7. Repository layout

```
thailand-travel-time/
├── PRD.md                    ← this document
├── overpass_check.py         ← phase 0
├── prepare_data.py           ← phase 1; takes --tour {cnx-district,cnx-nationwide,bkk-district,bkk-nationwide}
├── index.html                ← phase 2; tour picker + dynamic SVG, loads every data/*.js
├── data/                     ← per-tour payloads (CITY_REGISTRY entries)
│   ├── cnx-district.js          ← Chiang Mai → 24 อำเภอ (drive)
│   ├── cnx-nationwide.js        ← Chiang Mai → 76 ศาลากลางจังหวัด (drive)
│   ├── bkk-district.js          ← Bangkok → 50 เขต (drive)
│   ├── bkk-district-transit.js  ← Bangkok → 50 เขต (transit; BTS / MRT / bus)
│   └── bkk-nationwide.js        ← Bangkok → 76 ศาลากลางจังหวัด (drive)
├── outputs/                  ← phase 1 archive: one JSON per run, full Geocoding + Routes capture
│   └── <UTC-iso>__<tour-key>.json
└── README.md                 ← short usage notes
```

Both Python scripts use `uv` inline script metadata (`# /// script ... ///`), so no `requirements.txt` or virtualenv setup needed — `./script.py` just works if `uv` is installed.

---

## 8. Out of scope (v1)

- Bangkok or any other city
- Multimodal (train, flight)
- Interactive features (hover tooltips, click-to-pin)
- Real-time data updates
- Multiple departure times in one chart
- Province-level (we're going one level deeper to amphoe)
- Mobile-responsive layout
- Internationalization (chart is Thai-only by design)
- Self-hosted OSRM as a free alternative (rejected in favor of Google for accuracy)

---

## 9. Open questions

- **Origin coordinate verification.** `(18.85283, 98.96739)` is the bearing reference; routing uses the address `ศาลากลางจังหวัดเชียงใหม่`. The coord still matters because the chart's compass directions are computed from it. Verify it lands on the correct building (ศูนย์ราชการ on ถนนโชตนา) before the first Phase-1 run. Also worth one-time-checking that Google geocodes `ศาลากลางจังหวัดเชียงใหม่` to the same building — if Google still points at the old city hall, override the origin to a `latLng` waypoint.
- ~~**Departure time choice.**~~ Resolved: 08:00 ICT, normal working-day morning, captures rush-hour traffic on close-in amphoes. Earlier 06:00 setting was abandoned because the artificially-clean numbers didn't reflect typical commute experience.
- ~~**Mae Ai / Fang terrain classification.**~~ Obviated 2026-05-05. v1 dropped the terrain encoding entirely after speed-based, manual-list, and elevation-diff approaches all proved either wrong or arbitrary (see §4 "Color encoding" for the reasoning). If a future version reintroduces a second-dimension encoding, ฝาง / แม่อาย / ไชยปราการ are the canonical "highway-but-route-crosses-mountains" edge cases to test it against.

---

## Appendix A — Google Routes API request shape

```python
POST https://routes.googleapis.com/directions/v2:computeRoutes
Headers:
  Content-Type:     application/json
  X-Goog-Api-Key:   $GOOGLE_MAPS_API_KEY
  X-Goog-FieldMask: routes.duration,routes.distanceMeters

Body:
{
  "origin":      {"address": "ศาลากลางจังหวัดเชียงใหม่"},
  "destination": {"address": "ที่ว่าการอำเภอสารภี จังหวัดเชียงใหม่"},
  "travelMode":        "DRIVE",
  "routingPreference": "TRAFFIC_AWARE",
  "departureTime":     "2026-05-11T08:00:00+07:00"
}
Response (relevant fields):
{
  "routes": [{
    "duration":       "1234s",   // string ending in 's'
    "distanceMeters":  18540
  }]
}
```

## Appendix B — Visual design tokens

```
Background:        #efece2  (warm off-white, paper)
Frame:             white with #1a1a1a 2px border, 6px hard shadow
Title bar:         #0a3982 fill, white text
Center circle:     #ffd700 fill, #1a1a1a 2px stroke
Marker:            #ffd700 fill, #1a1a1a 1.2px stroke, r=6.5
Spoke:             #555, 1.6px (single neutral color — no second-dimension encoding in v1)
Leader (label):    #888, 0.6px (drawn only for labels nudged by collision resolver)

Interaction tokens:
Dim opacity:       0.18 (non-active amphoes when something is hovered or locked)
Hover hit radius:  14px around marker (invisible — labels also clickable via bbox)
Selected marker:   r 6.5 → 8.5, stroke-width 1.2 → 1.5
Selected label:    font-weight 500 → 700
Transition:        opacity 0.12s ease (no transition on selection toggle)
Ring:              #b5b5b0, 0.7px, dasharray 2 3
Label:             #1a1a1a, 13px, IBM Plex Sans Thai 500
Time numerals:     #555,    13px, IBM Plex Sans 600
Ring labels:       #777,    13px, IBM Plex Sans 500
```

Polar coordinate math: `r = 50 + duration_minutes × 1.3` (50px center + 78px per hour).
