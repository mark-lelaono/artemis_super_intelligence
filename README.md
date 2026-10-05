# Artemis

**Point an agent at a system. Have it read the data, analyse it, and explain
itself — with every number traceable back to the query that produced it.**

Artemis is a small core plus one connector per system. The core owns the agent
loop, the tool registry, and a single rule enforced everywhere: **no figure
reaches a user without provenance that lets them re-run it.**

The first working connector reads [ICPAC East Africa Hazards
Watch](https://eahazardswatch.icpac.net/) — rainfall, temperature, heat stress
and drought products for the Greater Horn of Africa.

```
$ python -m artemis query weekly_total_precipitation --place nairobi
Total Rainfall Forecast: precip = 11.6007 mm
  system=eahw dataset=weekly_total_precipitation lon=36.8219 lat=-1.2921 time=2026-10-06
  source=https://eahazardswatch.icpac.net/mapserver/
```

Nothing there is hardcoded. The valid date came from ICPAC's catalogue at call
time, which is why it tracks each new forecast issue without a code change.

## Status — read this before relying on anything

| Component | State |
|---|---|
| Connector contract, registry, prompt (`artemis/core/`) | Working, 50 offline tests |
| Agent loop (`artemis/core/agent.py`) | **Loop verified with a fake client; never run against the real API** |
| ICPAC Hazards Watch connector (`artemis/connectors/eahw/`) | **Working, verified live** |
| AWS Athena connector (`artemis/connectors/athena.py`) | **Contract only — every call raises** |
| Vector/exposure layers, zonal statistics, streaming | Not built |
| Tests | 50 offline + 6 live, all passing |

The data path is proven end to end. The LLM path is structurally tested but has
never spent a token, because no API key was available when it was written. Set
`ANTHROPIC_API_KEY` and run `python -m artemis ask --trace` to be the first.

**This is a foundation to build on, not a finished product.** The name is
aspirational; the code is early.

## Install

```bash
git clone https://github.com/mark-lelaono/artemis_super_intelligence
cd artemis_super_intelligence
pip install -r requirements.txt
```

## Use

No credentials needed for anything except `ask`:

```bash
python -m artemis systems                      # what is connected, and its tools
python -m artemis datasets --readable          # what can actually be read
python -m artemis datasets --filter drought
python -m artemis query weekly_total_precipitation --place nairobi
python -m artemis query "Mean Temprature" --lon 35.6 --lat 3.12
python -m artemis health                       # what each system really serves

export ANTHROPIC_API_KEY=sk-ant-...
python -m artemis ask "Compare this week's rainfall forecast for Nairobi, Juba \
and Mogadishu and say what it means for flood risk" --trace
```

## Architecture

```
artemis/
  core/
    connector.py   # the ABC you implement: tools(), dispatch(), health()
    registry.py    # namespaces tools, routes calls, never raises at the boundary
    prompt.py      # the provenance discipline, stated to the model
    agent.py       # the Opus 5.5 tool-use loop
  connectors/
    eahw/          # ICPAC Hazards Watch — working
    athena.py      # AWS Athena — contract only
  cli.py
```

Adding a system means implementing three methods. See
**[docs/CONNECTORS.md](docs/CONNECTORS.md)**.

### Why there is no universal `read()` method

The obvious design — one `read(dataset, **selectors)` for everything — is wrong.
A point query on a 0.1° raster and a `GROUP BY` over a fact table have genuinely
different shapes, and forcing both through one signature produces connectors
that lie about what they can do.

So each connector declares its own tools, and the core supplies what really is
common: tool namespacing, the provenance envelope, uniform error and no-data
handling, and health reporting.

### Four design decisions

**Every number carries provenance.** `Provenance` records the system, the exact
endpoint, and a query dict sufficient to re-run the call. It is not optional on
a `Reading`. An early-warning narrative is only as trustworthy as its ability to
be checked.

**"No data" is a finding, not an error.** Connectors return `{"no_data": true}`
with an explanation, and raise only on genuine failure. Conflating the two makes
a real zero look like a malfunction, or a malfunction look like a zero.

**Unreadable datasets say so up front.** `Dataset.readable=False` with a reason,
so the model can tell a user "that variable is not retrievable" instead of
hitting a stack trace mid-analysis.

**Unknown place names are refused, not geocoded.** 22 anchor points across the
eight IGAD states; anything else needs explicit coordinates. A silently
mis-geocoded town yields a number that looks entirely plausible and is wrong.

## What we learned about ICPAC's platform

The connector exists because the platform has a JSON catalogue and an OGC WMS
underneath its MapLibre front end. **Scraping the rendered page would have been
slower, more fragile, and unkind to a public humanitarian service.**

- **`GET /api/datasets/`** — the authoritative catalogue: 38 datasets / 60
  layers, each with current valid date, units, resolution, legend, description,
  and `prediction` vs `monitoring` mode.
- **`GET /api/metadata/<uuid>/`** — full ISO-19115-style record per dataset.
- **`GET /mapserver/`** — MapServer WMS 1.3.0, domain 21–51°E / −11.75–24°N.

### Four details that silently return nothing when wrong

1. **`TIME` must be a bare `YYYY-MM-DD`.** The catalogue publishes
   `2026-09-29T08:00:00+03:00`; passing that yields *"Search returned no
   results"* — a clean HTTP 200 with nothing in it. The date alone returns the
   pixel. This one took longest to find and is the difference between "this
   platform has no API" and the output at the top of this README.
2. **`INFO_FORMAT` must be `application/vnd.ogc.gml`.** `text/plain` names the
   feature and omits every attribute, so the value exists only in GML.
3. **`STYLES=` must be present**, and may be empty. MapServer 8 refuses without
   it.
4. **Use WMS 1.1.1**, where `EPSG:4326` is lon,lat. In 1.3.0 the axis order
   flips and your coordinates land elsewhere.

### What is not reachable there

- **WFS and WCS are both disabled** (`"WCS request not enabled"`). No raw-grid
  or vector-feature download, so **district-level totals cannot be computed from
  this system alone.** For rasters use the [ICPAC
  Geoportal](https://geoportal.icpac.net/), [IGAD
  GeoNode](http://geonode.igad.int/), or [ICPAC on
  HDX](https://data.humdata.org/organization/igad-climate-prediction-and-application-center).
- **Only 23 of 60 layers answer point queries.** Seven more are WMS on
  `/mapcache/`, a pre-rendered tile cache returning `LayerNotDefined`; the rest
  are vector tiles needing MVT decoding, which is not built yet.
- The exposure layers — **health facilities, roads, dams, power plants,
  airports, protected areas** — are all in that vector-tile group.

### The working surface is smaller than the advertised one

`python -m artemis health`, run 2026-10-05 at Nairobi, of the 23 readable
layers: **8 answered, 11 returned no data, 4 failed server-side.**

- Answering: weekly rainfall total, rainfall anomaly, heat-stress risk, mean
  temperature, three monthly rainfall tercile layers.
- Empty: the heavy/very-heavy/extreme rainfall thresholds (plausibly genuine
  zeroes), and **every seasonal tercile layer** — nothing at Nairobi, Mogadishu,
  Juba *or* Turkana on its own advertised date, so the published date looks
  stale rather than the location being wrong.
- Failing: all four `subseasonal_*` layers return
  `msPostGISLayerWhichShapes(): Query error` — a fault on ICPAC's side, not in
  this client, and quite possibly already fixed by the time you read this.

Run the probe yourself rather than trusting this paragraph. That is what it is
for.

## Limits to understand before acting on output

- **A grid cell is not a district.** Resolutions run 0.1° to 10 km. A point
  query samples one cell. District statements need zonal statistics over a real
  boundary, which needs raw rasters this endpoint will not provide.
- **A forecast is not an observation.** Check `mode`.
- **A weekly total says nothing about intensity.** 11 mm over a week and 11 mm
  in twenty minutes are different flood problems.
- **No rate limiting.** The catalogue is cached for an hour; keep it that way.
  This is a public operational service.
- **The agent layer is unvalidated.** The loop is correct by construction and by
  test, but no one has yet checked whether the model's *narratives* are any
  good. Build an eval set before anyone acts on them.

## Roadmap, in the order worth doing

1. **Run the agent once** and read the trace. Everything else is speculation
   until that happens.
2. **Vector tiles** — decode `/api/vector-tiles/{z}/{x}/{y}` to reach health
   facilities, roads, dams and power plants. This turns hazard *lookup* into
   impact *analysis*, and it is where the value is.
3. **Implement the Athena connector** against your own exposure database.
   Hazard × exposure in one registry is impact-based early warning; this already
   owns the hazard half.
4. **Zonal statistics over admin boundaries** — "how much of Turkana is in
   severe drought", not "what is the value at this point".
5. **An eval set** — fixed questions with known-correct values, so a prompt
   change that breaks accuracy gets caught.
6. **Sibling ICPAC platforms**, each with its own service: `droughtwatch`,
   `floodwatch`, `agriculturehotspots` (has its own `/wms`),
   `anticipatoryaction`, `eatriggersthresholds`.

## Attribution

Code: MIT, see [LICENSE](LICENSE).

Data accessed through Artemis keeps its own licence. ICPAC data is © ICPAC /
IGAD Climate Prediction and Applications Centre, East Africa Hazards Watch
(eahazardswatch.icpac.net), and the `eahw` connector carries that attribution in
every reading's provenance so it travels into derived work.
