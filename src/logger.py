"""
Structured Observability and Trace Logging Module.

Provides production-grade JSON-formatted logging with correlation IDs (`trace_id`
for end-to-end request tracking and `span_id` for per-agent step isolation).
Logs are simultaneously emitted to standard output (for live terminal walkthroughs)
and appended to a persistent JSON Lines file (`logs/app.jsonl`).
"""

import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Resolve path to logs/app.jsonl relative to project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE_PATH = os.path.join(PROJECT_ROOT, "logs", "app.jsonl")
os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
_LOG_WRITE_LOCK = threading.Lock()


class StructuredTracer:
    """
    Manages distributed-style trace and span logging across the Agentic RAG pipeline.

    Attributes:
        trace_id (str): Unique correlation identifier assigned to a single contract analysis run.
    """

    def __init__(self, trace_id: Optional[str] = None) -> None:
        """
        Initializes the tracer with an explicit or auto-generated trace ID.

        Args:
            trace_id (Optional[str]): Custom trace identifier. Defaults to 'tr-<12-hex-chars>'.
        """
        self.trace_id: str = trace_id or f"tr-{uuid.uuid4().hex[:12]}"

    def log_event(
        self,
        component: str,
        event_type: str,
        message: str,
        level: str = "INFO",
        span_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Creates and persists a structured JSON log event.

        Args:
            component (str): System module emitting the log (e.g., 'RouterAgent', 'EvaluatorAgent').
            event_type (str): Discrete lifecycle event code (e.g., 'ROUTE_DECISION', 'RETRY_TRIGGERED').
            message (str): Human-readable summary of the decision or operation.
            level (str): Severity level ('INFO', 'WARN', 'ERROR'). Defaults to 'INFO'.
            span_id (Optional[str]): Sub-operation identifier. Auto-generated if omitted.
            metadata (Optional[Dict[str, Any]]): Arbitrary structured telemetry payload (latencies,
                chunk IDs, confidence scores, token counts).

        Returns:
            Dict[str, Any]: The constructed log payload dictionary.
        """
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "trace_id": self.trace_id,
            "span_id": span_id or f"sp-{uuid.uuid4().hex[:8]}",
            "component": component,
            "event_type": event_type,
            "message": message,
            "metadata": metadata or {},
        }

        serialized_line: str = json.dumps(entry)

        # Questions are analyzed concurrently; the lock keeps each JSON line intact.
        with _LOG_WRITE_LOCK:
            # 1. Emit to console stdout for real-time terminal monitoring during live interview
            sys.stdout.write(serialized_line + "\n")
            sys.stdout.flush()

            # 2. Persist to JSONL file for observability platforms and UI log viewer
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as log_file:
                log_file.write(serialized_line + "\n")

        return entry


def seed_logs_if_empty() -> None:
    """
    Copies the committed real-run trace logs (`data/seed/app.seed.jsonl`) into `logs/app.jsonl`
    when that file is missing or empty, so the log viewer has real traces on a fresh install.

    Returns:
        None
    """
    seed_path = os.path.join(PROJECT_ROOT, "data", "seed", "app.seed.jsonl")
    if not os.path.exists(seed_path):
        return
    if os.path.exists(LOG_FILE_PATH) and os.path.getsize(LOG_FILE_PATH) > 0:
        return
    with open(seed_path, "r", encoding="utf-8") as seed_file:
        content = seed_file.read()
    with open(LOG_FILE_PATH, "w", encoding="utf-8") as log_file:
        log_file.write(content)


def read_recent_logs(trace_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """
    Reads and filters recent structured JSON log entries from disk.

    Args:
        trace_id (Optional[str]): If provided, filters logs to only those matching this trace ID.
        limit (int): Maximum number of most recent log records to return. Defaults to 200.

    Returns:
        List[Dict[str, Any]]: Chronological list of parsed JSON log objects.
    """
    if not os.path.exists(LOG_FILE_PATH):
        return []

    matching_entries: List[Dict[str, Any]] = []
    with open(LOG_FILE_PATH, "r", encoding="utf-8") as log_file:
        for raw_line in log_file:
            clean_line = raw_line.strip()
            if not clean_line:
                continue
            try:
                parsed_record: Dict[str, Any] = json.loads(clean_line)
                if trace_id is None or parsed_record.get("trace_id") == trace_id:
                    matching_entries.append(parsed_record)
            except json.JSONDecodeError:
                continue

    return matching_entries[-limit:]