---
name: n8n-conductor
description: n8n workflow authoring, REST API usage, and sandbox-specific patterns for the Conductor build. Apply when editing workflows, writing Code nodes, making HTTP calls from workflows, scheduling cron, or interacting with the self-hosted n8n instance. Encodes the sandbox gotchas that have caused HTTP 400s and silent drift throughout Phases 1-2.
---

# n8n Authoring Patterns — Conductor

## When this applies

Fires on: any edit to a production n8n workflow, any new Code node, any HTTP Request node calling Notion/Zoho/Google APIs, any cron schedule, any `.env` variable access from a workflow, or any direct interaction with the n8n REST API.

## Environment facts

- **n8n version:** self-hosted v2.15.0 on Docker + Caddy (SSL via Caddy, not n8n)
- **Deployment:** `/opt/n8n/` with `docker-compose.yml` and SQLite at `/opt/n8n/n8n_data/database.sqlite`
- **Container name:** `n8n-n8n-1`
- **REST API (preferred for SSH/Conductor sessions):** `http://127.0.0.1:5678/api/v1/` (loopback-only)
- **REST API (external):** `https://conductor.verifundcapital.com/api/v1/` (TLS via Caddy)
- **Both endpoints require:** `X-N8N-API-KEY` header (key in `/opt/conductor/.env` as `N8N_API_KEY`)
- **Active workflows:** 13. Known IDs include `VF-250g` (Gmail label-triggered intake), `EQKquutkV4g1NWgp` (DealSwarm-v2 swarm), `HYUc4tKZJvRvLj1G` (output router). `VF-DealIntake-v1` deactivated (label-trigger rebuild pending).

## Workflow authoring — always complete file, never partial

When delivering a workflow change to the user, provide the **complete workflow JSON as a downloadable file**. Never provide partial edits or inline diffs. Reason: n8n workflows must be imported or PUT-replaced atomically; partial edits against an unseen base drift silently.

When modifying an existing workflow via REST API:
1. GET current workflow JSON, save to local file
2. Edit locally
3. Diff against original, confirm scope of change
4. PUT complete new JSON back
5. Verify new `versionId` differs from old

## Workflow naming convention

`[Entity]-[Function]-[Version]` — e.g., `VF-DealSwarm-v2`, `VF-EveningNarrative-v3`. Preserve this on every new workflow. Deprecated versions stay in the instance but get deactivated, not renamed.

## HTTP Request nodes — body format

When calling external APIs (Notion, Zoho, Google) via HTTP Request node:

**Correct:**
```javascript
body: {
  parent: { data_source_id: "..." },
  properties: { ... }
}
```

**Wrong — causes HTTP 400 via double-encoding:**
```javascript
body: JSON.stringify({
  parent: { data_source_id: "..." },
  properties: { ... }
})
```

n8n HTTP Request node serializes the body object itself. Pre-stringifying results in a JSON-encoded string being wrapped in another JSON object, which the receiving API rejects.

## OAuth calls — HTTP Request nodes only, never Code nodes

OAuth-authenticated APIs (Zoho, Google Workspace, Gmail) must go through dedicated HTTP Request nodes with their native credential type. Code nodes **cannot refresh OAuth tokens**. If an OAuth token expires inside a Code node, the call fails silently or with a stale-token error that looks like an auth issue but isn't.

Pattern: HTTP Request node with `googleOAuth2Api` / `zohoOAuth2Api` / `gmailOAuth2Api` credential → pass response into Code node for transformation.

## Code node restrictions (n8n sandbox)

The JavaScript sandbox has specific limits:

- **`fs` module sandboxed.** Cannot read/write files directly. Use `$getWorkflowStaticData('global')` for state persistence across executions.
- **Execute Command nodes unavailable** in this instance configuration.
- **`this.helpers.httpRequest()` only works inline at top level.** Helper functions that wrap it with `.bind(this)` fail in the sandbox — the binding context doesn't survive.
- **`process.env` not available.** Use `$env.VAR_NAME` for environment variable access. Requires `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` in compose environment block, and the specific variable must be whitelisted.

## Template literals and emoji — avoid in Code nodes

Template literals and emoji characters can cause HTTP 400 errors when they appear in strings passed to external APIs. Use plain string concatenation:

**Safe:**
```javascript
const title = "Scout: " + topic + " (" + dateStr + ")";
```

