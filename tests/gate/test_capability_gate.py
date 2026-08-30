import json
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from recoveryloop.decision.decision_engine import decide
from recoveryloop.diagnosis.diagnoser import diagnose
from recoveryloop.gate.capability_gate import authorize
from recoveryloop.schema import (
    ActionType,
    CandidateAction,
    Diagnosis,
    FailureCode,
    FailureEvent,
    GateDecision,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic_batch.json"


def _mk_event(
    failure_code: FailureCode = FailureCode.bank_timeout,
    *,
    amount: str = "100.00",
    retry_count: int = 0,
    has_active_ptp: bool = False,
    ptp_date: date | None = None,
    timestamp: datetime = datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC),
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


def _mk_diag(is_retryable: bool = True) -> Diagnosis:
    return Diagnosis(
        case_id="T-001",
        root_cause="test cause",
        is_retryable=is_retryable,
        confidence_note="test note",
    )


def _mk_action(action: ActionType, score: float = 0.5) -> CandidateAction:
    return CandidateAction(action_type=action, reasoning="test reasoning", score=score)


def _assert_denied(decision: GateDecision, rule: str, fact: str) -> None:
    assert decision.authorized is False
    assert decision.policy_rule_triggered == rule
    assert decision.denial_reason is not None
    assert fact in decision.denial_reason


def test_rule_2_retry_budget_exceeded() -> None:
    event = _mk_event(retry_count=12)
    decision = authorize(event, _mk_diag(), _mk_action(ActionType.retry_now))
    _assert_denied(decision, "retry_budget_exceeded", "12")


def test_rule_2_applies_to_retry_later_too() -> None:
    event = _mk_event(retry_count=10)
    decision = authorize(event, _mk_diag(), _mk_action(ActionType.retry_later))
    _assert_denied(decision, "retry_budget_exceeded", "10")


def test_rule_3_valid_ptp_blocks_contact() -> None:
    event = _mk_event(
        has_active_ptp=True,
        ptp_date=date(2026, 8, 31),
        timestamp=datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC),
    )
    decision = authorize(event, _mk_diag(), _mk_action(ActionType.escalate))
    _assert_denied(decision, "active_valid_ptp_blocks_contact", "2026-08-31")


def test_rule_3_blocks_retry_actions() -> None:
    for action in (ActionType.retry_now, ActionType.retry_later):
        event = _mk_event(
            has_active_ptp=True,
            ptp_date=date(2026, 8, 31),
            timestamp=datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC),
        )
        decision = authorize(event, _mk_diag(), _mk_action(action))
        assert decision.authorized is False
        assert decision.policy_rule_triggered == "active_valid_ptp_blocks_contact"


def test_rule_4_quiet_hours_blocks_retry_now() -> None:
    event = _mk_event(timestamp=datetime(2026, 8, 30, 23, 30, tzinfo=UTC))
    decision = authorize(event, _mk_diag(), _mk_action(ActionType.retry_now))
    _assert_denied(decision, "quiet_hours", "23")


def test_rule_4_quiet_hours_blocks_escalate() -> None:
    event = _mk_event(timestamp=datetime(2026, 8, 30, 2, 30, tzinfo=UTC))
    decision = authorize(event, _mk_diag(), _mk_action(ActionType.escalate))
    _assert_denied(decision, "quiet_hours", "2")


def test_rule_4_quiet_hours_boundary_22_denied_7_allowed() -> None:
    denied = authorize(
        _mk_event(timestamp=datetime(2026, 8, 30, 22, 0, tzinfo=UTC)),
        _mk_diag(),
        _mk_action(ActionType.retry_now),
    )
    _assert_denied(denied, "quiet_hours", "22")

    allowed = authorize(
        _mk_event(timestamp=datetime(2026, 8, 30, 7, 0, tzinfo=UTC)),
        _mk_diag(),
        _mk_action(ActionType.retry_now),
    )
    assert allowed.authorized is True
    assert allowed.policy_rule_triggered is None


