# RecoveryLoop — Decision Schema

Canonical data model for the recovery pipeline. All components exchange these
Pydantic models. Enums are `str`-based so they serialize cleanly to JSON for
Razorpay API payloads, HTTP responses, and the audit log.

## Enums

```python
from enum import Enum


class FailureCode(str, Enum):
    insufficient_funds = "insufficient_funds"
    expired_card = "expired_card"
    bank_timeout = "bank_timeout"
    limit_drop = "limit_drop"
    gateway_error = "gateway_error"
    unknown = "unknown"


class ActionType(str, Enum):
    retry_now = "retry_now"
    retry_later = "retry_later"
    escalate = "escalate"
    no_action = "no_action"


class Channel(str, Enum):
    email = "email"
    whatsapp = "whatsapp"
```

## Models

```python
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class FailureEvent(BaseModel):
    case_id: str
    merchant_id: str
    amount: Decimal
    currency: str
    failure_code: FailureCode
    timestamp: datetime
    retry_count: int = Field(default=0, ge=0)
    has_active_ptp: bool = False
    ptp_date: Optional[date] = None


class Diagnosis(BaseModel):
    case_id: str
    root_cause: str
    is_retryable: bool
    confidence_note: str


class CandidateAction(BaseModel):
    action_type: ActionType
    reasoning: str
    score: float = Field(ge=0.0, le=1.0)


class GateDecision(BaseModel):
    case_id: str
    chosen_action: CandidateAction
    authorized: bool
    denial_reason: Optional[str] = None
    policy_rule_triggered: Optional[str] = None


class NotificationResult(BaseModel):
    channel: Channel
    delivered: bool
    provider_message_id: Optional[str] = None
    error: Optional[str] = None


class AuditRecord(BaseModel):
    case_id: str
    diagnosis: Diagnosis
    candidates: list[CandidateAction]
    gate_decision: GateDecision
    executed: bool
    execution_result: Optional[str] = None
    notification_result: Optional[NotificationResult] = None
    timestamp: datetime
```

## Evaluation-Only Metadata

The model below is NOT part of the live pipeline schema — components never
exchange it, and the pipeline never emits it. It exists solely so the eval
harness (M7) has a ground truth to score Diagnoser/DecisionEngine/Gate
outputs against. It is produced by the synthetic dataset generator in M1.

```python
class GroundTruthLabel(BaseModel):
    case_id: str
    expected_root_cause: str
    expected_is_retryable: bool
    expected_action: ActionType
    expected_authorized: bool
    is_adversarial: bool = False
    adversarial_reason: Optional[str] = None
```

## Field-Contract Notes

- **`amount`** is a `Decimal` denominated in `currency`; both must be present
  on every event. amount uses Decimal, not float, to avoid rounding errors in
  recovered-amount reporting.
- **`retry_count`** is `>= 0`; exhaustion rules (e.g. "no more than N retry_now
  per case") are DecisionEngine policy, not schema constraints.
- **`score`** is bounded `[0.0, 1.0]`; bounds are enforced by the validator.
- **`ptp_date`** is nullable — a promise-to-pay may exist (`has_active_ptp`)
  without a recorded date, but a stale past date must be treated as invalid by
  downstream components.
- **`authorized=True`** is the ONLY state in which the Executor may act or the
  Notifier may send. Any `GateDecision` with `authorized=False` must never reach
  an execution path.
- **`notification_result`** is populated only when the Notifier was actually
  invoked — i.e. only on authorized, executed cases.
- **`AuditRecord`** is immutable-by-convention: once written it is never
  mutated, only appended.