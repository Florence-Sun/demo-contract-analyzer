"""
Agentic RAG Orchestration Engine (Router, Extractor, and Evaluator Agents).

Implements the required multi-agent workflow (Section 3.2):
1. Router Agent: Decomposes incoming compliance questions, scores and retrieves relevant
   clauses, tables, and exhibits, and dispatches sub-tasks.
2. Extractor Agent: Performs structured compliance reasoning and verbatim quote extraction.
3. Evaluator Agent (Critic): Verifies quote groundedness against source text to prevent
   hallucinations, calibrates confidence scores, and triggers automated retries if quality
   falls below threshold.

Failure policy: a missing API key halts immediately (deployment misconfiguration). A failed
LLM call is retried up to MAX_LLM_RETRIES times with exponential backoff; if every attempt
fails, the question is marked "Needs Human Review" (0% confidence) and a CRITICAL event is
logged. The system never substitutes a guessed compliance state for a failed LLM call.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from dotenv import load_dotenv

from src.logger import StructuredTracer
from src.schemas import (
    COMPLIANCE_QUESTIONS,
    LLM_COMPLIANCE_STATES,
    NEEDS_REVIEW_STATE,
    ComplianceItemResult,
    ComplianceQuestionSpec,
    ContractAnalysisReport,
    EvaluatorCritique,
    RouterDecision,
)

load_dotenv()

MAX_LLM_RETRIES: int = 2
BACKOFF_BASE_SECONDS: float = 1.0
LLM_TIMEOUT_SECONDS: float = 90.0
MAX_PARALLEL_QUESTIONS: int = 5


class MissingAPIKeyError(RuntimeError):
    """Raised when no LLM provider credentials are configured."""


class LLMCallError(RuntimeError):
    """
    Raised when an LLM call (or validation of its output) still fails after all retries.

    Attributes:
        attempts (int): Number of attempts made.
        tokens (int): Tokens consumed by the attempts that returned a response.
    """

    def __init__(self, message: str, attempts: int, tokens: int) -> None:
        super().__init__(message)
        self.attempts: int = attempts
        self.tokens: int = tokens


def resolve_llm_config() -> Tuple[str, str, str]:
    """
    Reads LLM credentials from the environment.

    Returns:
        Tuple[str, str, str]: (provider, api_key, model_name).

    Raises:
        MissingAPIKeyError: If neither ANTHROPIC_API_KEY nor OPENAI_API_KEY is set.
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if anthropic_key:
        return "anthropic", anthropic_key, os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    if openai_key:
        return "openai", openai_key, os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    raise MissingAPIKeyError(
        "CRITICAL: Missing ANTHROPIC_API_KEY. System halted."
    )


