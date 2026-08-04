# ARCHITECTURE

## Experiment design (frozen through Mentor Rulings 001–013)
Question: does adding a fixed neutral numerical feature summary (the
"Feature"/TA arm) to identical raw market data change an AI trader's
behaviour/performance across BTC, ETH, SOL?
- 18 isolated paper accounts: 3 coins x 3 models (haiku/sonnet/opus) x 2 arms
  (raw / ta), each starting at exactly $10,000 (Decimal accounting).
- Hourly decision boundaries; matched Raw/TA twins are PAIR-ATOMIC: both
  commit at the same frozen price P_T or the pair aborts.
- Kraken is canonical market data. Coinbase spot on the site is display-only.
- Decisions via forced tool_choice against a strict JSON schema; semantics
  A–F with an exact $10 executable delta; machine-checked invalidation
  lifecycles; 5x max leverage; fee 0.0005/execution.

## Engine (engine/, all offline, 258 hermetic tests, ~95% branch coverage)
- config.py       — frozen config; TWO manifests: engine (canonical files +
                    all engine/scripts .py) and site (docs/index.html +
                    prestart/demo payloads). check_approved_digest /
                    provision_store: stores can only be provisioned when the
                    CURRENT tree matches EXTERNALLY supplied approved digests.
- state.py        — accounts, equity_at, params verified against config.
- marketdata.py   — snapshot building + strict 1m coverage validation.
- prompts.py      — renders system/user prompts (raw vs feature separation).
- decisions.py    — schema then semantic validation.
- rounds.py       — wave order, collect_one (4 transport attempts, budget
                    checks), _commit_account.
- execution.py / replay.py — order application; post-boundary 1m replay with
  stops/TP/invalidation/liquidation; 10h gap => coin termination.
- persistence.py  — atomic checksummed state (checksum REQUIRED, full
                    18-roster enforced, internal-id validation), durable
                    prompt/attempt archives, transactional outbox to
                    ledger.jsonl.
- recovery.py     — run_checkpointed(): THE production coordinator. Verifies
                    approved manifests BEFORE anything; crash-safe
                    checkpoints; hard ABSOLUTE deadline (pass deadline=T+720
                    anchored at the scheduled boundary — pre-request delays
                    consume the same budget; results at/after T+720 never
                    execute). recover(): frozen rule = non-finalized pairs
                    abort as crash_recovery.
- pilot.py        — provision (dual-digest gated), persisted schedule,
                    run_pilot loop: recover -> wait -> THINKING publish
                    (HARD GATE: not publicly verified => zero model calls,
                    halt for publication-only retry) -> fetch -> coordinator
                    with absolute deadline -> mark complete -> publish
                    ROUND_COMMITTED. Restarts resume the SAME schedule.
- publisher.py    — READY/THINKING/ROUND_COMMITTED lifecycle payloads built
                    ONLY from persisted state + persisted boundary marks
                    (never cash-as-price; explicit nulls when no mark);
                    durable payload files; publications.json log
                    (PUBLISHED/FAILED/SUPERSEDED); reconcile() =
                    publication-only retry; verifies the REMOTE payload
                    (publication_id) before recording PUBLISHED; site
                    manifest integrity checked before every publish.
- dashboard.py    — payload model incl. per-account equity series ($10k
                    start + one point per boundary at persisted marks).
- metrics.py      — outcomes + reliability metrics.

## Production scripts (scripts/, FROZEN — part of engine digest)
- run_pilot_12h.py     — guarded activation (env approvals + both digests +
                         deploy token + dulwich preflight); real Kraken
                         fetcher; real Anthropic caller; git_publish pushes
                         docs/live_payload.js then polls the PUBLIC Pages URL
                         (cache-busted) until it serves the exact
                         publication_id.
- archive_pilot_reset.py — dual-digest gated; archives pilot to
                         docs/pilot-12h-archive/, provisions pristine
                         data-v1 official accounts.
- mock_season.py / gen_demo_dashboard.py — offline evidence/demo tooling.

## Stores
- data-pilot-12h/ — completed pilot (also copied into the archive).
- data-v1/        — pristine official store (untouched until activation).
- Each store: state.json (checksummed), ledger.jsonl, prompts/, attempts/,
  publish/, publications.json, pilot_schedule.json, launch_manifest.json,
  site_manifest.json.

## Website (docs/, GitHub Pages from v1-clean-experiment:/docs)
- index.html reads window.ARENA_LIVE (live_payload.js, published only by an
  active run) -> ARENA_PRESTART -> ARENA_DEMO (?demo=1). Auto-polls
  live_payload.js every 60s with cache-busting. Explicit PREP/DEMO/PILOT
  rendering; null market values render "MARK N/A", never 0.
- tests/frontend_harness.js runs the page's real scripts under Node with a
  DOM stub and asserts rendered text per panel.
