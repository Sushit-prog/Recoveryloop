from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import httpx

from recoveryloop.decision.decision_engine import decide
from recoveryloop.diagnosis.diagnoser import diagnose
from recoveryloop.executor.fake_razorpay import FakeRazorpayClient
from recoveryloop.executor.protocols import CustomerContact, PaymentLinkOutcome
from recoveryloop.gate.capability_gate import authorize
from recoveryloop.notifier import RazorpayStatusNotifier
from recoveryloop.schema import (
    Channel,
    FailureEvent,
    NotificationResult,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic_batch.json"


class _FakeTransport(httpx.BaseTransport):
    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        self.body = body or {}
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body, request=request)


def _mk_notifier(transport: httpx.BaseTransport) -> RazorpayStatusNotifier:
    n = RazorpayStatusNotifier()
    n._http = httpx.Client(transport=transport)
    n._auth = ("ak_test", "sk_test")  # type: ignore[assignment]
    return n


def _mk_outcome(
    link_id: str = "plink_abc", status: str = "created"
) -> PaymentLinkOutcome:
    return PaymentLinkOutcome(
        link_id=link_id,
        short_url=f"https://rzp.io/i/{link_id}",
        status=status,
        provider_message_id=link_id,
    )


def _mk_contact() -> CustomerContact:
    return CustomerContact(email="a@b.com", phone="+91123", name="T")


# ── success path ─────────────────────────────────────────────────────────


def test_record_returns_delivered_receipt_on_2xx() -> None:
    transport = _FakeTransport(
        status=200, body={"id": "plink_abc", "status": "created"}
    )
    notifier = _mk_notifier(transport)

    result = notifier.record(
        case_id="T-001",
        channel="email",
        outcome=_mk_outcome(),
        contact=_mk_contact(),
    )

    assert isinstance(result, NotificationResult)
    assert result.channel == Channel.email
    assert result.delivered is True
    assert result.provider_message_id == "plink_abc"
    assert result.error is None

    req = transport.requests[0]
    assert req.method == "GET"
    assert str(req.url) == "https://api.razorpay.com/v1/payment_links/plink_abc"


def test_record_accepts_paid_and_partially_paid_status() -> None:
    for status in ("paid", "partially_paid"):
        transport = _FakeTransport(status=200, body={"status": status})
        notifier = _mk_notifier(transport)
        result = notifier.record(
            case_id="T-001", channel="email", outcome=_mk_outcome()
        )
        assert result.delivered is True


def test_record_channel_normalization() -> None:
    transport = _FakeTransport(status=200, body={"status": "created"})
    notifier = _mk_notifier(transport)
    result = notifier.record(case_id="T-001", channel="whatsapp", outcome=_mk_outcome())
    assert result.channel == Channel.whatsapp
    assert result.delivered is True


# ── failure paths ─────────────────────────────────────────────────────────


def test_record_link_not_found_returns_failed() -> None:
    transport = _FakeTransport(status=404, body={"error": "not found"})
    notifier = _mk_notifier(transport)

    result = notifier.record(case_id="T-001", channel="email", outcome=_mk_outcome())
    assert result.delivered is False
    assert result.error is not None
    assert "404" in result.error


def test_record_server_error_returns_failed() -> None:
    transport = _FakeTransport(status=500, body={"error": "boom"})
    notifier = _mk_notifier(transport)

    result = notifier.record(case_id="T-001", channel="email", outcome=_mk_outcome())
    assert result.delivered is False
    assert "500" in result.error  # type: ignore[union-attr]


def test_record_network_error_no_raise() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(boom)
    notifier = _mk_notifier(transport)

    result = notifier.record(case_id="T-001", channel="email", outcome=_mk_outcome())
    assert result.delivered is False
    assert result.error is not None


def test_record_unknown_channel_no_raise() -> None:
    notifier = _mk_notifier(_FakeTransport(status=200, body={"status": "created"}))

    result = notifier.record(case_id="T-001", channel="pigeon", outcome=_mk_outcome())
    assert result.delivered is False
    assert result.error is not None
    assert "pigeon" in result.error


def test_record_no_created_link_returns_failed() -> None:
    transport = _FakeTransport(status=200, body={"status": "created"})
    notifier = _mk_notifier(transport)

    result = notifier.record(
        case_id="T-001",
        channel="email",
        outcome=_mk_outcome(status="failed"),
    )
    assert result.delivered is False
    assert transport.requests == []


# ── delivered semantics documentation present ────────────────────────────


def test_record_delivered_semantics_documented() -> None:
    doc = re.sub(r"\s+", " ", RazorpayStatusNotifier.record.__doc__ or "")
    assert "delivered=True" in doc
    assert "auto-notify" in doc
    assert "cannot independently confirm" in doc


# ── end-to-end visibility run ────────────────────────────────────────────


def test_end_to_end_notifier_visibility_across_dataset() -> None:
    with open(DATA, encoding="utf-8") as fh:
        records = json.load(fh)

    transport = _FakeTransport(status=200, body={"status": "created"})
    real_notifier = _mk_notifier(transport)
    client = FakeRazorpayClient()

    rows: list[str] = []
    delivered_count = 0
    failures_by_channel: Counter[str] = Counter()
    notifier_calls = 0

    for record in records:
        event = FailureEvent(**record["event"])
        diag = diagnose(event)
        gate = authorize(event, diag, decide(event, diag)[0])

        if not gate.authorized:
            continue

        outcome = client.create_payment_link(
            amount=event.amount,
            currency=event.currency,
            reference_id=event.case_id,
            notify=_mk_contact(),
        )

        if outcome.status != "created":
            rows.append(f"{event.case_id:<8}link_failed")
            continue

        result = real_notifier.record(
            case_id=event.case_id,
            channel="email",
            outcome=outcome,
            contact=_mk_contact(),
        )
        notifier_calls += 1
        if result.delivered:
            delivered_count += 1
        else:
            failures_by_channel[result.channel.value] += 1

        rows.append(
            f"{event.case_id:<8}{str(result.delivered):<10}{result.provider_message_id or '-'}"
        )

    total = len([r for r in records])
    print("\n== Notifier end-to-end (executed retries -> status receipt) ==")
    print(f"{'case_id':<8}{'delivered':<10}provider_message_id")
    for row in rows:
        print(row)
    print(f"notifier calls: {notifier_calls}/{total}")
    print(f"delivered receipts: {delivered_count}")
    print(f"failures by channel: {dict(failures_by_channel)}")

    assert notifier_calls <= total
    assert delivered_count + sum(failures_by_channel.values()) == notifier_calls
