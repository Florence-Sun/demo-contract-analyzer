# Architecture & Design Rationale

| Field | Value |
| :--- | :--- |
| **Document ID** | ADR-0001 |
| **Title** | Agentic RAG architecture for contract compliance analysis |
| **Status** | Accepted *(Proposed / Accepted / Superseded / Deprecated)* |
| **Date** | 2026-10-18 |
| **Last updated** | 2026-10-18 |
| **Approver** | Florence Sun |
| **Approval date** | 2026-10-18 |
| **Decision drivers** | Explainable agent flow; traceable logs; fail-fast reliability; reproducible reviewer demo; low dependency footprint |
| **Supersedes / Superseded by** | None |
| **Related** | [README](../../README.md); the confidential assignment brief is not included in this repository |
| **Template** | [architecture-template.md](../template/architecture-template.md) |

## Key Decisions

| # | Decision | Alternative considered | Why |
| :--- | :--- | :--- | :--- |
| 1 | Plain-Python orchestrator with three agents | LangGraph / CrewAI | Fixed, small flow; fewer dependencies; control flow stays visible |
| 2 | Lexical, keyword-scored routing (top-K = 4, table/exhibit boosts) | Embedding / hybrid retrieval | Cheap, deterministic, explainable; known recall limits |
| 3 | Heuristic Evaluator (quote groundedness + formula-based confidence + one retry) | LLM-as-judge | No extra LLM cost or latency; limits stated in §9 |
| 4 | Fail fast without an API key; retry twice with backoff; then flag the question "Needs Human Review" (0% confidence, CRITICAL log) | Rule-engine fallback that guesses a state | A missing key is a deployment error; a guessed compliance state is worse than an explicit "could not determine" |
| 5 | The 5 questions run concurrently (thread pool, 5 workers); logging serialised by a lock | Sequential loop; asyncio | Questions are independent, so latency drops from about 94–119 s to about 24 s with no change to results; threads suit blocking SDK calls |
| 7 | Reviewer demo via a temporary key plus pre-seeded real runs | Offline mode / synthetic history | Dashboard and logs show real Claude runs without a key; nothing is fabricated |
| 5 | Streamlit UI and MCP server as thin adapters over `src/` | Separate backend service | One core, minimal setup |
| 6 | SQLite telemetry plus JSONL logs with `trace_id`/`span_id` | Hosted observability platform | Zero setup; reproducible for the panel |

## 1. Objective

Verify a third-party contract against five security requirements (Password Management, IT Asset Management, Security Training & Background Checks, Data-in-Transit Encryption, Network Authentication & Authorization). For each requirement the system returns a state (`Fully Compliant` / `Partially Compliant` / `Non-Compliant`), a calibrated confidence, verbatim quotes with section/exhibit references, and a rationale. The system is built as an observable pipeline: every decision is logged, and every run is measured.

## 2. System Diagram

```mermaid
flowchart LR
    subgraph Clients
        UI[Streamlit UI<br/>app.py]
        MCPC[MCP client<br/>Claude Desktop / Cursor]
        GPT[ChatGPT Action /<br/>Copilot Studio]
    end
    GPT -. openapi.yaml<br/>(spec only, no server yet) .-> API[Thin REST wrapper<br/>to be built]
    MCPC -- stdio --> MCP[mcp_server.py<br/>FastMCP tools]
    UI --> CORE
    MCP --> CORE
    API -.-> CORE

    subgraph CORE[Core library: src/]
        P[parser.py<br/>text / tables / images] --> R[Router Agent]
        R --> X[Extractor Agent]
        X --> E[Evaluator Agent]
        E -- retry once if ungrounded --> X
    end

    X -- prompt --> LLM[(Anthropic / OpenAI)]
    CORE --> LOG[logger.py<br/>logs/app.jsonl + stdout]
    CORE --> DB[(SQLite<br/>kpi_telemetry.db)]
    DB --> UI
    LOG --> UI
```

