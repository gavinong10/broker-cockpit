# Feature-factory safety requirements (owner-approved 2026-07-11)

Binding requirements for the prompt-to-feature factory (owner tab → Claude
implements in a git worktree → Preview / Accept / Revert). Any factory
implementation MUST satisfy every item below before it ships; reviewers should
treat violations as blocking. Approved verbatim by the owner.

1. **Read-only-to-the-world by credential ABSENCE, not instructions.** The
   feature agent runs with no access to `.env`, `secrets/`, the Discord
   webhook, GCS keys, the RH session pickle, or the docker socket. Worktree
   mounted only; network egress limited to GitHub + Anthropic. An instructed
   agent can be confused or injected; an uncredentialed one cannot write to
   what it cannot reach.
2. **Preview never touches prod state.** Preview runs the worktree's stack on
   ephemeral ports against a THROWAWAY clone of the database (never the live
   one), behind the same Google auth. Feature-branch migrations rehearse on
   the clone; they run on prod only via Accept, after being seen to work.
3. **Main-branch discipline enforced in code** (GitHub deploy keys cannot be
   branch-scoped): feature agents push only `factory/*` branches; only the
   Accept handler constructs a push to `main`, merging the exact reviewed SHA.
   Revert is `git revert` (history-preserving) — never reset, never
   force-push. A GitHub ruleset on `main` blocks force-push/deletion (this
   does not interfere with Accept).
4. **Protected-paths tripwire.** Any diff touching `auth.ts`, `roles.ts`,
   `internal_auth.py`, `compose*.yml`, `Caddyfile`, `.gitignore`,
   `Dockerfile*`, or anything under `secrets/` / `infra/` renders a loud
   warning on the Preview screen and requires a second explicit owner
   confirmation before Accept.
5. **Bounded runs.** Single-flight (one feature at a time), wall-clock cap,
   token budget cap, diff-size cap. Every run audited in `audit_log`: prompt,
   model, branch, start/end SHAs, outcome.
6. **The Accept screen shows the diff itself,** not just the running preview —
   the owner is the review gate; the review material must be unavoidable.
7. **Kill switch:** one owner toggle pauses the factory and terminates any
   running agent.
8. **Model default:** Fable, overridable per-prompt. Dangerous/permissive mode
   applies only inside the sandbox described in (1).
