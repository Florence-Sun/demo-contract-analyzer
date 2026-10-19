"""
Streamlit Web User Interface for the Enterprise Contract Compliance Analyzer.

Provides an interactive 4-tab web interface satisfying all UI deliverables:
1. Tab 1: PDF contract upload, live processing status, structured compliance table, and JSON output.
2. Tab 2: Production KPI Dashboard with operational/quality metrics and historical Plotly charts.
3. Tab 3: Live structured JSON trace log inspector (`trace_id` / `span_id` correlation).
4. Tab 4: Bonus Conversational Contract Assistant and MCP/OpenAPI extensibility overview.
"""

import json
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.agents import AgenticComplianceOrchestrator, MissingAPIKeyError, resolve_llm_config
from src.database import init_db, load_kpi_dataframe, save_report
from src.logger import StructuredTracer, read_recent_logs, seed_logs_if_empty
from src.parser import ContractPDFParser

# Contract supplied by the interview team (optional one-click demo input)
SAMPLE_PDF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "Sample Contract.pdf")
SAMPLE_PDF_NAME = os.path.basename(SAMPLE_PDF_PATH)

# Page configuration
st.set_page_config(
    page_title="Enterprise Contract Compliance Analyzer",
    page_icon="⚖️",
    layout="wide",
)

# Initialize SQLite database and trace logs (loads committed real-run history on first start)
init_db()
seed_logs_if_empty()

st.title("⚖️ Enterprise Contract Compliance Analyzer")
st.caption(
    "Production Agentic RAG Architecture • Router + Extractor + Evaluator Agents • "
    "Real-Time & Historical KPI Telemetry • MCP & OpenAPI Extensible"
)

# Sidebar configuration controls
with st.sidebar:
    st.header("⚙️ System Configuration")
    api_key_input = st.text_input(
        "Anthropic or OpenAI API Key",
        type="password",
        help=(
            "Required to analyze contracts or chat. Alternatively set ANTHROPIC_API_KEY in `.env`. "
            "The KPI dashboard and logs work without a key."
        ),
    )
    if api_key_input:
        if api_key_input.startswith("sk-ant"):
            os.environ["ANTHROPIC_API_KEY"] = api_key_input
        else:
            os.environ["OPENAI_API_KEY"] = api_key_input

    st.divider()
    if os.path.exists(SAMPLE_PDF_PATH):
        st.subheader("📄 Quick Demo")
        if st.button(f"Analyze provided sample ({SAMPLE_PDF_NAME})", type="primary", use_container_width=True):
            with open(SAMPLE_PDF_PATH, "rb") as sample_file:
                st.session_state["uploaded_bytes"] = sample_file.read()
                st.session_state["uploaded_name"] = SAMPLE_PDF_NAME
                st.session_state["trigger_run"] = True

        st.divider()
    st.markdown(
        """
        **Pipeline Architecture:**
        1. **Ingestion:** Multi-modal PDF Parser (`PyMuPDF` + `pdfplumber`)
        2. **Router Agent:** Question decomposition & chunk/table dispatch
        3. **Extractor Agent:** Clause synthesis & verbatim quote extraction
        4. **Evaluator Agent:** Groundedness verification & confidence calibration
        """
    )

# Fail fast on missing credentials: analysis and chat are blocked, history views still render.
try:
    resolve_llm_config()
    llm_ready = True
except MissingAPIKeyError as config_error:
    llm_ready = False
    st.error(
        f"{config_error} Contract analysis and chat are disabled. "
        "Set ANTHROPIC_API_KEY in `.env` (see `.env.example`) or paste a key in the sidebar."
    )

# Define application tabs
tab_analyze, tab_kpi, tab_logs, tab_chat = st.tabs(
    [
        "🔍 1. Contract Compliance Analysis",
        "📊 2. Production KPI Dashboard",
        "🪵 3. Live Observability & Trace Logs",
        "💬 4. Bonus: Contract Chat & MCP Spec",
    ]
)

