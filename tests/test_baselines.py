from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

import recoveryloop.pipeline as pipeline
from baselines.policies import (
    THRESHOLD,
    always_retry_policy,
    diagnosis_only_policy,
    threshold_policy,
)
from recoveryloop.audit.audit_log import AuditLog
from recoveryloop.executor.fake_razorpay import FakeNotifier, FakeRazorpayClient
from recoveryloop.gate.capability_gate import authorize
from recoveryloop.pipeline import run_pipeline
from recoveryloop.schema import (
    ActionType,
    CandidateAction,
    Diagnosis,
    FailureCode,
    FailureEvent,
    GateDecision,
)

BASE_TIME = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
QUIET_HOURS_TIME = datetime(2026, 8, 30, 23, 0, 0, tzinfo=UTC)


def _mk_event(
    failure_code: FailureCode,
    *,
    amount: str = "1499.00",
    retry_count: int = 0,
    has_active_ptp: bool = False,
    ptp_date: date | None = None,
    timestamp: datetime = BASE_TIME,
) -> FailureEvent:
    return FailureEvent(
        case_id="T-001",
        merchant_id="mer_test",
        amount=Decimal(amount),
        currency="INR",
        failure_code=failure_code,
        timestamp=timestamp,
        retry_count=retry_count,
        has_active_ptp=has_active_ptp,
        ptp_date=ptp_date,
    )


def _mk_diag(root_cause: str, is_retryable: bool) -> Diagnosis:
    return Diagnosis(
        case_id="T-001",
        root_cause=root_cause,
        is_retryable=is_retryable,
        confidence_note="test note",
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        (FailureCode.insufficient_funds, ActionType.retry_now),
        (FailureCode.bank_timeout, ActionType.retry_now),
    ],
)
def test_always_retry_executes_retryable(
    code: FailureCode, expected: ActionType
) -> None:
    event = _mk_event(code)
    diag = _mk_diag("retryable cause", True)
    result = always_retry_policy(event, diag)
    assert result[0].action_type == expected


@pytest.mark.parametrize("code", [FailureCode.expired_card, FailureCode.unknown])
def test_always_retry_skips_non_retryable(code: FailureCode) -> None:
    event = _mk_event(code)
    diag = _mk_diag("cause cannot be fixed by retry", False)
    result = always_retry_policy(event, diag)
    assert result[0].action_type == ActionType.no_action


@pytest.mark.parametrize(
    "amount,is_retryable,expected",
    [
        ("1499.00", True, ActionType.retry_now),
        (str(THRESHOLD), True, ActionType.retry_now),
        ("25000.50", True, ActionType.no_action),
        ("84500.00", True, ActionType.no_action),
    ],
)
def test_threshold_uses_retryability_and_amount(
    amount: str, is_retryable: bool, expected: ActionType
) -> None:
    event = _mk_event(FailureCode.insufficient_funds, amount=amount)
    diag = _mk_diag("retryable cause", is_retryable)
    result = threshold_policy(event, diag)
    assert result[0].action_type == expected


def test_threshold_ignores_amount_when_not_retryable() -> None:
    event = _mk_event(FailureCode.expired_card, amount="1499.00")
    diag = _mk_diag("card expired", False)
    result = threshold_policy(event, diag)
    assert result[0].action_type == ActionType.no_action


@pytest.mark.parametrize(
    "is_retryable,expected",
    [
        (True, ActionType.retry_now),
        (False, ActionType.no_action),
    ],
)
def test_diagnosis_only_executes_retryability_only(
    is_retryable: bool, expected: ActionType
) -> None:
    event = _mk_event(FailureCode.insufficient_funds)
    diag = _mk_diag("retryable cause", is_retryable)
    result = diagnosis_only_policy(event, diag)
    assert result[0].action_type == expected


def test_policies_satisfy_decide_contract() -> None:
    events = [
        _mk_event(FailureCode.insufficient_funds),
        _mk_event(FailureCode.expired_card),
    ]
    diags = [
        _mk_diag("retryable", True),
        _mk_diag("not retryable", False),
    ]
    for policy in (always_retry_policy, threshold_policy, diagnosis_only_policy):
        for event, diag in zip(events, diags):
            result = policy(event, diag)
            assert isinstance(result, list)
            assert result
            assert all(
                isinstance(c, CandidateAction) and 0.0 <= c.score <= 1.0 for c in result
            )
            assert result[0].reasoning


def _always_authorized(
    event: FailureEvent, diagnosis: Diagnosis, candidate: CandidateAction
) -> GateDecision:
    return GateDecision(
        case_id=event.case_id,
        chosen_action=candidate,
        authorized=True,
    )


def test_diagnosis_only_bypasses_gate_in_pipeline() -> None:
    original_decide = pipeline.decide
    original_authorize = pipeline.authorize
    event = _mk_event(
        FailureCode.bank_timeout, timestamp=QUIET_HOURS_TIME, amount="7999.00"
    )
    diag = _mk_diag("bank timeout", True)
    try:
        pipeline.decide = diagnosis_only_policy
        pipeline.authorize = _always_authorized
        record = run_pipeline(
            event, FakeRazorpayClient(), FakeNotifier(), AuditLog(db_path=":memory:")
        )
    finally:
        pipeline.decide = original_decide
        pipeline.authorize = original_authorize

    assert record.executed is True
    assert record.gate_decision.authorized is True
    assert record.candidates[0].action_type == ActionType.retry_now

    denied_by_real_gate = authorize(event, diag, record.candidates[0])
    assert denied_by_real_gate.authorized is False
    assert denied_by_real_gate.policy_rule_triggered == "quiet_hours"
    assert pipeline.decide is original_decide
    assert pipeline.authorize is original_authorize
