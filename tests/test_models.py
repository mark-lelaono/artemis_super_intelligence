"""Per-model request shapes.

Each assertion here stands for a 400 the API would otherwise return at runtime,
after the user has already typed their question.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artemis.core.models import (  # noqa: E402
    ALL_EFFORT,
    MIN_THINKING_BUDGET,
    PROFILES,
    THINKING_ADAPTIVE,
    THINKING_ALWAYS_ON,
    THINKING_BUDGET,
    UnsupportedSetting,
    build_params,
    profile_for,
)


class TestAlwaysOnThinking:
    """Opus 5.5 and the Fable family: no thinking field at all."""

    @pytest.mark.parametrize(
        "model",
        ["claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5"],
    )
    def test_no_thinking_field_is_sent(self, model):
        params, notes = build_params(model, effort="high", max_tokens=32000)
        # Both {"type":"disabled"} and a budget are rejected on these models,
        # so the only correct move is to omit the field.
        assert "thinking" not in params
        assert params["output_config"] == {"effort": "high"}
        assert notes == []

    def test_effort_defaults_to_high(self):
        params, _ = build_params("claude-opus-5-5", effort=None, max_tokens=32000)
        assert params["output_config"] == {"effort": "high"}


class TestAdaptiveThinking:
    """Opus 4.8/4.7/4.6, Sonnet 5/4.6: adaptive must be explicit."""

    @pytest.mark.parametrize(
        "model", ["claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5"]
    )
    def test_adaptive_is_sent_explicitly(self, model):
        # Omitting it means no thinking at all on Opus 4.8 and earlier, which
        # silently degrades analytical work rather than erroring.
        params, _ = build_params(model, effort="high", max_tokens=32000)
        assert params["thinking"] == {"type": "adaptive"}
        assert "budget_tokens" not in params["thinking"]

    @pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-4-6"])
    def test_xhigh_is_rejected_before_the_4_6_generation(self, model):
        with pytest.raises(UnsupportedSetting, match="does not support effort"):
            build_params(model, effort="xhigh", max_tokens=32000)

    def test_4_6_still_takes_max(self):
        params, _ = build_params("claude-opus-4-6", effort="max", max_tokens=32000)
        assert params["output_config"] == {"effort": "max"}


class TestBudgetThinking:
    """Haiku 4.5: effort is rejected; thinking needs a manual budget."""

    def test_budget_is_set_and_effort_omitted(self):
        params, notes = build_params("claude-haiku-4-5", effort=None, max_tokens=8000)
        assert "output_config" not in params
        assert params["thinking"]["type"] == "enabled"
        budget = params["thinking"]["budget_tokens"]
        assert MIN_THINKING_BUDGET <= budget < 8000
        assert any("manual thinking budget" in n for n in notes)

    def test_explicit_effort_is_a_clear_local_error(self):
        with pytest.raises(UnsupportedSetting, match="does not support the effort"):
            build_params("claude-haiku-4-5", effort="high", max_tokens=8000)

    def test_max_tokens_too_small_for_a_budget_is_refused(self):
        with pytest.raises(UnsupportedSetting, match="too small"):
            build_params("claude-haiku-4-5", effort=None, max_tokens=1024)


class TestUnknownModels:
    def test_unknown_model_is_accepted_not_rejected(self):
        # A model released after this table was written is real, not a typo.
        params, notes = build_params(
            "claude-opus-6", effort="high", max_tokens=32000
        )
        assert params["model"] == "claude-opus-6"
        assert "thinking" not in params
        assert params["output_config"] == {"effort": "high"}

    def test_the_assumption_is_announced(self):
        _, notes = build_params("claude-opus-6", effort=None, max_tokens=32000)
        assert any("not in the known-model table" in n for n in notes)

    def test_profile_for_reports_recognition(self):
        _, known = profile_for("claude-opus-5-5")
        _, unknown = profile_for("claude-something-else")
        assert known is True and unknown is False


class TestInvariantsAcrossEveryProfile:
    @pytest.mark.parametrize("model", sorted(PROFILES))
    def test_no_sampling_parameters_are_ever_sent(self, model):
        profile = PROFILES[model]
        effort = profile.effort_levels[-1] if profile.supports_effort else None
        params, _ = build_params(model, effort=effort, max_tokens=32000)
        # No current model accepts these; any value is a 400.
        for forbidden in ("temperature", "top_p", "top_k"):
            assert forbidden not in params

    @pytest.mark.parametrize("model", sorted(PROFILES))
    def test_max_tokens_is_always_set(self, model):
        profile = PROFILES[model]
        effort = profile.effort_levels[0] if profile.supports_effort else None
        params, _ = build_params(model, effort=effort, max_tokens=32000)
        assert params["max_tokens"] == 32000
        assert params["model"] == model

    @pytest.mark.parametrize("model", sorted(PROFILES))
    def test_every_supported_effort_level_builds(self, model):
        for level in PROFILES[model].effort_levels:
            params, _ = build_params(model, effort=level, max_tokens=32000)
            assert params["output_config"]["effort"] == level

    def test_thinking_modes_are_the_three_known_ones(self):
        modes = {p.thinking for p in PROFILES.values()}
        assert modes <= {THINKING_ALWAYS_ON, THINKING_ADAPTIVE, THINKING_BUDGET}

    def test_effort_levels_are_ordered_subsets_of_the_full_ladder(self):
        for model, profile in PROFILES.items():
            assert set(profile.effort_levels) <= set(ALL_EFFORT), model
            ranked = [ALL_EFFORT.index(e) for e in profile.effort_levels]
            assert ranked == sorted(ranked), model
