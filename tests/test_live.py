"""Live tests against the ICPAC service. Opt in with `-m live`.

These are deliberately separate: they depend on a public humanitarian service
being up and on ICPAC still publishing a given forecast, so a failure here is
news about the platform, not necessarily a bug in this code.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artemis.connectors.eahw import EAHWConnector  # noqa: E402
from artemis.connectors.eahw.catalogue import (  # noqa: E402
    fetch_catalogue,
    find_layer,
)
from artemis.connectors.eahw.wms import point_query  # noqa: E402
from artemis.core.registry import Registry  # noqa: E402

pytestmark = pytest.mark.live

NAIROBI = (36.8219, -1.2921)


def test_catalogue_is_reachable_and_populated():
    layers = fetch_catalogue()
    assert len(layers) > 30
    assert any(l.point_queryable for l in layers)


def test_rainfall_forecast_returns_a_plausible_value():
    layer = find_layer("weekly_total_precipitation")
    assert layer is not None and layer.point_queryable
    assert layer.latest_day is not None
    readings = point_query(
        layer.layer_name, *NAIROBI, layer.latest_day, units=layer.units
    )
    assert readings, "no reading for the latest weekly rainfall forecast"
    assert readings[0].variable == "precip"
    # Weekly totals in the region; a value outside this is a units change.
    assert 0 <= readings[0].value < 2000
    assert readings[0].units == "mm"


def test_temperature_is_in_a_physical_range():
    layer = find_layer("weekly_mean_temperature")
    readings = point_query(layer.layer_name, *NAIROBI, layer.latest_day)
    assert readings
    assert -40 < readings[0].value < 60


def test_iso_datetime_is_rejected_before_it_reaches_the_service():
    # Guards the single most expensive mistake: the API publishes
    # 2026-09-29T08:00:00+03:00 but MapServer only matches 2026-09-29.
    with pytest.raises(ValueError, match="bare YYYY-MM-DD"):
        point_query("weekly_total_precipitation", *NAIROBI,
                    "2026-09-29T08:00:00+03:00")


def test_registry_round_trip_against_the_live_service():
    """The whole stack: registry -> connector -> WMS -> provenance envelope."""
    import json

    reg = Registry([EAHWConnector()])
    out = json.loads(reg.dispatch(
        "eahw__query_point",
        {"layer": "weekly_total_precipitation", "place": "nairobi"},
    ))
    assert "error" not in out, out
    assert out["readings"], out
    reading = out["readings"][0]
    assert reading["variable"] == "precip"
    assert reading["units"] == "mm"
    prov = reading["provenance"]
    assert prov["system"] == "eahw"
    assert "mapserver" in prov["source"]
    assert prov["query"]["layer"] == "weekly_total_precipitation"
    assert "ICPAC" in prov["attribution"]


def test_health_probe_reports_the_working_surface():
    health = EAHWConnector().health()
    assert health["implemented"] is True
    counts = health["counts"]
    # The platform's advertised surface exceeds its working surface; the probe
    # exists to make that visible rather than to assert it away.
    assert counts["answered"] >= 1, health
    assert counts["answered"] + counts["empty"] + counts["broken"] >= 15
