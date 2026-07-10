# Corporate KYC Remediation Loop — Placement Presentation

> **Format:** 6 speakers × 1.5 minutes = 9 minutes total
> **Reference Diagram:** The flowchart shown on screen (Triggers → Enrichment → External Fetch → Verification → Audit/Manager → Communication → Manual Review)

---

## Speaker 1 — Problem Statement & Solution Overview

**Focus:** Why this system exists, why multi-agent, why LangGraph

---

Good [morning/afternoon], everyone. We built an **End-to-End Corporate KYC Remediation Loop** — a system that continuously detects stale or incomplete corporate KYC records, enriches them from internal and external sources, decides a remediation path, verifies the result, and either auto-closes the case or escalates it to a human Relationship Manager.

**The Problem:** Corporate KYC records go stale. Beneficial owners change, tax IDs get missed, documents expire, policy versions update. Today, compliance teams manually check each record — it's slow, error-prone, and cases fall through the cracks.

**Our Solution:** An 8-agent pipeline orchestrated by LangGraph that automates this entire workflow.

**Why Multi-Agent, Not a Single Script:**

If you look at our flowchart, there are clear distinct stages — detection, enrichment, external fetch, verification, manager assignment, communication. Each stage has different inputs, different outputs, and different failure modes. A monolithic script would be untestable and unmaintainable. By separating concerns into agents, each one is independently testable, and we can swap or upgrade any stage without touching the others.

**Why LangGraph:**

Our flowchart has conditional edges — for example, after the external fetch, we route to either verification (AUTO/VERIFY) or directly to a manager (HUMAN). It also has a retry loop — verification can route back to external fetch up to 2 times. LangGraph's `StateGraph` gives us exactly this: a directed graph with conditional routing, bounded loops, and shared state — all compiled into a single executable pipeline.

The entry point is `graph/build.py`, where we wire 8 nodes with conditional edges:

```
START → detection → {gap: enrichment, no_gap: END}
enrichment → fetch_external_source
fetch_external_source → {AUTO: verification, VERIFY: verification, HUMAN: manager}
verification → {verified: audit, retry: fetch_external_source, escalate: manager}
manager → communication → rm_queue
rm_queue → {resolved: verification, pending: END}
audit → END
```

This maps directly to the flowchart on screen.

---

## Speaker 2 — Event-Driven Triggers & Idempotency

**Focus:** How events enter the system, why EventBus, why idempotency fingerprinting

---

Looking at the left side of our flowchart, we have **Triggers** — these are the entry points into the system. There are four event types:

| Event | What triggers it | What it does |
|---|---|---|
| `PolicyUpdated` | New KYC policy published | Flags all records on old policy version |
| `DocumentUploaded` | Client uploads a new document | Compares hash against stored hash |
| `ExpiryReminder` | Periodic scan (daily cron) | Flags documents expiring within threshold |
| `ManualReviewCompleted` | RM resolves a pending case | Feeds back corrected values for re-verification |

These are defined as dataclasses in `events/types.py`:

```python
@dataclass
class PolicyUpdated:
    new_policy_version: int

@dataclass
class DocumentUploaded:
    customer_id: str
    document_type: str
    new_hash: str
```

**Architecture Decision — In-Memory EventBus:**

The flowchart shows triggers flowing into the system. We use an in-memory publish-subscribe pattern (`events/bus.py`) — when a `PolicyUpdated` event fires, it publishes to all subscribers, and each subscriber handles its own logic. For this demo, the bus is synchronous and lives for the lifetime of a Streamlit session. In production, you'd swap this for a durable message queue like Kafka or RabbitMQ.

**Architecture Decision — Idempotency Fingerprinting:**

This is critical. What if the same trigger fires twice? Or a user clicks the same button twice? We generate a `run_id` using `SHA256(customer_id + trigger_type + trigger_key)` in `graph/build.py:87-92`:

```python
def make_run_id(customer_id: str, trigger_type: str, trigger_key: str) -> str:
    fingerprint = f"{customer_id}:{trigger_type}:{trigger_key}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
```

Before invoking the graph, we check: does a `gap_events` row with this `run_id` already exist? If yes, skip. This is the **dispatcher-level guard** that prevents duplicate processing. Additionally, every audit write deduplicates on `(run_id, agent, new_state)` — so even if a duplicate trigger slips through, the audit trail won't have duplicate entries.

This gives us **exactly-once semantics** without a distributed transaction system.

---

