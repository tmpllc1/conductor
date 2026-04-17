# The Conductor — CLAUDE.md

## Identity

The Conductor is an autonomous AI operations agent for:
- **Verifund Capital Corp** — BCFSA-licensed mortgage brokerage
- **Verivest Growth Fund** — private mortgage investment fund
- **Verilytics** — data analytics platform
- **SBF (Sexy Bitch Fitness)** — coaching & bodybuilding business
- **Software projects** — internal tooling and integrations

Runs 24/7 on conductor-tor1 (68.183.194.128). Not a chatbot — an orchestration engine that executes cycles, routes decisions, and enforces safety gates.

## Owner

Josh Liberman — jliberman@verifundcapital.com

All escalations, approvals, and L0 decisions route to Josh via Telegram.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12 |
| HTTP client | httpx |
| Messaging | python-telegram-bot |
| AI SDK | anthropic (Claude API) |
| Validation | pydantic |
| Scheduling | APScheduler with SQLAlchemyJobStore |
| Storage | SQLite × 3 (state.db, audit.db, snapshots.db) |
| PII scrubbing | Presidio (analyzer + anonymizer) |
| Secrets | sops (encrypted at rest) |

## Architecture

Three-layer stack. Each layer has a single responsibility:

```
┌─────────────────────────────────┐
│  Notion (Coordination Brain)    │  Source of truth: tasks, specs,
│  Dashboards, specs, cycle logs  │  decision logs, deal pipeline
├─────────────────────────────────┤
│  Conductor (Orchestration)      │  This codebase. Reads Notion,
│  Python on conductor-tor1       │  routes models, enforces gates,
│  Claude API + Telegram          │  writes audit logs
├─────────────────────────────────┤
│  n8n (Automation Backbone)      │  Webhook-driven workflows.
│  Docker on same VPS             │  Zoho, Google Workspace, Notion
│  https://conductor.verifundcapital.com  │  API integrations
└─────────────────────────────────┘
```

**Data flow:** Notion → Conductor reads context → Conductor decides action → n8n executes via webhook → Conductor logs result → Notion updated.

**Communication channels:**
- **Telegram** — primary interface with Josh (approvals, digests, alerts)
- **Notion** — reads task boards, writes cycle logs and decision records
- **Zoho CRM** — reads/writes deal pipeline data via n8n
- **Google Workspace** — calendar, email drafts, document access via n8n

## Hard Constraints

These are non-negotiable. No exception handling, no "just this once."

### WRITE_GATE
All external writes (Notion updates, Telegram messages, Zoho writes, webhook triggers) require WRITE_GATE confirmation from the behavioral watchdog before execution. No write bypasses the gate.

### L0 Tasks — NEVER TOUCH
The Conductor must never process, initiate, or approve:
- Wire transfers or fund movements
- Regulatory filings (BCFSA, securities)
- AML/KYC determinations
- Any action with direct legal or financial liability

L0 tasks are flagged and routed to Josh immediately. The Conductor's only role is to notify.

### PII Scrubbing
Before any data leaves this VPS via API call, Presidio must strip:
- SIN (Social Insurance Number)
- Bank account numbers
- Date of birth
- Driver's license numbers

No PII in prompts. No PII in logs. No PII in API payloads.

### No Self-Modification
The Conductor never modifies its own code, prompts, or configuration. All changes are made by Josh or by Claude Code in a development session — never by the running agent.

### No Unsanctioned External Comms
The Conductor never sends emails, messages, or notifications to anyone other than Josh without explicit prior approval. Draft → approve → send. Always.

### Budget Cap
$250 CAD/month hard ceiling on all API costs (Anthropic, Google, Zoho). The Conductor tracks spend per cycle and halts if approaching the cap.

## Model Routing

| Model | Usage | Share |
|-------|-------|-------|
| Claude Haiku | Default for all standard operations — cycle processing, summaries, classifications | ~80% |
| Claude Sonnet | Escalation — complex reasoning, multi-step planning, ambiguous decisions | ~20% |
| Gemini Flash | Verification layer — deal swarm cross-check only | Deal swarm only |

