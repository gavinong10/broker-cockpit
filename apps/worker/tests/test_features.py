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


# --- async build start: 202 immediately, single-flight 409 ------------------


STATUS_BUILT = ("STATUS built\nDIFFSTAT 1 file changed\n"
                "RISKY_BEGIN\nRISKY_END\nREPORT_BEGIN\ndone\nREPORT_END\n")


def test_build_endpoint_returns_immediately_and_refuses_concurrent(monkeypatch):
    import threading
    import time

    from app import features_api

    features._build_threads.clear()
    release = threading.Event()
    build_started = threading.Event()

    def fake_ssh(args, **kw):
        if args[0] == "build":
            build_started.set()
            assert release.wait(timeout=10), "test never released the build"
            return "built\n"
        if args[0] == "status":
            return STATUS_BUILT
        return "OK\n"

    monkeypatch.setattr(features, "_ssh", fake_ssh)
    monkeypatch.setattr(features, "_set", lambda *a, **kw: None)
    monkeypatch.setattr(features, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(features, "create_feature",
                        lambda eng, p, m, a: {"slug": "my-widget", "model": "claude-fable-5",
                                              "status": "created"})
    monkeypatch.setattr(features_api, "_engine", lambda: None)

    c = _client()
    t0 = time.monotonic()
    r = c.post("/internal/features", headers=TOKEN,
               json={"prompt": "add a widget to the dashboard please", "actor": "o@x"})
    elapsed = time.monotonic() - t0
    assert r.status_code == 202
    assert r.json()["status"] == "building"
    assert elapsed < 2, "build start must not wait for the build"
    assert build_started.wait(timeout=5)

    # Second build while the first thread is alive: refused, no create attempted.
    def no_create(*a, **kw):
        raise AssertionError("create_feature must not run while a build is in flight")
    monkeypatch.setattr(features, "create_feature", no_create)
    r2 = c.post("/internal/features", headers=TOKEN,
                json={"prompt": "add another widget somewhere else please", "actor": "o@x"})
    assert r2.status_code == 409
    assert "already in progress" in r2.json()["error"]

    release.set()
    for t in list(features._build_threads.values()):
        t.join(timeout=10)
    assert features.building_slug() is None


def test_start_build_registry_single_flight(monkeypatch):
    import threading

    features._build_threads.clear()
    release = threading.Event()
    monkeypatch.setattr(features, "_set", lambda *a, **kw: None)
    monkeypatch.setattr(features, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(features, "_ssh",
                        lambda args, **kw: (release.wait(10) and "") or
                        (STATUS_BUILT if args[0] == "status" else "built\n"))

    features.start_build(None, "slug-a", "claude-fable-5", "o@x")
    assert features.building_slug() == "slug-a"
    try:
        features.start_build(None, "slug-b", "claude-fable-5", "o@x")
        raise AssertionError("expected BuildInProgress")
    except features.BuildInProgress:
        pass
    release.set()
    features._build_threads["slug-a"].join(timeout=10)
    assert features.building_slug() is None


# --- reconciliation: adopt host outcomes for reconcilable rows only ---------


STATUS_ACCEPTED = ("STATUS accepted\nDIFFSTAT 1 file changed\nMERGE deadbeef1234\n"
                   "RISKY_BEGIN\nRISKY_END\nREPORT_BEGIN\ndone\nREPORT_END\n")


def test_reconcile_refreshes_only_reconcilable_rows(monkeypatch):
    ssh_calls, set_calls = [], []

    def fake_ssh(args, **kw):
        ssh_calls.append(args)
        return STATUS_BUILT

    monkeypatch.setattr(features, "_ssh", fake_ssh)
    monkeypatch.setattr(features, "_set",
                        lambda eng, slug, **cols: set_calls.append((slug, cols)))
    rows = [
        {"slug": "running", "status": "building"},
        {"slug": "done", "status": "built"},        # reconciled: accept may have landed host-side
        {"slug": "dead", "status": "failed"},
        {"slug": "fresh", "status": "created"},
        {"slug": "live", "status": "accepted"},
        {"slug": "gone", "status": "discarded"},
    ]
    assert features.reconcile_features(None, rows) is True
    # Only building/created/built rows cost an SSH round-trip.
    assert ssh_calls == [["status", "running"], ["status", "done"], ["status", "fresh"]]
    # And all adopt the host's authoritative status.
    assert [(s, c["status"]) for s, c in set_calls] == [
        ("running", "built"), ("done", "built"), ("fresh", "built")]


def test_reconcile_built_row_adopts_host_accept_with_merge_sha(monkeypatch):
    # An accept whose response the worker never received (its container was
    # rebuilt): the host row says accepted + MERGE sha; the DB adopts both.
    set_calls = []
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: STATUS_ACCEPTED)
    monkeypatch.setattr(features, "_set",
                        lambda eng, slug, **cols: set_calls.append((slug, cols)))
    assert features.reconcile_features(None, [{"slug": "game", "status": "built"}]) is True
    assert set_calls[0][0] == "game"
    assert set_calls[0][1]["status"] == "accepted"
    assert set_calls[0][1]["merge_sha"] == "deadbeef1234"


def test_reconcile_terminal_rows_make_no_ssh_calls(monkeypatch):
    def boom(args, **kw):
        raise AssertionError("terminal rows must not be re-fetched")
    monkeypatch.setattr(features, "_ssh", boom)
    rows = [{"slug": "a", "status": "accepted"}, {"slug": "b", "status": "reverted"},
            {"slug": "c", "status": "failed_blocked_paths"}, {"slug": "d", "status": "killed"},
            {"slug": "e", "status": "failed"}, {"slug": "f", "status": "discarded"}]
    assert features.reconcile_features(None, rows) is False


def test_reconcile_survives_unreachable_host(monkeypatch):
    def down(args, **kw):
        raise features.RunnerError("host unreachable")
    monkeypatch.setattr(features, "_ssh", down)
    # No refresh, no exception: the DB's view stands until the next poll.
    assert features.reconcile_features(None, [{"slug": "a", "status": "building"}]) is False


# --- accept: runner echoes outcome BEFORE its detached rebuild --------------


def test_accept_parses_merge_sha_from_reordered_output(monkeypatch):
    # New runner order: (optional WARN) -> MERGE sha -> detached rebuild.
    set_calls, audits = [], []
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: "MERGE abc123def456\n")
    monkeypatch.setattr(features, "_set",
                        lambda eng, slug, **cols: set_calls.append((slug, cols)))
    monkeypatch.setattr(features, "_audit",
                        lambda eng, actor, cat, payload: audits.append((cat, payload)))
    sha = features.accept_feature(None, "my-slug", "owner@x")
    assert sha == "abc123def456"
    assert set_calls == [("my-slug", {"status": "accepted", "merge_sha": "abc123def456"})]
    assert audits[0][1]["push_failed"] is False


