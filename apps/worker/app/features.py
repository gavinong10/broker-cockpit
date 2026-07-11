"""Feature-factory orchestration: worker-side client for the host runner.

SECURITY: the worker talks to the host ONLY through an SSH key whose
authorized_keys entry forces scripts/feature_runner.sh as the command —
this module cannot execute arbitrary host commands even if compromised.
The builder itself runs on the host as the unprivileged `factory` user with
a scrubbed environment; see the runner script and
docs/capabilities/feature-factory.md.
"""
import json
import re
import subprocess
import threading

from sqlalchemy import text
from sqlalchemy.engine import Engine

SSH_KEY = "/secrets/feature_runner_key"
SSH_DEST = "root@host-gateway"
MODELS = {
    "fable": "claude-fable-5",
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-5",
}
DEFAULT_MODEL = "claude-fable-5"

_build_lock = threading.Lock()  # single-flight: one build at a time

# Async build registry: slug -> daemon thread running build_feature_blocking.
# The HTTP request that starts a build returns immediately; the UI polls the
# feature list, which reconciles non-terminal rows from the host (see
# reconcile_features). Module-level on purpose: one worker process, one registry.
_build_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()

# Statuses that mean "the host may know more than the DB row does".
NON_TERMINAL = frozenset({"created", "building"})
# Reconciled on every list poll: NON_TERMINAL plus 'built' — accept/revert
# outcomes land host-side first (the accept rebuild kills this very worker),
# so a 'built' row can be 'accepted' on the host without the DB hearing back.
RECONCILE = NON_TERMINAL | {"built"}


class RunnerError(Exception):
    pass


class BuildInProgress(RunnerError):
    pass


def _ssh(args: list[str], stdin: str | None = None, timeout: int = 120) -> str:
    cmd = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=accept-new",
           "-o", "BatchMode=yes", SSH_DEST, " ".join(args)]
    try:
        res = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"runner timeout after {timeout}s") from exc
    if res.returncode != 0:
        raise RunnerError((res.stderr or res.stdout or "runner failed").strip()[:500])
    return res.stdout


def runner_status() -> dict:
    """Ping the host runner. Line 1: configured|unconfigured; line 2: paused|active."""
    try:
        lines = _ssh(["ping"], timeout=20).strip().splitlines()
    except RunnerError:
        return {"configured": False, "paused": False}
    return {
        "configured": bool(lines) and lines[0].strip() == "configured",
        "paused": len(lines) > 1 and lines[1].strip() == "paused",
    }


def set_paused(engine: Engine | None, paused: bool, actor: str) -> dict:
    """Kill switch (safety spec item 7): pause blocks create/build host-side."""
    _ssh(["pause" if paused else "resume"], timeout=30)
    _audit(engine, actor, "feature.factory_paused" if paused else "feature.factory_resumed", {})
    return {"paused": paused}


def kill_feature(engine: Engine, slug: str, actor: str) -> dict:
    """Terminate a running build (signals the build's process group on the host)."""
    _ssh(["kill", slug], timeout=60)
    _audit(engine, actor, "feature.killed", {"slug": slug})
    return sync_feature(engine, slug)


def resolve_model(prompt: str, requested: str | None) -> str:
    """UI choice wins; a leading 'model: xxx' line in the prompt overrides default."""
    if requested and requested in MODELS:
        return MODELS[requested]
    m = re.match(r"^\s*model:\s*([a-z0-9.-]+)", prompt, re.IGNORECASE)
    if m:
        alias = m.group(1).lower()
        return MODELS.get(alias, alias if alias.startswith("claude-") else DEFAULT_MODEL)
    return DEFAULT_MODEL


def slugify(prompt: str) -> str:
    words = re.findall(r"[a-z0-9]+", prompt.lower())[:5]
    return "-".join(words)[:40] or "feature"


def _set(engine: Engine, slug: str, **cols) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE features SET {sets}, updated_at = now() WHERE slug = :slug"),
                     {**cols, "slug": slug})


def _audit(engine: Engine, actor: str, category: str, payload: dict) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) VALUES (:a, :c, CAST(:p AS jsonb))"),
                {"a": actor, "c": category, "p": json.dumps(payload)})
    except Exception:
        pass


def create_feature(engine: Engine, prompt: str, requested_model: str | None, actor: str) -> dict:
    slug = slugify(prompt)
    model = resolve_model(prompt, requested_model)
    with engine.begin() as conn:
        dup = conn.execute(text("SELECT 1 FROM features WHERE slug = :s"), {"s": slug}).first()
        if dup:
            raise RunnerError(f"a feature named '{slug}' already exists — reword the prompt")
        conn.execute(text(
            "INSERT INTO features (slug, prompt, model, status) VALUES (:s, :p, :m, 'created')"),
            {"s": slug, "p": prompt, "m": model})
    _ssh(["create", slug], stdin=prompt, timeout=60)
    _audit(engine, actor, "feature.created", {"slug": slug, "model": model})
    return {"slug": slug, "model": model, "status": "created"}