**Risk:**
```javascript
const title = `🔭 Scout: ${topic} (${dateStr})`;
```

The emoji survives most APIs, but combined with template-literal interpolation in certain n8n versions it has triggered malformed-JSON errors. If you need an emoji in the final output, concatenate it as a literal string constant.

## Cron expressions — 6-field format

n8n uses a 6-field cron format with seconds as the first field, not standard 5-field cron.

- `0 0 5 * * *` = 5:00:00 AM daily (NOT 5 minutes past midnight as standard cron would parse)
- `0 30 5 * * *` = 5:30:00 AM daily
- `0 0 */2 * * *` = every 2 hours on the hour

When migrating from standard cron reference material, prepend `0 ` for seconds.

## Notion-specific gotchas from within n8n

1. **`is_empty` filter unreliable via MCP.** Filter server-side returns inconsistent results. Pattern: fetch pages without `is_empty`, filter in JavaScript post-fetch.
2. **Status value `Archived - Obsolete` uses regular hyphen**, not em-dash. Em-dash matches silently fail because Notion stores the regular-hyphen version.
3. **Notion MCP does not update `last_edited_time`.** Any Claude-originated MCP edit must also append to the Task Change Log DB manually, or the change-detection workflow will miss it.

## Workflow error handling — Tier 1 #2 requirement

Every production workflow must have an error workflow trigger attached. Missing error handlers are why the pipeline currently fails silently. Pattern:

1. Create or reuse a dedicated error-handler workflow
2. In each production workflow, set `Error Workflow` to the error handler
3. Add a silence-detector: a scheduled workflow that checks expected-execution signatures (e.g., "morning briefing ran between 5:00-5:10 AM") and alerts on absence

## Idempotency — Tier 4 #32 folded in

On any write to an external system (Notion create, Zoho upsert, Google Drive upload), include an idempotency key derived from stable input:

- **Email-triggered workflows:** use message-ID hash
- **Scheduled workflows:** use `date + workflow-id` hash
- **Manual triggers:** use execution ID

Short-TTL dedup cache in `$getWorkflowStaticData('global')` prevents duplicate writes on re-runs.

## Trace ID propagation — A2 folded in

Generate a `trace_id` at workflow entry (intake, scheduled start, webhook receipt). Propagate through every downstream node. Include in every Claude API call, every external API write, every log line. Format suggestion: `trace_<entity>_<yyyymmdd>_<random8>`. Single search across logs answers "what did workflow X do for trace_id Y" without stitching execution views.

## Model routing in Claude API nodes

For swarm agents calling Claude API:

- **Extraction tasks** (document parsing, field extraction, no reasoning required): model `claude-haiku-4-5-20251001`
- **Reasoning tasks** (risk assessment, lender matching, compliance check): model `claude-sonnet-4-6`
- **Complex synthesis** (executive summaries, cross-document reconciliation): model `claude-sonnet-4-6` with `thinking: {type: "enabled"}` block

**Never use `claude-sonnet-4-20250514`.** Sonnet 4 retires June 15, 2026; degraded availability starts May 14. The Apr 14 migration replaced all instances with `claude-sonnet-4-6`. Regressions to the old string are a deploy error.

## Batch API for non-urgent runs

Anthropic Batch API costs ~50% of standard API with a 24h SLA. Use for:
- GT fixture re-runs (Tier 2 #8)
- Historical deal backfills
- Output quality sampling (A6)
- Any swarm run not triggered by a live inbox event

Do NOT use for: live deal processing through VF-250g, time-sensitive Telegram responses, morning briefing generation.

## Known limitations

- **Google Drive OAuth 403 issue** exists — the Gmail OAuth2 credential lacks `drive.file` scope. Resolution requires manual `googleOAuth2Api` credential creation in n8n UI. Creates an audit-trail gap until resolved.
- **Staging environment not yet built** (Tier 1 #7). Until it lands, every workflow edit is direct-to-prod. Flag every such edit as a conscious deviation.

## Verification before declaring workflow change done

After every PUT/import:
1. Compare old vs new `versionId` — they must differ
2. Trigger one test execution with known-good input
3. Check execution log for errors
4. If error workflow exists, confirm no error alert fired
5. One-line verification — "Verified: PUT returned 200, versionId changed [old] → [new], test execution succeeded"

No "workflow updated" without those four checks.
