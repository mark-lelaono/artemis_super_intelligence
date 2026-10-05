"""Offline tests for the parsing layer — no network, no API key.

The GML fixture is a verbatim response captured from the live service on
2026-09-28 for `weekly_total_precipitation` at Nairobi.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artemis.connectors.eahw.catalogue import (  # noqa: E402
    _endpoint_of,
    _units_from_legend,
)
from artemis.connectors.eahw.wms import WMSError, parse_gml_values  # noqa: E402

GML_OK = """<?xml version="1.0" encoding="UTF-8"?>
<msGMLOutput xmlns:gml="http://www.opengis.net/gml">
    <weekly_total_precipitation_layer>
    <gml:name>Weekly Total Precipitation Forecast</gml:name>
        <weekly_total_precipitation_feature>
            <gml:boundedBy>
                <gml:Box srsName="EPSG:4326">
                    <gml:coordinates>36.800000,-1.300000 36.900000,-1.200000</gml:coordinates>
                </gml:Box>
            </gml:boundedBy>
            <id>3918479</id>
            <precip>11.637558937072754</precip>
        </weekly_total_precipitation_feature>
    </weekly_total_precipitation_layer>
</msGMLOutput>
"""

GML_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<msGMLOutput xmlns:gml="http://www.opengis.net/gml">
</msGMLOutput>
"""

EXCEPTION_POSTGIS = """<?xml version='1.0' encoding="UTF-8" standalone="no" ?>
<ServiceExceptionReport version="1.1.1">
<ServiceException>
msPostGISLayerWhichShapes(): Query error. Error executing query.
</ServiceException>
</ServiceExceptionReport>
"""

EXCEPTION_CODED = """<?xml version='1.0' encoding="UTF-8" standalone="no" ?>
<ServiceExceptionReport version="1.1.1">
<ServiceException code="LayerNotDefined">
Invalid layer(s) given in the LAYERS parameter.
</ServiceException>
</ServiceExceptionReport>
"""


def test_parses_value_and_ignores_gml_scaffolding():
    values = parse_gml_values(GML_OK)
    assert values == {"precip": pytest.approx(11.637558937072754)}
    # The feature id is not a measurement and must not leak in as one.
    assert "id" not in values
    assert "coordinates" not in values


def test_empty_response_is_no_data_not_an_error():
    assert parse_gml_values(GML_EMPTY) == {}


def test_service_exception_becomes_wms_error():
    with pytest.raises(WMSError, match="msPostGISLayerWhichShapes"):
        parse_gml_values(EXCEPTION_POSTGIS)


def test_exception_code_is_preserved():
    with pytest.raises(WMSError, match="LayerNotDefined"):
        parse_gml_values(EXCEPTION_CODED)


def test_malformed_xml_raises_wms_error():
    with pytest.raises(WMSError, match="Malformed"):
        parse_gml_values("<msGMLOutput><unclosed>")


class TestUnitsHeuristic:
    def test_numeric_ramp_with_trailing_unit(self):
        legend = {"items": [{"name": "1"}, {"name": "10"}, {"name": "mm"}]}
        assert _units_from_legend(legend) == "mm"

    def test_categorical_legend_has_no_units(self):
        legend = {"items": [
            {"name": "No Warning"}, {"name": "Watch"}, {"name": "Not Analyzed"},
        ]}
        assert _units_from_legend(legend) is None

    def test_break_value_is_not_a_unit(self):
        # "53 °C" is the top break of the heat-stress ramp, not a bare unit.
        legend = {"items": [{"name": "20"}, {"name": "35"}, {"name": "53 °C"}]}
        assert _units_from_legend(legend) is None

    def test_pure_numeric_ramp_publishes_no_unit(self):
        legend = {"items": [{"name": "1"}, {"name": "2"}, {"name": "3"}]}
        assert _units_from_legend(legend) is None

    def test_missing_or_tiny_legend(self):
        assert _units_from_legend(None) is None
        assert _units_from_legend({"items": []}) is None
        assert _units_from_legend({"items": [{"name": "mm"}]}) is None


class TestEndpointDerivation:
    def test_mapserver_layer(self):
        cfg = {"source": {"tiles": [
            "https://eahazardswatch.icpac.net/mapserver/?SERVICE=WMS&LAYERS=x"
        ]}}
        assert _endpoint_of(cfg) == "/mapserver/"

    def test_mapcache_layer(self):
        cfg = {"source": {"tiles": [
            "https://eahazardswatch.icpac.net/mapcache/?SERVICE=WMS&LAYERS=y"
        ]}}
        assert _endpoint_of(cfg) == "/mapcache/"

    def test_vector_tile_layer(self):
        cfg = {"source": {"tiles": [
            "https://eahazardswatch.icpac.net/api/vector-tiles/{z}/{x}/{y}"
        ]}}
        assert _endpoint_of(cfg) == "/api/"

    def test_absent_source(self):
        assert _endpoint_of(None) is None
        assert _endpoint_of({"source": {}}) is None
