# UI v12 CONSOLIDATED PUBLICATION EVIDENCE (2026-08-06)

Owner-approved consolidated UI revision (branch ui-wording-v12, commit
adabe8b6) published to GitHub Pages. Static UI files only.

## Approved contents (all owner-directed, 2026-08-06)
- Top-level scoreboard: WHO IS WINNING? (rank by combined equity,
  gold/silver/bronze), LEADER NOW vs CLOSED-PROFIT LEADER, RAW vs TA —
  WHO IS AHEAD?, WHAT IS HAPPENING NOW?
- Beginner-friendly trade-closing wording: TRADE RESULT (colored) separated
  from neutral HOW THE TRADE CLOSED badge + owner's exact explanations
  (grounded by the eth_opus_raw-L2 forensic check — classification B,
  engine reason code correct, record untouched).
- Compact RAW vs TA cells: OPEN TRADES summary (count + direction +
  open P/L only); CLOSED TRADES stats kept.
- Header cleanup: subtitle, About button/section, duplicate status strip
  removed; live banner + notice = single status summary.

## Owner-accepted limitations
- Max drawdown omitted (payload holds hourly boundary closes only —
  not exactly computable).
- Closed-profit leader = payload realized_pnl (cash P/L after all fees
  paid so far, excluding open-position P/L).
- Displayed RAW-vs-TA difference may differ by $0.01 from subtracting the
  individually rounded displayed values.

## Digests
- ENGINE digest verified EXACT before and after publication:
  4336a0f83eb23071d8b19b58e3ee7ee120741ff9b6402d35e48e54215d5b68e8
- NEW SITE digest (published):
  5a058c9237da547b646c96f1a85b76192bd0028c2762af2df6eae5b44902941c
- PUBLIC GitHub Pages bytes (index.html, prestart_payload.js,
  demo_payload.js; cache-busted fetches after Pages build of adabe8b6)
  each hash-match the SITE manifest EXACTLY.

## Publication mechanics
- gh browser device-flow auth by the owner (no token in chat); dulwich
  push of v1-clean-experiment fast-forwarded 1a4856f5 -> adabe8b6 with
  credential output suppressed; Pages build polled to "built adabe8b6";
  gh logged out immediately after; owner advised to revoke the OAuth grant.

## Experiment untouched (verified before + after publication)
- Service: PID 23679, NRestarts=1 (the pre-activation credential halt
  only), never restarted.
- Store: sealed schedule T0 1785931200, 336 boundaries, final 1787137200
  unchanged; 18 accounts; ledger 501 organic entries; boundary count
  advanced NATURALLY during the work (12 -> 18/336); state WAITING.
- No change to engine, payload schema, prompts, models, rules, accounts,
  balances, schedule, VPS runner, or official data. 483 hermetic tests
  pass (scoreboard DOM contract test added).

## Live public page tests (post-publication, PUBLIC URL)
- Desktop 1280px: OFFICIAL banner; scoreboard renders real payload values
  ("Haiku 4.5 is currently leading." — combined equities 60,603.86 /
  60,092.25 / 59,686.71 matching an independent Python computation from
  the live payload); subtitle/About/status-strip absent.
- iPhone 14 Safari viewport: 0 px horizontal overflow; stacked
  gold/silver/bronze cards; profitable protective-stop card shows the
  neutral badge + "closed automatically with a profit" wording live.
- Screenshots: pubv12_d1_scoreboard.png, pubv12_m1_top.png,
  pubv12_m2_trade_wording.png (archived with the owner).
