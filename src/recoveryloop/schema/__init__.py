from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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


class GroundTruthLabel(BaseModel):
    """Evaluation-only metadata (synthetic dataset / M7 eval harness).

    NEVER exchanged by the live recovery pipeline; it exists only so the
    eval harness has a ground truth to score Diagnoser/DecisionEngine/Gate
    outputs against.
    """

    case_id: str
    expected_root_cause: str
    expected_is_retryable: bool
    expected_action: ActionType
    expected_authorized: bool
    is_adversarial: bool = False
    adversarial_reason: Optional[str] = None
