# RecoveryLoop

A governed decision agent for autonomous financial recovery — diagnose payment failures, decide the right intervention, and execute bounded recovery with a full audit trail.

**Submission for Razorpay AI Buildathon 2026, Track 03 — AI Revenue Recovery**

---

## Problem

When a payment fails, most systems either do nothing (and lose the revenue) or blindly retry every failure the same way. That either misses cases that need human escalation, or annoys customers with premature retries. RecoveryLoop solves this by making recovery decisions safely autonomous: diagnose why a payment failed, choose the correct intervention, but gate every execution behind explicit authorization rules — and record every action in an append-only audit log. The goal is recovery that is auditable, governed, and honest about where it is uncertain.

---

## Architecture

```
Detect → Diagnose → Decide → Gate → Execute → Audit
          |            |         |       |          |
      Diagnoser   DecisionEng.  Gate  Executor+Notifier  AuditLog
```

Six stages, one owner per decision:

| Stage | Owner | What it does |
|---|---|---|
| **Detect** | — | A `FailureEvent` arrives with amount, failure code, retry count, promise-to-pay status, and merchant context. |
| **Diagnose** | `Diagnoser` | Maps the failure code to a structured `Diagnosis`: root cause, retryability, and a confidence note. Deterministic and rule-based. |
| **Decide** | `DecisionEngine` | Ranks candidate recovery actions (`retry_now`, `retry_later`, `escalate`, `no_action`) scored `[0,1]` from diagnosis + event context. |
| **Gate** | `CapabilityGate` | The safety boundary. Checks retry budget, promise-to-pay validity, quiet-hours policy, and channel authorization. Returns `authorized: True/False` with which rule fired if denied. |
| **Execute** | `Executor` + `Notifier` | Creates a Razorpay payment link and records notification delivery. Only runs when `authorized=True`. Hard-refuses any gate denial. |
| **Audit** | `AuditLog` | Append-only, immutable SQLite trail of every stage of every run. No update or delete path exists. |

**Why the Gate matters:** The CapabilityGate is the non-bypassable stopping boundary. No upstream component — including any future LLM-based planner — can produce a side effect without its authorization. This is the buildathon's "stopping rules" criterion made explicit: the system can decide, but it cannot act unless the gate says it is allowed to.

---

## Evaluation Results

The pipeline is scored against 60 synthetic cases with hand-authored ground truth, using a deterministic fake Razorpay transport — no external calls, no keys, fully reproducible.

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

Attempted vs. recovered is separated deliberately. Executing an action — creating a payment link and instructing Razorpay to auto-notify — is a recovery **attempt**, not proof of recovered revenue. Revenue is only counted as recovered when the case's outcome is `paid`. The ~74% realized conversion rate among retried cases is an assumed illustrative parameter, documented in the dataset generator and not claimed as real Razorpay data.

---

## Honest Divergences

Of the 30 divergences between the engine and the dataset's ground truth:

- **29 are deliberate engine-policy choices** — places where the engine's rule is defended as the better call (e.g. `unknown` failure codes escalate rather than silently get no-action; retry_count 3-9 uses backoff rather than immediate escalation; quiet-hours narrowed to block `retry_now` only, not `escalate`).
- **1 is a dataset labeling artifact** — C-051's promise-to-pay staleness was computed against a fixed reference date in the generator rather than the event's own timestamp.

Every one of the 30 is documented with full reasoning in [`docs/DECISIONS.md`](docs/DECISIONS.md). This is a deliberate design choice: transparency over cherry-picked results. Most recovery-agent demos show a single success case. We show the full distribution, including where the engine disagrees with the ground truth — and why.

---

## Baseline Comparison

To prove the governed decision engine earns its complexity, we compared it against three naive policies through the same eval harness:

| Policy | Precision | Recall | Recovery rate | Recovered amount |
|---|---|---|---|---|
| **DecisionEngine** (full) | 76.5% | 96.3% | 78.6% | ₹869,946.00 |
| Always-retry (diagnosis + gate) | 75.0% | 66.7% | 55.5% | ₹613,950.00 |
| Amount threshold (≤ ₹25K + gate) | 80.0% | 44.4% | 2.0% | ₹21,949.50 |
| Diagnosis-only (no gate) | 69.2% | 100.0% | 96.6% | ₹1,068,946.00 |

The key finding: a simple "only recover under ₹25,000" rule looks reasonable on paper — but collapses to a 2% recovery rate. The recoverable revenue in this dataset concentrates in the big-ticket cases above that threshold. The naive rule forfeits roughly ₹848K compared to the full engine by optimizing for the wrong variable. Its precision is the highest in the table (80.0%), but only because it rarely acts — recall falls to 44.4%.

