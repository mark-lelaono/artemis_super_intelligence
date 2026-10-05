"""The registry: assembles tools from connectors and routes calls back.

One registry can hold several systems at once, which is the point — asking
"does the rainfall forecast line up with what our own gauge table recorded"
is a question across two connectors, and the model should be able to reach
both in one conversation.
"""

from __future__ import annotations

import json
from typing import Any

from .connector import Connector, ConnectorError, ToolSpec

#: Separator between connector name and tool name. Double underscore so it
#: cannot collide with a tool name containing a single one.
SEP = "__"


class Registry:
    """Holds connectors, exposes their tools, dispatches calls to them."""

    def __init__(self, connectors: list[Connector] | None = None) -> None:
        self._connectors: dict[str, Connector] = {}
        for c in connectors or []:
            self.add(c)

    def add(self, connector: Connector) -> None:
        if SEP in connector.name:
            raise ValueError(
                f"Connector name {connector.name!r} may not contain {SEP!r}: "
                "it is the tool-name separator."
            )
        if connector.name in self._connectors:
            raise ValueError(f"Connector {connector.name!r} already registered.")
        self._connectors[connector.name] = connector

    @property
    def connectors(self) -> dict[str, Connector]:
        return dict(self._connectors)

    def tools(self) -> list[ToolSpec]:
        """Every tool from every connector, namespaced and ready for the API."""
        out: list[ToolSpec] = []
        for name, conn in self._connectors.items():
            for spec in conn.tools():
                namespaced = dict(spec)
                namespaced["name"] = f"{name}{SEP}{spec['name']}"
                # Prefix the description so the model knows which system it is
                # reaching when several are loaded.
                namespaced["description"] = (
                    f"[system: {name}] {spec.get('description', '').strip()}"
                )
                out.append(namespaced)
        return out

    def systems_overview(self) -> str:
        """A short block for the system prompt describing what is connected."""
        if not self._connectors:
            return "No systems are connected."
        lines = []
        for name, conn in self._connectors.items():
            lines.append(f"- {name}: {conn.description.strip()}")
            if conn.attribution:
                lines.append(f"  attribution: {conn.attribution}")
        return "\n".join(lines)

    def dispatch(self, namespaced_tool: str, args: dict[str, Any]) -> str:
        """Route one namespaced tool call and return JSON text for the model.

        Never raises. Every failure becomes a JSON object with an `error` key,
        because a tool that raises ends the agent's run, while a tool that
        reports an error lets it adapt — try another dataset, narrow a query,
        or tell the user plainly that the value is unavailable.
        """
        if SEP not in namespaced_tool:
            return json.dumps(
                {"error": f"Malformed tool name {namespaced_tool!r}; "
                          f"expected <system>{SEP}<tool>."}
            )
        system, tool = namespaced_tool.split(SEP, 1)
        conn = self._connectors.get(system)
        if conn is None:
            return json.dumps(
                {"error": f"No system named {system!r}. "
                          f"Connected: {', '.join(self._connectors) or 'none'}."}
            )
        try:
            result = conn.dispatch(tool, args)
        except ConnectorError as exc:
            return json.dumps({"error": str(exc), "system": system, "tool": tool})
        except Exception as exc:  # unexpected: still data, not a crash
            return json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "system": system,
                    "tool": tool,
                    "unexpected": True,
                }
            )
        try:
            return json.dumps(result, default=str)
        except (TypeError, ValueError) as exc:
            return json.dumps(
                {"error": f"Connector {system!r} returned unserialisable "
                          f"result: {exc}"}
            )

    def health(self) -> dict[str, Any]:
        """Health report for every connected system."""
        out: dict[str, Any] = {}
        for name, conn in self._connectors.items():
            try:
                out[name] = conn.health()
            except Exception as exc:  # a broken probe must not hide the others
                out[name] = {"error": f"{type(exc).__name__}: {exc}"}
        return out
