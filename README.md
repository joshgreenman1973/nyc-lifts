# Lifts

Every elevator in the New York City subway, and whether it is working right now.

The Metropolitan Transportation Authority reports about 97 percent elevator
availability. That number is true and it is not the number a rider in a
wheelchair needs, which is whether the one elevator at their station is running
today. This site reports both.

**Live site:** https://joshgreenman1973.github.io/nyc-lifts/

## What it shows

- **A map of all 445 station complexes** drawn on the subway's own lines. 123
  have full step-free access, 15 partial, 307 none.
- **"No way in"** — station complexes where every accessibility elevator is out
  in the live feed at once.
- **Outage duration bars** — every unit currently reported out, drawn to how
  long it has been out, on a square root scale.
- **An eleven year grid** — one row per elevator, one cell per month since
  January 2015, shaded by that month's availability.
- **Availability and entrapment trends**, with a zero baseline toggle and a
  per-100-elevators normalisation.

## Data

All from the MTA, all public, none scraped from a web page:

| Feed | Source |
| --- | --- |
| Live outages | MTA `nyct_ene.json`, no key required |
| Monthly availability from 2015 | data.ny.gov `rc78-7x78` |
| Equipment inventory | data.ny.gov `94fv-bak7` |
| Station complexes | data.ny.gov `5f5g-n3cz` |
| Station ridership | data.ny.gov `ak4z-sape` |
| Subway service lines | data.ny.gov `s692-irgq` |

## Running it

```bash
python3 scripts/fetch.py   # pulls every input into data/raw
python3 scripts/build.py   # writes docs/data/lifts.json
```

`fetch.py` fails loudly. It refuses to write a truncated file and treats a live
feed reporting zero outages as a broken feed rather than good news, since across
700-plus units the agency has never reported zero.

## Methodology

Full detail in [docs/METHODOLOGY.html](docs/METHODOLOGY.html), including how
availability is aggregated (summed hours, never averaged percentages), how
removed equipment is separated from broken equipment, and where two MTA files
disagree with each other.
