# End-to-End Corporate KYC Remediation Loop

A production-inspired, hackathon-scoped **multi-agent** system that continuously detects
stale/incomplete corporate KYC records, enriches them from mocked internal *and* external
sources, decides a remediation path, verifies the result, auto-closes cases when possible,
and escalates uncertain cases to a manager-selected Relationship Manager (RM) queue — all
with a full replayable audit trail.

This is **not** a document checker. It's a continuous remediation engine: triggers
(policy changes, new document uploads, periodic expiry checks) flow through an in-memory
event bus into a LangGraph-orchestrated 8-agent pipeline, backed by SQLite.

## Quickstart

```bash
pip install -r requirements.txt
python -m migration.seed        # optional — app.py auto-seeds on first run anyway
streamlit run app.py
```

### Optional: enabling a real LLM

The Manager and Communication agents use a pluggable LLM client
(`services/llm_client.py`). By default **no API key is required** — they fall back to
deterministic templates so the demo always runs offline. To use a real model, install
the provider SDK and set env vars before launching:

```bash
pip install anthropic          # or: pip install openai
export LLM_PROVIDER=anthropic  # or: openai
export ANTHROPIC_API_KEY=sk-...  # or: OPENAI_API_KEY=sk-...
streamlit run app.py
```

---

## i. Architecture

```
Triggers (PolicyUpdated | DocumentUploaded | ExpiryReminder)
    │  published on an in-memory EventBus (events/bus.py)
    ▼
Detection Agent  ──gap?──no──▶ Exit
    │ yes
    ▼
Enrichment Agent  (internal sources only: Previous KYC, CRM — avoids any outreach)
    │
    ▼
Fetch External Source Agent  (external registries; decides AUTO | VERIFY | HUMAN)
   /                                              \
AUTO / VERIFY                                    HUMAN
   │                                                │
   ▼                                                ▼
Verification Agent                          Manager Agent (assigns + summarizes RM)
   │ verified        \ not verified                 │
   │                   \  (retry ≤2, else escalate)  ▼
   │                     ╲───────────────▶  Communication Agent (drafts client message)
   ▼                                                │
Audit Agent (Update KYC + Complete)                 ▼
   ▲                                        Human Review (RM queue)
   │                                        (customer follow-up simulated in UI)
   └──────────────── re-verify after resume ────────┘
```

Mapped onto the ingress/orchestration/investigation/decision/egress/async/replay
lifecycle:

| Stage | Implementation |
|---|---|
| Ingress | `events/` — 3 trigger types published on an in-memory pub/sub bus; the migration (`migration/seed.py`) is the one-time batch index of the "existing manual KYC database" into `corporate_records` + `metadata` |
| Orchestration | `graph/build.py` — a LangGraph `StateGraph` with conditional edges and a bounded retry loop (see §iv) |
| Investigation | `agents/detection.py` + `agents/enrichment.py` + `agents/fetch_external_source.py` |
| Decision | `agents/fetch_external_source.py` (AUTO/VERIFY/HUMAN) |
| Egress | SQLite writes (`services/db.py`) + Streamlit dashboard (`app.py`, `ui/`) |
| Async follow-up | `agents/manager.py` + `agents/communication.py` + `agents/human_review.py` — manager assignment, drafted outreach, RM queue pause/resume |
| Replay / feedback | `services/db.py::replay_audit()` + `events.types.ManualReviewCompleted` |

## ii. Agent Catalogue