## Speaker 3 — Detection + Enrichment Pipeline

**Focus:** Why separate detection from enrichment, internal-first strategy

---

Following the flowchart from triggers, we reach the **Enrichment Agent that queries the internal DB**. In our implementation, this is actually two separate agents: **Detection** and **Enrichment**. Here's why.

**Architecture Decision — Separate Detection from Enrichment:**

Detection is a **cheap, fast check** — it reads the existing record and metadata and asks "is there a gap?". It doesn't look up any new data. Enrichment is **expensive** — it queries sources (CRM, Previous KYC, external registries) to fill gaps. By separating them, we avoid running expensive lookups when the record is already clean.

The Detection Agent (`agents/detection.py:65-133`) checks 5 gap types in priority order:

```
1. MISSING_FIELD  — beneficial_owner or tax_id is null
2. STALE_RECORD   — last_checked > 180 days ago
3. EXPIRED_DOC    — document expiry ≤ today + threshold
4. POLICY_MISMATCH — record policy version < current version
5. CHANGED_DOC    — uploaded doc hash ≠ stored hash
```

If no gap → graph ends immediately. If gap found → proceeds to Enrichment.

**Architecture Decision — Internal-First Enrichment:**

The Enrichment Agent (`agents/enrichment.py:20-23`) queries **only internal sources** first:

```python
INTERNAL_FIELD_SOURCES = {
    "beneficial_owner": ["crm"],
    "tax_id": ["previous_kyc"],
}
```

Why? Internal sources are:
- **Cheaper** — no API costs, no rate limits
- **Faster** — in-memory dict lookups, not network calls
- **No customer contact** — we don't want to bother the customer if we already have the answer on file

If internal enrichment resolves everything with high confidence, the case can be auto-closed without ever touching an external registry or contacting the client. Only unresolved fields get passed to the Fetch External Source Agent.

This two-pass design — cheap internal first, expensive external second — is a common pattern in production data pipelines.

---

## Speaker 4 — Decision Engine & Verification Retry Loop

**Focus:** 3-tier decision routing, bounded retry loop

---

After enrichment, we reach the **Fetch External Source Agent** in the flowchart. This agent tries external registries for whatever internal enrichment couldn't resolve, then makes the critical decision.

**Architecture Decision — 3-Tier Decision (AUTO / VERIFY / HUMAN):**

Looking at `agents/fetch_external_source.py:87-108`:

```python
if config.get_automation_paused():
    outcome = "HUMAN"     # safety control
elif result["sources_unavailable"] and not result["internally_resolvable"]:
    outcome = "HUMAN"     # graceful degradation
elif not result["internally_resolvable"]:
    outcome = "HUMAN"     # insufficient evidence
elif confidence > threshold:
    outcome = "AUTO"      # close automatically
else:
    outcome = "VERIFY"    # resolved but low confidence
```

This is better than a simple pass/fail because:
- **AUTO** — high confidence, close the case without human intervention (saves RM time)
- **VERIFY** — resolved but confidence is borderline, so we verify against an official source before closing
- **HUMAN** — not resolvable automatically, escalate to a manager

**Architecture Decision — Bounded Verification Retry Loop:**

The flowchart shows a feedback loop from Verification back to External Fetch. In `agents/verification.py:24-66`:

```python
MAX_RETRIES = 2

if verified:
    state["status"] = "RUNNING"           # proceed to audit
elif attempt < MAX_RETRIES:
    state["retry_count"] = attempt + 1
    state["status"] = "RETRYING"          # route back to fetch_external_source
else:
    state["status"] = "ESCALATING"        # retries exhausted, escalate to manager
```

When verification finds a mismatch, it doesn't immediately escalate. It routes back to the Fetch External Source Agent, which **skips already-tried sources** and tries the next-best one. This gives the system 3 chances to auto-resolve before escalating to a human.

Each attempt is logged distinctly: `VERIFICATION_FAILED_RETRY1`, `_RETRY2`, `_ESCALATED` — so the retry loop is fully visible in the audit trail. This is important for debugging and compliance.

---

## Speaker 5 — Human-in-the-Loop (Manager, Communication, RM Queue)

**Focus:** Two-phase re-invoke, AI never contacts client, RM assignment by specialty

---

When a case routes to HUMAN, it flows through three agents in the flowchart: **Manager → Communication → Manual Review**.

**Architecture Decision — Manager Assigns by Specialty:**

