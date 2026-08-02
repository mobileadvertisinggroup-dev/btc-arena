# V1.1 DESIGN PACK — revision under Mentor Ruling 002

Status: **PROPOSAL — awaiting mentor audit. Not approved for implementation.**
Supersedes `DESIGN_PACK_V1.md` (retained for history). Canonical artifacts at
this commit: `prompts/v1/{system.txt,user_raw.txt,user_ta.txt,blocks.md}`,
`schemas/v1/decision.schema.json`, `config/v1/experiment.json`.

Unchanged from V1.0 (per ruling, "keep"): three independent arenas; 18
isolated accounts; Raw vs Raw+TA; no sentiment/outside data; equal visible
candle history (72h/40d both arms); versioned prompts/schema; hermetic tests;
paired reporting; no cross-coin leaderboard; pre-launch config hashes;
indicator set and window-seeded formulas (§F of V1.0); USD-notional sizing;
dashboard views; reproducibility record fields.

## D. Pair-atomic round protocol (ruling 1)

Atomic unit: matched Raw/TA pair (same model, same coin) — 9 independent
pairs. Per coin boundary T: one frozen snapshot; all six prompts generated
from it before any API call; decisions collected independently; each pair
commits or aborts together (PAIR_COMMITTED / PAIR_ABORTED). A failed
Haiku decision aborts only the Haiku pair; Sonnet/Opus pairs proceed. A Raw
account never trades in a round its TA twin missed, and vice versa.

## E. Immutable invalidation lifecycle (rulings 2–3)

Lifecycle = open-from-flat or reversal → close/reversal. Invalidation set
exactly once at lifecycle creation; immutable through hold/increase/reduce/
partial exits; no pre-trigger versioning; thesis notes never alter it.
Operators inclusive: `price_at_or_below` / `price_at_or_above`. Metrics:
continuous (%-reduction of invalidated-direction exposure, closed?, reversed?,
pair-rounds-to-exit, hours-to-exit) + primary binary discipline = no longer
exposed in the invalidated direction by end of next committed pair-round
(stop/liquidation/reversal/full-close before that boundary qualifies); partial
reductions descriptive only.

## F. Canonical-source data policy (rulings 4–7)

Kraken spot OHLC is the sole committed source; instrument renamed to
"synthetic USD-settled perpetual paper contract, marked and executed using
Kraken {COIN}/USD spot-market OHLC data" with full simulation disclosures in
the system prompt. Coinbase: health/alerting/fixtures only. Kraken problem =>
PAIR_ABORTED + DATA_UNAVAILABLE; no venue substitution. Completeness: exactly
72 contiguous closed hourly + 40 contiguous daily + contiguous 1m for replay;
zero-tolerance for silent omission; empty intervals filled by deterministic
zero-volume flat candles (Option B, pending approval; end-of-series gaps are
staleness => abort). 1m auto-catch-up capped at 10 h (Kraken 720-candle
window); beyond => halt coin, preserve state, NEEDS_REVIEW.

## G. Latency replay protocol (ruling 8)

Round = virtual event at boundary T: replay 1m→T; freeze accounts at T;
freeze snapshot and P_T; generate all six prompts; collect/validate; apply
committed pairs logically at T using P_T; immediately replay 1m candles after
T through the latest completed candle at workflow end (stops/targets/
liquidation/invalidation evaluated in-order); publish. Wall-clock model
latency is excluded from simulated market time.

## H. Explicit model request payloads (rulings 12–13)

See `config/v1/experiment.json → request_payloads`. All three model IDs are
fixed snapshots (haiku dated; sonnet-5 and opus-4-8 fixed dateless snapshots —
the V1.0 "alias drift" risk was incorrect and is withdrawn). Thinking:
explicitly disabled on sonnet-5 and opus-4-8; omitted (off) on haiku.
temperature/top_p/top_k never sent. One forced strict tool with
disable_parallel_tool_use. max_tokens 1500. Raw/TA twins byte-identical
except the user prompt. requested model + response.model both logged.

## I. Scheduler & persistence architecture (rulings 17–18)

Heartbeat cron `2-57/5 * * * *` (off the top of the hour); simulated boundary
stays :00 UTC; grace 15 min; unprocessed boundary => ROUND_SKIPPED, never
retrospective. Code/runtime separation: `main` pinned at the approved launch
tag (code, prompts, schema, config); runtime writes go only to branch
`v1-runtime` (data-v1/, prompt archive, heartbeat, docs/ for Pages); every run
verifies SHA-256 of code+prompts+schema+config against the launch manifest and
halts on mismatch; recorded code hash = approved launch commit; Pages deploys
from the runtime branch and cannot modify frozen source.

## Execution amendments (rulings 9–11)

