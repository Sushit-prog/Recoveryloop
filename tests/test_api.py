from __future__ import annotations

from fastapi.testclient import TestClient

from recoveryloop.api import app

client = TestClient(app)


def test_post_events_returns_audit_record() -> None:
    event_payload = {
        "case_id": "T-001",
        "merchant_id": "mer_test",
        "amount": "150.00",
        "currency": "INR",
        "failure_code": "bank_timeout",
        "timestamp": "2026-08-30T14:00:00Z",
        "retry_count": 0,
        "has_active_ptp": False,
        "ptp_date": None,
    }

    resp = client.post("/events", json=event_payload)
    assert resp.status_code == 200

    body = resp.json()
    assert body["case_id"] == "T-001"
    assert "diagnosis" in body
    assert "gate_decision" in body
    assert "event" in body
    assert body["pipeline_version"] == "0.0.1"


def test_get_audit_not_found() -> None:
    resp = client.get("/audit/nonexistent:id")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "record not found"


def test_get_metrics_returns_eval_report() -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200

    body = resp.json()
    assert body["total_cases"] == 60
    assert "correct_diagnosis_rate" in body
    assert "correct_decision_rate" in body
    assert "precision" in body
    assert "recall" in body
    assert "recovery_rate" in body
    assert "attempted_amount" in body
    assert "recovered_amount" in body
