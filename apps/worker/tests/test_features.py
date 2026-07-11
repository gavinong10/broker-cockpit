"""Unit tests for feature-factory logic that must never regress: model
resolution, slugging, and the accept/revert branch rules. The SSH runner is
mocked — no host interaction, no builds."""
from app import features


def test_resolve_model_ui_choice_wins():
    assert features.resolve_model("model: opus\ndo x", "sonnet") == "claude-sonnet-5"


def test_resolve_model_prompt_override():
    assert features.resolve_model("model: opus\nbuild a thing", None) == "claude-opus-4-8"


def test_resolve_model_prompt_raw_claude_id():
    assert features.resolve_model("model: claude-haiku-4-5\nx", None) == "claude-haiku-4-5"


def test_resolve_model_default_is_fable():
    assert features.resolve_model("just build a widget", None) == "claude-fable-5"


def test_resolve_model_unknown_alias_falls_back():
    assert features.resolve_model("model: gpt4\nx", None) == "claude-fable-5"


def test_slugify_bounded_and_clean():
    s = features.slugify("Add a Refresh!! button, please, to the Exposure tab now")
    assert s == "add-a-refresh-button-please" and len(s) <= 40
    assert features.slugify("!!!") == "feature"