No 5% churn guard: target notional executes exactly when |Δ| ≥ $10 (below =>
NO_EXECUTION_BELOW_MINIMUM, logged); >5x equity => TARGET_EXCEEDS_MAX_LEVERAGE
validation failure (one retry, then pair abort) — never silently clamped.
Cross-field rules per ruling 10 (full list in blocks.md failure strings +
tests). Decimal arithmetic everywhere (equity/P&L/fees/notional/qty/prices);
quantity rounds DOWN to per-coin precision; display rounding cosmetic only;
0.05% fee on every exit including stop, target, liquidation, close, and both
reversal legs. Null stop/target = REMOVE (stated in schema, system prompt, and
tested).

## J–K. Token measurement & cost methodology (ruling 14)

No generic-token cost claims. During preflight (real API, non-executing):
`count_tokens` per model × {first-round raw, first-round TA, mature raw,
mature TA} + measured usage from the 18 preflight calls. Cost projection is
then rebuilt from measured numbers into a hard daily estimate + run-wide
range. V1.0's figures are downgraded to "provisional order-of-magnitude"
(~$4–8.5/day) and excluded from any decision-making. No automatic
experiment-rule changes based on spend.

## Fixed run contract (rulings 15–16)

Hourly cadence; exactly 14 days = 336 boundaries from a predeclared UTC start
boundary after all launch gates pass; no result-driven changes of any kind.
Primary outcomes are behavioural (direction disagreement; |Δ target size| as %
equity; turnover Δ; stop/target usage Δ; invalidation-response Δ). Performance
outcomes (paired equity/return/drawdown/fees, liquidations) are secondary and
descriptive. V1 is never described as proving indicators improve
profitability.

## Preflight & soak (ruling 19)

Gate 1: 24 h mock-model scheduler soak (real scheduling/data/state; criteria
as V1.0 §L incl. injected failure). Gate 2: real-API non-executing preflight —
production prompts/schemas, all 18 accounts, fixed archived snapshot, zero
account mutation, zero simulated orders; verify forced tool + schema +
semantic parsing on all 18; record latency + token usage. Gate 3: mentor
audit of soak+preflight results. Only then account initialization.

## L. Test matrix additions (ruling 20)

All V1.0 tests retained, plus: pair-level atomicity; one failed pair while
others commit; immutable lifecycle invalidation (replacement rejected);
tiny-reduction ≠ compliance; full 1m replay across simulated API latency;
exactly-72-contiguous-hourly enforcement; zero-volume synthetic-candle rule;
mixed-venue rejection; canonical-source-unavailable abort; 10 h catch-up
boundary; no-silent-leverage-clamp; $10 minimum delta; Decimal accounting and
round-down quantity; fees on stop/target/liquidation exits; code/config hash
mismatch halt; runtime-branch isolation (no writes to code branch); exact
request-payload snapshot tests; preflight parser against saved real responses.

## M. Change log V1.0 → V1.1

1 atomicity: coin-level (6 accounts) → pair-level (9 Raw/TA pairs). 2
invalidation: pre-trigger append-only versioning REMOVED → immutable per
lifecycle; operators → inclusive at_or_below/at_or_above. 3 compliance:
any-reduction → binary "no longer exposed by next committed pair-round" +
continuous descriptives. 4 instrument wording → synthetic perp marked to
Kraken spot, full disclosures. 5 Coinbase demoted from fallback → monitoring/
fixtures only. 6 10% missing-candle tolerance REMOVED → exact contiguity +
Option-B flat-candle rule (pending approval). 7 24 h replay cap → 10 h +
NEEDS_REVIEW. 8 virtual-event-time protocol added (latency excluded). 9 5%
churn guard REMOVED → exact targets, $10 min delta, leverage violation =
validation failure not clamp. 10 cross-field rule list expanded + null-removes
made explicit in prompt. 11 float accounting → Decimal; fees confirmed on all
exit types; round-down quantities. 12 model-ID "alias drift" claim corrected.
13 full per-model request payloads specified; disable_parallel_tool_use added.
14 cost projection demoted to provisional; measured-token methodology added.
15 outcome hierarchy inverted: behaviour primary, performance descriptive. 16
fixed 14-day/336-boundary run contract. 17 heartbeat off-minute-zero cron;
grace 30→15 min. 18 code/runtime branch separation + hash-verification halt.
19 real-API preflight gate added. 20 test matrix expanded accordingly.

## N. Unresolved decisions

Mirrored in the audit response; headline items: Option-B flat-candle approval;
start-boundary date; sequential vs concurrent in-round calls; consecutive-
abort escalation threshold; public-vs-private dashboard during the run;
Sonnet intro-pricing straddle; archive commit location; NEEDS_REVIEW
resolution procedure; preflight spend authorization; pilot freeze still not
executed (workflow active, owner action pending).
