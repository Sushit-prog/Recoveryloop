from __future__ import annotations

from datetime import UTC, datetime

from recoveryloop.audit.audit_log import AuditLog
from recoveryloop.decision.decision_engine import decide
from recoveryloop.diagnosis.diagnoser import diagnose
from recoveryloop.executor.executor import execute
from recoveryloop.executor.protocols import (
    ExecutionNotAuthorized,
    Notifier,
    RazorpayClient,
)
from recoveryloop.gate.capability_gate import authorize
from recoveryloop.schema import AuditRecord, FailureEvent


def run_pipeline(
    event: FailureEvent,
    client: RazorpayClient,
    notifier: Notifier,
    audit_log: AuditLog,
) -> AuditRecord:
    """Run the full recovery pipeline for a single ``FailureEvent``.

    Chains Diagnoser -> DecisionEngine -> CapabilityGate -> Executor (if
    authorized) -> Notifier -> AuditLog.write().  If the CapabilityGate
    blocks the action, a full ``AuditRecord`` is still produced — this is
    a valid, auditable outcome, not an error.

    If the Executor raises ``ExecutionNotAuthorized`` despite the gate
    check (a structural invariant violation), the exception is caught and
    recorded in the audit trail rather than crashing the pipeline.
    """
    diagnosis = diagnose(event)
    candidates = decide(event, diagnosis)
    gate = authorize(event, diagnosis, candidates[0])

    executed = False
    execution_result: str | None = None
    notification_result = None

    if gate.authorized:
        try:
            exec_result = execute(event, diagnosis, gate, client, notifier)
            executed = exec_result.executed
            execution_result = exec_result.detail
            notification_result = exec_result.notification_result
        except ExecutionNotAuthorized as exc:
            execution_result = f"invariant violation: {exc}"
    else:
        execution_result = f"denied: {gate.denial_reason}"

    record = AuditRecord(
        event=event,
        case_id=event.case_id,
        diagnosis=diagnosis,
        candidates=candidates,
        gate_decision=gate,
        executed=executed,
        execution_result=execution_result,
        notification_result=notification_result,
        timestamp=datetime.now(UTC),
    )

    audit_log.write(record)
    return record
