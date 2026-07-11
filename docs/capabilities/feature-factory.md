# Capability: Feature Factory

Owner-only **Features** tab (`/features`): describe a change in one prompt and
Claude builds it in an isolated build sandbox on the VPS — never on the live
branch. Each feature has **Preview** (view the diff), **Accept** (merge to main
+ redeploy), and **Revert/Discard**. A running build can be terminated with
**Stop**, and the whole factory has a **Pause/Resume** kill switch. Default
model is **Fable**; override in the UI dropdown or with a
`model: opus|sonnet|<claude-id>` first line in the prompt.

## Sandbox model — what is enforced by construction vs by contract

The build runs with `--dangerously-skip-permissions`, so the safety case must
not depend on the model's cooperation. Be precise about which layer enforces
what:

### Enforced by construction (the builder *cannot*, even if fully hostile)

1. **Unprivileged builder user.** The builder runs as the system user
   `factory` (`sudo -u factory` from the root runner): no sudo rights, nologin
   shell, home `/home/factory`. `/root` is mode 0700, so the builder has **no
   filesystem path** to `/root/broker-cockpit/.env` (0600 root:root),
   `secrets/`, `/root/.ssh` (0700, incl. the deploy key), or the docker
   socket-adjacent tooling that lives in root's world.
   `scripts/setup_feature_factory.sh` ends with a self-check that *proves*
   these denials on the actual host (PASS/FAIL lines) and refuses to complete
   otherwise.
2. **Self-contained build sandbox — a clone, not a worktree.** Each build
   happens in `/home/factory/features/<slug>`, a full `git clone` (via the
   `file://` transport, so objects are copied) of the repo, chowned to
   `factory`, with the `origin` remote removed. A worktree was rejected
   deliberately: a worktree's `.git` file points back into
   `/root/broker-cockpit/.git/worktrees/<slug>`, which would require opening a
   traverse path through `/root` to the factory user — exactly the hole this
   design closes. The clone shares nothing with `/root`.
3. **Scrubbed environment.** The builder runs under `env -i` with only
   `HOME`/`PATH` and its own Anthropic credential from
   `/etc/feature-factory.env` (root:factory 0640 — the *only* secret the
   factory user can read on the host). No `INTERNAL_API_TOKEN`, Postgres,
   Discord, GCS, IBKR, or Google credentials exist in its environment or on
   any path it can read.
4. **Secrets never enter sandboxes.** `.env` and `secrets/` are untracked;
   `git clone` only materializes tracked files.
5. **Root never runs git inside the sandbox.** A hostile build owns its clone
   and could plant repo config/hooks (`core.fsmonitor`, etc.) that execute on
   git invocation. So all in-sandbox git (commit, bundle) runs as `factory`;
   the work travels back as a `git bundle` — pure data — which root fetches
   (with `fetch.fsckObjects`) into `feature/<slug>` in the trusted root repo.
   The preview diff is generated from **root-repo objects**, and Accept
   re-verifies the branch head against the SHA recorded when that diff was
   produced: what the owner reviews is byte-for-byte what gets merged.
6. **Root-only metadata.** Status, report, diff, and build PID live in
   `/root/broker-cockpit/.features/<slug>` — the builder cannot forge its own
   status, report, or reviewed diff.
7. **main is immutable except accept/revert.** The runner only ever writes to
   `main` via `git merge --no-ff` (accept) or `git revert -m 1` (revert). The
   sandbox has no `origin` remote and no credential to push anywhere.
8. **Forced-command SSH.** The worker reaches the host runner through an SSH
   key whose `authorized_keys` entry pins `command="scripts/feature_runner.sh"`
   with `no-pty,no-port-forwarding,no-agent-forwarding`. Even a fully
   compromised worker can only invoke the fixed verbs
   (create/build/kill/pause/resume/status/diff/accept/revert/discard) with a
   validated `[a-z0-9-]` slug — never an arbitrary host command.
