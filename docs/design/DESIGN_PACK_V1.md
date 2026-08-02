# V1 DESIGN PACK — BTC/ETH/SOL Raw-vs-TA Experiment

Status: **PROPOSAL — awaiting mentor audit. Not approved for implementation or launch.**
Branch: `v1-clean-experiment`. Canonical verbatim artifacts (same commit as this file):

- `prompts/v1/system.txt` — system prompt
- `prompts/v1/user_raw.txt` — Raw-arm user template
- `prompts/v1/user_ta.txt` — TA-arm user template
- `prompts/v1/blocks.md` — all sub-blocks + the only permitted failure/retry messages
- `schemas/v1/decision.schema.json` — decision tool schema
- `config/v1/experiment.json` — machine-readable experiment configuration

## A. Experiment question

Does giving an AI trader a fixed technical-indicator summary, in addition to the
same raw market data, change its trading behaviour and performance across BTC,
ETH, and SOL? Primary comparison: Raw vs TA within the same model and coin.

## B. Roster

Three independent single-coin arenas (BTC, ETH, SOL) × six fresh accounts
(haiku/sonnet/opus × raw/ta) = 18 accounts. IDs: `{coin}_{model}_{arm}`
(e.g. `btc_haiku_raw`). Every account: $10,000, zero history, coin-locked,
no shared capital. Within a coin all six get the same first snapshot, decision
timestamp, and execution price. All three arenas start at the same UTC boundary.

## C. Scope

BTC, ETH, SOL only; one position max per account; a model-arm may hold up to 3
positions via its 3 independent accounts; no portfolio selection or cross-coin
allocation. No additional coins during V1.

## D. Prompts

See canonical files listed above. Key properties: coin/instrument/quote/perp
declared; $10,000 independence declared; coin lock declared; no information
about twins or other coins; competitor references limited to "other independent
accounts trade in parallel; you receive no information about them".

## E. Input equality

Raw and TA differ ONLY in the `=== TECHNICAL INDICATORS ===` block of the user
prompt. Identical: coin, execution price, 72 hourly OHLCV candles, 40 daily
closes, 24h change, timestamps, source label, account block, trades block,
memory block, condition block, execution rules, fees, leverage, cadence, data
source, candle boundaries, snapshot, execution price, system prompt, schema,
model params. Removed everywhere: Fear & Greed, sentiment, news, social,
on-chain, macro. All indicators computed exclusively from the 72 hourly candles
and 40 daily closes supplied verbatim to the Raw twin. No SMA200 (would require
history the Raw arm does not see). Known non-equalized factor: prompt token
length (TA longer) — accepted per mentor ruling §12, recorded via usage logs.

## F. Indicator block

Set (identical for all coins), all library-free, deterministic, computed from
supplied candles only:

| Indicator | Params | TF | Needs | Formula |
|---|---|---|---|---|
| RSI | 14 | 1h | 15 | Wilder: first avg gain/loss = simple mean of first 14 diffs of the supplied series; then Wilder smoothing across remaining candles |
| MACD | 12,26,9 | 1h | 35 (72 supplied) | EMA(k)=2/(k+1); EMA seeded with the first close of the 72-candle window (window-seeded, disclosed); signal = EMA9 of MACD line over the window |
| Bollinger | 20, 2.0 | 1h | 20 | mid = SMA20 of closes; sd = population σ of last 20 closes; upper/lower = mid ± 2σ |
| SMA | 20 / 50 | 1h | 20/50 | arithmetic mean of last N closes |
| ATR | 14 | 1h | 15 | TR = max(h−l, |h−pc|, |l−pc|); first ATR = mean of first 14 TRs; then Wilder smoothing |
| Daily SMA | 20 | 1d | 20 | mean of last 20 supplied daily closes |
| Daily RSI | 14 | 1d | 15 | as hourly RSI over the 40 supplied daily closes |

Rounding in display: RSI 1 decimal; MACD 1 decimal signed; prices (BB/SMA/ATR)
= the coin's price precision (2 decimals). Missing-data behaviour: if the
snapshot has fewer candles than an indicator needs, that indicator renders
`n/a (insufficient history)`; the round still proceeds (both arms saw the same
short history). Warm-up: only the supplied window is ever used; EMAs/RSI/ATR
are window-seeded by definition above, so a Raw model with the same candles can
reproduce every number exactly. Displayed wording: exactly as in
`prompts/v1/user_ta.txt`.

## G. Decision schema

See `schemas/v1/decision.schema.json`. Position ∈ long/short/flat; size as
**USD notional** (recommended because it is explicit, unit-identical across
coins whose prices differ by 400x, directly comparable to the 5x-equity cap,
and avoids model-side unit conversion errors; %-of-equity and asset-quantity
were rejected for cross-coin comparability and arithmetic-error risk);
stop_loss/take_profit number|null; thesis string; `invalidation` structured
object (required non-null when holding, null when flat); `watch_condition`
structured object (optional when flat, null when holding). Semantic rules that
strict JSON cannot express are validated by the engine (see blocks.md failure
list) and enforced via the atomic-round retry policy.