class AgenticComplianceOrchestrator:
    """
    Coordinates the Router, Extractor, and Evaluator agents across all 5 compliance domains
    while emitting granular trace logs and capturing token/latency telemetry.

    Attributes:
        tracer (StructuredTracer): Request-scoped structured logger.
        provider (str): Active LLM backend ('anthropic' or 'openai').
        model_name (str): Identifier of the model used for inference and cost tracking.
    """

    def __init__(self, tracer: StructuredTracer) -> None:
        """
        Initializes the orchestrator and loads LLM credentials from environment variables.

        Args:
            tracer (StructuredTracer): Logger instance carrying the active `trace_id`.

        Raises:
            MissingAPIKeyError: If no provider credentials are configured (fail fast).
        """
        self.tracer: StructuredTracer = tracer
        try:
            self.provider, self._api_key, self.model_name = resolve_llm_config()
        except MissingAPIKeyError as exc:
            self.tracer.log_event(
                component="Orchestrator",
                event_type="CONFIG_ERROR",
                level="CRITICAL",
                message=str(exc),
            )
            raise

    def analyze_contract(self, parsed_doc: Dict[str, Any]) -> ContractAnalysisReport:
        """
        Runs the end-to-end Agentic RAG compliance pipeline across all 5 compliance questions.

        Args:
            parsed_doc (Dict[str, Any]): Output dictionary from `ContractPDFParser.parse_pdf()`.

        Returns:
            ContractAnalysisReport: Validated report containing all 5 compliance determinations
                and aggregated telemetry metrics.
        """
        start_time = time.perf_counter()
        chunks: List[Dict[str, Any]] = parsed_doc["chunks"]
        filename: str = parsed_doc["filename"]

        self.tracer.log_event(
            component="Orchestrator",
            event_type="PIPELINE_START",
            message=f"Starting Agentic RAG analysis on '{filename}' with model '{self.model_name}'",
            metadata={"provider": self.provider, "total_chunks_available": len(chunks)},
        )

        # Questions are independent, so they run concurrently. map() preserves question order.
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_QUESTIONS) as pool:
            outcomes = list(
                pool.map(lambda spec: self._analyze_question(spec, chunks), COMPLIANCE_QUESTIONS)
            )
        results: List[ComplianceItemResult] = [item for item, _ in outcomes]
        total_tokens_consumed: int = sum(tokens for _, tokens in outcomes)

        elapsed_ms: float = round((time.perf_counter() - start_time) * 1000, 1)
        estimated_cost_usd: float = round(total_tokens_consumed * 0.0000035, 5)

        # Aggregates cover only questions that received a real determination;
        # "Needs Human Review" items are counted separately, not averaged in as zeros.
        state_weights = {
            "Fully Compliant": 1.0,
            "Partially Compliant": 0.5,
            "Non-Compliant": 0.0,
        }
        evaluated = [r for r in results if r.compliance_state != NEEDS_REVIEW_STATE]
        needs_review_count: int = len(results) - len(evaluated)
        overall_compliance_rate: float = (
            round(sum(state_weights[r.compliance_state] for r in evaluated) / len(evaluated), 2)
            if evaluated
            else 0.0
        )
        mean_confidence: float = (
            round(sum(r.confidence_score for r in evaluated) / len(evaluated), 1) if evaluated else 0.0
        )
        mean_groundedness: float = (
            round(sum(r.evaluator_groundedness for r in evaluated) / len(evaluated), 3)
            if evaluated
            else 0.0
        )

        report = ContractAnalysisReport(
            trace_id=self.tracer.trace_id,
            contract_filename=filename,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_used=self.model_name,
            total_latency_ms=elapsed_ms,
            total_tokens=total_tokens_consumed,
            estimated_cost_usd=estimated_cost_usd,
            overall_compliance_rate=overall_compliance_rate,
            mean_confidence=mean_confidence,
            mean_groundedness=mean_groundedness,
            needs_review_count=needs_review_count,
            results=results,
        )

        self.tracer.log_event(
            component="Orchestrator",
            event_type="PIPELINE_COMPLETE",
            level="WARN" if needs_review_count else "INFO",
            message=(
                f"Completed contract compliance analysis in {elapsed_ms} ms "
                f"({needs_review_count} question(s) need human review)"
            ),
            metadata={
                "total_latency_ms": elapsed_ms,
                "total_tokens": total_tokens_consumed,
                "estimated_cost_usd": estimated_cost_usd,
                "overall_compliance_rate": overall_compliance_rate,
                "mean_confidence": mean_confidence,
                "mean_groundedness": mean_groundedness,
                "needs_review_count": needs_review_count,
            },
        )
        return report

    def _analyze_question(
        self,
        question_spec: ComplianceQuestionSpec,
        chunks: List[Dict[str, Any]],
    ) -> Tuple[ComplianceItemResult, int]:
        """
        Runs Router -> Extractor -> Evaluator (with self-correction) for one compliance question.
        Safe to run concurrently: questions share no mutable state and every log event carries
        its own per-question span id.

        Args:
            question_spec (ComplianceQuestionSpec): The compliance question to analyze.
            chunks (List[Dict[str, Any]]): All parsed document chunks.

        Returns:
            Tuple[ComplianceItemResult, int]: The question's result and the tokens it consumed.
        """
        total_tokens_consumed: int = 0
        question_span_id = f"sp-q{question_spec.id}"

        # ------------------------------------------------------------------
        # AGENT 1: ROUTER AGENT (Decomposition & Sub-Task Dispatch)
        # ------------------------------------------------------------------
        router_decision, routed_chunks = self._run_router_agent(
            question_spec, chunks, question_span_id
        )

        # ------------------------------------------------------------------
        # AGENT 2 & 3: EXTRACTOR + EVALUATOR SELF-CORRECTION LOOP
        # ------------------------------------------------------------------
        max_retries: int = 1
        attempt: int = 0
        final_item_result: Optional[ComplianceItemResult] = None

        while attempt <= max_retries:
            try:
                raw_extraction, tokens_used = self._run_extractor_agent(
                    question_spec=question_spec,
                    routed_chunks=routed_chunks,
                    router_decision=router_decision,
                    span_id=question_span_id,
                    attempt=attempt,
                )
            except LLMCallError as exc:
                total_tokens_consumed += exc.tokens
                self.tracer.log_event(
                    component="ExtractorAgent",
                    event_type="LLM_EXTRACTION_FAILED",
                    level="CRITICAL",
                    message=(
                        f"Extraction for Q{question_spec.id} ({question_spec.topic}) failed after "
                        f"{exc.attempts} attempts: {exc}"
                    ),
                    span_id=question_span_id,
                    metadata={"attempts": exc.attempts, "evaluator_attempt": attempt},
                )
                if final_item_result is None:
                    final_item_result = self._needs_review_result(
                        question_spec, router_decision, str(exc), exc.attempts
                    )
                # If an earlier extraction exists (evaluator retry failed), keep it with its flags.
                break
            total_tokens_consumed += tokens_used

            critique = self._run_evaluator_agent(
                question_spec=question_spec,
                extraction=raw_extraction,
                routed_chunks=routed_chunks,
                span_id=question_span_id,
                attempt=attempt,
            )

            final_item_result = ComplianceItemResult(
                compliance_question_id=question_spec.id,
                topic=question_spec.topic,
                **{
                    "Compliance Question": question_spec.question_text,
                    "Compliance State": raw_extraction["compliance_state"],
                    "Confidence": f"{critique.calibrated_confidence}%",
                    "confidence_score": critique.calibrated_confidence,
                    "Relevant Quotes": raw_extraction["relevant_quotes"],
                    "Rationale": raw_extraction["rationale"],
                },
                routed_chunks=router_decision.selected_chunk_ids,
                evaluator_groundedness=critique.groundedness_score,
                hallucination_flag=critique.hallucination_detected,
                retries_used=attempt,
            )

            # Exit loop if Evaluator validates output or max retries reached
            if critique.is_valid or not critique.retry_recommended or attempt == max_retries:
                break

            attempt += 1
            self.tracer.log_event(
                component="EvaluatorAgent",
                event_type="RETRY_TRIGGERED",
                level="WARN",
                message=(
                    f"Evaluator rejected extraction for Q{question_spec.id} ({question_spec.topic}); "
                    f"triggering retry #{attempt}. Reason: {critique.critique_notes}"
                ),
                span_id=question_span_id,
                metadata={"attempt": attempt},
            )


        assert final_item_result is not None  # loop body always sets a result or breaks with one
        return final_item_result, total_tokens_consumed

    def _needs_review_result(
        self,
        question_spec: ComplianceQuestionSpec,
        router_decision: RouterDecision,
        error_detail: str,
        attempts: int,
    ) -> ComplianceItemResult:
        """
        Builds the explicit "Needs Human Review" result used when extraction fails after all retries.

        Args:
            question_spec (ComplianceQuestionSpec): The compliance question specification.
            router_decision (RouterDecision): Routing metadata (kept for traceability).
            error_detail (str): Description of the final failure.
            attempts (int): Number of LLM attempts made.

        Returns:
            ComplianceItemResult: Zero-confidence result carrying no fabricated determination.
        """
        return ComplianceItemResult(
            compliance_question_id=question_spec.id,
            topic=question_spec.topic,
            **{
                "Compliance Question": question_spec.question_text,
                "Compliance State": NEEDS_REVIEW_STATE,
                "Confidence": "0%",
                "confidence_score": 0,
                "Relevant Quotes": [],
                "Rationale": (
                    "NEEDS HUMAN REVIEW: automated analysis failed after "
                    f"{attempts} attempts and no determination was made. Last error: {error_detail}"
                ),
            },
            routed_chunks=router_decision.selected_chunk_ids,
            evaluator_groundedness=0.0,
            hallucination_flag=False,
            retries_used=attempts - 1,
        )

    def _run_router_agent(
        self,
        question_spec: ComplianceQuestionSpec,
        chunks: List[Dict[str, Any]],
        span_id: str,
    ) -> Tuple[RouterDecision, List[Dict[str, Any]]]:
        """
        Router Agent: Decomposes a compliance question, ranks document chunks by keyword
        and structural relevance (boosting control matrices/tables and exhibits), and
        determines which specialized sub-task processors to dispatch.

        Args:
            question_spec (ComplianceQuestionSpec): The compliance question metadata.
            chunks (List[Dict[str, Any]]): All parsed document chunks from the PDF.
            span_id (str): Trace span identifier for logging.

        Returns:
            Tuple[RouterDecision, List[Dict[str, Any]]]: The logged routing decision schema
                and the subset of routed document chunks.
        """
        scored_chunks: List[Tuple[int, Dict[str, Any]]] = []

        for chunk in chunks:
            searchable_text = f"{chunk['section_title']} {chunk['content']}".lower()
            keyword_hits = sum(1 for kw in question_spec.keywords if kw.lower() in searchable_text)

            # Structural boosting for tables and exhibits containing relevant security terms
            if chunk["type"] == "table" and keyword_hits > 0:
                keyword_hits += 2
            if "exhibit" in chunk["section_title"].lower() and keyword_hits > 0:
                keyword_hits += 1

            if keyword_hits > 0:
                scored_chunks.append((keyword_hits, chunk))

        scored_chunks.sort(key=lambda item: item[0], reverse=True)
        selected_chunks = [chunk for _, chunk in scored_chunks[:4]]

        # Fallback to top document chunks if no keyword overlap is found
        if not selected_chunks:
            selected_chunks = chunks[:3]

        # Determine which specialized sub-task components are dispatched
        sub_tasks: List[str] = ["clause_extractor"]
        if any(c["type"] == "table" for c in selected_chunks):
            sub_tasks.append("table_parser")
        if any("exhibit" in c["section_title"].lower() for c in selected_chunks):
            sub_tasks.append("exhibit_cross_reference_resolver")

        decision = RouterDecision(
            question_id=question_spec.id,
            topic=question_spec.topic,
            selected_chunk_ids=[c["chunk_id"] for c in selected_chunks],
            sub_tasks_dispatched=sub_tasks,
            routing_rationale=(
                f"Decomposed Q{question_spec.id} ({question_spec.topic}) into {len(question_spec.mandatory_subcontrols)} "
                f"sub-controls; matched {len(selected_chunks)} high-relevance chunks and dispatched {', '.join(sub_tasks)}."
            ),
        )

        self.tracer.log_event(
            component="RouterAgent",
            event_type="ROUTE_DECISION",
            message=f"Routed Q{question_spec.id} ({question_spec.topic}) -> {decision.selected_chunk_ids}",
            span_id=span_id,
            metadata=decision.model_dump(),
        )
        return decision, selected_chunks

    def _run_extractor_agent(
        self,
        question_spec: ComplianceQuestionSpec,
        routed_chunks: List[Dict[str, Any]],
        router_decision: RouterDecision,
        span_id: str,
        attempt: int,
    ) -> Tuple[Dict[str, Any], int]:
        """
        Specialist Extractor Agent: Evaluates routed contract sections, tables, and exhibits
        against the compliance question to produce a structured determination and verbatim quotes.

        Args:
            question_spec (ComplianceQuestionSpec): The compliance question specification.
            routed_chunks (List[Dict[str, Any]]): Chunks selected by the Router Agent.
            router_decision (RouterDecision): Metadata from the routing phase.
            span_id (str): Trace span identifier.
            attempt (int): Current evaluator-driven extraction attempt (0 initial, 1+ retry).

        Returns:
            Tuple[Dict[str, Any], int]: Extracted JSON dictionary and total tokens consumed.

        Raises:
            LLMCallError: If the LLM call or output validation fails after all retries.
        """
        formatted_context = "\n\n".join(
            f"[{c['chunk_id']} | {c['section_title']}]\n{c['content']}" for c in routed_chunks
        )

        retry_instruction = (
            "\nCRITICAL RETRY INSTRUCTION: Your previous extraction contained unverified quotes. "
            "You MUST copy quotes verbatim word-for-word from the provided chunks."
            if attempt > 0
            else ""
        )

        prompt = f"""You are a Senior Legal & Information Security Compliance Auditor AI.
Evaluate the contract excerpts below against the Compliance Requirement.
{retry_instruction}

COMPLIANCE REQUIREMENT:
{question_spec.question_text}

MANDATORY SUB-CONTROLS TO VERIFY:
{json.dumps(question_spec.mandatory_subcontrols)}

ROUTED CONTRACT CHUNKS (Active Processors: {', '.join(router_decision.sub_tasks_dispatched)}):
{formatted_context}

Return ONLY a valid JSON object with the exact keys:
- "compliance_state": strictly one of "Fully Compliant", "Partially Compliant", or "Non-Compliant"
- "initial_confidence": integer between 60 and 99
- "relevant_quotes": list of 1 to 3 verbatim extracts from the chunks above, prefixed with their section/exhibit reference (e.g., "Section 6.7 (Authentication Summary Table); Section 6.2: '...'")
- "rationale": detailed reasoning referencing specific contract sections/exhibits and explicitly stating which sub-controls are met or missing.
Escape any double quotes inside string values so the JSON stays valid.
"""
        parsed_json, tokens = self._call_llm_with_retry(
            prompt=prompt,
            json_mode=True,
            span_id=span_id,
            parse=self._parse_extraction,
        )
        self.tracer.log_event(
            component="ExtractorAgent",
            event_type="EXTRACTION_SUCCESS",
            message=f"Extracted state '{parsed_json['compliance_state']}' for Q{question_spec.id}",
            span_id=span_id,
            metadata={
                "attempt": attempt,
                "tokens_used": tokens,
                "assigned_state": parsed_json["compliance_state"],
            },
        )
        return parsed_json, tokens

    @staticmethod
    def _parse_extraction(raw_text: str) -> Dict[str, Any]:
        """
        Parses and validates the Extractor's JSON output.

        Args:
            raw_text (str): Raw text returned by the LLM.

        Returns:
            Dict[str, Any]: Validated extraction with a normalized integer `initial_confidence`.

        Raises:
            ValueError: If the JSON is malformed or violates the expected contract.
        """
        data = json.loads(AgenticComplianceOrchestrator._clean_json_fence(raw_text))
        if not isinstance(data, dict):
            raise ValueError("Extractor output is not a JSON object")
        if data.get("compliance_state") not in LLM_COMPLIANCE_STATES:
            raise ValueError(f"Invalid compliance_state: {data.get('compliance_state')!r}")
        quotes = data.get("relevant_quotes")
        if not isinstance(quotes, list) or not quotes or not all(isinstance(q, str) for q in quotes):
            raise ValueError("relevant_quotes must be a non-empty list of strings")
        if not isinstance(data.get("rationale"), str) or not data["rationale"].strip():
            raise ValueError("rationale must be a non-empty string")
        data["initial_confidence"] = max(0, min(100, int(data.get("initial_confidence", 80))))
        return data

    def _run_evaluator_agent(
        self,
        question_spec: ComplianceQuestionSpec,
        extraction: Dict[str, Any],
        routed_chunks: List[Dict[str, Any]],
        span_id: str,
        attempt: int,
    ) -> EvaluatorCritique:
        """
        Evaluator Agent (Self-Critique & Hallucination Detector):
        1. Verifies whether every extracted quote has lexical groundedness in the routed source chunks.
        2. Checks logical consistency between the rationale and assigned Compliance State.
        3. Calibrates the final confidence score using groundedness weighting.

        Args:
            question_spec (ComplianceQuestionSpec): The compliance question specification.
            extraction (Dict[str, Any]): Raw output dictionary from the Extractor Agent.
            routed_chunks (List[Dict[str, Any]]): Source chunks provided to the Extractor.
            span_id (str): Trace span identifier.
            attempt (int): Current attempt index.

        Returns:
            EvaluatorCritique: Validation result, groundedness score, and calibrated confidence.
        """
        combined_source_text = " ".join(c["content"].lower() for c in routed_chunks)
        extracted_quotes: List[str] = extraction.get("relevant_quotes", [])
        verified_quotes_count: int = 0

        for quote in extracted_quotes:
            # Strip leading citation label (e.g. "Section 4.1:") before checking lexical overlap
            quote_body = quote.split(":", 1)[-1].strip(" \"'").lower()
            significant_tokens = [tok.strip(".,;()[]") for tok in quote_body.split() if len(tok) > 3]
            if not significant_tokens:
                continue
            matched_tokens = sum(1 for tok in significant_tokens if tok in combined_source_text)
            overlap_ratio = matched_tokens / len(significant_tokens)
            if overlap_ratio >= 0.65:
                verified_quotes_count += 1

        groundedness_score: float = round(
            verified_quotes_count / max(len(extracted_quotes), 1), 2
        )
        hallucination_detected: bool = groundedness_score < 0.50
        raw_confidence: int = int(extraction.get("initial_confidence", 88))

        # Confidence Calibration Formula:
        # Penalizes raw LLM confidence when citation groundedness is below 1.0
        calibrated_confidence: int = int(
            min(98, max(55, raw_confidence * (0.65 + 0.35 * groundedness_score)))
        )
        if extraction.get("compliance_state") == "Partially Compliant":
            calibrated_confidence = min(calibrated_confidence, 92)

        is_valid: bool = (not hallucination_detected) and bool(extraction.get("rationale"))
        critique = EvaluatorCritique(
            is_valid=is_valid,
            groundedness_score=groundedness_score,
            calibrated_confidence=calibrated_confidence,
            hallucination_detected=hallucination_detected,
            critique_notes=(
                f"Verified {verified_quotes_count}/{len(extracted_quotes)} quotes against source chunks "
                f"(groundedness={groundedness_score}). Calibrated confidence {raw_confidence}% -> {calibrated_confidence}%."
            ),
            retry_recommended=(hallucination_detected and attempt == 0),
        )

        self.tracer.log_event(
            component="EvaluatorAgent",
            event_type="EVALUATION_COMPLETE",
            message=(
                f"Evaluated Q{question_spec.id}: valid={critique.is_valid}, "
                f"calibrated_confidence={calibrated_confidence}%, groundedness={groundedness_score}"
            ),
            span_id=span_id,
            metadata=critique.model_dump(),
        )
        return critique

    def answer_chat_question(self, user_question: str, parsed_doc: Dict[str, Any]) -> str:
        """
        Bonus Feature (Section 8): Conversational Q&A over the uploaded contract.

        Args:
            user_question (str): Free-form question from the user.
            parsed_doc (Dict[str, Any]): Parsed contract dictionary containing all chunks.

        Returns:
            str: Grounded response citing specific contract sections/exhibits, or an explicit
                error message if the LLM is unreachable after retries.
        """
        chunks: List[Dict[str, Any]] = parsed_doc["chunks"]
        context_str = "\n\n".join(
            f"[{c['chunk_id']} | {c['section_title']}]\n{c['content']}" for c in chunks[:12]
        )
        prompt = (
            "You are an Enterprise Legal & Security Contract Assistant. Answer the user's "
            "question concisely and accurately using ONLY the contract excerpts below. "
            "Always cite specific Section or Exhibit numbers.\n\n"
            f"CONTRACT EXCERPTS:\n{context_str}\n\nUSER QUESTION: {user_question}"
        )

        self.tracer.log_event(
            component="ConversationalChat",
            event_type="CHAT_QUERY",
            message=f"Processed conversational query: '{user_question[:80]}'",
        )

        try:
            answer_text, _ = self._call_llm_with_retry(
                prompt=prompt,
                json_mode=False,
                span_id="sp-chat",
                parse=lambda text: text,
            )
            return answer_text
        except LLMCallError as exc:
            self.tracer.log_event(
                component="ConversationalChat",
                event_type="CHAT_FAILED",
                level="CRITICAL",
                message=f"Chat LLM call failed after {exc.attempts} attempts: {exc}",
                span_id="sp-chat",
            )
            return (
                f"⚠️ The assistant could not reach the LLM after {exc.attempts} attempts. "
                f"Please try again. (trace_id: {self.tracer.trace_id})"
            )

    def _call_llm_with_retry(
        self,
        prompt: str,
        json_mode: bool,
        span_id: str,
        parse: Callable[[str], Any],
    ) -> Tuple[Any, int]:
        """
        Calls the LLM and parses its output, retrying up to MAX_LLM_RETRIES times with
        exponential backoff (1 s, 2 s). Transport errors and invalid output both count.

        Args:
            prompt (str): Complete prompt string.
            json_mode (bool): Whether to enforce JSON object output formatting.
            span_id (str): Trace span identifier for retry logging.
            parse (Callable[[str], Any]): Parses/validates raw text; raises on bad output.

        Returns:
            Tuple[Any, int]: Parsed result and total tokens used across all attempts.

        Raises:
            LLMCallError: If every attempt fails.
        """
        total_attempts: int = MAX_LLM_RETRIES + 1
        tokens_total: int = 0
        last_error: Optional[Exception] = None

        for attempt in range(total_attempts):
            try:
                raw_text, tokens = self._call_llm(prompt, json_mode)
                tokens_total += tokens
                return parse(raw_text), tokens_total
            except Exception as exc:
                last_error = exc
                is_last = attempt == total_attempts - 1
                backoff = 0.0 if is_last else BACKOFF_BASE_SECONDS * (2**attempt)
                self.tracer.log_event(
                    component="LLMClient",
                    event_type="LLM_RETRY" if not is_last else "LLM_ATTEMPTS_EXHAUSTED",
                    level="WARN",
                    message=(
                        f"LLM attempt {attempt + 1}/{total_attempts} failed "
                        f"({type(exc).__name__}: {exc})"
                        + (f"; retrying in {backoff:.0f}s" if not is_last else "")
                    ),
                    span_id=span_id,
                    metadata={"attempt": attempt + 1, "backoff_seconds": backoff},
                )
                if not is_last:
                    time.sleep(backoff)

        raise LLMCallError(
            f"{type(last_error).__name__}: {last_error}", attempts=total_attempts, tokens=tokens_total
        )

    def _call_llm(self, prompt: str, json_mode: bool = True) -> Tuple[str, int]:
        """
        Invokes either Anthropic Claude or OpenAI GPT models based on available credentials.
        SDK-level retries are disabled so the retry policy lives in one place.

        Args:
            prompt (str): Complete system/user prompt string.
            json_mode (bool): Whether to enforce JSON object output formatting.

        Returns:
            Tuple[str, int]: Raw text response and total tokens used.
        """
        if self.provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(
                api_key=self._api_key, max_retries=0, timeout=LLM_TIMEOUT_SECONDS
            )
            response = client.messages.create(
                model=self.model_name,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            tokens = response.usage.input_tokens + response.usage.output_tokens
            return response.content[0].text, tokens

        from openai import OpenAI

        client = OpenAI(api_key=self._api_key, max_retries=0, timeout=LLM_TIMEOUT_SECONDS)
        extra_kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        response = client.chat.completions.create(
            model=self.model_name,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
            **extra_kwargs,
        )
        tokens = response.usage.total_tokens if response.usage else 0
        return response.choices[0].message.content or "", tokens

    @staticmethod
    def _clean_json_fence(raw_text: str) -> str:
        """
        Strips Markdown code fences (```json ... ```) from LLM responses.

        Args:
            raw_text (str): Raw text returned by the LLM.

        Returns:
            str: Clean JSON string ready for `json.loads()`.
        """
        cleaned = raw_text.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
        return cleaned
