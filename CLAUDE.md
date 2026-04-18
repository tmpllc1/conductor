# Conductor — Claude Code Operating Context

**Project:** The Conductor — AI-powered automation platform for Verifund Capital (BCFSA-registered mortgage brokerage), Verivest Growth Fund, Verilytics, and adjacent entities. Regulated-industry workload; operational discipline and audit-trail integrity are non-negotiable.

**Build state:** Phase 2 Stage 2 deployed April 17, 2026. Zero gate blockers currently. Next milestone: Tier 1 A-list items through first live deal via VF-250g, then Phase 3 (coding agents).

---

## Tool priority ladder

Use in order. Only fall back to a lower tier when a higher tier genuinely cannot do the task.

1. **Claude Code via SSH to VPS** — default for all file edits, testing, commits, n8n API calls, systemd ops. SSH target in local config; skip `scp`, build directly on VPS.
2. **n8n REST API** — loopback `http://127.0.0.1:5678/api/v1/` (VPS-local, preferred for SSH/Conductor sessions) or external `https://conductor.verifundcapital.com/api/v1/` (TLS via Caddy). Both enforce `X-N8N-API-KEY`.
3. **MCP tools in claude.ai** (Notion, Gmail, Zoho) — for knowledge queries, task updates, CRM reads during interactive sessions.
4. **Manual action by human** — only for OAuth consent flows, visual UI arrangement, or gate evaluation decisions.

---

## Critical inline rules

These must apply immediately at session start, without waiting for Skill load:

1. **Git discipline:** never `git add` without first running `git diff HEAD <file>`. Never `git add .` or `git add -A` on Conductor. Full protocol in `.claude/skills/conductor-git-discipline/SKILL.md`.
2. **Token hygiene:** never paste full tokens, never `cat`/`tail` on token files. See git-discipline Skill.
3. **Exit-code capture:** use `${PIPESTATUS[0]}`, not piped `$?`. See git-discipline Skill.
4. **Model string:** always `claude-sonnet-4-6` across Conductor code and n8n workflows. Sonnet 4 retires June 15, 2026; degraded availability starts May 14.
5. **PUBLIC repo flag:** `github.com/tmpllc1/conductor` is public. Assume every line of every committed file is publicly visible. Scan every diff before push for secrets, PII, or identifying details.
6. **Docker env refresh:** `.env` changes require `docker compose up -d --force-recreate n8n` from `/opt/n8n/`. `/opt/n8n/.env` is a symlink to `/opt/conductor/.env`.
7. **Prove-before-done:** every change to production state includes a verification line. No "done" without proof. See quality-protocols Skill.

---

## Skills available

Located in `.claude/skills/`:
- **conductor-git-discipline** — git, rotation, verification hygiene. Encodes INF-137, INF-144, and April 17 Telegram lessons.
- **conductor-quality-protocols** — COVE, VF-VERIFY, confidence markers, RAT GATE, inversion gate, sycophancy check, drift check, injection resistance. Mirrors claude.ai User Preferences v7.4.
- **n8n-conductor** — n8n workflow authoring, REST API usage, Code node sandbox patterns, 6-field cron, HTTP body format, idempotency and trace IDs.
- **notion-mcp-conductor** — Notion MCP parent format, update_page shape, nine-database inventory, Task Change Log rule, search-before-create, scouting and archive protocols.
- **conductor-vps-ops** — SSH, systemd, Docker, pytest, Caddy, UFW patterns on conductor-tor1. Includes the two-mechanism env refresh rule (systemd restart vs Docker force-recreate).

Load and apply these when their trigger conditions fire. Do not require explicit user prompting.

---

## Extended thinking defaults

Enable for:
- P1 compliance decisions
- Tripwire evaluations (1-4)
- Phase gate transitions
- Architectural decisions touching >2 systems
- Output where a wrong answer is hard to reverse

Do not enable for routine edits or mechanical refactors.

