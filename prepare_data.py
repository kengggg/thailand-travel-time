#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""
Prepare per-tour travel-time data for the Thailand chart.

A "tour" is a complete (origin × destination-scope) configuration:

    cnx-district     Chiang Mai → 24 อำเภอ in Chiang Mai
    cnx-nationwide   Chiang Mai → 76 ศาลากลางจังหวัด across Thailand
    bkk-district     Bangkok    → 50 เขต in Bangkok
    bkk-nationwide   Bangkok    → 76 ศาลากลางจังหวัด across Thailand

Usage:
    export GOOGLE_MAPS_API_KEY=...
    ./prepare_data.py --tour cnx-district
    ./prepare_data.py --tour bkk-nationwide --dry-run

Output per tour:
  ./data/<tour-key>.js                     — namespaced CITY_REGISTRY entry
  ./outputs/<UTC-iso>__<tour-key>.json     — full Geocoding + Routes archive

Engines:
  Google Geocoding API  — authoritative coord per destination (used for the
                          chart's bearing math, not for routing)
  Google Routes API     — Compute Routes, TRAFFIC_AWARE drive

Two API calls per destination. Free-tier ceilings: 10,000/mo Geocoding,
5,000/mo Routes Pro — every tour stays at $0 even with daily renders.

No-fallback policy:
  Address routing only. If Geocoding *or* Routes fails for a destination,
  the row is recorded as a failure and skipped — no centroid fallback.
  Failed rows still appear in the archive (full request + raw response).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx


# ─── tour config ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class Tour:
    # Identity
    key: str                                 # 'cnx-district', 'bkk-nationwide', ...
    dest_kind: str                           # 'district' | 'province'

    # Routing
    origin_address: str
    origin_coord: tuple[float, float]        # bearing reference (verify before first run)

    # Destination scope (kind-specific)
    iso: str                                 # district: ISO3166-2 of containing province; nationwide: 'TH'
    name_prefix: str                         # 'อำเภอ' / 'เขต' / 'จังหวัด'
    office_template: str                     # district kind only — e.g. 'ที่ว่าการอำเภอ{name} จังหวัดเชียงใหม่'
    skip_short_names: frozenset[str]         # short names to drop (origin's own / duplicates)

    # Display (written verbatim into data/<key>.js)
    display_name: str                        # 'เชียงใหม่' / 'กรุงเทพมหานคร'
    english_name: str
    picker_label: str                        # 'เชียงใหม่ → 24 อำเภอ' (button text)
    title_origin: str                        # short title-bar form
    origin_short_line1: str
    origin_short_line2: str
    dest_unit: str                           # counted unit: 'อำเภอ' / 'เขต' / 'จังหวัด'
    dest_label: str                          # title destination noun
    dest_prefix: str                         # row label prefix

    # Departure
    departure_hour: int
    departure_minute: int
    departure_day_thai: str                  # 'จันทร์'
    time_context_thai: str                   # 'เช้าวันจันทร์'

    # Routing mode. 'DRIVE' = traffic-aware car (default). 'TRANSIT' = public
    # transit (BTS/MRT/bus) — Bangkok-only in practice; may use a different
    # Google Routes SKU and require Compute Routes Advanced enabled on the key.
    travel_mode: str = "DRIVE"

    # Departure-time sampling, in minutes-offsets from the base departure.
    # Multiple samples → the script keeps the WORST-case (max) duration across
    # samples, approximating the upper bound of the duration range that
    # Google Maps' web UI displays for rush-hour drives. Default (0,) keeps
    # the existing single-sample behavior. Use (0, 15, 30) for short urban
    # DRIVE tours where 30-min variability is meaningful; leave at (0,) for
    # transit (schedule-driven) and long-distance drives (variance dilutes).
    departure_samples: tuple[int, ...] = (0,)


# ─── tours ────────────────────────────────────────────────────────────
TOURS: dict[str, Tour] = {
    "cnx-district": Tour(
        key="cnx-district",
        dest_kind="district",
        origin_address="ศาลากลางจังหวัดเชียงใหม่",
        origin_coord=(18.85283, 98.96739),
        iso="TH-50",
        name_prefix="อำเภอ",
        office_template="ที่ว่าการอำเภอ{name} จังหวัดเชียงใหม่",
        skip_short_names=frozenset({"เมืองเชียงใหม่"}),
        display_name="เชียงใหม่",
        english_name="Chiang Mai",
        picker_label="เชียงใหม่ → 24 อำเภอ",
        title_origin="ศาลากลางเชียงใหม่",
        origin_short_line1="ศาลากลาง",
        origin_short_line2="เชียงใหม่",
        dest_unit="อำเภอ",
        dest_label="ที่ว่าการอำเภอ",
        dest_prefix="อ.",
        departure_hour=8,
        departure_minute=0,
        departure_day_thai="จันทร์",
        time_context_thai="เช้าวันจันทร์",
        departure_samples=(0, 15, 30),
    ),
    "cnx-nationwide": Tour(
        key="cnx-nationwide",
        dest_kind="province",
        origin_address="ศาลากลางจังหวัดเชียงใหม่",
        origin_coord=(18.85283, 98.96739),
        iso="TH",
        name_prefix="จังหวัด",   # OSM names like "จังหวัดอ่างทอง" → strip → "อ่างทอง"; Bangkok stays bare
        office_template="",      # unused for province kind
        skip_short_names=frozenset({"เชียงใหม่"}),  # skip own province
        display_name="เชียงใหม่",
        english_name="Chiang Mai (nationwide)",
        picker_label="เชียงใหม่ → 76 จังหวัด",
        title_origin="ศาลากลางเชียงใหม่",
        origin_short_line1="ศาลากลาง",
        origin_short_line2="เชียงใหม่",
        dest_unit="จังหวัด",
        dest_label="ศาลากลางจังหวัด",
        dest_prefix="จ.",
        departure_hour=8,
        departure_minute=0,
        departure_day_thai="จันทร์",
        time_context_thai="เช้าวันจันทร์",
    ),
    "bkk-district": Tour(
        key="bkk-district",
        dest_kind="district",
        origin_address="ศาลาว่าการกรุงเทพมหานคร",
        # เสาชิงช้า (the historic city hall in เขตพระนคร).
        # Verify before first run — if Google resolves the address to the newer
        # ดินแดง building instead, also add 'ดินแดง' to skip_short_names below.
        origin_coord=(13.7549, 100.5024),
        iso="TH-10",
        name_prefix="เขต",
        # Official BMA term is สำนักงานเขต. The กรุงเทพมหานคร qualifier matters:
        # without it, Google's geocoder mis-matches "สำนักงานเขตบางเขน" to a
        # sub-district named บางเขน in Nonthaburi (admin_area_level_3, a real but
        # wrong place). Other district names are mostly unique to Bangkok, but
        # the qualifier defends against the few ambiguous ones at zero cost.
        office_template="สำนักงานเขต{name} กรุงเทพมหานคร",
        skip_short_names=frozenset({"พระนคร"}),
        display_name="กรุงเทพมหานคร",
        english_name="Bangkok",
        picker_label="กรุงเทพ → 50 เขต",
        title_origin="ศาลาว่าการ กทม.",
        origin_short_line1="ศาลาว่าการ",
        origin_short_line2="กทม.",
        dest_unit="เขต",
        dest_label="สำนักงานเขต",
        dest_prefix="เขต",
        departure_hour=8,
        departure_minute=0,
        departure_day_thai="จันทร์",
        time_context_thai="เช้าวันจันทร์",
        departure_samples=(0, 15, 30),
    ),
    "bkk-nationwide": Tour(
        key="bkk-nationwide",
        dest_kind="province",
        origin_address="ศาลาว่าการกรุงเทพมหานคร",
        origin_coord=(13.7549, 100.5024),
        iso="TH",
        name_prefix="จังหวัด",
        office_template="",
        skip_short_names=frozenset({"กรุงเทพมหานคร"}),  # skip own city — would route to itself
        display_name="กรุงเทพมหานคร",
        english_name="Bangkok (nationwide)",
        picker_label="กรุงเทพ → 76 จังหวัด",
        title_origin="ศาลาว่าการ กทม.",
        origin_short_line1="ศาลาว่าการ",
        origin_short_line2="กทม.",
        dest_unit="จังหวัด",
        dest_label="ศาลากลางจังหวัด",
        dest_prefix="จ.",
        departure_hour=8,
        departure_minute=0,
        departure_day_thai="จันทร์",
        time_context_thai="เช้าวันจันทร์",
    ),
    # Same scope as bkk-district (50 เขต offices), but routed by public transit
    # instead of driving. Some outer districts will fail with ZERO_RESULTS where
    # Google has no transit option — those rows are skipped, not faked.
    "bkk-district-transit": Tour(
        key="bkk-district-transit",
        dest_kind="district",
        origin_address="ศาลาว่าการกรุงเทพมหานคร",
        origin_coord=(13.7549, 100.5024),
        iso="TH-10",
        name_prefix="เขต",
        # Same กรุงเทพมหานคร qualifier as bkk-district — disambiguates บางเขน
        # (a sub-district of Nonthaburi has the same name) and other rare conflicts.
        office_template="สำนักงานเขต{name} กรุงเทพมหานคร",
        skip_short_names=frozenset({"พระนคร"}),
        display_name="กรุงเทพมหานคร",
        english_name="Bangkok (transit)",
        picker_label="กรุงเทพ → 50 เขต (รถสาธารณะ)",
        title_origin="ศาลาว่าการ กทม.",
        origin_short_line1="ศาลาว่าการ",
        origin_short_line2="กทม.",
        dest_unit="เขต",
        dest_label="สำนักงานเขต",
        dest_prefix="เขต",
        departure_hour=8,
        departure_minute=0,
        departure_day_thai="จันทร์",
        time_context_thai="เช้าวันจันทร์ (รถสาธารณะ)",
        travel_mode="TRANSIT",
    ),
}


# ─── runtime ──────────────────────────────────────────────────────────
OVERPASS = "https://overpass-api.de/api/interpreter"
ROUTES   = "https://routes.googleapis.com/directions/v2:computeRoutes"
GEOCODE  = "https://maps.googleapis.com/maps/api/geocode/json"
USER_AGENT = "thailand-travel-time/0.1"

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "outputs"
DATA_DIR   = SCRIPT_DIR / "data"

FIELD_MASK = "routes.duration,routes.distanceMeters"

THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def thai_date(dt: datetime) -> str:
    return f"{dt.day} {THAI_MONTHS[dt.month - 1]} {dt.year + 543}"


# ─── CLI ──────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--tour", default="cnx-district", choices=list(TOURS.keys()),
                    help="tour key (default: cnx-district)")
