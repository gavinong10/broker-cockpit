#!/usr/bin/env python3
"""Turn a Claude Code session ID into a deployed basket.

Usage:
    python3 scripts/import_basket.py <session-id> [--since YYYY-MM-DD] [--dry-run] [--yes]

Pipeline (docs/capabilities/conversation-import.md):
  1. Locate the local transcript under ~/.claude/projects/*/<session-id>.jsonl
  2. Extract USER/ASSISTANT text (tool noise stripped)
  3. Draft a basket manifest via headless `claude -p` (strict JSON schema)
  4. Show it, confirm, push to the deployed worker's /internal/baskets/import
     over SSH — the internal token never leaves the VPS container.

Stdlib only. Running this script manually IS the per-action consent for its
one `claude -p` call and its SSH push (project standing rules).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.conversation import extract_text, find_transcript, run_claude_json  # noqa: E402

VPS = "root@204.168.169.27"
REMOTE_REPO = "/root/broker-cockpit"
IMPORT_URL = "http://localhost:8000/internal/baskets/import"
BASKET_URL_BASE = "https://cockpit.gavinong.org/baskets"

# EXACT manifest schema from the plan's Task A (app/baskets.py create_basket).
MANIFEST_SCHEMA = """{
  "slug": "kebab-case-identifier",
  "name": "Human-readable basket name",
  "thesis": "The core thesis of the trade campaign, in a few sentences",
  "source_ref": "<session-id>",
  "horizon": "time horizon, e.g. '3-6 months' (or null)",
  "invalidation": "what would invalidate the thesis (or null)",
  "legs": [
    {
      "symbol_or_underlying": "TICKER or full OCC option symbol",
      "sec_type": "OPT" | "STK",
      "qty": "optional decimal string; STK legs REQUIRE qty, OPT legs may omit it"
    }
  ]
}"""

PROMPT_TEMPLATE = """You are drafting a trade-basket manifest from a Claude Code conversation \
transcript between an operator and an assistant. Return ONLY one JSON object matching \
exactly this schema (no extra keys, no commentary outside the JSON):

{schema}

Rules:
- legs = ONLY the instruments the conversation CONCLUDED should actually be traded \
(final decisions, not every ticker mentioned or considered-and-rejected).
- Use underlyings (plain tickers) unless the conversation named specific option \
contracts (specific strike/expiry), in which case use the full OCC symbol.
- sec_type is "OPT" for options legs, "STK" for stock legs.
- slug must be kebab-case, short, derived from the thesis.
- source_ref must be exactly: {session_id}
- Focus on conclusions reached on or after {since} (ignore stale earlier drafts \
that the conversation later superseded).

Transcript follows:

{transcript}
"""

# Remote helper executed in-container: decodes the base64 payload and POSTs it
# to the import endpoint with the token from the container's own env.
REMOTE_PY = """
import base64, json, os, sys, urllib.request
payload = base64.b64decode("{b64}").decode("utf-8")
req = urllib.request.Request(
    "{url}",
    data=payload.encode("utf-8"),
    headers={{
        "Content-Type": "application/json",
        "X-Internal-Token": os.environ["INTERNAL_API_TOKEN"],
    }},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        print(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(e.read().decode("utf-8"), file=sys.stderr)
    sys.exit(1)
"""


def push_manifest(manifest: dict, dry_run: bool) -> tuple[int, str, str]:
    """POST the manifest to the deployed worker via SSH.

    The payload is base64-encoded into the remote python one-liner so no shell
    quoting of JSON is needed; the internal token is read from the worker
    container's env and never exists on this Mac.
    """
    payload = json.dumps({"manifest": manifest, "dry_run": dry_run})
    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    remote_py = REMOTE_PY.format(b64=b64, url=IMPORT_URL)
    remote_cmd = (
        f"cd {REMOTE_REPO} && "
        f"docker compose -f compose.yml -f compose.prod.yml "
        f"exec -T worker uv run python -c {shell_quote(remote_py)}"
    )
    proc = subprocess.run(
        ["ssh", VPS, remote_cmd], capture_output=True, text=True, timeout=120
    )
    return proc.returncode, proc.stdout, proc.stderr


def shell_quote(s: str) -> str:
    """Single-quote a string for the remote POSIX shell."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import a basket from a Claude Code conversation transcript."
    )
    parser.add_argument("session_id", help="Claude Code session ID (transcript must exist locally)")
    parser.add_argument(
        "--since",
        default=(dt.date.today() - dt.timedelta(days=7)).isoformat(),
        metavar="YYYY-MM-DD",
        help="focus the LLM on conclusions on/after this date (default: 7 days ago)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and show allocations on the worker without writing anything",
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the manifest confirmation prompt"
    )
    args = parser.parse_args()

    try:
        dt.date.fromisoformat(args.since)
    except ValueError:
        parser.error(f"--since must be YYYY-MM-DD, got {args.since!r}")

    path = find_transcript(args.session_id)
    print(f"Transcript: {path}")
    transcript = extract_text(path)
    print(f"Extracted {len(transcript)} chars of conversation text.")

    prompt = PROMPT_TEMPLATE.format(
        schema=MANIFEST_SCHEMA,
        session_id=args.session_id,
        since=args.since,
        transcript=transcript,
    )
    print("Drafting manifest via headless `claude -p` (this run is your consent)...")
    manifest = run_claude_json(prompt)
    manifest["source_ref"] = args.session_id  # never trust the model with the ref

    print("\n=== Basket manifest ===")
    print(json.dumps(manifest, indent=2))
    print("=======================\n")

    if not args.yes:
        answer = input(
            f"Push to worker ({'DRY RUN' if args.dry_run else 'LIVE import'})? [y/N] "
        ).strip().lower()
        if answer != "y":
            print("Aborted; nothing pushed.")
            return 1

    code, out, err = push_manifest(manifest, dry_run=args.dry_run)
    parsed = None
    if out.strip():
        try:
            parsed = json.loads(out)
            print(json.dumps(parsed, indent=2))
        except json.JSONDecodeError:
            print(out)
    if code != 0:
        # In a dry run, matching conflicts (e.g. no_matching_position before the
        # trades have synced) are the expected preview output, not a failure.
        conflicts = isinstance(parsed, dict) and (
            parsed.get("detail", {}).get("error") == "over_allocation"
            if isinstance(parsed.get("detail"), dict) else False
        )
        if args.dry_run and conflicts:
            print("Dry run complete — matching preview above (no positions matched "
                  "yet is expected before the trades sync). Nothing written.")
            return 0
        print(f"Import failed (ssh/remote exit {code}).", file=sys.stderr)
        if err.strip():
            print(err.strip()[:2000], file=sys.stderr)
        return code

    if args.dry_run:
        print("Dry run complete — nothing written.")
    else:
        print(f"Basket live: {BASKET_URL_BASE}/{manifest['slug']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
