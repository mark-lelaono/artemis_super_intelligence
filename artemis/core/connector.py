"""The Connector contract — what it takes to point Artemis at a new system.

The design question this file answers: how do you generalise over a WMS raster
service, a SQL warehouse, and a streaming dashboard without flattening them
into a lowest common denominator?

The answer is *not* one universal `read(dataset, **selectors)` method. A point
query on a 0.1-degree raster and a GROUP BY over a fact table have genuinely
different shapes, and forcing them through one signature produces a connector
that lies about what it can do.

Instead, each connector **declares its own tools** and the core supplies what
is actually common to every system:

- tool namespacing, so two connectors can both have a `query` tool
- a provenance envelope every result carries, so no number is ever unsourced
- uniform error and no-data handling, so a failure is data the model reasons
  about rather than an exception that ends the run
- health reporting, so "this dataset is advertised but broken" is visible
  before an analysis rather than after

Implement `tools()`, `dispatch()`, and ideally `health()`. That is the whole
contract. See `docs/CONNECTORS.md` for a worked example.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

# How a tool is declared: the Anthropic tool schema, minus the namespace the
# registry prepends to `name`.
ToolSpec = dict[str, Any]


@dataclass(frozen=True)
class Provenance:
    """Where a value came from, in enough detail to re-run it.

    Every Reading carries one. This is the single most important type in the
    project: an early-warning narrative is only as trustworthy as its ability
    to be checked, and a number whose origin cannot be restated is a number
    nobody should act on.
    """

    system: str              # connector name, e.g. "eahw"
    source: str              # the service, table or endpoint actually hit
    query: dict[str, Any]    # the parameters, sufficient to reproduce
    retrieved_at: float = field(default_factory=time.time)
    attribution: str | None = None   # licence / credit line to carry downstream

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Dataset:
    """One thing a system can be asked about.

    `readable` is deliberately part of the contract. Most real systems
    advertise more than they serve — a view whose permissions changed, a
    forecast layer whose backing table errors, a stream that stopped. A
    connector that reports `readable=False` with a `reason` lets the model say
    "that variable is not retrievable" instead of discovering it as a stack
    trace halfway through an analysis.
    """

    id: str
    name: str
    description: str | None = None
    units: str | None = None
    readable: bool = True
    reason: str | None = None        # why not, when readable is False
    temporal: str | None = None      # e.g. "weekly", "streaming", "static"
    latest: str | None = None        # most recent valid value, ISO-ish
    resolution: str | None = None
    mode: str | None = None          # "prediction" | "monitoring" | "actual"
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return {k: v for k, v in out.items() if v not in (None, {}, [])}


@dataclass(frozen=True)
class Reading:
    """One measured value, with its provenance attached and non-optional."""

    dataset_id: str
    variable: str
    value: Any
    provenance: Provenance
    units: str | None = None
    at: dict[str, Any] = field(default_factory=dict)   # where/when, system's terms

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "variable": self.variable,
            "value": self.value,
            "units": self.units,
            "at": self.at,
            "provenance": self.provenance.as_dict(),
        }


class ConnectorError(RuntimeError):
    """The system answered, but refused or failed.

    Raise this (not a bare Exception) for anything the model should be told
    about and could reasonably work around — a disabled layer, a permission
    denial, a malformed response. The registry turns it into a structured tool
    error rather than letting it end the run.
    """


class Connector(ABC):
    """A system Artemis can be pointed at."""

    #: Short machine name. Becomes the tool prefix, so keep it identifier-safe.
    name: str = "unnamed"

    #: One or two sentences the model reads to decide whether to use this
    #: system at all. Say what it holds and what its limits are.
    description: str = ""

    #: Licence or credit line that must travel with any derived output.
    attribution: str | None = None

    @abstractmethod
    def tools(self) -> list[ToolSpec]:
        """Anthropic tool schemas this system exposes.

        Use unprefixed names (`list_datasets`, `query_point`); the registry
        namespaces them as `<connector>__<tool>`. Set
        `additionalProperties: false` on every schema so the model cannot pass
        arguments `dispatch` would silently drop.
        """

    @abstractmethod
    def dispatch(self, tool: str, args: dict[str, Any]) -> Any:
        """Run one unprefixed tool name and return a JSON-serialisable result.

        Raise `ConnectorError` for an expected failure. Return a dict with a
        `no_data` key for a clean "the system answered with nothing" — that is
        a finding, not an error, and the two must not be conflated.
        """

    def health(self) -> dict[str, Any]:
        """Optional: probe what actually works right now.

        Default is "not implemented", which is honest. Implement it for any
        system whose advertised surface drifts from its working surface — which
        is most of them.
        """
        return {"implemented": False}

    def provenance(self, source: str, query: dict[str, Any]) -> Provenance:
        """Build a Provenance stamped with this connector's identity."""
        return Provenance(
            system=self.name,
            source=source,
            query=query,
            attribution=self.attribution,
        )