def test_accept_surfaces_push_warning_in_audit(monkeypatch):
    audits = []
    monkeypatch.setattr(
        features, "_ssh",
        lambda args, **kw: "WARN push to origin failed — merge is local to the VPS; push manually\n"
                           "MERGE abc123def456\n")
    monkeypatch.setattr(features, "_set", lambda *a, **kw: None)
    monkeypatch.setattr(features, "_audit",
                        lambda eng, actor, cat, payload: audits.append((cat, payload)))
    assert features.accept_feature(None, "my-slug", "owner@x") == "abc123def456"
    assert audits[0][1]["push_failed"] is True


def test_sync_feature_parses_merge_line(monkeypatch):
    set_calls = []
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: STATUS_ACCEPTED)
    monkeypatch.setattr(features, "_set",
                        lambda eng, slug, **cols: set_calls.append((slug, cols)))
    out = features.sync_feature(None, "game")
    assert out["status"] == "accepted" and out["merge_sha"] == "deadbeef1234"
    assert set_calls[0][1]["merge_sha"] == "deadbeef1234"


def test_sync_feature_without_merge_line_leaves_merge_sha_untouched(monkeypatch):
    set_calls = []
    monkeypatch.setattr(features, "_ssh", lambda args, **kw: STATUS_BUILT)
    monkeypatch.setattr(features, "_set",
                        lambda eng, slug, **cols: set_calls.append((slug, cols)))
    features.sync_feature(None, "game")
    assert "merge_sha" not in set_calls[0][1]
