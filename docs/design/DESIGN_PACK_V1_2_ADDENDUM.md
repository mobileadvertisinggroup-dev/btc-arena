# DESIGN PACK V1.2 — FINAL ADDENDUM (Mentor Ruling 003)

Status: V1.1 stands as the main experiment contract; this addendum amends it.
Trading-engine implementation remains unauthorized until V1.2 passes audit.

1. **CHG_24H removed** from both user templates (and it never existed in
   config). Raw arm receives raw candles, execution price, volume, and account
   state only; no derived market feature outside the approved TA block. New
   test: derived-feature scan of the rendered Raw prompt.
2. **Missing-candle policy: strict (Option B rejected).** Exactly 72 real
   contiguous closed hourly + 40 real contiguous daily + complete real 1m
   replay coverage, all Kraken. Any missing interval invalidates the affected
   snapshot/replay → PAIR_ABORTED + DATA_UNAVAILABLE; resting exit rules
   operate only where complete 1m data exists; no interpolation, forward-fill,
   fabrication, or substitution.
3. **Concurrent collection.** All 3 market + 18 account snapshots frozen and
   all 18 first-attempt prompts generated & archived before any request; then
   bounded concurrency (max 6 simultaneous requests), one global collection
   deadline T+12 min (retries inside it); frozen prompts/state/P_T invariant
   to response order; per-pair commit/abort; committed decisions applied at T
   after collection closes; one common post-T replay per coin. Preflight
   verifies 6-way concurrency vs rate limits; the final limit is locked at
   launch.
4. **PAIR_ABORTED semantics clarified:** zero *decision-generated* state
   change at T (no discretionary execution, no thesis-memory update, no
   replacement of stop/target/lifecycle invalidation); all attempts still
   archived; post-T replay and all market-driven exits continue for both
   accounts — monitoring is never suspended by an aborted or skipped round.
5. **PAIR_TERMINAL_SPLIT:** a permanently-closed (zero-equity) account gets no
   further model calls; its twin continues normally; post-terminal rounds are
   excluded from paired behavioural metrics; both accounts remain in
   performance reporting; terminal timestamp and cause reported prominently.
6. **Deterministic intrabar event ordering** (config `event_ordering`):
   opening-gap sequence liquidation→stop→take-profit at the open; intracandle
   protective exit = higher (long) / lower (short) valid level of
   stop/liquidation; protective-vs-target ambiguity resolves protective-first
   with AMBIGUOUS_CANDLE_PROTECTIVE_FIRST; 1m_intrabar invalidation latches in
   any candle where the lifecycle existed at open (SAME_CANDLE_INVALIDATION_
   AND_EXIT recorded when applicable; such exits count as no-longer-exposed);
   1h_close invalidation only at completed closes and never after lifecycle
   end; ended lifecycles can never trigger later.
7. **Liquidation contract** (config `liquidation_contract`): E = realized cash
   equity (Decimal, excludes unrealized); u(p)=q·(p−entry); liquidate when
   E+u(p) ≤ 0.02·|q|·p; L_long=(q·entry−E)/((1−0.02)·q), L_short=(q·entry−E)/
   ((1+0.02)·q) with executed rounded-down q; gap-through fills at the open;
   0.05% fee on the liquidation fill; E′=E+q·(fill−entry)−fee; positive
   residual equity closes the position but the account continues; permanent
   closure only at E′ ≤ 0 (floored to 0).
8. **Validation-attempt archive:** every attempt (not just the accepted one)
   recorded at `data-v1/attempts/{round_id}/{account_id}_attempt{n}.json`
   with the 17 fields in config `attempt_archive.fields`.
9. **Retry metrics** as a first-class behavioural outcome: first-attempt
   validity, validation-retry, successful-correction, transport-retry, and
   pair-abort rates, with the causing arm identified; the executed corrected
   decision is the operative decision.
10. **Primary-metric denominators locked** (config `metrics.denominators`);
    aborted/skipped/pre-launch/post-terminal rounds excluded from paired
    denominators and reported separately; no p-values or significance claims
    in V1.
11. **Halt & recovery policy:** normal events never halt (validation failures,
    pair aborts, transient transport, one missed boundary, recovered Kraken
    blips). Integrity halts: (A) hash mismatch → permanent experiment halt;
    (B) state corruption / non-atomic persistence → permanent halt; (C) >10 h
    unrecoverable 1m history → that coin's arena terminated for the run,
    others continue; (D) terminal account → PAIR_TERMINAL_SPLIT, no halt.
    No discretionary mid-run clearing; the earlier 3-consecutive-abort
    NEEDS_REVIEW trigger is withdrawn.
12. **Dashboard public from the first committed boundary**, carrying the V1
    label, frozen question, code/config/prompt hashes, scheduler/data health,
    round statuses, and a clearly-labeled link to the archived pilot page
    (which keeps its PILOT / SYSTEM TEST / NOT VALID EXPERIMENTAL EVIDENCE
    banner).
13. **Start contract:** hourly × exactly 336 boundaries × 14 days; T0 = first
    suitable :00 UTC boundary ≥ 24 h after final mentor launch approval,
    which requires offline tests + 24 h mock soak + production-payload
    preflight to have passed.
14. **Machine validation** of canonical files (duplicate-key-rejecting JSON
    parse, placeholder manifest check, Raw/TA byte-identity minus the TA
    block, forbidden-pattern scan) is part of the deliverable and will run in
    CI thereafter.

## Amended/added test cases (ruling-mapped)

- R1: rendered Raw prompt contains no derived market feature (scan for
  precomputed returns/percent-change strings); TA prompt derived values appear
  only inside the TA block.
- R2: hourly window with any gap → snapshot invalid → PAIR_ABORTED
  DATA_UNAVAILABLE; 1m replay gap → exits deferred until complete data; no
  synthetic candle path exists (code scan + behavioural test).
- R3: 18 first-attempt prompts archived before first request (order
  assertion); concurrency ≤ 6 (semaphore test); collection deadline aborts
  in-flight pairs at T+12; response order permutation leaves prompts/P_T/
  outcomes invariant.
- R4: aborted pair — thesis memory unchanged, resting levels unchanged,
  attempts archived, post-T replay still executes exits for both twins.
- R5: terminal account skipped in call plan; twin still called; paired
  metrics exclude post-terminal rounds; PAIR_TERMINAL_SPLIT recorded.
- R6: fixture matrix covering every ordering combination: gap-liq / gap-stop /
  gap-tp at open; stop-vs-liq higher/lower selection both sides; protective+
  target same candle → protective first + AMBIGUOUS record; intrabar
  invalidation same-candle-as-exit → latch + SAME_CANDLE record + counts as
  no-longer-exposed; 1h_close not triggered when lifecycle ended intra-hour;
  post-lifecycle candles never trigger.
- R7: Decimal liquidation formulas exact on fixtures (long & short); rounded-
  down q affects threshold; gap-through-liquidation at open; liquidation fee
  charged; positive-residual liquidation keeps account alive; zero-equity
  closure permanent.
- R8/R9: every attempt archived with all 17 fields; invalid first attempt
  retained; retry metrics computed from the archive.
- R10: denominator rules computed on a scripted fixture season, exclusions
  honoured.
- R11: hash mismatch halts everything; simulated state corruption halts;
  >10 h gap terminates only that coin; terminal account does not halt.
- R14: JSON duplicate-key rejection; placeholder manifest equality;
  byte-identity of templates minus TA block; forbidden-pattern scans wired
  into CI.
