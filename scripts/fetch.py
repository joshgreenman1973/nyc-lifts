#!/usr/bin/env python3
"""
Lifts - fetch every input from MTA open data.

Fails loudly on empty or short responses. A silent zero-row fetch would
publish a page claiming the subway has no broken elevators, which is the
one thing this project must never do.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "data", "raw")
os.makedirs(RAW, exist_ok=True)

SOCRATA = "https://data.ny.gov/resource"
ENE_LIVE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fnyct_ene.json"

UA = {"User-Agent": "nyc-lifts/1.0 (public transit accessibility research)"}


def get(url, tries=4):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            # Socrata answers 403 when throttling a keyless caller; retry it.
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"FATAL: {url} failed after {tries} tries: {last}")


def paged(dataset, select=None, where=None, order=None, group=None, page=50000,
          expect_min=1):
    """Pull a full Socrata dataset in pages. Ordered so paging is stable."""
    out = []
    offset = 0
    while True:
        params = {"$limit": page, "$offset": offset}
        if select:
            params["$select"] = select
        if where:
            params["$where"] = where
        if group:
            params["$group"] = group
        params["$order"] = order or ":id"
        url = f"{SOCRATA}/{dataset}.json?" + urllib.parse.urlencode(params)
        chunk = get(url)
        out.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
        if offset > 2_000_000:
            raise SystemExit(f"FATAL: {dataset} paging ran away past 2M rows")
    if len(out) < expect_min:
        raise SystemExit(
            f"FATAL: {dataset} returned {len(out)} rows, expected at least {expect_min}. "
            "Refusing to write a truncated file."
        )
    return out


def save(name, obj):
    path = os.path.join(RAW, name)
    with open(path, "w") as f:
        json.dump(obj, f)
    n = len(obj) if isinstance(obj, list) else 1
    print(f"  wrote {name}  ({n:,} records, {os.path.getsize(path)/1e6:.1f} MB)")
    return obj


def main():
    print("Lifts - fetching MTA data")

    # 1. Live outages. The whole live board depends on this one.
    print("\n[1/5] live elevator + escalator outages")
    live = get(ENE_LIVE)
    if not isinstance(live, list):
        raise SystemExit(f"FATAL: live feed returned {type(live)}, expected a list")
    if len(live) == 0:
        # Zero outages across 750 units is not plausible; treat as a broken feed.
        raise SystemExit(
            "FATAL: live outage feed returned 0 outages. The MTA has never "
            "reported zero. Assuming the feed is broken rather than publishing it."
        )
    required = {"station", "equipment", "equipmenttype", "outagedate"}
    missing = required - set(live[0].keys())
    if missing:
        raise SystemExit(f"FATAL: live feed missing expected fields: {missing}")
    save("live_outages.json", live)

    # 2. Equipment inventory: location, age, ADA status, redundancy.
    print("\n[2/5] equipment inventory")
    inv = paged("94fv-bak7", expect_min=600)
    save("inventory.json", inv)

    # 3. Monthly availability per unit, 2015 to now.
    print("\n[3/5] monthly availability since 2015 (large)")
    avail = paged("rc78-7x78", order="month", expect_min=70000)
    save("availability.json", avail)

    # 4. Station complexes with ADA status and coordinates.
    print("\n[4/5] station complexes")
    stations = paged("5f5g-n3cz", expect_min=400)
    save("stations.json", stations)

    # 5. Recent ridership, to weigh an outage by how many people meet it.
    print("\n[5/5] station ridership (trailing 12 months)")
    rid = paged(
        "ak4z-sape",
        select="station_complex_id, sum(ridership) AS riders",
        where="month >= '2025-07-01T00:00:00.000'",
        group="station_complex_id",
        order="station_complex_id",
        expect_min=300,
    )
    save("ridership.json", rid)

    # 6. Route geometry, so the map reads as the subway and not a dot scatter.
    print("\n[6/6] subway service line geometry")
    lines = paged("s692-irgq", expect_min=20)
    save("subway_lines.json", lines)

    meta = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": {
            "live_outages": ENE_LIVE,
            "inventory": f"{SOCRATA}/94fv-bak7.json",
            "availability": f"{SOCRATA}/rc78-7x78.json",
            "stations": f"{SOCRATA}/5f5g-n3cz.json",
            "ridership": f"{SOCRATA}/ak4z-sape.json",
        },
        "counts": {
            "live_outages": len(live),
            "inventory": len(inv),
            "availability_rows": len(avail),
            "stations": len(stations),
        },
    }
    save("fetch_meta.json", meta)
    print("\nAll inputs fetched.")


if __name__ == "__main__":
    main()
