#!/usr/bin/env python3
"""M?: run the existing eval harness against 4 decision policies and diff them.

Supplementary ablation tool. The locked harness (``recoveryloop.eval.harness``)
is used unchanged; this script swaps the *pipeline's* ``decide``/``authorize``
globals per run and calls ``run_eval``, so all scoring/metrics are the existing
ones reused exactly. For ``diagnosis_only`` the CapabilityGate is bypassed by
replacing ``pipeline.authorize`` with an always-authorized verdict.

Deterministic: same frozen 60-case dataset, same fake Razorpay transport,
no new randomness. Writes a single table-renderable JSON file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import recoveryloop.pipeline as pipeline
from baselines.policies import (
    always_retry_policy,
    diagnosis_only_policy,
    threshold_policy,
)
from recoveryloop.decision.decision_engine import decide
from recoveryloop.eval.harness import EvalReport, run_eval
from recoveryloop.schema import FailureEvent, GateDecision, Diagnosis, CandidateAction

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH = ROOT / "data" / "synthetic_batch.json"
DEFAULT_OUT = ROOT / "data" / "baseline_comparison.json"

GATE_BYPASS_NOTE = (
    "diagnosis_only bypasses the CapabilityGate entirely (pipeline.authorize is "
    "swapped for an always-authorized verdict), so its retryable decisions execute "
    "unconditionally. Every other row runs Detect -> Diagnose -> Decide -> "
    "Gate -> Execute -> Audit."
)

_ORIGINAL_DECIDE = pipeline.decide
_ORIGINAL_AUTHORIZE = pipeline.authorize


def _always_authorized(
    event: FailureEvent, diagnosis: Diagnosis, candidate: CandidateAction
) -> GateDecision:
    return GateDecision(
        case_id=event.case_id,
        chosen_action=candidate,
        authorized=True,
    )


def _amount(value) -> float:
    return round(float(value), 2)


def _pct(value: float) -> float:
    return round(value * 100, 1)


def _run_policy(
    policy: str, decide_fn, capability_gate: bool, batch: Path
) -> tuple[EvalReport, dict]:
    try:
        pipeline.decide = decide_fn
        if not capability_gate:
            pipeline.authorize = _always_authorized
        report = run_eval(batch)
    finally:
        pipeline.decide = _ORIGINAL_DECIDE
        pipeline.authorize = _ORIGINAL_AUTHORIZE

    return report, {
        "policy": policy,
        "capability_gate": capability_gate,
        "total_cases": report.total_cases,
        "correct_diagnosis_pct": _pct(report.correct_diagnosis_rate),
        "correct_decision_pct": _pct(report.correct_decision_rate),
        "precision": round(report.precision, 4),
        "recall": round(report.recall, 4),
        "recovery_rate_pct": _pct(report.recovery_rate),
        "total_at_risk": _amount(report.total_at_risk),
        "attempted_amount": _amount(report.attempted_amount),
        "recovered_amount": _amount(report.recovered_amount),
        "divergences": len(report.exceptions),
        "exceptions": [ex.model_dump() for ex in report.exceptions],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", default=str(DEFAULT_BATCH), help="60-case JSON batch"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output JSON path")
    args = parser.parse_args(argv)

    batch = Path(args.dataset)
    runs = [
        ("decision_engine", decide, True),
        ("always_retry", always_retry_policy, True),
        ("threshold", threshold_policy, True),
        ("diagnosis_only", diagnosis_only_policy, False),
    ]

    results: list[dict] = []
    for name, decide_fn, gate in runs:
        print(
            f"\n===== {name} — CapabilityGate: {'ENFORCED' if gate else 'BYPASSED'} ====="
        )
        report, result = _run_policy(name, decide_fn, gate, batch)
        print(report.summary_text())
        results.append(result)

    payload = {
        "dataset": str(batch),
        "n_cases": results[0]["total_cases"] if results else 0,
        "capability_gate_note": GATE_BYPASS_NOTE,
        "runs": results,
    }
    out = Path(args.out)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {out}")

    headers = [
        "policy",
        "correct_decision%",
        "precision",
        "recall",
        "recovery_rate",
        "attempted_amount",
        "recovered_amount",
    ]
    rows = []
    for r in results:
        rows.append(
            [
                r["policy"],
                f"{r['correct_decision_pct']:.1f}",
                f"{r['precision']:.1%}",
                f"{r['recall']:.1%}",
                f"{r['recovery_rate_pct']:.1f}%",
                f"{r['attempted_amount']:,.2f}",
                f"{r['recovered_amount']:,.2f}",
            ]
        )
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print("\n" + line)
    print("-|-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(c.ljust(widths[i]) for i, c in enumerate(row)))


if __name__ == "__main__":
    main()
