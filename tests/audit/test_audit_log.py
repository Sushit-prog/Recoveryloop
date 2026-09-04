from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from recoveryloop.audit.audit_log import AuditLog
from recoveryloop.schema import (
    ActionType,
    AuditRecord,
    CandidateAction,
    Channel,
    Diagnosis,
    FailureCode,
    FailureEvent,
    GateDecision,
    NotificationResult,
)


def _mk_record(
    case_id: str = "T-001",
    amount: str = "100.00",
    retry_count: int = 0,
    executed: bool = False,
) -> AuditRecord:
    event = FailureEvent(
        case_id=case_id,
        merchant_id="mer_test",
        amount=Decimal(amount),
        currency="INR",
        failure_code=FailureCode.bank_timeout,
        timestamp=datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC),
        retry_count=retry_count,
    )
    diagnosis = Diagnosis(
        case_id=case_id,
        root_cause="bank/network timeout during authorization",
        is_retryable=True,
        confidence_note="base mapping",
    )
    candidate = CandidateAction(
        action_type=ActionType.retry_now,
        reasoning="test reasoning",
        score=0.9,
    )
    gate = GateDecision(
        case_id=case_id,
        chosen_action=candidate,
        authorized=True,
    )
    nr: NotificationResult | None = None
    if executed:
        nr = NotificationResult(
            channel=Channel.email,
            delivered=True,
            provider_message_id="notif_test",
        )
    return AuditRecord(
        event=event,
        case_id=case_id,
        diagnosis=diagnosis,
        candidates=[candidate],
        gate_decision=gate,
        executed=executed,
        execution_result="payment link created" if executed else None,
        notification_result=nr,
        timestamp=datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC),
    )


def test_write_then_readback_exact_match() -> None:
    log = AuditLog(db_path=":memory:")
    record = _mk_record()
    rid = log.write(record)

    readback = log.get_by_id(rid)
    assert readback is not None
    assert readback.model_dump() == record.model_dump()


def test_no_mutation_api() -> None:
    log = AuditLog(db_path=":memory:")
    public = [m for m in dir(log) if not m.startswith("_")]
    assert "update" not in public
    assert "delete" not in public
    assert "upsert" not in public
    assert "remove" not in public


def test_get_all_returns_everything() -> None:
    log = AuditLog(db_path=":memory:")
    r1 = _mk_record(case_id="T-001")
    r2 = _mk_record(case_id="T-002")
    r3 = _mk_record(case_id="T-003")
    log.write(r1)
    log.write(r2)
    log.write(r3)

    all_records = log.get_all()
    assert len(all_records) == 3
    assert [r.case_id for r in all_records] == ["T-001", "T-002", "T-003"]


def test_get_by_date_range() -> None:
    log = AuditLog(db_path=":memory:")

    early = _mk_record(case_id="T-001")
    early.event = early.event.model_copy(
        update={"timestamp": datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)}
    )
    early.timestamp = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
    log.write(early)

    mid = _mk_record(case_id="T-002")
    mid.event = mid.event.model_copy(
        update={"timestamp": datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)}
    )
    mid.timestamp = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    log.write(mid)

    late = _mk_record(case_id="T-003")
    late.event = late.event.model_copy(
        update={"timestamp": datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)}
    )
    late.timestamp = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    log.write(late)

    results = log.get_by_date_range(
        datetime(2026, 8, 10, tzinfo=UTC),
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert len(results) == 1
    assert results[0].case_id == "T-002"


def test_empty_db_returns_none() -> None:
    log = AuditLog(db_path=":memory:")
    assert log.get_by_id("nonexistent") is None


def test_write_stores_event() -> None:
    log = AuditLog(db_path=":memory:")
    record = _mk_record(case_id="T-042", amount="999.50")
    rid = log.write(record)

    readback = log.get_by_id(rid)
    assert readback is not None
    assert readback.event.case_id == "T-042"
    assert readback.event.amount == Decimal("999.50")
    assert readback.event.failure_code == FailureCode.bank_timeout