# ==========================================
# TAB 1: CONTRACT COMPLIANCE ANALYSIS
# ==========================================
with tab_analyze:
    uploaded_file = st.file_uploader("Upload Third-Party Contract (PDF)", type=["pdf"])
    if uploaded_file is not None:
        st.session_state["uploaded_bytes"] = uploaded_file.getvalue()
        st.session_state["uploaded_name"] = uploaded_file.name

    col_action1, col_action2 = st.columns([1, 4])
    with col_action1:
        run_analysis_clicked = st.button(
            "🚀 Run Compliance Analysis", type="primary", use_container_width=True
        )

    if run_analysis_clicked or st.session_state.get("trigger_run", False):
        st.session_state["trigger_run"] = False
        if not llm_ready:
            st.error("CRITICAL: Missing ANTHROPIC_API_KEY. System halted. Analysis was not started.")
        elif "uploaded_bytes" not in st.session_state:
            st.warning("Please upload a PDF contract first.")
        else:
            tracer = StructuredTracer()
            st.session_state["last_trace_id"] = tracer.trace_id

            with st.status(
                f"Running Agentic RAG Pipeline (Trace ID: `{tracer.trace_id}`)...", expanded=True
            ) as status_box:
                st.write("📥 **Step 1/3:** Parsing PDF text sections, tables, and image artifacts...")
                parser = ContractPDFParser(tracer)
                parsed_document = parser.parse_pdf(
                    st.session_state["uploaded_bytes"], st.session_state["uploaded_name"]
                )
                st.session_state["parsed_doc"] = parsed_document
                st.write(
                    f"✅ Extracted **{len(parsed_document['chunks'])}** chunks "
                    f"({parsed_document['tables_extracted']} tables, {parsed_document['images_extracted']} images) "
                    f"across **{parsed_document['num_pages']}** pages."
                )

                st.write(
                    "🧭 **Step 2/3:** Executing Router Agent decomposition & Specialist Extraction across 5 Compliance Domains..."
                )
                orchestrator = AgenticComplianceOrchestrator(tracer)
                report = orchestrator.analyze_contract(parsed_document)

                st.write(
                    "🛡️ **Step 3/3:** Running Evaluator Agent quote groundedness check, confidence calibration & SQLite telemetry persistence..."
                )
                save_report(report)
                st.session_state["last_report"] = report
                status_box.update(
                    label=f"Analysis Complete! Trace ID: `{report.trace_id}` ({report.total_latency_ms} ms)",
                    state="complete",
                )

    if "last_report" in st.session_state:
        rep = st.session_state["last_report"]
        st.subheader(f"Compliance Assessment Results — `{rep.contract_filename}`")

        if rep.needs_review_count:
            st.warning(
                f"{rep.needs_review_count} of {len(rep.results)} questions could not be analyzed "
                "after retries and are marked **Needs Human Review**. Metrics below exclude them. "
                f"See the CRITICAL events for trace `{rep.trace_id}` in Tab 3."
            )

        col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
        col_m1.metric("Trace ID", rep.trace_id)
        col_m2.metric("Overall Compliance", f"{int(rep.overall_compliance_rate * 100)}%")
        col_m3.metric("Mean Confidence", f"{rep.mean_confidence}%")
        col_m4.metric("Groundedness", f"{int(rep.mean_groundedness * 100)}%")
        col_m5.metric("Latency", f"{rep.total_latency_ms} ms")

        # Table matching Section 7 Output Schema presentation
        table_rows = []
        for item in rep.results:
            state_badge = {
                "Fully Compliant": "🟢 Fully Compliant",
                "Partially Compliant": "🟡 Partially Compliant",
                "Non-Compliant": "🔴 Non-Compliant",
                "Needs Human Review": "⚪ Needs Human Review",
            }[item.compliance_state]
            table_rows.append(
                {
                    "#": item.compliance_question_id,
                    "Compliance Domain": item.topic,
                    "State": state_badge,
                    "Confidence": item.confidence,
                    "Groundedness": f"{int(item.evaluator_groundedness * 100)}%",
                    "Routed Chunks": ", ".join(item.routed_chunks),
                    "Relevant Quotes": " | ".join(item.relevant_quotes),
                    "Rationale": item.rationale,
                }
            )
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

        st.subheader("📦 Validated Structured JSON Output (Section 7 Schema)")
        schema_json_output = [
            {
                "Compliance Question": item.compliance_question,
                "Compliance State": item.compliance_state,
                "Confidence": item.confidence,
                "Relevant Quotes": item.relevant_quotes,
                "Rationale": item.rationale,
            }
            for item in rep.results
        ]
        st.json(schema_json_output)

