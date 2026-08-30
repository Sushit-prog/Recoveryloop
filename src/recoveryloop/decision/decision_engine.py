from __future__ import annotations

from recoveryloop.schema import ActionType, CandidateAction, Diagnosis, FailureEvent

RuleCandidates = list[tuple[ActionType, float, str]]


def decide(event: FailureEvent, diagnosis: Diagnosis) -> list[CandidateAction]:
    """Rank candidate recovery actions for one case.

    Pure and deterministic: "today" is derived from ``event.timestamp``,
    never from ``datetime.now()``, so the function is stable over
    historical/fixed dataset timestamps. Output is always sorted highest
    score first and always contains at least one candidate (``no_action``
    is the universal fallback).
    """
    today = event.timestamp.date()

    if event.retry_count >= 10:
        # Rule 1 — budget exhaustion (checked first; a policy call, not a
        # cause judgment).
        candidates = [
            (
                ActionType.escalate,
                0.8,
                f"retry budget exhausted at {event.retry_count} attempts; cause may "
                "still be retryable but requires human review, not further automation",
            ),
            (
                ActionType.no_action,
                0.3,
                f"no further automated retries after {event.retry_count} prior attempts",
            ),
        ]
    elif event.amount == 0:
        # Rule 2 — zero amount: nothing to recover.
        candidates = [
            (
                ActionType.no_action,
                0.95,
                f"zero-amount charge (amount={event.amount}); nothing to recover",
            ),
            (
                ActionType.escalate,
                0.05,
                f"zero-amount charge (amount={event.amount}) leaves only a manual review option",
            ),
        ]
    elif (
        event.has_active_ptp and event.ptp_date is not None and event.ptp_date >= today
    ):
        # Rule 3 — valid promise-to-pay: do not re-contact a converting customer.
        candidates = [
            (
                ActionType.no_action,
                0.85,
                f"promise-to-pay is active and valid on {event.ptp_date.isoformat()}; "
                "do not re-contact a customer who already committed",
            ),
            (
                ActionType.retry_later,
                0.4,
                f"promise-to-pay may lapse after {event.ptp_date.isoformat()}; "
                "schedule a fallback retry once the date passes",
            ),
        ]
    elif event.has_active_ptp and event.ptp_date is not None and event.ptp_date < today:
        # Rule 4 — stale promise-to-pay: it has lapsed; fall through to
        # rules 5/6 as if unblocked, but tag the winning candidate.
        stale_note = (
            f"prior promise-to-pay lapsed on {event.ptp_date.isoformat()}; "
            "proceeding with recovery"
        )
        candidates = _unblocked_candidates(event, diagnosis)
        if candidates:
            action, score, reasoning = candidates[0]
            candidates[0] = (action, score, f"{stale_note}. {reasoning}")
    else:
        candidates = _unblocked_candidates(event, diagnosis)

    result = [
        CandidateAction(action_type=a, reasoning=r, score=s) for a, s, r in candidates
    ]
    return sorted(result, key=lambda c: c.score, reverse=True)


def _unblocked_candidates(event: FailureEvent, diagnosis: Diagnosis) -> RuleCandidates:
    """Rules 5 and 6 for cases with no blocking PTP condition."""
    if not diagnosis.is_retryable:
        # Rule 5 — cause itself cannot be fixed by retrying.
        return [
            (
                ActionType.escalate,
                0.7,
                f"{diagnosis.root_cause}; not fixable by an automated retry, "
                "needs a human-issued remedy",
            ),
            (
                ActionType.no_action,
                0.2,
                f"{diagnosis.root_cause}; no automated path is available",
            ),
        ]

    # Rule 6 — retryable with no blocking conditions.
    n = event.retry_count
    if n == 0:
        return [
            (
                ActionType.retry_now,
                0.9,
                f"retryable cause ({diagnosis.root_cause}) with {n} prior attempts; "
                "nothing has been spent yet, try immediately",
            ),
            (
                ActionType.retry_later,
                0.5,
                f"retryable cause ({diagnosis.root_cause}) with {n} prior attempts; "
                "a spaced-out second attempt also remains plausible",
            ),
        ]
    if n <= 2:
        return [
            (
                ActionType.retry_later,
                0.7,
                f"retryable cause ({diagnosis.root_cause}) with {n} prior attempts; "
                "after one or more failures on a transient condition, a brief backoff "
                "before retrying is more defensible than an immediate re-attempt",
            ),
            (
                ActionType.retry_now,
                0.5,
                f"retryable cause ({diagnosis.root_cause}) with {n} prior attempts; "
                "an immediate attempt remains plausible if the backoff is skipped",
            ),
        ]
    return [
        (
            ActionType.retry_later,
            0.65,
            f"retryable cause ({diagnosis.root_cause}) with {n} prior attempts; "
            "reaching 'not working easily' territory, prefer spacing attempts out",
        ),
        (
            ActionType.escalate,
            0.35,
            f"retryable cause ({diagnosis.root_cause}) with {n} prior attempts; "
            "start surfacing to a human as a backup option",
        ),
    ]
