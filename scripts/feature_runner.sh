#!/bin/bash
# Feature-factory host runner.
#
# SECURITY MODEL: this script is installed as the SSH *forced command* for the
# worker's feature key (authorized_keys: command="...feature_runner.sh",
# no-pty,no-port-forwarding,...). The worker container therefore cannot run
# arbitrary host commands — only the fixed verbs below, with validated args.
#
# Verbs (args arrive via SSH_ORIGINAL_COMMAND):
#   ping                      -> "configured" | "unconfigured" (claude CLI + builder token present)
#   create <slug>             -> new worktree .features/<slug> on branch feature/<slug> from main; prompt read from stdin
#   build  <slug> <model>     -> run claude in the worktree (long); writes .feature/{status,report.md,diff.patch,risky.txt}
#   status <slug>             -> emit status + risky paths + diffstat as JSON-ish lines
#   diff   <slug>             -> emit the stored diff
#   accept <slug>             -> merge --no-ff into main, rebuild containers; prints merge sha
#   revert <slug> <merge_sha> -> git revert -m 1 of an accepted merge, rebuild
#   discard <slug>            -> remove worktree + branch (unaccepted features)
#
# The builder runs with a SCRUBBED environment (env -i): no INTERNAL_API_TOKEN,
# no Postgres/Discord/IBKR/GCS credentials — only PATH/HOME and the builder's
# own Anthropic credential from /root/.feature-factory.env. Untracked secrets
# (.env, secrets/) never appear in worktrees: git only materializes tracked files.
set -euo pipefail

REPO=/root/broker-cockpit
FEATURES=$REPO/.features
ENV_FILE=/root/.feature-factory.env
BUILD_TIMEOUT=1800
CLAUDE_BIN=/root/.local/bin/claude

# Paths a feature diff may NEVER touch (hard fail) and paths that flag review.
BLOCKED_RE='^(\.env|secrets/|\.github/)'
RISKY_RE='^(apps/worker/migrations/|compose.*\.yml|infra/|apps/web/src/auth\.ts|apps/web/src/proxy\.ts|apps/worker/app/internal_auth\.py|scripts/feature_runner\.sh)'

if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  # shellcheck disable=SC2086
  set -- $SSH_ORIGINAL_COMMAND
fi
verb=${1:-}; slug=${2:-}; arg3=${3:-}

if [ "$verb" != "ping" ]; then
  [[ "$slug" =~ ^[a-z0-9][a-z0-9-]{0,47}$ ]] || { echo "ERR bad slug" >&2; exit 2; }
fi
WT="$FEATURES/$slug"; BR="feature/$slug"; META="$WT/.feature"

contract() {
cat <<'EOF'
# Build contract (non-negotiable)
You are building ONE feature inside this git worktree of the broker-cockpit repo.
- Your ONLY deliverable is code changes inside this worktree. Read the repo's
  CLAUDE.md for conventions; write tests in the repo's existing styles.
- FORBIDDEN, no exceptions: any network call with side effects (Discord, GCS,
  Robinhood, IBKR, gcloud, GitHub, any POST/PUT/DELETE to external services);
  running docker or docker compose; running alembic upgrade/downgrade against
  any database; git push; reading or writing any path outside this worktree;
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
    if [ -x "$CLAUDE_BIN" ] && [ -f "$ENV_FILE" ]; then echo configured; else echo unconfigured; fi
    ;;
  create)
    mkdir -p "$FEATURES"
    [ -e "$WT" ] && { echo "ERR exists" >&2; exit 3; }
    git -C "$REPO" worktree add -b "$BR" "$WT" main -q
    mkdir -p "$META"
    cat > "$META/PROMPT.md"                # prompt arrives on stdin
    echo "created" > "$META/status"
    echo OK
    ;;
  build)
    model=${arg3:-claude-fable-5}
    [[ "$model" =~ ^[a-z0-9.-]{1,48}$ ]] || { echo "ERR bad model" >&2; exit 2; }
    [ -d "$META" ] || { echo "ERR no such feature" >&2; exit 3; }
    echo "building" > "$META/status"
    base_sha=$(git -C "$WT" rev-parse HEAD)
    set +e
    { contract; echo; echo "# Feature request"; cat "$META/PROMPT.md"; } | \
      timeout "$BUILD_TIMEOUT" env -i \
        HOME=/root PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin \
        $(grep -v '^#' "$ENV_FILE" | xargs) \
        "$CLAUDE_BIN" -p --dangerously-skip-permissions --model "$model" \
        > "$META/report.md" 2> "$META/build.err"
    rc=$?
    set -e
    cd "$WT"
    git add -A >/dev/null 2>&1 || true
    git -c user.name=feature-factory -c user.email=factory@localhost \
        commit -q -m "feature/$slug: $(head -c 60 "$META/PROMPT.md" | tr '\n' ' ')" 2>/dev/null || true
    git diff "$base_sha"..HEAD > "$META/diff.patch" || true
    changed=$(git diff --name-only "$base_sha"..HEAD || true)
    echo "$changed" | grep -E "$BLOCKED_RE" > "$META/blocked.txt" || true
    echo "$changed" | grep -E "$RISKY_RE" > "$META/risky.txt" || true
    git diff --stat "$base_sha"..HEAD | tail -1 > "$META/diffstat" || true
    if [ -s "$META/blocked.txt" ]; then
      echo "failed_blocked_paths" > "$META/status"
    elif [ $rc -ne 0 ]; then
      echo "failed" > "$META/status"
    elif [ -z "$changed" ]; then
      echo "failed_no_changes" > "$META/status"
    else
      echo "built" > "$META/status"
    fi
    cat "$META/status"
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
    if ! git merge --no-ff -q -m "Accept feature/$slug (feature-factory)" "$BR"; then
      git merge --abort || true
      echo "ERR merge conflict" >&2; exit 5
    fi
    merge_sha=$(git rev-parse HEAD)
    docker compose -f compose.yml -f compose.prod.yml up -d --build worker web >/dev/null 2>&1
    echo "accepted" > "$META/status"
    echo "MERGE $merge_sha"
    ;;
  revert)
    [[ "$arg3" =~ ^[0-9a-f]{7,40}$ ]] || { echo "ERR bad sha" >&2; exit 2; }
    cd "$REPO"
    git revert -m 1 --no-edit "$arg3" -q
    docker compose -f compose.yml -f compose.prod.yml up -d --build worker web >/dev/null 2>&1
    echo "reverted" > "$META/status"
    echo "REVERTED $(git rev-parse HEAD)"
    ;;
  discard)
    st=$(cat "$META/status" 2>/dev/null || echo unknown)
    [ "$st" = "accepted" ] && { echo "ERR accepted features are reverted, not discarded" >&2; exit 4; }
    git -C "$REPO" worktree remove --force "$WT" 2>/dev/null || rm -rf "$WT"
    git -C "$REPO" branch -D "$BR" 2>/dev/null || true
    echo OK
    ;;
  *)
    echo "ERR unknown verb" >&2; exit 2
    ;;
esac
