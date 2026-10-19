"""
Model Context Protocol (MCP) Server for the Enterprise Contract Compliance Analyzer.

Exposes contract compliance auditing and conversational contract interrogation
as standardized MCP tool endpoints consumable by any MCP-compatible client
(e.g., Claude Desktop, Cursor IDE, Microsoft Copilot).
"""

import base64
import json
from typing import Any, Dict, Optional
from mcp.server.fastmcp import FastMCP

from src.agents import AgenticComplianceOrchestrator
from src.database import save_report
from src.logger import StructuredTracer
from src.parser import ContractPDFParser

# Initialize FastMCP server instance
mcp = FastMCP("Enterprise-Contract-Compliance-Analyzer")

# Most recently analyzed contract, so follow-up questions in the same session have context.
# Process-local demo state; a production deployment would key this by tenant and document_id.
_last_parsed_doc: Optional[Dict[str, Any]] = None


@mcp.tool()
def analyze_contract_pdf(pdf_base64: str, filename: str = "contract.pdf") -> Dict[str, Any]:
    """
    Executes the full Agentic RAG Compliance Pipeline (Router + Extractor + Evaluator)
    against the 5 mandatory security compliance questions and returns validated JSON.
    The parsed contract is retained so `ask_contract_followup` can answer questions about it.

    Args:
        pdf_base64 (str): Base64-encoded PDF contract binary string.
        filename (str): Name of the contract document.

    Returns:
        Dict[str, Any]: Validated JSON report conforming to Section 7 Output Schema.
    """
    global _last_parsed_doc
    tracer = StructuredTracer()
    pdf_bytes = base64.b64decode(pdf_base64)

    parser = ContractPDFParser(tracer)
    parsed_document = parser.parse_pdf(pdf_bytes, filename)
    _last_parsed_doc = parsed_document
    orchestrator = AgenticComplianceOrchestrator(tracer)
    report = orchestrator.analyze_contract(parsed_document)
    save_report(report)
    return json.loads(report.model_dump_json(by_alias=True))


@mcp.tool()
def ask_contract_followup(question: str) -> Dict[str, str]:
    """
    Enables multi-turn conversational interrogation over the most recently analyzed contract.
    Call `analyze_contract_pdf` first.

    Args:
        question (str): Free-form question about the contract clauses.

    Returns:
        Dict[str, str]: Grounded answer dictionary with trace ID and section citations.
    """
    tracer = StructuredTracer()
    if _last_parsed_doc is None:
        return {
            "trace_id": tracer.trace_id,
            "question": question,
            "answer": "No contract loaded. Call analyze_contract_pdf with a PDF first.",
        }

    orchestrator = AgenticComplianceOrchestrator(tracer)
    answer_text = orchestrator.answer_chat_question(question, _last_parsed_doc)
    return {"trace_id": tracer.trace_id, "question": question, "answer": answer_text}


if __name__ == "__main__":
    mcp.run()
