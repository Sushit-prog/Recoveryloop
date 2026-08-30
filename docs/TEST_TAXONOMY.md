# RecoveryLoop — Test Taxonomy

Test organization is per-component across four categories. The categories are
deliberately vertical: **unit** covers pure logic in isolation, **integration**
covers the full single-case pipeline, **batch-eval** covers metrics
correctness across many cases, and **adversarial** covers pathological and
malformed inputs. Every component must be exercised in all four categories.

## Categories

1. **Unit tests** — pure logic, no I/O, no side effects. In-memory fixtures
   only. Fast and deterministic.
2. **Integration tests** — full pipeline on one case: FailureEvent →
   Diagnoser → DecisionEngine → CapabilityGate → (Executor/Notifier) →
   AuditLog. Uses fake providers/channels.
3. **Batch-eval tests** — run the pipeline over a synthetic dataset and assert
   metric correctness (recovery rate, authorized-execution ratio,
   notification delivery rate, audit completeness).
4. **Adversarial cases** — invalid, ambiguous, or malicious input plus
   boundary conditions designed to violate component invariants.

## Per-Component Matrix

| Component           | Unit tests                                                                                                                                        | Integration tests                                                                                                | Batch-eval tests                                                                    | Adversarial cases                                                                                          |
|---------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| **Schema**          | Enum/value validation; `score` bounds `[0,1]`; `retry_count >= 0`; optional-field defaults; datetime parsing                                        | (no standalone integration — exercised via pipeline fixtures in every other component)                         | Schema round-trip of 1000 generated events loses no fields                                | Negative amount, unknown failure code string, `authorized` missing, union-type misuse, NaN score            |
| **Diagnoser**       | Failure code → root cause mapping; retryable judgment; confidence note formatting                                                                | Diagnoser output feeds real DecisionEngine and Gate inputs on one case                                          | Root-cause distribution sanity over the eval dataset                                  | Ambiguous failure codes (`unknown`, `gateway_error` vs `bank_timeout`); empty description; code not in enum |
| **DecisionEngine**  | Score ordering & ranking; candidate generation incl. forced `no_action`; retry-budget count logic                                                | DecisionEngine candidates feed the Gate on one case; chosen action matches top score                            | Recovery-rate metric; retry_later/escalate mix; no unranked cases                    | Retry-count exhaustion at limit; expired/missing PTP date; zero-candidate refusal; duplicate case ids      |
| **CapabilityGate**  | Policy-rule matching; `authorized` flip per rule; `policy_rule_triggered` & `denial_reason` population                                          | Gate verdict gates the real Executor/Notifier on one case                                                        | Authorized-execution ratio; denial-rate per policy rule                             | PTP expired in the past; merchant blacklist; unknown policy rule; malformed `GateDecision` input           |
| **Executor**        | Serialization of authorized action; result mapping to `AuditRecord.execution_result`                                                             | Executor runs with fake Razorpay client after `authorized=True` and records on AuditLog                         | Retry-success rate; execution attempt count vs authorized count                       | Attempt on `authorized=False` (must refuse); provider 5xx/timeout; empty case id; partial response         |
| **Notifier**        | Channel routing; message template rendering (payment link + context); `NotificationResult` mapping                                              | Notifier runs via real pipeline from Executor (fake channel provider) after gate approval on one case           | Delivery rate per channel; provider_message_id capture rate                          | Send attempted on `authorized=False` (must refuse); missing contact/channel config; provider outage; unsupported channel, non-enum channel |
| **AuditLog**        | Record serialization; required-field presence; timestamp population                                                                               | Full single-case flow produces exactly one complete append-only AuditRecord                                    | Audit completeness = 100% of processed cases; no partial/empty records               | Partial record rejects cleanly; duplicate append attempt; mutation attempt on stored record; corrupt payload |

## Adversarial Cases (Unified List)

- **Ambiguous failure codes** — `unknown` and gateway-level codes that blur the
  retryable/not-retryable line must resolve deterministically.
- **Expired promise-to-pay** — `has_active_ptp=True` with `ptp_date` in the
  past must not authorize a retry_later tied to a dead PTP.
- **Retry-count exhaustion** — a case at the retry ceiling must never produce
  an executed retry_now, regardless of score.
- **Malformed input** — invalid enums, negative amounts, out-of-bounds scores,
  and forbidden values must be rejected at the schema boundary, never silently
  coerced.