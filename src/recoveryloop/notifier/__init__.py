from __future__ import annotations

import os

import httpx

from recoveryloop.executor.protocols import CustomerContact, PaymentLinkOutcome
from recoveryloop.schema import Channel, NotificationResult

_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayStatusNotifier:
    """Delivery-receipt notifier backed by the Razorpay payment-link API.

    The Executor already instructs Razorpay to auto-notify the customer
    (``notify:{email,sms}``) when it creates a payment link.  This notifier
    therefore does not transport the message itself: it queries Razorpay's
    payment-link status and turns that into a ``NotificationResult`` receipt.

    IMPORTANT semantics of ``delivered``: Razorpay's
    ``GET /v1/payment_links/{id}`` only reports *payment* status
    (``created`` / ``paid`` / ``expired`` / ``cancelled``); it exposes no
    field about whether the notification was actually delivered to the
    customer.  Consequently ``delivered=True`` means "the payment link was
    created and Razorpay was instructed to auto-notify" — it is NOT an
    independent confirmation that the customer received the message.

    Like the Razorpay client, this notifier never raises into the pipeline:
    network errors and non-2xx responses become ``delivered=False``.
    """

    def __init__(self) -> None:
        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
        self._auth = (key_id, key_secret)
        self._http = httpx.Client(timeout=30.0)

    def record(
        self,
        *,
        case_id: str,
        channel: str,
        outcome: PaymentLinkOutcome,
        contact: CustomerContact | None = None,
    ) -> NotificationResult:
        """Build a delivery receipt for one payment-link notification.

        Delivers nothing itself; it queries Razorpay for the payment-link's
        status and reports that status as a receipt.

        ``delivered=True`` means the payment link was created and Razorpay
        was instructed to auto-notify the customer — NOT that the customer
        confirmed receipt.  Razorpay's link-status endpoint reports payment
        status only (created/paid/expired/cancelled) and has no
        notification-delivery field, so this system cannot independently
        confirm delivery.
        """
        try:
            enum_channel = Channel(channel)
        except ValueError:
            return NotificationResult(
                channel=Channel.email,
                delivered=False,
                error=f"unknown channel: {channel!r}",
            )

        if outcome.link_id is None or outcome.status != "created":
            return NotificationResult(
                channel=enum_channel,
                delivered=False,
                error="no created payment link to report delivery for",
            )

        try:
            resp = self._http.get(
                f"{_BASE_URL}/payment_links/{outcome.link_id}",
                auth=self._auth,
            )
            if resp.status_code >= 400:
                return NotificationResult(
                    channel=enum_channel,
                    delivered=False,
                    error=f"HTTP {resp.status_code}: {resp.text}",
                )
            data = resp.json()
            status = data.get("status", "unknown")
            if status not in ("created", "paid", "partially_paid"):
                return NotificationResult(
                    channel=enum_channel,
                    delivered=False,
                    error=f"unexpected link status: {status}",
                )
            return NotificationResult(
                channel=enum_channel,
                delivered=True,
                provider_message_id=outcome.link_id,
            )
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            return NotificationResult(
                channel=enum_channel,
                delivered=False,
                error=str(exc),
            )
