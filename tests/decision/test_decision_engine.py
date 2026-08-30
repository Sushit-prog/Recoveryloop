import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from recoveryloop.decision.decision_engine import decide
from recoveryloop.diagnosis.diagnoser import diagnose
from recoveryloop.schema import (
    ActionType,
    CandidateAction,
    Diagnosis,
    FailureCode,
    FailureEvent,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic_batch.json"

BASE_TIME = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


def _mk_event(
    failure_code: FailureCode,
    *,
    amount: str = "100.00",
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


def test_rule_1_budget_exhaustion() -> None:
    event = _mk_event(FailureCode.insufficient_funds, retry_count=10)
    diag = _mk_diag("insufficient funds at time of charge", True)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.escalate
    assert result[0].score == pytest.approx(0.8)
    assert "retry budget exhausted at 10 attempts" in result[0].reasoning
    assert result[1].action_type == ActionType.no_action
    assert result[1].score == pytest.approx(0.3)


def test_rule_2_zero_amount() -> None:
    event = _mk_event(FailureCode.gateway_error, amount="0.00")
    diag = _mk_diag("payment gateway error", True)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.no_action
    assert result[0].score == pytest.approx(0.95)
    assert "zero-amount" in result[0].reasoning
    assert "0.00" in result[0].reasoning
    assert result[1].action_type == ActionType.escalate


def test_rule_3_valid_ptp() -> None:
    event = _mk_event(
        FailureCode.insufficient_funds,
        retry_count=1,
        has_active_ptp=True,
        ptp_date=date(2026, 8, 31),
    )
    diag = _mk_diag("insufficient funds at time of charge", True)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.no_action
    assert result[0].score == pytest.approx(0.85)
    assert "2026-08-31" in result[0].reasoning
    assert result[1].action_type == ActionType.retry_later


def test_rule_4_stale_ptp_falls_through_with_note() -> None:
    event = _mk_event(
        FailureCode.bank_timeout,
        retry_count=2,
        has_active_ptp=True,
        ptp_date=date(2026, 8, 20),
        timestamp=datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC),
    )
    diag = _mk_diag("bank/network timeout during authorization", True)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.retry_later
    assert result[0].score == pytest.approx(0.7)
    assert "2026-08-20" in result[0].reasoning
    assert "proceeding with recovery" in result[0].reasoning


def test_rule_5_not_retryable() -> None:
    event = _mk_event(FailureCode.expired_card, retry_count=0)
    diag = _mk_diag("card expired before charge was attempted", False)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.escalate
    assert result[0].score == pytest.approx(0.7)
    assert "card expired before charge was attempted" in result[0].reasoning
    assert result[1].action_type == ActionType.no_action


def test_rule_6_retry_count_zero() -> None:
    event = _mk_event(FailureCode.insufficient_funds, retry_count=0)
    diag = _mk_diag("insufficient funds at time of charge", True)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.retry_now
    assert result[0].score == pytest.approx(0.9)
    assert "0 prior attempts" in result[0].reasoning
    assert result[1].action_type == ActionType.retry_later


def test_rule_6_retry_count_low() -> None:
    event = _mk_event(FailureCode.bank_timeout, retry_count=2)
    diag = _mk_diag("bank/network timeout during authorization", True)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.retry_later
    assert result[0].score == pytest.approx(0.7)
    assert "2 prior attempts" in result[0].reasoning
    assert result[0].reasoning.count("backoff") >= 1
    assert result[1].action_type == ActionType.retry_now
    assert result[1].score == pytest.approx(0.5)


def test_rule_6_retry_count_high() -> None:
    event = _mk_event(FailureCode.limit_drop, retry_count=5)
    diag = _mk_diag("issuer-side transaction limit drop", True)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.retry_later
    assert result[0].score == pytest.approx(0.65)
    assert "5 prior attempts" in result[0].reasoning
    assert result[1].action_type == ActionType.escalate


def test_rule_priority_budget_wins_over_not_retryable() -> None:
    event = _mk_event(FailureCode.expired_card, retry_count=10)
    diag = _mk_diag("card expired before charge was attempted", False)
    result = decide(event, diag)
    assert result[0].action_type == ActionType.escalate
    assert result[0].score == pytest.approx(0.8)
    assert "retry budget exhausted at 10 attempts" in result[0].reasoning
    assert "card expired" not in result[0].reasoning


def test_never_empty_and_sorted() -> None:
    ptp_states: list[dict] = [
        {},
        {"has_active_ptp": True, "ptp_date": date(2026, 8, 31)},
        {"has_active_ptp": True, "ptp_date": date(2026, 8, 20)},
    ]
    for code in FailureCode:
        for retry_count in (0, 1, 2, 3, 9, 10):
            diag = _mk_diag(f"cause for {code.value}", retry_count < 10)
            for amount in ("0.00", "100.00"):
                for ptp in ptp_states:
                    event = _mk_event(
                        code, amount=amount, retry_count=retry_count, **ptp
                    )
                    result = decide(event, diag)
                    assert len(result) >= 1
                    scores = [c.score for c in result]
                    assert scores == sorted(scores, reverse=True)
                    assert all(isinstance(c, CandidateAction) for c in result)


def test_dataset_agreement_reports_visibility_not_equality() -> None:
    with open(DATA, encoding="utf-8") as fh:
        records = json.load(fh)

    rows = []
    matches = 0
    total = 0
    for record in records:
        event = FailureEvent(**record["event"])
        diag = diagnose(event)
        result = decide(event, diag)
        assert result, event.case_id
        top = result[0].action_type.value
        expected = record["ground_truth"]["expected_action"]
        match = top == expected
        matches += match
        total += 1
        rows.append(
            f"{event.case_id:<8}{event.failure_code.value:<18}{event.retry_count:<6}"
            f"{top:<13}{expected:<13}{'OK' if match else 'DIFF'}"
        )

    print("\n== DecisionEngine top-candidate agreement vs M1 ground truth ==")
    print(
        f"{'case_id':<8}{'failure_code':<18}{'retry':<6}{'top_action':<13}{'expected':<13}result"
    )
    for row in rows:
        print(row)
    print(f"agreement: {matches}/{total}")
    assert total == 60
