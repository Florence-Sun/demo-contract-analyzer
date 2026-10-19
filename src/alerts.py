"""
Threshold-based KPI alert evaluation for the dashboard.

Pure functions over the telemetry DataFrame: no I/O and no pipeline coupling. Thresholds
mirror the KPI Defense Matrix shown in the dashboard. Per-run KPIs are checked on the most
recent run, and the retry rate is checked over a rolling window of recent runs.
"""

from typing import List, NamedTuple
import pandas as pd

LATENCY_ALERT_MS: float = 60_000.0
COST_ALERT_USD: float = 0.10
GROUNDEDNESS_FLOOR: float = 0.80
CONFIDENCE_FLOOR: float = 75.0
RETRY_RATE_ALERT_PCT: float = 25.0
ROLLING_WINDOW: int = 10


class Alert(NamedTuple):
    """
    A single KPI threshold breach.

    Attributes:
        severity (str): 'critical' (quality/reliability) or 'warning' (operational/cost).
        kpi (str): KPI name, matching the KPI Defense Matrix.
        message (str): Human-readable breach description with the measured value.
    """

    severity: str
    kpi: str
    message: str


def evaluate_alerts(df: pd.DataFrame) -> List[Alert]:
    """
    Checks the latest run (and a rolling window for retry rate) against the alert thresholds.

    Args:
        df (pd.DataFrame): Telemetry records in chronological order (see `load_kpi_dataframe`).

    Returns:
        List[Alert]: Active alerts, critical first. Empty if everything is within thresholds.
    """
    if df.empty:
        return []

    latest = df.iloc[-1]
    run = latest["trace_id"]
    alerts: List[Alert] = []

    needs_review = int(latest.get("needs_review_count") or 0)
    if needs_review > 0:
        alerts.append(
            Alert("critical", "Needs-Review Rate",
                  f"{needs_review} question(s) in run {run} could not be analyzed after retries "
                  "(Needs Human Review). Check provider status/keys and the CRITICAL events in the logs.")
        )

    hallucinations = int(latest.get("hallucination_count") or 0)
    if hallucinations > 0:
        alerts.append(
            Alert("critical", "Hallucination Count",
                  f"{hallucinations} question(s) in run {run} had ungrounded quotes flagged by the Evaluator.")
        )

    if needs_review == 0 and latest["mean_groundedness"] < GROUNDEDNESS_FLOOR:
        alerts.append(
            Alert("critical", "Citation Groundedness",
                  f"Groundedness {latest['mean_groundedness'] * 100:.0f}% in run {run} "
                  f"is below the {GROUNDEDNESS_FLOOR * 100:.0f}% floor.")
        )

    if needs_review == 0 and latest["mean_confidence"] < CONFIDENCE_FLOOR:
        alerts.append(
            Alert("warning", "Confidence Calibration Drift",
                  f"Mean confidence {latest['mean_confidence']:.1f}% in run {run} "
                  f"is below {CONFIDENCE_FLOOR:.0f}%; the contract may be out of distribution.")
        )

    if latest["total_latency_ms"] > LATENCY_ALERT_MS:
        alerts.append(
            Alert("warning", "End-to-End Latency",
                  f"Run {run} took {latest['total_latency_ms'] / 1000:.0f} s "
                  f"(alert above {LATENCY_ALERT_MS / 1000:.0f} s).")
        )

    if latest["estimated_cost_usd"] > COST_ALERT_USD:
        alerts.append(
            Alert("warning", "Cost per Contract",
                  f"Run {run} cost an estimated ${latest['estimated_cost_usd']:.3f} "
                  f"(alert above ${COST_ALERT_USD:.2f}).")
        )

    window = df.tail(ROLLING_WINDOW)
    retry_rate = float((window["evaluator_retry_count"] > 0).mean() * 100)
    if retry_rate > RETRY_RATE_ALERT_PCT:
        alerts.append(
            Alert("warning", "Evaluator Retry Rate",
                  f"{retry_rate:.0f}% of the last {len(window)} runs needed an evaluator retry "
                  f"(alert above {RETRY_RATE_ALERT_PCT:.0f}%).")
        )

    return sorted(alerts, key=lambda a: a.severity != "critical")