parser.add_argument("--dry-run", action="store_true",
                    help="print planned destinations without calling Google APIs")
args = parser.parse_args()

TOUR    = TOURS[args.tour]
DRY_RUN = args.dry_run

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
if not DRY_RUN and not API_KEY:
    sys.exit("error: export GOOGLE_MAPS_API_KEY first (or pass --dry-run)")


def overpass_query_for(t: Tour) -> str:
    """Per-kind Overpass QL. district = admin_level=6 inside one province;
    province = all admin_level=4 across Thailand (= the 76 จังหวัด + Bangkok)."""
    if t.dest_kind == "district":
        return f"""
[out:json][timeout:120];
area["ISO3166-2"="{t.iso}"][admin_level=4]->.p;
relation[admin_level=6][boundary=administrative](area.p);
out center tags;
"""
    if t.dest_kind == "province":
        return """
[out:json][timeout:120];
relation[admin_level=4]["ISO3166-2"~"^TH-"];
out center tags;
"""
    raise ValueError(f"unknown dest_kind: {t.dest_kind}")


def destination_address(t: Tour, short_name: str) -> str:
    """How to phrase a destination as an address Google can route to."""
    if t.dest_kind == "district":
        return t.office_template.format(name=short_name)
    if t.dest_kind == "province":
        # Bangkok doesn't have a "ศาลากลางจังหวัด" — its destination is the city hall.
        if short_name == "กรุงเทพมหานคร":
            return "ศาลาว่าการกรุงเทพมหานคร"
        return f"ศาลากลางจังหวัด{short_name}"
    raise ValueError(f"unknown dest_kind: {t.dest_kind}")