| Agent | Responsibility | Input | Output | Downstream |
|---|---|---|---|---|
| **Detection** (`agents/detection.py`) | Detect missing fields, stale records, expired documents, policy mismatch, changed documents — checked in that priority order | `trigger_event`, `record`, `metadata` | `gap_event` or `None` | Enrichment (gap) / exit (no gap) |
| **Enrichment** (`agents/enrichment.py`) | Resolve gaps using only sources already **on file** (Previous KYC, CRM) — the cheapest, fastest pass, avoiding any external lookup or customer contact | `gap_event`, `record` | `enrichment_result` = `{updated_fields, confidence, evidence, sources_used, sources_unavailable, internally_resolvable}` | Fetch External Source |
| **Fetch External Source** (`agents/fetch_external_source.py`) | Tries mocked external registries (Corporate Registry, Tax DB, Beneficial Ownership DB) for whatever's still unresolved, then itself decides: `AUTO` (confidence > threshold), `VERIFY` (resolved, ≤ threshold), or `HUMAN` (unresolved / source unavailable / automation paused). Also re-entered on a verification retry, skipping sources already tried | `enrichment_result` | mutated `enrichment_result` + `decision` = `{outcome, confidence, reason}` | Verification (AUTO/VERIFY) / Manager (HUMAN) |
| **Verification** (`agents/verification.py`) | Compare enriched `beneficial_owner`/`tax_id` against a mocked official source. On mismatch, retries up to **2 times** via Fetch External Source before escalating | `enrichment_result` | `verification_result` = `{verified, evidence, reason}` | Audit (verified) / Fetch External Source (retry) / Manager (escalate) |
| **Manager** (`agents/manager.py`) | Chooses the right RM by specialty (ownership / tax & compliance / documentation — see `mock_data/managers.py`) and writes a case summary via the pluggable LLM client (template fallback if no key) | `gap_event`, `enrichment_result`, `decision`/`verification_result` | `assigned_manager`, `manager_summary` | Communication |
| **Communication** (`agents/communication.py`) | Drafts the client-facing follow-up message explaining what's outstanding — for the RM to review and send. **The AI never contacts the customer directly.** | `gap_event`, `manager_summary` | `client_message_draft` | Human Review |
| **Human Review / RM queue** (`agents/human_review.py`) | Simulated RM queue holding the assigned manager + drafted message until the UI "resolves" it (RM sent the message, customer responded) | `assigned_manager`, `client_message_draft` | `human_review` = `{status, rm_notes, resolved_at, assigned_manager, client_message_draft}` | Verification (on resume) |
| **Audit** (`agents/audit.py`) | Logs timestamp/agent/decision/evidence/confidence/previous_state/new_state for **every** action (inline, via `log_step`, called by all agents) and performs the terminal "Update KYC" write-back | every agent's output | `audits` rows in SQLite | Replayable audit trail |

## iii. Working Prototype

`app.py` (Streamlit, single page): sidebar config (confidence/expiry thresholds, pause-automation
safety toggle, reset demo data) → customer table with 🟢/🟡/🔴 status badges → trigger panel
(Policy Update / Document Upload / Expiry Check) → "Run Remediation" with live per-agent cards →
Relationship Manager panel (shows assigned manager + drafted message when a case is pending) →
replayable Audit Trail.

## iv. Decision & Orchestration Workflow

Implemented as a LangGraph `StateGraph` (`graph/build.py`):

```
START -> detection
detection -> {gap: enrichment, no_gap: END}
enrichment -> fetch_external_source
fetch_external_source -> {AUTO: verification, VERIFY: verification, HUMAN: manager}
verification -> {verified: audit, retry: fetch_external_source, escalate: manager}
manager -> communication -> human_review
human_review -> {resolved: verification, pending: END}
audit -> END
```

**Bounded verification retry loop:** on a mismatch, `verification` routes back to
`fetch_external_source` (which skips sources already tried and attempts the next-best
one) up to **2 retries**; the 3rd consecutive failure escalates to `manager`. Each
attempt is logged distinctly (`VERIFICATION_FAILED_RETRY1`, `_RETRY2`,
`_ESCALATED`) so the retry loop is fully visible in the audit trail.

**Human-in-the-loop pause/resume:** rather than LangGraph's native `interrupt()` +
checkpointer (higher setup risk for this build), we use a **two-phase re-invoke**:
phase 1 runs until it naturally lands on `human_review: pending -> END`; the full
state dict is cached (`st.session_state["pending_state"]`). The RM panel's "Simulate
customer response" button mutates `human_review.status = RESOLVED` (and, for a
verification failure, corrects the mismatched field to match the official source —
see `agents/human_review.py::resolve_human_review`) and re-invokes the graph from
`START`. Every node is idempotent (dedup on `run_id` for `gap_events`, dedup on
`(run_id, agent, new_state)` for `audits`), so the replay fast-forwards through
detection/enrichment/fetch_external_source and proceeds to verification → audit → complete.

## v. Sample End-to-End Traces

**Beacon Trading Corp** (missing beneficial owner, resolved externally, auto-closed):
```
detection            : GAP_DETECTED    MISSING_FIELD — missing beneficial_owner
enrichment            : ENRICHED        confidence=0.0 — CRM has no entry, hand off externally
fetch_external_source : DECISION_AUTO   confidence=0.99 via beneficial_ownership_db
verification           : VERIFIED        no official-source conflict
audit                  : COMPLETE        record updated, case closed
```

**Falcon Resources SA** (unresolved gap, needs RM/customer outreach):
```
detection            : GAP_DETECTED    MISSING_FIELD — missing tax_id
enrichment            : ENRICHED        confidence=0.0, nothing on file
fetch_external_source : DECISION_HUMAN  no external source has it either
manager                : MANAGER_ASSIGNED   assigned to Tax & Compliance specialist
communication           : CLIENT_MESSAGE_DRAFTED
human_review            : HUMAN_REVIEW_PENDING  (RM panel appears in UI)
  ... judge clicks "Simulate customer response received" ...
human_review            : HUMAN_REVIEW_RESOLVED
verification            : VERIFIED       customer-provided value accepted
audit                   : COMPLETE
```

