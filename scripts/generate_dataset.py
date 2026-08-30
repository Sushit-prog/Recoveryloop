#!/usr/bin/env python3
"""M1: deterministic synthetic dataset generator.

Writes exactly 60 (FailureEvent, GroundTruthLabel) cases to
data/synthetic_batch.json. Byte-identical on every run: Random(SEED) and a
fixed REFERENCE_TIME anchor. Ground-truth labels encode what a genuinely
correct recovery decision would be, so the M7 eval harness can score the
Diagnoser / DecisionEngine / Gate against them.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recoveryloop.schema import ActionType, FailureCode, FailureEvent, GroundTruthLabel

SEED = 42
N_CASES = 60
OUT = ROOT / "data" / "synthetic_batch.json"

REFERENCE_TIME = datetime(2026, 8, 30, 21, 0, 0, tzinfo=UTC)
REFERENCE_DATE = REFERENCE_TIME.date()

MERCHANTS = ("mer_alpha", "mer_beta", "mer_gamma", "mer_delta", "mer_epsilon")

INR_AMOUNTS = (
    Decimal("499.00"),
    Decimal("1099.50"),
    Decimal("1499.00"),
    Decimal("2500.75"),
    Decimal("7999.00"),
    Decimal("12999.50"),
    Decimal("25000.50"),
    Decimal("84500.00"),
    Decimal("199000.00"),
    Decimal("385000.50"),
    Decimal("1249999.00"),
)
USD_AMOUNTS = (
    Decimal("49.99"),
    Decimal("89.00"),
    Decimal("250.50"),
    Decimal("1499.99"),
)

NORMAL_BUCKETS = {
    # code -> (zero_retry, low_retry, exhausted)
    FailureCode.insufficient_funds: (4, 3, 3),
    FailureCode.expired_card: (2, 4, 5),
    FailureCode.bank_timeout: (2, 4, 3),
    FailureCode.limit_drop: (2, 2, 2),
    FailureCode.gateway_error: (1, 5, 0),
    FailureCode.unknown: (1, 3, 2),
}

ROOT_CAUSES = {
    FailureCode.insufficient_funds: "customer lacks funds for the payment",
    FailureCode.expired_card: "card expired before charge was attempted",
    FailureCode.bank_timeout: "issuing bank did not respond within the window",
    FailureCode.limit_drop: "transaction exceeds the current card limit",
    FailureCode.gateway_error: "payment gateway raised a processing error",
    FailureCode.unknown: "failure code could not be classified",
}

IS_RETRYABLE = {
    FailureCode.insufficient_funds: True,
    FailureCode.expired_card: False,
    FailureCode.bank_timeout: True,
    FailureCode.limit_drop: True,
    FailureCode.gateway_error: True,
    FailureCode.unknown: False,
}

STALE_PTP_REASON = "stale promise-to-pay date should not block recovery"

SPECIALS = {
    7: "amount_zero",
    19: "retry_absurd",
    31: "unknown_high_retry",
    43: "ptp_null",
    48: ("stale_ptp", FailureCode.insufficient_funds),
    50: ("stale_ptp", FailureCode.bank_timeout),
    56: ("stale_ptp", FailureCode.limit_drop),
}
PTP_VALID = {
    3: FailureCode.insufficient_funds,
    12: FailureCode.expired_card,
    22: FailureCode.bank_timeout,
    33: FailureCode.limit_drop,
    44: FailureCode.gateway_error,
}
USD_CASE_INDICES = (2, 9, 16, 24, 31, 40, 49, 58)
RECENT_CASE_COUNT = 10


def expected_action(code: FailureCode, retry_count: int, valid_ptp: bool) -> ActionType:
    if valid_ptp:
        return ActionType.no_action
    if retry_count >= 3:
        if code in (FailureCode.gateway_error, FailureCode.unknown):
            return ActionType.no_action
        return ActionType.escalate
    if code == FailureCode.unknown:
        return ActionType.no_action
    if code == FailureCode.expired_card:
        return ActionType.escalate
    if code == FailureCode.insufficient_funds:
        return ActionType.retry_now
    return ActionType.retry_later


def _jsonable(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def build_dataset(rng: random.Random) -> list[dict]:
    codes: list[FailureCode] = []
    retries: list[int] = []
    for code, (zero, low, ex) in NORMAL_BUCKETS.items():
        codes += [code] * (zero + low + ex)
        retries += [0] * zero
        retries += [rng.randint(1, 2) for _ in range(low)]
        retries += [rng.randint(3, 5) for _ in range(ex)]
    rng.shuffle(codes)
    rng.shuffle(retries)
    normal_iter = iter(zip(codes, retries))

    recent_indices = set(rng.sample(range(N_CASES), RECENT_CASE_COUNT))
    records: list[dict] = []

    for i in range(N_CASES):
        merchant_id: str = rng.choice(MERCHANTS)
        currency: str = "USD" if i in USD_CASE_INDICES else "INR"
        if i in recent_indices:
            timestamp: datetime = REFERENCE_TIME - timedelta(
                minutes=rng.randint(0, 170), seconds=rng.randint(0, 59)
            )
        else:
            timestamp = REFERENCE_TIME - timedelta(
                days=rng.randint(2, 30),
                hours=rng.randint(0, 23),
                minutes=rng.randint(0, 59),
            )
            if (REFERENCE_TIME - timestamp).total_seconds() < 3 * 3600:
                timestamp -= timedelta(hours=4)

        amount = rng.choice(USD_AMOUNTS if currency == "USD" else INR_AMOUNTS)
        code: FailureCode
        retry_count: int
        has_active_ptp: bool
        ptp_date: date | None = None
        valid_ptp: bool = False
        action_override: ActionType | None = None
        root_cause_override: str | None = None
        retryable_override: bool | None = None
        adversarial_reason: str | None = None

        if i in SPECIALS:
            spec = SPECIALS[i]
            if spec == "amount_zero":
                code = FailureCode.insufficient_funds
                retry_count = 0
                has_active_ptp = False
                amount = Decimal("0.00")
                currency = "INR"
                adversarial_reason = "zero-amount case is not recoverable"
                action_override = ActionType.no_action
                root_cause_override = "zero-amount transaction; nothing to recover"
                retryable_override = False
            elif spec == "retry_absurd":
                code = FailureCode.bank_timeout
                retry_count = 55
                has_active_ptp = False
                adversarial_reason = (
                    "absurd retry count means the budget is blown; never retry again"
                )
                action_override = ActionType.no_action
            elif spec == "unknown_high_retry":
                code = FailureCode.unknown
                retry_count = 6
                has_active_ptp = False
                adversarial_reason = (
                    "unknown failure code compounded by exhausted retries"
                )
            elif spec == "ptp_null":
                code = FailureCode.insufficient_funds
                retry_count = 0
                has_active_ptp = True
                adversarial_reason = "active PTP without a date is ambiguous; do not assume it blocks recovery"
                action_override = ActionType.retry_later
            else:  # stale_ptp: (kind, code)
                code = spec[1]
                retry_count = 0
                has_active_ptp = True
                ptp_date = REFERENCE_DATE - timedelta(days=rng.randint(1, 10))
                adversarial_reason = STALE_PTP_REASON
        elif i in PTP_VALID:
            code = PTP_VALID[i]
            retry_count = rng.randint(0, 5)
            has_active_ptp = True
            valid_ptp = True
            ptp_date = REFERENCE_DATE + timedelta(days=rng.randint(1, 7))
        else:
            code, retry_count = next(normal_iter)
            has_active_ptp = False

        if action_override is not None:
            action = action_override
        elif valid_ptp:
            action = ActionType.no_action
        else:
            action = expected_action(code, retry_count, False)

        is_retryable = (
            IS_RETRYABLE[code] if retryable_override is None else retryable_override
        )
        root_cause = (
            ROOT_CAUSES[code] if root_cause_override is None else root_cause_override
        )

        case_id = f"C-{i + 1:03d}"
        event = FailureEvent(
            case_id=case_id,
            merchant_id=merchant_id,
            amount=amount,
            currency=currency,
            failure_code=code,
            timestamp=timestamp,
            retry_count=retry_count,
            has_active_ptp=has_active_ptp,
            ptp_date=ptp_date,
        )
        label = GroundTruthLabel(
            case_id=case_id,
            expected_root_cause=root_cause,
            expected_is_retryable=is_retryable,
            expected_action=action,
            expected_authorized=action != ActionType.no_action,
            is_adversarial=i in SPECIALS,
            adversarial_reason=adversarial_reason,
        )
        records.append({"event": event, "ground_truth": label})

    labels = [r["ground_truth"] for r in records]
    assert len(records) == N_CASES
    assert {r["event"].failure_code for r in records} == set(FailureCode)
    assert sum(l.is_adversarial for l in labels) >= 6
    for r in records:
        e, l = r["event"], r["ground_truth"]
        if e.retry_count >= 3:
            assert l.expected_action in (ActionType.no_action, ActionType.escalate), (
                e.case_id
            )
    non_ptp_no_action = sum(
        1
        for r in records
        if r["ground_truth"].expected_action == ActionType.no_action
        and not (
            r["event"].has_active_ptp
            and r["event"].ptp_date
            and r["event"].ptp_date > REFERENCE_DATE
        )
    )
    assert non_ptp_no_action >= 8
    return records


def main() -> None:
    rng = random.Random(SEED)
    records = build_dataset(rng)
    payload = [
        {
            "event": _jsonable(r["event"].model_dump(mode="python")),
            "ground_truth": _jsonable(r["ground_truth"].model_dump(mode="python")),
        }
        for r in records
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    codes = Counter(r["event"].failure_code.value for r in records)
    adv = sum(r["ground_truth"].is_adversarial for r in records)
    no_action = sum(
        r["ground_truth"].expected_action == ActionType.no_action for r in records
    )
    actions = Counter(r["ground_truth"].expected_action.value for r in records)
    print(f"generated {len(records)} cases -> {OUT}")
    print(f"failure_code breakdown: {dict(codes)}")
    print(f"adversarial: {adv}")
    print(f"expected no_action: {no_action}")
    print(f"expected_action breakdown: {dict(actions)}")


if __name__ == "__main__":
    main()
