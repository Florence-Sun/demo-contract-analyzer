"""
KPI Telemetry Persistence Layer (SQLite).

Stores operational metrics (latency, token usage, cost) and quality metrics
(calibrated confidence, quote groundedness, evaluator retry rates, hallucination counts,
human-review counts) for every contract analysis run.

On first startup the table is seeded from `data/seed/analysis_runs.json`, which holds real
runs of the provided sample contract (see `scripts/generate_seed_data.py`), so the KPI
dashboard renders immediately without an API key. No synthetic data is generated.
"""

import json
import os
import sqlite3
from typing import Any, Dict, List
import pandas as pd
from src.schemas import ContractAnalysisReport

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "kpi_telemetry.db")
SEED_RUNS_PATH = os.path.join(PROJECT_ROOT, "data", "seed", "analysis_runs.json")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

RUN_COLUMNS: List[str] = [
    "trace_id",
    "contract_filename",
    "timestamp",
    "model_used",
    "total_latency_ms",
    "total_tokens",
    "estimated_cost_usd",
    "overall_compliance_rate",
    "mean_confidence",
    "mean_groundedness",
    "evaluator_retry_count",
    "hallucination_count",
    "needs_review_count",
    "raw_json",
]


def init_db() -> None:
    """
    Initializes the SQLite telemetry schema and loads the committed real-run seed data
    if the table is currently empty.

    Returns:
        None
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_runs (
            trace_id TEXT PRIMARY KEY,
            contract_filename TEXT,
            timestamp TEXT,
            model_used TEXT,
            total_latency_ms REAL,
            total_tokens INTEGER,
            estimated_cost_usd REAL,
            overall_compliance_rate REAL,
            mean_confidence REAL,
            mean_groundedness REAL,
            evaluator_retry_count INTEGER,
            hallucination_count INTEGER,
            needs_review_count INTEGER,
            raw_json TEXT
        )
        """
    )
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM analysis_runs")
    existing_row_count: int = cursor.fetchone()[0]
    if existing_row_count == 0:
        _seed_from_file(conn)

    conn.close()


def _seed_from_file(conn: sqlite3.Connection) -> None:
    """
    Inserts the committed seed runs (real executions) into an empty telemetry table.

    Args:
        conn (sqlite3.Connection): Active SQLite database connection.

    Returns:
        None
    """
    if not os.path.exists(SEED_RUNS_PATH):
        return
    with open(SEED_RUNS_PATH, "r", encoding="utf-8") as seed_file:
        seed_rows: List[Dict[str, Any]] = json.load(seed_file)

    placeholders = ", ".join("?" for _ in RUN_COLUMNS)
    conn.executemany(
        f"INSERT OR IGNORE INTO analysis_runs ({', '.join(RUN_COLUMNS)}) VALUES ({placeholders})",
        [tuple(row.get(col) for col in RUN_COLUMNS) for row in seed_rows],
    )
    conn.commit()


def save_report(report: ContractAnalysisReport) -> None:
    """
    Persists a completed `ContractAnalysisReport` into the SQLite telemetry store.

    Args:
        report (ContractAnalysisReport): Validated analysis report object from the orchestrator.

    Returns:
        None
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    total_retries: int = sum(item.retries_used for item in report.results)
    total_hallucinations: int = sum(1 for item in report.results if item.hallucination_flag)

    placeholders = ", ".join("?" for _ in RUN_COLUMNS)
    cursor.execute(
        f"INSERT OR REPLACE INTO analysis_runs ({', '.join(RUN_COLUMNS)}) VALUES ({placeholders})",
        (
            report.trace_id,
            report.contract_filename,
            report.timestamp,
            report.model_used,
            report.total_latency_ms,
            report.total_tokens,
            report.estimated_cost_usd,
            report.overall_compliance_rate,
            report.mean_confidence,
            report.mean_groundedness,
            total_retries,
            total_hallucinations,
            report.needs_review_count,
            report.model_dump_json(by_alias=True),
        ),
    )
    conn.commit()
    conn.close()


def load_kpi_dataframe() -> pd.DataFrame:
    """
    Loads all contract analysis telemetry records into a Pandas DataFrame.

    Returns:
        pd.DataFrame: Chronologically ordered telemetry records for dashboard plotting.
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM analysis_runs ORDER BY timestamp ASC", conn)
    conn.close()
    return df