# ==========================================
# TAB 2: KPI DASHBOARD (REAL-TIME & HISTORICAL)
# ==========================================
with tab_kpi:
    st.subheader("📊 Production Compliance & System Telemetry Dashboard")
    st.markdown(
        "Tracks operational health (latency, token cost) and agentic RAG quality "
        "(evaluator groundedness, confidence calibration, self-correction retry rates) across runs."
    )

    df_telemetry = load_kpi_dataframe()

    k_col1, k_col2, k_col3, k_col4, k_col5, k_col6, k_col7 = st.columns(7)
    review_rate = round(df_telemetry["needs_review_count"].fillna(0).sum() / (len(df_telemetry) * 5) * 100, 1)
    p50_latency = round(df_telemetry["total_latency_ms"].median(), 0)
    p95_latency = round(df_telemetry["total_latency_ms"].quantile(0.95), 0)
    mean_cost = round(df_telemetry["estimated_cost_usd"].mean(), 4)
    mean_groundedness = round(df_telemetry["mean_groundedness"].mean() * 100, 1)
    mean_confidence = round(df_telemetry["mean_confidence"].mean(), 1)
    retry_rate = round((df_telemetry["evaluator_retry_count"] > 0).mean() * 100, 1)

    k_col1.metric("p50 / p95 Latency", f"{int(p50_latency)} / {int(p95_latency)} ms", delta="Proposed target p95 < 30s", delta_color="off")
    k_col2.metric("Mean Cost / Run", f"${mean_cost}", delta="Target < $0.05")
    k_col3.metric("Mean Groundedness", f"{mean_groundedness}%", delta="Target >= 85%")
    k_col4.metric("Mean Confidence", f"{mean_confidence}%", delta="Target >= 80%")
    k_col5.metric("Evaluator Retry Rate", f"{retry_rate}%", delta="Alert > 25%", delta_color="inverse")
    k_col6.metric("Needs-Review Rate", f"{review_rate}%", delta="Alert > 0%", delta_color="inverse")
    k_col7.metric("Total Audited Runs", len(df_telemetry))

    st.divider()
    chart_col_left, chart_col_right = st.columns(2)

    with chart_col_left:
        fig_latency = px.line(
            df_telemetry,
            x="timestamp",
            y="total_latency_ms",
            hover_data=["contract_filename", "trace_id"],
            title="Historical End-to-End Processing Latency (ms) & SLA Alert Threshold",
            markers=True,
        )
        fig_latency.add_hline(
            y=60000, line_dash="dash", line_color="red", annotation_text="p95 Alert (60,000 ms)"
        )
        st.plotly_chart(fig_latency, use_container_width=True)

        fig_cost = px.bar(
            df_telemetry.tail(15),
            x="trace_id",
            y="estimated_cost_usd",
            color="total_tokens",
            title="Token Consumption & Cost per Contract Run (Last 15 Runs)",
        )
        st.plotly_chart(fig_cost, use_container_width=True)

    with chart_col_right:
        fig_quality = go.Figure()
        fig_quality.add_trace(
            go.Scatter(
                x=df_telemetry["timestamp"],
                y=df_telemetry["mean_confidence"],
                mode="lines+markers",
                name="Mean Confidence (%)",
            )
        )
        fig_quality.add_trace(
            go.Scatter(
                x=df_telemetry["timestamp"],
                y=df_telemetry["mean_groundedness"] * 100,
                mode="lines+markers",
                name="Quote Groundedness (%)",
            )
        )
        fig_quality.add_hline(
            y=80, line_dash="dot", line_color="orange", annotation_text="Quality Floor (80%)"
        )
        fig_quality.update_layout(
            title="Historical Model Quality: Confidence vs. Citation Groundedness (%)"
        )
        st.plotly_chart(fig_quality, use_container_width=True)

        fig_compliance = px.histogram(
            df_telemetry,
            x="overall_compliance_rate",
            nbins=5,
            title="Portfolio Compliance Score Distribution Across Audited Contracts",
        )
        st.plotly_chart(fig_compliance, use_container_width=True)

    with st.expander("📖 KPI Defense Matrix (Interview Walkthrough Reference)", expanded=False):
        st.markdown(
            """
            | KPI Metric | Category | Baseline / Target | Alert Threshold | Operational Action / Playbook |
            | :--- | :--- | :--- | :--- | :--- |
            | **End-to-End Latency (p50/p95)** | Operational | Measured ~24 s with the 5 questions in parallel (was ~94–119 s sequential); proposed target p95 < 30 s | p95 > 60 s | Route simple questions to a smaller model tier; watch provider rate limits as concurrency grows. |
            | **Citation Groundedness Score** | Quality / Safety | >= 90% verbatim match | < 80% on any run | Evaluator Agent triggers automatic self-correction retry with stricter quote constraints. |
            | **Confidence Calibration Drift** | Model Quality | Mean 83%–95% | Mean < 75% | Indicates out-of-distribution contract templates; update Router keyword/embedding index. |
            | **Evaluator Retry / Rejection Rate** | Agentic Health | < 16% of questions | > 25% rolling window | Inspect prompt drift or PDF OCR degradation in `logs/app.jsonl`. |
            | **Cost per Contract (USD)** | FinOps | $0.02 – $0.04 / contract | > $0.10 / contract | Enforce token budget cap on Router top-K chunks (max K=4). |
            | **Needs-Review Rate** | Reliability | 0% of questions | any > 0% | LLM calls failed after 2 retries; check provider status/keys and the CRITICAL events in `logs/app.jsonl`. |
            """
        )

