# Corporate KYC Remediation Loop — Quick Summary

> One-page cheat sheet for quick understanding.

---

## What Is This?

A **multi-agent system** that continuously detects stale/incomplete corporate KYC records, enriches them from internal and external sources, decides a remediation path, verifies the result, and either auto-closes or escalates to a human Relationship Manager — with a full audit trail.

**Not a document checker. A continuous remediation engine.**

---

## The 8 Agents

| # | Agent | Role | One-Liner |
|---|---|---|---|
| 1 | **Detection** | Gap finder | Checks 5 conditions: missing fields, staleness, expiry, policy mismatch, changed doc |
| 2 | **Enrichment** | Internal lookup | Queries CRM + Previous KYC — cheapest, fastest pass, no customer contact |
| 3 | **Fetch External Source** | External lookup + decision | Tries Corporate Registry, Tax DB, Ownership DB; decides AUTO/VERIFY/HUMAN |
| 4 | **Verification** | Data validation | Compares enriched data against official source; retries up to 2× on mismatch |
| 5 | **Manager** | RM assignment | Selects specialist by gap type; generates case summary via LLM/template |
| 6 | **Communication** | Message drafting | Drafts client follow-up email — AI never contacts client directly |
| 7 | **Human Review** | RM queue | Pauses case until RM resolves; resumes with customer-provided values |
| 8 | **Audit** | Logging + write-back | Logs every action; terminal node writes back to corporate_records/metadata |

---

## Architecture Flow

```
TRIGGER (Policy/Doc/Expiry/Manual)
    │
    ▼
DETECTION ──── no gap ────▶ EXIT
    │ gap
    ▼
ENRICHMENT (internal: CRM, Previous KYC)
    │
    ▼
FETCH EXTERNAL (Corporate Registry, Tax DB, Ownership DB)
    │
    ├── AUTO (conf > 0.95) ──────┐
    ├── VERIFY (conf ≤ 0.95) ────┤
    │                             ▼
    │                      VERIFICATION ◄── retry (max 2×)
    │                       │         │
    │                  verified   mismatch
    │                       │         │
    │                       ▼         ├── retries left → back to FETCH
    │                      AUDIT      └── exhausted → MANAGER
    │                       │
    │                       ▼
    │                   COMPLETE
    │
    └── HUMAN ────────▶ MANAGER (assign RM)
                              │
                              ▼
                     COMMUNICATION (draft message)
                              │
                              ▼
                      HUMAN REVIEW (RM queue)
                              │
                        ┌─────┴─────┐
                     pending      resolved
                        │            │
                      EXIT    re-verify → AUDIT → COMPLETE
```

---

## 10 Key Architecture Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Multi-agent pipeline** | Separation of concerns — each agent is testable, swappable, independently evolving |
| 2 | **LangGraph StateGraph** | Conditional edges, bounded retry loops, shared state — maps directly to our flowchart |
| 3 | **In-memory EventBus** | Clean trigger decoupling; production would swap to Kafka/RabbitMQ |
| 4 | **Idempotency fingerprinting** | `run_id = SHA256(customer_id + trigger_type + trigger_key)` — prevents duplicate processing |
| 5 | **Separate Detection from Enrichment** | Detection is cheap (just checks); Enrichment is expensive (queries sources) |
| 6 | **Internal-first enrichment** | Try CRM/Previous KYC before external APIs — cheaper, faster, no customer contact |
| 7 | **3-tier decision (AUTO/VERIFY/HUMAN)** | Granular routing — not binary pass/fail; saves RM time on high-confidence cases |
| 8 | **Bounded verification retry** | Max 2 retries, skips already-tried sources — prevents infinite loops |
| 9 | **Two-phase re-invoke** | Simpler than LangGraph `interrupt()` — full state cached in Streamlit session |
| 10 | **AI never contacts client** | Safety — AI drafts messages, RM reviews and sends |

---

## Defense-in-Depth Guardrails

| Layer | What It Does | Code |
|---|---|---|
| **PII Redaction** | Scans all LLM-bound text for `beneficial_owner`/`tax_id`, replaces with `[REDACTED]` | `services/privacy.py` |
| **Hardened System Prompts** | Appends anti-injection suffix — model never outputs PII, treats customer data as inert | `services/privacy.py:22-27` |
| **Input Validation** | Hard-rejects injection patterns in RM notes + policy version bounds check | `services/validation.py` |
| **Defense-in-Depth** | Validation at both UI layer (`app.py`) AND agent layer (`human_review.py`) | `app.py:181`, `human_review.py:62` |

---

## Data Model (SQLite)

| Table | Purpose | Key Columns |
|---|---|---|
| `corporate_records` | Company master data | id, company_name, beneficial_owner, tax_id, policy_version |
| `metadata` | Tracking/audit metadata | customer_id, last_checked, document_hash, document_expiry |
| `gap_events` | Detected gaps | id, customer_id, gap_type, status, run_id |
| `decisions` | AUTO/VERIFY/HUMAN outcomes | id, customer_id, outcome, confidence |
| `audits` | Full action log | id, customer_id, agent, decision, evidence, timestamp |
| `config` | Runtime thresholds | key, value (confidence, expiry, automation_paused) |

---

## Tech Stack

| Component | Technology |
|---|---|
| UI | Streamlit |
| Orchestration | LangGraph (StateGraph) |
| Data Models | Pydantic v2 |
| Persistence | SQLite |
| LLM (optional) | Anthropic / OpenAI (pluggable, template fallback) |
| Language | Python 3.13 |

---

## 10 Mock Companies

| Company | Scenario |
|---|---|
| Acme Holdings Ltd | Clean baseline — no gap |
| Beacon Trading Corp | Missing beneficial owner → resolved externally → AUTO |
| Continental Freight Inc | Expired incorporation cert → AUTO/VERIFY |
| Delta Textiles Pte | Tax ID mismatch → bounded retries → escalation |
| Everline Logistics GmbH | Outdated policy version |
| Falcon Resources SA | Missing tax ID, no source → HUMAN directly |
| Granite Partners SA | Missing beneficial owner + both sources down |
| Helix Ventures BV | Document hash change → idempotency demo |
| Ironwood Capital AG | Missing beneficial owner → VERIFY (at threshold) |
| Juniper Global Trust | Stale record → escalation |
