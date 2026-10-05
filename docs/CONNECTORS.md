# Writing a connector

A connector is how you point Artemis at a system. Three methods, one of which
is optional.

## The contract

```python
from artemis.core.connector import Connector, ConnectorError, Dataset, Reading

class MySystem(Connector):
    name = "mysystem"              # tool prefix; identifier-safe, no "__"
    description = "..."            # the model reads this to decide to use you
    attribution = "..."            # licence/credit carried into outputs

    def tools(self) -> list[dict]:     # Anthropic tool schemas, unprefixed
        ...

    def dispatch(self, tool, args):    # run one tool, return JSON-able
        ...

    def health(self) -> dict:          # optional but strongly advised
        ...
```

Register it and it is live:

```python
from artemis.core.registry import Registry
registry = Registry([MySystem(), EAHWConnector()])
```

The registry prefixes your tool names (`mysystem__query`), so two systems can
both expose a tool called `query`. It also catches every exception at the
boundary and turns it into a structured tool error, because **a tool that
raises ends the agent's run, while a tool that reports an error lets the agent
adapt.**

## Why there is no universal `read()` method

The obvious design is one method — `read(dataset, **selectors)` — and it is
wrong. A point query on a 0.1° raster and a `GROUP BY` over a fact table have
genuinely different shapes. Forcing both through one signature produces a
connector that lies about what it can do: either the raster connector pretends
to accept SQL, or the SQL connector pretends its tables have coordinates.

So each connector declares its own tools, and the core supplies what is
actually common to every system: namespacing, the provenance envelope, uniform
error and no-data handling, and health reporting.

## The five rules

**1. Discovery is a tool, not a constant.** Expose `list_datasets` (or
`list_tables`) that asks the system what it holds *now*. Hardcoded names rot.
The EAHW connector reads ICPAC's catalogue on every run, which is why it picked
up a new forecast date without a code change.

**2. Every value carries provenance.** Use `self.provenance(source=..., query=...)`
and attach it to each `Reading`. The query dict must be sufficient to re-run the
call. This is the point of the whole project: a number whose origin cannot be
restated is a number nobody should act on.

**3. "No data" is not an error.** Return `{"no_data": True, ...}` with a note
explaining what an empty result means for your system. Raise `ConnectorError`
only when something actually failed. Conflating the two makes the model treat a
genuine zero as a malfunction, or a malfunction as a zero — both bad.

**4. Report unreadable datasets as unreadable.** Set `Dataset.readable=False`
with a `reason`. Most real systems advertise more than they serve: a view whose
permissions changed, a layer whose backing table errors, a stream that stopped.
Saying so up front lets the model tell the user "that variable is not
retrievable" instead of hitting a stack trace mid-analysis.

**5. Set `additionalProperties: false` on every schema.** Otherwise the model
can pass arguments your `dispatch` silently drops, and you get a confidently
wrong answer with no error anywhere.

## Health probes

Implement `health()` for any system whose advertised surface drifts from its
working surface — which is most of them. The EAHW probe reads every readable
layer at one point and buckets them into answered / empty / broken. That probe
is how we learned a third of ICPAC's advertised query surface returns nothing
right now.

## Kinds of system, and what each needs

**A tiled or raster map service (WMS/WMTS/XYZ).** `GetFeatureInfo` is your point
query. Read `artemis/connectors/eahw/wms.py` — it documents four details that
each silently return nothing when wrong, which is the normal experience with
OGC services.

**A SQL warehouse (Athena, Postgres, BigQuery).** See
`artemis/connectors/athena.py` for the contract. Non-negotiables: expose the
schema before accepting SQL (the model cannot write correct SQL against a schema
it has not seen, and guessing column names is the top text-to-SQL failure);
parse the statement rather than keyword-matching it, since `SELECT ...; DROP ...`
defeats a substring check; bound the cost; and make truncation announce itself —
a silently truncated result set makes the model state a wrong total with total
confidence.

**A streaming or live feed.** The agent loop is request/response, so a connector
cannot block on a stream. Expose it as windowed reads instead — `latest(n)`,
`window(start, end)`, `summary(since)` — and put the window in the provenance so
the reading is reproducible. If the system has a visualisation layer, find the
API the dashboard itself calls rather than scraping the rendered page: the EAHW
connector exists because `/api/datasets/` was sitting under a MapLibre front end
the whole time.

**A REST API with a spec.** If there is OpenAPI, generate the tool schemas from
it rather than hand-writing them, and keep the operation id as the tool name so
errors are traceable to the spec.

## Testing

Split offline from live, as `tests/` does:

- **Offline, always run.** Parsing against recorded fixtures, schema strictness,
  error shapes, argument handling. `tests/test_agent_loop.py` drives the whole
  loop with a fake client and no credentials.
- **Live, opt-in** (`pytest -m live`). A failure here is news about the external
  system, not necessarily a bug in your code — which is exactly why it must not
  gate the default suite.

Assert on ranges and shapes, not exact values. An external system's numbers
change; `0 <= rainfall < 2000` catches a units change, while
`rainfall == 11.6376` just breaks next week.
