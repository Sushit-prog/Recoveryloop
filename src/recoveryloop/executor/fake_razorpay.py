from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from recoveryloop.executor.protocols import CustomerContact, PaymentLinkOutcome
from recoveryloop.schema import NotificationResult


def _seeded_id(seed: str, prefix: str) -> str:
    h = hashlib.sha256(seed.encode()).hexdigest()[:16]
    return f"{prefix}_{h}"


@dataclass
class FakeRazorpayClient:
    """Deterministic stand-in for RazorpayPaymentLinkClient.

    Every call produces a predictable link_id / short_url derived from
    ``reference_id`` + ``amount``.  A ``calls`` log is maintained for
    zero-call assertions.
    """

    fail: bool = False
    calls: list[dict] = field(default_factory=list, repr=False)

    def create_payment_link(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference_id: str,
        notify: Optional[CustomerContact] = None,
        callback_url: Optional[str] = None,
    ) -> PaymentLinkOutcome:
        self.calls.append(
            {
                "amount": amount,
                "currency": currency,
                "reference_id": reference_id,
                "notify": notify,
                "callback_url": callback_url,
            }
        )
        if self.fail:
            return PaymentLinkOutcome(status="failed", error="simulated failure")

        link_id = _seeded_id(f"{reference_id}:{amount}", "plink")
        short_url = f"https://rzp.io/i/{link_id}"
        return PaymentLinkOutcome(
            link_id=link_id,
            short_url=short_url,
            status="created",
            provider_message_id=link_id,
        )


@dataclass
class FakeNotifier:
    """Deterministic stand-in for the real Notifier (M6).

    Records every ``record()`` call and returns a synthetic
    ``NotificationResult``.
    """

    calls: list[dict] = field(default_factory=list, repr=False)

    def record(
        self,
        *,
        case_id: str,
        channel: str,
        outcome: PaymentLinkOutcome,
        contact: Optional[CustomerContact] = None,
    ) -> NotificationResult:
        self.calls.append(
            {
                "case_id": case_id,
                "channel": channel,
                "outcome": outcome,
                "contact": contact,
            }
        )
        msg_id = _seeded_id(f"{case_id}:{channel}", "notif")
        return NotificationResult(
            channel=channel,  # type: ignore[arg-type]
            delivered=outcome.status == "created",
            provider_message_id=msg_id,
        )