---

## Model routing guidance (DealSwarm-v2 and future swarms)

- **Extraction agents** (document parsing, field extraction, no reasoning required) → Haiku
- **Reasoning agents** (risk assessment, lender matching, compliance check) → Sonnet (`claude-sonnet-4-6`)
- **Complex synthesis agents** (executive summary, cross-document reconciliation) → Sonnet with extended thinking enabled
- Opus reserved for: gate-evaluation decisions and assumption audits, not swarm runtime

Cost target: <$15 CAD per deal end-to-end. If exceeded, re-evaluate routing before adding hardware or scaling up model tier.

---

## Anti-patterns — never use in production

- **Claude Cowork** — Anthropic explicitly scopes it out of "regulated workloads." Decision #37 retired it for the PM layer.
- **Claude in Chrome beyond non-critical UI** — Decision #19. Never for Zoho CRM, investor comms, or deal processing.
- **Headless Claude Code** — explicit Phase 5 evaluation gate. Do not rely on it for overnight production runs until gate passes.
- **Inline token literals in shell commands** — always via `sh -c '... $(cat /tmp/tok)'` pattern. See git-discipline Skill.
- **`git add .` or `git add -A`** — always narrow-stage with `git add -p` or explicit paths.
- **Direct-to-prod prompt edits** — once staging env (Tier 1 #7) lands, all prompt changes promote through staging. Until then, flag every prod-direct edit as a conscious deviation from the standard.

---

## Current phase gates

Block first live deal through VF-250g until these close:

1. **Tier 1 #2** — error workflows + silence detector on all production workflows
2. **Tier 1 #3** — DR rehearsal completed (snapshot → scratch droplet → restore <60 min)
3. **Tier 1 #4** — `RiskRating` Pydantic↔prompt enum drift resolved (INF-108)
4. **Tier 1 #5** — AI-Assisted Underwriting Operational Protocol v0.1 drafted
5. **Tier 1 #6** — hard monthly cost cap in Anthropic Console + per-node `max_tokens` and retry caps
6. **Tier 1 #7** — minimal staging environment operational

Full A-grade priority list: `Conductor_A_Grade_Priority_List_20260417.md` (in claude.ai project knowledge).

---

## Tripwire state

- **Tripwire 1 (deal accuracy):** 0/20 deals processed. Target: 90%+ numerical, 85%+ risk classification.
- **Tripwire 2 (code quality):** N/A until Phase 3 TenantCase MVP.
- **Tripwire 3 (PM coordination):** EE workflows in production since April 14. Evaluate at 28-day mark.
- **Tripwire 4 (cost control):** Track monthly; base target <$600 CAD, ceiling $750 CAD.

---

## Key paths on VPS

- `/opt/conductor/` — main codebase, `pyproject.toml`, prompts (target: INF-105 externalization)
- `/opt/conductor/.env` — credentials (symlinked from `/opt/n8n/.env`)
- `/opt/n8n/docker-compose.yml` — n8n + Caddy stack
- `/opt/n8n/n8n_data/database.sqlite` — n8n workflow storage
- `/opt/conductor/.claude/skills/` — Skill definitions

---

## Key external references

- Notion Master Tasks DB: `2e31cd933f028044a7e4c708809105dd`
- Notion Extended Memory: `2ee1cd933f02818394cdd2c5cb8c21bf`
- Notion Decisions DB: `c0778c5d-bd7d-44fa-aa7a-87fdb191709c`
- Notion Project Archives: `9f29fe05-1817-41dc-995b-127454cac58a`
- GitHub: `github.com/tmpllc1/conductor` (PUBLIC)
- VPS: conductor-tor1, DigitalOcean Toronto region

---

*Phase A of Wave 1 — minimal CLAUDE.md. To be enriched in Phase B with the full Skill set (n8n-conductor, notion-mcp-conductor, conductor-vps-ops).*
