"""Dataset catalogue for East Africa Hazards Watch.

The platform is a Next.js front end over a Wagtail/geomanager backend. The
catalogue endpoint `/api/datasets/` is the authoritative list of what exists,
what its current valid date is, and which MapServer layer backs it. Reading it
beats hardcoding layer names: the platform adds datasets, and `latest_date`
moves every time ICPAC issues a new forecast.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .const import API

CACHE_DIR = Path.home() / ".cache" / "artemis" / "eahw"
CATALOGUE_TTL_SECONDS = 3600  # forecasts are issued daily at most; an hour is polite

# Where each layer is actually served, derived from the catalogue's own tile
# template rather than guessed from the name. Only the MapServer-backed WMS
# layers answer GetFeatureInfo; the /mapcache/ ones are a pre-rendered tile
# cache (LayerNotDefined on a point query), and the vector layers are MVT.
_MAPSERVER = "/mapserver/"


def _endpoint_of(layer_config: dict[str, Any] | None) -> str | None:
    """The service path a layer's tiles come from, e.g. '/mapserver/'."""
    tiles = ((layer_config or {}).get("source") or {}).get("tiles") or []
    if not tiles:
        return None
    match = re.search(r"icpac\.net(/[a-z0-9-]+/?)", tiles[0])
    return match.group(1) if match else None


@dataclass(frozen=True)
class Layer:
    """One queryable layer of a dataset."""

    dataset_id: str
    dataset_name: str
    layer_name: str | None       # the MapServer LAYERS= value
    layer_type: str | None       # "wms" | "vector_file" | ...
    variable: str | None         # e.g. "ppt"
    latest_date: str | None      # ISO datetime from the API
    period_type: str | None      # weekly | monthly | seasonal | ...
    mode: str | None             # prediction | monitoring
    resolution: str | None
    description: str | None
    units: str | None            # scraped from the legend's trailing item
    endpoint: str | None = None  # "/mapserver/", "/mapcache/", "/api/", ...
    multi_temporal: bool = False
    legend: list[dict[str, Any]] = field(default_factory=list)

    @property
    def point_queryable(self) -> bool:
        """True when `wms.point_query` can return a number for this layer."""
        return (
            self.layer_type == "wms"
            and bool(self.layer_name)
            and self.endpoint == _MAPSERVER
        )

    @property
    def latest_day(self) -> str | None:
        """`latest_date` as the bare `YYYY-MM-DD` the WMS TIME parameter wants.

        The API reports a full ISO datetime with an offset
        (`2026-09-29T08:00:00+03:00`), but MapServer returns "no results" for
        that form and matches only the date. This is the single most important
        detail in the whole integration.
        """
        return self.latest_date.split("T")[0] if self.latest_date else None

    def summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "layer_name": self.layer_name,
            "variable": self.variable,
            "units": self.units,
            "period_type": self.period_type,
            "mode": self.mode,
            "resolution": self.resolution,
            "latest_date": self.latest_day,
            "point_queryable": self.point_queryable,
            "served_from": self.endpoint,
            "description": self.description,
        }


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _read_cache(key: str, ttl: int) -> Any | None:
    p = _cache_path(key)
    if not p.exists() or (time.time() - p.stat().st_mtime) > ttl:
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(key: str, payload: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _cache_path(key).with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(_cache_path(key))


def _units_from_legend(legend: dict[str, Any] | None) -> str | None:
    """Recover units from a legend, or None when the legend is categorical.

    geomanager legends come in two shapes. A numeric ramp lists its break
    values as item names and tacks the unit on as a trailing item
    ("1", "10", "30", ..., "mm"). A categorical legend lists class labels
    ("No Warning", "Watch", "Warning") and has no unit at all.

    Treating the last label of a categorical legend as a unit produces
    nonsense like `[Not Analyzed]`, so a unit is only recognised when every
    *other* item parses as a number.
    """
    items = (legend or {}).get("items") or []
    if len(items) < 2:
        return None
    names = [str(i.get("name", "")).strip() for i in items]
    last = names[-1]
    if not last:
        return None

    def _numeric(text: str) -> bool:
        try:
            float(text)
            return True
        except ValueError:
            return False

    if _numeric(last):
        return None  # pure numeric ramp, unit not published
    if not all(_numeric(n) for n in names[:-1]):
        return None  # categorical legend, no unit to recover
    if any(ch.isdigit() for ch in last):
        return None  # a break value like "53 °C", not a bare unit
    return last


def fetch_catalogue(*, force: bool = False, timeout: float = 30.0) -> list[Layer]:
    """Return every layer the platform publishes, newest metadata first."""
    raw = None if force else _read_cache("datasets", CATALOGUE_TTL_SECONDS)
    if raw is None:
        resp = httpx.get(f"{API}/datasets/", timeout=timeout)
        resp.raise_for_status()
        raw = resp.json()
        _write_cache("datasets", raw)

    layers: list[Layer] = []
    for ds in raw:
        for lyr in ds.get("layers") or []:
            more = lyr.get("moreInfo") or {}
            layers.append(
                Layer(
                    dataset_id=str(ds.get("id")),
                    dataset_name=str(ds.get("name")),
                    layer_name=lyr.get("layerName"),
                    layer_type=lyr.get("layerType"),
                    variable=lyr.get("dataset_variable"),
                    latest_date=ds.get("latest_date"),
                    period_type=ds.get("period_type"),
                    mode=ds.get("dataset_mode"),
                    resolution=ds.get("resolution"),
                    description=more.get("text") or lyr.get("name"),
                    units=_units_from_legend(lyr.get("legendConfig")),
                    endpoint=_endpoint_of(lyr.get("layerConfig")),
                    multi_temporal=bool(lyr.get("multiTemporal")),
                    legend=(lyr.get("legendConfig") or {}).get("items") or [],
                )
            )
    return layers


def find_layer(needle: str, layers: list[Layer] | None = None) -> Layer | None:
    """Resolve a layer by exact MapServer name, then by name substring."""
    layers = layers if layers is not None else fetch_catalogue()
    key = needle.strip().lower()
    for lyr in layers:
        if (lyr.layer_name or "").lower() == key:
            return lyr
    for lyr in layers:
        haystack = f"{lyr.dataset_name} {lyr.layer_name or ''}".lower()
        if key in haystack:
            return lyr
    return None


def fetch_metadata(metadata_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Full ISO-style metadata record for a dataset (`/api/metadata/<uuid>/`)."""
    cached = _read_cache(f"meta-{metadata_id}", CATALOGUE_TTL_SECONDS * 24)
    if cached is not None:
        return cached
    resp = httpx.get(f"{API}/metadata/{metadata_id}/", timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    _write_cache(f"meta-{metadata_id}", payload)
    return payload
