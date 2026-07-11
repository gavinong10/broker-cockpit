# Capability: conversation-import

Turn a local Claude Code **conversation** into a structured, deployed artifact.
First consumer: `scripts/import_basket.py <session-id>` — extracts a trading
thesis from a session transcript, drafts a basket manifest via headless
`claude -p`, and pushes it to the deployed worker's
`POST /internal/baskets/import` over SSH.

The capability is generic: any future "turn a conversation into X" script
(rule-sets, research imports, watchlists) should reuse
`scripts/lib/conversation.py` instead of reinventing the pipeline.

## Pipeline

```
session-id
   │
   ▼
1. LOCATOR    find_transcript(session_id)
   │          globs ~/.claude/projects/*/<session-id>.jsonl (local-only files)
   ▼
2. EXTRACTOR  extract_text(path, max_chars=150_000)
   │          USER: lines (all kept — they carry intent) +
   │          ASSISTANT: text blocks (each truncated to first 1500 chars);
   │          tool_use/tool_result skipped; over budget → drop assistant
   │          blocks oldest-first, never user messages
   ▼
3. LLM        run_claude_json(prompt)
   │          headless `claude -p <prompt> --output-format json` on the
   │          operator's subscription; parses the CLI JSON envelope's
   │          "result" field and extracts the first balanced {...} object
   ▼
4. PUSH       ssh root@204.168.169.27 → docker compose exec -T worker →
              in-container python one-liner POSTs the payload to
              http://localhost:8000/internal/baskets/import with
              X-Internal-Token from the container env
```

## The manifest-schema pattern