The UI and MCP server are thin adapters over the same core (`parser` → `agents` → `database`/`logger`). A REST connector would be a third adapter. Business logic lives only in `src/`.

## 3. Ingestion (`src/parser.py`)

Two passes over the PDF:

- **PyMuPDF** extracts text per page and splits it into chunks at `Section N.N`, `Exhibit X` and `ARTICLE` headings (regex). Pages with embedded images get an `image_metadata` chunk. Image *content* is not interpreted (no OCR or vision).
- **pdfplumber** extracts tables and converts them to Markdown so the LLM can read rows and columns. Tables are kept as separate chunks because control matrices (for example an authentication summary table) are often the strongest evidence.

Each chunk has `chunk_id` (`P3-S2`, `P4-TBL1`), `page`, `type`, `section_title` and `content`. These IDs are what the Router logs and what makes routing decisions traceable.

## 4. Agentic Architecture (`src/agents.py`)

### Orchestration choice
A plain-Python orchestrator (`AgenticComplianceOrchestrator`) calls the three agents directly as methods. There is no LangGraph/CrewAI dependency. The flow is a fixed, small DAG per question (route → extract → evaluate → optional retry), so a framework would add dependencies and hide the control flow without adding capability. If the flow became dynamic (tool-calling agents, branching), I would move to LangGraph for checkpointing and typed state. Inter-agent communication is by typed Python objects (`RouterDecision`, `EvaluatorCritique`, extraction dict), which are Pydantic models where they cross a boundary.

### Router Agent
For each question, score every chunk by counting that question's keyword hits in the title and content. Tables with hits get +2 and exhibits with hits get +1. Keep the top **K=4**; if nothing matches, fall back to the first 3 chunks. It then records which sub-task processors apply (`clause_extractor`, `table_parser`, `exhibit_cross_reference_resolver`) and logs a `ROUTE_DECISION` with chunk IDs and rationale.

This is **lexical routing**, not semantic. It is cheap, deterministic and easy to explain, but it can miss paraphrased clauses and it does not follow cross-references. The "sub-task dispatch" is currently descriptive metadata passed into the prompt, not separate processors.

### Extractor Agent
Builds a prompt containing the requirement text, the mandatory sub-controls and the routed chunks, and asks for JSON with `compliance_state`, `initial_confidence` (60–99), `relevant_quotes` and `rationale`. `max_tokens` is 2048 and the prompt tells the model to escape quotes. The response is de-fenced, parsed with `json.loads` and validated (state is one of the three allowed values, quotes is a non-empty list of strings, rationale is non-empty). A transport error, timeout, malformed JSON or failed validation counts as a failed attempt. Retries are up to 2, with exponential backoff (1 s, 2 s). SDK-level retries are disabled so the policy lives in one place.

### Evaluator Agent
Independent post-hoc checks on the extraction:

1. **Groundedness:** for each quote, strip the citation label, take words longer than 3 characters, and check what fraction appears in the routed chunks' text. A quote is "verified" at ≥65% overlap. Groundedness = verified quotes / total quotes. Below 0.50 raises `hallucination_detected`.
2. **Confidence calibration:** `calibrated = clamp(55, 98, raw × (0.65 + 0.35 × groundedness))`, capped at 92 for `Partially Compliant`. A fully grounded answer keeps its raw confidence, and an ungrounded answer is cut by up to 35%.
3. **Retry:** if hallucination is detected on attempt 0, the Extractor re-runs with a stricter "copy verbatim" instruction. Maximum 1 retry; the second result is accepted and its flags are surfaced (`hallucination_flag`, `retries_used`) rather than hidden.

