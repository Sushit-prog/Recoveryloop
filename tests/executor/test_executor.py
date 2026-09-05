from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from recoveryloop.decision.decision_engine import decide
from recoveryloop.diagnosis.diagnoser import diagnose
from recoveryloop.executor.executor import execute
from recoveryloop.executor.fake_razorpay import FakeNotifier, FakeRazorpayClient
from recoveryloop.executor.protocols import (
    CustomerContact,
    ExecutionNotAuthorized,
    PaymentLinkOutcome,
)
from recoveryloop.executor.razorpay_client import RazorpayPaymentLinkClient
from recoveryloop.gate.capability_gate import authorize
from recoveryloop.schema import (
    ActionType,
    CandidateAction,
    Diagnosis,
    FailureCode,
    FailureEvent,
    GateDecision,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic_batch.json"


# ── helpers ──────────────────────────────────────────────────────────────


def _mk_event(
    failure_code: FailureCode = FailureCode.bank_timeout,
    *,
    amount: str = "100.00",
    retry_count: int = 0,
    has_active_ptp: bool = False,
    ptp_date: date | None = None,
    timestamp: datetime = datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC),
) -> FailureEvent:
    return FailureEvent(
        case_id="T-001",
        merchant_id="mer_test",
        amount=Decimal(amount),
        currency="INR",
        failure_code=failure_code,
        timestamp=timestamp,
        retry_count=retry_count,
        has_active_ptp=has_active_ptp,
        ptp_date=ptp_date,
    )


def _mk_diag(is_retryable: bool = True) -> Diagnosis:
    return Diagnosis(
        case_id="T-001",
        root_cause="test cause",
        is_retryable=is_retryable,
        confidence_note="test note",
    )


def _mk_gate(
    action: ActionType,
    *,
    authorized: bool = True,
    denial_reason: str | None = None,
) -> GateDecision:
    return GateDecision(
        case_id="T-001",
        chosen_action=CandidateAction(action_type=action, reasoning="test", score=0.5),
        authorized=authorized,
        denial_reason=denial_reason,
    )


# ── denied-gate raises with zero transport calls ─────────────────────────


def test_denied_gate_raises_no_transport_calls() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    gate = _mk_gate(ActionType.retry_now, authorized=False, denial_reason="quiet hours")

    with pytest.raises(ExecutionNotAuthorized):
        execute(_mk_event(), _mk_diag(), gate, client, notifier)

    assert client.calls == []
    assert notifier.calls == []


# ── retry path: paise / currency / notify body + notifier invoked ────────


def test_retry_now_creates_link_and_notifies() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    event = _mk_event(amount="250.50")
    gate = _mk_gate(ActionType.retry_now)

    result = execute(event, _mk_diag(), gate, client, notifier)

    assert result.executed is True
    assert result.action_type == ActionType.retry_now
    assert result.link_id is not None
    assert result.link_url is not None
    assert result.notification_result is not None
    assert result.notification_result.delivered is True

    call = client.calls[0]
    assert call["amount"] == Decimal("250.50")
    assert call["currency"] == "INR"
    assert call["reference_id"] == "T-001"

    assert len(notifier.calls) == 1
    assert notifier.calls[0]["case_id"] == "T-001"
    assert notifier.calls[0]["outcome"].status == "created"


def test_retry_later_creates_link_and_notifies() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    gate = _mk_gate(ActionType.retry_later)

    result = execute(_mk_event(), _mk_diag(), gate, client, notifier)

    assert result.executed is True
    assert result.link_id is not None
    assert len(client.calls) == 1
    assert len(notifier.calls) == 1


def test_customer_contact_forwarded() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    customer = CustomerContact(email="a@b.com", phone="+91123", name="T")
    gate = _mk_gate(ActionType.retry_now)

    execute(_mk_event(), _mk_diag(), gate, client, notifier, customer=customer)

    call = client.calls[0]
    assert call["notify"] is customer


# ── escalate / no_action: no link ────────────────────────────────────────


def test_escalate_creates_no_link() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    gate = _mk_gate(ActionType.escalate)

    result = execute(_mk_event(), _mk_diag(), gate, client, notifier)

    assert result.executed is False
    assert result.action_type == ActionType.escalate
    assert result.link_id is None
    assert result.link_url is None
    assert result.notification_result is None
    assert client.calls == []
    assert notifier.calls == []


def test_no_action_creates_no_link() -> None:
    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    gate = _mk_gate(ActionType.no_action)

    result = execute(_mk_event(), _mk_diag(), gate, client, notifier)

    assert result.executed is False
    assert result.action_type == ActionType.no_action
    assert client.calls == []
    assert notifier.calls == []


# ── live client shape via httpx.MockTransport ────────────────────────────


class _FakeTransport(httpx.BaseTransport):
    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        self.body = body or {}
        self.last_request: httpx.Request | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return httpx.Response(
            self.status,
            json=self.body,
            request=request,
        )


def test_live_client_method_url_auth_body() -> None:
    body = {"id": "plink_test123", "short_url": "https://rzp.io/i/test"}
    transport = _FakeTransport(status=200, body=body)
    client = RazorpayPaymentLinkClient()
    client._http = httpx.Client(transport=transport)
    client._auth = ("ak_test", "sk_test")  # type: ignore[assignment]

    outcome = client.create_payment_link(
        amount=Decimal("150.00"),
        currency="INR",
        reference_id="CASE-7",
        notify=CustomerContact(email="x@y.com"),
    )

    req = transport.last_request
    assert req is not None
    assert req.method == "POST"
    assert str(req.url) == "https://api.razorpay.com/v1/payment_links"
    loaded = json.loads(req.content)
    assert loaded["amount"] == 15000
    assert loaded["currency"] == "INR"
    assert loaded["reference_id"] == "CASE-7"
    assert loaded["notify"]["email"] is True
    assert loaded["customer"]["email"] == "x@y.com"

    assert outcome.status == "created"
    assert outcome.link_id == "plink_test123"
    assert outcome.short_url == "https://rzp.io/i/test"


