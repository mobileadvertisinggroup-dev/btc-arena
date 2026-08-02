# AKRA ARENA — Operations (website + pilot)

## Website structure (docs/, served by GitHub Pages when deployed)

| Path | Content |
|---|---|
| `/` (`docs/index.html`) | AKRA ARENA dashboard: 18 accounts, Aggregate/BTC/ETH/SOL tabs, Raw-vs-Feature pairs. Data source precedence: `live_payload.js` (`window.ARENA_LIVE`, published only by an ACTIVE pilot) → `prestart_payload.js` (honest pre-start, 0/12) → `demo_payload.js` (labeled demo, opt-in `?demo=1`). |
| `/pilot-archive/` | OLD BTC-only pilot page, labeled "OLD PILOT / SYSTEM TEST / NOT VALID EXPERIMENTAL EVIDENCE"; linked from the homepage nav. |
| `/pilot-12h-archive/` | Created later by the reset script; preserves the 12-hour pilot results. |
| `/design/` | Design pack documents. |

Regenerate the mock payload: `python3 scripts/gen_demo_dashboard.py`
(offline; fixtures only; embeds the DEMO notice).

## Exact activation command — 12-hour visible paper-trading pilot (NOT YET AUTHORIZED)

```
ARENA_PILOT_APPROVED=YES-AUDIT-PASSED \
ARENA_APPROVED_MANIFEST_SHA256=<mentor-approved combined-manifest digest> \
ARENA_DEPLOY_TOKEN=<GitHub push token for the Pages branch> \
ANTHROPIC_API_KEY=<key> \
  python3 scripts/run_pilot_12h.py --activate
```

`ARENA_APPROVED_MANIFEST_SHA256` is issued EXTERNALLY by the independent
auditor after a formal PASS. The current tree must hash to exactly this
combined manifest digest BEFORE any state initialization, prompt rendering,
network access, or model call — otherwise the script halts with zero model
calls. The tree can never approve itself (Ruling 010.1).

When active it: persists a fixed 12-boundary hourly schedule; fetches real
Kraken OHLC; asks real Claude Haiku/Sonnet/Opus for hourly decisions (paper
money, temporary $10,000 accounts, Raw-vs-Feature); drives the audited
coordinator; and after each committed boundary publishes
`docs/live_payload.js` to the public site with the banner "12-HOUR PILOT —
REAL AI DECISIONS — PAPER MONEY — NOT OFFICIAL EXPERIMENTAL EVIDENCE".

Restart safety: re-running the activation command resumes the SAME persisted
schedule — an incomplete boundary is recovered under the frozen rule
(non-finalized pairs abort as `crash_recovery`), then the run continues; a
pilot always produces exactly 12 unique scheduled boundaries. Publication
failure never re-executes a round: it is persisted as `FAILED` in
`data-pilot-12h/publications.json` and retried (publication only) at next
startup.

## Exact archive/reset command — after the pilot, before the official run

```
ARENA_APPROVED_MANIFEST_SHA256=<mentor-approved combined-manifest digest> \
  python3 scripts/archive_pilot_reset.py --confirm
```

Preserves the pilot (page + raw data) under `/pilot-12h-archive/`, then writes
18 completely fresh official accounts at exactly $10,000 to
`data-v1/state.json` in a store provisioned ONLY against the externally
approved digest. It never reuses pilot balances, trades, positions, or
reasoning, and it does NOT start the 14-day experiment (separate gate).

## Still not authorized

Claude model calls · hourly scheduler · the 12-hour pilot itself · official
account initialization · the 14-day experiment. Gate: independent source-audit
approval (formal PASS + externally issued approved digest), then Ziad's
activation command above.
