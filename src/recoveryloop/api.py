from __future__ import annotations

# Demo-grade FastAPI layer — no auth, no rate limiting, no production hardening.
# Intentionally minimal for the buildathon demo.

from pathlib import Path

from fastapi import FastAPI, HTTPException

from recoveryloop.audit.audit_log import AuditLog
from recoveryloop.eval.harness import EvalReport, run_eval
from recoveryloop.executor.razorpay_client import RazorpayPaymentLinkClient
from recoveryloop.notifier import RazorpayStatusNotifier
from recoveryloop.pipeline import run_pipeline
from recoveryloop.schema import AuditRecord, FailureEvent

app = FastAPI(
    title="RecoveryLoop",
    description="Bounded revenue-recovery agent for the Razorpay AI Buildathon",
    version="0.0.1",
)

_client = RazorpayPaymentLinkClient()
_notifier = RazorpayStatusNotifier()
_audit = AuditLog(db_path=Path("data/audit.db"))

_BATCH = Path("data/synthetic_batch.json")


@app.post("/events", response_model=AuditRecord)
def post_event(event: FailureEvent) -> AuditRecord:
    """Accept a FailureEvent and run the full recovery pipeline."""
    return run_pipeline(event, _client, _notifier, _audit)


@app.get("/audit/{record_id}", response_model=AuditRecord)
def get_audit(record_id: str) -> AuditRecord:
    """Retrieve a persisted AuditRecord by its id."""
    record = _audit.get_by_id(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="record not found")
    return record


@app.get("/metrics", response_model=EvalReport)
def get_metrics() -> EvalReport:
    """Return the current eval report. Recomputed on request (fine at demo scale)."""
    return run_eval(_BATCH)
