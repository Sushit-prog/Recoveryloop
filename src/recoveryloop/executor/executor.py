from __future__ import annotations

from decimal import Decimal
from typing import Optional

from recoveryloop.executor.protocols import (
    CustomerContact,
    ExecutionNotAuthorized,
    ExecutionResult,
    Notifier,
    PaymentLinkOutcome,
    RazorpayClient,
)
from recoveryloop.schema import (
    ActionType,
    Diagnosis,
    FailureEvent,
    GateDecision,
    NotificationResult,
)


def execute(
    event: FailureEvent,
    diagnosis: Diagnosis,
    gate_decision: GateDecision,
    client: RazorpayClient,
    notifier: Notifier,
    customer: Optional[CustomerContact] = None,
) -> ExecutionResult:
    """Dispatch a single recovery action.

    The orchestrator MUST verify ``gate_decision.authorized`` before
    calling this function.  If it doesn't, ``ExecutionNotAuthorized`` is
    raised immediately — no transport calls are made.
    """
    if not gate_decision.authorized:
        raise ExecutionNotAuthorized(
            f"gate denied action for {event.case_id}: {gate_decision.denial_reason}"
        )

    action = gate_decision.chosen_action.action_type

    if action == ActionType.retry_now or action == ActionType.retry_later:
        return _create_link(event, client, notifier, customer)

    if action == ActionType.escalate:
        return ExecutionResult(
            executed=False,
            action_type=ActionType.escalate,
            detail="escalation recorded; no payment link created",
        )

    if action == ActionType.no_action:
        return ExecutionResult(
            executed=False,
            action_type=ActionType.no_action,
            detail="no action taken",
        )

    raise ValueError(f"unhandled action type: {action}")


def _create_link(
    event: FailureEvent,
    client: RazorpayClient,
    notifier: Notifier,
    customer: Optional[CustomerContact],
) -> ExecutionResult:
    outcome: PaymentLinkOutcome = client.create_payment_link(
        amount=event.amount,
        currency=event.currency,
        reference_id=event.case_id,
        notify=customer,
    )

    if outcome.status == "created":
        nr: Optional[NotificationResult] = notifier.record(
            case_id=event.case_id,
            channel="email",
            outcome=outcome,
            contact=customer,
        )
        return ExecutionResult(
            executed=True,
            action_type=ActionType.retry_now,
            detail="payment link created and notification dispatched",
            link_id=outcome.link_id,
            link_url=outcome.short_url,
            notification_result=nr,
        )

    return ExecutionResult(
        executed=False,
        action_type=ActionType.retry_now,
        detail=f"payment link creation failed: {outcome.error}",
    )