## H. Invalidation semantics

- Types (V1): price-vs-level only. `{timeframe: 1h_close | 1m_intrabar, operator: price_below | price_above, level}`.
- Evaluation (engine, every tick): `1h_close` — compare each newly completed
  hourly candle close; `1m_intrabar` — compare each 1m candle low (price_below)
  or high (price_above). Comparisons are strict (< / >).
- First trigger: timestamp + trigger price recorded, **latched permanently** on
  the position-lifecycle record; original condition retained verbatim; later
  decisions can never modify the triggered record.
- Deadline: none in V1 (no expiry field) — a condition lives as long as its
  position lifecycle.
- After trigger: position is NOT force-closed. The next round's prompt shows the
  TRIGGERED block (see blocks.md). The engine records the trader's next action
  as `post_invalidation_action` ∈ closed / reduced / held / increased / reversed.
- Compliance metric: fraction of triggered invalidations followed by
  close-or-reduce in the next committed round.
- Malformed output: schema violations are impossible (strict tool use);
  semantic violations (wrong-side stop, out-of-range level, missing
  invalidation while positioned, watch+position simultaneously) → decision
  rejected → one validation retry with the fixed failure message → still
  invalid → round-level failure handling (§I).
- Modification while open: allowed pre-trigger, append-only — each version is
  stored with its set-timestamp; the version active at trigger time is the
  scored one; post-trigger modifications are recorded but do not overwrite the
  latched record.
- Lifecycle binding: a lifecycle starts at open or reversal and ends at
  close/flip/liquidation. Increases/reductions stay within the lifecycle
  (invalidation persists unless re-stated); a reversal ends the lifecycle and
  requires a fresh invalidation.

## I. Atomic round protocol

Per coin, at boundary T: (1) build market snapshot (all candles closed ≤ T,
execution price = last completed 1m close, single source, source logged);
(2) freeze all six account snapshots; (3) generate six prompts from frozen data
only; (4) call six models sequentially; (5) validate; (6) retries: transport →
up to 3 attempts (backoff 15s/30s), validation → 1 retry with fixed message;
(7) no execution until six valid decisions exist; (8) any missing → the whole
coin round ABORTED: no state change for that coin, all accounts keep prior
positions/stops, log `ROUND_ABORTED{coin, T, reason}`; (9) commit: all six
executed at the identical frozen price, state saved atomically, log
`ROUND_COMMITTED`. Fairness: prompts are pre-generated from the frozen
snapshot before any call, so call order and response latency cannot leak
information or change inputs; execution price is the frozen price regardless of
when each model answered. Coins are independent: SOL aborting does not touch
BTC/ETH. Round IDs: `v1-{COIN}-{ISO_BOUNDARY}` e.g. `v1-BTC-2026-08-05T12:00:00Z`.

## J. Execution specification

Identical across coins (disclosed simplification: same fee 0.05%, maintenance
2%, leverage 5x for BTC/ETH/SOL; real venues differ). Open: qty=notional/price
(negative short), fee=0.05%×notional. Increase/reduce: only if |Δnotional| >5%
of current; fee on Δ only; increase → weighted-average entry; reduce → realize
P&L on reduced part. Hold: no trade, stop/tp/invalidation may update
(pre-trigger, append-only). Close: P&L=qty×(price−entry) − fee. Reverse: close
then open, two fees, one frozen price. Stops/TP between rounds: 1m-candle
replay, chronological; stop priority within a candle; gap fills at candle open
when opened through the level. Liquidation: maintenance 2%; effective trigger =
worse of stop/liq; equity ≤0 → floored at 0, account closed permanently.
Leverage: clamp at decision time only; drift from mark-to-market is not
deleveraged (documented; liquidation is the backstop). Partial failures: only
at round level (§I) — there are no partial rounds. Restart recovery: state is
committed atomically per tick; a crashed run leaves the previous commit intact;
the next run resumes from it and replays 1m candles for exits. Duplicate
prevention: a round ID present in the committed round log can never execute
again; plus workflow concurrency group; plus heartbeat ordering check.

## K. Market-data specification

Symbols: Kraken XBTUSD/ETHUSD/SOLUSD (primary); Coinbase Exchange
BTC-USD/ETH-USD/SOL-USD (fallback). Timeframes: 1m (exit enforcement +
execution price), 1h (briefing + 1h_close invalidation), 1d (briefing). Candle
timestamps: UTC open-time; a candle is used only when fully closed (open_time +
duration ≤ now). Missing candles inside a window: rendered as absent rows; if
>10% of the hourly window is missing from the primary source, switch to
fallback; if both fail → that coin's round is not attempted (`ROUND_ABORTED`,
reason data_unavailable); exits for that coin pause until data returns (replay
covers the gap). Precision: prices 2 decimals (all three coins); quantities
BTC 6 / ETH 5 / SOL 3 decimals; volume in coin units, 1 decimal. Snapshot
construction: one source for the entire coin snapshot; source name recorded in
the round record and shown in the prompt header; mixed sources within one
committed round are forbidden. Frozen execution price: close of the last
completed 1-minute candle at snapshot build time, used for every fill in that
round.