# ==========================================
# TAB 3: LIVE STRUCTURED LOGS & TRACE INSPECTOR
# ==========================================
with tab_logs:
    st.subheader("🪵 Structured JSON Logs (`logs/app.jsonl`)")
    st.caption(
        "Every event includes `timestamp`, `trace_id`, `span_id`, `component`, `event_type`, and granular `metadata`."
    )

    trace_id_filter = st.text_input(
        "Filter by Trace ID (leave empty to view all recent logs):",
        value=st.session_state.get("last_trace_id", ""),
    )
    log_entries = read_recent_logs(trace_id=trace_id_filter.strip() or None, limit=100)
    st.write(f"Showing **{len(log_entries)}** structured log entries:")

    if log_entries:
        st.dataframe(pd.DataFrame(log_entries), use_container_width=True)
        with st.expander("Raw JSONL Stream (Terminal View)", expanded=True):
            st.code(
                "\n".join(json.dumps(entry) for entry in log_entries[-25:]), language="json"
            )
    else:
        st.info(
            "No logs recorded yet. Run a contract analysis in Tab 1 to stream live structured trace events."
        )

# ==========================================
# TAB 4: BONUS CHAT & EXTENSIBILITY
# ==========================================
with tab_chat:
    chat_col, mcp_col = st.columns([1, 1])

    with chat_col:
        st.subheader("💬 Bonus: Conversational Contract Assistant")
        st.caption("Ask free-form follow-up questions over the uploaded contract beyond the 5 compliance items.")
        user_question = st.text_input(
            "Ask a question about the contract (e.g., 'What is the break-glass rotation period?'):"
        )
        if st.button("Ask Assistant") and user_question:
            if not llm_ready:
                st.error("CRITICAL: Missing ANTHROPIC_API_KEY. System halted.")
            elif "parsed_doc" not in st.session_state:
                st.warning("Upload and analyze a contract in Tab 1 first.")
            else:
                orchestrator_instance = AgenticComplianceOrchestrator(StructuredTracer())
                answer_text = orchestrator_instance.answer_chat_question(
                    user_question, st.session_state["parsed_doc"]
                )
                st.markdown(answer_text)

    with mcp_col:
        st.subheader("🔌 Extensibility: MCP Server & OpenAPI Connector")
        st.markdown(
            """
            This solution exposes its Agentic RAG engine via:
            1. **`mcp_server.py`**: Model Context Protocol (MCP) Server exposing `analyze_contract_pdf` and `ask_contract_followup` tools.
            2. **`openapi.yaml`**: REST OpenAPI 3.1 specification for ChatGPT Actions / Microsoft Copilot Studio plugins.
            """
        )
        if os.path.exists("openapi.yaml"):
            with open("openapi.yaml", "r", encoding="utf-8") as yaml_file:
                st.code(yaml_file.read(), language="yaml")