The Manager Agent (`agents/manager.py:42-63`) selects an RM based on the gap type:

```python
from mock_data.managers import assign_manager
manager = assign_manager(gap["gap_type"], gap["details"].get("missing_fields"))
```

- Missing `beneficial_owner` → Ownership specialist
- Missing `tax_id` → Tax & Compliance specialist
- Document issues → Documentation specialist

It also generates a case summary via the pluggable LLM client (`services/llm_client.py`). The LLM is **optional** — if no API key is configured, it falls back to a deterministic template. This means the demo always works offline, but a real model can be swapped in with one environment variable.

**Architecture Decision — AI Never Contacts the Client:**

The Communication Agent (`agents/communication.py:43-58`) drafts a client-facing message, but it's **only a draft**. The RM reviews it, modifies if needed, and sends it themselves. This is a deliberate safety decision — the AI prepares the work, but a human makes the final client contact.

**Architecture Decision — Two-Phase Re-Invoke:**

The flowchart shows Manual Review flowing back to Verification. In `agents/human_review.py:51-100`, when the RM resolves the case:

1. `resolve_human_review()` updates the enrichment with customer-provided values
2. The full state dict is cached in `st.session_state`
3. The graph re-invokes from `START`
4. Every node is **idempotent** — dedup on `run_id` means detection/enrichment/fast-forward
5. Case proceeds to Verification → Audit → Complete

We chose this **two-phase re-invoke** over LangGraph's native `interrupt()` + checkpointer because it's simpler to build and debug. The tradeoff is that we cache the full state in Streamlit's session state, which works for a single-session demo but wouldn't scale to production — there you'd use a durable checkpointer.

---

## Speaker 6 — Data Architecture, Audit, Guardrails & Production Readiness

**Focus:** SQLite schema, audit trail, PII redaction, input validation, production readiness

---

The flowchart shows **Metadata Table**, **Internal DB**, and **Update the Metadata**. Let me walk through the data architecture, then cover how we protect sensitive data.

**SQLite Schema — 6 Tables:**

```sql
corporate_records   — company_name, beneficial_owner, tax_id, policy_version
metadata            — last_checked, document_hash, document_expiry, verification_status
gap_events          — gap_type, details, status, run_id
decisions           — outcome, confidence, reason
audits              — timestamp, agent, decision, evidence, confidence, previous_state, new_state
config              — runtime thresholds (key-value)
```

The `metadata` table is what the flowchart labels as "Metadata Table" — it contains hashes, last checked date, and earliest expiry information. The `corporate_records` table is the "Internal DB" with the actual company data.

**Architecture Decision — Per-Node Inline Audit Writes:**

In `agents/audit.py:24-48`, `log_step()` is a shared helper imported by **every agent**:

```python
def log_step(state, agent, previous_state, new_state, evidence=None, ...):
    audit = {
        "id": str(uuid.uuid4()),
        "customer_id": state["customer_id"],
        "timestamp": datetime.utcnow().isoformat(),
        "agent": agent,
        "previous_state": previous_state,
        "new_state": new_state,
        "run_id": state["run_id"],
    }
    written = db.log_audit(audit)
```

Every action — not just the terminal one — is logged with timestamp, agent, decision, evidence, confidence, and state transition. This satisfies the compliance requirement for a full audit trail.

**Architecture Decision — Idempotent Audit Writes:**

In `services/db.py:259-290`, `log_audit()` deduplicates on `(run_id, agent, new_state)`:

```python
existing = conn.execute(
    "SELECT 1 FROM audits WHERE run_id = ? AND agent = ? AND new_state = ?",
    (audit["run_id"], audit["agent"], audit["new_state"]),
).fetchone()
if existing:
    return False  # duplicate, don't write
```

This means replayed or duplicate triggers don't create duplicate audit entries. The audit trail is clean and accurate.

**Full Replayability:**

`db.replay_audit(customer_id)` returns the complete ordered action log for any customer — every detection, enrichment, fetch, verification, and audit step. This is critical for compliance: you can reconstruct exactly what happened, when, and why.

**Architecture Decision — PII Never Reaches the LLM:**

The Manager and Communication agents use an LLM to generate summaries and drafts. But `beneficial_owner` and `tax_id` are sensitive PII. Our defense-in-depth approach in `services/privacy.py` ensures they never leak:

