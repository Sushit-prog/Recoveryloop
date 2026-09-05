from __future__ import annotations

from decimal import Decimal

from recoveryloop.schema import ActionType, CandidateAction, Diagnosis, FailureEvent

# Threshold picked from the synthetic dataset's amount distribution
# (scripts/generate_dataset.py). Amounts are drawn from fixed tiers: the
# INR tiers are 499 / 1099.50 / 1499 / 2500.75 / 7999 / 12999.50 /
# 25000.50 / 84500 / 199000 / 385000.50 / 1249999, and the USD tiers are
# 49.99 / 89 / 250.50 / 1499.99. A cutoff of Rs.25,000.00 covers the four
# sub-Rs.2.6K INR tiers plus all USD tiers, i.e. 39 of 60 cases (~65th
# percentile by count); the very next INR tier is Rs.25,000.50 then jumps
# to Rs.84,500, so Rs.25K is the natural break between routine tickets and
# big-ticket charges that a competent engineer's simple rule would send to
# human review rather than auto-retry.
THRESHOLD = Decimal("25000.00")


def always_retry_policy(
    event: FailureEvent, diagnosis: Diagnosis
) -> list[CandidateAction]:
    """Baseline 1: raw retryability, no extra signals.

    Retry *every* case the Diagnoser marks retryable, regardless of
    amount, confidence, PTP context or budget. This is an operator rule
    that fully trusts the diagnosis and nothing else; the CapabilityGate
    (when wired downstream by ``harness_runner``) still applies on top.
    Signatures match ``DecisionEngine.decide`` so the policy is drop-in
    swappable in the eval harness.
    """
    if diagnosis.is_retryable:
        return [
            CandidateAction(
                action_type=ActionType.retry_now,
                score=1.0,
                reasoning="baseline always_retry: diagnosis says retryable, so retry now",
            )
        ]
    return [
        CandidateAction(
            action_type=ActionType.no_action,
            score=1.0,
            reasoning="baseline always_retry: diagnosis says not retryable, do nothing",
        )
    ]


def threshold_policy(
    event: FailureEvent, diagnosis: Diagnosis
) -> list[CandidateAction]:
    """Baseline 2: retryability AND amount <= threshold.

    A competent engineer's simple two-signal rule: only auto-retry
    retryable tickets up to ``THRESHOLD`` (Rs.25,000.00, see module
    comment); larger tickets go to no_action. Deliberately simpler than
    the DecisionEngine's multi-signal rules -- no PTP, budget, backoff or
    confidence handling here -- but amount is a real, defensible recovery
    signal: big-ticket failures deserve a human, not an automated retry.
    """
    if diagnosis.is_retryable and event.amount <= THRESHOLD:
        return [
            CandidateAction(
                action_type=ActionType.retry_now,
                score=1.0,
                reasoning=(
                    "baseline threshold: retryable and amount "
                    f"{event.amount} <= {THRESHOLD}, so retry now"
                ),
            )
        ]
    return [
        CandidateAction(
            action_type=ActionType.no_action,
            score=1.0,
            reasoning=(
                "baseline threshold: not retryable or amount "
                f"{event.amount} > {THRESHOLD}, do nothing"
            ),
        )
    ]


def diagnosis_only_policy(
    event: FailureEvent, diagnosis: Diagnosis
) -> list[CandidateAction]:
    """Baseline 3: retryability alone, NO capability gate downstream.

    Output is identical to ``always_retry_policy``; the difference is
    enforced by ``harness_runner``, which bypasses the CapabilityGate for
    this policy so its decisions execute unconditionally. This isolates
    what the diagnosis alone buys versus what the full gated architecture
    adds on top.
    """
    if diagnosis.is_retryable:
        return [
            CandidateAction(
                action_type=ActionType.retry_now,
                score=1.0,
                reasoning="baseline diagnosis_only: retryable, retry now (gate bypassed)",
            )
        ]
    return [
        CandidateAction(
            action_type=ActionType.no_action,
            score=1.0,
            reasoning=(
                "baseline diagnosis_only: not retryable, do nothing (gate bypassed)"
            ),
        )
    ]
