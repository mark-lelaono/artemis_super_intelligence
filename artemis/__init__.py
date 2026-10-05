"""Artemis — point an agent at a system and have it read, analyse and explain.

Artemis is a small core plus a connector per system. The core owns the agent
loop, the tool registry, and one rule enforced everywhere: no number reaches a
user without provenance that lets them re-run the query.

Connectors shipped here:

- `eahw` — ICPAC East Africa Hazards Watch. Working and verified live.
- `athena` — AWS Athena / S3. Contract and schema only, not implemented.

Adding a system means implementing three methods. See `docs/CONNECTORS.md`.
"""

__version__ = "0.1.0"

from .core import Connector, ConnectorError, Dataset, Provenance, Reading, Registry

__all__ = [
    "Connector",
    "ConnectorError",
    "Dataset",
    "Provenance",
    "Reading",
    "Registry",
    "__version__",
]
