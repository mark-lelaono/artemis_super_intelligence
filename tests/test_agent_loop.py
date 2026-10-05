"""The agent loop, driven by a fake client — no API key, no network.

The loop has three properties that matter more than anything else it does, and
all three are invisible until they break in production:

1. Assistant turns are echoed back **verbatim**, so Opus 5.5 thinking blocks
   survive into the next request. Rebuilding the turn from text blocks earns a
   400 on the following call.
2. All tool results for one turn go back in **one** user message. Splitting them
   teaches the model to stop calling tools in parallel.
3. A `refusal` stop reason is handled before content is read.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artemis.core.agent import ask  # noqa: E402
from artemis.core.connector import Connector  # noqa: E402
from artemis.core.registry import Registry  # noqa: E402


class Echo(Connector):
    name = "echo"
    description = "Returns what it is given."

    def tools(self):
        return [{
            "name": "get",
            "description": "Get a value.",
            "input_schema": {
                "type": "object", "properties": {"k": {"type": "string"}},
                "required": ["k"], "additionalProperties": False,
            },
        }]

    def dispatch(self, tool, args):
        return {"value": 42, "key": args.get("k")}


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _thinking(t=""):
    return SimpleNamespace(type="thinking", thinking=t)


def _tool_use(tid, name, inp):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=inp)


def _resp(content, stop_reason="end_turn", **kw):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        **kw,
    )


class FakeClient:
    """Replays a scripted list of responses and records what it was sent."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def test_single_turn_answer():
    client = FakeClient([_resp([_text("the answer")])])
    answer, trace = ask("q", Registry([Echo()]), client=client)
    assert answer == "the answer"
    assert trace.turns == 1 and trace.calls == []
    assert trace.input_tokens == 10 and trace.output_tokens == 5


def test_tool_call_then_answer():
    client = FakeClient([
        _resp([_thinking(), _tool_use("t1", "echo__get", {"k": "x"})],
              stop_reason="tool_use"),
        _resp([_text("42 it is")]),
    ])
    answer, trace = ask("q", Registry([Echo()]), client=client)
    assert answer == "42 it is"
    assert trace.turns == 2
    assert len(trace.calls) == 1
    assert trace.calls[0]["name"] == "echo__get"
    assert '"value": 42' in trace.calls[0]["result"]


def test_assistant_turn_is_echoed_verbatim_preserving_thinking():
    thinking = _thinking("reasoning")
    tool = _tool_use("t1", "echo__get", {"k": "x"})
    client = FakeClient([
        _resp([thinking, tool], stop_reason="tool_use"),
        _resp([_text("done")]),
    ])
    ask("q", Registry([Echo()]), client=client)

    sent = client.requests[1]["messages"]
    assistant = next(m for m in sent if m["role"] == "assistant")
    # The exact objects, in order — not a rebuilt text-only turn.
    assert assistant["content"] == [thinking, tool]
    assert assistant["content"][0] is thinking


def test_parallel_tool_results_go_back_in_one_message():
    client = FakeClient([
        _resp(
            [_tool_use("t1", "echo__get", {"k": "a"}),
             _tool_use("t2", "echo__get", {"k": "b"})],
            stop_reason="tool_use",
        ),
        _resp([_text("both")]),
    ])
    ask("q", Registry([Echo()]), client=client)

    sent = client.requests[1]["messages"]
    user_turns = [m for m in sent if m["role"] == "user"]
    # The original question, plus exactly one message carrying both results.
    assert len(user_turns) == 2
    results = user_turns[1]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result"]
    assert {b["tool_use_id"] for b in results} == {"t1", "t2"}


def test_refusal_is_handled_before_content_is_read():
    client = FakeClient([
        _resp([], stop_reason="refusal",
              stop_details=SimpleNamespace(type="refusal", category="cyber",
                                           explanation="no")),
    ])
    answer, trace = ask("q", Registry([Echo()]), client=client)
    assert "declined" in answer and "cyber" in answer
    assert trace.stopped_because == "refusal (cyber)"


def test_request_shape_matches_opus_5_5():
    client = FakeClient([_resp([_text("ok")])])
    ask("q", Registry([Echo()]), client=client)
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    # Rejected with a 400 on this model family.
    assert "temperature" not in req
    assert "top_p" not in req and "top_k" not in req
    # Adaptive thinking is always on; effort is the only depth control.
    assert "thinking" not in req
    assert req["output_config"] == {"effort": "high"}
    # max_tokens must cover thinking plus text.
    assert req["max_tokens"] >= 16000
    assert req["tools"][0]["name"] == "echo__get"


def test_max_turns_is_enforced():
    # Always asks for a tool, never answers.
    client = FakeClient([
        _resp([_tool_use(f"t{i}", "echo__get", {"k": "x"})], stop_reason="tool_use")
        for i in range(5)
    ])
    answer, trace = ask("q", Registry([Echo()]), client=client, max_turns=3)
    assert "stopped after 3 turns" in answer
    assert trace.turns == 3 and "max_turns=3" in trace.stopped_because


def test_tool_error_does_not_end_the_run():
    class Broken(Echo):
        name = "broken"

        def dispatch(self, tool, args):
            raise RuntimeError("kaboom")

    client = FakeClient([
        _resp([_tool_use("t1", "broken__get", {"k": "x"})], stop_reason="tool_use"),
        _resp([_text("I could not read that")]),
    ])
    answer, trace = ask("q", Registry([Broken()]), client=client)
    assert answer == "I could not read that"
    assert "kaboom" in trace.calls[0]["result"]


def test_system_prompt_carries_the_provenance_rule():
    client = FakeClient([_resp([_text("ok")])])
    ask("q", Registry([Echo()]), client=client)
    system = client.requests[0]["system"]
    assert "must come from a tool result" in system
    assert "echo: Returns what it is given." in system
