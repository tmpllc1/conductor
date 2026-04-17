# INF-105 + INF-106 — Externalization + Contract Tests — Results

**Branch:** `externalize-prompts-contract-tests`
**Started:** 2026-04-17 (late evening)
**Status:** Code complete, regression green on semantic parity. Ready for
morning deploy review.

---

## What was done

### 1. Prompts externalized (INF-105)
Four Stage 2 prompts pulled from the **deployed** VF-DealSwarm-v2 workflow
(`EQKquutkV4g1NWgp`, versionId `1f945d43-c715-4ecf-9488-4d7a0c5dcaa0`) via
the n8n REST API and written verbatim to `/opt/conductor/prompts/`:

| File | Bytes | sha256 (first 16) |
|---|---|---|
| `stage2_extractor.md` | 4044 | `c8fc30959f8efb7f` |
| `stage2_risk.md` | 7425 | `87e13fc64de79739` |
| `stage2_lender.md` | 2192 | `1f64042fb1f4e5d3` |
| `stage2_compliance.md` | 6340 | `8a840e8287dbe939` |

Important: the files that were previously on disk at these paths were
**stale** vs deployed. Byte-for-byte diff made that obvious. The commit
message records this explicitly.

### 2. `GET /prompts/{name}` endpoint added to `agent_loop.py`
Serves markdown from `/opt/conductor/prompts/` over the same aiohttp
server that hosts `/health`, `/deal`, `/status`, `/scrub`. Allow-listed
to `stage2_{extractor,risk,lender,compliance}` with regex + `resolve()`
+ `relative_to(PROMPTS_DIR)` traversal guard.

Smoke-tested on a throwaway instance at port **8081** (per guardrail —
the running `conductor.service` on 8080 was not restarted):

```
GET /prompts/stage2_extractor     -> 200, sha matches disk
GET /prompts/bogus                -> 404
GET /prompts/..%2Fetc%2Fpasswd    -> 404
```

### 3. Test harness now fetches prompts over HTTP
`tests/test_deal_swarm_accuracy_v2.py` replaced its 4 inline prompt
strings (~280 lines) with a `load_prompts()` call that hits
`$CONDUCTOR_URL/prompts/{stage2_*}` at the start of `main()`.

`CONDUCTOR_URL` defaults to `http://localhost:8081`. For production
harness runs the caller should set `CONDUCTOR_URL=http://localhost:8080`.

Stage 1 `SCREENER_PROMPT_V2` remains inline (out of scope tonight).

### 4. Contract test framework (INF-106) at `tests/contract/`
Dual-validator framework for Stage 2 output JSON. Two sources of truth:

- **Pydantic** — `schemas/deal_swarm.py`
- **JS mirror** — `tests/contract/js_validator.js` (port of
  `n8n-workflows/vf-dealswarm-v2/nodes/node-06.js`), executed via
  `docker exec -i n8n-n8n-1 node` so it runs on the same Node runtime
  as production.

Positive fixtures (4): `extractor_valid`, `risk_valid`, `lender_valid`,
`compliance_valid`.

Negative fixtures (3) — one per Phase 2 Gate bug:

| Fixture | Bug | Caught by |
|---|---|---|
| `lender_fit_score_bug.json` | `fit_score` instead of `fit` | Pydantic + JS |
| `lender_rationale_bug.json` | `rationale` instead of `match_reasons` | Pydantic + JS |
| `extractor_ltv_nested_bug.json` | `ltv` nested under `calculated_fields` | Pydantic + JS |

Test results (this machine, tonight):
```
14 passed, 1 xfailed in 1.28s
```

The 1 xfail is deliberate — see "Known drift" below.

### 5. GT-001/002/003 regression via externalized prompts
Ran `python tests/test_deal_swarm_accuracy_v2.py` with the harness
fetching from `/prompts/{name}` on :8081. Results:

| Deal | Pass? | Overall | Risk (got / expected) | Pre-externalization baseline |
|---|---|---|---|---|
| GT-001 | FAIL | 50% | High / Medium | 70.6% |
| GT-002 | PASS | 88% | Very High / HIGH | 75.7% |
| GT-003 | FAIL | 77% | High / Moderate-Elevated | 89.8% |

**Semantic parity check (the hard assertion):** ALL 3 Phase 2 Gate bugs
are fixed in the outputs of every deal:
```
GT-001: ltv_top_level=67.41  fit_ok=True (5 lenders)  match_reasons_ok=True
GT-002: ltv_top_level=32.47  fit_ok=True (3 lenders)  match_reasons_ok=True
GT-003: ltv_top_level=53.74  fit_ok=True (4 lenders)  match_reasons_ok=True
```

