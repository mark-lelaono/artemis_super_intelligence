# The chain, input to output

What happens between a typed question and a cited answer, which technology does
each step, and where the credentials sit.

```
INPUT: "Compare rainfall for Nairobi and Juba this week"
   │
   ▼
┌─ 1. CLI ──────────────────── artemis/cli.py ────────────── argparse ─┐
│  parse --model / --effort / --max-tokens → cmd_ask()                 │
└──────────────────────────────────────────────────────────────────────┘
   ▼
┌─ 2. ASSEMBLE ─────────────── core/registry.py ───────────────────────┐
│  Registry([EAHWConnector()])                                         │
│  .tools()            → 4 namespaced schemas (eahw__query_point, …)   │
│  .systems_overview() → what is connected, plus attribution           │
└──────────────────────────────────────────────────────────────────────┘
   ▼
┌─ 3. PROMPT + SHAPE ───────── core/prompt.py, core/models.py ─────────┐
│  prompt.build()  → the provenance rules + the systems block          │
│  build_params()  → per-family request shape (thinking / effort)      │
└──────────────────────────────────────────────────────────────────────┘
   ▼
┌─ 4. MODEL ────────────────── core/agent.py ──── anthropic SDK ───────┐
│  client.messages.create(model=claude-opus-5-5, tools=…, effort=high) │
│  ← returns thinking blocks + tool_use blocks                         │
└──────────────────────────────────────────────────────────────────────┘
   ▼  (tool_use)
┌─ 5. ROUTE ────────────────── core/registry.py ───────────────────────┐
│  "eahw__query_point" → split on "__" → EAHWConnector.dispatch()      │
│  catches everything: an error becomes data, never a crash            │
└──────────────────────────────────────────────────────────────────────┘
   ▼
┌─ 6. CONNECTOR ───────────── connectors/eahw/ ────────────────────────┐
│  6a  places.py     "nairobi" → (36.8219, -1.2921)  refuse if unknown │
│  6b  catalogue.py  GET /api/datasets/   ── httpx ── cached 1h        │
│                    resolve layer; require served_from == /mapserver/ │
│  6c                latest_date 2026-10-06T08:00+03:00 → 2026-10-06   │
│  6d  wms.py        GET /mapserver/ GetFeatureInfo  ── httpx ──       │
│                    v1.1.1 · STYLES= · info_format=GML · 0.1° bbox    │
│  6e                parse GML  ── xml.etree.ElementTree ──            │
│                    → {"precip": 11.6007}                             │
│  6f  connector.py  stamp Provenance(system, source, query, time)     │
└──────────────────────────────────────────────────────────────────────┘
   ▼
┌─ 7. RETURN ───────────────── JSON ───────────────────────────────────┐
│  every tool_result for the turn in ONE user message                  │
│  loop back to 4 · max 16 turns                                       │
└──────────────────────────────────────────────────────────────────────┘
   ▼
OUTPUT: narrative text + Trace(model, turns, calls, tokens)
```

## Technology per link

| Stage | Technology | Why this one |
|---|---|---|
| CLI | Python 3.10+, `argparse` | Stdlib; no dependency needed to parse arguments |
| HTTP | **httpx** | Timeouts and params done properly; only hard dep of the data layer |
| Catalogue | ICPAC `/api/datasets/` (Wagtail/geomanager) | Authoritative, so layer names and dates are never hardcoded |
| Cache | JSON on disk, `~/.cache/artemis/eahw`, 1 h TTL | Public operational service — politeness, not performance |
| Hazard query | **OGC WMS 1.1.1** `GetFeatureInfo` → MapServer 8 | 1.1.1 keeps `EPSG:4326` as lon,lat; 1.3.0 flips the axes |
| Parsing | `xml.etree.ElementTree` | GML is the only format carrying the value; stdlib suffices |
| Types | `dataclasses` (frozen) | `Provenance` / `Reading` / `Dataset` immutable by construction |
| Model | **anthropic SDK** + Claude Opus 5.5 | Tool use, adaptive thinking, 1M context |
| Tool wire | JSON Schema, `additionalProperties: false` | Model cannot pass arguments `dispatch` would drop |
| Tests | **pytest** — 107 offline, 6 live | A fake client drives the whole loop with no key |

Three dependencies in total: `httpx`, `anthropic`, `pytest`. Everything else is
the standard library.

## Where credentials sit

```
ICPAC     → no auth at all      (returns HTTP 200 with nothing sent)
Anthropic → ANTHROPIC_API_KEY   (step 4 only)
```

Steps 1-3 and 5-7 need no credentials, which is why `datasets`, `query` and
`health` return real numbers with nothing configured.

## The four invariants the chain preserves

1. **No number without provenance.** Step 6f is not optional on a `Reading`.
2. **An error is data.** Step 5 never raises; a tool that raises ends the run,
   a tool that reports lets the model adapt.
3. **Assistant turns are echoed verbatim.** Step 7 appends `response.content`
   unmodified, so thinking blocks survive into the next request.
4. **Nothing about the data is hardcoded.** Step 6b reads the catalogue every
   run, which is why a new forecast issue needs no code change.

## Links not yet in the chain

| Missing | Technology it needs |
|---|---|
| Exposure layers (health facilities, roads, dams) | MVT decode — `mapbox-vector-tile` or raw protobuf against `/api/vector-tiles/{z}/{x}/{y}` |
| Zonal statistics (% of a district affected) | `rasterio` + `geopandas` + `shapely`, plus rasters from Geoportal/HDX, since WCS is disabled upstream |
| In-house exposure database | `boto3` → Athena; the connector is a contract that raises |
| Memory across sessions | SQLite or Postgres read at query time — data, not weights |
| Eval harness | pytest or a small runner, scored on whether the tool was called and provenance cited |

## Two honest caveats

**Step 4 has never executed.** Steps 1-3 and 5-7 are proven against the live
service; the model call is structurally tested against a fake client and has
never spent a token.

**Step 6d currently succeeds on 8 layers of 60.** That is the real ceiling on
analysis today — a property of the upstream platform, not of this chain. Run
`python -m artemis health` for the current figure rather than trusting this one.
