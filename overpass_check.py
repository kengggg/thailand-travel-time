#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""
Overpass smoke test — fetch Chiang Mai amphoes and print a clean table.
No API key needed. Run before build_data_google.py to verify the query.

Usage:
    ./overpass_check.py
"""

import sys
import httpx

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = """
[out:json][timeout:120];
area["ISO3166-2"="TH-50"][admin_level=4]->.cm;
relation[admin_level=6][boundary=administrative](area.cm);
out center tags;
"""

headers = {"User-Agent": "thailand-travel-time/0.1 (overpass smoke test)"}
r = httpx.post(OVERPASS, data={"data": QUERY}, headers=headers, timeout=180)
r.raise_for_status()
elements = r.json()["elements"]

rows = []
for e in elements:
    tags = e.get("tags", {})
    name_th = tags.get("name:th", "")
    name_en = tags.get("name:en") or tags.get("name", "")
    has_center = "center" in e
    lat = e.get("center", {}).get("lat")
    lon = e.get("center", {}).get("lon")
    rows.append((name_th, name_en, has_center, lat, lon))

rows.sort(key=lambda r: r[0] or r[1])

missing_name_th = [r for r in rows if not r[0]]
missing_center = [r for r in rows if not r[2]]

print(f"got {len(rows)} relations\n")
print(f"{'name:th':<22} {'name:en':<22} {'lat':>9}  {'lon':>9}")
print("─" * 68)
for name_th, name_en, has_center, lat, lon in rows:
    if has_center:
        print(f"{name_th:<22} {name_en:<22} {lat:>9.4f}  {lon:>9.4f}")
    else:
        print(f"{name_th:<22} {name_en:<22} {'(no center)':>20}", file=sys.stderr)

print()
print(f"missing name:th: {len(missing_name_th)}"
      + (f" ({', '.join(r[1] for r in missing_name_th)})" if missing_name_th else ""))
print(f"missing center:  {len(missing_center)}")
