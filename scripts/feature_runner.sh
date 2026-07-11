#!/bin/bash
# Feature-factory host runner.
#
# SECURITY MODEL: this script is installed as the SSH *forced command* for the
# worker's feature key (authorized_keys: command="...feature_runner.sh",
# no-pty,no-port-forwarding,...). The worker container therefore cannot run
# arbitrary host commands — only the fixed verbs below, with validated args.
#
# PRIVILEGE MODEL (v2): the builder (claude with --dangerously-skip-permissions)
# runs as the unprivileged system user `factory`, NEVER as root:
#   * Build sandboxes are full `git clone`s (file:// transport, self-contained,
#     no gitdir pointer back into /root) at /home/factory/features/<slug>,
#     chowned to factory. A clone, not a worktree: a worktree's .git file points
#     into /root/broker-cockpit/.git/worktrees/<slug>, which would force opening
#     a traverse path through /root (0700) to the factory user. A clone shares
#     nothing with /root at all.
#   * factory has NO access to /root (0700): not the repo's .env, not secrets/,
#     not /root/.ssh. Its only credential is /etc/feature-factory.env
#     (root:factory 0640) — the builder Anthropic key, nothing else.
#   * Metadata (prompt, status, report, diff, pids) lives ROOT-ONLY at
#     /root/broker-cockpit/.features/<slug> — the builder cannot forge its own
#     status/report/diff.
#   * Root NEVER runs git inside the factory-owned clone (a hostile build could
#     plant repo config/hooks there). Transfer back is one-way data: factory
#     creates a `git bundle`; root fetches from the bundle (fsck'd) into the
#     trusted repo's feature/<slug> branch. The reviewed diff and the accepted
#     merge both come from the ROOT repo's objects — what the owner previews is
#     byte-for-byte what accept merges (head SHA re-verified at accept).
#
# Verbs (args arrive via SSH_ORIGINAL_COMMAND):
#   ping                      -> line1 configured|unconfigured, line2 paused|active
#   pause / resume            -> toggle the kill-switch flag (create/build refuse while paused)
#   create <slug>             -> clone build sandbox + branch feature/<slug> from main; prompt on stdin
#   build  <slug> <model>     -> run claude AS factory in the sandbox (long); harvest into root repo
#   kill   <slug>             -> terminate a running build (signals the recorded process group)
#   status <slug>             -> emit status + risky paths + diffstat as JSON-ish lines
#   diff   <slug>             -> emit the stored diff
#   accept <slug>             -> merge --no-ff into main, rebuild containers; prints merge sha
#   revert <slug> <merge_sha> -> git revert -m 1 of an accepted merge, rebuild
#   discard <slug>            -> remove sandbox + metadata + branch (unaccepted features)
#
# The builder additionally runs under `env -i`: no INTERNAL_API_TOKEN, no
# Postgres/Discord/IBKR/GCS credentials — only HOME/PATH and the builder's own
# Anthropic credential from /etc/feature-factory.env. Untracked secrets (.env,
# secrets/) never appear in clones: git only materializes tracked files.
set -euo pipefail

REPO=/root/broker-cockpit
FEATURES=$REPO/.features            # root-only metadata, gitignored
BUILD_ROOT=/home/factory/features   # factory-owned build sandboxes
FACTORY_USER=factory
FACTORY_HOME=/home/factory
ENV_FILE=/etc/feature-factory.env
PAUSE_FLAG=/root/.feature-factory.paused
BUILD_TIMEOUT=1800
CLAUDE_BIN=/usr/local/bin/claude

# Paths a feature diff may NEVER touch (hard fail) and paths that flag review.
BLOCKED_RE='^(\.env|secrets/|\.github/)'
RISKY_RE='^(apps/worker/migrations/|compose.*\.yml|infra/|apps/web/src/auth\.ts|apps/web/src/proxy\.ts|apps/worker/app/internal_auth\.py|scripts/feature_runner\.sh)'

if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  # shellcheck disable=SC2086
  set -- $SSH_ORIGINAL_COMMAND
fi
verb=${1:-}; slug=${2:-}; arg3=${3:-}

case "$verb" in
  ping|pause|resume) ;;
  *) [[ "$slug" =~ ^[a-z0-9][a-z0-9-]{0,47}$ ]] || { echo "ERR bad slug" >&2; exit 2; } ;;
esac
WT="$BUILD_ROOT/$slug"; BR="feature/$slug"; META="$FEATURES/$slug"

# git inside the (untrusted, factory-owned) sandbox ALWAYS runs as factory.
fgit() { sudo -Hu "$FACTORY_USER" git -C "$WT" "$@"; }

refuse_if_paused() {
  [ -f "$PAUSE_FLAG" ] && { echo "ERR factory is paused (kill switch) — resume it from the Features tab before building" >&2; exit 6; }
  return 0
}