1. **Redaction guard** (`redact_pii`) — scans all text going TO the LLM and coming BACK from the LLM, replacing any occurrence of `beneficial_owner`/`tax_id` values with `[REDACTED]`
2. **Hardened system prompts** (`build_hardened_system_prompt`) — appends standing rules telling the model never to output or infer PII, and to treat `[CUSTOMER_DATA]`-delimited text as inert data
3. **Applied in `llm_client.complete()`** — every LLM call goes through this, so no call site can forget it

```python
# services/llm_client.py:86-99
system = privacy.build_hardened_system_prompt(system)
system = privacy.redact_pii(system, record)
prompt = privacy.redact_pii(prompt, record)
# ... call LLM ...
return privacy.redact_pii(result, record)
```

The audit trail in SQLite is intentionally **not** redacted — that's the compliance record. The guardrail applies to the LLM/RM-facing surface only.

**Architecture Decision — Input Validation with Hard-Reject:**

User-supplied free text (RM notes, policy version) is validated in `services/validation.py`:

- `validate_rm_notes()` — non-empty, max 2000 chars, strips control characters, and **hard-rejects** prompt injection patterns (ignore-previous-instructions, role overrides, fake XML tags, etc.)
- `validate_policy_version()` — must be integer, strictly greater than current, within +1000 bound

Why hard-reject, not sanitize-and-continue? An RM note is a short factual record — there's no legitimate reason for instruction-override phrasing. A hard block forces a human to re-enter in plain language.

Validation is enforced at **both layers** — UI (`app.py` button handlers) and agent (`agents/human_review.py:62` calls `validate_rm_notes()` independently). This is defense-in-depth: even if a future caller bypasses the UI, the agent still validates.

**Configurable Thresholds:**

The `config` table stores runtime thresholds — confidence threshold (default 0.95), expiry threshold (default 30 days), automation pause toggle — all editable from the Streamlit sidebar without redeploying. This is the "Pause Automation" safety control that forces all cases to HUMAN review.

**Production-Readiness Summary:**

| Concern | MVP Approach | Production Upgrade |
|---|---|---|
| Idempotency | `run_id` fingerprint + dedup | Durable dedup store, exactly-once delivery |
| Reliability | Mocked sources return `None` when down | Circuit breakers, retries with backoff |
| Observability | Console prints + SQLite audits | Structured logs, metrics, traces |
| Auditability | `replay_audit()` in SQLite | Immutable append-only audit store |
| Safety | "Pause Automation" toggle | Per-segment kill switches, staged rollout |
| Security/Privacy | PII redaction + injection validation | RBAC, field-level encryption, access logging |

---

## Summary Slide — Key Architecture Decisions

| Decision | Rationale | Code Reference |
|---|---|---|
| Multi-agent pipeline | Separation of concerns, testability, independent evolution | `graph/build.py` |
| LangGraph StateGraph | Conditional edges, bounded retry loops, shared state | `graph/build.py:42-74` |
| In-memory EventBus | Clean trigger decoupling, production-swappable | `events/bus.py` |
| Idempotency fingerprinting | Exactly-once semantics without distributed transactions | `graph/build.py:87-99` |
| Separate Detection from Enrichment | Cheap check before expensive lookup | `agents/detection.py`, `agents/enrichment.py` |
| Internal-first enrichment | Cheaper, faster, no customer contact | `agents/enrichment.py:20-23` |
| 3-tier decision (AUTO/VERIFY/HUMAN) | Granular routing vs binary pass/fail | `agents/fetch_external_source.py:87-108` |
| Bounded verification retry | Max 2 retries, tries next-best source | `agents/verification.py:24-66` |
| Two-phase re-invoke | Simpler than LangGraph interrupt() for demo | `agents/human_review.py:51-100` |
| AI never contacts client | Safety — AI drafts, RM sends | `agents/communication.py:43-58` |
| Per-node inline audit | Every action logged, not just terminal | `agents/audit.py:24-48` |
| Pluggable LLM with template fallback | Works offline, upgradeable with one env var | `services/llm_client.py` |
| PII redaction on LLM boundary | `beneficial_owner`/`tax_id` never reach the LLM | `services/privacy.py` |
| Hardened system prompts | Anti-injection suffix on every LLM call | `services/privacy.py:22-27` |
| Input validation (hard-reject) | Injection patterns blocked at UI + agent layer | `services/validation.py` |
| Defense-in-depth validation | Both `app.py` and `human_review.py` validate independently | `app.py:181`, `agents/human_review.py:62` |

---

*Total speaking time: ~9 minutes (6 × 1.5 min)*
