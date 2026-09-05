from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from recoveryloop.audit.audit_log import AuditLog
from recoveryloop.eval.harness import run_eval
from recoveryloop.executor.fake_razorpay import FakeNotifier, FakeRazorpayClient
from recoveryloop.pipeline import run_pipeline
from recoveryloop.schema import FailureEvent

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "data" / "synthetic_batch.json"
BASELINES = ROOT / "data" / "baseline_comparison.json"

st.set_page_config(page_title="RecoveryLoop", layout="wide")

st.title("RecoveryLoop")
st.caption("A governed decision agent for autonomous financial recovery")


def _load_baselines() -> dict:
    with open(BASELINES, encoding="utf-8") as fh:
        return json.load(fh)


def _build_cases() -> pd.DataFrame:
    with open(BATCH, encoding="utf-8") as fh:
        records = json.load(fh)

    report = run_eval(BATCH)
    divergent = {
        ex.case_id: (
            "engine-policy choice (DECISIONS.md)"
            if ex.field == "decision"
            else "diagnosis wording artifact"
        )
        for ex in report.exceptions
    }

    client = FakeRazorpayClient()
    notifier = FakeNotifier()
    audit = AuditLog(db_path=":memory:")

    rows = []
    for record in records:
        event = FailureEvent(**record["event"])
        gt = record["ground_truth"]
        outcome = run_pipeline(event, client, notifier, audit)
        approved = outcome.gate_decision.authorized
        gate = (
            "authorized"
            if approved
            else f"blocked: {outcome.gate_decision.policy_rule_triggered or 'denied'}"
        )
        category = divergent.get(event.case_id)
        rows.append(
            {
                "case_id": event.case_id,
                "amount": float(event.amount),
                "root_cause": outcome.diagnosis.root_cause,
                "is_retryable": outcome.diagnosis.is_retryable,
                "decision": outcome.candidates[0].action_type.value,
                "gate": gate,
                "executed": outcome.executed,
                "ground_truth": gt["expected_action"],
                "gt_outcome": gt["outcome"],
                "divergence": "yes" if category else "",
                "category": category if category else "",
            }
        )
    return pd.DataFrame(rows)


st.subheader("Headline metrics")
report = run_eval(BATCH)
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total at risk", f"Rs.{report.total_at_risk:,.2f}")
c2.metric("Attempted recovery", f"Rs.{report.attempted_amount:,.2f}")
c3.metric("Simulated recovered", f"Rs.{report.recovered_amount:,.2f}")
c4.metric("Recovery rate", f"{report.recovery_rate:.1%}")
c5.metric("Precision", f"{report.precision:.1%}")
c6.metric("Recall", f"{report.recall:.1%}")
st.caption(
    "Computed in-process by the existing eval harness "
    "(recoveryloop.eval.harness.run_eval) over data/synthetic_batch.json — "
    "60 synthetic cases, fake Razorpay transport, no live data. "
    f"Correct decision: {report.correct_decision_rate:.1%} across "
    f"{len(report.exceptions)} documented divergences."
)

st.subheader("Case-by-case pipeline trace")
cases = _build_cases()
show_divergent_only = st.checkbox("Show only divergent rows")
display = cases[cases["divergence"] == "yes"] if show_divergent_only else cases
st.dataframe(
    display,
    width="stretch",
    column_config={
        "amount": "amount (INR)",
        "is_retryable": "retryable",
        "decision": "decision",
        "gate": "gate outcome",
        "executed": "executed",
        "ground_truth": "gt action",
        "gt_outcome": "gt outcome",
        "divergence": "divergence",
        "category": "divergence category",
    },
)
st.caption(
    "divergence is flagged from the eval report's own exception list; category is "
    "derived from the existing field (decision = documented engine-policy choice, "
    "diagnosis = wording artifact on C-008). Per-case reasoning for every one is in "
    "docs/DECISIONS.md."
)

st.subheader("Baseline comparison")
baselines = _load_baselines()
runs = baselines["runs"]
df = pd.DataFrame(
    [
        {
            "policy": r["policy"],
            "recovery_rate_pct": r["recovery_rate_pct"],
            "recovered_amount": r["recovered_amount"],
            "correct_decision_pct": r["correct_decision_pct"],
            "precision": round(r["precision"] * 100, 1),
            "recall": round(r["recall"] * 100, 1),
        }
        for r in runs
    ]
)
st.dataframe(df, width="stretch", hide_index=True)

left, right = st.columns(2)
left.subheader("Recovery rate (%)")
left.bar_chart(df.set_index("policy")["recovery_rate_pct"])
right.subheader("Recovered amount (INR)")
right.bar_chart(df.set_index("policy")["recovered_amount"])
st.caption(baselines["capability_gate_note"])
st.caption(
    "The decision_engine row reproduces the locked README evaluation numbers; "
    "the threshold policy collapses because its Rs.25,000 amount cap skips the "
    "big-ticket cases that hold most recoverable revenue — see docs/BASELINES.md."
)

st.divider()
st.caption(
    "All numbers are from a fixed 60-case synthetic dataset with a fake transport — "
    "no live or production data. See docs/BASELINES.md for the baseline ablation and "
    "docs/DECISIONS.md for the documented divergence reasoning."
)
