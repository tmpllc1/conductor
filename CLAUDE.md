# The Conductor — AI Operating System
## Updated: April 12, 2026

## What This Is

AI project management system for Josh Liberman, a solo mortgage broker managing 8 business entities (Verifund Capital Corp, Verivest Growth Fund, Verilytics, SBF, Tailored Trader, and more). n8n workflows (Docker, same VPS) handle scheduling, monitoring, and integrations (Notion, Zoho, Google Workspace, Telegram). Python code on this VPS handles deal analysis schemas, PII scrubbing, model routing, and validation. The Conductor reads context from Notion, decides actions, and routes execution through n8n.

## Directory Structure

```
/opt/conductor/
├── .env                        # API keys and config (chmod 600)
├── .env.example                # Template for .env
├── __init__.py                 # Package marker (v0.1.0)
├── backup.sh                   # Weekly backup script (cron active)
├── setup_dirs.sh               # Directory setup script
├── CLAUDE.md                   # This file
├── requirements.txt            # Python dependencies
├── config.py                   # .env loader + typed config constants
├── sandbox.py                  # TEST_MODE routing + guard_write protection
├── pii_scrubber.py             # PII redaction (SIN, bank#, DOB, DL#)
├── test_pii_scrubber.py        # 27 tests for PII scrubber
├── model_router.py             # LiteLLM wrapper for model selection
├── models/
│   ├── router.py               # Model selection logic (Fast/Balanced/Verify)
│   └── cache.py                # Response caching layer
├── schemas/
│   ├── deal_swarm.py           # Deal analysis pipeline schemas (32 tests)
│   ├── audit.py                # Audit log entry schema
│   ├── cycle_context.py        # Cycle input/output context schema
│   └── tool_outputs.py         # Expected tool output schemas
├── prompts/
│   ├── system.md               # Core system prompt
│   ├── tools.json              # Tool definitions for Claude API
│   ├── gemini_verifier.md      # Gemini cross-verification prompt
│   ├── stage1_screener.md      # Deal swarm Stage 1: initial screening
│   ├── stage2_extractor.md     # Deal swarm Stage 2: data extraction
│   ├── stage2_risk.md          # Deal swarm Stage 2: risk assessment
│   ├── stage2_lender.md        # Deal swarm Stage 2: lender matching
│   ├── stage2_compliance.md    # Deal swarm Stage 2: compliance check
│   └── templates/              # Jinja2 output templates
├── templates/
│   └── CLAUDE.md.template      # Dual-audience CLAUDE.md template
├── claude-md/                  # Project-specific CLAUDE.md files
├── n8n-workflows/              # n8n Code node scripts (reference only)
├── tests/
│   ├── test_deal_swarm_schemas.py  # 32 schema validation tests
│   ├── test_config.py          # 7 config loading tests
│   └── test_sandbox.py         # 14 sandbox routing tests
├── data/                       # SQLite databases (gitignored)
├── backups/                    # Database backups (gitignored)
└── logs/                       # Application logs (gitignored)
```

## Key Files

| File | Purpose |
|------|---------|
| config.py | Loads .env, exports typed constants (API keys, DB IDs, TEST_MODE flag) |
| sandbox.py | Routes API calls to test/production targets based on TEST_MODE. guard_write() blocks production writes in test mode |
| pii_scrubber.py | Regex-based PII redaction for mortgage documents. Strips SIN, bank account#, DOB, DL#. Preserves names, addresses, income, credit scores, LTV |
| model_router.py | LiteLLM wrapper for routing to Fast (Gemini Flash Lite), Balanced (Claude Sonnet), or Verify (Gemini Flash) tiers |
| models/router.py | Model selection logic based on task complexity |
| models/cache.py | Response caching layer |

## Schemas

schemas/deal_swarm.py contains Pydantic models for the 5-stage deal analysis pipeline:
- ScreenerOutput — Stage 1: proceed/reject verdict with document inventory
- ExtractorOutput — Stage 2: borrower info, property info, loan details extraction
- RiskOutput — Stage 2: risk rating, factors, overall score (0-100)
- LenderOutput — Stage 2: ranked lender matches with fit scores
- ComplianceOutput — Stage 2: BCFSA/FINTRAC compliance checks
- DealSwarmResult — Aggregates all stages with validation (proceed requires all Stage 2 outputs)

Also: audit.py (audit log entries), cycle_context.py (cycle I/O), tool_outputs.py (tool output schemas).

## Prompts

| File | Deal Swarm Stage | Purpose |
|------|-----------------|---------|
| stage1_screener.md | Stage 1 | Initial deal screening — proceed or reject |
| stage2_extractor.md | Stage 2 | Extract structured data from deal documents |
| stage2_risk.md | Stage 2 | Assess risk factors and overall risk score |
| stage2_lender.md | Stage 2 | Match deal to lenders by fit criteria |
| stage2_compliance.md | Stage 2 | Check BCFSA and FINTRAC compliance |
| gemini_verifier.md | Verification | Cross-model verification for deal decisions |
| system.md | — | Core system prompt for the Conductor agent |
| tools.json | — | Tool definitions passed to Claude API |

