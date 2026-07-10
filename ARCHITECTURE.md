# Corporate KYC Remediation Loop — Architecture

> Detailed architecture with Mermaid diagrams. Each diagram maps to the presentation flowchart.

---

## 1. Main System Flow

This is the primary remediation pipeline — maps directly to the flowchart on screen.

```mermaid
flowchart TD
    subgraph TRIGGERS["<b>TRIGGER LAYER</b>"]
        T1[PolicyUpdated]
        T2[DocumentUploaded]
        T3[ExpiryReminder]
        T4[ManualReviewCompleted]
    end

    subgraph BUS["<b>EventBus</b><br/>events/bus.py"]
        EB[In-memory pub/sub]
    end

    subgraph ORCHESTRATION["<b>ORCHESTRATION</b><br/>graph/build.py — LangGraph StateGraph"]
        DET{Detection Agent}
        ENR[Enrichment Agent<br/>Internal sources only]
        EXT[Fetch External Source Agent<br/>+ AUTO/VERIFY/HUMAN decision]
        VER{Verification Agent<br/>max 2 retries}
        MAN[Manager Agent<br/>assign RM by specialty]
        COM[Communication Agent<br/>draft client message]
        HR[Human Review<br/>RM queue pause/resume]
        AUD[Audit Agent<br/>log + write-back]
    end

    subgraph PERSISTENCE["<b>PERSISTENCE</b><br/>services/db.py — SQLite"]
        DB[(SQLite<br/>6 tables)]
    end

    subgraph GUARDRAILS["<b>DEFENSE-IN-DEPTH</b>"]
        PRIV[PII Redaction<br/>services/privacy.py]
        VAL[Input Validation<br/>services/validation.py]
    end

    T1 & T2 & T3 --> EB
    EB --> DET

    DET -->|gap found| ENR
    DET -->|no gap| EXIT1([EXIT])

    ENR --> EXT

    EXT -->|AUTO| VER
    EXT -->|VERIFY| VER
    EXT -->|HUMAN| MAN

    VER -->|verified| AUD
    VER -->|mismatch + retries left| EXT
    VER -->|retries exhausted| MAN

    MAN --> COM --> HR

    HR -->|resolved| VER
    HR -->|pending| EXIT2([EXIT])

    AUD --> EXIT3([END])

    AUD -.-> DB
    DET -.-> DB
    EXT -.-> DB

    COM -.-> PRIV
    MAN -.-> PRIV
    HR -.-> VAL
    EB -.-> VAL

    style TRIGGERS fill:#e8f5e9,stroke:#4caf50
    style ORCHESTRATION fill:#e3f2fd,stroke:#2196f3
    style PERSISTENCE fill:#fff3e0,stroke:#ff9800
    style GUARDRAILS fill:#fce4ec,stroke:#e91e64
    style BUS fill:#f3e5f5,stroke:#9c27b0
```

**How it works:**
- Triggers enter via the EventBus and invoke the LangGraph pipeline
- Detection checks for gaps; if none, the graph exits immediately
- Enrichment queries internal sources (CRM, Previous KYC) — cheap and fast
- Fetch External Source tries external registries, then decides AUTO/VERIFY/HUMAN
- Verification checks enriched data against an official source; retries up to 2×
- Manager assigns an RM and drafts a case summary
- Communication drafts a client message — AI never contacts client directly
- Human Review pauses the case; RM resolves it; graph resumes
- Audit logs every action and writes back to corporate_records/metadata
- PII redaction and input validation guard the LLM boundary and user inputs

---

## 2. Decision Routing

After enrichment, the system decides how to proceed based on confidence and source availability.

```mermaid
flowchart TD
    EXT[Fetch External Source Agent] --> DEC{Decision Logic}

    DEC -->|automation paused| HUMAN[HUMAN]
    DEC -->|sources down + not resolvable| HUMAN
    DEC -->|insufficient evidence| HUMAN
    DEC -->|confidence > 0.95| AUTO[AUTO]
    DEC -->|confidence ≤ 0.95| VERIFY[VERIFY]

    AUTO --> VER[Verification Agent]
    VERIFY --> VER

    VER -->|verified| AUD[Audit → Complete]
    VER -->|mismatch| RETRY{Retries left?}
    RETRY -->|yes| EXT
    RETRY -->|no| HUMAN

    HUMAN --> MAN[Manager Agent]

    style AUTO fill:#c8e6c9,stroke:#4caf50
    style VERIFY fill:#fff9c4,stroke:#fbc02d
    style HUMAN fill:#ffcdd2,stroke:#e53935
    style VER fill:#e3f2fd,stroke:#2196f3
```

