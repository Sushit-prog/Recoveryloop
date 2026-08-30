# RecoveryLoop — Component Interfaces

Each component has exactly one responsibility, one input type, one output
type, and one or more hard invariants expressed as "MUST NEVER" rules. No
component may reach outside its contract — all execution, notification, and
side effects happen only in the Executor and Notifier after the gate approves.

## Diagnoser

The Diagnoser is responsible for converting a raw `FailureEvent` into a
structured `Diagnosis`: it classifies the failure root cause and decides
whether the failure is retryable, producing a one-line confidence note for
humans. Its input is a single `FailureEvent`, and its output is a single
`Diagnosis`. The Diagnoser MUST NEVER contact any external service, MUST NEVER
make a policy judgement on retry budget or merchant limits (that is the
DecisionEngine's job), and MUST NEVER emit or mutate anything other than the
`Diagnosis` object it returns.

## DecisionEngine

The DecisionEngine is responsible for taking a `Diagnosis` (plus the original
`FailureEvent` context it needs) and ranking candidate recovery actions —
`retry_now`, `retry_later`, `escalate`, `no_action` — each with a bounded
[0, 1] score, using retry-count exhaustion, promise-to-pay state, and failure
code as inputs. Its input is a `FailureEvent` together with its `Diagnosis`,
and its output is an ordered `list[CandidateAction]`. The DecisionEngine MUST
NEVER execute any recovery action itself, MUST NEVER call the Notifier or any
provider API, and MUST NEVER return fewer than one candidate per case (a
case can always be `no_action`).

## CapabilityGate

The CapabilityGate is responsible for checking a chosen candidate action
against configured client policy and capability constraints (retry budget,
PTP validity, channel availability, blast radius) and returning an
authorization verdict. Its input is a `FailureEvent`, its `Diagnosis`, and a
`CandidateAction` chosen by the DecisionEngine; its output is a
`GateDecision` carrying `authorized=True` or `False`, plus the specific policy
rule that triggered the denial. The CapabilityGate MUST NEVER call any Razorpay
API directly — execution happens only in the Executor, only after
`authorized=True` — and MUST NEVER itself execute, send, or schedule anything;
it is purely a decision object.

## Executor

The Executor is responsible for carrying out an authorized action against the
external world — e.g. invoking the Razorpay payment API for a retry — when and
only when the CapabilityGate returns `authorized=True`, and for reporting back
the outcome. Its input is the full context: `FailureEvent`, `GateDecision`
(`authorized` must be `True`), and the chosen `Diagnosis`; its output is an
execution result string (or structured result) recorded on the `AuditRecord`.
On successful execution of an authorized action, the Executor invokes the
Notifier to deliver the recovery message and includes the resulting
NotificationResult in what it reports back for the AuditRecord. The Executor
MUST NEVER act on a `GateDecision` where `authorized=False`, MUST
NEVER invoke Notifier or any recovery effect for a denied case, and MUST NEVER
decide policy — it only executes what the gate has approved.

## Notifier

The Notifier is responsible for delivering a recovery message — payment link
plus recovery context — to the customer over a configured channel, and
returning a delivery receipt. Its input is a `CandidateAction` that has passed
the CapabilityGate (`authorized=True`) plus the `FailureEvent` it belongs to,
and its output is a `NotificationResult` carrying the `Channel`, a
`delivered` flag, an optional `provider_message_id`, and an optional `error`.
The Notifier MUST NEVER send anything for a `GateDecision` where
`authorized=False`, and MUST NEVER be called directly by the DecisionEngine —
only by the Executor, after the gate has approved the action.

## AuditLog

The AuditLog is responsible for persisting an append-only, immutable record of
every stage of a recovery attempt — the diagnosis, the ranked candidates, the
gate verdict, execution state, and timestamp — so the whole pipeline is
reconstructible. Its input is an assembled `AuditRecord` (with the diagnosis,
candidates, `GateDecision`, and execution result), and its output is an
acknowledgement/persisted confirmation of the record. The AuditLog MUST NEVER
alter or delete a previously written record, MUST NEVER write a partial
record (it either persists the whole `AuditRecord` or fails cleanly), and MUST
NEVER emit a business decision — it is a passive sink, not a component that
can veto or approve an action.