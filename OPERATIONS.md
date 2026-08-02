# BTC Arena — Operations (website + pilot)

## Website structure (docs/, served by GitHub Pages when deployed)

| Path | Content |
|---|---|
| `/` (`docs/index.html`) | NEW dashboard: 18 accounts, Aggregate/BTC/ETH/SOL tabs, Raw-vs-Feature pairs, banner "NEW BTC ARENA — PREPARATION MODE — EXPERIMENT NOT STARTED". Data source: `docs/demo_payload.js` (**mock/demo only**). |
| `/pilot-archive/` | OLD BTC-only pilot page, labeled "OLD PILOT / SYSTEM TEST / NOT VALID EXPERIMENTAL EVIDENCE"; linked from the homepage nav. |
| `/pilot-12h-archive/` | Created later by the reset script; preserves the 12-hour pilot results. |
| `/design/` | Design pack documents. |

Regenerate the mock payload: `python3 scripts/gen_demo_dashboard.py`
(offline; fixtures only; embeds the DEMO notice).

## Exact activation command — 12-hour visible paper-trading pilot (NOT YET AUTHORIZED)

```
ARENA_PILOT_APPROVED=YES-AUDIT-PASSED ANTHROPIC_API_KEY=<key> \
  python3 scripts/run_pilot_12h.py --activate
```

Without BOTH the env value and the flag, the script refuses and makes zero
model calls. When active it fetches real Kraken OHLC, asks real Claude
Haiku/Sonnet/Opus for hourly decisions (paper money, temporary $10,000
accounts, Raw-vs-Feature), drives the audited coordinator, and publishes the
dashboard with the banner: "12-HOUR PILOT — REAL AI DECISIONS — PAPER MONEY —
NOT OFFICIAL EXPERIMENTAL EVIDENCE".

## Exact archive/reset command — after the pilot, before the official run

```
python3 scripts/archive_pilot_reset.py --confirm
```

Preserves the pilot (page + raw data) under `/pilot-12h-archive/`, then writes
18 completely fresh official accounts at exactly $10,000 to
`data-v1/state.json`. It never reuses pilot balances, trades, positions, or
reasoning, and it does NOT start the 14-day experiment (separate gate).

## Still not authorized

Claude model calls · hourly scheduler · the 12-hour pilot itself · official
account initialization · the 14-day experiment. Gate: independent source-audit
approval, then Ziad's activation command above.
