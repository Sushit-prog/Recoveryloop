from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from recoveryloop.audit.audit_log import AuditLog
from recoveryloop.executor.fake_razorpay import FakeNotifier, FakeRazorpayClient
from recoveryloop.executor.protocols import ExecutionNotAuthorized
from recoveryloop.pipeline import run_pipeline
from recoveryloop.schema import (
    AuditRecord,
    FailureCode,
    FailureEvent,
)


def _mk_event(
    *,
    retry_count: int = 0,
    amount: str = "100.00",
    timestamp: datetime = datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC),
) -> FailureEvent:
    return FailureEvent(
        case_id="T-001",
        merchant_id="mer_test",
        amount=Decimal(amount),
        currency="INR",
        failure_code=FailureCode.bank_timeout,
        timestamp=timestamp,
        retry_count=retry_count,
    )


def test_happy_path_full_execution() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    audit = AuditLog()

    event = _mk_event()
    record = run_pipeline(event, client, notifier, audit)

    assert isinstance(record, AuditRecord)
    assert record.executed is True
    assert record.notification_result is not None
    assert record.notification_result.delivered is True
    assert len(client.calls) == 1
    assert len(notifier.calls) == 1

    stored = audit.get_by_id(f"{record.case_id}:{record.timestamp.isoformat()}")
    assert stored is not None
    assert stored.executed is True


def test_gate_blocks_action() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    audit = AuditLog()

    event = _mk_event(
        retry_count=0,
        timestamp=datetime(2026, 8, 30, 23, 0, 0, tzinfo=UTC),
    )
    record = run_pipeline(event, client, notifier, audit)

    assert record.executed is False
    assert record.gate_decision.authorized is False
    assert "quiet_hours" in (record.gate_decision.policy_rule_triggered or "")
    assert client.calls == []
    assert notifier.calls == []


def test_defensive_execution_not_authorized() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    audit = AuditLog()

    event = _mk_event()

    original_execute = __import__(
        "recoveryloop.executor.executor", fromlist=["execute"]
    ).execute

    def _explode(*args, **kwargs):
        raise ExecutionNotAuthorized("invariant test")

    with patch("recoveryloop.pipeline.execute", side_effect=_explode):
        record = run_pipeline(event, client, notifier, audit)

    assert record.executed is False
    assert "invariant violation" in (record.execution_result or "")
    assert client.calls == []
    assert notifier.calls == []


def test_audit_record_covers_all_fields() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    audit = AuditLog()

    event = _mk_event()
    record = run_pipeline(event, client, notifier, audit)

    assert record.event == event
    assert record.case_id == event.case_id
    assert record.diagnosis is not None
    assert record.candidates is not None and len(record.candidates) > 0
    assert record.gate_decision is not None
    assert record.timestamp is not None
    assert record.pipeline_version is not None
