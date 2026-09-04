from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from recoveryloop.audit.audit_log import AuditLog
from recoveryloop.executor.fake_razorpay import FakeNotifier, FakeRazorpayClient
from recoveryloop.pipeline import run_pipeline
from recoveryloop.schema import ActionType, FailureEvent, GroundTruthLabel


class EvalException(BaseModel):
    case_id: str
    field: str
    expected: str
    actual: str
    reason: str


class EvalReport(BaseModel):
    total_cases: int
    correct_diagnosis_rate: float
    correct_decision_rate: float
    precision: float
    recall: float
    recovery_rate: float
    total_at_risk: Decimal
    attempted_amount: Decimal
    recovered_amount: Decimal
    exceptions: list[EvalException]

    def summary_text(self) -> str:
        lines = [
            "=== RecoveryLoop Eval Report ===",
            f"Total cases:          {self.total_cases}",
            f"Correct diagnosis:    {self.correct_diagnosis_rate:.1%}",
            f"Correct decision:     {self.correct_decision_rate:.1%}",
            f"Precision (execute):  {self.precision:.1%}",
            f"Recall (execute):     {self.recall:.1%}",
            f"Recovery rate:        {self.recovery_rate:.1%}",
            f"Total at-risk revenue: Rs.{self.total_at_risk:,.2f}",
            f"Attempted recovery:   Rs.{self.attempted_amount:,.2f}",
            f"Simulated recovered:  Rs.{self.recovered_amount:,.2f}",
            f"Divergences:          {len(self.exceptions)}",
        ]
        if self.exceptions:
            lines.append("")
            lines.append("--- Exception List ---")
            for ex in self.exceptions:
                lines.append(
                    f"  {ex.case_id:<8} {ex.field:<12} "
                    f"expected={ex.expected:<16} actual={ex.actual:<16} {ex.reason}"
                )
        return "\n".join(lines)

    def to_json(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


def run_eval(batch_path: Path) -> EvalReport:
    """Run the pipeline over every case in the batch and score against ground truth.

    Uses ``FakeRazorpayClient`` and ``FakeNotifier`` so no external calls are made.
    Returns an ``EvalReport`` with headline metrics and the full exception list.
    """
    with open(batch_path, encoding="utf-8") as fh:
        records = json.load(fh)

    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    audit = AuditLog(db_path=":memory:")

    correct_diag = 0
    correct_decision = 0
    tp = 0  # should execute AND did execute
    fp = 0  # should NOT execute BUT did
    fn = 0  # should execute BUT did NOT
    tn = 0  # should NOT execute AND did NOT
    total_at_risk = Decimal("0")
    attempted = Decimal("0")
    recovered = Decimal("0")
    exceptions: list[EvalException] = []

    for record in records:
        event = FailureEvent(**record["event"])
        gt = GroundTruthLabel(**record["ground_truth"])

        audit_result = run_pipeline(event, client, notifier, audit)
        top = audit_result.candidates[0]

        diag_match = gt.expected_root_cause in audit_result.diagnosis.root_cause
        if diag_match:
            correct_diag += 1
        else:
            exceptions.append(
                EvalException(
                    case_id=event.case_id,
                    field="diagnosis",
                    expected=gt.expected_root_cause,
                    actual=audit_result.diagnosis.root_cause,
                    reason="root cause mismatch",
                )
            )

        decision_match = top.action_type == gt.expected_action
        if decision_match:
            correct_decision += 1
        else:
            reason = (
                audit_result.gate_decision.denial_reason or "action type mismatch"
                if not audit_result.gate_decision.authorized
                else "action type mismatch"
            )
            exceptions.append(
                EvalException(
                    case_id=event.case_id,
                    field="decision",
                    expected=gt.expected_action.value,
                    actual=top.action_type.value,
                    reason=reason,
                )
            )

        should_execute = (
            audit_result.gate_decision.authorized
            and top.action_type in (ActionType.retry_now, ActionType.retry_later)
            and audit_result.executed
        )
        expected_execute = (
            gt.expected_action in (ActionType.retry_now, ActionType.retry_later)
            and gt.expected_authorized
        )

        if expected_execute:
            total_at_risk += event.amount

        if should_execute and expected_execute:
            tp += 1
            attempted += event.amount
            if gt.outcome == "paid":
                recovered += event.amount
        elif should_execute and not expected_execute:
            fp += 1
        elif not should_execute and expected_execute:
            fn += 1
        else:
            tn += 1

    total = len(records)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    recovery_rate = recovered / total_at_risk if total_at_risk > 0 else Decimal("0")

    return EvalReport(
        total_cases=total,
        correct_diagnosis_rate=correct_diag / total,
        correct_decision_rate=correct_decision / total,
        precision=precision,
        recall=recall,
        recovery_rate=float(recovery_rate),
        total_at_risk=total_at_risk,
        attempted_amount=attempted,
        recovered_amount=recovered,
        exceptions=exceptions,
    )
