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


class RunnerError(Exception):
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
    """Long-running; call via asyncio.to_thread. Single-flight."""
    if not _build_lock.acquire(blocking=False):
        raise RunnerError("another feature build is in progress")
    try:
        _set(engine, slug, status="building")
        out = _ssh(["build", slug, model], timeout=2000).strip().splitlines()
        status = out[-1] if out else "failed"
        sync_feature(engine, slug)
        return status
    except RunnerError as exc:
        _set(engine, slug, status="failed", report=str(exc))
        raise
    finally:
        _build_lock.release()


def sync_feature(engine: Engine, slug: str) -> dict:
    """Pull authoritative state from the host into the DB row."""
    raw = _ssh(["status", slug], timeout=60)
    status = "unknown"
    diffstat = ""
    risky: list[str] = []
    report_lines: list[str] = []
    mode = None
    for line in raw.splitlines():
        if line.startswith("STATUS "):
            status = line[7:].strip()
        elif line.startswith("DIFFSTAT "):
            diffstat = line[9:].strip()
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
    _set(engine, slug, status=status, diff_stat=diffstat,
         risky_paths=json.dumps(risky), report="\n".join(report_lines).strip())
    return {"status": status, "diffstat": diffstat, "risky": risky}


def feature_diff(slug: str) -> str:
    return _ssh(["diff", slug], timeout=60)


def accept_feature(engine: Engine, slug: str, actor: str) -> str:
    out = _ssh(["accept", slug], timeout=900)
    m = re.search(r"MERGE ([0-9a-f]+)", out)
    merge_sha = m.group(1) if m else None
    _set(engine, slug, status="accepted", merge_sha=merge_sha)
    _audit(engine, actor, "feature.accepted", {"slug": slug, "merge_sha": merge_sha})
    return merge_sha or ""


def revert_feature(engine: Engine, slug: str, merge_sha: str, actor: str) -> None:
    _ssh(["revert", slug, merge_sha], timeout=900)
    _set(engine, slug, status="reverted")
    _audit(engine, actor, "feature.reverted", {"slug": slug, "merge_sha": merge_sha})


def discard_feature(engine: Engine, slug: str, actor: str) -> None:
    _ssh(["discard", slug], timeout=120)
    _set(engine, slug, status="discarded")
    _audit(engine, actor, "feature.discarded", {"slug": slug})
