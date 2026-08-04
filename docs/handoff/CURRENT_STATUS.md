# CURRENT STATUS — frozen facts as of 2026-08-04

## Formal standing
- Offline audit: **PASSED** (Mentor Final Ruling, 2026-08-03) for engine +
  static site. Engine tree frozen at the approved digest.
- 18-request real-model preflight: **PASSED 18/18** (schema, identity,
  raw/feature separation, durable archives, zero transport failures;
  report: evidence/preflight_report_1785740400.json).
- 12-hour visible paper pilot: **COMPLETE** (2026-08-03, 11:00–22:00 Asia/
  Beirut) and **ARCHIVED**. See PILOT_RESULTS.md.
- Official 14-day experiment: **OFF. Not authorized. Not started.**
- No runner process remains active anywhere. Nothing is scheduled.

## Approved digests (activation gates; also in AUDIT_AND_DIGESTS.md)
- Engine combined manifest:
  c425200e1b840524bb444288d6725bc1f728f60d94d72ed725d0fc704ec5432c
- Static site manifest:
  2bfff2cb28b970c09a76b5a9045c4ffee909d37ccd8f39ab69001a95c264b7fa
- Any change to engine/scripts/config/prompts/schemas or the three static
  site files invalidates these and requires a new mentor audit round.

## Pilot archive (immutable evidence)
- Folder: docs/pilot-12h-archive/  (includes data/, website-snapshot/,
  final-evidence/ with FINAL_REPORT.md, both aborted-round packages,
  sanitized runner log, midpoint pack, preflight report, approved digests)
- Sealed tarball: ~/trading-research-machine/pilot-12h-archive-final.tar.gz
- Tarball SHA-256:
  8d80b7cf7b9002aa03b847d8fdc47728c8e5794c8de3f93765e2292662dd823d
- Manifest: 432 entries, 432/432 verified, secret-scan clean.

## Official store
- data-v1/: 18 pristine accounts at exactly $10,000.00 each, zero positions/
  trades/theses/decisions; only state.json + the two approved manifests.
  Provisioned 2026-08-03 via the digest-gated path. DO NOT TOUCH until the
  official activation gate opens.

## Public site
- https://mobileadvertisinggroup-dev.github.io/btc-arena/ — currently shows
  the completed pilot's final payload (12/12). Repo:
  mobileadvertisinggroup-dev/btc-arena, branch v1-clean-experiment (Pages
  serves /docs). The pilot archive folder exists locally but has not been
  pushed; the last pushed commits are the pilot's live-payload updates.

## Credentials (locations only — values never stored here)
- ANTHROPIC_API_KEY: env / ~/trading-research-machine/btc-arena/.env
- Deploy token: owner issues per purpose with an explicit scope statement;
  the pilot token's scope was btc-arena repo lifecycle publishing +
  archiving the pilot dashboard only. Ask the owner for any new purpose.
