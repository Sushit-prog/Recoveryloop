# RecoveryLoop — Live Demo Script

Timed walkthrough (~5 minutes). All on-screen commands and numbers are verified
against the repo. Two terminal commands are run live (eval harness, baseline
ablation) and the final segment is a single unmocked call to Razorpay's test-mode
API. See `docs/BASELINES.md` and `docs/DECISIONS.md` for the cited numbers.

## 1. Intro + Project Objectives (45–60s)

[Talking head or slide, no screen share yet]

"Hi, I'm Sushit, and this is RecoveryLoop — a governed decision agent for
autonomous financial recovery, built for the Razorpay AI Buildathon's Revenue
Recovery track.

The problem it solves: when a payment fails, most systems either do nothing and
lose the revenue, or they blindly retry every failure the same way — which either
annoys customers with premature retries, or misses cases that need a human
escalation instead of automation.

RecoveryLoop's objective is to make that recovery decision safely autonomous —
diagnose why a payment failed, decide the right intervention, but gate every
execution behind explicit authorization rules, and keep a full audit trail of
everything the system did and why. The goal isn't just 'recover more money' — it's
'recover money in a way that's auditable, governed, and honest about where it's
uncertain.'"

## 2. Architecture Walkthrough (45s)

[Screen share: architecture diagram or README]

"The pipeline is six stages: Detect → Diagnose → Decide → Gate → Execute → Audit. A
rule-based Diagnoser classifies the failure and its root cause. A DecisionEngine
picks an action — retry now, retry later, escalate to a human, or do nothing. A
CapabilityGate then checks whether that action is actually authorized right now —
things like quiet hours, promise-to-pay status, retry budgets. Only then does the
Executor act, and every step gets written to an append-only audit log."

## 3. Evaluation Honesty (45–60s)

[Screen share: terminal — run the eval harness live]

"Here's the evaluation, running live against our 60-case synthetic dataset."

```bash
python -c "from pathlib import Path; from recoveryloop.eval.harness import run_eval; print(run_eval(Path('data/synthetic_batch.json')).summary_text())"
```

"98.3% diagnosis accuracy, 78.6% recovery rate, ₹869,946 recovered out of ₹1.1
million at risk. But the number I actually want to highlight is this one: 30
documented divergences between the engine and the dataset's ground truth. We
didn't hide these — we categorized every one of them in `docs/DECISIONS.md`. 29
are deliberate engine-policy choices where we think the engine's call is actually
better than the ground truth's label. Only 1 is a genuine labeling artifact. That
honesty is deliberate — most recovery-agent demos show a cherry-picked success
case. We wanted to show the whole distribution, warts included."

## 4. Baseline Ablation (60s)

[Screen share: terminal — run harness_runner.py, then switch to Streamlit]

"To prove the governed decision engine is actually earning its complexity, we
built an ablation — comparing it against naive baselines through the exact same
eval harness."

```bash
python src/baselines/harness_runner.py      # 4-policy comparison table
streamlit run dashboard/app.py              # visual dashboard
```

"Always-retry, a naive amount-threshold rule, and diagnosis-only with no gating
at all. Here's the one number that tells the whole story."

[Switch to Streamlit dashboard, point at the bar charts]

"A simple 'only recover under ₹25,000' rule looks reasonable on paper — but it
collapses to a 2% recovery rate. Why? Because the recoverable revenue in this
dataset concentrates almost entirely in eight high-value cases above that
threshold. The naive rule forfeits ₹847,996 compared to the full engine, simply
by optimizing for the wrong variable. Its precision actually looks great — but
only because it rarely acts. That trade is the whole point: optimizing for the
wrong variable collapses recall to 44% and recovery to 2%. It's the exact kind of
failure a governed, multi-signal decision engine is designed to avoid."

## 5. Live End-to-End Run (60–75s)

[Screen share: terminal — run scripts/live_demo_run.py live]

"Everything so far has been a deterministic synthetic eval. So to prove this
actually works against the real world, here's one case running through the full
pipeline against Razorpay's live test-mode API — right now, no mocks."

```bash
python scripts/live_demo_run.py
```

"Diagnosed as insufficient funds, retryable. Decision: retry now — top-scored at
0.9. Gate: authorized. Execution: a real payment link just got created on
Razorpay's servers."

[Cut/jump to Gmail tab — email already arrived]

"And here's the email Razorpay actually sent — the payment-link reference in it
matches the case/link ID from the terminal, ₹3,499, sent to a real inbox."

[Back to terminal — audit read-back]

"And this is the audit record for that exact same run, pulled back from the
append-only log — same link ID, same case ID, tying the live call, the email, and
the persisted audit trail together as one verifiable chain."

"One honest caveat worth stating: our notifier reports 'delivered: True' when
Razorpay is instructed to auto-notify — not confirmed delivery. That distinction
is documented, because we didn't want to overclaim something we can't actually
verify."

## 6. Build Challenges & Technical Obstacles (60–75s)

[Talking head or slide]

"A few real obstacles worth mentioning.

First, evaluation correctness itself was harder than it looked. Early on, our
eval bugs made diagnosis accuracy read as 20% instead of the real ~98% — caused
by the dataset generator and the diagnoser using different root-cause strings. We
also found our recovery metric was conflating 'attempted execution' with 'actual
recovery' — creating a payment link isn't revenue recovered until the customer
actually pays. We fixed this by adding a seeded, independent ground-truth outcome
field, so attempted and recovered amounts are now measured distinctly.

Second — and this is the one I'm most glad we caught — our fully mocked test
suite was 88 out of 88 green, and we genuinely believed the Executor was correct.
But the moment we ran one real call against Razorpay's live test-mode API for
this demo, it got rejected: `notify:email must be boolean`. Our fake transport
layer had never validated against Razorpay's actual API contract — it turned out
`notify.email` needs to be a boolean flag, with the actual address passed
separately in a `customer` object. That's a real lesson: a green test suite
against a mock isn't the same as a system that works — we only caught this
because we insisted on one live, unmocked, end-to-end run instead of trusting
synthetic coverage alone. We fixed the contract, added regression tests for it,
and re-ran the live call successfully — which is the run you just watched.

Third, we deliberately scoped out an LLM-based diagnoser for this submission,
even though it was suggested to us. Rule-based diagnosis at 98.3% accuracy was
already strong, and introducing LLM nondeterminism this close to a deadline was
more risk than reward. The interface is schema-first specifically so an LLM
diagnoser can be a drop-in replacement later, without touching the rest of the
governed pipeline."

## 7. Close (20s)

"RecoveryLoop is complete, tested, documented, and now proven against a real
live API call. Next steps are the LLM-backed diagnoser as a drop-in replacement,
and a larger production-scale evaluation. Thanks for watching."