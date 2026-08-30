from __future__ import annotations

from recoveryloop.schema import (
    ActionType,
    CandidateAction,
    Diagnosis,
    FailureEvent,
    GateDecision,
)

_RETRY_ACTIONS = (ActionType.retry_now, ActionType.retry_later)
_CONTACT_ACTIONS = (ActionType.retry_now, ActionType.escalate)


def authorize(
    event: FailureEvent,
    diagnosis: Diagnosis,
    candidate: CandidateAction,
) -> GateDecision:
    """Authorization verdict for ONE candidate action.

    Pure and deterministic. ``diagnosis`` is intentionally not consulted:
    the gate re-derives every safety-relevant fact from the raw event and
    never takes the DecisionEngine's word (or its reasoning string) for a
    gating fact. Typically called with the engine's top candidate, but the
    gate never assumes that and only ever evaluates the single candidate it
    is given.
    """
    action = candidate.action_type
    today = event.timestamp.date()

    def _allow() -> GateDecision:
        return GateDecision(
            case_id=event.case_id,
            chosen_action=candidate,
            authorized=True,
        )

    def _deny(rule: str, reason: str) -> GateDecision:
        return GateDecision(
            case_id=event.case_id,
            chosen_action=candidate,
            authorized=False,
            policy_rule_triggered=rule,
            denial_reason=reason,
        )

    if action == ActionType.no_action:
        # Rule 1 — doing nothing never needs gating.
        return _allow()

    if action in _RETRY_ACTIONS and event.retry_count >= 10:
        # Rule 2 — hard cap on runaway retries, independent of engine scoring.
        return _deny(
            "retry_budget_exceeded",
            f"retry budget exceeded: retry_count={event.retry_count} >= 10",
        )

    if (
        action in (ActionType.retry_now, ActionType.retry_later, ActionType.escalate)
        and event.has_active_ptp
        and event.ptp_date is not None
        and event.ptp_date >= today
    ):
        # Rule 3 — a valid promise-to-pay blocks customer-facing/retry actions.
        return _deny(
            "active_valid_ptp_blocks_contact",
            f"valid promise-to-pay in effect on {event.ptp_date.isoformat()}; "
            "customer-facing or retry action blocked",
        )

    if action in _CONTACT_ACTIONS and (
        event.timestamp.hour >= 22 or event.timestamp.hour < 7
    ):
        # Rule 4 — do not contact customers during quiet hours (22:00-07:00 UTC).
        return _deny(
            "quiet_hours",
            f"quiet hours: event hour {event.timestamp.hour} UTC is within 22:00-07:00",
        )

    if action in _RETRY_ACTIONS and event.amount == 0:
        # Rule 5 — nothing to recover on a zero-amount case.
        return _deny(
            "zero_amount_no_recovery_action",
            f"zero-amount case: amount={event.amount}; nothing to recover",
        )

    return _allow()
