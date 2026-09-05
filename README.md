# RecoveryLoop

RecoveryLoop is a bounded, auditable decision agent for autonomous financial actions. Payment recovery is the domain it is applied to; the architecture — capability-gated execution, immutable audit trail, honest batch evaluation — generalizes to any agent that makes consequential real-world decisions.

## Architecture

```
Detect -> Diagnose -> Decide -> Gate -> Execute -> Audit
          |            |         |       |          |
      Diagnoser   DecisionEng.  Gate  Executor+Notifier  AuditLog
```

`Detection` is the raw `FailureEvent` (failed payment, retry budget, promise-to-pay context, merchant). Exactly one component owns each decision after that:

| Component | Responsibility |
|---|---|
| **Diagnoser** | Converts a raw `FailureEvent` into a structured `Diagnosis`: root cause, retryability, one-line confidence note. Never executes or judges policy. |
| **DecisionEngine** | Ranks candidate recovery actions (`retry_now`, `retry_later`, `escalate`, `no_action`), each scored `[0,1]`, from diagnosis + event context. Never executes anything. |
| **CapabilityGate** | The safety boundary. Given the top candidate, it returns a deterministic `authorized=True/False` verdict against retry budget, PTP validity, channel and quiet-hours policy — plus which rule fired if denied. |
| **Executor** | Carries out an authorized action against Razorpay (creates a payment link) and reports the outcome. Only ever runs when `authorized=True`. |
| **Notifier** | Emits the recovery message through the approved channel and returns a delivery receipt. Only the Executor may call it, only after gate approval. |
| **AuditLog** | Append-only, immutable SQLite trail of every stage of every run. No update or delete path exists. |

The Gate is the deterministic, non-bypassable authority. Whatever the decision layer proposes, the Gate must authorize before any side effect exists; no upstream component — including a future LLM-based planner — can act without its approval. The Executor and Notifier hard-refuse any `GateDecision` with `authorized=False`.

## The Diagnoser: why it is rule-based

The Diagnoser is currently rule-based and fully deterministic, mapping failure codes and overrides (zero amount, retry budget exhaustion) onto a structured schema: `root_cause`, `is_retryable`, `confidence_note`.

This is a deliberate choice. Financial decisions need the behavior that produced an action to be reconstructible on demand; a deterministic classifier makes the audit trail self-explanatory with zero stochastic variance between runs. The interface is schema-first precisely so an LLM-backed diagnoser can be a drop-in replacement later — same input contract (`FailureEvent`), same output contract (`Diagnosis`), no rewrite of the DecisionEngine, Gate, or any downstream component. This architecture was built for the day the diagnoser becomes a model; it is not waiting to be retrofitted.

## Evaluation

The pipeline is scored against 60 synthetic cases with hand-authored ground truth, using the deterministic fake Razorpay transport — no external calls, no keys, fully reproducible in a CI run.

| Metric | Value |
|---|---|
| Total cases | 60 |
| Correct diagnosis | 98.3% |
| Correct decision | 51.7% |
| Precision (should-execute) | 76.5% |
| Recall (should-execute) | 96.3% |
| Recovery rate | 78.6% |
| Total at-risk revenue | ₹1,106,584.23 |
| Attempted recovery | ₹907,584.23 |
| Simulated recovered | ₹869,946.00 |
| Divergences from ground truth | 30 |

Attempted vs. recovered is separated deliberately. Executing an action — creating a payment link and instructing Razorpay to auto-notify — is a recovery **attempt**, not proof of recovered revenue. Revenue is only counted as recovered when the case's outcome is `paid`. The ~74% realized conversion rate among retried cases is an assumed illustrative parameter, documented openly in the dataset generator (`scripts/generate_dataset.py`) and not claimed as real Razorpay data. Understating recovery beats overstating it.

The 30 divergences are the honesty mechanism, and they exist precisely so this eval is not "60/60 passed." Of those, 29 are decision-level and documented one-by-one in `docs/DECISIONS.md` as deliberate engine choices against the synthetic ground truth — places where the engine's policy (escalate an unknown cause, back off at 1–2 retries, keep the overnight escalation path) is the defended answer, with the reasoning on record. The remaining divergence is a diagnosis wording artifact on the zero-amount adversarial case (C-008). Every one of the 30 is emitted into the eval report's exception list; none is swept into an aggregate.

## Appendix: baseline comparison

A supplementary ablation comparison of naive baseline decision policies against the full DecisionEngine, run through the same eval harness, is documented in docs/BASELINES.md. It is appendix material only and does not change the evaluation numbers above.

## Running it

Requires Python ≥ 3.11.

```bash
pip install -e . pytest uvicorn
```

Regenerate the deterministic 60-case dataset:

```bash
python scripts/generate_dataset.py
```

Run the full test suite:

```bash
python -m pytest
```

Run the evaluation and print the human-readable report:

```bash
python -c "from pathlib import Path; from recoveryloop.eval.harness import run_eval; print(run_eval(Path('data/synthetic_batch.json')).summary_text())"
```

Evaluation uses the fake Razorpay transport: no keys required, deterministic across runs.

Serve the demo API:

```bash
uvicorn recoveryloop.api:app --reload
```

| Endpoint | Purpose | Notes |
|---|---|---|
| `POST /events` | Accept a `FailureEvent`, run the full pipeline, return the `AuditRecord` | Demo-grade; needs `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` for live execution |
| `GET /audit/{id}` | Fetch a persisted `AuditRecord` by id | Read from the SQLite audit trail |
| `GET /metrics` | Return the current `EvalReport` | Recomputed on request; no keys needed |

## Non-goals / next iteration

- No telephony or voice channels — a deliberate scope cut for this submission window.
- The Diagnoser is rule-based today; an LLM-backed Diagnoser behind the same interface is the next iteration.
- The dataset is 60 synthetic cases, not a live production dataset.
- No baseline-vs-RecoveryLoop benchmark was run in this submission window — the numbers above measure RecoveryLoop against ground truth, not against alternatives.