### Failure strategy
| Failure | Behaviour |
| :--- | :--- |
| No API key | Fail fast: `MissingAPIKeyError`, `CRITICAL` `CONFIG_ERROR` log, UI banner; analysis and chat disabled |
| LLM error / invalid JSON | Retry up to 2× with exponential backoff (1 s, 2 s), `WARN` `LLM_RETRY` each time |
| All retries fail | Question becomes `Needs Human Review`, 0% confidence, no quotes; `CRITICAL` `LLM_EXTRACTION_FAILED`; excluded from aggregate metrics and counted in `needs_review_count` |
| Ungrounded quotes | One stricter retry, then accept with the flag set |
| No keyword matches | Router falls back to first 3 chunks |

## 5. Output Contract (`src/schemas.py`)

Pydantic v2 models validate every result. `ComplianceStateType` is a `Literal` of the three Table 1 states plus `Needs Human Review`, so an invalid state fails validation instead of reaching the UI. **Assumption:** the fourth state is an addition to the spec, assigned only by the orchestrator (never by the LLM) when extraction fails after all retries, so a failure is never reported as a real determination. The Section 7 fields (`Compliance Question`, `Compliance State`, `Confidence`, `Relevant Quotes`, `Rationale`) are exposed as aliases. The internal fields (`routed_chunks`, `evaluator_groundedness`, `hallucination_flag`, `retries_used`) travel alongside for the dashboard and are stripped from the Section 7 JSON view in the UI.

## 6. Connector & MCP Design

**MCP (`mcp_server.py`):** FastMCP over stdio, two tools: `analyze_contract_pdf(pdf_base64, filename)` and `ask_contract_followup(question, filename)`. Both reuse the same parser, orchestrator and telemetry as the UI, so MCP calls appear on the KPI dashboard and in the logs with their own `trace_id`.

**REST connector (`openapi.yaml`):** an OpenAPI 3.1 spec for ChatGPT Actions and Copilot Studio. No server implements it yet. The intended implementation is a small FastAPI app calling the same core functions.

**Production design decisions (the target, not what is built):**

- **Authentication:** stdio MCP inherits the local user's trust boundary. For a remote MCP/REST deployment: OAuth 2.0 (client-credentials for service clients, authorization-code for ChatGPT Actions), scoped tokens (`contracts:analyze`, `contracts:read`), and per-tenant API keys behind an API gateway with rate limits. LLM provider keys stay server-side only.
- **Request/response contracts:** the response is exactly the Section 7 schema plus `trace_id` and run metadata, so any client can correlate with logs. Large PDFs should be uploaded to object storage and passed by reference rather than as base64.
- **State and multi-turn:** the core is stateless per call. Multi-turn needs a `document_id` returned by analysis, with parsed chunks cached server-side (and ideally embedded in a vector store) keyed by tenant and `document_id`. The clients then send `document_id` plus a question.
- **Async:** analysis takes seconds to minutes, so the REST layer should be submit-and-poll (`202` plus job ID), with MCP progress notifications.

## 7. Observability (`src/logger.py`, `src/database.py`)

**Logs:** one JSON object per line with `timestamp`, `level`, `trace_id` (one per request), `span_id` (one per question, `sp-q{n}`, or per stage), `component`, `event_type`, `message` and `metadata`. They go to stdout (live terminal) and `logs/app.jsonl`. Key events: `PARSE_START/COMPLETE`, `PIPELINE_START/COMPLETE`, `ROUTE_DECISION`, `EXTRACTION_SUCCESS`, `EVALUATION_COMPLETE`, `RETRY_TRIGGERED` (evaluator), `LLM_RETRY` and `LLM_ATTEMPTS_EXHAUSTED` (WARN), and `CONFIG_ERROR` and `LLM_EXTRACTION_FAILED` (CRITICAL). Filtering by `trace_id` reconstructs a whole request.

**Telemetry:** each run writes one row to `analysis_runs` (latency, tokens, cost, compliance rate, mean confidence, mean groundedness, retry count, hallucination count, needs-review count, full JSON). On first start the table and `logs/app.jsonl` are loaded from `data/seed/`, which holds two real runs of the provided sample contract (`scripts/generate_seed_data.py`), so the dashboard and logs are populated without an API key.