def test_live_client_notify_email_is_boolean_customer_carries_address() -> None:
    transport = _FakeTransport(status=200, body={"id": "pl", "short_url": "u"})
    client = RazorpayPaymentLinkClient()
    client._http = httpx.Client(transport=transport)

    outcome = client.create_payment_link(
        amount=Decimal("150.00"),
        currency="INR",
        reference_id="CASE-9",
        notify=CustomerContact(email="buyer@example.com"),
    )

    req = transport.last_request
    assert req is not None
    loaded = json.loads(req.content)
    assert loaded["notify"] == {"email": True}
    assert loaded["customer"] == {"email": "buyer@example.com"}
    assert outcome.status == "created"


def test_live_client_notify_phone_and_name_shape() -> None:
    transport = _FakeTransport(status=200, body={"id": "pl", "short_url": "u"})
    client = RazorpayPaymentLinkClient()
    client._http = httpx.Client(transport=transport)

    client.create_payment_link(
        amount=Decimal("99.00"),
        currency="INR",
        reference_id="CASE-10",
        notify=CustomerContact(name="A Customer", phone="+911234567890"),
    )

    req = transport.last_request
    assert req is not None
    loaded = json.loads(req.content)
    assert loaded["notify"] == {"sms": True}
    assert loaded["customer"] == {
        "name": "A Customer",
        "contact": "+911234567890",
    }


def test_live_client_no_notify_sends_no_notify_or_customer() -> None:
    transport = _FakeTransport(status=200, body={"id": "pl", "short_url": "u"})
    client = RazorpayPaymentLinkClient()
    client._http = httpx.Client(transport=transport)

    client.create_payment_link(
        amount=Decimal("50.00"), currency="INR", reference_id="CASE-11"
    )

    req = transport.last_request
    assert req is not None
    loaded = json.loads(req.content)
    assert "notify" not in loaded
    assert "customer" not in loaded


def test_live_client_500_error_returns_failed() -> None:
    transport = _FakeTransport(status=500, body={"error": "boom"})
    client = RazorpayPaymentLinkClient()
    client._http = httpx.Client(transport=transport)

    outcome = client.create_payment_link(
        amount=Decimal("10.00"),
        currency="INR",
        reference_id="CASE-8",
    )
    assert outcome.status == "failed"
    assert "500" in outcome.error  # type: ignore[union-attr]


# ── fake determinism ────────────────────────────────────────────────────


def test_fake_client_deterministic_ids() -> None:
    a = FakeRazorpayClient().create_payment_link(
        amount=Decimal("100"), currency="INR", reference_id="C1"
    )
    b = FakeRazorpayClient().create_payment_link(
        amount=Decimal("100"), currency="INR", reference_id="C1"
    )
    assert a.link_id == b.link_id
    assert a.short_url == b.short_url


def test_fake_client_fail_mode() -> None:
    client = FakeRazorpayClient(fail=True)
    outcome = client.create_payment_link(
        amount=Decimal("100"), currency="INR", reference_id="C1"
    )
    assert outcome.status == "failed"
    assert len(client.calls) == 1


# ── 60-case end-to-end visibility run ───────────────────────────────────


def test_end_to_end_executor_visibility_across_dataset() -> None:
    with open(DATA, encoding="utf-8") as fh:
        records = json.load(fh)

    client = FakeRazorpayClient()
    notifier = FakeNotifier()

    rows: list[str] = []
    executed_count = 0
    links_by_action: Counter[str] = Counter()
    escalations = 0
    noops = 0
    denials = 0

    for record in records:
        event = FailureEvent(**record["event"])
        diag = diagnose(event)
        candidates = decide(event, diag)
        gate = authorize(event, diag, candidates[0])

        if not gate.authorized:
            denials += 1
            rows.append(
                f"{event.case_id:<8}{gate.chosen_action.action_type.value:<13}"
                f"{'denied':<9}{gate.policy_rule_triggered}"
            )
            continue

        result = execute(event, diag, gate, client, notifier)

        if result.executed:
            executed_count += 1
            links_by_action[result.action_type.value] += 1
        elif result.action_type == ActionType.escalate:
            escalations += 1
        else:
            noops += 1

        link_col = result.link_id or "-"
        rows.append(
            f"{event.case_id:<8}{result.action_type.value:<13}"
            f"{str(result.executed):<9}{link_col}"
        )

    total = len(records)
    print("\n== Executor end-to-end (diagnose -> decide -> authorize -> execute) ==")
    print(f"{'case_id':<8}{'action':<13}{'executed':<9}link_id / policy_rule")
    for row in rows:
        print(row)
    print(f"executed: {executed_count}/{total}")
    print(f"links by action: {dict(links_by_action)}")
    print(f"escalations: {escalations}")
    print(f"no-ops: {noops}")
    print(f"denials: {denials}")
    print(f"transport calls: {len(client.calls)}")
    print(f"notifier calls:  {len(notifier.calls)}")

    assert total == 60
    assert executed_count + escalations + noops + denials == total