def build_feature_blocking(engine: Engine, slug: str, model: str) -> str:
    """Long-running (multi-minute). Never call from a request path — use
    start_build, which runs this in a daemon thread. Persists its own outcome:
    'building' on entry, the host's authoritative status (via sync_feature) on
    completion, 'failed' + error report if the runner call itself errors."""
    if not _build_lock.acquire(blocking=False):
        raise BuildInProgress("another feature build is in progress")
    try:
        _set(engine, slug, status="building")
        out = _ssh(["build", slug, model], timeout=2000).strip().splitlines()
        status = out[-1] if out else "failed"
        try:
            sync_feature(engine, slug)
        except RunnerError:
            # Build finished (we have its stdout status); don't mark it failed
            # just because the follow-up status fetch hiccuped.
            _set(engine, slug, status=status)
        return status
    except RunnerError as exc:
        _set(engine, slug, status="failed", report=str(exc))
        raise
    finally:
        _build_lock.release()


def building_slug() -> str | None:
    """Slug of the build currently running in this process, if any.
    Prunes finished threads from the registry as a side effect."""
    with _threads_lock:
        for slug, thread in list(_build_threads.items()):
            if thread.is_alive():
                return slug
            del _build_threads[slug]
    return None


def start_build(engine: Engine, slug: str, model: str, actor: str) -> None:
    """Spawn build_feature_blocking in a daemon thread and return immediately.
    Raises BuildInProgress if any build thread is still alive (single-flight)."""
    with _threads_lock:
        for s, thread in list(_build_threads.items()):
            if thread.is_alive():
                raise BuildInProgress(f"a build for '{s}' is already in progress — wait for it to finish")
            del _build_threads[s]
        thread = threading.Thread(
            target=_run_build_thread, args=(engine, slug, model, actor),
            name=f"feature-build-{slug}", daemon=True)
        _build_threads[slug] = thread
        thread.start()


def _run_build_thread(engine: Engine, slug: str, model: str, actor: str) -> None:
    try:
        status = build_feature_blocking(engine, slug, model)
        _audit(engine, actor, "feature.build_finished", {"slug": slug, "status": status})
    except Exception as exc:  # outcome already persisted by build_feature_blocking
        _audit(engine, actor, "feature.build_failed", {"slug": slug, "error": str(exc)[:300]})


def reconcile_features(engine: Engine, rows: list[dict]) -> bool:
    """Adopt host-side outcomes for rows stuck in a non-terminal state — e.g. a
    build whose starting request died but whose host process ran to completion.
    Also covers 'built' rows, whose accept/revert outcome is written host-side
    before the deploy rebuild kills this worker. Only RECONCILE-state rows cost
    an SSH round-trip. Returns True if any row was refreshed (caller should
    re-read the DB)."""
    refreshed = False
    for row in rows:
        if row["status"] not in RECONCILE:
            continue
        try:
            sync_feature(engine, row["slug"])
            refreshed = True
        except RunnerError:
            pass  # host unreachable: keep the DB's view, try again next poll
    return refreshed


def sync_feature(engine: Engine, slug: str) -> dict:
    """Pull authoritative state from the host into the DB row."""
    raw = _ssh(["status", slug], timeout=60)
    status = "unknown"
    diffstat = ""
    merge_sha = None
    risky: list[str] = []
    report_lines: list[str] = []
    mode = None
    for line in raw.splitlines():
        if line.startswith("STATUS "):
            status = line[7:].strip()
        elif line.startswith("DIFFSTAT "):
            diffstat = line[9:].strip()
        elif line.startswith("MERGE "):
            merge_sha = line[6:].strip()
        elif line == "RISKY_BEGIN":
            mode = "risky"
        elif line == "RISKY_END":
            mode = None
        elif line == "REPORT_BEGIN":
            mode = "report"
        elif line == "REPORT_END":
            mode = None
        elif mode == "risky" and line.strip():
            risky.append(line.strip())
        elif mode == "report":
            report_lines.append(line)
    cols = dict(status=status, diff_stat=diffstat,
                risky_paths=json.dumps(risky), report="\n".join(report_lines).strip())
    if merge_sha:  # adopt an accept that this worker never heard back about
        cols["merge_sha"] = merge_sha
    _set(engine, slug, **cols)
    return {"status": status, "diffstat": diffstat, "risky": risky, "merge_sha": merge_sha}


def feature_diff(slug: str) -> str:
    return _ssh(["diff", slug], timeout=60)


def accept_feature(engine: Engine, slug: str, actor: str) -> str:
    """The runner echoes MERGE <sha> BEFORE kicking off its detached rebuild,
    so this persists the outcome before that rebuild can kill this worker."""
    out = _ssh(["accept", slug], timeout=900)
    m = re.search(r"MERGE ([0-9a-f]+)", out)
    merge_sha = m.group(1) if m else None
    push_warned = "WARN push" in out
    _set(engine, slug, status="accepted", merge_sha=merge_sha)
    _audit(engine, actor, "feature.accepted",
           {"slug": slug, "merge_sha": merge_sha, "push_failed": push_warned})
    return merge_sha or ""


def revert_feature(engine: Engine, slug: str, merge_sha: str, actor: str) -> None:
    _ssh(["revert", slug, merge_sha], timeout=900)
    _set(engine, slug, status="reverted")
    _audit(engine, actor, "feature.reverted", {"slug": slug, "merge_sha": merge_sha})


def discard_feature(engine: Engine, slug: str, actor: str) -> None:
    _ssh(["discard", slug], timeout=120)
    _set(engine, slug, status="discarded")
    _audit(engine, actor, "feature.discarded", {"slug": slug})
