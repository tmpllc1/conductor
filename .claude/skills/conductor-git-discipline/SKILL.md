---
name: conductor-git-discipline
description: Git, verification, and rotation hygiene for the Conductor codebase. Apply when staging commits, rotating credentials, verifying file changes, or capturing exit codes. Prevents the failure classes documented in INF-137, INF-144, and the Apr 17 Telegram rotation.
---

# Conductor Git & Verification Discipline

## When this applies

Fires on: any `git add`, `git commit`, credential rotation, file-content verification, or shell command whose pass/fail matters. If the session is touching anything that could ship bad state to production or leak a secret, this Skill applies.

## Pre-commit protocol — diff before add

Before any `git add`, run `git diff HEAD <file>` for each file you intend to stage. If the diff contains changes you did not author in this session, perform commit surgery:

1. Stage narrowly — use `git add -p` to select only this-session hunks
2. Re-apply intended edits on top of the correct base if needed
3. Restore unrelated-dirty files from /tmp backup or `git checkout` before continuing

Never `git add` a file with unattributed dirty state. Never `git add .` or `git add -A` on the Conductor repo. Source: INF-137, where 157 lines of a prior CLAUDE.md refactor almost shipped unintentionally.

## Token verification — never paste token bytes

When verifying a rotated credential, **never** do any of the following:
- Paste the full token to chat
- Run `cat` on the token file
- Run `tail -c N` on a token or token line (leaks suffix)
- Run `cat -A | tail -c N` (same leak)

Safe verification pattern:

1. Heredoc the new token into `/tmp/token` (single-quoted heredoc to prevent expansion)
2. Check length locally: `wc -c /tmp/token` — report numeric result only
3. Spot-check last 8 chars locally: `tail -c 12 /tmp/token | cat -A` — keep output local, report yes/no match only
4. Apply via sed reading from file, never inline literal:
   `sh -c 'sed -i "s|OLD_PREFIX.*|$(cat /tmp/token | tr -d \\r\\n)|" /opt/conductor/.env'`
5. Verify sed result via `awk 'length($0)' file` on the target line and `grep "$LAST_8" file && echo MATCH` — these do not reveal the token
6. `shred -u /tmp/token`

Source: Apr 17 Telegram rotation, where three cycles were burned on leaky verification patterns.

## Exit-code capture — `${PIPESTATUS[0]}`, not piped `$?`

When capturing a command's exit code through a pipe, `$?` captures the **last** command's exit (typically `tee` or similar), not the command of interest. This masks real failures as success.

Wrong:
```bash
git commit -m "msg" 2>&1 | tee -a log.txt
echo "exit: $?"  # captures tee's exit, always 0
```

Correct:
```bash
git commit -m "msg" 2>&1 | tee -a log.txt
EXIT=${PIPESTATUS[0]}
echo "exit: $EXIT"
```

Or capture before the pipe:
```bash
git commit -m "msg"
EXIT=$?
```

Verification signals for git-commit success/failure (not the exit code alone):
- Success: `[<branch> <sha>]` or `[detached HEAD <sha>]` line in output, HEAD moved to new sha
- Failure: "Failed" line in output, HEAD unchanged

Source: INF-144, where a pre-commit hook block was initially mis-logged as pass because piped exit was captured.

## Repository visibility awareness

**`github.com/tmpllc1/conductor` is PUBLIC.** Before every push, assume every line is publicly visible. Verify nothing in the diff contains:
- API keys, bot tokens, bearer tokens, OAuth secrets
- Absolute paths revealing user directory structure on non-VPS machines
- Client names, borrower details, deal amounts, property addresses
- Internal IPs or hostnames beyond what's already public (the Caddy-fronted domain)

`tmpllc1/tenantcase`, `tmpllc1/tailored-trader`, `tmpllc1/verilens` are **private**. The rule still applies — private today does not mean private forever. Treat every commit as if the repo could flip public tomorrow.

Gitleaks pre-commit hook (INF-132) is the backstop, not the primary defense. The primary defense is the human diff review before `git add`.

## Post-rotation webhook re-registration (Telegram-specific)

When rotating a Telegram bot token, the webhook dies with the old token. Rotation is not complete until re-registration:

```bash
curl -X POST "https://api.telegram.org/bot<NEW_TOKEN>/setWebhook" \
  -d "url=https://conductor.verifundcapital.com/webhook/telegram-callback" \
  -d 'allowed_updates=["message","callback_query"]'
```

Verify via `/getWebhookInfo` — url populated, no `last_error_*` fields = healthy. Outbound-only behavior (Conductor messages work, `/status` silently fails) = webhook not registered.

## Docker env var refresh

Docker `${VAR}` references in `docker-compose.yml` are frozen at container `up` time. Editing `.env` does NOT propagate to running containers. After any `.env` change affecting a container:

```bash
cd /opt/n8n && docker compose up -d --force-recreate n8n
```

`/opt/n8n/.env` is a symlink to `/opt/conductor/.env` — one source of truth. Do not break the symlink.

## Notion token rotation — concurrent not mutually-exclusive

Internal Notion tokens (`ntn_` prefix) have a 7-day grace on rotation. Grace is **concurrent**, not mutually-exclusive — rotating while a prior revocation is pending means TWO live tokens exist until each expires on its own schedule. Verify a leaked-token kill by `curl`ing `/v1/users/me` with the old token and confirming 401. Do not assume rotation killed the predecessor.

## Prove-before-claiming-done

On any change that affects production state, state the verification before claiming completion:

- Internal work (local edits, scratch scripts): one-line — "Verified: [what checked] → [what found] → [match Y/N]"
- External/compliance/investor-facing: full proof table — Source → Expected → Found → Match
- Never say "done" without the verification line. Never skip the verification even on small changes; that is exactly how INF-137 happened.