9. **Blocked paths.** A diff that touches `.env`, `secrets/`, or `.github/` is
   hard-failed (`failed_blocked_paths`, never acceptable) — scanned from the
   trusted root-repo diff, not the sandbox.
10. **Kill switch + bounded runs.** `pause`/`resume` set a host flag that makes
    `create` and `build` hard-fail; `kill` terminates a running build's whole
    process group. One build at a time (worker lock); every build is killed
    after 30 minutes.

### Enforced by contract only (honest limits — the builder *could* but is told not to)

- **Network egress is NOT sandboxed.** The factory user has no credentials, so
  it cannot *authenticate* to Discord/GCS/IBKR/GitHub/etc., but nothing
  host-side blocks outbound connections. Unauthenticated egress (fetching
  URLs, exfiltrating the diff-in-progress or the builder API key it can read)
  is limited only by credential absence plus the prompt contract. If this ever
  matters, the fix is an nftables/`cgroup` egress rule for the factory uid —
  not more prompt text.
- The code-only contract (no docker, no `alembic upgrade`, no package
  installs, stay inside the sandbox) is prompt text. Filesystem escapes are
  blocked by permissions, but e.g. burning CPU or writing junk inside
  `/home/factory` is merely forbidden, not impossible.

### Review-gate paths flagged, not blocked

A diff touching migrations, compose files, `infra/`, auth/proxy/internal-auth
code, or the runner script itself is flagged **risky** in the UI so the owner
reads it before accepting.

## One-time host setup (owner, on the VPS)

Run `scripts/setup_feature_factory.sh` as root on the VPS. It:
- creates the `factory` system user and `/home/factory/features`,
- installs the Claude CLI at `/usr/local/bin/claude` (a system path the
  factory user can execute — `/root/.local/bin` is unreachable for it),
- writes `/etc/feature-factory.env` (root:factory 0640; you paste an
  `ANTHROPIC_API_KEY=`), migrating any legacy `/root/.feature-factory.env`,
- asserts the secret perimeter: `/root` 0700, `/root/.ssh` 0700, repo `.env`
  0600 root:root, `.features/` metadata 0700,
- generates the forced-command SSH keypair, installs the public half into
  `authorized_keys` pinned to `scripts/feature_runner.sh`, and drops the
  private half at `secrets/feature_runner_key` (mounted read-only into the
  worker),
- runs the **isolation self-check** (factory must FAIL to read `.env`,
  `/root/.ssh`, `secrets/`; must SUCCEED reading `/etc/feature-factory.env`,
  running `claude --version`, and running git in a chowned clone) and exits
  nonzero if any line fails.

### REQUIRED activation step: spend limit on the builder key

The host cannot cap Anthropic spend. **Before the factory is ever enabled**,
create a dedicated workspace for the builder key in the Anthropic Console and
set a **monthly spend limit** on it (console.anthropic.com → Settings →
Limits). This is the enforcement mechanism for the safety spec's token-budget
cap — activation without it violates the spec (item 5). The setup script
prints this reminder loudly; treat it as blocking.

Until setup is done the Features tab shows "Build runner not configured" and
the Build button is disabled — everything else in the app is unaffected.

## Kill switch (owner)

- **Pause factory** (Features tab banner, or `feature_runner.sh pause` on the
  host): sets `/root/.feature-factory.paused`; `create`/`build` refuse with a
  clear error until **Resume**. The tab shows the paused state.
- **Stop** on a building feature: signals the build's recorded process group
  (TERM, then KILL); the feature lands in status `killed` and can be
  discarded. Partial work, the diff so far, and the report remain inspectable.

## Writing a good feature prompt

Be specific about the surface and the acceptance check ("add X to the Exposure
tab; masked viewers must still see •••; add a vitest for the helper"). The build
report (shown per feature) says what it did and how it verified. Always
**Preview** before Accept, especially when the risky-paths warning appears.
