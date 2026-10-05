"""AWS Athena / S3 connector — CONTRACT ONLY, NOT IMPLEMENTED.

This file exists to show what the second connector looks like, and to be
honest that it does not work yet. Every method raises. Nothing here has been
run against a real warehouse.

Why this one is next: pairing a hazard grid with an exposure table is the whole
point of impact-based early warning. ICPAC's platform gives you the hazard;
your own S3/Athena exposure database gives you what is exposed to it. One
registry holding both lets a single question reach both.

To implement, in this order:

1. **Schema discovery.** `SHOW TABLES` / `DESCRIBE`, or read the Glue catalogue
   directly via boto3. Return one `Dataset` per table with its column list in
   `extra`. The model cannot write correct SQL against a schema it has not seen,
   and guessing column names is the most common failure in text-to-SQL.

2. **A read-only, cost-bounded query tool.** Three guards, none optional:
   - Reject anything that is not a single `SELECT` / `WITH` statement. Parse it;
     do not pattern-match for keywords, since `SELECT ... ; DROP ...` defeats a
     substring check.
   - Set a `MaxBytesScanned` workgroup limit. Athena bills per byte scanned and
     a model exploring a partitioned lake can scan a lot of it. Make the budget
     a constructor argument, not a constant.
   - Require a partition predicate on partitioned tables, or refuse with an
     error that names the partition key. A full-table scan on a country-
     partitioned exposure dataset is the expensive mistake.

3. **Provenance per row set.** `self.provenance(source=f"athena://{db}.{table}",
   query={"sql": sql, "execution_id": qid, "bytes_scanned": n})`. Athena returns
   a query execution id — carry it, because it makes a result auditable later.

4. **Result truncation that announces itself.** Return at most N rows and set
   `"truncated": true` with the full row count. A silently truncated result set
   makes the model state a wrong total with complete confidence.

Credentials: standard boto3 resolution (env, profile, instance role). Do not
accept keys as constructor arguments; do not log them.
"""

from __future__ import annotations

from typing import Any

from ..core.connector import Connector, ConnectorError, ToolSpec

_NOT_IMPLEMENTED = (
    "The Athena connector is a contract, not an implementation. See the module "
    "docstring in artemis/connectors/athena.py for the four steps to build it."
)


class AthenaConnector(Connector):
    """Query a Glue-catalogued S3 data lake through Athena. Not implemented."""

    name = "athena"
    description = (
        "AWS Athena over an S3 data lake. NOT IMPLEMENTED — every call raises. "
        "Intended for exposure and reference tables that pair with a hazard "
        "system."
    )

    def __init__(
        self,
        database: str,
        *,
        workgroup: str = "primary",
        output_location: str | None = None,
        max_bytes_scanned: int = 1_000_000_000,
        max_rows: int = 500,
    ) -> None:
        self.database = database
        self.workgroup = workgroup
        self.output_location = output_location
        self.max_bytes_scanned = max_bytes_scanned
        self.max_rows = max_rows

    def tools(self) -> list[ToolSpec]:
        return [
            {
                "name": "list_tables",
                "description": (
                    "List tables in the database with their columns and "
                    "partition keys. Call before writing SQL: never guess a "
                    "column name."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "filter": {"type": "string", "description": "Name substring."}
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
            {
                "name": "query",
                "description": (
                    "Run a read-only SELECT. Must include a partition predicate "
                    "on partitioned tables. Results are capped and will say so "
                    "when truncated."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "A single SELECT or WITH."}
                    },
                    "required": ["sql"],
                    "additionalProperties": False,
                },
            },
        ]

    def dispatch(self, tool: str, args: dict[str, Any]) -> Any:
        raise ConnectorError(_NOT_IMPLEMENTED)

    def health(self) -> dict[str, Any]:
        return {"implemented": False, "note": _NOT_IMPLEMENTED}
