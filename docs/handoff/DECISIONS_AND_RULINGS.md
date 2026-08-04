# DECISIONS AND MENTOR RULINGS (governance history)

Roles: Ziad = owner/final authority. ChatGPT = independent auditor issuing
numbered "Mentor Rulings". Claude = implementation engineer. The original
casual BTC-only pilot (separate repo btc-arena) was formally frozen as
"PILOT / SYSTEM TEST / NOT VALID EXPERIMENTAL EVIDENCE".

Every ruling below found REAL defects; each was fixed with regression tests
before the next round. Summary of what each round established:

- 001–005: clean-room V1 design; 18-account Raw-vs-Feature structure; Kraken
  canonical; Decimal accounting; pair-atomicity; config-authoritative
  parameters; deterministic 72-boundary mock season (twice-identical hashes);
  revoke/never reuse the old PAT.
- 006–007: schema validation actually enforced before semantics; exact $10
  executable-delta boundary semantics (long/short) proven.
- 008: checkpointed crash-safe coordinator: durable prompt archives BEFORE
  any request; 4 total transport attempts; bounded concurrency (6); hard
  monotonic deadline w/ budget checks; transactional outbox (exactly-once
  ledger publication); replay gap policy (CATCHUP_REQUIRED, 10h =>
  COIN_TERMINATED).
- 009: launch-manifest integrity gate actually WIRED into the coordinator
  (Halt A before any request); strict state loading (checksum REQUIRED,
  full roster).
- 010: tree cannot approve itself — activation requires an EXTERNALLY issued
  approved digest; internal account-id validation; publisher implemented
  in audited source (exactly-once per boundary, retry = publication only);
  pilot restart/recovery wired (same persisted schedule, never new
  boundaries).
- 011: dashboard files in the audit archive + separate approved SITE digest;
  persisted boundary marks + durable equity history (cash is NEVER a price;
  explicit nulls); READY/THINKING/ROUND_COMMITTED lifecycle publication;
  production publisher verifies the PUBLIC URL, not the local file;
  browser auto-poll; Node front-end contract harness.
- 012: live PREP/DEMO/PILOT rendering with genuine ledger pair status (no
  demo placeholders in live mode); THINKING publication is a HARD pre-model
  gate; collection deadline anchored ABSOLUTELY at scheduled boundary T
  (publication + fetch consume the same T..T+12min budget; restart never
  resets it); rendezvous-deterministic deadline tests (719/720/721s).
- 013: ENGINE FORMALLY PASSED. Final static-UI patch: null market values
  render MARK N/A everywhere (open positions, pair gaps, leaderboard  —
  unranked but visible), never coerced to zero.
- FINAL RULING (2026-08-03): FORMAL PASS for preflight + 12h pilot. 24h mock
  soak waived. Two-phase authorization: 18-request preflight, then automatic
  pilot start at next full hour. Executed same day (see PILOT_RESULTS.md).

Standing owner decisions:
- Official 14-day experiment must run on an always-on cloud server (VPS),
  never depending on the owner's Mac (2026-08-03).
- UI changes batched with the cloud runner into ONE audit round
  (UI_WISHLIST.md, 12 items, recorded 2026-08-03).
- Credentials are granted per purpose with explicit scope; the engineer must
  not exceed the stated scope and must never persist secret values.
