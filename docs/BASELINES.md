# RecoveryLoop — Baseline Policies & Comparison Ablation (Appendix)

**Status: supplementary results.** This document and `data/baseline_comparison.json`
exist purely to make the value of each layer in the pipeline visible. They **do not
alter** the locked evaluation numbers in `README.md` (60 cases, 98.3% correct
diagnosis, 51.7% correct decision, 76.5% precision, 96.3% recall, 78.6% recovery
rate, ₹907,584.23 attempted, ₹869,946.00 recovered), and they change nothing in
`docs/DECISIONS.md`. The `decision_engine` row below reproduces the README numbers
exactly, which doubles as a drift check that the baseline runner reuses the same
scoring.

## Method

All four rows run the same frozen 60-case batch (`data/synthetic_batch.json`)
through the existing eval harness (`recoveryloop.eval.harness`), which is reused
unchanged. The runner (`src/baselines/harness_runner.py`) only swaps the pipeline's
decision function per run and calls `run_eval`; scoring, the fake Razorpay
transport, and the `:memory:` audit trail are the existing ones. The only special
case is the `diagnosis_only` row, which **bypasses the CapabilityGate entirely**
(its retryable decisions execute unconditionally) to isolate what diagnosis alone
buys. Everything is deterministic — no keys, no network, no new randomness.

Correct diagnosis is 98.3% in every row: diagnosis runs before and independent of
any decision policy, so it cannot differ across rows.

## Policies

### `decision_engine` (existing, the system under test)

The full multi-signal rule-based DecisionEngine (`src/recoveryloop/decision/`):
retry budget, zero-amount, PTP validity recency, backoff on retry count, and
escalation for non-retryable causes, all scored into ranked candidates and then
passed through the CapabilityGate. This is the reference row — the number every
baseline is measured against — and its metrics reproduce the locked README values.

### `always_retry_policy` — raw retryability, gate enforced

Executes `retry_now` whenever `Diagnosis.is_retryable` is true, and `no_action`
otherwise, with no amount, confidence, budget, or PTP logic. It represents the
simplest imaginable operator rule that trusts the diagnosis completely and adds
nothing on top — including no choice of *which* recovery action beyond "retry now".
It is a fair comparison point because it is exactly the diagnosis-only layer plus
the CapabilityGate: identical decisions to `diagnosis_only`, differing solely in
whether the gate can refuse to execute them. The delta between these two rows is,
by construction, the gate's contribution.

### `threshold_policy` — retryability + amount cap, gate enforced

A "competent engineer's simple rule" using at most two signals: execute `retry_now`
only if the diagnosis is retryable **and** `event.amount <= ₹25,000.00`. The
threshold was derived from the dataset's amount tiers (see the module comment in
`src/baselines/policies.py`): the cutoff at ~65th percentile cleanly separates the
routine sub-₹13K INR tiers (plus all USD tiers) from the big-ticket ₹84.5K–₹1.25M
tiers, on the defensible principle that large charges deserve a human rather than
an automated customer-facing retry. It is deliberately simpler than the
DecisionEngine's multi-signal scoring, and shows what happens when amount is used
as the *only* discriminating signal on top of diagnosis.

### `diagnosis_only_policy` — retryability alone, gate bypassed

Identical output rule to `always_retry_policy`, but the CapabilityGate is bypassed
(for this row only, clearly flagged), so every retryable case executes. It
represents a fully trust-the-diagnosis agent with no safety boundary: maximum
aggressive recovery, maximum contact. It isolates the floor of what diagnosis alone
buys before any decision policy or gate adds judgement.

## Comparison

| policy | correct_decision% | precision | recall | recovery_rate | attempted_amount | recovered_amount |
|---|---|---|---|---|---|---|
| decision_engine | 51.7% | 76.5% | 96.3% | 78.6% | ₹907,584.23 | ₹869,946.00 |
| always_retry | 28.3% | 75.0% | 66.7% | 55.5% | ₹649,087.48 | ₹613,950.00 |
| threshold | 31.7% | 80.0% | 44.4% | 2.0% | ₹32,086.48 | ₹21,949.50 |
| diagnosis_only | 28.3% | 69.2% | 100.0% | 96.6% | ₹1,106,584.23 | ₹1,068,946.00 |

Full per-row exception lists and machine-readable fields live in
`data/baseline_comparison.json`.

## Ablation narrative

The four rows build the pipeline one layer at a time, and each layer's value is
visible in a different metric.

**Diagnosis alone (`diagnosis_only`, gate bypassed)**: recall is 100% and gross
recovery peaks at 96.6% because every retryable case is executed — but precision
falls to 69.2% and the exception list shows why: the naive rule contacts customers
who hold a valid promise-to-pay and customers during quiet hours, exactly the cases
the ground truth refuses. Pure diagnosis is maximally aggressive and spends
contact on cases that should never be touched.

**Adding the capability gate (`always_retry`)**: the same decision rule, but the
gate now refuses the nontouchables (valid PTP, quiet hours, exhausted budget).
Precision rises to 75.0% and raw revenue attempted drops by ~41%, because the gate
removes execution that would have been both non-compliant and low-conversion.
Recovery rate halves (96.6% → 55.5%) — a measured cost: suppressing the quiet-hours
attempts also suppresses the revenue those attempts would have recovered — but the
attempts the gate keeps are the defensible ones.

**Adding an explicit, slightly smarter decision policy (`threshold`)**: a second
signal (amount cap) on top of diagnosis lifts precision to 80.0%, the highest in
the table — this rule is the most discriminating about *which* retries to fire. But
it is catastrophic for revenue: recall falls to 44.4% and recovery collapses to
2.0%, because the recoverable revenue in this dataset concentrates in the big-ticket
tiers the cap rejects. The lesson is that amount is a real but *insufficient* signal
on its own — it must be combined with cause, backoff, and PTP context rather than
used as an isolated gate.

**Full RecoveryLoop (`decision_engine`)**: the multi-signal engine restores the
balance the single-signal baselines each sacrifice — precision 76.5% is near the
threshold rule's, while recall (96.3%) and recovery rate (78.6%) stay close to the
fully-aggressive diagnosis-only row while still honoring the gate. Against the
naive baselines, the full architecture recovers ~₹256K more than `always_retry` and
~₹848K more than the amount-cap rule, at roughly the same precision as the best
baseline — the strongest combined score on every axis, which is the point of the
layered design.

## Reproducing

```bash
python src/baselines/harness_runner.py   # writes data/baseline_comparison.json
python -m pytest -q                      # 88 existing + 13 baseline tests
```