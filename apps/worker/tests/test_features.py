"""Unit tests for feature-factory logic that must never regress: model
resolution, slugging, the kill switch, and the accept/revert branch rules.
The SSH runner is mocked — no host interaction, no builds."""
from fastapi.testclient import TestClient

from app import features

TOKEN = {"X-Internal-Token": "dev-token"}


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


# --- kill switch (safety spec item 7): pause state + verbs -----------------


def test_runner_status_configured_and_paused(monkeypatch):
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: "configured\npaused\n")
    assert features.runner_status() == {"configured": True, "paused": True}


def test_runner_status_configured_active(monkeypatch):
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: "configured\nactive\n")
    assert features.runner_status() == {"configured": True, "paused": False}


def test_runner_status_legacy_single_line(monkeypatch):
    # An old runner without the pause line must still parse as unpaused.
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: "configured\n")
    assert features.runner_status() == {"configured": True, "paused": False}


def test_runner_status_unreachable_is_unconfigured(monkeypatch):
    def boom(args, **kw):
        raise features.RunnerError("no route")
    monkeypatch.setattr(features, "_ssh", boom)
    assert features.runner_status() == {"configured": False, "paused": False}


def test_set_paused_sends_pause_and_resume_verbs(monkeypatch):
    calls = []
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: calls.append(args) or "OK\n")
    monkeypatch.setattr(features, "_audit", lambda *a, **kw: None)
    assert features.set_paused(None, True, "owner@x") == {"paused": True}
    assert features.set_paused(None, False, "owner@x") == {"paused": False}
    assert calls == [["pause"], ["resume"]]


def test_kill_feature_sends_kill_then_syncs(monkeypatch):
    calls = []
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: calls.append(args) or "OK\n")
    monkeypatch.setattr(features, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(features, "sync_feature", lambda eng, slug: {"status": "killed"})
    assert features.kill_feature(None, "my-slug", "owner@x") == {"status": "killed"}
    assert calls == [["kill", "my-slug"]]


# --- API surfacing of the pause state / toggle -----------------------------


def _client():
    from app.main import app
    return TestClient(app)


def test_runner_endpoint_surfaces_paused(monkeypatch):
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: "configured\npaused\n")
    r = _client().get("/internal/features/runner", headers=TOKEN)
    assert r.status_code == 200
    assert r.json() == {"configured": True, "paused": True}


def test_pause_and_resume_endpoints(monkeypatch):
    calls = []
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: calls.append(args) or "OK\n")
    monkeypatch.setattr(features, "_audit", lambda *a, **kw: None)
    c = _client()
    r = c.post("/internal/features/runner/pause", headers=TOKEN, json={"actor": "owner@x"})
    assert r.status_code == 200 and r.json() == {"paused": True}
    r = c.post("/internal/features/runner/resume", headers=TOKEN, json={"actor": "owner@x"})
    assert r.status_code == 200 and r.json() == {"paused": False}
    assert calls == [["pause"], ["resume"]]


def test_pause_endpoint_surfaces_runner_error(monkeypatch):
    def boom(args, **kw):
        raise features.RunnerError("host unreachable")
    monkeypatch.setattr(features, "_ssh", boom)
    r = _client().post("/internal/features/runner/pause", headers=TOKEN, json={"actor": "owner@x"})
    assert r.status_code == 502
    assert "host unreachable" in r.json()["error"]


def test_pause_endpoint_requires_internal_token(monkeypatch):
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: "OK\n")
    r = _client().post("/internal/features/runner/pause", json={"actor": "owner@x"})
    assert r.status_code == 401
