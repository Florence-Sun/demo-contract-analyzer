"""
Generates the committed seed data (real LLM runs of the provided sample contract).

Runs the full pipeline N times on `data/Sample Contract.pdf` using the configured API key, then
exports the resulting telemetry rows and trace logs to `data/seed/`. The app loads these on
first start, so the KPI dashboard and log viewer show real history even without an API key.

WARNING: by default deletes the local telemetry DB, `logs/app.jsonl` and the existing seed files
first. With --append, existing seed runs are kept and the new runs are added to them.

Usage:
    python scripts/generate_seed_data.py --yes [--runs 2] [--append]
"""

import argparse
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.agents import AgenticComplianceOrchestrator, resolve_llm_config  # noqa: E402
from src.database import DB_PATH, RUN_COLUMNS, SEED_RUNS_PATH, init_db, save_report  # noqa: E402
from src.logger import LOG_FILE_PATH, StructuredTracer, seed_logs_if_empty  # noqa: E402
from src.parser import ContractPDFParser  # noqa: E402

SAMPLE_PDF = os.path.join(PROJECT_ROOT, "data", "Sample Contract.pdf")
SEED_LOGS_PATH = os.path.join(PROJECT_ROOT, "data", "seed", "app.seed.jsonl")


def main() -> None:
    """Runs the sample contract through the live pipeline and exports seed files."""
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--runs", type=int, default=2)
    arg_parser.add_argument("--yes", action="store_true", help="confirm deletion of local DB/logs/seeds")
    arg_parser.add_argument("--append", action="store_true", help="keep existing seed runs and add new ones")
    args = arg_parser.parse_args()
    if not args.yes:
        sys.exit("Refusing to run without --yes (this deletes the local DB and logs, and the seeds unless --append).")

    resolve_llm_config()  # fail fast if no API key

    doomed = [DB_PATH, LOG_FILE_PATH] + ([] if args.append else [SEED_RUNS_PATH, SEED_LOGS_PATH])
    for path in doomed:
        if os.path.exists(path):
            os.remove(path)
    os.makedirs(os.path.dirname(SEED_RUNS_PATH), exist_ok=True)
    if args.append:  # reload the kept seed runs/logs into the fresh DB and log file
        init_db()
        seed_logs_if_empty()

    with open(SAMPLE_PDF, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()

    trace_ids = []
    for run_index in range(args.runs):
        tracer = StructuredTracer()
        parsed = ContractPDFParser(tracer).parse_pdf(pdf_bytes, os.path.basename(SAMPLE_PDF))
        report = AgenticComplianceOrchestrator(tracer).analyze_contract(parsed)
        save_report(report)
        trace_ids.append(report.trace_id)
        print(
            f"run {run_index + 1}/{args.runs}: {report.trace_id} "
            f"latency={report.total_latency_ms}ms tokens={report.total_tokens} "
            f"needs_review={report.needs_review_count}",
            file=sys.stderr,
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [
        {col: row[col] for col in RUN_COLUMNS}
        for row in conn.execute("SELECT * FROM analysis_runs ORDER BY timestamp ASC")
    ]
    conn.close()
    with open(SEED_RUNS_PATH, "w", encoding="utf-8") as seed_file:
        json.dump(rows, seed_file, indent=2)

    all_trace_ids = {row["trace_id"] for row in rows}
    with open(LOG_FILE_PATH, "r", encoding="utf-8") as log_file:
        log_lines = [ln for ln in log_file if ln.strip() and json.loads(ln).get("trace_id") in all_trace_ids]
    with open(SEED_LOGS_PATH, "w", encoding="utf-8") as seed_log_file:
        seed_log_file.writelines(log_lines)

    print(f"Wrote {len(rows)} runs to {SEED_RUNS_PATH} and {len(log_lines)} log lines to {SEED_LOGS_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
