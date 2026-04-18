---
name: conductor-vps-ops
description: VPS operational patterns for the Conductor host — SSH, systemd, Docker, testing, Caddy, UFW. Apply when Claude Code sessions reach the VPS, when investigating service state, when running tests, or when performing any operational task on conductor-tor1. Complements conductor-git-discipline for rotation and verification hygiene.
---

# Conductor VPS Operations

## When this applies

Fires on: SSH connection to the VPS, systemd service interactions, Docker container lifecycle, test runs, Caddy config edits, UFW rule changes, cron/systemd timer debugging, disk or resource investigation.

## Host facts

- **Provider:** DigitalOcean, Toronto region (tor1)
- **Droplet name:** conductor-tor1
- **Specs:** 4 GB RAM, 2 vCPU, 120 GB NVMe, Ubuntu 24.04 LTS
- **Monthly cost:** $32 USD
- **Domain:** conductor.verifundcapital.com (DNS via Squarespace)
- **SSH:** key-auth only (ed25519), root login disabled, UFW active
- **Primary user:** `josh` with sudo and docker group membership
- **SSH target:** use local SSH config alias, not the literal IP (which is the public repo reason for not hardcoding it here)

## Directory layout

- `/opt/conductor/` — main Python codebase, `pyproject.toml`, agent loop, prompts
- `/opt/conductor/.env` — credentials (authoritative)
- `/opt/n8n/` — Docker stack
- `/opt/n8n/.env` — **symlink** to `/opt/conductor/.env` (one source of truth; do not break)
- `/opt/n8n/docker-compose.yml` — n8n + Caddy definitions
- `/opt/n8n/n8n_data/database.sqlite` — n8n workflow storage (backup target)
- `/opt/n8n/caddy_data/` — Caddy TLS cert cache
- `/opt/conductor/.claude/skills/` — Skill definitions (this file lives here)
- `/var/lib/conductor/` — runtime state if present (logs, cache)

## Discovery before building — always

Before writing any new code or editing existing modules, run:

```bash
find /opt/conductor -name '*.py' -not -path '*__pycache__*' | sort
```

Python module layout has shifted during Phases 1-2 and session context may be stale. Discovery prevents writing a "new" file that already exists under a different name.

## Systemd services