## n8n Workflows

10 active workflows running in Docker on this VPS (managed separately under /opt/n8n/):

| # | Workflow | Trigger | Purpose |
|---|----------|---------|---------|
| 1 | Morning Briefing v3 | Daily 5:00 AM | Aggregates overnight changes, sends daily summary to Telegram |
| 2 | Change Detection v3 | Every 5 min | Monitors Notion for task/deal changes, triggers state updates |
| 3 | Rollover Handler v3 | Daily 5:30 PM | Rolls incomplete tasks to next day |
| 4 | Evening Narrative v3 | Daily 5:35 PM | Sends end-of-day summary to Telegram |
| 5 | Stale Sweep v2 | Monday 8:00 AM | Flags tasks with no activity for 7+ days |
| 6 | Cross-Entity Balance v3 | Friday 4:30 PM | Reconciles task counts across all 8 entities |
| 7 | Callback Handler v2 | Webhook /telegram-callback | Processes Telegram callback button responses |
| 8 | Watch Conditions v1 | Monday 6:00 AM | Evaluates watch conditions on monitored items |
| 9 | INFRA-CalendarSync-v1 | Every 15 min | Syncs Google Calendar events to Notion |
| 10 | State Manager | Sub-workflow (concurrency=1) | Central state machine — called by other workflows |

## Testing

```bash
source /opt/conductor/.venv/bin/activate
cd /opt/conductor
python -m pytest -v
```

Current test count: 80 passing (as of April 12, 2026)

| Test File | Tests | Covers |
|-----------|-------|--------|
| test_pii_scrubber.py | 27 | SIN, bank account, DOB, DL scrubbing + false positive prevention |
| tests/test_deal_swarm_schemas.py | 32 | All deal swarm Pydantic schemas, validation rules, edge cases |
| tests/test_config.py | 7 | .env loading, TEST_MODE parsing, typed constant exports |
| tests/test_sandbox.py | 14 | Notion/Zoho/Telegram routing, guard_write, is_production |

## Development Rules

- Always activate venv: source /opt/conductor/.venv/bin/activate
- Always run pytest after changes
- Never call external APIs from test code
- TEST_MODE must be True for any local development
- Never modify anything under /opt/n8n/ — that is the n8n Docker setup
- Never restart Docker containers from this codebase
- PII scrubber strips: SIN, bank account#, DOB, DL#
- PII scrubber preserves: names, addresses, income, credit scores, LTV (needed for underwriting)
- All secrets in .env — never hardcode API keys in code

## Credentials

All stored in .env (never committed, chmod 600):

| Key | Service | Notes |
|-----|---------|-------|
| ANTHROPIC_API_KEY | Claude API | Balanced tier |
| NOTION_TOKEN | Notion API | Integration token |
| TELEGRAM_BOT_TOKEN | Telegram Bot API | @verifund_conductor_bot |
| TELEGRAM_CHAT_ID | Telegram | Josh chat ID |
| GEMINI_API_KEY | Google Gemini | Blank — not yet configured |
| TEST_MODE | Conductor | false in production, true for development |

Additional Notion IDs, LiteLLM model strings, SQLite paths, and n8n URLs also in .env.


## n8n Code Node Credential Rule

n8n Code nodes MUST reference environment variables via `$env.VAR_NAME` (n8n's native accessor), never inline credentials as string literals. `$env.` is preferred over `process.env.` in n8n Code nodes — it is n8n's documented pattern and already in use across live workflows.

**Required configuration** (`/opt/n8n/docker-compose.yml`, n8n service `environment:` block):
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`
- Each credential var listed: e.g. `- NOTION_TOKEN=${NOTION_TOKEN}`, `- TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}`, `- ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}`

**Source of truth**: `/opt/conductor/.env` (symlinked to `/opt/n8n/.env`).

**Anti-pattern** (gitleaks pre-commit blocks):
```js
var NOTION_TOKEN = "ntn_xyz...";
var ANTHROPIC_KEY = "sk-ant-api03-...";
```

**Correct**:
```js
var NOTION_TOKEN = $env.NOTION_TOKEN;
var TELEGRAM_TOKEN = $env.TELEGRAM_BOT_TOKEN;  // JS var name can differ from env var name
var ANTHROPIC_KEY = $env.ANTHROPIC_API_KEY;
```

Established: INF-137 (Apr 17, 2026). Applies to all n8n workflow Code nodes.

## What Is Next

Phase 2 (Target: April 21, 2026): Deal analysis swarm — n8n intake workflow triggers Claude API agents, output routes to Zoho CRM + Notion. Schemas and prompts are ready. Remaining work:
- n8n deal intake workflow (webhook receives deal docs, triggers orchestrator)
- Agent orchestrator Python code (runs 5-stage swarm, aggregates results)
- Output routing (writes deal verdicts to Zoho CRM pipeline + Notion deal board)