contract() {
cat <<'EOF'
# Build contract (non-negotiable)
You are building ONE feature inside this git clone of the broker-cockpit repo.
- Your ONLY deliverable is code changes inside this working directory. Read the
  repo's CLAUDE.md for conventions; write tests in the repo's existing styles.
- FORBIDDEN, no exceptions: any network call with side effects (Discord, GCS,
  Robinhood, IBKR, gcloud, GitHub, any POST/PUT/DELETE to external services);
  running docker or docker compose; running alembic upgrade/downgrade against
  any database; git push; reading or writing any path outside this directory;
  reading .env files or secrets/ anywhere; installing global software.
- You MAY: read/edit files here, run local unit tests that need no database or
  docker (pytest unit mode, vitest, builds), and call the Anthropic API you
  yourself run on.
- If the feature seems to require a schema migration, WRITE the migration file
  but never execute it; the owner reviews migrations before accept.
- When done: ensure `git add -A && git commit` would capture your work (the
  runner commits for you), and end your final message with a short report:
  what you built, files touched, how you verified, anything the owner must do.
EOF
}

case "$verb" in
  ping)
    # Credential: either an api-key env file OR the factory user's own claude
    # OAuth login (subscription mode; creds under /home/factory/.claude/).
    cred=no
    [ -f "$ENV_FILE" ] && cred=yes
    sudo -u "$FACTORY_USER" test -s "$FACTORY_HOME/.claude/.credentials.json" 2>/dev/null && cred=yes
    if [ -x "$CLAUDE_BIN" ] && [ "$cred" = yes ] && id -u "$FACTORY_USER" >/dev/null 2>&1; then
      echo configured
    else
      echo unconfigured
    fi
    if [ -f "$PAUSE_FLAG" ]; then echo paused; else echo active; fi
    ;;
  pause)
    touch "$PAUSE_FLAG"
    echo OK
    ;;
  resume)
    rm -f "$PAUSE_FLAG"
    echo OK
    ;;
  create)
    refuse_if_paused
    mkdir -p "$FEATURES" && chmod 700 "$FEATURES"
    mkdir -p "$BUILD_ROOT"
    chown "$FACTORY_USER:$FACTORY_USER" "$BUILD_ROOT"
    { [ -e "$WT" ] || [ -e "$META" ]; } && { echo "ERR exists" >&2; exit 3; }
    # file:// forces the transport path: objects are COPIED, nothing shared
    # with or pointing into /root. The sandbox is fully self-contained.
    git clone -q --branch main "file://$REPO" "$WT"
    git -C "$WT" checkout -q -b "$BR"
    git -C "$WT" remote remove origin       # push target structurally absent
    chown -R "$FACTORY_USER:$FACTORY_USER" "$WT"
    mkdir -p "$META"
    cat > "$META/PROMPT.md"                 # prompt arrives on stdin
    git -C "$REPO" rev-parse main > "$META/base_sha"
    echo "created" > "$META/status"
    echo OK
    ;;
  build)
    refuse_if_paused
    model=${arg3:-claude-fable-5}
    [[ "$model" =~ ^[a-z0-9.-]{1,48}$ ]] || { echo "ERR bad model" >&2; exit 2; }
    [ -d "$META" ] && [ -d "$WT" ] || { echo "ERR no such feature" >&2; exit 3; }
    rm -f "$META/killreq" "$META/build.pid"
    echo "building" > "$META/status"
    base_sha=$(cat "$META/base_sha")
    cd "$WT"
    set +e
    # setsid: own process group so `kill` can terminate the whole build tree.
    # sudo -u factory + env -i: unprivileged AND scrubbed — the builder's world
    # is this clone, /home/factory, and its own Anthropic key. Root's shell
    # owns the redirections, so report/err land in root-only metadata.
    # Credential env: api-key mode injects ANTHROPIC_API_KEY; subscription
    # mode injects nothing — the CLI finds its OAuth creds via HOME.
    cred_env=""
    [ -f "$ENV_FILE" ] && cred_env=$(grep -v '^#' "$ENV_FILE" | xargs)
    { contract; echo; echo "# Feature request"; cat "$META/PROMPT.md"; } | \
      setsid timeout "$BUILD_TIMEOUT" sudo -u "$FACTORY_USER" env -i \
        HOME="$FACTORY_HOME" PATH=/usr/local/bin:/usr/bin:/bin \
        $cred_env \
        "$CLAUDE_BIN" -p --dangerously-skip-permissions --model "$model" \
        > "$META/report.md" 2> "$META/build.err" &
    bpid=$!
    echo "$bpid" > "$META/build.pid"
    wait "$bpid"
    rc=$?
    set -e
    rm -f "$META/build.pid"
    # Commit the builder's work AS factory (root never runs git in the sandbox).
    fgit add -A >/dev/null 2>&1 || true
    fgit -c user.name=feature-factory -c user.email=factory@localhost \
        commit -q -m "feature/$slug: $(head -c 60 "$META/PROMPT.md" | tr '\n' ' ')" 2>/dev/null || true
    # One-way harvest: factory bundles its branch; root fetches the bundle
    # (pure data, fsck'd) into the TRUSTED repo. Diff/scan/accept all read
    # root-repo objects from here on — the sandbox is no longer trusted input.
    fgit bundle create "$WT/.git/factory.bundle" "$BR" >/dev/null 2>&1 || true
    git -C "$REPO" -c fetch.fsckObjects=true fetch -q \
        "$WT/.git/factory.bundle" "+refs/heads/$BR:refs/heads/$BR" 2>/dev/null || true
    git -C "$REPO" diff "$base_sha".."$BR" > "$META/diff.patch" 2>/dev/null || true
    changed=$(git -C "$REPO" diff --name-only "$base_sha".."$BR" 2>/dev/null || true)
    echo "$changed" | grep -E "$BLOCKED_RE" > "$META/blocked.txt" || true
    echo "$changed" | grep -E "$RISKY_RE" > "$META/risky.txt" || true
    git -C "$REPO" diff --stat "$base_sha".."$BR" 2>/dev/null | tail -1 > "$META/diffstat" || true
    git -C "$REPO" rev-parse "refs/heads/$BR" > "$META/head_sha" 2>/dev/null || true
    if [ -s "$META/blocked.txt" ]; then
      echo "failed_blocked_paths" > "$META/status"
    elif [ -f "$META/killreq" ]; then
      echo "killed" > "$META/status"
    elif [ $rc -ne 0 ]; then
      echo "failed" > "$META/status"
    elif [ -z "$changed" ]; then
      echo "failed_no_changes" > "$META/status"
    else
      echo "built" > "$META/status"
    fi
    cat "$META/status"
    ;;
  kill)
    [ -f "$META/build.pid" ] || { echo "ERR no running build" >&2; exit 4; }
    bpid=$(cat "$META/build.pid")
    pgid=$(ps -o pgid= -p "$bpid" 2>/dev/null | tr -d ' ')
    [ -n "$pgid" ] || { echo "ERR build already exited" >&2; exit 4; }
    touch "$META/killreq"
    kill -TERM -- "-$pgid" 2>/dev/null || true
    sleep 2
    kill -KILL -- "-$pgid" 2>/dev/null || true
    echo OK
    ;;
  status)
    [ -d "$META" ] || { echo "missing"; exit 0; }
    echo "STATUS $(cat "$META/status" 2>/dev/null || echo unknown)"
    echo "DIFFSTAT $(cat "$META/diffstat" 2>/dev/null || true)"
    echo "RISKY_BEGIN"; cat "$META/risky.txt" 2>/dev/null || true; echo "RISKY_END"
    echo "REPORT_BEGIN"; tail -c 20000 "$META/report.md" 2>/dev/null || true; echo "REPORT_END"
    ;;
  diff)
    tail -c 400000 "$META/diff.patch" 2>/dev/null || true
    ;;
  accept)
    [ "$(cat "$META/status")" = "built" ] || { echo "ERR not in built state" >&2; exit 4; }
    cd "$REPO"
    # Merge exactly the reviewed SHA: the branch lives in the ROOT repo (fetched
    # at build end); re-verify it still matches what the diff was generated from.
    head_sha=$(cat "$META/head_sha" 2>/dev/null || true)
    [ -n "$head_sha" ] && [ "$(git rev-parse "refs/heads/$BR")" = "$head_sha" ] \
      || { echo "ERR branch head does not match reviewed SHA" >&2; exit 5; }
    # Inline identity: never depend on host git config for the merge commit.
    if ! merr=$(git -c user.name=feature-factory -c user.email=factory@localhost \
                merge --no-ff -q -m "Accept feature/$slug (feature-factory)" "$BR" 2>&1); then
      git merge --abort 2>/dev/null || true
      echo "ERR merge failed: $(echo "$merr" | head -c 300)" >&2; exit 5
    fi
    merge_sha=$(git rev-parse HEAD)
    docker compose -f compose.yml -f compose.prod.yml up -d --build worker web >/dev/null 2>&1
    echo "accepted" > "$META/status"
    echo "MERGE $merge_sha"
    ;;
  revert)
    [[ "$arg3" =~ ^[0-9a-f]{7,40}$ ]] || { echo "ERR bad sha" >&2; exit 2; }
    cd "$REPO"
    git -c user.name=feature-factory -c user.email=factory@localhost \
      revert -m 1 --no-edit "$arg3" -q
    docker compose -f compose.yml -f compose.prod.yml up -d --build worker web >/dev/null 2>&1
    echo "reverted" > "$META/status"
    echo "REVERTED $(git rev-parse HEAD)"
    ;;
  discard)
    st=$(cat "$META/status" 2>/dev/null || echo unknown)
    [ "$st" = "accepted" ] && { echo "ERR accepted features are reverted, not discarded" >&2; exit 4; }
    rm -rf "$WT"
    git -C "$REPO" branch -D "$BR" 2>/dev/null || true
    rm -rf "$META"
    echo OK
    ;;
  *)
    echo "ERR unknown verb" >&2; exit 2
    ;;
esac
