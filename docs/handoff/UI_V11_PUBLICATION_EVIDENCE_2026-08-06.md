# UI v11 PUBLICATION EVIDENCE (2026-08-06)

Owner-approved UI-only revision (branch ui-revision-v11, commit 1a4856f5)
published to GitHub Pages. The three payload-unavailable fields (completed-
trade holding time, target-at-entry, stop-at-entry) remain omitted by owner
decision; no engine or payload-schema change was made during the active
experiment.

## Digests
- ENGINE digest verified EXACT before and after publication:
  4336a0f83eb23071d8b19b58e3ee7ee120741ff9b6402d35e48e54215d5b68e8
- NEW SITE digest (published):
  00bdd9d1327aa4abcae7bde8daeb5e45ab791e0b5d1abde580a8bfaca5830b8b
- The PUBLIC GitHub Pages bytes (index.html + prestart_payload.js +
  demo_payload.js, cache-busted fetches) reproduce the new SITE digest
  EXACTLY.

## Publication mechanics
- gh browser device-flow auth by the owner (no token in chat); push of
  v1-clean-experiment fast-forwarded to 1a4856f5; the credential-bearing
  push output was SUPPRESSED this time (no token echo); gh logged out
  immediately after; owner advised to revoke the OAuth grant.

## Experiment untouched (verified before + after)
- Service: PID 23679, NRestarts=1 (the pre-activation credential halt from
  2026-08-05 11:03 — nothing since), never restarted.
- Store: schedule total 336, T0 1785931200 unchanged; boundary count
  advanced NATURALLY during the work (10/336 pre-publication ->
  11/336 post-publication, latest_terminal 1785967200); ledger 303 entries,
  all organic.
- No change to prompts, models, rules, balances, positions, schedule, VPS
  runner, or official data.

## Public page tests
- Desktop Chrome (1280px): live banner shows the truthful official status
  with progress; sticky MODEL/MARKET | RAW ARM | TA ARM header pins below
  the nav while scrolling the RAW vs TA table.
- iPhone 14 Safari viewport (390px): 0 px horizontal overflow (measured
  programmatically on the PUBLIC page); stacked RAW/TA cards with permanent
  arm labels; readable position/trade cards.
- Screenshots archived with the owner (pub_d1/d2, pub_m1/m2).
