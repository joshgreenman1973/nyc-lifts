#!/usr/bin/env python3
"""
Lifts - turn raw MTA files into the site payload.

Aggregation rule used throughout: availability is recomputed from summed
hours (hours_available / total_hours), never averaged from the per-unit
ratios. Averaging ratios would weigh a single elevator that was tracked
for one month the same as one tracked for eleven years.
"""
import json
import os
import collections
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")   # the MTA stamps outages in local time

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "data", "raw")
OUT = os.path.join(HERE, "..", "docs", "data")
os.makedirs(OUT, exist_ok=True)

RECENT_MONTHS = 24  # trailing window for station and unit level reliability


def load(n):
    with open(os.path.join(RAW, n)) as f:
        return json.load(f)


def num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def parse_outage_dt(s):
    """MTA live feed uses US format: 09/30/2024 12:05:00 PM."""
    if not s:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def main():
    inv = load("inventory.json")
    live = load("live_outages.json")
    av = load("availability.json")
    stations = load("stations.json")
    ridership = load("ridership.json")
    meta = load("fetch_meta.json")

    now = datetime.now(timezone.utc)

    # ---- what counts as part of the fleet ----------------------------------
    # The inventory's service_status_code is not a reliable signal. Seven units
    # it marks RNOS ("Removed") reported 94-99 percent availability in the most
    # recent month, and every INOS/IFOS unit reports nothing at all. Presence is
    # therefore behavioural: an elevator is in the operating fleet if it filed
    # service hours in the latest month of the availability data. Sixty-two
    # elevators the inventory lists never appear in that data in any month;
    # they are counted and disclosed separately rather than silently included.
    months_all = sorted({r["month"][:7] for r in av})
    latest_month = months_all[-1]
    reported_latest = {
        r["equipment_code"] for r in av
        if r["month"][:7] == latest_month and num(r.get("_24_hour_total_hours")) > 0
    }
    ever_reported = {r["equipment_code"] for r in av}

    # ---- station complexes -------------------------------------------------
    # ada: 0 none, 1 full, 2 partial. Verified against MTA's own field values.
    st = {}
    for s in stations:
        cid = str(s.get("complex_id"))
        st[cid] = {
            "id": cid,
            "name": s.get("display_name") or s.get("stop_name"),
            "borough": s.get("borough"),
            "routes": s.get("daytime_routes"),
            "ada": int(num(s.get("ada"))),
            "lat": num(s.get("latitude"), None),
            "lon": num(s.get("longitude"), None),
        }
    riders = {str(r["station_complex_id"]): num(r.get("riders")) for r in ridership}
    for cid, v in st.items():
        v["riders12"] = riders.get(cid, 0.0)

    # ---- equipment inventory ----------------------------------------------
    units = {}
    for r in inv:
        code = r["equipment_code"]
        units[code] = {
            "code": code,
            "kind": r.get("elevator_or_escalator"),
            "complex": str(r.get("station_complex_mrn")),
            "station": r.get("station_description") or r.get("station_name"),
            "borough": r.get("borough"),
            "ada": (r.get("ada_compliant") or "").upper() == "YES",
            # '+' has a redundant elevator, '-' does not. Escalators are null.
            "redundant": r.get("redundant_elevator") == "+",
            "redundancy_known": r.get("redundant_elevator") in ("+", "-"),
            # Behavioural presence, not the inventory's own status field.
            "present": code in reported_latest,
            "ever_reported": code in ever_reported,
            "status": r.get("service_status"),
            "status_code": r.get("service_status_code"),
            # Kept for disclosure only: the inventory's claim about itself.
            "inv_removed": r.get("service_status_code") == "RNOS",
            "street": (r.get("street_access") or "").upper() == "YES",
            "installed": (r.get("latest_installation_date") or "")[:10],
            "serving": r.get("notes"),
            "lat": num(r.get("x_coordinate"), None),
            "lon": num(r.get("y_coordinate"), None),
        }

    elevators = {c: u for c, u in units.items() if u["kind"] == "Elevator"}
    # Accessibility elevators that are demonstrably in the operating fleet and
    # attached to a known station. Twenty-three inventory records carry no
    # station at all; they cannot be reasoned about per station and are
    # excluded here and counted for disclosure.
    ada_elevators = {c: u for c, u in elevators.items()
                     if u["ada"] and u["present"] and u["complex"] not in ("None", "", None)}
    orphan_units = sum(1 for u in units.values()
                       if u["complex"] in ("None", "", None))

    # ---- system trend by year ---------------------------------------------
    year = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in av:
        y = r["month"][:4]
        kind = r["equipment_type"]
        k = year[(y, kind)]
        k["hours_avail"] += num(r.get("_24_hour_hours_available"))
        k["hours_total"] += num(r.get("_24_hour_total_hours"))
        k["entrapments"] += num(r.get("entrapments"))
        k["unscheduled"] += num(r.get("unscheduled_outages"))
        k["scheduled"] += num(r.get("scheduled_outages"))
        k["units"] = 0  # filled below

    units_per_year = collections.defaultdict(set)
    for r in av:
        units_per_year[(r["month"][:4], r["equipment_type"])].add(r["equipment_code"])

    trend = []
    for (y, kind), k in sorted(year.items()):
        if not k["hours_total"]:
            continue
        n_units = len(units_per_year[(y, kind)])
        trend.append({
            "year": int(y),
            "kind": kind,
            "availability": round(100 * k["hours_avail"] / k["hours_total"], 2),
            "entrapments": int(k["entrapments"]),
            "unscheduled": int(k["unscheduled"]),
            "scheduled": int(k["scheduled"]),
            "units": n_units,
            # Normalised so a growing fleet does not look like a worsening one.
            "entrapments_per_100_units": round(100 * k["entrapments"] / n_units, 1)
            if n_units else None,
        })

    # Partial-year flag: the latest year is incomplete.
    months_seen = sorted({r["month"][:7] for r in av})
    latest_month = months_seen[-1]
    latest_year = latest_month[:4]
    months_in_latest = len({m for m in months_seen if m.startswith(latest_year)})
    for t in trend:
        t["partial"] = (str(t["year"]) == latest_year)

    # ---- trailing window, per unit and per station -------------------------
    recent_months = months_seen[-RECENT_MONTHS:]
    recent_set = set(recent_months)

    unit_recent = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in av:
        if r["month"][:7] not in recent_set:
            continue
        k = unit_recent[r["equipment_code"]]
        k["hours_avail"] += num(r.get("_24_hour_hours_available"))
        k["hours_total"] += num(r.get("_24_hour_total_hours"))
        k["entrapments"] += num(r.get("entrapments"))
        k["unscheduled"] += num(r.get("unscheduled_outages"))
        k["scheduled"] += num(r.get("scheduled_outages"))

    unit_rows = []
    for code, k in unit_recent.items():
        u = units.get(code)
        if not u or not k["hours_total"]:
            continue
        unit_rows.append({
            "code": code,
            "kind": u["kind"],
            "station": u["station"],
            "complex": u["complex"],
            "borough": u["borough"],
            "ada": u["ada"],
            "redundant": u["redundant"],
            "redundancy_known": u["redundancy_known"],
            "availability": round(100 * k["hours_avail"] / k["hours_total"], 2),
            "entrapments": int(k["entrapments"]),
            "unscheduled": int(k["unscheduled"]),
            "serving": u["serving"],
            "installed": u["installed"],
        })
    unit_rows.sort(key=lambda r: r["availability"])

    # station complex rollup, elevators only (the accessibility question)
    comp = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in unit_rows:
        if r["kind"] != "Elevator":
            continue
        k = comp[r["complex"]]
        k["n"] += 1
        k["entrapments"] += r["entrapments"]
        k["unscheduled"] += r["unscheduled"]
    for code, k in unit_recent.items():
        u = units.get(code)
        if not u or u["kind"] != "Elevator" or not u["present"]:
            continue
        c = comp[u["complex"]]
        c["hours_avail"] += k["hours_avail"]
        c["hours_total"] += k["hours_total"]

    # ---- current outages ---------------------------------------------------
    current, upcoming = [], []
    for o in live:
        code = o.get("equipment")
        u = units.get(code, {})
        started = parse_outage_dt(o.get("outagedate"))
        eta = parse_outage_dt(o.get("estimatedreturntoservice"))
        # MTA stamps are Eastern local time; comparing them to a UTC clock
        # inflated every duration by four or five hours.
        days = round((now - started.replace(tzinfo=ET)).total_seconds() / 86400, 1) \
            if started else None
        row = {
            "code": code,
            "kind": "Elevator" if o.get("equipmenttype") == "EL" else "Escalator",
            "station": o.get("station"),
            "complex": u.get("complex"),
            "borough": u.get("borough") or o.get("borough"),
            "routes": o.get("trainno"),
            "serving": o.get("serving"),
            "ada": o.get("ADA") == "Y",
            "redundant": u.get("redundant"),
            "redundancy_known": u.get("redundancy_known", False),
            "present": u.get("present", False),
            "inv_removed": u.get("inv_removed", False),
            "reason": o.get("reason"),
            "started": started.isoformat() if started else None,
            "eta": eta.isoformat() if eta else None,
            "days_out": days,
            "maintenance": o.get("ismaintenanceoutage") == "Y",
        }
        (upcoming if o.get("isupcomingoutage") == "Y" else current).append(row)
    current.sort(key=lambda r: -(r["days_out"] or 0))

    # Stations with no working step-free access right now: every in-service
    # ADA elevator at the complex is currently out.
    out_codes = {r["code"] for r in current if r["kind"] == "Elevator"}
    # Units the inventory calls out of service while the live feed does not
    # list them as out. Disclosed rather than assumed either way.
    disputed = sorted(c for c, u in ada_elevators.items()
                      if u["status_code"] in ("INOS", "IFOS") and c not in out_codes)

    ada_by_complex = collections.defaultdict(list)
    for code, u in ada_elevators.items():
        ada_by_complex[u["complex"]].append(code)

    cut_off = []
    for cid, codes in ada_by_complex.items():
        # The station must be one the MTA considers accessible in the first
        # place. A few complexes the agency lists as having no step-free
        # access nonetheless carry an accessibility-flagged elevator in the
        # equipment file; announcing those as "no way in" would contradict
        # the same page's own map.
        if st.get(cid, {}).get("ada", 0) not in (1, 2):
            continue
        down = [c for c in codes if c in out_codes]
        if codes and len(down) == len(codes):
            s = st.get(cid, {})
            cut_off.append({
                "complex": cid,
                "name": s.get("name") or units[codes[0]]["station"],
                "borough": s.get("borough"),
                "routes": s.get("routes"),
                "ada_elevators": len(codes),
                "riders12": s.get("riders12", 0),
                "lat": s.get("lat"), "lon": s.get("lon"),
                "codes": codes,
                "longest_days": max(
                    [(r["days_out"] or 0) for r in current if r["code"] in down]
                    or [0]),
            })
    cut_off.sort(key=lambda r: -r["longest_days"])

    # ---- station map payload ----------------------------------------------
    station_rows = []
    for cid, s in st.items():
        k = comp.get(cid)
        n_elev = sum(1 for u in elevators.values() if u["complex"] == cid
                     and u["present"])
        station_rows.append({
            **s,
            "elevators": n_elev,
            "availability": round(100 * k["hours_avail"] / k["hours_total"], 2)
            if k and k["hours_total"] else None,
            "entrapments": int(k["entrapments"]) if k else 0,
            "unscheduled": int(k["unscheduled"]) if k else 0,
            "out_now": sum(1 for r in current
                           if r["complex"] == cid and r["kind"] == "Elevator"),
            "cut_off": any(c["complex"] == cid for c in cut_off),
        })

    # ---- headline numbers --------------------------------------------------
    elev_trend = [t for t in trend if t["kind"] == "Elevator"]
    esc_trend = [t for t in trend if t["kind"] == "Escalator"]
    total_entrap = sum(t["entrapments"] for t in elev_trend)
    latest_full = [t for t in elev_trend if not t["partial"]][-1]
    first = elev_trend[0]

    ada_full = sum(1 for s in st.values() if s["ada"] == 1)
    ada_part = sum(1 for s in st.values() if s["ada"] == 2)

    no_redundancy = sum(1 for u in elevators.values()
                        if u["present"] and u["redundancy_known"]
                        and not u["redundant"])
    redundancy_known_n = sum(1 for u in elevators.values()
                             if u["present"] and u["redundancy_known"])

    # The longest running outage of an elevator that is actually in service.
    # Sorting the raw feed put a removed escalator at the top of a stat grid
    # that is otherwise entirely about elevators.
    el_out = [r for r in current if r["kind"] == "Elevator" and r["present"]]
    longest_el = max(el_out, key=lambda r: r["days_out"] or 0) if el_out else None

    headline = {
        "stations_total": len(st),
        "ada_full": ada_full,
        "ada_partial": ada_part,
        "ada_none": len(st) - ada_full - ada_part,
        "elevators_total": sum(1 for u in elevators.values() if u["present"]),
        "escalators_total": sum(1 for u in units.values()
                                if u["kind"] == "Escalator" and u["present"]),
        "elevators_no_redundancy": no_redundancy,
        "redundancy_known": redundancy_known_n,
        # Outages are counted against the operating fleet. Units that are not
        # currently filing service hours still appear in the live feed, often
        # for years under capital replacement; they are reported separately
        # rather than counted as outages of machines that are running.
        "out_now": len([r for r in current
                        if r["kind"] == "Elevator" and r["present"]]),
        "out_now_offline": len([r for r in current
                                if r["kind"] == "Elevator" and not r["present"]]),
        "escalators_out_now": len([r for r in current
                                   if r["kind"] == "Escalator" and r["present"]]),
        "upcoming": len(upcoming),
        "cut_off_now": len(cut_off),
        "longest_outage_days": longest_el["days_out"] if longest_el else None,
        "longest_outage_station": longest_el["station"] if longest_el else None,
        "longest_outage_code": longest_el["code"] if longest_el else None,
        "entrapment_events_total": total_entrap,
        "entrapments_first_year": first["entrapments"],
        "fleet_inventory": sum(1 for u in elevators.values()
                               if u["status_code"] != "RNOS"),
        "never_report": sum(1 for u in elevators.values()
                            if not u["ever_reported"]),
        "orphan_units": orphan_units,
        "fleet_month": latest_month,
        "entrapments_first_year_label": first["year"],
        "entrapments_latest_full": latest_full["entrapments"],
        "entrapments_latest_full_year": latest_full["year"],
        "entrapments_per_day_recent": round(latest_full["entrapments"] / 365, 1),
        "availability_latest": latest_full["availability"],
        "availability_first": first["availability"],
        "downtime_days_per_elevator": round(
            365 * (100 - latest_full["availability"]) / 100, 1),
        "escalator_availability_latest": [
            t for t in esc_trend if not t["partial"]][-1]["availability"],
        "disputed_units": len(disputed),
        "data_through": latest_month,
        "months_in_latest_year": months_in_latest,
        "riders_cut_off": int(sum(c["riders12"] for c in cut_off)),
    }

    # ---- history grid ------------------------------------------------------
    # One row per elevator, one character per month since 2015. Buckets are
    # deliberately fine at the top end because almost all of the mass sits
    # above 95 percent and that is exactly where the differences matter.
    def bucket(a):
        if a >= 99.5: return "9"
        if a >= 99: return "8"
        if a >= 98: return "7"
        if a >= 97: return "6"
        if a >= 95: return "5"
        if a >= 90: return "4"
        if a >= 80: return "3"
        if a >= 60: return "2"
        if a >= 30: return "1"
        return "0"

    by_unit_month = collections.defaultdict(dict)
    for r in av:
        if r["equipment_type"] != "Elevator":
            continue
        tot = num(r.get("_24_hour_total_hours"))
        if tot <= 0:
            continue
        pct = 100 * num(r.get("_24_hour_hours_available")) / tot
        by_unit_month[r["equipment_code"]][r["month"][:7]] = pct

    grid = []
    gap_stats = collections.Counter()
    for code, months in by_unit_month.items():
        u = units.get(code)
        if not u:
            continue
        # Blank cells mean three different things and the page should not
        # claim they all mean "not yet reporting": before a unit's first
        # filing, after its last, and gaps in between.
        idx = [i for i, m in enumerate(months_seen) if m in months]
        first_i, last_i = (idx[0], idx[-1]) if idx else (0, -1)
        chars = []
        for i, m in enumerate(months_seen):
            if m in months:
                chars.append(bucket(months[m]))
            elif i < first_i:
                chars.append("-")      # not yet reporting
                gap_stats["before"] += 1
            elif i > last_i:
                chars.append("x")      # stopped reporting
                gap_stats["after"] += 1
            else:
                chars.append("g")      # gap in the filings
                gap_stats["gap"] += 1
        row = "".join(chars)
        grid.append({
            "code": code,
            "station": u["station"],
            "borough": u["borough"],
            "ada": u["ada"],
            "row": row,
        })
    # Order by borough then station so the picture reads geographically.
    grid.sort(key=lambda g: (g["borough"] or "", g["station"] or "", g["code"]))

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grid_months": months_seen,
        "grid": grid,
        "fetched_at": meta.get("fetched_at"),
        "recent_window": {"from": recent_months[0], "to": recent_months[-1],
                          "months": len(recent_months)},
        "headline": headline,
        "trend": trend,
        "current": current,
        "upcoming": upcoming,
        "cut_off": cut_off,
        "stations": station_rows,
        "units": unit_rows,
    }

    # ---- route geometry ----------------------------------------------------
    # Coordinates rounded to five decimals (about a metre) to keep the file
    # small; the lines are drawn as context, not measured against.
    try:
        raw_lines = load("subway_lines.json")
    except FileNotFoundError:
        raw_lines = []
    # The published geometry carries survey-grade vertex density, which is
    # 4.5 MB for a map drawn at city scale. Simplified to roughly 15 metres
    # and rounded to four decimals; the lines are context, not measurement.
    from shapely.geometry import MultiLineString, mapping
    TOL = 0.00015

    feats = []
    for ln in raw_lines:
        g = ln.get("geometry")
        if not g or not g.get("coordinates"):
            continue
        try:
            geom = MultiLineString(g["coordinates"]).simplify(TOL)
        except Exception:
            continue
        gm = mapping(geom)
        if gm["type"] == "LineString":
            parts = [gm["coordinates"]]
        else:
            parts = gm["coordinates"]
        coords = [[[round(p[0], 4), round(p[1], 4)] for p in part]
                  for part in parts if len(part) > 1]
        if not coords:
            continue
        feats.append({"type": "Feature",
                      "properties": {"service": ln.get("service")},
                      "geometry": {"type": "MultiLineString", "coordinates": coords}})
    if feats:
        gj = {"type": "FeatureCollection", "features": feats}
        gp = os.path.join(OUT, "subway_lines.geojson")
        with open(gp, "w") as f:
            json.dump(gj, f, separators=(",", ":"))
        print(f"wrote {gp}  ({os.path.getsize(gp)/1e6:.2f} MB, {len(feats)} services)")

    path = os.path.join(OUT, "lifts.json")
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {path}  ({os.path.getsize(path)/1e6:.2f} MB)")

    print("\n--- headline ---")
    for k, v in headline.items():
        print(f"  {k}: {v}")
    print(f"\n  stations cut off right now: {len(cut_off)}")
    for c in cut_off[:8]:
        print(f"    {c['name']} ({c['borough']}) - {c['longest_days']:.0f} days, "
              f"{c['ada_elevators']} ADA elevator(s)")


if __name__ == "__main__":
    main()