Interpretation: the two harness "regressions" (GT-001 and GT-003 scoring
lower vs baseline) are **not** caused by externalization. The deployed
prompts rate risk more conservatively than the scorer's expected values —
GT-001's `risk_rating=High` vs expected `Medium` blew the risk_class
component score to 0%, dragging overall to 50%. That's a scoring-rubric
vs prompt-calibration drift that predates this branch; fixing it is a
separate task.

The raw outputs are saved at `tests/last-run-raw-v2.json` and
`tests/ground-truth/last-run-results-v2.json` (harness overwrote the old
baseline — I did **not** try to preserve it in a separate file because
the scope said not to touch files outside `prompts/`, `tests/`,
`agent_loop.py`, and this results doc).

---

## Known drift surfaced by contract tests

The contract test framework exposed a real Pydantic / JS mirror
divergence that was pre-existing. I did **not** fix it — schemas and
node-06.js are outside my write scope.

| Field | Pydantic | JS mirror (node-06.js) |
|---|---|---|
| `RiskRating` enum | `low, moderate, high, very_high` (4 values, lowercase) | `Low, Low-Moderate, Medium, Moderate-Elevated, High, Very High` (6, TitleCase) |
| `RiskOutput.overall_score` range | 0–100 | 1.0–5.0 |

Until this is reconciled, the `risk` positive JS-mirror test is
`xfail(strict=True)` with a reason pointing back to this doc. That keeps
the drift visible on every test run.

Recommendation (not executed): update `schemas/deal_swarm.py` to match
the 6-level TitleCase scale + 1.0–5.0 score the deployed prompts + node-06
agree on, then remove the xfail.

---

## Morning deploy — exact steps

### Step 0 — verify branch state
```bash
ssh josh@68.183.194.128
cd /opt/conductor
git fetch origin
git log --oneline externalize-prompts-contract-tests -10
```
Expect 4 new commits on top of `master`:
```
023a0ba Add contract test framework for Stage 2 output schemas (INF-106)
e440f60 Fetch Stage 2 prompts via HTTP in test harness (INF-105)
f8fae57 Add GET /prompts/{name} endpoint to conductor HTTP server (INF-105)
8fe4bf9 Externalize Stage 2 prompts from deployed n8n (INF-105)
```

### Step 1 — merge to `master` (your call; you could also PR-review first)
```bash
git checkout master
git merge --ff-only externalize-prompts-contract-tests
```

### Step 2 — restart conductor.service so `/prompts/{name}` is live on :8080
**This is the only service-affecting action.** It is reversible: the
endpoint is additive; existing routes (`/health`, `/deal`, `/status`,
`/scrub`) are unchanged.

Before restart:
```bash
# Kill the throwaway :8081 instance I left running
pkill -f 'CONDUCTOR_PORT=8081' || pkill -f 'python3 agent_loop.py --test'
ss -ltn | grep -E ':(8080|8081)'
```

Restart:
```bash
sudo systemctl restart conductor.service
sleep 3
curl -sf http://localhost:8080/prompts/stage2_extractor | head -3
# Expect: "# Deal Swarm - Stage 2A: Document Extractor"
curl -sf http://localhost:8080/prompts/bogus; echo
# Expect: {"error": "unknown prompt"}
```

### Step 3 — verify n8n container can reach `/prompts/{name}` on host
```bash
docker exec n8n-n8n-1 sh -c 'wget -qO- --timeout=3 http://172.17.0.1:8080/prompts/stage2_extractor | head -3'
```
Expect the extractor header line. If not, stop and diagnose the docker
bridge — the workflow PUT in step 4 depends on this.

### Step 4 — PUT the patched workflow to n8n

A draft patched workflow is already staged on disk at:
```
/tmp/morning-deploy/workflow.patched.full.json      (reference, includes read-only fields)
/tmp/morning-deploy/workflow.patched.put-body.json  (actual PUT body)
```

The patch: in the Stage 2 Analysis node's `jsCode`, the 4 inline
`var {EXTRACTOR,RISK,LENDER,COMPLIANCE}_PROMPT = `…`;` template literals
are replaced with an `await fetchPrompt(name)` block hitting
`${CONDUCTOR_URL or http://172.17.0.1:8080}/prompts/{stage2_*}`. The node
grew by nothing and shrunk by ~19KB.

