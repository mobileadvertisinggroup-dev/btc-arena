# AKRA ARENA V1 — Claude Code orientation (read this first)

This repo is a mentor-audited AI trading experiment ("AKRA ARENA", codename
btc-arena-v1). Three Claude models (Haiku 4.5, Sonnet 5, Opus 4.8) paper-trade
BTC/ETH/SOL hourly in matched Raw-vs-Feature(TA) account pairs. PAPER MONEY
ONLY. The 12-hour visible pilot completed and was archived on 2026-08-03; the
official 14-day experiment has NOT started.

Start every fresh session by reading, in order:
1. docs/handoff/CURRENT_STATUS.md      — exact state of the world
2. docs/handoff/NEXT_STEPS.md          — what to build next and what is gated
3. docs/handoff/OPERATIONS_RUNBOOK.md  — every command you may need

## Governance (non-negotiable)
- Ziad = owner and only activation authority. ChatGPT = independent auditor
  ("Mentor Rulings"). Claude = implementation engineer.
- The engine tree is FROZEN at a mentor-approved digest. NEVER edit anything
  in engine/, scripts/, config/, prompts/, schemas/ without planning a full
  re-audit; any byte change breaks the approved digest and blocks activation.
  New tooling goes in tools/ (unhashed). Static UI (docs/index.html,
  prestart_payload.js, demo_payload.js) is frozen by a second site digest.
- Current approved digests are listed in docs/handoff/AUDIT_AND_DIGESTS.md.
  Verify with: python3 -c "from engine import config;
  print(config.build_manifest()['combined']);
  print(config.build_site_manifest()['combined'])"

## FORBIDDEN without explicit owner authorization
- Starting the official 14-day experiment (data-v1 accounts are provisioned
  but must stay untouched).
- Any model calls, scheduler, preflight, soak, or pilot activation.
- Deploying/pushing anything to the public site or repo.
- Using any credential beyond the exact scope the owner stated when granting
  it. Never store tokens/keys in the repo, code, logs, or docs.
- Deleting or rewriting the pilot archive (docs/pilot-12h-archive/) or
  evidence/.

## Environment quirks
- No git CLI / Xcode CLT: use dulwich (pure Python). No brew/sudo.
- Python 3.13 user installs; pytest suite (258 tests) is hermetic and
  zero-network — run `python3 -m pytest tests/ -q` freely.
- Node 22 exists; front-end contract tests run it via tests/frontend_harness.js.
- This project shares the machine with unrelated projects (including a
  restaurant website that confusingly shares the AKRA name). Do not mix them.
