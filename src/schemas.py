"""
Data Schemas and Domain Models for the Enterprise Contract Compliance Analyzer.

This module defines Pydantic v2 models that enforce strict data contracts across:
1. The five mandatory compliance requirements (Table 1 of the specification).
2. Router Agent chunk selection and sub-task dispatch decisions.
3. Evaluator Agent critique, hallucination detection, and confidence calibration.
4. Final validated JSON output conforming to Section 7 Output Schema.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

# The three states the LLM may assign (Table 1)
LLM_COMPLIANCE_STATES = ("Fully Compliant", "Partially Compliant", "Non-Compliant")

# Assigned by the orchestrator (never the LLM) when extraction fails after all retries.
# An explicit extension of Table 1, so a failure is never reported as a real determination.
NEEDS_REVIEW_STATE = "Needs Human Review"

ComplianceStateType = Literal[
    "Fully Compliant",
    "Partially Compliant",
    "Non-Compliant",
    "Needs Human Review",
]


class ComplianceQuestionSpec(BaseModel):
    """
    Represents a single regulatory/internal policy compliance question from Table 1.

    Attributes:
        id (int): Unique numeric identifier (1 through 5).
        topic (str): Short human-readable domain name for UI display and logging.
        question_text (str): Full verbatim requirement text from Table 1.
        keywords (List[str]): Domain-specific lexical anchors used by the Router Agent
            for hybrid lexical/semantic section retrieval.
        mandatory_subcontrols (List[str]): Granular sub-requirements that must be
            present in the contract to achieve a 'Fully Compliant' state.
    """

    id: int
    topic: str
    question_text: str
    keywords: List[str]
    mandatory_subcontrols: List[str]


# Master definition of the 5 required compliance questions from Section 6 (Table 1)
COMPLIANCE_QUESTIONS: List[ComplianceQuestionSpec] = [
    ComplianceQuestionSpec(
        id=1,
        topic="Password Management",
        question_text=(
            "Password Management. The contract must require a documented password standard covering "
            "password length/strength, prohibition of default and known-compromised passwords, secure "
            "storage (no plaintext; salted hashing if stored), brute-force protections (lockout/rate "
            "limiting), prohibition on password sharing, vaulting of privileged credentials/recovery "
            "codes, and time-based rotation for break-glass credentials. Based on the contract language "
            "and exhibits, what is the compliance state for Password Management?"
        ),
        keywords=[
            "password", "credential", "hashing", "salted", "plaintext",
            "brute-force", "lockout", "rate limiting", "sharing", "vault",
            "break-glass", "rotation",
        ],
        mandatory_subcontrols=[
            "password length/strength",
            "prohibition of default/compromised passwords",
            "salted hashing / no plaintext",
            "brute-force lockout/rate limiting",
            "no password sharing",
            "privileged credential vaulting",
            "break-glass time-based rotation",
        ],
    ),
    ComplianceQuestionSpec(
        id=2,
        topic="IT Asset Management",
        question_text=(
            "IT Asset Management. The contract must require an in-scope asset inventory (including cloud "
            "accounts/subscriptions, workloads, databases, security tooling), define minimum inventory "
            "fields, require at least quarterly reconciliation/review, and require secure configuration "
            "baselines with drift remediation and prohibition of insecure defaults. Based on the contract "
            "language and exhibits, what is the compliance state for IT Asset Management?"
        ),
        keywords=[
            "asset", "inventory", "cloud", "workload", "database",
            "reconciliation", "quarterly", "baseline", "drift",
            "remediation", "insecure defaults",
        ],
        mandatory_subcontrols=[
            "in-scope asset inventory (cloud, workloads, DBs, security tools)",
            "minimum inventory fields",
            "quarterly reconciliation/review",
            "secure configuration baselines & drift remediation",
        ],
    ),
    ComplianceQuestionSpec(
        id=3,
        topic="Security Training & Background Checks",
        question_text=(
            "Security Training & Background Checks. The contract must require security awareness training "
            "on hire and at least annually, and background screening for personnel with access to Company "
            "Data to the extent permitted by law, including maintaining a screening policy and "
            "attestation/evidence. Based on the contract language and exhibits, what is the compliance "
            "state for Security Training and Background Checks?"
        ),
        keywords=[
            "training", "awareness", "hire", "annually", "annual",
            "background", "screening", "personnel", "policy",
            "attestation", "evidence",
        ],
        mandatory_subcontrols=[
            "security awareness training on hire and annually",
            "background screening for personnel with Company Data access",
            "maintained screening policy and attestation/evidence",
        ],
    ),
    ComplianceQuestionSpec(
        id=4,
        topic="Data in Transit Encryption",
        question_text=(
            "Data in Transit Encryption. The contract must require encryption of Company Data in transit "
            "using TLS 1.2+ (preferably TLS 1.3 where feasible) for Company-to-Service traffic, "
            "administrative access pathways, and applicable Service-to-Subprocessor transfers, with "
            "certificate management and avoidance of insecure cipher suites. Based on the contract "
            "language and exhibits, what is the compliance state for Data in Transit Encryption?"
        ),
        keywords=[
            "encryption", "transit", "tls", "1.2", "1.3", "traffic",
            "administrative", "subprocessor", "certificate", "cipher",
        ],
        mandatory_subcontrols=[
            "TLS 1.2+ / TLS 1.3 encryption in transit",
            "covers Company-to-Service, admin pathways, and Subprocessor transfers",
            "certificate management & avoidance of insecure cipher suites",
        ],
    ),
    ComplianceQuestionSpec(
        id=5,
        topic="Network Authentication & Authorization Protocols",
        question_text=(
            "Network Authentication & Authorization Protocols. The contract must specify the "
            "authentication mechanisms (e.g., SAML SSO for users, OAuth/token-based for APIs), require "
            "MFA for privileged/production access, require secure admin pathways (bastion/secure gateway) "
            "with session logging, and require RBAC authorization. Based on the contract language and "
            "exhibits, what is the compliance state for Network Authentication and Authorization Protocols?"
        ),
        keywords=[
            "authentication", "authorization", "saml", "sso", "oauth",
            "token", "mfa", "privileged", "bastion", "gateway",
            "session logging", "rbac", "net-01", "exhibit",
        ],
        mandatory_subcontrols=[
            "SAML SSO for users & OAuth/token for APIs",
            "MFA for privileged/production access",
            "secure admin pathways (bastion/gateway) with session logging",
            "RBAC authorization",
        ],
    ),
]


class RouterDecision(BaseModel):
    """
    Output schema for the Router Agent's decomposition and routing step.

    Attributes:
        question_id (int): ID of the compliance question being routed.
        topic (str): Compliance domain name.
        selected_chunk_ids (List[str]): Ordered list of document chunk IDs selected for context.
        sub_tasks_dispatched (List[str]): Specialized extractors invoked (e.g., 'clause_extractor',
            'table_parser', 'exhibit_cross_reference_resolver').
        routing_rationale (str): Explanation logged for observability audit trails.
    """

    question_id: int
    topic: str
    selected_chunk_ids: List[str] = Field(
        description="IDs of relevant contract sections, tables, or exhibits."
    )
    sub_tasks_dispatched: List[str] = Field(
        description="Downstream processing modules invoked by the Router Agent."
    )
    routing_rationale: str


class EvaluatorCritique(BaseModel):
    """
    Output schema for the Evaluator Agent's quality, consistency, and hallucination verification.

    Attributes:
        is_valid (bool): True if the extraction passes completeness, consistency, and quote checks.
        groundedness_score (float): Ratio [0.0, 1.0] of extracted quotes verified against source chunks.
        calibrated_confidence (int): Final calibrated confidence score [0, 100] after penalizing unverified claims.
        hallucination_detected (bool): True if quotes are fabricated or contradict the contract text.
        critique_notes (str): Detailed explanation of the evaluator's verification checks.
        retry_recommended (bool): Signals the orchestrator to re-prompt the Extractor Agent.
    """

    is_valid: bool
    groundedness_score: float = Field(ge=0.0, le=1.0)
    calibrated_confidence: int = Field(ge=0, le=100)
    hallucination_detected: bool
    critique_notes: str
    retry_recommended: bool = False


class ComplianceItemResult(BaseModel):
    """
    Validated compliance output for a single requirement, strictly conforming to Section 7 Output Schema
    while preserving internal telemetry metadata for the UI and KPI dashboard.
    """

    model_config = ConfigDict(populate_by_name=True)

    compliance_question_id: int
    topic: str
    compliance_question: str = Field(alias="Compliance Question")
    compliance_state: ComplianceStateType = Field(alias="Compliance State")
    confidence: str = Field(
        alias="Confidence",
        description="Formatted confidence percentage string, e.g., '95%'.",
    )
    confidence_score: int = Field(ge=0, le=100)
    relevant_quotes: List[str] = Field(
        alias="Relevant Quotes",
        description="Verbatim extracts with section/exhibit references.",
    )
    rationale: str = Field(
        alias="Rationale",
        description="Reasoning referencing specific contract language and exhibits.",
    )
    routed_chunks: List[str] = Field(default_factory=list)
    evaluator_groundedness: float = 1.0
    hallucination_flag: bool = False
    retries_used: int = 0


class ContractAnalysisReport(BaseModel):
    """
    Complete end-to-end analysis report containing both Section 7 compliance outputs
    and operational/quality KPIs for telemetry persistence.
    """

    trace_id: str
    contract_filename: str
    timestamp: str
    model_used: str
    total_latency_ms: float
    total_tokens: int
    estimated_cost_usd: float
    overall_compliance_rate: float
    mean_confidence: float
    mean_groundedness: float
    needs_review_count: int = 0
    results: List[ComplianceItemResult]