Primary service: `conductor.service`
- **Status check:** `sudo systemctl status conductor.service`
- **Logs:** `sudo journalctl -u conductor.service -f` (follow) or `-n 200` (last 200 lines)
- **Restart:** `sudo systemctl restart conductor.service`
- **Environment file:** `EnvironmentFile=/opt/conductor/.env` in the unit file — edits to `.env` require service restart to propagate (different mechanism from Docker's recreate requirement)

Current production state: agent loop runs in PRODUCTION MODE since April 14, 2026. The unit is enabled and running. Do not add `--dry-run` flags without an explicit reason and documentation.

## Docker operations (n8n stack)

```bash
cd /opt/n8n
docker compose ps                       # state
docker compose logs -f n8n              # follow n8n logs
docker compose logs -f caddy            # follow Caddy logs
docker compose restart n8n              # restart n8n only (keeps env frozen!)
docker compose up -d --force-recreate n8n   # force env refresh after .env change
docker compose pull && docker compose up -d # upgrade (careful — images unpinned)
```

**Critical:** `docker compose restart` does NOT refresh `.env` variables. Only `up -d --force-recreate` does. This is a different mechanism from systemd — do not apply systemd mental model to Docker env handling.

**Image pinning warning:** `n8nio/n8n` and `caddy:2` are currently unpinned (Tier 2 #11 in A-grade list). A surprise `docker compose pull` could introduce breaking changes. Prefer explicit version pins once that task lands.

## Container identification

- `n8n-n8n-1` — the n8n service
- `n8n-caddy-1` — Caddy reverse proxy + TLS

Use these names for targeted operations:
```bash
docker exec -it n8n-n8n-1 /bin/sh       # shell inside n8n
docker inspect n8n-n8n-1 | jq '.[0].Config.Env'  # view running env vars
```

## Testing

Test suite: 339 tests as of April 17, 2026 (264 pass, 74 pre-existing failures, 1 xfail).

```bash
cd /opt/conductor
pytest                                   # full suite
pytest -m inf051                         # marker-based selection (INF-051 scope)
pytest -m inf052                         # INF-052 scope (and so on through inf054)
pytest tests/path/to/specific_test.py    # focused run
pytest -x                                # stop on first failure
pytest --lf                              # last failed only
```

Markers `inf051–inf054` are defined in `pyproject.toml` under `[tool.pytest.ini_options]` with `testpaths` configured. Do not introduce new markers without updating `pyproject.toml`.

**Known issue:** `config/` package shadows `config.py` file depending on `sys.path` resolution order (INF-101). Surfaced during Notion token rotation work; 15-minute rename fix is queued but not yet done. If a test suddenly fails with `ImportError` on config, this is the likely cause — do not chase it as a new bug.

## Conductor agent loop

The agent loop runs as `conductor.service`. To observe it without disrupting production:

```bash
sudo journalctl -u conductor.service -f          # follow live
grep "cycle" /var/log/conductor/*.log 2>/dev/null # if file logging exists
```

The service reads configuration from `/opt/conductor/.env` via the systemd `EnvironmentFile` directive. Editing `.env` and restarting is the canonical config change flow.

## UFW (firewall) — current posture

Active rules (do not modify without explicit need):
- 22/tcp (SSH) — key-auth only
- 80/tcp (HTTP) — Caddy redirects to HTTPS
- 443/tcp (HTTPS) — Caddy terminates TLS
- All other ports denied by default

Check state: `sudo ufw status verbose`

Port 5678 (n8n) is **not** open externally — n8n is reached via Caddy reverse proxy on 443, or via loopback (`127.0.0.1:5678`) from on-host sessions. This is the INF-125 resolution.

## Caddy TLS

TLS certs are obtained and renewed automatically by Caddy via Let's Encrypt. Cert cache lives in `/opt/n8n/caddy_data/`. To check cert status:

```bash
docker exec n8n-caddy-1 caddy list-modules | head -5  # sanity check Caddy running
openssl s_client -connect conductor.verifundcapital.com:443 -servername conductor.verifundcapital.com </dev/null 2>/dev/null | openssl x509 -noout -dates
```

Cert expiry = webhook failure = morning briefing stops. If Uptime Robot SSL monitoring isn't active (Tier 4 #33), check expiry manually during ops.

## Backups and DR

Snapshots are DigitalOcean-managed; schedule configured in DO control panel. DR rehearsal (snapshot → scratch droplet → confirmed restore under 60 minutes) is **pending** (Tier 1 #3). Until executed, assume restore time is unverified.

For on-host backup of n8n workflows:
```bash
cp /opt/n8n/n8n_data/database.sqlite /tmp/n8n_backup_$(date +%Y%m%d_%H%M%S).sqlite
```

For `.env` backup before rotation:
```bash
cp /opt/conductor/.env /opt/conductor/.env.bak.$(date +%s)
```

## Disk and resource sanity checks

```bash
df -h                                    # disk usage
free -h                                  # memory
docker system df                         # Docker disk usage (images, containers, volumes)
docker system prune -f                   # clean unused images/containers (careful!)
htop                                     # live process view
```

Watch n8n container memory usage during swarm runs. DealSwarm-v2 has occasionally pushed n8n memory to 80%+; a 5× load test (A13) is planned.

## Claude Code on this host — operating mode

- **SSH directly to VPS**, skip `scp` — build on VPS, not locally
- **Auto mode** (Shift+Tab to cycle permission modes) acceptable on trusted codebases (this one). Review commits before pushing.
- **Shared task list across sessions:** `CLAUDE_CODE_TASK_LIST_ID=conductor` keeps state across Claude Code sessions on this host
- **Extended thinking toggle mid-session:** `Alt+T` for architectural decisions, gate evaluations, P1 compliance calls (see conductor-quality-protocols Skill)
- **Background long commands:** Ctrl+B backgrounds, retrieve output via Read. Useful for long test runs.
- **Rewind bad edits:** Esc+Esc rewinds state if a VPS edit went wrong

## Telegram bot — operational reference

Bot webhook: `https://conductor.verifundcapital.com/webhook/telegram-callback`
Token lives in `/opt/conductor/.env` as `TELEGRAM_BOT_TOKEN`. Do not print the token to any log, chat, or commit. Rotation protocol: see conductor-git-discipline Skill, including the post-rotation webhook re-registration step which is required for inbound commands (e.g., `/status`) to work.

To check webhook health without revealing the token (uses the env-resolved value):
```bash
BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' /opt/conductor/.env | cut -d= -f2-)
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" | jq '.result | {url, last_error_message, last_error_date}'
unset BOT_TOKEN
```

Healthy state: `url` populated, `last_error_*` fields null or absent.

## Environment variable refresh — two mechanisms

This is the most common operational confusion, so restating plainly:

| Service type | `.env` edit requires |
|---|---|
| systemd unit (`conductor.service`) | `sudo systemctl restart conductor.service` |
| Docker container (`n8n-n8n-1`) | `cd /opt/n8n && docker compose up -d --force-recreate n8n` |

`docker compose restart` does NOT refresh environment. Using it after an `.env` edit is the single most common 2 AM mistake.

## Verification before declaring ops change done

After any service restart, container recreate, or config edit:
1. Check service/container is in expected state (`systemctl status` / `docker compose ps`)
2. Tail logs for 30-60 seconds to confirm no startup errors
3. Run one functional smoke test (hit health endpoint, trigger a test workflow execution)
4. One-line verification — "Verified: [service] [restart/recreate] at [ts], logs clean, smoke test [result]"

No "restarted" or "deployed" without those three checks.
