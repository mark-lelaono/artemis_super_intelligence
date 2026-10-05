"""Registry behaviour: namespacing, routing, and never raising at the boundary.

A tool that raises ends the agent's run. A tool that returns an error lets it
adapt. Everything here protects that distinction.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artemis.core.connector import Connector, ConnectorError  # noqa: E402
from artemis.core.registry import SEP, Registry  # noqa: E402


class Fake(Connector):
    name = "fake"
    description = "A fake system for tests."
    attribution = "nobody"

    def tools(self):
        return [{
            "name": "ping",
            "description": "Say pong.",
            "input_schema": {
                "type": "object", "properties": {}, "required": [],
                "additionalProperties": False,
            },
        }]

    def dispatch(self, tool, args):
        if tool == "ping":
            return {"pong": True}
        if tool == "refuse":
            raise ConnectorError("declined politely")
        if tool == "explode":
            raise RuntimeError("unexpected boom")
        if tool == "unserialisable":
            return {"fn": object()}
        raise ConnectorError(f"unknown tool {tool!r}")


def reg():
    return Registry([Fake()])


def test_tools_are_namespaced():
    names = [t["name"] for t in reg().tools()]
    assert names == [f"fake{SEP}ping"]


def test_tool_description_names_its_system():
    assert "[system: fake]" in reg().tools()[0]["description"]


def test_dispatch_routes_to_the_connector():
    assert json.loads(reg().dispatch("fake__ping", {})) == {"pong": True}


def test_connector_error_becomes_structured_error():
    out = json.loads(reg().dispatch("fake__refuse", {}))
    assert out["error"] == "declined politely"
    assert out["system"] == "fake" and out["tool"] == "refuse"
    assert "unexpected" not in out


def test_unexpected_exception_is_caught_and_flagged():
    out = json.loads(reg().dispatch("fake__explode", {}))
    assert "unexpected boom" in out["error"]
    assert out["unexpected"] is True


def test_unserialisable_result_is_reported_not_raised():
    out = json.loads(reg().dispatch("fake__unserialisable", {}))
    # default=str rescues most objects; either way it must not raise.
    assert isinstance(out, dict)


def test_unknown_system_lists_what_is_connected():
    out = json.loads(reg().dispatch("nope__ping", {}))
    assert "No system named 'nope'" in out["error"]
    assert "fake" in out["error"]


def test_malformed_tool_name_is_rejected():
    out = json.loads(reg().dispatch("noseparator", {}))
    assert "Malformed tool name" in out["error"]


def test_duplicate_connector_is_refused():
    r = reg()
    with pytest.raises(ValueError, match="already registered"):
        r.add(Fake())


def test_connector_name_may_not_contain_the_separator():
    class Bad(Fake):
        name = f"a{SEP}b"

    with pytest.raises(ValueError, match="separator"):
        Registry([Bad()])


def test_systems_overview_includes_description_and_attribution():
    text = reg().systems_overview()
    assert "fake: A fake system for tests." in text
    assert "nobody" in text


def test_empty_registry_says_so():
    assert "No systems" in Registry().systems_overview()
    assert Registry().tools() == []


def test_health_survives_a_broken_probe():
    class BadHealth(Fake):
        name = "badhealth"

        def health(self):
            raise RuntimeError("probe failed")

    out = Registry([Fake(), BadHealth()]).health()
    assert out["fake"] == {"implemented": False}
    assert "probe failed" in out["badhealth"]["error"]