## 8. KPIs and Thresholds

| KPI | Category | Target | Alert | Action |
| :--- | :--- | :--- | :--- | :--- |
| End-to-end latency p50/p95 | Operational | Measured 24 s parallel (94–119 s sequential before); proposed p95 < 30 s | p95 > 60 s | Parallelize per-question extraction; use a smaller model for easy questions |
| Citation groundedness | Safety/quality | ≥ 90% | < 80% on a run | Inspect the prompt and chunking; check for OCR degradation |
| Mean calibrated confidence | Quality | 83–95% | mean < 75% | Likely out-of-distribution template; review Router keywords |
| Evaluator retry rate | Agent health | < 16% | > 25% rolling | Check for prompt or model drift in `app.jsonl` |
| Cost per contract | FinOps | measured ≈ $0.045 (estimate) | > $0.10 | Cap K and chunk size |
| Hallucination count | Safety | 0 | any sustained | Review flagged traces |
| Needs-review rate | Reliability | 0% of questions | any > 0% | Check provider status and keys; inspect `LLM_EXTRACTION_FAILED` events |

The latency and cost figures come from the three real seeded runs, so n = 3 (one parallel run). The other targets are **proposed baselines, not measured ones**, and should be recalibrated once real traffic exists. A real production system would also need ground-truth labelled contracts to measure state accuracy and confidence calibration (expected calibration error). Groundedness measures citation fidelity, not whether the state is correct.

## 9. Known Limitations (stated up front)

1. **The Evaluator is heuristic.** Groundedness is bag-of-words overlap, not exact-substring matching or an LLM judge. A quote that reuses words from the source but misstates the clause can pass. It also does not check that the state follows from the quotes.
2. **Confidence is calibrated by formula, not against labelled outcomes.** It is a groundedness-weighted discount on the model's self-reported confidence.
3. **Routing is keyword-based** and does not follow cross-references.
4. **Cost is a flat per-token rate,** not provider pricing.
5. **KPI history is thin:** three real seeded runs (n = 3), so trend charts show little trend until more runs accumulate.
5a. **Concurrency is bounded by the slowest question and by provider rate limits.** All 5 calls start at once, and a 429 is handled only by the generic retry/backoff. There is no shared rate limiter or concurrency cap beyond 5 workers.
5b. **"Needs Human Review" is a spec extension,** a fourth state beyond Table 1. Aggregate metrics exclude those questions.
6. **Chat uses only the first 12 chunks** as context, with no retrieval, so questions about later pages may go unanswered. The MCP `ask_contract_followup` answers only about the last contract analyzed in the same server process (module-level state, not keyed by tenant or document).
7. **Images are detected, not interpreted.** There is no OCR for scanned PDFs.
8. **No auth, no persistence of documents, no concurrency control** (the shared SQLite file and log file are fine for a demo only).
9. **No automated tests or evaluation set yet.**

## 10. Model, Hosting & Cost

Default: Anthropic `claude-sonnet-4-6` (cloud API), 2,048 max output tokens, SDK default sampling temperature (the `temperature` argument is rejected by the installed SDK); alternative OpenAI `gpt-4o-mini` at temperature 0.1. There is no offline mode: a missing key halts analysis. Five questions × (1 + at most 1 evaluator retry) calls, plus up to 2 transport retries per call, so about 5–10 calls per contract in the normal case. Measured on the provided sample: about 12.7–12.9k tokens, an estimated $0.045 per run, and about 24 s end to end with the questions in parallel. The model ID is overridable by environment variable.

## 11. Next Steps

Exact-substring (or normalized fuzzy) quote verification; LLM-as-judge for state consistency; embedding-based hybrid retrieval with cross-reference resolution; a labelled evaluation set with calibration curves; the FastAPI wrapper for `openapi.yaml` with auth; parallel per-question extraction; unit tests for parser, router and evaluator.