**Delta Textiles Pte** (verification failure → bounded retries → escalation):
```
detection             : GAP_DETECTED     MISSING_FIELD — missing tax_id
enrichment             : ENRICHED         nothing on file internally
fetch_external_source  : DECISION_AUTO    confidence=0.96 via tax_database ("TX-DELTA-2044")
verification            : VERIFICATION_FAILED_RETRY1   mismatch vs official source
fetch_external_source   : DECISION_AUTO_RETRY1  (no new source available — same result)
verification            : VERIFICATION_FAILED_RETRY2
fetch_external_source   : DECISION_AUTO_RETRY2
verification            : VERIFICATION_FAILED_ESCALATED   retries exhausted
manager                 : MANAGER_ASSIGNED   assigned to Tax & Compliance specialist
communication            : CLIENT_MESSAGE_DRAFTED
human_review             : HUMAN_REVIEW_PENDING
  ... RM follow-up corrects tax_id to the official value ...
verification             : VERIFIED
audit                    : COMPLETE
```

## vi. Threshold and Escalation Notes

- **Confidence threshold** (default `0.95`, sidebar-configurable): confidence strictly
  greater than this closes `AUTO`; at/below it but still resolvable closes `VERIFY`
  (both proceed to Verification before closing — see §iv).
- **Expiry threshold** (default `30` days, sidebar-configurable): documents expiring
  within this window are flagged `EXPIRED_DOC` and picked up by the Trigger 3 scan.
- **Staleness threshold** (`180` days, constant — see Assumptions): records not
  `last_checked` within this window are flagged `STALE_RECORD`.
- **Verification retry limit** (`2`, constant — `agents/verification.py::MAX_RETRIES`):
  a mismatch is retried twice via Fetch External Source before escalating.
- **Escalation to a Manager happens when:** the official source is unavailable or empty
  for a required field, confidence doesn't clear the bar after internal *and* external
  lookup, a required source is down, automation is paused, or Verification fails 3
  times in a row.

## vii. Production-Readiness Notes

| Concern | MVP approach | Production upgrade |
|---|---|---|
| Idempotency | `run_id = sha256(customer_id, trigger_type, trigger_key)`; dedup on `gap_events.run_id` and `audits.(run_id, agent, new_state)` | Durable dedup store, exactly-once event delivery |
| Reliability | Mocked sources return `None` when "down" (`__down__` flag); agents record `sources_unavailable` and escalate rather than crashing | Circuit breakers, retries with backoff, real SLAs |
| Configurability | `confidence_threshold`, `expiry_threshold_days`, `automation_paused`, `current_policy_version` live in a `config` SQLite table, editable from the sidebar; LLM provider/key are env-driven (`services/llm_client.py`) | Policy-as-config service, versioned rule sets, secrets manager |
| Observability | Every agent action logged to `audits`; console `print` for event-bus activity | Structured logs/metrics/traces, queue depth alerting |
| Auditability | `services/db.py::replay_audit()` returns the full ordered, side-effect-free action log per customer, including every retry attempt | Immutable/append-only audit store, tamper-evidence |
| Safety controls | "Pause Automation" sidebar toggle forces every case to `HUMAN`; bounded retry loop prevents runaway looping | Per-segment kill switches, staged rollout by cohort |
| Security/privacy | Mock data only, no real PII; SQLite file-local; LLM calls only fire when a key is explicitly configured | RBAC, field-level encryption, data minimisation, access logging |
| Feedback loop | `ManualReviewCompleted` event + RM-corrected values feed back into the same case's re-verification | Full case re-open workflow (see Descoped, below) |

## viii. Assumptions and Trade-offs

- **No MCP servers** for the mocked registry/tax/ownership sources — plain mocked
  Python functions (`services/mock_sources.py`) are sufficient for this scope.
- **LLM usage is optional and pluggable** (`services/llm_client.py`): Manager/Communication
  agents call a real model only if `LLM_PROVIDER`+API key env vars are set; otherwise a
  deterministic template keeps the demo fully offline-reliable. A failed LLM call falls
  back to the template rather than breaking the run.
- **Two-phase re-invoke** instead of LangGraph's native `interrupt()`/`SqliteSaver`
  checkpointer, to reduce build-time risk (see §iv).