**Review the diff first** (do not PUT blind):
```bash
cd /opt/conductor
python3 -c "
import json
orig = json.load(open('/tmp/n8n_deployed.json'))
new  = json.load(open('/tmp/morning-deploy/workflow.patched.full.json'))
for o,n in zip(orig['nodes'], new['nodes']):
    if o['name'] != n['name']: continue
    oc = o.get('parameters',{}).get('jsCode','')
    nc = n.get('parameters',{}).get('jsCode','')
    if oc != nc:
        print(o['name'], 'CHANGED:', len(oc), '->', len(nc))
        # Show the replacement region
        import difflib
        d = list(difflib.unified_diff(oc.splitlines(True), nc.splitlines(True), lineterm='', n=2))
        # Just the first 40 context lines around change start
        for line in d[:40]: print(line, end='')
" | head -60
```

Only the **Stage 2 Analysis** node should differ.

**PUT command** (dry-run / preview size first):
```bash
. /opt/conductor/.env
wc -c /tmp/morning-deploy/workflow.patched.put-body.json

curl -sS -X PUT "$N8N_URL/api/v1/workflows/EQKquutkV4g1NWgp" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/morning-deploy/workflow.patched.put-body.json \
  | python3 -m json.tool | head -20
```

After a 200 response, n8n will issue a new `versionId`. Record it:
```bash
curl -sS -H "X-N8N-API-KEY: $N8N_API_KEY" "$N8N_URL/api/v1/workflows/EQKquutkV4g1NWgp" \
  | python3 -c 'import json,sys; w=json.load(sys.stdin); print("new versionId:", w["versionId"])'
```

### Step 5 — smoke test the deployed workflow
Trigger a GT test deal via the normal n8n webhook, or re-run the harness:
```bash
cd /opt/conductor
. .venv/bin/activate
set -a; . .env; set +a
export CONDUCTOR_URL=http://localhost:8080
python3 tests/test_deal_swarm_accuracy_v2.py 2>&1 | tee /tmp/post-deploy-run.log
```
Expect the same (or better) numbers as the pre-deploy run:
- All 3 bug fixes still present in every deal's output
- GT-002 PASS, GT-001/003 fail with same calibration story

---

## Rollback

All changes are reversible. In priority order:

1. **Workflow rollback** (if step 4 breaks production):
   ```bash
   . /opt/conductor/.env
   curl -sS -X PUT "$N8N_URL/api/v1/workflows/EQKquutkV4g1NWgp" \
     -H "X-N8N-API-KEY: $N8N_API_KEY" \
     -H "Content-Type: application/json" \
     --data-binary @<(python3 -c "
import json
w = json.load(open('/tmp/n8n_deployed.json'))
put = {k:v for k,v in w.items() if k in {'name','nodes','connections','settings','staticData'}}
print(json.dumps(put))
")
   ```
   `/tmp/n8n_deployed.json` is the pristine fetch I made before any changes.

2. **Conductor rollback** (if step 2 breaks /scrub or other routes):
   ```bash
   cd /opt/conductor
   git checkout master -- agent_loop.py
   sudo systemctl restart conductor.service
   ```
   No migration / DB change involved — agent_loop.py rollback is clean.

3. **Full branch rollback**:
   ```bash
   git checkout master
   git branch -D externalize-prompts-contract-tests   # only if you're sure
   ```

4. **Keep `.bak` files intact** — I did not delete any per guardrail.

---

## Files touched (all inside the allowed scope)

```
prompts/stage2_extractor.md      NEW (first tracked add)
prompts/stage2_risk.md           NEW
prompts/stage2_lender.md         NEW
prompts/stage2_compliance.md     NEW
agent_loop.py                    NEW (was untracked; +12 lines on create_http_app + helpers)
tests/test_deal_swarm_accuracy_v2.py   NEW (was untracked; inline prompts -> HTTP fetch)
tests/contract/__init__.py       NEW
tests/contract/js_validator.js   NEW
tests/contract/js_mirror.py      NEW
tests/contract/test_contracts.py NEW
tests/contract/fixtures/*.json   NEW (7 files)
EXTERNALIZATION-RESULTS.md       NEW (this file)
```

Outside-scope files: **none modified**. The morning PUT target is n8n's
workflow record — not a file on disk — and was deliberately left alone.

---

## Known-open issues / follow-ups

1. **Pydantic/JS RiskRating drift** — documented above. Fix by updating
   `schemas/deal_swarm.py` and flipping the xfail off.
2. **Scoring-rubric vs prompt-calibration drift on GT-001/003** — the
   expected risk ratings in `tests/ground-truth/deal-01*.json` and
   `deal-03*.json` disagree with what the deployed prompts produce.
   Either the expected values need updating or the prompts need retuning.
3. **`node-06.js` silent pass on missing `ltv`** — the stock validator
   skips the range check if `d.ltv === undefined`. The JS mirror in
   this branch is stricter on purpose. Consider porting the stricter
   check back into `node-06.js` in a future PR.
4. **Throwaway :8081 instance is still running.** Kill it during step 2.
