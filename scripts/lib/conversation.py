"""Reusable helpers for turning local Claude Code transcripts into structured data.

Stdlib only — runs with plain `python3` on the Mac, no venv/pyproject.

Pipeline pieces (see docs/capabilities/conversation-import.md):
  find_transcript(session_id)  -> Path to the local JSONL transcript
  extract_text(path)           -> compact USER/ASSISTANT text of the conversation
  run_claude_json(prompt)      -> dict parsed from a headless `claude -p` call
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Cap on argv prompt size; beyond this we pipe the prompt via stdin
# (`claude -p --output-format json` reads the prompt from stdin when the
# positional prompt argument is omitted).
_ARGV_PROMPT_LIMIT = 100_000

_ASSISTANT_BLOCK_HEAD = 1500  # chars kept per assistant text block


def find_transcript(session_id: str) -> Path:
    """Locate `<session_id>.jsonl` under ~/.claude/projects/*/.

    Transcripts only exist on the machine where the session ran. Raises
    FileNotFoundError listing the searched root if absent.
    """
    matches = sorted(PROJECTS_ROOT.glob(f"*/{session_id}.jsonl"))
    if not matches:
        raise FileNotFoundError(
            f"No transcript {session_id}.jsonl found under {PROJECTS_ROOT}/*/ "
            f"(searched root: {PROJECTS_ROOT}). Transcripts are local to the "
            f"machine where the session ran."
        )
    return matches[0]


def _iter_segments(path: Path):
    """Yield (kind, text) segments in chronological order.

    kind is "user" or "assistant". Tool_use / tool_result blocks are skipped
    entirely; user messages whose content is a list (tool results) are skipped;
    user strings starting with "<" (system-reminders, command wrappers) or with
    len <= 10 are skipped; each assistant text block is truncated to its first
    1500 chars.
    """
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = entry.get("type")
            message = entry.get("message") or {}
            content = message.get("content")
            if etype == "user":
                if (
                    isinstance(content, str)
                    and len(content) > 10
                    and not content.startswith("<")
                ):
                    yield "user", content
            elif etype == "assistant":
                if isinstance(content, list):
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and block.get("text")
                        ):
                            yield "assistant", block["text"][:_ASSISTANT_BLOCK_HEAD]


def extract_text(path: Path, max_chars: int = 150_000) -> str:
    """Extract the human-readable conversation from a transcript JSONL.

    Keeps every qualifying user message (prefixed "USER: ") and every assistant
    text block (prefixed "ASSISTANT: ", truncated to its first 1500 chars),
    chronologically, skipping tool calls/results.

    Budgeting: if the joined text exceeds `max_chars`, ALL user messages are
    kept and assistant blocks are dropped oldest-first until it fits — user
    messages carry the intent signal, and the newest assistant blocks carry the
    conversation's conclusions, so old assistant chatter is the cheapest cut.
    """
    segments = []  # (kind, rendered_text)
    for kind, text in _iter_segments(path):
        prefix = "USER: " if kind == "user" else "ASSISTANT: "
        segments.append((kind, prefix + text))

    sep = "\n\n"

    def total(segs):
        return sum(len(s) for _, s in segs) + max(0, len(segs) - 1) * len(sep)

    if total(segments) > max_chars:
        # Drop assistant segments oldest-first; never drop user segments.
        assistant_idxs = [i for i, (k, _) in enumerate(segments) if k == "assistant"]
        drop = set()
        for i in assistant_idxs:
            if total([s for j, s in enumerate(segments) if j not in drop]) <= max_chars:
                break
            drop.add(i)
        segments = [s for j, s in enumerate(segments) if j not in drop]

    return sep.join(text for _, text in segments)


def _extract_first_json_object(text: str) -> dict:
    """Return the first balanced {...} JSON object found in `text`."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    raise ValueError("no balanced JSON object found")


def run_claude_json(prompt: str, timeout_s: int = 300) -> dict:
    """Run headless `claude -p <prompt> --output-format json`, return the first
    JSON object embedded in its result text.

    Uses the operator's Claude subscription — callers must have per-action
    consent before invoking (see project standing rules). Prompts over 100KB
    are piped via stdin (claude -p reads stdin when the prompt arg is omitted).
    Raises RuntimeError with a stderr/result excerpt (truncated to 500 chars)
    on any failure.
    """

    def excerpt(s):
        return (s or "").strip()[:500]

    if len(prompt) <= _ARGV_PROMPT_LIMIT:
        argv = ["claude", "-p", prompt, "--output-format", "json"]
        stdin_input = None
    else:
        argv = ["claude", "-p", "--output-format", "json"]
        stdin_input = prompt

    try:
        proc = subprocess.run(
            argv,
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"claude -p timed out after {timeout_s}s") from exc
    except OSError as exc:
        raise RuntimeError(f"failed to launch claude CLI: {exc}") from exc

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude -p exited {proc.returncode}: {excerpt(proc.stderr or proc.stdout)}"
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude -p output was not JSON: {excerpt(proc.stdout)}"
        ) from exc

    result = envelope.get("result")
    if not isinstance(result, str):
        raise RuntimeError(
            f"claude -p envelope had no string 'result': {excerpt(proc.stdout)}"
        )

    try:
        return _extract_first_json_object(result)
    except ValueError as exc:
        raise RuntimeError(
            f"no JSON object in claude result: {excerpt(result)}"
        ) from exc
