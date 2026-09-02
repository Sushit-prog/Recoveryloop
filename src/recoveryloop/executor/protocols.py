from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from recoveryloop.schema import ActionType, NotificationResult


@dataclass(frozen=True)
class CustomerContact:
    """Runtime-supplied customer details for notification delivery.

    Optional and intentionally NOT part of the schema: the Executor receives
    it at call time, so no dataset/model changes are required.
    """

    email: Optional[str] = None
    phone: Optional[str] = None
    name: Optional[str] = None


@dataclass(frozen=True)
class PaymentLinkOutcome:
    """Result of a single Razorpay payment-link creation attempt.

    ``status`` is one of: ``"created"`` or ``"failed"``.  On failure the
    caller can inspect ``error``; on success ``link_id`` / ``short_url``
    and ``provider_message_id`` are populated.
    """

    link_id: Optional[str] = None
    short_url: Optional[str] = None
    status: str = "failed"
    provider_message_id: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ExecutionResult:
    """Immutable record of what the Executor did for one case."""

    executed: bool
    action_type: ActionType
    detail: str
    link_id: Optional[str] = None
    link_url: Optional[str] = None
    notification_result: Optional[NotificationResult] = None


class ExecutionNotAuthorized(Exception):
    """Raised when the gate denied the action but execute() was called anyway."""


@runtime_checkable
class RazorpayClient(Protocol):
    def create_payment_link(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference_id: str,
        notify: Optional[CustomerContact] = None,
        callback_url: Optional[str] = None,
    ) -> PaymentLinkOutcome: ...


@runtime_checkable
class Notifier(Protocol):
    def record(
        self,
        *,
        case_id: str,
        channel: str,
        outcome: PaymentLinkOutcome,
        contact: Optional[CustomerContact] = None,
    ) -> NotificationResult: ...
