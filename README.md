# Thailand travel-time chart

Static polar travel-time chart for Thailand, inspired by the Tokyo → prefectural-capitals "fastest arrival time map." Each marker's radius from origin is the drive (or transit) time at the next Monday 08:00 ICT; each marker's angle is its true geographic bearing.

→ **Live: https://kengggg.github.io/thailand-travel-time/**

## What's in the picker

| Tour | Origin | Destinations | Mode |
|---|---|---|---|
| `bkk-district` | ศาลาว่าการกรุงเทพมหานคร | 49 เขต (own district skipped) | DRIVE, traffic-aware |
| `bkk-district-transit` | ศาลาว่าการกรุงเทพมหานคร | 50 เขต (some far districts may fail with ZERO_RESULTS — no transit coverage) | TRANSIT (BTS / MRT / bus) |
| `bkk-nationwide` | ศาลาว่าการกรุงเทพมหานคร | 76 ศาลากลางจังหวัด | DRIVE |
| `cnx-district` | ศาลากลางจังหวัดเชียงใหม่ | 24 อำเภอ | DRIVE, traffic-aware |
| `cnx-nationwide` | ศาลากลางจังหวัดเชียงใหม่ | 76 ศาลากลางจังหวัด | DRIVE |

For urban DRIVE tours (`*-district`), durations are the worst-case across an 08:00–08:30 departure window — approximating the upper bound of Google Maps' "X–Y min" rush-hour range. Nationwide and transit tours use a single 08:00 sample (rush-hour variance is dilute over long-distance highways and irrelevant to schedule-driven transit).

## Refreshing data

```bash
export GOOGLE_MAPS_API_KEY=...     # needs Routes API + Geocoding API enabled
./prepare_data.py --tour cnx-district
./prepare_data.py --tour bkk-nationwide
# ... etc.
git add data/ && git commit -m "Refresh data" && git push
```

GitHub Pages auto-deploys on push to `main`. CDN cache refreshes within ~10 min; hard-refresh the browser to see new numbers immediately.

A dry-run mode exercises Overpass + address construction without calling Google:

```bash
./prepare_data.py --tour <tour-key> --dry-run
```

## Design and architecture

- [`PRD.md`](PRD.md) — design decisions, rationale, multi-version roadmap
- [`CLAUDE.md`](CLAUDE.md) — non-obvious invariants for AI tooling and future contributors
- [`prepare_data.py`](prepare_data.py) — pipeline; tours configured in the `TOURS` dict (uv inline-script metadata, runs without venv setup)
- [`overpass_check.py`](overpass_check.py) — Overpass smoke test (no API key needed)
- `data/<tour>.js` — per-tour `window.CITY_REGISTRY[<key>] = {...}` payloads loaded by `index.html`
- `outputs/` — full per-run request/response archives (gitignored; local only)
