# Conductor Operations Runbook
## conductor-tor1 · DigitalOcean Toronto · Updated April 13, 2026

---

## 1. SYSTEM OVERVIEW

| Component | Details |
|-----------|---------|
| VPS | DigitalOcean Toronto, conductor-tor1, 4GB/2vCPU/120GB NVMe |
| IP | 68.183.194.128 |
| OS | Ubuntu 24.04 LTS |
| Domain | conductor.verifundcapital.com |
| SSH | `ssh josh@68.183.194.128` (ed25519 key auth, root disabled) |
| User | josh (sudo + docker groups) |
| Firewall | UFW active (22, 80, 443) |
| Data Residency | Toronto approved as permanent location (Decision #44, April 13, 2026) |

### Docker Stack

| Container | Image | Port | Memory Limit | Restart |
|-----------|-------|------|-------------|---------|
| n8n | n8nio/n8n | 5678 | 2GB | unless-stopped |
| caddy | caddy:2 | 80, 443 | — | unless-stopped |
| uptime-kuma | louislam/uptime-kuma:1 | 3001 | 64MB | unless-stopped |

### Key Paths

| Path | Contents |
|------|----------|
| `/opt/n8n/` | docker-compose.yml, n8n data volume |
| `/opt/n8n/caddy/` | Caddyfile |
| `/opt/conductor/` | All Conductor Python code (see Section 10) |
| `/opt/conductor/schemas/` | Gate 1: Pydantic validation models (15 models, 41 tests) |
| `/opt/conductor/swarm/` | Deal pipeline: prompts, runner, fixtures (22 tests) |
| `/opt/conductor/watchdog/` | Gate 2: Behavioral watchdog + HALT system (23 tests) |
| `/opt/conductor/gemini/` | Gate 3: Gemini dual-AI verification (17 tests) |
| `/opt/conductor/archive/` | Retired code (old 5-stage orchestrator) |

---

## 2. COMMON OPERATIONS

### Check system status
```bash
ssh josh@68.183.194.128
docker ps                          # Running containers
docker stats --no-stream           # CPU/RAM per container
df -h /                            # Disk usage
free -h                            # Memory
uptime                             # Load + uptime
```

### Check n8n logs
```bash
cd /opt/n8n
docker compose logs --tail 100     # Last 100 lines
docker compose logs -f             # Live follow
docker compose logs n8n --since 1h # Last hour
```

### Restart n8n
```bash
cd /opt/n8n
docker compose restart n8n
```

### Restart entire stack (n8n + Caddy + Uptime Kuma)
```bash
cd /opt/n8n
docker compose down
docker compose up -d
```

### Restart VPS
```bash
sudo reboot
# Wait 60 seconds, then:
ssh josh@68.183.194.128
docker ps    # Verify all containers running
```

### Apply Ubuntu security updates
```bash
sudo apt update && sudo apt upgrade -y
# If "System restart required" appears:
sudo reboot
```

### Run Conductor test suite
```bash
cd /opt/conductor
python -m pytest -v                # Full suite (118 tests)
python -m pytest -v --tb=short -q  # Summary only
```

---

## 3. BACKUP STRATEGY

### What to back up
| Data | Location | Method | Frequency |
|------|----------|--------|-----------|
| n8n workflows + credentials | /opt/n8n/n8n_data/ (Docker volume) | DigitalOcean snapshot | Weekly |
| Conductor Python code | /opt/conductor/ | Git push to tmpllc1 org (future) / manual scp | On change |
| Caddyfile | /opt/n8n/caddy/Caddyfile | Included in snapshot | Weekly |
| Uptime Kuma data | Docker volume uptime-kuma-data | Included in snapshot | Weekly |

### DigitalOcean snapshots
1. Log in to https://cloud.digitalocean.com
2. Droplets → conductor-tor1 → Snapshots
3. Click "Take Snapshot" (costs ~$0.06/GB/mo, ~$0.72/mo for 12GB used)
4. Keep last 4 snapshots (rolling 4-week retention)

**TODO — Automated Snapshot Rotation (INF-152, P1, Session 2 of INF-112 follow-up):** Currently no automated snapshot creation exists. `doctl snapshot list` returns empty. Manual DO web UI snapshots work but require discipline. Upcoming cron (see INF-152) will create weekly snapshots, rotate oldest beyond 4. Until that lands, run manually once weekly:

```bash
set -a && source /opt/conductor/.env && set +a
doctl compute droplet-action snapshot 563846072 \
  --snapshot-name "conductor-weekly-$(date +%Y-%m-%d)" --wait
```

### Manual backup (n8n workflows)

**Note:** Until INF-153 automates this, the commands below must be run by hand; no cron creates `~/backups/workflows-*.json`. The DO snapshot (weekly) is currently the only automated backup of workflow state.

```bash
# Export all workflows via n8n CLI
docker exec n8n-n8n-1 n8n export:workflow --all --output=/tmp/workflows.json
docker cp n8n-n8n-1:/tmp/workflows.json ~/backups/workflows-$(date +%Y%m%d).json

# Export credentials (encrypted — cannot import to different instance)
docker exec n8n-n8n-1 n8n export:credentials --all --output=/tmp/creds.json
docker cp n8n-n8n-1:/tmp/creds.json ~/backups/creds-$(date +%Y%m%d).json
```

### Manual backup (Conductor code)
```bash
# From local machine:
mkdir -p ~/conductor-backups
scp -r josh@68.183.194.128:/opt/conductor/ ~/conductor-backups/conductor-$(date +%Y%m%d)/
```

### Download all backups to local

**TODO: broken as of INF-112 drill 3 (2026-04-18) — see Gap 11 (INF-153) for replacement plan.** `~/backups/*.json` only exists if the manual export above has been run; no cron creates it.

```bash
mkdir -p ~/conductor-backups
scp josh@68.183.194.128:~/backups/*.json ~/conductor-backups/
```

---

## 4. DISASTER RECOVERY

### Scenario: VPS destroyed / unrecoverable

**Recovery time: ~6 minutes measured (341 seconds, drill 3, 2026-04-18).** Wall-clock including DNS propagation depends on Squarespace TTL — see Step 0 below.

**Step 0 (proactive, before any incident):** Ensure Squarespace DNS A-record TTL for conductor.verifundcapital.com is pre-set to 300s (5 min). Without this, actual RTO is dominated by DNS propagation (default TTL often 1–24 hours), not the 6-min restore time. Check annually as part of runbook review.

**Step 1: Create new droplet from latest snapshot.**

```bash
# Source credentials (contains DIGITALOCEAN_ACCESS_TOKEN)
set -a && source /opt/conductor/.env && set +a

# Identify the snapshot ID to restore from
SNAP_ID=$(doctl compute snapshot list --resource=droplet \
  --format ID,Name,CreatedAt --no-header | sort -k3 -r | head -1 | awk '{print $1}')
echo "Restoring from snapshot: $SNAP_ID"

# Spin new droplet with matching size + region + VPC + SSH key.
# Note: doctl is snap-confined on Ubuntu 24.04 and cannot read /tmp - if
# staging a cloud-init user-data file, put it in $HOME, not /tmp.
doctl compute droplet create conductor-tor1-restored \
  --image "$SNAP_ID" \
  --size s-4vcpu-8gb \
  --region tor1 \
  --vpc-uuid 48c5e9b3-aa96-40ca-8ec0-e8e93cdc59b1 \
  --ssh-keys 55490129 \
  --wait --format ID,Name,PublicIPv4,Status
```

Note: s-4vcpu-8gb has 160GB disk. The original s-2vcpu-4gb slug does NOT support our 120GB disk in tor1 as of 2026-04 (drill 1 finding). Intel variants are also unavailable in tor1.

2. Update DNS: Squarespace → conductor.verifundcapital.com → new IP (should propagate within 5 min thanks to Step 0 TTL)
3. SSH in, verify `docker ps` shows n8n + caddy + uptime-kuma
4. Re-register Telegram webhook with new IP:
```bash
curl "https://api.telegram.org/bot[REDACTED:telegram_token]/setWebhook?url=https://conductor.verifundcapital.com/webhook/telegram-callback&allowed_updates=[\"message\",\"callback_query\"]"
```
5. Verify: send `/status` in Telegram
6. Update Uptime Robot monitors with new IP

### Scenario: n8n database corrupted

1. Stop n8n: `cd /opt/n8n && docker compose stop n8n`
2. Back up corrupted data: `sudo cp -r n8n_data n8n_data_corrupted_$(date +%Y%m%d)`
3. Restore from backup:

**TODO — n8n Workflow Daily Export (INF-153, P1, Session 3 of INF-112 follow-up):** The path `~/backups/workflows-YYYYMMDD.json` referenced below does NOT currently exist — no process creates it. Until INF-153 cron lands, workflow recovery relies entirely on DO snapshots (which include the full n8n SQLite DB at /opt/n8n/n8n_data/database.sqlite). For corruption-only (DB intact but workflow broken) recovery, the n8n UI's per-workflow version history is available at http://conductor.verifundcapital.com.

```bash
# ASPIRATIONAL — assumes INF-153 cron has created workflows-YYYYMMDD.json:
docker compose up -d n8n
docker cp ~/backups/workflows-YYYYMMDD.json n8n-n8n-1:/tmp/workflows.json
docker exec n8n-n8n-1 n8n import:workflow --input=/tmp/workflows.json
```
4. **Re-enter credentials.** All production credentials live in /opt/conductor/.env. If restoring from DO snapshot, .env IS restored with the disk — no re-entry needed. Re-entry is only required if .env was lost (snapshot corrupted or manual setup on fresh droplet):

| Credential | Env Var | Source | Used By |
|---|---|---|---|
| Anthropic API | ANTHROPIC_API_KEY | console.anthropic.com → API Keys | conductor.service, n8n DealSwarm |
| Notion Integration | NOTION_TOKEN | notion.so → Settings → Integrations → n8n-conductor | n8n workflows |
| Telegram Bot | TELEGRAM_BOT_TOKEN | BotFather (@verifund_conductor_bot) | n8n Callback Handler, conductor.service alerts |
| DigitalOcean API | DIGITALOCEAN_ACCESS_TOKEN | cloud.digitalocean.com/account/api/tokens | DR snapshot/restore only |
| n8n API | N8N_API_KEY | n8n UI → Settings → API | External workflow management (INF-125) |
| Zoho | ZOHO_* (multiple) | Zoho console OAuth | Zoho MCP integrations |

Permissions: `chmod 600 /opt/conductor/.env; chown josh:josh .env`. After re-entering, restart conductor.service and recreate n8n container (env vars frozen at container `up` — see Memory #18).

5. **Activate workflows selectively.** Production has 40 total workflows; only 18 are active. Do NOT activate all — the other 22 are retired/deprecated and activating them will cause scheduler conflicts and duplicate execution.

```bash
# Identify the 18 workflows that should be active (matches production state):
docker exec n8n-n8n-1 \
  sqlite3 /home/node/.n8n/database.sqlite \
  "SELECT id, name FROM workflow_entity WHERE active=1 ORDER BY id;"
```

6. Verify State Manager sub-workflow is active first (dependency for others), then activate the remaining 17 via n8n REST API or UI.

7. **Restore success criterion:** `SELECT COUNT(*) FROM workflow_entity WHERE active=1` returns 18 (and `SELECT COUNT(*) FROM workflow_entity` returns 40). The 18 active count is the operational target; the 40 total includes retired workflows from prior phases.

### Scenario: Docker won't start

```bash
sudo systemctl status docker
sudo systemctl restart docker
cd /opt/n8n && docker compose up -d
docker ps
```

If Docker itself is broken:
```bash
sudo apt install --reinstall docker-ce docker-ce-cli containerd.io
sudo systemctl start docker
cd /opt/n8n && docker compose up -d
```

### Scenario: Conductor tests failing after VPS restore

```bash
cd /opt/conductor
pip install -e . --break-system-packages  # reads pyproject.toml; authoritative dep list
python -m pytest -v
```

If tests fail on import errors, check Python version: `python3 --version` (needs 3.12+).

### Lockdown Pattern for Scratch/Test Droplets

When spinning a droplet from a production snapshot for testing (NOT real recovery), the droplet carries production credentials. Prevent conductor.service from activating using a systemd drop-in + marker file (NOT `systemctl mask` — that fails silently on regular unit files at /etc/systemd/system/):

```yaml
# In cloud-init user-data (stage in $HOME, NOT /tmp — doctl is snap-confined):
write_files:
  - path: /opt/conductor/.dr-rehearsal-lockdown
    permissions: '0644'
    content: LOCKDOWN=true
  - path: /etc/systemd/system/conductor.service.d/00-dr-lockdown.conf
    permissions: '0644'
    content: |
      [Unit]
      ConditionPathExists=!/opt/conductor/.dr-rehearsal-lockdown
      [Service]
      ExecStartPre=/bin/sh -c 'test ! -f /opt/conductor/.dr-rehearsal-lockdown'

bootcmd:
  - systemctl daemon-reload
  - [ mv, /opt/conductor/.env, /opt/conductor/.env.quarantined-dr-drill ]

runcmd:
  - systemctl mask docker.service docker.socket containerd.service
```

Verify post-boot: `systemctl show conductor.service --property=ConditionResult` should return `ConditionResult=no` (conductor cannot activate). Drill 3 (2026-04-18) validated this pattern end-to-end.

### Re-activating conductor after scratch validation

If a scratch droplet was used for drill/testing and later needs to become the real restored production (e.g., original prod is unrecoverable):

```bash
# 1. Verify this droplet is intended to become production (confirm via DO console)
# 2. Restore .env from quarantine
sudo mv /opt/conductor/.env.quarantined-dr-drill /opt/conductor/.env
sudo chown josh:josh /opt/conductor/.env
sudo chmod 600 /opt/conductor/.env

# 3. Remove the lockdown marker and drop-in
sudo rm /opt/conductor/.dr-rehearsal-lockdown
sudo rm /etc/systemd/system/conductor.service.d/00-dr-lockdown.conf
sudo systemctl daemon-reload

# 4. Unmask docker stack
sudo systemctl unmask docker.service docker.socket containerd.service
sudo systemctl start docker

# 5. Verify conductor can now start
sudo systemctl start conductor.service
systemctl is-active conductor.service  # expect: active
```

Do NOT run this on a scratch droplet intended for destruction-after-drill.

---

## 5. MONITORING

### External: Uptime Robot (free tier)
- Dashboard: https://dashboard.uptimerobot.com
- Account: joshliberman@gmail.com
- Monitors:
  - conductor.verifundcapital.com (HTTPS, 5-min interval)
  - 68.183.194.128 port 22 (SSH, 5-min interval)
- Alerts: joshliberman@gmail.com (email)
- Response: if alert fires, SSH in and check Docker containers

### Internal: Uptime Kuma (self-hosted)
- URL: http://68.183.194.128:3001 (or status.conductor.verifundcapital.com when DNS configured)
- Monitors to configure in Uptime Kuma UI:
  - n8n: HTTP → http://localhost:5678 (every 60s)
  - Telegram Bot: HTTP → https://api.telegram.org/bot[REDACTED:telegram_token]/getMe (every 300s)
  - Notion API: HTTP with headers → https://api.notion.com/v1/users/me (every 300s)
  - Caddy: HTTP → http://localhost:80 (every 60s)

### Health check script
```bash
cd /opt/conductor && python3 healthcheck.py
```
Returns JSON with per-service status. Exit code 0 = all healthy, 1 = failures detected.

### Manual checks
```bash
# n8n responding
curl -s https://conductor.verifundcapital.com | head -5

# Notion API reachable from VPS
curl -s -H "Authorization: Bearer [REDACTED:notion_token]" \
  -H "Notion-Version: 2022-06-28" \
  https://api.notion.com/v1/users/me | python3 -m json.tool

# Telegram bot alive
curl -s "https://api.telegram.org/bot[REDACTED:telegram_token]/getMe"

# All Docker containers running
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

---

## 6. CREDENTIALS REFERENCE

| Service | Location | Notes |
|---------|----------|-------|
| Notion API | n8n credential "Notion API" | $NOTION_TOKEN (stored in /opt/conductor/.env; rotation: INF-136 precedent) |
| Anthropic API | n8n credential "Anthropic" | Get from console.anthropic.com |
| Google Calendar | n8n credential "Google Calendar account" | OAuth, joshliberman@gmail.com |
| Telegram Bot | $TELEGRAM_BOT_TOKEN in /opt/conductor/.env | Read via n8n HTTP nodes; rotation: INF-136 precedent |
| DigitalOcean | https://cloud.digitalocean.com | Email TBD |
| Uptime Robot | https://dashboard.uptimerobot.com | joshliberman@gmail.com |
| Uptime Kuma | http://68.183.194.128:3001 | Set password on first access |
| SSH Key | Local machine ~/.ssh/ | ed25519, key auth only |
| Gemini API | .env file at /opt/conductor/ | GEMINI_API_KEY env var |
| GitHub | github.com/tmpllc1 | Private repos |

---

## 7. N8N WORKFLOW INVENTORY

| # | Workflow | Trigger | Schedule | Nodes |
|---|---------|---------|----------|-------|
| 0 | State Manager | Sub-workflow | On demand (concurrency=1) | Sub-Workflow Trigger → Code |
| 1 | Morning Briefing v3 | Cron | Daily 5:00 AM (Mon-Fri), 9:00 AM (Sat-Sun) | Schedule → Sub-Workflow (read) → Code → Sub-Workflow (write) |
| 2 | Change Detection v3 | Cron | Every 5 min | Schedule → Sub-Workflow (read) → Code → Sub-Workflow (write) |
| 3 | Rollover Handler v3 | Cron | Daily 5:30 PM | Schedule → Code |
| 4 | Evening Narrative v3 | Cron | Daily 5:35 PM | Schedule → Sub-Workflow (read) → Code → Sub-Workflow (write) |
| 5 | Stale Sweep v2 | Cron | Monday 8:00 AM | Schedule → Code |
| 6 | Cross-Entity Balance v3 | Cron | Friday 4:30 PM | Schedule → Code |
| 7 | Callback Handler v3 | Webhook | /telegram-callback | Webhook → Code |

**Critical:** State Manager must be active before all other workflows. It serializes state access with concurrency=1.

**All workflows use 2-4 node pattern.** All Notion queries are unfiltered (page_size: 100, filter in JS) — Notion API status filters throw 400 on n8n v2.15.0.

### State Keys

| Key | Contents | Used By |
|-----|----------|---------|
| briefing_state | streak, last_completion_date, weekly_completions | Morning Briefing |
| change_state | task_snapshots, today_changes, change_date, change_msg_id | Change Detection |
| narrative_state | last_narrative_date | Evening Narrative |

---

## 8. SECURITY CHECKLIST

- [x] SSH: key-only auth (password disabled)
- [x] SSH: root login disabled
- [x] UFW: only 22, 80, 443 open
- [x] n8n: not exposed directly (behind Caddy reverse proxy)
- [x] Caddy: auto-HTTPS via Let's Encrypt
- [x] Telegram webhook: chat ID validation in Code node
- [x] No PII in n8n workflow data (processed via Claude API with DPA)
- [x] API keys in n8n credentials store (encrypted at rest)
- [x] PII scrubber strips SIN, bank#, DOB, DL# before API calls
- [x] Anthropic DPA in effect (auto-included in Commercial Terms)
- [ ] Gemini API key stored in .env (not committed to git)
- [ ] Uptime Kuma password set on first access
- [ ] Regular DigitalOcean snapshot schedule configured

---

## 9. THREE-GATE VALIDATION PIPELINE

All deal swarm outputs pass through three validation gates before any write operation executes.

```
Raw documents → PII scrub → Agent 1 (extraction+risk) → Pydantic validate →
Agent 2 (lender match+summary) → Pydantic validate →
Compliance (8 checks) → Pydantic validate →
Silent failure check → Token budget check (50K cap) →
Watchdog semantic review → Gemini verification → WRITE_GATE → Execute
```

| Gate | Module | What It Catches | Tests |
|------|--------|----------------|-------|
| 1: Pydantic schemas | schemas/validators.py | Structural errors: wrong types, missing fields, malformed JSON | 41 |
| 2: Behavioral watchdog | watchdog/watchdog.py | Semantic drift: correct schema but wrong values, autonomy violations, urgency bypass attempts | 23 |
| 3: Gemini verification | gemini/verifier.py | Cross-model audit: LTV miscalculations, risk rating inconsistencies, missed compliance flags | 17 |

**WRITE_GATE** requires both watchdog approval AND Gemini verification (for deal swarm outputs only). Routine operations (Notion writes, status updates) skip Gate 3.

**HALT system** (watchdog/halt.py): Any gate can freeze all operations. 3 consecutive human denials auto-triggers HALT. Requires manual `/resume` to restart.

---

## 10. CONDUCTOR CODE INVENTORY

All code at `/opt/conductor/`. Python 3.12+, Pydantic v2, pytest. Total: 118 tests passing.

```
/opt/conductor/
├── model_router.py           # LiteLLM 3-tier routing (fast/balanced/verify) — 15 tests
├── test_model_router.py
├── pii_scrubber.py           # Regex PII scrubbing (SIN/bank/DOB/DL) — 27 tests
├── test_pii_scrubber.py
├── healthcheck.py            # Service dependency health checks
├── test_healthcheck.py
├── pytest.ini                # Excludes archive/ from collection
├── schemas/
│   ├── __init__.py
│   ├── deal_swarm.py         # 15 Pydantic v2 models for 2-stage swarm
│   ├── validators.py         # validate_agent_output() + validate_deal_complete()
│   └── test_schemas.py       # 41 tests
├── swarm/
│   ├── __init__.py
│   ├── prompts.py            # 3 system prompts (Agent 1, Agent 2, Compliance)
│   ├── runner.py             # DealSwarmRunner (dry_run, run_with_mock, 50K token cap)
│   ├── sample_deal.py        # Surrey BC $450K second mortgage fixture
│   └── test_harness.py       # 22 tests
├── watchdog/
│   ├── __init__.py
│   ├── config.py             # 10 safety rules, L0-L4 autonomy, WRITE_GATE
│   ├── halt.py               # HALTSystem with JSON persistence, 3-denial auto-trigger
│   ├── watchdog.py           # 6 deterministic checks + Claude API semantic review
│   └── test_watchdog.py      # 23 tests
├── gemini/
│   ├── __init__.py
│   ├── verifier.py           # GeminiVerifier (verify + should_verify)
│   └── test_verifier.py      # 17 tests
├── monitoring/
│   └── uptime-robot-setup.md # Uptime Robot configuration guide
└── archive/
    ├── deal_swarm_v1_5stage.py
    ├── gemini_verifier_v1.py
    └── tests_v1/             # Old test files
```

### Model Routing Tiers

| Tier | Model | Use Case | Cost (per 1M tokens) |
|------|-------|----------|---------------------|
| fast | gemini/gemini-2.5-flash-lite | Monitoring, status checks, classifications | $0.10 / $0.40 |
| balanced | anthropic/claude-sonnet-4-6 | Deal analysis, briefings, swarm agents, watchdog | $3.00 / $15.00 |
| verify | gemini/gemini-2.5-flash | Gemini dual-AI verification (deal swarm only) | $0.30 / $2.50 |

---

## 11. N8N CODING PATTERNS (CRITICAL)

These patterns have been discovered through debugging. Violating them causes silent failures.

**Notion API calls in Code nodes:**
- Use `body: { ... }` as a plain object — NEVER `body: JSON.stringify()` (causes double-encoding/400 errors)
- Pass `database_id` in URL path only, not duplicated in body
- Status value is exactly `'Archived - Obsolete'` with a regular hyphen — never an em dash
- `is_empty` filter is unreliable for rich_text — filter in JavaScript after fetch
- Pagination: cap at 10 pages maximum
- Filter completed tasks by `Status = Complete` first, then apply date filtering in JS

**Code node sandbox:**
- `fetch()` is unavailable — all HTTP calls must use `this.helpers.httpRequest()`
- `this.helpers.httpRequestWithAuthentication()` is unavailable — OAuth calls must go through dedicated HTTP Request nodes
- `.bind(this)` on helpers causes errors — call helpers inline at top level only
- Template literals and emojis can cause unexpected errors — prefer string concatenation
- Wrap all Code nodes in global try/catch that sends failure alerts to Telegram

**n8n self-hosted specifics:**
- 6-field cron (seconds first): e.g., `0 0 5 * * *` for 5 AM
- Docker container name: `n8n-n8n-1` — reference by name directly
- Docker Compose file: `/opt/n8n/docker-compose.yml`
- Execute Command nodes are not present in this instance
- State persistence: `$getWorkflowStaticData('global')` via State Manager sub-workflow

---

## 12. TROUBLESHOOTING

### Morning briefing didn't arrive

1. Check if workflow executed: n8n UI → Executions → filter by Morning Briefing
2. If no execution: verify Schedule node is active and cron is correct
3. If execution failed: check error in execution log
4. Common cause: Telegram API rate limit (429). Wait and retry.
5. Common cause: Notion API timeout. Check `curl` to Notion from VPS (Section 5).

### Change Detection message not updating

1. Change Detection edits a single Telegram message in-place. If the message is >24h old, Telegram blocks edits.
2. The workflow auto-creates a new message at midnight (date change in change_state).
3. If stuck: reset change_state by calling State Manager with `action: write, key: change_state, value: {}`.

### n8n out of memory

1. Check: `docker stats --no-stream`
2. If n8n > 2GB: restart with `docker compose restart n8n`
3. Prevent: enable execution pruning in n8n Settings → Workflows → Execution Data
4. Long-term: set `NODE_OPTIONS="--max-old-space-size=1536"` in docker-compose.yml environment

### Telegram webhook not receiving

1. Verify webhook is set:
```bash
curl "https://api.telegram.org/bot[REDACTED:telegram_token]/getWebhookInfo"
```
2. Check URL matches `https://conductor.verifundcapital.com/webhook/telegram-callback`
3. Check Caddy is running: `docker ps | grep caddy`
4. Check SSL cert: `curl -vI https://conductor.verifundcapital.com 2>&1 | grep -i "SSL\|certificate"`
5. Re-register if needed:
```bash
curl -X POST "https://api.telegram.org/bot[REDACTED:telegram_token]/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://conductor.verifundcapital.com/webhook/telegram-callback"}'
```

### Notion API returning 400

1. Most common cause: filter syntax. The n8n instance has known issues with compound `and` filters mixing `date` and `status` conditions.
2. Fix: query with single condition via API, apply secondary filtering in JavaScript.
3. Check: em dash vs regular hyphen in status values. `Archived - Obsolete` uses a regular hyphen.

### Disk space low

```bash
df -h /
# If < 20% free:
docker system prune -f              # Remove unused containers/images
sudo journalctl --vacuum-time=7d    # Trim system logs
```

---

## 13. COST

| Item | Monthly |
|------|---------|
| DigitalOcean VPS | $32 USD (~$46 CAD) |
| DigitalOcean snapshots (est.) | ~$1 USD |
| Uptime Robot | $0 (free tier) |
| Uptime Kuma | $0 (self-hosted) |
| **Total infrastructure** | **~$33 USD (~$47 CAD)** |

Full AI tool stack budget tracked separately — see Build Plan v4 Section: Budget.

---

## 14. CONTACTS & ACCOUNTS

| Service | URL | Account |
|---------|-----|---------|
| DigitalOcean | cloud.digitalocean.com | TBD |
| Squarespace DNS | squarespace.com | verifundcapital.com domain |
| Uptime Robot | dashboard.uptimerobot.com | joshliberman@gmail.com |
| n8n UI | conductor.verifundcapital.com | josh (local auth) |
| Anthropic Console | console.anthropic.com | joshliberman@gmail.com |
| Notion | notion.so | joshliberman@gmail.com |
| Telegram BotFather | t.me/BotFather | @verifund_conductor_bot |
| GitHub | github.com/tmpllc1 | Private org |


## PII Scrubber Endpoint (/scrub) — INF-085 single source of truth

**Added 2026-04-16.** Replaces the inline JS regex block in n8n DealSwarm-v2 "Prepare Deal Package" node. Single source of truth is `pii_scrubber.py` (27 unit tests + 12 endpoint tests).

### Endpoint URLs

- From VPS host (curl, ops): `http://localhost:8080/scrub`
- From n8n container (workflows): `http://172.18.0.1:8080/scrub`  (docker bridge gateway; `host.docker.internal` is not resolvable on this Linux Docker setup)

### Request / response schema

```
POST /scrub
Content-Type: application/json
Body: { "text": string (max 1 MB) }

Response 200:
{
  "scrubbed": string,
  "report": {
    "sin_count": int,
    "bank_count": int,
    "dob_count": int,
    "dl_count": int,
    "total_redacted": int,
    "original_length": int,
    "scrubbed_length": int
  }
}

Response 400: missing text, non-string text, non-object body, >1 MB payload
Response 500: scrubber raised (logs full traceback, Telegram alert sent)
```

### Fail-closed behavior

- On any HTTP error, n8n Prepare Deal Package node `throws` (re-thrown through its outer catch) so DealSwarm execution HALTS before raw PII reaches the Claude API.
- Telegram alert is sent from both ends: conductor.service `/scrub` 500s alert; n8n outer catch also alerts on any node failure.
- 5 s timeout on the n8n-side httpRequest.

### Monitoring

- No new monitor needed. conductor.service is already covered by the heartbeat (/health on 8080). If conductor.service is down, all DealSwarm executions halt by design (no silent regex fallback).
- Check service: `systemctl status conductor.service`
- Check endpoint: `curl -sS http://localhost:8080/scrub -H 'Content-Type: application/json' -d '{"text":"SIN: 046-454-286"}'`

### How to test

```
curl -sS -X POST http://localhost:8080/scrub -H 'Content-Type: application/json'   -d '{"text":"Borrower John Smith, SIN: 046-454-286, DOB: 1985-03-15, Account #: 12345-001-9876543"}'
```

Expected: 3 redacted items (SIN, DOB, bank), total_redacted=3.

### Source files

- Endpoint: `agent_loop.py` `scrub_handler` + `_scrub_validate`
- Logic: `pii_scrubber.py` (unchanged — single source of truth)
- Tests: `test_scrub_endpoint.py` (12 tests), `test_pii_scrubber.py` (27 tests)
- n8n node: VF-DealSwarm-v2 (id EQKquutkV4g1NWgp), "Prepare Deal Package" jsCode
