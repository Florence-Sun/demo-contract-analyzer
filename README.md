# Enterprise Contract Compliance Analyzer

An **Agentic RAG** prototype that audits third-party contracts against five internal security requirements. It uses a Router → Extractor → Evaluator agent pipeline, structured JSON trace logging, a KPI dashboard backed by SQLite, and an MCP server for extensibility.

For design rationale, trade-offs and KPI definitions, see [docs/adr/ARCHITECTURE.md](docs/adr/ARCHITECTURE.md).

---

## Quick Start

Requires **Python 3.11+** (tested on 3.13) and `git`.

```bash
git clone https://github.com/Florence-Sun/demo-contract-analyzer.git
cd demo-contract-analyzer
```

**1. Add an LLM key.** Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` with the API key that the interviewee, Zhimin (Florence) Sun, provided. Analysis and chat require a key; the dashboard and logs open without one (see *Temporary API key for reviewers*).

**2. Run.**

**Windows (PowerShell)**

```powershell
.\run.ps1
```

**macOS / Linux**

```bash
./run.sh
```

Both scripts create `.venv`, install the pinned `requirements.txt` and launch Streamlit. Wait for `You can now view your Streamlit app in your browser`, then open **http://localhost:8501**. The browser does not open automatically.

To do it manually:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt       # macOS/Linux: .venv/bin/python -m pip ...
.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true
```

**Troubleshooting**

| Symptom | Fix |
| :--- | :--- |
| `running scripts is disabled` (PowerShell) | `Set-ExecutionPolicy -Scope Process Bypass`, then re-run `.\run.ps1` |
| `python` not found on Windows | Use the `py` launcher (`py -m venv .venv`) |
| `pip.exe` / `streamlit.exe` "blocked by an Application Control policy" | The scripts already call `python -m pip` and `python -m streamlit` to avoid those launchers. If `python.exe` itself is blocked, use your system Python (`py -m venv .venv`) or ask IT to allow the venv folder |
| Browser says "cannot reach the page" | Wait for the `Local URL` line in the terminal; try http://127.0.0.1:8501; if port 8501 is busy, use the port Streamlit prints |
| Red `CRITICAL: Missing ANTHROPIC_API_KEY` banner | Set the key in `.env` (copy `.env.example`) or paste it in the sidebar, then re-run |
| A question shows `Needs Human Review` | Its LLM calls failed 3 times; check the `CRITICAL` event for that `trace_id` in Tab 3 and the provider status |

## LLM Configuration

Create a `.env` file in the project root from `.env.example` (never commit it):

```
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Optional model override (default: claude-sonnet-4-6)
# ANTHROPIC_MODEL=claude-sonnet-4-6
```

You can also paste a key into the Streamlit sidebar.

| Credentials present | Result |
| :--- | :--- |
| `ANTHROPIC_API_KEY` | Analysis and chat run on `claude-sonnet-4-6` (or `ANTHROPIC_MODEL`) |
| none | **Analysis and chat are disabled** |

**Failure behaviour (fail fast, never guess):**
- **No API key:** the app shows `CRITICAL: Missing ANTHROPIC_API_KEY ... System halted.`, blocks analysis and chat, and logs a `CRITICAL` `CONFIG_ERROR` event. The KPI dashboard and log viewer still work, because they read the pre-seeded history.
- **Key present but an LLM call fails** (network, rate limit, malformed JSON): retried up to 2 times with exponential backoff (1 s, then 2 s), logging a `WARN` `LLM_RETRY` event each time. If all 3 attempts fail, that question is reported as **`Needs Human Review`** with 0% confidence, and a `CRITICAL` `LLM_EXTRACTION_FAILED` event is logged. No compliance state is guessed. This state is an addition to the three states in the assignment's Table 1, so a failure is never reported as a real determination.

## Temporary API key for reviewers

Analysis needs a key. The interviewee, Zhimin (Florence) Sun, will send a **temporary, spend-limited Anthropic key** separately (not in this repo). Set `ANTHROPIC_API_KEY` in `.env` with that key (or paste it in the sidebar) before running an analysis. A run costs roughly $0.05.

**Without a key you can still review:** Tab 2 (KPI dashboard) and Tab 3 (logs) open pre-loaded with three real, completed Claude Sonnet runs of the provided sample contract (see below).

## Using the App

1. **Tab 1, Analysis:** upload a PDF and click *Run Compliance Analysis*, or click *Analyze provided sample* in the sidebar to use `data/Sample Contract.pdf`. Results appear as a table and as the Section 7 JSON.
2. **Tab 2, KPI Dashboard:** latency, cost, groundedness, confidence and retry-rate views, a KPI defense matrix, and threshold alert banners (`src/alerts.py`) that show red/amber when the latest run breaches a KPI threshold (for example, any Needs Human Review question).
3. **Tab 3, Logs:** structured JSON events from `logs/app.jsonl`, filterable by `trace_id`. The same events stream to the terminal that launched Streamlit.
4. **Tab 4, Chat and MCP:** free-form questions about the contract (bonus feature), plus the OpenAPI spec.

