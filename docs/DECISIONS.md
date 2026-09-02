# RecoveryLoop — Design Decisions

A dated log of deliberate choices where the DecisionEngine's rules (M3) were
kept even though they disagree with the M1 dataset's hand-authored ground
truth, plus the one case where the dataset was corrected instead. Entries
state the disagreement, which side is right, and why.

## 2026-09-02

### 7. `NotificationResult.delivered` reflects "Razorpay told to auto-notify", not confirmed delivery

M6's `RazorpayStatusNotifier` builds a delivery receipt by querying Razorpay's
`GET /v1/payment_links/{id}`. That endpoint reports only payment status
(`created` / `paid` / `expired` / `cancelled`) and has no notification-delivery
field. So `delivered=True` means "the payment link was created and Razorpay was
instructed to auto-notify the customer" — it is NOT an independent confirmation
that the customer received the message. This limitation is documented on
`RazorpayStatusNotifier.record()` and on the `NotificationResult` semantics;
downstream consumers (M7 AuditLog, M9 API) must not treat `delivered=True` as
proof of customer receipt.

## 2026-08-30

### 1. `unknown` failure_code → escalate (M3) vs no_action (M1 ground truth)

M1 labels every `unknown` case as `no_action` ("correctly refused"), while
the DecisionEngine's rule 5 maps a non-retryable diagnosis to `escalate` (0.7)
with `no_action` (0.2) as the secondary. The engine is right: rule 5's
semantics are "the cause cannot be fixed by an automated retry", and `unknown`
being unclassifiable means we cannot safely automate — but that is exactly when
a human should review the case rather than abandon it. Dropping `unknown`
cases to `no_action` would silently surrender potentially recoverable revenue;
escalation preserves the case for human judgment while still never firing an
automated retry. Kept `escalate`.

### 2. Exhausted-but-under-budget retries (3-9) → retry_later (M3) vs escalate (M1 ground truth)

For retryable causes with `retry_count` in 3-9, M1 labels `escalate` while the
engine's rule 6 bucket emits `retry_later` (0.65) with `escalate` (0.35) as the
secondary. The engine is right: retry_count 3-9 is "getting into this isn't
working easily" territory, which argues for spacing out attempts and *beginning*
to surface the case to a human — not handing it entirely to a human yet. The
escalate path is still present at 0.35, so the human gets visibility as a
backup while the automated backoff continues. Kept `retry_later` primary.

### 3. `retry_count >= 10` with a cause-retryable diagnosis (e.g. C-020) → escalate (M3) vs no_action (M1 ground truth)

M1 labels the retry_count=55 case `no_action`; the engine's rule 1 returns
`escalate` (0.8). The engine is right: a blown retry budget is a policy
decision that the *cause* may still be retryable does not override — but the
correct response is to route the case to a human, not to dead-end it. Rule 1's
reasoning says the cause "may still be retryable but requires human review",
which treats the exhausted budget as a hand-off trigger rather than a
write-off. `no_action` remains the 0.3 secondary. Kept `escalate`.

### 4. `insufficient_funds` (and other causes) at retry_count 1-2 → retry_later (M3, uniform) vs retry_now (M1 for insufficient_funds)

M1 reserved `retry_now` for `insufficient_funds` at retry 0-2, while the
engine applies its rule 6 {1,2} bucket uniformly across all retryable causes:
`retry_later` (0.7) primary, `retry_now` (0.5) secondary. The engine is right:
after one or more failures on a transient condition, a brief backoff before
retrying is more defensible than an immediate re-attempt, and this does not
depend on the specific failure code. Making the rule uniform also removes a
fragile special case that would otherwise require per-code exceptions to
maintain. Kept `retry_later` primary for all codes.

### 5. C-051 PTP staleness computed against the event's own timestamp (M3) vs a fixed reference date (M1)

**Dataset artifact, not a policy disagreement.** M1 classified C-051 as a
"stale promise-to-pay" by comparing its `ptp_date` (2026-08-27) against the
generator's fixed `REFERENCE_DATE` (2026-08-30), while the DecisionEngine
evaluates staleness against the event's own `timestamp` (2026-08-25), per the
M3 spec's determinism requirement. Against the event's date the PTP is still
valid, so the engine correctly returns `no_action` (rule 3) instead of
proceeding to recovery. The engine is definitively right here: staleness is a
property of the moment the event occurred, never of a wall-clock anchor, and
using the event's timestamp is what keeps the function deterministic and
testable. This is an M1 generation artifact of back-dated events, not a rule
disagreement; the dataset label for that one case is the side being corrected
in spirit (no dataset edit made — the disagreement is documented instead).

### 6. quiet_hours narrowed from {retry_now, escalate} to retry_now only

quiet_hours was initially specified to also block escalate, but escalate means
queuing for human review (no customer contact), so the quiet-hours restriction
was narrowed to retry_now only after the M4 end-to-end run showed 7/60 cases —
all escalations — being incorrectly denied overnight.