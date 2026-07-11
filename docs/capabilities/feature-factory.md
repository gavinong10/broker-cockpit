# Capability: Feature Factory

Owner-only **Features** tab (`/features`): describe a change in one prompt and
Claude builds it in an isolated git worktree on the VPS — never on the live
branch. Each feature has **Preview** (view the diff), **Accept** (merge to main
+ redeploy), and **Revert/Discard**. Default model is **Fable**; override in the
UI dropdown or with a `model: opus|sonnet|<claude-id>` first line in the prompt.

## Why it is safe

The build runs with `--dangerously-skip-permissions`, so safety is enforced by
*construction*, not by the model's cooperation:

1. **Worktree isolation.** Each build happens in `.features/<slug>`, a git
   worktree on branch `feature/<slug>` cut from `main`. The live checkout and
   the running containers are never touched during a build.
2. **main is immutable except accept/revert.** The runner only ever writes to
   `main` via `git merge --no-ff` (accept) or `git revert -m 1` (revert). No
   build step, and no model action, can push or commit to main.
3. **Scrubbed build environment.** The builder runs under `env -i` with only
   `PATH`/`HOME` and its own Anthropic credential from
   `/root/.feature-factory.env`. It has **no** `INTERNAL_API_TOKEN`, Postgres,
   Discord, GCS, IBKR, or Google credentials in its environment.
4. **Secrets never enter worktrees.** `.env` and `secrets/` are untracked, and
   `git worktree` only materializes tracked files — so they are physically
   absent from every feature worktree.
5. **Forced-command SSH.** The worker reaches the host runner through an SSH key
   whose `authorized_keys` entry pins `command="scripts/feature_runner.sh"` with
   `no-pty,no-port-forwarding,no-agent-forwarding`. Even a fully compromised
   worker can only invoke the fixed verbs (create/build/status/diff/accept/
   revert/discard) with a validated `[a-z0-9-]` slug — never an arbitrary
   host command.
6. **Blocked & risky paths.** A diff that touches `.env`, `secrets/`, or
   `.github/` is hard-failed (`failed_blocked_paths`, never acceptable). A diff
   touching migrations, compose files, `infra/`, auth/proxy/internal-auth code,
   or the runner script itself is flagged **risky** in the UI so the owner reads
   it before accepting.
7. **Code-only contract.** The prompt is prepended with a non-negotiable
   contract forbidding external side effects, docker, `alembic upgrade`,
   `git push`, and any file access outside the worktree. Migrations may be
   *written* but never *run* — the owner applies them on accept.
8. **Single-flight + timeout.** One build at a time (a lock in the worker); each
   build is killed after 30 minutes.

The `.features/` worktrees dir is gitignored. Builds still cost Anthropic
credits on the host key's account — the manual "Build feature" click is the
per-action consent.

## One-time host setup (owner, on the VPS)

Run `scripts/setup_feature_factory.sh` as root on the VPS. It:
- installs the Claude CLI for root if absent (`/root/.local/bin/claude`),
- writes `/root/.feature-factory.env` (you paste an `ANTHROPIC_API_KEY=` — a key
  scoped/budgeted for this is wise),
- generates the forced-command SSH keypair, installs the public half into
  `authorized_keys` pinned to `scripts/feature_runner.sh`, and drops the private
  half at `secrets/feature_runner_key` (mounted read-only into the worker),
- adds a `host-gateway` alias so the worker can SSH back to the host.

Until this is done the Features tab shows "Build runner not configured" and the
Build button is disabled — everything else in the app is unaffected.

## Writing a good feature prompt

Be specific about the surface and the acceptance check ("add X to the Exposure
tab; masked viewers must still see •••; add a vitest for the helper"). The build
report (shown per feature) says what it did and how it verified. Always
**Preview** before Accept, especially when the risky-paths warning appears.