## L. Scheduler design

Tick cron `*/5 * * * *` (GitHub Actions) + `workflow_dispatch` reserved for
soak-test failure injection only. Decision boundaries: hourly at :00 UTC. A
tick runs: heartbeat → exit replay per coin → if now ≥ next boundary and
boundary+grace(30 min) not exceeded and hourly candle for the boundary is
closed → run that boundary's three coin rounds (each atomic, independent).
Boundaries missed by >30 min are skipped permanently (`ROUND_SKIPPED`, logged —
stale-snapshot trading is forbidden). Heartbeat: `data-v1/heartbeat.json`
{ts, commit, last_boundary_processed} committed every tick. Stale detection:
dashboard banner + `status.json` flag when heartbeat age >15 min. Duplicate
prevention: committed-round registry + concurrency group. Max catch-up: 24 h of
1m replay; beyond → engine halts trading actions and raises the alert flag
(positions remain; manual review required). Alerts: red dashboard banner +
status flag; optional GitHub issue auto-open on failed run. One coin's data
unavailable → that coin aborts/pauses, others proceed. Soak acceptance (24 h,
before launch): ≥ 24 boundaries attempted with zero manual dispatches; zero
duplicate rounds; no unexplained heartbeat gap >15 min; all boundary outcomes ∈
{COMMITTED, ABORTED(reasoned), SKIPPED(reasoned)}; at least one injected
failure (config-forced bad data source for one coin for one boundary) produces:
that coin ABORTED, other coins COMMITTED, clean recovery next boundary. Soak
runs with model calls in mock mode (no API spend) but real scheduling, data,
and state paths.

## M. Reproducibility

Every decision record: round ID, coin, trader ID, model family, arm, account
ID, requested model ID, returned `response.model`, code commit SHA (from
workflow env), experiment-config hash, system-prompt hash, user-template hash,
full generated-prompt hash, market-snapshot hash, archived prompt path
(`data-v1/prompts/{round_id}/{account_id}.txt`, committed), token usage,
raw decision JSON, validation result, execution result, data source, frozen
price. Hashes = SHA-256 of canonical bytes.

## N. Metrics

(1) Per-account trading outcomes: final equity, realized/unrealized P&L, total
return %, max drawdown (on tick equity series), fees, trade count, exposure
(fraction of time in position), liquidation flag, turnover ($ traded/equity),
mean holding duration. (2) Per-account behaviour: long/short/flat frequency,
size distribution, effective leverage at open, stop/TP usage rate, invalidation
trigger rate, post-invalidation action distribution, malformed-decision count,
aborted-round count, thesis-consistency notes. (3) Paired Raw-vs-TA (same
model, same coin, same round): direction agreement %, direction conflicts
(long-vs-short), |Δsize|, Δstop-distance, Δtarget-distance, invalidation
parameter differences, Δturnover, ΔP&L, Δdrawdown, Δfees. (4) Reporting: BTC,
ETH, SOL separately first; any aggregate preserves (model, coin) pairing;
no cross-coin blending that lets one trending coin mask the others; no ranking
from single-model/single-coin/small-N results.

## O. Test plan

Offline, hermetic, fixture-driven (`tests/fixtures/{btc,eth,sol}_{1m,1h,1d}.json`
+ canned model responses). Files: test_init.py, test_isolation.py,
test_prompts.py, test_equality.py, test_indicators.py, test_decisions.py,
test_round_atomicity.py, test_execution.py, test_exits.py,
test_liquidation.py, test_invalidation.py, test_persistence.py,
test_scheduler.py, test_market_data.py, test_dashboard.py. Case list in the
design-pack chat deliverable §O (mirrored in tests/README.md at implementation
time). No live network calls in any test.

## P. Dashboard

Single static page, four views (JS tabs, no external assets): per-coin view
(6 accounts, paired Raw/TA tiles + equity chart), pairs view (Raw-vs-TA deltas
per model per coin), model view (one model across three coins), status view
(heartbeat age, per-coin last round result, committed/aborted/skipped counts,
data-source in use, alerts). Global status strip on every view. No single
mixed-coin leaderboard without context.

## Q. Cost projection

18 calls/boundary, hourly → 432 calls/day (+retry allowance 10%). Token
estimates: Raw input ≈ 2,400 (incl. tool schema), TA input ≈ 2,700, output ≈
350. Per-day ≈ $5.60 base, ≈ $6.20 with retries; 7d ≈ $43; 14d ≈ $87; 30d ≈
$186. Range $4–8.5/day (terse vs verbose outputs, intro Sonnet pricing,
retry rate). Pilot (6-account BTC hourly) was ≈ $1.9/day → V1 ≈ 3.2x. Full
per-model table in the chat deliverable §Q.

## R. Unresolved decisions

See chat deliverable — mirrored at the end of this document's audit response.
