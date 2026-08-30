from __future__ import annotations

from recoveryloop.schema import Diagnosis, FailureCode, FailureEvent

_ROOT_CAUSE_AND_RETRYABLE: dict[FailureCode, tuple[str, bool]] = {
    FailureCode.insufficient_funds: ("insufficient funds at time of charge", True),
    FailureCode.expired_card: ("card expired before charge was attempted", False),
    FailureCode.bank_timeout: ("bank/network timeout during authorization", True),
    FailureCode.limit_drop: ("issuer-side transaction limit drop", True),
    FailureCode.gateway_error: ("payment gateway error", True),
    FailureCode.unknown: ("failure code not recognized by the diagnosis engine", False),
}

_NOTES: dict[FailureCode, str] = {
    FailureCode.insufficient_funds: (
        "Funds were short at charge time; they may become available later."
    ),
    FailureCode.expired_card: (
        "Card expiry is a customer-side issue; won't resolve on its own retry."
    ),
    FailureCode.bank_timeout: (
        "Bank/network timed out during authorization; likely transient."
    ),
    FailureCode.limit_drop: (
        "Issuer-side limit dropped the transaction; usually temporary."
    ),
    FailureCode.gateway_error: (
        "Payment gateway error; may recover on a later attempt."
    ),
    FailureCode.unknown: (
        "Failure code is unrecognized; not safe to retry on an unknown cause."
    ),
}


def diagnose(event: FailureEvent) -> Diagnosis:
    root_cause, is_retryable = _ROOT_CAUSE_AND_RETRYABLE[event.failure_code]
    note = _NOTES[event.failure_code]

    if event.amount == 0:
        is_retryable = False
        root_cause += "; zero-amount, nothing to recover"
        note += " Amount is zero; nothing to recover."

    if event.retry_count >= 10:
        is_retryable = False
        root_cause += f"; retry budget exhausted (retry_count={event.retry_count})"
        note += f" Retry budget exhausted ({event.retry_count} attempts); marking unretryable."

    return Diagnosis(
        case_id=event.case_id,
        root_cause=root_cause,
        is_retryable=is_retryable,
        confidence_note=note,
    )