**Code:** `agents/fetch_external_source.py:87-108` (decision logic), `agents/verification.py:24-66` (retry loop)

---

## 3. Verification Retry Loop

On mismatch, the system retries with the next-best source before escalating.

```mermaid
flowchart LR
    VER[Verification] -->|mismatch| EXT[Fetch External Source<br/>skip tried sources]
    EXT --> VER2[Verification]
    VER2 -->|mismatch| EXT2[Fetch External Source<br/>skip tried sources]
    EXT2 --> VER3[Verification]
    VER3 -->|mismatch| MAN[Manager<br/>escalate]

    VER -->|verified| AUD[Audit]
    VER2 -->|verified| AUD

    style VER fill:#e3f2fd,stroke:#2196f3
    style VER2 fill:#e3f2fd,stroke:#2196f3
    style VER3 fill:#ffcdd2,stroke:#e53935
    style MAN fill:#ffcdd2,stroke:#e53935
```

**Why bounded:** Prevents infinite loops. Each attempt is logged distinctly (`VERIFICATION_FAILED_RETRY1`, `_RETRY2`, `_ESCALATED`) for full audit visibility.

**Code:** `agents/verification.py:24` — `MAX_RETRIES = 2`

---

## 4. Human-in-the-Loop Pause/Resume

The two-phase re-invoke pattern for human review.

```mermaid
sequenceDiagram
    participant G as Graph
    participant HR as Human Review Node
    participant UI as Streamlit UI
    participant RM as Relationship Manager

    G->>HR: Case arrives (status: PENDING)
    HR->>HR: Log HUMAN_REVIEW_PENDING
    HR->>G: Return state (graph → END)

    Note over UI: State cached in st.session_state

    UI->>RM: Show assigned manager + drafted message
    RM->>UI: "Simulate customer response"
    UI->>UI: validate_rm_notes(rm_notes)
    UI->>HR: resolve_human_review(state, rm_notes)
    HR->>HR: Update enrichment with customer values
    HR->>G: Re-invoke graph from START

    Note over G: Idempotent nodes fast-forward<br/>through detection/enrichment/fetch

    G->>G: Verification → Audit → COMPLETE
```

**Why two-phase re-invoke:** Simpler than LangGraph's `interrupt()` + `SqliteSaver` checkpointer. Works for single-session demo; production would use durable checkpointer.

**Code:** `agents/human_review.py:51-100` (resolve), `graph/build.py:134-139` (resume)

---

## 5. Data Flow Between Components

```mermaid
flowchart TD
    subgraph INPUT["Input"]
        TRIGGER[trigger_event]
        RECORD[corporate_record]
        META[metadata]
    end

    subgraph AGENTS["Agent Pipeline"]
        DET[Detection]
        ENR[Enrichment]
        EXT[Fetch External]
        VER[Verification]
        MAN[Manager]
        COM[Communication]
        HR[Human Review]
        AUD[Audit]
    end

    subgraph STATE["LangGraph State<br/>models/state.py"]
        S[RemediationState<br/>TypedDict with 14 fields]
    end

    subgraph OUTPUT["Output"]
        GAP[gap_event]
        ERES[enrichment_result]
        DEC[decision]
        VRES[verification_result]
        MGR[assigned_manager]
        DRAFT[client_message_draft]
        REV[human_review]
    end

    TRIGGER & RECORD & META --> S
    S --> DET --> GAP --> S
    S --> ENR --> ERES --> S
    S --> EXT --> DEC --> S
    S --> VER --> VRES --> S
    S --> MAN --> MGR --> S
    S --> COM --> DRAFT --> S
    S --> HR --> REV --> S
    S --> AUD

    style STATE fill:#e3f2fd,stroke:#2196f3
    style INPUT fill:#e8f5e9,stroke:#4caf50
    style OUTPUT fill:#fff3e0,stroke:#ff9800
```

**Key insight:** All agents read from and write to a shared `RemediationState` TypedDict. The LangGraph runtime handles state threading between nodes.

---

## 6. Defense-in-Depth Architecture

