from __future__ import annotations

import os
from decimal import Decimal

import httpx

from recoveryloop.executor.protocols import CustomerContact, PaymentLinkOutcome

_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayPaymentLinkClient:
    """Live Razorpay payment-link client.

    Uses HTTP BasicAuth with ``RAZORPAY_KEY_ID`` / ``RAZORPAY_KEY_SECRET``
    environment variables.  Amounts are converted from the canonical
    ``Decimal`` form (rupees) to paise (int) before sending.

    This client **never** raises into the pipeline: network errors and
    non-2xx responses are converted into ``PaymentLinkOutcome(status="failed")``.
    """

    def __init__(self) -> None:
        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
        self._auth = (key_id, key_secret)
        self._http = httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    def create_payment_link(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference_id: str,
        notify: CustomerContact | None = None,
        callback_url: str | None = None,
    ) -> PaymentLinkOutcome:
        paise = int(amount * 100)

        body: dict = {
            "amount": paise,
            "currency": currency,
            "reference_id": reference_id,
        }

        if notify is not None:
            # Razorpay's API wants notify.email/sms as BOOLEAN flags telling it
            # to send the notification; the recipient's contact details go in
            # a separate `customer` object. Sending the address in notify.email
            # is rejected with HTTP 400 ("notify:email must be boolean").
            notify_payload: dict = {}
            customer_payload: dict = {}
            if notify.email is not None:
                notify_payload["email"] = True
                customer_payload["email"] = notify.email
            if notify.phone is not None:
                notify_payload["sms"] = True
                customer_payload["contact"] = notify.phone
            if notify.name is not None:
                customer_payload["name"] = notify.name
            if notify_payload:
                body["notify"] = notify_payload
            if customer_payload:
                body["customer"] = customer_payload

        if callback_url is not None:
            body["callback_url"] = callback_url

        try:
            resp = self._http.post(
                f"{_BASE_URL}/payment_links",
                json=body,
                auth=self._auth,
            )
            if resp.status_code >= 400:
                return PaymentLinkOutcome(
                    status="failed",
                    error=f"HTTP {resp.status_code}: {resp.text}",
                )
            data = resp.json()
            return PaymentLinkOutcome(
                link_id=data.get("id"),
                short_url=data.get("short_url"),
                status="created",
                provider_message_id=data.get("id"),
            )
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            return PaymentLinkOutcome(status="failed", error=str(exc))
