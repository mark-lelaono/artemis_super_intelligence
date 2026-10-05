"""The agent loop: Claude with whatever systems the registry holds.

Opus 5.5 request-shape notes, each of which is a 400 or a silent quality loss
if an older pattern is carried forward:

- No `temperature` / `top_p` / `top_k`. Any value is rejected.
- No `thinking` field. Adaptive thinking is always on and cannot be disabled;
  `output_config.effort` is the only control over depth. Default effort is
  `medium`; analysis runs here at `high`.
- `max_tokens` covers thinking *plus* response text, so it is set generously.
- Assistant turns are appended **verbatim** (`response.content`), never rebuilt
  by filtering for text blocks. Thinking blocks must be returned unmodified or
  the next request is rejected with a 400.
- `stop_reason == "refusal"` is a real outcome on an HTTP 200; check it before
  reading content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import prompt as prompt_mod
from .registry import Registry

MODEL = "claude-opus-5-5"
MAX_TOKENS = 32000        # thinking + text
EFFORT = "high"
MAX_TURNS = 16


@dataclass
class Trace:
    """What the agent did, so an answer can be audited afterwards."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stopped_because: str = ""

    def render(self, width: int = 300) -> str:
        if not self.calls:
            return "(no tool calls)"
        lines = []
        for c in self.calls:
            lines.append(f"  {c['name']}({json.dumps(c['input'])})")
            lines.append(f"    -> {c['result'][:width]}")
        return "\n".join(lines)

    def summary(self) -> str:
        return (
            f"{self.turns} turn(s), {len(self.calls)} tool call(s), "
            f"{self.input_tokens} in / {self.output_tokens} out tokens"
            + (f", stopped: {self.stopped_because}" if self.stopped_because else "")
        )


def ask(
    question: str,
    registry: Registry,
    *,
    extra_instructions: str | None = None,
    verbose: bool = False,
    max_turns: int = MAX_TURNS,
    client: Any | None = None,
) -> tuple[str, Trace]:
    """Answer `question` using the connected systems. Returns (answer, trace).

    `client` is injectable so the loop can be tested without a network call.
    """
    if client is None:
        import anthropic  # imported late: only this path needs credentials

        client = anthropic.Anthropic()

    system_prompt = prompt_mod.build(registry.systems_overview(), extra_instructions)
    tools = registry.tools()
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    trace = Trace()

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=tools,
            output_config={"effort": EFFORT},
            messages=messages,
        )
        trace.turns += 1
        trace.input_tokens += response.usage.input_tokens
        trace.output_tokens += response.usage.output_tokens

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            trace.stopped_because = f"refusal ({category})"
            return f"[the model declined this request: {category}]", trace

        # Append the assistant turn exactly as received. Thinking blocks are
        # part of it and must survive untouched into the next request.
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            trace.stopped_because = response.stop_reason or "end_turn"
            text = "".join(b.text for b in response.content if b.type == "text")
            return text.strip(), trace

        # Every result for one assistant turn goes back in a single user
        # message. Splitting them teaches the model to stop calling tools in
        # parallel, which costs turns on every later question.
        results = []
        for block in tool_uses:
            if verbose:
                print(f"  -> {block.name}({json.dumps(block.input)})", flush=True)
            out = registry.dispatch(block.name, dict(block.input))
            trace.calls.append(
                {"name": block.name, "input": dict(block.input), "result": out}
            )
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": out}
            )
        messages.append({"role": "user", "content": results})

    trace.stopped_because = f"hit max_turns={max_turns}"
    return f"[stopped after {max_turns} turns without a final answer]", trace
