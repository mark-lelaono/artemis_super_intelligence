"""ICPAC East Africa Hazards Watch connector.

Working and verified against the live service. What it reaches:

- `GET /api/datasets/` — the authoritative catalogue (38 datasets / 60 layers),
  each with its current valid date, units, resolution and prediction-vs-
  monitoring mode. Read it; never hardcode layer names, they move as ICPAC
  publishes.
- `GET /mapserver/` — MapServer WMS 1.3.0, point queries via GetFeatureInfo and
  rendered PNGs via GetMap, over the Greater Horn domain (21-51E, -11.75-24N).

What it cannot reach, and why it matters: WFS and WCS are both disabled on this
endpoint, so there is no raw-grid or vector-feature download. Only the 23 WMS
layers served from `/mapserver/` answer point queries; seven more are WMS on
`/mapcache/` (a pre-rendered tile cache that returns LayerNotDefined) and the
rest are vector tiles needing MVT decoding, which this connector does not do
yet. The exposure layers — health facilities, roads, dams, power plants — are
all in that vector-tile group.
"""

from __future__ import annotations

from typing import Any

from ...core.connector import Connector, ConnectorError, Dataset, Reading, ToolSpec
from . import wms
from .catalogue import Layer, fetch_catalogue, find_layer
from .const import ATTRIBUTION, MAPSERVER
from .places import KNOWN_PLACES


def _as_dataset(layer: Layer) -> Dataset:
    """Project a catalogue Layer onto the generic Dataset shape."""
    reason = None
    if not layer.point_queryable:
        reason = (
            f"served from {layer.endpoint or 'an unknown endpoint'} as "
            f"layer_type={layer.layer_type}; only WMS layers on /mapserver/ "
            "answer point queries"
        )
    return Dataset(
        id=layer.layer_name or layer.dataset_id,
        name=layer.dataset_name,
        description=layer.description,
        units=layer.units,
        readable=layer.point_queryable,
        reason=reason,
        temporal=layer.period_type,
        latest=layer.latest_day,
        resolution=layer.resolution,
        mode=layer.mode,
        extra={"served_from": layer.endpoint, "variable": layer.variable},
    )