def next_monday_at(hour: int, minute: int = 0) -> datetime:
    """Next Monday at HH:MM ICT — stable historical-traffic context."""
    ict = timezone(timedelta(hours=7))
    now = datetime.now(ict)
    days = (7 - now.weekday()) % 7
    if days == 0 and now.hour >= hour:
        days = 7
    return (now + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


DEPARTURE = next_monday_at(TOUR.departure_hour, TOUR.departure_minute)


# ─── types ────────────────────────────────────────────────────────────
@dataclass
class Place:
    short_name: str  # name with prefix stripped, e.g. "สารภี" / "บางรัก" / "อ่างทอง" / "กรุงเทพมหานคร"

    def address(self, t: Tour) -> str:
        return destination_address(t, self.short_name)


# ─── overpass ─────────────────────────────────────────────────────────
def fetch_destinations(t: Tour) -> list[Place]:
    """Overpass is the canonical list. The 'center' tag is requested as a
    sanity check — bearings come from per-destination geocoding now, so
    centroids aren't used after this filter."""
    headers = {"User-Agent": USER_AGENT}
    r = httpx.post(OVERPASS, data={"data": overpass_query_for(t)},
                   headers=headers, timeout=180)
    r.raise_for_status()
    out: list[Place] = []
    for e in r.json()["elements"]:
        if "center" not in e:
            continue
        tags = e.get("tags", {})
        full = (tags.get("name:th") or tags.get("name") or "").strip()
        short = full.removeprefix(t.name_prefix).strip()
        if not short or short in t.skip_short_names:
            continue
        out.append(Place(short))
    return out


# ─── geometry / routing ──────────────────────────────────────────────
def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """True bearing, degrees, 0=N clockwise."""
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δλ = math.radians(lon2 - lon1)
    x = math.sin(Δλ) * math.cos(φ2)
    y = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(Δλ)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _route_one(t: Tour, addr: str, departure: datetime) -> dict:
    """One Routes API call at a specific departure time. Returns a sample dict
    with raw request + response (for archive) plus parsed minutes/km/error.
    Never raises — failures are recorded in the dict's `error` field."""
    body = {
        "origin":        {"address": t.origin_address},
        "destination":   {"address": addr},
        "travelMode":    t.travel_mode,
        "departureTime": departure.isoformat(),
    }
    if t.travel_mode == "DRIVE":
        # routingPreference is DRIVE-only — sending it with TRANSIT errors out.
        body["routingPreference"] = "TRAFFIC_AWARE"
    headers = {
        "Content-Type":     "application/json",
        "X-Goog-Api-Key":   API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    sample: dict = {
        "departure":       departure.isoformat(),
        "url":             ROUTES,
        "headers":         {k: v for k, v in headers.items() if k != "X-Goog-Api-Key"},
        "body":            body,
        "response_status": None,
        "response":        None,
        "minutes":         None,
        "km":              None,
        "error":           None,
    }
    try:
        rr = httpx.post(ROUTES, json=body, headers=headers, timeout=30)
        sample["response_status"] = rr.status_code
        try:
            sample["response"] = rr.json()
        except Exception:
            sample["response"] = {"_raw_text": rr.text}
        rr.raise_for_status()
        rj = sample["response"]
        if not rj.get("routes"):
            raise RuntimeError(f"no route in response: {rj}")
        rt = rj["routes"][0]
        sample["minutes"] = float(rt["duration"].rstrip("s")) / 60.0
        sample["km"]      = rt["distanceMeters"] / 1000.0
    except Exception as exc:
        sample["error"] = f"{type(exc).__name__}: {exc}"
    return sample


def route_via_address(t: Tour, p: Place, base_departure: datetime) -> dict:
    """Geocode once, then route at each `t.departure_samples` offset. Returns a
    record with the worst-case (max minutes) sample as `result`. The raw
    request/response for every sample is preserved in `routes` so the archive
    shows exactly what Google returned at each departure time."""
    addr = p.address(t)
    record: dict = {
        "place":   p.short_name,
        "address": addr,
        "geocode": None,
        "routes":  [],     # list of per-departure samples
        "result":  None,
        "error":   None,
    }
    try:
        # Step 1 — Geocoding API (once per destination, regardless of samples).
        gparams = {"address": addr, "region": "th", "language": "th", "key": API_KEY}
        record["geocode"] = {
            "url":             GEOCODE,
            "params":          {k: v for k, v in gparams.items() if k != "key"},
            "response_status": None,
            "response":        None,
            "coord":           None,
        }
        gr = httpx.get(GEOCODE, params=gparams, timeout=30)
        record["geocode"]["response_status"] = gr.status_code
        try:
            record["geocode"]["response"] = gr.json()
        except Exception:
            record["geocode"]["response"] = {"_raw_text": gr.text}
        gr.raise_for_status()
        gj = record["geocode"]["response"]
        if gj.get("status") != "OK" or not gj.get("results"):
            raise RuntimeError(
                f"geocode failed: status={gj.get('status')}, "
                f"error={gj.get('error_message', 'none')}"
            )
        loc = gj["results"][0]["geometry"]["location"]
        coord = (loc["lat"], loc["lng"])
        record["geocode"]["coord"] = list(coord)

        # Step 2 — Routes API at each departure sample.
        for offset in t.departure_samples:
            dep = base_departure + timedelta(minutes=offset)
            sample = _route_one(t, addr, dep)
            sample["offset_minutes"] = offset
            record["routes"].append(sample)
            time.sleep(0.05)  # gentle pacing between samples for the same destination

        # Step 3 — pick worst-case (max minutes) among successful samples.
        successful = [s for s in record["routes"] if s["minutes"] is not None]
        if not successful:
            errs = "; ".join(s["error"] or "?" for s in record["routes"])
            raise RuntimeError(f"all {len(record['routes'])} samples failed: {errs}")
        worst = max(successful, key=lambda s: s["minutes"])
        bearing = bearing_deg(*t.origin_coord, *coord)
        record["result"] = {
            "minutes":             worst["minutes"],
            "km":                  worst["km"],
            "bearing_deg":         bearing,
            "worst_offset_minutes": worst["offset_minutes"],
            "samples_total":       len(record["routes"]),
            "samples_minutes":     [s["minutes"] for s in record["routes"]],
        }
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


# ─── data.js writer ──────────────────────────────────────────────────
def write_data_js(t: Tour, rows: list[list], snapshot_date: str | None,
                  archive_name: str | None) -> Path:
    j = lambda v: json.dumps(v, ensure_ascii=False)
    lines = [
        f"// Generated by prepare_data.py for {t.key}.",
        f"// Archive: outputs/{archive_name}" if archive_name else "// Archive: (none)",
        "",
        "window.CITY_REGISTRY = window.CITY_REGISTRY || {};",
        f"window.CITY_REGISTRY[{j(t.key)}] = {{",
        f"  key: {j(t.key)},",
        f"  displayName: {j(t.display_name)},",
        f"  englishName: {j(t.english_name)},",
        f"  pickerLabel: {j(t.picker_label)},",
        f"  origin: {j(t.origin_address)},",
        f"  originShort: {{ line1: {j(t.origin_short_line1)}, line2: {j(t.origin_short_line2)} }},",
        f"  titleOrigin: {j(t.title_origin)},",
        f"  destUnit: {j(t.dest_unit)},",
        f"  destLabel: {j(t.dest_label)},",
        f"  destPrefix: {j(t.dest_prefix)},",
        f"  departure: {{ hour: {t.departure_hour}, minute: {t.departure_minute}, "
            f"dayThai: {j(t.departure_day_thai)} }},",
        f"  timeContext: {j(t.time_context_thai)},",
        f"  travelMode: {j(t.travel_mode)},",
        f"  departureSamples: {json.dumps(list(t.departure_samples))},",
        f"  snapshotDate: {j(snapshot_date)},",
        f"  archive: {j(f'outputs/{archive_name}') if archive_name else 'null'},",
        f"  rows: {json.dumps(rows, ensure_ascii=False, indent=4)},",
        "};",
        "",
    ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{t.key}.js"
    path.write_text("\n".join(lines))
    return path


# ─── main ─────────────────────────────────────────────────────────────
def main() -> None:
    t = TOUR
    print(f"# tour:      {t.key} ({t.english_name})", file=sys.stderr)
    print(f"# dest kind: {t.dest_kind}", file=sys.stderr)
    print(f"# origin:    {t.origin_address}", file=sys.stderr)
    print(f"# departure: {DEPARTURE.isoformat()}", file=sys.stderr)

    destinations = sorted(fetch_destinations(t), key=lambda x: x.short_name)
    print(f"# fetched {len(destinations)} destinations from Overpass", file=sys.stderr)

    if DRY_RUN:
        print("# DRY RUN — no Google calls. Planned destinations:", file=sys.stderr)
        for p in destinations:
            print(f"#   {p.short_name:<22} → {p.address(t)}", file=sys.stderr)
        return

    records: list[dict] = []
    for p in destinations:
        rec = route_via_address(t, p, DEPARTURE)
        records.append(rec)
        if rec["error"]:
            print(f"# fail {p.short_name}: {rec['error']}", file=sys.stderr)
        else:
            r = rec["result"]
            # When multi-sampled, show the [min–max] spread so the worst-case
            # selection is auditable on the console (matches Google Maps' UI range).
            spread = ""
            if r["samples_total"] > 1:
                mins = r["samples_minutes"]
                spread = f"  [{min(mins):.0f}–{max(mins):.0f}]"
            print(
                f"#   {p.short_name:<22} {r['minutes']:>6.1f} min  "
                f"bearing {r['bearing_deg']:>5.1f}°  {r['km']:>6.1f} km{spread}",
                file=sys.stderr,
            )
        time.sleep(0.1)

    rows = [
        [rec["place"], round(rec["result"]["minutes"]),
         round(rec["result"]["bearing_deg"])]
        for rec in records if rec["result"]
    ]
    rows.sort(key=lambda r: r[1])

    succeeded = sum(1 for rec in records if rec["result"])
    failed    = sum(1 for rec in records if rec["error"])
    print(f"# done: {succeeded}/{len(records)} succeeded, {failed} failed",
          file=sys.stderr)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    archive_path = OUTPUT_DIR / f"{run_at}__{t.key}.json"
    archive = {
        "run_at":             run_at,
        "tour_key":           t.key,
        "dest_kind":          t.dest_kind,
        "origin_address":     t.origin_address,
        "origin_coord_for_bearing": list(t.origin_coord),
        "departure":          DEPARTURE.isoformat(),
        "travel_mode":        t.travel_mode,
        "routing_preference": "TRAFFIC_AWARE" if t.travel_mode == "DRIVE" else None,
        "field_mask":         FIELD_MASK,
        "summary":            {"total": len(records), "succeeded": succeeded, "failed": failed},
        "calls":              records,
        "data_js_rows":       rows,
    }
    archive_path.write_text(json.dumps(archive, ensure_ascii=False, indent=2))
    print(f"# archived: {archive_path.relative_to(SCRIPT_DIR)}", file=sys.stderr)

    snapshot_date = thai_date(datetime.now())
    out_path = write_data_js(t, rows, snapshot_date, archive_path.name)
    print(f"# wrote: {out_path.relative_to(SCRIPT_DIR)} "
          f"({len(rows)} rows; refresh index.html to see them)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
