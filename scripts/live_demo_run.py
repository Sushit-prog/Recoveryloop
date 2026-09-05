#!/usr/bin/env python3
"""Standalone ONE-OFF live demo run through RecoveryLoop's real pipeline.

NOT part of the eval/test suite and never imported by anything else. It runs a
single hand-built ``FailureEvent`` through the standard ``run_pipeline`` using
the LIVE Razorpay test-mode client, so Razorpay really creates a payment link
and (via the ``notify`` payload) really sends an email to the supplied address.

This is intentionally non-deterministic (live API call) and isolated from
``data/synthetic_batch.json``, the eval harness, and all 101 tests. The audit
trail for this run is written to ``data/live_demo_audit.db`` — a separate file
from the eval path's ``data/audit.db``.

Usage:
    RAZORPAY_KEY_ID=rzp_test_xxx RAZORPAY_KEY_SECRET=xxx \
    LIVE_DEMO_EMAIL=you@example.com python scripts/live_demo_run.py

Safety: refuses to run unless RAZORPAY_KEY_ID starts with ``rzp_test_``, and
never prints the full key or the secret. If either env var is missing it fails
cleanly and does not proceed.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import recoveryloop.pipeline as pipeline  # noqa: E402
from recoveryloop.audit.audit_log import AuditLog  # noqa: E402
from recoveryloop.executor.protocols import CustomerContact  # noqa: E402
from recoveryloop.executor.razorpay_client import RazorpayPaymentLinkClient  # noqa: E402
from recoveryloop.notifier import RazorpayStatusNotifier  # noqa: E402
from recoveryloop.schema import FailureCode, FailureEvent  # noqa: E402

AUDIT_DB = ROOT / "data" / "live_demo_audit.db"
DEMO_AMOUNT = Decimal("3499.00")
DEMO_CURRENCY = "INR"
DEMO_MERCHANT = "mer_live_demo"


def mask_key_id(key_id: str) -> str:
    """``rzp_test_`` prefix + last 4, middle masked, e.g. ``rzp_test_***1234``."""
    return f"{key_id[:9]}***{key_id[-4:]}"


def _load_dotenv(path: Path) -> None:
    """Load ``KEY=VALUE`` lines from ``path`` into the environment.

    Dependency-free fallback so the one-off demo can be run as
    ``python scripts/live_demo_run.py`` without exporting any secrets by hand.
    Comment/blank lines are skipped, whitespace is stripped, and variables that
    are already set in the real shell environment are never overridden.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def validate_env() -> tuple[str, str, str]:
    """Validate and return ``(key_id, key_secret, email)`` from the environment.

    Loads a repo-root ``.env`` first (if present) so keys can be kept in a
    gitignored file; real shell environment variables take precedence. Exits 2
    if any required variable is missing (naming only the missing ones, never
    echoing the secret) and exits 3 if the key id is not a test key.
    """
    _load_dotenv(ROOT / ".env")
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    email = os.environ.get("LIVE_DEMO_EMAIL", "")

    missing = []
    if not key_id:
        missing.append("RAZORPAY_KEY_ID")
    if not key_secret:
        missing.append("RAZORPAY_KEY_SECRET")
    if not email:
        missing.append("LIVE_DEMO_EMAIL")
    if missing:
        print("ERROR: missing required environment variable(s): " + ", ".join(missing))
        print(
            "Example:\n  RAZORPAY_KEY_ID=rzp_test_xxx RAZORPAY_KEY_SECRET=xxx \\\n"
            "  LIVE_DEMO_EMAIL=you@example.com python scripts/live_demo_run.py"
        )
        sys.exit(2)

    if not key_id.startswith("rzp_test_"):
        print(
            "REFUSING TO RUN: RAZORPAY_KEY_ID must be a Razorpay TEST key "
            "starting with 'rzp_test_' (got a key that does not look like one). "
            "This script must never run against live/production keys."
        )
        sys.exit(3)

    return key_id, key_secret, email