> **Pre-seeded history (real runs):** on first start the app loads `data/seed/analysis_runs.json` and `data/seed/app.seed.jsonl` into `data/kpi_telemetry.db` and `logs/app.jsonl`. These are **3 real runs of `data/Sample Contract.pdf` with `claude-sonnet-4-6`** (about 12.7–12.9k tokens each), produced by `scripts/generate_seed_data.py`. Nothing is synthetic. The first two ran before the five questions were parallelized (94 s and 119 s), and the third ran after (24 s), so the latency chart shows that change. Runs you perform are appended. Delete `data/kpi_telemetry.db` and `logs/app.jsonl` to reset to the seeded state. Three points make for a thin history, so the charts show little trend until more runs accumulate.

## MCP Server

```powershell
python mcp_server.py        # stdio transport
```

Tools exposed: `analyze_contract_pdf(pdf_base64, filename)` and `ask_contract_followup(question)`, which answers about the most recently analyzed contract. Example Claude Desktop config:

```json
{
  "mcpServers": {
    "contract-analyzer": {
      "command": "D:/Manulife_assignment/contract-analyzer/.venv/Scripts/python.exe",
      "args": ["D:/Manulife_assignment/contract-analyzer/mcp_server.py"]
    }
  }
}
```

`openapi.yaml` is the REST connector specification (ChatGPT Actions / Copilot Studio). It is a spec only, and no REST server in this repo implements it yet.

## Project Structure

```
contract-analyzer/
├── app.py                   # Streamlit UI (Analysis, KPI Dashboard, Logs, Chat)
├── mcp_server.py            # MCP server (FastMCP) exposing analysis + follow-up tools
├── openapi.yaml             # OpenAPI 3.1 connector specification
├── requirements.txt
├── run.ps1 / run.sh         # One-step setup + launch
├── README.md
├── docs/
│   ├── adr/ARCHITECTURE.md  # Design rationale, trade-offs, KPI defense (ADR-0001)
│   ├── template/            # architecture-template.md for future ADRs
│   └── spec/                # Original assignment PDF (confidential)
├── data/                    # Sample Contract.pdf (interview-provided), kpi_telemetry.db (runtime)
│   └── seed/                # Real-run seed data (analysis_runs.json, app.seed.jsonl)
├── scripts/                 # generate_seed_data.py (re-creates the seed from live runs)
├── logs/                    # Structured JSON trace logs (app.jsonl)
└── src/
    ├── schemas.py           # Pydantic v2 models, the 5 questions, Section 7 output schema
    ├── logger.py            # StructuredTracer (trace_id / span_id), log reader
    ├── database.py          # SQLite KPI store; loads data/seed on first start
    ├── parser.py            # PDF ingestion: text sections, tables, image metadata
    └── agents.py            # Router, Extractor, Evaluator orchestration and chat
```

## Dependencies

Direct dependencies, pinned in `requirements.txt`. Licences are read from each installed package's metadata (versions as pinned).

| Package | Version | Purpose | Licence |
| :--- | :--- | :--- | :--- |
| streamlit | 1.66.0 | Web UI | Apache-2.0 |
| plotly | 7.1.0 | Dashboard charts | MIT |
| pandas | 3.0.6 | Data frames for telemetry | BSD-3-Clause |
| pydantic | 2.14.1 | Schema validation | MIT |
| pymupdf | 1.28.2 | PDF text and image extraction | **AGPL-3.0 or Artifex commercial licence** (dual) |
| pdfplumber | 0.11.10 | PDF table extraction | MIT |
| anthropic | 1.15.0 | Claude API client | MIT |
| openai | 3.35.1 | In `requirements.txt` only; unused unless `ANTHROPIC_API_KEY` is absent and `OPENAI_API_KEY` is set (untested) | Apache-2.0 |
| python-dotenv | 1.2.4 | `.env` loading | BSD-3-Clause |
| mcp | 1.30.0 | MCP server (FastMCP) | MIT |

Transitive dependencies (numpy, httpx, starlette and others) carry their own licences, and `pip show <package>` or `pip-licenses` lists them.

**PyMuPDF note:** AGPL-3.0 is acceptable for this local, non-redistributed prototype. Offering the app as a network service would require either releasing the source under AGPL or buying Artifex's commercial licence. A production deployment would need that decision, or a swap to a permissively licensed PDF library.

**LLM service:** the Anthropic API is a commercial service governed by Anthropic's terms, not an open-source dependency.

## Cost

Cost is billed by the LLM provider. The app's `estimated_cost_usd` is a flat-rate approximation (tokens × $0.0000035), not provider billing. The three seeded runs of the sample contract used about 12.7–12.9k tokens each, an estimated **$0.045 per contract**. Check actual spend on the provider's usage page. Opening the app without a key makes no network calls and costs nothing.

## Confidentiality

The assignment brief in `docs/spec/` is confidential. Keep any Git repository private, and make sure `.env` and `data/*.db` are in `.gitignore`.