- **Per-node inline audit writes** (via `agents/audit.py::log_step`) rather than a
  literal single fan-in "Audit Agent" node for every transition — the terminal
  `audit_node` performs only the closing "Update KYC / Complete" write. This still
  satisfies "every action is logged."
- **In-memory event bus**, not durable — acceptable for a single-session demo.
- **Verification only checks `beneficial_owner`/`tax_id`** against the official
  source; other enrichable fields (document hash/expiry, policy version) are
  internal reconciliations with no external ground truth in this MVP.
- **Staleness threshold (180 days) and retry limit (2)** are constants, not
  sidebar-configurable (only confidence/expiry thresholds are, per the spec's ask).
- Single-page ops dashboard, not a customer-facing portal or full case-management UI
  (explicitly out of scope).

### Stretch / Explicitly Descoped

- **Parent/subsidiary cascades** — `parent_entity_id` exists on the data model
  (see Ironwood Capital AG) but no cascade remediation logic is implemented.
- **Partial/ignored outreach state machine** — represented only via the
  `last_remediation` timestamp (see Juniper Global Trust).
- **Case re-open on new evidence** — not implemented. Future work: a `ReOpenCase`
  event transitioning `COMPLETED -> RUNNING` while preserving prior audit history.
- **External observability stack** — console logging + the SQLite `audits` table
  is the full observability story here.

---

## Mock Dataset (10 companies)

| Company | Scenario |
|---|---|
| Acme Holdings Ltd | Clean baseline — no gap |
| Beacon Trading Corp | Missing beneficial owner, resolved externally → AUTO |
| Continental Freight Inc | Expired incorporation cert, otherwise complete → AUTO/VERIFY |
| Delta Textiles Pte | Tax ID resolved but mismatches official source → bounded retries → escalation to a manager |
| Everline Logistics GmbH | Outdated policy version — Trigger 1 target |
| Falcon Resources SA | Missing tax ID, no source has it → Manager/Human Review directly |
| Granite Partners SA | Beneficial owner missing AND both relevant sources are down → reliability/graceful-degradation demo |
| Helix Ventures BV | Document hash change → idempotency/duplicate-trigger demo |
| Ironwood Capital AG | Missing beneficial owner, resolved at exactly the confidence threshold → `VERIFY` outcome; `parent_entity_id` stretch field |
| Juniper Global Trust | Stale record (last checked years ago) + old `last_remediation` → escalation |

## Demo Script (~5 minutes)

1. **Reset Demo Data** (sidebar) for a clean slate.
2. Select **Beacon Trading Corp** → Run Remediation → AUTO path end-to-end, 🟢.
3. Select **Falcon Resources SA** → Run Remediation → routes to Manager/RM queue →
   click "Simulate customer response received" → resumes → verified → complete.
4. Select **Delta Textiles Pte** → Run Remediation → watch the retry loop
   (`VERIFICATION_FAILED_RETRY1/2`) play out card-by-card → escalates (🔴) →
   resolve via RM panel → re-verifies → complete.
5. Select **Granite Partners SA** → Run Remediation → enrichment reports both
   external sources unavailable → Manager/Human Review (reliability path).
6. Select **Helix Ventures BV** → Trigger 2 "Simulate Upload" → click it again →
   second click shows "Duplicate trigger ignored" (idempotency).
7. Trigger 1 "Publish PolicyUpdated" → **Everline Logistics GmbH** (and any other
   stale-policy company) gets remediated in one fan-out.
8. Expand **Audit Trail** on any processed company → full replayable, timestamped log.

## Project Structure

```
app.py                          Streamlit entrypoint
models/                         Pydantic entities + LangGraph state
events/                         In-memory pub/sub bus + event dataclasses
agents/
  detection.py                    Detection Agent
  enrichment.py                   Enrichment Agent (internal sources)
  fetch_external_source.py        Fetch External Source Agent (+ AUTO/VERIFY/HUMAN decision)
  verification.py                 Verification Agent (+ bounded retry loop)
  manager.py                      Manager Agent (RM selection + LLM/template summary)
  communication.py                Communication Agent (drafted client message)
  human_review.py                 RM queue pause/resume
  audit.py                        Audit Agent + shared log_step() helper
  routing.py                      Conditional-edge predicates
graph/build.py                   LangGraph StateGraph wiring + dispatcher helpers
services/
  db.py                            SQLite persistence
  mock_sources.py                  Mocked internal/external sources + official verification source
  config.py                        Configurable thresholds
  llm_client.py                    Pluggable LLM client (env-driven, template fallback)
mock_data/
  companies.py                     10 seed companies
  managers.py                      Mock RM directory + assignment rule
migration/seed.py                Idempotent batch migration / demo reset
ui/components.py                 Status badges, agent cards, timeline
```