The diagnosis-only row (gate bypassed) shows 96.6% gross recovery but contacts customers who hold a valid promise-to-pay and during quiet hours — exactly the cases the gate blocks. Adding the gate raises precision from 69.2% to 75.0% while the multi-signal engine restores recall to 96.3%.

Full per-row exception lists and machine-readable fields are in [`data/baseline_comparison.json`](data/baseline_comparison.json). Detailed ablation narrative in [`docs/BASELINES.md`](docs/BASELINES.md).

---

## Live Verification

Everything above uses a deterministic fake transport. To prove the system works against the real world, [`scripts/live_demo_run.py`](scripts/live_demo_run.py) runs a single case through the full pipeline against Razorpay's **live test-mode API** — no mocks.

What happens:

1. A `FailureEvent` is constructed (insufficient funds, retryable, ₹3,499 INR).
2. The pipeline diagnoses, decides (retry now, score 0.9), gates (authorized), and executes.
3. Razorpay creates a real payment link and is instructed to email the customer.
4. The audit record is written to `data/live_demo_audit.db` and read back to verify consistency.

This live run caught a real bug that the fully mocked test suite (88/88 green) had missed: Razorpay's `notify.email` field must be a boolean flag (`true`/`false`), with the actual email address passed separately in a `customer` object. The fake transport never validated against the real API contract. The contract was fixed, regression tests were added, and the live call succeeded.

**Caveat:** `NotificationResult.delivered=True` means Razorpay was instructed to auto-notify the customer — it is not an independent confirmation that the customer received the message. Razorpay's payment-link API reports payment status only, not notification delivery. This distinction is documented in [`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## Dashboard

A read-only Streamlit dashboard provides an interactive view of the evaluation results:

- **Headline metrics** — recovery rate, precision, recall, amounts at risk and recovered.
- **60-case pipeline trace table** — every case's diagnosis, decision, gate outcome, execution, ground truth, and divergence status. A filter toggle shows only divergent rows.
- **Baseline comparison charts** — side-by-side bar charts for recovery rate and recovered amount across the four policies.

```bash
streamlit run dashboard/app.py
```

All numbers are computed in-process by the existing eval harness over `data/synthetic_batch.json`. No live or production data is used.

---

## Running It

Requires Python ≥ 3.11.

```bash
pip install -e . pytest uvicorn
```

**Tests** (core pipeline + baselines + live-contract regression):

```bash
python -m pytest -q
```

**Regenerate the 60-case dataset:**

```bash
python scripts/generate_dataset.py
```

**Run the evaluation harness:**

```bash
python -c "from pathlib import Path; from recoveryloop.eval.harness import run_eval; print(run_eval(Path('data/synthetic_batch.json')).summary_text())"
```

**Run the baseline comparison ablation:**

```bash
python src/baselines/harness_runner.py    # writes data/baseline_comparison.json
```

**Serve the dashboard:**

```bash
streamlit run dashboard/app.py
```

**Serve the demo API:**

```bash
uvicorn recoveryloop.api:app --reload
```

| Endpoint | Purpose |
|---|---|
| `POST /events` | Accept a `FailureEvent`, run the full pipeline, return the `AuditRecord` |
| `GET /audit/{id}` | Fetch a persisted `AuditRecord` by id |
| `GET /metrics` | Return the current `EvalReport` |

**Live demo run** (requires Razorpay test-mode keys):

```bash
RAZORPAY_KEY_ID=rzp_test_xxx RAZORPAY_KEY_SECRET=xxx \
LIVE_DEMO_EMAIL=you@example.com python scripts/live_demo_run.py
```

Requires `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `LIVE_DEMO_EMAIL` via `.env` file or environment variables. The script safety-guards against production keys — it refuses to run unless the key ID starts with `rzp_test_`. Keys are never printed or committed.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Pipeline & logic | Python 3.11+, Pydantic |
| API | FastAPI |
| Audit storage | SQLite (append-only) |
| Dashboard | Streamlit |
| Payment integration | Razorpay test-mode API |
| Testing | pytest |

---

## Next Iteration / Non-Goals

**Planned:**

- **LLM-backed Diagnoser** — The current rule-based Diagnoser achieves 98.3% accuracy, so introducing LLM nondeterminism was deliberately deferred as a risk/scope decision for this submission, not an oversight. The interface is schema-first (`FailureEvent` in, `Diagnosis` out) specifically so an LLM diagnoser can be a drop-in replacement without touching the DecisionEngine, Gate, Executor, or AuditLog.
- **Larger production-scale evaluation** — The current dataset is 60 synthetic cases, not a live production dataset.

**Explicitly out of scope:**

- No telephony or voice channels.
- No production keys or live payment amounts beyond the Razorpay test-mode demo.
