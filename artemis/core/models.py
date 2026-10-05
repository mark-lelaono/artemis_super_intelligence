"""Per-model request shapes.

Swapping the model string is not enough: the Claude families disagree about how
thinking is configured and whether `effort` exists at all, and the wrong
combination is an HTTP 400 rather than a graceful degradation. Three shapes are
in play.

**Thinking always on** (Opus 5.5, Fable 5/5.1, Mythos 5/5.1). Omit `thinking`
entirely. `{"type": "disabled"}` and `{"type": "enabled", "budget_tokens": N}`
are both rejected. `effort` is the only control over depth.

**Adaptive, opt-in** (Opus 5, 4.8, 4.7, 4.6, Sonnet 5, 4.6). Send
`{"type": "adaptive"}` *explicitly*: on Opus 4.8 and earlier, omitting the field
means the model runs without thinking at all, which quietly costs quality on
exactly the analytical work this project does. (Opus 5 runs adaptive either way;
sending it explicitly is harmless and keeps one code path.)

**Manual budget** (Haiku 4.5 and older). `effort` is rejected outright; thinking
needs `{"type": "enabled", "budget_tokens": N}` with N >= 1024 and N <
`max_tokens`.

No current model accepts `temperature`, `top_p` or `top_k`, so no shape here
ever sets them.

An unrecognised model ID is assumed to be newer than this table and gets the
always-on shape, since that is where the families have been converging. The
caller is told, because an assumption about a request shape should not be
silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Effort levels, weakest to strongest, as of the Claude 5 generation.
ALL_EFFORT = ("low", "medium", "high", "xhigh", "max")

THINKING_ALWAYS_ON = "always_on"
THINKING_ADAPTIVE = "adaptive"
THINKING_BUDGET = "budget"


@dataclass(frozen=True)
class ModelProfile:
    """How to shape a request for one family of models."""

    thinking: str
    effort_levels: tuple[str, ...] = ALL_EFFORT

    @property
    def supports_effort(self) -> bool:
        return bool(self.effort_levels)


#: Exact model IDs. Dateless IDs are themselves pinned snapshots, so these do
#: not drift underneath us.
PROFILES: dict[str, ModelProfile] = {
    # Thinking always on; effort is the only depth control.
    "claude-opus-5-5": ModelProfile(THINKING_ALWAYS_ON),
    "claude-fable-5-1": ModelProfile(THINKING_ALWAYS_ON),
    "claude-fable-5": ModelProfile(THINKING_ALWAYS_ON),
    "claude-opus-5": ModelProfile(THINKING_ALWAYS_ON),
    # Adaptive must be requested explicitly or these run without thinking.
    "claude-opus-4-8": ModelProfile(THINKING_ADAPTIVE),
    "claude-opus-4-7": ModelProfile(THINKING_ADAPTIVE),
    "claude-sonnet-5": ModelProfile(THINKING_ADAPTIVE),
    # No xhigh before Opus 4.7.
    "claude-opus-4-6": ModelProfile(THINKING_ADAPTIVE, ("low", "medium", "high", "max")),
    "claude-sonnet-4-6": ModelProfile(THINKING_ADAPTIVE, ("low", "medium", "high", "max")),
    # Effort is rejected; thinking takes a manual token budget.
    "claude-haiku-4-5": ModelProfile(THINKING_BUDGET, ()),
    "claude-haiku-4-5-20251001": ModelProfile(THINKING_BUDGET, ()),
}

#: Shape assumed for a model this table has not heard of.
UNKNOWN_PROFILE = ModelProfile(THINKING_ALWAYS_ON)

#: Minimum the API accepts for a manual thinking budget.
MIN_THINKING_BUDGET = 1024


class UnsupportedSetting(ValueError):
    """The requested model and effort combination would be rejected by the API."""


def profile_for(model: str) -> tuple[ModelProfile, bool]:
    """Return (profile, recognised) for a model ID."""
    if model in PROFILES:
        return PROFILES[model], True
    return UNKNOWN_PROFILE, False


def build_params(
    model: str,
    *,
    effort: str | None,
    max_tokens: int,
) -> tuple[dict[str, Any], list[str]]:
    """Build the model-specific half of a request, plus any notes for the user.

    Raises `UnsupportedSetting` when the caller explicitly asked for something
    the model would reject — better a clear local error than a 400 from the API
    after the question has been typed.
    """
    profile, recognised = profile_for(model)
    notes: list[str] = []
    if not recognised:
        notes.append(
            f"{model!r} is not in the known-model table; assuming thinking is "
            "always on and effort is supported. If the API rejects the request, "
            "add the model to artemis/core/models.py."
        )

    params: dict[str, Any] = {"model": model, "max_tokens": max_tokens}

    if profile.supports_effort:
        chosen = effort or "high"
        if chosen not in profile.effort_levels:
            raise UnsupportedSetting(
                f"{model} does not support effort {chosen!r}. "
                f"Supported: {', '.join(profile.effort_levels)}."
            )
        params["output_config"] = {"effort": chosen}
    elif effort is not None:
        raise UnsupportedSetting(
            f"{model} does not support the effort parameter at all; it is "
            "rejected with a 400. Drop --effort, or choose a model that "
            "supports it."
        )

    if profile.thinking == THINKING_ADAPTIVE:
        # Explicit, because omitting it means no thinking on Opus 4.8 and earlier.
        params["thinking"] = {"type": "adaptive"}
    elif profile.thinking == THINKING_BUDGET:
        budget = max(MIN_THINKING_BUDGET, max_tokens // 2)
        if budget >= max_tokens:
            raise UnsupportedSetting(
                f"max_tokens={max_tokens} is too small for {model}: a manual "
                f"thinking budget needs at least {MIN_THINKING_BUDGET} tokens "
                "and must stay below max_tokens."
            )
        params["thinking"] = {"type": "enabled", "budget_tokens": budget}
        notes.append(
            f"{model} uses a manual thinking budget ({budget} tokens) because "
            "it does not support adaptive thinking or effort."
        )
    # THINKING_ALWAYS_ON: send no thinking field at all.

    return params, notes