```mermaid
flowchart TD
    subgraph USER_INPUT["User Input Layer"]
        UI_APP[app.py<br/>button handlers]
    end

    subgraph VALIDATION["Validation Layer"]
        VAL[services/validation.py]
        V1[validate_rm_notes<br/>injection patterns]
        V2[validate_policy_version<br/>bounds check]
    end

    subgraph AGENT_LAYER["Agent Layer"]
        HR[human_review.py<br/>re-validates independently]
    end

    subgraph LLM_BOUNDARY["LLM Boundary"]
        LLM_CLIENT[services/llm_client.py]
        PRIV[services/privacy.py]
        P1[redact_pii<br/>outbound + inbound]
        P2[build_hardened_system_prompt<br/>anti-injection suffix]
    end

    subgraph AUDIT_STORE["Audit Store"]
        AUD[(SQLite audits<br/>INTENTIONALLY NOT redacted)]
    end

    UI_APP --> VAL
    VAL --> V1 & V2
    UI_APP --> HR
    HR --> VAL

    LLM_CLIENT --> PRIV
    PRIV --> P1 & P2

    style USER_INPUT fill:#e8f5e9,stroke:#4caf50
    style VALIDATION fill:#fff3e0,stroke:#ff9800
    style AGENT_LAYER fill:#e3f2fd,stroke:#2196f3
    style LLM_BOUNDARY fill:#fce4ec,stroke:#e91e64
    style AUDIT_STORE fill:#f3e5f5,stroke:#9c27b0
```

**What's redacted:** `beneficial_owner` and `tax_id` never reach the LLM. All LLM-bound text is scanned and redacted.

**What's NOT redacted:** The SQLite `audits` table keeps full, unredacted detail — this is the compliance record.

**Code:** `services/privacy.py` (redaction), `services/validation.py` (injection checks), `services/llm_client.py:86-99` (applied in `complete()`)

---

## 7. SQLite ER Diagram

```mermaid
erDiagram
    corporate_records {
        text id PK
        text company_name
        text beneficial_owner
        text tax_id
        text documents
        int policy_version
        text status
        text parent_entity_id
        text updated_at
    }

    metadata {
        text customer_id PK, FK
        text last_checked
        text document_hash
        int policy_version
        text document_expiry
        text verification_status
        text last_remediation
        text audit_pointer
    }

    gap_events {
        text id PK
        text customer_id FK
        text gap_type
        text details
        text detected_at
        text trigger_source
        text status
        text run_id
    }

    decisions {
        text id PK
        text customer_id FK
        text gap_event_id FK
        text outcome
        real confidence
        text reason
        text created_at
    }

    audits {
        text id PK
        text customer_id FK
        text timestamp
        text agent
        text decision
        text evidence
        real confidence
        text previous_state
        text new_state
        text run_id
    }

    config {
        text key PK
        text value
    }

    corporate_records ||--o| metadata : "has"
    corporate_records ||--o{ gap_events : "generates"
    gap_events ||--o{ decisions : "produces"
    corporate_records ||--o{ audits : "logged"
```

**Schema:** `services/db.py:20-82`

---

## 8. Component Responsibilities

| Component | File | Responsibility |
|---|---|---|
| `app.py` | Streamlit entrypoint | UI layout, event handlers, session state |
| `events/bus.py` | In-memory pub/sub | Decouples triggers from handlers |
| `events/types.py` | Event dataclasses | 4 event types as typed dataclasses |
| `graph/build.py` | LangGraph wiring | StateGraph, conditional edges, dispatcher helpers |
| `models/state.py` | RemediationState | TypedDict shared across all agents |
| `models/entities.py` | Pydantic models | Entity definitions mirroring SQLite schema |
| `agents/detection.py` | Gap detection | 5 gap types, priority-ordered checks |
| `agents/enrichment.py` | Internal enrichment | CRM + Previous KYC lookups |
| `agents/fetch_external_source.py` | External fetch + decision | External registries + AUTO/VERIFY/HUMAN |
| `agents/verification.py` | Data validation | Official source comparison + retry loop |
| `agents/manager.py` | RM assignment | Specialty-based assignment + LLM summary |
| `agents/communication.py` | Message drafting | Client follow-up email draft |
| `agents/human_review.py` | RM queue | Pause/resume + resolve logic |
| `agents/audit.py` | Logging | `log_step()` + terminal write-back |
| `agents/routing.py` | Edge predicates | Conditional routing functions |
| `services/db.py` | SQLite layer | All CRUD operations |
| `services/config.py` | Runtime config | Threshold management |
| `services/mock_sources.py` | Mock data | Internal/external/official sources |
| `services/llm_client.py` | LLM interface | Pluggable provider with template fallback |
| `services/privacy.py` | PII protection | Redaction + hardened prompts |
| `services/validation.py` | Input validation | Injection rejection + bounds checks |
| `mock_data/companies.py` | Seed data | 10 companies with test scenarios |
| `mock_data/managers.py` | RM directory | Specialist assignment rules |
| `migration/seed.py` | Data seeder | Idempotent batch migration |
| `ui/components.py` | UI helpers | Badges, agent cards, timeline |
