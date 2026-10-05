"""EAHW connector: schemas, place handling, and the Dataset projection.

Offline only — no network. The live checks are in test_live.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artemis.connectors.eahw import EAHWConnector, _as_dataset  # noqa: E402
from artemis.connectors.eahw.catalogue import Layer  # noqa: E402
from artemis.connectors.eahw.places import KNOWN_PLACES  # noqa: E402
from artemis.core.connector import ConnectorError  # noqa: E402


@pytest.fixture
def conn():
    return EAHWConnector()


def test_every_tool_schema_is_strict(conn):
    for spec in conn.tools():
        schema = spec["input_schema"]
        assert spec["name"] and spec["description"]
        assert schema["type"] == "object"
        # Without this the model can pass arguments dispatch() silently drops.
        assert schema["additionalProperties"] is False
        assert "required" in schema


def test_tool_names_unique(conn):
    names = [s["name"] for s in conn.tools()]
    assert len(names) == len(set(names))


def test_connector_declares_attribution(conn):
    assert conn.attribution and "ICPAC" in conn.attribution


def test_known_place_resolves(conn):
    lon, lat, label = conn._point({"place": "Nairobi"})
    assert label == "nairobi"
    assert 36.5 < lon < 37.2 and -1.6 < lat < -1.0


def test_unknown_place_is_refused_not_guessed(conn):
    with pytest.raises(ConnectorError, match="Unknown place"):
        conn._point({"place": "Atlantis"})


def test_explicit_coordinates_accepted(conn):
    lon, lat, label = conn._point({"lon": 32.0, "lat": 5.0})
    assert (lon, lat) == (32.0, 5.0) and "5.0000" in label


def test_missing_location_is_an_error(conn):
    with pytest.raises(ConnectorError, match="either 'place'"):
        conn._point({})


def test_unknown_tool_is_a_connector_error(conn):
    with pytest.raises(ConnectorError, match="Unknown tool"):
        conn.dispatch("no_such_tool", {})


def test_all_places_inside_the_coverage_domain():
    # The service masks to 21-51E / -11.75-24N. A point outside it would
    # always return no data, which would look like a platform fault.
    for name, (lon, lat) in KNOWN_PLACES.items():
        assert 21 <= lon <= 51, name
        assert -11.75 <= lat <= 24, name


def test_all_eight_igad_states_have_an_anchor():
    # One per member state at minimum, so no country is unreachable.
    expected = ["nairobi", "addis ababa", "mogadishu", "juba",
                "khartoum", "kampala", "djibouti", "asmara"]
    for place in expected:
        assert place in KNOWN_PLACES


class TestDatasetProjection:
    def _layer(self, **kw):
        base = dict(
            dataset_id="d1", dataset_name="Total Rainfall Forecast",
            layer_name="weekly_total_precipitation", layer_type="wms",
            variable="ppt", latest_date="2026-09-29T08:00:00+03:00",
            period_type="weekly", mode="prediction", resolution="0.1dd",
            description="rainfall", units="mm", endpoint="/mapserver/",
        )
        base.update(kw)
        return Layer(**base)

    def test_mapserver_layer_is_readable(self):
        d = _as_dataset(self._layer())
        assert d.readable is True and d.reason is None
        assert d.id == "weekly_total_precipitation"
        assert d.units == "mm" and d.mode == "prediction"
        # latest is the bare date the WMS wants, not the ISO datetime.
        assert d.latest == "2026-09-29"

    def test_mapcache_layer_is_unreadable_with_a_reason(self):
        d = _as_dataset(self._layer(endpoint="/mapcache/"))
        assert d.readable is False
        assert "/mapcache/" in d.reason and "point queries" in d.reason

    def test_vector_layer_is_unreadable(self):
        d = _as_dataset(self._layer(layer_type="vector_file", endpoint="/api/"))
        assert d.readable is False

    def test_as_dict_drops_empty_fields(self):
        d = _as_dataset(self._layer(units=None, description=None))
        out = d.as_dict()
        assert "units" not in out and "description" not in out
        assert out["id"] == "weekly_total_precipitation"