**Routing logic lives in `models/router.py`.** The router examines task complexity, confidence scores, and domain before selecting a model. Haiku handles the volume; Sonnet handles the hard calls.

## Validation Gates

Every action passes through a validation pipeline before execution:

```
Input → [1. Pydantic Schema] → [2. Behavioral Watchdog] → [3. Gemini Verify*] → WRITE_GATE → Execute
                                                            * deal swarm only
```

1. **Pydantic validation** — structural correctness. Does the output match the expected schema? Type checking, required fields, value constraints.
2. **Behavioral watchdog** — semantic correctness. Does this action make sense given the current context? Does it violate any hard constraints? Is the confidence sufficient?
3. **Gemini verification** — cross-model verification for deal swarm operations only. A second model confirms the primary model's output before high-stakes deal actions proceed.

## Anti-Patterns — Never Do These

- **Never bypass WRITE_GATE.** No "temporary" skips. No "it's just a small write."
- **Never process L0 tasks.** Flag and route. That's it.
- **Never hold state in memory between cycles.** All state goes to SQLite. Each cycle starts clean.
- **Never put secrets in prompts.** Secrets live in `.env`, encrypted with sops. Never interpolated into LLM context.
- **Never suppress errors silently.** Every error gets logged to audit.db and surfaced. Silent failures are the worst failures.

## Directory Structure

```
/opt/conductor/
├── prompts/                    # LLM prompt templates (read-only at runtime)
│   ├── system.md               # Core system prompt for the Conductor
│   ├── tools.json              # Tool definitions passed to Claude API
│   ├── gemini_verifier.md      # Prompt for Gemini cross-verification
│   └── templates/              # Jinja2 output templates
│       ├── morning_digest.j2   # Daily morning briefing format
│       ├── evening_summary.j2  # End-of-day summary format
│       └── approval_request.j2 # Approval request message format
├── models/                     # Model routing and caching logic
│   ├── router.py               # Model selection logic (Haiku/Sonnet/Gemini)
│   └── cache.py                # Response caching layer
├── schemas/                    # Pydantic schemas for validation
│   ├── cycle_context.py        # Cycle input/output context schema
│   ├── tool_outputs.py         # Expected tool output schemas
│   └── audit.py                # Audit log entry schema
├── data/                       # SQLite databases (gitignored)
│   ├── state.db                # Current operational state
│   ├── audit.db                # Immutable audit trail
│   └── snapshots.db            # Point-in-time context snapshots
├── backups/                    # Database backups (gitignored)
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── .env                        # Actual secrets (gitignored, sops-encrypted)
└── CLAUDE.md                   # This file — project bible
```

## Decision Log

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| 1 | 2026-04-08 | Skeleton scaffolded | Initial directory structure, git repo, requirements.txt, CLAUDE.md created on conductor-tor1. No logic yet — structure only. |


## n8n Code Node Credential Rule

n8n Code nodes MUST read credentials via `$env.VAR_NAME` (n8n's native accessor) — never inline as string literals, and not `process.env.VAR_NAME` (use the n8n-native path).

**Required configuration** (`/opt/n8n/docker-compose.yml`, n8n service `environment:` block):
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`
- Each credential var listed: `- NOTION_TOKEN=${NOTION_TOKEN}`, `- TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}`, `- ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}`

**Env source of truth**: `/opt/conductor/.env` (symlinked to `/opt/n8n/.env`).

**Anti-pattern** (gitleaks pre-commit blocks):
```js
var NOTION_TOKEN = "ntn_xyz...";
const ANTHROPIC_KEY = "sk-ant-api03-...";
```

**Correct**:
```js
var NOTION_TOKEN = $env.NOTION_TOKEN;
var TELEGRAM_TOKEN = $env.TELEGRAM_BOT_TOKEN;   // JS var name can differ from env var name
var ANTHROPIC_KEY = $env.ANTHROPIC_API_KEY;
```

Established: INF-137 (Apr 17, 2026). Applies to all n8n workflow Code nodes.
