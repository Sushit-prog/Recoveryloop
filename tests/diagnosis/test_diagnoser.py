import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from recoveryloop.diagnosis.diagnoser import diagnose
from recoveryloop.schema import Diagnosis, FailureCode, FailureEvent

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic_batch.json"

EXPECTED = {
    FailureCode.insufficient_funds: ("insufficient funds at time of charge", True),
    FailureCode.expired_card: ("card expired before charge was attempted", False),
    FailureCode.bank_timeout: ("bank/network timeout during authorization", True),
    FailureCode.limit_drop: ("issuer-side transaction limit drop", True),
    FailureCode.gateway_error: ("payment gateway error", True),
    FailureCode.unknown: ("failure code not recognized by the diagnosis engine", False),
}


def _mk_event(
    failure_code: FailureCode,
    amount: str = "100.00",
    retry_count: int = 0,
    **kwargs: object,
) -> FailureEvent:
    return FailureEvent(
        case_id="T-001",
        merchant_id="mer_test",
        amount=Decimal(amount),
        currency="INR",
        failure_code=failure_code,
        timestamp=datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC),
        retry_count=retry_count,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("code", list(FailureCode))
def test_failure_code_maps_to_expected_base(code: FailureCode) -> None:
    expected_cause, expected_retryable = EXPECTED[code]
    diag = diagnose(_mk_event(code))
    assert diag.case_id == "T-001"
    assert expected_cause in diag.root_cause
    assert diag.is_retryable is expected_retryable
    assert isinstance(diag.confidence_note, str) and diag.confidence_note.strip()


@pytest.mark.parametrize(
    "code",
    [FailureCode.insufficient_funds, FailureCode.gateway_error],
)
def test_zero_amount_override_fires_for_retryable_codes(code: FailureCode) -> None:
    diag = diagnose(_mk_event(code, amount="0.00"))
    assert diag.is_retryable is False
    assert "; zero-amount, nothing to recover" in diag.root_cause


def test_zero_amount_override_on_non_retryable_code_stays_false() -> None:
    diag = diagnose(_mk_event(FailureCode.expired_card, amount="0.00"))
    assert diag.is_retryable is False
    assert "; zero-amount, nothing to recover" in diag.root_cause
    assert "card expired before charge was attempted" in diag.root_cause


@pytest.mark.parametrize(
    "code",
    [FailureCode.bank_timeout, FailureCode.limit_drop],
)
def test_retry_budget_override_fires_at_threshold(code: FailureCode) -> None:
    diag = diagnose(_mk_event(code, retry_count=10))
    assert diag.is_retryable is False
    assert "; retry budget exhausted (retry_count=10)" in diag.root_cause


def test_retry_budget_override_does_not_fire_below_threshold() -> None:
    diag = diagnose(_mk_event(FailureCode.bank_timeout, retry_count=9))
    assert diag.is_retryable is True
    assert "retry budget" not in diag.root_cause


def test_overrides_stack() -> None:
    diag = diagnose(_mk_event(FailureCode.bank_timeout, amount="0.00", retry_count=10))
    assert diag.is_retryable is False
    assert diag.root_cause == (
        "bank/network timeout during authorization"
        "; zero-amount, nothing to recover"
        "; retry budget exhausted (retry_count=10)"
    )


def test_diagnose_never_raises_for_any_failure_code() -> None:
    for code in FailureCode:
        diag = diagnose(_mk_event(code))
        assert isinstance(diag, Diagnosis)
        assert diag.is_retryable in (True, False)
        assert diag.root_cause


def test_dataset_agreement_reports_visibility_not_equality() -> None:
    with open(DATA, encoding="utf-8") as fh:
        records = json.load(fh)

    rows = []
    matches = 0
    total = 0
    for record in records:
        event = FailureEvent(**record["event"])
        diag = diagnose(event)
        assert isinstance(diag, Diagnosis)
        assert diag.case_id == event.case_id
        expected = bool(record["ground_truth"]["expected_is_retryable"])
        match = diag.is_retryable is expected
        matches += match
        total += 1
        rows.append(
            f"{event.case_id:<8}{event.failure_code.value:<18}"
            f"{event.retry_count:<6}{str(diag.is_retryable):<6}{str(expected):<6}{'OK' if match else 'DIFF'}"
        )

    print("\n== Diagnoser agreement vs M1 ground truth (is_retryable) ==")
    print(f"{'case_id':<8}{'failure_code':<18}{'retry':<6}{'diag':<6}{'gt':<6}result")
    for row in rows:
        print(row)
    print(f"agreement: {matches}/{total}")
    assert total == 60