Each importer embeds its target JSON schema **verbatim** in the prompt and
instructs the model to return ONLY one JSON object. The basket manifest schema
(mirrors the worker's `app/baskets.py` `create_basket` contract):

```json
{
  "slug": "kebab-case-identifier",
  "name": "Human-readable basket name",
  "thesis": "Core thesis in a few sentences",
  "source_ref": "<session-id>",
  "horizon": "e.g. '3-6 months' (or null)",
  "invalidation": "what kills the thesis (or null)",
  "legs": [
    {"symbol_or_underlying": "TICKER or OCC symbol", "sec_type": "OPT" | "STK", "qty": "optional decimal string"}
  ]
}
```

Prompt rules that make it reliable:

- legs = instruments the conversation **concluded** should be traded, not
  every ticker mentioned;
- underlyings only, unless specific contracts (strike/expiry) were named —
  then full OCC symbols;
- `source_ref` is overwritten by the script with the real session id after
  the LLM call (never trust the model with provenance);
- `--since` steers the model to the latest conclusions, ignoring superseded
  earlier drafts in long sessions.

## Worker endpoint contract

`POST /internal/baskets/import` (internal auth, `X-Internal-Token` header):

- request body: `{"manifest": <manifest>, "dry_run": <bool>}`
- `dry_run: true` — compute allocations, return them, write nothing;
- success: created basket + allocations (Decimals as strings);
- `400` with a conflict list if any leg over-allocates against quantities
  already claimed by other baskets;
- OPT legs match all option positions on the underlying (OCC prefix) unless a
  full OCC symbol is given; STK legs require an explicit `qty` slice.

On non-dry success the basket is visible at
`https://cockpit.gavinong.org/baskets/<slug>`.

## Writing a new importer (worked example: rules-set from a conversation)

A future `scripts/import_rules.py` that turns a strategy conversation into a
rules-set pushed to a (hypothetical) `/internal/rules/import` endpoint:

```python
#!/usr/bin/env python3
"""Import a trading rules-set from a Claude Code conversation."""
import argparse, base64, json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.conversation import extract_text, find_transcript, run_claude_json

SCHEMA = """{
  "slug": "kebab-case-id",
  "name": "Rules-set name",
  "rules": [
    {"trigger": "condition in plain english", "action": "ALERT" | "PROPOSE",
     "params": {"symbol": "TICKER", "threshold": "decimal string"}}
  ]
}"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session_id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    text = extract_text(find_transcript(args.session_id))
    prompt = (
        "Return ONLY one JSON object matching this schema:\n" + SCHEMA +
        "\nRules = ONLY the alerting/monitoring rules the conversation "
        "CONCLUDED should run. Transcript:\n\n" + text
    )
    ruleset = run_claude_json(prompt)
    print(json.dumps(ruleset, indent=2))
    if input("Push? [y/N] ").lower() != "y":
        return 1

    payload = json.dumps({"ruleset": ruleset, "dry_run": args.dry_run})
    b64 = base64.b64encode(payload.encode()).decode()
    remote_py = (
        "import base64,json,os,urllib.request;"
        f"p=base64.b64decode('{b64}');"
        "r=urllib.request.Request('http://localhost:8000/internal/rules/import',"
        "data=p,headers={'Content-Type':'application/json',"
        "'X-Internal-Token':os.environ['INTERNAL_API_TOKEN']},method='POST');"
        "print(urllib.request.urlopen(r,timeout=60).read().decode())"
    )
    cmd = ("cd /root/broker-cockpit && docker compose -f compose.yml "
           f"-f compose.prod.yml exec -T worker uv run python -c \"{remote_py}\"")
    subprocess.run(["ssh", "root@204.168.169.27", cmd], check=True)

if __name__ == "__main__":
    sys.exit(main())
```

The only importer-specific parts are the schema, the prompt rules, and the
endpoint — locator, extractor, LLM invocation, and the base64-over-SSH push
pattern all come from the library.

## Operational notes

- **Transcripts are local to this Mac** under
  `~/.claude/projects/<project-slug>/<session-id>.jsonl` (e.g.
  `~/.claude/projects/-private-tmp/5a6b9ddd-490e-4ae6-91c7-74db07e4140f.jsonl`).
  The pipeline cannot run on the VPS.
- **`claude -p` runs on the operator's Claude subscription.** The manual
  invocation of the importer **is** the per-action consent required by this
  project's standing rules (one LLM call + one SSH push per run). Scripts must
  never call `claude -p` or ssh from cron/automation without a separate,
  explicit approval.
- **Always `--dry-run` first.** It computes and prints allocations (or
  conflicts) on the worker without writing anything; only after reviewing the
  manifest and the allocation preview run the real import.
- **The internal token never exists on the Mac.** The push base64-encodes the
  payload into a python one-liner executed inside the worker container, which
  reads `INTERNAL_API_TOKEN` from its own environment.
- The VPS uses the `docker compose` plugin (space); this Mac's colima setup
  only has `docker-compose` (hyphen) — the SSH command targets the VPS form.
- Tests: `python3 -m unittest discover scripts/tests` — pure stdlib, no live
  `claude`/ssh calls (subprocess is mocked).

## Plan block (pending structures)

The manifest may include an optional top-level `plan` object — structures the
conversation decided to buy but has not bought yet, with specific contracts
and a planned entry cost:

```json
"plan": {
  "legs": [
    {
      "label": "NBIS Dec-28 220/330",
      "structure": [
        {"occ": "NBIS281215C00220000", "sec_type": "OPT", "ratio": 1},
        {"occ": "NBIS281215C00330000", "sec_type": "OPT", "ratio": -1}
      ],
      "qty": "1",
      "planned_net_debit": "17.23",
      "tolerance_pct": "5",
      "thesis_note": "short strike at street-high zone"
    }
  ]
}
```

`import_basket.py` splits this off and, after the basket import succeeds,
POSTs it to `/internal/baskets/{slug}/plan`. Plan legs are *monitored intent*:
the worker grades each pending structure against live quotes on the sync
cadence (in_window / drifted / thesis_stale / unquotable), Discord-alerts on
status transitions, records mark history, and graduates legs to held when the
synced positions cover their contracts (see the basket page's Plan section and
docs/superpowers/plans/2026-07-11-basket-plan-monitor.md). Plans never place
orders. Dry runs preview the plan block without pushing it.
