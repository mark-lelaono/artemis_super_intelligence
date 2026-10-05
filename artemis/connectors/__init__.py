"""Connectors — one module or package per system Artemis can be pointed at.

- `eahw` — ICPAC East Africa Hazards Watch. Working, verified live.
- `athena` — AWS Athena / S3. Contract only, raises on every call.
"""

from .eahw import EAHWConnector

__all__ = ["EAHWConnector"]
