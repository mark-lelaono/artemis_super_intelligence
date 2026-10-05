"""WMS client for the East Africa Hazards Watch MapServer.

Three hard-won details, each of which silently returns nothing when wrong:

1. `STYLES=` must be present and may be empty. MapServer 8 rejects a request
   without it (`Missing required parameter STYLES`).
2. `TIME` must be a bare `YYYY-MM-DD`. The catalogue's own
   `2026-09-29T08:00:00+03:00` yields "Search returned no results"; the date
   alone returns the pixel.
3. `INFO_FORMAT=application/vnd.ogc.gml`, not `text/plain`. The plain-text
   response names the feature but omits every attribute, so the value is only
   reachable through GML.

WFS and WCS are disabled on this endpoint ("WCS request not enabled"), so
point queries and rendered images are the whole vector of access. There is no
raw-grid download here — for that, go to the ICPAC Geoportal, IGAD GeoNode or
HDX.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

from .const import MAPSERVER

GML_INFO_FORMAT = "application/vnd.ogc.gml"

# A tight box around the point, sampled at its centre pixel. 0.05 degrees is
# ~5.5 km, comfortably wider than the coarsest published grid (0.25dd) so the
# centre pixel always falls inside a cell.
_HALF_SPAN_DEG = 0.05
_GRID = 101          # odd, so (50, 50) is exactly the centre
_CENTRE = _GRID // 2


class WMSError(RuntimeError):
    """The service answered, but with a ServiceException."""


@dataclass(frozen=True)
class PointValue:
    """One pixel of one layer at one instant — with its whole provenance."""

    layer_name: str
    variable: str
    value: float
    lon: float
    lat: float
    time: str
    units: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer_name": self.layer_name,
            "variable": self.variable,
            "value": self.value,
            "units": self.units,
            "lon": self.lon,
            "lat": self.lat,
            "time": self.time,
        }


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _raise_if_exception(body: str) -> None:
    if "ServiceException" not in body:
        return
    try:
        root = ET.fromstring(body)
        node = next(
            (e for e in root.iter() if _strip_ns(e.tag) == "ServiceException"), None
        )
        detail = (node.text or "").strip() if node is not None else body[:300]
        code = (node.get("code") or "") if node is not None else ""
    except ET.ParseError:
        detail, code = body[:300], ""
    raise WMSError(f"{code + ': ' if code else ''}{detail}".strip())


def parse_gml_values(body: str) -> dict[str, float]:
    """Pull `{variable: value}` out of a GML GetFeatureInfo response.

    Kept separate from the HTTP call so it can be tested against a recorded
    response with no network.
    """
    _raise_if_exception(body)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise WMSError(f"Malformed GML response: {exc}") from exc

    values: dict[str, float] = {}
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        # Skip GML structural elements and MapServer's internal feature id.
        if tag == "id" or "gml" in elem.tag or not (elem.text or "").strip():
            continue
        if len(list(elem)):  # container, not a leaf
            continue
        try:
            values[tag] = float(elem.text.strip())
        except ValueError:
            continue
    return values


def point_query(
    layer_name: str,
    lon: float,
    lat: float,
    time: str,
    *,
    units: str | None = None,
    timeout: float = 30.0,
) -> list[PointValue]:
    """Sample `layer_name` at (lon, lat) for the given `YYYY-MM-DD`.

    Returns one PointValue per numeric band the layer exposes; an empty list
    means the service answered cleanly with no data at that location and time
    (ocean, outside the mask, or a gap in the forecast).
    """
    if len(time) != 10 or time.count("-") != 2:
        raise ValueError(
            f"time must be a bare YYYY-MM-DD date, got {time!r}. "
            "MapServer returns no results for a full ISO datetime."
        )

    params = {
        "service": "WMS",
        "version": "1.1.1",          # 1.1.1 keeps EPSG:4326 as lon,lat
        "request": "GetFeatureInfo",
        "layers": layer_name,
        "query_layers": layer_name,
        "styles": "",                # required by MapServer 8, may be empty
        "srs": "EPSG:4326",
        "bbox": (
            f"{lon - _HALF_SPAN_DEG},{lat - _HALF_SPAN_DEG},"
            f"{lon + _HALF_SPAN_DEG},{lat + _HALF_SPAN_DEG}"
        ),
        "width": _GRID,
        "height": _GRID,
        "x": _CENTRE,
        "y": _CENTRE,
        "info_format": GML_INFO_FORMAT,
        "time": time,
    }
    resp = httpx.get(MAPSERVER, params=params, timeout=timeout)
    resp.raise_for_status()
    return [
        PointValue(
            layer_name=layer_name,
            variable=var,
            value=val,
            lon=lon,
            lat=lat,
            time=time,
            units=units,
        )
        for var, val in parse_gml_values(resp.text).items()
    ]


def render_map(
    layer_name: str,
    bbox: tuple[float, float, float, float],
    time: str,
    *,
    width: int = 800,
    height: int = 800,
    timeout: float = 60.0,
) -> bytes:
    """GetMap PNG for a bbox (minlon, minlat, maxlon, maxlat).

    Useful when a spatial pattern matters more than a single number — hand the
    bytes to the model as an image block.
    """
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": layer_name,
        "styles": "",
        "srs": "EPSG:4326",
        "bbox": ",".join(str(v) for v in bbox),
        "width": width,
        "height": height,
        "format": "image/png",
        "transparent": "TRUE",
        "time": time,
    }
    resp = httpx.get(MAPSERVER, params=params, timeout=timeout)
    resp.raise_for_status()
    if "xml" in (resp.headers.get("content-type") or ""):
        _raise_if_exception(resp.text)
    return resp.content