def test_rule_5_zero_amount_denies_retry() -> None:
    for action in (ActionType.retry_now, ActionType.retry_later):
        event = _mk_event(amount="0.00")
        decision = authorize(event, _mk_diag(), _mk_action(action))
        _assert_denied(decision, "zero_amount_no_recovery_action", "0.00")


@pytest.mark.parametrize(
    "event_kwargs",
    [
        {"retry_count": 55},
        {"amount": "0.00"},
        {"has_active_ptp": True, "ptp_date": date(2026, 8, 31)},
        {"timestamp": datetime(2026, 8, 30, 23, 30, tzinfo=UTC)},
    ],
)
def test_no_action_is_always_authorized(event_kwargs: dict) -> None:
    event = _mk_event(**event_kwargs)
    decision = authorize(event, _mk_diag(), _mk_action(ActionType.no_action))
    assert decision.authorized is True
    assert decision.policy_rule_triggered is None
    assert decision.denial_reason is None


def test_rule_priority_budget_wins_over_valid_ptp() -> None:
    event = _mk_event(
        retry_count=12,
        has_active_ptp=True,
        ptp_date=date(2026, 8, 31),
        timestamp=datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC),
    )
    decision = authorize(event, _mk_diag(), _mk_action(ActionType.retry_now))
    _assert_denied(decision, "retry_budget_exceeded", "12")


def test_clean_conditions_allow_all_action_types() -> None:
    retry_now = authorize(
        _mk_event(retry_count=0, timestamp=datetime(2026, 8, 30, 14, 0, tzinfo=UTC)),
        _mk_diag(),
        _mk_action(ActionType.retry_now),
    )
    assert retry_now.authorized is True

    retry_later_at_quiet_hour = authorize(
        _mk_event(retry_count=5, timestamp=datetime(2026, 8, 30, 23, 0, tzinfo=UTC)),
        _mk_diag(),
        _mk_action(ActionType.retry_later),
    )
    assert retry_later_at_quiet_hour.authorized is True

    escalate = authorize(
        _mk_event(timestamp=datetime(2026, 8, 30, 14, 0, tzinfo=UTC)),
        _mk_diag(is_retryable=False),
        _mk_action(ActionType.escalate),
    )
    assert escalate.authorized is True

    no_action = authorize(_mk_event(), _mk_diag(), _mk_action(ActionType.no_action))
    assert no_action.authorized is True


def test_end_to_end_gate_visibility_across_dataset() -> None:
    with open(DATA, encoding="utf-8") as fh:
        records = json.load(fh)

    rows: list[str] = []
    total_authorized = 0
    total_denied = 0
    denials_by_rule: Counter[str] = Counter()
    total = 0
    for record in records:
        event = FailureEvent(**record["event"])
        diag = diagnose(event)
        top = decide(event, diag)[0]
        decision = authorize(event, diag, top)
        total += 1
        if decision.authorized:
            total_authorized += 1
            rule_display = "-"
        else:
            total_denied += 1
            assert decision.policy_rule_triggered is not None
            denials_by_rule[decision.policy_rule_triggered] += 1
            rule_display = decision.policy_rule_triggered
        rows.append(
            f"{event.case_id:<8}{top.action_type.value:<13}{str(decision.authorized):<7}{rule_display}"
        )

    print("\n== CapabilityGate end-to-end (diagnose -> decide -> authorize) ==")
    print(f"{'case_id':<8}{'top_action':<13}{'auth':<7}policy_rule_triggered")
    for row in rows:
        print(row)
    print(f"total authorized: {total_authorized}/{total}")
    print(f"total denied: {total_denied}/{total}")
    if denials_by_rule:
        print("denied by rule:")
        for rule, count in sorted(denials_by_rule.items()):
            print(f"  {rule}: {count}")
    assert total == 60