class EAHWConnector(Connector):
    """ICPAC East Africa Hazards Watch."""

    name = "eahw"
    description = (
        "ICPAC East Africa Hazards Watch: rainfall, temperature, heat stress, "
        "drought and agricultural hazard layers for the Greater Horn of Africa "
        "(Djibouti, Eritrea, Ethiopia, Kenya, Somalia, South Sudan, Sudan, "
        "Uganda). Gridded forecasts and monitoring products at 0.1 degree to "
        "10 km, read one point at a time. No raw-grid download, so district "
        "totals cannot be computed from this system alone."
    )
    attribution = ATTRIBUTION

    def tools(self) -> list[ToolSpec]:
        places = ", ".join(sorted(KNOWN_PLACES))
        return [
            {
                "name": "list_datasets",
                "description": (
                    "List the hazard layers this platform publishes, with each "
                    "one's current valid date, units, resolution and whether it "
                    "can actually be read. Call this before querying: layer "
                    "names and valid dates change as ICPAC issues new forecasts."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": (
                                "Case-insensitive substring to match against "
                                "dataset and layer names, e.g. 'rainfall', "
                                "'drought', 'temperature', 'heat'."
                            ),
                        },
                        "only_readable": {
                            "type": "boolean",
                            "description": (
                                "When true, return only layers that support "
                                "point queries. Default false."
                            ),
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
            {
                "name": "query_point",
                "description": (
                    "Read the value of one hazard layer at one location and "
                    "date. This is the only way to obtain a number from this "
                    "system."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "layer": {
                            "type": "string",
                            "description": (
                                "Layer name from list_datasets (e.g. "
                                "'weekly_total_precipitation') or a dataset "
                                "name substring to resolve."
                            ),
                        },
                        "place": {
                            "type": "string",
                            "description": f"A known place. One of: {places}",
                        },
                        "lon": {"type": "number", "description": "Longitude, EPSG:4326."},
                        "lat": {"type": "number", "description": "Latitude, EPSG:4326."},
                        "date": {
                            "type": "string",
                            "description": (
                                "YYYY-MM-DD. Omit for the layer's latest valid "
                                "date, which is usually what you want."
                            ),
                        },
                    },
                    "required": ["layer"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "query_many",
                "description": (
                    "Read one layer at several places in a single call, to "
                    "compare locations or trace a gradient. Prefer this over "
                    "repeated query_point calls for more than two places."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "layer": {"type": "string", "description": "Layer name or substring."},
                        "places": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": f"Known places. One of: {places}",
                        },
                        "date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                    },
                    "required": ["layer", "places"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "health",
                "description": (
                    "Probe every readable layer at one location and report which "
                    "actually return data right now. Use it when a query comes "
                    "back empty and you need to tell the user whether the "
                    "platform or the location is the reason."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "place": {
                            "type": "string",
                            "description": f"Probe point. Default nairobi. One of: {places}",
                        }
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        ]

    # -- internals ---------------------------------------------------------

    def _point(self, args: dict[str, Any]) -> tuple[float, float, str]:
        place = str(args.get("place") or "").strip().lower()
        if place:
            if place not in KNOWN_PLACES:
                raise ConnectorError(
                    f"Unknown place {place!r}. Pass lon and lat explicitly, or "
                    f"use one of: {', '.join(sorted(KNOWN_PLACES))}"
                )
            lon, lat = KNOWN_PLACES[place]
            return lon, lat, place
        if args.get("lon") is None or args.get("lat") is None:
            raise ConnectorError("Provide either 'place', or both 'lon' and 'lat'.")
        lon, lat = float(args["lon"]), float(args["lat"])
        return lon, lat, f"{lat:.4f},{lon:.4f}"

    def _read(self, layer_key: str, lon: float, lat: float, date: str | None) -> Any:
        layer = find_layer(layer_key)
        if layer is None:
            raise ConnectorError(
                f"No layer matches {layer_key!r}. Call list_datasets first."
            )
        if not layer.point_queryable:
            raise ConnectorError(
                f"{layer.dataset_name!r} cannot be point-queried "
                f"(layer_type={layer.layer_type}, served_from={layer.endpoint}). "
                "Only WMS layers on /mapserver/ answer point queries. Say that "
                "this variable is not retrievable rather than substituting "
                "another one."
            )
        when = date or layer.latest_day
        if not when:
            raise ConnectorError(f"{layer.dataset_name!r} publishes no valid date.")

        query = {"layer": layer.layer_name, "lon": lon, "lat": lat, "time": when}
        try:
            readings = wms.point_query(
                layer.layer_name, lon, lat, when, units=layer.units
            )
        except (wms.WMSError, ValueError) as exc:
            raise ConnectorError(f"Query failed: {exc}") from exc

        if not readings:
            return {
                "no_data": True,
                "dataset_id": layer.layer_name,
                "dataset_name": layer.dataset_name,
                "query": query,
                "note": (
                    "The service answered with no value here: outside the "
                    "coverage mask, over water, or a gap in this issue. This is "
                    "a finding to report, not a reason to substitute another "
                    "dataset."
                ),
            }

        prov = self.provenance(source=MAPSERVER, query=query)
        return {
            "dataset_name": layer.dataset_name,
            "mode": layer.mode,
            "period_type": layer.period_type,
            "resolution": layer.resolution,
            "readings": [
                Reading(
                    dataset_id=layer.layer_name,
                    variable=r.variable,
                    value=r.value,
                    units=r.units,
                    at={"lon": r.lon, "lat": r.lat, "time": r.time},
                    provenance=prov,
                ).as_dict()
                for r in readings
            ],
        }

    # -- contract ----------------------------------------------------------

    def dispatch(self, tool: str, args: dict[str, Any]) -> Any:
        if tool == "list_datasets":
            datasets = [_as_dataset(l) for l in fetch_catalogue()]
            needle = str(args.get("filter") or "").strip().lower()
            if needle:
                datasets = [
                    d for d in datasets
                    if needle in f"{d.name} {d.id}".lower()
                ]
            if args.get("only_readable"):
                datasets = [d for d in datasets if d.readable]
            return {
                "count": len(datasets),
                "datasets": [d.as_dict() for d in datasets],
            }

        if tool == "query_point":
            lon, lat, label = self._point(args)
            out = self._read(str(args["layer"]), lon, lat, args.get("date"))
            if isinstance(out, dict):
                out["location"] = label
            return out

        if tool == "query_many":
            samples = []
            for place in args.get("places") or []:
                try:
                    lon, lat, label = self._point({"place": place})
                    out = self._read(str(args["layer"]), lon, lat, args.get("date"))
                except ConnectorError as exc:
                    samples.append({"location": place, "error": str(exc)})
                    continue
                if isinstance(out, dict):
                    out["location"] = label
                samples.append(out)
            return {"samples": samples}

        if tool == "health":
            place = str(args.get("place") or "nairobi").lower()
            lon, lat = KNOWN_PLACES.get(place, KNOWN_PLACES["nairobi"])
            return self.health(lon=lon, lat=lat)

        raise ConnectorError(f"Unknown tool {tool!r} for system {self.name!r}.")

    def health(self, *, lon: float = 36.8219, lat: float = -1.2921) -> dict[str, Any]:
        """Probe every readable layer and report what actually answers.

        ICPAC's advertised surface drifts from its working surface: layers go
        empty between issues, and some fail server-side while still being
        listed. Reporting that up front beats discovering it mid-analysis.
        """
        answered, empty, broken = [], [], []
        for layer in fetch_catalogue():
            if not layer.point_queryable or not layer.latest_day:
                continue
            try:
                readings = wms.point_query(
                    layer.layer_name, lon, lat, layer.latest_day, units=layer.units
                )
            except (wms.WMSError, ValueError) as exc:
                broken.append({"layer": layer.layer_name, "error": str(exc)[:160]})
                continue
            if readings:
                r = readings[0]
                answered.append({
                    "layer": layer.layer_name,
                    "variable": r.variable,
                    "value": r.value,
                    "units": r.units,
                    "date": layer.latest_day,
                })
            else:
                empty.append({"layer": layer.layer_name, "date": layer.latest_day})
        return {
            "implemented": True,
            "probe_point": {"lon": lon, "lat": lat},
            "answered": answered,
            "empty": empty,
            "broken": broken,
            "counts": {
                "answered": len(answered),
                "empty": len(empty),
                "broken": len(broken),
            },
        }