def build_demo_event() -> FailureEvent:
    """One realistic retryable failed payment, guaranteed outside quiet hours."""
    midnight_noon = datetime.combine(
        datetime.now(UTC).date(), time(12, 0, 0), tzinfo=UTC
    )
    return FailureEvent(
        case_id=f"LIVE-DEMO-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        merchant_id=DEMO_MERCHANT,
        amount=DEMO_AMOUNT,
        currency=DEMO_CURRENCY,
        failure_code=FailureCode.insufficient_funds,
        timestamp=midnight_noon,
        retry_count=0,
        has_active_ptp=False,
        ptp_date=None,
    )


def main() -> None:
    key_id, _key_secret, email = validate_env()

    print("=== RecoveryLoop LIVE demo run (test mode) ===")
    print(f"Key ID:                {mask_key_id(key_id)}")
    print(f"Delivery email:        {email}")
    print("Note: this will create a REAL Razorpay payment link and instruct")
    print("Razorpay to email the customer. Audit file: data/live_demo_audit.db")
    print()

    event = build_demo_event()
    print("--- Constructed FailureEvent ---")
    print(
        f"case_id={event.case_id} amount={event.amount} {event.currency} "
        f"code={event.failure_code.value} retry_count={event.retry_count}"
    )
    print(
        f"timestamp={event.timestamp.isoformat()} "
        "(pinned to 12:00 UTC so the gate's quiet-hours rule cannot block)"
    )
    print()

    client = RazorpayPaymentLinkClient()
    notifier = RazorpayStatusNotifier()
    audit = AuditLog(db_path=AUDIT_DB)

    contact = CustomerContact(email=email)

    original_execute = pipeline.execute
    captured: dict = {}

    def _execute_with_customer(ev, diagnosis, gate_decision, cl, ntf):
        result = original_execute(
            ev, diagnosis, gate_decision, cl, ntf, customer=contact
        )
        captured["execution_result"] = result
        return result

    try:
        pipeline.execute = _execute_with_customer
        record = pipeline.run_pipeline(event, client, notifier, audit)
    finally:
        pipeline.execute = original_execute

    top = record.candidates[0]
    print("--- Diagnosis ---")
    print(f"root_cause:    {record.diagnosis.root_cause}")
    print(f"is_retryable:  {record.diagnosis.is_retryable}")
    print(f"confidence:    {record.diagnosis.confidence_note}")
    print()
    print("--- Decision (top candidate) ---")
    print(f"action:        {top.action_type.value}")
    print(f"score:         {top.score}")
    print(f"reasoning:     {top.reasoning}")
    print()
    print("--- CapabilityGate ---")
    if record.gate_decision.authorized:
        print("authorized:    True")
    else:
        print("authorized:    False")
        print(f"rule:          {record.gate_decision.policy_rule_triggered}")
        print(f"reason:        {record.gate_decision.denial_reason}")
    print()
    print("--- Execution ---")
    if record.executed:
        exec_result = captured.get("execution_result")
        if exec_result is not None:
            print(f"executed:      {exec_result.executed}")
            print(f"link_id:       {exec_result.link_id}")
            print(f"payment link:  {exec_result.link_url}")
        print(f"detail:        {record.execution_result}")
    else:
        print(f"executed:      {record.executed}")
        print(f"detail:        {record.execution_result}")
    print()
    print("--- Notifier receipt ---")
    if record.notification_result is not None:
        nr = record.notification_result
        print(f"channel:       {nr.channel.value}")
        print(f"delivered:     {nr.delivered}")
        if nr.error:
            print(f"error:         {nr.error}")
        print(
            "NOTE: delivered=True means Razorpay was INSTRUCTED to auto-notify "
            "the customer,"
        )
        print("      not that delivery was confirmed (Razorpay's link API reports")
        print("      payment status only — see docs/DECISIONS.md section 7).")
    else:
        print("no notifier receipt (no payment link was created)")
    print()

    print("--- Audit record persisted to data/live_demo_audit.db ---")
    readback = AuditLog(db_path=AUDIT_DB)
    prior = [r for r in readback.get_all() if r.case_id != event.case_id]
    matches = [r for r in readback.get_all() if r.case_id == event.case_id]
    print(f"(append-only file; {len(prior)} prior record(s) in file)")
    for rec in matches:
        print(rec.model_dump_json(indent=2))
    print()
    print(
        "=== LIVE demo run complete — compare the emitted audit record side-by-side "
        "with the email you received ==="
    )


if __name__ == "__main__":
    main()
